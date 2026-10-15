from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
import asyncio
import json

from PIL import Image, UnidentifiedImageError
import pytest

from src.platform_io import avatar
from src.plugin_runtime import avatar_provider


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(avatar, "AVATAR_CACHE_ROOT", tmp_path)
    query = AsyncMock(
        return_value={"status": "available", "url": "https://example.com/avatar.png", "expires_in": 86400}
    )
    monkeypatch.setattr(avatar_provider, "query_adapter_avatar", query)
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    download = AsyncMock(return_value=output.getvalue())
    instance = avatar.AvatarService()
    monkeypatch.setattr(instance, "_download", download)
    return instance, query, download


@pytest.mark.asyncio
async def test_cache_expiry_force_refresh_and_format_change(service, monkeypatch: pytest.MonkeyPatch):
    instance, query, download = service
    clock = 1000.0
    monkeypatch.setattr(avatar.time, "time", lambda: clock)
    first = await instance.get_avatar("qq", "1026294844")
    assert first.path.read_bytes() == download.return_value
    assert (await instance.get_avatar("qq", "1026294844")) == first
    assert query.await_count == 1
    assert set(first.to_dict()) == {"status", "url", "expires_at"}

    clock += 86401
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="JPEG")
    download.return_value = output.getvalue()
    refreshed = await instance.get_avatar("qq", "1026294844")
    assert refreshed.path.suffix == ".jpg"
    assert not first.path.exists()
    assert refreshed.expires_at > first.expires_at
    await instance.get_avatar("qq", "1026294844", force_refresh=True)
    assert query.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unsupported", "missing"])
async def test_absent_avatars_have_short_expiring_cache(service, monkeypatch: pytest.MonkeyPatch, status: str):
    instance, query, download = service
    query.return_value = {"status": status}
    clock = 1000.0
    monkeypatch.setattr(avatar.time, "time", lambda: clock)
    result = await instance.get_avatar("custom", "person")
    assert result.status == status and result.path is None and result.url is None
    await instance.get_avatar("custom", "person")
    assert query.await_count == 1
    download.assert_not_awaited()
    clock += avatar.AVATAR_NEGATIVE_TTL + 1
    await instance.get_avatar("custom", "person")
    assert query.await_count == 2


@pytest.mark.asyncio
async def test_failure_does_not_serve_stale_image_or_hide_error(service):
    instance, query, download = service
    first = await instance.get_avatar("qq", "1")
    query.side_effect = RuntimeError("adapter disconnected")
    with pytest.raises(RuntimeError, match="adapter disconnected"):
        await instance.get_avatar("qq", "1", force_refresh=True)
    assert first.path.is_file()
    query.side_effect = None
    download.return_value = b"not an image"
    with pytest.raises(UnidentifiedImageError):
        await instance.get_avatar("qq", "1", force_refresh=True)
    assert first.path.is_file()


@pytest.mark.asyncio
async def test_concurrent_requests_share_refresh_and_keys_are_isolated(service):
    instance, query, download = service
    results = await asyncio.gather(*(instance.get_avatar("qq", "1") for _ in range(8)))
    assert len({result.path for result in results}) == 1
    assert download.await_count == 1
    group = await instance.get_avatar("qq", "1", "group")
    account = await instance.get_avatar("qq", "1", account_id="2", scope="connection")
    assert len({results[0].path, group.path, account.path}) == 3


@pytest.mark.asyncio
async def test_expired_cache_failure_is_exposed(service):
    instance, query, download = service
    await instance.get_avatar("qq", "1")
    metadata_path = next(avatar.AVATAR_CACHE_ROOT.glob("remote/*.json"))
    metadata = json.loads(metadata_path.read_text())
    metadata["expires_at"] = 0
    metadata_path.write_text(json.dumps(metadata))
    download.side_effect = RuntimeError("download failed")
    with pytest.raises(RuntimeError, match="download failed"):
        await instance.get_avatar("qq", "1")
