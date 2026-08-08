"""LLM 上下文 Item 模型。

该模块是模型响应、Maisaka 历史和 Provider 请求投影共同使用的唯一上下文事实来源。
Provider wire payload 只以不可变 replay fragment 的形式附着在对应 Item 上。
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List, Mapping, Sequence, Tuple, TypeAlias

import json
import uuid


CONTEXT_ITEM_SCHEMA_VERSION = 1
PROVIDER_REPLAY_SCHEMA_VERSION = 1
SUPPORTED_IMAGE_FORMATS = ("jpg", "jpeg", "png", "webp", "gif")


class RoleType(str, Enum):
    """可直接投影为消息的上下文角色。"""

    System = "system"
    User = "user"
    Assistant = "assistant"
    Tool = "tool"


def _canonical_json_bytes(value: Any) -> bytes:
    """将 JSON 值编码为稳定且不可变的 UTF-8 bytes。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_provider_endpoint_fingerprint(client_type: str, base_url: str) -> str:
    """生成不包含鉴权信息的 Provider 端点指纹。"""

    normalized_base_url = str(base_url).strip().rstrip("/")
    payload = f"{str(client_type).strip()}\n{normalized_base_url}"
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextItemMeta:
    """单个上下文 Item 的稳定身份和关系元数据。"""

    item_id: str
    logical_turn_id: str | None
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("Context Item ID 不能为空")

    @classmethod
    def create(
        cls,
        *,
        logical_turn_id: str | None = None,
        timestamp: datetime | None = None,
        item_id: str | None = None,
    ) -> "ContextItemMeta":
        """创建带随机稳定 ID 的 Item 元数据。"""

        return cls(
            item_id=item_id or uuid.uuid4().hex,
            logical_turn_id=logical_turn_id,
            timestamp=timestamp or datetime.now(),
        )


@dataclass(frozen=True, slots=True)
class ContextTextPart:
    """不可变文本内容片段。"""

    text: str

    def __post_init__(self) -> None:
        if self.text == "":
            raise ValueError("文本内容片段不能为空字符串")


@dataclass(frozen=True, slots=True)
class ContextImagePart:
    """不可变 Base64 图片内容片段。"""

    image_format: str
    image_base64: str

    def __post_init__(self) -> None:
        normalized_format = self.image_format.lower()
        if normalized_format not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError("不受支持的图片格式")
        if not self.image_base64:
            raise ValueError("图片的 base64 编码不能为空")

    @property
    def normalized_image_format(self) -> str:
        """返回 Provider 通常接受的规范化图片格式。"""

        if self.image_format.lower() in {"jpg", "jpeg"}:
            return "jpeg"
        return self.image_format.lower()


@dataclass(frozen=True, slots=True)
class ContextRefusalPart:
    """模型拒答内容片段。"""

    refusal: str

    def __post_init__(self) -> None:
        if not self.refusal:
            raise ValueError("拒答内容不能为空")


ContextContentPart: TypeAlias = ContextTextPart | ContextImagePart | ContextRefusalPart


@dataclass(frozen=True, slots=True)
class ProviderScope:
    """Provider 原生 Item 可以安全回放的严格范围。"""

    schema_version: int
    client_type: str
    provider_name: str
    endpoint_fingerprint: str
    model_identifier: str

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError("Provider scope schema_version 必须为正整数")
        if not all(
            value.strip()
            for value in (
                self.client_type,
                self.provider_name,
                self.endpoint_fingerprint,
                self.model_identifier,
            )
        ):
            raise ValueError("Provider scope 字段不能为空")


@dataclass(frozen=True, slots=True)
class ProviderReplayFragment:
    """附着到单个 Item 的深层不可变 Provider wire payload。"""

    scope: ProviderScope
    payload_json: bytes
    payload_sha256: str

    def __post_init__(self) -> None:
        if not self.payload_json:
            raise ValueError("Provider replay payload 不能为空")
        actual_sha256 = sha256(self.payload_json).hexdigest()
        if actual_sha256 != self.payload_sha256:
            raise ValueError("Provider replay payload 校验失败")
        materialized = json.loads(self.payload_json.decode("utf-8"))
        if not isinstance(materialized, dict):
            raise ValueError("Provider replay payload 必须是 JSON object")

    @classmethod
    def from_payload(cls, scope: ProviderScope, payload: Mapping[str, Any]) -> "ProviderReplayFragment":
        """从 Provider Item 字典创建不可变回放片段。"""

        payload_json = _canonical_json_bytes(dict(payload))
        return cls(
            scope=scope,
            payload_json=payload_json,
            payload_sha256=sha256(payload_json).hexdigest(),
        )

    def materialize(self) -> Dict[str, Any]:
        """返回与内部 bytes 隔离的新 Provider Item 字典。"""

        payload = json.loads(self.payload_json.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Provider replay payload 必须是 JSON object")
        return payload

    @property
    def byte_size(self) -> int:
        """返回回放片段的实际字节数。"""

        return len(self.payload_json)


@dataclass(frozen=True, slots=True)
class ContextToolCall:
    """不可变工具调用；参数以规范 JSON bytes 保存。"""

    call_id: str
    func_name: str
    args_json: bytes
    extra_content_json: bytes | None = None

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("工具调用 ID 不能为空")
        if not self.func_name:
            raise ValueError("工具函数名称不能为空")
        args = json.loads(self.args_json.decode("utf-8"))
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON object")
        if self.extra_content_json is not None:
            extra_content = json.loads(self.extra_content_json.decode("utf-8"))
            if not isinstance(extra_content, dict):
                raise ValueError("工具额外内容必须是 JSON object")

    @classmethod
    def create(
        cls,
        *,
        call_id: str,
        func_name: str,
        args: Mapping[str, Any] | None = None,
        extra_content: Mapping[str, Any] | None = None,
    ) -> "ContextToolCall":
        """从普通映射创建不可变工具调用。"""

        return cls(
            call_id=call_id,
            func_name=func_name,
            args_json=_canonical_json_bytes(dict(args or {})),
            extra_content_json=None if extra_content is None else _canonical_json_bytes(dict(extra_content)),
        )

    def materialize_args(self) -> Dict[str, Any]:
        """返回独立的工具参数字典。"""

        args = json.loads(self.args_json.decode("utf-8"))
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON object")
        return args

    def materialize_extra_content(self) -> Dict[str, Any] | None:
        """返回独立的工具额外内容字典。"""

        if self.extra_content_json is None:
            return None
        extra_content = json.loads(self.extra_content_json.decode("utf-8"))
        if not isinstance(extra_content, dict):
            raise ValueError("工具额外内容必须是 JSON object")
        return extra_content


@dataclass(frozen=True, slots=True)
class SystemMessageItem:
    """系统消息 Item。"""

    meta: ContextItemMeta
    parts: Tuple[ContextContentPart, ...]

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("系统消息 Item 内容不能为空")

    @property
    def role(self) -> RoleType:
        return RoleType.System


@dataclass(frozen=True, slots=True)
class UserMessageItem:
    """用户消息 Item。"""

    meta: ContextItemMeta
    parts: Tuple[ContextContentPart, ...]

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("用户消息 Item 内容不能为空")

    @property
    def role(self) -> RoleType:
        return RoleType.User


class ReasoningRepresentation(str, Enum):
    """reasoning 的可读性与可移植语义。"""

    RAW_TEXT = "raw_text"
    SUMMARY = "summary"
    OPAQUE = "opaque"


@dataclass(frozen=True, slots=True)
class ReasoningItem:
    """独立 reasoning Item，不从属于 assistant message。"""

    meta: ContextItemMeta
    summary_parts: Tuple[str, ...] = ()
    text_parts: Tuple[str, ...] = ()
    representation: ReasoningRepresentation = ReasoningRepresentation.OPAQUE
    replay: ProviderReplayFragment | None = None

    def __post_init__(self) -> None:
        if any(part == "" for part in self.summary_parts + self.text_parts):
            raise ValueError("reasoning 内容片段不能为空字符串")
        if self.representation == ReasoningRepresentation.SUMMARY and not self.summary_parts:
            raise ValueError("SUMMARY reasoning 必须包含 summary_parts")
        if self.representation == ReasoningRepresentation.RAW_TEXT and not self.text_parts:
            raise ValueError("RAW_TEXT reasoning 必须包含 text_parts")


@dataclass(frozen=True, slots=True)
class AssistantMessageItem:
    """模型可见正文 Item。"""

    meta: ContextItemMeta
    parts: Tuple[ContextContentPart, ...]
    phase: str | None = None
    replay: ProviderReplayFragment | None = None

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("Assistant message Item 内容不能为空")

    @property
    def role(self) -> RoleType:
        return RoleType.Assistant


@dataclass(frozen=True, slots=True)
class FunctionCallItem:
    """模型函数调用 Item。"""

    meta: ContextItemMeta
    tool_call: ContextToolCall
    replay: ProviderReplayFragment | None = None

    def __post_init__(self) -> None:
        if not self.meta.logical_turn_id:
            raise ValueError(f"function call 必须具有 logical_turn_id: {self.tool_call.call_id}")


@dataclass(frozen=True, slots=True)
class FunctionCallOutputItem:
    """应用侧函数调用结果 Item。"""

    meta: ContextItemMeta
    call_id: str
    output: str
    tool_name: str = ""
    success: bool = True

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("工具结果的调用 ID 不能为空")
        if not self.meta.logical_turn_id:
            raise ValueError(f"function call output 必须具有 logical_turn_id: {self.call_id}")

    @property
    def role(self) -> RoleType:
        return RoleType.Tool

    @property
    def parts(self) -> Tuple[ContextContentPart, ...]:
        """提供工具结果的统一文本内容视图。"""

        return (ContextTextPart(self.output),) if self.output else ()


@dataclass(frozen=True, slots=True)
class ProviderActivityItem:
    """Provider 内置工具或活动 Item 的规范化投影。"""

    meta: ContextItemMeta
    provider_type: str
    call_id: str = ""
    status: str = ""
    display_summary: str = ""
    action_type: str = ""
    details: Tuple[str, ...] = ()
    source_count: int = 0
    replay: ProviderReplayFragment | None = None

    def __post_init__(self) -> None:
        if not self.provider_type:
            raise ValueError("Provider activity type 不能为空")
        if self.source_count < 0:
            raise ValueError("Provider activity source_count 不能为负数")


@dataclass(frozen=True, slots=True)
class ProviderOpaqueItem:
    """未知 Provider Item；仅保存展示摘要与可选原生回放片段。"""

    meta: ContextItemMeta
    provider_type: str
    display_summary: str = ""
    replay: ProviderReplayFragment | None = None

    def __post_init__(self) -> None:
        if not self.provider_type:
            raise ValueError("Provider opaque type 不能为空")


ContextItem: TypeAlias = (
    SystemMessageItem
    | UserMessageItem
    | ReasoningItem
    | AssistantMessageItem
    | FunctionCallItem
    | FunctionCallOutputItem
    | ProviderActivityItem
    | ProviderOpaqueItem
)


class ContextItemBuilder:
    """构建 system/user/assistant/function-call-output 输入 Item。"""

    def __init__(self) -> None:
        self.__role = RoleType.User
        self.__parts: List[ContextContentPart] = []
        self.__tool_call_id: str | None = None
        self.__tool_name = ""
        self.__meta = ContextItemMeta.create()

    def set_role(self, role: RoleType = RoleType.User) -> "ContextItemBuilder":
        self.__role = role
        return self

    def set_meta(self, meta: ContextItemMeta) -> "ContextItemBuilder":
        self.__meta = meta
        return self

    def add_text_part(self, text: str) -> "ContextItemBuilder":
        self.__parts.append(ContextTextPart(text))
        return self

    def add_text_content(self, text: str) -> "ContextItemBuilder":
        return self.add_text_part(text)

    def add_image_base64_part(
        self,
        image_format: str,
        image_base64: str,
        support_formats: Sequence[str] = SUPPORTED_IMAGE_FORMATS,
    ) -> "ContextItemBuilder":
        if image_format.lower() not in support_formats:
            raise ValueError("不受支持的图片格式")
        self.__parts.append(ContextImagePart(image_format=image_format, image_base64=image_base64))
        return self

    def add_image_content(
        self,
        image_format: str,
        image_base64: str,
        support_formats: Sequence[str] = SUPPORTED_IMAGE_FORMATS,
    ) -> "ContextItemBuilder":
        return self.add_image_base64_part(image_format, image_base64, support_formats)

    def set_tool_call_id(self, tool_call_id: str) -> "ContextItemBuilder":
        if self.__role != RoleType.Tool:
            raise ValueError("仅当角色为 Tool 时才能设置工具调用 ID")
        if not tool_call_id:
            raise ValueError("工具调用 ID 不能为空")
        self.__tool_call_id = tool_call_id
        return self

    def add_tool_call(self, tool_call_id: str) -> "ContextItemBuilder":
        return self.set_tool_call_id(tool_call_id)

    def set_tool_name(self, tool_name: str) -> "ContextItemBuilder":
        if self.__role != RoleType.Tool:
            raise ValueError("仅当角色为 Tool 时才能设置工具名称")
        self.__tool_name = tool_name
        return self

    def build(self) -> SystemMessageItem | UserMessageItem | AssistantMessageItem | FunctionCallOutputItem:
        parts = tuple(self.__parts)
        if self.__role == RoleType.System:
            return SystemMessageItem(meta=self.__meta, parts=parts)
        if self.__role == RoleType.User:
            return UserMessageItem(meta=self.__meta, parts=parts)
        if self.__role == RoleType.Assistant:
            return AssistantMessageItem(meta=self.__meta, parts=parts)
        if self.__tool_call_id is None:
            raise ValueError("Tool Item 缺少工具调用 ID")
        if any(not isinstance(part, ContextTextPart) for part in parts):
            raise ValueError("工具结果 Item 仅支持文本片段")
        return FunctionCallOutputItem(
            meta=self.__meta,
            call_id=self.__tool_call_id,
            output="".join(part.text for part in parts if isinstance(part, ContextTextPart)),
            tool_name=self.__tool_name,
        )


ModelOutputItem: TypeAlias = (
    ReasoningItem | AssistantMessageItem | FunctionCallItem | ProviderActivityItem | ProviderOpaqueItem
)


def get_item_text(item: ContextItem) -> str:
    """提取一个 Item 的可见文本，不混合 reasoning 与正文语义。"""

    if isinstance(item, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
        return "".join(
            part.text if isinstance(part, ContextTextPart) else part.refusal
            for part in item.parts
            if isinstance(part, (ContextTextPart, ContextRefusalPart))
        )
    if isinstance(item, ReasoningItem):
        parts = item.summary_parts or item.text_parts
        return "\n".join(parts)
    if isinstance(item, FunctionCallOutputItem):
        return item.output
    if isinstance(item, (ProviderActivityItem, ProviderOpaqueItem)):
        return item.display_summary
    return ""


def get_response_text(items: Sequence[ContextItem]) -> str:
    """按 Item 顺序拼接模型可见正文。"""

    return "".join(get_item_text(item) for item in items if isinstance(item, AssistantMessageItem))


def get_response_reasoning(items: Sequence[ContextItem]) -> str:
    """按 Item 顺序拼接可展示 reasoning。"""

    return "\n".join(text for item in items if isinstance(item, ReasoningItem) if (text := get_item_text(item)))


def get_response_tool_calls(items: Sequence[ContextItem]) -> List[ContextToolCall]:
    """按 Item 顺序提取模型函数调用。"""

    return [item.tool_call for item in items if isinstance(item, FunctionCallItem)]


def build_portable_output_items(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    tool_calls: Sequence[ContextToolCall] = (),
    logical_turn_id: str,
    timestamp: datetime | None = None,
) -> Tuple[ModelOutputItem, ...]:
    """将无原生 Item 数组的 Provider 响应冻结为确定顺序的通用输出 Items。"""

    if not logical_turn_id:
        raise ValueError("logical_turn_id 不能为空")
    active_timestamp = timestamp or datetime.now()
    items: List[ModelOutputItem] = []
    normalized_reasoning = str(reasoning or "").strip()
    normalized_content = str(content or "").strip()
    if normalized_reasoning:
        items.append(
            ReasoningItem(
                meta=ContextItemMeta.create(logical_turn_id=logical_turn_id, timestamp=active_timestamp),
                text_parts=(normalized_reasoning,),
                representation=ReasoningRepresentation.RAW_TEXT,
            )
        )
    if normalized_content:
        items.append(
            AssistantMessageItem(
                meta=ContextItemMeta.create(logical_turn_id=logical_turn_id, timestamp=active_timestamp),
                parts=(ContextTextPart(normalized_content),),
            )
        )
    for tool_call in tool_calls:
        items.append(
            FunctionCallItem(
                meta=ContextItemMeta.create(logical_turn_id=logical_turn_id, timestamp=active_timestamp),
                tool_call=tool_call,
            )
        )
    return tuple(items)


def bind_output_items_to_turn(
    items: Sequence[ModelOutputItem],
    logical_turn_id: str,
) -> Tuple[ModelOutputItem, ...]:
    """为一次模型输出的所有 Items 绑定所属逻辑工具轮次。"""

    if not logical_turn_id:
        raise ValueError("logical_turn_id 不能为空")
    return tuple(replace(item, meta=replace(item.meta, logical_turn_id=logical_turn_id)) for item in items)


def replace_output_projection(
    items: Sequence[ModelOutputItem],
    *,
    content: str | None = None,
    replace_content: bool = False,
    tool_calls: Sequence[ContextToolCall] = (),
    replace_tool_calls: bool = False,
) -> Tuple[ModelOutputItem, ...]:
    """将旧式正文/工具 Hook 修改映射回 Items，并只清除被替换 Item 的 replay。"""

    output = list(items)
    if not output and not (content or tool_calls):
        return ()

    template_meta = output[0].meta if output else ContextItemMeta.create()
    if replace_content:
        assistant_indexes = [index for index, item in enumerate(output) if isinstance(item, AssistantMessageItem)]
        insert_index = assistant_indexes[0] if assistant_indexes else len(output)
        if assistant_indexes:
            template_meta = output[assistant_indexes[0]].meta
        else:
            template_meta = replace(template_meta, item_id=uuid.uuid4().hex)
        output = [item for item in output if not isinstance(item, AssistantMessageItem)]
        normalized_content = str(content or "").strip()
        if normalized_content:
            output.insert(
                min(insert_index, len(output)),
                AssistantMessageItem(
                    meta=template_meta,
                    parts=(ContextTextPart(normalized_content),),
                    replay=None,
                ),
            )

    if replace_tool_calls:
        call_indexes = [index for index, item in enumerate(output) if isinstance(item, FunctionCallItem)]
        insert_index = call_indexes[0] if call_indexes else len(output)
        if call_indexes:
            template_meta = output[call_indexes[0]].meta
        output = [item for item in output if not isinstance(item, FunctionCallItem)]
        for offset, tool_call in enumerate(tool_calls):
            output.insert(
                min(insert_index + offset, len(output)),
                FunctionCallItem(
                    meta=replace(template_meta, item_id=uuid.uuid4().hex),
                    tool_call=tool_call,
                    replay=None,
                ),
            )

    return tuple(output)


def get_item_replay(item: ContextItem) -> ProviderReplayFragment | None:
    """读取支持 replay 的 Item 上的回放片段。"""

    if isinstance(
        item,
        (ReasoningItem, AssistantMessageItem, FunctionCallItem, ProviderActivityItem, ProviderOpaqueItem),
    ):
        return item.replay
    return None


def without_item_replay(item: ContextItem) -> ContextItem:
    """仅清除当前 Item 的 replay fragment。"""

    if get_item_replay(item) is None:
        return item
    if isinstance(
        item,
        (ReasoningItem, AssistantMessageItem, FunctionCallItem, ProviderActivityItem, ProviderOpaqueItem),
    ):
        return replace(item, replay=None)
    return item


__all__ = [
    "AssistantMessageItem",
    "CONTEXT_ITEM_SCHEMA_VERSION",
    "ContextContentPart",
    "ContextImagePart",
    "ContextItem",
    "ContextItemBuilder",
    "ContextItemMeta",
    "ContextTextPart",
    "ContextToolCall",
    "FunctionCallItem",
    "FunctionCallOutputItem",
    "ModelOutputItem",
    "PROVIDER_REPLAY_SCHEMA_VERSION",
    "ProviderActivityItem",
    "ProviderOpaqueItem",
    "ProviderReplayFragment",
    "ProviderScope",
    "ReasoningItem",
    "ReasoningRepresentation",
    "RoleType",
    "SystemMessageItem",
    "UserMessageItem",
    "get_item_replay",
    "get_item_text",
    "get_response_reasoning",
    "get_response_text",
    "get_response_tool_calls",
    "without_item_replay",
    "build_provider_endpoint_fingerprint",
    "bind_output_items_to_turn",
    "build_portable_output_items",
    "replace_output_projection",
]
