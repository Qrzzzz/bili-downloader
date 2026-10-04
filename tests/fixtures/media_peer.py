"""Real cross-process stage admission; no network or media placeholders."""
from pathlib import Path
import sys
import os
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.downloader import DownloadController, _media_slot
from yt_dlp.utils import DownloadCancelled

controller = DownloadController()
controller.task_id = sys.argv[1]
released = threading.Event()

def record(stage):
    path = os.environ.get("BILI_MEDIA_ORDER")
    if path:
        with open(path, "a", encoding="utf-8") as log:
            log.write(stage + "\n")

def commands():
    for line in sys.stdin:
        if line.strip() == "cancel":
            controller.cancel()
        else:
            released.set()

threading.Thread(target=commands, daemon=True).start()
print("requested", flush=True)
try:
    with _media_slot(controller):
        record(controller.task_id)
        print("acquired", flush=True)
        if controller.task_id == "merge":
            released.wait(10)
        else:
            time.sleep(0.15)
    if controller.task_id == "merge":
        with _media_slot(controller):
            record("merge-verification")
            print("verification-acquired", flush=True)
except DownloadCancelled:
    print("cancelled", flush=True)
