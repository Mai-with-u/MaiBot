from pathlib import Path
from typing import Any, Dict

import importlib
import sys


BUILT_IN_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins" / "built_in"
if str(BUILT_IN_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(BUILT_IN_PLUGIN_ROOT))

NapCatOutboundCodec = importlib.import_module("napcat_adapter.codec_outbound").NapCatOutboundCodec


def test_napcat_outbound_codec_supports_binary_and_forward_segments() -> None:
    codec = NapCatOutboundCodec()
    raw_message = [
        {"type": "text", "data": "hello"},
        {"type": "image", "data": "", "hash": "h1", "binary_data_base64": "aW1hZ2U="},
        {"type": "emoji", "data": "", "hash": "h2", "binary_data_base64": "ZW1vamk="},
        {"type": "voice", "data": "", "hash": "h3", "binary_data_base64": "dm9pY2U="},
        {
            "type": "reply",
            "data": {
                "target_message_id": "origin-1",
                "target_message_content": "origin text",
            },
        },
        {
            "type": "forward",
            "data": [
                {
                    "user_id": "42",
                    "user_nickname": "alice",
                    "user_cardname": "Alice",
                    "message_id": "fwd-1",
                    "content": [{"type": "text", "data": "node-text"}],
                }
            ],
        },
    ]

    converted = codec.convert_segments(raw_message)

    assert converted[0] == {"type": "text", "data": {"text": "hello"}}
    assert converted[1]["type"] == "image"
    assert converted[1]["data"]["file"] == "base64://aW1hZ2U="
    assert converted[2]["type"] == "image"
    assert converted[2]["data"]["subtype"] == 1
    assert converted[3] == {"type": "record", "data": {"file": "base64://dm9pY2U="}}
    assert converted[4] == {"type": "reply", "data": {"id": "origin-1"}}
    assert converted[5]["type"] == "node"
    assert converted[5]["data"]["name"] == "alice"
    assert converted[5]["data"]["content"] == [{"type": "text", "data": {"text": "node-text"}}]


def test_napcat_outbound_codec_builds_private_action_from_route_metadata() -> None:
    codec = NapCatOutboundCodec()
    message: Dict[str, Any] = {
        "message_info": {
            "user_info": {"user_id": "10001", "user_nickname": "tester"},
            "additional_config": {},
        },
        "raw_message": [{"type": "text", "data": "hello"}],
    }

    action_name, params = codec.build_outbound_action(message, {"target_user_id": "30001"})

    assert action_name == "send_private_msg"
    assert params == {"message": [{"type": "text", "data": {"text": "hello"}}], "user_id": "30001"}
