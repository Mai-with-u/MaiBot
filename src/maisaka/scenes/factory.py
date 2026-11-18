"""场景选择边界：业务场景必须有明确类型，未绑定请求只使用通用提示词。"""

from typing import TYPE_CHECKING, Optional

from .base import ChatScene
from .group.behavior import GroupChatScene
from .private.behavior import PrivateChatScene
from .prompting import ChatPromptProfile, UnscopedPromptProfile

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler

_GROUP = GroupChatScene()
_PRIVATE = PrivateChatScene()
_UNSCOPED_PROMPTS = UnscopedPromptProfile()


def get_scene(is_group: bool) -> ChatScene:
    if is_group is True:
        return _GROUP
    if is_group is False:
        return _PRIVATE
    raise ValueError("聊天场景必须明确为群聊或私聊")


def get_session_scene(session_id: str) -> ChatScene:
    from src.chat.message_receive.chat_manager import chat_manager

    session = chat_manager.get_session_by_session_id(session_id)
    if session is None:
        raise ValueError(f"聊天场景对应的真实会话不存在：{session_id}")
    return get_scene(session.is_group_session)


def get_prompt_profile(is_group: Optional[bool]) -> ChatPromptProfile:
    return _UNSCOPED_PROMPTS if is_group is None else get_scene(is_group).prompts


def create_turn_scheduler(runtime: "MaisakaHeartFlowChatting") -> "MessageTurnScheduler":
    return get_scene(runtime.chat_stream.is_group_session).create_scheduler(runtime)
