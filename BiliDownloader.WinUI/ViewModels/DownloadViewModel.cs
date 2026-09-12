using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class DownloadViewModel(ApplicationSession session) : ViewModelBase
{
    private string input = "", directory = "", status = "等待输入", logs = "";
    private int modeIndex, credentialIndex = 1;
    private long inputRevision;
    private VideoInfo? video;
    private BatchResult? result;
    private BitmapImage? thumbnail;
    private bool showResult;
    private string progressPhase = "preparing";
    private string? selectedOutput;
    private FormatChoice? selectedFormat;
    private bool applyingPreferences, rememberPreferences = true;
    private int? preferredHeight;
    private Task preferenceSave = Task.CompletedTask;
    internal Task WaitForPreferenceSaveAsync() => preferenceSave;
    private bool adding;
    private string queueMessage = "";
    private readonly Dictionary<string, string> submissionTokens = [];
    public ObservableCollection<BatchInputRow> BatchItems { get; } = [];
    public string QueueMessage { get => queueMessage; private set { Set(ref queueMessage, value); Refresh(); } }
    public Visibility QueueMessageVisibility => string.IsNullOrEmpty(QueueMessage) ? Visibility.Collapsed : Visibility.Visible;
    public Visibility BatchVisibility => BatchItems.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    public bool CanAddBatch => session.Available && !adding && BatchItems.Any(r => r.Video is not null && !r.Added);
    public ObservableCollection<VideoPart> Parts { get; } = [];
    public ObservableCollection<FormatChoice> Formats { get; } = [];
    public ObservableCollection<PartResult> Results { get; } = [];
    public ObservableCollection<string> OutputFiles { get; } = [];
    public ObservableCollection<OutputFileItem> OutputChoices { get; } = [];
    public HashSet<int> SelectedParts { get; } = [];
    public FormatChoice? SelectedFormat
    {
        get => selectedFormat;
        set
        {
            if (!Set(ref selectedFormat, value)) return;
            if (!applyingPreferences && value is not null && Formats.Contains(value))
            {
                preferredHeight = value.Height;
                RememberPreference(new { preferred_quality = preferredHeight });
            }
            Refresh();
        }
    }
    public string? SelectedOutput { get => selectedOutput; set { if (Set(ref selectedOutput, value)) Refresh(); } }
    public OutputFileItem? SelectedOutputChoice { get => OutputChoices.FirstOrDefault(file => file.Path == SelectedOutput); set => SelectedOutput = value?.Path; }
    public bool CanOpenFile => SelectedOutput is not null && File.Exists(SelectedOutput);
    public string ResultDirectory => SelectedOutput is { } path ? Path.GetDirectoryName(path) ?? "" : result?.OutputDir ?? "";
    public bool CanOpenFolder => Directory.Exists(ResultDirectory);
    public string Input { get => input; set { if (CanEditInput && Set(ref input, value)) Invalidate(); } }
    public int CredentialIndex { get => credentialIndex; set { if (Set(ref credentialIndex, value)) Invalidate(); } }
    public int ModeIndex
    {
        get => modeIndex;
        set
        {
            if (value is not (0 or 1) || !Set(ref modeIndex, value)) return;
            if (!applyingPreferences) RememberPreference(new { download_mode = value == 1 ? "audio_mp3" : "audio_video" });
            Refresh();
        }
    }
    private void RememberPreference(object patch)
    {
        if (rememberPreferences && session.Connected && !session.Closing)
            preferenceSave = session.ExecuteAsync(() => session.UpdateSettingsAsync(patch, fromDownload: true));
    }
    internal void ApplyPreferences(AppSettings settings)
    {
        applyingPreferences = true;
        try
        {
            rememberPreferences = settings.RememberDownloadPreferences;
            ModeIndex = settings.DownloadMode == "audio_mp3" ? 1 : 0;
            preferredHeight = settings.PreferredQuality;
            SelectedFormat = ResolveFormat(Formats);
        }
        finally { applyingPreferences = false; }
        Refresh();
    }
    private FormatChoice? ResolveFormat(IEnumerable<FormatChoice> formats) => preferredHeight is { } height
        ? formats.FirstOrDefault(f => f.Height == height)
        : formats.FirstOrDefault(f => f.Height is null) ?? formats.OrderByDescending(f => f.Height).FirstOrDefault();
    public string QualityHint => SelectedFormat is null && preferredHeight is not null
        ? $"此视频未提供偏好的 {preferredHeight}p，请选择其他画质后加入队列。原偏好会保留，直到你主动更改。"
        : "指定分辨率时严格匹配，不自动降档。";
    public string DownloadDirectory { get => directory; set { if (Set(ref directory, value)) Refresh(); } }
    public string Status { get => status; set => Set(ref status, value); }
    public string Logs { get => logs; private set => Set(ref logs, value); }
    public string Title => video?.Title ?? "";
    public string Metadata => video is null ? "" : $"{video.Uploader} · {Duration(video.DurationSeconds)} · {video.Parts.Length} 个分 P";
    public string SelectionSummary => $"选择分 P · 已选 {SelectedParts.Count} / {Parts.Count}";
    public string AccessHint => CredentialIndex == 0 ? session.Account.HasCredentials
        ? "使用账号当前可用的画质与访问权限。" : "请先在“账号”页扫码登录，或选择匿名访问。"
        : "无需登录；可用画质以平台实际返回为准。";
    public BitmapImage? Thumbnail { get => thumbnail; private set { if (Set(ref thumbnail, value)) Refresh(); } }
    public double Percent { get; private set; }
    public string Metrics { get; private set; } = "";
    public bool CanParse => session.Available && !adding && !string.IsNullOrWhiteSpace(input);
    public bool CanDownload => session.Available && !adding && video is not null && SelectedParts.Count > 0 && !string.IsNullOrWhiteSpace(directory)
        && (ModeIndex == 1 || SelectedFormat is not null);
    public bool CanRetry => session.Available && result?.RetryAllowed == true && video is not null;
    public bool CanChoose => session.Available && !adding;
    public bool CanEditInput => !session.Closing && !IsDownloading && !adding;
    public bool CanCancel => IsLocalTask && session.CanCancel;
    public string CancelText => session.CancelRequested ? "正在安全取消…" : "取消当前任务";
    private bool IsLocalTask => session.Busy && session.ActiveMethod is "parse.start" or "parse.batch" or "media.thumbnail" or "download.start" or "download.retry";
    public bool IsIndeterminate => IsLocalTask && (!IsDownloading || progressPhase is "preparing" or "merging" or "converting");
    public Visibility VideoVisibility => video is null || showResult || IsDownloading ? Visibility.Collapsed : Visibility.Visible;
    private bool IsDownloading => session.Busy && session.ActiveMethod is "download.start" or "download.retry";
    public Visibility PartsVisibility => Parts.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ThumbnailVisibility => Thumbnail is null ? Visibility.Collapsed : Visibility.Visible;
    public double ThumbnailSpacing => Thumbnail is null ? 0 : 16;
    public Visibility FormatVisibility => ModeIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ProgressVisibility => IsLocalTask ? Visibility.Visible : Visibility.Collapsed;
    public Visibility OtherTaskVisibility => session.Busy && !IsLocalTask ? Visibility.Visible : Visibility.Collapsed;
    public string OtherTaskStatus => session.ActiveMethod switch
    {
        "auth.qr.start" => "正在扫码登录。请在账号页完成或取消后继续。",
        "session.validate" => "正在验证登录状态，请稍候。",
        "session.clear" => "正在清除登录状态，请稍候。",
        "diagnostics.run" => "正在运行环境诊断，请稍候。",
        "updates.check" => "正在检查更新，请稍候。",
        _ => ""
    };
    public Visibility ActiveTitleVisibility => IsDownloading && video is not null ? Visibility.Visible : Visibility.Collapsed;
    public Visibility MetricsVisibility => IsDownloading && !string.IsNullOrEmpty(Metrics) ? Visibility.Visible : Visibility.Collapsed;
    public Visibility LogsVisibility => string.IsNullOrEmpty(Logs) ? Visibility.Collapsed : Visibility.Visible;
    public Visibility ResultVisibility => showResult ? Visibility.Visible : Visibility.Collapsed;
    public Visibility DetailVisibility => Results.Count > 1 || OutputFiles.Count != 1 || result?.Outcome != "completed" ? Visibility.Visible : Visibility.Collapsed;
    public string ResultSummary => result is null ? "" : $"{(result.Outcome switch { "completed" => "下载完成", "partial" => "部分完成", "cancelled" => "已取消", _ => "下载失败" })} · 成功 {Results.Count(p => p.Status == "completed")} / {Results.Count}";
    public string SelectedOutputName => SelectedOutput is null ? "" : Path.GetFileName(SelectedOutput);
    public Visibility OutputVisibility => OutputFiles.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility SingleOutputVisibility => OutputFiles.Count == 1 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility OutputPickerVisibility => OutputFiles.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility EmptyOutputFolderVisibility => OutputFiles.Count == 0 && CanOpenFolder ? Visibility.Visible : Visibility.Collapsed;
    public Visibility RetryVisibility => result?.RetryAllowed == true && video is not null ? Visibility.Visible : Visibility.Collapsed;

    public void Invalidate()
    {
        inputRevision++;
        BatchItems.Clear(); QueueMessage = ""; submissionTokens.Clear();
        video = null; Thumbnail = null;
        if (result is not null) result = result with { RetryAllowed = false };
        if (!session.Busy) { showResult = false; Status = "链接或账号模式已改变，请重新解析。"; }
        Refresh();
        if (session.Connected && !session.Closing) _ = session.ExecuteAsync(async () => { await session.Client.RequestAsync("parse.invalidate"); });
    }

    public async Task ParseAsync()
    {
        if (!CanParse) return;
        long revision = ++inputRevision;
        video = null; result = null; showResult = false; Thumbnail = null;
        SelectedParts.Clear(); Parts.Clear(); Formats.Clear(); SelectedFormat = null;
        Percent = 0; Metrics = "";
        Status = "正在解析…"; session.Shell.IsOpen = false; Refresh();
        var parsed = Protocol.Read<VideoInfo>(await session.RunAsync("parse.start", new { input, credential_mode = CredentialIndex == 0 ? "saved" : "anonymous" }));
        if (revision != inputRevision || session.Closing) return;
        ApplyVideo(parsed);
        Status = "解析完成，请选择下载规格。"; Refresh();
        if (parsed.ThumbnailAvailable)
        {
            Status = "正在加载封面…";
            try
            {
                var image = await session.RunAsync("media.thumbnail", new { parse_id = parsed.ParseId });
                var decoded = await WindowsShellService.DecodeImageAsync(image.GetProperty("base64").GetString()!);
                if (revision == inputRevision && !session.Closing) Thumbnail = decoded;
            }
            catch (Exception ex) { AppendLog("封面加载失败，解析结果仍可下载。"); FrontendLog.Write(ex); }
        }
    }

    internal void ApplyVideo(VideoInfo parsed)
    {
        video = parsed; result = null; showResult = false; Thumbnail = null;
        SelectedParts.Clear(); Parts.Clear();
        foreach (var p in parsed.Parts) Parts.Add(p);
        applyingPreferences = true;
        try
        {
            Formats.Clear();
            foreach (var f in parsed.Formats) Formats.Add(f);
            SelectedFormat = ResolveFormat(Formats);
        }
        finally { applyingPreferences = false; }
        if (parsed.Parts.Length > 0) SelectedParts.Add(parsed.Parts.Any(p => p.Index == parsed.CurrentPartIndex) ? parsed.CurrentPartIndex : parsed.Parts[0].Index);
        Refresh();
    }

    public async Task ParseBatchAsync()
    {
        if (!CanParse) return;
        long revision = ++inputRevision;
        BatchItems.Clear(); QueueMessage = ""; video = null; showResult = false;
        Status = "正在逐项解析…"; Refresh();
        var batch = Protocol.Read<BatchParseResult>(await session.RunAsync("parse.batch", new { input, credential_mode = CredentialIndex == 0 ? "saved" : "anonymous" }));
        if (revision != inputRevision || session.Closing) return;
        foreach (var row in batch.Items) BatchItems.Add(new BatchInputRow(row));
        QueueMessage = $"解析成功 {BatchItems.Count(r => r.Video is not null)} / {BatchItems.Count}。默认只选择链接指向的分 P，可逐项配置。";
        var first = BatchItems.FirstOrDefault(r => r.Video is not null);
        if (first?.Video is { } parsed) ApplyVideo(parsed);
        Refresh();
    }

    public void ConfigureBatch(BatchInputRow row)
    {
        if (row.Video is { } parsed && CanChoose) { ApplyVideo(parsed); QueueMessage = "正在配置：" + parsed.Title; }
    }

    private async Task<TaskReply> EnqueueVideoAsync(VideoInfo parsed, int[] indices, string? formatId)
    {
        string key = System.Text.Json.JsonSerializer.Serialize(new { parsed.ParseId, indices, formatId, ModeIndex, directory });
        if (!submissionTokens.TryGetValue(key, out var token)) submissionTokens[key] = token = Guid.NewGuid().ToString("N");
        var reply = Protocol.Read<TaskReply>(await session.Client.RequestAsync("tasks.create", new { parse_id = parsed.ParseId,
            part_indices = indices, format_id = formatId, download_mode = ModeIndex == 0 ? "audio_video" : "audio_mp3", download_dir = directory, token }));
        session.Tasks.Upsert(reply.Task);
        return reply;
    }

    public async Task EnqueueAsync()
    {
        if (!CanDownload) return;
        adding = true; Refresh();
        try
        {
            var reply = await EnqueueVideoAsync(video!, SelectedParts.Order().ToArray(), SelectedFormat?.FormatId);
            QueueMessage = reply.Duplicate ? "此任务已存在，可在任务页查看。" : "已加入队列。可继续添加视频，或前往任务页查看。";
            foreach (var row in BatchItems.Where(r => r.Video?.ParseId == video!.ParseId)) row.MarkAdded();
            await session.Tasks.ReloadAsync();
        }
        finally { adding = false; Refresh(); }
    }

    public async Task EnqueueBatchAsync()
    {
        if (!CanAddBatch) return;
        adding = true; Refresh();
        int count = 0;
        try
        {
            foreach (var row in BatchItems.Where(r => r.Video is not null && !r.Added).ToArray())
            {
                var parsed = row.Video!;
                var format = ResolveFormat(parsed.Formats);
                if (ModeIndex == 0 && format is null) { row.SetMessage("所选画质不可用，请逐项配置。"); continue; }
                try
                {
                    await EnqueueVideoAsync(parsed, [parsed.CurrentPartIndex], format?.FormatId);
                    row.MarkAdded(); count++;
                }
                catch (BackendException ex) { row.SetMessage(ex.Message); }
            }
            QueueMessage = $"已加入 {count} 个任务。未成功的项目可查看提示后重试。";
            await session.Tasks.ReloadAsync();
        }
        finally { adding = false; Refresh(); }
    }

    public async Task DownloadAsync(bool retry = false)
    {
        if (retry ? !CanRetry : !CanDownload) return;
        showResult = false; progressPhase = "preparing"; Percent = 0; Metrics = ""; Status = "正在准备下载…";
        session.Shell.IsOpen = false; Refresh();
        var value = retry
            ? await session.RunAsync("download.retry", new { batch_id = result!.BatchId })
            : await session.RunAsync("download.start", new { parse_id = video!.ParseId, part_indices = SelectedParts.Order().ToArray(),
                format_id = SelectedFormat?.FormatId, download_mode = ModeIndex == 0 ? "audio_video" : "audio_mp3",
                credential_mode = CredentialIndex == 0 ? "saved" : "anonymous", download_dir = directory });
        if (session.Closing) return;
        ApplyResult(Protocol.Read<BatchResult>(value));
        session.Shell.Notify(Status, result!.Outcome == "completed" ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        try { session.ApplySettings(Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get"))); }
        catch (Exception ex) { FrontendLog.Write(ex); }
        if (result.PartResults.Any(p => p.Error?.Code is "login_invalid" or "access_403")) await session.Account.ValidateAsync();
    }

    internal void ApplyResult(BatchResult value)
    {
        result = value;
        Results.Clear(); OutputFiles.Clear(); OutputChoices.Clear();
        foreach (var p in result.PartResults) Results.Add(p);
        foreach (var file in result.SavedFiles) { OutputFiles.Add(file); OutputChoices.Add(new OutputFileItem(file)); }
        SelectedOutput = OutputFiles.FirstOrDefault();
        showResult = true; Status = ResultSummary;
        Refresh();
    }

    public void CollapseResult() { showResult = false; Refresh(); }
    public void Progress(DownloadProgress p)
    {
        progressPhase = p.Phase;
        Percent = p.OverallPercent ?? Percent;
        string phase = p.Phase switch { "downloading" => "下载", "merging" => "合并", "converting" => "转换 MP3", "completed" => "完成", "failed" => "失败", "cancelled" => "取消", _ => "准备" };
        Status = p.CancelRequested || session.CancelRequested ? "已请求取消，等待当前文件安全处理结束…" : $"P{p.PartIndex} · {phase}（{p.PartNumber}/{p.PartCount}）";
        var metrics = new List<string> { $"{Percent:0.0}%" };
        if (p.DownloadedBytes is not null) metrics.Add($"{Bytes(p.DownloadedBytes)} / {Bytes(p.TotalBytes ?? p.TotalBytesEstimate)}");
        if (p.SpeedBytesPerSecond is not null) metrics.Add($"{Bytes(p.SpeedBytesPerSecond)}/s");
        if (p.EtaSeconds is not null) metrics.Add($"剩余 {Duration(p.EtaSeconds)}");
        Metrics = p.Phase is "merging" or "converting" ? "正在处理已下载的文件，请稍候。" : string.Join(" · ", metrics);
        Refresh();
    }
    public void AppendLog(string text) { Logs = (Logs + text + Environment.NewLine); if (Logs.Length > 64000) Logs = Logs[^48000..]; Refresh(); }
    private static string Duration(double? seconds) => seconds is null ? "未知" : TimeSpan.FromSeconds(Math.Max(0, Math.Min(seconds.Value, 31536000))).ToString(@"hh\:mm\:ss");
    private static string Bytes(double? bytes) => bytes is null ? "未知" : bytes >= 1048576 ? $"{bytes / 1048576:0.0} MiB" : $"{bytes / 1024:0.0} KiB";
}

public sealed record OutputFileItem(string Path)
{
    public string Name => System.IO.Path.GetFileName(Path);
    public override string ToString() => Name;
}

public sealed class BatchInputRow(BatchParseItem item) : ViewModelBase
{
    public VideoInfo? Video => item.Video;
    public string Title => item.Video?.Title ?? item.Input;
    public string Message { get; private set; } = item.Message;
    public bool Added { get; private set; }
    public bool CanConfigure => Video is not null && !Added;
    public void MarkAdded() { Added = true; Message = "已加入队列"; Refresh(); }
    public void SetMessage(string text) { Message = text; Refresh(); }
}
