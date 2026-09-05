using System.Diagnostics;
using System.Collections.Concurrent;
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
            var hello = Protocol.Read<Hello>(await c.RequestAsync("hello", new { protocol_version = 1, frontend_version = "2.9" }));
            Check(hello.BackendVersion == "2.9" && hello.ProtocolVersion == 1, "Version negotiation failed");
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
    await Test("normal failed terminal completes exactly once with BackendException", async () =>
    {
        var c = Client("normal_failed"); var events = new ConcurrentQueue<string>();
        c.Event += e => events.Enqueue(e.Name); c.Start();
        try
        {
            await Throws<BackendException>(() => c.RunAsync("work", null, _ => { }));
            Check(events.SequenceEqual(new[] { "operation.failed" }), "Valid failed terminal was not published exactly once");
        }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
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
    foreach (string scenario in new[]
    {
        "terminal_completed_missing_result", "terminal_completed_wrong_result_type",
        "terminal_failed_missing_error", "terminal_failed_wrong_error_type",
        "terminal_failed_wrong_error_field_type", "terminal_cancelled_missing_result",
        "terminal_cancelled_wrong_result_type", "invalid_seq"
    })
        await Test(scenario + " faults the accepted call without publishing a terminal event", async () =>
        {
            var c = Client(scenario); var events = new ConcurrentQueue<string>();
            c.Event += e => events.Enqueue(e.Name); c.Start();
            try
            {
                await Throws<InvalidDataException>(() => c.RunAsync("work", null, _ => { }));
                Check(!events.Any(name => name.StartsWith("operation.")), "Malformed terminal reached the UI event surface");
                await Throws<IOException>(() => c.RequestAsync("session.status"));
            }
            finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
        });
    await Test("EOF after acceptance faults the operation and shutdown completes", async () =>
    {
        var c = Client("accepted_eof"); c.Start();
        try { await Throws<IOException>(() => c.RunAsync("work", null, _ => { })); }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
    });
    await Test("one malformed terminal faults every accepted operation", async () =>
    {
        var c = Client("multiple_accepted_malformed"); c.Start();
        var acceptedOne = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var acceptedTwo = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        try
        {
            Task<JsonElement> one = c.RunAsync("work", new { ordinal = 1 }, _ => acceptedOne.TrySetResult(true));
            await acceptedOne.Task.WaitAsync(TimeSpan.FromSeconds(3));
            Task<JsonElement> two = c.RunAsync("work", new { ordinal = 2 }, _ => acceptedTwo.TrySetResult(true));
            await acceptedTwo.Task.WaitAsync(TimeSpan.FromSeconds(3));
            await Throws<InvalidDataException>(() => one);
            await Throws<InvalidDataException>(() => two);
        }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
    });
    await Test("malformed accepted terminal also faults an unacknowledged pending request", async () =>
    {
        for (int attempt = 0; attempt < 20; attempt++)
        {
            var c = Client("accepted_and_pending_malformed"); c.Start();
            var accepted = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            try
            {
                Task<JsonElement> operation = c.RunAsync("work", null, _ => accepted.TrySetResult(true));
                await accepted.Task.WaitAsync(TimeSpan.FromSeconds(3));
                Task<JsonElement> pending = c.RequestAsync("hold");
                await Throws<InvalidDataException>(() => operation);
                await Throws<InvalidDataException>(() => pending);
            }
            finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
        }
    });
    await Test("duplicate and late terminal events are ignored", async () =>
    {
        var c = Client("duplicate_terminal"); var events = new ConcurrentQueue<string>();
        c.Event += e => events.Enqueue(e.Name); c.Start();
        try
        {
            var result = await c.RunAsync("work", null, _ => { });
            Check(result.GetProperty("title").GetString() == "once", "Late terminal replaced the first result");
            await Task.Delay(100);
            Check(events.SequenceEqual(new[] { "operation.completed" }), "Duplicate or late event escaped stale filtering");
        }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
    });
    await Test("terminal cleanup permits a later operation to reuse the same opaque id and seq", async () =>
    {
        var c = Client("reuse_operation_id"); c.Start();
        try
        {
            var first = await c.RunAsync("work", null, _ => { });
            var second = await c.RunAsync("work", null, _ => { });
            Check(first.GetProperty("ordinal").GetInt32() == 1 && second.GetProperty("ordinal").GetInt32() == 2,
                  "Operation or sequence state was not cleaned after terminal completion");
        }
        finally { await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
    });
    await Test("shutdown with an accepted operation finishes both shutdown and the caller", async () =>
    {
        var c = Client("shutdown_with_accepted"); c.Start();
        var accepted = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        Task<JsonElement> operation = c.RunAsync("work", null, _ => accepted.TrySetResult(true));
        await accepted.Task.WaitAsync(TimeSpan.FromSeconds(3));
        await c.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8));
        await Throws<IOException>(() => operation);
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
