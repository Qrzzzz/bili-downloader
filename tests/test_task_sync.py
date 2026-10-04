from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("ending", ["finish", "cancel", "crash"])
def test_separate_process_phase_progress_ownership_staging_and_recovery(isolated_paths, ending):
    from app.config import AppConfig
    from app.backend.protocol import ProtocolError
    from app.services.task_service import TaskManager
    from app.services.task_staging import stage_lease
    folder = isolated_paths.root / "sync-downloads"
    peer = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fixtures/task_sync_peer.py"), str(folder)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    observer = None
    worker_pid = None
    try:
        ready = json.loads(peer.stdout.readline())
        task_id, worker_pid = ready["task_id"], ready["pid"]
        observer = TaskManager(lambda _: None, AppConfig(download_dir=str(folder)), "observer")
        def send(value):
            peer.stdin.write(json.dumps(value) + "\n"); peer.stdin.flush()
            return json.loads(peer.stdout.readline())
        for phase in ["downloading", "waiting_resources", "merging", "converting", "verifying"]:
            writes = send({"phase": phase, "overall_percent": 42, "speed": 1048576, "repeat": 30})["writes"]
            assert writes == 1  # Phase transition immediate, repeated callbacks throttled.
            task = next(t for t in observer.snapshot()["tasks"] if t["task_id"] == task_id)
            assert task["state"] == phase and task["progress"]["overall_percent"] == 42
            assert task["progress"]["speed_bytes_per_second"] == 1048576 and task["foreign"]
            for action in ["cancel", "reorder", "remove", "resume"]:
                with pytest.raises(ProtocolError, match="另一个"):
                    observer.command(action, task_id)
            entry = observer.staging.scan()["entries"][0]
            assert entry["category"] == "active" and not entry["can_clean"]
        time.sleep(1.05)
        assert send({"phase": "verifying", "overall_percent": 57, "speed": 2097152})["writes"] == 1
        assert observer.snapshot()["tasks"][0]["progress"]["overall_percent"] == 57
        assert send({"phase": "verifying", "overall_percent": 58, "speed": 2097152})["writes"] == 0
        deadline = time.monotonic() + 2
        while observer.snapshot()["tasks"][0]["progress"]["overall_percent"] != 58 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert observer.snapshot()["tasks"][0]["progress"]["overall_percent"] == 58
        # Leave a flush pending across completion/exit; it must never resurrect
        # an in-flight stage after the terminal record or database close.
        send({"phase": "verifying", "overall_percent": 59})
        if ending == "crash":
            os.kill(worker_pid, signal.SIGTERM); peer.wait(timeout=5)
        else:
            send(ending); peer.wait(timeout=5)
            assert peer.returncode == 0, peer.stderr.read()
        task = observer.snapshot()["tasks"][0]
        assert task["state"] == {"finish": "completed", "cancel": "cancelled", "crash": "interrupted"}[ending]
        assert task.get("progress") is None and not task["foreign"]
        time.sleep(1.05)
        assert observer.snapshot()["tasks"][0].get("progress") is None
        entry = observer.staging.scan()["entries"][0]
        assert entry["category"] == ("completed" if ending == "finish" else "recoverable")
        # The kernel releases staging leases even after a forced process exit.
        lease = stage_lease(str(folder), task_id)
        assert lease.acquire(); lease.close()
        if ending == "crash":
            observer.command("resume", task_id)
            assert observer.snapshot()["tasks"][0]["state"] == "queued"
    finally:
        if peer.poll() is None:
            if worker_pid:
                os.kill(worker_pid, signal.SIGTERM)
            peer.kill(); peer.wait(timeout=5)
        for stream in [peer.stdin, peer.stdout, peer.stderr]:
            stream.close()
        if observer:
            observer.close(); observer.wait()
