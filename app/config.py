from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_DIR_NAME = "BiliDownloader"
CONFIG_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 1024 * 1024

_LOGGER = logging.getLogger("bili_downloader.config")
_CONFIG_LOCK = threading.RLock()
_LAST_CONFIG_DIAGNOSTICS: tuple[str, ...] = ()


class ConfigError(RuntimeError):
    """Base class for configuration failures."""


class ConfigValidationError(ConfigError, ValueError):
    """Raised when an in-memory configuration is not safe to use."""


class ConfigSaveError(ConfigError, OSError):
    """Raised when an otherwise valid configuration cannot be saved."""


def _default_download_dir() -> str:
    downloads = Path.home() / "Downloads"
    try:
        if downloads.exists() and not downloads.is_dir():
            return str(Path.home())
    except OSError:
        return str(Path.home())
    return str(downloads)


def validate_download_dir(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigValidationError("下载目录必须是文本路径。")

    candidate = value.strip()
    if not candidate:
        raise ConfigValidationError("下载目录不能为空。")
    if "\x00" in candidate:
        raise ConfigValidationError("下载目录包含无效的空字符。")

    try:
        normalized = os.path.abspath(os.path.expanduser(candidate))
        path = Path(normalized)
    except (OSError, TypeError, ValueError) as exc:
        raise ConfigValidationError("下载目录路径格式无效。") from exc

    try:
        if path.exists() and not path.is_dir():
            raise ConfigValidationError("下载目录指向了文件，而不是文件夹。")
    except OSError as exc:
        raise ConfigValidationError("无法检查下载目录路径。") from exc

    return str(path)


@dataclass
class AppConfig:
    download_dir: str = field(default_factory=_default_download_dir)
    schema_version: int = CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ConfigValidationError("配置版本必须是整数。")
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise ConfigValidationError(f"不支持的配置版本：{self.schema_version}。")
        self.download_dir = validate_download_dir(self.download_dir)


def app_data_dir() -> Path:
    """Return the current local application-data directory, creating it if needed."""

    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    base = Path(root) if root else Path.home() / ".config"
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_app_data_dir() -> Path:
    """Return the former roaming app-data location without creating it."""

    root = os.environ.get("APPDATA")
    base = Path(root) if root else Path.home() / ".config"
    return base / APP_DIR_NAME


def session_dir() -> Path:
    path = app_data_dir() / "session"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.json"


def config_diagnostics() -> tuple[str, ...]:
    """Return diagnostics produced by the most recent ``load_config`` call."""

    with _CONFIG_LOCK:
        return _LAST_CONFIG_DIAGNOSTICS


def _publish_diagnostics(diagnostics: list[str]) -> None:
    global _LAST_CONFIG_DIAGNOSTICS
    _LAST_CONFIG_DIAGNOSTICS = tuple(diagnostics)
    for diagnostic in diagnostics:
        _LOGGER.warning("配置诊断：%s", diagnostic)


def _fallback(diagnostics: list[str], reason: str) -> AppConfig:
    diagnostics.append(reason)
    _publish_diagnostics(diagnostics)
    return AppConfig()


def load_config() -> AppConfig:
    with _CONFIG_LOCK:
        diagnostics: list[str] = []
        try:
            path = config_path()
        except (OSError, TypeError, ValueError) as exc:
            return _fallback(
                diagnostics,
                f"无法访问应用配置目录（{type(exc).__name__}），已使用默认配置。",
            )

        try:
            if path.stat().st_size > MAX_CONFIG_BYTES:
                return _fallback(diagnostics, "config.json 过大，已使用默认配置。")
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            _publish_diagnostics(diagnostics)
            return AppConfig()
        except (OSError, UnicodeError) as exc:
            return _fallback(diagnostics, f"无法读取 config.json（{type(exc).__name__}），已使用默认配置。")

        try:
            data: Any = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return _fallback(diagnostics, f"config.json 不是有效 JSON（{type(exc).__name__}），已使用默认配置。")

        if not isinstance(data, dict):
            return _fallback(diagnostics, "config.json 顶层必须是对象，已使用默认配置。")

        schema = data.get("schema_version", 0)
        if isinstance(schema, bool) or not isinstance(schema, int):
            return _fallback(diagnostics, "schema_version 必须是整数，已使用默认配置。")
        if schema not in {0, CONFIG_SCHEMA_VERSION}:
            return _fallback(diagnostics, f"不支持 schema_version={schema}，已使用默认配置。")
        if schema == 0:
            diagnostics.append("检测到旧版无版本号配置，已按当前 schema 迁移。")

        unknown = sorted(set(data) - {"schema_version", "download_dir"})
        if unknown:
            diagnostics.append("已忽略未知配置项：" + "、".join(unknown))

        try:
            config = AppConfig(
                schema_version=CONFIG_SCHEMA_VERSION,
                download_dir=data.get("download_dir", _default_download_dir()),
            )
        except ConfigValidationError as exc:
            return _fallback(diagnostics, f"配置字段无效：{exc} 已使用默认配置。")

        _publish_diagnostics(diagnostics)
        return config


def save_config(config: AppConfig) -> None:
    if not isinstance(config, AppConfig):
        raise ConfigValidationError("只能保存 AppConfig 配置对象。")

    validated = AppConfig(**asdict(config))
    payload = json.dumps(asdict(validated), ensure_ascii=False, indent=2) + "\n"

    with _CONFIG_LOCK:
        try:
            path = config_path()
        except (OSError, TypeError, ValueError) as exc:
            raise ConfigSaveError(f"无法访问应用配置目录：{type(exc).__name__}。") from exc
        temporary: Path | None = None
        descriptor: int | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except (OSError, ValueError) as exc:
            raise ConfigSaveError(f"无法原子保存配置：{type(exc).__name__}。") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    _LOGGER.warning("无法清理配置临时文件：%s", temporary.name)
