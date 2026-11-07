"""reply 工具适配与共用回复执行服务。"""

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

import asyncio
import json
import traceback

from src.chat.replyer.replyer_manager import replyer_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import get_bot_account, is_bot_self
from src.cli.maisaka_cli_sender import CLI_PLATFORM_NAME, render_cli_message
from src.common.data_models.message_component_data_model import TextComponent
from src.common.data_models.reply_generation_data_models import ReplyGenerationResult, build_reply_monitor_detail
from src.common.llm_json import parse_llm_json
from src.common.logger import get_logger
from src.config import config as config_module
from src.maisaka.scenes.factory import get_scene
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.quote import decide_quote
from src.maisaka.context.message_adapter import build_visible_text_from_sequence, parse_speaker_content
from src.maisaka.context.message_id_alias import to_display_message_id
from src.maisaka.context.messages import LLMContextMessage, SessionBackedMessage
from src.maisaka.context.planner_messages import extract_quote_ids_from_message_sequence
from src.plugin_runtime.host.hook_dispatcher import HookAbortedError, build_hook_abort_tool_result
from src.plugin_runtime.host.message_utils import PluginMessageUtils
from src.plugin_runtime.host.reply_extensions import ReplyExtensionExecution, build_reply_extensions_schema, get_reply_extensions
from src.services import send_service

from .context import BuiltinToolRuntimeContext, PostProcessedReplyMessage, ResolvedReplyAttachments

if TYPE_CHECKING:
    from src.maisaka.reply_supplement import ReplySupplement

logger = get_logger("maisaka_builtin_reply")
_REPLY_TOOL_INTERNAL_ARGUMENTS = {"msg_id", "_reply_id", "_plugin_reply_prompt", "after_reply", "wait_seconds"}
_DUPLICATE_TARGET_REPLY_REMINDER_ARG = "_duplicate_target_reply_reminder"
_DUPLICATE_TARGET_REPLY_REMINDER_TEMPLATE = (
    "你刚刚已经回复过这条消息，你刚刚的发言是：“{previous_reply}”\n"
    "你现在想再次回复这条消息，进行补充，注意请不要和之前你的发言重复。"
)


def _normalize_reply_arguments(raw_arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """兼容模型把 reply 参数重复包裹在 arguments 字段中的情况。"""

    if set(raw_arguments) != {"arguments"}:
        return raw_arguments, ""

    wrapped_arguments = raw_arguments["arguments"]
    if not isinstance(wrapped_arguments, str):
        return raw_arguments, (
            "reply 工具参数结构错误：顶层 `arguments` 应为包含 reply 参数的 JSON 字符串，"
            f"实际类型为 {type(wrapped_arguments).__name__}。"
        )

    try:
        parsed_arguments = parse_llm_json(wrapped_arguments)
    except json.JSONDecodeError as exc:
        return raw_arguments, f"reply 工具参数结构错误：顶层 `arguments` 不是有效的 JSON 对象：{exc.msg}。"

    if not isinstance(parsed_arguments, dict):
        return raw_arguments, (
            "reply 工具参数结构错误：顶层 `arguments` 必须解析为 JSON 对象，"
            f"实际解析结果为 {type(parsed_arguments).__name__}。"
        )

    return parsed_arguments, ""


def _use_expression_intent() -> bool:
    return config_module.global_config.expression.use_vector_expression


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
    before_post_process_result.raise_if_aborted()
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


def get_tool_spec(context: Optional[ToolAvailabilityContext] = None) -> ToolSpec:
    """获取 reply 工具声明。"""

    properties: dict[str, Any] = {
        "after_reply": {
            "type": "string",
            "enum": ["continue", "stop", "wait"],
            "default": "stop",
            "description": "回复发送成功后的行为：continue 继续思考和调用工具；stop 结束本轮（默认）；wait 等待后再判断。stop/wait 会取消同批后续工具。",
        },
        "wait_seconds": {
            "type": "integer",
            "minimum": 0,
            "default": 30,
            "description": "after_reply=wait 时的等待秒数，默认 30 秒。",
        },
        "msg_id": {
            "type": "string",
            "description": "要回复的消息msg_id。",
        },
        "reply_reference": {
            "type": "string",
            "minLength": 1,
            "description": (
                "提供本次回复的上下文关键信息，对当前聊天逻辑和内容的梳理，以及你认为此时需要回复的原因，"
                "包括当前聊天状态、人物关系、事实信息、回忆信息；不是回复正文。"
            ),
        },
        "reply_style": {
            "type": "string",
            "description": "控制本次回复的篇幅和表达方式，当其他人回复都偏短时使用正常回复或者简短表达。当需要解释说明复杂问题时使用长回复，其他情况视情况决定。",
            "enum": ["简短表达", "正常回复", "长回复"],
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
        "description": "可选。在文字后发送一张表情包。可引用上下文表情，或按情绪、画面内容匹配。省略表示不发送表情包。",
        "anyOf": [
            {
                "type": "object",
                "description": "只填一项就按该项匹配，两项都填就合并分数。",
                "properties": {
                    "emotion": {"type": "string", "minLength": 1, "description": "想表达的情绪或语气。"},
                    "content": {"type": "string", "minLength": 1, "description": "期望图中出现的人物、物体、动作或文字。"},
                },
                "minProperties": 1,
                "additionalProperties": False,
            },
            {"type": "string", "minLength": 1, "description": "想表达的情绪或意图，例如轻松地自嘲。"},
            {
                "type": "object",
                "properties": {
                    "msg_id": {"type": "string", "minLength": 1, "description": "含表情包的上下文消息编号。"},
                    "index": {"type": "integer", "minimum": 0, "description": "该消息中第几张表情包，从 0 开始。"},
                },
                "required": ["msg_id"],
                "additionalProperties": False,
            },
        ],
    }
    if context is not None and context.is_group_chat is not None and get_scene(context.is_group_chat).allow_reply_at:
        properties["attach_at"] = {
            "type": "array",
            "description": "可选。随本次回复 at 一个或多个目标消息的发送者，填写目标 msg_id。",
            "items": {"type": "string"},
            "default": [],
        }

    extension_schema = build_reply_extensions_schema(context)
    if extension_schema is not None:
        properties["plugin_options"] = extension_schema

    return ToolSpec(
        name="reply",
        description="根据当前思考生成并发送可见回复；可以使用参数附加回复内容。",
        parameters_schema={
            "type": "object",
            "properties": properties,
            "required": ["msg_id", "reply_reference"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def _build_monitor_metadata(reply_result: ReplyGenerationResult, reply_id: str = "") -> Dict[str, object]:
    """从 reply 结果中提取统一监控详情。"""

    monitor_detail = reply_result.monitor_detail
    metadata: Dict[str, object] = {"reply_id": reply_id} if reply_id else {}
    metadata["reply_schedule"] = reply_result.metrics.extra.get("reply_schedule")
    supplement_detail = reply_result.metrics.extra.get("reply_supplement")
    if isinstance(supplement_detail, dict):
        metadata["reply_supplement"] = supplement_detail
        monitor_detail = dict(monitor_detail)
        monitor_detail["reply_supplement"] = supplement_detail
    if isinstance(monitor_detail, dict):
        metadata["monitor_detail"] = monitor_detail
    return metadata


def _build_reply_failure(
    tool_ctx: BuiltinToolRuntimeContext,
    target_message_id: str,
    error_code: str,
    reason: str,
    metadata: Dict[str, Any],
    hook_abort: Optional[Dict[str, Any]] = None,
) -> ToolExecutionResult:
    """统一向 Planner 反馈回复失败类型；主动拦截同时携带来源和原因。"""
    content: Dict[str, Any] = {"msg_id": target_message_id, "error_code": error_code, "reason": reason}
    if hook_abort is not None:
        content["hook_abort"] = hook_abort
    if "send_results" in metadata:
        content["sent_message_ids"] = metadata["sent_message_ids"]
        content["send_results"] = metadata["send_results"]
    if hook_abort is not None:
        return build_hook_abort_tool_result("reply", hook_abort, structured_content=content, metadata=metadata)
    content["message"] = reason
    return tool_ctx.build_failure_result("reply", reason, structured_content=content, metadata=metadata)


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


def _resolve_segment_reply_context(
    *,
    index: int,
    quote_previous: bool,
    effective_set_quote: bool,
    target_message: Any,
    previous_sent_message: Any,
    first_reply_index: int = 0,
) -> tuple[bool, Any]:
    """确定当前分段是否引用，以及引用所使用的真实消息对象。"""

    if index == first_reply_index:
        return effective_set_quote, target_message
    if quote_previous and previous_sent_message is not None:
        return True, previous_sent_message
    return False, target_message


async def _resolve_reply_quote(
    tool_ctx: BuiltinToolRuntimeContext,
    text: str,
    target_message: SessionMessage,
    bot_user_id: str,
) -> Tuple[bool, Optional[float]]:
    """群聊和私聊分别按固定公式及频率决定引用。"""
    stream = tool_ctx.runtime.chat_stream
    frequency = get_scene(stream.is_group_session).quote_frequency
    if frequency == "never" or stream.platform == CLI_PLATFORM_NAME:
        return False, 0.0
    return decide_quote(
        context=tool_ctx.runtime.get_reply_quote_context(target_message), bot_user_id=bot_user_id,
        text=text, frequency=frequency, is_group=stream.is_group_session,
    )


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

    raw_invocation_arguments = dict(invocation.arguments or {})
    invocation_arguments, argument_structure_error = _normalize_reply_arguments(raw_invocation_arguments)
    if argument_structure_error:
        return tool_ctx.build_failure_result(invocation.tool_name, argument_structure_error)
    if invocation_arguments != raw_invocation_arguments:
        logger.warning(
            f"{tool_ctx.runtime.log_prefix} 检测到 reply 工具参数被重复包裹，已自动解包: "
            f"调用编号={invocation.call_id}"
        )
    after_reply = invocation_arguments.get("after_reply", "stop")
    if after_reply not in ("continue", "stop", "wait"):
        return tool_ctx.build_failure_result("reply", "after_reply 必须为 continue、stop 或 wait。")
    wait_seconds = invocation_arguments.get("wait_seconds", 30)
    if type(wait_seconds) is not int or wait_seconds < 0:
        return tool_ctx.build_failure_result("reply", "wait_seconds 必须为非负整数。")
    result = await execute_reply(tool_ctx, invocation_arguments, call_id=invocation.call_id)
    if result.success:
        # 控制由各自的 Planner 循环执行，避免 Focus 意外暂停单个成员的普通循环。
        result.metadata["after_reply"] = after_reply
        if after_reply == "wait":
            result.metadata["wait_seconds"] = wait_seconds
    return result


async def execute_reply(
    tool_ctx: BuiltinToolRuntimeContext,
    arguments: Dict[str, Any],
    *,
    call_id: str,
    target_message: Optional[SessionMessage] = None,
    supplement_runner: Optional[Callable[[], Awaitable["ReplySupplement"]]] = None,
) -> ToolExecutionResult:
    """持有本轮附件任务，失败或取消时回收，禁止任务越过本轮回复。"""
    stream = tool_ctx.runtime.chat_stream
    # 与发送链路使用同一身份解析；有歧义时在生成正文及补充内容前暴露错误。
    bot_user_id = await asyncio.to_thread(get_bot_account, stream.platform, stream.account_id)
    if not bot_user_id:
        return tool_ctx.build_failure_result("reply", "无法确定 Bot 发送账号，请为聊天流明确指定 account_id。")
    task = asyncio.create_task(supplement_runner()) if supplement_runner is not None else None
    attachment_task: Optional[asyncio.Task[ResolvedReplyAttachments]] = None

    async def resolve_supplement() -> "ReplySupplement":
        return await asyncio.shield(task)

    def start_attachment_resolution(attachments: Dict[str, Any]) -> asyncio.Task[ResolvedReplyAttachments]:
        nonlocal attachment_task
        attachment_task = asyncio.create_task(tool_ctx.resolve_reply_attachments(attachments))
        return attachment_task

    try:
        return await _execute_reply(
            tool_ctx, arguments, call_id=call_id, bot_user_id=bot_user_id, target_message=target_message,
            supplement_runner=resolve_supplement if task is not None else None,
            start_attachment_resolution=start_attachment_resolution,
        )
    finally:
        tasks = [pending for pending in (task, attachment_task) if pending is not None]
        for pending in tasks:
            if not pending.done():
                pending.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if task is not None and not task.cancelled() and task.exception() is None:
            supplement_attachments = task.result().attachments
            if not supplement_attachments.done():
                supplement_attachments.cancel()
            await asyncio.gather(supplement_attachments, return_exceptions=True)


async def _execute_reply(
    tool_ctx: BuiltinToolRuntimeContext,
    arguments: Dict[str, Any],
    *,
    call_id: str,
    bot_user_id: str,
    start_attachment_resolution: Callable[[Dict[str, Any]], asyncio.Task[ResolvedReplyAttachments]],
    target_message: Optional[SessionMessage] = None,
    supplement_runner: Optional[Callable[[], Awaitable["ReplySupplement"]]] = None,
) -> ToolExecutionResult:
    """共用回复执行入口，保留附件、插件扩展、Hook 和发送统计。

    普通 Planner 直接调用；Focus 的 reply 工具经参数解包后调用同一入口。
    target_message 可传入请求前固定的真实消息，避免生成期间历史变化影响引用目标。
    正文与补充编排并行；Mai Replier 按实际模型等待补充器确定目标。
    """

    invocation_arguments = deepcopy(arguments)
    planner_snapshot = None
    if config_module.global_config.experimental.reply_cache and not config_module.global_config.experimental.super_creative_mode:
        # Focus 使用共享 Planner 快照，普通聊天使用所属会话的快照。
        planner_service = (
            tool_ctx.runtime._focus_group._service if tool_ctx.runtime._focus_group is not None
            else tool_ctx.runtime._chat_loop_service
        )
        planner_snapshot = planner_service.planner_request_snapshot
        if planner_snapshot is None:
            return tool_ctx.build_failure_result("reply", "回复缓存模式缺少本轮 Planner 请求快照，无法生成回复。")
    if type(invocation_arguments.get("attach_emoji")) is int and invocation_arguments["attach_emoji"] == 0:
        invocation_arguments.pop("attach_emoji")
    reply_reference = invocation_arguments.get("reply_reference")
    if not isinstance(reply_reference, str) or not reply_reference.strip():
        return tool_ctx.build_failure_result(
            "reply",
            "reply 工具必须提供非空字符串 `reply_reference`，请填写本次回复的上下文关键信息与回复方向后重新调用。",
        )
    invocation_arguments["reply_reference"] = reply_reference.strip()
    target_message_id = str(invocation_arguments.get("msg_id") or "").strip()
    if invocation_arguments.get("attach_at") and not config_module.global_config.chat.enable_reply_at:
        return tool_ctx.build_failure_result("reply", "回复时 @ 用户已关闭，不能使用 attach_at。")
    reply_tool_args = {
        key: value
        for key, value in invocation_arguments.items()
        if key not in _REPLY_TOOL_INTERNAL_ARGUMENTS
    }
    if not _use_expression_intent():
        reply_tool_args.pop("expression_intent", None)

    if not target_message_id:
        return tool_ctx.build_failure_result(
            "reply",
            "reply 工具需要提供有效的 `msg_id` 参数。",
        )

    if target_message is None:
        target_message = tool_ctx.runtime.find_source_message_by_id(target_message_id)
    elif target_message.message_id != target_message_id:
        raise ValueError("回复快照的消息 ID 与回复参数不一致")
    if target_message is None:
        return tool_ctx.build_failure_result(
            "reply",
            f"未找到要回复的目标消息，msg_id={to_display_message_id(target_message_id)}",
        )

    context = ToolAvailabilityContext(
        session_id=tool_ctx.runtime.session_id, stream_id=tool_ctx.runtime.session_id,
        platform=tool_ctx.runtime.chat_stream.platform,
        is_group_chat=tool_ctx.runtime.chat_stream.is_group_session,
        group_id=tool_ctx.runtime.chat_stream.group_id, user_id=tool_ctx.runtime.chat_stream.user_id,
    )
    available_extensions = get_reply_extensions(context)
    dependencies: List[str] = []
    schedule: Dict[str, Any] = {"mode": "parallel", "dependencies": dependencies}
    # 条件式能力提示不依赖本轮参数，正文无需等待补充器选择插件。
    reply_tool_args["_plugin_reply_prompt"] = "\n\n".join(
        target.entry.generation_prompt for target in available_extensions.values()
        if supplement_runner is not None and target.entry.generation_prompt
    )
    supplement: Optional["ReplySupplement"] = None
    extension_execution = ReplyExtensionExecution(context=context, reply_message_id=target_message_id, call_id=call_id)
    preparation_metadata: Dict[str, Any] = {"reply_id": extension_execution.reply_id, "reply_schedule": schedule}

    async def collect_supplement() -> Optional[SessionMessage]:
        nonlocal supplement, target_message, target_message_id
        if supplement_runner is not None and supplement is None:
            try:
                supplement = await supplement_runner()
            except Exception as exc:
                preparation_metadata["reply_supplement"] = {"status": "failed", "error": str(exc)}
                raise
            if supplement.target_message is not None:
                target_message = supplement.target_message
                target_message_id = target_message.message_id
                invocation_arguments["msg_id"] = target_message_id
            invocation_arguments["plugin_options"] = supplement.plan.plugin_options
            preparation_metadata["reply_supplement"] = {**supplement.metadata, "status": "completed"}
            extension_execution.reply_message_id = target_message_id
        return supplement.target_message if supplement is not None else target_message

    async def resolve_reply_target() -> Optional[SessionMessage]:
        if "replyer:reply_target" not in dependencies:
            dependencies.append("replyer:reply_target")
        schedule["mode"] = "serial"
        return await collect_supplement()

    async def prepare_extensions() -> None:
        await extension_execution.prepare(invocation_arguments.get("plugin_options", {}))
        if extension_execution.targets:
            reply_tool_args["plugin_options"] = deepcopy(extension_execution.parameters)
            reply_tool_args["_reply_id"] = extension_execution.reply_id

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
            "reply",
            "获取 Maisaka 回复生成器时发生异常。",
            metadata=preparation_metadata,
        )

    if replyer is None:
        logger.error(f"{tool_ctx.runtime.log_prefix} 获取 Maisaka 回复生成器失败")
        return tool_ctx.build_failure_result(
            "reply",
            "Maisaka 回复生成器当前不可用。",
            metadata=preparation_metadata,
        )

    replyer_chat_history = list(tool_ctx.runtime._chat_history)
    # 并行编排的引用目标尚未确定，不能把请求快照的最后一条消息强制指定为正文回复对象。
    previous_target_reply = (
        _find_recent_reply_to_target(replyer_chat_history, target_message_id) if supplement_runner is None else ""
    )
    if previous_target_reply:
        reply_tool_args = _with_duplicate_target_reply_reminder(reply_tool_args, previous_target_reply)
    try:
        tool_ctx.runtime._update_stage_status("Replyer", "生成可见回复")
        if supplement_runner is None:
            # 原有 reply 工具先准备插件参数及正文要求，实验链路才等待并行编排。
            await prepare_extensions()
            reply_tool_args["_plugin_reply_prompt"] = extension_execution.extra_prompt
        resolved_attachments = None
        if supplement_runner is None and any(
            invocation_arguments.get(key) is not None for key in ("attach_at", "attach_pic", "attach_emoji")
        ):
            resolved_attachments = start_attachment_resolution(invocation_arguments)
        generation = replyer.generate_reply_with_context(
            reply_reason="",
            tool_call_id=call_id,
            stream_id=tool_ctx.runtime.session_id,
            reply_message=target_message if supplement_runner is None else None,
            resolve_reply_target=resolve_reply_target if supplement_runner is not None else None,
            chat_history=replyer_chat_history,
            retro_history_builder=tool_ctx.runtime.build_retro_reply_history,
            image_recognition_refresher=lambda history: (
                tool_ctx.runtime._reasoning_engine._refresh_chat_history_visual_placeholders(
                    chat_history=history,
                    wait_for_text_model=True,
                )
            ),
            reply_tool_args=reply_tool_args,
            sub_agent_runner=lambda system_prompt: _run_expression_selector(
                tool_ctx,
                system_prompt,
            ),
            log_reply=False,
            planner_snapshot=planner_snapshot,
        )
        success, reply_result = await generation
        if supplement_runner is not None:
            await collect_supplement()
            await prepare_extensions()
            if extension_execution.extra_prompt:
                raise ValueError("平行补充器模式下请通过 generation_prompt 声明正文要求，prepare 不能在正文生成后追加要求")
            reply_result.metrics.extra["reply_schedule"] = schedule
        if supplement is not None:
            reply_result.metrics.extra["reply_supplement"] = {**supplement.metadata, "status": "completed"}
    except Exception as exc:
        logger.exception(
            f"{tool_ctx.runtime.log_prefix} 回复生成器执行异常: 目标消息编号={target_message_id} 异常={exc}"
        )
        return tool_ctx.build_failure_result(
            "reply",
            f"生成可见回复或补充内容时发生异常：{type(exc).__name__}: {exc}",
            metadata=preparation_metadata,
        )

    reply_text = reply_result.completion.response_text.strip() if success else ""

    if not reply_text:
        reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
        reply_metadata = _build_monitor_metadata(reply_result, extension_execution.reply_id)
        logger.warning(
            f"{tool_ctx.runtime.log_prefix} 回复生成器返回空文本: "
            f"目标消息编号={target_message_id} 错误信息={reply_result.error_message!r}"
        )
        hook_abort = reply_result.error_details.get("hook_abort")
        return _build_reply_failure(
            tool_ctx, target_message_id, reply_result.error_code or "generation_failed",
            hook_abort["reason"] if hook_abort is not None else reply_result.error_message,
            reply_metadata, hook_abort,
        )

    if supplement is not None:
        reply_tool_args["reply_supplement"] = supplement.plan.model_dump()
        invocation_arguments["reply_supplement"] = supplement.plan.model_dump()

    try:
        reply_text, post_process_options = await _invoke_before_post_process_hook(
            tool_ctx,
            reply_text,
            target_message_id,
            reply_tool_args,
        )
    except HookAbortedError as exc:
        return build_hook_abort_tool_result(
            "reply", exc.to_dict(), structured_content={"msg_id": target_message_id},
            metadata=_build_monitor_metadata(reply_result, extension_execution.reply_id),
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
        return _build_reply_failure(
            tool_ctx, target_message_id, "plugin_empty_response", "回复文本后处理前 Hook 将回复正文修改为空。",
            _build_monitor_metadata(reply_result, extension_execution.reply_id),
        )

    try:
        reply_items = await tool_ctx.post_process_reply_message_items_async(
            reply_text,
            resolved_attachments=resolved_attachments,
            **post_process_options,
        )
        if supplement is not None:
            reply_items = await supplement.compose(reply_items)
    except Exception as exc:
        reply_result.completion.response_text = reply_text
        reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
        reply_metadata = _build_monitor_metadata(reply_result, extension_execution.reply_id)
        logger.exception(f"{tool_ctx.runtime.log_prefix} 解析回复附件失败: {exc}")
        return tool_ctx.build_failure_result(
            "reply",
            f"解析回复附件失败：{exc}",
            metadata=reply_metadata,
        )
    if extension_execution.targets:
        try:
            # 序列化可能读取图片库和文件，放到线程中；整组消息只在发送前转换一次。
            serialized = await asyncio.to_thread(
                lambda: [
                    {
                        "segments": PluginMessageUtils._message_sequence_to_dict(item.sequence),
                        "quote_previous": item.quote_previous,
                    }
                    for item in reply_items
                ]
            )
            transformed = await extension_execution.transform(reply_text, serialized)
            reply_items = await asyncio.to_thread(
                lambda: [
                    PostProcessedReplyMessage(
                        sequence=PluginMessageUtils._message_sequence_from_dict(item["segments"]),
                        quote_previous=item["quote_previous"],
                    )
                    for item in transformed
                ]
            )
            extension_execution.ensure_active()
        except Exception as exc:
            logger.exception(f"{tool_ctx.runtime.log_prefix} 插件回复扩展处理失败: {exc}")
            return tool_ctx.build_failure_result(
                "reply", f"插件回复扩展处理失败：{exc}",
                metadata=_build_monitor_metadata(reply_result, extension_execution.reply_id),
            )
    reply_sequences = [item.sequence for item in reply_items]
    reply_segments = [build_visible_text_from_sequence(sequence) for sequence in reply_sequences]
    combined_reply_text = "".join(reply_segments)
    reply_result.completion.response_text = combined_reply_text
    reply_result.text_fragments = reply_segments
    reply_result.monitor_detail = build_reply_monitor_detail(reply_result)
    reply_metadata = _build_monitor_metadata(reply_result, extension_execution.reply_id)
    if extension_execution.targets:
        reply_metadata["reply_extensions"] = list(extension_execution.parameters)
    # 两条链路均按首段正文与当前聊天情境抽样一次；补充器只选择候选引用目标。
    first_reply_index = next(
        (index for index, sequence in enumerate(reply_sequences)
         if any(isinstance(c, TextComponent) and c.text.strip() for c in sequence.components)), 0,
    )
    first_text = (
        "".join(c.text for c in reply_sequences[first_reply_index].components if isinstance(c, TextComponent))
        if reply_sequences else ""
    )
    set_quote, quote_probability = await _resolve_reply_quote(tool_ctx, first_text, target_message, bot_user_id)
    if supplement is not None:
        reply_metadata["reply_supplement"] = {**supplement.metadata, "status": "completed"}
    # 补充器选中的引用同样遵守群聊/私聊的三档频率与 CLI 平台限制。
    effective_set_quote = set_quote
    if quote_probability is not None:
        reply_metadata["quote_probability"] = quote_probability
    sent_message_ids: list[str] = []
    send_results: list[dict[str, Any]] = []
    # 逐段更新回执，后续发送抛错时也保留已经送达的消息，避免误判为完全未发送。
    reply_metadata["sent_message_ids"] = sent_message_ids
    reply_metadata["send_results"] = send_results
    try:
        sent = False
        if tool_ctx.runtime.chat_stream.platform == CLI_PLATFORM_NAME:
            for index, segment in enumerate(reply_segments):
                render_cli_message(segment)
                send_results.append(
                    _build_send_result(
                        index=index,
                        segment=segment,
                        set_quote=effective_set_quote if index == first_reply_index else False,
                        success=True,
                    )
                )
            sent = True
        else:
            previous_sent_message = None
            for index, reply_item in enumerate(reply_items):
                reply_sequence = reply_item.sequence
                segment = reply_segments[index]
                segment_set_quote, segment_reply_message = _resolve_segment_reply_context(
                    index=index,
                    quote_previous=reply_item.quote_previous and (
                        get_scene(tool_ctx.runtime.chat_stream.is_group_session).quote_frequency != "never"
                    ),
                    effective_set_quote=effective_set_quote,
                    target_message=target_message,
                    previous_sent_message=previous_sent_message,
                    first_reply_index=first_reply_index,
                )
                sent_message = await send_service._send_to_target_with_message(
                    message_sequence=reply_sequence,
                    stream_id=tool_ctx.runtime.session_id,
                    processed_plain_text=segment,
                    set_reply=segment_set_quote,
                    # 引用上一段只改变显示目标；所有分段仍回复原始入站消息，沿用其被动额度。
                    reply_message=target_message,
                    quote_message=segment_reply_message,
                    selected_expressions=reply_result.selected_expression_ids or None,
                    typing=index > 0,
                    sync_to_maisaka_history=True,
                    maisaka_source_kind="guided_reply",
                    raise_on_hook_abort=True,
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
                sent_message_id = sent_message.message_id.strip()
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
                previous_sent_message = sent_message
    except HookAbortedError as exc:
        send_results.append(
            _build_send_result(index=index, segment=segment, set_quote=segment_set_quote, success=False)
        )
        return _build_reply_failure(
            tool_ctx, target_message_id, "plugin_aborted", exc.reason, reply_metadata, exc.to_dict(),
        )
    except Exception:
        logger.exception(f"{tool_ctx.runtime.log_prefix} 发送文字消息时发生异常，目标消息编号={target_message_id}")
        return tool_ctx.build_failure_result(
            "reply",
            "发送可见回复时发生异常。",
            metadata=reply_metadata,
        )

    if not sent:
        return tool_ctx.build_failure_result(
            "reply",
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
    bot_name = config_module.global_config.bot.nickname.strip() or "MaiSaka"
    if is_bot_self(tool_ctx.runtime.chat_stream.platform, target_user_info.user_id):
        # 自身消息只是回复的引用锚点，不能把其作者当作实际收件人。
        target_user_name = ""
        reply_receipt = f'"{bot_name}"已生成并向当前会话发送了回复"{combined_reply_text}"'
    else:
        target_user_name = target_user_info.user_cardname or target_user_info.user_nickname or target_user_info.user_id
        reply_receipt = f'"{bot_name}"已生成并向"{target_user_name}"发送了回复"{combined_reply_text}"'

    if tool_ctx.runtime.chat_stream.platform == CLI_PLATFORM_NAME:
        tool_ctx.append_guided_reply_to_chat_history(combined_reply_text)
    tool_ctx.runtime.record_planner_reply()
    return tool_ctx.build_success_result(
        "reply",
        reply_receipt,
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
