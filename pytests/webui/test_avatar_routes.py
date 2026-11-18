from pathlib import Path
from unittest.mock import AsyncMock
import time

from fastapi import HTTPException
import pytest

from src.webui.routers import avatar
from src.platform_io.avatar import AvatarResult


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"avatar-data"


def test_save_webui_user_avatar_replaces_previous_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(avatar, "AVATAR_CACHE_ROOT", tmp_path)
    old_path = tmp_path / "internal" / "webui_example.jpg"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"\xff\xd8\xffold-avatar")

    saved_path = avatar.save_webui_user_avatar(
        "webui_example",
        "image/png",
        PNG_BYTES,
    )

    assert saved_path == tmp_path / "internal" / "webui_example.png"
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


@pytest.mark.asyncio
async def test_remote_avatar_route_uses_shared_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache_path = tmp_path / "avatar.png"
    cache_path.write_bytes(PNG_BYTES)
    get_avatar = AsyncMock(
        return_value=AvatarResult("available", time.time() + 60, "https://example.com/avatar", cache_path)
    )
    monkeypatch.setattr(avatar.avatar_service, "get_avatar", get_avatar)
    response = await avatar.get_webui_avatar("custom", "person", None, "account", "connection", False, None)
    assert response.path == cache_path
    assert "public" not in response.headers["cache-control"]
    assert int(response.headers["cache-control"].split("=")[1]) <= 60
    get_avatar.assert_awaited_once_with(
        "custom", "person", "user", account_id="account", scope="connection", force_refresh=False
    )


@pytest.mark.asyncio
async def test_remote_avatar_route_distinguishes_missing_and_failure(monkeypatch: pytest.MonkeyPatch):
    get_avatar = AsyncMock(return_value=AvatarResult("unsupported", time.time() + 300))
    monkeypatch.setattr(avatar.avatar_service, "get_avatar", get_avatar)
    absent = await avatar.get_webui_avatar("custom", "person", None, "", "", False, None)
    assert absent.status_code == 204
    assert absent.headers["x-avatar-status"] == "unsupported"
    assert absent.headers["cache-control"] == "private, max-age=300"
    get_avatar.side_effect = RuntimeError("adapter failure")
    with pytest.raises(HTTPException) as failure:
        await avatar.get_webui_avatar("custom", "person", None, "", "", False, None)
    assert failure.value.status_code == 502
    assert "adapter failure" in failure.value.detail


@pytest.mark.asyncio
async def test_local_avatar_remains_persistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(avatar, "AVATAR_CACHE_ROOT", tmp_path)
    cache_path = avatar.save_webui_user_avatar("webui_example", "image/png", PNG_BYTES)
    response = await avatar.get_webui_avatar("internal", "webui_example", None, "", "", False, None)
    # 浏览器带着上次的 ETag 重新校验时只返回 304，头像被替换后才重新下发。
    etag = response.headers["etag"]
    unchanged = await avatar.get_webui_avatar("internal", "webui_example", None, "", "", False, f"W/{etag}")
    assert unchanged.status_code == 304
    assert unchanged.headers["etag"] == etag
    changed = await avatar.get_webui_avatar("internal", "webui_example", None, "", "", False, '"stale"')
    assert changed.path == cache_path
    assert response.path == cache_path
    assert response.headers["cache-control"] == "private, no-cache"
