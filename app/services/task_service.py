"""Durable video tasks and a bounded scheduler independent of the input editor."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable

from app.backend.dto import batch_dto, error_dto, progress_dto
from app.backend.protocol import ProtocolError
from app.config import AppConfig, app_data_dir
from app.cookies import CredentialMode
from app.downloader import DownloadController, DownloadMode
from app.logger import redact_sensitive
from app.services.download_service import DownloadRequest, run_download
from app.services.parse_service import parse_video
from app.services.session_service import session_status
from app.services.task_resources import FileLease, owner_alive, resource_lease
from yt_dlp.utils import DownloadCancelled
from app.video_urls import normalize_video_input

ACTIVE = {"preparing", "downloading", "waiting_resources", "merging", "converting", "postprocessing", "verifying", "cancelling"}
TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted", "blocked"}


class TaskRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "tasks.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("任务数据库版本不兼容，请使用匹配的应用版本。")
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS submissions (token TEXT PRIMARY KEY, task_id TEXT NOT NULL, identity TEXT NOT NULL)")
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()

    def all(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM tasks")]

    def get(self, task_id: str) -> dict:
        row = self.db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ProtocolError("task_missing", "任务记录不存在。")
        return json.loads(row[0])

    def put(self, task: dict) -> None:
        self.db.execute("INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (task["task_id"], task["token"], json.dumps(task, ensure_ascii=False, allow_nan=False)))


class TaskManager:
    def __init__(self, notify: Callable[[dict], None], config: AppConfig, owner: str, path: Path | None = None):
        self.lock = threading.RLock()
        self.owner = owner
        self.lease = FileLease("instance:" + owner)
        if not self.lease.acquire():
            raise RuntimeError("任务实例已存在。")
        try:
            self.repo = TaskRepository(path)
        except Exception:
            self.lease.close()
            raise
        self.notify = notify
        self.config = replace(config)
        self.running: dict[str, tuple[DownloadController, threading.Thread]] = {}
        self.live: dict[str, dict] = {}
        self.paused = False
        self.account_busy = False
        self.closing = False
        self.closed = False
        self.epoch = 0
        self.storage_failed = False
        self.recover()

    def recover(self) -> None:
        # BEGIN IMMEDIATE serializes recovery/claims with other live instances.
        with self.lock, self.repo.db:
            self.repo.db.execute("BEGIN IMMEDIATE")
            for task in self.repo.all():
                if task["state"] in ACTIVE | {"queued"} and not owner_alive(task["owner"]):
                    task.update(state="interrupted", message="上次运行未完成，请确认后继续。")
                    self._save(task)

    def _save(self, task: dict) -> None:
        task["revision"] += 1
        task["updated_at"] = time.time()
        self.repo.put(task)

    def _public(self, task: dict, detail: bool = False) -> dict:
        result = {k: v for k, v in task.items() if k not in {"token", "owner", "spec", "identity"}}
        result["foreign"] = task["owner"] != self.owner and owner_alive(task["owner"])
        result.update(self.live.get(task["task_id"], {}))
        if not detail:
            result["logs"] = ""
            result["result"] = None
        return result

    def snapshot(self) -> dict:
        with self.lock:
            self.recover()
            tasks = sorted(self.repo.all(), key=lambda t: (t["position"], t["created_at"]))
            return {"paused": self.paused, "max_parallel": self.config.max_parallel,
                    "tasks": [self._public(t) for t in tasks], "revision": self.epoch}

    def _changed(self, task: dict) -> None:
        with self.lock:
            self.epoch += 1
            self.notify({"task": self._public(task), "revision": self.epoch, "paused": self.paused})

    def create(self, parsed, indices: list[int], mode: str, format_id: str | None, directory: str, token: str) -> dict:
        if self.storage_failed:
            raise ProtocolError("task_storage", "任务存储发生错误，请释放空间并重新打开应用。")
        config = replace(self.config, download_dir=directory)
        mode = DownloadMode(mode)
        if not isinstance(indices, list) or not indices or len(indices) > 1000 or any(type(i) is not int for i in indices) or len(set(indices)) != len(indices):
            raise ProtocolError("invalid_params", "请选择有效的分 P（最多 1000 个）。")
        parts = [p for p in parsed.info.parts if p.index in indices]
        if len(parts) != len(indices):
            raise ProtocolError("invalid_params", "分 P 不属于当前解析结果。")
        height, label = None, "MP3 · 192 kbps"
        if mode == DownloadMode.AUDIO_VIDEO:
            choices = {str(i): f for i, f in enumerate(parsed.info.formats)}
            if format_id not in choices:
                raise ProtocolError("invalid_params", "画质不属于当前解析结果。")
            choice = choices[format_id]
            height, label = choice.height, "MP4 · " + choice.label
        source = normalize_video_input(parsed.info.source_url)
        spec = {"source_url": source, "parts": [{"index": p.index, "id": p.id} for p in parts],
                "mode": mode.value, "height": height, "directory": config.download_dir,
                "credential_mode": parsed.mode.value, "generation": parsed.generation}
        identity = json.dumps([parsed.info.raw_id or source.split("?")[0], sorted(indices), mode.value, height,
                               str(Path(config.download_dir).resolve()).casefold(), parsed.mode.value, parsed.generation])
        with self.lock, self.repo.db:
            self.repo.db.execute("BEGIN IMMEDIATE")
            submission = self.repo.db.execute("SELECT task_id, identity FROM submissions WHERE token=?", (token,)).fetchone()
            if submission:
                if submission[1] != identity:
                    raise ProtocolError("idempotency_conflict", "此提交标识已用于另一任务。")
                return {"task": self._public(self.repo.get(submission[0])), "duplicate": True}
            tasks = self.repo.all()
            for old in tasks:
                if old["token"] == token:
                    if old["identity"] != identity:
                        raise ProtocolError("idempotency_conflict", "此提交标识已用于另一任务。")
                    return {"task": self._public(old), "duplicate": True}
                if old["identity"] == identity and old["state"] not in TERMINAL:
                    self.repo.db.execute("INSERT INTO submissions VALUES(?,?,?)", (token, old["task_id"], identity))
                    return {"task": self._public(old), "duplicate": True}
            if len(tasks) >= 500:
                raise ProtocolError("task_limit", "任务记录已达到 500 条，请先清理已完成记录。")
            now = time.time()
            task = {"task_id": uuid.uuid4().hex, "token": token, "owner": self.owner, "attempt_id": "",
                    "title": parsed.info.title[:2048], "format_label": label, "part_count": len(parts),
                    "credential_mode": parsed.mode.value, "output_dir": config.download_dir,
                    "state": "queued", "message": "等待下载", "revision": 0, "created_at": now,
                    "position": max((t["position"] for t in tasks), default=0) + 1,
                    "result": None, "logs": "", "spec": spec, "identity": identity}
            self._save(task)
            self.repo.db.execute("INSERT INTO submissions VALUES(?,?,?)", (token, task["task_id"], identity))
        return {"task": self._public(task), "duplicate": False}

    def start_ready(self) -> None:
        with self.lock:
            if self.paused or self.closing or self.storage_failed:
                return
            for task in sorted(self.repo.all(), key=lambda t: t["position"]):
                if len(self.running) >= self.config.max_parallel:
                    break
                if task["owner"] != self.owner or task["state"] != "queued":
                    continue
                if self.account_busy and task["credential_mode"] == "saved":
                    continue
                controller = DownloadController()
                controller.task_id, controller.task_owner = task["task_id"], self.owner
                task.update(state="preparing", attempt_id=uuid.uuid4().hex, message="正在准备", logs="")
                self.live.pop(task["task_id"], None)
                try:
                    thread = threading.Thread(target=self._run, args=(task["task_id"], controller), name="task-" + task["task_id"][:8])
                    with self.repo.db:
                        self._save(task)
                    self.running[task["task_id"]] = (controller, thread)
                    thread.start()
                except Exception as exc:
                    self.running.pop(task["task_id"], None)
                    task.update(state="failed", message=redact_sensitive(exc)[:2048])
                    with self.repo.db:
                        self._save(task)
                self._changed(task)

    @property
    def saved_running(self) -> bool:
        with self.lock:
            return any(self.repo.get(i)["credential_mode"] == "saved" for i in self.running)

    def account_gate(self, busy: bool) -> None:
        with self.lock:
            if busy and self.saved_running:
                raise ProtocolError("backend_busy", "请等待使用登录态的下载安全结束后再更改账号。")
            self.account_busy = busy
        if not busy:
            self.start_ready()

    def _run(self, task_id: str, controller: DownloadController) -> None:
        with self.lock:
            task = self.repo.get(task_id)
        spec = task["spec"]
        last = 0.0

        def progress(data):
            nonlocal last
            now = time.monotonic()
            if data.get("phase") == "downloading" and now - last < 0.25:
                return
            last = now
            value = progress_dto(data)
            with self.lock:
                current = self.repo.get(task_id)
                phase = value["phase"]
                # A per-P terminal never terminates the owning task.
                state = "cancelling" if controller.cancelled else phase if phase in ACTIVE else "preparing"
                self.live[task_id] = {"progress": value, "state": state}
                self._changed(current)

        def log(text):
            with self.lock, self.repo.db:
                current = self.repo.get(task_id)
                current["logs"] = (current["logs"] + redact_sensitive(text)[:4096] + "\n")[-16000:]
                self._save(current)

        try:
            mode = CredentialMode(spec["credential_mode"])
            if mode == CredentialMode.SAVED and session_status()["generation"] != spec["generation"]:
                raise ProtocolError("stale_session", "登录状态已改变，请重新确认账号后继续。")
            config = replace(self.config, download_dir=spec["directory"])
            with resource_lease("metadata", controller):
                info = parse_video(spec["source_url"], config, mode, log, spec["generation"])
            if controller.cancelled:
                raise DownloadCancelled("任务已取消")
            by_index = {p.index: p for p in info.parts}
            selected = []
            for expected in spec["parts"]:
                actual = by_index.get(expected["index"])
                if actual is None or (expected["id"] and actual.id != expected["id"]):
                    raise ProtocolError("stale_parts", "视频分 P 已改变，请重新添加任务。")
                selected.append(actual)
            selector = "bestaudio/best"
            if spec["mode"] == "audio_video":
                choice = next((f for f in info.formats if f.height == spec["height"]), None)
                if choice is None:
                    raise ProtocolError("format_unavailable", "当前账号无法取得原任务画质，请重新添加或稍后重试。")
                selector = choice.selector
            old = task["result"]
            finished = {p["index"] for p in old["part_results"] if p["status"] == "completed"} if old else set()
            retry_indices = task.get("retry_indices")
            pending = tuple(p for p in selected if p.index not in finished and (retry_indices is None or p.index in retry_indices))
            request = DownloadRequest(spec["source_url"], task["title"], tuple(selected), config, config.download_dir,
                                      selector, task["format_label"], mode, DownloadMode(spec["mode"]), spec["generation"])
            result = batch_dto(run_download(request, controller, progress, log, pending), task_id, True, config.download_dir)
            if old:
                replacements = {p["index"]: p for p in result["part_results"]}
                result["part_results"] = [replacements.get(p["index"], p) for p in old["part_results"]]
                included = {p["index"] for p in result["part_results"]}
                result["part_results"].extend(p for i, p in replacements.items() if i not in included)
                result["saved_files"] = list(dict.fromkeys(f for p in result["part_results"] for f in p["saved_files"]))
                statuses = {p["status"] for p in result["part_results"]}
                result["outcome"] = "cancelled" if "cancelled" in statuses else "partial" if statuses == {"completed", "failed"} else "failed" if "failed" in statuses else "completed"
                result["retry_allowed"] = "failed" in statuses
            reason = next((p["error"]["message"] for p in result["part_results"] if p.get("error")), "")
            task.update(state=result["outcome"], result=result, message=reason)
        except Exception as exc:
            code = getattr(exc, "code", "")
            task.update(state="cancelled" if controller.cancelled else "blocked" if code in {"stale_session", "stale_parts", "format_unavailable"} else "failed",
                        message=redact_sensitive(str(exc))[:2048])
        finally:
            with self.lock:
                try:
                    current = self.repo.get(task_id)
                    task["logs"] = current["logs"]
                    task["revision"] = current["revision"]
                    if self.closing and task["state"] != "completed":
                        task["state"] = "interrupted"
                    with self.repo.db:
                        self._save(task)
                    self.live.pop(task_id, None)
                    self._changed(task)
                except (sqlite3.Error, OSError):
                    self.storage_failed = True
                    self.paused = True
                    for other, _ in self.running.values():
                        other.cancel()
                    self.live[task_id] = {"state": "interrupted", "message": "任务结果无法保存，请释放空间并重新打开应用。"}
                    self._changed(task)
                finally:
                    self.running.pop(task_id, None)
            self.start_ready()

    def command(self, action: str, task_id: str, reauthorize: bool = False) -> dict:
        if self.storage_failed:
            raise ProtocolError("task_storage", "任务存储发生错误，请释放空间并重新打开应用。")
        with self.lock, self.repo.db:
            self.repo.db.execute("BEGIN IMMEDIATE")
            task = self.repo.get(task_id)
            if task["owner"] != self.owner and owner_alive(task["owner"]):
                raise ProtocolError("task_owned", "任务由另一个应用窗口管理，请在原窗口操作。")
            if action == "cancel":
                if task_id in self.running:
                    self.running[task_id][0].cancel()
                    task.update(state="cancelling", message="等待当前文件安全处理结束…")
                    self.live.pop(task_id, None)
                elif task["state"] == "queued":
                    task.update(state="cancelled", message="已取消等待")
            elif action in {"resume", "retry"}:
                if task["state"] not in TERMINAL - {"completed"}:
                    raise ProtocolError("task_state", "此任务当前不能继续。")
                if action == "retry" and task["result"] and not any(p["status"] == "failed" for p in task["result"]["part_results"]):
                    raise ProtocolError("task_state", "没有失败的分 P。")
                task["retry_indices"] = [p["index"] for p in task["result"]["part_results"] if p["status"] == "failed"] if action == "retry" and task["result"] else None
                if task["credential_mode"] == "saved":
                    generation = session_status()["generation"]
                    if generation != task["spec"]["generation"]:
                        if not reauthorize or not generation:
                            raise ProtocolError("stale_session", "请登录并重新确认账号后继续。")
                        task["spec"]["generation"] = generation
                        identity = json.loads(task["identity"])
                        identity[-1] = generation
                        task["identity"] = json.dumps(identity)
                task.update(owner=self.owner, state="queued", message="等待下载")
            elif action == "reorder":
                if task["state"] != "queued":
                    raise ProtocolError("task_state", "只能调整等待任务。")
                task["position"] = min((t["position"] for t in self.repo.all()), default=0) - 1
            elif action == "remove":
                if task_id in self.running or task["state"] == "queued":
                    raise ProtocolError("task_state", "请先取消任务，再移除记录。")
                self.repo.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                self.repo.db.execute("DELETE FROM submissions WHERE task_id=?", (task_id,))
                return {"removed": task_id}
            else:
                raise ProtocolError("invalid_params", "未知任务操作。")
            self._save(task)
        self._changed(task)
        return {"task": self._public(task)}

    def close(self) -> None:
        with self.lock:
            self.closing = True
            for controller, _ in self.running.values():
                controller.cancel()

    def wait(self) -> None:
        with self.lock:
            threads = [t for _, t in self.running.values()]
        for thread in threads:
            thread.join()
        with self.lock:
            if not self.closed:
                try:
                    with self.repo.db:
                        for task in self.repo.all():
                            if task["owner"] == self.owner and task["state"] == "queued":
                                task.update(state="interrupted", message="已保存，确认后可继续。")
                                self._save(task)
                finally:
                    self.repo.db.close()
                    self.lease.close()
                    self.closed = True
