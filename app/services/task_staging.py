"""Preview and quarantine only owned task staging, never published media."""
from __future__ import annotations

import os
import re
import stat
import time
import uuid
from pathlib import Path

from app.backend.protocol import ProtocolError
from app.logger import redact_sensitive
from app.services.task_resources import FileLease

TASK_ID = re.compile(r"[0-9a-f]{32}\Z")
IN_FLIGHT = {"queued", "preparing", "downloading", "waiting_resources", "merging",
             "converting", "postprocessing", "verifying", "cancelling"}
FILE_LIMIT = 20000
ENTRY_LIMIT = 1000


def check_path(path: Path) -> None:
    """Reject symlinks and all Windows reparse points, including ancestors."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("路径含链接或重解析点，已保护。")


def inventory(path: Path, budget: list[int] | None = None) -> tuple[int, int, tuple]:
    if budget is not None and budget[0] <= 0:
        raise OSError("本次扫描已达到文件上限，未提供清理。")
    check_path(path)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("暂存项不是任务目录，已保护。")
    size, count = 0, 0
    fingerprints = []
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                count += 1
                if budget is not None:
                    budget[0] -= 1
                    if budget[0] < 0:
                        raise OSError("本次扫描已达到文件上限，未提供清理。")
                if count > FILE_LIMIT:
                    raise OSError("暂存项超过扫描上限，未提供清理。")
                value = child.stat(follow_symlinks=False)
                fingerprints.append((str(Path(child.path).relative_to(path)), value.st_dev, value.st_ino,
                                     value.st_mtime_ns, value.st_size, value.st_mode))
                if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
                    raise OSError("暂存项内含链接或重解析点，已保护。")
                if stat.S_ISDIR(value.st_mode):
                    pending.append(Path(child.path))
                elif stat.S_ISREG(value.st_mode):
                    size += value.st_size
                else:
                    raise OSError("暂存项含特殊文件，已保护。")
    return size, count, (info.st_dev, info.st_ino, info.st_mtime_ns, tuple(sorted(fingerprints)), size, count)


def stage_lease(directory: str, task_id: str) -> FileLease:
    return FileLease("staging:" + os.path.normcase(str(Path(directory).absolute())) + ":" + task_id)


def reclaim_empty(directory: str, task_id: str) -> None:
    if not TASK_ID.fullmatch(task_id):
        return
    lease = stage_lease(directory, task_id)
    try:
        if not lease.acquire():
            return
        path = Path(directory).absolute() / ".bili-tasks" / task_id
        check_path(path)
        path.rmdir()  # No recursion: useful partial files always survive.
    except OSError:
        pass
    finally:
        lease.close()


class TaskStaging:
    def __init__(self, manager):
        self.manager = manager
        self.preview: dict[str, dict] = {}
        self.scanned_at = 0.0

    def _tasks(self):
        return {t["task_id"]: t for t in self.manager.repo.all()}

    @staticmethod
    def _classification(task: dict | None, directory: Path) -> tuple[str, str]:
        if task is None:
            return "orphan", "无关联任务记录；清理后可还原文件，但不会重建任务。"
        if os.path.normcase(str(Path(task["spec"]["directory"]).absolute())) != os.path.normcase(str(directory)):
            return "protected", "任务路径已变化；原路径暂存保留，不能清理。"
        if task["state"] in IN_FLIGHT:
            return "active", "等待或执行中的任务，不能清理。"
        if task["state"] != "completed":
            return "recoverable", "继续任务会使用这些断点，已保留。"
        return "completed", "任务已完成；仅处理暂存，保留最终媒体。"

    def scan(self) -> dict:
        with self.manager.lock, self.manager.repo.db:
            self.manager.recover()
            tasks = self._tasks()
            # Remember removed tasks' directories across application restarts.
            roots = {str(Path(self.manager.config.download_dir).absolute())}
            roots.update(str(Path(t["spec"]["directory"]).absolute()) for t in tasks.values())
            for root in roots:
                self.manager.repo.db.execute("INSERT OR IGNORE INTO staging_roots VALUES(?)", (root,))
            roots.update(row[0] for row in self.manager.repo.db.execute("SELECT path FROM staging_roots"))
        entries, errors, preview = [], [], {}
        budget = [FILE_LIMIT]
        for root in sorted(roots):
            if len(entries) >= ENTRY_LIMIT:
                errors.append("已达到 1000 项扫描上限，其余目录未扫描。")
                break
            directory = Path(root)
            for recycled, name in ((False, ".bili-tasks"), (True, ".bili-tasks-recycle")):
                base = directory / name
                try:
                    check_path(base)
                    if not base.exists():
                        continue
                    with os.scandir(base) as children:
                        for child in children:
                            if len(entries) >= ENTRY_LIMIT:
                                raise OSError("超过 1000 项扫描上限；请先处理已扫描项再刷新。")
                            path = Path(child.path)
                            try:
                                check_path(path)
                                if recycled:
                                    if not TASK_ID.fullmatch(child.name):
                                        raise OSError("暂存回收区含未知项，已保护。")
                                    with os.scandir(path) as recycled_children:
                                        first = next(recycled_children, None)
                                        if first is None or next(recycled_children, None) is not None:
                                            raise OSError("暂存回收项结构异常，已保护。")
                                        path = Path(first.path)
                                task_id = path.name
                                if not TASK_ID.fullmatch(task_id):
                                    raise OSError("暂存区含非任务目录，已保护。")
                            except OSError as exc:
                                entries.append({"entry_id": uuid.uuid4().hex, "task_id": path.name,
                                    "path": str(path), "title": "未知或异常暂存项", "bytes": 0,
                                    "file_count": 0, "category": "protected", "can_clean": False,
                                    "can_restore": False, "message": redact_sensitive(str(exc))[:1024]})
                                continue
                            entry_id = uuid.uuid4().hex
                            row = {"entry_id": entry_id, "task_id": task_id, "path": str(path),
                                   "title": tasks.get(task_id, {}).get("title", "无关联任务"),
                                   "bytes": 0, "file_count": 0, "category": "protected",
                                   "message": "", "can_clean": False, "can_restore": False}
                            try:
                                size, count, fingerprint = inventory(path, budget)
                                row.update(bytes=size, file_count=count)
                                category, message = self._classification(tasks.get(task_id), directory)
                                if recycled:
                                    row.update(category="recycled", message="保存在暂存回收区；占用仍计入磁盘，可还原。",
                                               can_restore=category not in {"active", "protected"})
                                else:
                                    lease = stage_lease(str(directory), task_id)
                                    try:
                                        available = lease.acquire()
                                    finally:
                                        lease.close()
                                    if not available:
                                        category, message = "active", "暂存文件被其他窗口占用，不能清理。"
                                    row.update(category=category, message=message,
                                               can_clean=category in {"orphan", "completed"})
                                preview[entry_id] = {"row": row, "root": directory, "path": path,
                                                     "fingerprint": fingerprint, "recycled": recycled}
                            except OSError as exc:
                                row["message"] = redact_sensitive(str(exc))[:1024]
                            entries.append(row)
                except OSError as exc:
                    errors.append(redact_sensitive(f"{base}：{exc}")[:2048])
        self.preview, self.scanned_at = preview, time.monotonic()
        return {"entries": entries, "errors": errors, "total_bytes": sum(e["bytes"] for e in entries),
                "cleanable_bytes": sum(e["bytes"] for e in entries if e["can_clean"]),
                "recycled_bytes": sum(e["bytes"] for e in entries if e["category"] == "recycled")}

    def apply(self, entry_ids: list[str], restore: bool = False) -> dict:
        if (not isinstance(entry_ids, list) or not 0 < len(entry_ids) <= ENTRY_LIMIT
                or any(not isinstance(i, str) for i in entry_ids) or len(set(entry_ids)) != len(entry_ids)):
            raise ProtocolError("invalid_params", "请选择扫描预览中的暂存项。")
        if time.monotonic() - self.scanned_at > 300:
            raise ProtocolError("staging_preview", "扫描预览已过期，请刷新后再操作。")
        if any(i not in self.preview for i in entry_ids):
            raise ProtocolError("staging_preview", "暂存项不属于当前预览，请刷新。")
        results = []
        for entry_id in entry_ids:
            item = self.preview[entry_id]
            row, directory, path = item["row"], item["root"], item["path"]
            result = {"entry_id": entry_id, "status": "protected", "message": ""}
            lease = None
            try:
                lease = stage_lease(str(directory), row["task_id"])
                if not lease.acquire():
                    raise OSError("暂存文件被其他窗口占用，已跳过。")
                # Serializes revalidation/rename with task creation, claims and removal.
                with self.manager.lock, self.manager.repo.db:
                    self.manager.repo.db.execute("BEGIN IMMEDIATE")
                    category, _ = self._classification(self._tasks().get(row["task_id"]), directory)
                    if category in {"active", "protected"} or (not restore and category == "recoverable"):
                        raise OSError("关联任务状态已变化或断点可恢复，已保护。")
                    if not row["can_restore" if restore else "can_clean"] or item["recycled"] != restore:
                        raise OSError("此暂存项不能执行该操作。")
                    if inventory(path)[2] != item["fingerprint"]:
                        raise OSError("暂存项自扫描后发生变化，请刷新。")
                    if restore:
                        parent = directory / ".bili-tasks"
                        destination = parent / row["task_id"]
                    else:
                        parent = directory / ".bili-tasks-recycle" / uuid.uuid4().hex
                        destination = parent / row["task_id"]
                    check_path(parent)
                    parent.mkdir(parents=True, exist_ok=True)
                    check_path(destination)
                    if destination.exists():
                        raise OSError("原位置已有暂存，未覆盖；请保留两份文件。")
                    # Same-volume rename keeps the complete directory recoverable.
                    try:
                        os.rename(path, destination)
                    except OSError:
                        if not restore:
                            try:
                                check_path(parent)
                                parent.rmdir()
                            except OSError:
                                pass
                        raise
                    result.update(status="restored" if restore else "recycled", message=str(destination))
                    self.preview.pop(entry_id, None)
                    if restore:
                        try:
                            path.parent.rmdir()
                        except OSError:
                            pass
            except OSError as exc:
                result["message"] = redact_sensitive(str(exc))[:2048]
            finally:
                if lease is not None:
                    lease.close()
            results.append(result)
        return {"results": results}
