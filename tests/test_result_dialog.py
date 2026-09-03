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


def test_result_panel_opens_existing_file_and_retries_only_failures(
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
    panel = dialogs.DownloadResultPanel(result, video_title="测试视频", format_label="1080p")
    qtbot.addWidget(panel)
    panel.retry_requested.connect(retried.append)
    panel.show()

    assert panel.table.rowCount() == 2
    assert panel.summary_label.text() == "任务结束：成功 1，失败 1，取消 0。"
    panel.table.selectRow(0)
    assert panel.open_file_button.isEnabled()
    panel.open_selected_file()
    panel.open_selected_folder()
    assert [Path(path) for path in opened] == [output, tmp_path]
    output.unlink()
    panel._update_actions()
    assert not panel.open_file_button.isEnabled()
    assert not panel.open_folder_button.isEnabled()

    panel.retry_failed()
    assert retried == [(second,)]
    assert not panel.retry_button.isEnabled()


def test_single_success_result_uses_compact_presentation(qtbot: Any, tmp_path: Path) -> None:
    dialogs = importlib.import_module("app.ui_dialogs")
    downloader = importlib.import_module("app.downloader")
    first, _second = _parts(downloader)
    output = tmp_path / "single.mp4"
    output.write_bytes(b"video")
    result = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(
                first,
                downloader.PartDownloadStatus.COMPLETED,
                (str(output),),
            ),
        )
    )

    panel = dialogs.DownloadResultPanel(result, video_title="单 P 视频", format_label="1080p")
    qtbot.addWidget(panel)

    assert panel.table.isHidden()
    assert panel.retry_button.isHidden()
    assert not panel.saved_label.isHidden()
    assert panel.saved_label.text() == "已保存：single.mp4"
    assert panel.saved_label.toolTip() == str(output)
    assert panel.summary_label.text() == "任务结束：成功 1，失败 0，取消 0。"
    assert panel.open_file_button.isEnabled()
    assert panel.open_folder_button.isEnabled()
    panel.show()
    panel.dismiss_button.click()
    assert panel.isHidden()


def test_single_success_with_multiple_outputs_keeps_detail_table(qtbot: Any, tmp_path: Path) -> None:
    dialogs = importlib.import_module("app.ui_dialogs")
    downloader = importlib.import_module("app.downloader")
    first, _second = _parts(downloader)
    video = tmp_path / "single.mp4"
    audio = tmp_path / "single.m4a"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    result = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(
                first,
                downloader.PartDownloadStatus.COMPLETED,
                (str(video), str(audio)),
            ),
        )
    )

    panel = dialogs.DownloadResultPanel(result, video_title="单 P 多输出", format_label="1080p")
    qtbot.addWidget(panel)

    assert not panel.table.isHidden()
    assert panel.saved_label.isHidden()
    assert panel.table.item(0, 3).text() == "single.mp4；single.m4a"
    assert panel.table.item(0, 3).toolTip() == f"{video}\n{audio}"


def test_single_retry_success_switches_to_compact_presentation(qtbot: Any, tmp_path: Path) -> None:
    dialogs = importlib.import_module("app.ui_dialogs")
    downloader = importlib.import_module("app.downloader")
    first, _second = _parts(downloader)
    error = downloader.ErrorClassification(downloader.ErrorKind.TIMEOUT, "网络超时", True)
    original = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(
                first,
                downloader.PartDownloadStatus.FAILED,
                error=error,
            ),
        )
    )
    output = tmp_path / "retried.mp4"
    output.write_bytes(b"video")
    retry = downloader.DownloadBatchResult(
        (
            downloader.PartDownloadResult(
                first,
                downloader.PartDownloadStatus.COMPLETED,
                (str(output),),
            ),
        )
    )
    panel = dialogs.DownloadResultPanel(original, video_title="重试视频", format_label="720p")
    qtbot.addWidget(panel)

    assert not panel.table.isHidden()
    assert not panel.retry_button.isHidden()
    assert panel.retry_button.isEnabled()

    panel.merge_retry_result(retry)

    assert panel.table.isHidden()
    assert panel.retry_button.isHidden()
    assert not panel.saved_label.isHidden()
    assert panel.saved_label.text() == "已保存：retried.mp4"


def test_result_panel_merge_and_invalidation(
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
    panel = dialogs.DownloadResultPanel(original, video_title="测试视频", format_label="720p")
    qtbot.addWidget(panel)
    panel.merge_retry_result(retry)

    assert [item.status for item in panel.result.part_results] == [
        downloader.PartDownloadStatus.CANCELLED,
        downloader.PartDownloadStatus.COMPLETED,
    ]
    assert not panel.retry_button.isEnabled()
    panel.invalidate_retry()
    assert panel.retry_valid is False
    assert "不能重试旧任务" in panel.retry_button.toolTip()


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
    window.download_mode_combo.setCurrentIndex(
        window.download_mode_combo.findData(ui.DownloadMode.AUDIO_MP3.value)
    )
    output_one = Path(window.config.download_dir) / "first.mp3"
    output_two = Path(window.config.download_dir) / "second.mp3"
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
    qtbot.waitUntil(lambda: window.download_thread is None and window.result_panel is not None, timeout=3000)
    assert len(calls) == 1
    assert window.result_panel is not None
    panel = window.result_panel
    flow_layout = window.flow_scroll.widget().layout()
    assert not panel.isWindow()
    assert panel.window() is window
    assert flow_layout.indexOf(window.activity_group) < flow_layout.indexOf(panel)
    assert flow_layout.indexOf(panel) < flow_layout.indexOf(window.log_group)
    panel.retry_failed()
    qtbot.waitUntil(lambda: len(calls) == 2 and window.download_thread is None, timeout=3000)

    assert calls[1][0] == (second.url,)
    assert calls[1][1:] == calls[0][1:]
    assert calls[0][2] == "bestaudio/best"
    assert calls[0][3] is ui.DownloadMode.AUDIO_MP3
    assert window.result_panel is panel
    assert len(panel.result.completed) == 2
    assert not panel.result.failed


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
    qtbot.waitUntil(lambda: window.download_thread is None and window.result_panel is not None, timeout=3000)

    assert window.result_panel is not None
    assert len(window.result_panel.result.failed) == 2
    assert all(
        item.error and item.error.kind is downloader.ErrorKind.DISK_FULL
        for item in window.result_panel.result.failed
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
    qtbot.waitUntil(lambda: window.download_thread is None and window.result_panel is not None, timeout=3000)
    assert window.result_panel is not None
    assert window.result_panel.retry_button.isEnabled()

    window.url_edit.setText("https://www.bilibili.com/video/BV1bb411c7mE")
    assert not window.result_panel.retry_valid
    window.result_panel.retry_failed()
    qtbot.wait(50)
    assert len(calls) == 1
