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
    private string? selectedOutput;
    public ObservableCollection<VideoPart> Parts { get; } = [];
    public ObservableCollection<FormatChoice> Formats { get; } = [];
    public ObservableCollection<PartResult> Results { get; } = [];
    public ObservableCollection<string> OutputFiles { get; } = [];
    public HashSet<int> SelectedParts { get; } = [];
    public FormatChoice? SelectedFormat { get; set; }
    public string? SelectedOutput { get => selectedOutput; set { if (Set(ref selectedOutput, value)) Refresh(); } }
    public bool CanOpenFile => SelectedOutput is not null && File.Exists(SelectedOutput);
    public string ResultDirectory => SelectedOutput is { } path ? Path.GetDirectoryName(path) ?? "" : result?.OutputDir ?? "";
    public bool CanOpenFolder => Directory.Exists(ResultDirectory);
    public string Input { get => input; set { if (Set(ref input, value)) Invalidate(); } }
    public int CredentialIndex { get => credentialIndex; set { if (Set(ref credentialIndex, value)) Invalidate(); } }
    public int ModeIndex { get => modeIndex; set { if (Set(ref modeIndex, value)) Refresh(); } }
    public string DownloadDirectory { get => directory; set => Set(ref directory, value); }
    public string Status { get => status; set => Set(ref status, value); }
    public string Logs { get => logs; private set => Set(ref logs, value); }
    public string Title => video?.Title ?? "";
    public string Metadata => video is null ? "" : $"{video.Uploader} · {Duration(video.DurationSeconds)} · {video.Parts.Length} 个分 P";
    public BitmapImage? Thumbnail { get => thumbnail; private set => Set(ref thumbnail, value); }
    public double Percent { get; private set; }
    public string Metrics { get; private set; } = "";
    public bool CanParse => session.Available && !string.IsNullOrWhiteSpace(input);
    public bool CanDownload => session.Available && video is not null && SelectedParts.Count > 0;
    public bool CanRetry => session.Available && result?.RetryAllowed == true && video is not null;
    public bool CanChoose => session.Available;
    public bool CanCancel => session.CanCancel;
    public bool IsIndeterminate => session.Busy && session.ActiveMethod is "parse.start" or "media.thumbnail";
    public Visibility VideoVisibility => video is null || showResult || IsDownloading ? Visibility.Collapsed : Visibility.Visible;
    private bool IsDownloading => session.Busy && session.ActiveMethod is "download.start" or "download.retry";
    public Visibility PartsVisibility => Parts.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility FormatVisibility => ModeIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ProgressVisibility => session.Busy ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ResultVisibility => showResult ? Visibility.Visible : Visibility.Collapsed;
    public Visibility DetailVisibility => Results.Count > 1 || OutputFiles.Count != 1 || result?.Outcome != "completed" ? Visibility.Visible : Visibility.Collapsed;
    public string ResultSummary => result is null ? "" : $"{(result.Outcome switch { "completed" => "下载完成", "partial" => "部分完成", "cancelled" => "已取消", _ => "下载失败" })} · 成功 {Results.Count(p => p.Status == "completed")} / {Results.Count}";

    public void Invalidate()
    {
        inputRevision++;
        video = null; Thumbnail = null;
        if (result is not null) result = result with { RetryAllowed = false };
        if (!session.Busy) { showResult = false; Status = "链接或账号模式已改变，请重新解析。"; }
        Refresh();
        if (session.Connected && !session.Closing) _ = session.ExecuteAsync(async () => { await session.Client.RequestAsync("parse.invalidate"); });
    }

    public async Task ParseAsync()
    {
        long revision = ++inputRevision;
        video = null; result = null; showResult = false; Thumbnail = null;
        Parts.Clear(); Formats.Clear(); SelectedParts.Clear();
        Status = "正在解析…"; session.Shell.Notify(Status); Refresh();
        var parsed = Protocol.Read<VideoInfo>(await session.RunAsync("parse.start", new { input, credential_mode = CredentialIndex == 0 ? "saved" : "anonymous" }));
        if (revision != inputRevision || session.Closing) return;
        video = parsed;
        foreach (var p in parsed.Parts) Parts.Add(p);
        foreach (var f in parsed.Formats) Formats.Add(f);
        SelectedFormat = Formats.FirstOrDefault();
        SelectedParts.Add(parsed.Parts.Any(p => p.Index == parsed.CurrentPartIndex) ? parsed.CurrentPartIndex : parsed.Parts.First().Index);
        Status = "解析完成，请选择下载规格。"; session.Shell.Notify(Status, InfoBarSeverity.Success); Refresh();
        if (parsed.ThumbnailAvailable)
        {
            try
            {
                var image = await session.RunAsync("media.thumbnail", new { parse_id = parsed.ParseId });
                var decoded = await WindowsShellService.DecodeImageAsync(image.GetProperty("base64").GetString()!);
                if (revision == inputRevision && !session.Closing) Thumbnail = decoded;
            }
            catch (Exception ex) { AppendLog("封面加载失败，解析结果仍可下载。"); FrontendLog.Write(ex); }
        }
    }

    public async Task DownloadAsync(bool retry = false)
    {
        if (video is null) return;
        showResult = false; Percent = 0; Metrics = ""; Status = "正在准备下载…"; Refresh();
        var value = retry
            ? await session.RunAsync("download.retry", new { batch_id = result!.BatchId })
            : await session.RunAsync("download.start", new { parse_id = video.ParseId, part_indices = SelectedParts.Order().ToArray(),
                format_id = SelectedFormat?.FormatId, download_mode = ModeIndex == 0 ? "audio_video" : "audio_mp3",
                credential_mode = CredentialIndex == 0 ? "saved" : "anonymous", download_dir = directory });
        result = Protocol.Read<BatchResult>(value);
        Results.Clear(); OutputFiles.Clear();
        foreach (var p in result.PartResults) Results.Add(p);
        foreach (var file in result.SavedFiles) OutputFiles.Add(file);
        SelectedOutput = OutputFiles.FirstOrDefault();
        showResult = true; Status = ResultSummary;
        session.Shell.Notify(Status, result.Outcome == "completed" ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        Refresh();
        try { session.ApplySettings(Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get"))); }
        catch (Exception ex) { FrontendLog.Write(ex); }
        if (result.PartResults.Any(p => p.Error?.Code is "login_invalid" or "access_403")) await session.Account.ValidateAsync();
    }

    public void CollapseResult() { showResult = false; Refresh(); }
    public void Progress(DownloadProgress p)
    {
        Percent = p.OverallPercent ?? Percent;
        string phase = p.Phase switch { "downloading" => "下载", "merging" => "合并", "converting" => "转换 MP3", "completed" => "完成", "failed" => "失败", "cancelled" => "取消", _ => "准备" };
        Status = p.CancelRequested ? "已请求取消，等待当前文件安全处理结束…" : $"P{p.PartIndex} · {phase}（{p.PartNumber}/{p.PartCount}）";
        Metrics = $"{Percent:0.0}% · {Bytes(p.DownloadedBytes)} / {Bytes(p.TotalBytes ?? p.TotalBytesEstimate)} · {Bytes(p.SpeedBytesPerSecond)}/s · 剩余 {Duration(p.EtaSeconds)}";
        session.Shell.Notify(Status, p.CancelRequested ? InfoBarSeverity.Warning : InfoBarSeverity.Informational);
        Refresh();
    }
    public void AppendLog(string text) { Logs = (Logs + text + Environment.NewLine); if (Logs.Length > 64000) Logs = Logs[^48000..]; }
    private static string Duration(double? seconds) => seconds is null ? "未知" : TimeSpan.FromSeconds(Math.Max(0, Math.Min(seconds.Value, 31536000))).ToString(@"hh\:mm\:ss");
    private static string Bytes(double? bytes) => bytes is null ? "未知" : bytes >= 1048576 ? $"{bytes / 1048576:0.0} MiB" : $"{bytes / 1024:0.0} KiB";
}
