using System.Text.Json;
using System.Reflection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Storage;
using System.Runtime.InteropServices.WindowsRuntime;

namespace BiliDownloader.WinUI.Services;

public static class NativeAcceptance
{
    public static async Task CaptureAsync(Window window, FrameworkElement root, TitleBar title, NavigationView navigation, Frame frame)
    {
        string output = Environment.GetEnvironmentVariable("BILI_ACCEPTANCE_OUTPUT") ?? Path.Combine(Path.GetTempPath(), "bili-winui-acceptance");
        Directory.CreateDirectory(output);
        var controls = new List<object>();
        void Visit(DependencyObject node)
        {
            if (node is FrameworkElement element)
            {
                var peer = FrameworkElementAutomationPeer.CreatePeerForElement(element);
                if (peer is not null) controls.Add(new { type = element.GetType().FullName, automation_id = AutomationProperties.GetAutomationId(element), name = peer.GetName(), control_type = peer.GetAutomationControlType().ToString() });
            }
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++) Visit(VisualTreeHelper.GetChild(node, i));
        }
        await Task.Delay(300);
        App.Session.Download.Input = "invalid";
        await App.Session.ExecuteAsync(App.Session.Download.ParseAsync);
        bool invalidInputHandled = App.Session.Shell.Severity == InfoBarSeverity.Error && !App.Session.Download.CanDownload;
        App.Session.Download.Input = "";
        App.Session.Shell.Notify("原生验收：跨页状态保留");
        Visit(root);
        var themes = new List<string>();
        ElementTheme original = root.RequestedTheme;
        foreach (var theme in new[] { ElementTheme.Light, ElementTheme.Dark })
        {
            root.RequestedTheme = theme;
            await Task.Delay(150);
            themes.Add(root.ActualTheme.ToString());
            var bitmap = new RenderTargetBitmap();
            await bitmap.RenderAsync(root);
            var pixels = await bitmap.GetPixelsAsync();
            var file = await StorageFile.GetFileFromPathAsync(CreateFile(Path.Combine(output, $"download-{theme.ToString().ToLowerInvariant()}.png")));
            using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
            var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
            encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied, (uint)bitmap.PixelWidth, (uint)bitmap.PixelHeight, 96, 96, pixels.ToArray());
            await encoder.FlushAsync();
        }
        root.RequestedTheme = original;
        navigation.SelectedItem = navigation.SettingsItem;
        await Task.Delay(100);
        string? settingsPage = frame.Content?.GetType().FullName;
        bool statusPreserved = App.Session.Shell.Message == "原生验收：跨页状态保留";
        navigation.SelectedItem = navigation.MenuItems[1];
        await Task.Delay(100);
        string? accountPage = frame.Content?.GetType().FullName;
        navigation.SelectedItem = navigation.MenuItems[0];
        var initialSize = window.AppWindow.Size;
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(640 * root.XamlRoot.RasterizationScale), (int)(480 * root.XamlRoot.RasterizationScale)));
        await Task.Delay(200);
        string narrowNavigationMode = navigation.DisplayMode.ToString();
        window.AppWindow.Resize(initialSize);
        var evidence = new { version = App.AppVersion, backend_connected = App.Session.Connected,
            git_commit = typeof(App).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().FirstOrDefault(a => a.Key == "GitCommit")?.Value,
            build_dirty = typeof(App).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().FirstOrDefault(a => a.Key == "BuildDirty")?.Value,
            window_type = window.GetType().BaseType?.FullName, backdrop_type = window.SystemBackdrop?.GetType().FullName,
            titlebar_type = title.GetType().FullName, navigation_type = navigation.GetType().FullName,
            extends_content_into_titlebar = window.ExtendsContentIntoTitleBar, settings_page = settingsPage,
            account_page = accountPage, invalid_input_handled = invalidInputHandled, status_preserved = statusPreserved,
            narrow_navigation_mode = narrowNavigationMode, caption_theme = window.AppWindow.TitleBar.PreferredTheme.ToString(),
            rasterization_scale = root.XamlRoot.RasterizationScale, themes, controls };
        await File.WriteAllTextAsync(Path.Combine(output, "native-evidence.json"), JsonSerializer.Serialize(evidence, new JsonSerializerOptions { WriteIndented = true }));
        if (!App.Session.Connected || !invalidInputHandled || !statusPreserved || narrowNavigationMode == "Expanded") Environment.ExitCode = 2;
    }
    private static string CreateFile(string path) { File.WriteAllBytes(path, []); return path; }
}
