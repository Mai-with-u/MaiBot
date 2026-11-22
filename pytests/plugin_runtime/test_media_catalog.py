"""插件媒体目录与精确取图的回归用例。"""

from base64 import b64decode
from contextlib import contextmanager
from hashlib import sha256
from unittest.mock import patch

from sqlmodel import Session, create_engine
import asyncio

import pytest

from src.common.database.database_model import Images, ImageType
from src.common.utils import image_path
from src.plugin_runtime.capabilities import media


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'images.db').as_posix()}")
    Images.__table__.create(engine)

    @contextmanager
    def session():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(media, "get_db_session", session)
    monkeypatch.setattr(media, "get_instance_root", lambda: tmp_path)
    monkeypatch.setattr(image_path, "PROJECT_ROOT", tmp_path)
    with Session(engine) as db:
        for i, (kind, registered, banned, missing) in enumerate([
            (ImageType.EMOJI, True, False, False), (ImageType.EMOJI, False, False, False),
            (ImageType.EMOJI, True, True, False), (ImageType.IMAGE, False, False, False),
            (ImageType.IMAGE, False, False, False), (ImageType.IMAGE, False, False, True),
        ]):
            content = bytes([i]) * 16
            digest = sha256(content).hexdigest()
            path = tmp_path / "data" / ("emoji" if kind == ImageType.EMOJI else "images") / (digest + ".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not missing:
                path.write_bytes(content)
            db.add(Images(image_hash=digest, image_type=kind, description=str(i), full_path=str(path),
                          is_registered=registered, is_banned=banned, no_file_flag=missing))
        db.commit()
    yield
    engine.dispose()


def test_catalog_paging_and_fetch(catalog):
    with patch.object(media, "resolve_stored_image_path", side_effect=AssertionError("目录不应读取图片文件")):
        emojis = asyncio.run(media.list_media("plugin", "emoji.list", {}))
        assert [item["emotion"] for item in emojis["emojis"]] == ["0"]
        first = asyncio.run(media.list_media("plugin", "image.list", {"limit": 1}))
        second = asyncio.run(media.list_media("plugin", "image.list", {"cursor": first["next_cursor"], "limit": 1}))
        assert first["count"] == second["count"] == 1
        assert second["next_cursor"] is None
        assert [item["description"] for item in first["images"] + second["images"]] == ["3", "4"]
        assert set(first["images"][0]) == {"hash", "description", "relative_path"}

    digest = first["images"][0]["hash"]
    obj = asyncio.run(media.get_media("plugin", "image.get_by_hash", {"hash": digest}))["image"]
    assert sha256(b64decode(obj["base64"])).hexdigest() == digest
    assert asyncio.run(media.get_media("plugin", "emoji.get_by_hash", {"hash": digest}))["emoji"] is None
    emoji_hash = emojis["emojis"][0]["hash"]
    emoji = asyncio.run(media.get_media("plugin", "emoji.get_by_hash", {"hash": emoji_hash}))["emoji"]
    assert sha256(b64decode(emoji["base64"])).hexdigest() == emoji_hash


def test_size_limits(catalog, monkeypatch):
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(media.list_media("plugin", "image.list", {"limit": 101}))
    monkeypatch.setattr(media, "_MAX_IMAGE_BYTES", 15)
    with pytest.raises(ValueError, match="单帧"):
        asyncio.run(media.get_media("plugin", "image.get_by_hash", {"hash": sha256(bytes([3]) * 16).hexdigest()}))

@pytest.mark.parametrize(("namespace", "key"), [("emoji", "emojis"), ("image", "images")])
def test_relative_path_round_trip(catalog, namespace, key):
    entry = asyncio.run(media.list_media("plugin", namespace + ".list", {}))[key][0]
    with patch.object(media, "resolve_stored_image_path", side_effect=AssertionError("取路径无需打开文件")):
        path = asyncio.run(media.get_media("plugin", namespace + ".get_relative_path", {"hash": entry["hash"]}))["relative_path"]
    assert path == entry["relative_path"]
    assert path.startswith("data/" + ("emoji/" if namespace == "emoji" else "images/"))
    result = asyncio.run(media.get_media("plugin", namespace + ".get_by_path", {"relative_path": path}))
    assert sha256(b64decode(result[namespace]["base64"])).hexdigest() == entry["hash"]
    for invalid in ["../secret", "/etc/passwd", "C:/other/file", "data/images/../emoji/test"]:
        with pytest.raises(ValueError):
            asyncio.run(media.get_media("plugin", namespace + ".get_by_path", {"relative_path": invalid}))
