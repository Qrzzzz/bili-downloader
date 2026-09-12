"""Production queue/IPC with deterministic metadata and cancellable media work."""
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.backend import host
from app.services import task_service
from app.downloader import VideoInfoResult, VideoPart, FormatChoice, DownloadBatchResult, PartDownloadResult, PartDownloadStatus
from app.video_urls import normalize_video_input


def parse(value, *args):
    url = normalize_video_input(value)
    name = url.split("/")[-1]
    return VideoInfoResult(name, "fixture", 1, "", [VideoPart(1, name, url, 1, "p1")],
                           [FormatChoice("最高可用", "bestvideo+bestaudio/best")], raw_id=name, source_url=url)


def download(request, controller, progress, log, parts):
    for i in range(100):
        if controller.cancelled:
            break
        progress({"phase": "downloading", "part_index": 1, "part_number": 1, "part_count": 1, "overall_percent": i,
                  "speed": 1048576, "downloaded_bytes": i * 1024})
        time.sleep(0.04)
    status = PartDownloadStatus.CANCELLED if controller.cancelled else PartDownloadStatus.COMPLETED
    return DownloadBatchResult(PartDownloadResult(p, status) for p in parts)


with patch.object(host, "parse_video", parse), patch.object(task_service, "parse_video", parse), patch.object(task_service, "run_download", download):
    raise SystemExit(host.serve(sys.stdin.buffer, sys.stdout.buffer))
