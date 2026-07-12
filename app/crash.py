from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from types import TracebackType
from typing import Any

from .config import app_data_dir, logs_dir
from .logger import redact_sensitive


CRASH_LOG_MAX_BYTES = 2 * 1024 * 1024
CRASH_LOG_BACKUP_COUNT = 2
_CRASH_LOG_LOCK = threading.RLock()
_TRUNCATION_MARKER = b"\n...[crash log entry truncated]...\n"


def crash_log_path() -> Path:
    path = logs_dir() / "crash.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def app_running_lock_path() -> Path:
    return app_data_dir() / "app_running.lock"


def _bounded_utf8(text: str, max_bytes: int) -> bytes:
    encoded = text.encode("utf-8", errors="backslashreplace")
    if len(encoded) <= max_bytes:
        return encoded
    if max_bytes <= len(_TRUNCATION_MARKER):
        return encoded[:max_bytes].decode("utf-8", errors="ignore").encode("utf-8")

    available = max_bytes - len(_TRUNCATION_MARKER)
    head_size = available * 2 // 3
    tail_size = available - head_size
    head = encoded[:head_size].decode("utf-8", errors="ignore").encode("utf-8")
    tail = encoded[-tail_size:].decode("utf-8", errors="ignore").encode("utf-8")
    return head + _TRUNCATION_MARKER + tail


def _backup_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def _move_bounded_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    try:
        if source.stat().st_size > CRASH_LOG_MAX_BYTES:
            source.unlink(missing_ok=True)
            return
    except OSError:
        source.unlink(missing_ok=True)
        return
    os.replace(source, destination)


def _rotate_crash_log(path: Path) -> None:
    if CRASH_LOG_BACKUP_COUNT <= 0:
        path.unlink(missing_ok=True)
        return

    _backup_path(path, CRASH_LOG_BACKUP_COUNT).unlink(missing_ok=True)
    for index in range(CRASH_LOG_BACKUP_COUNT - 1, 0, -1):
        _move_bounded_backup(_backup_path(path, index), _backup_path(path, index + 1))
    _move_bounded_backup(path, _backup_path(path, 1))


def _prune_crash_backups(path: Path) -> None:
    for candidate in path.parent.glob(f"{path.name}.*"):
        suffix = candidate.name.removeprefix(f"{path.name}.")
        if not suffix.isdigit():
            continue
        try:
            too_old = int(suffix) > CRASH_LOG_BACKUP_COUNT
            too_large = candidate.stat().st_size > CRASH_LOG_MAX_BYTES
            if too_old or too_large:
                candidate.unlink(missing_ok=True)
        except OSError:
            # A later crash must still be writable even if an old backup is locked.
            continue


def _write_crash_text(text: str) -> None:
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        sanitized = redact_sensitive(text)
        suffix = "" if sanitized.endswith("\n") else "\n"
        entry = _bounded_utf8(f"\n===== {timestamp} =====\n{sanitized}{suffix}", CRASH_LOG_MAX_BYTES)
        path = crash_log_path()
        with _CRASH_LOG_LOCK:
            _prune_crash_backups(path)
            try:
                current_size = path.stat().st_size if path.exists() else 0
            except OSError:
                current_size = CRASH_LOG_MAX_BYTES
            if current_size + len(entry) > CRASH_LOG_MAX_BYTES:
                try:
                    _rotate_crash_log(path)
                except OSError:
                    path.unlink(missing_ok=True)

            try:
                remaining_size = path.stat().st_size if path.exists() else 0
            except OSError:
                remaining_size = CRASH_LOG_MAX_BYTES
            mode = "ab" if remaining_size + len(entry) <= CRASH_LOG_MAX_BYTES else "wb"
            with path.open(mode) as handle:
                handle.write(entry)
    except Exception:
        # Last-resort crash logging must never raise a second exception.
        pass


def log_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    context: str = "Unhandled exception",
) -> None:
    body = "".join(traceback.format_exception(exc_type, exc, tb))
    _write_crash_text(f"{context}\n{body}")


def log_current_exception(context: str = "Exception") -> None:
    exc_type, exc, tb = sys.exc_info()
    if exc_type is not None and exc is not None:
        log_exception(exc_type, exc, tb, context)


def install_exception_hooks() -> None:
    original_sys_hook = sys.excepthook
    original_thread_hook = getattr(threading, "excepthook", None)

    def sys_hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        log_exception(exc_type, exc, tb, "sys.excepthook")
        original_sys_hook(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        log_exception(args.exc_type, args.exc_value, args.exc_traceback, f"threading.excepthook thread={args.thread}")
        if original_thread_hook is not None:
            original_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()

        def asyncio_hook(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            message = context.get("message", "asyncio unhandled exception")
            exception = context.get("exception")
            if isinstance(exception, BaseException):
                log_exception(type(exception), exception, exception.__traceback__, f"asyncio: {message}")
            else:
                _write_crash_text(f"asyncio: {message}\n{context!r}")

        loop.set_exception_handler(asyncio_hook)
    except Exception:
        _write_crash_text("Failed to install asyncio exception hook:\n" + traceback.format_exc())


def acquire_running_lock() -> bool:
    """Return True when a previous lock indicates the last run may have crashed."""
    path = app_running_lock_path()
    previous_crash = path.exists()
    try:
        path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        _write_crash_text("Failed to write app_running.lock:\n" + traceback.format_exc())
    return previous_crash


def release_running_lock() -> None:
    try:
        app_running_lock_path().unlink(missing_ok=True)
    except Exception:
        _write_crash_text("Failed to remove app_running.lock:\n" + traceback.format_exc())
