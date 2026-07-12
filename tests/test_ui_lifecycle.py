from __future__ import annotations

import importlib
import logging
import os
import sys
import threading
import time
import types
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
        screenshot = Signal(bytes)
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

            # Model Playwright cleanup happening before the terminal outcome.
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


def test_login_worker_closes_playwright_objects_before_terminal_signal(
    ui: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real LoginWorker closes context/browser inside sync_playwright."""

    events: list[str] = []

    class FakePlaywrightError(Exception):
        pass

    class FakePage:
        def goto(self, *_args: Any, **_kwargs: Any) -> None:
            events.append("page.goto")

        def bring_to_front(self) -> None:
            events.append("page.front")

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def cookies(self, _urls: list[str]) -> list[dict[str, str]]:
            events.append("context.cookies")
            return [{"name": "synthetic", "value": "not-a-real-cookie"}]

        def close(self) -> None:
            events.append("context.close")

    class FakeBrowser:
        def __init__(self) -> None:
            self.context = FakeContext()

        def new_context(self, **_kwargs: Any) -> FakeContext:
            events.append("browser.new_context")
            return self.context

        def close(self) -> None:
            events.append("browser.close")

    class FakeChromium:
        def launch(self, **_kwargs: Any) -> FakeBrowser:
            events.append("browser.launch")
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            events.append("playwright.enter")
            return FakePlaywright()

        def __exit__(self, *_args: Any) -> None:
            events.append("playwright.exit")

    playwright_package = types.ModuleType("playwright")
    playwright_package.__path__ = []  # type: ignore[attr-defined]
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError  # type: ignore[attr-defined]
    sync_api.sync_playwright = lambda: FakeManager()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(ui, "ensure_playwright_runtime", lambda: None)
    monkeypatch.setattr(ui, "cookies_indicate_logged_in", lambda _cookies: True)
    monkeypatch.setattr(
        ui,
        "save_context_storage_state_atomic",
        lambda _context: events.append("state.saved"),
    )

    worker = ui.LoginWorker()
    worker.completed.connect(lambda outcome: events.append(f"completed:{outcome.code}"))
    worker.run()

    assert worker.terminal_outcome is not None
    assert worker.terminal_outcome.code == "success"
    assert events.count("context.close") == 1
    assert events.count("browser.close") == 1
    assert _index(events, "state.saved") < _index(events, "context.close")
    assert _index(events, "context.close") < _index(events, "browser.close")
    assert _index(events, "browser.close") < _index(events, "playwright.exit")
    assert _index(events, "playwright.exit") < _index(events, "completed:success")


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
    assert window.windowTitle() == "Bili Downloader Lite V1.0"
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

    release.set()
    assert thread.wait(2000)
    qtbot.waitUntil(lambda: window._allow_close, timeout=2000)
    assert thread.terminate_calls == 0
