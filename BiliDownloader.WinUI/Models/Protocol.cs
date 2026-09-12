using System.Text.Json;

namespace BiliDownloader.WinUI.Models;

public static class Protocol
{
    public const int Version = 2;
    public const int MaximumMessageBytes = 16 * 1024 * 1024;
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = false,
        MaxDepth = 32
    };
    public static T Read<T>(JsonElement value) => value.Deserialize<T>(Json) ?? throw new InvalidDataException("后端返回空数据。");
}
public sealed record AppSettings(string DownloadDir, string Theme = "system", int SchemaVersion = 1, int MaxParallel = 2,
    bool RememberDownloadPreferences = true, string DownloadMode = "audio_video", int? PreferredQuality = null);
public sealed record QualityPreference(int? Height)
{
    public string Label => Height is null ? "最高可用画质" : $"{Height}p（严格匹配）";
    public override string ToString() => Label;
}
public sealed record LoginStatus(string Code, string Text, string? Generation);
public sealed record Hello(int ProtocolVersion, string BackendVersion, string SessionId, bool SafeMode,
                          AppSettings Settings, LoginStatus Status, string[] ConfigDiagnostics);
public sealed record ErrorInfo(string Code, string Message, bool Retryable, string Detail);
public sealed class BackendException(ErrorInfo error) : Exception(error.Message)
{
    public ErrorInfo Error { get; } = error;
}
public sealed record FormatChoice(string FormatId, string Label, int? Height, string Policy)
{
    public override string ToString() => Label;
}
public sealed record VideoPart(int Index, string Title, double? DurationSeconds, string Id)
{
    public string Label => $"P{Index} · {Title}";
    public override string ToString() => Label;
}
public sealed record VideoInfo(string ParseId, int InputRevision, string Title, string Uploader,
                              double? DurationSeconds, string RawId, int CurrentPartIndex,
                              bool ThumbnailAvailable, VideoPart[] Parts, FormatChoice[] Formats);
public sealed record DownloadProgress(string Phase, int? PartIndex, int? PartNumber, int? PartCount,
                                     double? PartPercent, double? OverallPercent, double? DownloadedBytes,
                                     double? TotalBytes, double? TotalBytesEstimate, double? SpeedBytesPerSecond,
                                     double? EtaSeconds, bool CancelRequested);
public sealed record PartResult(int Index, string Title, string Status, string[] SavedFiles, ErrorInfo? Error)
{
    public string Label => $"P{Index} · {Title}";
    public string StatusText => Status switch { "completed" => "已完成", "cancelled" => "已取消", _ => "失败" };
    public string Detail => Error?.Message ?? string.Join(Environment.NewLine, SavedFiles);
    public override string ToString() => $"{Label}，{StatusText}，{Detail}";
}
public sealed record BatchResult(string BatchId, string Outcome, string[] SavedFiles, bool RetryAllowed, PartResult[] PartResults, string OutputDir);
public sealed record DiagnosticItem(string Name, string Status, string Summary, string Detail)
{
    public string Label => $"{Name}：{Summary}";
    public override string ToString() => Label;
}
public sealed record DiagnosticReport(DiagnosticItem[] Items, string Text);
public sealed record UpdateResult(string CurrentVersion, string? LatestVersion, string? ReleaseUrl, bool UpdateAvailable, string Message);
public sealed record BackendEvent(string OperationId, long Sequence, string Name, JsonElement Data);
public sealed record DownloadTask(string TaskId, string AttemptId, string Title, string FormatLabel,
    int PartCount, string CredentialMode, string OutputDir, string State, string Message, long Revision,
    double CreatedAt, double Position, bool Foreign, BatchResult? Result, string Logs, DownloadProgress? Progress);
public sealed record TaskSnapshot(bool Paused, int MaxParallel, DownloadTask[] Tasks, long Revision);
public sealed record TaskChange(DownloadTask Task, long Revision, bool Paused = false);
public sealed record TaskReply(DownloadTask Task, bool Duplicate = false);
public sealed record BatchParseItem(string Input, string Message, VideoInfo? Video);
public sealed record BatchParseResult(BatchParseItem[] Items);
