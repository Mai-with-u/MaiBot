import asyncio
import traceback

from rich.traceback import install
from maim_message import Seg

from src.common.message_server.api import get_global_api
from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.chat.message_receive.message import MessageSending
from src.chat.utils.utils import truncate_message
from src.chat.utils.utils import calculate_typing_time

install(extra_lines=3)

logger = get_logger("sender")

# WebUI 聊天室的消息广播器（延迟导入避免循环依赖）
_webui_chat_broadcaster = None

# 虚拟群 ID 前缀（与 chat_routes.py 保持一致）
VIRTUAL_GROUP_ID_PREFIX = "webui_virtual_group_"


def get_webui_chat_broadcaster():
    """获取 WebUI 聊天室广播器"""
    global _webui_chat_broadcaster
    if _webui_chat_broadcaster is None:
        try:
            from src.webui.chat_routes import chat_manager, WEBUI_CHAT_PLATFORM

            _webui_chat_broadcaster = (chat_manager, WEBUI_CHAT_PLATFORM)
        except ImportError:
            _webui_chat_broadcaster = (None, None)
    return _webui_chat_broadcaster


def is_webui_virtual_group(group_id: str) -> bool:
    """检查是否是 WebUI 虚拟群"""
    return group_id and group_id.startswith(VIRTUAL_GROUP_ID_PREFIX)


def parse_message_segments(segment) -> list:
    """解析消息段，转换为 WebUI 可用的格式

    参考 NapCat 适配器的消息解析逻辑

    Args:
        segment: Seg 消息段对象

    Returns:
        list: 消息段列表，每个元素为 {"type": "...", "data": ...}
    """

    result = []

    if segment is None:
        return result

    if segment.type == "seglist":
        # 处理消息段列表
        if segment.data:
            for seg in segment.data:
                result.extend(parse_message_segments(seg))
    elif segment.type == "text":
        # 文本消息
        if segment.data:
            result.append({"type": "text", "data": segment.data})
    elif segment.type == "image":
        # 图片消息（base64）
        if segment.data:
            result.append({"type": "image", "data": f"data:image/png;base64,{segment.data}"})
    elif segment.type == "emoji":
        # 表情包消息（base64）
        if segment.data:
            result.append({"type": "emoji", "data": f"data:image/gif;base64,{segment.data}"})
    elif segment.type == "imageurl":
        # 图片链接消息
        if segment.data:
            result.append({"type": "image", "data": segment.data})
    elif segment.type == "face":
        # 原生表情
        result.append({"type": "face", "data": segment.data})
    elif segment.type == "voice":
        # 语音消息（base64）
        if segment.data:
            result.append({"type": "voice", "data": f"data:audio/wav;base64,{segment.data}"})
    elif segment.type == "voiceurl":
        # 语音链接
        if segment.data:
            result.append({"type": "voice", "data": segment.data})
    elif segment.type == "video":
        # 视频消息（base64）
        if segment.data:
            result.append({"type": "video", "data": f"data:video/mp4;base64,{segment.data}"})
    elif segment.type == "videourl":
        # 视频链接
        if segment.data:
            result.append({"type": "video", "data": segment.data})
    elif segment.type == "music":
        # 音乐消息
        result.append({"type": "music", "data": segment.data})
    elif segment.type == "file":
        # 文件消息
        result.append({"type": "file", "data": segment.data})
    elif segment.type == "reply":
        # 回复消息
        result.append({"type": "reply", "data": segment.data})
    elif segment.type == "forward":
        # 转发消息
        forward_items = []
        if segment.data:
            for item in segment.data:
                forward_items.append(
                    {
                        "content": parse_message_segments(item.get("message_segment", {}))
                        if isinstance(item, dict)
                        else []
                    }
                )
        result.append({"type": "forward", "data": forward_items})
    else:
        # 未知类型，尝试作为文本处理
        if segment.data:
            result.append({"type": "unknown", "original_type": segment.type, "data": str(segment.data)})

    return result


async def _send_message(message: MessageSending, show_log=True) -> bool:
    """合并后的消息发送函数，包含WS发送和日志记录"""
    message_preview = truncate_message(message.processed_plain_text, max_length=200)
    platform = message.platform
    group_id = message.session.group_id

    try:
        # 检查是否是 WebUI 平台的消息，或者是 WebUI 虚拟群的消息
        chat_manager, webui_platform = get_webui_chat_broadcaster()
        is_webui_message = (platform == webui_platform) or is_webui_virtual_group(group_id)

        if is_webui_message and chat_manager is not None:
            # WebUI 聊天室消息（包括虚拟身份模式），通过 WebSocket 广播
            import time
            from src.config.config import global_config

            # 解析消息段，获取富文本内容
            message_segments = parse_message_segments(message.message_segment)

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

        # Fallback 逻辑: 尝试通过 API Server 发送
        async def send_with_new_api(legacy_exception=None):
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

    def __init__(self):
        pass

    async def send_message(
        self, message: MessageSending, typing=False, set_reply=False, storage_message=True, show_log=True
    ):
        """
        处理、发送并存储一条消息。

        参数：
            message: MessageSending 对象，待发送的消息。
            typing: 是否模拟打字等待。

        用法：
            - typing=True 时，发送前会有打字等待。
        """
        if not message.session:
            logger.error("消息缺少 session，无法发送")
            raise ValueError("消息缺少 session，无法发送")
        if not message.message_id:
            logger.error("消息缺少 message_id，无法发送")
            raise ValueError("消息缺少 message_id，无法发送")

        chat_id = message.session_id
        message_id = message.message_id

        try:
            if set_reply:
                message.build_reply()
                logger.debug(f"[{chat_id}] 选择回复引用消息: {message.processed_plain_text[:20]}...")

            from src.core.event_bus import event_bus
            from src.chat.event_helpers import build_event_message
            from src.core.types import EventType

            _event_msg = build_event_message(EventType.POST_SEND_PRE_PROCESS, message=message, stream_id=chat_id)
            continue_flag, modified_message = await event_bus.emit(
                EventType.POST_SEND_PRE_PROCESS, _event_msg
            )
            if not continue_flag:
                logger.info(f"[{chat_id}] 消息发送被插件取消: {str(message.message_segment)[:100]}...")
                return False
            if modified_message:
                if modified_message._modify_flags.modify_message_segments:
                    message.message_segment = Seg(type="seglist", data=modified_message.message_segments)
                if modified_message._modify_flags.modify_plain_text:
                    logger.warning(f"[{chat_id}] 插件修改了消息的纯文本内容，可能导致此内容被覆盖。")
                    message.processed_plain_text = modified_message.plain_text

            await message.process()

            _event_msg = build_event_message(EventType.POST_SEND, message=message, stream_id=chat_id)
            continue_flag, modified_message = await event_bus.emit(
                EventType.POST_SEND, _event_msg
            )
            if not continue_flag:
                logger.info(f"[{chat_id}] 消息发送被插件取消: {str(message.message_segment)[:100]}...")
                return False
            if modified_message:
                if modified_message._modify_flags.modify_message_segments:
                    message.message_segment = Seg(type="seglist", data=modified_message.message_segments)
                if modified_message._modify_flags.modify_plain_text:
                    message.processed_plain_text = modified_message.plain_text

            if typing:
                typing_time = calculate_typing_time(
                    input_string=message.processed_plain_text,
                    thinking_start_time=message.thinking_start_time,
                    is_emoji=message.is_emoji,
                )
                await asyncio.sleep(typing_time)

            sent_msg = await _send_message(message, show_log=show_log)
            if not sent_msg:
                return False

            _event_msg = build_event_message(EventType.AFTER_SEND, message=message, stream_id=chat_id)
            continue_flag, modified_message = await event_bus.emit(
                EventType.AFTER_SEND, _event_msg
            )
            if not continue_flag:
                logger.info(f"[{chat_id}] 消息发送后续处理被插件取消: {str(message.message_segment)[:100]}...")
                return True
            if modified_message:
                if modified_message._modify_flags.modify_message_segments:
                    message.message_segment = Seg(type="seglist", data=modified_message.message_segments)
                if modified_message._modify_flags.modify_plain_text:
                    message.processed_plain_text = modified_message.plain_text

            if storage_message:
                with get_db_session() as db_session:
                    db_session.add(message.to_db_instance())

            return sent_msg

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e
