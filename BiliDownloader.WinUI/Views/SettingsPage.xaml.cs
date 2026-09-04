using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.Views;

public sealed partial class SettingsPage : Page
{
    public SettingsPage() { InitializeComponent(); DataContext = App.Session.Settings; NavigationCacheMode = NavigationCacheMode.Required; }
    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await App.Session.ExecuteAsync(App.Session.Settings.ReloadAsync);
    }
    private async void Browse_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(async () => { var path = await WindowsShellService.PickFolderAsync(); if (path is not null) App.Session.Settings.DownloadDirectory = path; });
    private async void Save_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.Settings.SaveAsync);
    private async void Diagnose_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.Settings.DiagnoseAsync);
    private void Copy_Click(object sender, RoutedEventArgs e) => WindowsShellService.Copy(App.Session.Settings.DiagnosticText);
    private async void Logs_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFolderAsync(FrontendLog.DirectoryPath));
    private async void Update_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.Settings.CheckUpdateAsync);
    private async void Release_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenReleaseAsync(App.Session.Settings.ReleaseUrl));
}
