from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

from .config import AppConfig
from .cookies import CredentialMode, cookiefile_lease
from .logger import LogEmitter, YtdlpQtLogger, redact_sensitive
from .utils import (
    AppError,
    ErrorClassification,
    ErrorKind,
    classify_error_details,
    ensure_dir,
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


@dataclass(frozen=True)
class ResolvedVideoUrl:
    canonical_url: str
    requested_page: int
    bvid: str = ""
    aid: str = ""

    @property
    def api_params(self) -> dict[str, str]:
        if self.bvid:
            return {"bvid": self.bvid}
        if self.aid:
            return {"aid": self.aid}
        return {}


class PartDownloadStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class DownloadBatchCancelled(DownloadCancelled):
    def __init__(self, result: DownloadBatchResult) -> None:
        self.result = result
        super().__init__("用户已取消下载")


@dataclass(frozen=True)
class MissingPartFormat:
    part: VideoPart
    available_heights: tuple[int, ...]


class FormatPreflightError(AppError):
    def __init__(self, height: int | None, missing: Iterable[MissingPartFormat]) -> None:
        self.height = height
        self.missing = tuple(missing)
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


@dataclass(frozen=True)
class DownloadPlan:
    parts: tuple[PlannedPart, ...]
    requested_height: int | None = None


BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
BVID_PATH_RE = re.compile(r"^/video/(BV[0-9A-Za-z]+)(?:/|$)", re.IGNORECASE)
AVID_PATH_RE = re.compile(r"^/video/(?:av)?(\d+)(?:/|$)", re.IGNORECASE)
HEIGHT_SELECTOR_RE = re.compile(r"height\s*(?:<=|=)\s*(\d+)", re.IGNORECASE)


def base_ydl_options(
    config: AppConfig,
    emitter: LogEmitter | None = None,
    cookiefile: Path | None = None,
    ffmpeg_path: str | None = None,
) -> dict[str, Any]:
    _ = config
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "logger": YtdlpQtLogger(emitter),
        "windowsfilenames": True,
        "noprogress": True,
        "socket_timeout": 20,
    }
    if cookiefile is not None:
        opts["cookiefile"] = str(cookiefile)
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path
    return opts


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


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _parse_and_validate_host(url: str, *, allow_short: bool) -> tuple[Any, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Bilibili 链接格式无效。")
    if parsed.username or parsed.password:
        raise ValueError("Bilibili 链接不能包含用户信息。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Bilibili 链接端口无效。") from exc
    if port not in {None, 80, 443}:
        raise ValueError("Bilibili 链接使用了不受支持的端口。")
    host = parsed.hostname.lower().rstrip(".")
    allowed = _host_matches(host, "bilibili.com") or (allow_short and _host_matches(host, "b23.tv"))
    if not allowed:
        raise ValueError("短链接重定向目标不是 Bilibili 视频页面。")
    return parsed, host


def resolve_bilibili_url(url: str) -> ResolvedVideoUrl:
    value = url.strip()
    parsed, host = _parse_and_validate_host(value, allow_short=True)
    if _host_matches(host, "b23.tv"):
        response = requests.get(
            value,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        try:
            response.raise_for_status()
            value = str(response.url)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        parsed, _ = _parse_and_validate_host(value, allow_short=False)

    bvid_match = BVID_PATH_RE.search(parsed.path)
    aid_match = AVID_PATH_RE.search(parsed.path)
    if not bvid_match and not aid_match:
        raise ValueError("链接不是有效的 Bilibili 视频页面。")

    query = parse_qs(parsed.query, keep_blank_values=True)
    page_values = query.get("p", [])
    if len(page_values) > 1:
        raise ValueError("链接包含多个 p 参数，无法确定目标分 P。")
    try:
        requested_page = int(page_values[0]) if page_values else 1
    except (TypeError, ValueError) as exc:
        raise ValueError("链接中的 p 参数必须是正整数。") from exc
    if requested_page < 1:
        raise ValueError("链接中的 p 参数必须是正整数。")

    bvid = bvid_match.group(1) if bvid_match else ""
    aid = aid_match.group(1) if aid_match else ""
    identifier = bvid or f"av{aid}"
    canonical = f"https://www.bilibili.com/video/{identifier}"
    if requested_page != 1:
        canonical += "?" + urlencode({"p": requested_page})
    return ResolvedVideoUrl(canonical, requested_page, bvid=bvid, aid=aid)


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
        return f"https://www.bilibili.com/video/{bvid}?p={page}"
    aid = view.get("aid")
    if aid:
        return f"https://www.bilibili.com/video/av{aid}?p={page}"
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
    emitter: LogEmitter | None = None,
    credential_mode: CredentialMode | str = CredentialMode.SAVED,
) -> VideoInfoResult:
    resolved = resolve_bilibili_url(url)
    logging.getLogger("bili_downloader").info("开始解析：%s", resolved.canonical_url)
    view = _fetch_bilibili_view(resolved)
    with cookiefile_lease(credential_mode) as cookiefile:
        with YoutubeDL(base_ydl_options(config, emitter, cookiefile)) as ydl:
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


def fetch_thumbnail(url: str) -> bytes:
    if not url:
        return b""
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.content


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


def _available_heights(formats: list[dict[str, Any]]) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                fmt["height"]
                for fmt in formats
                if isinstance(fmt, dict)
                and fmt.get("vcodec") != "none"
                and isinstance(fmt.get("height"), int)
                and not isinstance(fmt.get("height"), bool)
                and fmt["height"] > 0
            },
            reverse=True,
        )
    )


def _has_playable_height(formats: list[dict[str, Any]], height: int) -> bool:
    video = [fmt for fmt in formats if fmt.get("vcodec") != "none" and fmt.get("height") == height]
    audio = [fmt for fmt in formats if fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"]
    muxed = [fmt for fmt in video if fmt.get("acodec") not in {None, "none"}]
    separate = [fmt for fmt in video if fmt.get("acodec") in {None, "none"}]
    return bool(muxed or (separate and audio))


def _has_playable_video(formats: list[dict[str, Any]]) -> bool:
    heights = _available_heights(formats)
    return any(_has_playable_height(formats, height) for height in heights)


def _format_size(fmt: dict[str, Any]) -> int:
    value = fmt.get("filesize") or fmt.get("filesize_approx") or 0
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _estimate_part_size(formats: list[dict[str, Any]], height: int | None) -> int | None:
    video = [
        fmt for fmt in formats
        if fmt.get("vcodec") != "none" and (height is None or fmt.get("height") == height)
    ]
    audio = [fmt for fmt in formats if fmt.get("vcodec") == "none" and fmt.get("acodec") != "none"]
    video_size = max((_format_size(fmt) for fmt in video), default=0)
    audio_size = max((_format_size(fmt) for fmt in audio), default=0)
    estimate = video_size + audio_size
    return estimate or None


def prepare_download_plan(
    parts: list[VideoPart],
    config: AppConfig,
    format_selector: str,
    *,
    emitter: LogEmitter | None = None,
    cookiefile: Path | None = None,
    controller: DownloadController | None = None,
) -> DownloadPlan:
    if not parts:
        raise ValueError("未选择任何分 P。")
    controller = controller or DownloadController()
    requested_height = _selector_height(format_selector)
    strict_selector = (
        f"bestvideo[height={requested_height}]+bestaudio/best[height={requested_height}]"
        if requested_height else format_selector or "bestvideo+bestaudio/best"
    )
    planned: list[PlannedPart] = []
    missing: list[MissingPartFormat] = []
    opts = base_ydl_options(config, emitter, cookiefile)
    opts.update({"noplaylist": True, "skip_download": True})
    with YoutubeDL(opts) as ydl:
        for part in parts:
            if controller.cancelled:
                raise DownloadCancelled("用户已取消下载")
            info = _require_info(ydl.extract_info(part.url, download=False))
            formats = [fmt for fmt in info.get("formats") or [] if isinstance(fmt, dict)]
            heights = _available_heights(formats)
            playable = _has_playable_video(formats) if requested_height is None else _has_playable_height(
                formats, requested_height
            )
            if not playable:
                missing.append(MissingPartFormat(part, heights))
                continue
            planned.append(PlannedPart(part, strict_selector, _estimate_part_size(formats, requested_height)))
    if missing:
        raise FormatPreflightError(requested_height, missing)
    return DownloadPlan(tuple(planned), requested_height)


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
        self.emit(ordinal, part, "merging", stage, status)

    def terminal(self, ordinal: int, part: VideoPart, status: str, *, complete: bool) -> None:
        fraction = 1.0 if complete else self._part_progress.get(ordinal, 0.0)
        self.emit(ordinal, part, status, fraction, {"status": status})


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
    emitter: LogEmitter | None = None,
    controller: DownloadController | None = None,
    credential_mode: CredentialMode | str = CredentialMode.SAVED,
) -> DownloadBatchResult:
    controller = controller or DownloadController()
    if controller.cancelled:
        raise DownloadBatchCancelled(
            DownloadBatchResult(PartDownloadResult(part, PartDownloadStatus.CANCELLED) for part in parts)
        )
    target_dir = _prepare_output_dir(download_dir)
    ffmpeg_path = require_ffmpeg()
    results: list[PartDownloadResult] = []

    try:
        with cookiefile_lease(credential_mode) as cookiefile:
            plan = prepare_download_plan(
                parts,
                config,
                format_selector,
                emitter=emitter,
                cookiefile=cookiefile,
                controller=controller,
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
                    controller.set_phase("merging")
                    progress.postprocess(ordinal, part, status)

                def final_path_hook(filename: str) -> None:
                    captured.append(filename)

                outtmpl = os.path.join(target_dir, f"P{part.index:03d}-%(title).180B-%(id)s.%(ext)s")
                opts = base_ydl_options(config, emitter, cookiefile, ffmpeg_path)
                opts.update(
                    {
                        "format": planned.selector,
                        "outtmpl": {"default": outtmpl},
                        "merge_output_format": "mp4",
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
                try:
                    if emitter:
                        emitter.message.emit(f"开始下载 P{part.index}：{sanitize_windows_filename(part.title)}")
                    with YoutubeDL(opts) as ydl:
                        info = _require_info(ydl.extract_info(part.url, download=True))
                    files = _existing_output_paths(info, captured)
                    if not files:
                        raise RuntimeError("下载结束，但未找到最终输出文件。")
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
