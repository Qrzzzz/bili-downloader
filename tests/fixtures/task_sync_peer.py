"""Separate process exercising production TaskManager with controlled callbacks."""
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import AppConfig
from app.cookies import CredentialMode
from app.downloader import VideoInfoResult, VideoPart, FormatChoice, DownloadBatchResult, PartDownloadResult, PartDownloadStatus
from app.services.task_service import TaskManager
from app.services.task_staging import stage_lease

folder = Path(sys.argv[1])
folder.mkdir(exist_ok=True)
url = "https://www.bilibili.com/video/BV1234567890"
info = VideoInfoResult("fixture", "", 1, "", [VideoPart(1, "p1", url, 1, "cid")],
                       [FormatChoice("最高可用", "bestvideo+bestaudio/best")], source_url=url, raw_id="BV1234567890")
parsed = SimpleNamespace(info=info, mode=CredentialMode.ANONYMOUS, generation=None)
commands = queue.Queue()
started = threading.Event()
saved = []
manager = TaskManager(lambda _: None, AppConfig(download_dir=str(folder)), "sync-peer")
save = manager._save
def save_counted(task):
    saved.append(task["state"])
    save(task)
manager._save = save_counted

def download(request, controller, progress, log, parts):
    lease = stage_lease(str(folder), controller.task_id)
    assert lease.acquire()
    try:
        stage = folder / ".bili-tasks" / controller.task_id
        stage.mkdir(parents=True)
        (stage / "fixture.part").write_bytes(b"resume")
        started.set()
        while True:
            value, done = commands.get(timeout=20)
            if value == "finish":
                done.set()
                return DownloadBatchResult(PartDownloadResult(p, PartDownloadStatus.COMPLETED) for p in parts)
            if value == "cancel":
                controller.cancel()
                done.set()
                return DownloadBatchResult(PartDownloadResult(p, PartDownloadStatus.CANCELLED) for p in parts)
            for _ in range(value.get("repeat", 1)):
                progress(value)
            done.set()
    finally:
        lease.close()

with patch("app.services.task_service.parse_video", lambda *a: info), patch("app.services.task_service.run_download", download):
    task = manager.create(parsed, [1], "audio_video", "0", str(folder), "sync-token")["task"]
    manager.start_ready()
    assert started.wait(5)
    print(json.dumps({"task_id": task["task_id"], "pid": os.getpid()}), flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        before = len(saved)
        done = threading.Event()
        commands.put((command, done))
        assert done.wait(5)
        if isinstance(command, str):
            deadline = time.monotonic() + 5
            while manager.running and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not manager.running
            manager.close(); manager.wait()
        print(json.dumps({"writes": len(saved) - before}), flush=True)
        if isinstance(command, str):
            break
