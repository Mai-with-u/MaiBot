"""带会话来源的 Focus 时间线、完整轮次压缩与新版检查点。"""

from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import json
import os
import uuid

from src.config.config import PROJECT_ROOT
from src.common.data_models.message_component_data_model import ImageComponent, MessageSequence, TextComponent
from src.llm_models.payload_content import context_item as items
from src.maisaka.context.messages import (
    LLMContextMessage,
    ModelOutputContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
    ToolResultMessage,
    ToolResultMediaMessage,
    build_context_items_from_history_entry,
)
from src.maisaka.context.emoji_candidates import EmojiCandidateMessage

CHECKPOINT_VERSION = 1
_ITEM_TYPES = {
    name: cls
    for name, cls in vars(items).items()
    if isinstance(cls, type) and is_dataclass(cls) and cls.__module__ == items.__name__
}
_ENUM_TYPES = {
    enum_type.__name__: enum_type for enum_type in (items.RoleType, items.ReasoningRepresentation)
}
# 已保存的首版检查点只记录枚举值；这两类的取值互不重叠，可无损恢复现有记录。
_V1_ENUM_TYPES = {member.value: enum_type for enum_type in _ENUM_TYPES.values() for member in enum_type}


@dataclass
class RestoredContextMessage(LLMContextMessage):
    """恢复真实消息的模型投影；原始 IM 数据仍以消息库为准。"""

    item: items.ContextItem
    text: str
    source_kind: str
    counted: bool
    message_role: str

    @property
    def timestamp(self) -> datetime:
        return self.item.meta.timestamp

    @property
    def processed_plain_text(self) -> str:
        return self.text

    @property
    def source(self) -> str:
        return self.source_kind

    @property
    def count_in_context(self) -> bool:
        return self.counted

    @property
    def role(self) -> str:
        return self.message_role

    def to_context_item(self, enable_visual_message: bool = True) -> items.ContextItem:
        return self.item


@dataclass
class FocusContextEntry:
    """一个入站消息或一个完整的模型—工具批次，压缩时不可拆分。"""

    messages: List[LLMContextMessage]
    session_ids: Set[str]
    sequence: int = 0
    watermarks: Dict[str, int] = field(default_factory=dict)
    inputs: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)

    @property
    def progress(self) -> Dict[str, int]:
        return self.watermarks or ({sid: self.sequence for sid in self.session_ids} if self.sequence else {})

    @property
    def size(self) -> int:
        return sum(message.count_in_context for message in self.messages)

    def summary_text(self) -> str:
        parts = []
        for message in self.messages:
            text = message.processed_plain_text
            if isinstance(message, ModelOutputContextMessage) and message.tool_calls:
                text += "\n" + json.dumps(
                    [
                        {"tool": call.func_name, "arguments": call.args, "call_id": call.call_id}
                        for call in message.tool_calls
                    ],
                    ensure_ascii=False,
                )
            if isinstance(message, SessionBackedMessage):
                text = f"msg_id={message.message_id} time={message.timestamp.isoformat()} " + text
            parts.append(f"[{message.role}/{message.source}] {text}")
        return f"session_ids={json.dumps(sorted(self.session_ids), ensure_ascii=False)}\n" + "\n".join(parts)


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, Enum):
        return {"$enum": value.value, "$enum_type": type(value).__name__}
    if isinstance(value, bytes):
        return {"$bytes": b64encode(value).decode("ascii")}
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "fields": {field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$datetime" in value:
        return datetime.fromisoformat(value["$datetime"])
    if "$bytes" in value:
        return b64decode(value["$bytes"], validate=True)
    if "$enum" in value:
        enum_type = _ENUM_TYPES[value["$enum_type"]] if "$enum_type" in value else _V1_ENUM_TYPES[value["$enum"]]
        return enum_type(value["$enum"])
    if "$tuple" in value:
        return tuple(_decode(item) for item in value["$tuple"])
    if "$type" in value:
        # 只允许本项目 Context Item 类型，不导入检查点指定的类，也不使用 pickle。
        cls = _ITEM_TYPES[value["$type"]]
        return cls(**{key: _decode(item) for key, item in value["fields"].items()})
    return {key: _decode(item) for key, item in value.items()}


def serialize_message(message: LLMContextMessage) -> Dict[str, Any]:
    # 检查点保存的是恢复后直接使用的模型投影，必须与 Planner 请求采用同一表情包策略。
    context_items = build_context_items_from_history_entry(message, limit_emoji_size=True)
    record = {
        "item": _encode(context_items[0] if context_items else None),
        "text": message.processed_plain_text,
        "source": message.source,
        "counted": message.count_in_context,
        "role": message.role,
    }
    if isinstance(message, ModelOutputContextMessage):
        record["kind"] = "model"
        record["source_kind"] = message.source_kind
    elif isinstance(message, ToolResultMessage):
        record["kind"] = "tool"
        record["metadata"] = message.metadata
    elif isinstance(message, ToolResultMediaMessage):
        record["kind"] = "media"
        record["tool_call_id"] = message.tool_call_id
        record["message_id"] = message.message_id
        record["session_id"] = message.session_id
    elif isinstance(message, EmojiCandidateMessage):
        record["kind"] = "emoji"
        record["emoji_hashes"] = message.emoji_hashes
        record["session_id"] = message.session_id
    elif isinstance(message, ReferenceMessage):
        record["kind"] = "reference"
        record["content"] = message.content
        record["prefix"] = message.display_prefix
        record["reference_type"] = message.reference_type.value
    else:
        record["kind"] = "content"
    return record


def deserialize_message(record: Dict[str, Any]) -> LLMContextMessage:
    item = _decode(record["item"])
    if record["kind"] == "model":
        return ModelOutputContextMessage(output_item=item, source_kind=record["source_kind"])
    if record["kind"] == "tool":
        return ToolResultMessage(
            content=item.output,
            timestamp=item.meta.timestamp,
            tool_call_id=item.call_id,
            logical_turn_id=item.meta.logical_turn_id,
            tool_name=item.tool_name,
            success=item.success,
            metadata=record["metadata"],
            context_item_id=item.meta.item_id,
        )
    if record["kind"] == "reference":
        return ReferenceMessage(
            content=record["content"],
            timestamp=item.meta.timestamp,
            reference_type=ReferenceMessageType(record["reference_type"]),
            remaining_uses_value=None,
            display_prefix=record["prefix"],
            context_item_id=item.meta.item_id,
        )
    if record["kind"] == "emoji":
        return EmojiCandidateMessage(
            item=item,
            emoji_hashes={int(key): value for key, value in record["emoji_hashes"].items()},
            visible_text=record["text"],
            timestamp=item.meta.timestamp,
            session_id=record["session_id"],
        )
    if record["kind"] == "media":
        components = []
        for part in item.parts:
            if isinstance(part, items.ContextImagePart):
                components.append(ImageComponent(binary_hash="", binary_data=b64decode(part.image_base64)))
            elif isinstance(part, items.ContextTextPart):
                components.append(TextComponent(part.text))
        return ToolResultMediaMessage(
            tool_call_id=record["tool_call_id"],
            session_id=record["session_id"],
            message_id=record["message_id"],
            raw_message=MessageSequence(components),
            visible_text=record["text"],
            timestamp=item.meta.timestamp,
            context_item_id=item.meta.item_id,
        )
    return RestoredContextMessage(item, record["text"], record["source"], record["counted"], record["role"])


class FocusCheckpointStore:
    """仅保存新版状态；写入临时文件后原子替换，所有调用在工作线程执行。"""

    def __init__(self, scope_id: str, root: Path = PROJECT_ROOT / "data" / "focus") -> None:
        self.scope_id = scope_id
        self.path = root / (sha256(scope_id.encode()).hexdigest() + ".json")

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload["version"] != CHECKPOINT_VERSION or payload["scope_id"] != self.scope_id:
            raise ValueError("Focus 检查点版本或共享组身份不匹配")
        payload["entries"] = [
            FocusContextEntry(
                [deserialize_message(message) for message in entry["messages"]],
                set(entry["session_ids"]),
                entry["sequence"],
                entry["watermarks"],
                {sid: [tuple(pair) for pair in pairs] for sid, pairs in entry["inputs"].items()},
            )
            for entry in payload["entries"]
        ]
        return payload

    def save(self, entries: List[FocusContextEntry], state: Dict[str, Any]) -> None:
        payload = {
            "version": CHECKPOINT_VERSION,
            "scope_id": self.scope_id,
            **state,
            "entries": [
                {
                    "messages": [serialize_message(message) for message in entry.messages],
                    "session_ids": sorted(entry.session_ids),
                    "sequence": entry.sequence,
                    "watermarks": entry.watermarks,
                    "inputs": entry.inputs,
                }
                for entry in entries
            ],
        }
        self._write(payload)

    def _write(self, payload: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def purge_session(
        cls,
        session_id: str,
        excluded: Set[Path],
        root: Path = PROJECT_ROOT / "data" / "focus",
    ) -> None:
        """清理尚未加载的新版检查点，避免重启或重新启用旧分组时恢复已清空的材料。"""
        for path in root.glob("*.json"):
            if path in excluded:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload["version"] != CHECKPOINT_VERSION:
                raise ValueError("Focus 检查点版本不匹配")
            store = cls(payload["scope_id"], root)
            if store.path != path:
                raise ValueError("Focus 检查点路径与共享组身份不匹配")
            payload["entries"] = [entry for entry in payload["entries"] if session_id not in entry["session_ids"]]
            payload["considered"].pop(session_id, None)
            payload["seen"] = [key for key in payload["seen"] if not key.startswith(session_id + "\0")]
            if payload["wait_session_id"] == session_id:
                payload.update(wait_until=None, wait_session_id=None, wait_token=None, consecutive_waits=0)
            store._write(payload)
