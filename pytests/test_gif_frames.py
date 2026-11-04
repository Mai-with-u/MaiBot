from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from src.chat.image_system import image_manager as image_manager_module
from src.common.utils.utils_image import ImageUtils


def _gif(colors, size=(64, 48)):
    frames = [Image.new("RGB", size, color) for color in colors]
    buffer = BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buffer.getvalue()


def test_gif_frame_strip_keeps_distinct_frames():
    data = _gif([(220, 40, 40), (40, 90, 220), (40, 170, 60)])

    with Image.open(BytesIO(ImageUtils.gif_2_static_image(data))) as strip:
        # 每帧缩放到高 200，三帧互不相同，应横向拼成三格
        assert strip.size == (int(200 / 48 * 64) * 3, 200)


def test_gif_frame_strip_skips_similar_frames():
    data = _gif([(220, 40, 40), (221, 40, 40), (40, 90, 220)])

    with Image.open(BytesIO(ImageUtils.gif_2_static_image(data))) as strip:
        assert strip.size == (int(200 / 48 * 64) * 2, 200)


def test_is_animated_gif():
    assert ImageUtils.is_animated_gif(_gif([(0, 0, 0), (255, 255, 255)]))
    assert not ImageUtils.is_animated_gif(_gif([(0, 0, 0)]))
    assert not ImageUtils.is_animated_gif(b"not an image")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "fmt", "expected_format", "expected_type"),
    [
        (_gif([(220, 40, 40), (40, 90, 220)]), "gif", "jpg", "GIF"),
        (_gif([(220, 40, 40)]), "gif", "gif", "STATIC"),
    ],
)
async def test_image_description_uses_frame_strip_for_animated_gif(monkeypatch, data, fmt, expected_format, expected_type):
    contexts = {}
    template = SimpleNamespace(add_context=lambda name, value: contexts.__setitem__(name, value))
    monkeypatch.setattr(image_manager_module.prompt_manager, "get_prompt", lambda name: template)
    monkeypatch.setattr(image_manager_module.prompt_manager, "render_prompt", AsyncMock(return_value="prompt"))
    vlm = AsyncMock(return_value=SimpleNamespace(response="描述"))
    monkeypatch.setattr(image_manager_module.vlm, "generate_response_for_image", vlm)

    manager = image_manager_module.ImageManager.__new__(image_manager_module.ImageManager)
    assert await manager._generate_image_description(data, fmt) == "描述"
    assert contexts == {"image_type": expected_type}
    assert vlm.await_args.args[2] == expected_format
