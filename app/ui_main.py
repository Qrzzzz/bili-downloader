from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import AppConfig, ConfigError, config_diagnostics, load_config, save_config
from .cookies import (
    CredentialMode,
    clear_login_state,
    cookies_indicate_logged_in,
    describe_login_status,
    ensure_playwright_runtime,
    has_saved_session,
    save_context_storage_state_atomic,
    validate_saved_session,
)
from .crash import crash_log_path
from .downloader import (
    DownloadBatchCancelled,
    DownloadBatchResult,
    DownloadController,
    FormatChoice,
    PartDownloadStatus,
    VideoInfoResult,
    VideoPart,
    download_videos,
    fetch_thumbnail,
    parse_video_info,
)
from .logger import LogEmitter, redact_sensitive, setup_logging
from .utils import (
    ErrorKind,
    classify_error_details,
    ffmpeg_status_text,
    format_bytes,
    format_duration,
    format_eta,
    format_speed,
    normalize_bilibili_url,
)


LOGIN_URL = "https://passport.bilibili.com/login"


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
    failed = Signal(str, str, str)
    log = Signal(str)

    def __init__(
        self,
        parts: list[VideoPart],
        config: AppConfig,
        download_dir: str,
        format_selector: str,
        controller: DownloadController,
        credential_mode: CredentialMode,
    ) -> None:
        super().__init__()
        self.parts = parts
        self.config = config
        self.download_dir = download_dir
        self.format_selector = format_selector
        self.controller = controller
        self.credential_mode = credential_mode
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
            )
            self.finished.emit(saved)
        except DownloadBatchCancelled as exc:
            self.finished.emit(exc.result)
        except Exception as exc:  # noqa: BLE001
            classified = classify_error_details(exc)
            self.failed.emit(classified.code, classified.message, redact_sensitive(exc))


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


class LoginWorker(QObject):
    status = Signal(str)
    screenshot = Signal(bytes)
    completed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()
        self._refresh_requested = threading.Event()
        self.terminal_outcome: LoginOutcome | None = None

    def request_cancel(self) -> None:
        self._cancelled.set()

    def request_refresh(self) -> None:
        self._refresh_requested.set()

    def _launch_browser(self, playwright):
        launch_kwargs = {
            "headless": False,
        }
        errors: list[str] = []
        for channel in (None, "chrome", "msedge"):
            try:
                if channel:
                    return playwright.chromium.launch(channel=channel, **launch_kwargs)
                return playwright.chromium.launch(**launch_kwargs)
            except Exception as exc:  # noqa: BLE001
                label = "Playwright Chromium" if channel is None else channel
                errors.append(f"{label}: {redact_sensitive(exc)}")
        raise RuntimeError("无法启动扫码登录浏览器。\n" + "\n".join(errors))

    @Slot()
    def run(self) -> None:
        outcome: LoginOutcome | None = None
        try:
            ensure_playwright_runtime()
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("Playwright 初始化失败")
            outcome = LoginOutcome(
                "failed",
                "Playwright 未安装或浏览器依赖缺失。",
                f"{redact_sensitive(exc)}\n请运行 build.ps1，或手动执行：python -m playwright install chromium",
            )
        else:
            browser = None
            context = None
            try:
                self.status.emit("请使用 Bilibili App 扫码登录")
                with sync_playwright() as playwright:
                    try:
                        browser = self._launch_browser(playwright)
                        context = browser.new_context(
                            locale="zh-CN",
                            viewport={"width": 520, "height": 720},
                        )
                        page = context.new_page()
                        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                        page.bring_to_front()

                        last_screenshot = 0.0
                        deadline = time.monotonic() + 300
                        while not self._cancelled.is_set() and time.monotonic() < deadline:
                            if self._refresh_requested.is_set():
                                self._refresh_requested.clear()
                                self.status.emit("请使用 Bilibili App 扫码登录")
                                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                                page.bring_to_front()

                            cookies = context.cookies(
                                [
                                    "https://www.bilibili.com",
                                    "https://passport.bilibili.com",
                                    "https://api.bilibili.com",
                                ]
                            )
                            if cookies_indicate_logged_in(cookies):
                                self.status.emit("登录成功，正在保存登录状态")
                                save_context_storage_state_atomic(context)
                                outcome = LoginOutcome("success")
                                break

                            try:
                                body_text = page.locator("body").inner_text(timeout=1000)
                                if any(
                                    token in body_text
                                    for token in ("扫码成功", "扫描成功", "确认登录", "请在手机")
                                ):
                                    self.status.emit("已扫码，请在手机上确认")
                                elif any(
                                    token in body_text for token in ("二维码已失效", "二维码已过期", "刷新二维码")
                                ):
                                    self.status.emit("登录失败或二维码已过期，请重试")
                            except PlaywrightError:
                                pass

                            now = time.monotonic()
                            if now - last_screenshot > 1.0:
                                try:
                                    self.screenshot.emit(page.screenshot(type="png", full_page=False))
                                    last_screenshot = now
                                except PlaywrightError:
                                    pass
                            page.wait_for_timeout(500)

                        if outcome is None:
                            if self._cancelled.is_set():
                                outcome = LoginOutcome("cancelled", "已取消扫码登录。")
                            else:
                                outcome = LoginOutcome(
                                    "timeout",
                                    "登录失败或二维码已过期，请重试。",
                                    "扫码登录超过 5 分钟未完成。",
                                )
                    finally:
                        # Playwright objects must be closed before leaving sync_playwright().
                        if context is not None:
                            try:
                                context.close()
                            except Exception:  # noqa: BLE001
                                logging.getLogger("bili_downloader").exception("关闭扫码登录浏览器上下文失败")
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception:  # noqa: BLE001
                                logging.getLogger("bili_downloader").exception("关闭扫码登录浏览器失败")
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("bili_downloader").exception("扫码登录流程失败")
                outcome = LoginOutcome("failed", "扫码登录失败。", redact_sensitive(exc))

        if outcome is None:
            outcome = LoginOutcome("failed", "扫码登录失败。", "登录线程未产生终态。")
        self.terminal_outcome = outcome
        self.completed.emit(outcome)


class LoginDialog(QDialog):
    status_for_main = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bilibili 扫码登录")
        self.resize(600, 760)
        self.worker: LoginWorker | None = None
        self.thread: QThread | None = None
        self._finished_thread: QThread | None = None
        self.login_succeeded = False
        self._pending_outcome: LoginOutcome | None = None
        self._final_outcome: LoginOutcome | None = None
        self._dismiss_requested = False

        layout = QVBoxLayout(self)
        self.preview_label = QLabel("正在打开 Bilibili 官方登录页面...")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(520, 620)
        self.preview_label.setStyleSheet("QLabel { border: 1px solid #d0d0d0; background: #f8f8f8; color: #555; }")
        self.status_label = QLabel("请使用 Bilibili App 扫码登录")
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
        thread = QThread(self)
        worker = LoginWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.status.connect(self.on_status)
        worker.screenshot.connect(self.on_screenshot)
        worker.completed.connect(self.on_terminal)
        worker.completed.connect(thread.quit)
        thread.finished.connect(self.on_thread_finished)
        thread.finished.connect(worker.deleteLater)
        self.worker = worker
        self.thread = thread
        thread.start()

    @Slot()
    def refresh_qr(self) -> None:
        if not self.thread or not self.thread.isRunning() or self._dismiss_requested:
            return
        self.status_label.setText("请使用 Bilibili App 扫码登录")
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
        elif "成功" in text:
            self.status_for_main.emit("正在保存本地登录凭据")
        elif "失败" in text or "过期" in text:
            self.status_for_main.emit("登录已失效")
        else:
            self.status_for_main.emit("等待扫码")

    @Slot(bytes)
    def on_screenshot(self, data: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.preview_label.setPixmap(
                pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
        self.setWindowTitle(f"合规用途 Bilibili 视频下载器 V{__version__}")
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
        self.safe_mode = safe_mode
        self.credential_mode = CredentialMode.ANONYMOUS if safe_mode else CredentialMode.SAVED
        self.login_status_code = "none"
        self._parsed_url: str | None = None
        self._closing = False
        self._allow_close = False
        self._shutdown_wait_attempted = False

        self.log_emitter = LogEmitter()
        self.logger = setup_logging()

        self._build_ui()
        self._connect()
        self._load_config_into_ui()
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
        root.setSpacing(10)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴 Bilibili 视频链接，或输入 BV/av 号")
        self.parse_button = QPushButton("解析视频")
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.parse_button)
        root.addLayout(url_row)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)

        info_group = QGroupBox("视频信息")
        info_layout = QGridLayout(info_group)
        self.cover_label = QLabel("封面")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(240, 150)
        self.cover_label.setStyleSheet("QLabel { border: 1px solid #d0d0d0; background: #f7f7f7; color: #666; }")
        self.title_label = QLabel("-")
        self.title_label.setWordWrap(True)
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
        left_layout.addWidget(info_group)

        parts_group = QGroupBox("分 P 选择")
        parts_layout = QVBoxLayout(parts_group)
        part_actions = QHBoxLayout()
        self.select_all_button = QPushButton("全选")
        self.select_first_button = QPushButton("仅第一 P")
        part_actions.addWidget(self.select_all_button)
        part_actions.addWidget(self.select_first_button)
        part_actions.addStretch(1)
        self.parts_list = QListWidget()
        self.parts_list.setSelectionMode(QAbstractItemView.NoSelection)
        parts_layout.addLayout(part_actions)
        parts_layout.addWidget(self.parts_list, 1)
        left_layout.addWidget(parts_group, 1)

        download_group = QGroupBox("下载设置")
        form = QFormLayout(download_group)
        self.format_combo = QComboBox()
        self.download_dir_edit = QLineEdit()
        self.browse_button = QPushButton("选择目录")
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.download_dir_edit, 1)
        dir_row.addWidget(self.browse_button)
        self.download_button = QPushButton("下载")
        self.download_button.setEnabled(False)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.cancel_button)
        form.addRow("清晰度：", self.format_combo)
        form.addRow("保存目录：", dir_row)
        form.addRow("", buttons)
        left_layout.addWidget(download_group)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        login_group = QGroupBox("Bilibili 官方扫码登录")
        login_layout = QVBoxLayout(login_group)
        self.login_status_label = QLabel("登录状态：未登录")
        self.login_status_label.setWordWrap(True)
        login_buttons = QHBoxLayout()
        self.qr_login_button = QPushButton("扫码登录")
        self.logout_button = QPushButton("退出登录 / 清除登录状态")
        self.view_crash_log_button = QPushButton("查看错误日志")
        login_buttons.addWidget(self.qr_login_button)
        login_buttons.addWidget(self.logout_button)
        login_buttons.addWidget(self.view_crash_log_button)
        compliance = QLabel(
            "不输入账号密码，不读取 Chrome/Edge/Firefox 等日常浏览器 Cookie。扫码登录只使用本程序打开的 Bilibili 官方登录页，登录态仅保存在本机应用数据目录。"
        )
        compliance.setWordWrap(True)
        compliance.setStyleSheet("color: #555;")
        login_layout.addWidget(self.login_status_label)
        login_layout.addLayout(login_buttons)
        login_layout.addWidget(compliance)
        right_layout.addWidget(login_group)

        progress_group = QGroupBox("进度")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QLabel("待命")
        self.metrics_label = QLabel("速度：-    剩余：-    文件：-")
        self.metrics_label.setWordWrap(True)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.metrics_label)
        right_layout.addWidget(progress_group)

        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1500)
        log_layout.addWidget(self.log_view)
        right_layout.addWidget(log_group, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([620, 420])
        root.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))

    def _connect(self) -> None:
        self.url_edit.textChanged.connect(self.invalidate_current_video)
        self.parse_button.clicked.connect(self.start_parse)
        self.browse_button.clicked.connect(self.choose_download_dir)
        self.download_button.clicked.connect(self.start_download)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.qr_login_button.clicked.connect(self.start_qr_login)
        self.logout_button.clicked.connect(self.logout)
        self.view_crash_log_button.clicked.connect(self.open_crash_log)
        self.select_all_button.clicked.connect(self.select_all_parts)
        self.select_first_button.clicked.connect(self.select_first_part)
        self.log_emitter.message.connect(self._append_log)

    def _load_config_into_ui(self) -> None:
        self.download_dir_edit.setText(self.config.download_dir)
        if self.safe_mode:
            self.set_login_status(
                "安全模式：本地凭据已禁用，解析和下载将匿名进行；重新扫码后可恢复登录模式。",
                "safe_mode",
            )
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
        self.login_status_label.setText(f"登录状态：{status}")

    def refresh_login_status(self) -> None:
        status = describe_login_status()
        self.set_login_status(status.text, status.code)

    def start_session_validation(self) -> None:
        if self.session_thread and self.session_thread.isRunning():
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
        elif code in {"invalid", "offline", "local_pending", "none"}:
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
    def start_qr_login(self) -> None:
        if self.login_dialog is not None:
            self.login_dialog.raise_()
            self.login_dialog.activateWindow()
            return
        result = QMessageBox.question(
            self,
            "保存登录态提示",
            "扫码登录会打开 Bilibili 官方登录页面。登录成功后，本程序会把登录态仅保存在本机应用数据目录，用于后续解析和下载你本来有权限观看的视频清晰度。\n\n是否继续？",
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
                self.set_login_status("本地登录凭据待服务端验证", "local_pending")
                self._append_log("扫码登录完成，已保存受保护的本地凭据，正在请求服务端验证。")
                self.start_session_validation()
                if self.url_edit.text().strip() and not self._closing:
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
            "将删除本程序保存的 Bilibili 登录态和内部 cookies.txt。是否继续？",
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
        self.cover_label.setText("封面")
        self.download_button.setEnabled(False)
        if self.url_edit.text().strip() and not self._closing:
            self.status_label.setText("链接已更改，请重新解析")

    def _input_matches(self, source_url: str) -> bool:
        try:
            return normalize_bilibili_url(self.url_edit.text()) == source_url
        except ValueError:
            return False

    def _can_download_current(self) -> bool:
        return bool(self.current_info and self._parsed_url and self._input_matches(self._parsed_url))

    @Slot()
    def start_parse(self) -> None:
        self.invalidate_current_video()
        try:
            url = normalize_bilibili_url(self.url_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "链接无效", str(exc))
            return

        if self.parse_thread and self.parse_thread.isRunning():
            return

        self.parse_button.setEnabled(False)
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
        thread.start()

    @Slot()
    def on_parse_thread_finished(self) -> None:
        self.parse_thread = None
        self.parse_worker = None
        if not self._closing:
            self.parse_button.setEnabled(True)
            self.download_button.setEnabled(self._can_download_current())

    @Slot(str, object)
    def on_parse_finished(self, source_url: str, result: VideoInfoResult) -> None:
        if not self._input_matches(source_url) or self._closing:
            self._append_log("解析结果已过期，已丢弃。")
            return
        self.current_info = result
        self.current_formats = result.formats
        self._parsed_url = source_url
        self.title_label.setText(result.title)
        self.uploader_label.setText(result.uploader)
        self.duration_label.setText(format_duration(result.duration))
        self.parts_label.setText(str(len(result.parts)))
        self.populate_parts(result.parts, result.current_part_index)
        self.populate_formats(result.formats)
        self.status_label.setText("解析成功")
        self._append_log(f"解析成功：{result.title}")
        self.notice_resolution_limits(result.formats)
        self.parse_button.setEnabled(True)
        self.download_button.setEnabled(True)

    @Slot(str, str, str, str)
    def on_parse_failed(self, source_url: str, error_code: str, friendly: str, detail: str) -> None:
        self.parse_button.setEnabled(True)
        self.download_button.setEnabled(False)
        if not self._input_matches(source_url) or self._closing:
            return
        self.status_label.setText("解析失败")
        if error_code == ErrorKind.LOGIN_INVALID.value:
            self.set_login_status("解析遇到登录相关错误，正在向服务端复核", "local_pending")
            self.start_session_validation()
        self._append_log(f"解析失败：{friendly}")
        self._append_log(detail)
        QMessageBox.warning(self, "解析失败", f"{friendly}\n\n详细信息：{detail}")

    @Slot(str)
    def on_parse_cancelled(self, _source_url: str) -> None:
        if not self._closing:
            self.parse_button.setEnabled(True)
            self.download_button.setEnabled(False)

    def notice_resolution_limits(self, choices: list[FormatChoice]) -> None:
        max_height = max((choice.height or 0 for choice in choices), default=0)
        if max_height >= 1080:
            return
        if self.login_status_code in {"verified", "local_pending", "offline"}:
            self._append_log("当前账号无该清晰度权限或视频本身不提供该清晰度；程序不会绕过会员、付费、地区或 DRM 限制。")
        else:
            self._append_log("未登录时可能只能解析普通清晰度；如需 1080p 及以上清晰度，请扫码登录后重新解析。")

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

    def populate_formats(self, choices: list[FormatChoice]) -> None:
        self.format_combo.clear()
        for choice in choices:
            self.format_combo.addItem(choice.label, choice.selector)

    @Slot()
    def select_all_parts(self) -> None:
        for i in range(self.parts_list.count()):
            self.parts_list.item(i).setCheckState(Qt.Checked)

    @Slot()
    def select_first_part(self) -> None:
        for i in range(self.parts_list.count()):
            self.parts_list.item(i).setCheckState(Qt.Checked if i == 0 else Qt.Unchecked)

    def selected_parts(self) -> list[VideoPart]:
        parts: list[VideoPart] = []
        for i in range(self.parts_list.count()):
            item = self.parts_list.item(i)
            if item.checkState() == Qt.Checked:
                parts.append(item.data(Qt.UserRole))
        return parts

    @Slot()
    def start_download(self) -> None:
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

        selector = str(self.format_combo.currentData() or "bestvideo+bestaudio/best")
        self._append_log(f"下载格式选择器：{selector}")

        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.parse_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("准备下载...")

        self.download_controller = DownloadController()
        thread = QThread(self)
        worker = DownloadWorker(
            parts,
            AppConfig(**asdict(self.config)),
            download_dir,
            selector,
            self.download_controller,
            self.credential_mode,
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
        thread.start()

    @Slot()
    def on_download_thread_finished(self) -> None:
        self.download_thread = None
        self.download_worker = None
        self.download_controller = None
        if not self._closing:
            self.cancel_button.setEnabled(False)
            self.parse_button.setEnabled(True)
            self.download_button.setEnabled(self._can_download_current())

    @Slot()
    def cancel_download(self) -> None:
        if self.download_controller:
            self.download_controller.cancel()
            if self.download_controller.waiting_for_merge:
                text = "已请求取消，正在等待当前 FFmpeg 合并安全结束..."
            else:
                text = "正在取消下载..."
            self.status_label.setText(text)
            self._append_log(text)

    @Slot(dict)
    def on_download_progress(self, status: dict[str, Any]) -> None:
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
            if self.download_controller and self.download_controller.waiting_for_merge:
                self.status_label.setText("已请求取消，正在等待当前 FFmpeg 合并安全结束...")
            else:
                self.status_label.setText(f"正在合并第 {part_number}/{part_count} 个分 P")
        elif phase == "completed":
            self.status_label.setText(f"第 {part_number}/{part_count} 个分 P 已完成")
        elif phase == "failed":
            self.status_label.setText(f"第 {part_number}/{part_count} 个分 P 失败，继续处理其余任务")

        self.metrics_label.setText(
            f"已下载：{format_bytes(downloaded)} / {format_bytes(total)}    "
            f"速度：{format_speed(speed)}    剩余：{format_eta(eta)}    文件：{filename}"
        )

    @Slot(object)
    def on_download_finished(self, result: DownloadBatchResult | list[str]) -> None:
        self.download_button.setEnabled(self._can_download_current())
        self.cancel_button.setEnabled(False)
        self.parse_button.setEnabled(True)
        if not isinstance(result, DownloadBatchResult):
            saved = list(result)
            self.progress_bar.setValue(100)
            summary = f"下载完成，保存了 {len(saved)} 个文件。"
            details = "\n".join(saved)
            self.status_label.setText(summary)
            self._append_log(summary)
            if not self._closing:
                QMessageBox.information(self, "下载完成", f"{summary}\n\n{details}")
            return

        completed = result.completed
        failed = result.failed
        cancelled = tuple(item for item in result.part_results if item.status is PartDownloadStatus.CANCELLED)
        if not cancelled:
            self.progress_bar.setValue(100)
        summary = f"任务结束：成功 {len(completed)}，失败 {len(failed)}，取消 {len(cancelled)}。"
        self.status_label.setText(summary)
        self._append_log(summary)
        for item in completed:
            for path in item.saved_files:
                self._append_log(f"P{item.part.index} 已保存：{path}")
        for item in failed:
            message = item.error.message if item.error else "下载失败"
            self._append_log(f"P{item.part.index} 失败：{message}；{item.detail}")

        if any(item.error and item.error.kind is ErrorKind.LOGIN_INVALID for item in failed):
            self.set_login_status("下载遇到登录相关错误，正在向服务端复核", "local_pending")
            self.start_session_validation()

        saved_preview = "\n".join(result.saved_files[:10])
        if len(result.saved_files) > 10:
            saved_preview += f"\n……另有 {len(result.saved_files) - 10} 个文件"
        dialog_text = summary + (f"\n\n已保存文件：\n{saved_preview}" if saved_preview else "")
        if self._closing:
            return
        if failed:
            QMessageBox.warning(self, "下载任务部分失败", dialog_text)
        else:
            QMessageBox.information(self, "下载任务结束", dialog_text)

    @Slot(str, str, str)
    def on_download_failed(self, error_code: str, friendly: str, detail: str) -> None:
        self.download_button.setEnabled(self._can_download_current())
        self.cancel_button.setEnabled(False)
        self.parse_button.setEnabled(True)
        self.status_label.setText(friendly)
        self._append_log(f"下载失败：{friendly}")
        self._append_log(detail)
        if error_code == ErrorKind.LOGIN_INVALID.value:
            self.set_login_status("下载遇到登录相关错误，正在向服务端复核", "local_pending")
            self.start_session_validation()
        if not self._closing:
            QMessageBox.warning(self, "下载失败", f"{friendly}\n\n详细信息：{detail}")

    def _active_threads(self) -> list[QThread]:
        candidates = [self.parse_thread, self.session_thread, self.download_thread]
        if self.login_dialog is not None:
            candidates.append(self.login_dialog.thread)
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
        self.parse_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.qr_login_button.setEnabled(False)
        self.logout_button.setEnabled(False)
        self.status_label.setText("正在安全关闭，请稍候...")
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
