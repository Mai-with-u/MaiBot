"""
事件消息构建工具

将 chat 层的消息对象 (SessionMessage / MessageSending) 转换为
核心事件系统使用的 MaiMessages，供调用 event_bus.emit() 前使用。
"""

from typing import List, Optional, TYPE_CHECKING

from src.common.logger import get_logger
from src.core.types import EventType, MaiMessages

if TYPE_CHECKING:
    from src.chat.message_receive.message import MessageSending, SessionMessage
    from src.common.data_models.llm_data_model import LLMGenerationDataModel

logger = get_logger("event_helpers")


def build_event_message(
    event_type: EventType | str,
    message: Optional["SessionMessage | MessageSending | MaiMessages"] = None,
    llm_prompt: Optional[str] = None,
    llm_response: Optional["LLMGenerationDataModel"] = None,
    stream_id: Optional[str] = None,
    action_usage: Optional[List[str]] = None,
) -> Optional[MaiMessages]:
    """根据事件类型和输入，准备和转换消息对象。

    迁移自 events_manager._prepare_message，保持相同的行为。
    """
    if isinstance(message, MaiMessages):
        return message.deepcopy()

    if message:
        return _transform_event_message(message, llm_prompt, llm_response)

    if event_type not in (EventType.ON_START, EventType.ON_STOP):
        assert stream_id, "如果没有消息，必须为非启动/关闭事件提供流ID"
        if event_type in (EventType.ON_MESSAGE, EventType.ON_PLAN, EventType.POST_LLM, EventType.AFTER_LLM):
            return _build_message_from_stream(stream_id, llm_prompt, llm_response)
        else:
            return _build_message_without_raw(stream_id, llm_prompt, llm_response, action_usage)

    return None  # ON_START / ON_STOP 没有消息体


def _transform_event_message(
    message: "SessionMessage | MessageSending",
    llm_prompt: Optional[str] = None,
    llm_response: Optional["LLMGenerationDataModel"] = None,
) -> MaiMessages:
    """将 SessionMessage / MessageSending 转换为 MaiMessages。"""
    from maim_message import Seg
    from src.chat.message_receive.message import MessageSending

    transformed = MaiMessages(
        llm_prompt=llm_prompt,
        llm_response_content=llm_response.content if llm_response else None,
        llm_response_reasoning=llm_response.reasoning if llm_response else None,
        llm_response_model=llm_response.model if llm_response else None,
        llm_response_tool_call=llm_response.tool_calls if llm_response else None,
        raw_message=message.processed_plain_text or "",
        additional_data={},
    )

    # 消息段处理
    if isinstance(message, MessageSending):
        if message.message_segment.type == "seglist":
            transformed.message_segments = list(message.message_segment.data)  # type: ignore
        else:
            transformed.message_segments = [message.message_segment]
    else:
        transformed.message_segments = [Seg(type="text", data=message.processed_plain_text or "")]

    # stream_id
    transformed.stream_id = message.session_id if hasattr(message, "session_id") else ""

    # 处理后文本
    transformed.plain_text = message.processed_plain_text

    # 基本信息
    if isinstance(message, MessageSending):
        transformed.message_base_info["platform"] = message.platform
        if message.session.group_id:
            transformed.is_group_message = True
            group_name = ""
            if (
                message.session.context
                and message.session.context.message
                and message.session.context.message.message_info.group_info
            ):
                group_name = message.session.context.message.message_info.group_info.group_name
            transformed.message_base_info.update(
                {
                    "group_id": message.session.group_id,
                    "group_name": group_name,
                }
            )
        transformed.message_base_info.update(
            {
                "user_id": message.bot_user_info.user_id,
                "user_cardname": message.bot_user_info.user_cardname,
                "user_nickname": message.bot_user_info.user_nickname,
            }
        )
        if not transformed.is_group_message:
            transformed.is_private_message = True
    elif hasattr(message, "message_info") and message.message_info:
        if message.platform:
            transformed.message_base_info["platform"] = message.platform
        if message.message_info.group_info:
            transformed.is_group_message = True
            transformed.message_base_info.update(
                {
                    "group_id": message.message_info.group_info.group_id,
                    "group_name": message.message_info.group_info.group_name,
                }
            )
        if message.message_info.user_info:
            if not transformed.is_group_message:
                transformed.is_private_message = True
            transformed.message_base_info.update(
                {
                    "user_id": message.message_info.user_info.user_id,
                    "user_cardname": message.message_info.user_info.user_cardname,
                    "user_nickname": message.message_info.user_info.user_nickname,
                }
            )

    return transformed


def _build_message_from_stream(
    stream_id: str,
    llm_prompt: Optional[str] = None,
    llm_response: Optional["LLMGenerationDataModel"] = None,
) -> MaiMessages:
    """从 stream_id 查找会话消息并转换。"""
    from src.chat.message_receive.chat_manager import chat_manager

    session = chat_manager.get_session_by_session_id(stream_id)
    assert session, f"未找到流ID为 {stream_id} 的会话"
    return _transform_event_message(session.context.message, llm_prompt, llm_response)


def _build_message_without_raw(
    stream_id: str,
    llm_prompt: Optional[str] = None,
    llm_response: Optional["LLMGenerationDataModel"] = None,
    action_usage: Optional[List[str]] = None,
) -> MaiMessages:
    """没有原始消息对象时，从 stream_id 构建最小 MaiMessages。"""
    from src.chat.message_receive.chat_manager import chat_manager

    session = chat_manager.get_session_by_session_id(stream_id)
    assert session, f"未找到流ID为 {stream_id} 的会话"
    return MaiMessages(
        stream_id=stream_id,
        llm_prompt=llm_prompt,
        llm_response_content=llm_response.content if llm_response else None,
        llm_response_reasoning=llm_response.reasoning if llm_response else None,
        llm_response_model=llm_response.model if llm_response else None,
        llm_response_tool_call=llm_response.tool_calls if llm_response else None,
        is_group_message=session.is_group_session,
        is_private_message=not session.is_group_session,
        action_usage=action_usage,
        additional_data={"response_is_processed": True},
    )
