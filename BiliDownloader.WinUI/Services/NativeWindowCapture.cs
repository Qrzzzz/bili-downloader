using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Windows.Graphics.Imaging;
using Windows.Storage;

namespace BiliDownloader.WinUI.Services;

// Captures only this application's HWND during explicit native acceptance.
internal static class NativeWindowCapture
{
    [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] private struct BitmapInfo
    {
        public uint Size; public int Width, Height; public ushort Planes, BitCount;
        public uint Compression, SizeImage; public int XPelsPerMeter, YPelsPerMeter;
        public uint ClrUsed, ClrImportant;
    }
    private delegate bool EnumWindowCallback(nint hwnd, nint state);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint hwnd, out Rect rect);
    [DllImport("user32.dll")] private static extern bool PrintWindow(nint hwnd, nint dc, uint flags);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowCallback callback, nint state);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint hwnd, out uint pid);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint hwnd);
    [DllImport("gdi32.dll")] private static extern nint CreateCompatibleDC(nint dc);
    [DllImport("gdi32.dll")] private static extern nint CreateDIBSection(nint dc, ref BitmapInfo info, uint usage, out nint bits, nint section, uint offset);
    [DllImport("gdi32.dll")] private static extern nint SelectObject(nint dc, nint obj);
    [DllImport("gdi32.dll")] private static extern bool DeleteObject(nint obj);
    [DllImport("gdi32.dll")] private static extern bool DeleteDC(nint dc);

    internal static int VisibleWindowCount()
    {
        int count = 0;
        EnumWindows((hwnd, _) => { GetWindowThreadProcessId(hwnd, out uint pid); if (pid == Environment.ProcessId && IsWindowVisible(hwnd)) count++; return true; }, 0);
        return count;
    }
    internal static async Task CaptureAsync(Window window, string path)
    {
        nint hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        if (!GetWindowRect(hwnd, out var rect)) throw new InvalidOperationException("Cannot read native window bounds.");
        int width = rect.Right - rect.Left, height = rect.Bottom - rect.Top;
        var info = new BitmapInfo { Size = (uint)Marshal.SizeOf<BitmapInfo>(), Width = width, Height = -height, Planes = 1, BitCount = 32 };
        nint dc = CreateCompatibleDC(0), bitmap = CreateDIBSection(dc, ref info, 0, out nint pixels, 0, 0);
        if (dc == 0 || bitmap == 0) { if (dc != 0) DeleteDC(dc); throw new InvalidOperationException("Cannot allocate native screenshot."); }
        nint previous = SelectObject(dc, bitmap);
        byte[] data = new byte[checked(width * height * 4)];
        try
        {
            if (!PrintWindow(hwnd, dc, 2)) throw new InvalidOperationException("Native window capture failed.");
            Marshal.Copy(pixels, data, 0, data.Length);
        }
        finally { SelectObject(dc, previous); DeleteObject(bitmap); DeleteDC(dc); }
        File.WriteAllBytes(path, []);
        var file = await StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, (uint)width, (uint)height, 96, 96, data);
        await encoder.FlushAsync();
    }

    // Explicit acceptance only: GPU/occlusion can make PrintWindow return a
    // blank client area. Keep an independently rendered XAML surface as well.
    internal static async Task CaptureContentAsync(FrameworkElement root, string path)
    {
        var target = new Microsoft.UI.Xaml.Media.Imaging.RenderTargetBitmap();
        await target.RenderAsync(root);
        var pixels = await target.GetPixelsAsync();
        byte[] data = new byte[pixels.Length];
        using (var reader = Windows.Storage.Streams.DataReader.FromBuffer(pixels)) reader.ReadBytes(data);
        File.WriteAllBytes(path, []);
        var file = await StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore,
            (uint)target.PixelWidth, (uint)target.PixelHeight, 96, 96, data);
        await encoder.FlushAsync();
    }
}
