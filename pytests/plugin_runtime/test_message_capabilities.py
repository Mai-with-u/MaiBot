from datetime import datetime

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.plugin_runtime.capabilities.data import RuntimeDataCapabilityMixin


@pytest.mark.asyncio
async def test_build_readable_accepts_messages_returned_by_get_recent() -> None:
    message = SessionMessage(message_id="message-1", timestamp=datetime.now(), platform="test")
    message.message_info = MessageInfo(user_info=UserInfo(user_id="user-1", user_nickname="测试用户"))
    message.raw_message = MessageSequence([TextComponent("你好")])
    message.session_id = "chat-1"
    message.processed_plain_text = "你好"

    capability = RuntimeDataCapabilityMixin()
    serialized_messages = capability._serialize_messages([message], include_binary_data=False)
    result = await capability._cap_message_build_readable(
        plugin_id="test.plugin",
        capability="message.build_readable",
        args={"messages": serialized_messages, "replace_bot_name": False, "timestamp_mode": None},
    )

    assert result == {"success": True, "text": "测试用户说：你好"}
