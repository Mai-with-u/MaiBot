"""实验性 ACP Planner：外部决策，原聊天运行时执行工具。"""

from collections import Counter
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from jsonschema import validate

import asyncio
import hashlib
import json
import uuid

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.prompt_i18n import load_prompt
from src.common.paths import get_instance_root
from src.core.tooling import (
    ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec,
)
from src.llm_models.payload_content.context_item import ContextItem, SystemMessageItem
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput
from src.plugin_runtime.hook_payloads import serialize_prompt_items, serialize_tool_definitions

from .acp import ACPConnection, desktop_manifest, session_cwd
from .config import AgentConfig
from .mcp_server import ToolMCPServer
from .planner_state import PlannerState, content_keys, select_delta


@dataclass
class ACPPlannerSnapshot:
    """供滚动摘要复用成功请求的上下文和当时的外接配置。"""

    config: AgentConfig
    context_items: List[ContextItem]
    history_item_ids: List[str] = field(default_factory=list)

    def detached_snapshot(self) -> "ACPPlannerSnapshot":
        return deepcopy(self)


class PlannerDecisions:
    """MCP 回执仅确认决策提交，不能冒充尚未执行的工具结果。"""

    provider_name = "acp_planner"
    provider_type = "builtin"

    def __init__(self, definitions: Sequence[ToolDefinitionInput]) -> None:
        self.specs = {}
        for definition in serialize_tool_definitions(definitions):
            function = definition["function"]
            self.specs[function["name"]] = ToolSpec(
                name=function["name"], description=function.get("description", ""),
                parameters_schema=function["parameters"],
            )
        self.calls: List[ToolCall] = []
        self.active = False

    async def list_tools(self, context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
        return list(self.specs.values())

    async def invoke(
        self, invocation: ToolInvocation, context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        if not self.active:
            raise RuntimeError("Planner 回合已结束，不能提交工具决策")
        spec = self.specs[invocation.tool_name]
        validate(invocation.arguments, spec.parameters_schema)
        call = ToolCall(call_id=uuid.uuid4().hex, func_name=invocation.tool_name, args=invocation.arguments)
        self.calls.append(call)
        return ToolExecutionResult(
            tool_name=invocation.tool_name, success=True,
            content=f"Request {call.call_id} accepted, NOT executed. End this turn and wait for the actual result.",
        )

    async def close(self) -> None:
        self.active = False


def build_prompt(items: Sequence[ContextItem], policy: str, images_supported: bool) -> List[Dict[str, Any]]:
    """保留角色及工具结果结构，图片作为 ACP 原生内容发送，不把 base64 当文本。"""
    prompt: List[Dict[str, Any]] = [{"type": "text", "text": policy}] if policy else []
    for item in serialize_prompt_items(items):
        images = []
        for part in item.get("parts", []):
            if part["type"] == "image":
                if not images_supported:
                    raise ValueError("外接 Planner 未声明图片输入能力")
                images.append({"type": "image", "data": part.pop("image_base64"),
                               "mimeType": "image/" + part.pop("image_format")})
                part["text"] = "Image attached after this context item."
        prompt.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
        prompt.extend(images)
    return prompt


async def generate_planner_response(
    config: AgentConfig, session_id: str, items: Sequence[ContextItem], tools: Sequence[ToolDefinitionInput],
    *, history_item_ids: Sequence[str] = (),
) -> LLMResponseResult:
    """同一聊天串行恢复持久会话，连接释放不删除外部会话。"""
    state = PlannerState(session_id)
    async with state.lock:
        state.bind()
        try:
            await state.load()
            return await _generate_planner_response(config, session_id, items, tools, state, history_item_ids)
        finally:
            state.unbind()


async def _generate_planner_response(
    config: AgentConfig, session_id: str, items: Sequence[ContextItem], tools: Sequence[ToolDefinitionInput],
    state: PlannerState, history_item_ids: Sequence[str],
) -> LLMResponseResult:
    """增量输入与身份原子保存；失败和打断不提交工具决策。"""
    config = config.model_copy(deep=True)
    decisions = PlannerDecisions(tools)
    text: List[str] = []
    thoughts: List[str] = []
    external_session: Optional[str] = None

    async def update(params: Dict[str, Any]) -> None:
        if params["sessionId"] != external_session:
            return
        event = params["update"]
        kind = event["sessionUpdate"]
        if kind in {"agent_message_chunk", "agent_thought_chunk"} and event["content"]["type"] == "text":
            (text if kind == "agent_message_chunk" else thoughts).append(event["content"]["text"])

    connection = ACPConnection(update)
    mcp = ToolMCPServer([decisions], session_id, config.acp_timeout, port=config.mcp_port)
    cwd = Path(config.acp_cwd).expanduser() if config.acp_cwd else get_instance_root() / "data/agent/workspace"
    cwd = await asyncio.to_thread(cwd.resolve)
    remote_cwd = session_cwd(config, cwd)
    await asyncio.to_thread(cwd.mkdir, parents=True, exist_ok=True)
    policy = await asyncio.to_thread(
        load_prompt, "agent_planner",
    )
    manifest = desktop_manifest(config)
    identity = {"connection": config.connection, "cwd": str(cwd)}
    if config.acp_remote_cwd:
        identity["remote_cwd"] = remote_cwd
    if config.connection == "dsh_desktop":
        identity["manifest"] = str(manifest.absolute())
    else:
        identity["command"] = config.acp_command
        identity["args"] = config.acp_args if config.connection == "custom" else ["--profile", "acp"]
        identity["env_hash"] = hashlib.sha256(json.dumps(config.acp_env, sort_keys=True).encode()).hexdigest()
    if state.data.get("identity", identity) != identity:
        raise ValueError("外接 Planner 连接归属已改变，请恢复原连接配置，或清空聊天后建立新会话")
    delta, seen = await asyncio.to_thread(
        select_delta, items, state.data["seen"], state.data.get("system", []), history_item_ids,
        state.data.get("transient"),
    )
    system_keys = await asyncio.to_thread(content_keys, [item for item in items if isinstance(item, SystemMessageItem)])
    stable_ids = set(history_item_ids)
    transient = dict(Counter(await asyncio.to_thread(
        content_keys, [item for item in items if item.meta.item_id not in stable_ids],
    )))
    policy_key = hashlib.sha256(policy.encode()).hexdigest()
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(mcp.close)
        cleanup.push_async_callback(connection.close, 1)
        mount = await mcp.start()
        capabilities = await connection.initialize(config, cwd, "maibot-planner", mcp_port=mcp.port)
        if "resume" not in capabilities.get("sessionCapabilities", {}):
            raise ValueError("外接 Planner 需要 session/resume 能力以恢复持久会话")
        session_id = state.data.get("session_id")
        session = await connection.open_session(remote_cwd, mount, session_id, config.acp_timeout)
        external_session = session_id if session_id else session["sessionId"]
        if not session_id:
            state.data.update(session_id=external_session, identity=identity)
            await state.save()

        async def close_session() -> None:
            if connection.failure is None and "close" in capabilities.get("sessionCapabilities", {}):
                await connection.request("session/close", {"sessionId": external_session}, min(config.acp_timeout, 2))

        cleanup.push_async_callback(close_session)
        await connection.select_model(config, session, external_session)
        prompt = await asyncio.to_thread(
            build_prompt, delta, policy if policy_key != state.data["policy"] else "",
            capabilities.get("promptCapabilities", {}).get("image", False),
        )
        if state.data["pending"] is not None:
            notice = await asyncio.to_thread(
                load_prompt, "agent_planner_resume",
            )
            prompt.insert(0, {"type": "text", "text": notice})
        if not prompt:
            notice = await asyncio.to_thread(
                load_prompt, "agent_planner_continue",
            )
            prompt.append({"type": "text", "text": notice})
        # 先记录在途状态；崩溃后仅恢复上下文，不采用上次尚未交付的工具决策。
        state.data["pending"] = True
        await state.save()
        decisions.active = True
        task = asyncio.create_task(connection.request("session/prompt", {
            "sessionId": external_session, "prompt": prompt,
        }), name="planner.acp.prompt")
        try:
            result = await asyncio.shield(task)
            decisions.active = False
            if result["stopReason"] != "end_turn":
                raise RuntimeError(f"外接 Planner 未正常完成：{result['stopReason']}")
            response = LLMResponseResult.from_portable_output(
                response="".join(text), reasoning="".join(thoughts), tool_calls=decisions.calls,
                model_name="acp:" + (config.acp_model or config.connection),
            )
            # 外部会话已经拥有本轮输出，后续聊天历史回流时不重复追加。
            for key in await asyncio.to_thread(
                content_keys, response.output_items, [item.meta.item_id for item in response.output_items],
            ):
                seen[key] = seen.get(key, 0) + 1
            state.data.update(seen=seen, policy=policy_key, system=system_keys, transient=transient, pending=None)
            await state.save()
        except asyncio.CancelledError:
            decisions.active = False
            state.data["pending"] = True
            if not task.done():
                await connection.notify("session/cancel", {"sessionId": external_session})
            try:
                await asyncio.wait_for(asyncio.shield(task), min(config.acp_timeout, 2))
                state.data.update(seen=seen, policy=policy_key, system=system_keys, transient=transient)
                await state.save()
            except asyncio.TimeoutError:
                # 取消未获确认时关闭传输，不允许旧回合继续提交决策。
                await connection.close(1)
            raise
        finally:
            decisions.active = False
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return response
