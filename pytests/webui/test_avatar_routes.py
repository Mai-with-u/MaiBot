from pathlib import Path

from fastapi import HTTPException
import pytest

from src.webui.routers import avatar


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"avatar-data"


def test_save_webui_user_avatar_replaces_previous_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(avatar, "AVATAR_CACHE_ROOT", tmp_path)
    old_path = tmp_path / "webui" / "webui_example.jpg"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"\xff\xd8\xffold-avatar")

    saved_path = avatar.save_webui_user_avatar(
        "webui_example",
        "image/png",
        PNG_BYTES,
    )

    assert saved_path == tmp_path / "webui" / "webui_example.png"
    assert saved_path.read_bytes() == PNG_BYTES
    assert not old_path.exists()


@pytest.mark.parametrize(
    ("user_id", "content_type", "image_bytes", "status_code"),
    [
        ("other_user", "image/png", PNG_BYTES, 400),
        ("webui_example", "text/plain", PNG_BYTES, 415),
        ("webui_example", "image/png", b"not-an-image", 415),
    ],
)
def test_save_webui_user_avatar_rejects_invalid_uploads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user_id: str,
    content_type: str,
    image_bytes: bytes,
    status_code: int,
) -> None:
    monkeypatch.setattr(avatar, "AVATAR_CACHE_ROOT", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        avatar.save_webui_user_avatar(user_id, content_type, image_bytes)

    assert exc_info.value.status_code == status_code
