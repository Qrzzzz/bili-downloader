from __future__ import annotations

import copy
import io
import json
import os
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import BaseAdapter


class NavAdapter(BaseAdapter):
    """Only synthetic credentials; Requests prepares every request, no sockets."""

    def __init__(self) -> None:
        self.requests: list[requests.PreparedRequest] = []
        self.status = 200
        self.headers: dict[str, str] = {}
        self.final_url: str | None = None
        self.payload: object = {"code": 0, "data": {"isLogin": True}}
        self.error: Exception | None = None
        self.closed = False

    def send(self, request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
        self.requests.append(request)
        assert kwargs["timeout"] == (2.5, 4.0)
        if self.error:
            raise self.error
        response = requests.Response()
        response.request = request
        response.url = self.final_url if self.final_url is not None else request.url
        response.raw = io.BytesIO()
        response.status_code = self.status if len(self.requests) == 1 else 200
        response.headers.update(self.headers)
        response._content = json.dumps(self.payload).encode()
        return response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def nav(monkeypatch: pytest.MonkeyPatch) -> NavAdapter:
    adapter = NavAdapter()
    original_init = requests.Session.__init__

    def init(session: requests.Session) -> None:
        original_init(session)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

    monkeypatch.setattr(requests.Session, "__init__", init)
    return adapter


@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 307, 308])
@pytest.mark.parametrize("target", [
    "https://redirect-target.invalid/nav",
    "http://api.bilibili.com/x/web-interface/nav",
    "https://api.bilibili.com/x/web-interface/nav",  # loops and same-origin redirects too
    "https://passport.bilibili.com/nav",
    "https://api.bilibili.com:444/nav",
    "//redirect-target.invalid/nav",
])
def test_nav_rejects_redirect_without_sending_a_second_request(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
    status: int, target: str,
) -> None:
    cookies = session_modules.cookies
    nav.status, nav.headers = status, {"Location": target}
    result = cookies._remote_validate_cookies(synthetic_credentials["cookies"], "original")
    assert result.code == "protocol_error"
    assert result.generation == "original"
    assert len(nav.requests) == 1
    assert nav.requests[0].url == cookies.NAV_API_URL
    assert nav.closed


def test_nav_rechecks_prepared_url_before_transport(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = requests.Request.prepare

    def prepare(request: requests.Request) -> requests.PreparedRequest:
        prepared = original(request)
        prepared.url = "http://api.bilibili.com/x/web-interface/nav"
        return prepared

    monkeypatch.setattr(requests.Request, "prepare", prepare)
    assert session_modules.cookies._remote_validate_cookies(synthetic_credentials["cookies"]).code == "protocol_error"
    assert not nav.requests


def test_secure_cookie_jar_excludes_credentials_on_http(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
) -> None:
    session_modules.cookies._remote_validate_cookies(synthetic_credentials["cookies"])
    prepared = requests.Request("GET", "http://api.bilibili.com/x/web-interface/nav",
                                cookies=nav.requests[0]._cookies).prepare()
    assert "Cookie" not in prepared.headers
    assert len(nav.requests) == 1  # the HTTP request above is never sent


@pytest.mark.skipif(os.name != "nt", reason="real Windows DPAPI roundtrip")
def test_validated_candidate_roundtrips_real_dpapi_in_isolated_profile(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
) -> None:
    cookies = session_modules.cookies
    cookies._set_protector_for_tests(None, None)
    path = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    original = path.read_bytes()
    candidate = copy.deepcopy(synthetic_credentials["cookies"])
    candidate[0]["value"] = "synthetic-new-dpapi-session"
    status = cookies.validate_and_commit_candidate_cookies(candidate)
    assert status.code == "verified"
    assert path.read_bytes() != original
    assert b"synthetic-new-dpapi-session" not in path.read_bytes()
    snapshot = cookies.load_session_snapshot()
    assert snapshot.generation == status.generation
    assert next(c["value"] for c in snapshot.cookies if c["name"] == "SESSDATA") == "synthetic-new-dpapi-session"
    assert len(nav.requests) == 1


@pytest.mark.parametrize("target", [
    "http://api.bilibili.com/x/web-interface/nav",
    "https://other.bilibili.com/x/web-interface/nav",
    "https://api.bilibili.com.invalid/x/web-interface/nav",
    "https://api.bilibili.com:444/x/web-interface/nav",
    "https://user@api.bilibili.com/x/web-interface/nav",
    "https://api.bilibili.com/x/web-interface/nav#fragment",
    "https://api.bilibili.com/x/web-interface/nav?unexpected=1",
    "https://api.bilibili.com/other",
    "https://api.bilibili.com./x/web-interface/nav",
    "https://127.0.0.1/x/web-interface/nav",
    "",
])
def test_nav_rejects_unexpected_initial_and_final_urls(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
    monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    cookies = session_modules.cookies
    nav.final_url = target
    assert cookies._remote_validate_cookies(synthetic_credentials["cookies"]).code == "protocol_error"
    assert len(nav.requests) == 1
    nav.requests.clear()
    monkeypatch.setattr(cookies, "NAV_API_URL", target)
    assert cookies._remote_validate_cookies(synthetic_credentials["cookies"]).code == "protocol_error"
    assert not nav.requests


def test_nav_preserves_cookie_domain_path_secure_expiry_and_duplicate_names(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
) -> None:
    candidate = copy.deepcopy(synthetic_credentials["cookies"][:2])
    for domain, path, value, expires in [
        ("api.bilibili.com", "/x", "synthetic-api", 4102444800),
        (".bilibili.com", "/x/web-interface", "synthetic-path", 4102444800),
        (".bilibili.com", "/x/web-interface/nav-child", "synthetic-wrong-path", 4102444800),
        (".bilibili.com", "/x", "synthetic-expired", 1),
        (".biliapi.net", "/", "synthetic-other-domain", 4102444800),
        ("bilibili.com", "/", "synthetic-host-only", 4102444800),
        (".example.com", "/", "synthetic-unapproved", 4102444800),
    ]:
        candidate.append(dict(name="bili_jct", value=value, domain=domain,
                              path=path, secure=True, expires=expires))
    result = session_modules.cookies._remote_validate_cookies(candidate)
    assert result.code == "verified"
    sent = nav.requests[0]
    header = sent.headers["Cookie"]
    assert synthetic_credentials["session_secret"] in header
    assert "synthetic-api" in header and "synthetic-path" in header
    for secret in ("synthetic-wrong-path", "synthetic-expired", "synthetic-other-domain",
                   "synthetic-host-only", "synthetic-unapproved"):
        assert secret not in header
    assert all(cookie.secure for cookie in sent._cookies)
    assert len([c for c in sent._cookies if c.name == "bili_jct"]) > 1
    assert nav.closed


@pytest.mark.parametrize(("status", "payload", "error", "expected"), [
    (200, {"code": 0, "data": {"isLogin": True}}, None, "verified"),
    (401, {}, None, "invalid"),
    (412, {}, None, "platform_412"),
    (503, {}, None, "offline"),
    (200, {"code": -101}, None, "invalid"),
    (200, {"code": 0, "data": {"isLogin": "yes"}}, None, "protocol_error"),
    (200, {}, requests.Timeout("synthetic timeout"), "offline"),
    (200, {}, requests.ConnectionError("synthetic offline"), "offline"),
])
def test_nav_wire_results_preserve_saved_state_and_classification(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter,
    status: int, payload: object, error: Exception | None, expected: str,
) -> None:
    cookies = session_modules.cookies
    path = cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    before = path.read_bytes()
    generation = cookies.load_session_snapshot().generation
    nav.status, nav.payload, nav.error = status, payload, error
    result = cookies.validate_saved_session()
    assert result.code == expected and result.generation == generation
    assert path.read_bytes() == before
    assert len(nav.requests) == 1 and nav.closed


@pytest.mark.parametrize("saved", [False, True])
def test_redirect_cannot_commit_candidate_or_replace_previous_session(
    session_modules: SimpleNamespace, synthetic_credentials: dict, nav: NavAdapter, saved: bool,
) -> None:
    cookies = session_modules.cookies
    path = cookies.canonical_session_path()
    if saved:
        cookies._store_cookies_for_tests(synthetic_credentials["cookies"])
    before = path.read_bytes() if saved else None
    candidate = copy.deepcopy(synthetic_credentials["cookies"])
    candidate[0]["value"] = "synthetic-replacement"
    nav.status, nav.headers = 302, {"Location": "https://redirect-target.invalid/nav"}
    result = cookies.validate_and_commit_candidate_cookies(candidate)
    assert result.code == "protocol_error"
    assert (path.read_bytes() if path.exists() else None) == before
    assert len(nav.requests) == 1
