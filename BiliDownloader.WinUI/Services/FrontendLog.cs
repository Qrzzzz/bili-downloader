namespace BiliDownloader.WinUI.Services;

public static class FrontendLog
{
    private static readonly object Gate = new();
    public static string DirectoryPath => Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BiliDownloader", "logs");
    public static void Write(Exception exception)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(DirectoryPath);
                string path = Path.Combine(DirectoryPath, "frontend.log");
                if (File.Exists(path) && new FileInfo(path).Length > 1024 * 1024) File.Move(path, path + ".1", true);
                // Never log request payloads or arbitrary exception messages containing secrets.
                File.AppendAllText(path, $"{DateTimeOffset.Now:O} {exception.GetType().FullName} HRESULT=0x{exception.HResult:X8}\n{exception.StackTrace}\n");
            }
        }
        catch { }
    }
}
