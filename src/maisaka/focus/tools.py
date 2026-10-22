"""Focus 工具声明与路由：模型显式指定会话，原 Provider 继续接收原生参数。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jsonschema import ValidationError, validate

import asyncio
import json

from src.core.tooling import (
    ToolAvailabilityContext,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.maisaka.builtin_tool import get_builtin_tool_visibility, is_builtin_tool_in_action_stage

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from .group_runtime import FocusGroupRuntime


def availability_context(runtime: MaisakaHeartFlowChatting) -> ToolAvailabilityContext:
    stream = runtime.chat_stream
    return ToolAvailabilityContext(
        session_id=runtime.session_id,
        stream_id=runtime.session_id,
        is_group_chat=stream.is_group_session,
        group_id=stream.group_id or "",
        user_id=stream.user_id or "",
        platform=stream.platform,
    )


def execution_context(runtime: MaisakaHeartFlowChatting, reasoning: str) -> ToolExecutionContext:
    scope = availability_context(runtime)
    return ToolExecutionContext(
        session_id=scope.session_id,
        stream_id=scope.stream_id,
        reasoning=reasoning,
        is_group_chat=scope.is_group_chat,
        group_id=scope.group_id,
        user_id=scope.user_id,
        platform=scope.platform,
    )


def _identity(spec: ToolSpec) -> Tuple[str, str, str, str]:
    return (
        spec.provider_name,
        str(spec.metadata.get("registry_original_name", spec.name)),
        str(spec.metadata.get("plugin_id", "")),
        str(spec.metadata.get("server_name", "")),
    )


def _prefix_local_refs(value: Any, prefix: str) -> Any:
    """包装参数或合并定义时同步移动本地引用，避免 $defs 指向另一会话的 schema。"""
    if isinstance(value, list):
        return [_prefix_local_refs(item, prefix) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: prefix + item[1:]
        if key == "$ref" and isinstance(item, str) and item.startswith("#/")
        else _prefix_local_refs(item, prefix)
        for key, item in value.items()
    }


def routed_schema(spec: ToolSpec, session_id: str) -> Dict[str, Any]:
    native_schema = deepcopy(spec.parameters_schema or {"type": "object", "properties": {}})
    # MCP/插件可能本来就有名为 session_id 的业务参数；使用容器保留它的原义。
    if "session_id" in native_schema.get("properties", {}):
        schema = {
            "type": "object",
            "properties": {"tool_arguments": _prefix_local_refs(native_schema, "#/properties/tool_arguments")},
            "required": ["tool_arguments"],
            "additionalProperties": False,
        }
    else:
        schema = native_schema
    schema.setdefault("properties", {})["session_id"] = {
        "type": "string",
        "enum": [session_id],
        "description": "目标聊天的真实 session_id；必须从共享组会话列表或消息来源中选择。",
    }
    schema["required"] = list(dict.fromkeys([*schema.get("required", []), "session_id"]))
    return schema


@dataclass
class FocusToolRoute:
    spec: ToolSpec
    variants: Dict[str, ToolSpec]


class FocusToolRouter:
    def __init__(self, group: FocusGroupRuntime) -> None:
        self.group = group
        self.routes: Dict[str, FocusToolRoute] = {}

    async def refresh(self) -> List[ToolSpec]:
        """合并各会话的工具，保留权限差异与同名工具的来源身份。"""
        identities: Dict[Tuple[str, str, str, str], str] = {}
        routes: Dict[str, FocusToolRoute] = {}
        for session_id, runtime in list(self.group.members.items()):
            if not self.group.is_member_valid(runtime):
                continue
            specs = await runtime._tool_registry.list_tools(availability_context(runtime))
            for original in specs:
                if original.provider_name == "maisaka_builtin" and (
                    not is_builtin_tool_in_action_stage(original) or get_builtin_tool_visibility(original) == "hidden"
                ):
                    continue
                identity = _identity(original)
                name = identities.get(identity)
                if name is None:
                    name = original.name
                    if name in routes:
                        suffix = sha256(json.dumps(identity).encode()).hexdigest()[:12]
                        name = f"{name[:51]}_{suffix}"
                    identities[identity] = name
                    spec = deepcopy(original)
                    spec.name = name
                    routes[name] = FocusToolRoute(spec, {})
                routes[name].variants[session_id] = original
        self.routes = routes
        for route in routes.values():
            variants = [routed_schema(spec, session_id) for session_id, spec in route.variants.items()]
            native_schemas = [spec.parameters_schema for spec in route.variants.values()]
            if all(schema == native_schemas[0] for schema in native_schemas):
                route.spec.parameters_schema = variants[0]
                route.spec.parameters_schema["properties"]["session_id"]["enum"] = sorted(route.variants)
            else:
                # 对模型展示参数并集，执行时仍按目标会话严格校验。
                # 保持工具根层为普通 object；嵌套引用按变体隔离，避免定义同名覆盖。
                properties: Dict[str, List[Dict[str, Any]]] = {}
                definitions = {}
                for index, variant in enumerate(variants):
                    name = f"session_variant_{index}"
                    scoped = _prefix_local_refs(variant, f"#/$defs/{name}")
                    definitions[name] = scoped
                    for key, value in scoped.get("properties", {}).items():
                        if value not in properties.setdefault(key, []):
                            properties[key].append(value)
                schema = {
                    "type": "object",
                    "required": [
                        name
                        for name in variants[0]["required"]
                        if all(name in variant["required"] for variant in variants)
                    ],
                    "$defs": definitions,
                    "properties": {
                        key: values[0] if len(values) == 1 else {"anyOf": values} for key, values in properties.items()
                    },
                }
                schema["properties"]["session_id"] = {"type": "string", "enum": sorted(route.variants)}
                route.spec.parameters_schema = schema
        for session_id, runtime in self.group.members.items():
            runtime.update_deferred_tool_specs(
                [
                    route.spec
                    for route in routes.values()
                    if session_id in route.variants and not self.is_default_visible(route.spec)
                ]
            )
        return [route.spec for route in routes.values()]

    @staticmethod
    def is_default_visible(spec: ToolSpec) -> bool:
        if spec.provider_name == "maisaka_builtin":
            return is_builtin_tool_in_action_stage(spec) and get_builtin_tool_visibility(spec) == "visible"
        return spec.metadata.get("visibility") == "visible"

    def definitions(self) -> List[Dict[str, Any]]:
        definitions = []
        for name, route in self.routes.items():
            if self.is_default_visible(route.spec) or any(
                name in self.group.members[session_id].discovered_tool_names for session_id in route.variants
            ):
                definitions.append(route.spec.to_llm_definition())
        return definitions

    def deferred_notice(self) -> str:
        session_ids = {sid for sid, runtime in self.group.members.items() if self.group.is_member_valid(runtime)}
        lines = [
            f"- {name}: {route.spec.description}"
            + (f" (session_ids={','.join(route.variants)})" if set(route.variants) != session_ids else "")
            for name, route in self.routes.items()
            if not self.is_default_visible(route.spec)
        ]
        return "<deferred_tools>\n" + "\n".join(lines) + "\n</deferred_tools>" if lines else ""

    async def invoke(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        call_id: str,
        reasoning: str,
    ) -> Tuple[ToolInvocation, ToolExecutionResult, Optional[MaisakaHeartFlowChatting], Optional[ToolSpec]]:
        session_id = arguments.get("session_id")
        invocation = ToolInvocation(
            tool_name=tool_name,
            arguments=dict(arguments),
            call_id=call_id,
            session_id=session_id if isinstance(session_id, str) else "",
            stream_id=session_id if isinstance(session_id, str) else "",
            reasoning=reasoning,
        )

        def failure(reason: str):
            return invocation, ToolExecutionResult(tool_name=tool_name, success=False, error_message=reason), None, None

        if not isinstance(session_id, str) or not session_id.strip():
            return failure("每个 Focus 工具调用必须填写非空 session_id。")
        runtime = self.group.members.get(session_id)
        if runtime is None or not self.group.is_member_valid(runtime):
            return failure(f"session_id={session_id} 不属于当前有效共享组，不能执行工具。")
        route = self.routes.get(tool_name)
        original = route.variants.get(session_id) if route is not None else None
        if original is None:
            return failure(f"工具 {tool_name} 在目标聊天不可用，请先使用 tool_search 查询。")
        try:
            await asyncio.to_thread(validate, arguments, routed_schema(original, session_id))
        except ValidationError as exc:
            return failure(f"工具参数不符合目标聊天的声明：{exc.message}")
        current = await runtime._tool_registry.get_tool_spec(original.name, availability_context(runtime))
        if current is None or _identity(current) != _identity(original):
            return failure("工具来源或目标权限已经变化，请重新查询工具。")
        if "session_id" in (original.parameters_schema or {}).get("properties", {}):
            native_arguments = dict(arguments["tool_arguments"])
        else:
            native_arguments = {key: value for key, value in arguments.items() if key != "session_id"}
        native_arguments = runtime.expand_message_id_aliases(native_arguments)
        try:
            await asyncio.to_thread(validate, native_arguments, current.parameters_schema or {"type": "object"})
        except ValidationError as exc:
            return failure(f"工具参数声明已经变化：{exc.message}")
        invocation.arguments = native_arguments
        invocation.metadata["focus_scope_id"] = self.group.scope_id
        if tool_name in {"wait", "stop"} and original.provider_name == "maisaka_builtin":
            result = self.group.execute_control(tool_name, native_arguments, session_id)
        else:
            native = ToolInvocation(
                tool_name=original.name,
                arguments=native_arguments,
                call_id=call_id,
                session_id=session_id,
                stream_id=session_id,
                reasoning=reasoning,
                metadata=dict(invocation.metadata),
            )
            result = await runtime._tool_registry.invoke(native, execution_context(runtime, reasoning))
            result.tool_name = tool_name
        result.metadata["session_id"] = session_id
        return invocation, result, runtime, original
