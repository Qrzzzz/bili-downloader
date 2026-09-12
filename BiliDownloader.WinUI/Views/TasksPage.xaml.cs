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
    private async void Details_Expanding(Expander sender, ExpanderExpandingEventArgs args)
    {
        var row = (TaskRow)sender.DataContext; row.DetailOpen = true;
        await App.Session.ExecuteAsync(row.LoadDetailsAsync);
    }
    private void Details_Collapsed(Expander sender, ExpanderCollapsedEventArgs args) => ((TaskRow)sender.DataContext).DetailOpen = false;
    private async void File_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFileAsync(((TaskRow)((FrameworkElement)sender).DataContext).SelectedFile?.Path));
    private async void Folder_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFolderAsync(((TaskRow)((FrameworkElement)sender).DataContext).Directory));
}
