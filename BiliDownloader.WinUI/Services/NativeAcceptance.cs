using System.Reflection;
using System.Text.Json;
using BiliDownloader.WinUI.Models;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace BiliDownloader.WinUI.Services;

public static class NativeAcceptance
{
    public static async Task CaptureAsync(Window window, FrameworkElement root, TitleBar title, NavigationView navigation, Frame frame)
    {
        string output = Environment.GetEnvironmentVariable("BILI_ACCEPTANCE_OUTPUT") ?? Path.Combine(Path.GetTempPath(), "bili-winui-acceptance");
        Directory.CreateDirectory(output);
        string[] stateChecks = [];
        var snapshots = new List<object>();
        var uiChecks = new List<string>();
        var controls = new List<object>();
        IEnumerable<FrameworkElement> Elements(DependencyObject node)
        {
            if (node is FrameworkElement element) yield return element;
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
                foreach (var child in Elements(VisualTreeHelper.GetChild(node, i))) yield return child;
        }
        bool Visible(FrameworkElement element)
        {
            for (DependencyObject? node = element; node is not null; node = VisualTreeHelper.GetParent(node))
                if (node is UIElement ui && ui.Visibility == Visibility.Collapsed) return false;
            return element.ActualWidth > 0 && element.ActualHeight > 0;
        }
        void Check(bool ok, string name)
        {
            if (!ok) throw new InvalidOperationException("Native UI acceptance: " + name);
            uiChecks.Add(name);
        }
        async Task Snapshot(string name, bool fixture)
        {
            root.UpdateLayout();
            await Task.Delay(250);
            var items = new List<object>();
            foreach (var element in Elements(root))
            {
                var peer = FrameworkElementAutomationPeer.CreatePeerForElement(element);
                var origin = element.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point());
                string id = AutomationProperties.GetAutomationId(element);
                if (Visible(element) && !string.IsNullOrEmpty(id) && id != "GlobalStatus")
                    Check(origin.X >= -1 && origin.X + element.ActualWidth <= root.ActualWidth + 1, name + "/" + id + "_fits_horizontally");
                if (peer is not null)
                    items.Add(new { type = element.GetType().FullName, element_name = element.Name, automation_id = id,
                        name = peer.GetName(), control_type = peer.GetAutomationControlType().ToString(),
                        x = origin.X, y = origin.Y, width = element.ActualWidth, height = element.ActualHeight, visible = Visible(element) });
            }
            int windows = NativeWindowCapture.VisibleWindowCount();
            Check(windows == 1, name + "/single_native_window");
            await NativeWindowCapture.CaptureAsync(window, Path.Combine(output, name + ".png"));
            snapshots.Add(new { name, fixture, theme = root.ActualTheme.ToString(), width = root.ActualWidth, height = root.ActualHeight, visible_window_count = windows, controls = items });
            controls.AddRange(items);
        }
        async Task InvokeButton(string content)
        {
            var button = Elements(frame).OfType<Button>().First(b => b.Content as string == content);
            var peer = new ButtonAutomationPeer(button);
            ((IInvokeProvider)peer.GetPattern(PatternInterface.Invoke)).Invoke();
            await Task.Delay(50);
        }
        void ScrollTop()
        {
            Elements(frame).OfType<ScrollViewer>().First(s => s.Name == "PageScroll").ChangeView(null, 0, null, true);
        }

        if (Environment.GetCommandLineArgs().Contains("--ui-regression"))
            stateChecks = await FrontendStateAcceptance.RunAsync(root.DispatcherQueue, async (fixtureModel, name) =>
            {
                var page = (Page)frame.Content;
                object originalContext = page.DataContext;
                try { page.DataContext = fixtureModel; ScrollTop(); await Snapshot(name, true); }
                finally { page.DataContext = originalContext; }
            });

        var session = App.Session;
        var model = session.Download;
        ElementTheme originalTheme = root.RequestedTheme;
        var initialSize = window.AppWindow.Size;
        var themes = new List<string>();
        await Task.Delay(250);
        root.RequestedTheme = ElementTheme.Light;
        var inputBox = Elements(frame).OfType<TextBox>().First(e => AutomationProperties.GetAutomationId(e) == "VideoInput");
        const string shareText = "【华强卖瓜-大厂版】\nhttps://www.bilibili.com/video/BV1kkbC6eEgm/?share_source=copy_web";
        inputBox.Text = shareText;
        await Task.Delay(50);
        Check(inputBox.AcceptsReturn && model.Input.Replace("\r\n", "\n").Replace('\r', '\n') == shareText && model.CanParse,
            "multiline_share_text_reaches_parse_input");
        await Snapshot("share-text-light", true);
        root.RequestedTheme = ElementTheme.Dark;
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(640 * root.XamlRoot.RasterizationScale), (int)(480 * root.XamlRoot.RasterizationScale)));
        await Task.Delay(250);
        await Snapshot("share-text-narrow-dark", true);
        window.AppWindow.Resize(initialSize);
        root.RequestedTheme = originalTheme;
        model.Input = "";
        await Task.Delay(250);
        foreach (var theme in new[] { ElementTheme.Light, ElementTheme.Dark })
        {
            root.RequestedTheme = theme;
            themes.Add(root.ActualTheme.ToString());
            await Snapshot("download-" + theme.ToString().ToLowerInvariant(), false);
        }
        root.RequestedTheme = ElementTheme.Light;
        model.Input = "invalid";
        await session.ExecuteAsync(model.ParseAsync);
        bool invalidInputHandled = session.Shell.Severity == InfoBarSeverity.Error && !model.CanDownload;
        await Snapshot("input-error", false);
        model.Input = "";
        session.Shell.Notify("原生验收：跨页状态保留");
        navigation.SelectedItem = navigation.SettingsItem;
        await Task.Delay(250);
        string? settingsPage = frame.Content?.GetType().FullName;
        session.Settings.DownloadDirectory = Path.Combine(output, "draft-downloads");
        session.Settings.ThemeIndex = 2;
        navigation.SelectedItem = navigation.MenuItems.OfType<NavigationViewItem>().First(i => (string)i.Tag == "account");
        await Task.Delay(250);
        string? accountPage = frame.Content?.GetType().FullName;
        bool statusPreserved = session.Shell.Message == "原生验收：跨页状态保留";
        session.Shell.IsOpen = false;
        await Snapshot("account-dark", false);
        navigation.SelectedItem = navigation.SettingsItem;
        await Task.Delay(250);
        Check(session.Settings.HasChanges && session.Settings.DownloadDirectory.EndsWith("draft-downloads"), "settings_draft_survives_real_navigation");
        await Snapshot("settings-draft-dark", false);
        session.ApplySettings(Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get")), discardDraft: true);
        root.RequestedTheme = ElementTheme.Light;
        await Snapshot("settings-light", false);
        var originalPreferences = Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get"));
        var preferences = Elements(frame).OfType<Expander>().Single(e => AutomationProperties.GetAutomationId(e) == "DownloadPreferences");
        preferences.IsExpanded = true;
        await Task.Delay(150);
        var remember = Elements(frame).OfType<ToggleSwitch>().Single(e => AutomationProperties.GetAutomationId(e) == "RememberDownloadPreferences");
        var defaultMode = Elements(frame).OfType<ComboBox>().Single(e => AutomationProperties.GetAutomationId(e) == "PreferredDownloadMode");
        var defaultQuality = Elements(frame).OfType<ComboBox>().Single(e => AutomationProperties.GetAutomationId(e) == "PreferredQuality");
        Check(new ToggleSwitchAutomationPeer(remember).GetPattern(PatternInterface.Toggle) is IToggleProvider, "remember_preference_has_native_toggle_semantics");
        Check(new ComboBoxAutomationPeer(defaultQuality).GetPattern(PatternInterface.ExpandCollapse) is IExpandCollapseProvider, "quality_preference_has_native_combobox_semantics");
        if (remember.IsOn) ((IToggleProvider)new ToggleSwitchAutomationPeer(remember).GetPattern(PatternInterface.Toggle)).Toggle();
        defaultMode.SelectedIndex = 1;
        defaultQuality.SelectedItem = session.Settings.QualityOptions.Single(q => q.Height == 1080);
        Check(session.Settings is { HasChanges: true, RememberDownloadPreferences: false, DownloadModeIndex: 1 } && session.Settings.PreferredQuality?.Height == 1080,
            "native_preference_controls_update_settings_draft");
        preferences.StartBringIntoView(new BringIntoViewOptions { AnimationDesired = false, VerticalAlignmentRatio = 0 });
        await Snapshot("preferences-light", false);
        await InvokeButton("保存设置");
        for (int i = 0; i < 100 && session.Settings.HasChanges; i++) await Task.Delay(20);
        var savedPreferences = Protocol.Read<AppSettings>(await session.Client.RequestAsync("settings.get"));
        Check(!session.Settings.HasChanges && savedPreferences is { RememberDownloadPreferences: false, DownloadMode: "audio_mp3", PreferredQuality: 1080 } && model.ModeIndex == 1,
            "native_save_button_persists_preferences_to_real_backend");
        root.RequestedTheme = ElementTheme.Dark;
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(640 * root.XamlRoot.RasterizationScale), (int)(480 * root.XamlRoot.RasterizationScale)));
        await Task.Delay(200);
        defaultQuality.StartBringIntoView(new BringIntoViewOptions { AnimationDesired = false });
        Check(defaultQuality.Focus(FocusState.Keyboard), "quality_preference_accepts_keyboard_focus");
        await Snapshot("preferences-narrow-dark", false);
        window.AppWindow.Resize(initialSize);
        await session.UpdateSettingsAsync(new { remember_download_preferences = originalPreferences.RememberDownloadPreferences,
            download_mode = originalPreferences.DownloadMode, preferred_quality = originalPreferences.PreferredQuality }, discardDraft: true);
        preferences.IsExpanded = false;
        root.RequestedTheme = ElementTheme.Light;
        navigation.SelectedItem = navigation.MenuItems[0];
        await Task.Delay(250);
        model.Input = "BV1nativeFixture";
        var video = new VideoInfo("native-fixture", 1, "原生界面验收示例：长标题与多分 P 的下载流程", "示例 UP 主", 3723, "BV1nativeFixture", 1, false,
            [new(1, "第一部分：准备与开始", 123, "p1"), new(2, "第二部分：较长的中文分 P 标题也应该完整显示并可选择", 3600, "p2")],
            [new("0", "最佳可用画质", null, "per_part"), new("1", "1080p", 1080, "per_part")]);
        model.ApplyVideo(video);
        ScrollTop();
        await Snapshot("parsed-light", true);
        var partsExpander = Elements(frame).OfType<Expander>().First(e => e.Header as string == model.SelectionSummary);
        partsExpander.IsExpanded = true;
        await Task.Delay(150);
        await InvokeButton("全选");
        Check(model.SelectedParts.SetEquals([1, 2]), "native_select_all");
        await InvokeButton("清空选择");
        Check(model.SelectedParts.Count == 0 && !model.CanDownload, "native_clear_selection_disables_download");
        await InvokeButton("全选");
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(640 * root.XamlRoot.RasterizationScale), (int)(480 * root.XamlRoot.RasterizationScale)));
        await Task.Delay(300);
        string narrowNavigationMode = navigation.DisplayMode.ToString();
        ScrollTop();
        await Snapshot("parsed-narrow", true);
        partsExpander.IsExpanded = false;
        model.ModeIndex = 1;
        var downloadButton = Elements(frame).First(e => AutomationProperties.GetAutomationId(e) == "DownloadButton");
        downloadButton.StartBringIntoView(new BringIntoViewOptions { AnimationDesired = false, VerticalAlignmentRatio = 1 });
        await Snapshot("mp3-narrow", true);
        Check(model.FormatVisibility == Visibility.Collapsed, "native_mp3_quality_hidden");

        string sampleFile = Path.Combine(output, "示例文件.mp4");
        await File.WriteAllBytesAsync(sampleFile, []);
        model.ApplyResult(new BatchResult("native-single", "completed", [sampleFile], false, [new(1, "第一部分", "completed", [sampleFile], null)], output));
        await Snapshot("result-single-narrow", true);
        Check(model.DetailVisibility == Visibility.Collapsed && model.OutputPickerVisibility == Visibility.Collapsed && model.CanOpenFile, "native_compact_single_file_result");
        window.AppWindow.Resize(initialSize);
        await Task.Delay(250);
        root.RequestedTheme = ElementTheme.Dark;
        await Snapshot("result-single-dark", true);
        string otherFile = Path.Combine(output, "第二个示例文件.mp3");
        await File.WriteAllBytesAsync(otherFile, []);
        model.ApplyResult(new BatchResult("native-partial", "partial", [sampleFile, otherFile], true,
            [new(1, "第一部分", "completed", [sampleFile, otherFile], null),
             new(2, "第二部分", "failed", [], new("timeout", "网络超时，请稍后重试。", true, "UI fixture"))], output));
        await Snapshot("result-partial-dark", true);
        Check(model.DetailVisibility == Visibility.Visible && model.OutputPickerVisibility == Visibility.Visible && model.CanRetry, "native_partial_details_and_retry");
        var picker = Elements(frame).OfType<ComboBox>().First(e => AutomationProperties.GetAutomationId(e) == "OutputPicker");
        picker.SelectedIndex = 1;
        await Task.Delay(50);
        Check(model.SelectedOutput == otherFile && model.SelectedOutputName == "第二个示例文件.mp3", "native_filename_selection_preserves_full_path");
        root.RequestedTheme = ElementTheme.Light;
        model.ApplyResult(new BatchResult("native-failed", "failed", [], true, [new(1, "第一部分", "failed", [], new("platform_412", "平台暂时拒绝请求（HTTP 412），请稍后重试。", true, "UI fixture"))], output));
        await Snapshot("result-failed-light", true);
        Check(model.OutputVisibility == Visibility.Collapsed && model.DetailVisibility == Visibility.Visible, "native_failed_result_without_empty_files");
        model.ApplyResult(new BatchResult("native-cancelled", "cancelled", [], false, [new(1, "第一部分", "cancelled", [], null)], output));
        await Snapshot("result-cancelled-light", true);
        Check(model.RetryVisibility == Visibility.Collapsed, "native_cancelled_result_no_false_retry");
        model.CollapseResult();
        Check(model.VideoVisibility == Visibility.Visible, "native_return_to_download_options");
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(640 * root.XamlRoot.RasterizationScale), (int)(480 * root.XamlRoot.RasterizationScale)));
        navigation.SelectedItem = navigation.MenuItems.OfType<NavigationViewItem>().First(i => (string)i.Tag == "account");
        await Snapshot("account-narrow", false);
        navigation.SelectedItem = navigation.SettingsItem;
        await Snapshot("settings-narrow", false);
        window.AppWindow.Resize(initialSize);
        // Source-only queue fixtures use the real Page and bindings, with no media/network work.
        if (Environment.GetCommandLineArgs().Contains("--ui-regression"))
        {
            navigation.SelectedItem = navigation.MenuItems.OfType<NavigationViewItem>().First(i => (string)i.Tag == "tasks");
            await Task.Delay(300);
            var page = frame.Content as Page ?? throw new InvalidOperationException("任务页未完成导航。");
            object context = page.DataContext;
            var fixtureQueue = new ViewModels.TasksViewModel(session);
            var taskA = new DownloadTask("native-task-a", "attempt-1", "课程学习：从第一章到第五章", "MP4 · 1080p", 5, "anonymous", output,
                "downloading", "", 1, 0, 1, false, null, "", new("downloading", 2, 2, 5, 54, 32, 10485760, 52428800, null, 3145728, 12, false));
            var taskB = taskA with { TaskId = "native-task-b", Title = "音乐现场 · 保存音频", FormatLabel = "MP3 · 192 kbps", PartCount = 1, Position = 2,
                Progress = new("downloading", 1, 1, 1, 68, 68, 8388608, 12582912, null, 1048576, 4, false) };
            fixtureQueue.Apply(new TaskSnapshot(false, 2, [taskA, taskB, taskA with { TaskId = "native-task-c", Title = "稍后下载的视频", State = "queued", Position = 3, Progress = null }], 1));
            page.DataContext = fixtureQueue;
            root.RequestedTheme = ElementTheme.Light;
            await Snapshot("tasks-parallel-light", true);
            Check(fixtureQueue.Items.Count == 3 && fixtureQueue.Items.Count(t => t.Active) == 2, "native_parallel_task_rows");
            root.RequestedTheme = ElementTheme.Dark;
            window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(640 * root.XamlRoot.RasterizationScale), (int)(480 * root.XamlRoot.RasterizationScale)));
            await Snapshot("tasks-parallel-narrow-dark", true);
            fixtureQueue.FilterIndex = 3;
            Check(fixtureQueue.Items.Count == 0, "native_task_filter_empty_state");
            await Snapshot("tasks-empty-filter-narrow", true);
            page.DataContext = context;
            window.AppWindow.Resize(initialSize);
        }
        var evidence = new { version = App.AppVersion, backend_connected = session.Connected,
            git_commit = typeof(App).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().FirstOrDefault(a => a.Key == "GitCommit")?.Value,
            build_dirty = typeof(App).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().FirstOrDefault(a => a.Key == "BuildDirty")?.Value,
            window_type = window.GetType().BaseType?.FullName, backdrop_type = window.SystemBackdrop?.GetType().FullName,
            titlebar_type = title.GetType().FullName, navigation_type = navigation.GetType().FullName,
            extends_content_into_titlebar = window.ExtendsContentIntoTitleBar, settings_page = settingsPage,
            account_page = accountPage, invalid_input_handled = invalidInputHandled, status_preserved = statusPreserved,
            narrow_navigation_mode = narrowNavigationMode, caption_theme = window.AppWindow.TitleBar.PreferredTheme.ToString(),
            rasterization_scale = root.XamlRoot.RasterizationScale, themes, controls, state_checks = stateChecks,
            ui_checks = uiChecks, snapshots };
        await File.WriteAllTextAsync(Path.Combine(output, "native-evidence.json"), JsonSerializer.Serialize(evidence, new JsonSerializerOptions { WriteIndented = true }));
        root.RequestedTheme = originalTheme;
        if (!session.Connected || !invalidInputHandled || !statusPreserved || narrowNavigationMode == "Expanded") Environment.ExitCode = 2;
    }
}
