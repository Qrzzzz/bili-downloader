using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Services;
using BiliDownloader.WinUI.ViewModels;

namespace BiliDownloader.WinUI.Views;

public sealed partial class TasksPage : Page
{
    private TasksViewModel Model => App.Session.Tasks;
    public TasksPage() { InitializeComponent(); DataContext = Model; }
    private async void Page_Loaded(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ReloadAsync);
    private void Page_Unloaded(object sender, RoutedEventArgs e) { }
    private async void Pause_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.TogglePauseAsync);
    private async void Clear_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ClearCompletedAsync);
    private Task Act(object sender, string action, bool reauthorize = false) => App.Session.ExecuteAsync(() => Model.ActAsync((TaskRow)((FrameworkElement)sender).DataContext, action, reauthorize));
    private async void Cancel_Click(object sender, RoutedEventArgs e) => await Act(sender, "cancel");
    private async void Resume_Click(object sender, RoutedEventArgs e) => await Act(sender, "resume");
    private async void Retry_Click(object sender, RoutedEventArgs e) => await Act(sender, "retry");
    private async void Reauthorize_Click(object sender, RoutedEventArgs e) => await Act(sender, "resume", true);
    private async void First_Click(object sender, RoutedEventArgs e) => await Act(sender, "reorder");
    private async void Remove_Click(object sender, RoutedEventArgs e) => await Act(sender, "remove");
    private async void Details_Click(object sender, RoutedEventArgs e)
    {
        var row = (TaskRow)((FrameworkElement)sender).DataContext;
        row.DetailOpen = !row.DetailOpen;
        if (row.DetailOpen) await App.Session.ExecuteAsync(row.LoadDetailsAsync);
    }
    private async void OpenOutput_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(async () =>
    {
        var row = (TaskRow)((FrameworkElement)sender).DataContext;
        await row.LoadDetailsAsync();
        if (row.Files.Count == 1) await WindowsShellService.OpenFileAsync(row.Files[0].Path);
        else
        {
            row.DetailOpen = true;
            if (row.Files.Count == 0) throw new IOException("此任务没有可打开的输出文件，请查看任务详情或日志。");
        }
    });
    private async void OutputFile_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFileAsync(((OutputFileItem)((FrameworkElement)sender).DataContext).Path));
    private bool logDialogOpen;
    private async void Logs_Click(object sender, RoutedEventArgs e)
    {
        if (logDialogOpen) return;
        logDialogOpen = true;
        try
        {
            await App.Session.ExecuteAsync(async () =>
            {
                var row = (TaskRow)((FrameworkElement)sender).DataContext;
                await row.LoadDetailsAsync();
                await CreateLogDialog(row.Logs).ShowAsync();
            });
        }
        finally { logDialogOpen = false; }
    }
    internal ContentDialog CreateLogDialog(string logs)
    {
        var text = new TextBox { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap,
            Height = Math.Clamp(XamlRoot.Size.Height - 260, 80, 320) };
        text.Text = string.IsNullOrWhiteSpace(logs) ? "暂无任务日志。" : logs;
        ScrollViewer.SetVerticalScrollBarVisibility(text, ScrollBarVisibility.Auto);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(text, "此任务的脱敏日志");
        var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = "任务日志",
            Content = text, PrimaryButtonText = "复制日志", CloseButtonText = "关闭", DefaultButton = ContentDialogButton.Close };
        dialog.PrimaryButtonClick += (_, args) =>
        {
            WindowsShellService.Copy(text.Text);
            args.Cancel = true;
        };
        return dialog;
    }
    private async void Folder_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFolderAsync(((TaskRow)((FrameworkElement)sender).DataContext).Directory));
}
