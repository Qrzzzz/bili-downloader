using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics.Imaging;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.Storage.Streams;
using Windows.System;

namespace BiliDownloader.WinUI.Services;

public static class WindowsShellService
{
    public static async Task<string?> PickFolderAsync()
    {
        var picker = new FolderPicker();
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindow));
        string? path = (await picker.PickSingleFolderAsync())?.Path;
        App.Session.Shell.Notify(path is null ? "已取消选择，保存目录未更改。" : "已选择保存目录。",
            path is null ? InfoBarSeverity.Informational : InfoBarSeverity.Success);
        return path;
    }
    public static async Task OpenFileAsync(string? path)
    {
        if (path is null || !File.Exists(path)) throw new FileNotFoundException("输出文件已移动或删除。");
        if (!await Launcher.LaunchFileAsync(await StorageFile.GetFileFromPathAsync(Path.GetFullPath(path))))
            throw new IOException("系统未能打开文件，请检查默认打开方式。位于：" + path);
        App.Session.Shell.Notify("已交给系统打开文件。", InfoBarSeverity.Success);
    }
    public static async Task OpenFolderAsync(string path)
    {
        if (!Directory.Exists(path)) throw new DirectoryNotFoundException("目录不存在。");
        if (!await Launcher.LaunchFolderAsync(await StorageFolder.GetFolderFromPathAsync(Path.GetFullPath(path))))
            throw new IOException("系统未能打开目录，请在资源管理器中手动打开。位于：" + path);
        App.Session.Shell.Notify("已打开文件夹。", InfoBarSeverity.Success);
    }
    public static async Task OpenReleaseAsync(string? url)
    {
        url ??= "https://github.com/Qrzzzz/bili-downloader/releases/latest";
        var uri = new Uri(url);
        if (uri.Scheme != "https" || uri.Host != "github.com" || !uri.AbsolutePath.StartsWith("/Qrzzzz/bili-downloader/releases/", StringComparison.Ordinal))
            throw new InvalidDataException("无效的发行页面地址。");
        if (!await Launcher.LaunchUriAsync(uri)) throw new IOException("系统未能打开发行页面，请检查默认浏览器设置。");
        App.Session.Shell.Notify("已交给浏览器打开发行页面。", InfoBarSeverity.Success);
    }
    public static void Copy(string text)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(text))
            {
                App.Session.Shell.Notify("没有可复制的内容。", InfoBarSeverity.Warning); return;
            }
            var package = new DataPackage(); package.SetText(text); Clipboard.SetContent(package);
            App.Session.Shell.Notify("已复制到剪贴板。", InfoBarSeverity.Success);
        }
        catch (Exception ex) { App.Session.Report(ex); }
    }
    public static async Task<BitmapImage?> DecodeImageAsync(string base64)
    {
        if (base64.Length == 0) return null;
        if (base64.Length > 8 * 1024 * 1024) throw new InvalidDataException("图片超过大小限制。");
        byte[] bytes = Convert.FromBase64String(base64);
        using var stream = new InMemoryRandomAccessStream();
        using (var writer = new DataWriter(stream.GetOutputStreamAt(0)))
        {
            writer.WriteBytes(bytes); await writer.StoreAsync(); await writer.FlushAsync();
        }
        stream.Seek(0);
        var decoder = await BitmapDecoder.CreateAsync(stream);
        if ((ulong)decoder.PixelWidth * decoder.PixelHeight > 32000000) throw new InvalidDataException("图片尺寸超过限制。");
        stream.Seek(0);
        var image = new BitmapImage { DecodePixelWidth = 1200 };
        await image.SetSourceAsync(stream);
        return image;
    }
}
