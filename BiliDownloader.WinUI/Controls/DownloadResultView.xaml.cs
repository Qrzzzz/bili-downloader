using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.Controls;

public sealed partial class DownloadResultView : UserControl
{
    public DownloadResultView() => InitializeComponent();
    private async void OpenFile_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFileAsync(App.Session.Download.SelectedOutput));
    private async void OpenFolder_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFolderAsync(App.Session.Download.ResultDirectory));
    private async void Retry_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => App.Session.Download.DownloadAsync(true));
    private void Collapse_Click(object sender, RoutedEventArgs e) => App.Session.Download.CollapseResult();
}
