using System.Collections.ObjectModel;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class SettingsViewModel(ApplicationSession session) : ViewModelBase
{
    private string directory = "", diagnosticText = "", updateText = "尚未检查更新。";
    private int themeIndex;
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
    public bool HasChanges => saved is not null && (directory != saved.DownloadDir || CurrentTheme != saved.Theme);
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
        bool keepDraft = HasChanges && !discardDraft;
        saved = settings;
        if (!keepDraft)
        {
            loading = true;
            try
            {
                DownloadDirectory = settings.DownloadDir;
                ThemeIndex = settings.Theme switch { "light" => 1, "dark" => 2, _ => 0 };
            }
            finally { loading = false; }
        }
        Refresh();
    }
    public async Task SaveAsync()
    {
        if (!CanSave) return;
        saving = true; Refresh();
        try
        {
            var settings = Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.update", new { download_dir = directory, theme = CurrentTheme }));
            session.ApplySettings(settings, discardDraft: true);
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
