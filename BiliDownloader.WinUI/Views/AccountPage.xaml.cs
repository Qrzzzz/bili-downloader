using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace BiliDownloader.WinUI.Views;

public sealed partial class AccountPage : Page
{
    public AccountPage() { InitializeComponent(); DataContext = App.Session.Account; NavigationCacheMode = NavigationCacheMode.Required; }
    private async void Login_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = "在本机保存登录凭据",
            Content = "登录凭据将使用 Windows DPAPI 加密保存，供解析和下载使用。你可以随时在账号页清除登录态。是否开始扫码？",
            PrimaryButtonText = "开始扫码", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary) await App.Session.ExecuteAsync(App.Session.Account.LoginAsync);
    }
    private async void Validate_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.Account.ValidateAsync);
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.RefreshQrAsync);
    private async void Cancel_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(App.Session.CancelAsync);
    private async void Clear_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = "清除本机登录态？", Content = "清除后，使用账号权限下载前需要重新扫码。已下载的文件不受影响。",
            PrimaryButtonText = "清除", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary) await App.Session.ExecuteAsync(App.Session.Account.ClearAsync);
    }
}
