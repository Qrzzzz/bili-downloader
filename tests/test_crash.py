from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def crash(monkeypatch: pytest.MonkeyPatch) -> Any:
    module = importlib.import_module("app.crash")
    current_pid = os.getpid()

    def query(pid: int) -> tuple[str, int | None]:
        if pid == current_pid:
            return "alive", 9_999_999
        return "dead", None

    monkeypatch.setattr(module, "_query_process_start_token", query)
    return module


def _write_marker(module: Any, *, pid: int, start: int, instance: str) -> Path:
    path = module.run_markers_dir() / f"run-{pid}-{instance}.json"
    module._write_run_marker_atomic(
        path,
        {
            "schema_version": module.RUN_MARKER_SCHEMA_VERSION,
            "pid": pid,
            "process_start_token": start,
            "instance_id": instance,
            "created_at": time.time(),
        },
    )
    return path


def test_normal_exit_removes_only_owned_structured_marker(crash: Any) -> None:
    other = _write_marker(crash, pid=101, start=1_001, instance="a" * 32)
    crash._process_identity_status = lambda pid, _start: "active" if pid == 101 else "dead"

    assert crash.acquire_running_lock() is False
    owned = crash._RUN_MARKER_PATH
    assert owned is not None and owned.exists()
    payload = json.loads(owned.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["pid"] == os.getpid()

    crash.release_running_lock()

    assert not owned.exists()
    assert other.exists()


def test_hard_crash_residue_and_reboot_dead_pid_are_detected(crash: Any) -> None:
    stale = _write_marker(crash, pid=202, start=2_002, instance="b" * 32)
    crash._process_identity_status = lambda _pid, _start: "dead"

    assert crash.acquire_running_lock() is True
    assert not stale.exists()
    assert crash._RUN_MARKER_PATH is not None and crash._RUN_MARKER_PATH.exists()
    crash.release_running_lock()


def test_two_live_instances_do_not_trigger_safe_mode_or_delete_each_other(crash: Any) -> None:
    active = _write_marker(crash, pid=303, start=3_003, instance="c" * 32)
    crash._process_identity_status = lambda pid, _start: "active" if pid == 303 else "dead"

    assert crash.acquire_running_lock() is False
    current = crash._RUN_MARKER_PATH
    assert current is not None and current.exists() and active.exists()

    crash.release_running_lock()
    assert not current.exists()
    assert active.exists()


def test_pid_reuse_is_stale_even_when_pid_is_alive(crash: Any) -> None:
    reused = _write_marker(crash, pid=404, start=4_004, instance="d" * 32)
    crash._process_identity_status = lambda pid, _start: "reused" if pid == 404 else "active"

    assert crash.acquire_running_lock() is True
    assert not reused.exists()
    crash.release_running_lock()


def test_corrupt_owned_marker_is_detected_but_unknown_schema_is_preserved(crash: Any) -> None:
    root = crash.run_markers_dir()
    root.mkdir(parents=True)
    corrupt = root / f"run-505-{'e' * 32}.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    future = root / f"run-506-{'f' * 32}.json"
    future.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "pid": 506,
                "process_start_token": 5_006,
                "instance_id": "f" * 32,
                "created_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    assert crash.acquire_running_lock() is True
    assert not corrupt.exists()
    assert future.exists()
    crash.release_running_lock()


def test_unreadable_marker_is_not_assumed_crashed_or_deleted(
    crash: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreadable = _write_marker(crash, pid=606, start=6_006, instance="1" * 32)
    original_read_text = Path.read_text

    def deny(self: Path, *args: object, **kwargs: object) -> str:
        if self == unreadable:
            raise PermissionError("synthetic denial")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)

    assert crash.acquire_running_lock() is False
    assert unreadable.exists()
    crash.release_running_lock()


def test_marker_write_permission_failure_does_not_crash_application(
    crash: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        crash,
        "_write_run_marker_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("synthetic denial")),
    )

    assert crash.acquire_running_lock() is False
    assert crash._RUN_MARKER_PATH is None
    crash.release_running_lock()


def test_legacy_v1_3_marker_uses_pid_and_creation_time_instead_of_existence(
    crash: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_at = "2026-08-30 12:00:00"
    marker_epoch = time.mktime(time.strptime(started_at, "%Y-%m-%d %H:%M:%S"))
    token = int(marker_epoch * 10_000_000 + crash._WINDOWS_EPOCH_OFFSET_100NS)
    legacy = crash.app_running_lock_path()
    legacy.write_text(json.dumps({"pid": 707, "started_at": started_at}), encoding="utf-8")
    current_pid = os.getpid()

    def active_query(pid: int) -> tuple[str, int | None]:
        if pid == 707:
            return "alive", token
        if pid == current_pid:
            return "alive", 9_999_999
        return "dead", None

    monkeypatch.setattr(crash, "_query_process_start_token", active_query)
    assert crash.acquire_running_lock() is False
    assert legacy.exists()
    crash.release_running_lock()

    monkeypatch.setattr(
        crash,
        "_query_process_start_token",
        lambda pid: ("alive", 9_999_999) if pid == current_pid else ("alive", token + 1_000_000_000),
    )
    assert crash.acquire_running_lock() is True
    assert not legacy.exists()
    crash.release_running_lock()
