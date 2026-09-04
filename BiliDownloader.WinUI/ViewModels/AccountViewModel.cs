using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.Services;

namespace BiliDownloader.WinUI.ViewModels;

public sealed class AccountViewModel(ApplicationSession session) : ViewModelBase
{
    private string status = "正在读取登录状态…", qrStatus = "";
    private BitmapImage? qrImage;
    private int generation;
    private int imageRevision;
    private bool refreshing;
    private BackendEvent? pendingImage, pendingState;
    public bool SafeMode { get; set; }
    public string Status { get => status; set => Set(ref status, value); }
    public string QrStatus { get => qrStatus; set => Set(ref qrStatus, value); }
    public BitmapImage? QrImage { get => qrImage; private set => Set(ref qrImage, value); }
    public bool CanLogin => session.Available && !SafeMode;
    public bool CanManage => session.Available;
    public bool IsLoggingIn => session.Busy && session.ActiveMethod == "auth.qr.start";
    public bool CanRefresh => IsLoggingIn && !refreshing;
    public Visibility QrVisibility => IsLoggingIn ? Visibility.Visible : Visibility.Collapsed;
    public void ClearQr() { imageRevision++; refreshing = true; pendingImage = pendingState = null; QrImage = null; QrStatus = "正在刷新二维码…"; Refresh(); }
    public void CancelRefresh() { refreshing = false; pendingImage = pendingState = null; Refresh(); }
    public void ExpectGeneration(int minimum)
    {
        generation = Math.Max(generation, minimum); refreshing = false;
        var state = pendingState; var image = pendingImage; pendingImage = pendingState = null;
        if (state is not null) Handle(state);
        if (image is not null) Handle(image);
        Refresh();
    }

    public async Task LoginAsync()
    {
        generation = 0; imageRevision++; CancelRefresh(); QrImage = null; QrStatus = "正在生成二维码…";
        System.Text.Json.JsonElement result;
        try { result = await session.RunAsync("auth.qr.start", new { consent = true }); }
        finally { imageRevision++; CancelRefresh(); QrImage = null; }
        Status = Protocol.Read<LoginStatus>(result.GetProperty("status")).Text;
        string code = result.GetProperty("code").GetString()!;
        string text = code == "success" ? "登录成功，凭据已安全保存在本机。" : result.GetProperty("friendly").GetString() ?? "扫码已结束。";
        session.Shell.Notify(text, code == "success" ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        if (code == "success") session.Download.Invalidate();
        Refresh();
    }
    public async Task ValidateAsync()
    {
        Status = "正在验证登录状态…";
        var result = Protocol.Read<LoginStatus>(await session.RunAsync("session.validate"));
        Status = result.Text;
        if (result.Code is "invalid" or "none") session.Download.Invalidate();
    }
    public async Task ClearAsync()
    {
        var result = await session.RunAsync("session.clear");
        Status = Protocol.Read<LoginStatus>(result.GetProperty("status")).Text;
        bool ok = result.GetProperty("ok").GetBoolean();
        session.Shell.Notify(ok ? "本机登录态已清除。" : "部分登录态未能清除，请查看诊断。", ok ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        session.Download.Invalidate();
    }
    public async void Handle(BackendEvent e)
    {
        if (refreshing)
        {
            if (e.Name == "auth.qr.image") pendingImage = e; else pendingState = e;
            return;
        }
        int next = e.Data.GetProperty("generation").GetInt32();
        if (!IsLoggingIn || next < generation) return;
        generation = next;
        if (e.Name == "auth.qr.state") { QrStatus = e.Data.GetProperty("text").GetString()!; return; }
        try
        {
            int revision = imageRevision;
            var image = await WindowsShellService.DecodeImageAsync(e.Data.GetProperty("base64").GetString()!);
            if (revision == imageRevision && next == generation && IsLoggingIn) QrImage = image;
        }
        catch (Exception ex) { session.Report(ex); }
    }
}
