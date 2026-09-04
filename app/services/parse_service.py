from __future__ import annotations

from app.config import AppConfig
from app.cookies import CredentialMode
from app.downloader import VideoInfoResult, parse_video_info
from app.logger import LogSink
from app.utils import AppError, ErrorKind, normalize_bilibili_url


def parse_video(value: str, config: AppConfig, mode: CredentialMode, log: LogSink) -> VideoInfoResult:
    try:
        url = normalize_bilibili_url(value)
    except ValueError as exc:
        raise AppError(ErrorKind.INVALID_URL, str(exc)) from exc
    return parse_video_info(url, config, log, mode)
