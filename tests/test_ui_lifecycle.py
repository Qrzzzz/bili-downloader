from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


# This must be selected before pytest-qt creates QApplication.  None of these
# tests may open a native window or depend on an interactive desktop session.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QObject = QtCore.QObject
QThread = QtCore.QThread
Signal = QtCore.Signal
QDialog = QtWidgets.QDialog


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch, isolated_paths: Any) -> Any:
    """Import the UI only after APPDATA/LOCALAPPDATA have been isolated."""

    module = importlib.import_module("app.ui_main")
    monkeypatch.setattr(module, "config_diagnostics", lambda: ())
    monkeypatch.setattr(module, "ffmpeg_status_text", lambda: "FFmpeg: test double")
    monkeypatch.setattr(module, "setup_logging", lambda: logging.getLogger("bili_downloader.ui-tests"))
    monkeypatch.setattr(module, "has_saved_session", lambda: False)
    monkeypatch.setattr(
        module,
        "describe_login_status",
        lambda: SimpleNamespace(code="none", text="无本地登录凭据"),
    )
    monkeypatch.setattr(
        module,
        "load_config",
        lambda: module.AppConfig(download_dir=str(Path(isolated_paths.root) / "downloads")),
    )
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args, **kwargs: module.QMessageBox.Ok)
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args, **kwargs: module.QMessageBox.Ok)
    return module


def _index(events: list[str], name: str) -> int:
    return events.index(name)


@pytest.mark.parametrize(
    ("scenario", "expected_result", "action"),
    [
        ("success", QDialog.Accepted, None),
        ("cancel", QDialog.Rejected, "cancel"),
        ("close", QDialog.Rejected, "close"),
        ("timeout", QDialog.Rejected, None),
    ],
)
def test_login_dialog_repeated_terminal_paths_wait_for_thread_finished(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    scenario: str,
    expected_result: int,
    action: str | None,
) -> None:
    """success/cancel/X/timeout never return while the login worker is alive."""

    runs: list[list[str]] = []

    class FakeLoginWorker(QObject):
        status = Signal(str)
        qr_image = Signal(bytes)
        completed = Signal(object)

        def __init__(self) -> None:
            super().__init__()
            self._cancelled = threading.Event()
            self.terminal_outcome: Any | None = None
            self.events: list[str] = []
            runs.append(self.events)

        def request_cancel(self) -> None:
            self.events.append("cancel_requested")
            self._cancelled.set()

        def request_refresh(self) -> None:
            self.events.append("refresh_requested")

        def run(self) -> None:
            self.events.append("run_entered")
            # Give the GUI test time to issue Cancel or a window-manager close.
            if scenario in {"cancel", "close"}:
                assert self._cancelled.wait(2.0), "dialog never requested cooperative cancellation"
                outcome = ui.LoginOutcome("cancelled", "cancelled")
            elif scenario == "success":
                time.sleep(0.01)
                outcome = ui.LoginOutcome("success")
            else:
                time.sleep(0.01)
                outcome = ui.LoginOutcome("timeout", "timed out", "synthetic timeout")

            # Model protocol-session cleanup happening before the terminal outcome.
            self.events.append("resources_closed")
            self.terminal_outcome = outcome
            self.completed.emit(outcome)
            self.events.append("terminal_emitted")

            # A result emitted from run() must still not close the dialog until
            # the worker has returned and QThread emits finished.
            time.sleep(0.025)
            self.events.append("run_returned")

    monkeypatch.setattr(ui, "LoginWorker", FakeLoginWorker)

    for _ in range(10):
        dialog = ui.LoginDialog()
        qtbot.addWidget(dialog)
        thread = dialog.thread
        assert thread is not None
        observations: list[tuple[int, bool, bool, tuple[str, ...]]] = []

        def record_finish(result: int, *, target: Any = dialog, owned_thread: Any = thread) -> None:
            observations.append(
                (
                    result,
                    owned_thread.isRunning(),
                    target.thread is None,
                    tuple(runs[-1]),
                )
            )
            runs[-1].append("dialog_finished")

        dialog.finished.connect(record_finish)
        dialog.open()
        qtbot.waitUntil(thread.isRunning, timeout=2000)

        if action == "cancel":
            dialog.cancel_login()
        elif action == "close":
            # This exercises LoginDialog.closeEvent (the title-bar X path).
            dialog.close()
            assert dialog.thread is thread

        qtbot.waitUntil(lambda: bool(observations), timeout=3000)
        result, was_running, reference_cleared, snapshot = observations[0]
        assert result == expected_result
        assert was_running is False
        assert reference_cleared is True
        assert "resources_closed" in snapshot
        assert "run_returned" in snapshot
        assert _index(runs[-1], "resources_closed") < _index(runs[-1], "terminal_emitted")
        assert _index(runs[-1], "run_returned") < _index(runs[-1], "dialog_finished")
        if scenario == "success":
            assert dialog.login_succeeded is True
        else:
            assert dialog.login_succeeded is False


def test_login_worker_closes_native_qr_session_before_terminal_signal(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeClient:
        def generate(self) -> Any:
            events.append("challenge.generated")
            return SimpleNamespace(qr_url="https://account.bilibili.com/synthetic")

        def poll(self, _challenge: Any) -> Any:
            events.append("challenge.polled")
            return SimpleNamespace(status=ui.QrStatus.SUCCESS)

        def candidate_cookies(self) -> list[dict[str, str]]:
            events.append("cookies.candidate")
            return [{"name": "synthetic"}]

        def close(self) -> None:
            events.append("session.close")

    monkeypatch.setattr(ui, "render_qr_png", lambda _url: b"synthetic-png")

    def validate(_cookies: object, *, cancelled: Any) -> Any:
        assert not cancelled()
        events.append("candidate.validated-and-committed")
        return SimpleNamespace(code="verified", text="verified")

    monkeypatch.setattr(ui, "validate_and_commit_candidate_cookies", validate)

    worker = ui.LoginWorker(client_factory=FakeClient, total_timeout=1, poll_interval=0)
    worker.completed.connect(lambda outcome: events.append(f"completed:{outcome.code}"))
    worker.run()

    assert worker.terminal_outcome is not None
    assert worker.terminal_outcome.code == "success"
    assert events.count("session.close") == 1
    assert _index(events, "candidate.validated-and-committed") < _index(events, "session.close")
    assert _index(events, "session.close") < _index(events, "completed:success")


def test_login_worker_refresh_discards_old_session_and_old_success(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_poll_entered = threading.Event()
    release_first_poll = threading.Event()
    events: list[str] = []
    clients: list[Any] = []

    class FakeClient:
        def __init__(self) -> None:
            self.index = len(clients) + 1
            clients.append(self)

        def generate(self) -> Any:
            events.append(f"generate:{self.index}")
            return SimpleNamespace(qr_url="https://account.bilibili.com/synthetic")

        def poll(self, _challenge: Any) -> Any:
            events.append(f"poll:{self.index}")
            if self.index == 1:
                first_poll_entered.set()
                assert release_first_poll.wait(2)
            return SimpleNamespace(status=ui.QrStatus.SUCCESS)

        def candidate_cookies(self) -> list[dict[str, int]]:
            return [{"client": self.index}]

        def close(self) -> None:
            events.append(f"close:{self.index}")

    committed: list[int] = []

    def validate(candidate: list[dict[str, int]], *, cancelled: Any) -> Any:
        assert not cancelled()
        committed.append(candidate[0]["client"])
        return SimpleNamespace(code="verified", text="verified")

    monkeypatch.setattr(ui, "render_qr_png", lambda _url: b"synthetic-png")
    monkeypatch.setattr(ui, "validate_and_commit_candidate_cookies", validate)
    worker = ui.LoginWorker(client_factory=FakeClient, total_timeout=2, poll_interval=0)
    thread = threading.Thread(target=worker.run)
    thread.start()
    assert first_poll_entered.wait(1)
    worker.request_refresh()
    release_first_poll.set()
    thread.join(2)

    assert not thread.is_alive()
    assert worker.terminal_outcome is not None and worker.terminal_outcome.code == "success"
    assert committed == [2]
    assert _index(events, "close:1") < _index(events, "generate:2")
    assert events.count("close:1") == 1 and events.count("close:2") == 1


def _video_result(ui: Any, label: str, source_url: str) -> Any:
    part = ui.VideoPart(index=1, title=f"{label} part", url=source_url, duration=15)
    choice = ui.FormatChoice(label="720p", selector="height<=720", height=720)
    return ui.VideoInfoResult(
        title=f"video {label}",
        uploader="synthetic uploader",
        duration=15,
        thumbnail_url="",
        parts=[part],
        formats=[choice],
        raw_id=label,
        source_url=source_url,
    )


def test_main_window_progressively_discloses_task_controls(ui: Any, qtbot: Any) -> None:
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)

    assert not window.empty_state_label.isHidden()
    assert window.video_group.isHidden()
    assert window.parts_group.isHidden()
    assert window.download_group.isHidden()
    assert window.activity_group.isHidden()
    assert window.log_group.isHidden()
    assert not window.qr_login_button.isHidden()
    assert window.logout_button.isHidden()

    source_url = "https://www.bilibili.com/video/BV1aa411c7mD"
    window.url_edit.setText(source_url)
    result = _video_result(ui, "Single", source_url)
    window.on_parse_finished(source_url, result)

    assert window.empty_state_label.isHidden()
    assert not window.video_group.isHidden()
    assert window.parts_group.isHidden()
    assert not window.download_group.isHidden()
    assert window.activity_group.isHidden()
    assert window.parts_list.count() == 1
    assert window.parts_list.item(0).checkState() == QtCore.Qt.Checked
    assert window.download_button.isEnabled()


def test_multi_part_result_shows_only_contextual_picker(ui: Any, qtbot: Any) -> None:
    source_url = "https://www.bilibili.com/video/BV1aa411c7mD"
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    window.url_edit.setText(source_url)
    result = _video_result(ui, "Multi", source_url)
    result.parts = [
        ui.VideoPart(index=1, title="P1", url=f"{source_url}?p=1", duration=10),
        ui.VideoPart(index=2, title="P2", url=f"{source_url}?p=2", duration=20),
    ]
    result.current_part_index = 2

    window.on_parse_finished(source_url, result)

    assert not window.parts_group.isHidden()
    assert window.parts_list.count() == 2
    assert window.parts_list.item(0).checkState() == QtCore.Qt.Unchecked
    assert window.parts_list.item(1).checkState() == QtCore.Qt.Checked
    assert window.parts_summary_label.text() == "已选 1 / 2"


def test_login_actions_and_log_panel_remain_contextual(ui: Any, qtbot: Any) -> None:
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)

    window.set_login_status("无本地登录凭据", "none")
    assert not window.qr_login_button.isHidden()
    assert window.logout_button.isHidden()

    window.set_login_status("登录凭据已通过服务端验证", "verified")
    assert window.qr_login_button.isHidden()
    assert not window.logout_button.isHidden()

    window.set_login_status("登录凭据失效", "invalid")
    assert not window.qr_login_button.isHidden()
    assert window.logout_button.isHidden()
    assert window.clear_login_action.isVisible()

    window.set_login_status("检测登录状态中", "checking")
    assert window.qr_login_button.isHidden()
    assert window.logout_button.isHidden()
    assert not window.clear_login_action.isVisible()

    for code in ("local_pending", "offline", "platform_412", "protocol_error"):
        window.set_login_status("需要重新确认登录状态", code)
        assert not window.qr_login_button.isHidden()
        assert window.qr_login_button.text() == "重新扫码"
        assert window.logout_button.isHidden()
        assert window.clear_login_action.isVisible()

    window.set_login_status("安全模式", "safe_mode")
    assert not window.qr_login_button.isHidden()
    assert window.qr_login_button.text() == "扫码登录"
    assert window.logout_button.isHidden()
    assert not window.clear_login_action.isVisible()

    window._append_log("synthetic detail")
    assert window.log_group.isHidden()
    assert "synthetic detail" in window.log_view.toPlainText()
    window.toggle_log_action.setChecked(True)
    assert not window.log_group.isHidden()
    assert window.details_button.text() == "隐藏任务详情"
    window._set_view_phase("changed")
    assert not window.log_group.isHidden()
    window.toggle_log_action.setChecked(False)
    assert window.log_group.isHidden()
    assert window.details_button.text() == "查看任务详情"


def test_safe_mode_still_offers_clearing_saved_login_state(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
) -> None:
    monkeypatch.setattr(ui, "has_saved_session", lambda: True)
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)

    assert window.login_status_label.text() == "账号：匿名模式"
    assert not window.qr_login_button.isHidden()
    assert window.logout_button.isHidden()
    assert window.clear_login_action.isVisible()


def test_url_change_during_download_keeps_progress_and_cancel_visible(ui: Any, qtbot: Any) -> None:
    source_url = "https://www.bilibili.com/video/BV1aa411c7mD"
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    window.url_edit.setText(source_url)
    window.on_parse_finished(source_url, _video_result(ui, "Active", source_url))

    window.download_thread = object()  # type: ignore[assignment]
    try:
        window._set_view_phase("downloading")
        window.status_label.setText("正在合并第 1/2 个分 P")
        assert not window.activity_group.isHidden()
        assert not window.cancel_button.isHidden()
        assert window.download_group.isHidden()

        window.url_edit.setText("https://www.bilibili.com/video/BV1bb411c7mE")
        assert window.current_info is None
        assert window._view_phase == "downloading"
        assert window.status_label.text() == "正在合并第 1/2 个分 P"
        assert not window.activity_group.isHidden()
        assert not window.cancel_button.isHidden()
        assert "当前下载继续" in window.log_view.toPlainText()

        active_source = ui.normalize_bilibili_url(window.url_edit.text())
        window.parse_button.setEnabled(False)
        window.on_parse_failed(active_source, "network", "synthetic parse failure", "detail")
        assert window._view_phase == "downloading"
        assert window.status_label.text() == "正在合并第 1/2 个分 P"
        assert not window.cancel_button.isHidden()
        assert not window.parse_button.isEnabled()

        window.start_parse()
        assert window.parse_thread is None
        window.url_edit.setText("https://www.bilibili.com/video/BV1bb411c7mE?p=2")
        assert window.log_view.toPlainText().count("当前下载继续") == 1
    finally:
        window.download_thread = None
        window._set_view_phase("finished")


def test_url_change_after_download_result_does_not_restore_downloading_phase(ui: Any, qtbot: Any) -> None:
    source_url = "https://www.bilibili.com/video/BV1aa411c7mD"
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    window.url_edit.setText(source_url)
    window.on_parse_finished(source_url, _video_result(ui, "Finished", source_url))

    window.download_thread = object()  # type: ignore[assignment]
    try:
        window._set_view_phase("finished")
        window.url_edit.setText("https://www.bilibili.com/video/BV1bb411c7mE")

        assert window.current_info is None
        assert window._view_phase == "changed"
        assert window.status_label.text() == "链接已更改，请重新解析"
        assert window.cancel_button.isHidden()
    finally:
        window.download_thread = None


def test_thumbnail_failure_does_not_turn_successful_parse_into_failure(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
    isolated_paths: Any,
) -> None:
    source_url = "https://www.bilibili.com/video/BV1Synthetic99"
    result = _video_result(ui, "Synthetic", source_url)
    result.thumbnail_url = "https://i0.hdslb.com/cover.jpg"
    monkeypatch.setattr(ui, "parse_video_info", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        ui,
        "fetch_thumbnail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("synthetic thumbnail timeout")),
    )
    worker = ui.ParseWorker(
        source_url,
        ui.AppConfig(download_dir=str(isolated_paths.root / "downloads")),
        ui.CredentialMode.ANONYMOUS,
    )
    finished: list[tuple[str, object]] = []
    failed: list[tuple[object, ...]] = []
    logs: list[str] = []
    worker.finished.connect(lambda url, value: finished.append((url, value)))
    worker.failed.connect(lambda *args: failed.append(args))
    worker.log.connect(logs.append)

    worker.run()

    assert finished == [(source_url, result)]
    assert failed == []
    assert any("封面加载失败" in message for message in logs)


def test_url_change_parse_failure_and_stale_callback_cannot_download_old_video(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
) -> None:
    url_a = "https://www.bilibili.com/video/BV1aa411c7mD"
    url_b = "https://www.bilibili.com/video/BV1bb411c7mE"
    info_messages: list[str] = []
    save_attempts: list[object] = []
    monkeypatch.setattr(
        ui.QMessageBox,
        "information",
        lambda _parent, _title, text, *_args, **_kwargs: info_messages.append(text) or ui.QMessageBox.Ok,
    )
    monkeypatch.setattr(ui, "save_config", lambda config: save_attempts.append(config))

    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    assert window.windowTitle() == "Bili Downloader Lite V2.0"
    window.url_edit.setText(url_a)
    result_a = _video_result(ui, "A", url_a)
    window.on_parse_finished(url_a, result_a)
    assert window.current_info is result_a
    assert window.current_formats == result_a.formats
    assert window.download_button.isEnabled()

    # Merely editing A to B must immediately revoke every A download object.
    window.url_edit.setText(url_b)
    assert window.current_info is None
    assert window.current_formats == []
    assert window._parsed_url is None
    assert window.parts_list.count() == 0
    assert window.format_combo.count() == 0
    assert not window.download_button.isEnabled()

    # An out-of-order callback from the cancelled A parse is ignored.
    window.on_parse_finished(url_a, result_a)
    assert window.current_info is None
    assert window.parts_list.count() == 0
    assert not window.download_button.isEnabled()

    # B failing cannot resurrect A, and pressing Download cannot create a task.
    window.on_parse_failed(url_b, "network", "synthetic failure", "no network used")
    assert window.current_info is None
    assert window.current_formats == []
    assert window._parsed_url is None
    window.start_download()
    assert info_messages
    assert save_attempts == []
    assert window.download_thread is None


def test_starting_a_new_parse_discards_previous_result_before_worker_start(
    ui: Any,
    qtbot: Any,
) -> None:
    """Even a same-URL retry cannot leave the previous result downloadable."""

    class AlreadyRunningThread:
        @staticmethod
        def isRunning() -> bool:
            return True

    class CancelRecorder:
        def __init__(self) -> None:
            self.cancelled = False

        def request_cancel(self) -> None:
            self.cancelled = True

    url = "https://www.bilibili.com/video/BV1aa411c7mD"
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    window.url_edit.setText(url)
    result = _video_result(ui, "A", url)
    window.on_parse_finished(url, result)
    worker = CancelRecorder()
    window.parse_worker = worker
    window.parse_thread = AlreadyRunningThread()  # type: ignore[assignment]

    window.start_parse()

    assert worker.cancelled
    assert window.current_info is None
    assert window.current_formats == []
    assert window._parsed_url is None
    assert not window.download_button.isEnabled()
    window.parse_thread = None
    window.parse_worker = None


def test_main_window_close_requests_cancel_and_keeps_running_qthread_alive(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    destroyed = threading.Event()

    class BlockingWorker(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_requested = threading.Event()

        def request_cancel(self) -> None:
            self.cancel_requested.set()

        def run(self) -> None:
            entered.set()
            release.wait(3.0)

    class TrackingThread(QThread):
        def __init__(self, parent: Any) -> None:
            super().__init__(parent)
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    source_url = "https://www.bilibili.com/video/BV1aa411c7mD"
    window.url_edit.setText(source_url)
    result = _video_result(ui, "Closing", source_url)
    result.parts.append(ui.VideoPart(2, "second", f"{source_url}?p=2", 20))
    window.on_parse_finished(source_url, result)
    assert not window.video_group.isHidden()
    assert not window.parts_group.isHidden()
    assert not window.download_group.isHidden()
    window.show()
    worker = BlockingWorker()
    thread = TrackingThread(window)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.destroyed.connect(lambda: destroyed.set())
    window.parse_worker = worker  # type: ignore[assignment]
    window.parse_thread = thread
    thread.start()
    assert entered.wait(2.0)
    assert thread.isRunning()

    # Keep this focused test fast while still exercising the bounded-wait miss
    # and deferred-close branch.  The real wait implementation is separately
    # used by production and must never call terminate().
    monkeypatch.setattr(window, "_wait_for_shutdown", lambda timeout_ms=1500: False)
    closed_immediately = window.close()

    assert closed_immediately is False
    assert worker.cancel_requested.is_set()
    assert thread.isInterruptionRequested()
    assert thread.isRunning()
    assert not destroyed.is_set()
    assert thread.terminate_calls == 0
    assert window._closing is True
    assert window._allow_close is False
    assert window._view_phase == "closing"
    assert window.status_label.text() == "正在安全关闭，请稍候..."
    assert window.video_group.isHidden()
    assert window.parts_group.isHidden()
    assert window.download_group.isHidden()
    assert not window.url_edit.isEnabled()
    assert not window.details_button.isEnabled()

    window.on_download_progress({"phase": "downloading", "overall_percent": 50})
    window.on_download_finished(ui.DownloadBatchResult(()))
    window.start_parse()
    assert window._view_phase == "closing"
    assert window.status_label.text() == "正在安全关闭，请稍候..."
    assert not window.parse_button.isEnabled()
    assert window.parse_thread is thread

    release.set()
    assert thread.wait(2000)
    qtbot.waitUntil(lambda: window._allow_close, timeout=2000)
    assert thread.terminate_calls == 0


def test_diagnostics_dialog_only_checks_updates_after_manual_action(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
) -> None:
    dialogs = importlib.import_module("app.ui_dialogs")
    diagnostics = importlib.import_module("app.diagnostics")
    update_calls: list[str] = []
    report = diagnostics.DiagnosticReport(
        (
            diagnostics.DiagnosticItem(
                "程序", diagnostics.DiagnosticStatus.INFO, "V2.0，测试"
            ),
        )
    )
    monkeypatch.setattr(dialogs, "collect_diagnostics", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        dialogs,
        "check_latest_release",
        lambda: update_calls.append("called")
        or diagnostics.UpdateCheckResult(
            "1.3",
            "2.0",
            "https://github.com/Qrzzzz/bili-downloader/releases/tag/v2.0",
            True,
            "发现新版",
        ),
    )

    dialog = dialogs.DiagnosticsDialog(ui.AppConfig(download_dir=str(Path.cwd())))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.report is report, timeout=2000)
    assert update_calls == []
    assert dialog.copy_button.isEnabled()

    dialog.start_update_check()
    qtbot.waitUntil(lambda: update_calls == ["called"], timeout=2000)
    qtbot.waitUntil(dialog.open_release_button.isEnabled, timeout=2000)
    dialog.close()
