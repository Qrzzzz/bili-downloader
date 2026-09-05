"""Real store/lease and backend contracts; isolated synthetic credentials, no network."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_backend import backend, finish


def change_session(cookies, credentials, action):
    if action == "clear":
        assert cookies.clear_login_state().ok
    elif action == "replace":
        candidate = copy.deepcopy(credentials["cookies"])
        candidate[0]["value"] += "-replacement"
        cookies._store_cookies_for_tests(candidate)
    elif action == "corrupt":
        cookies.canonical_session_path().write_bytes(b"invalid-envelope")


def test_validation_read_failure_does_not_return_old_verified(session_modules, synthetic_credentials, monkeypatch):
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    target = cookies.canonical_session_path()
    original_read = Path.read_bytes
    def remote(candidate, generation):
        def denied(path):
            if path == target:
                raise PermissionError("synthetic read denial")
            return original_read(path)
        monkeypatch.setattr(Path, "read_bytes", denied)
        return cookies.LoginStatus("verified", "ok", generation)
    monkeypatch.setattr(cookies, "_remote_validate_cookies", remote)
    assert cookies.validate_saved_session().code == "invalid"


@pytest.mark.parametrize("action", ["clear", "replace", "corrupt", "stable"])
def test_backend_validation_revokes_parse_only_when_session_changes(session_modules, synthetic_credentials,
                                                                   backend, monkeypatch, action):
    from app.backend.host import ParsedVideo
    from app.downloader import VideoInfoResult
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    generation = cookies.load_session_snapshot().generation
    host, messages, send = backend
    host.parsed = ParsedVideo("p", host.revision, VideoInfoResult("test", "", 1, "", [], []),
                              cookies.CredentialMode.SAVED, generation)
    def remote(candidate, validated_generation):
        change_session(cookies, synthetic_credentials, action)
        return cookies.LoginStatus("verified", "ok", validated_generation)
    monkeypatch.setattr(cookies, "_remote_validate_cookies", remote)
    assert send("session.validate")["ok"]
    finish(host)
    assert (host.parsed is not None) == (action == "stable")


@pytest.mark.parametrize("action,expected", [("clear", "none"), ("replace", "local_pending"),
                                             ("corrupt", "invalid"), ("stable", "verified")])
def test_validation_reconciles_current_store(session_modules, synthetic_credentials, monkeypatch, action, expected):
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    generation = cookies.load_session_snapshot().generation

    def remote(candidate, validated_generation):
        assert validated_generation == generation
        change_session(cookies, synthetic_credentials, action)
        return cookies.LoginStatus("verified", "verified", generation)

    monkeypatch.setattr(cookies, "_remote_validate_cookies", remote)
    result = cookies.validate_saved_session()
    assert result.code == expected
    if action == "replace":
        assert result.generation != generation
    if action == "clear":
        assert not cookies.canonical_session_path().exists()


@pytest.mark.parametrize("action", ["clear", "replace", "corrupt"])
def test_bound_lease_rejects_changed_store_before_export(session_modules, synthetic_credentials, monkeypatch, action):
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    generation = cookies.load_session_snapshot().generation
    change_session(cookies, synthetic_credentials, action)
    monkeypatch.setattr(cookies, "export_cookies_to_netscape", lambda *a: pytest.fail("must not export credentials"))
    with pytest.raises(cookies.SessionSaveError):
        with cookies.cookiefile_lease(expected_generation=generation):
            pytest.fail("must not enter credential-bearing work")


def test_bound_absence_and_anonymous_do_not_switch_accounts(session_modules, synthetic_credentials, monkeypatch):
    cookies = session_modules.cookies
    with cookies.cookiefile_lease(expected_generation=None) as path:
        assert path is None
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    with pytest.raises(cookies.SessionSaveError, match="登录状态已改变"):
        with cookies.cookiefile_lease(expected_generation=None):
            pytest.fail("must not adopt a new account")
    monkeypatch.setattr(cookies, "_load_snapshot_locked", lambda: pytest.fail("anonymous must not read the store"))
    with cookies.cookiefile_lease("anonymous", expected_generation="old") as path:
        assert path is None


def test_acquired_lease_is_stable_and_cleaned_after_account_change(session_modules, synthetic_credentials):
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    generation = cookies.load_session_snapshot().generation
    with cookies.cookiefile_lease(expected_generation=generation) as path:
        original = path.read_bytes()
        change_session(cookies, synthetic_credentials, "replace")
        assert path.read_bytes() == original
    assert not path.exists()


@pytest.mark.parametrize("action", ["clear", "replace"])
@pytest.mark.parametrize("boundary", ["before_accept", "after_accept", "retry_before_accept", "retry_after_accept"])
def test_backend_download_binds_original_generation(session_modules, synthetic_credentials, backend, monkeypatch,
                                                   tmp_path, action, boundary):
    from app.backend.host import ParsedVideo
    from app.downloader import VideoInfoResult, VideoPart, FormatChoice
    from app import downloader
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    generation = cookies.load_session_snapshot().generation
    host, messages, send = backend
    part = VideoPart(1, "part", "https://www.bilibili.com/video/BV1TEST?p=1")
    info = VideoInfoResult("test", "", 1, "", [part], [FormatChoice("720p", "best", 720)])
    host.parsed = ParsedVideo("p", host.revision, info, cookies.CredentialMode.SAVED, generation)
    params = dict(parse_id="p", part_indices=[1], format_id="0", download_mode="audio_video",
                  credential_mode="saved", download_dir=str(tmp_path / "media"))
    monkeypatch.setattr(downloader, "_youtube_dl", lambda *a: pytest.fail("must fail before yt-dlp"))
    if boundary.startswith("retry"):
        def missing_ffmpeg():
            raise RuntimeError("synthetic preflight failure")
        monkeypatch.setattr(downloader, "require_ffmpeg", missing_ffmpeg)
        assert send("download.start", params)["ok"]
        finish(host)
        batch_id = next(iter(host.batches))
        assert host.batches[batch_id][0].expected_generation == generation
        method, params = "download.retry", {"batch_id": batch_id}
    else:
        method = "download.start"
    if boundary.endswith("before_accept"):
        change_session(cookies, synthetic_credentials, action)
        assert send(method, params)["error"]["code"] == "stale_session"
    else:
        def ffmpeg():
            change_session(cookies, synthetic_credentials, action)
            return "X:/synthetic/ffmpeg.exe"
        monkeypatch.setattr(downloader, "require_ffmpeg", ffmpeg)
        response = send(method, params)
        assert response["ok"]
        finish(host)
        result = list(host.batches.values())[-1][1]
        assert len(result.failed) == 1 and not result.saved_files
        assert "登录状态已改变" in result.failed[0].detail


@pytest.mark.parametrize("action", ["clear", "replace"])
def test_parse_worker_binds_generation_before_ytdlp(session_modules, synthetic_credentials, backend, monkeypatch, action):
    from app import downloader
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    host, messages, send = backend
    def view(*args):
        change_session(cookies, synthetic_credentials, action)
        return None
    monkeypatch.setattr(downloader, "_fetch_bilibili_view", view)
    monkeypatch.setattr(downloader, "_youtube_dl", lambda *a: pytest.fail("changed account must never reach yt-dlp"))
    response = send("parse.start", {"input": "https://www.bilibili.com/video/BV1xx411c7mD", "credential_mode": "saved"})
    assert response["ok"]
    finish(host)
    assert host.parsed is None
    terminal = [m for m in messages if m.get("event") == "operation.failed"][-1]
    assert "登录状态已改变" in str(terminal)


@pytest.mark.parametrize("action,code,status", [("stable", "success", "verified"),
    ("clear", "session_changed", "none"), ("replace", "session_changed", "local_pending"),
    ("corrupt", "session_changed", "invalid"), ("cancel", "cancelled", "local_pending"),
    ("refresh", "cancelled", "local_pending")])
def test_real_qr_workflow_terminal_keeps_verified_generation(session_modules, synthetic_credentials, backend,
                                                           monkeypatch, action, code, status):
    from app.services import login_service
    cookies = session_modules.cookies
    host, messages, send = backend
    host.safe_mode = False
    monkeypatch.setattr(cookies, "_remote_validate_cookies", lambda *a: cookies.LoginStatus("verified", "ok"))
    monkeypatch.setattr(login_service, "render_qr_png", lambda *a: b"synthetic-png")
    class Client:
        def generate(self):
            return SimpleNamespace(qr_url="https://account.bilibili.com/test")
        def poll(self, challenge):
            return SimpleNamespace(status=login_service.QrStatus.SUCCESS)
        def candidate_cookies(self):
            return synthetic_credentials["cookies"]
        def close(self):
            if action == "cancel":
                host.active.login.request_cancel()
            elif action == "refresh":
                host.active.login.request_refresh()
            else:
                change_session(cookies, synthetic_credentials, action)
    monkeypatch.setattr("app.backend.host.LoginWorkflow",
                        lambda **kwargs: login_service.LoginWorkflow(client_factory=Client, **kwargs))
    assert send("auth.qr.start", {"consent": True})["ok"]
    finish(host)
    terminal = [m for m in messages if m.get("event") == "operation.completed"][-1]
    assert terminal["data"]["result"]["code"] == code
    assert terminal["data"]["result"]["status"]["code"] == status
    if action == "stable":
        assert terminal["data"]["result"]["status"]["generation"] == cookies.load_session_snapshot().generation
    if action == "clear":
        assert not cookies.canonical_session_path().exists()
