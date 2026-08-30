from __future__ import annotations

import asyncio
import ctypes
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

from .config import app_data_dir, logs_dir
from .logger import redact_sensitive


CRASH_LOG_MAX_BYTES = 2 * 1024 * 1024
CRASH_LOG_BACKUP_COUNT = 2
_CRASH_LOG_LOCK = threading.RLock()
_TRUNCATION_MARKER = b"\n...[crash log entry truncated]...\n"
RUN_MARKER_SCHEMA_VERSION = 1
RUN_MARKER_MAX_BYTES = 16 * 1024
RUN_MARKER_RE = re.compile(r"^run-(\d+)-([0-9a-f]{32})\.json$")
_RUN_MARKER_LOCK = threading.RLock()
_RUN_MARKER_PATH: Path | None = None
_RUN_MARKER_INSTANCE_ID: str | None = None
_WINDOWS_EPOCH_OFFSET_100NS = 116_444_736_000_000_000
_PORTABLE_PROCESS_START_TOKEN = time.time_ns()


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def crash_log_path() -> Path:
    path = logs_dir() / "crash.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def app_running_lock_path() -> Path:
    """Return the v1.3 legacy marker path for one-time compatibility handling."""

    return app_data_dir() / "app_running.lock"


def run_markers_dir() -> Path:
    return app_data_dir() / "run-markers"


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


def _query_process_start_token(pid: int) -> tuple[str, int | None]:
    """Return (alive/dead/unknown, Windows creation FILETIME token)."""

    if pid <= 0:
        return "dead", None
    if os.name != "nt":
        if pid == os.getpid():
            return "alive", _PORTABLE_PROCESS_START_TOKEN
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "dead", None
        except (OSError, PermissionError):
            return "unknown", None
        return "unknown", None

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ("dead", None) if ctypes.get_last_error() == error_invalid_parameter else ("unknown", None)
    creation = _FileTime()
    exit_time = _FileTime()
    kernel_time = _FileTime()
    user_time = _FileTime()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return "unknown", None
        return "alive", (int(creation.high) << 32) | int(creation.low)
    finally:
        kernel32.CloseHandle(handle)


def _process_identity_status(pid: int, expected_start_token: int) -> str:
    state, actual = _query_process_start_token(pid)
    if state != "alive":
        return state
    return "active" if actual == expected_start_token else "reused"


def _unlink_owned_marker(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _write_crash_text(f"Failed to remove stale run marker {path.name}:\n" + traceback.format_exc())


def _load_run_marker(path: Path, filename_pid: int, filename_instance: str) -> dict[str, Any]:
    if path.stat().st_size > RUN_MARKER_MAX_BYTES:
        raise ValueError("run marker too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("run marker is not an object")
    if payload.get("schema_version") != RUN_MARKER_SCHEMA_VERSION:
        raise LookupError("unsupported run marker schema")
    pid = payload.get("pid")
    start_token = payload.get("process_start_token")
    instance_id = payload.get("instance_id")
    created_at = payload.get("created_at")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid != filename_pid
        or isinstance(start_token, bool)
        or not isinstance(start_token, int)
        or start_token <= 0
        or instance_id != filename_instance
        or isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not (0 < float(created_at) <= time.time() + 300)
    ):
        raise ValueError("run marker fields are invalid")
    return payload


def _scan_versioned_run_markers() -> bool:
    root = run_markers_dir()
    try:
        candidates = list(root.iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        _write_crash_text("Failed to enumerate run markers:\n" + traceback.format_exc())
        return False

    previous_crash = False
    for path in candidates:
        match = RUN_MARKER_RE.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            continue
        try:
            payload = _load_run_marker(path, int(match.group(1)), match.group(2))
        except LookupError:
            # A newer instance may own a future schema. Leave it untouched.
            continue
        except (OSError, UnicodeError):
            # Permission/read failures are not evidence of a crash and may refer
            # to a live instance. Leave the marker untouched.
            _write_crash_text(f"Failed to read run marker {path.name}:\n" + traceback.format_exc())
            continue
        except (ValueError, TypeError, json.JSONDecodeError):
            previous_crash = True
            _unlink_owned_marker(path)
            continue

        status = _process_identity_status(int(payload["pid"]), int(payload["process_start_token"]))
        if status in {"active", "unknown"}:
            continue
        previous_crash = True
        _unlink_owned_marker(path)
    return previous_crash


def _legacy_marker_start_epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except (OverflowError, ValueError):
        return None


def _filetime_to_unix_seconds(value: int) -> float:
    return (value - _WINDOWS_EPOCH_OFFSET_100NS) / 10_000_000


def _scan_legacy_running_lock() -> bool:
    path = app_running_lock_path()
    try:
        if not path.exists():
            return False
        if path.is_symlink() or path.stat().st_size > RUN_MARKER_MAX_BYTES:
            _unlink_owned_marker(path)
            return True
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("legacy marker is not an object")
        pid = payload.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("legacy marker pid is invalid")
    except (OSError, UnicodeError):
        _write_crash_text("Failed to read legacy app_running.lock:\n" + traceback.format_exc())
        return False
    except (ValueError, TypeError, json.JSONDecodeError):
        _unlink_owned_marker(path)
        return True

    state, process_start = _query_process_start_token(pid)
    if state == "unknown":
        return False
    if state == "alive" and process_start is not None:
        marker_start = _legacy_marker_start_epoch(payload.get("started_at"))
        if marker_start is not None and abs(_filetime_to_unix_seconds(process_start) - marker_start) <= 60:
            return False
    _unlink_owned_marker(path)
    return True


def _write_run_marker_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def acquire_running_lock() -> bool:
    """Create this instance's marker and report only confidently stale runs."""

    global _RUN_MARKER_INSTANCE_ID, _RUN_MARKER_PATH
    with _RUN_MARKER_LOCK:
        if _RUN_MARKER_PATH is not None:
            return False
        versioned_crash = _scan_versioned_run_markers()
        legacy_crash = _scan_legacy_running_lock()
        previous_crash = versioned_crash or legacy_crash
        state, start_token = _query_process_start_token(os.getpid())
        if state != "alive" or start_token is None:
            _write_crash_text("Failed to establish current process identity; run marker was not created.")
            return previous_crash

        instance_id = uuid.uuid4().hex
        path = run_markers_dir() / f"run-{os.getpid()}-{instance_id}.json"
        payload = {
            "schema_version": RUN_MARKER_SCHEMA_VERSION,
            "pid": os.getpid(),
            "process_start_token": start_token,
            "instance_id": instance_id,
            "created_at": time.time(),
        }
        try:
            _write_run_marker_atomic(path, payload)
        except Exception:
            _write_crash_text("Failed to write versioned run marker:\n" + traceback.format_exc())
            return previous_crash
        _RUN_MARKER_PATH = path
        _RUN_MARKER_INSTANCE_ID = instance_id
        return previous_crash


def release_running_lock() -> None:
    """Remove only the marker whose random identity is owned by this instance."""

    global _RUN_MARKER_INSTANCE_ID, _RUN_MARKER_PATH
    with _RUN_MARKER_LOCK:
        path = _RUN_MARKER_PATH
        instance_id = _RUN_MARKER_INSTANCE_ID
        _RUN_MARKER_PATH = None
        _RUN_MARKER_INSTANCE_ID = None
        if path is None or instance_id is None:
            return
        try:
            match = RUN_MARKER_RE.fullmatch(path.name)
            if match is None or match.group(2) != instance_id or not path.exists():
                return
            payload = _load_run_marker(path, int(match.group(1)), instance_id)
            if payload.get("pid") != os.getpid():
                return
            path.unlink()
            root = path.parent
            if root.exists() and not any(root.iterdir()):
                root.rmdir()
        except Exception:
            _write_crash_text("Failed to remove owned run marker:\n" + traceback.format_exc())
