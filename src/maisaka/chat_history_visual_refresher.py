"""Maisaka 聊天历史视觉占位刷新器。"""

from typing import Awaitable, Callable, Optional

from sqlmodel import select

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import EmojiComponent, ForwardNodeComponent, ImageComponent
from src.common.database.database import get_db_session
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger

from .context_messages import LLMContextMessage, SessionBackedMessage

logger = get_logger("maisaka_chat_history_visual_refresher")


_UNRESOLVED_IMAGE_PLACEHOLDERS = {"", "[image]", "[图片]"}
_UNRESOLVED_EMOJI_PLACEHOLDERS = {"", "[emoji]", "[表情包]"}

BuildHistoryMessage = Callable[[SessionMessage, str], Awaitable[Optional[LLMContextMessage]]]
BuildVisibleText = Callable[[SessionMessage], str]


async def refresh_chat_history_visual_placeholders(
    *,
    chat_history: list[LLMContextMessage],
    build_history_message: BuildHistoryMessage,
    build_visible_text: BuildVisibleText,
) -> int:
    """在进入新一轮规划前，尝试用已完成的识图结果刷新历史占位。"""

    refreshed_count = 0
    for index, history_message in enumerate(chat_history):
        if not isinstance(history_message, SessionBackedMessage):
            continue

        original_message = history_message.original_message
        if original_message is None:
            continue

        visual_components_updated = _refresh_pending_visual_components(original_message.raw_message.components)
        if visual_components_updated:
            await original_message.process(
                enable_heavy_media_analysis=False,
                enable_voice_transcription=False,
            )

        refreshed_visible_text = build_visible_text(original_message)
        if not visual_components_updated and refreshed_visible_text == history_message.visible_text:
            continue

        rebuilt_history_message = await build_history_message(original_message, history_message.source_kind)
        if rebuilt_history_message is None:
            continue

        chat_history[index] = rebuilt_history_message
        refreshed_count += 1

    return refreshed_count


def _refresh_pending_visual_components(components: list[object]) -> bool:
    """用缓存中的描述更新尚未补全文本的图片与表情组件。"""

    refreshed = False
    for component in components:
        if isinstance(component, ImageComponent):
            if _should_refresh_image_component(component):
                image_description = _lookup_cached_image_description(component.binary_hash)
                if image_description:
                    component.content = f"[图片：{image_description}]"
                    refreshed = True
            continue

        if isinstance(component, EmojiComponent):
            if _should_refresh_emoji_component(component):
                emoji_description = _lookup_cached_emoji_description(component.binary_hash)
                if emoji_description:
                    component.content = f"[表情包: {emoji_description}]"
                    refreshed = True
            continue

        if not isinstance(component, ForwardNodeComponent):
            continue

        for forward_component in component.forward_components:
            if _refresh_pending_visual_components(forward_component.content):
                refreshed = True

    return refreshed


def _should_refresh_image_component(component: ImageComponent) -> bool:
    """判断图片组件当前是否仍处于待补全文本的占位状态。"""

    return (component.content or "").strip().lower() in _UNRESOLVED_IMAGE_PLACEHOLDERS


def _should_refresh_emoji_component(component: EmojiComponent) -> bool:
    """判断表情组件当前是否仍处于待补全文本的占位状态。"""

    return (component.content or "").strip().lower() in _UNRESOLVED_EMOJI_PLACEHOLDERS


def _lookup_cached_image_description(image_hash: str) -> str:
    """从数据库读取已完成的图片描述，不触发新的识图请求。"""

    if not image_hash:
        return ""

    try:
        with get_db_session() as session:
            statement = select(Images).filter_by(image_hash=image_hash, image_type=ImageType.IMAGE).limit(1)
            if image_record := session.exec(statement).first():
                if image_record.no_file_flag:
                    return ""
                if image_record.vlm_processed and image_record.description:
                    return str(image_record.description).strip()
    except Exception as exc:
        logger.warning(f"读取图片缓存描述失败，image_hash={image_hash}: {exc}")

    return ""


def _lookup_cached_emoji_description(emoji_hash: str) -> str:
    """从数据库读取已完成的表情描述，不触发新的识别请求。"""

    if not emoji_hash:
        return ""

    try:
        with get_db_session() as session:
            statement = select(Images).filter_by(image_hash=emoji_hash, image_type=ImageType.EMOJI).limit(1)
            if image_record := session.exec(statement).first():
                if image_record.no_file_flag or not image_record.description:
                    return ""
                return str(image_record.description).strip()
    except Exception as exc:
        logger.warning(f"读取表情缓存描述失败，emoji_hash={emoji_hash}: {exc}")

    return ""
