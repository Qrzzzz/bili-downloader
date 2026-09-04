using Microsoft.UI.Xaml.Media.Imaging;
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
        return (await picker.PickSingleFolderAsync())?.Path;
    }
    public static async Task OpenFileAsync(string? path)
    {
        if (path is null || !File.Exists(path)) throw new FileNotFoundException("输出文件已移动或删除。");
        await Launcher.LaunchFileAsync(await StorageFile.GetFileFromPathAsync(Path.GetFullPath(path)));
    }
    public static async Task OpenFolderAsync(string path)
    {
        if (!Directory.Exists(path)) throw new DirectoryNotFoundException("目录不存在。");
        await Launcher.LaunchFolderAsync(await StorageFolder.GetFolderFromPathAsync(Path.GetFullPath(path)));
    }
    public static async Task OpenReleaseAsync(string? url)
    {
        if (url is null) return;
        var uri = new Uri(url);
        if (uri.Scheme != "https" || uri.Host != "github.com" || !uri.AbsolutePath.StartsWith("/Qrzzzz/bili-downloader/releases/", StringComparison.Ordinal))
            throw new InvalidDataException("无效的发行页面地址。");
        await Launcher.LaunchUriAsync(uri);
    }
    public static void Copy(string text)
    {
        var package = new DataPackage(); package.SetText(text); Clipboard.SetContent(package);
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
