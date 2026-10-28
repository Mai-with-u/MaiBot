"""GIF 抽帧的像素差计算与相似帧过滤回归测试。"""

from io import BytesIO
from typing import List

from PIL import Image
import pytest

from src.common.utils.utils_image import ImageUtils


def _build_gif(values: List[int]) -> bytes:
    frames = [Image.new("RGB", (20, 20), (value, value, value)) for value in values]
    buffer = BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("values", "expected", "max_frames"),
    [
        ([0, 255], [0, 255], 15),
        ([255, 0], [255, 0], 15),
        ([0, 20], [0], 15),
        ([0, 20, 40], [0, 40], 15),
        ([0, 255, 0, 255], [0, 255, 0, 255], 15),
        ([0, 255, 0, 255], [0, 255], 2),
        ([0, 255] * 9, [0, 255] * 7 + [0], 15),
    ],
)
def test_gif_frame_selection(values: List[int], expected: List[int], max_frames: int) -> None:
    result = ImageUtils.gif_2_static_image(_build_gif(values), max_frames=max_frames)

    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (200 * len(expected), 200)
        for i, value in enumerate(expected):
            pixel = image.getpixel((i * 200 + 100, 100))
            assert all(abs(channel - value) <= 2 for channel in pixel)


def test_gif_frame_selection_uses_strict_mse_threshold() -> None:
    result = ImageUtils.gif_2_static_image(_build_gif([0, 30, 31]), similarity_threshold=900.0)

    with Image.open(BytesIO(result)) as image:
        assert image.size == (400, 200)
        assert image.getpixel((100, 100)) == (0, 0, 0)
        assert all(abs(channel - 31) <= 2 for channel in image.getpixel((300, 100)))
