using Microsoft.UI.Xaml;
using Windows.UI.ViewManagement;

namespace BiliDownloader.WinUI.Services;

public sealed class ThemeService : IDisposable
{
    private readonly FrameworkElement root;
    private readonly UISettings settings = new();
    private string preference = "system";
    public ThemeService(FrameworkElement root)
    {
        this.root = root; settings.ColorValuesChanged += SystemThemeChanged;
    }
    public void Apply(string value)
    {
        preference = value;
        // System color is used only to resolve the theme, never to restyle controls.
        var systemBackground = settings.GetColorValue(UIColorType.Background);
        var systemTheme = systemBackground.R + systemBackground.G + systemBackground.B > 3 * 128 ? ElementTheme.Light : ElementTheme.Dark;
        root.RequestedTheme = value switch { "light" => ElementTheme.Light, "dark" => ElementTheme.Dark, _ => systemTheme };
    }
    private void SystemThemeChanged(UISettings sender, object args) => root.DispatcherQueue.TryEnqueue(() => Apply(preference));
    public void Dispose() => settings.ColorValuesChanged -= SystemThemeChanged;
}
