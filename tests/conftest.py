from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import types
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


class _BoundSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)  # type: ignore[operator]


class _SignalDescriptor:
    def __init__(self, *_types: object) -> None:
        self.key = f"_test_signal_{id(self)}"

    def __get__(self, instance: object, owner: type[object]) -> object:
        if instance is None:
            return self
        namespace = vars(instance)
        return namespace.setdefault(self.key, _BoundSignal())


class _QObject:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass


def _install_qtcore_stub_if_needed() -> None:
    if "PySide6.QtCore" in sys.modules:
        return
    try:
        available = importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        available = False
    if available:
        return

    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.QObject = _QObject  # type: ignore[attr-defined]
    qtcore.Signal = _SignalDescriptor  # type: ignore[attr-defined]
    package = types.ModuleType("PySide6")
    package.__path__ = []  # type: ignore[attr-defined]
    package.QtCore = qtcore  # type: ignore[attr-defined]
    sys.modules["PySide6"] = package
    sys.modules["PySide6.QtCore"] = qtcore


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
    _install_qtcore_stub_if_needed()
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
