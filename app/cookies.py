from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from yt_dlp import YoutubeDL

from .config import AppConfig, app_data_dir, session_dir
from .logger import redact_sensitive
from .utils import resource_root


LOGIN_COOKIE_NAMES = {"SESSDATA", "DedeUserID"}
BILIBILI_COOKIE_DOMAINS = ("bilibili.com", "biliapi.net")
SESSION_DIR_NAME = "session"
PUBLIC_PARSE_TEST_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


class SessionSaveError(RuntimeError):
    """Raised when扫码登录 succeeded but local session persistence failed."""


@dataclass(frozen=True)
class LoginStatus:
    code: str
    text: str


def storage_state_path() -> Path:
    return session_dir() / "storage_state.json"


def storage_state_tmp_path() -> Path:
    return session_dir() / "storage_state.tmp.json"


def cookies_txt_path() -> Path:
    return session_dir() / "cookies.txt"


def cookies_tmp_path() -> Path:
    return session_dir() / "cookies.tmp.txt"


def legacy_browser_profile_dir() -> Path:
    return session_dir() / "playwright-profile"


def login_cache_dir() -> Path:
    return session_dir() / "login-cache"


def session_lock_path() -> Path:
    return session_dir() / "session.lock"


def has_saved_session() -> bool:
    try:
        return storage_state_path().exists() or cookies_txt_path().exists()
    except OSError:
        logging.getLogger("bili_downloader").exception("检查登录态文件失败")
        return False


def cookie_options(config: AppConfig | None = None) -> dict[str, Any]:
    """Return yt-dlp cookie options. Invalid sessions fail closed to anonymous mode."""
    try:
        path = ensure_cookiefile_from_saved_state(network_check=False)
    except Exception:
        logging.getLogger("bili_downloader").exception("登录态加载失败，已回退到未登录模式")
        return {}
    if path and is_login_cookie_file_valid(path):
        return {"cookiefile": str(path)}
    return {}


def describe_login_status() -> LoginStatus:
    """Lightweight status only; never starts Playwright or performs network I/O."""
    try:
        if storage_state_path().exists() or cookies_txt_path().exists():
            return LoginStatus("checking", "检测登录状态中")
        return LoginStatus("none", "未登录")
    except OSError:
        logging.getLogger("bili_downloader").exception("读取登录态状态失败")
        return LoginStatus("expired", "登录状态异常，请重新扫码登录")


def ensure_playwright_runtime() -> None:
    bundled = resource_root() / "ms-playwright"
    if bundled.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))


def normalize_cookie_domain(domain: str) -> str:
    if domain.startswith("#HttpOnly_"):
        domain = domain[len("#HttpOnly_") :]
    return domain.lstrip(".").lower()


def is_bilibili_cookie_domain(domain: str) -> bool:
    normalized = normalize_cookie_domain(domain)
    return any(normalized == item or normalized.endswith(f".{item}") for item in BILIBILI_COOKIE_DOMAINS)


def filter_bilibili_cookies(cookies: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if is_bilibili_cookie_domain(domain):
            selected.append(cookie)
    return selected


def cookies_indicate_logged_in(cookies: Iterable[dict[str, Any]]) -> bool:
    now = time.time()
    found: set[str] = set()
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        if name not in LOGIN_COOKIE_NAMES:
            continue
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires not in (-1, 0) and expires < now:
            continue
        if cookie.get("value"):
            found.add(name)
    return LOGIN_COOKIE_NAMES.issubset(found)


def _load_storage_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SessionSaveError(f"登录态 JSON 无法读取：{exc}") from exc
    if not isinstance(data, dict):
        raise SessionSaveError("登录态 JSON 格式错误：根对象不是 JSON object")
    cookies = data.get("cookies")
    if not isinstance(cookies, list):
        raise SessionSaveError("登录态 JSON 格式错误：缺少 cookies 数组")
    return data


def validate_storage_state(path: Path) -> list[dict[str, Any]]:
    state = _load_storage_state(path)
    cookies = filter_bilibili_cookies(state.get("cookies") or [])
    if not cookies:
        raise SessionSaveError("登录态中没有 bilibili.com 或 biliapi.net 相关 Cookie")
    if not cookies_indicate_logged_in(cookies):
        raise SessionSaveError("登录态中缺少有效的 Bilibili 登录 Cookie")
    return cookies


def _netscape_line_from_cookie(cookie: dict[str, Any]) -> str:
    domain = str(cookie.get("domain") or "").strip()
    path = str(cookie.get("path") or "/").strip() or "/"
    name = str(cookie.get("name") or "").strip()
    value = str(cookie.get("value") or "")

    if not domain:
        raise SessionSaveError("Cookie domain 为空")
    if not is_bilibili_cookie_domain(domain):
        raise SessionSaveError("Cookie domain 不属于 Bilibili")
    if not path:
        raise SessionSaveError("Cookie path 为空")
    if not name:
        raise SessionSaveError("Cookie name 为空")
    if not value:
        raise SessionSaveError("Cookie value 为空")
    if "\t" in domain or "\t" in path or "\t" in name or "\t" in value:
        raise SessionSaveError("Cookie 字段包含非法制表符")
    if "\n" in value or "\r" in value:
        raise SessionSaveError("Cookie value 包含非法换行")

    output_domain = domain
    if bool(cookie.get("httpOnly")) and not output_domain.startswith("#HttpOnly_"):
        output_domain = f"#HttpOnly_{output_domain}"
    include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
    secure = "TRUE" if cookie.get("secure") else "FALSE"
    expires = cookie.get("expires")
    expires_int = int(expires) if isinstance(expires, (int, float)) and expires > 0 else 0
    return "\t".join([output_domain, include_subdomains, path, secure, str(expires_int), name, value])


def export_cookies_to_netscape(cookies: Iterable[dict[str, Any]], target: Path) -> Path:
    selected = filter_bilibili_cookies(cookies)
    if not cookies_indicate_logged_in(selected):
        raise SessionSaveError("未检测到有效的 Bilibili 登录态 Cookie")

    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by BiliDownloader from Bilibili official扫码登录 storage_state.",
        "# Do not share this file. It contains local login state.",
    ]
    for cookie in selected:
        lines.append(_netscape_line_from_cookie(cookie))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    if not is_login_cookie_file_valid(target):
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logging.getLogger("bili_downloader").exception("删除无效临时 cookies 文件失败：%s", target)
        raise SessionSaveError("生成的 cookies.txt 未通过格式校验")
    return target


def export_storage_state_to_cookies(storage_path: Path, target: Path) -> Path:
    cookies = validate_storage_state(storage_path)
    return export_cookies_to_netscape(cookies, target)


def verify_cookiefile_with_ytdlp(cookiefile: Path) -> None:
    """Run a light yt-dlp parse to catch unreadable cookie files before replacing session."""
    try:
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": str(cookiefile),
                "skip_download": True,
                "noplaylist": True,
            }
        ) as ydl:
            ydl.extract_info(PUBLIC_PARSE_TEST_URL, download=False)
    except Exception as exc:  # noqa: BLE001
        raise SessionSaveError(f"yt-dlp 无法使用该登录态执行轻量解析：{exc}") from exc


def save_context_storage_state_atomic(context) -> Path:
    """Atomically persist Playwright storage_state and matching yt-dlp cookies."""
    tmp_state = storage_state_tmp_path()
    final_state = storage_state_path()
    tmp_cookie = cookies_tmp_path()
    final_cookie = cookies_txt_path()
    for path in (tmp_state, tmp_cookie):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger("bili_downloader").exception("清理旧临时登录态文件失败：%s", path)

    try:
        tmp_state.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(tmp_state))
        validate_storage_state(tmp_state)
        export_storage_state_to_cookies(tmp_state, tmp_cookie)
        verify_cookiefile_with_ytdlp(tmp_cookie)
        os.replace(tmp_state, final_state)
        os.replace(tmp_cookie, final_cookie)
        return final_state
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("bili_downloader").exception("保存扫码登录态失败")
        for path in (tmp_state, tmp_cookie):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("bili_downloader").exception("清理失败的临时登录态文件失败：%s", path)
        raise SessionSaveError(redact_sensitive(exc)) from exc


def save_playwright_cookies_as_netscape(cookies: Iterable[dict[str, Any]]) -> Path:
    """Compatibility wrapper used by tests; new login flow saves storage_state first."""
    tmp_cookie = cookies_tmp_path()
    final_cookie = cookies_txt_path()
    try:
        export_cookies_to_netscape(cookies, tmp_cookie)
        verify_cookiefile_with_ytdlp(tmp_cookie)
        os.replace(tmp_cookie, final_cookie)
        return final_cookie
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("bili_downloader").exception("保存扫码 Cookie 失败")
        try:
            tmp_cookie.unlink(missing_ok=True)
        except OSError:
            logging.getLogger("bili_downloader").exception("清理临时 Cookie 文件失败")
        raise SessionSaveError(redact_sensitive(exc)) from exc


def is_login_cookie_file_valid(path: Path | None = None) -> bool:
    target = path or cookies_txt_path()
    if not target.exists():
        return False

    now = int(time.time())
    found: set[str] = set()
    try:
        for line in target.read_text(encoding="utf-8", errors="strict").splitlines():
            if not line:
                continue
            if line.startswith("#") and not line.startswith("#HttpOnly_"):
                continue
            normalized = line
            if normalized.startswith("#HttpOnly_"):
                normalized = normalized[len("#HttpOnly_") :]
            fields = normalized.split("\t")
            if len(fields) != 7:
                return False
            domain, include_subdomains, cookie_path, secure, expiry_text, name, value = fields
            if not domain or not cookie_path or not name or not value:
                return False
            if not is_bilibili_cookie_domain(domain):
                continue
            if include_subdomains not in {"TRUE", "FALSE"} or secure not in {"TRUE", "FALSE"}:
                return False
            if not expiry_text.isdigit():
                return False
            expiry = int(expiry_text)
            if name in LOGIN_COOKIE_NAMES and (expiry == 0 or expiry > now):
                found.add(name)
    except Exception:  # noqa: BLE001
        logging.getLogger("bili_downloader").exception("cookies.txt 格式校验失败：%s", target)
        return False
    return LOGIN_COOKIE_NAMES.issubset(found)


def quarantine_session(reason: str) -> Path | None:
    src = session_dir()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_dest = app_data_dir() / f"session_corrupted_{timestamp}"
    dest = base_dest
    suffix = 1
    while dest.exists():
        dest = app_data_dir() / f"{base_dest.name}_{suffix}"
        suffix += 1
    try:
        if src.exists() and any(src.iterdir()):
            shutil.move(str(src), str(dest))
            session_dir().mkdir(parents=True, exist_ok=True)
            logging.getLogger("bili_downloader").warning("登录态异常，已备份到 %s。原因：%s", dest, reason)
            return dest
    except Exception:  # noqa: BLE001
        logging.getLogger("bili_downloader").exception("备份损坏登录态失败：%s", reason)
    return None


def ensure_cookiefile_from_saved_state(network_check: bool = False) -> Path | None:
    final_cookie = cookies_txt_path()
    final_state = storage_state_path()

    if final_cookie.exists() and is_login_cookie_file_valid(final_cookie):
        return final_cookie

    if final_state.exists():
        tmp_cookie = cookies_tmp_path()
        try:
            export_storage_state_to_cookies(final_state, tmp_cookie)
            if network_check:
                verify_cookiefile_with_ytdlp(tmp_cookie)
            os.replace(tmp_cookie, final_cookie)
            return final_cookie
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("登录态转换失败")
            try:
                tmp_cookie.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("bili_downloader").exception("删除失败的临时 cookies 文件失败")
            quarantine_session(f"登录态转换失败：{redact_sensitive(exc)}")
            return None

    if final_cookie.exists():
        quarantine_session("cookies.txt 非法且没有可恢复的 storage_state.json")
    return None


def validate_saved_session() -> LoginStatus:
    try:
        if not has_saved_session():
            return LoginStatus("none", "未登录")
        cookiefile = ensure_cookiefile_from_saved_state(network_check=False)
        if cookiefile and is_login_cookie_file_valid(cookiefile):
            return LoginStatus("logged_in", "已登录")
        return LoginStatus("expired", "登录已失效，请重新扫码")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("bili_downloader").exception("后台验证登录态失败")
        quarantine_session(f"后台验证登录态异常：{redact_sensitive(exc)}")
        return LoginStatus("expired", "登录状态异常，请重新扫码登录")


def clear_login_state() -> None:
    path = session_dir()
    targets = [
        storage_state_path(),
        storage_state_tmp_path(),
        cookies_txt_path(),
        cookies_tmp_path(),
        legacy_browser_profile_dir(),
        login_cache_dir(),
        session_lock_path(),
    ]
    for target in targets:
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("清理登录态文件失败：%s", target)

    try:
        if path.exists():
            for child in path.iterdir():
                if child.name.lower().startswith(("storage_state", "cookies", "login", "playwright")):
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
        path.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logging.getLogger("bili_downloader").exception("清理登录态目录失败")
