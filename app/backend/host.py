from __future__ import annotations

import base64
import logging
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import BinaryIO, Callable

from app import __version__
from app.config import AppConfig, config_diagnostics, load_config, save_config
from app.cookies import CredentialMode, reconcile_login_status
from app.diagnostics import check_latest_release, collect_diagnostics
from app.downloader import DownloadBatchResult, DownloadController, DownloadMode, VideoInfoResult, fetch_thumbnail
from app.logger import redact_sensitive
from app.services.download_service import DownloadRequest, run_download
from app.services.login_service import LoginWorkflow
from app.services.parse_service import parse_video
from app.services.session_service import clear_session, session_status, validate_session

from .dto import batch_dto, error_dto, progress_dto, video_dto
from .protocol import MAX_MESSAGE_BYTES, MAX_REQUEST_BYTES, VERSION, ProtocolError, decode_request, encode_message, fields, string


@dataclass
class ParsedVideo:
    id: str
    revision: int
    info: VideoInfoResult
    mode: CredentialMode
    generation: str | None


class Operation:
    def __init__(self, method: str, send: Callable[[dict, bool], None]):
        self.id = uuid.uuid4().hex
        self.method = method
        self.cancelled = threading.Event()
        self.controller = DownloadController()
        self.login: LoginWorkflow | None = None
        self.thread: threading.Thread | None = None
        self._send = send
        self._seq = 0
        self._lock = threading.Lock()
        self._last_progress = 0.0

    def emit(self, name: str, data: dict, lossy: bool = False) -> None:
        with self._lock:
            self._seq += 1
            self._send({"v": VERSION, "type": "event", "operation_id": self.id,
                        "seq": self._seq, "event": name, "data": data}, lossy)

    def progress(self, data: dict) -> None:
        now = time.monotonic()
        phase = data.get("phase")
        if phase == "downloading" and now - self._last_progress < 0.1:
            return
        self._last_progress = now
        value = progress_dto(data)
        value["cancel_requested"] = self.cancelled.is_set()
        self.emit("download.progress", value, lossy=phase == "downloading")

    def log(self, value: str) -> None:
        safe = redact_sensitive(value)[:8192]
        logging.getLogger("bili_downloader").info(safe)
        self.emit("log.message", {"text": safe}, lossy=True)

    def cancel(self) -> None:
        self.cancelled.set()
        self.controller.cancel()
        if self.login:
            self.login.request_cancel()


class Backend:
    """One authority for task snapshots, credential mutations and safe shutdown."""

    def __init__(self, send: Callable[[dict, bool], None], safe_mode: bool = False):
        self.send = send
        self.config = load_config()
        self.safe_mode = safe_mode
        self.session_id = uuid.uuid4().hex
        self.handshaken = False
        self.seen: set[str] = set()
        self.revision = 0
        self.parsed: ParsedVideo | None = None
        self.batches: dict[str, tuple[DownloadRequest, DownloadBatchResult, int, str | None]] = {}
        self.active: Operation | None = None
        self.closing = False
        self.lock = threading.RLock()

    def reply(self, id: str | None, result: dict | None = None, error: dict | None = None) -> None:
        body = {"v": VERSION, "type": "response", "id": id, "ok": error is None}
        body["error" if error else "result"] = error if error else result or {}
        self.send(body, False)

    def request(self, request: dict) -> None:
        id = request["id"]
        with self.lock:
            try:
                if id in self.seen:
                    raise ProtocolError("duplicate_request", "请求 ID 已使用。")
                if len(self.seen) >= 100000:
                    raise ProtocolError("session_limit", "请重新启动应用。")
                self.seen.add(id)
                result, work = self.prepare(request["method"], request["params"])
            except Exception as exc:
                error = ({"code": exc.code, "message": str(exc), "retryable": False, "detail": ""}
                         if isinstance(exc, ProtocolError) else error_dto(exc))
                self.reply(id, error=error)
                return
            # Once acknowledged, failures belong to the operation terminal, never
            # a second response for this request. Keep ACK before worker events.
            self.reply(id, result)
            if work:
                work()

    def invalidate(self) -> None:
        self.parsed = None
        self.revision += 1
        if self.active and self.active.method == "parse.start":
            self.active.cancel()

    def require_idle(self) -> None:
        if self.active is not None:
            raise ProtocolError("backend_busy", "请等待当前任务结束。")

    def prepare(self, method: str, params: dict) -> tuple[dict, Callable | None]:
        if method == "hello":
            fields(params, {"protocol_version", "frontend_version"}, {"protocol_version", "frontend_version"})
            if type(params["protocol_version"]) is not int or params["protocol_version"] != VERSION or params["frontend_version"] != __version__:
                raise ProtocolError("protocol_mismatch", "前后端版本不一致，请使用同一发行包。")
            self.handshaken = True
            return {"protocol_version": VERSION, "backend_version": __version__, "session_id": self.session_id,
                    "safe_mode": self.safe_mode, "settings": asdict(self.config), "status": session_status(),
                    "config_diagnostics": list(config_diagnostics()),
                    "capabilities": ["parse", "thumbnail", "audio_video", "audio_mp3", "qr", "retry", "diagnostics"],
                    "limits": {"request_bytes": MAX_REQUEST_BYTES, "message_bytes": MAX_MESSAGE_BYTES}}, None
        if not self.handshaken:
            raise ProtocolError("handshake_required", "请先完成版本握手。")
        if self.closing:
            raise ProtocolError("shutting_down", "应用正在安全退出。")
        if method == "shutdown":
            fields(params, set())
            self.close()
            return {"state": "draining"}, None
        if method == "operation.cancel":
            fields(params, {"operation_id"}, {"operation_id"})
            op = self.active
            if not op or op.id != string(params, "operation_id", limit=80):
                return {"state": "already_finished"}, None
            op.cancel()
            return {"state": "cancel_requested", "waiting_for_postprocessing": op.controller.waiting_for_postprocessing}, None
        if method == "auth.qr.refresh":
            fields(params, {"operation_id"}, {"operation_id"})
            op = self.active
            if not op or not op.login or op.id != string(params, "operation_id", limit=80):
                raise ProtocolError("stale_operation", "扫码任务已经结束。")
            generation = op.login.request_refresh()
            return {"state": "refresh_requested", "minimum_generation": generation}, None
        if method == "parse.invalidate":
            fields(params, set())
            self.invalidate()
            return {"input_revision": self.revision}, None
        if method == "settings.get":
            fields(params, set())
            return asdict(self.config), None
        if method == "settings.update":
            fields(params, {"download_dir", "theme"})
            updated = replace(self.config, **params)
            save_config(updated)
            self.config = updated
            return asdict(updated), None
        if method == "session.status":
            fields(params, set())
            return session_status(), None

        self.require_idle()
        op = Operation(method, self.send)
        work: Callable[[], dict]
        if method == "parse.start":
            fields(params, {"input", "credential_mode"}, {"input", "credential_mode"})
            value = string(params, "input")
            mode = CredentialMode(string(params, "credential_mode"))
            self.invalidate()
            revision, config = self.revision, replace(self.config)
            generation = session_status()["generation"] if mode == CredentialMode.SAVED else None

            def parse() -> dict:
                info = parse_video(value, config, mode, op.log, generation)
                parsed = ParsedVideo(uuid.uuid4().hex, revision, info, mode, generation)
                with self.lock:
                    if op.cancelled.is_set() or revision != self.revision:
                        return {"outcome": "cancelled"}
                    self.parsed = parsed
                return video_dto(info, parsed.id, revision)
            work = parse
        elif method == "media.thumbnail":
            fields(params, {"parse_id"}, {"parse_id"})
            parsed = self.get_parse(params)

            def thumbnail() -> dict:
                if not parsed.info.thumbnail_url:
                    return {"parse_id": parsed.id, "base64": ""}
                data = fetch_thumbnail(parsed.info.thumbnail_url)
                return {"parse_id": parsed.id, "base64": base64.b64encode(data).decode("ascii")}
            work = thumbnail
        elif method in {"download.start", "download.retry"}:
            if method == "download.start":
                fields(params, {"parse_id", "part_indices", "format_id", "download_mode", "credential_mode", "download_dir"},
                       {"parse_id", "part_indices", "download_mode", "credential_mode", "download_dir"})
                parsed = self.get_parse(params)
                mode = DownloadMode(string(params, "download_mode"))
                credential = CredentialMode(string(params, "credential_mode"))
                if credential != parsed.mode:
                    raise ProtocolError("stale_parse", "账号模式已变更，请重新解析。")
                indices = params["part_indices"]
                if not isinstance(indices, list) or not indices or any(type(i) is not int for i in indices) or len(set(indices)) != len(indices):
                    raise ProtocolError("invalid_params", "请选择有效的分 P。")
                parts = tuple(p for p in parsed.info.parts if p.index in indices)
                if len(parts) != len(indices):
                    raise ProtocolError("invalid_params", "分 P 不属于当前解析结果。")
                selector, label = "bestaudio/best", "仅音频（MP3，192 kbps）"
                if mode == DownloadMode.AUDIO_VIDEO:
                    format_id = string(params, "format_id", limit=12)
                    choices = {str(i): f for i, f in enumerate(parsed.info.formats)}
                    if format_id not in choices:
                        raise ProtocolError("invalid_params", "画质不属于当前解析结果。")
                    selector, label = choices[format_id].selector, choices[format_id].label
                config = replace(self.config, download_dir=string(params, "download_dir"))
                save_config(config)
                self.config = config
                request = DownloadRequest(parsed.info.source_url, parsed.info.title, parts, replace(config),
                                          config.download_dir, selector, label, credential, mode, parsed.generation)
                batch_id, previous, revision, generation = uuid.uuid4().hex, None, self.revision, parsed.generation
            else:
                fields(params, {"batch_id"}, {"batch_id"})
                batch_id = string(params, "batch_id", limit=80)
                if batch_id not in self.batches:
                    raise ProtocolError("stale_batch", "下载记录已失效。")
                request, previous, revision, generation = self.batches[batch_id]
                if revision != self.revision or not previous.failed:
                    raise ProtocolError("stale_batch", "当前记录不能重试，请重新解析。")
                parts = tuple(p.part for p in previous.failed)
            if request.credential_mode == CredentialMode.SAVED and session_status()["generation"] != generation:
                raise ProtocolError("stale_session", "登录状态已改变，请重新解析。")

            def download() -> dict:
                result = run_download(request, op.controller, op.progress, op.log, parts)
                if previous is not None:
                    result = previous.merged_with_retry(result)
                with self.lock:
                    self.batches[batch_id] = (request, result, revision, generation)
                    while len(self.batches) > 16:
                        del self.batches[next(iter(self.batches))]
                    return batch_dto(result, batch_id, self.revision == revision, request.download_dir)
            work = download
        elif method == "auth.qr.start":
            fields(params, {"consent"}, {"consent"})
            if params["consent"] is not True:
                raise ProtocolError("consent_required", "请先确认在本机安全保存登录凭据。")
            if self.safe_mode:
                raise ProtocolError("safe_mode", "上次运行异常退出。请正常关闭并重启后扫码；仍可清除登录态。")
            op.login = LoginWorkflow(notify=op.emit)

            def login() -> dict:
                assert op.login is not None
                outcome = op.login.run()
                status = session_status()
                if outcome.code == "success":
                    if op.cancelled.is_set() or op.login._candidate_cancelled():
                        outcome = replace(outcome, code="cancelled", friendly="扫码登录已取消或刷新。")
                    elif outcome.status is not None and outcome.status.code == "verified" and outcome.status.generation:
                        status = asdict(reconcile_login_status(outcome.status))
                        if status["code"] != "verified":
                            outcome = replace(outcome, code="session_changed", friendly="登录凭据已改变，请重新验证或扫码。")
                    else:
                        outcome = replace(outcome, code="failed", friendly="未收到有效的登录验证结果，请重新扫码。")
                    with self.lock:
                        self.invalidate()
                return {**asdict(outcome), "status": status}
            work = login
        elif method == "session.validate":
            fields(params, set())
            def validate() -> dict:
                result = validate_session()
                if result["code"] in {"invalid", "none", "local_pending"}:
                    with self.lock:
                        self.invalidate()
                return result
            work = validate
        elif method == "session.clear":
            fields(params, set())

            def clear() -> dict:
                result = clear_session()
                with self.lock:
                    self.invalidate()
                return result
            work = clear
        elif method == "diagnostics.run":
            fields(params, set())
            config = replace(self.config)

            def diagnostics() -> dict:
                report = collect_diagnostics(config, cancelled=op.cancelled.is_set)
                return {"items": [{**asdict(i), "detail": redact_sensitive(i.detail)} for i in report.items],
                        "text": report.to_redacted_text()}
            work = diagnostics
        elif method == "updates.check":
            fields(params, set())
            work = lambda: asdict(check_latest_release())
        else:
            raise ProtocolError("unknown_method", "不支持的操作。")

        op.thread = threading.Thread(target=self.run, args=(op, work), name=f"backend-{method}")
        self.active = op
        return {"operation_id": op.id, "state": "accepted"}, lambda: self.start(op)

    def start(self, op: Operation) -> None:
        try:
            assert op.thread is not None
            op.thread.start()
        except Exception as exc:
            # No worker exists to unwind this accepted operation. Settle it under
            # the request lock so cancel/shutdown cannot observe an unstarted active.
            self.complete(op, "operation.failed", {"error": error_dto(exc)})

    def get_parse(self, params: dict) -> ParsedVideo:
        if not self.parsed or self.parsed.id != string(params, "parse_id", limit=80):
            raise ProtocolError("stale_parse", "解析结果已失效，请重新解析。")
        if self.parsed.mode == CredentialMode.SAVED and session_status()["generation"] != self.parsed.generation:
            self.invalidate()
            raise ProtocolError("stale_session", "登录状态已改变，请重新解析。")
        return self.parsed

    def run(self, op: Operation, work: Callable[[], dict]) -> None:
        event = "operation.completed"
        try:
            result = work()
            if op.cancelled.is_set() and op.method not in {"download.start", "download.retry", "auth.qr.start"}:
                result = {"outcome": "cancelled"}
                event = "operation.cancelled"
        except Exception as exc:
            result = {"error": error_dto(exc)}
            event = "operation.cancelled" if op.cancelled.is_set() else "operation.failed"
        self.complete(op, event, result)

    def complete(self, op: Operation, event: str, result: dict) -> None:
        with self.lock:
            # Work has unwound all requests/lease/FFmpeg contexts before terminal delivery.
            self.active = None
            try:
                encode_message({"v": VERSION, "type": "event", "operation_id": op.id,
                                "seq": 1, "event": event, "data": {"method": op.method, "result": result}})
            except (ProtocolError, ValueError, TypeError):
                event = "operation.failed"
                result = {"error": {"code": "result_too_large", "message": "后端结果超过协议限制或包含无效数据。",
                                    "retryable": False, "detail": ""}}
            op.emit(event, {"method": op.method, "result": result})

    def close(self) -> None:
        with self.lock:
            self.closing = True
            if self.active:
                self.active.cancel()

    def wait(self) -> None:
        with self.lock:
            thread = self.active.thread if self.active else None
        if thread:
            thread.join()


def serve(input: BinaryIO, output: BinaryIO, safe_mode: bool = False) -> int:
    messages: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
    disconnected = threading.Event()

    def send(value: dict, lossy: bool = False) -> None:
        data = encode_message(value)
        while not disconnected.is_set():
            try:
                messages.put(data, block=not lossy, timeout=0.1 if not lossy else None)
                return
            except queue.Full:
                if lossy:
                    return

    backend = Backend(send, safe_mode)

    def writer() -> None:
        try:
            while True:
                data = messages.get()
                if data is None:
                    return
                output.write(data)
                output.flush()
        except (OSError, ValueError):
            disconnected.set()
            backend.close()

    worker = threading.Thread(target=writer, name="ipc-writer", daemon=True)
    worker.start()
    try:
        while not backend.closing and not disconnected.is_set():
            line = input.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                break
            try:
                backend.request(decode_request(line))
            except ProtocolError as exc:
                backend.reply(None, error={"code": exc.code, "message": str(exc), "retryable": False, "detail": ""})
                # Oversize/deep malformed streams cannot be safely resynchronized.
                if len(line) > MAX_REQUEST_BYTES:
                    break
    finally:
        backend.close()
        backend.wait()
        if not disconnected.is_set():
            send({"v": VERSION, "type": "event", "operation_id": backend.session_id,
                  "seq": 1, "event": "shutdown.ready", "data": {}}, False)
            while worker.is_alive():
                try:
                    messages.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue
        worker.join(timeout=5)
    return 0
