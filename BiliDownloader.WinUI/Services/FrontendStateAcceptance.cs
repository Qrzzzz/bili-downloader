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
            return checks.ToArray();
        }
        finally { if (!session.Closing) await session.ShutdownAsync(); }
    }
}
