from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from app.auth_qr import QrLoginClient, QrLoginError, QrStatus, render_qr_png
from app.cookies import SessionSaveError, validate_and_commit_candidate_cookies
from app.logger import redact_sensitive

@dataclass(frozen=True)
class LoginOutcome:
    code: str
    friendly: str = ""
    detail: str = ""


class LoginWorkflow:
    def __init__(
        self,
        *,
        notify: Callable[[str, dict], None] = lambda *_: None,
        client_factory: Callable[[], QrLoginClient] = QrLoginClient,
        total_timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> None:
        self.notify = notify
        self.generation = 0
        self._generation_lock = threading.Lock()
        self._requested_generation = 0
        self._cancelled = threading.Event()
        self._refresh_requested = threading.Event()
        self._client_factory = client_factory
        self._total_timeout = total_timeout
        self._poll_interval = poll_interval
        self.terminal_outcome: LoginOutcome | None = None

    def request_cancel(self) -> None:
        self._cancelled.set()

    def request_refresh(self) -> int:
        with self._generation_lock:
            self._refresh_requested.set()
            self._requested_generation = max(self.generation, self._requested_generation) + 1
            return self._requested_generation

    def _stopping(self) -> bool:
        return self._cancelled.is_set()

    def _candidate_cancelled(self) -> bool:
        return self._cancelled.is_set() or self._refresh_requested.is_set()

    @staticmethod
    def _validation_failure(code: str, text: str) -> LoginOutcome:
        if code == "platform_412":
            return LoginOutcome(
                "platform_412",
                "Bilibili 返回 HTTP 412，当前环境受平台限制。",
                "未尝试绕过限制，原有本地登录态未更改。",
            )
        if code == "offline":
            return LoginOutcome("network_failure", "网络超时或当前离线。", text)
        if code == "invalid":
            return LoginOutcome("invalid", "扫码返回的登录凭据无效。", text)
        return LoginOutcome("protocol_error", "Bilibili 登录验证响应异常。", text)

    def run(self) -> LoginOutcome:
        outcome: LoginOutcome | None = None
        client: QrLoginClient | None = None
        challenge = None
        deadline = time.monotonic() + self._total_timeout
        try:
            while outcome is None:
                if self._stopping():
                    outcome = LoginOutcome("cancelled", "已取消扫码登录。")
                    break
                if time.monotonic() >= deadline:
                    outcome = LoginOutcome(
                        "timeout",
                        "扫码登录超时，请重试。",
                        "扫码登录超过 5 分钟未完成，原有登录态未更改。",
                    )
                    break

                if challenge is None or self._refresh_requested.is_set():
                    if client is not None:
                        client.close()
                    with self._generation_lock:
                        self._refresh_requested.clear()
                        self.generation = max(self.generation + 1, self._requested_generation)
                    client = self._client_factory()
                    self.notify("auth.qr.state", {"code": "generating", "text": "正在生成 Bilibili 官方二维码...", "generation": self.generation})
                    challenge = client.generate()
                    if self._candidate_cancelled():
                        challenge = None
                        continue
                    self.notify("auth.qr.image", {"generation": self.generation, "mime_type": "image/png", "base64": base64.b64encode(render_qr_png(challenge.qr_url)).decode("ascii")})
                    self.notify("auth.qr.state", {"code": "waiting_scan", "text": "等待扫码：请使用 Bilibili App", "generation": self.generation})

                assert client is not None and challenge is not None
                polled = client.poll(challenge)
                if self._candidate_cancelled():
                    challenge = None
                    continue

                if polled.status is QrStatus.WAITING_SCAN:
                    self.notify("auth.qr.state", {"code": "waiting_scan", "text": "等待扫码：请使用 Bilibili App", "generation": self.generation})
                elif polled.status is QrStatus.WAITING_CONFIRMATION:
                    self.notify("auth.qr.state", {"code": "waiting_confirmation", "text": "已扫码，请在手机上确认登录", "generation": self.generation})
                elif polled.status is QrStatus.EXPIRED:
                    self.notify("auth.qr.state", {"code": "expired", "text": "二维码已过期，请点击“刷新二维码”", "generation": self.generation})
                    while (
                        not self._stopping()
                        and not self._refresh_requested.is_set()
                        and time.monotonic() < deadline
                    ):
                        self._refresh_requested.wait(0.2)
                    challenge = None
                    continue
                elif polled.status is QrStatus.SUCCESS:
                    self.notify("auth.qr.state", {"code": "validating", "text": "手机已确认，正在验证登录凭据...", "generation": self.generation})
                    validation = validate_and_commit_candidate_cookies(
                        client.candidate_cookies(),
                        cancelled=self._candidate_cancelled,
                    )
                    if validation.code == "cancelled":
                        if self._stopping():
                            outcome = LoginOutcome("cancelled", "已取消扫码登录。")
                        else:
                            challenge = None
                        continue
                    if validation.code == "verified":
                        self.notify("auth.qr.state", {"code": "verified", "text": "登录成功，凭据已验证并安全保存", "generation": self.generation})
                        outcome = LoginOutcome("success")
                    else:
                        outcome = self._validation_failure(validation.code, validation.text)
                    continue

                self._cancelled.wait(self._poll_interval)
        except QrLoginError as exc:
            if exc.status is QrStatus.CANCELLED or self._stopping():
                outcome = LoginOutcome("cancelled", "已取消扫码登录。")
            elif exc.code == "platform_412":
                outcome = self._validation_failure("platform_412", str(exc))
            elif exc.status is QrStatus.NETWORK_FAILURE:
                outcome = LoginOutcome("network_failure", "网络超时或当前离线。", str(exc))
            else:
                outcome = LoginOutcome("protocol_error", "Bilibili 扫码协议响应异常。", str(exc))
        except SessionSaveError as exc:
            outcome = LoginOutcome(
                "save_failed",
                "登录凭据已验证，但本地原子保存失败。",
                redact_sensitive(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("bili_downloader").exception("扫码登录流程发生未预期异常")
            outcome = LoginOutcome("failed", "扫码登录失败。", redact_sensitive(exc))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    logging.getLogger("bili_downloader").exception("关闭扫码协议会话失败")

        if outcome is None:
            outcome = LoginOutcome("failed", "扫码登录失败。", "登录线程未产生终态。")
        self.terminal_outcome = outcome
        return outcome
