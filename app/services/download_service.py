from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.config import AppConfig
from app.cookies import CredentialMode, GenerationPolicy
from app.downloader import (
    DownloadBatchCancelled, DownloadBatchResult, DownloadController, DownloadMode,
    PartDownloadResult, PartDownloadStatus, VideoPart, download_videos,
)
from app.logger import LogSink, redact_sensitive
from app.utils import classify_error_details


@dataclass(frozen=True)
class DownloadRequest:
    source_url: str
    video_title: str
    parts: tuple[VideoPart, ...]
    config: AppConfig
    download_dir: str
    format_selector: str
    format_label: str
    credential_mode: CredentialMode
    mode: DownloadMode
    expected_generation: str | None | GenerationPolicy = GenerationPolicy.UNBOUND


def run_download(
    request: DownloadRequest,
    controller: DownloadController,
    progress: Callable[[dict], None],
    log: LogSink,
    parts: tuple[VideoPart, ...] | None = None,
) -> DownloadBatchResult:
    selected = request.parts if parts is None else parts
    try:
        return download_videos(
            list(selected), request.config, request.download_dir, request.format_selector,
            progress, log, controller, request.credential_mode, request.mode,
            expected_generation=request.expected_generation,
        )
    except DownloadBatchCancelled as exc:
        return exc.result
    except Exception as exc:
        # Preflight/global failures are results for every requested P, preserving retry.
        error = classify_error_details(exc)
        return DownloadBatchResult(
            PartDownloadResult(part, PartDownloadStatus.FAILED, error=error, detail=redact_sensitive(exc))
            for part in selected
        )
