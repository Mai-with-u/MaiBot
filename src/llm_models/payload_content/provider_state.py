from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, List, Sequence

import json

from .tool_option import ToolCall


PROVIDER_STATE_SCHEMA_VERSION = 1
"""当前 Provider 原生状态结构版本。"""


@dataclass(slots=True)
class ProviderState:
    """模型提供商专用的连续请求状态。

    业务层只负责透明传递该状态，具体内容仅由对应模型客户端解释。
    """

    client_type: str
    provider_name: str
    endpoint_fingerprint: str
    model_identifier: str
    message_fingerprint: str
    output_items: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    schema_version: int = PROVIDER_STATE_SCHEMA_VERSION


def build_provider_endpoint_fingerprint(client_type: str, base_url: str) -> str:
    """生成不包含鉴权信息的 Provider 端点指纹。"""

    normalized_base_url = str(base_url).strip().rstrip("/")
    payload = f"{str(client_type).strip()}\n{normalized_base_url}"
    return sha256(payload.encode("utf-8")).hexdigest()


def build_assistant_message_fingerprint(
    content: str,
    tool_calls: Sequence[ToolCall] | None,
) -> str:
    """根据可移植的 assistant 内容生成稳定指纹。"""

    payload = {
        "content": str(content),
        "tool_calls": [
            {
                "args": tool_call.args or {},
                "call_id": tool_call.call_id,
                "name": tool_call.func_name,
            }
            for tool_call in (tool_calls or [])
        ],
    }
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical_payload.encode("utf-8")).hexdigest()
