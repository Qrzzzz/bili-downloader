using Microsoft.UI.Xaml.Controls;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class ShellViewModel : ViewModelBase
{
    private string message = "正在连接下载服务…";
    private bool isOpen = true;
    private InfoBarSeverity severity = InfoBarSeverity.Informational;
    public string Message { get => message; set => Set(ref message, value); }
    public bool IsOpen { get => isOpen; set => Set(ref isOpen, value); }
    public InfoBarSeverity Severity { get => severity; set => Set(ref severity, value); }
    public void Notify(string text, InfoBarSeverity kind = InfoBarSeverity.Informational)
    {
        Message = text; Severity = kind; IsOpen = true;
    }
}
