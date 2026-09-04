using System.Diagnostics;
using System.Text.Json;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

// Dependency-free executable tests exercise the production transport over real pipes.
string python = Environment.GetEnvironmentVariable("BILI_TEST_PYTHON") ?? throw new Exception("Set BILI_TEST_PYTHON to an absolute Python path.");
string repo = Environment.GetEnvironmentVariable("BILI_BACKEND_SOURCE") ?? throw new Exception("Set BILI_BACKEND_SOURCE.");
string profile = Path.Combine(Path.GetTempPath(), "bili-transport-tests", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(profile);
int passed = 0;
void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
async Task Throws<T>(Func<Task> action) where T : Exception
{
    try { await action().WaitAsync(TimeSpan.FromSeconds(8)); }
    catch (T) { return; }
    throw new Exception($"Expected {typeof(T).Name}");
}
BackendClient Client(string scenario, double timeout = 3) => new(() =>
{
    var start = new ProcessStartInfo(python) { WorkingDirectory = repo, UseShellExecute = false, CreateNoWindow = true,
        RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
    if (scenario == "real") { start.ArgumentList.Add("-m"); start.ArgumentList.Add("app.backend"); }
    else { start.ArgumentList.Add(Path.Combine(repo, "tests", "fixtures", "ipc_peer.py")); start.ArgumentList.Add(scenario); }
    start.Environment["LOCALAPPDATA"] = Path.Combine(profile, scenario, "Local");
    start.Environment["APPDATA"] = Path.Combine(profile, scenario, "Roaming");
    start.Environment["PYTHONUTF8"] = "1";
    return Process.Start(start)!;
}, TimeSpan.FromSeconds(timeout));

async Task Test(string name, Func<Task> test) { await test(); passed++; Console.WriteLine($"PASS {name}"); }
try
{
    await Test("accessible item text does not expose DTO implementation details", () =>
    {
        var part = new VideoPart(2, "第二部分", 30, "private-id");
        var failed = new PartResult(2, "第二部分", "failed", [], new ErrorInfo("timeout", "网络超时", true, "details"));
        Check(part.ToString() == "P2 · 第二部分", "Part has no accessible display text");
        Check(failed.ToString() == "P2 · 第二部分，失败，网络超时", "Result exposes record fields instead of user text");
        Check(new FormatChoice("0", "1080p", 1080, "per_part").ToString() == "1080p", "Format exposes its DTO");
        return Task.CompletedTask;
    });
    await Test("real Qt-free backend: handshake, validation, settings, clean shutdown", async () =>
    {
        var c = Client("real"); c.Start();
        try
        {
            var hello = Protocol.Read<Hello>(await c.RequestAsync("hello", new { protocol_version = 1, frontend_version = "2.5" }));
            Check(hello.BackendVersion == "2.5" && hello.ProtocolVersion == 1, "Version negotiation failed");
            Check(Directory.EnumerateDirectories(profile, "run-markers", SearchOption.AllDirectories).SelectMany(Directory.EnumerateFiles).Any(), "Backend did not acquire a running marker");
            string? operation = null;
            await Throws<BackendException>(() => c.RunAsync("parse.start", new { input = "invalid", credential_mode = "anonymous" }, id => operation = id));
            Check(operation is not null, "Failure must follow acceptance");
            var settings = await c.RequestAsync("settings.update", new { theme = "dark" });
            Check(settings.GetProperty("theme").GetString() == "dark", "Preferences did not persist");
            await c.RequestAsync("session.status");
        }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(10)); }
        Check(!Directory.EnumerateDirectories(profile, "run-markers", SearchOption.AllDirectories).SelectMany(Directory.EnumerateFiles).Any(), "Backend did not release running marker");
    });
    await Test("fragmented Unicode frames: acceptance precedes events; stale events ignored", async () =>
    {
        var c = Client("fragmented"); var events = new List<string>();
        c.Event += e => events.Add(e.Name); c.Start();
        try
        {
            var result = await c.RunAsync("parse.start", null, _ => events.Add("accepted"));
            Check(result.GetProperty("title").GetString() == "中文分 P", "UTF-8 framing failed");
            Check(events.SequenceEqual(new[] { "accepted", "download.progress", "operation.completed" }), "Event ordering or stale filtering failed");
        }
        finally { await c.ShutdownAsync(); }
    });
    foreach (string scenario in new[] { "cancelled", "bad_sequence", "partial", "wrong_version", "malformed" })
        await Test(scenario, async () =>
        {
            var c = Client(scenario); c.Start();
            try
            {
                if (scenario == "cancelled") await Throws<OperationCanceledException>(() => c.RunAsync("work", null, _ => { }));
                else await Throws<Exception>(() => c.RunAsync("work", null, _ => { }));
            }
            finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
        });
    await Test("unacknowledged operation times out and drains through EOF", async () =>
    {
        var c = Client("unacknowledged", 0.2); c.Start();
        try
        {
            await Throws<TimeoutException>(() => c.RunAsync("work", null, _ => { }));
            await Throws<IOException>(() => c.RequestAsync("session.status"));
        }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
    });
    Console.WriteLine($"{passed} transport checks passed");
}
finally
{
    // This path is created above solely for this test run.
    Directory.Delete(profile, true);
}
