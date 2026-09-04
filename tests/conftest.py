from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


@dataclass(frozen=True)
class IsolatedPaths:
    root: Path
    local: Path
    roaming: Path
    home: Path


def _purge_app_modules() -> None:
    for name in tuple(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def _close_app_log_handlers() -> None:
    names = ["bili_downloader"]
    names.extend(
        name
        for name in logging.Logger.manager.loggerDict
        if isinstance(name, str) and name.startswith("bili_downloader.")
    )
    for name in names:
        logger = logging.getLogger(name)
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def isolated_app_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsolatedPaths:
    """Isolate every app import from the real Windows profile."""

    root = tmp_path / "profile"
    local = root / "local"
    roaming = root / "roaming"
    home = root / "home"
    for path in (local, roaming, home):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    _close_app_log_handlers()
    _purge_app_modules()

    paths = IsolatedPaths(root, local, roaming, home)
    yield paths

    cookies = sys.modules.get("app.cookies")
    if cookies is not None and hasattr(cookies, "_set_protector_for_tests"):
        cookies._set_protector_for_tests(None, None)  # type: ignore[attr-defined]
    _close_app_log_handlers()
    _purge_app_modules()


@pytest.fixture
def isolated_paths(isolated_app_environment: IsolatedPaths) -> IsolatedPaths:
    return isolated_app_environment


@pytest.fixture
def synthetic_credentials() -> dict[str, object]:
    session_secret = "synthetic-session-secret-7f583f"
    user_secret = "synthetic-user-secret-3a91d2"
    csrf_secret = "synthetic-csrf-secret-a4291c"
    third_party_secret = "synthetic-third-party-secret-291b7e"
    origin_secret = "synthetic-origin-secret-8a5ff1"
    expires = 4_102_444_800
    cookies = [
        {
            "name": "SESSDATA",
            "value": session_secret,
            "domain": ".bilibili.com",
            "path": "/",
            "expires": expires,
            "secure": True,
            "httpOnly": True,
            "sameSite": "Lax",
            "unexpected": "must-not-be-persisted",
        },
        {
            "name": "DedeUserID",
            "value": user_secret,
            "domain": ".bilibili.com",
            "path": "/",
            "expires": expires,
            "secure": True,
        },
        {
            "name": "bili_jct",
            "value": csrf_secret,
            "domain": "api.bilibili.com",
            "path": "/",
            "expires": expires,
            "secure": True,
        },
        {
            "name": "third_party_session",
            "value": third_party_secret,
            "domain": ".example.com",
            "path": "/",
            "expires": expires,
        },
    ]
    return {
        "cookies": cookies,
        "session_secret": session_secret,
        "user_secret": user_secret,
        "csrf_secret": csrf_secret,
        "third_party_secret": third_party_secret,
        "origin_secret": origin_secret,
    }


@pytest.fixture
def session_modules(isolated_app_environment: IsolatedPaths) -> SimpleNamespace:
    """Import app modules only after profile isolation and install a reversible test protector."""

    config = importlib.import_module("app.config")
    logger = importlib.import_module("app.logger")
    cookies = importlib.import_module("app.cookies")
    prefix = b"BILI-TEST-PROTECTED-v1:"

    def protect(data: bytes) -> bytes:
        return prefix + bytes(value ^ 0xA5 for value in reversed(data))

    def unprotect(data: bytes) -> bytes:
        if not data.startswith(prefix):
            raise ValueError("invalid test protection envelope")
        body = data[len(prefix) :]
        return bytes(value ^ 0xA5 for value in reversed(body))

    cookies._set_protector_for_tests(protect, unprotect)
    try:
        yield SimpleNamespace(config=config, logger=logger, cookies=cookies)
    finally:
        cookies._set_protector_for_tests(None, None)
