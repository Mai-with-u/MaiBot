# ruff: noqa: E402

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(1, str(PROJECT_ROOT))

from src.config.config import config_manager
from src.llm_models.model_client.base_client import (
    AudioTranscriptionRequest,
    EmbeddingRequest,
    ResponseRequest,
    client_registry,
)
from src.llm_models.payload_content.context_item import get_item_text
from src.llm_models.request_snapshot import (
    deserialize_context_items_snapshot,
    deserialize_model_info_snapshot,
    deserialize_persisted_context_items_snapshot,
    deserialize_response_format_snapshot,
    deserialize_structured_context_items_snapshot,
    deserialize_tool_options_snapshot,
    read_structured_audio_base64,
)


def _load_snapshot(snapshot_path: Path) -> dict[str, Any]:
    """加载请求快照。"""
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _resolve_api_provider(provider_name: str):
    """根据名称解析当前配置中的 API Provider。"""
    model_config = config_manager.get_model_config()
    for api_provider in model_config.api_providers:
        if api_provider.name == provider_name:
            return api_provider
    raise ValueError(f"当前配置中不存在名为 {provider_name!r} 的 API Provider")


def _build_response_request(snapshot: dict[str, Any]) -> ResponseRequest:
    """从快照构建响应请求对象。"""
    return ResponseRequest(
        extra_params=dict(snapshot.get("extra_params") or {}),
        max_tokens=snapshot.get("max_tokens"),
        context_items=deserialize_context_items_snapshot(snapshot.get("context_items") or []),
        model_info=deserialize_model_info_snapshot(snapshot.get("model_info") or {}),
        response_format=deserialize_response_format_snapshot(snapshot.get("response_format")),
        temperature=snapshot.get("temperature"),
        tool_options=deserialize_tool_options_snapshot(snapshot.get("tool_options")),
    )


def _build_embedding_request(snapshot: dict[str, Any]) -> EmbeddingRequest:
    """从快照构建嵌入请求对象。"""
    return EmbeddingRequest(
        embedding_input=str(snapshot.get("embedding_input") or ""),
        extra_params=dict(snapshot.get("extra_params") or {}),
        model_info=deserialize_model_info_snapshot(snapshot.get("model_info") or {}),
    )


def _build_audio_request(snapshot: dict[str, Any]) -> AudioTranscriptionRequest:
    """从快照构建音频转写请求对象。"""
    return AudioTranscriptionRequest(
        audio_base64=str(snapshot.get("audio_base64") or ""),
        extra_params=dict(snapshot.get("extra_params") or {}),
        max_tokens=snapshot.get("max_tokens"),
        model_info=deserialize_model_info_snapshot(snapshot.get("model_info") or {}),
    )


def _build_unified_response_request(snapshot: dict[str, Any]) -> ResponseRequest:
    """从推理过程统一格式构建响应请求。"""

    request_parameters = dict(snapshot.get("request_parameters") or {})
    raw_request_items = snapshot.get("request_items")
    if isinstance(raw_request_items, list):
        context_items = deserialize_persisted_context_items_snapshot(raw_request_items)
    else:
        context_items = deserialize_structured_context_items_snapshot(snapshot.get("messages") or [])
    return ResponseRequest(
        extra_params=dict(request_parameters.get("extra_params") or {}),
        max_tokens=request_parameters.get("max_tokens"),
        context_items=context_items,
        model_info=deserialize_model_info_snapshot(snapshot.get("model_info") or {}),
        response_format=deserialize_response_format_snapshot(request_parameters.get("response_format")),
        temperature=request_parameters.get("temperature"),
        tool_options=deserialize_tool_options_snapshot(snapshot.get("tool_definitions")),
    )


def _build_unified_embedding_request(snapshot: dict[str, Any]) -> EmbeddingRequest:
    """从推理过程统一格式构建嵌入请求。"""

    request_parameters = dict(snapshot.get("request_parameters") or {})
    raw_request_items = snapshot.get("request_items")
    if isinstance(raw_request_items, list):
        request_items = deserialize_persisted_context_items_snapshot(raw_request_items)
        embedding_input = get_item_text(request_items[0]) if request_items else ""
    else:
        messages = snapshot.get("messages") or []
        first_message = messages[0] if isinstance(messages, list) and messages else {}
        embedding_input = str(first_message.get("content") or "") if isinstance(first_message, dict) else ""
    return EmbeddingRequest(
        embedding_input=embedding_input,
        extra_params=dict(request_parameters.get("extra_params") or {}),
        model_info=deserialize_model_info_snapshot(snapshot.get("model_info") or {}),
    )


def _build_unified_audio_request(snapshot: dict[str, Any]) -> AudioTranscriptionRequest:
    """从推理过程统一格式构建音频转写请求。"""

    request_parameters = dict(snapshot.get("request_parameters") or {})
    return AudioTranscriptionRequest(
        audio_base64=read_structured_audio_base64(snapshot.get("request_items") or snapshot.get("messages") or []),
        extra_params=dict(request_parameters.get("extra_params") or {}),
        max_tokens=request_parameters.get("max_tokens"),
        model_info=deserialize_model_info_snapshot(snapshot.get("model_info") or {}),
    )


async def _replay(snapshot_path: Path) -> int:
    """回放一条失败请求快照。"""
    config_manager.initialize()
    snapshot = _load_snapshot(snapshot_path)

    provider_snapshot = snapshot.get("api_provider")
    if not isinstance(provider_snapshot, dict):
        raise ValueError("快照缺少 api_provider 字段")

    provider_name = str(provider_snapshot.get("name") or "")
    if not provider_name:
        raise ValueError("快照中的 api_provider.name 不能为空")

    api_provider = _resolve_api_provider(provider_name)
    client = client_registry.get_client_class_instance(api_provider, force_new=True)

    internal_request = snapshot.get("internal_request")
    if isinstance(internal_request, dict):
        request_kind = str(internal_request.get("request_kind") or "").strip()
        if request_kind == "response":
            response = await client.get_response(_build_response_request(internal_request))
        elif request_kind == "embedding":
            response = await client.get_embedding(_build_embedding_request(internal_request))
        elif request_kind == "audio_transcription":
            response = await client.get_audio_transcriptions(_build_audio_request(internal_request))
        else:
            raise ValueError(f"不支持的 request_kind: {request_kind!r}")
    else:
        request_info = snapshot.get("request")
        if not isinstance(request_info, dict):
            raise ValueError("快照缺少 request 字段")
        request_kind = str(request_info.get("kind") or "").strip()
        if request_kind == "response":
            response = await client.get_response(_build_unified_response_request(snapshot))
        elif request_kind == "embedding":
            response = await client.get_embedding(_build_unified_embedding_request(snapshot))
        elif request_kind == "audio_transcription":
            response = await client.get_audio_transcriptions(_build_unified_audio_request(snapshot))
        else:
            raise ValueError(f"不支持的 request_kind: {request_kind!r}")

    output_payload = {
        "content": response.content,
        "embedding_length": len(response.embedding or []),
        "has_embedding": response.embedding is not None,
        "model_name": response.usage.model_name if response.usage is not None else None,
        "provider_name": response.usage.provider_name if response.usage is not None else None,
        "raw_data_type": type(response.raw_data).__name__ if response.raw_data is not None else None,
        "reasoning_content": response.reasoning_content,
        "tool_calls": [
            {
                "args": tool_call.args,
                "call_id": tool_call.call_id,
                "func_name": tool_call.func_name,
            }
            for tool_call in (response.tool_calls or [])
        ],
        "usage": {
            "completion_tokens": response.usage.completion_tokens,
            "prompt_tokens": response.usage.prompt_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        if response.usage is not None
        else None,
    }
    print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    """脚本入口。"""
    parser = argparse.ArgumentParser(description="回放失败的 LLM 请求快照。")
    parser.add_argument("snapshot_path", help="请求快照 JSON 文件路径")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot_path).expanduser().resolve()
    if not snapshot_path.exists():
        raise FileNotFoundError(f"快照文件不存在: {snapshot_path}")

    return asyncio.run(_replay(snapshot_path))


if __name__ == "__main__":
    raise SystemExit(main())
