"""Process-safe resource leases. OS locks expire with their owning process."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

from app.config import app_data_dir
from app.utils import AppError, ErrorKind
from yt_dlp.utils import DownloadCancelled


class FileLease:
    def __init__(self, name: str):
        root = app_data_dir() / "task-locks"
        root.mkdir(exist_ok=True)
        self.path = root / (hashlib.sha256(name.encode()).hexdigest() + ".lock")
        self.handle = None

    def acquire(self) -> bool:
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, 2) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def owner_alive(owner: str) -> bool:
    lease = FileLease("instance:" + owner)
    if not lease.acquire():
        return True
    lease.close()
    return False


@contextmanager
def resource_lease(name: str, controller=None):
    lease = FileLease(name)
    wait = threading.Event()
    try:
        while not lease.acquire():
            if controller and controller.cancelled:
                raise DownloadCancelled("等待资源时已取消")
            wait.wait(0.05)
        if controller and controller.cancelled:
            raise DownloadCancelled("任务已取消")
        yield
    finally:
        lease.close()


@contextmanager
def reserve_space(target: str, estimate: int | None, owner: str, controller):
    """Conservative source + final-output reservation, shared by all instances."""
    volume = str(os.stat(target).st_dev)
    path = app_data_dir() / "space-reservations.json"
    key = owner + ":" + str(threading.get_ident())
    required = int((estimate or 64 * 1024 * 1024) * 2.1) + 16 * 1024 * 1024

    def read():
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if owner_alive(v["owner"])}

    def write(data):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, path)

    with resource_lease("space-reservations", controller):
        data = read()
        reserved = sum(v["bytes"] for v in data.values() if v["volume"] == volume)
        if shutil.disk_usage(target).free < required + reserved:
            raise AppError(ErrorKind.DISK_FULL, "并行任务预留后剩余空间不足，请释放空间后重试。")
        data[key] = {"volume": volume, "bytes": required, "owner": owner}
        write(data)

    def grow(known_bytes):
        nonlocal required
        if type(known_bytes) not in (int, float) or not math.isfinite(known_bytes) or known_bytes <= 0:
            return
        needed = int(known_bytes * 2.1) + 16 * 1024 * 1024
        if needed <= required:
            return
        # Grow in chunks so progress callbacks do not turn into disk writes.
        needed = max(needed, required + 16 * 1024 * 1024)
        with resource_lease("space-reservations", controller):
            data = read()
            reserved = sum(v["bytes"] for k, v in data.items() if k != key and v["volume"] == volume)
            if shutil.disk_usage(target).free < needed + reserved:
                raise AppError(ErrorKind.DISK_FULL, "媒体大小增加后剩余空间不足，已安全停止当前下载。")
            required = needed
            data[key] = {"volume": volume, "bytes": required, "owner": owner}
            write(data)
    try:
        yield grow
    finally:
        with resource_lease("space-reservations"):
            data = read()
            data.pop(key, None)
            write(data)
