from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
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
from .downloader import DownloadBatchResult, PartDownloadResult, PartDownloadStatus, VideoPart


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


class DownloadResultDialog(QDialog):
    retry_requested = Signal(object)

    def __init__(
        self,
        result: DownloadBatchResult,
        *,
        video_title: str,
        format_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("下载结果")
        self.resize(860, 500)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.result = result
        self.retry_valid = True
        self.video_title = video_title

        layout = QVBoxLayout(self)
        self.context_label = QLabel(f"视频：{video_title}\n清晰度：{format_label}")
        self.context_label.setWordWrap(True)
        self.summary_label = QLabel()
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["分 P", "标题", "状态", "输出文件", "错误"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        buttons = QHBoxLayout()
        self.open_file_button = QPushButton("打开文件")
        self.open_folder_button = QPushButton("打开所在目录")
        self.retry_button = QPushButton("重试失败项")
        self.close_button = QPushButton("关闭")
        buttons.addWidget(self.open_file_button)
        buttons.addWidget(self.open_folder_button)
        buttons.addStretch(1)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.close_button)

        layout.addWidget(self.context_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

        self.table.itemSelectionChanged.connect(self._update_actions)
        self.open_file_button.clicked.connect(self.open_selected_file)
        self.open_folder_button.clicked.connect(self.open_selected_folder)
        self.retry_button.clicked.connect(self.retry_failed)
        self.close_button.clicked.connect(self.close)
        self.set_result(result)

    def set_result(self, result: DownloadBatchResult) -> None:
        self.result = result
        completed = len(result.completed)
        failed = len(result.failed)
        cancelled = sum(item.status is PartDownloadStatus.CANCELLED for item in result.part_results)
        self.summary_label.setText(f"任务结束：成功 {completed}，失败 {failed}，取消 {cancelled}。")
        self.table.setRowCount(len(result.part_results))
        status_labels = {
            PartDownloadStatus.COMPLETED: "成功",
            PartDownloadStatus.FAILED: "失败",
            PartDownloadStatus.CANCELLED: "取消",
        }
        for row, item in enumerate(result.part_results):
            saved = "；".join(Path(path).name for path in item.saved_files)
            error = item.error.message if item.error else ""
            values = [f"P{item.part.index}", item.part.title, status_labels[item.status], saved, error]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, item)
                if column == 3 and item.saved_files:
                    cell.setToolTip("\n".join(item.saved_files))
                if column == 4 and item.detail:
                    cell.setToolTip(item.detail)
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        if self.table.rowCount():
            self.table.selectRow(0)
        self.retry_button.setEnabled(bool(result.failed) and self.retry_valid)
        self._update_actions()

    def merge_retry_result(self, result: DownloadBatchResult) -> None:
        self.set_result(self.result.merged_with_retry(result))

    def invalidate_retry(self) -> None:
        self.retry_valid = False
        self.retry_button.setEnabled(False)
        self.retry_button.setToolTip("链接或解析目标已改变，不能重试旧任务。")

    def set_busy(self, busy: bool) -> None:
        self.retry_button.setEnabled(not busy and self.retry_valid and bool(self.result.failed))
        self.close_button.setEnabled(not busy)
        if busy:
            self.summary_label.setText("正在重试失败项...")

    def _selected_result(self) -> PartDownloadResult | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        value = item.data(Qt.UserRole) if item else None
        return value if isinstance(value, PartDownloadResult) else None

    def _selected_existing_file(self) -> Path | None:
        item = self._selected_result()
        if not item or item.status is not PartDownloadStatus.COMPLETED:
            return None
        return next((Path(path) for path in item.saved_files if Path(path).is_file()), None)

    @Slot()
    def _update_actions(self) -> None:
        path = self._selected_existing_file()
        self.open_file_button.setEnabled(path is not None)
        self.open_folder_button.setEnabled(path is not None)

    @Slot()
    def open_selected_file(self) -> None:
        path = self._selected_existing_file()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.information(self, "文件不存在", "文件可能已被移动或删除。")
        self._update_actions()

    @Slot()
    def open_selected_folder(self) -> None:
        path = self._selected_existing_file()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        else:
            QMessageBox.information(self, "文件不存在", "文件可能已被移动或删除。")
        self._update_actions()

    @Slot()
    def retry_failed(self) -> None:
        if not self.retry_valid:
            return
        parts = tuple(item.part for item in self.result.failed)
        if parts:
            self.set_busy(True)
            self.retry_requested.emit(parts)
