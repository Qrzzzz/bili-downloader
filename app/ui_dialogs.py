from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig
from .diagnostics import (
    DiagnosticReport,
    DiagnosticStatus,
    UpdateCheckResult,
    check_latest_release,
    collect_diagnostics,
)


class _DiagnosticWorker(QObject):
    finished = Signal(object)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self._cancelled = threading.Event()

    def request_cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        self.finished.emit(collect_diagnostics(self.config, cancelled=self._cancelled.is_set))


class _UpdateWorker(QObject):
    finished = Signal(object)

    @Slot()
    def run(self) -> None:
        self.finished.emit(check_latest_release())


@dataclass
class _Task:
    thread: QThread
    worker: QObject


class DiagnosticsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("环境诊断")
        self.resize(720, 560)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.config = config
        self.report: DiagnosticReport | None = None
        self.update_result: UpdateCheckResult | None = None
        self._tasks: list[_Task] = []
        self._closing = False

        layout = QVBoxLayout(self)
        intro = QLabel("检测均在本机完成；只有点击“检查更新”时才会访问 GitHub。")
        intro.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["状态", "项目", "结果", "详情"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.update_label = QLabel("尚未检查更新")
        self.update_label.setWordWrap(True)

        buttons = QHBoxLayout()
        self.rerun_button = QPushButton("重新检测")
        self.copy_button = QPushButton("复制脱敏报告")
        self.update_button = QPushButton("检查更新")
        self.open_release_button = QPushButton("打开新版页面")
        self.open_release_button.setEnabled(False)
        self.close_button = QPushButton("关闭")
        for button in (
            self.rerun_button,
            self.copy_button,
            self.update_button,
            self.open_release_button,
            self.close_button,
        ):
            buttons.addWidget(button)

        layout.addWidget(intro)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.update_label)
        layout.addLayout(buttons)

        self.rerun_button.clicked.connect(self.start_diagnostics)
        self.copy_button.clicked.connect(self.copy_report)
        self.update_button.clicked.connect(self.start_update_check)
        self.open_release_button.clicked.connect(self.open_release)
        self.close_button.clicked.connect(self.close)
        self.copy_button.setEnabled(False)
        QTimer.singleShot(0, self.start_diagnostics)

    def active_threads(self) -> list[QThread]:
        return [task.thread for task in self._tasks if task.thread.isRunning()]

    def _start_task(self, worker: QObject, callback: Callable[[object], None]) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        task = _Task(thread, worker)
        self._tasks.append(task)
        thread.started.connect(getattr(worker, "run"))
        getattr(worker, "finished").connect(callback)
        getattr(worker, "finished").connect(thread.quit)
        thread.finished.connect(lambda owned=task: self._task_finished(owned))
        thread.start()

    @Slot()
    def start_diagnostics(self) -> None:
        if self.active_threads() or self._closing:
            return
        self.tree.clear()
        self.tree.addTopLevelItem(QTreeWidgetItem(["…", "环境", "正在检测", ""]))
        self.rerun_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self._start_task(_DiagnosticWorker(self.config), self.on_diagnostics_finished)

    @Slot(object)
    def on_diagnostics_finished(self, report: DiagnosticReport) -> None:
        self.report = report
        self.tree.clear()
        labels = {
            DiagnosticStatus.OK: "正常",
            DiagnosticStatus.WARNING: "警告",
            DiagnosticStatus.FAILED: "失败",
            DiagnosticStatus.INFO: "信息",
        }
        for item in report.items:
            self.tree.addTopLevelItem(QTreeWidgetItem([labels[item.status], item.name, item.summary, item.detail]))
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        self.copy_button.setEnabled(True)

    @Slot()
    def copy_report(self) -> None:
        if self.report is not None:
            QApplication.clipboard().setText(self.report.to_redacted_text())

    @Slot()
    def start_update_check(self) -> None:
        if self.active_threads() or self._closing:
            return
        self.update_button.setEnabled(False)
        self.open_release_button.setEnabled(False)
        self.update_label.setText("正在检查 GitHub 最新版本...")
        self._start_task(_UpdateWorker(), self.on_update_finished)

    @Slot(object)
    def on_update_finished(self, result: UpdateCheckResult) -> None:
        self.update_result = result
        self.update_label.setText(result.message)
        self.open_release_button.setEnabled(bool(result.update_available and result.release_url))

    @Slot()
    def open_release(self) -> None:
        if self.update_result and self.update_result.update_available and self.update_result.release_url:
            QDesktopServices.openUrl(QUrl(self.update_result.release_url))

    @Slot()
    def _task_finished(self, task: _Task) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        task.worker.deleteLater()
        task.thread.deleteLater()
        if not self._closing:
            self.rerun_button.setEnabled(True)
            self.update_button.setEnabled(True)
        elif not self.active_threads():
            QTimer.singleShot(0, self.close)

    def request_shutdown(self) -> None:
        self._closing = True
        for button in (self.rerun_button, self.copy_button, self.update_button, self.open_release_button, self.close_button):
            button.setEnabled(False)
        for task in self._tasks:
            request_cancel = getattr(task.worker, "request_cancel", None)
            if callable(request_cancel):
                request_cancel()
            task.thread.requestInterruption()
            task.thread.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.active_threads():
            self.request_shutdown()
            event.ignore()
            return
        super().closeEvent(event)
