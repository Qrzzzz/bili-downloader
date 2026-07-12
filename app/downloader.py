from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

from .config import AppConfig
from .cookies import CredentialMode, cookiefile_lease
from .logger import LogEmitter, YtdlpQtLogger
from .utils import ensure_dir, find_ffmpeg, format_duration, sanitize_windows_filename


@dataclass
class VideoPart:
    index: int
    title: str
    url: str
    duration: int | None = None
    id: str = ""


@dataclass
class FormatChoice:
    label: str
    selector: str
    height: int | None = None


@dataclass
class VideoInfoResult:
    title: str
    uploader: str
    duration: int | None
    thumbnail_url: str
    parts: list[VideoPart]
    formats: list[FormatChoice]
    raw_id: str = ""
    current_part_index: int = 1


BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
BVID_PATH_RE = re.compile(r"/video/(BV[0-9A-Za-z]+)", re.IGNORECASE)
AVID_PATH_RE = re.compile(r"/video/(?:av)?(\d+)", re.IGNORECASE)


def base_ydl_options(
    config: AppConfig,
    emitter: LogEmitter | None = None,
    cookiefile: Path | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "logger": YtdlpQtLogger(emitter),
        "windowsfilenames": True,
        "noprogress": True,
    }
    if cookiefile is not None:
        opts["cookiefile"] = str(cookiefile)

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = str(Path(ffmpeg).parent)

    return opts


def build_format_choices(formats: list[dict[str, Any]]) -> list[FormatChoice]:
    choices: list[FormatChoice] = [
        FormatChoice("最高可用（yt-dlp bestvideo+bestaudio）", "bestvideo+bestaudio/best", None)
    ]

    heights: dict[int, set[str]] = {}
    for fmt in formats or []:
        if fmt.get("vcodec") == "none":
            continue
        height = fmt.get("height")
        if not isinstance(height, int) or height <= 0:
            continue
        notes = heights.setdefault(height, set())
        note = str(fmt.get("format_note") or fmt.get("resolution") or "").strip()
        if note:
            notes.add(note)

    def label_for_height(height: int, notes: set[str]) -> str:
        if height >= 2160:
            base = "2160p / 4K"
        elif height >= 1440:
            base = "1440p / 2K"
        else:
            base = f"{height}p"
        joined = "、".join(sorted(n for n in notes if n and n.lower() != "unknown"))
        if "高码率" in joined and "高码率" not in base:
            base = f"{base} 高码率"
        return base

    for height in sorted(heights.keys(), reverse=True):
        choices.append(
            FormatChoice(
                label=f"{label_for_height(height, heights[height])}（该高度内最佳音视频）",
                selector=f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                height=height,
            )
        )

    if len(choices) == 1:
        choices.append(FormatChoice("自动选择最佳可用格式", "bestvideo+bestaudio/best", None))

    return choices


def _entry_to_part(entry: dict[str, Any], fallback_url: str, index: int) -> VideoPart:
    title = str(entry.get("title") or entry.get("fulltitle") or f"P{index}")
    url = str(entry.get("webpage_url") or entry.get("url") or fallback_url)
    return VideoPart(
        index=index,
        title=title,
        url=url,
        duration=entry.get("duration"),
        id=str(entry.get("id") or ""),
    )


def _requested_page_from_url(url: str) -> int:
    try:
        values = parse_qs(urlparse(url).query).get("p") or []
        page = int(values[0])
    except (IndexError, TypeError, ValueError):
        return 1
    return max(page, 1)


def _resolve_b23_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host != "b23.tv" and not host.endswith(".b23.tv"):
        return url

    response = requests.get(
        url,
        allow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()
    return response.url


def _video_api_params(url: str) -> dict[str, str] | None:
    resolved = _resolve_b23_url(url)
    parsed = urlparse(resolved)
    bvid = BVID_PATH_RE.search(parsed.path)
    if bvid:
        return {"bvid": bvid.group(1)}

    aid = AVID_PATH_RE.search(parsed.path)
    if aid:
        return {"aid": aid.group(1)}

    return None


def _fetch_bilibili_view(url: str) -> dict[str, Any] | None:
    try:
        params = _video_api_params(url)
        if not params:
            return None

        response = requests.get(
            BILIBILI_VIEW_API,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": url,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logging.getLogger("bili_downloader").warning("Bilibili view API unavailable: %s", exc)
        return None

    if payload.get("code") != 0:
        logging.getLogger("bili_downloader").warning(
            "Bilibili view API failed: %s %s",
            payload.get("code"),
            payload.get("message"),
        )
        return None

    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return None


def _canonical_part_url(view: dict[str, Any], page: int) -> str:
    bvid = str(view.get("bvid") or "").strip()
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}?p={page}"

    aid = view.get("aid")
    if aid:
        return f"https://www.bilibili.com/video/av{aid}?p={page}"

    return ""


def _pages_to_parts(view: dict[str, Any]) -> list[VideoPart]:
    pages = view.get("pages") or []
    if not isinstance(pages, list) or not pages:
        return []

    parts: list[VideoPart] = []
    for fallback_index, page_info in enumerate(pages, start=1):
        if not isinstance(page_info, dict):
            continue

        page = page_info.get("page")
        index = page if isinstance(page, int) and page > 0 else fallback_index
        url = _canonical_part_url(view, index)
        if not url:
            continue

        title = str(page_info.get("part") or f"P{index}")
        raw_id = str(view.get("bvid") or view.get("aid") or "")
        part_id = f"{raw_id}_p{index}" if raw_id else ""
        parts.append(
            VideoPart(
                index=index,
                title=title,
                url=url,
                duration=page_info.get("duration"),
                id=part_id,
            )
        )

    return parts


def parse_video_info(
    url: str,
    config: AppConfig,
    emitter: LogEmitter | None = None,
    credential_mode: CredentialMode | str = CredentialMode.SAVED,
) -> VideoInfoResult:
    logging.getLogger("bili_downloader").info("开始解析：%s", url)
    requested_page = _requested_page_from_url(url)
    view = _fetch_bilibili_view(url)
    with cookiefile_lease(credential_mode) as cookiefile:
        opts = base_ydl_options(config, emitter, cookiefile)
        with YoutubeDL(opts) as ydl:
            if view:
                parts = _pages_to_parts(view)
                current_part_index = min(requested_page, len(parts)) if parts else 1
                reference_url = _canonical_part_url(view, current_part_index) or url
                reference = ydl.extract_info(reference_url, download=False)
                info = reference
            else:
                info = ydl.extract_info(url, download=False)
                entries = list(info.get("entries") or [])
                if entries:
                    parts = [_entry_to_part(entry, url, idx) for idx, entry in enumerate(entries, start=1)]
                    current_part_index = min(requested_page, len(parts))
                    reference = next((entry for entry in entries if entry.get("formats")), entries[0])
                    if not reference.get("formats") and parts:
                        reference = ydl.extract_info(parts[current_part_index - 1].url, download=False)
                else:
                    parts = [_entry_to_part(info, url, 1)]
                    current_part_index = 1
                    reference = info

    owner = view.get("owner") if isinstance(view, dict) else {}
    if not isinstance(owner, dict):
        owner = {}
    title = str((view or {}).get("title") or info.get("title") or reference.get("title") or "未命名视频")
    uploader = str(
        owner.get("name")
        or info.get("uploader")
        or info.get("uploader_id")
        or reference.get("uploader")
        or "-"
    )
    duration = (view or {}).get("duration") or info.get("duration") or reference.get("duration")
    thumbnail = str((view or {}).get("pic") or info.get("thumbnail") or reference.get("thumbnail") or "")
    formats = build_format_choices(reference.get("formats") or [])
    raw_id = str((view or {}).get("bvid") or info.get("id") or "")

    logging.getLogger("bili_downloader").info(
        "解析成功：%s，UP：%s，时长：%s，分P：%s，格式：%s",
        title,
        uploader,
        format_duration(duration),
        len(parts),
        len(formats),
    )
    return VideoInfoResult(title, uploader, duration, thumbnail, parts, formats, raw_id, current_part_index)


def fetch_thumbnail(url: str) -> bytes:
    if not url:
        return b""
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.content


class DownloadController:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def download_videos(
    parts: list[VideoPart],
    config: AppConfig,
    download_dir: str,
    format_selector: str,
    progress_hook,
    emitter: LogEmitter | None = None,
    controller: DownloadController | None = None,
    credential_mode: CredentialMode | str = CredentialMode.SAVED,
) -> list[str]:
    target_dir = ensure_dir(download_dir)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未检测到 FFmpeg，无法可靠合并音视频。")

    controller = controller or DownloadController()
    saved_files: list[str] = []

    def hook(status: dict[str, Any]) -> None:
        if controller.cancelled:
            raise DownloadCancelled("用户已取消下载")
        progress_hook(status)

    outtmpl = os.path.join(target_dir, "%(title).200B-%(id)s.%(ext)s")
    with cookiefile_lease(credential_mode) as cookiefile:
        opts = base_ydl_options(config, emitter, cookiefile)
        opts.update(
            {
                "format": format_selector,
                "outtmpl": {"default": outtmpl},
                "merge_output_format": "mp4",
                "continuedl": True,
                "retries": 5,
                "fragment_retries": 5,
                "windowsfilenames": True,
                "progress_hooks": [hook],
                "postprocessor_hooks": [hook],
                "paths": {"home": target_dir},
                "noplaylist": True,
            }
        )

        with YoutubeDL(opts) as ydl:
            for part in parts:
                if controller.cancelled:
                    raise DownloadCancelled("用户已取消下载")
                safe_title = sanitize_windows_filename(part.title)
                if emitter:
                    emitter.message.emit(f"开始下载 P{part.index}：{safe_title}")
                ydl.download([part.url])
                saved_files.append(target_dir)

    return saved_files
