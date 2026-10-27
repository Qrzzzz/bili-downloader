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


parse_attempts = {}


def parse(value, *args):
    url = normalize_video_input(value)
    name = url.split("/")[-1]
    parse_attempts[name] = parse_attempts.get(name, 0) + 1
    if name == "BV1234567819" and parse_attempts[name] == 1:
        raise RuntimeError("Deterministic first-attempt failure")
    if name == "BV1234567818":
        time.sleep(1.5)
    formats = [FormatChoice("最高可用", "bestvideo+bestaudio/best")]
    if not name.endswith("1"):
        formats.append(FormatChoice("1080p", "bestvideo[height=1080]+bestaudio/best[height=1080]", 1080, "exact_height"))
    formats.append(FormatChoice("720p", "bestvideo[height=720]+bestaudio/best[height=720]", 720, "exact_height"))
    return VideoInfoResult(name, "fixture", 2, "", [VideoPart(1, name, url, 1, "p1"),
        VideoPart(2, name + " second", url + "?p=2", 1, "p2")],
                           formats, raw_id=name, source_url=url)


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
