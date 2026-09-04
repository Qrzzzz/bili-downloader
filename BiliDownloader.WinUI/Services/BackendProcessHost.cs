using System.Diagnostics;

namespace BiliDownloader.WinUI.Services;

public static class BackendProcessHost
{
    public static Process Start()
    {
        string root = AppContext.BaseDirectory;
        string executable = Path.Combine(root, "BiliDownloader.Backend.exe");
        var start = new ProcessStartInfo
        {
            FileName = executable, WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new System.Text.UTF8Encoding(false),
            StandardOutputEncoding = new System.Text.UTF8Encoding(false),
            StandardErrorEncoding = new System.Text.UTF8Encoding(false)
        };
        // Explicit source-development opt-in; never search PATH or the working directory.
        if (!File.Exists(executable))
        {
            var python = Environment.GetEnvironmentVariable("BILI_BACKEND_PYTHON");
            var source = Environment.GetEnvironmentVariable("BILI_BACKEND_SOURCE");
            if (string.IsNullOrWhiteSpace(python) || string.IsNullOrWhiteSpace(source) ||
                !Path.IsPathFullyQualified(python) || !Path.IsPathFullyQualified(source) ||
                !File.Exists(python) || !File.Exists(Path.Combine(source, "app", "backend", "__main__.py")))
                throw new FileNotFoundException("未找到配套 Python 后端。请完整解压 v2.5 发行包。", executable);
            start.FileName = python;
            start.WorkingDirectory = source;
            start.ArgumentList.Add("-m"); start.ArgumentList.Add("app.backend");
        }
        start.Environment["PYTHONUTF8"] = "1";
        return Process.Start(start) ?? throw new IOException("无法启动下载后端。");
    }
}
