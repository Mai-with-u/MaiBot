"""
发送服务模块。

统一封装内部模块的出站消息发送逻辑：

1. 内部模块统一调用本模块。
2. send service 只负责构造和预处理消息。
3. 具体走插件链还是 legacy 旧链，由 Platform IO 内部统一决策。
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional

import asyncio
import base64
import hashlib
import time
import traceback
from datetime import datetime

from src.chat.message_receive.chat_manager import BotChatSession
from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import calculate_typing_time, get_bot_account
from src.common.data_models.mai_message_data_model import GroupInfo, MaiMessage, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    AtComponent,
    DictComponent,
    EmojiComponent,
    FileComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    StandardMessageComponents,
    TextComponent,
    VoiceComponent,
)
from src.common.logger import get_logger
from src.common.utils.utils_message import MessageUtils
from src.config.config import global_config
from src.platform_io import DeliveryBatch, DriverKind, get_platform_io_manager
from src.platform_io.route_key_factory import RouteKeyFactory
from src.plugin_runtime.hook_payloads import deserialize_session_message, serialize_session_message
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry

logger = get_logger("send_service")


def register_send_service_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """注册发送服务内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 实际注册的 Hook 规格列表。
    """

    return registry.register_hook_specs(
        [
            HookSpec(
                name="send_service.after_build_message",
                description="在出站 SessionMessage 构建完成后触发，可改写消息体或取消发送。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "待发送消息的序列化 SessionMessage。",
                        },
                        "stream_id": {
                            "type": "string",
                            "description": "目标会话 ID。",
                        },
                        "processed_plain_text": {
                            "type": "string",
                            "description": "可选的预处理纯文本内容。",
                        },
                        "typing": {
                            "type": "boolean",
                            "description": "是否模拟打字。",
                        },
                        "set_reply": {
                            "type": "boolean",
                            "description": "是否附带引用回复。",
                        },
                        "storage_message": {
                            "type": "boolean",
                            "description": "发送成功后是否写库。",
                        },
                        "show_log": {
                            "type": "boolean",
                            "description": "是否输出发送日志。",
                        },
                    },
                    required=[
                        "message",
                        "stream_id",
                        "processed_plain_text",
                        "typing",
                        "set_reply",
                        "storage_message",
                        "show_log",
                    ],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="send_service.before_send",
                description="在真正调用 Platform IO 发送前触发，可改写消息或取消本次发送。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "待发送消息的序列化 SessionMessage。",
                        },
                        "typing": {
                            "type": "boolean",
                            "description": "是否模拟打字。",
                        },
                        "set_reply": {
                            "type": "boolean",
                            "description": "是否附带引用回复。",
                        },
                        "reply_message_id": {
                            "type": "string",
                            "description": "被引用消息 ID。",
                        },
                        "storage_message": {
                            "type": "boolean",
                            "description": "发送成功后是否写库。",
                        },
                        "show_log": {
                            "type": "boolean",
                            "description": "是否输出发送日志。",
                        },
                    },
                    required=["message", "typing", "set_reply", "storage_message", "show_log"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="send_service.after_send",
                description="在发送流程结束后触发，用于观察最终发送结果。",
                parameters_schema=build_object_schema(
                    {
                        "message": {
                            "type": "object",
                            "description": "本次发送消息的序列化 SessionMessage。",
                        },
                        "sent": {
                            "type": "boolean",
                            "description": "本次发送是否成功。",
                        },
                        "typing": {
                            "type": "boolean",
                            "description": "是否模拟打字。",
                        },
                        "set_reply": {
                            "type": "boolean",
                            "description": "是否附带引用回复。",
                        },
                        "reply_message_id": {
                            "type": "string",
                            "description": "被引用消息 ID。",
                        },
                        "storage_message": {
                            "type": "boolean",
                            "description": "发送成功后是否写库。",
                        },
                        "show_log": {
                            "type": "boolean",
                            "description": "是否输出发送日志。",
                        },
                    },
                    required=["message", "sent", "typing", "set_reply", "storage_message", "show_log"],
                ),
                default_timeout_ms=5000,
                allow_abort=False,
                allow_kwargs_mutation=False,
            ),
        ]
    )


def _get_runtime_manager() -> Any:
    """获取插件运行时管理器。

    Returns:
        Any: 插件运行时管理器单例。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    return get_plugin_runtime_manager()


def _coerce_bool(value: Any, default: bool) -> bool:
    """将任意值安全转换为布尔值。

    Args:
        value: 待转换的值。
        default: 当值为空时使用的默认值。

    Returns:
        bool: 转换后的布尔值。
    """

    if value is None:
        return default
    return bool(value)


async def _invoke_send_hook(
    hook_name: str,
    message: SessionMessage,
    **kwargs: Any,
) -> tuple[HookDispatchResult, SessionMessage]:
    """触发携带出站消息的命名 Hook。

    Args:
        hook_name: 目标 Hook 名称。
        message: 当前待发送消息。
        **kwargs: 需要附带的额外参数。

    Returns:
        tuple[HookDispatchResult, SessionMessage]: Hook 聚合结果以及可能被改写后的消息对象。
    """

    hook_result = await _get_runtime_manager().invoke_hook(
        hook_name,
        message=serialize_session_message(message),
        **kwargs,
    )
    mutated_message = message
    raw_message = hook_result.kwargs.get("message")
    if raw_message is not None:
        try:
            mutated_message = deserialize_session_message(raw_message)
        except Exception as exc:
            logger.warning(f"Hook {hook_name} 返回的 message 无法反序列化，已忽略: {exc}")
    return hook_result, mutated_message


def _inherit_platform_io_route_metadata(target_stream: BotChatSession) -> Dict[str, object]:
    """从目标会话继承 Platform IO 路由元数据。

    Args:
        target_stream: 当前消息要发送到的会话对象。

    Returns:
        Dict[str, object]: 可安全透传到出站消息 ``additional_config`` 中的
        路由辅助字段。
    """
    inherited_metadata: Dict[str, object] = {}

    context_message = target_stream.context.message if target_stream.context else None
    if context_message is not None:
        additional_config = context_message.message_info.additional_config
        if isinstance(additional_config, dict):
            for key in (*RouteKeyFactory.ACCOUNT_ID_KEYS, *RouteKeyFactory.SCOPE_KEYS):
                value = additional_config.get(key)
                if value is None:
                    continue
                normalized_value = str(value).strip()
                if normalized_value:
                    inherited_metadata[key] = value

    # 当目标会话没有可继承的上下文消息时，至少补齐当前平台账号，
    # 让按 ``platform + account_id`` 绑定的路由仍有机会命中。
    if not RouteKeyFactory.extract_components(inherited_metadata)[0]:
        bot_account = get_bot_account(target_stream.platform)
        if bot_account:
            inherited_metadata["platform_io_account_id"] = bot_account

    if target_stream.group_id and (normalized_group_id := str(target_stream.group_id).strip()):
        inherited_metadata["platform_io_target_group_id"] = normalized_group_id

    if target_stream.user_id and (normalized_user_id := str(target_stream.user_id).strip()):
        inherited_metadata["platform_io_target_user_id"] = normalized_user_id

    return inherited_metadata


def _build_binary_component_from_base64(component_type: str, raw_data: str) -> StandardMessageComponents:
    """根据 Base64 数据构造二进制消息组件。

    Args:
        component_type: 组件类型名称。
        raw_data: Base64 编码后的二进制数据。

    Returns:
        StandardMessageComponents: 转换后的内部消息组件。

    Raises:
        ValueError: 当组件类型不受支持时抛出。
    """
    binary_data = base64.b64decode(raw_data)
    binary_hash = hashlib.sha256(binary_data).hexdigest()

    if component_type == "image":
        return ImageComponent(binary_hash=binary_hash, binary_data=binary_data)
    if component_type == "emoji":
        return EmojiComponent(binary_hash=binary_hash, binary_data=binary_data)
    if component_type == "voice":
        return VoiceComponent(binary_hash=binary_hash, binary_data=binary_data)
    raise ValueError(f"不支持的二进制组件类型: {component_type}")


def _build_message_sequence_from_custom_message(
    message_type: str,
    content: str | Dict[str, Any],
) -> MessageSequence:
    """根据自定义消息类型构造内部消息组件序列。

    Args:
        message_type: 自定义消息类型。
        content: 自定义消息内容。

    Returns:
        MessageSequence: 转换后的消息组件序列。
    """
    normalized_type = message_type.strip().lower()

    if normalized_type == "text":
        return MessageSequence(components=[TextComponent(text=str(content))])

    if normalized_type in {"image", "emoji", "voice"}:
        return MessageSequence(components=[_build_binary_component_from_base64(normalized_type, str(content))])

    if normalized_type == "at":
        return MessageSequence(components=[AtComponent(target_user_id=str(content))])

    if normalized_type == "reply":
        return MessageSequence(components=[ReplyComponent(target_message_id=str(content))])

    if normalized_type == "file" and isinstance(content, dict):
        return MessageSequence(components=[FileComponent.from_payload(deepcopy(content))])

    if normalized_type == "dict" and isinstance(content, dict):
        return MessageSequence(components=[DictComponent(data=deepcopy(content))])

    return MessageSequence(
        components=[
            DictComponent(
                data={
                    "type": normalized_type,
                    "data": deepcopy(content),
                }
            )
        ]
    )


def _clone_message_sequence(message_sequence: MessageSequence) -> MessageSequence:
    """复制消息组件序列，避免原对象被发送流程修改。

    Args:
        message_sequence: 原始消息组件序列。

    Returns:
        MessageSequence: 深拷贝后的消息组件序列。
    """
    return deepcopy(message_sequence)


def _detect_outbound_message_flags(message_sequence: MessageSequence) -> Dict[str, bool]:
    """根据消息组件序列推断出站消息标记。

    Args:
        message_sequence: 待发送的消息组件序列。

    Returns:
        Dict[str, bool]: 包含 ``is_emoji``、``is_picture``、``is_command`` 的标记字典。
    """
    if len(message_sequence.components) != 1:
        return {
            "is_emoji": False,
            "is_picture": False,
            "is_command": False,
        }

    component = message_sequence.components[0]
    is_command = False
    if isinstance(component, DictComponent) and isinstance(component.data, dict):
        is_command = str(component.data.get("type") or "").strip().lower() == "command"

    return {
        "is_emoji": isinstance(component, EmojiComponent),
        "is_picture": isinstance(component, ImageComponent),
        "is_command": is_command,
    }


def _describe_message_sequence(message_sequence: MessageSequence) -> str:
    """生成消息组件序列的简短描述文本。

    Args:
        message_sequence: 待描述的消息组件序列。

    Returns:
        str: 适用于日志的简短类型描述。
    """
    if len(message_sequence.components) != 1:
        return "message_sequence"

    component = message_sequence.components[0]
    if isinstance(component, DictComponent) and isinstance(component.data, dict):
        custom_type = str(component.data.get("type") or "").strip()
        return custom_type or "dict"

    if isinstance(component, TextComponent):
        return component.format_name

    if isinstance(component, ImageComponent):
        return component.format_name

    if isinstance(component, EmojiComponent):
        return component.format_name

    if isinstance(component, VoiceComponent):
        return component.format_name

    if isinstance(component, FileComponent):
        return component.format_name

    if isinstance(component, AtComponent):
        return component.format_name

    if isinstance(component, ReplyComponent):
        return component.format_name

    if isinstance(component, ForwardNodeComponent):
        return component.format_name

    return "unknown"


def _build_processed_plain_text(message: SessionMessage) -> str:
    """为出站消息构造轻量纯文本摘要。

    Args:
        message: 待发送的内部消息对象。

    Returns:
        str: 适用于日志与打字时长估算的纯文本摘要。
    """
    processed_parts: List[str] = []
    for component in message.raw_message.components:
        if isinstance(component, TextComponent):
            processed_parts.append(component.text)
            continue

        if isinstance(component, ImageComponent):
            processed_parts.append(component.content.strip() or "[图片]")
            continue

        if isinstance(component, EmojiComponent):
            processed_parts.append(component.content.strip() or "[表情]")
            continue

        if isinstance(component, VoiceComponent):
            processed_parts.append(component.content.strip() or "[语音]")
            continue

        if isinstance(component, FileComponent):
            processed_parts.append(component.to_plain_text())
            continue

        if isinstance(component, AtComponent):
            at_target = component.target_user_cardname or component.target_user_nickname or component.target_user_id
            processed_parts.append(f"@{at_target}")
            continue

        if isinstance(component, ReplyComponent):
            processed_parts.append(component.target_message_content or "[回复消息]")
            continue

        if isinstance(component, DictComponent):
            raw_type = component.data.get("type") if isinstance(component.data, dict) else None
            if isinstance(raw_type, str) and raw_type.strip():
                processed_parts.append(f"[{raw_type.strip()}消息]")
            else:
                processed_parts.append("[自定义消息]")
            continue

    return " ".join(part for part in processed_parts if part)


def _build_outbound_log_preview(message: SessionMessage, max_length: int = 160) -> str:
    """构造出站消息的日志预览文本。

    Args:
        message: 待发送的内部消息对象。
        max_length: 预览文本最大长度。

    Returns:
        str: 适用于日志展示的消息摘要。
    """
    preview_text = (message.processed_plain_text or "").strip()
    if not preview_text:
        preview_text = f"[{_describe_message_sequence(message.raw_message)}]"

    normalized_preview = " ".join(preview_text.split())
    if len(normalized_preview) <= max_length:
        return normalized_preview
    return f"{normalized_preview[:max_length]}..."


def _build_outbound_session_message(
    message_sequence: MessageSequence,
    stream_id: str,
    processed_plain_text: str = "",
    reply_message: Optional[MaiMessage] = None,
    selected_expressions: Optional[List[int]] = None,
) -> Optional[SessionMessage]:
    """根据目标会话构建待发送的内部消息对象。

    Args:
        message_sequence: 待发送的消息组件序列。
        stream_id: 目标会话 ID。
        processed_plain_text: 可选的预处理纯文本内容。
        reply_message: 被回复的锚点消息。
        selected_expressions: 可选的表情候选索引列表。

    Returns:
        Optional[SessionMessage]: 构建成功时返回内部消息对象；若目标会话或
        机器人账号不存在，则返回 ``None``。
    """
    target_stream = _chat_manager.get_session_by_session_id(stream_id)
    if target_stream is None:
        logger.error(f"[SendService] 未找到聊天流: {stream_id}")
        return None

    bot_user_id = get_bot_account(target_stream.platform)
    if not bot_user_id:
        logger.error(f"[SendService] 平台 {target_stream.platform} 未配置机器人账号，无法发送消息")
        return None

    current_time = time.time()
    message_id = f"send_api_{int(current_time * 1000)}"
    anchor_message = reply_message.deepcopy() if reply_message is not None else None

    group_info: Optional[GroupInfo] = None
    if target_stream.group_id:
        group_name = str(target_stream.group_name or "").strip()
        if (
            not group_name
            and target_stream.context
            and target_stream.context.message
            and target_stream.context.message.message_info.group_info
        ):
            group_name = target_stream.context.message.message_info.group_info.group_name
        if group_name:
            group_info = GroupInfo(
                group_id=target_stream.group_id,
                group_name=group_name,
            )

    additional_config: Dict[str, object] = _inherit_platform_io_route_metadata(target_stream)
    if selected_expressions is not None:
        additional_config["selected_expressions"] = selected_expressions

    outbound_message = SessionMessage(
        message_id=message_id,
        timestamp=datetime.fromtimestamp(current_time),
        platform=target_stream.platform,
    )
    outbound_message.message_info = MessageInfo(
        user_info=UserInfo(
            user_id=bot_user_id,
            user_nickname=global_config.bot.nickname,
        ),
        group_info=group_info,
        additional_config=additional_config,
    )
    outbound_message.raw_message = _clone_message_sequence(message_sequence)
    outbound_message.session_id = target_stream.session_id
    outbound_message.processed_plain_text = processed_plain_text.strip() or _build_processed_plain_text(
        outbound_message
    )
    outbound_message.reply_to = anchor_message.message_id if anchor_message is not None else None
    message_flags = _detect_outbound_message_flags(outbound_message.raw_message)
    outbound_message.is_emoji = message_flags["is_emoji"]
    outbound_message.is_picture = message_flags["is_picture"]
    outbound_message.is_command = message_flags["is_command"]
    outbound_message.initialized = True
    return outbound_message


def _build_reply_component(reply_message_id: str, reply_message: Optional[MaiMessage]) -> ReplyComponent:
    """根据被引用消息构造包含预览信息的回复组件。"""
    if reply_message is None or reply_message.message_id != reply_message_id:
        return ReplyComponent(target_message_id=reply_message_id)

    user_info = reply_message.message_info.user_info
    return ReplyComponent(
        target_message_id=reply_message_id,
        target_message_content=reply_message.processed_plain_text or "",
        target_message_sender_id=user_info.user_id,
        target_message_sender_nickname=user_info.user_nickname,
        target_message_sender_cardname=user_info.user_cardname,
    )


def _fill_reply_component(component: ReplyComponent, reply_message: Optional[MaiMessage]) -> None:
    """补齐已存在回复组件中的目标消息预览信息。"""
    if reply_message is None or reply_message.message_id != component.target_message_id:
        return

    user_info = reply_message.message_info.user_info
    if not component.target_message_content:
        component.target_message_content = reply_message.processed_plain_text or ""
    if not component.target_message_sender_id:
        component.target_message_sender_id = user_info.user_id
    if not component.target_message_sender_nickname:
        component.target_message_sender_nickname = user_info.user_nickname
    if not component.target_message_sender_cardname:
        component.target_message_sender_cardname = user_info.user_cardname


def _ensure_reply_component(
    message: SessionMessage,
    reply_message_id: str,
    reply_message: Optional[MaiMessage] = None,
) -> None:
    """为消息补充回复组件。

    Args:
        message: 待发送的内部消息对象。
        reply_message_id: 被引用消息的 ID。
    """
    if message.raw_message.components:
        first_component = message.raw_message.components[0]
        if isinstance(first_component, ReplyComponent) and first_component.target_message_id == reply_message_id:
            _fill_reply_component(first_component, reply_message)
            return

    message.raw_message.components.insert(0, _build_reply_component(reply_message_id, reply_message))


async def _prepare_message_for_platform_io(
    message: SessionMessage,
    *,
    typing: bool,
    set_reply: bool,
    reply_message_id: Optional[str],
    reply_message: Optional[MaiMessage] = None,
) -> None:
    """为 Platform IO 发送链预处理消息。

    Args:
        message: 待发送的内部消息对象。
        typing: 是否模拟打字等待。
        set_reply: 是否构建引用回复组件。
        reply_message_id: 被引用消息的 ID。

    Raises:
        ValueError: 当要求设置引用回复但缺少 ``reply_message_id`` 时抛出。
    """
    if set_reply:
        if not reply_message_id:
            raise ValueError("set_reply=True 时必须提供 reply_message_id")
        _ensure_reply_component(message, reply_message_id, reply_message)

    if set_reply or not message.processed_plain_text:
        message.processed_plain_text = _build_processed_plain_text(message)
    if typing:
        typing_time = calculate_typing_time(
            input_string=message.processed_plain_text or "",
            is_emoji=message.is_emoji,
        )
        await asyncio.sleep(typing_time)


async def _store_sent_message(message: SessionMessage) -> None:
    """将已成功发送的消息写入数据库。

    Args:
        message: 已成功发送的内部消息对象。
    """
    await MessageUtils.store_message_to_db_async(message)


async def _apply_successful_delivery_receipt(message: SessionMessage, delivery_batch: DeliveryBatch) -> None:
    """将成功回执中的平台消息 ID 回填到内部消息。

    Args:
        message: 已发送成功的内部消息对象。
        delivery_batch: Platform IO 返回的批量回执。
    """
    if not delivery_batch.sent_receipts:
        return

    original_message_id = str(message.message_id or "").strip()
    external_message_id = str(delivery_batch.sent_receipts[0].external_message_id or "").strip()
    if not external_message_id or external_message_id == original_message_id:
        return

    message.message_id = external_message_id


async def _dispatch_adapter_callbacks(delivery_batch: DeliveryBatch) -> None:
    """分发适配器随成功回执返回的自定义回调。

    Args:
        delivery_batch: Platform IO 返回的批量回执。
    """
    try:
        from src.common.message_server import api as message_server_api

        global_api = getattr(message_server_api, "global_api", None)
        custom_handlers = getattr(global_api, "_custom_message_handlers", None)
        if not isinstance(custom_handlers, dict):
            return

        for receipt in delivery_batch.sent_receipts:
            raw_callbacks = receipt.metadata.get("adapter_callbacks")
            if not isinstance(raw_callbacks, list):
                continue

            for raw_callback in raw_callbacks:
                if not isinstance(raw_callback, dict):
                    continue

                callback_name = str(raw_callback.get("name") or "").strip()
                payload = raw_callback.get("payload")
                if not callback_name or not isinstance(payload, dict):
                    continue

                handler = custom_handlers.get(callback_name)
                if handler is None:
                    continue

                await handler(payload)
    except Exception as exc:
        logger.warning(f"[SendService] 分发适配器回调失败: {exc}")


async def _notify_memory_automation_on_message_sent(message: SessionMessage) -> None:
    """在发送成功后通知长期记忆自动化服务。

    Args:
        message: 已成功发送的内部消息对象。
    """
    try:
        from src.services.memory_flow_service import memory_automation_service

        await memory_automation_service.on_message_sent(message)
    except Exception as exc:
        session_id = message.session_id or "unknown-session"
        logger.warning(f"[{session_id}] 长期记忆人物事实写回注册失败: {exc}")


def _record_sent_emoji_usage(message: SessionMessage) -> None:
    """在消息真实发送成功后记录其中表情包的使用次数。"""

    emoji_hashes = [
        component.binary_hash.strip()
        for component in message.raw_message.components
        if isinstance(component, EmojiComponent) and component.binary_hash.strip()
    ]
    if not emoji_hashes:
        return

    try:
        from src.emoji_system.emoji_manager import emoji_manager

        for emoji_hash in emoji_hashes:
            emoji_manager.update_emoji_usage_by_hash(emoji_hash, log_missing=False)
    except Exception as exc:
        logger.warning(f"[SendService] 记录表情包使用次数失败: {exc}")


def _sync_sent_message_to_maisaka_history(
    message: SessionMessage,
    *,
    source_kind: str,
) -> None:
    """将已发送成功的消息同步到当前会话对应的 Maisaka 历史。"""

    session_id = str(message.session_id or "").strip()
    if not session_id:
        return

    try:
        from src.chat.heart_flow.heartflow_manager import heartflow_manager

        runtime = heartflow_manager.heartflow_chat_list.get(session_id)
        if runtime is None:
            return
        runtime.append_sent_message_to_chat_history(message, source_kind=source_kind)
    except Exception as exc:
        logger.warning(f"[SendService] 同步消息到 Maisaka 历史失败: session_id={session_id} error={exc}")


def _log_platform_io_failures(delivery_batch: DeliveryBatch) -> None:
    """输出 Platform IO 批量发送失败详情。

    Args:
        delivery_batch: Platform IO 返回的批量回执。
    """
    failed_details = (
        "; ".join(
            f"driver={receipt.driver_id} status={receipt.status} error={receipt.error}"
            for receipt in delivery_batch.failed_receipts
        )
        or "未命中任何发送路由"
    )
    logger.warning(f"[SendService] Platform IO 发送失败: platform={delivery_batch.route_key.platform} {failed_details}")


async def _send_via_platform_io(
    message: SessionMessage,
    *,
    typing: bool,
    set_reply: bool,
    reply_message_id: Optional[str],
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool,
    show_log: bool,
) -> Optional[SessionMessage]:
    """通过 Platform IO 发送消息。

    Args:
        message: 待发送的内部消息对象。
        typing: 是否模拟打字等待。
        set_reply: 是否设置引用回复。
        reply_message_id: 被引用消息的 ID。
        storage_message: 发送成功后是否写入数据库。
        show_log: 是否输出发送成功日志。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    before_send_result, message = await _invoke_send_hook(
        "send_service.before_send",
        message,
        typing=typing,
        set_reply=set_reply,
        reply_message_id=reply_message_id,
        storage_message=storage_message,
        show_log=show_log,
    )
    if before_send_result.aborted:
        logger.info(f"[SendService] 消息 {message.message_id} 在发送前被 Hook 中止")
        return None

    before_kwargs = before_send_result.kwargs
    typing = _coerce_bool(before_kwargs.get("typing"), typing)
    set_reply = _coerce_bool(before_kwargs.get("set_reply"), set_reply)
    storage_message = _coerce_bool(before_kwargs.get("storage_message"), storage_message)
    show_log = _coerce_bool(before_kwargs.get("show_log"), show_log)
    raw_reply_message_id = before_kwargs.get("reply_message_id", reply_message_id)
    reply_message_id = None if raw_reply_message_id in {None, ""} else str(raw_reply_message_id)

    platform_io_manager = get_platform_io_manager()
    try:
        await platform_io_manager.ensure_send_pipeline_ready()
    except Exception as exc:
        logger.error(f"[SendService] 准备 Platform IO 发送管线失败: {exc}")
        logger.debug(traceback.format_exc())
        return None

    try:
        route_key = platform_io_manager.build_route_key_from_message(message)
    except Exception as exc:
        logger.warning(f"[SendService] 根据消息构造 Platform IO 路由键失败: {exc}")
        return None

    try:
        await _prepare_message_for_platform_io(
            message,
            typing=typing,
            set_reply=set_reply,
            reply_message_id=reply_message_id,
            reply_message=reply_message,
        )
        delivery_batch = await platform_io_manager.send_message(
            message,
            route_key,
            metadata={"show_log": False},
        )
    except Exception as exc:
        logger.error(f"[SendService] Platform IO 发送异常: {exc}")
        logger.debug(traceback.format_exc())
        return None

    sent = bool(delivery_batch.has_success)
    if sent:
        await _apply_successful_delivery_receipt(message, delivery_batch)
        await _dispatch_adapter_callbacks(delivery_batch)
    await _invoke_send_hook(
        "send_service.after_send",
        message,
        sent=sent,
        typing=typing,
        set_reply=set_reply,
        reply_message_id=reply_message_id,
        storage_message=storage_message,
        show_log=show_log,
    )

    if delivery_batch.has_success:
        _record_sent_emoji_usage(message)
        if storage_message:
            await _store_sent_message(message)
        await _notify_memory_automation_on_message_sent(message)
        should_log_delivery = show_log and any(
            receipt.driver_kind != DriverKind.LOCAL
            for receipt in delivery_batch.sent_receipts
        )
        if should_log_delivery:
            successful_driver_ids = [receipt.driver_id or "unknown" for receipt in delivery_batch.sent_receipts]
            logger.info(
                f"已通过 Platform IO 将消息发往平台 '{route_key.platform}' "
                f"(drivers: {', '.join(successful_driver_ids)}) "
                f"message={_build_outbound_log_preview(message)}"
            )
        return message

    _log_platform_io_failures(delivery_batch)
    return None


async def send_session_message_with_message(
    message: SessionMessage,
    *,
    typing: bool = False,
    set_reply: bool = False,
    reply_message_id: Optional[str] = None,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    show_log: bool = True,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> Optional[SessionMessage]:
    """统一发送一条内部消息，并返回最终发送成功的消息对象。"""
    if not message.message_id:
        logger.error("[SendService] 消息缺少 message_id，无法发送")
        raise ValueError("消息缺少 message_id，无法发送")

    sent_message = await _send_via_platform_io(
        message,
        typing=typing,
        set_reply=set_reply,
        reply_message_id=reply_message_id,
        reply_message=reply_message,
        storage_message=storage_message,
        show_log=show_log,
    )
    if sent_message is not None and sync_to_maisaka_history:
        _sync_sent_message_to_maisaka_history(
            sent_message,
            source_kind=str(maisaka_source_kind or "outbound_send"),
        )
    return sent_message


async def send_session_message(
    message: SessionMessage,
    *,
    typing: bool = False,
    set_reply: bool = False,
    reply_message_id: Optional[str] = None,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    show_log: bool = True,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """统一发送一条内部消息。

    该方法是内部模块的统一发送入口：

    1. 构造并维护内部消息对象。
    2. 由 Platform IO 统一决定走插件链还是 legacy 旧链。
    3. send service 不再自行判断底层发送路径。

    Args:
        message: 待发送的内部消息对象。
        typing: 是否模拟打字等待。
        set_reply: 是否设置引用回复。
        reply_message_id: 被引用消息的 ID。
        storage_message: 发送成功后是否写入数据库。
        show_log: 是否输出发送日志。

    Returns:
        bool: 发送成功时返回 ``True``，否则返回 ``False``。
    """
    if not message.message_id:
        logger.error("[SendService] 消息缺少 message_id，无法发送")
        raise ValueError("消息缺少 message_id，无法发送")

    return (
        await send_session_message_with_message(
            message,
            typing=typing,
            set_reply=set_reply,
            reply_message_id=reply_message_id,
            reply_message=reply_message,
            storage_message=storage_message,
            show_log=show_log,
            sync_to_maisaka_history=sync_to_maisaka_history,
            maisaka_source_kind=maisaka_source_kind,
        )
        is not None
    )


async def _send_to_target(
    message_sequence: MessageSequence,
    stream_id: str,
    processed_plain_text: str = "",
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    show_log: bool = True,
    selected_expressions: Optional[List[int]] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """向指定目标构建并发送消息，并返回是否发送成功。"""
    return (
        await _send_to_target_with_message(
            message_sequence=message_sequence,
            stream_id=stream_id,
            processed_plain_text=processed_plain_text,
            typing=typing,
            set_reply=set_reply,
            reply_message=reply_message,
            storage_message=storage_message,
            show_log=show_log,
            selected_expressions=selected_expressions,
            sync_to_maisaka_history=sync_to_maisaka_history,
            maisaka_source_kind=maisaka_source_kind,
        )
        is not None
    )


async def _send_to_target_with_message(
    message_sequence: MessageSequence,
    stream_id: str,
    processed_plain_text: str = "",
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    show_log: bool = True,
    selected_expressions: Optional[List[int]] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> Optional[SessionMessage]:
    """向指定目标构建并发送消息。

    Args:
        message_sequence: 待发送的消息组件序列。
        stream_id: 目标会话 ID。
        processed_plain_text: 可选的预处理纯文本内容。
        typing: 是否显示输入中状态。
        set_reply: 是否在发送时附带引用回复。
        reply_message: 被回复的消息对象。
        storage_message: 是否将发送结果写入消息存储。
        show_log: 是否输出发送日志。
        selected_expressions: 可选的表情候选索引列表。

    Returns:
        bool: 发送成功返回 ``True``，否则返回 ``False``。
    """
    try:
        if set_reply and reply_message is None:
            logger.warning("[SendService] 使用引用回复，但未提供回复消息")
            return None

        if show_log:
            logger.debug(f"[SendService] 发送{_describe_message_sequence(message_sequence)}消息到 {stream_id}")

        outbound_message = _build_outbound_session_message(
            message_sequence=message_sequence,
            stream_id=stream_id,
            processed_plain_text=processed_plain_text,
            reply_message=reply_message,
            selected_expressions=selected_expressions,
        )
        if outbound_message is None:
            return None

        after_build_result, outbound_message = await _invoke_send_hook(
            "send_service.after_build_message",
            outbound_message,
            stream_id=stream_id,
            processed_plain_text=processed_plain_text,
            typing=typing,
            set_reply=set_reply,
            storage_message=storage_message,
            show_log=show_log,
        )
        if after_build_result.aborted:
            logger.info(f"[SendService] 消息 {outbound_message.message_id} 在构建后被 Hook 中止")
            return None

        after_build_kwargs = after_build_result.kwargs
        typing = _coerce_bool(after_build_kwargs.get("typing"), typing)
        set_reply = _coerce_bool(after_build_kwargs.get("set_reply"), set_reply)
        storage_message = _coerce_bool(after_build_kwargs.get("storage_message"), storage_message)
        show_log = _coerce_bool(after_build_kwargs.get("show_log"), show_log)

        sent_message = await send_session_message_with_message(
            outbound_message,
            typing=typing,
            set_reply=set_reply,
            reply_message_id=reply_message.message_id if reply_message is not None else None,
            reply_message=reply_message,
            storage_message=storage_message,
            show_log=show_log,
            sync_to_maisaka_history=sync_to_maisaka_history,
            maisaka_source_kind=maisaka_source_kind,
        )
        if sent_message is not None:
            logger.debug(f"[SendService] 成功发送消息到 {stream_id}")
            return sent_message

        logger.error("[SendService] 发送消息失败")
        return None
    except Exception as exc:
        logger.error(f"[SendService] 发送消息时出错: {exc}")
        traceback.print_exc()
        return None


async def text_to_stream_with_message(
    text: str,
    stream_id: str,
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    selected_expressions: Optional[List[int]] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> Optional[SessionMessage]:
    """向指定流发送文本消息，并返回发送成功后的消息对象。"""
    return await _send_to_target_with_message(
        message_sequence=MessageSequence(components=[TextComponent(text=text)]),
        stream_id=stream_id,
        typing=typing,
        set_reply=set_reply,
        reply_message=reply_message,
        storage_message=storage_message,
        selected_expressions=selected_expressions,
        sync_to_maisaka_history=sync_to_maisaka_history,
        maisaka_source_kind=maisaka_source_kind,
    )


async def text_to_stream(
    text: str,
    stream_id: str,
    typing: bool = False,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    storage_message: bool = True,
    selected_expressions: Optional[List[int]] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """向指定流发送文本消息。

    Args:
        text: 要发送的文本内容。
        stream_id: 目标会话 ID。
        typing: 是否显示输入中状态。
        set_reply: 是否附带引用回复。
        reply_message: 被回复的消息对象。
        storage_message: 是否在发送成功后写入数据库。
        selected_expressions: 可选的表情候选索引列表。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    return (
        await text_to_stream_with_message(
            text=text,
            stream_id=stream_id,
            typing=typing,
            set_reply=set_reply,
            reply_message=reply_message,
            storage_message=storage_message,
            selected_expressions=selected_expressions,
            sync_to_maisaka_history=sync_to_maisaka_history,
            maisaka_source_kind=maisaka_source_kind,
        )
        is not None
    )


async def emoji_to_stream_with_message(
    emoji_base64: str,
    stream_id: str,
    storage_message: bool = True,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> Optional[SessionMessage]:
    """向指定流发送表情消息，并返回发送成功后的消息对象。"""
    return await _send_to_target_with_message(
        message_sequence=_build_message_sequence_from_custom_message("emoji", emoji_base64),
        stream_id=stream_id,
        typing=False,
        storage_message=storage_message,
        set_reply=set_reply,
        reply_message=reply_message,
        sync_to_maisaka_history=sync_to_maisaka_history,
        maisaka_source_kind=maisaka_source_kind,
    )


async def emoji_to_stream(
    emoji_base64: str,
    stream_id: str,
    storage_message: bool = True,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """向指定流发送表情消息。

    Args:
        emoji_base64: 表情图片的 Base64 内容。
        stream_id: 目标会话 ID。
        storage_message: 是否在发送成功后写入数据库。
        set_reply: 是否附带引用回复。
        reply_message: 被回复的消息对象。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    return (
        await emoji_to_stream_with_message(
            emoji_base64=emoji_base64,
            stream_id=stream_id,
            storage_message=storage_message,
            set_reply=set_reply,
            reply_message=reply_message,
            sync_to_maisaka_history=sync_to_maisaka_history,
            maisaka_source_kind=maisaka_source_kind,
        )
        is not None
    )


async def image_to_stream(
    image_base64: str,
    stream_id: str,
    storage_message: bool = True,
    set_reply: bool = False,
    reply_message: Optional[MaiMessage] = None,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """向指定流发送图片消息。

    Args:
        image_base64: 图片的 Base64 内容。
        stream_id: 目标会话 ID。
        storage_message: 是否在发送成功后写入数据库。
        set_reply: 是否附带引用回复。
        reply_message: 被回复的消息对象。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    return await _send_to_target(
        message_sequence=_build_message_sequence_from_custom_message("image", image_base64),
        stream_id=stream_id,
        typing=False,
        storage_message=storage_message,
        set_reply=set_reply,
        reply_message=reply_message,
        sync_to_maisaka_history=sync_to_maisaka_history,
        maisaka_source_kind=maisaka_source_kind,
    )


async def custom_to_stream(
    message_type: str,
    content: str | Dict[str, Any],
    stream_id: str,
    processed_plain_text: str = "",
    typing: bool = False,
    reply_message: Optional[MaiMessage] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """向指定流发送自定义类型消息。

    Args:
        message_type: 自定义消息类型。
        content: 自定义消息内容。
        stream_id: 目标会话 ID。
        processed_plain_text: 可选的预处理纯文本内容。
        typing: 是否显示输入中状态。
        reply_message: 被回复的消息对象。
        set_reply: 是否附带引用回复。
        storage_message: 是否在发送成功后写入数据库。
        show_log: 是否输出发送日志。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    return await _send_to_target(
        message_sequence=_build_message_sequence_from_custom_message(message_type, content),
        stream_id=stream_id,
        processed_plain_text=processed_plain_text,
        typing=typing,
        reply_message=reply_message,
        set_reply=set_reply,
        storage_message=storage_message,
        show_log=show_log,
        sync_to_maisaka_history=sync_to_maisaka_history,
        maisaka_source_kind=maisaka_source_kind,
    )


async def custom_reply_set_to_stream(
    reply_set: MessageSequence,
    stream_id: str,
    processed_plain_text: str = "",
    typing: bool = False,
    reply_message: Optional[MaiMessage] = None,
    set_reply: bool = False,
    storage_message: bool = True,
    show_log: bool = True,
    sync_to_maisaka_history: bool = False,
    maisaka_source_kind: str = "outbound_send",
) -> bool:
    """向指定流发送消息组件序列。

    Args:
        reply_set: 待发送的消息组件序列。
        stream_id: 目标会话 ID。
        processed_plain_text: 可选的预处理纯文本内容。
        typing: 是否显示输入中状态。
        reply_message: 被回复的消息对象。
        set_reply: 是否附带引用回复。
        storage_message: 是否在发送成功后写入数据库。
        show_log: 是否输出发送日志。

    Returns:
        bool: 发送成功时返回 ``True``。
    """
    return await _send_to_target(
        message_sequence=reply_set,
        stream_id=stream_id,
        processed_plain_text=processed_plain_text,
        typing=typing,
        reply_message=reply_message,
        set_reply=set_reply,
        storage_message=storage_message,
        show_log=show_log,
        sync_to_maisaka_history=sync_to_maisaka_history,
        maisaka_source_kind=maisaka_source_kind,
    )
