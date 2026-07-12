from __future__ import annotations

import copy
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QObject, Signal

from .config import logs_dir


APP_LOG_MAX_BYTES = 2 * 1024 * 1024
APP_LOG_BACKUP_COUNT = 3
REDACTED = "<redacted>"

SENSITIVE_COOKIE_NAMES = (
    r"SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|sid|buvid3|buvid4|b_nut|"
    r"ac_time_value|access_key|refresh_token"
)
SENSITIVE_COOKIE_RE = re.compile(
    rf"(?i)\b({SENSITIVE_COOKIE_NAMES})\b(\s*[:=\t]\s*)([^;\s,\t]+)"
)
SENSITIVE_COOKIE_QUOTED_RE = re.compile(
    rf"(?i)\b({SENSITIVE_COOKIE_NAMES})\b(\s*[:=\t]\s*)([\"'])(.*?)(\3)"
)
SENSITIVE_COOKIE_JSON_RE = re.compile(
    rf"(?is)([\"']name[\"']\s*:\s*[\"'](?:{SENSITIVE_COOKIE_NAMES})[\"']"
    rf"[^{{}}]*?[\"']value[\"']\s*:\s*[\"'])(.*?)([\"'])"
)
SENSITIVE_COOKIE_JSON_REVERSED = re.compile(
    rf"(?is)([\"']value[\"']\s*:\s*[\"'])(.*?)([\"'][^{{}}]*?"
    rf"[\"']name[\"']\s*:\s*[\"'](?:{SENSITIVE_COOKIE_NAMES})[\"'])"
)
COOKIE_HEADER_RE = re.compile(r"(?im)\b(Cookie|Set-Cookie)(\s*:\s*)([^\r\n]+)")
URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    candidate = raw
    trailing = ""
    while candidate and candidate[-1] in ".,;!?)]}":
        trailing = candidate[-1] + trailing
        candidate = candidate[:-1]

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        base = re.split(r"[?#]", candidate, maxsplit=1)[0]
        query_marker = f"?{REDACTED}" if "?" in candidate else ""
        fragment_marker = f"#{REDACTED}" if "#" in candidate else ""
        return base + query_marker + fragment_marker + trailing

    netloc = parsed.netloc
    has_userinfo = "@" in netloc
    if has_userinfo:
        netloc = f"{REDACTED}@{netloc.rsplit('@', 1)[1]}"
    if not parsed.query and not parsed.fragment and not has_userinfo:
        return raw

    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    if parsed.query:
        sanitized += f"?{REDACTED}"
    if parsed.fragment:
        sanitized += f"#{REDACTED}"
    return sanitized + trailing


def redact_sensitive(message: object) -> str:
    text = str(message)
    text = COOKIE_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = SENSITIVE_COOKIE_JSON_RE.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", text)
    text = SENSITIVE_COOKIE_JSON_REVERSED.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", text)
    text = SENSITIVE_COOKIE_QUOTED_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}",
        text,
    )
    text = SENSITIVE_COOKIE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return URL_RE.sub(_redact_url, text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        safe_record.exc_text = None
        return redact_sensitive(super().format(safe_record))


class LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter) -> None:
        super().__init__()
        self.emitter = emitter

    def set_emitter(self, emitter: LogEmitter) -> None:
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
        if self.emitter is not None:
            self.emitter.message.emit(message)
        else:
            self.logger.log(level, message)

    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            self.logger.debug(redact_sensitive(message))
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

    log_file: Path = logs_dir() / "app.log"
    expected_path = log_file.resolve()
    file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
    file_handler = next(
        (
            handler
            for handler in file_handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename).resolve() == expected_path
        ),
        None,
    )
    if file_handler is None:
        for handler in file_handlers:
            logger.removeHandler(handler)
            handler.close()
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=APP_LOG_MAX_BYTES,
            backupCount=APP_LOG_BACKUP_COUNT,
            encoding="utf-8",
            errors="backslashreplace",
            delay=True,
        )
        logger.addHandler(file_handler)
    else:
        file_handler.maxBytes = APP_LOG_MAX_BYTES
        file_handler.backupCount = APP_LOG_BACKUP_COUNT
        for handler in file_handlers:
            if handler is file_handler:
                continue
            logger.removeHandler(handler)
            handler.close()
    file_handler.setFormatter(formatter)

    qt_handlers = [handler for handler in logger.handlers if isinstance(handler, QtLogHandler)]
    qt_handler = qt_handlers[0] if qt_handlers else None
    if emitter is not None:
        if qt_handler is None:
            qt_handler = QtLogHandler(emitter)
            logger.addHandler(qt_handler)
        else:
            qt_handler.set_emitter(emitter)
    if qt_handler is not None:
        qt_handler.setFormatter(formatter)
        for handler in qt_handlers[1:]:
            logger.removeHandler(handler)
            handler.close()

    return logger
