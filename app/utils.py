from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse


WINDOWS_ILLEGAL_CHARS = r'<>:"/\|?*'
BV_RE = re.compile(r"^(BV[0-9A-Za-z]{8,})$", re.IGNORECASE)
AV_RE = re.compile(r"^(av\d+)$", re.IGNORECASE)


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


def find_ffmpeg() -> str | None:
    bundled = resource_root() / "tools" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("ffmpeg")
    return found


def ffmpeg_status_text() -> str:
    path = find_ffmpeg()
    if path:
        return f"FFmpeg 可用：{path}"
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


def classify_error(exc: BaseException | str) -> str:
    msg = str(exc)
    low = msg.lower()

    if "ffmpeg" in low or "ffprobe" in low:
        return "FFmpeg 缺失或不可用。请安装 FFmpeg，或把 ffmpeg.exe 放到程序目录的 tools 文件夹。"

    if any(token in low for token in ("cookie", "cookies", "login", "sign in", "authentication", "unauthorized")):
        return (
            "登录态可能失效或账号未登录。请重新扫码登录。"
            "本程序不会要求输入账号密码，也不会绕过账号权限。"
        )

    if any(token in low for token in ("403", "forbidden", "permission", "vip", "premium", "member", "地区", "区域")):
        return (
            "当前账号、地区或视频权限不足，无法访问对应清晰度或视频内容。"
            "请确认账号本身有权限；程序不会绕过付费、会员、地区或 DRM 限制。"
        )

    if any(token in low for token in ("requested format is not available", "format is not available", "no video formats")):
        return (
            "所选清晰度当前不可用。请降低清晰度，检查账号权限/登录状态，或更新 yt-dlp 后重试。"
        )

    if any(token in low for token in ("unsupported url", "invalid url", "not a valid url")):
        return "链接无效或不是 yt-dlp 支持的 Bilibili 视频链接。"

    if any(token in low for token in ("404", "not found", "unavailable", "private", "deleted", "不存在")):
        return "视频不存在、已删除、私密，或当前网络不可访问。"

    if "certificate" in low or "ssl" in low:
        return "网络证书校验失败。请检查系统时间、网络代理或证书环境。"

    return (
        "解析或下载失败。可能是网络异常、视频受限、登录态失效，或 yt-dlp/Bilibili 提取器需要更新。"
    )


def ensure_dir(path: str) -> str:
    normalized = os.path.abspath(os.path.expanduser(path))
    os.makedirs(normalized, exist_ok=True)
    return normalized
