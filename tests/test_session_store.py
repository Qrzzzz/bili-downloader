from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import os
import threading
import time

import pytest
import requests
from requests.adapters import BaseAdapter


class FakeResponse(requests.Response):
    def __init__(self, payload: object, status_code: int = 200) -> None:
        super().__init__()
        self.payload = payload
        self.status_code = status_code
        self.raw = io.BytesIO()
        self._content = b"{}"

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return copy.deepcopy(self.payload)


def _mock_nav(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Keep transaction scenarios above the real Requests preparation boundary."""
    class Adapter(BaseAdapter):
        def send(self, request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
            response = handler(request.url, cookies=request._cookies, **kwargs)
            response.request, response.url = request, request.url
            return response

        def close(self) -> None:
            pass

    adapter = Adapter()
    monkeypatch.setattr(requests.Session, "get_adapter", lambda *_args, **_kwargs: adapter)

def _replace_cookie_values(cookies: object, suffix: str) -> list[dict[str, object]]:
    result = copy.deepcopy(cookies)
    assert isinstance(result, list)
    for cookie in result:
        if cookie["name"] in {"SESSDATA", "DedeUserID", "bili_jct"}:
            cookie["value"] = f"{cookie['value']}-{suffix}"
    return result


def test_status_none_does_not_touch_network(session_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = session_modules.cookies
    _mock_nav(
        monkeypatch,
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
    saved_path = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    pending = cookies.describe_login_status()
    captured: dict[str, object] = {}

    def verified(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({"code": 0, "data": {"isLogin": True}})

    _mock_nav(monkeypatch, verified)
    status = cookies.validate_saved_session()

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
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    path = cookies.canonical_session_path()
    before = path.read_bytes()

    def offline(*_args: object, **_kwargs: object) -> object:
        raise cookies.requests.ConnectionError("synthetic offline")

    _mock_nav(monkeypatch, offline)
    assert cookies.validate_saved_session().code == "offline"
    assert path.read_bytes() == before

    _mock_nav(
        monkeypatch,
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
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    path = cookies.canonical_session_path()
    before = path.read_bytes()
    monkeypatch.setattr(cookies.time, "time", lambda: 4_200_000_000)
    _mock_nav(
        monkeypatch,
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
    path = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
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


def test_candidate_is_filtered_verified_before_commit_and_then_atomically_saved(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    candidate = copy.deepcopy(synthetic_credentials["cookies"])
    assert isinstance(candidate, list)
    candidate.append(
        {
            "name": "unapproved_cookie",
            "value": "must-not-be-sent-or-saved",
            "domain": ".bilibili.com",
            "path": "/",
            "secure": True,
        }
    )
    observed: dict[str, object] = {}

    def verified(url: str, **kwargs: object) -> FakeResponse:
        assert url == cookies.NAV_API_URL
        assert not cookies.canonical_session_path().exists()
        observed.update(kwargs)
        return FakeResponse({"code": 0, "data": {"isLogin": True}})

    _mock_nav(monkeypatch, verified)
    status = cookies.validate_and_commit_candidate_cookies(candidate)

    assert status.code == "verified" and status.generation
    assert cookies.canonical_session_path().exists()
    assert "unapproved_cookie" not in observed["cookies"]  # type: ignore[operator]
    snapshot = cookies.load_session_snapshot()
    assert snapshot is not None
    assert {item["name"] for item in snapshot.cookies} == {"SESSDATA", "DedeUserID", "bili_jct"}
    assert all(set(item) <= cookies.ALLOWED_COOKIE_FIELDS for item in snapshot.cookies)


@pytest.mark.parametrize(
    ("remote", "expected_code"),
    [
        (lambda *_args, **_kwargs: FakeResponse({"code": -101, "data": {"isLogin": False}}), "invalid"),
        (lambda *_args, **_kwargs: FakeResponse({"code": 0, "data": {}}, 412), "platform_412"),
        (lambda *_args, **_kwargs: FakeResponse({"code": 0, "data": {"isLogin": "yes"}}), "protocol_error"),
        (lambda *_args, **_kwargs: FakeResponse(ValueError("invalid json")), "protocol_error"),
    ],
)
def test_candidate_validation_failures_preserve_existing_valid_session(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    remote: object,
    expected_code: str,
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    path = cookies.canonical_session_path()
    before = path.read_bytes()
    replacement = _replace_cookie_values(synthetic_credentials["cookies"], "candidate")
    _mock_nav(monkeypatch, remote)

    status = cookies.validate_and_commit_candidate_cookies(replacement)

    assert status.code == expected_code
    assert path.read_bytes() == before


def test_candidate_offline_and_cancel_after_validation_preserve_existing_session(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    path = cookies.canonical_session_path()
    before = path.read_bytes()
    replacement = _replace_cookie_values(synthetic_credentials["cookies"], "candidate")

    _mock_nav(
        monkeypatch,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(cookies.requests.Timeout("offline")),
    )
    assert cookies.validate_and_commit_candidate_cookies(replacement).code == "offline"
    assert path.read_bytes() == before

    state = {"cancelled": False}

    def verified_then_cancel(*_args: object, **_kwargs: object) -> FakeResponse:
        state["cancelled"] = True
        return FakeResponse({"code": 0, "data": {"isLogin": True}})

    _mock_nav(monkeypatch, verified_then_cancel)
    status = cookies.validate_and_commit_candidate_cookies(
        replacement,
        cancelled=lambda: state["cancelled"],
    )
    assert status.code == "cancelled"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "bad_candidate",
    [
        [{"domain": ".bilibili.com", "name": "SESSDATA", "value": 123}],
        ["not-a-cookie"],
        [
            {"domain": ".bilibili.com", "name": "SESSDATA", "value": "secret"},
            {"domain": ".bilibili.com", "name": "DedeUserID", "value": "user", "secure": "yes"},
        ],
    ],
)
def test_candidate_cookie_field_type_errors_fail_closed(
    session_modules: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    bad_candidate: object,
) -> None:
    cookies = session_modules.cookies
    _mock_nav(
        monkeypatch,
        lambda *_args, **_kwargs: pytest.fail("invalid candidates must not reach NAV"),
    )
    assert cookies.validate_and_commit_candidate_cookies(bad_candidate).code == "invalid"
    assert not cookies.canonical_session_path().exists()


def test_atomic_replace_failure_retains_previous_generation(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    path = cookies.canonical_session_path()
    previous_bytes = path.read_bytes()
    previous_generation = cookies.load_session_snapshot().generation
    replacement = _replace_cookie_values(synthetic_credentials["cookies"], "replacement")

    with monkeypatch.context() as scoped:
        scoped.setattr(cookies.os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fault")))
        with pytest.raises(cookies.SessionSaveError, match="原子保存"):
            cookies._store_cookies_for_tests(replacement)

    assert path.read_bytes() == previous_bytes
    assert cookies.load_session_snapshot().generation == previous_generation
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_stale_remote_result_cannot_overwrite_new_generation(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    old_generation = cookies.load_session_snapshot().generation
    replacement = _replace_cookie_values(synthetic_credentials["cookies"], "new-generation")

    def update_during_validation(*_args: object, **_kwargs: object) -> FakeResponse:
        cookies._store_cookies_for_tests(replacement)
        return FakeResponse({"code": 0, "data": {"isLogin": True}})

    _mock_nav(monkeypatch, update_during_validation)
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
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
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


@pytest.mark.parametrize("legacy_name", ["storage_state.json", "cookies.txt"])
def test_v1_2_legacy_migration_commits_before_plaintext_and_profile_cleanup(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    isolated_paths: object,
    legacy_name: str,
) -> None:
    cookies = session_modules.cookies
    legacy = isolated_paths.roaming / "BiliDownloader" / "session"  # type: ignore[attr-defined]
    legacy.mkdir(parents=True)
    source = legacy / legacy_name
    if legacy_name == "storage_state.json":
        source.write_text(
            json.dumps({"cookies": synthetic_credentials["cookies"], "origins": []}),
            encoding="utf-8",
        )
    else:
        cookies.export_cookies_to_netscape(synthetic_credentials["cookies"], source)
    profile = legacy / "playwright-profile"
    cache = legacy / "login-cache"
    profile.mkdir()
    cache.mkdir()
    (profile / "residue.bin").write_text(str(synthetic_credentials["session_secret"]), encoding="utf-8")
    (cache / "residue.bin").write_text(str(synthetic_credentials["session_secret"]), encoding="utf-8")

    assert cookies.migrate_legacy_session()

    snapshot = cookies.load_session_snapshot()
    assert snapshot is not None
    assert {item["name"] for item in snapshot.cookies} == {"SESSDATA", "DedeUserID", "bili_jct"}
    assert not source.exists()
    assert not profile.exists()
    assert not cache.exists()


def test_v1_2_canonical_schema_remains_readable_and_cleans_obsolete_profile(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
) -> None:
    cookies = session_modules.cookies
    path = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    before = path.read_bytes()
    profile = cookies.legacy_browser_profile_dir()
    profile.mkdir(parents=True)
    (profile / "residue.bin").write_bytes(b"synthetic legacy profile")

    assert cookies.migrate_legacy_session()
    assert path.read_bytes() == before
    assert cookies.load_session_snapshot() is not None
    assert not profile.exists()


def test_v1_3_valid_canonical_cleans_known_v1_1_and_v1_2_plaintext_idempotently(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    isolated_paths: object,
) -> None:
    cookies = session_modules.cookies
    canonical = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    before = canonical.read_bytes()
    legacy_files = [
        cookies.session_dir() / "storage_state.json",
        cookies.session_dir() / "cookies.txt",
        isolated_paths.roaming / "BiliDownloader" / "session" / "storage_state.json",  # type: ignore[attr-defined]
        isolated_paths.roaming / "BiliDownloader" / "session" / "cookies.txt",  # type: ignore[attr-defined]
    ]
    for path in legacy_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(synthetic_credentials["session_secret"]), encoding="utf-8")

    assert cookies.cleanup_legacy_login_residue() == ()
    assert cookies.cleanup_legacy_login_residue() == ()

    assert canonical.read_bytes() == before
    assert cookies.load_session_snapshot() is not None
    assert all(not path.exists() for path in legacy_files)


def test_corrupt_canonical_never_triggers_plaintext_or_residue_deletion(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
) -> None:
    cookies = session_modules.cookies
    canonical = cookies.canonical_session_path()
    canonical.write_bytes(b"synthetic corrupt canonical")
    plaintext = cookies.cookies_txt_path()
    plaintext.write_text(str(synthetic_credentials["session_secret"]), encoding="utf-8")
    profile = cookies.legacy_browser_profile_dir()
    profile.mkdir()
    (profile / "state.bin").write_bytes(b"synthetic")

    first = cookies.cleanup_legacy_login_residue()
    second = cookies.cleanup_legacy_login_residue()

    assert first == second
    assert plaintext.exists() and profile.exists()
    assert canonical.read_bytes() == b"synthetic corrupt canonical"
    assert all(str(synthetic_credentials["session_secret"]) not in failure for failure in first)
    assert all("canonical" not in failure.lower() or "SessionSaveError" in failure for failure in first)


def test_partial_plaintext_cleanup_failure_is_redacted_and_preserves_canonical(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookies = session_modules.cookies
    canonical = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    before = canonical.read_bytes()
    first = cookies.cookies_txt_path()
    second = isolated_paths.roaming / "BiliDownloader" / "session" / "cookies.txt"  # type: ignore[attr-defined]
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(synthetic_credentials["session_secret"]), encoding="utf-8")
    original_unlink = Path.unlink

    def fail_one(self: Path, *args: object, **kwargs: object) -> None:
        if self == first:
            raise PermissionError(f"synthetic denial {synthetic_credentials['session_secret']}")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    failures = cookies.cleanup_legacy_login_residue()

    assert first.exists() and not second.exists()
    assert canonical.read_bytes() == before
    assert len(failures) == 1
    assert failures[0] == "<local-app-data>/BiliDownloader/session/cookies.txt: PermissionError"
    assert str(isolated_paths.root) not in failures[0]  # type: ignore[attr-defined]
    assert str(synthetic_credentials["session_secret"]) not in failures[0]


def test_stale_owned_credential_residue_uses_name_age_and_owner_checks(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    now = time.time()
    old = now - max(
        cookies.STALE_LEASE_AGE_SECONDS,
        cookies.STALE_ATOMIC_TEMP_AGE_SECONDS,
        cookies.STALE_QUARANTINE_AGE_SECONDS,
    ) - 60
    root = cookies.session_dir()
    leases = root / "leases"
    leases.mkdir()

    stale_id = "a" * 32
    stale_lease = leases / f"lease-{stale_id}"
    stale_lease.mkdir()
    (stale_lease / "cookies.txt").write_text("synthetic", encoding="utf-8")
    os.utime(stale_lease, (old, old))

    recent_id = "b" * 32
    recent_lease = leases / f"lease-{recent_id}"
    recent_lease.mkdir()
    (recent_lease / "cookies.txt").write_text("synthetic", encoding="utf-8")

    unowned_lease = leases / f"lease-{'c' * 32}"
    unowned_lease.mkdir()
    (unowned_lease / "unexpected.bin").write_bytes(b"synthetic")
    os.utime(unowned_lease, (old, old))

    stale_temp = root / f".session.dat.123.{'d' * 32}.tmp"
    recent_temp = root / f".session.dat.124.{'e' * 32}.tmp"
    lookalike_temp = root / ".session.dat.user-file.tmp"
    for path in (stale_temp, recent_temp, lookalike_temp):
        path.write_bytes(b"synthetic")
    os.utime(stale_temp, (old, old))
    os.utime(lookalike_temp, (old, old))

    app_root = cookies.app_data_dir()
    stale_quarantine = app_root / "session_corrupted_20260801_1"
    recent_quarantine = app_root / "session_corrupted_20260830_1"
    unrelated = app_root / "session_corrupted_user_notes"
    for path in (stale_quarantine, recent_quarantine, unrelated):
        path.mkdir()
        (path / "cookies.txt").write_text("synthetic", encoding="utf-8")
    os.utime(stale_quarantine, (old, old))
    os.utime(unrelated, (old, old))

    assert cookies.cleanup_legacy_login_residue() == ()

    assert not stale_lease.exists()
    assert recent_lease.exists() and unowned_lease.exists()
    assert not stale_temp.exists()
    assert recent_temp.exists() and lookalike_temp.exists()
    assert not stale_quarantine.exists()
    assert recent_quarantine.exists() and unrelated.exists()


def test_live_cookie_lease_releases_store_lock_and_is_not_deleted_by_competing_cleanup(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    outcome: list[str] = []

    with cookies.cookiefile_lease(cookies.CredentialMode.SAVED) as cookiefile:
        assert cookiefile is not None and cookiefile.exists()

        def compete() -> None:
            try:
                with cookies._cross_process_lock(timeout=0.1):
                    outcome.append("acquired")
                    cookies._cleanup_stale_lease_residue_locked(now=time.time() + 10**9)
            except cookies.SessionBusyError:
                outcome.append("busy")

        thread = threading.Thread(target=compete)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert outcome == ["acquired"]
        assert cookiefile.exists()

    assert not cookiefile.exists()


def test_saved_cookie_leases_can_overlap_without_sharing_plaintext_files(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    second_entered = threading.Event()
    release_second = threading.Event()
    second_paths: list[Path] = []
    errors: list[BaseException] = []

    with cookies.cookiefile_lease(cookies.CredentialMode.SAVED) as first:
        assert first is not None and first.exists()

        def overlap() -> None:
            try:
                with cookies.cookiefile_lease(cookies.CredentialMode.SAVED) as second:
                    assert second is not None and second.exists()
                    second_paths.append(second)
                    second_entered.set()
                    release_second.wait(2)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                second_entered.set()

        thread = threading.Thread(target=overlap)
        thread.start()
        try:
            assert second_entered.wait(1)
            assert not errors
            assert second_paths and second_paths[0] != first
            assert first.exists() and second_paths[0].exists()
        finally:
            release_second.set()
            thread.join(timeout=2)
        assert not thread.is_alive()
        assert not errors
        assert not second_paths[0].exists()

    assert not first.exists()


def test_logout_refuses_to_delete_another_active_cookie_lease(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    canonical = cookies.canonical_session_path()

    with cookies.cookiefile_lease(cookies.CredentialMode.SAVED) as cookiefile:
        assert cookiefile is not None and cookiefile.exists()
        result = cookies.clear_login_state()

        assert not result.ok
        assert result.failures == ("登录态正由另一个实例的活动任务使用，请先等待该任务结束",)
        assert canonical.exists()
        assert cookiefile.exists()

    result = cookies.clear_login_state()
    assert result.ok
    assert not canonical.exists()


def test_logout_removes_current_legacy_and_quarantine_credentials(
    session_modules: SimpleNamespace,
    synthetic_credentials: dict[str, object],
    isolated_paths: object,
) -> None:
    cookies = session_modules.cookies
    cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
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
    assert result.remaining == ("<local-app-data>/BiliDownloader/session-quarantine-locked",)
    assert all(str(isolated_paths.root) not in item for item in (*result.failures, *result.remaining))  # type: ignore[attr-defined]
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
