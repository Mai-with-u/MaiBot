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


def test_history_dict_media_obeys_binary_data_flag() -> None:
    from src.common.data_models.message_component_data_model import DictComponent
    from src.plugin_runtime.host.message_utils import PluginMessageUtils
    import msgpack

    # 线上转发历史：媒体嵌套在 DictComponent，而不是 ImageComponent。
    payload = {
        "nodes": [
            {
                "content": [
                    {
                        "type": "image",
                        "data": {
                            "hash": "image-hash",
                            "description": "图片描述",
                            "binary_data_base64": "A" * (17 * 1024 * 1024),
                        },
                    }
                ]
            }
        ]
    }
    component = DictComponent(data=payload)
    full = PluginMessageUtils._component_to_dict(component, include_binary_data=True)
    lean = PluginMessageUtils._component_to_dict(component, include_binary_data=False)
    assert len(msgpack.packb(full)) > 16 * 1024 * 1024
    assert len(msgpack.packb(lean)) < 1024
    media = lean["data"]["nodes"][0]["content"][0]["data"]
    assert media == {"hash": "image-hash", "description": "图片描述"}
    assert "binary_data_base64" in payload["nodes"][0]["content"][0]["data"]
