"""模型图片限制的边界、分段内容与历史消息回放验证。"""

from datetime import datetime
from io import BytesIO
from typing import List, Tuple
import base64

from PIL import Image
import pytest

from src.llm_models.image_normalizer import (
    MODEL_IMAGE_MAX_SEGMENTS,
    MODEL_IMAGE_MAX_SIDE,
    MODEL_IMAGE_OVERLAP,
    normalize_context_images,
    normalize_image_part,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItemMeta,
    ContextTextPart,
    ProviderReplayFragment,
    ProviderScope,
    UserMessageItem,
)


def _encode(image: Image.Image, image_format: str = "PNG") -> ContextImagePart:
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return ContextImagePart(image_format.lower(), base64.b64encode(buffer.getvalue()).decode("ascii"))


def _decode(parts: List[ContextImagePart]) -> List[Image.Image]:
    return [Image.open(BytesIO(base64.b64decode(part.image_base64))) for part in parts]


def test_image_at_limit_keeps_original_bytes() -> None:
    part = _encode(Image.new("L", (8000, 20)))
    assert normalize_image_part(part) == [part]
    assert normalize_image_part(part)[0] is part


def test_oversized_regular_image_scales_proportionally() -> None:
    parts = normalize_image_part(_encode(Image.new("L", (8100, 2700))))
    assert len(parts) == 1
    assert _decode(parts)[0].size == (8000, 2666)


@pytest.mark.parametrize("size", [(700, 10800), (10800, 700)])
def test_long_screenshot_splits_with_overlap_and_preserves_pixels(size: Tuple[int, int]) -> None:
    image = Image.new("L", size)
    vertical = size[1] > size[0]
    # 渐变标记用于验证片段顺序、完整覆盖和重叠内容，而非仅检查输出尺寸。
    for position in range(10800):
        box = (0, position, 700, position + 1) if vertical else (position, 0, position + 1, 700)
        image.paste(position % 256, box)
    segments = _decode(normalize_image_part(_encode(image)))
    assert len(segments) == 6
    start = 0
    for segment in segments:
        assert max(segment.size) <= MODEL_IMAGE_MAX_SIDE
        length = segment.height if vertical else segment.width
        box = (0, start, 700, start + length) if vertical else (start, 0, start + length, 700)
        assert segment.tobytes() == image.crop(box).tobytes()
        start += length - MODEL_IMAGE_OVERLAP
    assert start + MODEL_IMAGE_OVERLAP == 10800


def test_extreme_image_has_bounded_segments() -> None:
    segments = _decode(normalize_image_part(_encode(Image.new("L", (10, 150000)))))
    assert len(segments) == MODEL_IMAGE_MAX_SEGMENTS
    assert all(max(segment.size) <= MODEL_IMAGE_MAX_SIDE for segment in segments)


def test_transparent_image_keeps_alpha() -> None:
    segments = _decode(normalize_image_part(_encode(Image.new("RGBA", (40, 8100), (255, 0, 0, 80)))))
    assert all(segment.mode == "RGBA" and segment.getpixel((0, 0)) == (255, 0, 0, 80) for segment in segments)


def test_oversized_gif_uses_first_frame() -> None:
    buffer = BytesIO()
    first = Image.new("RGB", (20, 8100), "red")
    first.save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (20, 8100), "blue")],
        duration=100,
    )
    part = ContextImagePart("gif", base64.b64encode(buffer.getvalue()).decode("ascii"))
    parts = normalize_image_part(part)
    assert len(parts) > 1
    assert all(part.image_format == "png" for part in parts)
    assert all(segment.convert("RGB").getpixel((0, 0)) == (255, 0, 0) for segment in _decode(parts))


def test_context_preserves_order_metadata_and_original_history() -> None:
    image = _encode(Image.new("L", (700, 10800)))
    before, after = ContextTextPart("before"), ContextTextPart("after")
    meta = ContextItemMeta(item_id="image-message", logical_turn_id=None, timestamp=datetime(2026, 10, 8))
    original = UserMessageItem(meta=meta, parts=(before, image, after))
    unchanged = UserMessageItem(
        meta=ContextItemMeta(item_id="text-message", logical_turn_id=None, timestamp=datetime(2026, 10, 8)),
        parts=(before,),
    )
    result = normalize_context_images([unchanged, original])
    assert result[0] is unchanged
    assert result[1].meta is meta
    assert result[1].parts[0] is before and result[1].parts[-1] is after
    assert len(result[1].parts) == 8
    assert original.parts == (before, image, after)


def test_modified_assistant_drops_replay() -> None:
    replay = ProviderReplayFragment.from_payload(
        ProviderScope(
            schema_version=1,
            client_type="openai_responses",
            provider_name="test",
            endpoint_fingerprint="test",
            model_identifier="test",
        ),
        {"type": "message", "content": []},
    )
    original = AssistantMessageItem(
        meta=ContextItemMeta(item_id="assistant-image", logical_turn_id=None, timestamp=datetime(2026, 10, 8)),
        parts=(_encode(Image.new("L", (700, 10800))),),
        replay=replay,
    )
    result = normalize_context_images([original])[0]
    assert result.replay is None
    assert original.replay is replay


def test_invalid_image_remains_for_existing_client_validation() -> None:
    part = ContextImagePart("png", "invalid-base64")
    assert normalize_image_part(part)[0] is part
