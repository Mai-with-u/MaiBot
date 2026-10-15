"""Maisaka 上下文对不受支持图片格式（MPO、BMP 等）的转码测试。"""

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Iterator, List, Optional, Tuple
import base64
import hashlib

from PIL import Image as PILImage
import pytest

from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ImageComponent,
    MessageSequence,
    StandardMessageComponents,
)
from src.llm_models.payload_content.context_item import ContextImagePart, UserMessageItem
from src.maisaka.context import messages as messages_module
from src.maisaka.context.messages import SessionBackedMessage

EXIF_ORIENTATION_TAG = 0x0112


@pytest.fixture(autouse=True)
def clear_transcode_cache() -> Iterator[None]:
    messages_module._transcoded_image_cache.clear()
    yield
    messages_module._transcoded_image_cache.clear()


def _build_mpo_bytes(*, orientation: Optional[int] = None) -> bytes:
    """构造双帧 MPO：首帧红色、次帧蓝色，与手机相机拍摄的多帧照片结构一致。"""

    frames = [PILImage.new("RGB", (8, 4), color) for color in ("red", "blue")]
    save_kwargs: Dict[str, Any] = {"format": "MPO", "save_all": True, "append_images": frames[1:]}
    if orientation is not None:
        exif = PILImage.Exif()
        exif[EXIF_ORIENTATION_TAG] = orientation
        save_kwargs["exif"] = exif
    output_buffer = BytesIO()
    frames[0].save(output_buffer, **save_kwargs)
    return output_buffer.getvalue()


def _encode_image(image_format: str, color: Any = "green", mode: str = "RGB") -> bytes:
    output_buffer = BytesIO()
    PILImage.new(mode, (6, 6), color).save(output_buffer, format=image_format)
    return output_buffer.getvalue()


def _build_image_parts(component: StandardMessageComponents) -> List[ContextImagePart]:
    message = SessionBackedMessage(
        raw_message=MessageSequence([component]),
        visible_text="[图片]",
        timestamp=datetime(2026, 10, 7, 12, 0, 0),
    )
    item = message.to_context_item(enable_visual_message=True)
    assert isinstance(item, UserMessageItem)
    return [part for part in item.parts if isinstance(part, ContextImagePart)]


def _decode_image_part(part: ContextImagePart, expected_format: str) -> PILImage.Image:
    assert part.image_format == expected_format
    with PILImage.open(BytesIO(base64.b64decode(part.image_base64))) as image:
        assert image.format == expected_format.upper()
        image.load()
        return image.copy()


def test_multi_frame_mpo_image_is_transcoded_to_jpeg_first_frame() -> None:
    mpo_bytes = _build_mpo_bytes()
    with PILImage.open(BytesIO(mpo_bytes)) as image:
        assert image.format == "MPO"
        assert image.n_frames == 2

    image_parts = _build_image_parts(ImageComponent(binary_hash="mpo", binary_data=mpo_bytes))

    assert len(image_parts) == 1
    # MPO 本身是 JPEG 容器，照片首帧应转码为 JPEG 而非体积膨胀数倍的 PNG。
    first_frame = _decode_image_part(image_parts[0], "jpeg")
    red, green, blue = first_frame.convert("RGB").getpixel((0, 0))
    assert red > 200 and green < 50 and blue < 50


def test_mpo_exif_orientation_is_applied_before_transcode() -> None:
    image_parts = _build_image_parts(
        ImageComponent(binary_hash="mpo-rotated", binary_data=_build_mpo_bytes(orientation=6))
    )

    assert _decode_image_part(image_parts[0], "jpeg").size == (4, 8)


def test_opaque_bmp_emoji_is_transcoded_to_jpeg() -> None:
    image_parts = _build_image_parts(EmojiComponent(binary_hash="bmp", binary_data=_encode_image("BMP")))

    assert len(image_parts) == 1
    red, green, blue = _decode_image_part(image_parts[0], "jpeg").convert("RGB").getpixel((0, 0))
    assert red < 20 and 110 < green < 145 and blue < 20


def test_transparent_tiff_emoji_is_transcoded_to_png_keeping_alpha() -> None:
    tiff_bytes = _encode_image("TIFF", color=(255, 0, 0, 0), mode="RGBA")

    image_parts = _build_image_parts(EmojiComponent(binary_hash="tiff-alpha", binary_data=tiff_bytes))

    assert len(image_parts) == 1
    assert _decode_image_part(image_parts[0], "png").convert("RGBA").getpixel((0, 0)) == (255, 0, 0, 0)


def test_supported_image_format_is_passed_through_without_transcode(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_transcode(image_bytes: bytes) -> Tuple[str, str]:
        raise AssertionError("受支持的图片格式不应被解码转码")

    monkeypatch.setattr(messages_module, "_transcode_first_frame_for_context", fail_transcode)
    jpeg_bytes = _encode_image("JPEG")

    image_parts = _build_image_parts(ImageComponent(binary_hash="jpeg", binary_data=jpeg_bytes))

    assert image_parts[0].image_format == "jpeg"
    assert image_parts[0].image_base64 == base64.b64encode(jpeg_bytes).decode("utf-8")


def test_unsupported_image_is_transcoded_once_across_context_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    """规划器每轮重建上下文时，同一张图片只解码转码一次，缓存键是摘要而不是原始图片字节。"""

    transcode_calls: List[bytes] = []
    original_transcode = messages_module._transcode_first_frame_for_context

    def counting_transcode(image_bytes: bytes) -> Tuple[str, str]:
        transcode_calls.append(image_bytes)
        return original_transcode(image_bytes)

    monkeypatch.setattr(messages_module, "_transcode_first_frame_for_context", counting_transcode)
    mpo_bytes = _build_mpo_bytes()
    component = ImageComponent(binary_hash="mpo", binary_data=mpo_bytes)

    first_parts = _build_image_parts(component)
    second_parts = _build_image_parts(component)

    assert first_parts[0].image_base64 == second_parts[0].image_base64
    assert len(transcode_calls) == 1
    assert list(messages_module._transcoded_image_cache) == [hashlib.sha256(mpo_bytes).hexdigest()]


def test_transcode_cache_evicts_least_recently_used_entry() -> None:
    """缓存条数不超过上限，超出时淘汰最久未使用的图片。"""

    bmp_images = [
        _encode_image("BMP", color=(index, 0, 0)) for index in range(messages_module.TRANSCODED_IMAGE_CACHE_SIZE + 1)
    ]
    for bmp_bytes in bmp_images:
        _build_image_parts(ImageComponent(binary_hash="bmp", binary_data=bmp_bytes))

    cache_keys = list(messages_module._transcoded_image_cache)
    assert len(cache_keys) == messages_module.TRANSCODED_IMAGE_CACHE_SIZE
    assert hashlib.sha256(bmp_images[0]).hexdigest() not in cache_keys
    assert cache_keys[-1] == hashlib.sha256(bmp_images[-1]).hexdigest()


def test_undecodable_unsupported_image_still_raises() -> None:
    truncated_bmp = _encode_image("BMP")[:60]
    with PILImage.open(BytesIO(truncated_bmp)) as image:
        assert image.format == "BMP"

    with pytest.raises(OSError):
        _build_image_parts(ImageComponent(binary_hash="broken-bmp", binary_data=truncated_bmp))
