"""Maisaka 图片规范化（分割/缩放）测试。"""

from io import BytesIO
import base64

from PIL import Image

from src.common.data_models.message_component_data_model import ImageComponent
from src.llm_models.payload_content.context_item import (
    ContextImagePart,
    ContextItemBuilder,
    RoleType,
)
from src.maisaka.context.messages import (
    MAISAKA_IMAGE_MAX_SIDE,
    MAISAKA_IMAGE_SEGMENT_OVERLAP,
    _append_image_component,
    _normalize_maisaka_image,
    _split_maisaka_image,
)


def _image_bytes(image: Image.Image, image_format: str = "JPEG") -> bytes:
    """将 PIL 图片编码为字节数据。"""

    output_buffer = BytesIO()
    image.save(output_buffer, format=image_format)
    return output_buffer.getvalue()


def _decode_segments(segments: list[tuple[str, str]]) -> list[Image.Image]:
    """将规范化后的 (format, base64) 段解码为 PIL 图片列表。"""

    return [Image.open(BytesIO(base64.b64decode(base64_data))) for _, base64_data in segments]


def test_normal_image_kept_unchanged() -> None:
    """普通尺寸图片应保持原样，不分割不缩放。"""

    image = Image.new("RGB", (716, 528), "white")
    segments = _normalize_maisaka_image(_image_bytes(image), "jpeg")

    assert len(segments) == 1
    decoded = _decode_segments(segments)[0]
    assert decoded.size == (716, 528)


def test_tall_image_split_along_height() -> None:
    """超长图应沿高度方向分割为多段。"""

    image = Image.new("RGB", (700, 10800), "white")
    segments = _normalize_maisaka_image(_image_bytes(image), "jpeg")

    assert len(segments) == 3
    decoded = _decode_segments(segments)
    assert all(segment.size[0] == 700 for segment in decoded)
    assert all(segment.size[1] <= MAISAKA_IMAGE_MAX_SIDE for segment in decoded)
    # 段间重叠：第一段与第二段应重叠 MAISAKA_IMAGE_SEGMENT_OVERLAP 像素
    assert decoded[0].size[1] == MAISAKA_IMAGE_MAX_SIDE
    assert decoded[1].size[1] == MAISAKA_IMAGE_MAX_SIDE


def test_wide_image_split_along_width() -> None:
    """超宽图应沿宽度方向分割为多段。"""

    image = Image.new("RGB", (10800, 700), "white")
    segments = _normalize_maisaka_image(_image_bytes(image), "jpeg")

    assert len(segments) == 3
    decoded = _decode_segments(segments)
    assert all(segment.size[1] == 700 for segment in decoded)
    assert all(segment.size[0] <= MAISAKA_IMAGE_MAX_SIDE for segment in decoded)


def test_large_image_scaled_down() -> None:
    """单边超限但长宽比正常的图片应等比例缩放。"""

    image = Image.new("RGB", (5000, 3000), "white")
    segments = _normalize_maisaka_image(_image_bytes(image), "jpeg")

    assert len(segments) == 1
    decoded = _decode_segments(segments)[0]
    assert max(decoded.size) <= MAISAKA_IMAGE_MAX_SIDE
    # 保持长宽比（允许取整误差）
    assert abs(decoded.size[0] / decoded.size[1] - 5000 / 3000) < 0.01


def test_split_segments_overlap() -> None:
    """分割段之间应保留重叠像素。"""

    image = Image.new("RGB", (700, 10800), "white")
    segments = _split_maisaka_image(image, 700, 10800)

    assert len(segments) == 3
    # 段0 高度 4096，段1 从 4096-24 开始，段2 从 8168-24 开始
    assert segments[0].size == (700, MAISAKA_IMAGE_MAX_SIDE)
    assert segments[1].size == (700, MAISAKA_IMAGE_MAX_SIDE)
    assert segments[2].size == (700, 10800 - 2 * (MAISAKA_IMAGE_MAX_SIDE - MAISAKA_IMAGE_SEGMENT_OVERLAP))


def test_animated_gif_kept_unchanged() -> None:
    """GIF 动图应保持原样，不做分割/缩放。"""

    gif_buffer = BytesIO()
    frames = [Image.new("RGB", (100, 100), "red"), Image.new("RGB", (100, 100), "blue")]
    frames[0].save(
        gif_buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    segments = _normalize_maisaka_image(gif_buffer.getvalue(), "gif")

    assert len(segments) == 1
    assert segments[0][0] == "gif"


def test_image_component_append_uses_normalized_segments() -> None:
    """验证 _append_image_component 对超长图会追加多张图片。"""

    image = Image.new("RGB", (700, 10800), "white")
    component = ImageComponent(
        binary_hash="test-hash",
        binary_data=_image_bytes(image),
    )
    builder = ContextItemBuilder().set_role(RoleType.User)

    appended = _append_image_component(builder, component, enable_visual_message=True)
    assert appended is True

    item = builder.build()
    image_parts = [part for part in item.parts if isinstance(part, ContextImagePart)]
    assert len(image_parts) == 3
    assert all(part.image_format == "jpeg" for part in image_parts)


def test_extreme_tall_image_falls_back_to_scaling() -> None:
    """极长图片分割段数超过上限时改为整体缩放。"""

    image = Image.new("RGB", (700, 10800 * 5), "white")
    segments = _normalize_maisaka_image(_image_bytes(image), "jpeg")

    assert len(segments) == 1
    decoded = _decode_segments(segments)[0]
    assert max(decoded.size) <= MAISAKA_IMAGE_MAX_SIDE
    # 整体缩放保持长宽比：宽度 = 4096 * (700 / 54000) ≈ 53
    assert abs(decoded.size[0] / decoded.size[1] - 700 / 54000) < 0.01


def test_rgba_image_flattened_to_white_background() -> None:
    """RGBA 透明图应合成到白色背景上，透明区域为白色而非黑色。"""

    image = Image.new("RGBA", (100, 100), (255, 0, 0, 0))  # 全透明红色
    segments = _normalize_maisaka_image(_image_bytes(image, "PNG"), "png")

    assert len(segments) == 1
    decoded = _decode_segments(segments)[0]
    assert decoded.mode == "RGB"
    # 透明区域合成到白色背景后应为白色
    assert decoded.getpixel((50, 50)) == (255, 255, 255)


def test_palette_image_with_transparency_flattened() -> None:
    """含 transparency 信息的 P 模式图片应合成到白色背景上。"""

    image = Image.new("P", (100, 100))
    image.info["transparency"] = 0
    image.putpalette([255, 0, 0, 0, 255, 0, 0, 0, 255] + [0, 0, 0] * 253)
    segments = _normalize_maisaka_image(_image_bytes(image, "PNG"), "png")

    assert len(segments) == 1
    decoded = _decode_segments(segments)[0]
    assert decoded.mode == "RGB"
