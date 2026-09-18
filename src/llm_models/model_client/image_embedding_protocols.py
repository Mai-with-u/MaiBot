"""百炼与豆包官方图片嵌入协议的自动适配。

两个厂商的多模态图片嵌入接口不是 OpenAI 兼容协议：端点路径、请求体结构
和向量返回位置都与 `/embeddings` 不同。当 `client_type = "openai"` 的
Provider 地址指向官方域名时，图片嵌入请求由本模块自动切换到对应原生协议；
其他地址仍走 OpenAI 兼容模板逻辑（`image_embedding_input` /
`image_embedding_body`），互不影响。

协议来源：
- 百炼: https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference
- 豆包: https://docs.volcengine.com/docs/ark/vectorization?lang=zh
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple
from urllib.parse import urlsplit

import base64
import hashlib
import json
import math

from src.llm_models.openai_compat import normalize_openai_base_url

from .base_client import UsageTuple

DASHSCOPE_PROTOCOL = "dashscope"
"""阿里云百炼（DashScope）原生多模态嵌入协议标识。"""

ARK_PROTOCOL = "ark"
"""火山引擎方舟（Ark）原生多模态嵌入协议标识。"""

_DASHSCOPE_NATIVE_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
"""百炼原生多模态向量接口路径；兼容层(/compatible-mode/v1)与原生(/api/v1)地址统一挂到该路径。"""

_ARK_NATIVE_SUFFIX = "/embeddings/multimodal"
"""豆包原生多模态向量接口后缀；套餐(/api/plan/v3)与普通(/api/v3)网关都在各自基路径下提供该端点。"""

_DASHSCOPE_BASE_PATHS = {"/compatible-mode/v1", "/api/v1"}
"""百炼 Provider 地址允许的基路径（规范化后精确匹配）。"""

_ARK_BASE_PATHS = {"/api/v3", "/api/plan/v3"}
"""豆包 Provider 地址允许的基路径（规范化后精确匹配）。"""

_DASHSCOPE_RESERVED_BODY_KEYS = {"model", "input"}
"""由本次图片输入独占的百炼请求字段，禁止通过 extra_params 覆盖。"""

_ARK_RESERVED_BODY_KEYS = {"model", "input", "encoding_format"}
"""由本次图片输入独占的豆包请求字段；encoding_format 固定为 float。"""


@dataclass(frozen=True)
class NativeImageEmbeddingRequest:
    """一次原生图片嵌入请求的完整描述。"""

    protocol: str
    """命中的原生协议标识（`dashscope` / `ark`）。"""

    endpoint_url: str
    """原生接口完整 URL；SDK 侧会按绝对地址直接发送。"""

    payload: Dict[str, Any]
    """已编码图片的 JSON 请求体。"""

    semantic_params: Dict[str, Any]
    """影响向量空间的有效业务参数，用于计算协议指纹。"""

    extra_headers: Dict[str, str] = field(default_factory=dict)
    """extra_params.headers 透传的请求头。"""

    extra_query: Dict[str, Any] = field(default_factory=dict)
    """extra_params.query 透传的查询参数。"""


@dataclass(frozen=True)
class NativeImageEmbeddingResult:
    """原生图片嵌入响应的统一解析结果。"""

    embedding: List[float]
    """校验后的嵌入向量。"""

    usage: UsageTuple | None
    """统一使用量五元组；响应未提供 usage 时为 None。"""


def resolve_native_image_embedding_protocol(base_url: str) -> str | None:
    """识别官方百炼/豆包地址并返回对应原生协议；其他地址返回 None。

    匹配基于规范化后的 hostname 与路径精确比对，不做子串或子域名猜测；
    未识别的地址一律返回 None，由调用方继续走原有 OpenAI 兼容逻辑。
    """
    parsed = urlsplit(normalize_openai_base_url(str(base_url or "")))
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return None
    hostname = parsed.hostname.lower()
    path = parsed.path.rstrip("/")
    if hostname == "dashscope.aliyuncs.com" and path in _DASHSCOPE_BASE_PATHS:
        return DASHSCOPE_PROTOCOL
    if hostname == "ark.cn-beijing.volces.com" and path in _ARK_BASE_PATHS:
        return ARK_PROTOCOL
    return None


def build_native_image_embedding_request(
    protocol: str,
    *,
    base_url: str,
    model_identifier: str,
    image_bytes: bytes,
    mime_type: str,
    extra_params: Mapping[str, Any] | None = None,
) -> NativeImageEmbeddingRequest:
    """构造指定协议的原生图片嵌入请求。

    Args:
        protocol: `resolve_native_image_embedding_protocol` 返回的协议标识。
        base_url: Provider 基础地址；原生端点从它的 host/路径派生。
        model_identifier: 服务商侧模型标识。
        image_bytes: 原始图片字节，编码为 Data URI。
        mime_type: 图片 MIME 类型。
        extra_params: 模型额外参数；`headers`/`query` 转为请求覆盖，
            其余键并入请求体，与保留字段冲突时报错。

    Raises:
        ValueError: 协议未知，或 extra_params 试图覆盖保留字段。
    """
    extra = _normalize_extra_params(extra_params)
    extra_headers = {str(key): str(value) for key, value in _pop_mapping(extra, "headers").items()}
    extra_query = dict(_pop_mapping(extra, "query"))
    # 与 OpenAI 兼容路径保持一致：body 子对象视为请求体参数，同名普通键优先级更高
    body_mapping = _pop_mapping(extra, "body")
    extra = {**body_mapping, **extra}

    encoded = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type};base64,{encoded}"
    endpoint_url = _build_native_endpoint_url(base_url, protocol)
    if protocol == DASHSCOPE_PROTOCOL:
        payload, semantic_params = _build_dashscope_payload(extra, model_identifier, data_uri)
    elif protocol == ARK_PROTOCOL:
        payload, semantic_params = _build_ark_payload(extra, model_identifier, data_uri)
    else:
        raise ValueError(f"未知的图片嵌入原生协议: {protocol}")
    return NativeImageEmbeddingRequest(
        protocol=protocol,
        endpoint_url=endpoint_url,
        payload=payload,
        semantic_params=semantic_params,
        extra_headers=extra_headers,
        extra_query=extra_query,
    )


def _build_native_endpoint_url(base_url: str, protocol: str) -> str:
    """从 Provider 基础地址派生原生端点。

    豆包的套餐网关(/api/plan/v3)与普通网关(/api/v3)都在各自基路径下提供
    `/embeddings/multimodal`，必须保留原基路径，改写到 /api/v3 会因套餐
    Key 无普通 API 权限而 401；百炼的原生接口统一挂在 /api/v1 下，
    兼容层地址(/compatible-mode/v1)需要跨路径改写。
    """
    parsed = urlsplit(normalize_openai_base_url(str(base_url or "")))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if protocol == DASHSCOPE_PROTOCOL:
        return origin + _DASHSCOPE_NATIVE_PATH
    if protocol == ARK_PROTOCOL:
        return origin + parsed.path.rstrip("/") + _ARK_NATIVE_SUFFIX
    raise ValueError(f"未知的图片嵌入原生协议: {protocol}")


def parse_native_image_embedding_response(protocol: str, raw_response: Any) -> NativeImageEmbeddingResult:
    """按命中协议解析原生图片嵌入响应。

    Raises:
        ValueError: 响应结构不符合对应协议，或向量非法。
    """
    if not isinstance(raw_response, Mapping):
        raise ValueError("图片嵌入响应不是 JSON 对象")
    if protocol == DASHSCOPE_PROTOCOL:
        embedding = _extract_dashscope_embedding(raw_response)
        usage = _extract_dashscope_usage(raw_response.get("usage"))
    elif protocol == ARK_PROTOCOL:
        embedding = _extract_ark_embedding(raw_response)
        usage = _extract_ark_usage(raw_response.get("usage"))
    else:
        raise ValueError(f"未知的图片嵌入原生协议: {protocol}")
    return NativeImageEmbeddingResult(embedding=_validate_embedding_vector(embedding), usage=usage)


def build_native_image_embedding_fingerprint(request: NativeImageEmbeddingRequest, model_identifier: str) -> str:
    """计算原生图片嵌入的向量空间指纹。

    指纹覆盖协议版本、实际端点、模型标识与影响向量空间的有效参数，
    不包含图片内容或任何凭据；图片字节与密钥变化不会改变向量空间归属。
    """
    fingerprint_payload = {
        "endpoint": urlsplit(request.endpoint_url).path,
        "model": model_identifier,
        "protocol_version": f"{request.protocol}_image_embedding_v1",
        "semantic_params": request.semantic_params,
    }
    return hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_native_image_embedding_diagnostic_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """构造可写入失败快照的请求体副本，图片 Data URI 以占位符代替。"""

    return _redact_data_uris(payload)


def _build_dashscope_payload(
    extra: Dict[str, Any],
    model_identifier: str,
    data_uri: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """构造百炼原生请求体，返回 (payload, 指纹语义参数)。"""
    for key in _DASHSCOPE_RESERVED_BODY_KEYS:
        if key in extra:
            _reject_reserved_conflict(DASHSCOPE_PROTOCOL, key)
    raw_parameters = extra.pop("parameters", None)
    if raw_parameters is None:
        parameters: Dict[str, Any] = {}
    elif isinstance(raw_parameters, Mapping):
        parameters = dict(raw_parameters)
    else:
        raise ValueError("extra_params.parameters 必须是对象")
    # 百炼的维度字段是 parameters.dimension；兼容通用的顶层 dimensions 写法
    dimensions = extra.pop("dimensions", None)
    if dimensions is not None:
        if "dimension" in parameters and parameters["dimension"] != dimensions:
            raise ValueError("extra_params.parameters.dimension 与 extra_params.dimensions 冲突")
        parameters["dimension"] = dimensions
    payload: Dict[str, Any] = {
        "model": model_identifier,
        "input": {"contents": [{"image": data_uri}]},
    }
    if parameters:
        payload["parameters"] = parameters
    # 其余键按原生协议原样并入顶层，交由服务端校验
    payload.update(extra)
    semantic_params = dict(parameters)
    semantic_params.update(extra)
    return payload, semantic_params


def _build_ark_payload(
    extra: Dict[str, Any],
    model_identifier: str,
    data_uri: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """构造豆包原生请求体，返回 (payload, 指纹语义参数)。"""
    for key in _ARK_RESERVED_BODY_KEYS:
        if key in extra:
            _reject_reserved_conflict(ARK_PROTOCOL, key)
    payload: Dict[str, Any] = {
        "model": model_identifier,
        "input": [{"type": "image_url", "image_url": {"url": data_uri}}],
        "encoding_format": "float",
    }
    # 其余键（如 dimensions、instructions）按原生协议并入顶层
    payload.update(extra)
    return payload, dict(extra)


def _extract_dashscope_embedding(raw_response: Mapping[str, Any]) -> Any:
    output = raw_response.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("百炼图片嵌入响应缺少 output 对象")
    embeddings = output.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        raise ValueError("百炼图片嵌入响应缺少 output.embeddings")
    first = embeddings[0]
    if not isinstance(first, Mapping) or "embedding" not in first:
        raise ValueError("百炼图片嵌入响应的 output.embeddings[0] 缺少 embedding")
    return first["embedding"]


def _extract_ark_embedding(raw_response: Mapping[str, Any]) -> Any:
    # 豆包多模态嵌入的 data 是单个结果对象，与 OpenAI 的 data 列表不同
    data = raw_response.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("豆包图片嵌入响应缺少 data 对象")
    if "embedding" not in data:
        raise ValueError("豆包图片嵌入响应的 data 缺少 embedding")
    return data["embedding"]


def _extract_dashscope_usage(usage: Any) -> UsageTuple | None:
    # 百炼的 input_tokens 只统计文本部分，计费与请求总量以 total_tokens 为准
    if not isinstance(usage, Mapping):
        return None
    total_tokens = _as_non_negative_int(usage.get("total_tokens"))
    return (total_tokens, 0, total_tokens, 0, 0)


def _extract_ark_usage(usage: Any) -> UsageTuple | None:
    if not isinstance(usage, Mapping):
        return None
    total_tokens = _as_non_negative_int(usage.get("total_tokens"))
    return (total_tokens, 0, total_tokens, 0, 0)


def _as_non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _validate_embedding_vector(embedding: Any) -> List[float]:
    """校验向量非空且全部为有限数值，避免把异常响应写进索引。"""
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("图片嵌入响应的向量为空或不是列表")
    validated: List[float] = []
    for item in embedding:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError("图片嵌入响应的向量包含非有限数值")
        validated.append(float(item))
    return validated


def _redact_data_uris(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value:
            return f"<data URI 已省略，原始长度 {len(value)} 字符>"
        return value
    if isinstance(value, Mapping):
        return {str(key): _redact_data_uris(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_data_uris(item) for item in value]
    return value


def _normalize_extra_params(extra_params: Mapping[str, Any] | None) -> Dict[str, Any]:
    if extra_params is None:
        return {}
    if not isinstance(extra_params, Mapping):
        raise ValueError("extra_params 必须是对象")
    return dict(extra_params)


def _pop_mapping(extra: Dict[str, Any], key: str) -> Mapping[str, Any]:
    value = extra.pop(key, None)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"extra_params.{key} 必须是对象")
    return value


def _reject_reserved_conflict(protocol: str, key: str) -> None:
    raise ValueError(
        f"extra_params 不允许设置原生图片嵌入协议的保留字段 {key}（协议: {protocol}），"
        "该字段由本次请求的模型与图片输入独占"
    )
