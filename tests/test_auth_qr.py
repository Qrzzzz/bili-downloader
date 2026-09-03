from __future__ import annotations

import copy
import struct
from collections.abc import Callable
from typing import Any

import pytest
import requests

from app import auth_qr


QR_URL = "https://account.bilibili.com/h5-app/passport/login/scan?navhide=1&qrcode_key=synthetic-secret-key"
SUCCESS_URL = (
    "https://passport.biligame.com/x/passport-login/web/crossDomain?"
    "DedeUserID=synthetic-user&SESSDATA=synthetic-session&bili_jct=synthetic-csrf&"
    "Expires=4102444800&gourl=https%3A%2F%2Fwww.bilibili.com%2F&"
    "first_domain=.bilibili.com&future_platform_field=opaque-value"
)


def envelope(data: object) -> dict[str, object]:
    return {"code": 0, "message": "0", "ttl": 1, "data": data}


def generate_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {"url": QR_URL, "qrcode_key": "synthetic-secret-key"}
    data.update(overrides)
    return envelope(data)


def poll_payload(code: object, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "url": SUCCESS_URL if code == 0 else "",
        "refresh_token": "synthetic-refresh-secret" if code == 0 else "",
        "timestamp": 1_725_000_000,
        "code": code,
        "message": "",
    }
    data.update(overrides)
    return envelope(data)


class FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self.payload = copy.deepcopy(payload)
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return copy.deepcopy(self.payload)


class FakeSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(self)
        assert isinstance(item, FakeResponse)
        return item

    def close(self) -> None:
        self.closed = True


def _client(responses: list[object]) -> tuple[auth_qr.QrLoginClient, FakeSession]:
    session = FakeSession(responses)
    return auth_qr.QrLoginClient(session), session  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (86101, auth_qr.QrStatus.WAITING_SCAN),
        (86090, auth_qr.QrStatus.WAITING_CONFIRMATION),
        (86038, auth_qr.QrStatus.EXPIRED),
        (0, auth_qr.QrStatus.SUCCESS),
    ],
)
def test_generate_and_known_poll_status_contract(code: int, expected: auth_qr.QrStatus) -> None:
    client, session = _client([FakeResponse(generate_payload()), FakeResponse(poll_payload(code))])

    challenge = client.generate()
    result = client.poll(challenge)

    assert result.status is expected
    assert "synthetic-secret-key" not in repr(challenge)
    assert session.calls[0][0] == auth_qr.GENERATE_API_URL
    assert session.calls[1][0] == auth_qr.POLL_API_URL
    assert session.calls[0][1]["timeout"] == auth_qr.REQUEST_TIMEOUT
    assert session.calls[1][1]["timeout"] == auth_qr.REQUEST_TIMEOUT
    assert session.calls[1][1]["params"] == {"qrcode_key": "synthetic-secret-key"}
    assert session.headers["User-Agent"].startswith("BiliDownloaderLite/2.3 ")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"code": 0, "message": "0", "data": {}},
        {"code": False, "message": "0", "ttl": 1, "data": {}},
        envelope({"url": QR_URL}),
        generate_payload(qrcode_key=123),
        generate_payload(qrcode_key="different-secret-key"),
        generate_payload(url="http://account.bilibili.com/unsafe"),
        generate_payload(url="https://attacker.example/unsafe"),
    ],
)
def test_generate_schema_types_and_origin_fail_closed(payload: object) -> None:
    client, _session = _client([FakeResponse(payload)])
    with pytest.raises(auth_qr.QrProtocolError):
        client.generate()


@pytest.mark.parametrize(
    "payload",
    [
        poll_payload(99999),
        poll_payload("86101"),
        poll_payload(86101, timestamp="bad"),
        poll_payload(86101, refresh_token="unexpected-secret"),
        poll_payload(0, url="https://attacker.example/callback?SESSDATA=secret"),
        poll_payload(
            0,
            url="https://attacker@passport.biligame.com/crossDomain?SESSDATA=secret",
        ),
        poll_payload(
            0,
            url="https://passport.biligame.com:444/crossDomain?SESSDATA=secret",
        ),
    ],
)
def test_poll_unknown_status_schema_and_callback_origin_fail_closed(payload: object) -> None:
    client, _session = _client([FakeResponse(generate_payload()), FakeResponse(payload)])
    challenge = client.generate()
    with pytest.raises(auth_qr.QrProtocolError):
        client.poll(challenge)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (requests.Timeout("synthetic timeout"), "network_failure"),
        (requests.ConnectionError("synthetic offline"), "network_failure"),
        (FakeResponse(envelope({}), status_code=412), "platform_412"),
        (FakeResponse(envelope({}), status_code=503), "network_failure"),
        (FakeResponse(ValueError("invalid json")), "protocol_error"),
        (
            FakeResponse(requests.exceptions.JSONDecodeError("invalid json", "{", 0)),
            "protocol_error",
        ),
    ],
)
def test_network_timeout_offline_412_and_json_errors_are_typed(response: object, code: str) -> None:
    client, _session = _client([response])
    with pytest.raises(auth_qr.QrLoginError) as raised:
        client.generate()
    assert raised.value.code == code


def test_cancel_and_close_invalidate_challenge_and_session() -> None:
    client, session = _client([FakeResponse(generate_payload())])
    challenge = client.generate()

    client.cancel()
    with pytest.raises(auth_qr.QrCancelled):
        client.poll(challenge)
    client.close()
    client.close()

    assert session.closed
    assert len(session.calls) == 1  # no poll request was issued after cancellation


def test_candidate_cookie_export_contains_values_but_not_unrelated_response_fields() -> None:
    def install_cookies(session: FakeSession) -> FakeResponse:
        session.cookies.set("SESSDATA", "session-secret", domain=".bilibili.com", path="/")
        session.cookies.set("DedeUserID", "user-secret", domain=".bilibili.com", path="/")
        return FakeResponse(poll_payload(0))

    client, _session = _client([FakeResponse(generate_payload()), install_cookies])
    challenge = client.generate()
    assert client.poll(challenge).status is auth_qr.QrStatus.SUCCESS

    cookies = client.candidate_cookies()

    assert {cookie["name"] for cookie in cookies} == {"SESSDATA", "DedeUserID"}
    assert {cookie["value"] for cookie in cookies} == {"session-secret", "user-secret"}
    assert all("refresh_token" not in cookie for cookie in cookies)


def test_qr_png_is_square_sharp_and_has_sufficient_quiet_zone() -> None:
    png = auth_qr.render_qr_png(QR_URL)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert width == height
    assert width >= 256
    assert len(png) < 200_000


def test_official_success_callback_query_is_opaque_and_never_used_for_cookies() -> None:
    callback = (
        "https://passport.biligame.com/x/passport-login/web/crossDomain?"
        "unknown_future_field=callback-secret&SESSDATA=callback-session&"
        "gourl=https%3A%2F%2Fattacker.example%2F"
    )

    def install_session_cookie(session: FakeSession) -> FakeResponse:
        session.cookies.set("SESSDATA", "jar-session", domain=".bilibili.com", path="/")
        session.cookies.set("DedeUserID", "jar-user", domain=".bilibili.com", path="/")
        return FakeResponse(poll_payload(0, url=callback))

    client, _session = _client([FakeResponse(generate_payload()), install_session_cookie])
    challenge = client.generate()

    assert client.poll(challenge).status is auth_qr.QrStatus.SUCCESS
    candidates = client.candidate_cookies()
    assert {item["value"] for item in candidates} == {"jar-session", "jar-user"}
    assert all("callback-secret" not in item["value"] for item in candidates)
