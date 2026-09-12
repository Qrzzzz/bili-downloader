using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Windowing;
using BiliDownloader.WinUI.Services;
using BiliDownloader.WinUI.Views;
using Windows.Graphics;

namespace BiliDownloader.WinUI;

public sealed partial class MainWindow : Microsoft.UI.Xaml.Window
{
    private bool loaded;
    private readonly ShutdownCoordinator shutdown;
    private readonly ThemeService theme;
    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        Root.ActualThemeChanged += (_, _) => UpdateCaptionTheme();
        UpdateCaptionTheme();
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
        double scale = GetDpiForWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)) / 96.0;
        var workArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Nearest).WorkArea;
        int width = Math.Min((int)(1080 * scale), workArea.Width);
        int height = Math.Min((int)(820 * scale), workArea.Height);
        AppWindow.MoveAndResize(new RectInt32(workArea.X + (workArea.Width - width) / 2, workArea.Y + (workArea.Height - height) / 2, width, height));
        Root.DataContext = App.Session;
        App.Session.Dispatcher = DispatcherQueue;
        theme = new ThemeService(Root);
        App.Session.ThemeRequested += theme.Apply;
        App.Session.TasksRequested += () => Navigation.SelectedItem = Navigation.MenuItems.OfType<NavigationViewItem>().First(i => (string)i.Tag == "tasks");
        shutdown = new ShutdownCoordinator(this, App.Session);
        Closed += (_, _) => { App.Session.ThemeRequested -= theme.Apply; theme.Dispose(); };
        Navigation.SelectedItem = Navigation.MenuItems[0];
    }
    private async void Root_Loaded(object sender, RoutedEventArgs e)
    {
        if (loaded) return;
        loaded = true;
        Root.XamlRoot.Changed += (_, _) => UpdateMinimumSize();
        UpdateMinimumSize();
        if (Navigation.SettingsItem is NavigationViewItem settingsItem)
        {
            settingsItem.Content = "设置";
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(settingsItem, "设置");
        }
        await App.Session.InitializeAsync();
        if (Environment.GetCommandLineArgs().Contains("--self-test"))
        {
            try { await NativeAcceptance.CaptureAsync(this, Root, AppTitleBar, Navigation, ContentFrame); }
            catch (Exception ex) { Environment.ExitCode = 2; App.Session.Report(ex); }
            finally { await shutdown.RequestCloseAsync(); }
        }
    }
    private void Navigation_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (App.Session.Closing) return;
        Type target = args.IsSettingsSelected ? typeof(SettingsPage) : (args.SelectedItemContainer?.Tag as string) switch
        { "account" => typeof(AccountPage), "tasks" => typeof(TasksPage), _ => typeof(DownloadPage) };
        if (ContentFrame.CurrentSourcePageType != target) ContentFrame.Navigate(target);
    }
    private void TitleBar_PaneToggleRequested(TitleBar sender, object args) => Navigation.IsPaneOpen = !Navigation.IsPaneOpen;
    private void UpdateCaptionTheme() => AppWindow.TitleBar.PreferredTheme = Root.ActualTheme == ElementTheme.Dark ? TitleBarTheme.Dark : TitleBarTheme.Light;
    private void UpdateMinimumSize()
    {
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            var workArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Nearest).WorkArea;
            double scale = Root.XamlRoot.RasterizationScale;
            presenter.PreferredMinimumWidth = Math.Min((int)(640 * scale), workArea.Width);
            presenter.PreferredMinimumHeight = Math.Min((int)(480 * scale), workArea.Height);
        }
    }
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint hwnd);
}
