"""reply 内置工具。"""

from typing import Any, Optional
import traceback

from src.chat.replyer.replyer_manager import replyer_manager
from src.cli.maisaka_cli_sender import CLI_PLATFORM_NAME, render_cli_message
from src.common.data_models.reply_generation_data_models import ReplyGenerationResult, build_reply_monitor_detail
from src.common.logger import get_logger
from src.config import config as config_module
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.context.message_adapter import build_visible_text_from_sequence
from src.services import send_service

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_reply")
_REPLY_TOOL_INTERNAL_ARGUMENTS = {"msg_id", "set_quote"}


def _use_expression_intent() -> bool:
    return config_module.global_config.expression.expression_selection_mode == "vector_intent"


async def _run_expression_selector(tool_ctx: BuiltinToolRuntimeContext, system_prompt: str) -> str:
    """运行 replyer 侧表达方式选择子代理，并返回文本结果。"""
    response = await tool_ctx.runtime.run_sub_agent(
        context_message_limit=10,
        system_prompt=system_prompt,
        request_kind="expression_selector",
    )
    return (response.content or "").strip()


def get_tool_spec() -> ToolSpec:
    """获取 reply 工具声明。"""

    properties: dict[str, Any] = {
        "msg_id": {
            "type": "string",
            "description": "要回复的消息msg_id。",
        },
        "set_quote": {
            "type": "boolean",
            "description": "以引用回复的方式发送这条回复，不用每句都引用。",
            "default": True,
        },
        "reply_guide": {
            "type": "string",
            "description": "回复需要注意的事项和回复指引",
        },
    }
    if _use_expression_intent():
        properties["expression_intent"] = {
            "type": "object",
            "description": (
                "可选。给 replyer 表达方式选择使用的结构化意图，不是回复正文。"
                "当这次回复需要特定语气、场景或话术时填写，避免表达选择只按关键词匹配。"
            ),
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "本次表达主要应该贴合的目标消息、片段或话题。",
                },
                "reply_act": {
                    "type": "string",
                    "description": "这次回复要完成的动作，例如澄清、安抚、调侃、追问、解释边界。",
                },
                "scene": {
                    "type": "string",
                    "description": "当前表达场景，例如技术排查、截图分享、撒娇玩笑、价格询问。",
                },
                "tone": {
                    "type": "string",
                    "description": "期望语气，例如轻松、可靠、吐槽、委婉、简短肯定。",
                },
                "prefer": {
                    "type": "array",
                    "description": "优先考虑的表达类型或话术倾向。",
                    "items": {"type": "string"},
                },
                "avoid": {
                    "type": "array",
                    "description": "需要避免的表达类型、误判方向或不该注入的具体结论。",
                    "items": {"type": "string"},
                },
            },
        }

    return ToolSpec(
        name="reply",
        description="根据当前思考生成并发送一条可见回复。",
        parameters_schema={
            "type": "object",
            "properties": properties,
            "required": ["msg_id"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def _build_monitor_metadata(reply_result: ReplyGenerationResult) -> dict[str, object]:
    """从 reply 结果中提取统一监控详情。"""

    monitor_detail = reply_result.monitor_detail
    if isinstance(monitor_detail, dict):
        return {"monitor_detail": monitor_detail}
    return {}


def _build_send_result(
    *,
    index: int,
    segment: str,
    set_quote: bool,
    success: bool,
    message_id: str = "",
) -> dict[str, Any]:
    """构建分段回复的轻量发送结果。"""

    return {
        "index": index,
        "segment": segment,
        "set_quote": set_quote,
        "success": success,
        "message_id": message_id,
    }


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 reply 内置工具。"""

    latest_thought = context.reasoning if context is not None else invocation.reasoning
    target_message_id = str(invocation.arguments.get("msg_id") or "").strip()
    set_quote = bool(invocation.arguments.get("set_quote", True))
    reply_tool_args = {
        key: value
        for key, value in dict(invocation.arguments or {}).items()
        if key not in _REPLY_TOOL_INTERNAL_ARGUMENTS
    }
    if not _use_expression_intent():
        reply_tool_args.pop("expression_intent", None)
    enable_reply_quote = bool(config_module.global_config.chat.reply_style.enable_reply_quote)
    effective_set_quote = set_quote and enable_reply_quote

    if not target_message_id:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "reply 工具需要提供有效的 `msg_id` 参数。",
        )

    target_message = tool_ctx.runtime.find_source_message_by_id(target_message_id)
    if target_message is None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"未找到要回复的目标消息，msg_id={target_message_id}",
        )

    try:
        replyer = replyer_manager.get_replyer(
            chat_stream=tool_ctx.runtime.chat_stream,
            request_type="maisaka.replyer",
            replyer_type="maisaka",
        )
    except Exception:
        logger.exception(f"{tool_ctx.runtime.log_prefix} 获取回复生成器时发生异常: 目标消息编号={target_message_id}")
        logger.info(traceback.format_exc())
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "获取 Maisaka 回复生成器时发生异常。",
        )

    if replyer is None:
        logger.error(f"{tool_ctx.runtime.log_prefix} 获取 Maisaka 回复生成器失败")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "Maisaka 回复生成器当前不可用。",
        )

    rich_reply_enabled = bool(config_module.global_config.experimental.enable_rich_reply)
    rich_reply_events: list[dict[str, Any]] = []
    rich_reply_checker_records: list[dict[str, Any]] = []
    replyer_chat_history = list(tool_ctx.runtime._chat_history)
    try:
        success, reply_result = await replyer.generate_reply_with_context(
            reply_reason=latest_thought,
            stream_id=tool_ctx.runtime.session_id,
            reply_message=target_message,
            chat_history=replyer_chat_history,
            reply_tool_args=reply_tool_args,
            sub_agent_runner=lambda system_prompt: _run_expression_selector(
                tool_ctx,
                system_prompt,
            ),
            log_reply=False,
        )
    except Exception as exc:
        logger.exception(
            f"{tool_ctx.runtime.log_prefix} 回复生成器执行异常: 目标消息编号={target_message_id} 异常={exc}"
        )
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "生成可见回复时发生异常。",
        )

    reply_text = reply_result.completion.response_text.strip() if success else ""
    original_reply_text_for_rich_output = reply_text
    if rich_reply_enabled and reply_text:
        reply_result.metrics.extra["rich_reply_original_response"] = original_reply_text_for_rich_output
        try:
            check_result = await replyer.check_rich_reply_output(
                generated_reply=reply_text,
                chat_history=replyer_chat_history,
                latest_chat_history=list(tool_ctx.runtime._chat_history),
                reply_message=target_message,
                reply_reason=latest_thought,
                stream_id=tool_ctx.runtime.session_id,
                reply_tool_args=reply_tool_args,
            )
        except Exception as exc:
            logger.exception(f"{tool_ctx.runtime.log_prefix} 丰富回复检查器执行异常: {exc}")
        else:
            rich_reply_event = {
                "attempt": 1,
                "action": check_result.action,
                "output": check_result.output_text,
                "model_name": check_result.model_name,
                "prompt_tokens": check_result.prompt_tokens,
                "completion_tokens": check_result.completion_tokens,
                "total_tokens": check_result.total_tokens,
            }
            rich_reply_events.append(rich_reply_event)
            reply_result.metrics.extra["rich_reply_checker_output"] = check_result.output_text
            rich_reply_checker_records.append(
                {
                    "prompt_title": "Reply Checker Prompt #1",
                    "reasoning_title": "Reply Checker 思考",
                    "output_title": "Reply Checker 输出",
                    "prompt_category": "replyer",
                    "request_kind": "replyer_checker",
                    "selection_reason": (
                        f"会话ID: {tool_ctx.runtime.session_id}\n"
                        f"目标消息: {target_message_id}\n"
                        f"检查器动作: {check_result.action}"
                    ),
                    "request_messages": check_result.request_messages,
                    "prompt_text": check_result.request_prompt,
                    "output_text": check_result.output_text,
                    "reasoning_text": check_result.reasoning_text,
                    "metrics": {
                        "model_name": check_result.model_name,
                        "prompt_tokens": check_result.prompt_tokens,
                        "completion_tokens": check_result.completion_tokens,
                        "total_tokens": check_result.total_tokens,
                    },
                }
            )

            if check_result.action == "rewrite":
                reply_text = check_result.output_text
                reply_result.metrics.extra["rich_reply_checker_request_message_count"] = check_result.request_message_count
                reply_result.metrics.extra["rich_reply_checker_request_messages"] = check_result.request_messages
                reply_result.metrics.extra["rich_reply_checker_reasoning"] = check_result.reasoning_text

    if rich_reply_events:
        reply_result.metrics.extra["rich_reply_checker_events"] = rich_reply_events
    if rich_reply_checker_records:
        reply_result.metrics.extra["rich_reply_checker_records"] = rich_reply_checker_records

    if not reply_text:
        reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
        reply_metadata = _build_monitor_metadata(reply_result)
        logger.warning(
            f"{tool_ctx.runtime.log_prefix} 回复生成器返回空文本: "
            f"目标消息编号={target_message_id} 错误信息={reply_result.error_message!r}"
        )
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "生成可见回复失败。",
            metadata=reply_metadata,
        )

    try:
        if rich_reply_enabled:
            reply_sequences = await tool_ctx.post_process_rich_reply_message_sequences_async(
                reply_text,
                original_reply_text_for_rich_output,
            )
        else:
            reply_sequences = await tool_ctx.post_process_reply_message_sequences_async(reply_text)
    except Exception as exc:
        reply_result.completion.response_text = reply_text
        reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
        reply_metadata = _build_monitor_metadata(reply_result)
        logger.exception(f"{tool_ctx.runtime.log_prefix} 解析丰富回复输出失败: {exc}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"解析丰富回复输出失败：{exc}",
            metadata=reply_metadata,
        )
    reply_segments = [build_visible_text_from_sequence(sequence) for sequence in reply_sequences]
    combined_reply_text = "".join(reply_segments)
    reply_result.completion.response_text = combined_reply_text
    reply_result.text_fragments = reply_segments
    reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
    reply_metadata = _build_monitor_metadata(reply_result)
    sent_message_ids: list[str] = []
    send_results: list[dict[str, Any]] = []
    try:
        sent = False
        if tool_ctx.runtime.chat_stream.platform == CLI_PLATFORM_NAME:
            for index, segment in enumerate(reply_segments):
                render_cli_message(segment)
                send_results.append(
                    _build_send_result(
                        index=index,
                        segment=segment,
                        set_quote=effective_set_quote if index == 0 else False,
                        success=True,
                    )
                )
            sent = True
        else:
            for index, reply_sequence in enumerate(reply_sequences):
                segment = reply_segments[index]
                segment_set_quote = effective_set_quote if index == 0 else False
                sent_message = await send_service._send_to_target_with_message(
                    message_sequence=reply_sequence,
                    stream_id=tool_ctx.runtime.session_id,
                    processed_plain_text=segment,
                    set_reply=segment_set_quote,
                    reply_message=target_message,
                    selected_expressions=reply_result.selected_expression_ids or None,
                    typing=index > 0,
                    sync_to_maisaka_history=True,
                    maisaka_source_kind="guided_reply",
                )
                sent = sent_message is not None
                if not sent:
                    send_results.append(
                        _build_send_result(
                            index=index,
                            segment=segment,
                            set_quote=segment_set_quote,
                            success=False,
                        )
                    )
                    break
                sent_message_id = str(getattr(sent_message, "message_id", "") or "").strip()
                if sent_message_id:
                    sent_message_ids.append(sent_message_id)
                send_results.append(
                    _build_send_result(
                        index=index,
                        segment=segment,
                        set_quote=segment_set_quote,
                        success=True,
                        message_id=sent_message_id,
                    )
                )
    except Exception:
        logger.exception(f"{tool_ctx.runtime.log_prefix} 发送文字消息时发生异常，目标消息编号={target_message_id}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "发送可见回复时发生异常。",
            metadata=reply_metadata,
        )

    if not sent:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "可见回复生成成功，但发送失败。",
            structured_content={
                "msg_id": target_message_id,
                "set_quote": set_quote,
                "effective_set_quote": effective_set_quote,
                "reply_segments": reply_segments,
                "send_results": send_results,
            },
            metadata=reply_metadata,
        )

    target_user_info = target_message.message_info.user_info
    target_user_name = target_user_info.user_cardname or target_user_info.user_nickname or target_user_info.user_id
    bot_name = config_module.global_config.bot.nickname.strip() or "MaiSaka"

    if tool_ctx.runtime.chat_stream.platform == CLI_PLATFORM_NAME:
        tool_ctx.append_guided_reply_to_chat_history(combined_reply_text)
    reply_metadata["sent_message_ids"] = sent_message_ids
    reply_metadata["send_results"] = send_results
    track_reply_effect = getattr(tool_ctx.runtime, "track_reply_effect", None)
    if track_reply_effect is not None:
        await track_reply_effect(
            tool_call_id=invocation.call_id,
            target_message=target_message,
            set_quote=effective_set_quote,
            reply_text=combined_reply_text,
            reply_segments=reply_segments,
            planner_reasoning=latest_thought,
            tool_context={
                "tool_name": invocation.tool_name,
                "call_id": invocation.call_id,
                "arguments": dict(invocation.arguments or {}),
                "reasoning": latest_thought,
            },
            send_results=send_results,
            reply_metadata=reply_metadata,
            replyer_context_messages=replyer_chat_history,
        )
    return tool_ctx.build_success_result(
        invocation.tool_name,
        f'"{bot_name}"已生成并向"{target_user_name}"发送了回复"{combined_reply_text}"',
        structured_content={
            "msg_id": target_message_id,
            "set_quote": set_quote,
            "effective_set_quote": effective_set_quote,
            "reply_text": combined_reply_text,
            "reply_segments": reply_segments,
            "send_results": send_results,
            "target_user_name": target_user_name,
        },
        metadata=reply_metadata,
    )
