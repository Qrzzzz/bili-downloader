from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
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
from yt_dlp.utils import DownloadCancelled

from .config import AppConfig, load_config, save_config
from .cookies import (
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
    DownloadController,
    FormatChoice,
    VideoInfoResult,
    VideoPart,
    download_videos,
    fetch_thumbnail,
    parse_video_info,
)
from .logger import LogEmitter, redact_sensitive, setup_logging
from .utils import (
    classify_error,
    ffmpeg_status_text,
    format_bytes,
    format_duration,
    format_eta,
    format_speed,
    normalize_bilibili_url,
)


LOGIN_URL = "https://passport.bilibili.com/login"


class ParseWorker(QObject):
    finished = Signal(object)
    failed = Signal(str, str)
    thumbnail = Signal(bytes)
    log = Signal(str)

    def __init__(self, url: str, config: AppConfig) -> None:
        super().__init__()
        self.url = url
        self.config = config
        self.emitter = LogEmitter()
        self.emitter.message.connect(self.log.emit)

    @Slot()
    def run(self) -> None:
        try:
            result = parse_video_info(self.url, self.config, self.emitter)
            if result.thumbnail_url:
                try:
                    self.thumbnail.emit(fetch_thumbnail(result.thumbnail_url))
                except Exception as exc:  # noqa: BLE001
                    self.log.emit(f"封面加载失败：{exc}")
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(classify_error(exc), str(exc))


class DownloadWorker(QObject):
    progress = Signal(dict)
    finished = Signal(list)
    failed = Signal(str, str)
    log = Signal(str)

    def __init__(
        self,
        parts: list[VideoPart],
        config: AppConfig,
        download_dir: str,
        format_selector: str,
        controller: DownloadController,
    ) -> None:
        super().__init__()
        self.parts = parts
        self.config = config
        self.download_dir = download_dir
        self.format_selector = format_selector
        self.controller = controller
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
            )
            self.finished.emit(saved)
        except DownloadCancelled as exc:
            self.failed.emit("下载已取消。", str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(classify_error(exc), str(exc))


class SessionValidationWorker(QObject):
    finished = Signal(str, str)
    log = Signal(str)

    @Slot()
    def run(self) -> None:
        try:
            status = validate_saved_session()
            self.finished.emit(status.code, status.text)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("登录态后台验证发生未预期异常")
            self.log.emit(f"登录态验证失败：{redact_sensitive(exc)}")
            self.finished.emit("expired", "登录状态异常，请重新扫码登录")


class LoginWorker(QObject):
    status = Signal(str)
    screenshot = Signal(bytes)
    succeeded = Signal()
    failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = False
        self._refresh_requested = False

    def request_cancel(self) -> None:
        self._cancelled = True

    def request_refresh(self) -> None:
        self._refresh_requested = True

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
        try:
            ensure_playwright_runtime()
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("Playwright 初始化失败")
            self.failed.emit(
                "Playwright 未安装或浏览器依赖缺失。",
                f"{redact_sensitive(exc)}\n请运行 build.ps1，或手动执行：python -m playwright install chromium",
            )
            return

        browser = None
        context = None
        try:
            self.status.emit("请使用 Bilibili App 扫码登录")
            with sync_playwright() as playwright:
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
                while not self._cancelled and time.monotonic() < deadline:
                    if self._refresh_requested:
                        self._refresh_requested = False
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
                        self.succeeded.emit()
                        return

                    try:
                        body_text = page.locator("body").inner_text(timeout=1000)
                        if any(token in body_text for token in ("扫码成功", "扫描成功", "确认登录", "请在手机")):
                            self.status.emit("已扫码，请在手机上确认")
                        elif any(token in body_text for token in ("二维码已失效", "二维码已过期", "刷新二维码")):
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

                if self._cancelled:
                    self.failed.emit("已取消扫码登录。", "")
                else:
                    self.failed.emit("登录失败或二维码已过期，请重试。", "扫码登录超过 5 分钟未完成。")
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("扫码登录流程失败")
            self.failed.emit("扫码登录失败。", redact_sensitive(exc))
        finally:
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


class LoginDialog(QDialog):
    status_for_main = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bilibili 扫码登录")
        self.resize(600, 760)
        self.worker: LoginWorker | None = None
        self.thread: QThread | None = None
        self.login_succeeded = False

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
        worker.succeeded.connect(self.on_success)
        worker.failed.connect(self.on_failed)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "thread", None))
        thread.finished.connect(lambda: setattr(self, "worker", None))
        self.worker = worker
        self.thread = thread
        thread.start()

    @Slot()
    def refresh_qr(self) -> None:
        self.status_label.setText("请使用 Bilibili App 扫码登录")
        self.status_for_main.emit("等待扫码")
        if self.worker:
            self.worker.request_refresh()

    @Slot()
    def cancel_login(self) -> None:
        if self.worker:
            self.worker.request_cancel()
        self.reject()

    @Slot(str)
    def on_status(self, text: str) -> None:
        self.status_label.setText(text)
        if "已扫码" in text:
            self.status_for_main.emit("等待手机确认")
        elif "成功" in text:
            self.status_for_main.emit("已登录")
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

    @Slot()
    def on_success(self) -> None:
        self.login_succeeded = True
        self.status_label.setText("登录成功，正在保存登录状态")
        self.status_for_main.emit("已登录")
        self.accept()

    @Slot(str, str)
    def on_failed(self, friendly: str, detail: str) -> None:
        if friendly.startswith("已取消"):
            return
        self.status_label.setText(friendly)
        self.status_for_main.emit("登录已失效")
        QMessageBox.warning(self, "扫码登录失败", f"{friendly}\n\n详细信息：{detail}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker and not self.login_succeeded:
            self.worker.request_cancel()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, safe_mode: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("合规用途 Bilibili 视频下载器")
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
        self.safe_mode = safe_mode
        self.login_status_code = "none"

        self.log_emitter = LogEmitter()
        self.logger = setup_logging()

        self._build_ui()
        self._connect()
        self._load_config_into_ui()
        self._append_log("程序已启动。")
        self._append_log(ffmpeg_status_text())
        if self.safe_mode:
            self._append_log("检测到上次程序可能异常退出，已暂时禁用自动登录。")

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
            self.set_login_status("检测到上次程序异常退出，已暂时禁用自动登录。你可以重新扫码登录。", "safe_mode")
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
            self.login_status_code = "logged_in"
        elif "检测" in status or "等待" in status:
            self.login_status_code = "checking"
        elif "未登录" in status:
            self.login_status_code = "none"
        elif "失效" in status or "异常" in status:
            self.login_status_code = "expired"
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
        self.set_login_status(text, code)
        if code == "logged_in":
            self._append_log("登录态验证成功。")
        elif code in {"expired", "none"}:
            self._append_log(f"登录态验证结果：{text}")

    @Slot()
    def choose_download_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择下载目录", self.download_dir_edit.text())
        if chosen:
            self.download_dir_edit.setText(chosen)
            self.config.download_dir = chosen
            save_config(self.config)

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
        dialog.status_for_main.connect(self.set_login_status)
        accepted = dialog.exec()
        if accepted and dialog.login_succeeded:
            self.set_login_status("已登录", "logged_in")
            self._append_log("扫码登录成功，登录态已保存在本机。")
            if self.url_edit.text().strip():
                self._append_log("登录成功，正在重新解析当前链接以刷新可用清晰度。")
                self.start_parse()
        else:
            if has_saved_session():
                self.start_session_validation()
            else:
                self.refresh_login_status()

    @Slot()
    def logout(self) -> None:
        result = QMessageBox.question(
            self,
            "退出登录",
            "将删除本程序保存的 Bilibili 登录态和内部 cookies.txt。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
        clear_login_state()
        self.set_login_status("未登录", "none")
        self._append_log("已清除本程序保存的扫码登录态。")

    @Slot()
    def start_parse(self) -> None:
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
        worker = ParseWorker(url, AppConfig(**asdict(self.config)))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._append_log)
        worker.thumbnail.connect(self.set_thumbnail)
        worker.finished.connect(self.on_parse_finished)
        worker.failed.connect(self.on_parse_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "parse_thread", None))
        thread.finished.connect(lambda: setattr(self, "parse_worker", None))
        self.parse_worker = worker
        self.parse_thread = thread
        thread.start()

    @Slot(object)
    def on_parse_finished(self, result: VideoInfoResult) -> None:
        self.current_info = result
        self.current_formats = result.formats
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

    @Slot(str, str)
    def on_parse_failed(self, friendly: str, detail: str) -> None:
        self.parse_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self.status_label.setText("解析失败")
        if "登录态" in friendly or "cookie" in detail.lower() or "login" in detail.lower():
            self.set_login_status("登录已失效", "expired")
        self._append_log(f"解析失败：{friendly}")
        self._append_log(detail)
        QMessageBox.warning(self, "解析失败", f"{friendly}\n\n详细信息：{detail}")

    def notice_resolution_limits(self, choices: list[FormatChoice]) -> None:
        max_height = max((choice.height or 0 for choice in choices), default=0)
        if max_height >= 1080:
            return
        if self.login_status_code == "logged_in":
            self._append_log("当前账号无该清晰度权限或视频本身不提供该清晰度；程序不会绕过会员、付费、地区或 DRM 限制。")
        else:
            self._append_log("未登录时可能只能解析普通清晰度；如需 1080p 及以上清晰度，请扫码登录后重新解析。")

    @Slot(bytes)
    def set_thumbnail(self, data: bytes) -> None:
        if not data:
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
        if not self.current_info:
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

        self.config.download_dir = download_dir
        save_config(self.config)

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
        thread.finished.connect(lambda: setattr(self, "download_thread", None))
        thread.finished.connect(lambda: setattr(self, "download_worker", None))
        self.download_worker = worker
        self.download_thread = thread
        thread.start()

    @Slot()
    def cancel_download(self) -> None:
        if self.download_controller:
            self.download_controller.cancel()
            self.status_label.setText("正在取消...")
            self._append_log("已请求取消下载。")

    @Slot(dict)
    def on_download_progress(self, status: dict[str, Any]) -> None:
        raw_status = status.get("status", "")
        downloaded = status.get("downloaded_bytes") or 0
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        filename = status.get("filename") or status.get("info_dict", {}).get("filepath") or "-"
        speed = status.get("speed")
        eta = status.get("eta")

        if total:
            percent = min(100, int(downloaded * 100 / total))
            self.progress_bar.setValue(percent)

        if raw_status == "downloading":
            self.status_label.setText("正在下载")
        elif raw_status == "finished":
            self.status_label.setText("下载完成，正在合并...")
            self.progress_bar.setValue(100)
        elif raw_status == "processing":
            self.status_label.setText("正在处理/合并...")

        self.metrics_label.setText(
            f"已下载：{format_bytes(downloaded)} / {format_bytes(total)}    "
            f"速度：{format_speed(speed)}    剩余：{format_eta(eta)}    文件：{filename}"
        )

    @Slot(list)
    def on_download_finished(self, saved: list[str]) -> None:
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.parse_button.setEnabled(True)
        self.progress_bar.setValue(100)
        target = self.download_dir_edit.text()
        self.status_label.setText(f"下载完成，已保存到：{target}")
        self._append_log(f"下载完成，已保存到：{target}")
        QMessageBox.information(self, "下载完成", f"已保存到：{target}")

    @Slot(str, str)
    def on_download_failed(self, friendly: str, detail: str) -> None:
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.parse_button.setEnabled(True)
        self.status_label.setText(friendly)
        self._append_log(f"下载失败：{friendly}")
        self._append_log(detail)
        if friendly != "下载已取消。":
            QMessageBox.warning(self, "下载失败", f"{friendly}\n\n详细信息：{detail}")

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
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3500, app.quit)

    return app.exec()
