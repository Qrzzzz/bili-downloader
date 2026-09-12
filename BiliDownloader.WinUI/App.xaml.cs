using Microsoft.UI.Xaml;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI;

public partial class App : Application
{
    public const string AppVersion = "2.12";
    public static ApplicationSession Session { get; private set; } = null!;
    public static MainWindow MainWindow { get; private set; } = null!;
    public App()
    {
        UnhandledException += (_, e) => FrontendLog.Write(e.Exception);
        InitializeComponent();
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        Session = new ApplicationSession();
        MainWindow = new MainWindow();
        MainWindow.Activate();
    }
}
