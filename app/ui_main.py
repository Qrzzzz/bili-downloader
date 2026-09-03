from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .auth_qr import QrLoginClient, QrLoginError, QrStatus, render_qr_png
from .config import AppConfig, ConfigError, config_diagnostics, load_config, save_config
from .cookies import (
    CredentialMode,
    clear_login_state,
    cleanup_legacy_login_residue,
    describe_login_status,
    has_saved_session,
    SessionSaveError,
    validate_and_commit_candidate_cookies,
    validate_saved_session,
)
from .crash import crash_log_path
from .downloader import (
    DownloadBatchCancelled,
    DownloadBatchResult,
    DownloadController,
    DownloadMode,
    FormatChoice,
    PartDownloadResult,
    PartDownloadStatus,
    VideoInfoResult,
    VideoPart,
    download_videos,
    fetch_thumbnail,
    parse_video_info,
)
from .logger import LogEmitter, redact_sensitive, setup_logging
from .ui_dialogs import DiagnosticsDialog, DownloadResultPanel
from .utils import (
    ErrorClassification,
    ErrorKind,
    classify_error_details,
    ffmpeg_status_text,
    format_bytes,
    format_duration,
    format_eta,
    format_speed,
    normalize_bilibili_url,
)
class ParseWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str, str, str)
    cancelled = Signal(str)
    thumbnail = Signal(str, bytes)
    log = Signal(str)

    def __init__(self, url: str, config: AppConfig, credential_mode: CredentialMode) -> None:
        super().__init__()
        self.url = url
        self.config = config
        self.credential_mode = credential_mode
        self._cancelled = threading.Event()
        self.emitter = LogEmitter()
        self.emitter.message.connect(self.log.emit)

    def request_cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            result = parse_video_info(self.url, self.config, self.emitter, self.credential_mode)
            if self._cancelled.is_set():
                self.cancelled.emit(self.url)
                return
            if result.thumbnail_url:
                try:
                    thumbnail = fetch_thumbnail(result.thumbnail_url)
                    if not self._cancelled.is_set():
                        self.thumbnail.emit(self.url, thumbnail)
                except Exception as exc:  # noqa: BLE001
                    self.log.emit(f"封面加载失败：{exc}")
            if self._cancelled.is_set():
                self.cancelled.emit(self.url)
            else:
                self.finished.emit(self.url, result)
        except Exception as exc:  # noqa: BLE001
            if self._cancelled.is_set():
                self.cancelled.emit(self.url)
            else:
                classified = classify_error_details(exc)
                self.failed.emit(self.url, classified.code, classified.message, redact_sensitive(exc))


class DownloadWorker(QObject):
    progress = Signal(dict)
    finished = Signal(object)
    failed = Signal(object, str)
    log = Signal(str)

    def __init__(
        self,
        parts: list[VideoPart],
        config: AppConfig,
        download_dir: str,
        format_selector: str,
        controller: DownloadController,
        credential_mode: CredentialMode,
        mode: DownloadMode,
    ) -> None:
        super().__init__()
        self.parts = parts
        self.config = config
        self.download_dir = download_dir
        self.format_selector = format_selector
        self.controller = controller
        self.credential_mode = credential_mode
        self.mode = mode
        self.emitter = LogEmitter()
        self.emitter.message.connect(self.log.emit)

    @Slot()
    def run(self) -> None:
        try:
            saved = download_videos(
                self.parts,
                self.config,
                self.download_dir,
                self.format_selector,
                self.progress.emit,
                self.emitter,
                self.controller,
                self.credential_mode,
                self.mode,
            )
            self.finished.emit(saved)
        except DownloadBatchCancelled as exc:
            self.finished.emit(exc.result)
        except Exception as exc:  # noqa: BLE001
            classified = classify_error_details(exc)
            self.failed.emit(classified, redact_sensitive(exc))


class SessionValidationWorker(QObject):
    finished = Signal(str, str)
    log = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()

    def request_cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            status = validate_saved_session()
            if self._cancelled.is_set():
                self.finished.emit("cancelled", "")
            else:
                self.finished.emit(status.code, status.text)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("登录态后台验证发生未预期异常")
            self.log.emit(f"登录态验证失败：{redact_sensitive(exc)}")
            if self._cancelled.is_set():
                self.finished.emit("cancelled", "")
            else:
                self.finished.emit("offline", "暂时无法验证登录状态，本地凭据未被更改")


@dataclass(frozen=True)
class LoginOutcome:
    code: str
    friendly: str = ""
    detail: str = ""


@dataclass(frozen=True)
class DownloadRequest:
    source_url: str
    video_title: str
    parts: tuple[VideoPart, ...]
    config: AppConfig
    download_dir: str
    format_selector: str
    format_label: str
    credential_mode: CredentialMode
    mode: DownloadMode


class LoginWorker(QObject):
    status = Signal(str)
    qr_image = Signal(bytes)
    completed = Signal(object)

    def __init__(
        self,
        *,
        client_factory: Callable[[], QrLoginClient] = QrLoginClient,
        total_timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> None:
        super().__init__()
        self._cancelled = threading.Event()
        self._refresh_requested = threading.Event()
        self._client_factory = client_factory
        self._total_timeout = total_timeout
        self._poll_interval = poll_interval
        self.terminal_outcome: LoginOutcome | None = None

    def request_cancel(self) -> None:
        self._cancelled.set()

    def request_refresh(self) -> None:
        self._refresh_requested.set()

    def _stopping(self) -> bool:
        return self._cancelled.is_set()

    def _candidate_cancelled(self) -> bool:
        return self._cancelled.is_set() or self._refresh_requested.is_set()

    @staticmethod
    def _validation_failure(code: str, text: str) -> LoginOutcome:
        if code == "platform_412":
            return LoginOutcome(
                "platform_412",
                "Bilibili 返回 HTTP 412，当前环境受平台限制。",
                "未尝试绕过限制，原有本地登录态未更改。",
            )
        if code == "offline":
            return LoginOutcome("network_failure", "网络超时或当前离线。", text)
        if code == "invalid":
            return LoginOutcome("invalid", "扫码返回的登录凭据无效。", text)
        return LoginOutcome("protocol_error", "Bilibili 登录验证响应异常。", text)

    @Slot()
    def run(self) -> None:
        outcome: LoginOutcome | None = None
        client: QrLoginClient | None = None
        challenge = None
        deadline = time.monotonic() + self._total_timeout
        try:
            while outcome is None:
                if self._stopping():
                    outcome = LoginOutcome("cancelled", "已取消扫码登录。")
                    break
                if time.monotonic() >= deadline:
                    outcome = LoginOutcome(
                        "timeout",
                        "扫码登录超时，请重试。",
                        "扫码登录超过 5 分钟未完成，原有登录态未更改。",
                    )
                    break

                if challenge is None or self._refresh_requested.is_set():
                    self._refresh_requested.clear()
                    if client is not None:
                        client.close()
                    client = self._client_factory()
                    self.status.emit("正在生成 Bilibili 官方二维码...")
                    challenge = client.generate()
                    if self._candidate_cancelled():
                        challenge = None
                        continue
                    self.qr_image.emit(render_qr_png(challenge.qr_url))
                    self.status.emit("等待扫码：请使用 Bilibili App")

                assert client is not None and challenge is not None
                polled = client.poll(challenge)
                if self._candidate_cancelled():
                    challenge = None
                    continue

                if polled.status is QrStatus.WAITING_SCAN:
                    self.status.emit("等待扫码：请使用 Bilibili App")
                elif polled.status is QrStatus.WAITING_CONFIRMATION:
                    self.status.emit("已扫码，请在手机上确认登录")
                elif polled.status is QrStatus.EXPIRED:
                    self.status.emit("二维码已过期，请点击“刷新二维码”")
                    while (
                        not self._stopping()
                        and not self._refresh_requested.is_set()
                        and time.monotonic() < deadline
                    ):
                        self._refresh_requested.wait(0.2)
                    challenge = None
                    continue
                elif polled.status is QrStatus.SUCCESS:
                    self.status.emit("手机已确认，正在验证登录凭据...")
                    validation = validate_and_commit_candidate_cookies(
                        client.candidate_cookies(),
                        cancelled=self._candidate_cancelled,
                    )
                    if validation.code == "cancelled":
                        if self._stopping():
                            outcome = LoginOutcome("cancelled", "已取消扫码登录。")
                        else:
                            challenge = None
                        continue
                    if validation.code == "verified":
                        self.status.emit("登录成功，凭据已验证并安全保存")
                        outcome = LoginOutcome("success")
                    else:
                        outcome = self._validation_failure(validation.code, validation.text)
                    continue

                self._cancelled.wait(self._poll_interval)
        except QrLoginError as exc:
            if exc.status is QrStatus.CANCELLED or self._stopping():
                outcome = LoginOutcome("cancelled", "已取消扫码登录。")
            elif exc.code == "platform_412":
                outcome = self._validation_failure("platform_412", str(exc))
            elif exc.status is QrStatus.NETWORK_FAILURE:
                outcome = LoginOutcome("network_failure", "网络超时或当前离线。", str(exc))
            else:
                outcome = LoginOutcome("protocol_error", "Bilibili 扫码协议响应异常。", str(exc))
        except SessionSaveError as exc:
            outcome = LoginOutcome(
                "save_failed",
                "登录凭据已验证，但本地原子保存失败。",
                redact_sensitive(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("扫码登录流程发生未预期异常")
            outcome = LoginOutcome("failed", "扫码登录失败。", redact_sensitive(exc))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    logging.getLogger("bili_downloader").exception("关闭扫码协议会话失败")

        if outcome is None:
            outcome = LoginOutcome("failed", "扫码登录失败。", "登录线程未产生终态。")
        self.terminal_outcome = outcome
        self.completed.emit(outcome)


class LoginThread(QThread):
    """Run the blocking login workflow without moving its QObject ownership."""

    def __init__(self, worker: LoginWorker, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.worker = worker

    def run(self) -> None:
        self.worker.run()


class LoginDialog(QDialog):
    status_for_main = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bilibili 扫码登录")
        self.resize(480, 560)
        self.worker: LoginWorker | None = None
        self.thread: QThread | None = None
        self._finished_thread: QThread | None = None
        self.login_succeeded = False
        self._pending_outcome: LoginOutcome | None = None
        self._final_outcome: LoginOutcome | None = None
        self._dismiss_requested = False

        layout = QVBoxLayout(self)
        self.preview_label = QLabel("正在生成 Bilibili 官方二维码...")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(360, 360)
        self.preview_label.setFrameShape(QFrame.StyledPanel)
        self.status_label = QLabel("正在生成二维码")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新二维码")
        self.cancel_button = QPushButton("取消")
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.cancel_button)

        layout.addWidget(self.preview_label, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(button_row)

        self.refresh_button.clicked.connect(self.refresh_qr)
        self.cancel_button.clicked.connect(self.cancel_login)
        self.start_worker()

    def start_worker(self) -> None:
        worker = LoginWorker()
        worker.setParent(self)
        thread = LoginThread(worker, self)
        worker.status.connect(self.on_status)
        worker.qr_image.connect(self.on_qr_image)
        worker.completed.connect(self.on_terminal)
        thread.finished.connect(self.on_thread_finished)
        self.worker = worker
        self.thread = thread
        thread.start()

    @Slot()
    def refresh_qr(self) -> None:
        if not self.thread or not self.thread.isRunning() or self._dismiss_requested:
            return
        self.preview_label.clear()
        self.preview_label.setText("正在生成新的二维码...")
        self.status_label.setText("正在刷新二维码")
        self.status_for_main.emit("等待扫码")
        if self.worker:
            self.worker.request_refresh()

    @Slot()
    def cancel_login(self) -> None:
        self.request_shutdown()

    def request_shutdown(self) -> None:
        self._dismiss_requested = True
        self.refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在关闭扫码登录...")
        if self.worker:
            self.worker.request_cancel()

    @Slot(str)
    def on_status(self, text: str) -> None:
        self.status_label.setText(text)
        if "已扫码" in text:
            self.status_for_main.emit("等待手机确认")
        elif "验证" in text or "保存" in text:
            self.status_for_main.emit("正在验证并保存本地登录凭据")
        elif "过期" in text:
            self.status_for_main.emit("二维码已过期，等待刷新")
        else:
            self.status_for_main.emit("等待扫码")

    @Slot(bytes)
    def on_qr_image(self, data: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.preview_label.setPixmap(
                pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
            )

    @Slot(object)
    def on_terminal(self, outcome: LoginOutcome) -> None:
        # Store only. The dialog must not accept/reject until QThread.finished.
        self._pending_outcome = outcome
        self.refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        if outcome.code == "success":
            self.status_label.setText("登录成功，正在关闭登录窗口...")
        elif outcome.code == "cancelled":
            self.status_label.setText("正在关闭扫码登录...")
        else:
            self.status_label.setText(outcome.friendly or "扫码登录失败。")

    @Slot()
    def on_thread_finished(self) -> None:
        thread = self.thread
        worker = self.worker
        outcome = self._pending_outcome or (worker.terminal_outcome if worker else None)
        # QThread is parented to the dialog, so retain its Python wrapper until
        # the dialog itself is destroyed. Calling deleteLater() while the
        # finished signal is still unwinding can race PySide's wrapper cleanup
        # during rapid dialog close/reopen cycles on Windows.
        self._finished_thread = thread
        self.thread = None
        self.worker = None
        if outcome is None:
            outcome = LoginOutcome("failed", "扫码登录失败。", "登录线程结束但未返回终态。")

        self._final_outcome = outcome
        # Let QThread.finished fully unwind before closing the dialog. This
        # prevents nested dialog.finished handlers from touching the native
        # QThread while Windows is still completing its teardown.
        QTimer.singleShot(0, self._finish_dialog)

    @Slot()
    def _finish_dialog(self) -> None:
        outcome = self._final_outcome
        if outcome is None:
            return
        self._final_outcome = None

        if outcome.code == "success":
            self.login_succeeded = True
            self.status_for_main.emit("本地登录凭据待服务端验证")
            self.accept()
            return

        if outcome.code != "cancelled" and not self._dismiss_requested:
            self.status_for_main.emit("登录已失效")
            if not self.parent() or not getattr(self.parent(), "_closing", False):
                QMessageBox.warning(
                    self,
                    "扫码登录失败",
                    f"{outcome.friendly}\n\n详细信息：{outcome.detail}",
                )
        self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.thread and self.thread.isRunning():
            self.request_shutdown()
            event.ignore()
            return
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, safe_mode: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(f"Bili Downloader Lite V{__version__}")
        self.resize(1040, 760)

        self.config: AppConfig = load_config()
        self.current_info: VideoInfoResult | None = None
        self.current_formats: list[FormatChoice] = []
        self.parse_thread: QThread | None = None
        self.parse_worker: ParseWorker | None = None
        self.download_thread: QThread | None = None
        self.download_worker: DownloadWorker | None = None
        self.session_thread: QThread | None = None
        self.session_worker: SessionValidationWorker | None = None
        self.download_controller: DownloadController | None = None
        self.login_dialog: LoginDialog | None = None
        self.diagnostics_dialog: DiagnosticsDialog | None = None
        self.result_panel: DownloadResultPanel | None = None
        self.download_request: DownloadRequest | None = None
        self._active_download_parts: tuple[VideoPart, ...] = ()
        self._download_is_retry = False
        self._retry_context_valid = False
        self.safe_mode = safe_mode
        self.credential_mode = CredentialMode.ANONYMOUS if safe_mode else CredentialMode.SAVED
        self.login_status_code = "none"
        self._parsed_url: str | None = None
        self._closing = False
        self._allow_close = False
        self._shutdown_wait_attempted = False
        self._view_phase = "idle"

        self.log_emitter = LogEmitter()
        self.logger = setup_logging()
        cleanup_legacy_login_residue()

        self._build_ui()
        self._connect()
        self._load_config_into_ui()
        self._set_view_phase("idle")
        self._append_log("程序已启动。")
        for diagnostic in config_diagnostics():
            self._append_log(f"配置诊断：{diagnostic}")
        self._append_log(ffmpeg_status_text())
        if self.safe_mode:
            self._append_log("安全模式已启用：当前解析和下载强制使用匿名模式，不会加载本地登录凭据。")

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        primary_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 Bilibili 视频链接，或输入 BV/av 号")
        self.url_edit.setAccessibleName("Bilibili 视频链接")
        self.parse_button = QPushButton("解析视频")
        self.login_status_label = QLabel("账号：未登录")
        self.login_status_label.setToolTip("登录态仅保存在本机应用数据目录。")
        self.qr_login_button = QPushButton("扫码登录")
        self.logout_button = QPushButton("退出登录")
        self.logout_button.hide()

        self.more_button = QToolButton()
        self.more_button.setText("更多")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        self.toggle_log_action = QAction("显示任务详情", self)
        self.toggle_log_action.setCheckable(True)
        self.diagnostics_action = QAction("环境诊断", self)
        self.view_crash_log_action = QAction("查看错误日志", self)
        self.login_privacy_action = QAction("登录与隐私说明", self)
        self.clear_login_action = QAction("清除登录状态", self)
        self.clear_login_action.setVisible(False)
        self.more_menu.addAction(self.toggle_log_action)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.diagnostics_action)
        self.more_menu.addAction(self.view_crash_log_action)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.login_privacy_action)
        self.more_menu.addAction(self.clear_login_action)
        self.more_button.setMenu(self.more_menu)

        primary_row.addWidget(self.url_edit, 1)
        primary_row.addWidget(self.parse_button)
        primary_row.addSpacing(8)
        primary_row.addWidget(self.login_status_label)
        primary_row.addWidget(self.qr_login_button)
        primary_row.addWidget(self.logout_button)
        primary_row.addWidget(self.more_button)
        root.addLayout(primary_row)

        self.flow_scroll = QScrollArea()
        self.flow_scroll.setWidgetResizable(True)
        self.flow_scroll.setFrameShape(QFrame.NoFrame)
        self.flow_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        flow = QWidget()
        flow_layout = QVBoxLayout(flow)
        self.flow_layout = flow_layout
        flow_layout.setContentsMargins(0, 0, 0, 0)
        flow_layout.setSpacing(10)

        self.empty_state_label = QLabel(
            "粘贴视频链接并解析。\n解析完成后才会显示视频信息、画质与下载选项。"
        )
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setMinimumHeight(220)
        flow_layout.addWidget(self.empty_state_label, 1)

        self.video_group = QGroupBox("视频")
        info_layout = QGridLayout(self.video_group)
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(192, 108)
        self.cover_label.setMaximumSize(240, 135)
        self.cover_label.setFrameShape(QFrame.StyledPanel)
        self.title_label = QLabel("-")
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.uploader_label = QLabel("-")
        self.duration_label = QLabel("-")
        self.parts_label = QLabel("-")
        info_layout.addWidget(self.cover_label, 0, 0, 4, 1)
        info_layout.addWidget(QLabel("标题："), 0, 1)
        info_layout.addWidget(self.title_label, 0, 2)
        info_layout.addWidget(QLabel("UP 主："), 1, 1)
        info_layout.addWidget(self.uploader_label, 1, 2)
        info_layout.addWidget(QLabel("时长："), 2, 1)
        info_layout.addWidget(self.duration_label, 2, 2)
        info_layout.addWidget(QLabel("分 P："), 3, 1)
        info_layout.addWidget(self.parts_label, 3, 2)
        info_layout.setColumnStretch(2, 1)
        self.video_group.hide()
        flow_layout.addWidget(self.video_group)

        self.parts_group = QGroupBox("分 P 选择")
        parts_layout = QVBoxLayout(self.parts_group)
        part_actions = QHBoxLayout()
        self.parts_summary_label = QLabel("已选 0 / 0")
        self.select_all_button = QPushButton("全选")
        self.select_first_button = QPushButton("仅第一 P")
        part_actions.addWidget(self.parts_summary_label)
        part_actions.addStretch(1)
        part_actions.addWidget(self.select_all_button)
        part_actions.addWidget(self.select_first_button)
        self.parts_list = QListWidget()
        self.parts_list.setAccessibleName("分 P 选择")
        self.parts_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.parts_list.setMinimumHeight(140)
        self.parts_list.setMaximumHeight(260)
        parts_layout.addLayout(part_actions)
        parts_layout.addWidget(self.parts_list, 1)
        self.parts_group.hide()
        flow_layout.addWidget(self.parts_group)

        self.download_group = QGroupBox("下载选项")
        form = QFormLayout(self.download_group)
        self.download_mode_combo = QComboBox()
        self.download_mode_combo.setAccessibleName("下载内容")
        self.download_mode_combo.addItem("音视频（MP4）", DownloadMode.AUDIO_VIDEO.value)
        self.download_mode_combo.addItem("仅音频（MP3）", DownloadMode.AUDIO_MP3.value)
        self.format_combo = QComboBox()
        self.format_combo.setAccessibleName("下载画质")
        self.format_combo.setToolTip("画质严格匹配，不会自动降档。")
        self.download_dir_edit = QLineEdit()
        self.download_dir_edit.setAccessibleName("保存目录")
        self.browse_button = QPushButton("选择目录")
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.download_dir_edit, 1)
        dir_row.addWidget(self.browse_button)
        self.format_note_label = QLabel()
        self.format_note_label.setWordWrap(True)
        self.format_note_label.hide()
        self.download_button = QPushButton("下载")
        self.download_button.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.download_button)
        self.format_field_label = QLabel("清晰度：")
        form.addRow("下载内容：", self.download_mode_combo)
        form.addRow(self.format_field_label, self.format_combo)
        form.addRow("", self.format_note_label)
        form.addRow("保存目录：", dir_row)
        form.addRow("", buttons)
        self.download_group.hide()
        flow_layout.addWidget(self.download_group)

        self.activity_group = QGroupBox("任务状态")
        progress_layout = QVBoxLayout(self.activity_group)
        activity_row = QHBoxLayout()
        self.status_label = QLabel("待命")
        self.status_label.setWordWrap(True)
        self.cancel_button = QPushButton("取消下载")
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.details_button = QPushButton("查看任务详情")
        activity_row.addWidget(self.status_label, 1)
        activity_row.addWidget(self.cancel_button)
        activity_row.addWidget(self.details_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setAccessibleName("任务进度")
        self.progress_bar.setRange(0, 100)
        self.metrics_label = QLabel()
        self.metrics_label.setWordWrap(True)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addLayout(activity_row)
        progress_layout.addWidget(self.metrics_label)
        self.activity_group.hide()
        self.metrics_label.hide()
        flow_layout.addWidget(self.activity_group)

        # Download results are inserted here on demand, directly below task status.
        self.result_panel_index = flow_layout.count()

        self.log_group = QGroupBox("任务详情")
        log_layout = QVBoxLayout(self.log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setAccessibleName("任务详情日志")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1500)
        self.log_view.setMinimumHeight(180)
        log_layout.addWidget(self.log_view)
        self.log_group.hide()
        flow_layout.addWidget(self.log_group)
        flow_layout.addStretch(1)

        self.flow_scroll.setWidget(flow)
        root.addWidget(self.flow_scroll, 1)
        self.setCentralWidget(central)

    def _connect(self) -> None:
        self.url_edit.textChanged.connect(self.invalidate_current_video)
        self.parse_button.clicked.connect(self.start_parse)
        self.browse_button.clicked.connect(self.choose_download_dir)
        self.download_button.clicked.connect(self.start_download)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.qr_login_button.clicked.connect(self.start_qr_login)
        self.logout_button.clicked.connect(self.logout)
        self.clear_login_action.triggered.connect(self.logout)
        self.view_crash_log_action.triggered.connect(self.open_crash_log)
        self.diagnostics_action.triggered.connect(self.open_diagnostics)
        self.login_privacy_action.triggered.connect(self.show_login_privacy)
        self.toggle_log_action.toggled.connect(self._set_log_expanded)
        self.details_button.clicked.connect(self._toggle_log_expanded)
        self.select_all_button.clicked.connect(self.select_all_parts)
        self.select_first_button.clicked.connect(self.select_first_part)
        self.parts_list.itemChanged.connect(self._update_parts_summary)
        self.download_mode_combo.currentIndexChanged.connect(self._on_download_mode_changed)
        self.log_emitter.message.connect(self._append_log)

    @Slot()
    def _toggle_log_expanded(self) -> None:
        self.toggle_log_action.setChecked(not self.toggle_log_action.isChecked())

    @Slot(bool)
    def _set_log_expanded(self, expanded: bool) -> None:
        self.log_group.setVisible(expanded)
        self.toggle_log_action.setText("隐藏任务详情" if expanded else "显示任务详情")
        self.details_button.setText("隐藏任务详情" if expanded else "查看任务详情")

    def _set_view_phase(self, phase: str) -> None:
        if self._closing:
            phase = "closing"
        elif phase not in {"finished", "error", "closing"}:
            download_active = self.download_thread is not None and (
                phase == "downloading" or self._view_phase == "downloading"
            )
            parse_active = self.parse_thread is not None and (
                phase == "parsing" or self._view_phase == "parsing"
            )
            if download_active:
                phase = "downloading"
            elif parse_active:
                phase = "parsing"
        self._view_phase = phase
        has_video = self.current_info is not None
        part_count = len(self.current_info.parts) if self.current_info else 0
        downloading = phase == "downloading"
        closing = phase == "closing"
        show_activity = phase in {"changed", "parsing", "downloading", "finished", "error", "closing"}

        self.empty_state_label.setVisible(not has_video and not show_activity)
        self.video_group.setVisible(has_video and not closing)
        self.parts_group.setVisible(has_video and part_count > 1 and not downloading and not closing)
        self.download_group.setVisible(has_video and not downloading and not closing)
        self.activity_group.setVisible(show_activity)
        self.download_button.setVisible(not downloading)
        self.cancel_button.setVisible(downloading)
        self.cancel_button.setEnabled(downloading and not self._closing)
        self.progress_bar.setVisible(phase in {"parsing", "downloading", "finished"})
        self.metrics_label.setVisible(downloading and bool(self.metrics_label.text()))

        if phase == "parsing":
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
        if closing:
            self.log_group.hide()
            if self.result_panel is not None:
                self.result_panel.hide()

    def _update_parts_summary(self, _item: QListWidgetItem | None = None) -> None:
        count = self.parts_list.count()
        selected = sum(self.parts_list.item(index).checkState() == Qt.Checked for index in range(count))
        self.parts_summary_label.setText(f"已选 {selected} / {count}")

    @Slot()
    def show_login_privacy(self) -> None:
        QMessageBox.information(
            self,
            "登录与隐私说明",
            "扫码登录会在应用内显示 Bilibili 官方二维码，不输入账号密码，也不会读取 "
            "Chrome、Edge、Firefox 等日常浏览器 Cookie。\n\n"
            "手机确认后，只有经 Bilibili 服务端验证有效的登录态才会受保护地保存在本机应用数据目录，"
            "仅用于解析和下载你本来有权限观看的内容。",
        )

    def _load_config_into_ui(self) -> None:
        self.download_dir_edit.setText(self.config.download_dir)
        if self.safe_mode:
            self.set_login_status(
                "安全模式：本地凭据已禁用，解析和下载将匿名进行；重新扫码后可恢复登录模式。",
                "safe_mode",
            )
            self.clear_login_action.setVisible(has_saved_session())
            return
        if has_saved_session():
            self.set_login_status("检测登录状态中", "checking")
            self.start_session_validation()
        else:
            self.refresh_login_status()

    def set_login_status(self, status: str, code: str | None = None) -> None:
        if code is not None:
            self.login_status_code = code
        elif status == "已登录":
            self.login_status_code = "verified"
        elif "本地登录凭据" in status or "本地凭据" in status:
            self.login_status_code = "local_pending"
        elif "检测" in status or "等待" in status:
            self.login_status_code = "checking"
        elif "未登录" in status:
            self.login_status_code = "none"
        elif "失效" in status or "异常" in status:
            self.login_status_code = "invalid"

        display = {
            "verified": "已登录",
            "safe_mode": "匿名模式",
            "checking": "正在确认",
            "local_pending": "待确认",
            "offline": "待联网确认",
            "platform_412": "暂时无法确认",
            "protocol_error": "登录异常",
            "invalid": "登录已失效",
            "none": "未登录",
        }.get(self.login_status_code, status)
        self.login_status_label.setText(f"账号：{display}")
        self.login_status_label.setToolTip(
            f"登录状态：{status}\n\n登录态仅保存在本机应用数据目录；不会读取日常浏览器 Cookie。"
        )

        verified = self.login_status_code == "verified"
        checking = self.login_status_code == "checking"
        recoverable = self.login_status_code in {
            "local_pending",
            "offline",
            "platform_412",
            "protocol_error",
            "invalid",
        }
        self.qr_login_button.setText("重新扫码" if recoverable else "扫码登录")
        self.qr_login_button.setVisible(not verified and not checking)
        self.qr_login_button.setEnabled(not self._closing and not checking)
        self.logout_button.setVisible(verified)
        self.logout_button.setEnabled(verified and not self._closing)
        self.clear_login_action.setVisible(recoverable)

    def refresh_login_status(self) -> None:
        status = describe_login_status()
        self.set_login_status(status.text, status.code)

    def start_session_validation(self) -> None:
        if self._closing or (self.session_thread and self.session_thread.isRunning()):
            return
        thread = QThread(self)
        worker = SessionValidationWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.finished.connect(self.on_session_validated)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "session_thread", None))
        thread.finished.connect(lambda: setattr(self, "session_worker", None))
        self.session_worker = worker
        self.session_thread = thread
        thread.start()

    @Slot(str, str)
    def on_session_validated(self, code: str, text: str) -> None:
        if code == "cancelled" or self._closing:
            return
        self.set_login_status(text, code)
        if code == "verified":
            self._append_log("登录态验证成功。")
        elif code in {"invalid", "offline", "platform_412", "protocol_error", "local_pending", "none"}:
            self._append_log(f"登录态验证结果：{text}")

    @Slot()
    def choose_download_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择下载目录", self.download_dir_edit.text())
        if chosen:
            try:
                updated = AppConfig(download_dir=chosen)
                save_config(updated)
            except ConfigError as exc:
                self._append_log(f"保存配置失败：{redact_sensitive(exc)}")
                QMessageBox.warning(self, "保存配置失败", str(exc))
                return
            self.config = updated
            self.download_dir_edit.setText(updated.download_dir)

    @Slot()
    def open_crash_log(self) -> None:
        path = crash_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("打开 crash.log 失败")
            QMessageBox.information(self, "错误日志位置", f"无法自动打开日志。\n\n路径：{path}\n\n错误：{redact_sensitive(exc)}")

    @Slot()
    def open_diagnostics(self) -> None:
        if self._closing:
            return
        if self.diagnostics_dialog is not None:
            self.diagnostics_dialog.show()
            self.diagnostics_dialog.raise_()
            self.diagnostics_dialog.activateWindow()
            return
        dialog = DiagnosticsDialog(AppConfig(**asdict(self.config)), self)
        dialog.destroyed.connect(lambda: setattr(self, "diagnostics_dialog", None))
        self.diagnostics_dialog = dialog
        dialog.show()

    @Slot()
    def start_qr_login(self) -> None:
        if self._closing:
            return
        if self.login_dialog is not None:
            self.login_dialog.raise_()
            self.login_dialog.activateWindow()
            return
        result = QMessageBox.question(
            self,
            "保存登录态提示",
            "扫码登录会在应用内显示 Bilibili 官方二维码，不启动或读取任何日常浏览器。"
            "手机确认后，只有经 Bilibili 服务端验证有效的登录态才会受保护地保存在本机，"
            "用于后续解析和下载你本来有权限观看的视频清晰度。\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if result != QMessageBox.Yes:
            return

        self.set_login_status("等待扫码")
        dialog = LoginDialog(self)
        self.login_dialog = dialog
        dialog.status_for_main.connect(self.set_login_status)
        try:
            accepted = dialog.exec()
            # LoginDialog can only finish after its worker thread has stopped.
            if accepted and dialog.login_succeeded:
                self.credential_mode = CredentialMode.SAVED
                self.safe_mode = False
                self.set_login_status("登录凭据已通过服务端验证", "verified")
                self._append_log("扫码登录完成，已验证并保存受保护的本地凭据。")
                if self.url_edit.text().strip() and not self._closing:
                    if self.download_thread is not None:
                        self._append_log("登录成功；当前下载继续，任务结束后重新解析可刷新可用清晰度。")
                    else:
                        self._append_log("登录成功，正在重新解析当前链接以刷新可用清晰度。")
                        self.start_parse()
            elif not self._closing:
                if has_saved_session():
                    self.start_session_validation()
                else:
                    self.refresh_login_status()
        finally:
            self.login_dialog = None

    @Slot()
    def logout(self) -> None:
        if self._active_threads():
            QMessageBox.information(
                self,
                "暂时无法退出登录",
                "解析、验证、登录或下载任务仍在使用登录态。请先取消任务并等待其完全结束。",
            )
            return
        result = QMessageBox.question(
            self,
            "退出登录",
            "将删除本程序保存的 Bilibili 登录态、临时 Cookie lease 和兼容迁移残留。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        clear_result = clear_login_state()
        self.invalidate_current_video()
        if clear_result.ok:
            self.credential_mode = CredentialMode.ANONYMOUS
            self.set_login_status("无本地登录凭据", "none")
            self._append_log("已清除本程序保存的全部登录凭据。")
            return

        self.set_login_status("登录凭据清理失败，仍有残留", "invalid")
        details = "\n".join((*clear_result.failures, *clear_result.remaining)) or "未知清理错误"
        self._append_log(f"登录凭据清理失败：{details}")
        QMessageBox.warning(self, "清理登录凭据失败", f"以下项目未能清理：\n{details}")

    @Slot(str)
    def invalidate_current_video(self, _text: str = "") -> None:
        """Discard every download-capable object as soon as the input changes."""
        had_video = self.current_info is not None or self._parsed_url is not None
        had_active_parse = self.parse_thread is not None and self._view_phase == "parsing"
        had_active_download = self.download_thread is not None and self._view_phase == "downloading"
        if self.parse_worker:
            self.parse_worker.request_cancel()
        self.current_info = None
        self.current_formats = []
        self._parsed_url = None
        self.parts_list.clear()
        self.format_combo.clear()
        self.title_label.setText("-")
        self.uploader_label.setText("-")
        self.duration_label.setText("-")
        self.parts_label.setText("-")
        self.cover_label.clear()
        self.cover_label.setText("暂无封面")
        self.title_label.setToolTip("")
        self.format_note_label.clear()
        self.format_note_label.hide()
        self._update_parts_summary()
        self.download_button.setEnabled(False)
        self._retry_context_valid = False
        if self.result_panel is not None:
            self.result_panel.invalidate_retry()
        if not self._closing and had_active_download:
            if had_video:
                self._append_log("链接已更改；当前下载继续，新链接需等待任务结束后重新解析。")
            self._set_view_phase("downloading")
        elif not self._closing and (had_video or had_active_parse):
            self.status_label.setText(
                "链接已更改，正在取消旧解析..." if had_active_parse else "链接已更改，请重新解析"
            )
            self._set_view_phase("changed")
        elif not self._closing:
            self.status_label.setText("待命")
            self._set_view_phase("idle")

    def _input_matches(self, source_url: str) -> bool:
        try:
            return normalize_bilibili_url(self.url_edit.text()) == source_url
        except ValueError:
            return False

    def _can_download_current(self) -> bool:
        return bool(self.current_info and self._parsed_url and self._input_matches(self._parsed_url))

    @Slot()
    def start_parse(self) -> None:
        if self._closing or self.download_thread is not None:
            return
        self.invalidate_current_video()
        try:
            url = normalize_bilibili_url(self.url_edit.text())
        except ValueError as exc:
            self.status_label.setText(str(exc))
            self._set_view_phase("error")
            QMessageBox.warning(self, "链接无效", str(exc))
            return

        if self.parse_thread and self.parse_thread.isRunning():
            return

        self.parse_button.setEnabled(False)
        self.parse_button.setText("解析中...")
        self.download_button.setEnabled(False)
        self.status_label.setText("正在解析视频信息...")
        self.progress_bar.setValue(0)
        self._append_log(f"解析链接：{url}")

        thread = QThread(self)
        worker = ParseWorker(url, AppConfig(**asdict(self.config)), self.credential_mode)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.thumbnail.connect(self.set_thumbnail)
        worker.finished.connect(self.on_parse_finished)
        worker.failed.connect(self.on_parse_failed)
        worker.cancelled.connect(self.on_parse_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.on_parse_thread_finished)
        self.parse_worker = worker
        self.parse_thread = thread
        self._set_view_phase("parsing")
        thread.start()

    @Slot()
    def on_parse_thread_finished(self) -> None:
        self.parse_thread = None
        self.parse_worker = None
        download_active = self.download_thread is not None and self._view_phase == "downloading"
        if not self._closing and not download_active:
            self.parse_button.setText("解析视频")
            self.parse_button.setEnabled(True)
            self.download_button.setEnabled(self._can_download_current())
            if self.current_info is not None:
                self._set_view_phase("ready")
            elif self._view_phase == "parsing":
                self._set_view_phase("changed" if self.url_edit.text().strip() else "idle")

    @Slot(str, object)
    def on_parse_finished(self, source_url: str, result: VideoInfoResult) -> None:
        if not self._input_matches(source_url) or self._closing:
            self._append_log("解析结果已过期，已丢弃。")
            return
        if self.download_thread is not None and self._view_phase == "downloading":
            self._append_log("下载期间收到新的解析结果，已忽略；当前下载继续。")
            return
        self.current_info = result
        self.current_formats = result.formats
        self._parsed_url = source_url
        self.title_label.setText(result.title)
        self.title_label.setToolTip(result.title)
        self.uploader_label.setText(result.uploader)
        self.duration_label.setText(format_duration(result.duration))
        self.parts_label.setText(str(len(result.parts)))
        self.populate_parts(result.parts, result.current_part_index)
        self.populate_formats(result.formats)
        self._on_download_mode_changed()
        self.status_label.setText("解析成功")
        self._append_log(f"解析成功：{result.title}")
        if self._selected_download_mode() is DownloadMode.AUDIO_VIDEO:
            self.notice_resolution_limits(result.formats)
        self.parse_button.setEnabled(True)
        self.parse_button.setText("解析视频")
        self.download_button.setEnabled(True)
        self._set_view_phase("ready")

    @Slot(str, str, str, str)
    def on_parse_failed(self, source_url: str, error_code: str, friendly: str, detail: str) -> None:
        if not self._input_matches(source_url) or self._closing:
            return
        if self.download_thread is not None and self._view_phase == "downloading":
            self._append_log(f"下载期间的解析失败已忽略：{friendly}")
            self._append_log(detail)
            return
        self.parse_button.setEnabled(True)
        self.download_button.setEnabled(False)
        self.status_label.setText(f"解析失败：{friendly}")
        if error_code == ErrorKind.LOGIN_INVALID.value:
            self.set_login_status("解析遇到登录相关错误，正在向服务端复核", "local_pending")
            self.start_session_validation()
        self._append_log(f"解析失败：{friendly}")
        self._append_log(detail)
        self._set_view_phase("error")
        QMessageBox.warning(self, "解析失败", f"{friendly}\n\n详细信息：{detail}")

    @Slot(str)
    def on_parse_cancelled(self, _source_url: str) -> None:
        download_active = self.download_thread is not None and self._view_phase == "downloading"
        if not self._closing and not download_active:
            self.parse_button.setEnabled(True)
            self.parse_button.setText("解析视频")
            self.download_button.setEnabled(False)
            self.status_label.setText("解析已取消")
            self._set_view_phase("changed" if self.url_edit.text().strip() else "idle")

    def notice_resolution_limits(self, choices: list[FormatChoice], *, emit_log: bool = True) -> None:
        max_height = max((choice.height or 0 for choice in choices), default=0)
        if max_height >= 1080:
            return
        if self.login_status_code in {"verified", "local_pending", "offline"}:
            if emit_log:
                self._append_log("当前账号无该清晰度权限或视频本身不提供该清晰度；程序不会绕过会员、付费、地区或 DRM 限制。")
            self.format_note_label.setText("当前账号可用画质已全部列出；不会绕过会员、付费、地区或 DRM 限制。")
        else:
            if emit_log:
                self._append_log("未登录时可能只能解析普通清晰度；如需 1080p 及以上清晰度，请扫码登录后重新解析。")
            self.format_note_label.setText("未登录时可能只有普通画质；扫码登录后重新解析，可能获得更多画质。")
        self.format_note_label.show()

    @Slot(int)
    def _on_download_mode_changed(self, _index: int = -1) -> None:
        audio_only = self._selected_download_mode() is DownloadMode.AUDIO_MP3
        self.format_field_label.setVisible(not audio_only)
        self.format_combo.setVisible(not audio_only)
        self.download_button.setText("下载音频" if audio_only else "下载")
        self.format_note_label.clear()
        self.format_note_label.hide()
        if audio_only:
            self.format_note_label.setText("将下载最佳可用音轨，并通过 FFmpeg 转换为 192 kbps MP3。")
            self.format_note_label.show()
        elif self.current_info is not None:
            self.notice_resolution_limits(self.current_formats, emit_log=False)

    def _selected_download_mode(self) -> DownloadMode:
        value = self.download_mode_combo.currentData()
        try:
            return DownloadMode(value)
        except ValueError:
            return DownloadMode.AUDIO_VIDEO

    @Slot(str, bytes)
    def set_thumbnail(self, source_url: str, data: bytes) -> None:
        if not data or not self._input_matches(source_url) or self._closing:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.cover_label.setPixmap(
                pixmap.scaled(self.cover_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def populate_parts(self, parts: list[VideoPart], selected_index: int = 1) -> None:
        self.parts_list.clear()
        for part in parts:
            item = QListWidgetItem(f"P{part.index}  {part.title}  [{format_duration(part.duration)}]")
            item.setData(Qt.UserRole, part)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if part.index == selected_index else Qt.Unchecked)
            self.parts_list.addItem(item)
        self._update_parts_summary()

    def populate_formats(self, choices: list[FormatChoice]) -> None:
        self.format_combo.clear()
        for choice in choices:
            label = choice.label.replace("最高可用（各分 P 分别选择）", "最高可用（推荐）")
            label = label.replace("（严格匹配，不降档）", "")
            self.format_combo.addItem(label, choice.selector)

    @Slot()
    def select_all_parts(self) -> None:
        for i in range(self.parts_list.count()):
            self.parts_list.item(i).setCheckState(Qt.Checked)
        self._update_parts_summary()

    @Slot()
    def select_first_part(self) -> None:
        for i in range(self.parts_list.count()):
            self.parts_list.item(i).setCheckState(Qt.Checked if i == 0 else Qt.Unchecked)
        self._update_parts_summary()

    def selected_parts(self) -> list[VideoPart]:
        parts: list[VideoPart] = []
        for i in range(self.parts_list.count()):
            item = self.parts_list.item(i)
            if item.checkState() == Qt.Checked:
                parts.append(item.data(Qt.UserRole))
        return parts

    @Slot()
    def start_download(self) -> None:
        if self._closing:
            return
        if not self._can_download_current():
            self.invalidate_current_video()
            QMessageBox.information(self, "请先解析", "请先解析视频，再开始下载。")
            return
        parts = self.selected_parts()
        if not parts:
            QMessageBox.information(self, "请选择分 P", "请至少选择一个分 P。")
            return

        download_dir = self.download_dir_edit.text().strip()
        if not download_dir:
            QMessageBox.information(self, "请选择目录", "请选择下载目录。")
            return

        try:
            updated_config = AppConfig(download_dir=download_dir)
            save_config(updated_config)
        except ConfigError as exc:
            self._append_log(f"保存配置失败：{redact_sensitive(exc)}")
            QMessageBox.warning(self, "保存配置失败", str(exc))
            return
        self.config = updated_config

        mode = self._selected_download_mode()
        if mode is DownloadMode.AUDIO_MP3:
            selector = "bestaudio/best"
            format_label = "仅音频（MP3，192 kbps）"
        else:
            selector = str(self.format_combo.currentData() or "bestvideo+bestaudio/best")
            format_label = f"音视频（MP4） · {self.format_combo.currentText() or '自动选择'}"
        self._append_log(f"下载规格：{format_label}")

        request = DownloadRequest(
            source_url=str(self._parsed_url),
            video_title=self.current_info.title,
            parts=tuple(parts),
            config=AppConfig(**asdict(self.config)),
            download_dir=download_dir,
            format_selector=selector,
            format_label=format_label,
            credential_mode=self.credential_mode,
            mode=mode,
        )
        self.download_request = request
        self._retry_context_valid = True
        self._download_is_retry = False
        if self.result_panel is not None:
            self.result_panel.hide()
        self._begin_download(request.parts)

    def _begin_download(self, parts: tuple[VideoPart, ...]) -> None:
        request = self.download_request
        if self._closing or request is None or self.download_thread is not None or not parts:
            return

        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.parse_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.metrics_label.clear()
        self.metrics_label.setToolTip("")
        self.status_label.setText("准备下载...")
        self._active_download_parts = parts

        self.download_controller = DownloadController()
        thread = QThread(self)
        worker = DownloadWorker(
            list(parts),
            AppConfig(**asdict(request.config)),
            request.download_dir,
            request.format_selector,
            self.download_controller,
            request.credential_mode,
            request.mode,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.progress.connect(self.on_download_progress)
        worker.finished.connect(self.on_download_finished)
        worker.failed.connect(self.on_download_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.on_download_thread_finished)
        self.download_worker = worker
        self.download_thread = thread
        self._set_view_phase("downloading")
        thread.start()

    @Slot()
    def on_download_thread_finished(self) -> None:
        self.download_thread = None
        self.download_worker = None
        self.download_controller = None
        self._active_download_parts = ()
        if self.result_panel is not None:
            self.result_panel.set_busy(False)
        if not self._closing:
            self.cancel_button.setEnabled(False)
            self.parse_button.setEnabled(True)
            self.download_button.setEnabled(self._can_download_current())
            if self._view_phase == "downloading":
                self._set_view_phase("ready" if self.current_info is not None else "idle")
            else:
                self._set_view_phase(self._view_phase)

    @Slot()
    def cancel_download(self) -> None:
        if self.download_controller:
            self.download_controller.cancel()
            if self.download_controller.waiting_for_postprocessing:
                text = "正在安全结束当前文件处理..."
                detail = "已请求取消，正在等待当前 FFmpeg 处理安全结束..."
            else:
                text = "正在取消下载..."
                detail = text
            self.status_label.setText(text)
            self._append_log(detail)

    @Slot(dict)
    def on_download_progress(self, status: dict[str, Any]) -> None:
        if self._closing:
            return
        raw_status = status.get("status", "")
        downloaded = status.get("downloaded_bytes") or 0
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        info_dict = status.get("info_dict") if isinstance(status.get("info_dict"), dict) else {}
        filename = status.get("filename") or info_dict.get("filepath") or "-"
        speed = status.get("speed")
        eta = status.get("eta")
        overall = status.get("overall_percent")
        if isinstance(overall, (int, float)):
            self.progress_bar.setValue(max(self.progress_bar.value(), min(100, int(overall))))

        part_number = status.get("part_number") or "-"
        part_count = status.get("part_count") or "-"
        phase = status.get("phase") or raw_status
        if phase == "downloading":
            self.status_label.setText(f"正在下载第 {part_number}/{part_count} 个分 P")
        elif phase == "merging":
            if self.download_controller and self.download_controller.waiting_for_postprocessing:
                self.status_label.setText("正在安全结束当前文件处理...")
            else:
                self.status_label.setText(f"正在合并第 {part_number}/{part_count} 个分 P")
        elif phase == "converting":
            if self.download_controller and self.download_controller.waiting_for_postprocessing:
                self.status_label.setText("正在安全结束当前文件处理...")
            else:
                self.status_label.setText(f"正在转换第 {part_number}/{part_count} 个分 P 为 MP3")
        elif phase == "completed":
            self.status_label.setText(f"第 {part_number}/{part_count} 个分 P 已完成")
        elif phase == "failed":
            self.status_label.setText(f"第 {part_number}/{part_count} 个分 P 失败，继续处理其余任务")

        full_filename = str(filename)
        try:
            display_filename = Path(full_filename).name or full_filename
        except (OSError, TypeError, ValueError):
            display_filename = full_filename
        self.metrics_label.setText(
            f"{format_bytes(downloaded)} / {format_bytes(total)}  ·  "
            f"{format_speed(speed)}  ·  剩余 {format_eta(eta)}  ·  {display_filename}"
        )
        self.metrics_label.setToolTip(full_filename if full_filename != "-" else "")
        self.metrics_label.show()
        self._set_view_phase("downloading")

    @Slot(object)
    def on_download_finished(self, result: DownloadBatchResult | list[str]) -> None:
        if not self._closing:
            self.download_button.setEnabled(self._can_download_current())
            self.cancel_button.setEnabled(False)
            self.parse_button.setEnabled(True)
        if not isinstance(result, DownloadBatchResult):
            legacy_files = tuple(dict.fromkeys(result))
            result = DownloadBatchResult(
                PartDownloadResult(
                    part,
                    PartDownloadStatus.COMPLETED,
                    legacy_files if index == 0 else (),
                )
                for index, part in enumerate(self._active_download_parts)
            )

        self._handle_download_result(result)

    def _handle_download_result(self, result: DownloadBatchResult) -> None:

        completed = result.completed
        failed = result.failed
        cancelled = tuple(item for item in result.part_results if item.status is PartDownloadStatus.CANCELLED)
        if not cancelled and not self._closing:
            self.progress_bar.setValue(100)
        summary = f"任务结束：成功 {len(completed)}，失败 {len(failed)}，取消 {len(cancelled)}。"
        if not self._closing:
            self.status_label.setText(summary)
        self._append_log(summary)
        for item in completed:
            for path in item.saved_files:
                self._append_log(f"P{item.part.index} 已保存：{path}")
        for item in failed:
            message = item.error.message if item.error else "下载失败"
            self._append_log(f"P{item.part.index} 失败：{message}；{item.detail}")

        if self._closing:
            return

        if any(item.error and item.error.kind is ErrorKind.LOGIN_INVALID for item in failed):
            self.set_login_status("下载遇到登录相关错误，正在向服务端复核", "local_pending")
            self.start_session_validation()

        self.metrics_label.hide()
        self._set_view_phase("error" if failed and not completed else "finished")

        request = self.download_request
        if self._download_is_retry and self.result_panel is not None:
            self.result_panel.merge_retry_result(result)
            self.result_panel.set_busy(False)
            self.result_panel.show()
        else:
            if self.result_panel is not None:
                self.result_panel.hide()
                self.result_panel.deleteLater()
            panel = DownloadResultPanel(
                result,
                video_title=request.video_title if request else "下载任务",
                format_label=request.format_label if request else "自动选择",
                parent=self,
            )
            if not self._retry_context_valid:
                panel.invalidate_retry()
            panel.retry_requested.connect(self.retry_failed_parts)
            self.flow_layout.insertWidget(self.result_panel_index, panel)
            self.result_panel = panel
            panel.show()
        self._download_is_retry = False

    @Slot(object)
    def retry_failed_parts(self, parts: tuple[VideoPart, ...]) -> None:
        request = self.download_request
        if (
            self._closing
            or not self._retry_context_valid
            or request is None
            or not self._input_matches(request.source_url)
            or self.download_thread is not None
        ):
            if self.result_panel is not None:
                self.result_panel.invalidate_retry()
            return
        self._download_is_retry = True
        self._begin_download(tuple(parts))

    @Slot(object, str)
    def on_download_failed(self, classified: ErrorClassification, detail: str) -> None:
        if not self._closing:
            self.download_button.setEnabled(self._can_download_current())
            self.cancel_button.setEnabled(False)
            self.parse_button.setEnabled(True)
            self.status_label.setText(classified.message)
        self._append_log(f"下载失败：{classified.message}")
        self._append_log(detail)
        if classified.kind is ErrorKind.LOGIN_INVALID and not self._closing:
            self.set_login_status("下载遇到登录相关错误，正在向服务端复核", "local_pending")
            self.start_session_validation()
        result = DownloadBatchResult(
            PartDownloadResult(
                part,
                PartDownloadStatus.FAILED,
                error=classified,
                detail=detail,
            )
            for part in self._active_download_parts
        )
        self._handle_download_result(result)

    def _active_threads(self) -> list[QThread]:
        candidates = [self.parse_thread, self.session_thread, self.download_thread]
        if self.login_dialog is not None:
            candidates.append(self.login_dialog.thread)
        if self.diagnostics_dialog is not None:
            candidates.extend(self.diagnostics_dialog.active_threads())
        active: list[QThread] = []
        for thread in candidates:
            if thread is None or thread in active:
                continue
            try:
                if thread.isRunning():
                    active.append(thread)
            except RuntimeError:
                continue
        return active

    def _request_shutdown(self) -> None:
        if self.parse_worker:
            self.parse_worker.request_cancel()
        if self.session_worker:
            self.session_worker.request_cancel()
        if self.login_dialog is not None:
            self.login_dialog.request_shutdown()
        if self.diagnostics_dialog is not None:
            self.diagnostics_dialog.request_shutdown()
        if self.download_controller:
            self.download_controller.cancel()
        for thread in self._active_threads():
            thread.requestInterruption()
            # quit() is cooperative: it never destroys a running worker.
            thread.quit()

    def _wait_for_shutdown(self, timeout_ms: int = 1500) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        for thread in self._active_threads():
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms <= 0 or not thread.wait(remaining_ms):
                return False
        return not self._active_threads()

    def _finish_close_when_idle(self) -> None:
        if not self._closing:
            return
        if self._active_threads():
            QTimer.singleShot(100, self._finish_close_when_idle)
            return
        self._allow_close = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            super().closeEvent(event)
            return

        self._closing = True
        self.url_edit.setEnabled(False)
        self.parse_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.qr_login_button.setEnabled(False)
        self.logout_button.setEnabled(False)
        self.more_button.setEnabled(False)
        self.details_button.setEnabled(False)
        self.diagnostics_action.setEnabled(False)
        self.status_label.setText("正在安全关闭，请稍候...")
        self._set_view_phase("closing")
        self._request_shutdown()

        if not self._shutdown_wait_attempted:
            self._shutdown_wait_attempted = True
            if self._wait_for_shutdown():
                self._allow_close = True
                super().closeEvent(event)
                return

        event.ignore()
        QTimer.singleShot(100, self._finish_close_when_idle)

    @Slot(str)
    def _append_log(self, message: str) -> None:
        message = redact_sensitive(message)
        self.log_view.appendPlainText(message)
        logging.getLogger("bili_downloader").info(message)


def run_app(self_test: bool = False, safe_mode: bool = False) -> int:
    app = QApplication(sys.argv)
    window = MainWindow(safe_mode=safe_mode)
    window.show()

    if self_test:
        # Exercise MainWindow.closeEvent instead of bypassing graceful shutdown.
        QTimer.singleShot(350, window.close)

    return app.exec()
