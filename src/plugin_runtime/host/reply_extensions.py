"""一次 reply 的插件扩展：准备生成要求、转换整组消息、统一交还主程序发送。"""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from uuid import uuid4

import asyncio
import base64
import json

from src.core.tooling import ToolAvailabilityContext

from .component_registry import ComponentTypes, ReplyExtensionEntry
from .component_timeout import resolve_component_rpc_timeout_ms
from .reply_extension_schema import validate_parameters

if TYPE_CHECKING:
    from .supervisor import PluginRunnerSupervisor


@dataclass
class ReplyExtensionTarget:
    supervisor: "PluginRunnerSupervisor"
    entry: ReplyExtensionEntry


def get_reply_extensions(
    context: Optional[ToolAvailabilityContext] = None,
) -> Dict[str, ReplyExtensionTarget]:
    """按当前聊天的组件开关和范围实时查询，不缓存插件生命周期。"""
    from src.plugin_runtime.integration import get_plugin_runtime_manager

    filters = asdict(context) if context is not None else {}
    filters.pop("stream_id", None)
    filters.pop("user_id", None)
    result: Dict[str, ReplyExtensionTarget] = {}
    for supervisor in get_plugin_runtime_manager().supervisors:
        for entry in supervisor.component_registry.get_components_by_type(
            ComponentTypes.REPLY_EXTENSION.value, **filters,
        ):
            if not isinstance(entry, ReplyExtensionEntry):
                raise TypeError(f"无效回复扩展组件：{entry.full_name}")
            if entry.full_name in result:
                raise ValueError(f"重复回复扩展命名空间：{entry.full_name}")
            result[entry.full_name] = ReplyExtensionTarget(supervisor, entry)
    return result


def build_reply_extensions_schema(context: Optional[ToolAvailabilityContext] = None) -> Optional[Dict[str, Any]]:
    entries = get_reply_extensions(context)
    if not entries:
        return None
    properties: Dict[str, Any] = {}
    for name in sorted(entries):
        entry = entries[name].entry
        properties[name] = deepcopy(entry.parameters_schema)
        properties[name]["description"] = entry.description
    return {
        "type": "object", "properties": properties, "additionalProperties": False,
        "description": "可选。按插件完整名称选择本次回复扩展。只填写要启用的扩展；未填写的插件不参与本次回复。",
    }


def _validate_segment(segment: Any) -> None:
    if not isinstance(segment, dict):
        raise ValueError("消息段必须是对象")
    kind = segment.get("type")
    data = segment.get("data")
    if kind == "text":
        if not isinstance(data, str) or not data:
            raise ValueError("text 消息段需要非空字符串 data")
    elif kind in {"image", "emoji", "voice"}:
        if not isinstance(data, str):
            raise ValueError(f"{kind} 消息段的 data 必须是字符串")
        if "binary_data_base64" in segment:
            binary = segment["binary_data_base64"]
            if not isinstance(binary, str) or not base64.b64decode(binary, validate=True):
                raise ValueError(f"{kind} 消息段需要有效且非空的 Base64 数据")
        elif kind == "voice" or not isinstance(segment.get("hash"), str) or not segment["hash"]:
            raise ValueError(f"{kind} 消息段缺少二进制数据")
    elif kind in {"at", "reply"}:
        key = "target_user_id" if kind == "at" else "target_message_id"
        if not isinstance(data, dict) or not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"{kind} 消息段缺少 {key}")
    elif kind in {"dict", "file"}:
        if not isinstance(data, dict) or not data:
            raise ValueError(f"{kind} 消息段需要非空对象 data")
    elif kind == "forward":
        if not isinstance(data, list) or not data:
            raise ValueError("forward 消息段需要非空节点列表")
        for node in data:
            if not isinstance(node, dict) or not isinstance(node.get("content"), list) or not node["content"]:
                raise ValueError("forward 节点缺少 content")
            for child in node["content"]:
                _validate_segment(child)
    else:
        raise ValueError(f"不支持的回复消息段类型：{kind}；自定义卡片请使用 dict")


def validate_reply_messages(messages: Any) -> List[Dict[str, Any]]:
    """拒绝空回复和畸形负载，避免消息转换器的兼容行为掩盖插件错误。"""
    if not isinstance(messages, list) or not messages:
        raise ValueError("回复扩展必须返回非空 messages 列表")
    json.dumps(messages, allow_nan=False)
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"segments", "quote_previous"}:
            raise ValueError("每条消息必须包含 segments 和 quote_previous")
        if type(message["quote_previous"]) is not bool:
            raise ValueError("quote_previous 必须是布尔值")
        if not isinstance(message["segments"], list) or not message["segments"]:
            raise ValueError("segments 必须是非空列表")
        for segment in message["segments"]:
            _validate_segment(segment)
    return deepcopy(messages)


@dataclass
class ReplyExtensionExecution:
    """调用局部状态；并发回复、同一目标多次回复也不共用参数。"""

    context: ToolAvailabilityContext
    reply_message_id: str
    call_id: str
    reply_id: str = field(default_factory=lambda: uuid4().hex)
    targets: List[ReplyExtensionTarget] = field(default_factory=list)
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    extra_prompt: str = ""

    async def prepare(self, options: Any) -> None:
        if not isinstance(options, dict):
            raise ValueError("plugin_options 必须是对象")
        if not options:
            return
        available = get_reply_extensions(self.context)
        # 所有参数先通过校验，再调用任一插件，避免部分执行后才发现其他插件参数错误。
        for name, parameters in options.items():
            if name not in available:
                raise ValueError(f"回复扩展 {name} 不存在、已停用或不适用于当前聊天")
            self.parameters[name] = await asyncio.to_thread(
                validate_parameters, parameters, available[name].entry.parameters_schema,
            )
            self.targets.append(available[name])
        self.targets.sort(key=lambda target: (target.entry.priority, target.entry.full_name))
        requirements: List[str] = []
        for target in self.targets:
            result = await self._invoke(target, "prepare", "", [])
            if set(result) - {"extra_prompt"} or not isinstance(result.get("extra_prompt", ""), str):
                raise ValueError(f"回复扩展 {target.entry.full_name} prepare 只能返回字符串 extra_prompt")
            if result.get("extra_prompt", "").strip():
                requirements.append(result["extra_prompt"].strip())
        self.extra_prompt = "\n\n".join(requirements)

    async def transform(self, text: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        current = await asyncio.to_thread(validate_reply_messages, messages)
        for target in self.targets:
            result = await self._invoke(target, "before_send", text, current)
            if set(result) - {"messages"}:
                raise ValueError(f"回复扩展 {target.entry.full_name} before_send 只能返回 messages")
            if "messages" in result:
                current = await asyncio.to_thread(validate_reply_messages, result["messages"])
        self.ensure_active()
        return current

    def ensure_active(self) -> None:
        for target in self.targets:
            self._assert_active(target)

    def _assert_active(self, target: ReplyExtensionTarget) -> None:
        name = target.entry.full_name
        active = get_reply_extensions(self.context).get(name)
        if active is None or active.entry is not target.entry or active.supervisor is not target.supervisor:
            raise ValueError(f"回复扩展 {name} 已停用、卸载或重新加载，请重新发起回复")

    async def _invoke(
        self, target: ReplyExtensionTarget, phase: str, text: str, messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        name = target.entry.full_name
        self._assert_active(target)
        timeout_ms = resolve_component_rpc_timeout_ms(target.entry.timeout_ms)
        try:
            response = await asyncio.wait_for(
                target.supervisor.invoke_plugin(
                    "plugin.invoke_reply_extension", target.entry.plugin_id, target.entry.name,
                    {
                        "phase": phase, "reply_id": self.reply_id, "call_id": self.call_id,
                        "session_id": self.context.session_id, "reply_message_id": self.reply_message_id,
                        "chat": asdict(self.context), "parameters": deepcopy(self.parameters[name]),
                        "text": text, "messages": deepcopy(messages),
                    }, timeout_ms=timeout_ms,
                ), timeout=timeout_ms / 1000,
            )
        except Exception as exc:
            raise RuntimeError(f"回复扩展 {name} {phase} 调用失败（超时上限 {timeout_ms}ms）：{exc}") from exc
        self._assert_active(target)
        if response.error:
            raise RuntimeError(f"回复扩展 {name} {phase} RPC 失败：{response.error}")
        payload = response.payload
        if payload.get("success") is not True:
            raise RuntimeError(f"回复扩展 {name} {phase} 执行失败：{payload.get('result')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"回复扩展 {name} {phase} 必须返回对象，空操作请返回 {{}}")
        return result
