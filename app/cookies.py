from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import requests

from .config import APP_DIR_NAME, AppConfig, app_data_dir, session_dir
from .logger import redact_sensitive
from .utils import resource_root


LOGIN_COOKIE_NAMES = {"SESSDATA", "DedeUserID"}
BILIBILI_COOKIE_DOMAINS = ("bilibili.com", "biliapi.net")
SESSION_SCHEMA_VERSION = 1
SESSION_DIR_NAME = "session"
SESSION_FILE_NAME = "session.dat"
SESSION_LOCK_NAME = "session-store.lock"
NAV_API_URL = "https://api.bilibili.com/x/web-interface/nav"
DPAPI_ENTROPY = b"BiliDownloader/session/v1"


class SessionSaveError(RuntimeError):
    """The canonical login state could not be validated or committed."""


class SessionBusyError(SessionSaveError):
    """Another process currently owns the session store."""


class CredentialMode(str, Enum):
    SAVED = "saved"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class LoginStatus:
    code: str
    text: str
    generation: str | None = None


@dataclass(frozen=True)
class SessionSnapshot:
    generation: str
    saved_at: float
    cookies: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ClearLoginResult:
    ok: bool
    deleted: tuple[str, ...]
    failures: tuple[str, ...]
    remaining: tuple[str, ...]


_SESSION_MUTEX = threading.RLock()
_PROTECT_OVERRIDE: tuple[Callable[[bytes], bytes], Callable[[bytes], bytes]] | None = None


def canonical_session_path() -> Path:
    return session_dir() / SESSION_FILE_NAME


def storage_state_path() -> Path:
    """Compatibility alias for callers that only need the canonical path."""
    return canonical_session_path()


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
    # The active lock intentionally lives outside session/, which logout removes.
    return app_data_dir() / SESSION_LOCK_NAME


def _legacy_app_root() -> Path | None:
    roaming = os.environ.get("APPDATA")
    if not roaming:
        return None
    return Path(roaming) / APP_DIR_NAME


def _legacy_session_dirs() -> list[Path]:
    roots = [session_dir()]
    legacy_root = _legacy_app_root()
    if legacy_root is not None:
        candidate = legacy_root / SESSION_DIR_NAME
        if candidate not in roots:
            roots.append(candidate)
    return roots


def _legacy_credential_files() -> list[Path]:
    result: list[Path] = []
    for root in _legacy_session_dirs():
        for name in ("storage_state.json", "cookies.txt"):
            path = root / name
            if path != canonical_session_path():
                result.append(path)
    return result


def ensure_playwright_runtime() -> None:
    bundled = resource_root() / "ms-playwright"
    if bundled.exists():
        # A packaged app must use the browser shipped with that artifact rather
        # than silently succeeding through a developer/user cache.
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)


def normalize_cookie_domain(domain: str) -> str:
    if domain.startswith("#HttpOnly_"):
        domain = domain[len("#HttpOnly_") :]
    return domain.lstrip(".").lower()


def is_bilibili_cookie_domain(domain: str) -> bool:
    normalized = normalize_cookie_domain(domain)
    return any(normalized == item or normalized.endswith(f".{item}") for item in BILIBILI_COOKIE_DOMAINS)


def _canonical_cookie(cookie: dict[str, Any]) -> dict[str, Any] | None:
    domain = str(cookie.get("domain") or "").strip().lower()
    name = str(cookie.get("name") or "").strip()
    value = str(cookie.get("value") or "")
    if not domain or not name or not value or not is_bilibili_cookie_domain(domain):
        return None
    if any(token in item for item in (domain, name, value) for token in ("\t", "\r", "\n")):
        raise SessionSaveError("Cookie 字段包含非法制表符或换行")

    path = str(cookie.get("path") or "/").strip() or "/"
    if any(token in path for token in ("\t", "\r", "\n")):
        raise SessionSaveError("Cookie path 包含非法制表符或换行")

    result: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "secure": bool(cookie.get("secure")),
        "httpOnly": bool(cookie.get("httpOnly")),
    }
    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and not isinstance(expires, bool):
        result["expires"] = float(expires)
    same_site = cookie.get("sameSite")
    if same_site in {"Strict", "Lax", "None"}:
        result["sameSite"] = same_site
    return result


def filter_bilibili_cookies(cookies: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        selected = _canonical_cookie(cookie)
        if selected is None:
            continue
        key = (selected["domain"], selected["path"], selected["name"])
        deduplicated[key] = selected
    return [deduplicated[key] for key in sorted(deduplicated)]


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


def _validate_cookie_set(
    cookies: Iterable[dict[str, Any]],
    *,
    require_unexpired: bool = True,
) -> list[dict[str, Any]]:
    selected = filter_bilibili_cookies(cookies)
    if not selected:
        raise SessionSaveError("登录态中没有允许的 Bilibili Cookie")
    login_names = {str(cookie.get("name") or "") for cookie in selected if cookie.get("value")}
    if not LOGIN_COOKIE_NAMES.issubset(login_names):
        raise SessionSaveError("登录态中缺少必要的 Bilibili 登录 Cookie")
    if require_unexpired and not cookies_indicate_logged_in(selected):
        raise SessionSaveError("登录态中缺少有效的 Bilibili 登录 Cookie")
    return selected


def _set_protector_for_tests(
    protect: Callable[[bytes], bytes] | None,
    unprotect: Callable[[bytes], bytes] | None,
) -> None:
    """Test seam; production callers must leave this unset."""
    global _PROTECT_OVERRIDE
    _PROTECT_OVERRIDE = None if protect is None or unprotect is None else (protect, unprotect)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[Any]]:
    buffer = ctypes.create_string_buffer(data)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    if _PROTECT_OVERRIDE is not None:
        function = _PROTECT_OVERRIDE[0 if protect else 1]
        return function(data)
    if os.name != "nt":
        raise SessionSaveError("当前平台不支持 Windows DPAPI，拒绝明文保存登录态")

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(DPAPI_ENTROPY)
    output_blob = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "BiliDownloader session",
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    # Keep the backing buffers alive until the native call has returned.
    _ = (input_buffer, entropy_buffer)
    if not ok:
        raise SessionSaveError(f"Windows DPAPI {'加密' if protect else '解密'}登录态失败")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _protect(data: bytes) -> bytes:
    return _dpapi_transform(data, protect=True)


def _unprotect(data: bytes) -> bytes:
    return _dpapi_transform(data, protect=False)


@contextmanager
def _cross_process_lock(timeout: float = 5.0) -> Iterator[None]:
    lock_path = session_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            while not acquired:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise SessionBusyError("登录态正在被另一个任务使用，请稍后重试")
                    time.sleep(0.05)
        else:
            import fcntl

            while not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise SessionBusyError("登录态正在被另一个任务使用，请稍后重试")
                    time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                logging.getLogger("bili_downloader").warning("释放登录态文件锁失败")
        handle.close()


@contextmanager
def _session_transaction(timeout: float = 5.0) -> Iterator[None]:
    with _SESSION_MUTEX:
        with _cross_process_lock(timeout):
            yield


def _snapshot_payload(cookies: Iterable[dict[str, Any]], generation: str | None = None) -> dict[str, Any]:
    selected = _validate_cookie_set(cookies)
    generation = generation or uuid.uuid4().hex
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "generation": generation,
        "saved_at": time.time(),
        "cookies": selected,
    }


def _encode_envelope(payload: dict[str, Any]) -> bytes:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    protected = _protect(plaintext)
    envelope = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "generation": payload["generation"],
        "protected": base64.b64encode(protected).decode("ascii"),
    }
    return (json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("ascii")


def _decode_envelope(data: bytes) -> SessionSnapshot:
    try:
        envelope = json.loads(data.decode("ascii"))
        if not isinstance(envelope, dict) or envelope.get("schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported envelope schema")
        protected = base64.b64decode(envelope["protected"], validate=True)
        payload = json.loads(_unprotect(protected).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported payload schema")
        generation = str(payload.get("generation") or "")
        if not generation or generation != str(envelope.get("generation") or ""):
            raise ValueError("generation mismatch")
        saved_at = float(payload.get("saved_at") or 0)
        # Expiration is a normal session state, not structural corruption.
        cookies = _validate_cookie_set(payload.get("cookies") or [], require_unexpired=False)
    except SessionSaveError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SessionSaveError(f"canonical 登录态损坏或无法解密：{redact_sensitive(exc)}") from exc
    return SessionSnapshot(generation, saved_at, tuple(cookies))


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _commit_cookies_locked(cookies: Iterable[dict[str, Any]]) -> SessionSnapshot:
    payload = _snapshot_payload(cookies)
    encoded = _encode_envelope(payload)
    target = canonical_session_path()
    try:
        _write_atomic(target, encoded)
        snapshot = _decode_envelope(target.read_bytes())
    except Exception as exc:  # noqa: BLE001
        raise SessionSaveError(f"原子保存登录态失败：{redact_sensitive(exc)}") from exc
    return snapshot


def _load_snapshot_locked() -> SessionSnapshot | None:
    path = canonical_session_path()
    if not path.exists():
        return None
    return _decode_envelope(path.read_bytes())


def load_session_snapshot() -> SessionSnapshot | None:
    with _session_transaction():
        return _load_snapshot_locked()


def save_context_storage_state_atomic(context: Any) -> Path:
    """Persist only canonical Bilibili cookies, protected for the current Windows user."""
    try:
        state = context.storage_state()
        if not isinstance(state, dict):
            raise SessionSaveError("Playwright 未返回有效的 storage_state")
        cookies = state.get("cookies")
        if not isinstance(cookies, list):
            raise SessionSaveError("Playwright storage_state 缺少 cookies 数组")
        with _session_transaction():
            _commit_cookies_locked(cookies)
        return canonical_session_path()
    except SessionSaveError:
        raise
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("bili_downloader").error("保存扫码登录态失败：%s", redact_sensitive(exc))
        raise SessionSaveError(redact_sensitive(exc)) from exc


def save_playwright_cookies_as_netscape(cookies: Iterable[dict[str, Any]]) -> Path:
    """Compatibility wrapper: commit cookies to the protected canonical store."""
    with _session_transaction():
        _commit_cookies_locked(cookies)
    return canonical_session_path()


def _netscape_line_from_cookie(cookie: dict[str, Any]) -> str:
    domain = str(cookie["domain"])
    output_domain = domain
    if bool(cookie.get("httpOnly")) and not output_domain.startswith("#HttpOnly_"):
        output_domain = f"#HttpOnly_{output_domain}"
    include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
    secure = "TRUE" if cookie.get("secure") else "FALSE"
    expires = cookie.get("expires")
    expires_int = int(expires) if isinstance(expires, (int, float)) and expires > 0 else 0
    return "\t".join(
        [output_domain, include_subdomains, str(cookie.get("path") or "/"), secure, str(expires_int), cookie["name"], cookie["value"]]
    )


def export_cookies_to_netscape(cookies: Iterable[dict[str, Any]], target: Path) -> Path:
    selected = _validate_cookie_set(cookies)
    lines = [
        "# Netscape HTTP Cookie File",
        "# Temporary BiliDownloader credential lease. Do not share.",
        *(_netscape_line_from_cookie(cookie) for cookie in selected),
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def _read_netscape_cookies(path: Path) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not raw_line or (raw_line.startswith("#") and not raw_line.startswith("#HttpOnly_")):
            continue
        http_only = raw_line.startswith("#HttpOnly_")
        line = raw_line[len("#HttpOnly_") :] if http_only else raw_line
        fields = line.split("\t")
        if len(fields) != 7:
            raise SessionSaveError("legacy cookies.txt 格式无效")
        domain, _include_subdomains, cookie_path, secure, expiry_text, name, value = fields
        if not expiry_text.isdigit():
            raise SessionSaveError("legacy cookies.txt 过期时间无效")
        cookies.append(
            {
                "domain": domain,
                "path": cookie_path,
                "secure": secure == "TRUE",
                "httpOnly": http_only,
                "expires": int(expiry_text),
                "name": name,
                "value": value,
            }
        )
    return cookies


def _read_legacy_cookies(path: Path) -> list[dict[str, Any]]:
    if path.name == "cookies.txt":
        return _read_netscape_cookies(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SessionSaveError(f"legacy storage_state 无法读取：{redact_sensitive(exc)}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise SessionSaveError("legacy storage_state 格式无效")
    return state["cookies"]


def migrate_legacy_session() -> bool:
    """Commit legacy state first; only then remove legacy plaintext files."""
    with _session_transaction():
        if canonical_session_path().exists():
            return True
        source = next((path for path in _legacy_credential_files() if path.exists()), None)
        if source is None:
            return False
        cookies = _read_legacy_cookies(source)
        _commit_cookies_locked(cookies)
        # Successful decrypt verification above is the migration commit point.
        failures: list[str] = []
        for path in _legacy_credential_files():
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                failures.append(f"{path}: {redact_sensitive(exc)}")
        if failures:
            logging.getLogger("bili_downloader").warning("legacy 登录态迁移成功，但旧明文清理失败：%s", "; ".join(failures))
        return True


def has_saved_session() -> bool:
    try:
        if canonical_session_path().exists():
            return True
        return any(path.exists() for path in _legacy_credential_files())
    except OSError:
        logging.getLogger("bili_downloader").warning("检查登录态文件失败")
        return False


def describe_login_status() -> LoginStatus:
    if not has_saved_session():
        return LoginStatus("none", "无本地登录凭据")
    try:
        if not canonical_session_path().exists():
            return LoginStatus("local_pending", "检测到本地登录凭据，等待验证")
        snapshot = load_session_snapshot()
        if snapshot is None:
            return LoginStatus("none", "无本地登录凭据")
        return LoginStatus("local_pending", "本地登录凭据待服务端验证", snapshot.generation)
    except SessionSaveError:
        return LoginStatus("invalid", "本地登录凭据已损坏或失效")


def _remote_validate(snapshot: SessionSnapshot) -> LoginStatus:
    cookie_map = {cookie["name"]: cookie["value"] for cookie in snapshot.cookies}
    try:
        response = requests.get(
            NAV_API_URL,
            cookies=cookie_map,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
            timeout=(5, 10),
        )
        if response.status_code == 401:
            return LoginStatus("invalid", "登录凭据已失效，请重新扫码登录", snapshot.generation)
        response.raise_for_status()
        payload = response.json()
    except (requests.Timeout, requests.ConnectionError):
        return LoginStatus("offline", "当前离线或网络超时，无法验证本地登录凭据", snapshot.generation)
    except (requests.RequestException, ValueError):
        return LoginStatus("offline", "服务端暂时不可用，本地登录凭据未被更改", snapshot.generation)

    if not isinstance(payload, dict):
        return LoginStatus("offline", "服务端返回异常，本地登录凭据未被更改", snapshot.generation)
    data = payload.get("data")
    if payload.get("code") == 0 and isinstance(data, dict) and data.get("isLogin") is True:
        return LoginStatus("verified", "登录凭据已通过服务端验证", snapshot.generation)
    if payload.get("code") == -101 or (isinstance(data, dict) and data.get("isLogin") is False):
        return LoginStatus("invalid", "登录凭据已失效，请重新扫码登录", snapshot.generation)
    return LoginStatus("offline", "服务端暂时无法确认登录状态，本地凭据未被更改", snapshot.generation)


def validate_saved_session() -> LoginStatus:
    if not has_saved_session():
        return LoginStatus("none", "无本地登录凭据")
    try:
        if not canonical_session_path().exists() and not migrate_legacy_session():
            return LoginStatus("none", "无本地登录凭据")
        snapshot = load_session_snapshot()
        if snapshot is None:
            return LoginStatus("none", "无本地登录凭据")
    except SessionSaveError as exc:
        logging.getLogger("bili_downloader").warning("本地登录凭据无效：%s", redact_sensitive(exc))
        return LoginStatus("invalid", "本地登录凭据已损坏或失效")

    if not cookies_indicate_logged_in(snapshot.cookies):
        return LoginStatus("invalid", "登录凭据已过期，请重新扫码登录", snapshot.generation)
    result = _remote_validate(snapshot)
    try:
        current = load_session_snapshot()
    except SessionSaveError:
        current = None
    if current is not None and current.generation != snapshot.generation:
        return LoginStatus("local_pending", "登录凭据已更新，等待重新验证", current.generation)
    return result


@contextmanager
def cookiefile_lease(mode: CredentialMode | str = CredentialMode.SAVED) -> Iterator[Path | None]:
    normalized_mode = CredentialMode(mode)
    if normalized_mode is CredentialMode.ANONYMOUS:
        yield None
        return

    with _session_transaction():
        snapshot = _load_snapshot_locked()
        if snapshot is None:
            yield None
            return
        if not cookies_indicate_logged_in(snapshot.cookies):
            raise SessionSaveError("登录态已失效或过期，请重新扫码登录")
        leases = session_dir() / "leases"
        if leases.exists():
            # The session lock guarantees no live lease belongs to another task.
            # Anything left here is from an interrupted process and must not persist.
            shutil.rmtree(leases)
        leases.mkdir(parents=True, exist_ok=True)
        lease_dir = Path(tempfile.mkdtemp(prefix="lease-", dir=leases))
        cookiefile = lease_dir / "cookies.txt"
        try:
            export_cookies_to_netscape(snapshot.cookies, cookiefile)
            yield cookiefile
        finally:
            try:
                shutil.rmtree(lease_dir)
                if leases.exists() and not any(leases.iterdir()):
                    leases.rmdir()
            except OSError:
                logging.getLogger("bili_downloader").warning("清理临时 Cookie lease 失败：%s", lease_dir)


def cookie_options(
    config: AppConfig | None = None,
    mode: CredentialMode | str = CredentialMode.SAVED,
) -> dict[str, Any]:
    """Compatibility helper. New code must use cookiefile_lease for bounded plaintext lifetime."""
    _ = config
    if CredentialMode(mode) is CredentialMode.ANONYMOUS:
        return {}
    raise SessionSaveError("cookie_options 不再返回长期明文 Cookie；请使用 cookiefile_lease")


def is_login_cookie_file_valid(path: Path | None = None) -> bool:
    target = path or cookies_txt_path()
    try:
        return cookies_indicate_logged_in(_read_netscape_cookies(target)) if target.exists() else False
    except (OSError, SessionSaveError):
        return False


def quarantine_session(reason: str) -> Path | None:
    # Never create an unbounded plaintext credential copy. Keep the canonical file in place
    # so an offline/transient failure cannot destroy or isolate a potentially valid session.
    logging.getLogger("bili_downloader").warning("登录态未被隔离或复制：%s", redact_sensitive(reason))
    return None


def ensure_cookiefile_from_saved_state(network_check: bool = False) -> Path | None:
    _ = network_check
    raise SessionSaveError("长期 cookies.txt 已停用；请使用 cookiefile_lease")


def _credential_targets() -> tuple[list[Path], list[str]]:
    current_root = app_data_dir()
    roots = [current_root]
    legacy_root = _legacy_app_root()
    if legacy_root is not None and legacy_root not in roots:
        roots.append(legacy_root)
    targets: list[Path] = []
    failures: list[str] = []
    for root in roots:
        targets.append(root / SESSION_DIR_NAME)
        try:
            if root.exists():
                targets.extend(
                    child
                    for child in root.iterdir()
                    if child.name.lower().startswith(("session_corrupted_", "session-quarantine", "credential-backup"))
                )
        except OSError as exc:
            failures.append(f"无法枚举登录态目录 {root}: {redact_sensitive(exc)}")
    return list(dict.fromkeys(targets)), failures


def clear_login_state() -> ClearLoginResult:
    deleted: list[str] = []
    targets, failures = _credential_targets()
    try:
        with _session_transaction():
            for target in targets:
                try:
                    if not target.exists():
                        continue
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    deleted.append(str(target))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{target}: {redact_sensitive(exc)}")
    except SessionBusyError as exc:
        failures.append(str(exc))

    remaining_items: list[str] = []
    for target in targets:
        try:
            if target.exists():
                remaining_items.append(str(target))
        except OSError as exc:
            failures.append(f"无法复查登录态路径 {target}: {redact_sensitive(exc)}")
    remaining = tuple(remaining_items)
    return ClearLoginResult(not failures and not remaining, tuple(deleted), tuple(failures), remaining)
