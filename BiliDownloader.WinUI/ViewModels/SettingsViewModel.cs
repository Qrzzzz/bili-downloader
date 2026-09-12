using System.Collections.ObjectModel;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class SettingsViewModel(ApplicationSession session) : ViewModelBase
{
    private string directory = "", diagnosticText = "", updateText = "尚未检查更新。";
    private int themeIndex;
    private int parallelIndex = 1;
    private int downloadModeIndex;
    private bool rememberDownloadPreferences = true;
    private QualityPreference? preferredQuality;
    public ObservableCollection<QualityPreference> QualityOptions { get; } = [new(null), new(4320), new(2160), new(1440), new(1080), new(720), new(480), new(360), new(240)];
    public int DownloadModeIndex { get => downloadModeIndex; set { if (Set(ref downloadModeIndex, value)) Refresh(); } }
    public bool RememberDownloadPreferences { get => rememberDownloadPreferences; set { if (Set(ref rememberDownloadPreferences, value)) Refresh(); } }
    public QualityPreference? PreferredQuality { get => preferredQuality; set { if (Set(ref preferredQuality, value)) Refresh(); } }
    private string CurrentDownloadMode => DownloadModeIndex == 1 ? "audio_mp3" : "audio_video";
    public int ParallelIndex { get => parallelIndex; set { if (Set(ref parallelIndex, value)) Refresh(); } }
    private bool loading, saving;
    private AppSettings? saved;
    public ObservableCollection<DiagnosticItem> Diagnostics { get; } = [];
    public string DownloadDirectory { get => directory; set { if (Set(ref directory, value)) Refresh(); } }
    public int ThemeIndex
    {
        get => themeIndex;
        set
        {
            if (!Set(ref themeIndex, value)) return;
            if (!loading) session.PreviewTheme(CurrentTheme);
            Refresh();
        }
    }
    public string CurrentTheme => ThemeIndex switch { 1 => "light", 2 => "dark", _ => "system" };
    public bool HasChanges => saved is not null && (directory != saved.DownloadDir || CurrentTheme != saved.Theme || ParallelIndex + 1 != saved.MaxParallel
        || RememberDownloadPreferences != saved.RememberDownloadPreferences || CurrentDownloadMode != saved.DownloadMode || PreferredQuality?.Height != saved.PreferredQuality);
    public bool CanManage => session.Available;
    public bool CanEdit => session.Connected && !session.Closing && !saving;
    public bool CanSave => CanEdit && HasChanges && !string.IsNullOrWhiteSpace(directory);
    public bool CanCopyDiagnostics => !string.IsNullOrWhiteSpace(DiagnosticText);
    public string SaveStatus => saving ? "正在保存…" : HasChanges ? "有未保存的更改。" : "设置已保存。";
    public string DiagnosticText { get => diagnosticText; set { if (Set(ref diagnosticText, value)) Refresh(); } }
    public string UpdateText { get => updateText; set => Set(ref updateText, value); }
    public string? ReleaseUrl { get; private set; }

    public void Load(AppSettings settings, bool discardDraft = false)
    {
        var previous = discardDraft ? null : saved;
        loading = true;
        try
        {
            // Merge untouched fields so a directory draft cannot overwrite newer remembered choices.
            if (previous is null || directory == previous.DownloadDir) DownloadDirectory = settings.DownloadDir;
            if (previous is null || ParallelIndex + 1 == previous.MaxParallel) ParallelIndex = settings.MaxParallel - 1;
            if (previous is null || CurrentTheme == previous.Theme) ThemeIndex = settings.Theme switch { "light" => 1, "dark" => 2, _ => 0 };
            if (previous is null || CurrentDownloadMode == previous.DownloadMode) DownloadModeIndex = settings.DownloadMode == "audio_mp3" ? 1 : 0;
            if (previous is null || RememberDownloadPreferences == previous.RememberDownloadPreferences) RememberDownloadPreferences = settings.RememberDownloadPreferences;
            if (previous is null || PreferredQuality?.Height == previous.PreferredQuality)
            {
                var choice = QualityOptions.FirstOrDefault(q => q.Height == settings.PreferredQuality);
                if (choice is null) { choice = new(settings.PreferredQuality); QualityOptions.Add(choice); }
                PreferredQuality = choice;
            }
            saved = settings;
        }
        finally { loading = false; }
        Refresh();
    }
    public async Task SaveAsync()
    {
        if (!CanSave) return;
        saving = true; Refresh();
        try
        {
            var patch = new Dictionary<string, object?>();
            if (directory != saved!.DownloadDir) patch["download_dir"] = directory;
            if (CurrentTheme != saved.Theme) patch["theme"] = CurrentTheme;
            if (ParallelIndex + 1 != saved.MaxParallel) patch["max_parallel"] = ParallelIndex + 1;
            if (RememberDownloadPreferences != saved.RememberDownloadPreferences) patch["remember_download_preferences"] = RememberDownloadPreferences;
            if (CurrentDownloadMode != saved.DownloadMode) patch["download_mode"] = CurrentDownloadMode;
            if (PreferredQuality?.Height != saved.PreferredQuality) patch["preferred_quality"] = PreferredQuality?.Height;
            await session.UpdateSettingsAsync(patch, discardDraft: true);
            session.Shell.Notify("设置已保存。", InfoBarSeverity.Success);
        }
        catch
        {
            if (saved is not null) session.PreviewTheme(saved.Theme);
            throw;
        }
        finally { saving = false; Refresh(); }
    }
    public async Task ReloadAsync()
    {
        if (session.Connected && !session.Closing && !HasChanges && !saving)
            Load(Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get")));
    }
    public async Task DiagnoseAsync()
    {
        if (!CanManage) return;
        var result = Protocol.Read<DiagnosticReport>(await session.RunAsync("diagnostics.run"));
        Diagnostics.Clear(); foreach (var item in result.Items) Diagnostics.Add(item);
        DiagnosticText = result.Text;
        session.Shell.Notify("环境诊断完成。", InfoBarSeverity.Success);
    }
    public async Task CheckUpdateAsync()
    {
        if (!CanManage) return;
        UpdateText = "正在检查更新…";
        try
        {
            var result = Protocol.Read<UpdateResult>(await session.RunAsync("updates.check"));
            UpdateText = result.Message; ReleaseUrl = result.ReleaseUrl;
        }
        catch { UpdateText = "检查失败，请稍后重试。"; throw; }
        finally { Refresh(); }
    }
}
