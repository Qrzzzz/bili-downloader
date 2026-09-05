from __future__ import annotations

import threading
import time

import pytest

from tests.test_backend import backend, finish  # shared isolated backend fixture


def test_shutdown_waits_for_download_cleanup_before_terminal(backend, monkeypatch, tmp_path):
    from app.backend.host import ParsedVideo
    from app.cookies import CredentialMode
    from app.downloader import VideoInfoResult, VideoPart, DownloadBatchResult, PartDownloadResult, PartDownloadStatus
    host, messages, send = backend
    part = VideoPart(1, "test", "https://www.bilibili.com/video/BV1TEST")
    host.parsed = ParsedVideo("parse", host.revision, VideoInfoResult("test", "", 1, "", [part], []), CredentialMode.ANONYMOUS, None)
    entered, cleanup = threading.Event(), threading.Event()
    events = []
    def work(request, controller, progress, log, selected):
        entered.set()
        assert cleanup.wait(3)
        assert controller.cancelled
        events.append("resources_closed")
        return DownloadBatchResult([PartDownloadResult(part, PartDownloadStatus.CANCELLED)])
    monkeypatch.setattr("app.backend.host.run_download", work)
    accepted = send("download.start", {"parse_id": "parse", "part_indices": [1], "download_mode": "audio_mp3", "credential_mode": "anonymous", "download_dir": str(tmp_path)})
    assert entered.wait(1)
    assert send("shutdown")["result"]["state"] == "draining"
    assert send("settings.update", {"theme": "light"})["error"]["code"] == "shutting_down"
    assert not any(m.get("event", "").startswith("operation.") for m in messages)
    cleanup.set()
    finish(host)
    terminal = next(m for m in messages if m.get("event") == "operation.completed")
    assert events == ["resources_closed"]
    assert terminal["data"]["result"]["outcome"] == "cancelled"
    assert terminal["operation_id"] == accepted["result"]["operation_id"]


def test_global_failure_is_one_result_per_requested_part(tmp_path):
    from app.services import download_service as service
    from app.config import AppConfig
    from app.cookies import CredentialMode
    from app.downloader import VideoPart, DownloadMode, DownloadController
    from app.utils import AppError, ErrorKind
    parts = (VideoPart(1, "one", "url"), VideoPart(2, "two", "url"))
    request = service.DownloadRequest("url", "title", parts, AppConfig(), str(tmp_path), "bestaudio/best", "MP3", CredentialMode.ANONYMOUS, DownloadMode.AUDIO_MP3)
    with pytest.MonkeyPatch.context() as patch:
        def fail(*args, **kwargs):
            raise AppError(ErrorKind.FFMPEG_MISSING, "test fixture")
        patch.setattr(service, "download_videos", fail)
        result = service.run_download(request, DownloadController(), lambda _: None, lambda _: None)
    assert len(result.failed) == 2
    assert all(p.error.code == "ffmpeg_missing" for p in result.failed)


def test_safe_mode_blocks_login_but_keeps_local_clear_available(backend, monkeypatch):
    host, messages, send = backend
    assert send("auth.qr.start", {"consent": False})["error"]["code"] == "consent_required"
    host.safe_mode = True
    assert send("auth.qr.start", {"consent": True})["error"]["code"] == "safe_mode"
    monkeypatch.setattr("app.backend.host.clear_session", lambda: {"ok": True, "status": {"code": "none"}})
    assert send("session.clear")["ok"]
    finish(host)


def test_failed_settings_save_preserves_previous_preferences(backend, monkeypatch):
    host, messages, send = backend
    before = host.config
    def fail(_):
        raise PermissionError("read-only test directory")
    monkeypatch.setattr("app.backend.host.save_config", fail)
    assert not send("settings.update", {"theme": "light"})["ok"]
    assert host.config is before


def test_oversized_terminal_becomes_small_error_and_releases_backend(backend, monkeypatch):
    host, messages, send = backend
    from app.backend.host import Operation
    op = Operation("fixture", host.send)
    host.active = op
    host.run(op, lambda: {"text": "a" * (16 * 1024 * 1024)})
    assert host.active is None
    assert messages[-1]["event"] == "operation.failed"
    assert messages[-1]["data"]["result"]["error"]["code"] == "result_too_large"
    assert send("settings.get")["ok"]


def test_invalid_input_has_stable_error_category_without_network(backend):
    host, messages, send = backend
    send("parse.start", {"input": "invalid", "credential_mode": "anonymous"})
    finish(host)
    assert messages[-1]["data"]["result"]["error"]["code"] == "invalid_url"


def test_invalid_session_validation_invalidates_old_parse(backend, monkeypatch):
    host, messages, send = backend
    revision = host.revision
    monkeypatch.setattr("app.backend.host.validate_session", lambda: {"code": "invalid", "text": "invalid", "generation": "same"})
    send("session.validate")
    finish(host)
    assert host.revision == revision + 1
