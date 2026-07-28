from pathlib import Path

from fastapi import HTTPException
import pytest

from src.common.data_models.message_component_data_model import EmojiComponent
from src.webui.routers import user_emoji
from src.webui.routers.chat.service import create_message_data
from src.webui.routers.chat.serializers import serialize_message_component


GIF_BYTES = b"GIF89a" + b"user-emoji"


def test_save_and_list_user_emoji(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_emoji, "USER_EMOJI_ROOT", tmp_path)

    saved_path = user_emoji.save_user_emoji("webui_example", "image/gif", GIF_BYTES)
    paths = user_emoji.list_user_emoji_paths("webui_example")

    assert saved_path.parent == tmp_path / "webui_example"
    assert saved_path.suffix == ".gif"
    assert paths == [saved_path]
    assert saved_path.read_bytes() == GIF_BYTES


@pytest.mark.parametrize(
    ("content_type", "image_bytes", "status_code"),
    [
        ("text/plain", GIF_BYTES, 415),
        ("image/png", b"not-an-image", 415),
        ("image/bmp", b"BMunsupported-emoji", 415),
    ],
)
def test_save_user_emoji_rejects_unsupported_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    image_bytes: bytes,
    status_code: int,
) -> None:
    monkeypatch.setattr(user_emoji, "USER_EMOJI_ROOT", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        user_emoji.save_user_emoji("webui_example", content_type, image_bytes)

    assert exc_info.value.status_code == status_code


def test_create_message_data_keeps_emoji_separate_from_images() -> None:
    message_data = create_message_data(
        content="",
        user_id="webui_user_example",
        user_name="人类",
        images=[],
        emojis=[
            {
                "name": "example.gif",
                "mime_type": "image/gif",
                "base64": "R0lGODlh",
            }
        ],
    )

    segments = message_data["message_segment"]["data"]
    assert [segment["type"] for segment in segments] == ["emoji"]
    assert segments[0]["data"] == "R0lGODlh"


def test_serialize_emoji_uses_detected_image_mime_type() -> None:
    component = EmojiComponent(
        binary_hash="example",
        binary_data=b"\x89PNG\r\n\x1a\nemoji-data",
    )

    segment = serialize_message_component(component)

    assert segment is not None
    assert segment["type"] == "emoji"
    assert str(segment["data"]).startswith("data:image/png;base64,")
