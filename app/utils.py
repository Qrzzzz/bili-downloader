from __future__ import annotations

import errno
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


WINDOWS_ILLEGAL_CHARS = r'<>:"/\|?*'
BV_RE = re.compile(r"^(BV[0-9A-Za-z]{8,})$", re.IGNORECASE)
AV_RE = re.compile(r"^(av\d+)$", re.IGNORECASE)


class ErrorKind(str, Enum):
    PLATFORM_412 = "platform_412"
    ACCESS_403 = "access_403"
    LOGIN_INVALID = "login_invalid"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    OUTPUT_PERMISSION = "output_permission"
    DISK_FULL = "disk_full"
    FORMAT_UNAVAILABLE = "format_unavailable"
    FFMPEG_MISSING = "ffmpeg_missing"
    FFMPEG_BROKEN = "ffmpeg_broken"
    FFMPEG_MERGE = "ffmpeg_merge"
    FFMPEG_MERGE_FAILED = "ffmpeg_merge"
    INVALID_URL = "invalid_url"
    VIDEO_UNAVAILABLE = "video_unavailable"
    TLS = "tls"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorClassification:
    kind: ErrorKind
    message: str
    retryable: bool = False

    @property
    def code(self) -> str:
        return self.kind.value


class AppError(RuntimeError):
    """An application failure with a stable machine-readable category."""

    def __init__(self, kind: ErrorKind, detail: str = "", message: str | None = None) -> None:
        self.kind = kind
        self.detail = detail
        self.user_message = message
        super().__init__(detail or message or kind.value)

    @property
    def code(self) -> str:
        return self.kind.value


class FFmpegProbeStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    BROKEN = "broken"


@dataclass(frozen=True)
class FFmpegProbeResult:
    status: FFmpegProbeStatus
    path: str | None = None
    version: str | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status is FFmpegProbeStatus.AVAILABLE


def resource_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def normalize_bilibili_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("请输入 Bilibili 视频链接或 BV/av 号。")

    bv = BV_RE.match(value)
    if bv:
        return f"https://www.bilibili.com/video/{bv.group(1)}"

    av = AV_RE.match(value)
    if av:
        return f"https://www.bilibili.com/video/{av.group(1)}"

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("链接格式无效。请粘贴 Bilibili 视频链接，或直接输入 BV/av 号。")

    host = parsed.netloc.lower()
    allowed = (
        host == "b23.tv"
        or host.endswith(".b23.tv")
        or host == "bilibili.com"
        or host.endswith(".bilibili.com")
    )
    if not allowed:
        raise ValueError("仅支持 bilibili.com 或 b23.tv 的视频链接。")

    return value


def sanitize_windows_filename(name: str, replacement: str = "_") -> str:
    table = str.maketrans({c: replacement for c in WINDOWS_ILLEGAL_CHARS})
    cleaned = name.translate(table)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if cleaned.upper() in reserved:
        cleaned = f"_{cleaned}"
    return cleaned or "video"


def _ffmpeg_candidates(candidates: Iterable[str | os.PathLike[str]] | None) -> list[Path]:
    if candidates is None:
        discovered: list[str | os.PathLike[str]] = [resource_root() / "tools" / "ffmpeg.exe"]
        path_candidate = shutil.which("ffmpeg")
        if path_candidate:
            discovered.append(path_candidate)
    else:
        discovered = list(candidates)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in discovered:
        try:
            path = Path(candidate).expanduser().absolute()
        except (OSError, TypeError, ValueError):
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def probe_ffmpeg(
    candidates: Iterable[str | os.PathLike[str]] | None = None,
    *,
    timeout: float = 5.0,
) -> FFmpegProbeResult:
    """Execute FFmpeg to verify capability instead of trusting path existence."""

    if timeout <= 0:
        raise ValueError("FFmpeg 探测超时必须大于 0。")

    failures: list[tuple[Path, str]] = []
    for path in _ffmpeg_candidates(candidates):
        try:
            if not path.exists():
                continue
            if not path.is_file():
                failures.append((path, "候选路径不是文件"))
                continue
        except OSError as exc:
            failures.append((path, f"无法检查候选文件（{type(exc).__name__}）"))
            continue

        try:
            completed = subprocess.run(
                [str(path), "-hide_banner", "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            failures.append((path, "执行 -version 超时"))
            continue
        except (OSError, ValueError) as exc:
            failures.append((path, f"无法执行（{type(exc).__name__}）"))
            continue

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        first_line = output.splitlines()[0].strip()[:300] if output else ""
        if completed.returncode != 0:
            failures.append((path, f"-version 退出码为 {completed.returncode}"))
            continue
        if not re.search(r"(?i)\bffmpeg\s+version\b", output):
            failures.append((path, "-version 输出无法确认 FFmpeg 身份"))
            continue

        return FFmpegProbeResult(
            status=FFmpegProbeStatus.AVAILABLE,
            path=str(path),
            version=first_line or None,
        )

    if failures:
        failed_path, reason = failures[0]
        if len(failures) > 1:
            reason = f"{reason}；另有 {len(failures) - 1} 个候选不可用"
        return FFmpegProbeResult(
            status=FFmpegProbeStatus.BROKEN,
            path=str(failed_path),
            detail=reason,
        )

    return FFmpegProbeResult(
        status=FFmpegProbeStatus.MISSING,
        detail="程序目录和 PATH 中均未找到 FFmpeg。",
    )


def find_ffmpeg() -> str | None:
    result = probe_ffmpeg()
    return result.path if result.available else None


def require_ffmpeg() -> str:
    """Return a verified FFmpeg path or raise a categorized ``AppError``."""

    result = probe_ffmpeg()
    if result.available and result.path:
        return result.path
    if result.status is FFmpegProbeStatus.BROKEN:
        raise AppError(ErrorKind.FFMPEG_BROKEN, result.detail)
    raise AppError(ErrorKind.FFMPEG_MISSING, result.detail)


def ffmpeg_status_text() -> str:
    result = probe_ffmpeg()
    if result.available:
        version = f"（{result.version}）" if result.version else ""
        return f"FFmpeg 可用：{result.path}{version}"
    if result.status is FFmpegProbeStatus.BROKEN:
        return f"检测到 FFmpeg，但无法执行：{result.path}（{result.detail}）。"
    return "未检测到 FFmpeg。可解析视频，但下载后音视频合并会失败。"


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "-"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_bytes(num: int | float | None) -> str:
    if not num:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_speed(num: int | float | None) -> str:
    if not num:
        return "-"
    return f"{format_bytes(num)}/s"


def format_eta(seconds: int | float | None) -> str:
    if seconds is None:
        return "-"
    return format_duration(seconds)


_ERROR_MESSAGES: dict[ErrorKind, tuple[str, bool]] = {
    ErrorKind.PLATFORM_412: (
        "Bilibili 返回 412 风控响应，当前环境的请求被平台拒绝。请稍后重试；程序不会尝试绕过平台风控。",
        True,
    ),
    ErrorKind.ACCESS_403: (
        "Bilibili 返回 403，媒体链接可能已过期，或当前账号、地区、视频权限不足。请重新解析并确认账号本身有权访问；程序不会绕过权限限制。",
        True,
    ),
    ErrorKind.LOGIN_INVALID: (
        "服务端确认登录态已失效或账号未登录，请重新扫码登录。网络失败本身不代表登录态失效。",
        False,
    ),
    ErrorKind.OFFLINE: ("当前无法连接网络。请检查网络、DNS 或代理后重试；本地登录态不会因此被删除。", True),
    ErrorKind.TIMEOUT: ("网络请求超时。请检查网络或代理后重试。", True),
    ErrorKind.OUTPUT_PERMISSION: ("下载目录不可写或访问被拒绝。请选择有写入权限的本地文件夹。", False),
    ErrorKind.DISK_FULL: ("磁盘空间不足，无法继续写入下载文件。请释放空间或更换下载目录。", False),
    ErrorKind.FORMAT_UNAVAILABLE: (
        "所选清晰度在至少一个分 P 中不可用。为避免静默降档，下载未继续；请重新选择清晰度。",
        False,
    ),
    ErrorKind.FFMPEG_MISSING: (
        "未找到可执行的 FFmpeg。请安装 FFmpeg，或把 ffmpeg.exe 放到程序目录的 tools 文件夹。",
        False,
    ),
    ErrorKind.FFMPEG_BROKEN: ("检测到了 FFmpeg，但程序无法正常执行它。请更换完整、可信的 FFmpeg 安装。", False),
    ErrorKind.FFMPEG_MERGE: ("FFmpeg 可以启动，但音视频合并失败。已完成的其他分 P 不会被丢弃。", False),
    ErrorKind.INVALID_URL: ("链接无效或不是 yt-dlp 支持的 Bilibili 视频链接。", False),
    ErrorKind.VIDEO_UNAVAILABLE: ("视频不存在、已删除、私密，或当前账号无权访问。", False),
    ErrorKind.TLS: ("网络证书校验失败。请检查系统时间、网络代理或证书环境。", True),
    ErrorKind.UNKNOWN: (
        "解析或下载失败。可能是网络异常、视频受限，或 yt-dlp/Bilibili 提取器需要更新。",
        False,
    ),
}


def _classification(kind: ErrorKind, message: str | None = None) -> ErrorClassification:
    default_message, retryable = _ERROR_MESSAGES[kind]
    return ErrorClassification(kind=kind, message=message or default_message, retryable=retryable)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(chain) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) >= 2 and isinstance(exc_info[1], BaseException):
            pending.append(exc_info[1])
    return chain


def _http_status(chain: list[BaseException], text: str) -> int | None:
    for item in chain:
        response = getattr(item, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
        for attribute in ("status_code", "status"):
            status = getattr(item, attribute, None)
            if isinstance(status, int):
                return status

    match = re.search(r"(?i)(?:http(?:\s+error)?|status(?:\s+code)?)\s*[:=]?\s*(403|412)\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(?i)\b(403|412)\b[^\n]{0,80}\b(forbidden|precondition failed)\b", text)
    if match:
        return int(match.group(1))
    return None


def _os_error_values(chain: list[BaseException]) -> tuple[set[int], set[int]]:
    errnos: set[int] = set()
    winerrors: set[int] = set()
    for item in chain:
        item_errno = getattr(item, "errno", None)
        if isinstance(item_errno, int):
            errnos.add(item_errno)
        item_winerror = getattr(item, "winerror", None)
        if isinstance(item_winerror, int):
            winerrors.add(item_winerror)
    return errnos, winerrors


def classify_error_details(exc: BaseException | str) -> ErrorClassification:
    if isinstance(exc, AppError):
        return _classification(exc.kind, exc.user_message)

    chain = _exception_chain(exc) if isinstance(exc, BaseException) else []
    text = "\n".join(str(item) for item in chain) if chain else str(exc)
    low = text.lower()
    class_names = {type(item).__name__.lower() for item in chain}
    errnos, winerrors = _os_error_values(chain)

    status = _http_status(chain, text)
    if status == 412:
        return _classification(ErrorKind.PLATFORM_412)
    if status == 403:
        return _classification(ErrorKind.ACCESS_403)

    disk_errnos = {errno.ENOSPC}
    if hasattr(errno, "EDQUOT"):
        disk_errnos.add(errno.EDQUOT)
    disk_tokens = ("no space left on device", "disk full", "磁盘空间不足", "磁盘已满")
    if errnos & disk_errnos or winerrors & {39, 112} or any(token in low for token in disk_tokens):
        return _classification(ErrorKind.DISK_FULL)

    timeout_by_type = any(isinstance(item, (TimeoutError, socket.timeout)) for item in chain)
    if timeout_by_type or any("timeout" in name for name in class_names) or errno.ETIMEDOUT in errnos or 10060 in winerrors:
        return _classification(ErrorKind.TIMEOUT)

    offline_errnos = {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETUNREACH,
    }
    offline_names = {
        "connectionerror",
        "connectionrefusederror",
        "connectionreseterror",
        "nameresolutionerror",
        "newconnectionerror",
        "proxyerror",
    }
    if class_names & offline_names or errnos & offline_errnos or winerrors & {10051, 10061, 11001}:
        return _classification(ErrorKind.OFFLINE)

    login_tokens = (
        "login required",
        "sign in to confirm",
        "sign in required",
        "authentication required",
        "not logged in",
        "session expired",
        "cookies are no longer valid",
        "cookie is no longer valid",
        "only available for registered users",
        "login to view this video",
        "登录态已失效",
        "登录状态已失效",
        "账号未登录",
        "请先登录",
    )
    if any(token in low for token in login_tokens):
        return _classification(ErrorKind.LOGIN_INVALID)

    format_tokens = (
        "requested format is not available",
        "format is not available",
        "no video formats",
        "no matching formats",
        "所选清晰度",
    )
    if any(token in low for token in format_tokens):
        return _classification(ErrorKind.FORMAT_UNAVAILABLE)

    if "ffmpeg" in low or "ffprobe" in low:
        missing_tokens = (
            "not found",
            "not installed",
            "no such file",
            "未检测到",
            "找不到",
        )
        broken_tokens = (
            "not a valid win32 application",
            "winerror 193",
            "exec format error",
            "cannot execute",
            "无法执行",
        )
        merge_tokens = (
            "conversion failed",
            "postprocessing",
            "post-processing",
            "merger",
            "merge failed",
            "invalid data found",
            "error opening input",
            "exited with code",
            "合并失败",
        )
        if any(token in low for token in missing_tokens):
            probe = probe_ffmpeg()
            if probe.status is FFmpegProbeStatus.BROKEN:
                return _classification(ErrorKind.FFMPEG_BROKEN)
            return _classification(ErrorKind.FFMPEG_MISSING)
        if any(token in low for token in broken_tokens):
            return _classification(ErrorKind.FFMPEG_BROKEN)
        if any(token in low for token in merge_tokens):
            return _classification(ErrorKind.FFMPEG_MERGE)
        return _classification(ErrorKind.FFMPEG_BROKEN)

    permission_errnos = {errno.EACCES, errno.EPERM}
    permission_tokens = ("permission denied", "access is denied", "拒绝访问", "目录不可写", "没有写入权限")
    if (
        any(isinstance(item, PermissionError) for item in chain)
        or errnos & permission_errnos
        or 5 in winerrors
        or any(token in low for token in permission_tokens)
    ):
        return _classification(ErrorKind.OUTPUT_PERMISSION)

    if any(token in low for token in ("unsupported url", "invalid url", "not a valid url")):
        return _classification(ErrorKind.INVALID_URL)

    if any(token in low for token in ("404", "not found", "unavailable", "private", "deleted", "不存在")):
        return _classification(ErrorKind.VIDEO_UNAVAILABLE)

    if any(token in low for token in ("certificate", "ssl", "tls")):
        return _classification(ErrorKind.TLS)

    if any(token in low for token in ("name resolution", "failed to establish a new connection", "network is unreachable")):
        return _classification(ErrorKind.OFFLINE)
    if any(token in low for token in ("timed out", "read timeout", "connect timeout")):
        return _classification(ErrorKind.TIMEOUT)

    return _classification(ErrorKind.UNKNOWN)


def classify_error(exc: BaseException | str) -> str:
    """Return the existing user-facing string API for a classified failure."""

    return classify_error_details(exc).message


def ensure_dir(path: str) -> str:
    normalized = os.path.abspath(os.path.expanduser(path))
    os.makedirs(normalized, exist_ok=True)
    return normalized
