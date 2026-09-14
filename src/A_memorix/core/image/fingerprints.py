from __future__ import annotations

from typing import Any, Dict

import hashlib
import json


def build_image_embedding_fingerprint(
    *,
    model: str,
    provider: str,
    dimension: int,
    preprocess_version: str,
    normalization: str = "l2",
    request_protocol_hash: str = "",
) -> Dict[str, Any]:
    """构造不含密钥的图片嵌入空间指纹。"""

    comparable = {
        "dimension": int(dimension),
        "modality": "image",
        "model": str(model or "").strip(),
        "normalization": str(normalization or "l2").strip(),
        "preprocess_version": str(preprocess_version or "identity_v1").strip(),
        "provider": str(provider or "").strip(),
        "version": 1,
    }
    if request_protocol_hash:
        comparable["request_protocol_hash"] = request_protocol_hash
    encoded = json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**comparable, "hash": f"sha256:{hashlib.sha256(encoded).hexdigest()}"}


def fingerprint_hash(fingerprint: Dict[str, Any]) -> str:
    return str((fingerprint or {}).get("hash") or "").strip()
