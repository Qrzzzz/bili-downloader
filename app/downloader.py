from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import DownloadCancelled

from .config import AppConfig
from .cookies import CredentialMode, GenerationPolicy, cookiefile_lease
from .logger import LogSink, YtdlpLogger, redact_sensitive
from .video_urls import BV_RE, ResolvedVideoUrl, canonicalize_video_url, validate_redirect_hop
from .utils import (
    AppError,
    ErrorClassification,
    ErrorKind,
    classify_error_details,
    ensure_dir,
    find_ffmpeg,
    format_duration,
    require_ffmpeg,
    sanitize_windows_filename,
)


ProgressHook = Callable[[dict[str, Any]], None]


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
    policy: str = "best_per_part"


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
    source_url: str = ""


class PartDownloadStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadMode(str, Enum):
    AUDIO_VIDEO = "audio_video"
    AUDIO_MP3 = "audio_mp3"


@dataclass(frozen=True)
class PartDownloadResult:
    part: VideoPart
    status: PartDownloadStatus
    saved_files: tuple[str, ...] = ()
    error: ErrorClassification | None = None
    detail: str = ""


class DownloadBatchResult(list[str]):
    """Structured result that remains compatible with the former ``list[str]`` API."""

    def __init__(self, part_results: Iterable[PartDownloadResult]) -> None:
        self.part_results = tuple(part_results)
        saved = [path for result in self.part_results for path in result.saved_files]
        super().__init__(dict.fromkeys(saved))

    @property
    def saved_files(self) -> tuple[str, ...]:
        return tuple(self)

    @property
    def cancelled(self) -> bool:
        return any(result.status is PartDownloadStatus.CANCELLED for result in self.part_results)

    @property
    def failed(self) -> tuple[PartDownloadResult, ...]:
        return tuple(result for result in self.part_results if result.status is PartDownloadStatus.FAILED)

    @property
    def completed(self) -> tuple[PartDownloadResult, ...]:
        return tuple(result for result in self.part_results if result.status is PartDownloadStatus.COMPLETED)

    def merged_with_retry(self, retry_result: DownloadBatchResult) -> DownloadBatchResult:
        """Replace retried part results while preserving the original batch order."""

        replacements = {result.part.url: result for result in retry_result.part_results}
        merged = [replacements.pop(result.part.url, result) for result in self.part_results]
        merged.extend(replacements.values())
        return DownloadBatchResult(merged)


class DownloadBatchCancelled(DownloadCancelled):
    def __init__(self, result: DownloadBatchResult) -> None:
        self.result = result
        super().__init__("用户已取消下载")


@dataclass(frozen=True)
class MissingPartFormat:
    part: VideoPart
    available_heights: tuple[int, ...]


class FormatPreflightError(AppError):
    def __init__(
        self,
        height: int | None,
        missing: Iterable[MissingPartFormat],
        mode: DownloadMode = DownloadMode.AUDIO_VIDEO,
    ) -> None:
        self.height = height
        self.missing = tuple(missing)
        if mode is DownloadMode.AUDIO_MP3:
            missing_text = "、".join(f"P{item.part.index}" for item in self.missing)
            requested = "可下载音频格式"
        else:
            missing_text = "、".join(
                f"P{item.part.index}（可用：{','.join(map(str, item.available_heights)) or '无'}）"
                for item in self.missing
            )
            requested = f"{height}p" if height else "可下载视频格式"
        super().__init__(ErrorKind.FORMAT_UNAVAILABLE, f"{requested} 预检失败：{missing_text}")


@dataclass(frozen=True)
class PlannedPart:
    part: VideoPart
    selector: str
    estimated_bytes: int | None = None
    output_title: str = ""
    output_id: str = ""


@dataclass(frozen=True)
class DownloadPlan:
    parts: tuple[PlannedPart, ...]
    requested_height: int | None = None
    mode: DownloadMode = DownloadMode.AUDIO_VIDEO


BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
HEIGHT_SELECTOR_RE = re.compile(r"height\s*(?:<=|=)\s*(\d+)", re.IGNORECASE)
B23_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
B23_MAX_REDIRECTS = 5
B23_TIMEOUT = (3.05, 8.0)
THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024
THUMBNAIL_TIMEOUT = (3.05, 8.0)
THUMBNAIL_MAX_REDIRECTS = 3
THUMBNAIL_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
}
THUMBNAIL_HOST_DOMAINS = ("hdslb.com", "biliimg.com", "bilibili.com")
BILIBILI_EXTRACTOR_KEY = "BiliBili"
BILIBILI_DURL_MUXED = "__bili_downloader_durl_muxed"
OUTPUT_TITLE_MAX_BYTES = 120
OUTPUT_ID_MAX_BYTES = 48
OUTPUT_COLLISION_LIMIT = 1000
MEDIA_PROBE_TIMEOUT = 10.0


def base_ydl_options(
    config: AppConfig,
    emitter: LogSink | None = None,
    cookiefile: Path | None = None,
    ffmpeg_path: str | None = None,
) -> dict[str, Any]:
    _ = config
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "logger": YtdlpLogger(emitter),
        "windowsfilenames": True,
        "noprogress": True,
        "socket_timeout": 20,
    }
    if cookiefile is not None:
        opts["cookiefile"] = str(cookiefile)
    if ffmpeg_path is not None and not Path(ffmpeg_path).is_absolute():
        raise AppError(ErrorKind.FFMPEG_BROKEN, "FFmpeg 路径必须是绝对路径")
    # In the locked yt-dlp, None enables bare-name search, while an empty
    # location disables both ffmpeg and ffprobe (os.path.exists('') is false).
    # Unlike a made-up missing filename, this sentinel cannot become a file.
    opts["ffmpeg_location"] = ffmpeg_path if ffmpeg_path is not None else find_ffmpeg() or ""
    return opts


@contextmanager
def _youtube_dl(options: dict[str, Any]) -> Iterator[YoutubeDL]:
    # yt-dlp's FFmpegFD.available() constructs a postprocessor without a
    # downloader, so options alone do not constrain that fallback. Match the
    # locked library's CLI context, scoped to this operation/thread and reset
    # even when construction, extraction or postprocessing fails.
    token = FFmpegPostProcessor._ffmpeg_location.set(options["ffmpeg_location"])
    try:
        with YoutubeDL(options) as ydl:
            yield ydl
    finally:
        FFmpegPostProcessor._ffmpeg_location.reset(token)


def build_format_choices(formats: list[dict[str, Any]]) -> list[FormatChoice]:
    choices = [FormatChoice("最高可用（各分 P 分别选择）", "bestvideo+bestaudio/best")]
    heights: dict[int, set[str]] = {}
    for fmt in formats or []:
        if fmt.get("vcodec") == "none":
            continue
        height = fmt.get("height")
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
            continue
        note = str(fmt.get("format_note") or fmt.get("resolution") or "").strip()
        if note:
            heights.setdefault(height, set()).add(note)
        else:
            heights.setdefault(height, set())

    labels = {4320: "4320p / 8K", 2160: "2160p / 4K", 1440: "1440p / 2K"}
    for height in sorted(heights, reverse=True):
        label = labels.get(height, f"{height}p")
        notes = "、".join(sorted(note for note in heights[height] if note.lower() != "unknown"))
        if "高码率" in notes:
            label += " 高码率"
        selector = f"bestvideo[height={height}]+bestaudio/best[height={height}]"
        choices.append(FormatChoice(f"{label}（严格匹配，不降档）", selector, height, "exact_height"))
    return choices


def resolve_bilibili_url(url: str) -> ResolvedVideoUrl:
    value = url.strip()
    parsed, host = validate_redirect_hop(value)
    if host != "b23.tv":
        return canonicalize_video_url(value)

    current = value
    seen: set[str] = set()
    for redirect_count in range(B23_MAX_REDIRECTS):
        parsed, _host = validate_redirect_hop(current)
        hop_key = parsed.geturl()
        if hop_key in seen:
            raise ValueError("b23.tv 短链接包含重定向循环。")
        seen.add(hop_key)

        try:
            response = requests.get(
                current,
                allow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=B23_TIMEOUT,
            )
        except requests.RequestException:
            raise ValueError("b23.tv 短链接请求失败。") from None
        try:
            status = int(response.status_code)
            if status not in B23_REDIRECT_STATUSES:
                raise ValueError("b23.tv 短链接未返回有效重定向。")
            location = response.headers.get("Location")
            if not isinstance(location, str) or not location.strip():
                raise ValueError("b23.tv 短链接重定向位置无效。")
            candidate = urljoin(current, location.strip())
            validate_redirect_hop(candidate)
            try:
                return canonicalize_video_url(candidate)
            except ValueError:
                if redirect_count + 1 >= B23_MAX_REDIRECTS:
                    raise ValueError("b23.tv 短链接重定向次数过多。") from None
                current = candidate
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    raise ValueError("b23.tv 短链接重定向次数过多。")


def _resolve_b23_url(url: str) -> str:
    return resolve_bilibili_url(url).canonical_url


def _requested_page_from_url(url: str) -> int:
    try:
        values = parse_qs(urlparse(url).query, keep_blank_values=True).get("p") or []
        page = int(values[0])
    except (IndexError, TypeError, ValueError):
        return 1
    return max(page, 1)


def _video_api_params(url: str | ResolvedVideoUrl) -> dict[str, str] | None:
    resolved = url if isinstance(url, ResolvedVideoUrl) else resolve_bilibili_url(url)
    return resolved.api_params or None


def _fetch_bilibili_view(url: str | ResolvedVideoUrl) -> dict[str, Any] | None:
    resolved = url if isinstance(url, ResolvedVideoUrl) else resolve_bilibili_url(url)
    params = resolved.api_params
    if not params:
        return None
    response = requests.get(
        BILIBILI_VIEW_API,
        params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": resolved.canonical_url},
        timeout=20,
    )
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if not isinstance(payload, dict):
        raise ValueError("Bilibili view API 返回了无效数据。")
    code = payload.get("code")
    if code in {412, -412}:
        raise AppError(ErrorKind.PLATFORM_412, str(payload.get("message") or code))
    if code != 0:
        logging.getLogger("bili_downloader").warning("Bilibili view API failed: %s", code)
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _canonical_part_url(view: dict[str, Any], page: int) -> str:
    bvid = str(view.get("bvid") or "").strip()
    if bvid:
        if BV_RE.fullmatch(bvid) is None:
            return ""
        candidate = f"https://www.bilibili.com/video/{bvid}?p={page}"
    else:
        aid = view.get("aid")
        if isinstance(aid, bool) or not str(aid or "").isdigit():
            return ""
        candidate = f"https://www.bilibili.com/video/av{aid}?p={page}"
    try:
        return canonicalize_video_url(candidate).canonical_url
    except ValueError:
        return ""


def _canonical_page_url(resolved: ResolvedVideoUrl, page: int) -> str:
    base = resolved.canonical_url.split("?", 1)[0]
    return base if page == 1 else f"{base}?p={page}"


def _entry_to_part(entry: dict[str, Any], fallback_url: str, index: int) -> VideoPart:
    return VideoPart(
        index=index,
        title=str(entry.get("title") or entry.get("fulltitle") or f"P{index}"),
        url=fallback_url,
        duration=entry.get("duration"),
        id=str(entry.get("id") or ""),
    )


def _pages_to_parts(view: dict[str, Any]) -> list[VideoPart]:
    pages = view.get("pages") or []
    if not isinstance(pages, list):
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
        raw_id = str(view.get("bvid") or view.get("aid") or "")
        parts.append(
            VideoPart(
                index=index,
                title=str(page_info.get("part") or f"P{index}"),
                url=url,
                duration=page_info.get("duration"),
                id=f"{raw_id}_p{index}" if raw_id else "",
            )
        )
    return parts


def _require_info(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("yt-dlp 未返回有效的视频信息。")
    return value


def parse_video_info(
    url: str,
    config: AppConfig,
    emitter: LogSink | None = None,
    credential_mode: CredentialMode | str = CredentialMode.SAVED,
    expected_generation: str | None | GenerationPolicy = GenerationPolicy.UNBOUND,
) -> VideoInfoResult:
    resolved = resolve_bilibili_url(url)
    logging.getLogger("bili_downloader").info("开始解析：%s", resolved.canonical_url)
    view = _fetch_bilibili_view(resolved)
    with cookiefile_lease(credential_mode, expected_generation=expected_generation) as cookiefile:
        with _youtube_dl(base_ydl_options(config, emitter, cookiefile)) as ydl:
            if view:
                parts = _pages_to_parts(view)
                selected = next((part for part in parts if part.index == resolved.requested_page), None)
                if selected is None:
                    if parts or resolved.requested_page != 1:
                        raise AppError(ErrorKind.VIDEO_UNAVAILABLE, f"P{resolved.requested_page} 不存在")
                    selected = VideoPart(1, str(view.get("title") or "P1"), resolved.canonical_url)
                    parts = [selected]
                reference = _require_info(ydl.extract_info(selected.url, download=False))
                info = reference
                current_part_index = selected.index
            else:
                info = _require_info(ydl.extract_info(resolved.canonical_url, download=False))
                entries = [entry for entry in list(info.get("entries") or []) if isinstance(entry, dict)]
                if entries:
                    parts = [
                        _entry_to_part(entry, _canonical_page_url(resolved, index), index)
                        for index, entry in enumerate(entries, start=1)
                    ]
                    if resolved.requested_page > len(parts):
                        raise AppError(ErrorKind.VIDEO_UNAVAILABLE, f"P{resolved.requested_page} 不存在")
                    current_part_index = resolved.requested_page
                    reference = entries[current_part_index - 1]
                    if not reference.get("formats"):
                        reference = _require_info(
                            ydl.extract_info(parts[current_part_index - 1].url, download=False)
                        )
                else:
                    current_part_index = resolved.requested_page
                    parts = [_entry_to_part(info, resolved.canonical_url, current_part_index)]
                    reference = info

    owner = view.get("owner") if isinstance(view, dict) else {}
    if not isinstance(owner, dict):
        owner = {}
    title = str((view or {}).get("title") or info.get("title") or reference.get("title") or "未命名视频")
    uploader = str(
        owner.get("name") or info.get("uploader") or info.get("uploader_id") or reference.get("uploader") or "-"
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
    return VideoInfoResult(
        title,
        uploader,
        duration,
        thumbnail,
        parts,
        formats,
        raw_id,
        current_part_index,
        resolved.canonical_url,
    )


def _is_allowed_thumbnail_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return any(
            normalized == domain or normalized.endswith(f".{domain}")
            for domain in THUMBNAIL_HOST_DOMAINS
        )
    return False


def _upgrade_trusted_thumbnail_url(url: str) -> str:
    """Upgrade an official CDN URL without ever requesting it over plain HTTP."""

    if not isinstance(url, str) or not url or any(ord(character) < 0x20 for character in url):
        return url
    parsed = urlparse(url)
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        return url
    if parsed.username is not None or parsed.password is not None:
        return url
    try:
        port = parsed.port
    except ValueError:
        return url
    if port is not None or not _is_allowed_thumbnail_host(parsed.hostname):
        return url
    return parsed._replace(scheme="https").geturl()


def _validate_thumbnail_url(url: str) -> None:
    if not isinstance(url, str) or not url or any(ord(character) < 0x20 for character in url):
        raise ValueError("封面地址格式无效。")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("封面地址必须使用 HTTPS。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("封面地址不能包含用户信息。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("封面地址端口无效。") from exc
    if port not in {None, 443}:
        raise ValueError("封面地址端口不受支持。")
    host = parsed.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("封面地址不能使用 IP 地址。")
    if not _is_allowed_thumbnail_host(parsed.hostname):
        raise ValueError("封面地址不是允许的 Bilibili/CDN 主机。")


def fetch_thumbnail(url: str) -> bytes:
    if not url:
        return b""
    current = _upgrade_trusted_thumbnail_url(url)
    seen: set[str] = set()
    for redirect_count in range(THUMBNAIL_MAX_REDIRECTS + 1):
        _validate_thumbnail_url(current)
        if current in seen:
            raise ValueError("封面地址包含重定向循环。")
        seen.add(current)
        try:
            response = requests.get(
                current,
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=THUMBNAIL_TIMEOUT,
            )
        except requests.RequestException:
            raise ValueError("封面请求失败。") from None
        try:
            status = int(response.status_code)
            if status in B23_REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not isinstance(location, str) or not location.strip():
                    raise ValueError("封面重定向位置无效。")
                if redirect_count >= THUMBNAIL_MAX_REDIRECTS:
                    raise ValueError("封面重定向次数过多。")
                candidate = urljoin(current, location.strip())
                _validate_thumbnail_url(candidate)
                current = candidate
                continue
            try:
                response.raise_for_status()
            except requests.RequestException:
                raise ValueError("封面请求失败。") from None

            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type not in THUMBNAIL_CONTENT_TYPES:
                raise ValueError("封面响应不是受支持的图片类型。")
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    declared_length = int(raw_length)
                except (TypeError, ValueError) as exc:
                    raise ValueError("封面响应长度无效。") from exc
                if declared_length < 0 or declared_length > THUMBNAIL_MAX_BYTES:
                    raise ValueError("封面文件超过大小限制。")

            chunks: list[bytes] = []
            total = 0
            try:
                iterator = response.iter_content(chunk_size=64 * 1024)
                for chunk in iterator:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > THUMBNAIL_MAX_BYTES:
                        raise ValueError("封面文件超过大小限制。")
                    chunks.append(bytes(chunk))
            except ValueError:
                raise
            except (OSError, requests.RequestException):
                raise ValueError("封面下载中断。") from None
            return b"".join(chunks)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    raise ValueError("封面重定向次数过多。")


class DownloadController:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._phase_lock = threading.Lock()
        self._phase = "idle"

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def phase(self) -> str:
        with self._phase_lock:
            return self._phase

    @property
    def waiting_for_merge(self) -> bool:
        return self.cancelled and self.phase in {"merging", "postprocessing"}

    @property
    def waiting_for_postprocessing(self) -> bool:
        return self.cancelled and self.phase in {"merging", "converting", "postprocessing"}

    def cancel(self) -> None:
        self._cancelled.set()

    def set_phase(self, phase: str) -> None:
        with self._phase_lock:
            self._phase = phase


def _selector_height(selector: str) -> int | None:
    heights = {int(value) for value in HEIGHT_SELECTOR_RE.findall(selector)}
    if len(heights) > 1:
        raise AppError(ErrorKind.FORMAT_UNAVAILABLE, "格式选择器包含多个清晰度")
    return next(iter(heights), None)


def _codec_is_none(fmt: dict[str, Any], key: str) -> bool:
    value = fmt.get(key)
    return isinstance(value, str) and value.strip().lower() == "none"


def _codec_is_known(fmt: dict[str, Any], key: str) -> bool:
    value = fmt.get(key)
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() != "none"


def _is_bilibili_durl_muxed(fmt: dict[str, Any]) -> bool:
    return fmt.get(BILIBILI_DURL_MUXED) is True and not (
        _codec_is_none(fmt, "vcodec") or _codec_is_none(fmt, "acodec")
    )


def _format_has_video(fmt: dict[str, Any]) -> bool:
    return _codec_is_known(fmt, "vcodec") or _is_bilibili_durl_muxed(fmt)


def _format_has_audio(fmt: dict[str, Any]) -> bool:
    return _codec_is_known(fmt, "acodec") or _is_bilibili_durl_muxed(fmt)


def _is_audio_only(fmt: dict[str, Any]) -> bool:
    return _codec_is_none(fmt, "vcodec") and _codec_is_known(fmt, "acodec")


def _is_muxed(fmt: dict[str, Any]) -> bool:
    return _is_bilibili_durl_muxed(fmt) or (
        _codec_is_known(fmt, "vcodec") and _codec_is_known(fmt, "acodec")
    )


def _is_raw_bilibili_durl_format(fmt: dict[str, Any]) -> bool:
    """Recognize the narrow legacy durl shape emitted by BilibiliBaseIE.

    DASH entries explicitly carry codec keys. A legacy durl entry carries a
    quality/height/duration tuple but no codec or ext keys; yt-dlp determines
    its muxed container from the direct URL during normalization.
    """

    quality = fmt.get("quality")
    height = fmt.get("height")
    duration = fmt.get("duration")
    return (
        "vcodec" not in fmt
        and "acodec" not in fmt
        and "ext" not in fmt
        and isinstance(fmt.get("url"), str)
        and bool(fmt["url"])
        and isinstance(quality, int)
        and not isinstance(quality, bool)
        and quality > 0
        and str(fmt.get("format_id") or "") == str(quality)
        and isinstance(height, int)
        and not isinstance(height, bool)
        and height > 0
        and isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and duration > 0
        and bool(str(fmt.get("format_note") or "").strip())
        and not fmt.get("manifest_url")
    )


def _mark_bilibili_durl_formats(info: dict[str, Any]) -> None:
    if info.get("extractor_key") != BILIBILI_EXTRACTOR_KEY:
        return
    for fmt in info.get("formats") or []:
        if isinstance(fmt, dict) and _is_raw_bilibili_durl_format(fmt):
            fmt[BILIBILI_DURL_MUXED] = True


def _extract_preflight_info(ydl: YoutubeDL, url: str) -> dict[str, Any]:
    # Inspect the extractor's raw Bilibili format provenance before yt-dlp
    # normalizes absent codec fields to None. Only the exact legacy durl shape
    # is marked as muxed; other unknown-codec entries remain untrusted.
    raw = _require_info(ydl.extract_info(url, download=False, process=False))
    _mark_bilibili_durl_formats(raw)
    return _require_info(ydl.process_ie_result(raw, download=False))


def _available_heights(formats: list[dict[str, Any]]) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                fmt["height"]
                for fmt in formats
                if isinstance(fmt, dict)
                and _format_has_video(fmt)
                and isinstance(fmt.get("height"), int)
                and not isinstance(fmt.get("height"), bool)
                and fmt["height"] > 0
            },
            reverse=True,
        )
    )


def _has_playable_height(formats: list[dict[str, Any]], height: int) -> bool:
    video = [fmt for fmt in formats if _format_has_video(fmt) and fmt.get("height") == height]
    audio = [fmt for fmt in formats if _is_audio_only(fmt)]
    muxed = [fmt for fmt in video if _is_muxed(fmt)]
    separate = [fmt for fmt in video if not _format_has_audio(fmt)]
    return bool(muxed or (separate and audio))


def _has_playable_video(formats: list[dict[str, Any]]) -> bool:
    heights = _available_heights(formats)
    return any(_has_playable_height(formats, height) for height in heights)


def _audio_formats(formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audio_only = [fmt for fmt in formats if _is_audio_only(fmt)]
    muxed = [fmt for fmt in formats if _is_muxed(fmt)]
    return audio_only or muxed


def _format_size(fmt: dict[str, Any]) -> int:
    value = fmt.get("filesize") or fmt.get("filesize_approx") or 0
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _estimate_part_size(
    formats: list[dict[str, Any]],
    height: int | None,
    mode: DownloadMode = DownloadMode.AUDIO_VIDEO,
) -> int | None:
    audio_only = [fmt for fmt in formats if _is_audio_only(fmt)]
    muxed = [
        fmt for fmt in formats
        if _is_muxed(fmt) and (height is None or fmt.get("height") == height)
    ]
    if mode is DownloadMode.AUDIO_MP3:
        estimate = max((_format_size(fmt) for fmt in (audio_only or muxed)), default=0)
        return estimate or None
    separate_video = [
        fmt for fmt in formats
        if _format_has_video(fmt)
        and not _is_muxed(fmt)
        and (height is None or fmt.get("height") == height)
    ]
    muxed_size = max((_format_size(fmt) for fmt in muxed), default=0)
    separate_size = max((_format_size(fmt) for fmt in separate_video), default=0)
    if separate_size and audio_only:
        separate_size += max((_format_size(fmt) for fmt in audio_only), default=0)
    else:
        separate_size = 0
    estimate = max(muxed_size, separate_size)
    return estimate or None


def prepare_download_plan(
    parts: list[VideoPart],
    config: AppConfig,
    format_selector: str,
    *,
    emitter: LogSink | None = None,
    cookiefile: Path | None = None,
    controller: DownloadController | None = None,
    mode: DownloadMode = DownloadMode.AUDIO_VIDEO,
    ffmpeg_path: str | None = None,
) -> DownloadPlan:
    if not parts:
        raise ValueError("未选择任何分 P。")
    controller = controller or DownloadController()
    mode = DownloadMode(mode)
    requested_height = None if mode is DownloadMode.AUDIO_MP3 else _selector_height(format_selector)
    if mode is DownloadMode.AUDIO_MP3:
        strict_selector = "bestaudio/best"
    else:
        strict_selector = (
            f"bestvideo[height={requested_height}]+bestaudio/best[height={requested_height}]"
            if requested_height else format_selector or "bestvideo+bestaudio/best"
        )
    planned: list[PlannedPart] = []
    missing: list[MissingPartFormat] = []
    opts = base_ydl_options(config, emitter, cookiefile, ffmpeg_path)
    opts.update({"noplaylist": True, "skip_download": True})
    with _youtube_dl(opts) as ydl:
        for part in parts:
            if controller.cancelled:
                raise DownloadCancelled("用户已取消下载")
            info = _extract_preflight_info(ydl, part.url)
            formats = [fmt for fmt in info.get("formats") or [] if isinstance(fmt, dict)]
            heights = _available_heights(formats)
            if mode is DownloadMode.AUDIO_MP3:
                playable = bool(_audio_formats(formats))
            else:
                playable = _has_playable_video(formats) if requested_height is None else _has_playable_height(
                    formats, requested_height
                )
            if not playable:
                missing.append(MissingPartFormat(part, heights))
                continue
            planned.append(
                PlannedPart(
                    part,
                    strict_selector,
                    _estimate_part_size(formats, requested_height, mode),
                    str(info.get("title") or part.title),
                    str(info.get("id") or part.id),
                )
            )
    if missing:
        raise FormatPreflightError(requested_height, missing, mode)
    return DownloadPlan(tuple(planned), requested_height, mode)


def _prepare_output_dir(download_dir: str) -> str:
    probe_path: str | None = None
    descriptor: int | None = None
    try:
        target = ensure_dir(download_dir)
        if not os.path.isdir(target):
            raise NotADirectoryError(target)
        descriptor, probe_path = tempfile.mkstemp(prefix=".bili-write-test-", dir=target)
        os.write(descriptor, b"ok")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.unlink(probe_path)
        probe_path = None
        return target
    except (OSError, TypeError, ValueError) as exc:
        classified = classify_error_details(exc)
        kind = classified.kind
        if kind not in {ErrorKind.DISK_FULL, ErrorKind.OUTPUT_PERMISSION}:
            kind = ErrorKind.OUTPUT_PERMISSION
        raise AppError(kind, str(exc)) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if probe_path:
            try:
                os.unlink(probe_path)
            except OSError:
                pass


def _check_disk_space(target_dir: str, plan: DownloadPlan) -> None:
    estimates = [part.estimated_bytes for part in plan.parts]
    if not estimates or any(value is None for value in estimates):
        return
    required = int(sum(value or 0 for value in estimates) * 1.05) + 16 * 1024 * 1024
    try:
        free = shutil.disk_usage(target_dir).free
    except OSError as exc:
        classified = classify_error_details(exc)
        kind = classified.kind if classified.kind in {ErrorKind.DISK_FULL, ErrorKind.OUTPUT_PERMISSION} else ErrorKind.OUTPUT_PERMISSION
        raise AppError(kind, str(exc)) from exc
    if free < required:
        raise AppError(ErrorKind.DISK_FULL, f"预计至少需要 {required} 字节")


class _ProgressAggregator:
    def __init__(self, plan: DownloadPlan, hook: ProgressHook) -> None:
        self.plan = plan
        self.hook = hook
        self._last = 0.0
        self._part_progress: dict[int, float] = {}

    @staticmethod
    def _download_fraction(status: dict[str, Any]) -> float:
        downloaded = status.get("downloaded_bytes") or 0
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        if total:
            return max(0.0, min(1.0, float(downloaded) / float(total)))
        fragment = status.get("fragment_index") or 0
        fragments = status.get("fragment_count") or 0
        if fragments:
            return max(0.0, min(1.0, float(fragment) / float(fragments)))
        return 1.0 if status.get("status") == "finished" else 0.0

    def emit(self, ordinal: int, part: VideoPart, phase: str, fraction: float, status: dict[str, Any]) -> None:
        fraction = max(self._part_progress.get(ordinal, 0.0), min(1.0, fraction))
        self._part_progress[ordinal] = fraction
        overall = ((ordinal - 1) + fraction) * 100.0 / max(len(self.plan.parts), 1)
        self._last = max(self._last, overall)
        payload = dict(status)
        payload.update(
            {
                "part_index": part.index,
                "part_number": ordinal,
                "part_count": len(self.plan.parts),
                "phase": phase,
                "part_percent": round(fraction * 100, 2),
                "overall_percent": round(self._last, 2),
            }
        )
        self.hook(payload)

    def download(self, ordinal: int, part: VideoPart, status: dict[str, Any]) -> None:
        self.emit(ordinal, part, "downloading", self._download_fraction(status) * 0.8, status)

    def postprocess(self, ordinal: int, part: VideoPart, status: dict[str, Any]) -> None:
        stage = {"started": 0.85, "processing": 0.9, "finished": 0.95}.get(status.get("status"), 0.85)
        phase = "converting" if self.plan.mode is DownloadMode.AUDIO_MP3 else "merging"
        self.emit(ordinal, part, phase, stage, status)

    def terminal(self, ordinal: int, part: VideoPart, status: str, *, complete: bool) -> None:
        fraction = 1.0 if complete else self._part_progress.get(ordinal, 0.0)
        self.emit(ordinal, part, status, fraction, {"status": status})


@dataclass(frozen=True)
class _MediaMetadata:
    containers: frozenset[str]
    has_video: bool
    video_heights: tuple[int, ...]
    has_audio: bool


def _truncate_filename_component(value: str, maximum_bytes: int) -> str:
    cleaned = sanitize_windows_filename(value)
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return cleaned
    truncated = encoded[:maximum_bytes].decode("utf-8", errors="ignore").rstrip(" .")
    return sanitize_windows_filename(truncated)


def _output_spec_identity(plan: DownloadPlan, planned: PlannedPart) -> str:
    if plan.mode is DownloadMode.AUDIO_MP3:
        return "audio-mp3-192k"
    if plan.requested_height is not None:
        return f"video-mp4-{plan.requested_height}p"
    if planned.selector in {"bestvideo+bestaudio/best", "bestvideo*+bestaudio/best"}:
        return "video-mp4-best"
    selector_id = sha256(planned.selector.encode("utf-8")).hexdigest()[:10]
    return f"video-mp4-custom-{selector_id}"


def _output_stem(planned: PlannedPart, plan: DownloadPlan, collision: int = 1) -> str:
    title = _truncate_filename_component(planned.output_title or planned.part.title, OUTPUT_TITLE_MAX_BYTES)
    raw_id = planned.output_id or planned.part.id
    if not raw_id:
        raw_id = sha256(planned.part.url.encode("utf-8")).hexdigest()[:12]
    output_id = _truncate_filename_component(raw_id, OUTPUT_ID_MAX_BYTES)
    identity = _output_spec_identity(plan, planned)
    suffix = "" if collision == 1 else f"-{collision}"
    return f"P{planned.part.index:03d}-{title}-{output_id}-[{identity}]{suffix}"


def _run_media_probe(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=MEDIA_PROBE_TIMEOUT,
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _probe_media(path: Path, ffmpeg_path: str) -> _MediaMetadata | None:
    selected_ffmpeg = Path(ffmpeg_path).resolve()
    probe_name = "ffprobe.exe" if selected_ffmpeg.suffix.lower() == ".exe" else "ffprobe"
    selected_ffprobe = selected_ffmpeg.with_name(probe_name)
    if selected_ffprobe.is_file():
        completed = _run_media_probe(
            [
                str(selected_ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=format_name:stream=codec_type,height",
                "-of",
                "json",
                str(path),
            ]
        )
        if completed is not None and completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout)
                streams = payload.get("streams") or []
                if not isinstance(streams, list):
                    raise TypeError("ffprobe streams must be a list")
                heights = tuple(
                    sorted(
                        {
                            stream["height"]
                            for stream in streams
                            if isinstance(stream, dict)
                            and stream.get("codec_type") == "video"
                            and isinstance(stream.get("height"), int)
                            and not isinstance(stream.get("height"), bool)
                            and stream["height"] > 0
                        },
                        reverse=True,
                    )
                )
                format_name = str((payload.get("format") or {}).get("format_name") or "")
                return _MediaMetadata(
                    frozenset(name.strip().lower() for name in format_name.split(",") if name.strip()),
                    any(isinstance(stream, dict) and stream.get("codec_type") == "video" for stream in streams),
                    heights,
                    any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams),
                )
            except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass

    completed = _run_media_probe(
        [str(selected_ffmpeg), "-hide_banner", "-nostdin", "-i", str(path)]
    )
    if completed is None:
        return None
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    video_lines = [line for line in output.splitlines() if " Video: " in line]
    heights = tuple(
        sorted(
            {
                int(match.group(2))
                for line in video_lines
                for match in [re.search(r"\b(\d{2,5})x(\d{2,5})(?:[\s,]|$)", line)]
                if match is not None
            },
            reverse=True,
        )
    )
    container_match = re.search(r"(?m)^Input #\d+,\s*(.+?),\s+from\s", output)
    containers = frozenset(
        name.strip().lower()
        for name in (container_match.group(1).split(",") if container_match else [])
        if name.strip()
    )
    if not containers and not video_lines and " Audio: " not in output:
        return None
    return _MediaMetadata(containers, bool(video_lines), heights, " Audio: " in output)


def _matches_output_spec(path: Path, plan: DownloadPlan, ffmpeg_path: str) -> bool:
    metadata = _probe_media(path, ffmpeg_path)
    if metadata is None:
        return False
    if plan.mode is DownloadMode.AUDIO_MP3:
        return "mp3" in metadata.containers and metadata.has_audio and not metadata.has_video
    mp4_containers = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
    if not metadata.containers.intersection(mp4_containers):
        return False
    if not metadata.has_video or not metadata.has_audio:
        return False
    return plan.requested_height is None or plan.requested_height in metadata.video_heights


def _choose_output_target(
    target_dir: str,
    planned: PlannedPart,
    plan: DownloadPlan,
    ffmpeg_path: str,
    emitter: LogSink | None,
) -> tuple[str, Path, bool]:
    extension = "mp3" if plan.mode is DownloadMode.AUDIO_MP3 else "mp4"
    for collision in range(1, OUTPUT_COLLISION_LIMIT + 1):
        stem = _output_stem(planned, plan, collision)
        final_path = Path(target_dir, f"{stem}.{extension}")
        try:
            exists = final_path.exists()
            valid_existing = final_path.is_file() and _matches_output_spec(final_path, plan, ffmpeg_path)
        except OSError:
            exists = True
            valid_existing = False
        if not exists or valid_existing:
            return stem, final_path, valid_existing
        if emitter:
            emitter(
                f"P{planned.part.index} 的同名文件与当前规格不匹配；已保留原文件并选择新的输出名称。"
            )
    raise AppError(ErrorKind.OUTPUT_PERMISSION, "同一分 P 和规格的保留文件过多，无法分配安全输出名称。")


def _existing_output_paths(info: dict[str, Any] | None, captured: Iterable[str]) -> tuple[str, ...]:
    candidates = list(captured)
    if isinstance(info, dict):
        for key in ("filepath", "_filename"):
            if info.get(key):
                candidates.append(str(info[key]))
        for item in info.get("requested_downloads") or []:
            if isinstance(item, dict) and item.get("filepath"):
                candidates.append(str(item["filepath"]))
    existing: list[str] = []
    for candidate in candidates:
        try:
            path = Path(candidate).resolve()
            if path.is_file() and str(path) not in existing:
                existing.append(str(path))
        except (OSError, ValueError):
            continue
    return tuple(existing)


def download_videos(
    parts: list[VideoPart],
    config: AppConfig,
    download_dir: str,
    format_selector: str,
    progress_hook: ProgressHook,
    emitter: LogSink | None = None,
    controller: DownloadController | None = None,
    credential_mode: CredentialMode | str = CredentialMode.SAVED,
    mode: DownloadMode = DownloadMode.AUDIO_VIDEO,
    expected_generation: str | None | GenerationPolicy = GenerationPolicy.UNBOUND,
) -> DownloadBatchResult:
    mode = DownloadMode(mode)
    controller = controller or DownloadController()
    if controller.cancelled:
        raise DownloadBatchCancelled(
            DownloadBatchResult(PartDownloadResult(part, PartDownloadStatus.CANCELLED) for part in parts)
        )
    target_dir = _prepare_output_dir(download_dir)
    ffmpeg_path = require_ffmpeg()
    results: list[PartDownloadResult] = []

    try:
        with cookiefile_lease(credential_mode, expected_generation=expected_generation) as cookiefile:
            plan = prepare_download_plan(
                parts,
                config,
                format_selector,
                emitter=emitter,
                cookiefile=cookiefile,
                controller=controller,
                mode=mode,
                ffmpeg_path=ffmpeg_path,
            )
            _check_disk_space(target_dir, plan)
            progress = _ProgressAggregator(plan, progress_hook)
            abort_error: ErrorClassification | None = None

            for ordinal, planned in enumerate(plan.parts, start=1):
                part = planned.part
                if controller.cancelled:
                    break
                controller.set_phase("downloading")
                captured: list[str] = []
                postprocessing_started = False

                def download_hook(status: dict[str, Any]) -> None:
                    if controller.cancelled and not postprocessing_started:
                        raise DownloadCancelled("用户已取消下载")
                    progress.download(ordinal, part, status)

                def postprocessor_hook(status: dict[str, Any]) -> None:
                    nonlocal postprocessing_started
                    if not postprocessing_started and controller.cancelled:
                        raise DownloadCancelled("用户已取消下载")
                    postprocessing_started = True
                    controller.set_phase("converting" if mode is DownloadMode.AUDIO_MP3 else "merging")
                    progress.postprocess(ordinal, part, status)

                def final_path_hook(filename: str) -> None:
                    captured.append(filename)

                output_stem, expected_output, reused_existing = _choose_output_target(
                    target_dir,
                    planned,
                    plan,
                    ffmpeg_path,
                    emitter,
                )
                if reused_existing:
                    existing_path = str(expected_output.resolve())
                    if emitter:
                        emitter(f"P{part.index} 已验证并复用相同规格的现有媒体。")
                    results.append(
                        PartDownloadResult(part, PartDownloadStatus.COMPLETED, (existing_path,))
                    )
                    progress.terminal(ordinal, part, "completed", complete=True)
                    continue
                escaped_stem = output_stem.replace("%", "%%")
                outtmpl = os.path.join(target_dir, f"{escaped_stem}.%(ext)s")
                opts = base_ydl_options(config, emitter, cookiefile, ffmpeg_path)
                opts.update(
                    {
                        "format": planned.selector,
                        "outtmpl": {"default": outtmpl},
                        "continuedl": True,
                        "retries": 5,
                        "fragment_retries": 5,
                        "progress_hooks": [download_hook],
                        "postprocessor_hooks": [postprocessor_hook],
                        "post_hooks": [final_path_hook],
                        "paths": {"home": target_dir},
                        "noplaylist": True,
                    }
                )
                if mode is DownloadMode.AUDIO_MP3:
                    opts["postprocessors"] = [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ]
                else:
                    opts["merge_output_format"] = "mp4"
                try:
                    if emitter:
                        emitter(f"开始下载 P{part.index}：{sanitize_windows_filename(part.title)}")
                    with _youtube_dl(opts) as ydl:
                        info = _require_info(ydl.extract_info(part.url, download=True))
                    reported_files = _existing_output_paths(info, captured)
                    expected_path = expected_output.resolve()
                    if str(expected_path) not in reported_files or not expected_path.is_file():
                        raise RuntimeError("下载结束，但未捕获到当前规格的最终输出文件。")
                    if not _matches_output_spec(expected_path, plan, ffmpeg_path):
                        state = "已有" if reused_existing else "新生成"
                        raise RuntimeError(f"{state}输出无法验证为当前请求的媒体规格。")
                    files = (str(expected_path),)
                    results.append(PartDownloadResult(part, PartDownloadStatus.COMPLETED, files))
                    progress.terminal(ordinal, part, "completed", complete=True)
                except DownloadCancelled:
                    results.append(PartDownloadResult(part, PartDownloadStatus.CANCELLED))
                    progress.terminal(ordinal, part, "cancelled", complete=False)
                    controller.cancel()
                    break
                except Exception as exc:  # noqa: BLE001
                    classified = classify_error_details(exc)
                    results.append(
                        PartDownloadResult(
                            part,
                            PartDownloadStatus.FAILED,
                            error=classified,
                            detail=redact_sensitive(exc),
                        )
                    )
                    progress.terminal(ordinal, part, "failed", complete=True)
                    if classified.kind in {
                        ErrorKind.DISK_FULL,
                        ErrorKind.OUTPUT_PERMISSION,
                        ErrorKind.FFMPEG_MISSING,
                        ErrorKind.FFMPEG_BROKEN,
                    }:
                        abort_error = classified
                        break

            processed = {result.part.index for result in results}
            if controller.cancelled:
                for planned in plan.parts:
                    if planned.part.index not in processed:
                        results.append(PartDownloadResult(planned.part, PartDownloadStatus.CANCELLED))
                controller.set_phase("cancelled")
                raise DownloadBatchCancelled(DownloadBatchResult(results))
            if abort_error is not None:
                for planned in plan.parts:
                    if planned.part.index not in processed:
                        results.append(
                            PartDownloadResult(
                                planned.part,
                                PartDownloadStatus.FAILED,
                                error=abort_error,
                                detail="批次因全局错误停止，未开始该分 P。",
                            )
                        )
            controller.set_phase("finished")
            return DownloadBatchResult(results)
    except DownloadCancelled as exc:
        if isinstance(exc, DownloadBatchCancelled):
            raise
        for part in parts:
            if all(result.part.index != part.index for result in results):
                results.append(PartDownloadResult(part, PartDownloadStatus.CANCELLED))
        controller.set_phase("cancelled")
        raise DownloadBatchCancelled(DownloadBatchResult(results)) from exc
