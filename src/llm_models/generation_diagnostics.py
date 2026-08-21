"""Provider 调用诊断的统一脱敏工具。"""

from dataclasses import asdict, is_dataclass
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import base64
import binascii
import json
import re


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer_token",
    "cookie",
    "password",
    "proxy_authorization",
    "secret",
    "token",
    "x_api_key",
}
PRIVATE_PROVIDER_KEYS = {"encrypted_content", "thought_signature"}
BINARY_KEYS = {
    "audio",
    "audio_base64",
    "blob",
    "bytes",
    "data_base64",
    "file_data",
    "image_base64",
}
AUTH_QUERY_HINTS = ("auth", "key", "secret", "signature", "token")
LARGE_BINARY_THRESHOLD_BYTES = 64 * 1024
BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def _is_sensitive_key(key: str) -> bool:
    """识别精确凭证字段及常见 Provider 前缀变体。"""

    return key in SENSITIVE_KEYS or key.endswith(
        ("_api_key", "_access_token", "_auth_token", "_password", "_secret")
    )


def _binary_summary(value: str | bytes, *, media_type: str = "") -> dict[str, Any]:
    raw_bytes = value.encode("utf-8") if isinstance(value, str) else value
    result: dict[str, Any] = {
        "type": "omitted_binary",
        "size_bytes": len(raw_bytes),
        "sha256": sha256(raw_bytes).hexdigest(),
    }
    if media_type:
        result["media_type"] = media_type
    return result


def sanitize_diagnostic_url(value: str) -> str:
    """移除 URL userinfo 与疑似鉴权 query 参数值。"""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value

    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    query = urlencode(
        [
            (key, "[REDACTED]" if any(hint in key.lower() for hint in AUTH_QUERY_HINTS) else item_value)
            for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, hostname, parsed.path, query, parsed.fragment))


def _looks_like_large_base64(value: str) -> bool:
    normalized = value.strip()
    if len(normalized) < LARGE_BINARY_THRESHOLD_BYTES * 4 // 3:
        return False
    if len(normalized) % 4 != 0 or BASE64_PATTERN.fullmatch(normalized) is None:
        return False
    try:
        base64.b64decode(normalized, validate=True)
    except (ValueError, binascii.Error):
        return False
    return True


def sanitize_generation_diagnostic(value: Any, *, key: str = "") -> Any:
    """把任意 SDK/wire 值转换为 JSON-safe 且脱敏的诊断值。"""

    normalized_key = _normalize_key(key)
    if _is_sensitive_key(normalized_key):
        return "[REDACTED]"
    if normalized_key in PRIVATE_PROVIDER_KEYS:
        return "[仅在内存 replay fragment 中保留]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return sanitize_generation_diagnostic(value.value, key=key)
    if isinstance(value, bytes):
        return _binary_summary(value)
    if isinstance(value, str):
        normalized_value = value.strip()
        if normalized_value.startswith("data:") and ";base64," in normalized_value:
            header, encoded_value = normalized_value.split(",", maxsplit=1)
            media_type = header.removeprefix("data:").split(";", maxsplit=1)[0]
            return _binary_summary(encoded_value, media_type=media_type)
        if normalized_key in BINARY_KEYS or _looks_like_large_base64(normalized_value):
            return _binary_summary(value)
        if normalized_value.startswith(("http://", "https://")):
            return sanitize_diagnostic_url(value)
        return value
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_generation_diagnostic(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_generation_diagnostic(item, key=key) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize_generation_diagnostic(asdict(value), key=key)

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return sanitize_generation_diagnostic(model_dump(exclude_none=True), key=key)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return sanitize_generation_diagnostic(to_dict(), key=key)
    try:
        converted = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        return sanitize_generation_diagnostic(converted, key=key)
    except (TypeError, ValueError):
        return {
            "type": "diagnostic_object",
            "class": f"{value.__class__.__module__}.{value.__class__.__qualname__}",
        }


__all__ = ["sanitize_diagnostic_url", "sanitize_generation_diagnostic"]
