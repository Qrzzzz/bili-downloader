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


def crash_log_path() -> Path:
    path = logs_dir() / "crash.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def app_running_lock_path() -> Path:
    return app_data_dir() / "app_running.lock"


def _write_crash_text(text: str) -> None:
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with crash_log_path().open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== {timestamp} =====\n")
            handle.write(redact_sensitive(text))
            if not text.endswith("\n"):
                handle.write("\n")
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
