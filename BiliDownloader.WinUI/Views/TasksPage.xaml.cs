using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using BiliDownloader.WinUI.Services;
using BiliDownloader.WinUI.ViewModels;

namespace BiliDownloader.WinUI.Views;

public sealed partial class TasksPage : Page
{
    private TasksViewModel Model => App.Session.Tasks;
    public TasksPage() { InitializeComponent(); DataContext = Model; }
    private async void Page_Loaded(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ReloadAsync);
    private void Page_Unloaded(object sender, RoutedEventArgs e) { }
    private async void Pause_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.TogglePauseAsync);
    private async void Clear_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(Model.ClearCompletedAsync);
    private Task Act(object sender, string action, bool reauthorize = false) => App.Session.ExecuteAsync(() => Model.ActAsync((TaskRow)((FrameworkElement)sender).DataContext, action, reauthorize));
    private async void Cancel_Click(object sender, RoutedEventArgs e) => await Act(sender, "cancel");
    private async void Resume_Click(object sender, RoutedEventArgs e) => await Act(sender, "resume");
    private async void Retry_Click(object sender, RoutedEventArgs e) => await Act(sender, "retry");
    private async void Reauthorize_Click(object sender, RoutedEventArgs e) => await Act(sender, "resume", true);
    private async void First_Click(object sender, RoutedEventArgs e) => await Act(sender, "reorder");
    private async void Remove_Click(object sender, RoutedEventArgs e) => await Act(sender, "remove");
    private bool stagingDialogOpen;
    private async void Staging_Click(object sender, RoutedEventArgs e)
    {
        if (stagingDialogOpen) return;
        stagingDialogOpen = true;
        try
        {
            await App.Session.ExecuteAsync(async () =>
            {
                var scan = Models.Protocol.Read<Models.StagingScan>(await App.Session.Client.RequestAsync("staging.scan"));
                await CreateStagingDialog(scan).ShowAsync();
            });
        }
        finally { stagingDialogOpen = false; }
    }
    internal ContentDialog CreateStagingDialog(Models.StagingScan scan)
    {
        var summary = new TextBlock { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        var notice = new TextBlock { Text = "仅扫描当前及任务使用过的目录。选择孤立残留或已完成暂存，再点击安全清理。活动任务与可恢复断点受保护。清理移入 .bili-tasks-recycle，可还原，仍占用磁盘；需要释放空间时可打开回收区，在资源管理器中自行删除。最终媒体保留。",
            TextWrapping = TextWrapping.Wrap };
        var list = new ListView { SelectionMode = ListViewSelectionMode.Multiple,
            Height = Math.Clamp(XamlRoot.Size.Height - 380, 90, 300), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        list.ItemTemplate = (DataTemplate)Microsoft.UI.Xaml.Markup.XamlReader.Load(
            "<DataTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'><TextBlock Text='{Binding}' TextWrapping='Wrap' Margin='0,6'/></DataTemplate>");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(list, "暂存扫描预览，多选需要清理或还原的项");
        var refresh = new Button { Content = "刷新扫描" };
        var open = new Button { Content = "打开暂存回收区", IsEnabled = false };
        var status = new TextBlock { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetLiveSetting(status, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        var panel = new StackPanel { Spacing = 8 };
        panel.Children.Add(summary); panel.Children.Add(notice); panel.Children.Add(list);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        actions.Children.Add(refresh); actions.Children.Add(open); panel.Children.Add(actions); panel.Children.Add(status);
        var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = "暂存管理",
            Content = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto },
            PrimaryButtonText = "安全清理所选", SecondaryButtonText = "还原所选", CloseButtonText = "关闭",
            DefaultButton = ContentDialogButton.Close, IsPrimaryButtonEnabled = false, IsSecondaryButtonEnabled = false };
        bool busy = false;
        void Selection()
        {
            var selected = list.SelectedItems.Cast<Models.StagingEntry>().ToArray();
            dialog.IsPrimaryButtonEnabled = !busy && selected.Length > 0 && selected.All(e => e.CanClean);
            dialog.IsSecondaryButtonEnabled = !busy && selected.Length > 0 && selected.All(e => e.CanRestore);
            open.IsEnabled = !busy && selected.Length == 1 && selected[0].Category == "recycled";
        }
        void Apply(Models.StagingScan value)
        {
            summary.Text = $"已扫描 {value.Entries.Length} 项 · 占用 {value.TotalBytes / 1048576d:0.##} MiB · 可清理 {value.CleanableBytes / 1048576d:0.##} MiB · 回收区 {value.RecycledBytes / 1048576d:0.##} MiB";
            if (value.Errors.Length > 0 || value.Entries.Any(e => e.Category == "protected" && e.FileCount == 0))
                summary.Text += "（扫描失败项未计入占用）";
            list.ItemsSource = value.Entries;
            status.Text = string.Join(Environment.NewLine, value.Errors);
            Selection();
        }
        async Task Reload()
        {
            Apply(Models.Protocol.Read<Models.StagingScan>(await App.Session.Client.RequestAsync("staging.scan")));
        }
        async Task Run(Func<Task> work)
        {
            if (busy) return;
            busy = true; refresh.IsEnabled = false; list.IsEnabled = false; Selection();
            try { await App.Session.ExecuteAsync(work); }
            finally { busy = false; refresh.IsEnabled = true; list.IsEnabled = true; Selection(); }
        }
        async Task Change(bool restore)
        {
            var ids = list.SelectedItems.Cast<Models.StagingEntry>().Select(e => e.EntryId).ToArray();
            await Run(async () =>
            {
                var result = Models.Protocol.Read<Models.StagingReply>(await App.Session.Client.RequestAsync(
                    restore ? "staging.restore" : "staging.clean", new { entry_ids = ids }));
                await Reload();
                status.Text = string.Join(Environment.NewLine, result.Results.Select(r =>
                    (r.Status == "recycled" ? "已移入回收区：" : r.Status == "restored" ? "已还原：" : "未处理：") + r.Message)) +
                    (string.IsNullOrWhiteSpace(status.Text) ? "" : Environment.NewLine + status.Text);
            });
        }
        list.SelectionChanged += (_, _) => Selection();
        refresh.Click += async (_, _) => await Run(Reload);
        open.Click += async (_, _) => await Run(() => WindowsShellService.OpenFolderAsync(
            System.IO.Directory.GetParent(list.SelectedItems.Cast<Models.StagingEntry>().Single().Path)!.Parent!.FullName));
        dialog.PrimaryButtonClick += async (_, args) => { args.Cancel = true; await Change(false); };
        dialog.SecondaryButtonClick += async (_, args) => { args.Cancel = true; await Change(true); };
        Apply(scan);
        return dialog;
    }
    private async void Details_Click(object sender, RoutedEventArgs e)
    {
        var row = (TaskRow)((FrameworkElement)sender).DataContext;
        row.DetailOpen = !row.DetailOpen;
        if (row.DetailOpen) await App.Session.ExecuteAsync(row.LoadDetailsAsync);
    }
    private async void OpenOutput_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(async () =>
    {
        var row = (TaskRow)((FrameworkElement)sender).DataContext;
        await row.LoadDetailsAsync();
        if (row.Files.Count == 1) await WindowsShellService.OpenFileAsync(row.Files[0].Path);
        else
        {
            row.DetailOpen = true;
            if (row.Files.Count == 0) throw new IOException("此任务没有可打开的输出文件，请查看任务详情或日志。");
        }
    });
    private async void OutputFile_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFileAsync(((OutputFileItem)((FrameworkElement)sender).DataContext).Path));
    private bool logDialogOpen;
    private async void Logs_Click(object sender, RoutedEventArgs e)
    {
        if (logDialogOpen) return;
        logDialogOpen = true;
        try
        {
            await App.Session.ExecuteAsync(async () =>
            {
                var row = (TaskRow)((FrameworkElement)sender).DataContext;
                await row.LoadDetailsAsync();
                await CreateLogDialog(row.Logs, row.Attempts, row.AttemptsDropped).ShowAsync();
            });
        }
        finally { logDialogOpen = false; }
    }
    internal ContentDialog CreateLogDialog(string logs, Models.TaskAttempt[]? attempts = null, int dropped = 0)
    {
        var text = new TextBox { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap,
            Height = Math.Clamp(XamlRoot.Size.Height - 260, 80, 320) };
        text.Text = string.IsNullOrWhiteSpace(logs) ? "暂无任务日志。" : logs;
        ScrollViewer.SetVerticalScrollBarVisibility(text, ScrollBarVisibility.Auto);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(text, "此任务的脱敏日志");
        var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = "任务日志",
            Content = text, PrimaryButtonText = "复制日志", CloseButtonText = "关闭", DefaultButton = ContentDialogButton.Close };
        if (attempts is { Length: > 0 })
        {
            var selector = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(selector, "选择执行轮次");
            var ordered = attempts.Reverse().ToArray();
            string StateLabel(string state) => state switch
            {
                "completed" => "已完成", "partial" => "部分完成", "failed" => "失败",
                "cancelled" => "已取消", "interrupted" => "已中断", "blocked" => "需处理",
                _ => "执行中"
            };
            foreach (var attempt in ordered)
            {
                var time = DateTimeOffset.FromUnixTimeMilliseconds((long)(attempt.StartedAt * 1000)).ToLocalTime();
                selector.Items.Add($"{time:MM-dd HH:mm:ss} · {StateLabel(attempt.State)} · {attempt.AttemptId[..Math.Min(8, attempt.AttemptId.Length)]}" + (attempt.Legacy ? " · 旧记录" : ""));
            }
            selector.SelectionChanged += (_, _) =>
            {
                if (selector.SelectedIndex < 0) return;
                var attempt = ordered[selector.SelectedIndex];
                var start = DateTimeOffset.FromUnixTimeMilliseconds((long)(attempt.StartedAt * 1000)).ToLocalTime().ToString("MM-dd HH:mm:ss");
                var end = attempt.EndedAt is { } ended
                    ? DateTimeOffset.FromUnixTimeMilliseconds((long)(ended * 1000)).ToLocalTime().ToString("MM-dd HH:mm:ss") : "执行中";
                text.Text = $"执行轮次：{attempt.AttemptId}\n" +
                    (attempt.Legacy ? $"旧记录保存时间：{start}\n" : $"开始时间：{start}\n结束时间：{end}\n") +
                    $"状态：{StateLabel(attempt.State)}\n" +
                    (attempt.Truncated ? "[日志已截断，保留有限内容]\n" : "") +
                    (string.IsNullOrWhiteSpace(attempt.Logs) ? "暂无任务日志。" : attempt.Logs);
            };
            selector.SelectedIndex = 0;
            var panel = new StackPanel { Spacing = 8 };
            panel.Children.Add(new TextBlock { Text = $"当前保留 {attempts.Length} 轮（最多 5 轮），每轮最多 16,000 字符。已移除 {dropped} 轮历史。", TextWrapping = TextWrapping.Wrap });
            panel.Children.Add(selector);
            panel.Children.Add(text);
            dialog.Content = panel;
        }
        dialog.PrimaryButtonClick += (_, args) =>
        {
            WindowsShellService.Copy(text.Text);
            args.Cancel = true;
        };
        return dialog;
    }
    private async void Folder_Click(object sender, RoutedEventArgs e) => await App.Session.ExecuteAsync(() => WindowsShellService.OpenFolderAsync(((TaskRow)((FrameworkElement)sender).DataContext).Directory));
}
