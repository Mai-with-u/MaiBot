from typing import Any, Optional, Tuple

import asyncio
import traceback

from rich.traceback import install

from src.chat.message_receive.message import SessionMessage
from src.chat.utils.utils import calculate_typing_time, truncate_message
from src.common.data_models.message_component_data_model import ReplyComponent
from src.common.database.database import get_db_session
from src.common.logger import get_logger
from src.common.message_server.api import get_global_api
from src.webui.routers.chat.serializers import serialize_message_sequence

install(extra_lines=3)

logger = get_logger("sender")

# WebUI 聊天室的消息广播器（延迟导入避免循环依赖）
_webui_chat_broadcaster: Optional[Tuple[Any, Optional[str]]] = None

# 虚拟群 ID 前缀（与 chat_routes.py 保持一致）
VIRTUAL_GROUP_ID_PREFIX = "webui_virtual_group_"


# TODO: 重构完成后完成webui相关
def get_webui_chat_broadcaster() -> Tuple[Any, Optional[str]]:
    """获取 WebUI 聊天室广播器。

    Returns:
        Tuple[Any, Optional[str]]: ``(chat_manager, platform_name)`` 二元组；
        若 WebUI 相关模块不可用，则元素会退化为 ``None``。
    """
    global _webui_chat_broadcaster
    if _webui_chat_broadcaster is None:
        try:
            from src.webui.routers.chat import WEBUI_CHAT_PLATFORM, chat_manager

            _webui_chat_broadcaster = (chat_manager, WEBUI_CHAT_PLATFORM)
        except ImportError:
            _webui_chat_broadcaster = (None, None)
    return _webui_chat_broadcaster


def is_webui_virtual_group(group_id: str) -> bool:
    """检查是否是 WebUI 虚拟群。

    Args:
        group_id: 待判断的群 ID。

    Returns:
        bool: 若群 ID 属于 WebUI 虚拟群则返回 ``True``。
    """
    return bool(group_id) and group_id.startswith(VIRTUAL_GROUP_ID_PREFIX)


async def _send_message(message: SessionMessage, show_log: bool = True) -> bool:
    """执行统一的消息发送流程。

    发送顺序为：
    1. WebUI 特殊链路
    2. Platform IO 适配器链路
    3. 旧版 ``maim_message`` / API Server 链路

    Args:
        message: 待发送的内部会话消息。
        show_log: 是否输出发送成功日志。

    Returns:
        bool: 是否最终发送成功。
    """
    message_preview = truncate_message(message.processed_plain_text, max_length=200)
    platform = message.platform
    group_info = message.message_info.group_info
    group_id = group_info.group_id if group_info is not None else ""

    try:
        # 检查是否是 WebUI 平台的消息，或者是 WebUI 虚拟群的消息
        chat_manager, webui_platform = get_webui_chat_broadcaster()
        is_webui_message = (platform == webui_platform) or is_webui_virtual_group(group_id)

        if is_webui_message and chat_manager is not None:
            # WebUI 聊天室消息（包括虚拟身份模式），通过 WebSocket 广播
            import time
            from src.config.config import global_config

            # 解析消息段，获取富文本内容
            message_segments = serialize_message_sequence(message.raw_message)

            # 判断消息类型
            # 如果只有一个文本段，使用简单的 text 类型
            # 否则使用 rich 类型，包含完整的消息段
            if len(message_segments) == 1 and message_segments[0].get("type") == "text":
                message_type = "text"
                segments = None
            else:
                message_type = "rich"
                segments = message_segments

            await chat_manager.broadcast(
                {
                    "type": "bot_message",
                    "content": message.processed_plain_text,
                    "message_type": message_type,
                    "segments": segments,  # 富文本消息段
                    "timestamp": time.time(),
                    "group_id": group_id,  # 包含群 ID 以便前端区分不同的聊天标签
                    "sender": {
                        "name": global_config.bot.nickname,
                        "avatar": None,
                        "is_bot": True,
                    },
                }
            )

            # 注意：机器人消息会由 MessageStorage.store_message 自动保存到数据库
            # 无需手动保存

            if show_log:
                if is_webui_virtual_group(group_id):
                    logger.info(f"已将消息  '{message_preview}'  发往 WebUI 虚拟群 (平台: {platform})")
                else:
                    logger.info(f"已将消息  '{message_preview}'  发往 WebUI 聊天室")
            return True

        try:
            from src.platform_io import DeliveryStatus
            from src.plugin_runtime.integration import get_plugin_runtime_manager

            receipt = await get_plugin_runtime_manager().try_send_message_via_platform_io(message)
            if receipt is not None:
                if receipt.status == DeliveryStatus.SENT:
                    if show_log:
                        logger.info(
                            f"已通过 Platform IO 将消息 '{message_preview}' 发往平台'{platform}' "
                            f"(driver: {receipt.driver_id or 'unknown'})"
                        )
                    return True

                logger.warning(
                    f"Platform IO 发送失败: platform={platform} driver={receipt.driver_id} "
                    f"status={receipt.status} error={receipt.error}"
                )
                return False
        except Exception as exc:
            logger.warning(f"检查 Platform IO 出站链路时出现异常，将回退旧发送链: {exc}")

        # Fallback 逻辑: 尝试通过 API Server 发送
        async def send_with_new_api(legacy_exception: Optional[Exception] = None) -> bool:
            """通过 API Server 回退链路发送消息。

            Args:
                legacy_exception: 旧发送链已经抛出的异常；若回退也失败，则重新抛出。

            Returns:
                bool: 回退链路是否发送成功。
            """
            try:
                from src.config.config import global_config

                # 如果未开启 API Server，直接跳过 Fallback
                if not global_config.maim_message.enable_api_server:
                    logger.debug("[API Server Fallback] API Server未开启，跳过fallback")
                    if legacy_exception:
                        raise legacy_exception
                    return False

                global_api = get_global_api()
                extra_server = getattr(global_api, "extra_server", None)

                if not extra_server:
                    logger.warning("[API Server Fallback] extra_server不存在")
                    if legacy_exception:
                        raise legacy_exception
                    return False

                if not extra_server.is_running():
                    logger.warning("[API Server Fallback] extra_server未运行")
                    if legacy_exception:
                        raise legacy_exception
                    return False

                # Fallback: 使用极其简单的 Platform -> API Key 映射
                # 只有收到过该平台的消息，我们才知道该平台的 API Key，才能回传消息
                platform_map = getattr(global_api, "platform_map", {})
                logger.debug(f"[API Server Fallback] platform_map: {platform_map}, 目标平台: '{platform}'")
                target_api_key = platform_map.get(platform)

                if not target_api_key:
                    logger.warning(f"[API Server Fallback] 未找到平台'{platform}'的API Key映射")
                    if legacy_exception:
                        raise legacy_exception
                    return False

                # 使用 MessageConverter 转换为 API 消息
                from maim_message import MessageConverter

                # 新架构：通过 to_maim_message() 转换，内部已处理私聊/群聊的 user_info 差异
                message_base = await message.to_maim_message()

                api_message = MessageConverter.to_api_send(
                    message=message_base,
                    api_key=target_api_key,
                    platform=platform,
                )

                # 直接调用 Server 的 send_message 接口，它会自动处理路由
                logger.debug("[API Server Fallback] 正在通过extra_server发送消息...")
                results = await extra_server.send_message(api_message)
                logger.debug(f"[API Server Fallback] 发送结果: {results}")

                # 检查是否有任何连接发送成功
                if any(results.values()):
                    if show_log:
                        logger.info(
                            f"已通过API Server Fallback将消息 '{message_preview}' 发往平台'{platform}' (key: {target_api_key})"
                        )
                    return True
                else:
                    logger.warning(f"[API Server Fallback] 没有连接发送成功, results={results}")
            except Exception as e:
                logger.error(f"[API Server Fallback] 发生异常: {e}")
                import traceback

                logger.debug(traceback.format_exc())

            # 如果 Fallback 失败，且存在 legacy 异常，则抛出 legacy 异常
            if legacy_exception:
                raise legacy_exception
            return False

        try:
            message_base = await message.to_maim_message()
            send_result = await get_global_api().send_message(message_base)
            if send_result:
                if show_log:
                    logger.info(f"已将消息  '{message_preview}'  发往平台'{message.platform}'")
                return True
            else:
                # Legacy API 返回 False (发送失败但未报错)，尝试 Fallback
                fallback_result = await send_with_new_api()
                if fallback_result and show_log:
                    # Fallback成功的日志已在send_with_new_api中打印
                    pass
                return fallback_result

        except Exception as legacy_e:
            # Legacy API 抛出异常，尝试 Fallback
            # 如果 Fallback 也失败，将重新抛出 legacy_e
            return await send_with_new_api(legacy_exception=legacy_e)

    except Exception as e:
        logger.error(f"发送消息   '{message_preview}'   发往平台'{message.platform}' 失败: {str(e)}")
        traceback.print_exc()
        raise e  # 重新抛出其他异常


class UniversalMessageSender:
    """管理消息的注册、即时处理、发送和存储，并跟踪思考状态。"""

    def __init__(self) -> None:
        """初始化统一消息发送器。"""
        pass

    async def send_message(
        self,
        message: "SessionMessage",
        typing: bool = False,
        set_reply: bool = False,
        reply_message_id: Optional[str] = None,
        storage_message: bool = True,
        show_log: bool = True,
    ) -> bool:
        """
        处理、发送并存储一条消息。

        参数：
            message: MessageSession 对象，待发送的消息。
            typing: 是否模拟打字等待。
            set_reply: 是否构建回复引用消息。


        用法：
            - typing=True 时，发送前会有打字等待。
        """
        if not message.message_id:
            logger.error("消息缺少 message_id，无法发送")
            raise ValueError("消息缺少 message_id，无法发送")

        chat_id = message.session_id
        message_id = message.message_id

        try:
            if set_reply:
                if not reply_message_id:
                    raise ValueError("set_reply=True 时必须提供 reply_message_id")
                message.raw_message.components.insert(0, ReplyComponent(reply_message_id))

            # TODO: fix
            # from src.core.event_bus import event_bus
            # from src.chat.event_helpers import build_event_message
            # from src.core.types import EventType

            # _event_msg = build_event_message(EventType.POST_SEND_PRE_PROCESS, message=message, stream_id=chat_id)
            # continue_flag, modified_message = await event_bus.emit(EventType.POST_SEND_PRE_PROCESS, _event_msg)
            # if not continue_flag:
            #     logger.info(f"[{chat_id}] 消息发送被插件取消: {str(message.message_segment)[:100]}...")
            #     return False
            # if modified_message:
            #     if modified_message._modify_flags.modify_message_segments:
            #         message.message_segment = Seg(type="seglist", data=modified_message.message_segments)
            #     if modified_message._modify_flags.modify_plain_text:
            #         logger.warning(f"[{chat_id}] 插件修改了消息的纯文本内容，可能导致此内容被覆盖。")
            #         message.processed_plain_text = modified_message.plain_text

            await message.process()

            # TODO: fix
            # _event_msg = build_event_message(EventType.POST_SEND, message=message, stream_id=chat_id)
            # continue_flag, modified_message = await event_bus.emit(EventType.POST_SEND, _event_msg)
            # if not continue_flag:
            #     logger.info(f"[{chat_id}] 消息发送被插件取消: {str(message.message_segment)[:100]}...")
            #     return False
            # if modified_message:
            #     if modified_message._modify_flags.modify_message_segments:
            #         message.message_segment = Seg(type="seglist", data=modified_message.message_segments)
            #     if modified_message._modify_flags.modify_plain_text:
            #         message.processed_plain_text = modified_message.plain_text

            if typing:
                typing_time = calculate_typing_time(
                    input_string=message.processed_plain_text,  # type: ignore
                    is_emoji=message.is_emoji,
                )
                await asyncio.sleep(typing_time)

            sent_msg = await _send_message(message, show_log=show_log)
            if not sent_msg:
                return False

            # _event_msg = build_event_message(EventType.AFTER_SEND, message=message, stream_id=chat_id)
            # continue_flag, modified_message = await event_bus.emit(EventType.AFTER_SEND, _event_msg)
            # if not continue_flag:
            #     logger.info(f"[{chat_id}] 消息发送后续处理被插件取消: {str(message.message_segment)[:100]}...")
            #     return True
            # if modified_message:
            #     if modified_message._modify_flags.modify_message_segments:
            #         message.message_segment = Seg(type="seglist", data=modified_message.message_segments)
            #     if modified_message._modify_flags.modify_plain_text:
            #         message.processed_plain_text = modified_message.plain_text

            if storage_message:
                with get_db_session() as db_session:
                    db_session.add(message.to_db_instance())

            return sent_msg

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e
