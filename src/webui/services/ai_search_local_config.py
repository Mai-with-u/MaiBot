"""WebUI AI 搜索读取本地配置当前值，并在交给模型前隐藏敏感内容。"""

from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit

from src.config.config import Config, ModelConfig, config_manager


LOCAL_CONFIG_MAX_READ_COUNT = 8
LOCAL_CONFIG_REDACTED_VALUE = "<已隐藏>"

# 字段名按下划线拆词后命中任意一项，即视为可能携带密钥，整个值都不会交给模型。
# 按词匹配而不是子串匹配，避免误伤 max_tokens、keywords、tokenizer_mode 这类普通字段。
_SENSITIVE_KEY_WORDS = frozenset(
    {
        "key",
        "keys",
        "token",
        "secret",
        "secrets",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "headers",
        "query",
        "env",
        "args",
    }
)


# ConfigBase 自带的内部字段，不属于用户配置
_INTERNAL_CONFIG_FIELDS = frozenset({"field_docs", "suppress_any_warning"})


def _split_key_words(key: str) -> List[str]:
    """把字段名拆成小写单词，兼容请求头风格的连字符写法。"""

    return key.lower().replace("-", "_").split("_")


def _strip_url_credentials(value: str) -> str:
    """去掉 URL 中可能携带凭据的用户信息、查询参数和片段。"""

    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def redact_config_value(key: str, value: Any) -> Any:
    """递归隐藏配置值中的敏感字段并剔除内部字段；未填写的敏感字段保留空值，便于判断是否漏填。"""

    key_words = _split_key_words(key)
    if not _SENSITIVE_KEY_WORDS.isdisjoint(key_words):
        return LOCAL_CONFIG_REDACTED_VALUE if value else value
    if isinstance(value, dict):
        return {
            item_key: redact_config_value(item_key, item_value)
            for item_key, item_value in value.items()
            if item_key not in _INTERNAL_CONFIG_FIELDS
        }
    if isinstance(value, list):
        return [redact_config_value(key, item) for item in value]
    if isinstance(value, str) and "url" in key_words:
        return _strip_url_credentials(value)
    return value


def _build_section_header(segments: List[str]) -> str:
    """把字段所在的配置表路径还原成 TOML 表头写法。"""

    table_segments = [segment for segment in segments if not segment.isdigit()]
    table_path = ".".join(table_segments)
    return f"[[{table_path}]]" if len(table_segments) != len(segments) else f"[{table_path}]"


def _read_config_path(path: str) -> Dict[str, Any]:
    """按点号路径读取单个配置节或字段的当前值。"""

    segments = path.split(".")
    root_key = segments[0]
    if root_key in Config.model_fields:
        source = "bot_config.toml"
        config: Config | ModelConfig = config_manager.get_global_config()
    elif root_key in ModelConfig.model_fields:
        source = "model_config.toml"
        config = config_manager.get_model_config()
    else:
        return {
            "title": path,
            "error": "配置路径不存在",
            "available": sorted([*Config.model_fields, *ModelConfig.model_fields]),
        }

    # 只导出命中的顶层配置节，并在下钻前先完成脱敏，保证任何路径都读不到原始密钥
    value = redact_config_value(root_key, config.model_dump(mode="json", include={root_key})[root_key])
    for depth, segment in enumerate(segments[1:], start=1):
        if value == LOCAL_CONFIG_REDACTED_VALUE:
            break
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        elif isinstance(value, list) and segment.isdigit() and int(segment) < len(value):
            value = value[int(segment)]
        else:
            available = sorted(value) if isinstance(value, dict) else []
            return {
                "title": path,
                "source": source,
                "error": f"配置路径不存在: {'.'.join(segments[: depth + 1])}",
                "available": available,
            }

    section_segments = segments if isinstance(value, dict) else segments[:-1]
    result: Dict[str, Any] = {"title": path, "source": source, "value": value}
    if section_segments:
        result["section"] = _build_section_header(section_segments)
    return result


def read_local_config(paths: Any) -> List[Dict[str, Any]]:
    """读取多个配置路径的当前值，忽略空路径和重复路径。"""

    if not isinstance(paths, list):
        return []
    normalized_paths = list(dict.fromkeys(path for raw_path in paths if (path := str(raw_path).strip().strip("."))))
    return [_read_config_path(path) for path in normalized_paths[:LOCAL_CONFIG_MAX_READ_COUNT]]
