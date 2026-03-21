from datetime import datetime
from pathlib import Path

import sys

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import GroupInfo, MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    ForwardComponent,
    ForwardNodeComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    TextComponent,
    VoiceComponent,
)
from src.plugin_runtime.host.message_utils import PluginMessageUtils


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_plugin_message_utils_preserves_binary_components_and_reply_metadata() -> None:
    message = SessionMessage(message_id="msg-1", timestamp=datetime.now(), platform="qq")
    message.message_info = MessageInfo(
        user_info=UserInfo(user_id="10001", user_nickname="tester"),
        group_info=GroupInfo(group_id="20001", group_name="group"),
        additional_config={"self_id": "999"},
    )
    message.session_id = "qq:20001:10001"
    message.processed_plain_text = "binary payload"
    message.display_message = "binary payload"
    message.raw_message = MessageSequence(
        components=[
            TextComponent("hello"),
            ImageComponent(binary_hash="", binary_data=b"image-bytes", content=""),
            VoiceComponent(binary_hash="", binary_data=b"voice-bytes", content=""),
            ReplyComponent(
                target_message_id="origin-1",
                target_message_content="origin text",
                target_message_sender_id="42",
                target_message_sender_nickname="alice",
                target_message_sender_cardname="Alice",
            ),
            ForwardNodeComponent(
                forward_components=[
                    ForwardComponent(
                        user_nickname="bob",
                        user_id="43",
                        user_cardname="Bob",
                        message_id="forward-1",
                        content=[
                            TextComponent("node-text"),
                            ImageComponent(binary_hash="", binary_data=b"node-image", content=""),
                        ],
                    )
                ]
            ),
        ]
    )

    message_dict = PluginMessageUtils._session_message_to_dict(message)
    rebuilt_message = PluginMessageUtils._build_session_message_from_dict(dict(message_dict))

    image_component = rebuilt_message.raw_message.components[1]
    voice_component = rebuilt_message.raw_message.components[2]
    reply_component = rebuilt_message.raw_message.components[3]
    forward_component = rebuilt_message.raw_message.components[4]

    assert isinstance(image_component, ImageComponent)
    assert image_component.binary_data == b"image-bytes"

    assert isinstance(voice_component, VoiceComponent)
    assert voice_component.binary_data == b"voice-bytes"

    assert isinstance(reply_component, ReplyComponent)
    assert reply_component.target_message_id == "origin-1"
    assert reply_component.target_message_content == "origin text"
    assert reply_component.target_message_sender_id == "42"
    assert reply_component.target_message_sender_nickname == "alice"
    assert reply_component.target_message_sender_cardname == "Alice"

    assert isinstance(forward_component, ForwardNodeComponent)
    assert isinstance(forward_component.forward_components[0].content[1], ImageComponent)
    assert forward_component.forward_components[0].content[1].binary_data == b"node-image"
