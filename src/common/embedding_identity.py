"""与业务索引和宿主配置无关的文本嵌入模型身份计算。"""

from hashlib import sha256
from typing import Any, Dict

import json


def build_embedding_fingerprint(
    *,
    model: str,
    provider: str,
    model_identifier: str,
    base_url: str,
    dimension: int,
    dimension_request_mode: str,
    extra_params: Dict[str, Any],
) -> Dict[str, Any]:
    """按实际模型、服务地址、有效维度及语义参数生成稳定指纹。"""
    semantic_params = dict(extra_params)
    semantic_params.pop("dimensions", None)
    semantic_params.pop("output_dimensionality", None)
    payload = {
        "model": model,
        "provider": provider,
        "model_identifier": model_identifier,
        "base_url": base_url.rstrip("/"),
        "dimension": int(dimension),
        "dimension_request_mode": dimension_request_mode,
        "extra_params": semantic_params,
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "version": 2,
        "hash": f"sha256:{sha256(normalized.encode('utf-8')).hexdigest()}",
        "model": model,
        "provider": provider,
        "model_identifier": model_identifier,
        "base_url": payload["base_url"],
        "dimension": int(dimension),
        "dimension_request_mode": dimension_request_mode,
        "source": "observed",
    }
