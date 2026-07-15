from __future__ import annotations

import platform
import re
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import requests

from . import __version__
from .config import AppConfig, app_data_dir
from .cookies import describe_login_status, ensure_playwright_runtime
from .utils import FFmpegProbeStatus, probe_ffmpeg


LATEST_RELEASE_API = "https://api.github.com/repos/Qrzzzz/bili-downloader/releases/latest"
RELEASE_URL_PREFIX = "https://github.com/Qrzzzz/bili-downloader/releases/"


class DiagnosticStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    INFO = "info"


@dataclass(frozen=True)
class DiagnosticItem:
    name: str
    status: DiagnosticStatus
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class DiagnosticReport:
    items: tuple[DiagnosticItem, ...]

    def to_redacted_text(self) -> str:
        from .logger import redact_sensitive

        home = str(Path.home())
        lines = [f"Bili Downloader Lite V{__version__} 环境诊断"]
        labels = {
            DiagnosticStatus.OK: "正常",
            DiagnosticStatus.WARNING: "警告",
            DiagnosticStatus.FAILED: "失败",
            DiagnosticStatus.INFO: "信息",
        }
        for item in self.items:
            body = f"[{labels[item.status]}] {item.name}: {item.summary}"
            if item.detail:
                body += f" ({item.detail})"
            if home:
                body = body.replace(home, "%USERPROFILE%").replace(home.replace("\\", "/"), "%USERPROFILE%")
            lines.append(redact_sensitive(body))
        return "\n".join(lines)


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str | None
    release_url: str | None
    update_available: bool
    message: str


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"无法识别版本号：{value}")
    parts = tuple(int(part) if part is not None else 0 for part in match.groups())
    return parts


def check_latest_release(
    current_version: str = __version__,
    *,
    get: Callable[..., Any] = requests.get,
) -> UpdateCheckResult:
    try:
        response = get(
            LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "BiliDownloaderLite"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("GitHub 返回内容不是对象")
        tag = str(payload.get("tag_name") or "").strip()
        url = str(payload.get("html_url") or "").strip()
        current = _version_tuple(current_version)
        latest = _version_tuple(tag)
        if not url.startswith(RELEASE_URL_PREFIX):
            raise ValueError("GitHub Release 地址无效")
    except (requests.RequestException, ValueError, TypeError) as exc:
        return UpdateCheckResult(
            current_version=current_version,
            latest_version=None,
            release_url=None,
            update_available=False,
            message=f"检查更新失败：{type(exc).__name__}",
        )

    latest_version = tag.removeprefix("v").removeprefix("V")
    if latest > current:
        message = f"发现新版本 V{latest_version}。"
        available = True
    else:
        message = f"当前已是最新版本 V{current_version}。"
        available = False
    return UpdateCheckResult(current_version, latest_version, url, available, message)


def _directory_item(name: str, path: Path) -> DiagnosticItem:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="bili-diagnostic-", dir=path, delete=False) as handle:
            probe = Path(handle.name)
            handle.write(b"ok")
        probe.unlink()
        return DiagnosticItem(name, DiagnosticStatus.OK, "可读写")
    except (OSError, ValueError) as exc:
        return DiagnosticItem(name, DiagnosticStatus.FAILED, "不可写", type(exc).__name__)


def _playwright_item(cancelled: Callable[[], bool]) -> DiagnosticItem:
    if cancelled():
        return DiagnosticItem("登录浏览器", DiagnosticStatus.WARNING, "检测已取消")
    errors: list[str] = []
    try:
        ensure_playwright_runtime()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            for channel, label in (("msedge", "系统 Edge"), ("chrome", "系统 Chrome"), (None, "Playwright Chromium")):
                if cancelled():
                    return DiagnosticItem("登录浏览器", DiagnosticStatus.WARNING, "检测已取消")
                try:
                    kwargs: dict[str, Any] = {"headless": True}
                    if channel:
                        kwargs["channel"] = channel
                    browser = playwright.chromium.launch(**kwargs)
                    browser.close()
                    return DiagnosticItem("登录浏览器", DiagnosticStatus.OK, f"{label} 可启动")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{label}: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Playwright: {type(exc).__name__}")
    return DiagnosticItem("登录浏览器", DiagnosticStatus.FAILED, "没有可启动的浏览器", "；".join(errors))


def collect_diagnostics(
    config: AppConfig,
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> DiagnosticReport:
    items: list[DiagnosticItem] = []
    mode = "打包程序" if getattr(sys, "frozen", False) else "源码运行"
    items.append(DiagnosticItem("程序", DiagnosticStatus.INFO, f"V{__version__}，{mode}"))
    items.append(DiagnosticItem("Windows", DiagnosticStatus.INFO, platform.platform()))
    items.append(DiagnosticItem("Python", DiagnosticStatus.INFO, platform.python_version()))

    try:
        from yt_dlp.version import __version__ as ytdlp_version

        items.append(DiagnosticItem("yt-dlp", DiagnosticStatus.OK, ytdlp_version))
    except Exception as exc:  # noqa: BLE001
        items.append(DiagnosticItem("yt-dlp", DiagnosticStatus.FAILED, "无法读取版本", type(exc).__name__))

    ffmpeg = probe_ffmpeg()
    if ffmpeg.status is FFmpegProbeStatus.AVAILABLE:
        source = "程序内置" if ffmpeg.path and "tools" in Path(ffmpeg.path).parts else "系统 PATH"
        items.append(DiagnosticItem("FFmpeg", DiagnosticStatus.OK, ffmpeg.version or "可执行", source))
    elif ffmpeg.status is FFmpegProbeStatus.BROKEN:
        items.append(DiagnosticItem("FFmpeg", DiagnosticStatus.FAILED, "存在但无法执行", ffmpeg.detail))
    else:
        items.append(DiagnosticItem("FFmpeg", DiagnosticStatus.FAILED, "未找到", ffmpeg.detail))

    if not cancelled():
        items.append(_playwright_item(cancelled))
    items.append(_directory_item("应用数据目录", app_data_dir()))
    items.append(_directory_item("下载目录", Path(config.download_dir)))

    status = describe_login_status()
    status_kind = DiagnosticStatus.WARNING if status.code in {"invalid", "offline"} else DiagnosticStatus.INFO
    items.append(DiagnosticItem("登录状态", status_kind, status.text))
    return DiagnosticReport(tuple(items))
