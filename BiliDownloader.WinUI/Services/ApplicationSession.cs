using System.Text.Json;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.ViewModels;

namespace BiliDownloader.WinUI.Services;

public sealed class ApplicationSession : ViewModelBase
{
    public BackendClient Client { get; }
    public ShellViewModel Shell { get; } = new();
    public DownloadViewModel Download { get; }
    public AccountViewModel Account { get; }
    public SettingsViewModel Settings { get; }
    public TasksViewModel Tasks { get; }
    private DispatcherQueueTimer? taskTimer;
    public DispatcherQueue Dispatcher { get; set; } = null!;
    public bool Connected { get; private set; }
    public bool Busy { get; private set; }
    public bool Closing { get; private set; }
    public bool Available => Connected && !Busy && !Closing;
    public bool CancelRequested { get; private set; }
    public bool CanCancel => Busy && !Closing && !CancelRequested && operationId is not null;
    public bool CanNavigate => !Closing;
    public string? ActiveMethod { get; private set; }
    private string? operationId;
    private readonly SemaphoreSlim settingsGate = new(1, 1);
    public event Action<string>? ThemeRequested;
    public event Action? TasksRequested;
    public void ShowTasks() => TasksRequested?.Invoke();

    public ApplicationSession(BackendClient? client = null)
    {
        Client = client ?? new BackendClient();
        Download = new DownloadViewModel(this);
        Account = new AccountViewModel(this);
        Settings = new SettingsViewModel(this);
        Tasks = new TasksViewModel(this);
        Client.Event += e => Dispatcher.TryEnqueue(() => HandleEvent(e));
        Client.Disconnected += ex => Dispatcher.TryEnqueue(() =>
        {
            Connected = false; taskTimer?.Stop(); Tasks.Disconnected(); Changed(); Report(ex);
        });
    }

    public async Task InitializeAsync()
    {
        try
        {
            Client.Start();
            var hello = Protocol.Read<Hello>(await Client.RequestAsync("hello", new { protocol_version = Protocol.Version, frontend_version = App.AppVersion }));
            if (hello.ProtocolVersion != Protocol.Version || hello.BackendVersion != App.AppVersion) throw new InvalidDataException("前后端版本不一致。");
            Connected = true;
            ApplySettings(hello.Settings);
            Account.ApplyStatus(hello.Status);
            Account.SafeMode = hello.SafeMode;
            if (hello.SafeMode) Shell.Notify("上次运行异常退出，已进入安全模式。正常关闭并重新打开后可扫码登录。", InfoBarSeverity.Warning);
            else Shell.IsOpen = false;
            foreach (var text in hello.ConfigDiagnostics) Download.AppendLog(text);
            Changed();
            if (!hello.SafeMode && hello.Status.Code is not "none") await Account.ValidateAsync();
            await Tasks.ReloadAsync();
            taskTimer = Dispatcher.CreateTimer();
            taskTimer.Interval = TimeSpan.FromSeconds(2);
            taskTimer.Tick += async (_, _) => await ExecuteAsync(Tasks.ReloadAsync);
            taskTimer.Start();
        }
        catch (Exception ex) { Report(ex); }
    }

    public async Task<JsonElement> RunAsync(string method, object? parameters = null)
    {
        if (!Available) throw new InvalidOperationException("请等待当前任务结束。");
        Busy = true; CancelRequested = false; ActiveMethod = method; Changed();
        try
        {
            return await Client.RunAsync(method, parameters, id => Dispatcher.TryEnqueue(() => { operationId = id; Changed(); }));
        }
        finally { Busy = false; CancelRequested = false; operationId = null; ActiveMethod = null; Changed(); }
    }

    public async Task ExecuteAsync(Func<Task> action)
    {
        if (Closing) return;
        try { await action(); }
        catch (OperationCanceledException) { Shell.Notify("操作已取消。", InfoBarSeverity.Warning); }
        catch (Exception ex) { Report(ex); }
    }

    public void Report(Exception ex)
    {
        FrontendLog.Write(ex);
        string message = ex is BackendException backend ? backend.Error.Message : ex.Message;
        Shell.Notify(message, InfoBarSeverity.Error);
        if (ex is BackendException detail) Download.AppendLog($"{detail.Error.Code}: {detail.Error.Detail}");
    }

    public async Task CancelAsync()
    {
        if (!CanCancel) return;
        string? cancelledOperation = operationId;
        CancelRequested = true; Changed();
        JsonElement result;
        try { result = await Client.RequestAsync("operation.cancel", new { operation_id = cancelledOperation }); }
        catch
        {
            if (operationId == cancelledOperation) { CancelRequested = false; Changed(); }
            throw;
        }
        if (operationId != cancelledOperation || Closing) return;
        string message = result.TryGetProperty("waiting_for_postprocessing", out var waiting) && waiting.GetBoolean()
            ? "已请求取消，正在等待当前文件合并或转码安全结束…" : "已请求取消，正在等待任务安全结束…";
        Download.Status = message;
        if (Account.IsLoggingIn) Account.QrStatus = message;
        Shell.Notify(message, InfoBarSeverity.Warning);
    }

    public async Task RefreshQrAsync()
    {
        if (Account.CanRefresh && operationId is not null)
        {
            Account.ClearQr();
            try
            {
                var result = await Client.RequestAsync("auth.qr.refresh", new { operation_id = operationId });
                Account.ExpectGeneration(result.GetProperty("minimum_generation").GetInt32());
            }
            catch { Account.CancelRefresh(); throw; }
        }
    }

    public void PreviewTheme(string value) => ThemeRequested?.Invoke(value);

    public void ApplySettings(AppSettings settings, bool discardDraft = false, bool applyDownloadPreferences = true)
    {
        Settings.Load(settings, discardDraft);
        if (applyDownloadPreferences)
        {
            Download.DownloadDirectory = settings.DownloadDir;
            Download.ApplyPreferences(settings);
        }
        PreviewTheme(Settings.CurrentTheme);
    }

    public async Task UpdateSettingsAsync(object patch, bool fromDownload = false, bool discardDraft = false)
    {
        await settingsGate.WaitAsync();
        try
        {
            var settings = Protocol.Read<AppSettings>(await Client.RequestAsync(fromDownload ? "settings.remember" : "settings.update", patch));
            ApplySettings(settings, discardDraft, applyDownloadPreferences: !fromDownload);
        }
        finally { settingsGate.Release(); }
    }

    public void Changed()
    {
        Refresh(); Download.Refresh(); Account.Refresh(); Settings.Refresh(); Tasks.Refresh();
    }

    private void HandleEvent(BackendEvent e)
    {
        if (Closing) return;
        if (e.Name == "task.changed") { Tasks.Changed(Protocol.Read<TaskChange>(e.Data)); return; }
        if (e.OperationId != operationId) return;
        if (e.Name == "log.message") Download.AppendLog(e.Data.GetProperty("text").GetString() ?? "");
        if (e.Name == "download.progress") Download.Progress(Protocol.Read<DownloadProgress>(e.Data));
        if (e.Name is "auth.qr.image" or "auth.qr.state") Account.Handle(e);
    }

    public async Task ShutdownAsync()
    {
        Closing = true; taskTimer?.Stop(); Changed();
        Shell.Notify("正在安全关闭，等待后台任务释放资源…", InfoBarSeverity.Warning);
        await Download.WaitForPreferenceSaveAsync();
        await Client.ShutdownAsync();
    }
}
