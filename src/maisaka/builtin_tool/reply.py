"""reply 内置工具。"""

from typing import Any, Optional
import traceback

from src.chat.replyer.replyer_manager import replyer_manager
from src.cli.maisaka_cli_sender import CLI_PLATFORM_NAME, render_cli_message
from src.common.data_models.reply_generation_data_models import ReplyGenerationResult, build_reply_monitor_detail
from src.common.logger import get_logger
from src.config import config as config_module
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.context.message_adapter import build_visible_text_from_sequence, parse_speaker_content
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage
from src.maisaka.context.planner_messages import extract_quote_ids_from_message_sequence
from src.services import send_service

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_reply")
_REPLY_TOOL_INTERNAL_ARGUMENTS = {"msg_id", "set_quote"}
_RICH_REPLY_ARGUMENTS = {"attach_pic", "attach_emoji", "attach_at"}
_DUPLICATE_TARGET_REPLY_REMINDER_ARG = "_duplicate_target_reply_reminder"
_DUPLICATE_TARGET_REPLY_REMINDER_TEMPLATE = (
    "你刚刚已经回复过这条消息，你刚刚的发言是：“{previous_reply}”\n"
    "你现在想再次回复这条消息，进行补充，注意请不要和之前你的发言重复。"
)


def _use_expression_intent() -> bool:
    return config_module.global_config.expression.expression_selection_mode == "vector_intent"


def _require_hook_bool(raw_value: Any, option_name: str) -> bool:
    """读取 Hook 返回的布尔值，并拒绝不符合协议的类型。"""

    if isinstance(raw_value, bool):
        return raw_value
    raise TypeError(f"Hook 返回的 {option_name} 必须是布尔值，实际为 {type(raw_value).__name__}")


async def _invoke_before_post_process_hook(
    tool_ctx: BuiltinToolRuntimeContext,
    reply_text: str,
    target_message_id: str,
    reply_tool_args: dict[str, Any],
) -> tuple[str, dict[str, bool]]:
    """调用最终回复文本后处理前 Hook，并返回本次回复的后处理策略。"""

    post_process_options = {
        "skip_post_process": False,
        "enable_splitter": True,
        "enable_chinese_typo": True,
    }
    before_post_process_result = await tool_ctx.get_runtime_manager().invoke_hook(
        "maisaka.reply.before_post_process",
        response=reply_text,
        session_id=tool_ctx.runtime.session_id,
        reply_message_id=target_message_id,
        reply_tool_args=dict(reply_tool_args),
        **post_process_options,
    )
    before_post_process_kwargs = before_post_process_result.kwargs
    if "response" in before_post_process_kwargs:
        reply_text = str(before_post_process_kwargs["response"] or "").strip()
    for option_name in post_process_options:
        post_process_options[option_name] = _require_hook_bool(
            before_post_process_kwargs.get(option_name),
            option_name,
        )
    return reply_text, post_process_options


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
            "description": "以引用回复的方式发送这条回复，当发言人数过多，聊天比较乱时使用。",
            "default": True,
        },
        "reply_guide": {
            "type": "string",
            "description": "回复需要注意的事项和回复指引，包含当前聊天状态，情感态度等等。",
        },
        "reference_info": {
            "type": "string",
            "description": (
                "上下文中的关键信息，包括人物关系，情感关系，事实信息，回忆信息，聊天情况。"
                "这些信息将，为回复提供信息参考"
            ),
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
    if bool(config_module.global_config.experimental.enable_rich_reply):
        properties["attach_pic"] = {
            "type": "array",
            "description": (
                "可选。随本次回复附加一张或多张上下文图片。每项使用 msg_id + index，"
                "或使用 media_index=tool_result:<call_id>:<item_index> 指向工具返回媒体。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "msg_id": {
                        "type": "string",
                        "description": "图片所在的消息编号。",
                        "default": "",
                    },
                    "media_index": {
                        "type": "string",
                        "description": "工具返回媒体索引，例如 tool_result:call_x:1；与 msg_id 二选一。",
                        "default": "",
                    },
                    "index": {
                        "type": "integer",
                        "description": "同一消息中的图片序号，从 0 开始。",
                        "default": 0,
                    },
                },
            },
            "default": [],
        }
        properties["attach_emoji"] = {
            "type": "string",
            "description": "可选。随本次回复附加一个表情包，填写情绪或表情描述。",
            "default": "",
        }
        properties["attach_at"] = {
            "type": "array",
            "description": "可选。随本次回复 at 一个或多个目标消息的发送者，填写目标 msg_id。",
            "items": {"type": "string"},
            "default": [],
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


def _extract_guided_reply_text(message: SessionBackedMessage) -> str:
    """提取 bot 已发送 guided reply 的可见正文。"""

    plain_text = message.processed_plain_text.strip()
    _speaker, body = parse_speaker_content(plain_text)
    normalized_text = (body or plain_text).strip()
    if not normalized_text:
        normalized_text = build_visible_text_from_sequence(message.raw_message).strip()
    return " ".join(normalized_text.split())


def _find_recent_reply_to_target(
    chat_history: list[LLMContextMessage],
    target_message_id: str,
) -> str:
    """查找最近一次已经引用回复过目标消息的 bot 发言。"""

    normalized_target_message_id = target_message_id.strip()
    if not normalized_target_message_id:
        return ""

    for message in reversed(chat_history):
        if not isinstance(message, SessionBackedMessage):
            continue
        if message.source_kind != "guided_reply":
            continue
        quote_ids = extract_quote_ids_from_message_sequence(message.raw_message)
        if normalized_target_message_id not in quote_ids:
            continue
        reply_text = _extract_guided_reply_text(message)
        if reply_text:
            return reply_text
    return ""


def _with_duplicate_target_reply_reminder(
    reply_tool_args: dict[str, Any],
    previous_reply: str,
) -> dict[str, Any]:
    """把重复回复同一目标消息的提醒作为独立提示传给 replyer。"""

    normalized_previous_reply = " ".join(previous_reply.split()).strip()
    if not normalized_previous_reply:
        return reply_tool_args

    updated_args = dict(reply_tool_args)
    updated_args[_DUPLICATE_TARGET_REPLY_REMINDER_ARG] = _DUPLICATE_TARGET_REPLY_REMINDER_TEMPLATE.format(
        previous_reply=normalized_previous_reply
    )
    return updated_args


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行 reply 内置工具。"""

    invocation_arguments = dict(invocation.arguments or {})
    latest_thought = context.reasoning if context is not None else invocation.reasoning
    target_message_id = str(invocation_arguments.get("msg_id") or "").strip()
    set_quote = bool(invocation_arguments.get("set_quote", True))
    rich_reply_enabled = bool(config_module.global_config.experimental.enable_rich_reply)
    reply_tool_args = {
        key: value
        for key, value in invocation_arguments.items()
        if key not in _REPLY_TOOL_INTERNAL_ARGUMENTS
    }
    if not rich_reply_enabled:
        for key in _RICH_REPLY_ARGUMENTS:
            reply_tool_args.pop(key, None)
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

    replyer_chat_history = list(tool_ctx.runtime._chat_history)
    previous_target_reply = _find_recent_reply_to_target(replyer_chat_history, target_message_id)
    if previous_target_reply:
        reply_tool_args = _with_duplicate_target_reply_reminder(reply_tool_args, previous_target_reply)
    try:
        tool_ctx.runtime._update_stage_status("Replyer", "生成可见回复")
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
        reply_text, post_process_options = await _invoke_before_post_process_hook(
            tool_ctx,
            reply_text,
            target_message_id,
            reply_tool_args,
        )
    except Exception as exc:
        logger.warning(f"{tool_ctx.runtime.log_prefix} 回复文本后处理前 Hook 调用失败，将使用默认策略: {exc}")
        post_process_options = {
            "skip_post_process": False,
            "enable_splitter": True,
            "enable_chinese_typo": True,
        }

    if not reply_text:
        reply_result.completion.response_text = ""
        reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "回复文本后处理前 Hook 返回了空回复。",
            metadata=_build_monitor_metadata(reply_result),
        )

    try:
        if rich_reply_enabled:
            reply_sequences = await tool_ctx.post_process_rich_reply_message_sequences_async(
                reply_text,
                invocation_arguments,
                **post_process_options,
            )
        else:
            reply_sequences = await tool_ctx.post_process_reply_message_sequences_async(
                reply_text,
                **post_process_options,
            )
    except Exception as exc:
        reply_result.completion.response_text = reply_text
        reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
        reply_metadata = _build_monitor_metadata(reply_result)
        logger.exception(f"{tool_ctx.runtime.log_prefix} 解析回复附件失败: {exc}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"解析回复附件失败：{exc}",
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
