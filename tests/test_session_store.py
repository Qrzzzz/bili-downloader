from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeContext:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = copy.deepcopy(state)
        self.calls = 0

    def storage_state(self) -> dict[str, object]:
        self.calls += 1
        return copy.deepcopy(self.state)


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return copy.deepcopy(self.payload)


def _context(credentials: dict[str, object]) -> FakeContext:
    return FakeContext(
        {
            "cookies": credentials["cookies"],
            "origins": [
                {
                    "origin": "https://passport.bilibili.com",
                    "localStorage": [
                        {"name": "third_party_payload", "value": credentials["origin_secret"]}
                    ],
                },
                {
                    "origin": "https://example.com",
                    "localStorage": [{"name": "token", "value": credentials["third_party_secret"]}],
                },
            ],
        }
    )


def _replace_cookie_values(cookies: object, suffix: str) -> list[dict[str, object]]:
    result = copy.deepcopy(cookies)
    assert isinstance(result, list)
    for cookie in result:
        if cookie["name"] in {"SESSDATA", "DedeUserID", "bili_jct"}:
            cookie["value"] = f"{cookie['value']}-{suffix}"
    return result


def test_status_none_does_not_touch_network(session_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = session_modules.cookies
    monkeypatch.setattr(
        cookies.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("no credentials must not trigger a network request"),
    )

    assert cookies.describe_login_status().code == "none"
    assert cookies.validate_saved_session().code == "none"


def test_local_pending_then_server_verified(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    context = _context(synthetic_credentials)
    saved_path = cookies.save_context_storage_state_atomic(context)
    pending = cookies.describe_login_status()
    captured: dict[str, object] = {}

    def verified(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({"code": 0, "data": {"isLogin": True}})

    monkeypatch.setattr(cookies.requests, "get", verified)
    status = cookies.validate_saved_session()

    assert context.calls == 1
    assert pending.code == "local_pending" and pending.generation
    assert status.code == "verified" and status.generation == pending.generation
    assert captured["url"] == cookies.NAV_API_URL
    assert captured["cookies"]["SESSDATA"] == synthetic_credentials["session_secret"]  # type: ignore[index]
    assert saved_path == cookies.canonical_session_path()


def test_offline_and_revoked_states_preserve_canonical_credentials(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies.save_context_storage_state_atomic(_context(synthetic_credentials))
    path = cookies.canonical_session_path()
    before = path.read_bytes()

    def offline(*_args: object, **_kwargs: object) -> object:
        raise cookies.requests.ConnectionError("synthetic offline")

    monkeypatch.setattr(cookies.requests, "get", offline)
    assert cookies.validate_saved_session().code == "offline"
    assert path.read_bytes() == before

    monkeypatch.setattr(
        cookies.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse({"code": -101, "data": {"isLogin": False}}),
    )
    assert cookies.validate_saved_session().code == "invalid"
    assert path.read_bytes() == before
    assert not list(cookies.app_data_dir().glob("session_corrupted_*"))


def test_expired_credentials_are_normal_invalid_state_not_corruption(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies.save_context_storage_state_atomic(_context(synthetic_credentials))
    path = cookies.canonical_session_path()
    before = path.read_bytes()
    monkeypatch.setattr(cookies.time, "time", lambda: 4_200_000_000)
    monkeypatch.setattr(
        cookies.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("expired local credentials must not hit the network"),
    )

    status = cookies.validate_saved_session()

    assert status.code == "invalid"
    assert "过期" in status.text
    assert path.read_bytes() == before


def test_corrupt_local_state_is_invalid_without_plaintext_quarantine(session_modules: SimpleNamespace) -> None:
    cookies = session_modules.cookies
    path = cookies.canonical_session_path()
    path.write_bytes(b"not-a-valid-protected-envelope")

    assert cookies.describe_login_status().code == "invalid"
    assert cookies.validate_saved_session().code == "invalid"
    assert path.read_bytes() == b"not-a-valid-protected-envelope"
    assert not list(cookies.app_data_dir().glob("session_corrupted_*"))


def test_canonical_store_filters_third_party_state_and_encrypts_at_rest(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    isolated_paths: object,
) -> None:
    cookies = session_modules.cookies
    context = _context(synthetic_credentials)
    path = cookies.save_context_storage_state_atomic(context)
    raw = path.read_bytes()
    snapshot = cookies.load_session_snapshot()
    assert snapshot is not None

    all_secrets = [
        synthetic_credentials["session_secret"],
        synthetic_credentials["user_secret"],
        synthetic_credentials["csrf_secret"],
        synthetic_credentials["third_party_secret"],
        synthetic_credentials["origin_secret"],
    ]
    for secret in all_secrets:
        assert str(secret).encode("utf-8") not in raw

    assert path.is_relative_to(isolated_paths.local)  # type: ignore[attr-defined]
    assert not (isolated_paths.roaming / "BiliDownloader" / "session" / "session.dat").exists()  # type: ignore[attr-defined]
    assert {cookie["name"] for cookie in snapshot.cookies} == {"SESSDATA", "DedeUserID", "bili_jct"}
    assert all(cookies.is_bilibili_cookie_domain(cookie["domain"]) for cookie in snapshot.cookies)
    assert all(
        set(cookie) <= {"name", "value", "domain", "path", "secure", "httpOnly", "expires", "sameSite"}
        for cookie in snapshot.cookies
    )
    assert all("unexpected" not in cookie for cookie in snapshot.cookies)


def test_atomic_replace_failure_retains_previous_generation(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies.save_context_storage_state_atomic(_context(synthetic_credentials))
    path = cookies.canonical_session_path()
    previous_bytes = path.read_bytes()
    previous_generation = cookies.load_session_snapshot().generation
    replacement = _replace_cookie_values(synthetic_credentials["cookies"], "replacement")

    with monkeypatch.context() as scoped:
        scoped.setattr(cookies.os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fault")))
        with pytest.raises(cookies.SessionSaveError, match="原子保存"):
            cookies.save_context_storage_state_atomic(FakeContext({"cookies": replacement, "origins": []}))

    assert path.read_bytes() == previous_bytes
    assert cookies.load_session_snapshot().generation == previous_generation
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_stale_remote_result_cannot_overwrite_new_generation(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies.save_context_storage_state_atomic(_context(synthetic_credentials))
    old_generation = cookies.load_session_snapshot().generation
    replacement = _replace_cookie_values(synthetic_credentials["cookies"], "new-generation")

    def update_during_validation(*_args: object, **_kwargs: object) -> FakeResponse:
        cookies.save_playwright_cookies_as_netscape(replacement)
        return FakeResponse({"code": 0, "data": {"isLogin": True}})

    monkeypatch.setattr(cookies.requests, "get", update_during_validation)
    status = cookies.validate_saved_session()

    assert status.code == "local_pending"
    assert status.generation and status.generation != old_generation
    assert cookies.load_session_snapshot().generation == status.generation


def test_anonymous_cookie_lease_never_reads_or_materializes_credentials(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies.save_context_storage_state_atomic(_context(synthetic_credentials))
    leases = cookies.session_dir() / "leases"

    with monkeypatch.context() as scoped:
        scoped.setattr(
            cookies,
            "_load_snapshot_locked",
            lambda: pytest.fail("anonymous mode must not read the saved session"),
        )
        with cookies.cookiefile_lease(cookies.CredentialMode.ANONYMOUS) as cookiefile:
            assert cookiefile is None

    assert cookies.cookie_options(mode=cookies.CredentialMode.ANONYMOUS) == {}
    assert not leases.exists()

    with cookies.cookiefile_lease(cookies.CredentialMode.SAVED) as cookiefile:
        assert cookiefile is not None and cookiefile.exists()
        lease_path = cookiefile
        text = cookiefile.read_text(encoding="utf-8")
        assert synthetic_credentials["session_secret"] in text
        assert synthetic_credentials["third_party_secret"] not in text

    assert not lease_path.exists()
    assert not leases.exists()


def test_logout_removes_current_legacy_and_quarantine_credentials(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    isolated_paths: object,
) -> None:
    cookies = session_modules.cookies
    cookies.save_context_storage_state_atomic(_context(synthetic_credentials))
    residue = str(synthetic_credentials["session_secret"])
    roots = [
        isolated_paths.local / "BiliDownloader",  # type: ignore[attr-defined]
        isolated_paths.roaming / "BiliDownloader",  # type: ignore[attr-defined]
    ]
    for index, root in enumerate(roots):
        legacy = root / "session"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "storage_state.json").write_text(residue, encoding="utf-8")
        (legacy / "login-cache").mkdir(exist_ok=True)
        (legacy / "login-cache" / "nested.txt").write_text(residue, encoding="utf-8")
        quarantine = root / f"session_corrupted_20260712_{index}"
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / "cookies.txt").write_text(residue, encoding="utf-8")

    result = cookies.clear_login_state()

    assert result.ok
    assert not result.failures and not result.remaining
    assert not cookies.has_saved_session()
    for root in roots:
        for file in root.rglob("*") if root.exists() else ():
            if file.is_file():
                assert residue.encode("utf-8") not in file.read_bytes()


def test_logout_reports_and_verifies_deletion_failure(
    session_modules: SimpleNamespace,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    locked = isolated_paths.local / "BiliDownloader" / "session-quarantine-locked"  # type: ignore[attr-defined]
    locked.mkdir(parents=True)
    (locked / "credentials.bin").write_bytes(b"synthetic-only")
    original_rmtree = cookies.shutil.rmtree

    def fail_selected(path: object, *args: object, **kwargs: object) -> object:
        if Path(path) == locked:
            raise PermissionError("synthetic locked directory")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cookies.shutil, "rmtree", fail_selected)
    result = cookies.clear_login_state()
    monkeypatch.setattr(cookies.shutil, "rmtree", original_rmtree)

    assert not result.ok
    assert result.failures
    assert str(locked) in result.remaining
    assert locked.exists()
    original_rmtree(locked)


def test_logout_reports_credential_root_enumeration_failure(
    session_modules: SimpleNamespace,
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    legacy_root = isolated_paths.roaming / "BiliDownloader"  # type: ignore[attr-defined]
    legacy_root.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def fail_legacy(self: Path):
        if self == legacy_root:
            raise PermissionError("synthetic unreadable credential root")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_legacy)
    result = cookies.clear_login_state()

    assert not result.ok
    assert any("无法枚举登录态目录" in item for item in result.failures)
