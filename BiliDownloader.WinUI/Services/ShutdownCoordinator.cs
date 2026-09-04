using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;

namespace BiliDownloader.WinUI.Services;

public sealed class ShutdownCoordinator
{
    private readonly Window window;
    private readonly ApplicationSession session;
    private bool requested, ready;
    public ShutdownCoordinator(Window window, ApplicationSession session)
    {
        this.window = window; this.session = session;
        window.AppWindow.Closing += Closing;
    }
    private async void Closing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (ready) return;
        args.Cancel = true;
        await RequestCloseAsync();
    }
    public async Task RequestCloseAsync()
    {
        if (requested) return;
        requested = true;
        try { await session.ShutdownAsync(); ready = true; window.Close(); }
        catch (Exception ex) { requested = false; session.Report(ex); }
    }
}
