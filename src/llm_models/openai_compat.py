from dataclasses import dataclass, field
from typing import Any, Mapping

from src.config.model_configs import APIProvider, OpenAICompatibleAuthType


@dataclass(slots=True)
class OpenAICompatibleClientConfig:
    """OpenAI 兼容客户端的基础配置。"""

    api_key: str
    base_url: str
    default_headers: dict[str, str] = field(default_factory=dict)
    default_query: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class OpenAICompatibleRequestOverrides:
    """单次请求级别的附加配置。"""

    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_query: dict[str, object] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)


def normalize_openai_base_url(base_url: str) -> str:
    """规范化 OpenAI 兼容接口的基础地址。

    去掉尾部斜杠，且如果缺少协议前缀则自动补全 http://。

    Args:
        base_url: 原始基础地址。

    Returns:
        str: 规范化后的地址。
    """
    base_url = base_url.strip()
    if base_url and "://" not in base_url:
        base_url = "http://" + base_url
    return base_url.rstrip("/")


def _build_auth_header_value(prefix: str, api_key: str) -> str:
    """构造鉴权请求头的值。

    Args:
        prefix: 请求头前缀。
        api_key: 实际密钥。

    Returns:
        str: 拼接完成的请求头值。
    """
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return api_key
    return f"{normalized_prefix} {api_key}"


def build_openai_compatible_client_config(api_provider: APIProvider) -> OpenAICompatibleClientConfig:
    """构建 OpenAI 兼容客户端配置。

    Args:
        api_provider: API 提供商配置。

    Returns:
        OpenAICompatibleClientConfig: 可直接用于初始化 SDK 客户端的配置。
    """
    default_headers = dict(api_provider.default_headers)
    default_query: dict[str, object] = dict(api_provider.default_query)
    client_api_key = api_provider.api_key

    if api_provider.auth_type == OpenAICompatibleAuthType.BEARER:
        if (
            api_provider.auth_header_name != "Authorization"
            or api_provider.auth_header_prefix.strip() != "Bearer"
        ):
            client_api_key = ""
            default_headers[api_provider.auth_header_name] = _build_auth_header_value(
                prefix=api_provider.auth_header_prefix,
                api_key=api_provider.api_key,
            )
    elif api_provider.auth_type == OpenAICompatibleAuthType.HEADER:
        client_api_key = ""
        default_headers[api_provider.auth_header_name] = _build_auth_header_value(
            prefix=api_provider.auth_header_prefix,
            api_key=api_provider.api_key,
        )
    elif api_provider.auth_type == OpenAICompatibleAuthType.QUERY:
        client_api_key = ""
        default_query[api_provider.auth_query_name] = api_provider.api_key
    elif api_provider.auth_type == OpenAICompatibleAuthType.NONE:
        client_api_key = ""

    return OpenAICompatibleClientConfig(
        api_key=client_api_key,
        base_url=normalize_openai_base_url(api_provider.base_url),
        default_headers=default_headers,
        default_query=default_query,
    )


def _extract_mapping(value: Any) -> dict[str, Any]:
    """将任意映射值规范化为普通字典。

    Args:
        value: 原始输入值。

    Returns:
        dict[str, Any]: 规范化后的字典。非映射值时返回空字典。
    """
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def split_openai_request_overrides(
    extra_params: Mapping[str, Any] | None,
    *,
    reserved_body_keys: set[str] | None = None,
) -> OpenAICompatibleRequestOverrides:
    """拆分单次请求中的头、查询参数和请求体扩展字段。

    Args:
        extra_params: 模型级别或请求级别的附加参数。
        reserved_body_keys: 由 SDK 原生参数承载、因此不应再进入 `extra_body` 的字段集合。

    Returns:
        OpenAICompatibleRequestOverrides: 拆分后的请求覆盖配置。
    """
    raw_params = dict(extra_params or {})
    extra_headers = _extract_mapping(raw_params.pop("headers", None))
    extra_query = _extract_mapping(raw_params.pop("query", None))
    extra_body = _extract_mapping(raw_params.pop("body", None))
    blocked_body_keys = reserved_body_keys or set()

    for key, value in raw_params.items():
        if key in blocked_body_keys:
            continue
        extra_body[key] = value

    return OpenAICompatibleRequestOverrides(
        extra_headers={key: str(value) for key, value in extra_headers.items()},
        extra_query=extra_query,
        extra_body=extra_body,
    )
