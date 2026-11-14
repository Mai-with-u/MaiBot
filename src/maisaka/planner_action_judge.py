"""将工具式 Planner 遗漏的动作补成标准工具调用。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import asyncio
import json
import time
import uuid

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.core.tooling import filter_tool_arguments
from src.llm_models.exceptions import LLMTaskTimeoutError, RespParseException
from src.llm_models.payload_content.context_item import (
    ContextItem, ContextItemBuilder, ContextItemMeta, ContextToolCall, FunctionCallItem, RoleType,
    get_response_text, get_response_tool_calls,
)
from src.llm_models.payload_content.tool_option import ToolDefinitionInput, normalize_tool_options
from src.services.llm_service import LLMServiceClient

from .context.messages import LLMContextMessage, SessionBackedMessage

logger = get_logger("maisaka_planner_action_judge")

JUDGE_TIMEOUT_SECONDS = 10.0
JUDGE_SOURCE = "action_judge"


@dataclass(frozen=True)
class PlannerActionJudgment:
    """判断结果和独立请求开销，保留在 Planner 预览中。"""

    tool_call: FunctionCallItem
    metadata: Dict[str, Any]


def build_recent_chat_context(
    chat_history: Sequence[LLMContextMessage], *, include_session_ids: bool = False,
) -> List[Dict[str, str]]:
    """只取最近五条真实聊天消息，排除通知、工具和内部上下文。"""
    recent = []
    for message in reversed(chat_history):
        if not isinstance(message, SessionBackedMessage) or message.original_message.is_notify:
            continue
        entry = {"text": message.render_visible_text()}
        if include_session_ids:
            entry["session_id"] = message.original_message.session_id
        recent.append(entry)
        if len(recent) == 5:
            break
    return list(reversed(recent))


async def complete_missing_planner_action(
    output_items: Sequence[ContextItem],
    tool_definitions: Sequence[ToolDefinitionInput],
    *,
    session_id: str,
    logical_turn_id: Optional[str],
    chat_history: Sequence[LLMContextMessage] = (),
    interrupt_flag: Optional[asyncio.Event] = None,
) -> Optional[PlannerActionJudgment]:
    """仅对有正文、无工具调用的响应进行一次短分类，不重跑 Planner。"""
    content = get_response_text(output_items).strip()
    if not content or get_response_tool_calls(output_items):
        return None
    tools = {tool.name: tool for tool in normalize_tool_options(list(tool_definitions)) or []}
    if "reply" not in tools or "wait" not in tools:
        return None

    # Focus 没有默认聊天；用实际工具声明中的路由枚举限制目标，不猜测会话归属。
    reply_schema = tools["reply"].parameters_schema
    wait_schema = tools["wait"].parameters_schema
    reply_routes = (reply_schema or {}).get("properties", {}).get("session_id", {}).get("enum", [])
    wait_routes = (wait_schema or {}).get("properties", {}).get("session_id", {}).get("enum", [])
    routes = [sid for sid in reply_routes if sid in wait_routes]
    if (reply_routes or wait_routes) and not routes:
        raise ValueError("Planner 动作判断没有同时允许 reply 和 wait 的真实会话")

    payload: Dict[str, Any] = {
        "recent_chat": await asyncio.to_thread(build_recent_chat_context, chat_history, include_session_ids=bool(routes)),
        "planner_text": content,
    }
    judge_prompt = load_prompt("planner_action_judge")
    if routes:
        payload["reply_sessions"] = routes
        judge_prompt += "\n" + load_prompt("planner_action_judge_focus")
    messages = [
        ContextItemBuilder().set_role(RoleType.System)
        .add_text_content(judge_prompt).build(),
        ContextItemBuilder().set_role(RoleType.User)
        .add_text_content(json.dumps(payload, ensure_ascii=False)).build(),
    ]
    client = LLMServiceClient("fast_model", request_type="maisaka.planner_action_judge", session_id=session_id)
    started = time.perf_counter()
    try:
        async with asyncio.timeout(JUDGE_TIMEOUT_SECONDS):
            result = await client.generate_response_with_context(
                context_factory=lambda _: messages,
                options=LLMGenerationOptions(
                    temperature=0, interrupt_flag=interrupt_flag,
                ),
            )
    except TimeoutError as exc:
        raise LLMTaskTimeoutError("fast_model", "planner_action_judge", JUDGE_TIMEOUT_SECONDS) from exc

    decision = result.response.strip()
    if result.tool_calls or result.native_tool_calls:
        raise RespParseException(message="Planner 动作判断器应只返回 JSON 对象，不应调用工具")
    try:
        parsed = json.loads(decision)
    except json.JSONDecodeError as exc:
        raise RespParseException(message=f"Planner 动作判断器返回了无效 JSON：{decision!r}") from exc
    if not isinstance(parsed, dict) or "action" not in parsed:
        raise RespParseException(message=f"Planner 动作判断器返回了无效字段：{decision!r}")
    action = parsed["action"]
    decision_properties = {"action": {}}
    if action == "reply" and routes:
        decision_properties["session_id"] = {}
    parsed = filter_tool_arguments(parsed, {"properties": decision_properties})
    arguments: Dict[str, Any] = {}
    if action == "wait" and set(parsed) == {"action"}:
        if routes:
            arguments["session_id"] = routes[0]
    elif action == "reply" and not routes and set(parsed) == {"action"}:
        pass
    elif action == "reply" and routes and set(parsed) == {"action", "session_id"} and parsed["session_id"] in routes:
        arguments["session_id"] = parsed["session_id"]
    else:
        raise RespParseException(message=f"Planner 动作判断器返回了无效选项：{decision!r}")
    if action == "reply":
        arguments["chat_anlyze"] = content

    turn_id = logical_turn_id or next(
        (item.meta.logical_turn_id for item in output_items if item.meta.logical_turn_id), uuid.uuid4().hex,
    )
    call = FunctionCallItem(
        meta=ContextItemMeta.create(logical_turn_id=turn_id),
        tool_call=ContextToolCall.create(
            call_id="call_action_judge_" + uuid.uuid4().hex,
            func_name=action,
            args=arguments,
            extra_content={"tool_call_source": JUDGE_SOURCE, "tool_call_source_label": "轻量判断器补全"},
        ),
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "Planner 正文动作补全: session=%s action=%s call_id=%s model=%s 耗时=%.0fms",
        session_id, action, call.tool_call.call_id, result.model_name, duration_ms,
    )
    return PlannerActionJudgment(call, {
        "action": action, "model_name": result.model_name, "duration_ms": duration_ms,
        "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
    })
