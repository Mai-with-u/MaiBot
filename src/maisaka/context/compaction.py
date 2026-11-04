"""将较早的对话压缩为常驻的滚动状态，近期原文继续保留。"""

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple
from pydantic import BaseModel, ConfigDict, field_validator
import asyncio
import json

from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.agent.planner import ACPPlannerSnapshot, generate_planner_response
from src.common.data_models.message_component_data_model import ReplyComponent
from src.common.llm_json import validate_llm_json
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.config.config import global_config
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.request_snapshot import serialize_tool_options_snapshot
from src.llm_models.payload_content.context_item import ContextItem, ContextItemBuilder, RoleType
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.services.llm_service import LLMServiceClient

from .messages import (
    LLMContextMessage,
    ModelOutputContextMessage,
    ReferenceMessage,
    SessionBackedMessage,
    ToolResultMessage,
)

logger = get_logger("maisaka_context_compaction")
STATE_LABELS: Dict[str, str] = {
    "topics": "当前话题",
    "background": "关键背景",
    "progress": "进展与结论",
    "pending": "未完事项",
    "agreements": "对话约定",
}


class ConversationState(BaseModel):
    """可直接接续对话的状态；字段缺失或格式错误时拒绝提交压缩。"""

    model_config = ConfigDict(extra="forbid")

    topics: List[str]
    background: List[str]
    progress: List[str]
    pending: List[str]
    agreements: List[str]

    @field_validator("*")
    @classmethod
    def clean_entries(cls, entries: List[str]) -> List[str]:
        return list(dict.fromkeys(entry.strip() for entry in entries if entry.strip()))

    def render(self) -> str:
        data = self.model_dump()
        return "\n\n".join(
            f"{label}:\n" + "\n".join(f"- {entry}" for entry in data[key])
            for key, label in STATE_LABELS.items()
            if data[key]
        )


@dataclass(slots=True)
class ContextSummaryMessage(ReferenceMessage):
    """常驻 Planner 的内部状态，不伪装成用户消息，也不占近期消息条数。"""

    remaining_uses_value: int | None = None
    display_prefix: str = "【上下文状态摘要-内部参考】"

    @property
    def source(self) -> str:
        return "context_compaction"


def message_text_for_compaction(message: LLMContextMessage) -> str:
    """将消息、工具参数和执行结果编码为资料，不重放工具调用或模型内部推理。"""

    if isinstance(message, ModelOutputContextMessage):
        if message.role in {"reasoning", "provider_activity", "provider_opaque"}:
            return ""
        calls = message.tool_calls
        if calls:
            return "\n".join(
                json.dumps(
                    {"kind": "tool_call", "call_id": call.call_id, "tool": call.func_name, "args": call.args},
                    ensure_ascii=False,
                )
                for call in calls
            )
    if isinstance(message, ToolResultMessage):
        return json.dumps(
            {
                "kind": "tool_result",
                "call_id": message.tool_call_id,
                "tool": message.tool_name,
                "success": message.success,
                "content": message.content,
            },
            ensure_ascii=False,
        )
    if isinstance(message, SessionBackedMessage):
        user_info = message.original_message.message_info.user_info
        return json.dumps(
            {
                "kind": message.source,
                "message_id": message.message_id,
                "session_id": message.original_message.session_id,
                "speaker": user_info.user_cardname or user_info.user_nickname or user_info.user_id,
                "quotes": [
                    component.target_message_id
                    for component in message.media_sequence.components
                    if isinstance(component, ReplyComponent)
                ],
                "time": message.timestamp.isoformat(),
                "content": message.processed_plain_text,
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "kind": message.source,
            "role": message.role,
            "time": message.timestamp.isoformat(),
            "content": message.processed_plain_text,
        },
        ensure_ascii=False,
    )


@dataclass(frozen=True, slots=True)
class PlannerRequestSnapshot:
    """实际成功的 Planner 请求及其已覆盖的历史对象，仅驻留内存。"""

    request: ResponseRequest | ACPPlannerSnapshot
    history: Tuple[LLMContextMessage, ...]


def save_compaction_prompt_preview(
    request_messages: Sequence[ContextItem],
    *,
    result: LLMResponseResult,
    session_id: str,
) -> None:
    """保存压缩输入与输出，沿用终端显示和 WebUI 记录各自的开关。"""

    if not global_config.debug.save_maisaka_reasoning:
        return
    PromptCLIVisualizer.build_prompt_preview_access(
        list(request_messages),
        category="context_compaction",
        chat_id=session_id,
        request_kind="context_compaction",
        selection_reason="滚动状态摘要（Planner 请求快照与任务配置）",
        tool_definitions=(
            serialize_tool_options_snapshot(result.request_snapshot.tool_options)
            if result.request_snapshot is not None else None
        ),
        output_items=list(result.output_items),
        metadata={
            "model_name": result.model_name,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "prompt_cache_hit_tokens": result.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": result.prompt_cache_miss_tokens,
            "prompt_cache_reported": result.prompt_cache_reported,
            "tool_choice": result.request_snapshot.tool_choice if result.request_snapshot is not None else None,
            "request_source": "planner_snapshot",
        },
        generation_attempts=result.generation_attempts,
    )


def _build_compaction_request(
    removed_messages: Sequence[LLMContextMessage],
    *,
    planner_snapshot: PlannerRequestSnapshot,
    previous_summary: ContextSummaryMessage | None,
    recent_messages: Sequence[LLMContextMessage],
    source_context: str = "",
) -> ResponseRequest | ACPPlannerSnapshot:
    """在线程池构造完整请求，序列化大工具结果和复制图片不占事件循环。"""
    if not removed_messages:
        raise ValueError("没有可压缩的历史内容")
    covered = {id(message) for message in planner_snapshot.history}
    messages = [*removed_messages, *recent_messages]
    if previous_summary is not None:
        messages.insert(0, previous_summary)
    # 未覆盖资料也可能是首次请求未选中的更早消息；保留时间和角色，避免重放未闭合的工具调用。
    seen = set(covered)
    delta = []
    for message in messages:
        if id(message) not in seen:
            seen.add(id(message))
            text = message_text_for_compaction(message)
            if text:
                delta.append(text)
    # 共享聊天携带来源资料并使用独立提示词，普通聊天不注入多会话规则。
    prompt_name = "context_compaction_focus" if source_context else "context_compaction"
    instruction = load_prompt(prompt_name)
    tail_parts = [
        "<additional_conversation_data>", *delta, "</additional_conversation_data>",
    ]
    if source_context:
        tail_parts.extend(["<conversation_sources>", source_context, "</conversation_sources>"])
    tail = "\n".join([*tail_parts, instruction])
    request = planner_snapshot.request.detached_snapshot()
    request.context_items.append(ContextItemBuilder().set_role(RoleType.User).add_text_content(tail).build())
    # 原生请求的工具定义、顺序和调用模式会影响前缀，全部继承 Planner；ACP 摘要不挂载工具。
    # 压缩仅消费摘要文本，不进入宿主工具执行流程；含工具调用的响应会被拒收。
    return request


async def build_context_summary(
    removed_messages: Sequence[LLMContextMessage],
    *,
    planner_snapshot: PlannerRequestSnapshot,
    previous_summary: ContextSummaryMessage | None,
    recent_messages: Sequence[LLMContextMessage],
    session_id: str,
    source_context: str = "",
) -> ContextSummaryMessage:
    """保留 Planner 的完整请求前缀，仅追加未覆盖资料和压缩指令。"""
    request = await asyncio.to_thread(
        _build_compaction_request, removed_messages, planner_snapshot=planner_snapshot,
        previous_summary=previous_summary, recent_messages=recent_messages,
        source_context=source_context,
    )
    if isinstance(request, ACPPlannerSnapshot):
        result = await generate_planner_response(
            request.config, session_id, request.context_items, [], history_item_ids=request.history_item_ids,
        )
    else:
        client = LLMServiceClient(task_name="planner", request_type="maisaka.context_compaction", session_id=session_id)
        result = await client.generate_response_from_snapshot(request)
    try:
        await asyncio.to_thread(
            save_compaction_prompt_preview, request.context_items, result=result, session_id=session_id,
        )
    except Exception:
        logger.exception("上下文压缩请求记录保存失败")
    if result.tool_calls or result.native_tool_calls:
        raise ValueError("压缩响应包含被禁止的工具调用，拒绝提交摘要")
    rendered = validate_llm_json(result.response, ConversationState).render()
    if not rendered:
        raise ValueError("状态摘要为空")
    logger.info(
        f"上下文压缩缓存: session={session_id} model={result.model_name} "
        f"input={result.prompt_tokens} hit={result.prompt_cache_hit_tokens} "
        f"miss={result.prompt_cache_miss_tokens} reported={result.prompt_cache_reported}"
    )
    return ContextSummaryMessage(content=rendered, timestamp=removed_messages[-1].timestamp)
