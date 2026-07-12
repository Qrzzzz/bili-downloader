from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class RecordingSignal:
    def __init__(self) -> None:
        self.values: list[str] = []

    def emit(self, value: str) -> None:
        self.values.append(value)


class RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def log(self, level: int, message: str) -> None:
        self.calls.append((level, message))

    def debug(self, message: str) -> None:
        self.calls.append((logging.DEBUG, message))


def test_full_traceback_cookie_and_url_redaction(session_modules: SimpleNamespace) -> None:
    logger_module = session_modules.logger
    cookie_secret = "traceback-cookie-secret-0df194"
    query_secret = "traceback-query-secret-781ca2"

    try:
        raise RuntimeError(
            f"Cookie: SESSDATA={cookie_secret}; bili_jct={cookie_secret}\n"
            f"https://www.bilibili.com/video/BV1TEST?p=3&token={query_secret}#private"
        )
    except RuntimeError:
        record = logging.LogRecord(
            "bili_downloader.test",
            logging.ERROR,
            __file__,
            1,
            "request failed",
            (),
            sys.exc_info(),
        )

    rendered = logger_module.RedactingFormatter("%(levelname)s %(message)s").format(record)

    assert "Traceback" in rendered
    assert cookie_secret not in rendered
    assert query_secret not in rendered
    assert "?<redacted>" in rendered and "#<redacted>" in rendered


def test_logging_handlers_are_deduplicated_and_bounded(
    session_modules: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger_module = session_modules.logger
    monkeypatch.setattr(logger_module, "APP_LOG_MAX_BYTES", 360)
    monkeypatch.setattr(logger_module, "APP_LOG_BACKUP_COUNT", 2)
    logger = logger_module.setup_logging()
    duplicate = logging.FileHandler(logger_module.logs_dir() / "duplicate.log", encoding="utf-8")
    logger.addHandler(duplicate)

    assert logger_module.setup_logging() is logger
    rotating = [
        handler for handler in logger.handlers if isinstance(handler, logger_module.RotatingFileHandler)
    ]
    assert len(rotating) == 1
    assert len([handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]) == 1

    for index in range(30):
        logger.info("bounded line %02d %s", index, "x" * 58)
    rotating[0].flush()
    files = list(logger_module.logs_dir().glob("app.log*"))

    assert 1 <= len(files) <= 3
    assert all(path.stat().st_size <= 360 for path in files)


def test_ytdlp_adapter_emits_once_when_ui_sink_is_present(session_modules: SimpleNamespace) -> None:
    logger_module = session_modules.logger
    signal = RecordingSignal()
    raw_logger = RecordingLogger()
    emitter = SimpleNamespace(message=signal)

    logger_module.YtdlpQtLogger(emitter=emitter, logger=raw_logger).info("single message")

    assert signal.values == ["single message"]
    assert raw_logger.calls == []


def test_crash_log_redacts_traceback_and_rotates(
    session_modules: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    crash = importlib.import_module("app.crash")
    cookie_secret = "crash-cookie-secret-c81d0a"
    query_secret = "crash-query-secret-1f82da"
    monkeypatch.setattr(crash, "CRASH_LOG_MAX_BYTES", 420)
    monkeypatch.setattr(crash, "CRASH_LOG_BACKUP_COUNT", 2)

    for index in range(14):
        try:
            raise RuntimeError(
                f"SESSDATA={cookie_secret} https://example.invalid/watch?token={query_secret}#private "
                + "界" * 70
            )
        except RuntimeError:
            exc_type, exc, tb = sys.exc_info()
            assert exc_type is not None and exc is not None
            crash.log_exception(exc_type, exc, tb, f"synthetic crash {index}")

    files = list(crash.logs_dir().glob("crash.log*"))
    combined = "".join(path.read_text(encoding="utf-8") for path in files)

    assert 1 <= len(files) <= 3
    assert all(path.stat().st_size <= 420 for path in files)
    assert cookie_secret not in combined
    assert query_secret not in combined


def test_invalid_config_falls_back_with_diagnostics_and_validates_types_and_paths(
    isolated_paths: object,
) -> None:
    import importlib

    config = importlib.import_module("app.config")
    path = config.config_path()
    path.write_text('{"schema_version": "bad", "download_dir": 42}', encoding="utf-8")

    loaded = config.load_config()

    assert loaded.schema_version == config.CONFIG_SCHEMA_VERSION
    assert config.config_diagnostics()
    assert any("schema_version" in item for item in config.config_diagnostics())
    with pytest.raises(config.ConfigValidationError):
        config.AppConfig(schema_version=config.CONFIG_SCHEMA_VERSION + 1)
    with pytest.raises(config.ConfigValidationError):
        config.AppConfig(download_dir=123)

    ordinary_file = isolated_paths.root / "not-a-directory.txt"  # type: ignore[attr-defined]
    ordinary_file.write_text("synthetic", encoding="utf-8")
    with pytest.raises(config.ConfigValidationError):
        config.AppConfig(download_dir=str(ordinary_file))


def test_config_atomic_save_retains_previous_file_on_replace_failure(
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    config = importlib.import_module("app.config")
    first_dir = isolated_paths.root / "downloads-one"  # type: ignore[attr-defined]
    second_dir = isolated_paths.root / "downloads-two"  # type: ignore[attr-defined]
    first_dir.mkdir()
    second_dir.mkdir()
    config.save_config(config.AppConfig(download_dir=str(first_dir)))
    path = config.config_path()
    previous = path.read_bytes()
    payload = json.loads(previous.decode("utf-8"))
    assert payload == {"download_dir": str(first_dir.resolve()), "schema_version": config.CONFIG_SCHEMA_VERSION}

    with monkeypatch.context() as scoped:
        scoped.setattr(config.os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fault")))
        with pytest.raises(config.ConfigSaveError, match="原子保存"):
            config.save_config(config.AppConfig(download_dir=str(second_dir)))

    assert path.read_bytes() == previous
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_config_directory_failure_falls_back_and_save_uses_typed_error(
    isolated_paths: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    config = importlib.import_module("app.config")
    monkeypatch.setattr(
        config,
        "app_data_dir",
        lambda: (_ for _ in ()).throw(PermissionError("synthetic config root denial")),
    )

    loaded = config.load_config()

    assert isinstance(loaded, config.AppConfig)
    assert any("无法访问应用配置目录" in item for item in config.config_diagnostics())
    with pytest.raises(config.ConfigSaveError, match="无法访问应用配置目录"):
        config.save_config(config.AppConfig(download_dir=str(isolated_paths.root)))  # type: ignore[attr-defined]
