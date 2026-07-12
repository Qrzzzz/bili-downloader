from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .config import logs_dir


SENSITIVE_COOKIE_RE = re.compile(
    r"(?i)\b(SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|sid)\b(\s*[:=\t]\s*)([^;\s,\t]+)"
)
SENSITIVE_COOKIE_JSON_RE = re.compile(
    r'(?is)("name"\s*:\s*"(?:SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|sid)"[^{}]*?"value"\s*:\s*")([^"]*)(")'
)


def redact_sensitive(message: object) -> str:
    text = str(message)
    text = SENSITIVE_COOKIE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    return SENSITIVE_COOKIE_JSON_RE.sub(lambda m: f"{m.group(1)}<redacted>{m.group(3)}", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = record.getMessage()
        record.msg = redact_sensitive(original)
        record.args = ()
        return super().format(record)


class LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter) -> None:
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.message.emit(self.format(record))
        except RuntimeError:
            pass


class YtdlpQtLogger:
    def __init__(self, emitter: LogEmitter | None = None, logger: logging.Logger | None = None) -> None:
        self.emitter = emitter
        self.logger = logger or logging.getLogger("bili_downloader.ytdlp")

    def _emit(self, level: int, message: str) -> None:
        message = redact_sensitive(message)
        self.logger.log(level, message)
        if self.emitter is not None:
            self.emitter.message.emit(message)

    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            self.logger.debug(message)
        else:
            self._emit(logging.INFO, message)

    def info(self, message: str) -> None:
        self._emit(logging.INFO, message)

    def warning(self, message: str) -> None:
        self._emit(logging.WARNING, f"警告：{message}")

    def error(self, message: str) -> None:
        self._emit(logging.ERROR, f"错误：{message}")


def setup_logging(emitter: LogEmitter | None = None) -> logging.Logger:
    logger = logging.getLogger("bili_downloader")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = RedactingFormatter("%(asctime)s [%(levelname)s] %(message)s")

    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        log_file: Path = logs_dir() / "app.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if emitter and not any(isinstance(h, QtLogHandler) for h in logger.handlers):
        qt_handler = QtLogHandler(emitter)
        qt_handler.setFormatter(formatter)
        logger.addHandler(qt_handler)

    return logger
