from io import BytesIO
from typing import List, Tuple
from PIL import Image

import base64
import pytest

from src.llm_models.image_normalizer import normalize_context_images, normalize_image_part
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItemMeta,
    ContextTextPart,
    ProviderReplayFragment,
    ProviderScope,
    UserMessageItem,
)


def picture(size: Tuple[int, int], color: str = "red") -> ContextImagePart:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return ContextImagePart("png", base64.b64encode(output.getvalue()).decode("ascii"))


@pytest.mark.parametrize("size", [(10, 20), (600, 400), (20, 2000), (2000, 20), (1, 2000)])
def test_resizing_and_splitting_obey_both_dimensions(size: Tuple[int, int]) -> None:
    parts = normalize_image_part(picture(size), max_side=100, min_side=32)
    assert 1 <= len(parts) <= 16
    for part in parts:
        with Image.open(BytesIO(base64.b64decode(part.image_base64))) as image:
            assert min(image.size) >= 32
            assert max(image.size) <= 100


def test_compliant_picture_preserves_original_bytes() -> None:
    part = picture((60, 80))
    assert normalize_image_part(part, max_side=100, min_side=32) == [part]
    assert normalize_image_part(part, max_side=100, min_side=32)[0] is part


def test_request_limit_keeps_recent_images_and_history_immutable() -> None:
    older = picture((10, 10), "red")
    newer = picture((10, 10), "blue")
    first = UserMessageItem(ContextItemMeta.create(), (older,))
    last = AssistantMessageItem(ContextItemMeta.create(), (ContextTextPart("描述"), newer))
    result = normalize_context_images([first, last], max_images=1)
    assert result[0].parts == (ContextTextPart("[图片因模型数量限制已省略]"),)
    assert result[1] is last
    assert first.parts == (older,)


def test_limit_counts_generated_slices() -> None:
    item = UserMessageItem(ContextItemMeta.create(), (picture((20, 2000)),))
    result = normalize_context_images([item], max_images=2, max_side=100)
    images: List[ContextImagePart] = [part for part in result[0].parts if isinstance(part, ContextImagePart)]
    assert len(images) == 2
    assert item.parts[0].image_base64 != images[0].image_base64


@pytest.mark.parametrize("max_images,max_side", [(1, 8000), (0, 8)])
def test_changed_assistant_images_invalidate_provider_replay(max_images: int, max_side: int) -> None:
    replay = ProviderReplayFragment.from_payload(
        ProviderScope(1, "openai", "provider", "endpoint", "model"), {"content": "original"}
    )
    original = AssistantMessageItem(
        ContextItemMeta.create(), (picture((10, 10)), picture((10, 10), "blue")), replay=replay
    )
    result = normalize_context_images([original], max_images=max_images, max_side=max_side)
    assert result[0].replay is None
    assert original.replay is replay


def test_one_pixel_maximum_terminates_splitting() -> None:
    parts = normalize_image_part(picture((1, 30)), max_side=1)
    assert len(parts) == 16


def test_invalid_limits_are_exposed() -> None:
    with pytest.raises(ValueError):
        normalize_context_images([], max_side=100, min_side=101)
