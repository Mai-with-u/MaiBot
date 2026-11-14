"""模型请求图片的尺寸限制与长截图分段。"""

from dataclasses import replace
from io import BytesIO
from typing import List
from PIL import Image, ImageOps, UnidentifiedImageError

import base64
import binascii
import math

from src.common.logger import get_logger
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextContentPart,
    ContextImagePart,
    ContextItem,
    ContextTextPart,
    SystemMessageItem,
    UserMessageItem,
)

MODEL_IMAGE_MAX_SIDE = 8000
MODEL_IMAGE_SPLIT_RATIO = 3
MODEL_IMAGE_OVERLAP = 24
MODEL_IMAGE_MAX_SEGMENTS = 16
logger = get_logger("model_image_normalizer")


def _resize_image(image: Image.Image, scale: float) -> Image.Image:
    """保持比例缩放图片，并保证极窄图片仍有至少一个像素。"""
    return image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )


def normalize_image_part(
    part: ContextImagePart,
    max_side: int = MODEL_IMAGE_MAX_SIDE,
    min_side: int = 1,
) -> List[ContextImagePart]:
    """按模型边长限制处理图片；长图按顺序分段，其他图片等比例缩放。

    超限动图使用首帧，PNG 等图片保留透明通道。尺寸合规的图片保持原始数据。
    """
    try:
        image_bytes = base64.b64decode(part.image_base64, validate=True)
        image = Image.open(BytesIO(image_bytes))
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        # 无效图片仍交给原有客户端校验逻辑处理，尺寸处理不改变其既有行为。
        return [part]

    with image:
        long_side, short_side = max(image.size), min(image.size)
        if long_side <= max_side and short_side >= min_side:
            return [part]

        image.seek(0)
        frame = image.copy()
        overlap = min(MODEL_IMAGE_OVERLAP, max_side - 1)
        if long_side <= max_side or long_side / short_side <= MODEL_IMAGE_SPLIT_RATIO:
            scale = min(max(1.0, min_side / short_side), max_side / long_side)
            frame = _resize_image(frame, scale)
            segments = [frame]
        else:
            # 先约束短边和最大总长度，再裁剪，避免极长图片生成无界数量的图片。
            max_length = (
                max_side * MODEL_IMAGE_MAX_SEGMENTS
                - overlap * (MODEL_IMAGE_MAX_SEGMENTS - 1)
            )
            scale = min(1.0, max_side / short_side, max_length / long_side)
            if scale < 1.0:
                frame = _resize_image(frame, scale)
            long_side, short_side = max(frame.size), min(frame.size)
            segment_length = min(
                max_side,
                max(
                    short_side * MODEL_IMAGE_SPLIT_RATIO,
                    math.ceil(
                        (long_side + overlap * (MODEL_IMAGE_MAX_SEGMENTS - 1))
                        / MODEL_IMAGE_MAX_SEGMENTS
                    ),
                ),
            )
            segments: List[Image.Image] = []
            start = 0
            while start < long_side:
                end = min(start + segment_length, long_side)
                box = (0, start, frame.width, end) if frame.height >= frame.width else (start, 0, end, frame.height)
                segments.append(frame.crop(box))
                if end == long_side:
                    break
                start = end - overlap

        normalized_parts: List[ContextImagePart] = []
        image_format = "jpeg" if image.format == "JPEG" else "png"
        for segment in segments:
            if min(segment.size) < min_side:
                scale = min(min_side / min(segment.size), max_side / max(segment.size))
                segment = _resize_image(segment, scale)
                # 极端长宽比无法同时满足两端限制时，补边以保留完整内容。
                segment = ImageOps.expand(
                    segment,
                    border=(0, 0, max(0, min_side - segment.width), max(0, min_side - segment.height)),
                )
            if image_format == "jpeg":
                segment = segment.convert("RGB")
            elif segment.mode not in {"RGB", "RGBA", "L", "LA", "P", "I;16"}:
                segment = segment.convert("RGBA")
            output = BytesIO()
            if image_format == "jpeg":
                segment.save(output, format="JPEG", quality=95)
            else:
                segment.save(output, format="PNG")
            normalized_parts.append(
                ContextImagePart(image_format, base64.b64encode(output.getvalue()).decode("ascii"))
            )
        return normalized_parts


def normalize_context_images(
    items: List[ContextItem],
    max_images: int = 0,
    max_side: int = MODEL_IMAGE_MAX_SIDE,
    min_side: int = 1,
) -> List[ContextItem]:
    """规范化全部图片并保留最近的限额内图片，不修改共享的历史对象。"""
    if max_images < 0 or not 1 <= min_side <= max_side:
        raise ValueError("图片限制必须满足数量 >= 0，1 <= 最小边长 <= 最大边长")
    normalized_items: List[ContextItem] = []
    for item in items:
        if not isinstance(item, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
            normalized_items.append(item)
            continue
        parts: List[ContextContentPart] = []
        changed = False
        for part in item.parts:
            if isinstance(part, ContextImagePart):
                images = normalize_image_part(part, max_side, min_side)
                parts.extend(images)
                changed = changed or len(images) != 1 or images[0] is not part
            else:
                parts.append(part)
        if not changed:
            normalized_items.append(item)
        elif isinstance(item, AssistantMessageItem):
            # 修改正文后，原始 Provider 回放片段已失效，不能绕过尺寸限制重发原图。
            normalized_items.append(replace(item, parts=tuple(parts), replay=None))
        else:
            normalized_items.append(replace(item, parts=tuple(parts)))
    if not max_images:
        return normalized_items
    image_count = sum(
        isinstance(part, ContextImagePart)
        for item in normalized_items
        if isinstance(item, (SystemMessageItem, UserMessageItem, AssistantMessageItem))
        for part in item.parts
    )
    excess = image_count - max_images
    if excess <= 0:
        return normalized_items
    logger.info(f"请求图片数量 {image_count} 超过模型上限 {max_images}，保留最近 {max_images} 张图片")
    limited_items: List[ContextItem] = []
    for item in normalized_items:
        if excess <= 0 or not isinstance(item, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
            limited_items.append(item)
            continue
        parts = []
        changed = False
        for part in item.parts:
            if isinstance(part, ContextImagePart) and excess > 0:
                parts.append(ContextTextPart("[图片因模型数量限制已省略]"))
                excess -= 1
                changed = True
            else:
                parts.append(part)
        if changed and isinstance(item, AssistantMessageItem):
            limited_items.append(replace(item, parts=tuple(parts), replay=None))
        elif changed:
            limited_items.append(replace(item, parts=tuple(parts)))
        else:
            limited_items.append(item)
    return limited_items
