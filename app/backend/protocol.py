from __future__ import annotations

import json
import re
from typing import Any

VERSION = 2
MAX_REQUEST_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _reject_constant(value: str) -> None:
    raise ProtocolError("invalid_request", "Non-finite JSON numbers are not supported.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("invalid_request", "Duplicate JSON fields are not supported.")
        result[key] = value
    return result


def decode_request(line: bytes) -> dict:
    if len(line) > MAX_REQUEST_BYTES:
        raise ProtocolError("message_too_large", "Request exceeds 64 KiB.")
    try:
        request = json.loads(line.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise ProtocolError("invalid_request", "Expected one UTF-8 JSON object per line.") from exc
    if not isinstance(request, dict) or set(request) != {"v", "type", "id", "method", "params"}:
        raise ProtocolError("invalid_request", "Invalid request envelope.")
    if type(request["v"]) is not int or request["v"] != VERSION:
        raise ProtocolError("protocol_mismatch", "IPC version 2 is required.")
    if request["type"] != "request" or not isinstance(request["id"], str) or not ID_PATTERN.fullmatch(request["id"]):
        raise ProtocolError("invalid_request", "Invalid request identity.")
    if not isinstance(request["method"], str) or not isinstance(request["params"], dict):
        raise ProtocolError("invalid_request", "Invalid method or parameters.")
    return request


def encode_message(message: dict) -> bytes:
    encoded = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message_too_large", "Response exceeds 16 MiB.")
    return encoded


def fields(params: dict, allowed: set[str], required: set[str] | None = None) -> None:
    if not set(params) <= allowed or not (required or set()) <= set(params):
        raise ProtocolError("invalid_params", "参数缺失或包含不支持的字段。")


def string(params: dict, name: str, *, default: str | None = None, limit: int = 8192) -> str:
    value = params.get(name, default)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
        raise ProtocolError("invalid_params", f"参数 {name} 无效。")
    return value
