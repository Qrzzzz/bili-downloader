using System.Collections.ObjectModel;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class SettingsViewModel(ApplicationSession session) : ViewModelBase
{
    private string directory = "", diagnosticText = "", updateText = "尚未检查更新。";
    private int themeIndex;
    private AppSettings? saved;
    public ObservableCollection<DiagnosticItem> Diagnostics { get; } = [];
    public string DownloadDirectory { get => directory; set => Set(ref directory, value); }
    public int ThemeIndex { get => themeIndex; set => Set(ref themeIndex, value); }
    public bool CanManage => session.Available;
    public bool CanSave => session.Connected && !session.Closing;
    public string DiagnosticText { get => diagnosticText; set => Set(ref diagnosticText, value); }
    public string UpdateText { get => updateText; set => Set(ref updateText, value); }
    public string? ReleaseUrl { get; private set; }
    public void Load(AppSettings settings)
    {
        saved = settings; DownloadDirectory = settings.DownloadDir;
        ThemeIndex = settings.Theme switch { "light" => 1, "dark" => 2, _ => 0 };
    }
    public async Task SaveAsync()
    {
        try
        {
            var settings = Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.update", new { download_dir = directory, theme = ThemeIndex switch { 1 => "light", 2 => "dark", _ => "system" } }));
            session.ApplySettings(settings); session.Shell.Notify("设置已保存。", InfoBarSeverity.Success);
        }
        catch { if (saved is not null) Load(saved); throw; }
    }
    public async Task ReloadAsync()
    {
        if (session.Connected && !session.Closing)
            Load(Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get")));
    }
    public async Task DiagnoseAsync()
    {
        var result = Protocol.Read<DiagnosticReport>(await session.RunAsync("diagnostics.run"));
        Diagnostics.Clear(); foreach (var item in result.Items) Diagnostics.Add(item);
        DiagnosticText = result.Text;
        session.Shell.Notify("环境诊断完成。", InfoBarSeverity.Success);
    }
    public async Task CheckUpdateAsync()
    {
        var result = Protocol.Read<UpdateResult>(await session.RunAsync("updates.check"));
        UpdateText = result.Message; ReleaseUrl = result.ReleaseUrl; Refresh();
    }
}
