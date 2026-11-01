from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from src.services import memory_flow_service as flow
from src.chat.image_system.image_manager import image_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["GIF", "PNG"])
async def test_writeback_normalizes_gif_without_changing_static_image(monkeypatch, format):
    data = BytesIO()
    Image.new("RGB", (8, 8), "red").save(data, format=format)
    original = data.getvalue()
    component = SimpleNamespace(binary_data=original, binary_hash="hash", content="")
    message = SimpleNamespace(
        raw_message=SimpleNamespace(components=[component]),
        session_id="test-chat", message_id="test-message", timestamp=datetime(2026, 11, 1),
    )
    monkeypatch.setattr(flow, "iter_message_image_components", lambda components: [("0", component)])
    monkeypatch.setattr(image_manager, "get_cached_image_description", lambda key: "")
    ingest = AsyncMock(return_value={"success": True, "content_hash": "hash"})
    monkeypatch.setattr(flow.memory_service, "image_memory", ingest)
    service = flow.ImageMemoryWritebackService()
    assert await service._handle_message(message)
    sent = ingest.await_args.kwargs["image_bytes"]
    if format == "GIF":
        assert not sent.startswith((b"GIF87a", b"GIF89a"))
        with Image.open(BytesIO(sent)) as image:
            assert image.format == "JPEG"
            assert not getattr(image, "is_animated", False)
            assert image.width > 0 and image.height > 0
    else:
        assert sent == original
    assert component.binary_data == b""
