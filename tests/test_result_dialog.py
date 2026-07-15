from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


os.environ["QT_QPA_PLATFORM"] = "offscreen"
QtCore = pytest.importorskip("PySide6.QtCore")


def _parts(downloader: Any) -> tuple[Any, Any]:
    return (
        downloader.VideoPart(1, "第一部分", "https://www.bilibili.com/video/BV1aa411c7mD?p=1"),
        downloader.VideoPart(2, "第二部分", "https://www.bilibili.com/video/BV1aa411c7mD?p=2"),
    )


def test_result_dialog_opens_existing_file_and_retries_only_failures(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    dialogs = importlib.import_module("app.ui_dialogs")
    downloader = importlib.import_module("app.downloader")
    first, second = _parts(downloader)
    output = tmp_path / "first.mp4"
    output.write_bytes(b"video")
    error = downloader.ErrorClassification(downloader.ErrorKind.TIMEOUT, "网络超时", True)
    result = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(first, downloader.PartDownloadStatus.COMPLETED, (str(output),)),
            downloader.PartDownloadResult(second, downloader.PartDownloadStatus.FAILED, error=error, detail="timeout"),
        )
    )
    opened: list[str] = []
    monkeypatch.setattr(
        dialogs.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    retried: list[tuple[Any, ...]] = []
    dialog = dialogs.DownloadResultDialog(result, video_title="测试视频", format_label="1080p")
    qtbot.addWidget(dialog)
    dialog.retry_requested.connect(retried.append)
    dialog.show()

    assert dialog.table.rowCount() == 2
    assert dialog.summary_label.text() == "任务结束：成功 1，失败 1，取消 0。"
    dialog.table.selectRow(0)
    assert dialog.open_file_button.isEnabled()
    dialog.open_selected_file()
    dialog.open_selected_folder()
    assert [Path(path) for path in opened] == [output, tmp_path]
    output.unlink()
    dialog._update_actions()
    assert not dialog.open_file_button.isEnabled()
    assert not dialog.open_folder_button.isEnabled()

    dialog.retry_failed()
    assert retried == [(second,)]
    assert not dialog.retry_button.isEnabled()


def test_result_dialog_merge_and_invalidation(
    qtbot: Any,
    tmp_path: Path,
) -> None:
    dialogs = importlib.import_module("app.ui_dialogs")
    downloader = importlib.import_module("app.downloader")
    first, second = _parts(downloader)
    error = downloader.ErrorClassification(downloader.ErrorKind.OFFLINE, "离线", True)
    original = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(first, downloader.PartDownloadStatus.CANCELLED),
            downloader.PartDownloadResult(second, downloader.PartDownloadStatus.FAILED, error=error),
        )
    )
    retried_file = tmp_path / "second.mp4"
    retried_file.write_bytes(b"video")
    retry = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(second, downloader.PartDownloadStatus.COMPLETED, (str(retried_file),)),
        )
    )
    dialog = dialogs.DownloadResultDialog(original, video_title="测试视频", format_label="720p")
    qtbot.addWidget(dialog)
    dialog.merge_retry_result(retry)

    assert [item.status for item in dialog.result.part_results] == [
        downloader.PartDownloadStatus.CANCELLED,
        downloader.PartDownloadStatus.COMPLETED,
    ]
    assert not dialog.retry_button.isEnabled()
    dialog.invalidate_retry()
    assert dialog.retry_valid is False
    assert "不能重试旧任务" in dialog.retry_button.toolTip()


def _prepare_main_window(
    monkeypatch: pytest.MonkeyPatch,
    isolated_paths: Any,
    qtbot: Any,
) -> tuple[Any, Any, Any]:
    ui = importlib.import_module("app.ui_main")
    downloader = importlib.import_module("app.downloader")
    monkeypatch.setattr(ui, "config_diagnostics", lambda: ())
    monkeypatch.setattr(ui, "ffmpeg_status_text", lambda: "FFmpeg: test double")
    monkeypatch.setattr(ui, "setup_logging", lambda: logging.getLogger("bili_downloader.result-tests"))
    monkeypatch.setattr(ui, "has_saved_session", lambda: False)
    monkeypatch.setattr(
        ui,
        "describe_login_status",
        lambda: SimpleNamespace(code="none", text="无本地登录凭据"),
    )
    download_dir = Path(isolated_paths.root) / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(ui, "load_config", lambda: ui.AppConfig(download_dir=str(download_dir)))
    monkeypatch.setattr(ui, "save_config", lambda _config: None)

    url = "https://www.bilibili.com/video/BV1aa411c7mD"
    first, second = _parts(downloader)
    choice = ui.FormatChoice("1080p", "height<=1080", 1080)
    info = ui.VideoInfoResult(
        "测试视频",
        "测试作者",
        30,
        "",
        [first, second],
        [choice],
        "BV1aa411c7mD",
        1,
        url,
    )
    window = ui.MainWindow(safe_mode=True)
    qtbot.addWidget(window)
    window.url_edit.setText(url)
    window.on_parse_finished(url, info)
    window.select_all_parts()
    return ui, downloader, window


def test_main_window_retry_reuses_original_request_and_merges_result(
    monkeypatch: pytest.MonkeyPatch,
    isolated_paths: Any,
    qtbot: Any,
) -> None:
    ui, downloader, window = _prepare_main_window(monkeypatch, isolated_paths, qtbot)
    first, second = _parts(downloader)
    output_one = Path(window.config.download_dir) / "first.mp4"
    output_two = Path(window.config.download_dir) / "second.mp4"
    output_one.write_bytes(b"one")
    output_two.write_bytes(b"two")
    error = downloader.ErrorClassification(downloader.ErrorKind.TIMEOUT, "网络超时", True)
    calls: list[tuple[tuple[str, ...], str, str, Any]] = []

    def fake_download(parts: list[Any], _config: Any, directory: str, selector: str, *_args: Any) -> Any:
        calls.append((tuple(part.url for part in parts), directory, selector, _args[-1]))
        if len(calls) == 1:
            return downloader.DownloadBatchResult(
                (
                    downloader.PartDownloadResult(first, downloader.PartDownloadStatus.COMPLETED, (str(output_one),)),
                    downloader.PartDownloadResult(second, downloader.PartDownloadStatus.FAILED, error=error),
                )
            )
        return downloader.DownloadBatchResult(
            (
                downloader.PartDownloadResult(second, downloader.PartDownloadStatus.COMPLETED, (str(output_two),)),
            )
        )

    monkeypatch.setattr(ui, "download_videos", fake_download)
    window.start_download()
    qtbot.waitUntil(lambda: window.download_thread is None and window.result_dialog is not None, timeout=3000)
    assert len(calls) == 1
    assert window.result_dialog is not None
    window.result_dialog.retry_failed()
    qtbot.waitUntil(lambda: len(calls) == 2 and window.download_thread is None, timeout=3000)

    assert calls[1][0] == (second.url,)
    assert calls[1][1:] == calls[0][1:]
    assert window.result_dialog is not None
    assert len(window.result_dialog.result.completed) == 2
    assert not window.result_dialog.result.failed


def test_batch_level_failure_becomes_per_part_result(
    monkeypatch: pytest.MonkeyPatch,
    isolated_paths: Any,
    qtbot: Any,
) -> None:
    ui, downloader, window = _prepare_main_window(monkeypatch, isolated_paths, qtbot)

    def fail_download(*_args: Any, **_kwargs: Any) -> Any:
        raise downloader.AppError(downloader.ErrorKind.DISK_FULL, "synthetic disk full")

    monkeypatch.setattr(ui, "download_videos", fail_download)
    window.start_download()
    qtbot.waitUntil(lambda: window.download_thread is None and window.result_dialog is not None, timeout=3000)

    assert window.result_dialog is not None
    assert len(window.result_dialog.result.failed) == 2
    assert all(
        item.error and item.error.kind is downloader.ErrorKind.DISK_FULL
        for item in window.result_dialog.result.failed
    )


def test_url_change_invalidates_result_retry_context(
    monkeypatch: pytest.MonkeyPatch,
    isolated_paths: Any,
    qtbot: Any,
) -> None:
    ui, downloader, window = _prepare_main_window(monkeypatch, isolated_paths, qtbot)
    first, second = _parts(downloader)
    error = downloader.ErrorClassification(downloader.ErrorKind.TIMEOUT, "网络超时", True)
    calls: list[tuple[str, ...]] = []

    def fake_download(parts: list[Any], *_args: Any) -> Any:
        calls.append(tuple(part.url for part in parts))
        return downloader.DownloadBatchResult(
            (
                downloader.PartDownloadResult(first, downloader.PartDownloadStatus.CANCELLED),
                downloader.PartDownloadResult(second, downloader.PartDownloadStatus.FAILED, error=error),
            )
        )

    monkeypatch.setattr(ui, "download_videos", fake_download)
    window.start_download()
    qtbot.waitUntil(lambda: window.download_thread is None and window.result_dialog is not None, timeout=3000)
    assert window.result_dialog is not None
    assert window.result_dialog.retry_button.isEnabled()

    window.url_edit.setText("https://www.bilibili.com/video/BV1bb411c7mE")
    assert not window.result_dialog.retry_valid
    window.result_dialog.retry_failed()
    qtbot.wait(50)
    assert len(calls) == 1
