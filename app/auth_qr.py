from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import requests
import segno

from . import __version__


GENERATE_API_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_API_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
REQUEST_TIMEOUT = (2.5, 4.0)
USER_AGENT = f"BiliDownloaderLite/{__version__} (+https://github.com/Qrzzzz/bili-downloader)"

# These are exact origins currently used by Bilibili's web QR protocol.  Do not
# broaden this to arbitrary subdomains: QR and callback URLs are untrusted API
# fields and must fail closed when the platform changes unexpectedly.
QR_URL_HOSTS = frozenset({"account.bilibili.com", "passport.bilibili.com"})
SUCCESS_CALLBACK_HOSTS = frozenset(
    {
        "account.bilibili.com",
        "passport.bilibili.com",
        "passport.biligame.com",
        "www.bilibili.com",
    }
)


class QrStatus(str, Enum):
    WAITING_SCAN = "waiting_scan"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXPIRED = "expired"
    SUCCESS = "success"
    CANCELLED = "cancelled"
    NETWORK_FAILURE = "network_failure"
    PROTOCOL_ERROR = "protocol_error"


class QrLoginError(RuntimeError):
    def __init__(self, status: QrStatus, message: str, *, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class QrNetworkError(QrLoginError):
    def __init__(self, message: str, *, code: str = "network_failure") -> None:
        super().__init__(QrStatus.NETWORK_FAILURE, message, code=code)


class QrProtocolError(QrLoginError):
    def __init__(self, message: str) -> None:
        super().__init__(QrStatus.PROTOCOL_ERROR, message, code="protocol_error")


class QrCancelled(QrLoginError):
    def __init__(self) -> None:
        super().__init__(QrStatus.CANCELLED, "扫码登录已取消。", code="cancelled")


@dataclass(frozen=True)
class QrChallenge:
    qr_url: str = field(repr=False)
    qrcode_key: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class QrPollResult:
    status: QrStatus


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_https_url(
    value: object,
    *,
    allowed_hosts: frozenset[str],
    label: str,
) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise QrProtocolError(f"{label}缺失或类型异常")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise QrProtocolError(f"{label}格式异常") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise QrProtocolError(f"{label}来源不在允许列表中")
    return value


def _validate_and_discard_success_callback(value: object) -> None:
    """Verify only the trusted origin; never consume the credential-bearing query."""
    _validate_https_url(
        value,
        allowed_hosts=SUCCESS_CALLBACK_HOSTS,
        label="成功回调 URL",
    )
    # The callback query is an opaque, credential-bearing platform value.  It
    # is never navigated, persisted, logged, or used to construct cookies.
    # Authentication candidates come exclusively from the controlled Session
    # cookie jar and must still pass the independent cookie/NAV transaction.


def _validate_envelope(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QrProtocolError("扫码接口返回的顶层类型异常")
    if not _is_int(payload.get("code")):
        raise QrProtocolError("扫码接口缺少整数 code")
    if not isinstance(payload.get("message"), str):
        raise QrProtocolError("扫码接口缺少文本 message")
    if not _is_int(payload.get("ttl")):
        raise QrProtocolError("扫码接口缺少整数 ttl")
    if payload["code"] != 0:
        raise QrProtocolError("扫码接口返回非成功应用状态")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise QrProtocolError("扫码接口缺少对象 data")
    return data


def _cookie_to_mapping(cookie: Any) -> dict[str, Any]:
    rest = getattr(cookie, "_rest", {})
    same_site = rest.get("SameSite") if isinstance(rest, dict) else None
    result: dict[str, Any] = {
        "name": str(cookie.name),
        "value": str(cookie.value),
        "domain": str(cookie.domain or ""),
        "path": str(cookie.path or "/"),
        "secure": bool(cookie.secure),
        "httpOnly": bool(isinstance(rest, dict) and "HttpOnly" in rest),
    }
    if _is_int(cookie.expires) or isinstance(cookie.expires, float):
        result["expires"] = float(cookie.expires)
    if same_site in {"Strict", "Lax", "None"}:
        result["sameSite"] = same_site
    return result


def render_qr_png(qr_url: str, *, scale: int = 8, border: int = 4) -> bytes:
    """Render a sharp QR PNG locally with the standard four-module quiet zone."""
    _validate_https_url(qr_url, allowed_hosts=QR_URL_HOSTS, label="二维码 URL")
    if scale < 4 or scale > 16 or border < 4 or border > 12:
        raise ValueError("QR scale/border is outside the approved rendering range")
    output = io.BytesIO()
    qr = segno.make(qr_url, error="m", micro=False)
    qr.save(output, kind="png", scale=scale, border=border, dark="#111111", light="#ffffff")
    data = output.getvalue()
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 2 * 1024 * 1024:
        raise QrProtocolError("本地二维码 PNG 生成异常")
    return data


class QrLoginClient:
    """One cancellable Bilibili QR session; a refresh must create a new instance."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Referer": "https://www.bilibili.com/",
                "User-Agent": USER_AGENT,
            }
        )
        self._cancelled = threading.Event()
        self._challenge: QrChallenge | None = None
        self._closed = False

    def _ensure_active(self) -> None:
        if self._closed or self._cancelled.is_set():
            raise QrCancelled()

    def _get_json(self, url: str, **kwargs: Any) -> object:
        self._ensure_active()
        try:
            response = self._session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            if response.status_code == 412:
                raise QrNetworkError(
                    "Bilibili 返回 HTTP 412，属于外部平台限制。",
                    code="platform_412",
                )
            response.raise_for_status()
            payload = response.json()
        except QrLoginError:
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise QrNetworkError("扫码登录网络超时或当前离线。") from exc
        # requests.JSONDecodeError is also a RequestException, so this must
        # precede the broad request catch to classify malformed payloads as a
        # closed protocol failure rather than a transient network failure.
        except ValueError as exc:
            raise QrProtocolError("扫码接口未返回有效 JSON") from exc
        except requests.RequestException as exc:
            raise QrNetworkError("扫码登录请求失败。") from exc
        self._ensure_active()
        return payload

    def generate(self) -> QrChallenge:
        data = _validate_envelope(self._get_json(GENERATE_API_URL))
        qr_url = _validate_https_url(data.get("url"), allowed_hosts=QR_URL_HOSTS, label="二维码 URL")
        qrcode_key = data.get("qrcode_key")
        if (
            not isinstance(qrcode_key, str)
            or not qrcode_key
            or len(qrcode_key) > 512
            or any(character in qrcode_key for character in "\r\n\t")
        ):
            raise QrProtocolError("扫码接口缺少有效 qrcode_key")
        try:
            qr_query = parse_qsl(
                urlsplit(qr_url).query,
                keep_blank_values=True,
                max_num_fields=16,
            )
        except ValueError as exc:
            raise QrProtocolError("二维码 URL 查询字段异常") from exc
        embedded_keys = [value for name, value in qr_query if name == "qrcode_key"]
        if embedded_keys != [qrcode_key]:
            raise QrProtocolError("二维码 URL 与 qrcode_key 不一致")
        challenge = QrChallenge(qr_url, qrcode_key)
        self._challenge = challenge
        return challenge

    def poll(self, challenge: QrChallenge) -> QrPollResult:
        self._ensure_active()
        if challenge is not self._challenge:
            raise QrCancelled()
        data = _validate_envelope(
            self._get_json(POLL_API_URL, params={"qrcode_key": challenge.qrcode_key})
        )
        code = data.get("code")
        message = data.get("message")
        callback_url = data.get("url")
        refresh_token = data.get("refresh_token")
        timestamp = data.get("timestamp")
        if (
            not _is_int(code)
            or not isinstance(message, str)
            or not isinstance(callback_url, str)
            or not isinstance(refresh_token, str)
            or not _is_int(timestamp)
        ):
            raise QrProtocolError("扫码轮询响应缺少必需字段或字段类型异常")

        statuses = {
            86101: QrStatus.WAITING_SCAN,
            86090: QrStatus.WAITING_CONFIRMATION,
            86038: QrStatus.EXPIRED,
            0: QrStatus.SUCCESS,
        }
        status = statuses.get(code)
        if status is None:
            raise QrProtocolError("扫码轮询返回未知状态")
        if status is QrStatus.SUCCESS:
            _validate_and_discard_success_callback(callback_url)
        elif callback_url or refresh_token:
            raise QrProtocolError("非成功轮询状态携带了未预期的敏感字段")
        return QrPollResult(status)

    def candidate_cookies(self) -> list[dict[str, Any]]:
        self._ensure_active()
        return [_cookie_to_mapping(cookie) for cookie in self._session.cookies]

    def cancel(self) -> None:
        self._cancelled.set()
        self._challenge = None

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True
        self._session.close()

    def __enter__(self) -> QrLoginClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def status_is_terminal(status: QrStatus) -> bool:
    return status in {
        QrStatus.SUCCESS,
        QrStatus.CANCELLED,
        QrStatus.NETWORK_FAILURE,
        QrStatus.PROTOCOL_ERROR,
    }
