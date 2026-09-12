using System.Diagnostics;
using BiliDownloader.WinUI.Models;
using BiliDownloader.WinUI.ViewModels;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace BiliDownloader.WinUI.Services;

// Explicit --ui-regression only. The peer is a test fixture, outside the shipped package.
internal static class FrontendStateAcceptance
{
    internal static async Task<string[]> RunAsync(DispatcherQueue dispatcher, Func<DownloadViewModel, string, Task>? snapshot = null)
    {
        var python = Environment.GetEnvironmentVariable("BILI_BACKEND_PYTHON")!;
        var source = Environment.GetEnvironmentVariable("BILI_BACKEND_SOURCE")!;
        var peer = Path.Combine(source, "tests", "fixtures", "winui_peer.py");
        if (!Path.IsPathFullyQualified(python) || !File.Exists(peer)) throw new InvalidOperationException("UI regression requires the source test environment.");
        var client = new BackendClient(() =>
        {
            var start = new ProcessStartInfo(python) { WorkingDirectory = source, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
            start.ArgumentList.Add(peer); start.Environment["PYTHONUTF8"] = "1";
            return Process.Start(start)!;
        });
        var session = new ApplicationSession(client) { Dispatcher = dispatcher };
        var checks = new List<string>();
        void Check(bool ok, string name) { if (!ok) throw new InvalidOperationException("Frontend regression: " + name); checks.Add(name); }
        async Task WaitUntil(Func<bool> ready)
        {
            for (int i = 0; i < 100 && !ready(); i++) await Task.Delay(20);
            if (!ready()) throw new TimeoutException("Fixture acknowledgement was not dispatched.");
        }
        async Task CompleteAsync(object result) => await client.RequestAsync("fixture.complete", result);
        try
        {
            await session.InitializeAsync();
            Check(session.Connected && !session.Shell.IsOpen, "connected_without_redundant_banner");
            Check(!session.Account.CanManage && session.Account.ManageVisibility == Visibility.Collapsed, "signed_out_actions");
            var model = session.Download;
            model.Input = "BV1fixture";
            Task parse = model.ParseAsync();
            await WaitUntil(() => session.CanCancel);
            model.Input = "BV2newer";
            var video = new VideoInfo("fixture-parse", 1, "测试视频", "测试 UP 主", 90, "BV1fixture", 1, false,
                [new(1, "第一部分", 45, "p1"), new(2, "第二部分", 45, "p2")], [new("0", "1080p", 1080, "per_part")]);
            await CompleteAsync(video); await parse;
            Check(!model.CanDownload && model.VideoVisibility == Visibility.Collapsed, "late_parse_does_not_replace_new_input");

            parse = model.ParseAsync(); await WaitUntil(() => session.CanCancel); await CompleteAsync(video); await parse;
            Check(model.CanDownload && model.SelectedParts.SetEquals([1]), "parse_selects_current_part");
            model.SelectedParts.Clear(); model.Refresh();
            Check(!model.CanDownload, "empty_selection_disables_download");
            model.SelectedParts.Add(1); model.ModeIndex = 1;
            Check(model.FormatVisibility == Visibility.Collapsed && model.CanDownload, "mp3_hides_quality");

            Task download = model.DownloadAsync(); await WaitUntil(() => session.CanCancel);
            string input = model.Input; model.Input = "attempt-during-download";
            Check(!model.CanEditInput && model.Input == input && !model.CanParse && !model.CanDownload, "download_locks_input_and_commands");
            Check(model.ProgressVisibility == Visibility.Visible && model.VideoVisibility == Visibility.Collapsed, "download_phase_visibility");
            if (snapshot is not null) await snapshot(model, "progress-preparing");
            await client.RequestAsync("fixture.progress", new DownloadProgress("downloading", 1, 1, 1, 55, 55, 5767168, 10485760, null, 1048576, 5, false));
            await WaitUntil(() => model.Percent == 55);
            if (snapshot is not null) await snapshot(model, "progress-downloading");
            await client.RequestAsync("fixture.progress", new DownloadProgress("converting", 1, 1, 1, 100, 90, 1000, 1000, null, null, null, false));
            await WaitUntil(() => model.Status.Contains("转换"));
            Check(model.IsIndeterminate && !session.Shell.IsOpen, "postprocessing_progress_without_banner_spam");
            if (snapshot is not null) await snapshot(model, "progress-converting");
            await session.CancelAsync(); await session.CancelAsync();
            var count = await client.RequestAsync("fixture.cancel_count");
            Check(session.CancelRequested && !model.CanCancel && count.GetProperty("count").GetInt32() == 1, "cancel_is_submitted_once");
            if (snapshot is not null) await snapshot(model, "progress-cancelling");
            await CompleteAsync(new BatchResult("batch-1", "cancelled", [], false, [new(1, "第一部分", "cancelled", [], null)], source));
            await download;
            Check(!session.CancelRequested && model.CanEditInput && model.ResultVisibility == Visibility.Visible, "cancel_finishes_in_inline_result");
            Check(model.OutputVisibility == Visibility.Collapsed && model.DetailVisibility == Visibility.Visible, "no_empty_file_picker");

            // These placeholder paths exercise the UI result contract only. Real
            // MP4/MP3 decode and cancellation checks live in test_downloader_v211.py.
            string resultFixtures = Path.Combine(Environment.GetEnvironmentVariable("BILI_ACCEPTANCE_OUTPUT") ?? Path.GetTempPath(), "v211-result-fixtures");
            Directory.CreateDirectory(resultFixtures);
            foreach (string extension in new[] { "mp4", "mp3" })
            {
                model.ApplyVideo(video with { Parts = [new(1, "第一部分", 45, "p1"), new(2, "第二部分", 45, "p2"), new(3, "第三部分", 45, "p3")] });
                model.SelectedParts.UnionWith([1, 2, 3]); model.ModeIndex = extension == "mp3" ? 1 : 0;
                string first = Path.Combine(resultFixtures, "first." + extension), second = Path.Combine(resultFixtures, "second." + extension);
                await File.WriteAllTextAsync(first, "UI fixture, not media");
                await File.WriteAllTextAsync(second, "UI fixture, not media");
                download = model.DownloadAsync(); await WaitUntil(() => session.CanCancel);
                await session.CancelAsync();
                await CompleteAsync(new BatchResult("deferred-" + extension, "cancelled", [first, second], false,
                    [new(1, "第一部分", "completed", [first], null), new(2, "第二部分", "completed", [second], null), new(3, "第三部分", "cancelled", [], null)], resultFixtures));
                await download;
                Check(!session.Busy && model.ResultVisibility == Visibility.Visible && model.OutputFiles.SequenceEqual([first, second]) &&
                      model.Results.Select(p => p.Status).SequenceEqual(["completed", "completed", "cancelled"]), extension + "_deferred_cancel_preserves_two_results");
                model.SelectedOutput = second;
                Check(model.CanOpenFile && model.OutputPickerVisibility == Visibility.Visible && !model.CanRetry,
                      extension + "_safe_current_file_remains_openable");
                if (snapshot is not null) await snapshot(model, "result-deferred-" + extension);
            }

            var operation = session.RunAsync("diagnostics.run"); await WaitUntil(() => session.CanCancel);
            Check(model.ProgressVisibility == Visibility.Collapsed && model.OtherTaskStatus.Contains("诊断") && !model.CanCancel, "unrelated_task_has_accurate_status");
            await CompleteAsync(new { }); await operation;
            model.ApplyResult(new BatchResult("batch-2", "failed", [], true, [new(1, "第一部分", "failed", [], new("timeout", "网络超时", true, ""))], source));
            Check(model.CanRetry && model.RetryVisibility == Visibility.Visible, "failed_result_exposes_retry");
            model.Input = "another-video";
            Check(!model.CanRetry && model.RetryVisibility == Visibility.Collapsed, "new_input_invalidates_retry");

            var settings = session.Settings;
            settings.DownloadDirectory = Path.Combine(source, "draft-folder"); settings.ThemeIndex = 2;
            await settings.ReloadAsync();
            session.ApplySettings(new AppSettings(source, "light"));
            Check(settings.HasChanges && settings.DownloadDirectory.EndsWith("draft-folder") && settings.ThemeIndex == 2, "settings_draft_survives_reload_and_download_refresh");
            await settings.SaveAsync();
            Check(!settings.HasChanges && !settings.CanSave && model.DownloadDirectory.EndsWith("draft-folder"), "save_commits_draft");
            string preview = ""; session.ThemeRequested += value => preview = value;
            settings.ThemeIndex = 1; settings.DownloadDirectory = "reject-save";
            await session.ExecuteAsync(settings.SaveAsync);
            Check(settings.HasChanges && settings.CanSave && preview == "dark", "failed_save_retains_draft_restores_saved_theme");
            session.Account.ApplyStatus(new LoginStatus("verified", "已验证", "fixture"));
            Check(session.Account.LoginVisibility == Visibility.Collapsed && session.Account.CanManage, "signed_in_actions");
            Task login = session.Account.LoginAsync(); await WaitUntil(() => session.CanCancel);
            await CompleteAsync(new { code = "success", friendly = "", detail = "", status = new LoginStatus("verified", "已验证并保存", "qr-generation") });
            await login;
            Check(session.Account.StatusTitle == "已登录" && session.Account.LoginVisibility == Visibility.Collapsed &&
                  session.Account.CanManage && session.Account.ManageVisibility == Visibility.Visible,
                  "qr_terminal_immediately_shows_verified_account");
            foreach (string code in new[] { "none", "invalid", "local_pending" })
            {
                session.Account.ApplyStatus(new LoginStatus("verified", "已验证", "old-generation"));
                parse = model.ParseAsync(); await WaitUntil(() => session.CanCancel); await CompleteAsync(video); await parse;
                Check(model.CanDownload, "account_" + code + "_starts_with_valid_parse");
                model.ApplyResult(new BatchResult("account-retry", "failed", [], true,
                    [new(1, "第一部分", "failed", [], new("timeout", "网络超时", true, ""))], source));
                Check(model.CanRetry, "account_" + code + "_starts_with_retry");
                Task validate = session.Account.ValidateAsync(); await WaitUntil(() => session.CanCancel);
                await CompleteAsync(new LoginStatus(code, "凭据已改变", code == "local_pending" ? "new-generation" : null));
                await validate;
                Check(!model.CanDownload && !model.CanRetry && session.Account.StatusTitle != "已登录" &&
                      session.Account.LoginVisibility == Visibility.Visible, "account_" + code + "_revokes_stale_parse");
                Check(session.Account.CanManage == (code != "none"), "account_" + code + "_management_matches_credentials");
            }
            login = session.Account.LoginAsync(); await WaitUntil(() => session.CanCancel);
            await CompleteAsync(new { code = "session_changed", friendly = "登录凭据已改变", detail = "", status = new LoginStatus("none", "无本地登录凭据", null) });
            await login;
            Check(session.Account.StatusTitle == "未登录" && !session.Account.CanManage && session.Shell.Severity == InfoBarSeverity.Warning,
                  "stale_qr_terminal_never_shows_success");
            var sampleTask = new DownloadTask("task-a", "attempt-a", "并行任务 A", "MP4", 1, "anonymous", source,
                "downloading", "", 1, 0, 1, false, null, "", new("downloading", 1, 1, 1, 25, 25, 100, 400, null, 1024, 1, false));
            var secondTask = sampleTask with { TaskId = "task-b", Title = "并行任务 B", Position = 2 };
            session.Tasks.Apply(new TaskSnapshot(false, 2, [sampleTask, secondTask], 10));
            Check(session.Available && model.CanEditInput && session.Tasks.Items.Count == 2, "parallel_queue_does_not_lock_input");
            session.Tasks.Changed(new TaskChange(sampleTask with { State = "cancelled", Revision = 2 }, 11));
            Check(session.Tasks.Items[0].CanResume && session.Tasks.Items[1].CanCancel, "task_cancel_does_not_change_other_row");
            session.Tasks.Apply(new TaskSnapshot(false, 2, [sampleTask], 9));
            Check(session.Tasks.Items.Count == 2 && session.Tasks.Items[0].Value.State == "cancelled", "stale_snapshot_cannot_replace_new_task_event");
            session.Tasks.FilterIndex = 3;
            Check(session.Tasks.Items.Count == 1 && session.Tasks.Items[0].Id == "task-a", "task_attention_filter");
            session.Tasks.FilterIndex = 0;
            session.Tasks.Changed(new TaskChange(secondTask with { CredentialMode = "saved" }, 12));
            Check(!session.Account.CanLogin, "running_saved_task_locks_account_change");
            session.Tasks.Apply(new TaskSnapshot(false, 2, [], 13));
            var terminalEvents = new System.Collections.Concurrent.ConcurrentQueue<string>();
            client.Event += e => { if (e.Name.StartsWith("operation.")) terminalEvents.Enqueue(e.Name); };
            await session.ExecuteAsync(async () =>
                await session.RunAsync("fixture.malformed_terminal")).WaitAsync(TimeSpan.FromSeconds(8));
            await WaitUntil(() => !session.Connected);
            Check(!session.Busy && session.ActiveMethod is null && !session.CancelRequested,
                  "malformed_terminal_releases_busy_finally");
            Check(!terminalEvents.Contains("operation.completed"),
                  "malformed_terminal_does_not_update_ui_as_success");
            Check(session.Shell.Severity == InfoBarSeverity.Error,
                  "malformed_terminal_is_reported_as_protocol_failure");
            await session.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8));
            Check(!session.CanNavigate && !model.CanEditInput && !model.CanDownload && !settings.CanSave, "closing_disables_interaction");
            Check(!session.Busy, "malformed_terminal_shutdown_finishes");
            await CheckWorkerFailuresAsync(dispatcher, python, source, checks);
            await CheckTaskFrontendAsync(dispatcher, python, source, checks);
            return checks.ToArray();
        }
        finally { if (!session.Closing) await session.ShutdownAsync(); }
    }

    private static async Task CheckWorkerFailuresAsync(DispatcherQueue dispatcher, string python, string source, List<string> checks)
    {
        foreach (string scenario in new[] { "worker_construct", "worker_start" })
        {
            var client = new BackendClient(() =>
            {
                var start = new ProcessStartInfo(python) { WorkingDirectory = source, UseShellExecute = false, CreateNoWindow = true,
                    RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
                start.ArgumentList.Add(Path.Combine(source, "tests", "fixtures", "backend_fault_peer.py"));
                start.ArgumentList.Add(scenario); start.Environment["PYTHONUTF8"] = "1";
                return Process.Start(start)!;
            });
            var session = new ApplicationSession(client) { Dispatcher = dispatcher };
            try
            {
                await session.InitializeAsync();
                Task failed = session.RunAsync("diagnostics.run");
                if (!session.Busy) throw new InvalidOperationException("Worker fixture did not enter Busy.");
                await session.ExecuteAsync(async () => await failed).WaitAsync(TimeSpan.FromSeconds(8));
                if (!session.Available || session.Busy || session.ActiveMethod is not null || session.CanCancel || session.Shell.Severity != InfoBarSeverity.Error)
                    throw new InvalidOperationException("Worker failure did not release ApplicationSession: " + scenario);
                checks.Add(scenario + "_releases_busy_on_live_connection");
                var recovered = await session.RunAsync("diagnostics.run").WaitAsync(TimeSpan.FromSeconds(8));
                if (recovered.GetProperty("text").GetString() != "recovered" || !session.Available)
                    throw new InvalidOperationException("Worker failure left the next task blocked: " + scenario);
                checks.Add(scenario + "_next_task_completes");
            }
            finally { await session.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(8)); }
        }
    }

    private static async Task CheckTaskFrontendAsync(DispatcherQueue dispatcher, string python, string source, List<string> checks)
    {
        var start = new ProcessStartInfo(python) { WorkingDirectory = source, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        start.ArgumentList.Add(Path.Combine(source, "tests", "fixtures", "task_peer.py"));
        string profile = Path.Combine(Environment.GetEnvironmentVariable("BILI_ACCEPTANCE_OUTPUT")!, "task-ui-profile", Guid.NewGuid().ToString("N"));
        start.Environment["LOCALAPPDATA"] = Path.Combine(profile, "Local");
        start.Environment["APPDATA"] = Path.Combine(profile, "Roaming");
        start.Environment["PYTHONUTF8"] = "1";
        var client = new BackendClient(() => Process.Start(start)!);
        var session = new ApplicationSession(client) { Dispatcher = dispatcher };
        void Check(bool ok, string name) { if (!ok) throw new InvalidOperationException(name); checks.Add(name); }
        try
        {
            await session.InitializeAsync();
            await session.Tasks.TogglePauseAsync();
            session.Download.Input = "BV1234567890\nBV1234567891";
            await session.Download.ParseBatchAsync();
            Check(session.Download.BatchItems.Count == 2 && session.Download.CanAddBatch, "native_vm_batch_ready");
            await session.Download.EnqueueBatchAsync();
            Check(session.Tasks.Items.Count == 2 && session.Tasks.Items.All(r => r.Value.State == "queued"), "native_vm_batch_durable_enqueue");
            Check(session.Download.BatchItems.All(r => r.Added), "native_vm_batch_marks_submitted");
            await session.Tasks.TogglePauseAsync();
            await Task.Delay(300);
            await session.Tasks.ReloadAsync();
            Check(session.Tasks.Items.All(r => r.Active) && session.Download.CanEditInput, "native_vm_parallel_does_not_lock_editor");
            var first = session.Tasks.Items[0];
            await session.Tasks.ActAsync(first, "cancel");
            await Task.Delay(200);
            await session.Tasks.ReloadAsync();
            Check(first.Value.State == "cancelled" && session.Tasks.Items[1].Active, "native_vm_cancel_is_independent");
            await first.LoadDetailsAsync();
            Check(first.Details.Contains("已取消"), "native_vm_details_match_cancelled_result");
            session.Download.Input = "BV1234567892";
            await session.Download.ParseAsync();
            await session.Download.EnqueueAsync();
            Check(session.Tasks.Items.Count == 3 && session.Download.CanParse, "native_vm_add_while_downloading");
        }
        finally { await session.ShutdownAsync().WaitAsync(TimeSpan.FromSeconds(10)); }
    }
}
