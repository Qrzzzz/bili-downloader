"""Explicit transport projections. Never serialize yt-dlp dictionaries or credentials."""
from __future__ import annotations

import math
from typing import Any

from app.downloader import DownloadBatchResult, VideoInfoResult
from app.logger import redact_sensitive
from app.utils import ErrorClassification, classify_error_details


def error_dto(error: ErrorClassification | Exception, detail: str = "") -> dict:
    classified = error if isinstance(error, ErrorClassification) else classify_error_details(error)
    return {"code": classified.code, "message": classified.message,
            "retryable": classified.retryable, "detail": redact_sensitive(detail or error)[:8192]}


def video_dto(info: VideoInfoResult, parse_id: str, revision: int) -> dict:
    return {
        "parse_id": parse_id, "input_revision": revision,
        "title": info.title, "uploader": info.uploader, "duration_seconds": info.duration,
        "raw_id": info.raw_id, "source_url": info.source_url,
        "current_part_index": info.current_part_index, "thumbnail_available": bool(info.thumbnail_url),
        "parts": [{"index": p.index, "title": p.title, "duration_seconds": p.duration, "id": p.id} for p in info.parts],
        "formats": [{"format_id": str(i), "label": f.label, "height": f.height, "policy": f.policy} for i, f in enumerate(info.formats)],
    }


def progress_dto(value: dict[str, Any]) -> dict:
    result: dict[str, Any] = {"phase": str(value.get("phase", "preparing"))[:64]}
    for name in ("part_index", "part_number", "part_count", "part_percent", "overall_percent",
                 "downloaded_bytes", "total_bytes", "total_bytes_estimate", "speed", "eta"):
        item = value.get(name)
        key = {"speed": "speed_bytes_per_second", "eta": "eta_seconds"}.get(name, name)
        result[key] = item if type(item) in (int, float) and math.isfinite(item) and item >= 0 else None
    return result


def batch_dto(result: DownloadBatchResult, batch_id: str, retry_allowed: bool, output_dir: str = "") -> dict:
    outcome = "cancelled" if result.cancelled else "partial" if result.failed and result.completed else "failed" if result.failed else "completed"
    return {
        "batch_id": batch_id, "outcome": outcome, "saved_files": list(result.saved_files),
        "output_dir": output_dir,
        "retry_allowed": retry_allowed and bool(result.failed),
        "part_results": [{"index": p.part.index, "title": p.part.title, "status": p.status.value,
                          "saved_files": list(p.saved_files),
                          "error": error_dto(p.error, p.detail) if p.error else None}
                         for p in result.part_results],
    }
