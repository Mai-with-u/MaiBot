"""将工具式 Planner 遗漏的动作补成标准工具调用。"""

from dataclasses import dataclass
from jsonschema import ValidationError, validate
from typing import Any, Dict, List, Optional, Sequence

import asyncio
import json
import time
import uuid

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.core.tooling import filter_tool_arguments
from src.llm_models.exceptions import RespParseException
from src.llm_models.payload_content.context_item import (
    ContextItem, ContextItemBuilder, ContextItemMeta, ContextToolCall, FunctionCallItem, RoleType,
    get_response_text, get_response_tool_calls,
)
from src.llm_models.payload_content.tool_option import ToolDefinitionInput, normalize_tool_options
from src.services.llm_service import LLMServiceClient

from .context.messages import LLMContextMessage, SessionBackedMessage

logger = get_logger("maisaka_planner_action_judge")

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
    result = await client.generate_response_with_context(
        context_factory=lambda _: messages,
        options=LLMGenerationOptions(
            temperature=0, interrupt_flag=interrupt_flag,
            tool_options=[tools["reply"], tools["wait"]],
        ),
    )

    # 文本工具调用的兼容解析及解析失败重试由 LLM 层统一处理。
    calls = get_response_tool_calls(result.output_items)
    if len(calls) != 1:
        raise RespParseException(message="Planner 动作判断器必须返回且仅返回一个 reply 或 wait 工具调用")
    action = calls[0].func_name
    arguments = calls[0].materialize_args()
    if action not in {"reply", "wait"}:
        raise RespParseException(message=f"Planner 动作判断器返回了无效工具：{action!r}")
    schema = tools[action].parameters_schema
    if filter_tool_arguments(arguments, schema) != arguments:
        raise RespParseException(message=f"Planner 动作判断器返回了未声明的工具参数：{arguments!r}")
    try:
        await asyncio.to_thread(validate, arguments, schema)
    except ValidationError as exc:
        raise RespParseException(message=f"Planner 动作判断器的 {action} 参数不符合工具声明：{exc.message}") from exc

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
