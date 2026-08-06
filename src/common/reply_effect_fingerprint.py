"""回复生成请求与稳定 Prompt 版本指纹。"""

from hashlib import sha256
from typing import Any, Dict, Iterable

import json

PROMPT_VERSION_FINGERPRINT_SCHEMA = 3
_CHAT_EXTRA_ATTENTION_MARKER = "当前聊天额外注意事项："
_ATTENTION_BLOCK_HEADER = "在该聊天中的注意事项："
_OUTPUT_INSTRUCTION_MARKERS = (
    "请注意不要输出多余内容",
    "Please do not output any extra content",
    "余計な内容",
)


def extract_generation_fingerprints(metadata: Dict[str, Any]) -> tuple[str, str, str]:
    """提取模型名、完整请求指纹与稳定 Prompt 版本指纹。"""

    monitor = metadata.get("monitor_detail")
    monitor = monitor if isinstance(monitor, dict) else {}
    metrics = monitor.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    model_name = str(metrics.get("model_name") or "").strip()
    prompt_payload = monitor.get("request_messages") or monitor.get("prompt_text") or ""
    return (
        model_name,
        fingerprint_request_payload(prompt_payload),
        fingerprint_prompt_version(prompt_payload),
    )


def fingerprint_request_payload(prompt_payload: Any) -> str:
    """计算包含动态上下文的完整实际请求指纹。"""

    if not prompt_payload:
        return ""
    serialized = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


def fingerprint_prompt_version(prompt_payload: Any) -> str:
    """只根据实际请求中的 System Prompt 内容计算稳定版本指纹。"""

    if not isinstance(prompt_payload, list):
        return ""
    system_messages = [
        _normalize_system_prompt_for_version(message)
        for message in _iter_system_message_text(prompt_payload)
    ]
    if not system_messages:
        return ""
    stable_payload = {
        "schema": PROMPT_VERSION_FINGERPRINT_SCHEMA,
        "system_messages": system_messages,
    }
    serialized = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


def extract_system_prompt_from_metadata(metadata: Dict[str, Any]) -> str:
    """从回复生成监控元数据中提取真实发送的 System Prompt。"""

    monitor = metadata.get("monitor_detail")
    monitor = monitor if isinstance(monitor, dict) else {}
    prompt_payload = monitor.get("request_messages")
    if not isinstance(prompt_payload, list):
        return ""
    return "\n\n".join(_iter_system_message_text(prompt_payload))


def _normalize_system_prompt_for_version(system_prompt: str) -> str:
    """剔除聊天流专属要求及排版差异，避免误判 Prompt 版本。"""

    marker_index = system_prompt.find(_CHAT_EXTRA_ATTENTION_MARKER)
    if marker_index < 0:
        return _normalize_prompt_whitespace(system_prompt)
    suffix_indexes = [
        index
        for marker in _OUTPUT_INSTRUCTION_MARKERS
        if (index := system_prompt.find(marker, marker_index)) >= 0
    ]
    suffix_index = min(suffix_indexes) if suffix_indexes else len(system_prompt)
    prefix = system_prompt[:marker_index].rstrip()
    if prefix.endswith(_ATTENTION_BLOCK_HEADER):
        prefix = prefix[: -len(_ATTENTION_BLOCK_HEADER)].rstrip()
    suffix = system_prompt[suffix_index:].lstrip()
    normalized_prompt = "\n".join(part for part in (prefix, suffix) if part)
    return _normalize_prompt_whitespace(normalized_prompt)


def _normalize_prompt_whitespace(system_prompt: str) -> str:
    """版本指纹只关心文字内容，不区分空格、换行等排版形式。"""

    return " ".join(system_prompt.split())


def _iter_system_message_text(prompt_payload: Iterable[Any]) -> Iterable[str]:
    """忽略每次请求变化的 item ID、时间戳和聊天消息，只保留系统文本。"""

    for raw_item in prompt_payload:
        if not isinstance(raw_item, dict):
            continue
        item_type = str(raw_item.get("item_type") or raw_item.get("type") or "")
        role = str(raw_item.get("role") or "")
        if item_type != "SystemMessageItem" and role.lower() != "system":
            continue
        parts = raw_item.get("parts")
        if not isinstance(parts, list):
            continue
        text_parts = [
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and str(part.get("type") or "") in {"text", "input_text"}
        ]
        normalized_text = "\n".join(text_parts).strip()
        if normalized_text:
            yield normalized_text
