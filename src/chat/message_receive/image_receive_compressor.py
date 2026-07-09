from dataclasses import dataclass
from typing import List, Literal

import hashlib

from src.common.data_models.message_component_data_model import (
    EmojiComponent,
    ForwardNodeComponent,
    ImageComponent,
    StandardMessageComponents,
)
from src.common.logger import get_logger
from src.common.utils.utils_image import ImageUtils

logger = get_logger("image_receive_compressor")

OversizedImageHandleMethod = Literal["compress", "discard"]


@dataclass
class ImageReceiveProcessReport:
    compressed_count: int = 0
    discarded_count: int = 0
    original_bytes: int = 0
    compressed_bytes: int = 0
    discarded_bytes: int = 0


def process_received_images_in_message(components: List[StandardMessageComponents]) -> ImageReceiveProcessReport:
    """按视觉配置处理入站消息中的过大图片组件。"""
    from src.config.config import global_config

    visual_config = global_config.visual
    if not visual_config.handle_oversized_images:
        return ImageReceiveProcessReport()

    max_image_size_bytes = int(float(visual_config.max_image_size_mb) * 1024 * 1024)
    if max_image_size_bytes <= 0:
        return ImageReceiveProcessReport()

    return _process_image_components(
        components,
        max_image_size_bytes,
        visual_config.oversized_image_handle_method,
    )


def _process_image_components(
    components: List[StandardMessageComponents],
    max_image_size_bytes: int,
    handle_method: OversizedImageHandleMethod,
) -> ImageReceiveProcessReport:
    report = ImageReceiveProcessReport()
    retained_components: List[StandardMessageComponents] = []
    for component in components:
        if isinstance(component, (ImageComponent, EmojiComponent)):
            if _process_image_component(component, max_image_size_bytes, handle_method, report):
                retained_components.append(component)
            continue
        if isinstance(component, ForwardNodeComponent):
            for forward_component in component.forward_components:
                child_report = _process_image_components(
                    forward_component.content,
                    max_image_size_bytes,
                    handle_method,
                )
                _merge_process_report(report, child_report)
        retained_components.append(component)
    components[:] = retained_components
    return report


def _merge_process_report(target: ImageReceiveProcessReport, source: ImageReceiveProcessReport) -> None:
    target.compressed_count += source.compressed_count
    target.discarded_count += source.discarded_count
    target.original_bytes += source.original_bytes
    target.compressed_bytes += source.compressed_bytes
    target.discarded_bytes += source.discarded_bytes


def _process_image_component(
    component: ImageComponent | EmojiComponent,
    max_image_size_bytes: int,
    handle_method: OversizedImageHandleMethod,
    report: ImageReceiveProcessReport,
) -> bool:
    image_bytes = component.binary_data
    if not image_bytes or len(image_bytes) <= max_image_size_bytes:
        return True

    component_name = _get_component_log_name(component)
    if handle_method == "discard":
        report.discarded_count += 1
        report.discarded_bytes += len(image_bytes)
        logger.info(f"接收{component_name}过大，已丢弃: {len(image_bytes) / 1024:.1f}KB, image_hash={component.binary_hash}")
        return False

    if handle_method == "compress":
        _compress_image_component(component, image_bytes, max_image_size_bytes, report, component_name)
        return True

    raise ValueError(f"未知的过大图片处理方法: {handle_method}")


def _get_component_log_name(component: ImageComponent | EmojiComponent) -> str:
    if isinstance(component, EmojiComponent):
        return "表情"
    return "图片"


def _compress_image_component(
    component: ImageComponent | EmojiComponent,
    image_bytes: bytes,
    max_image_size_bytes: int,
    report: ImageReceiveProcessReport,
    component_name: str,
) -> None:
    try:
        compressed_bytes = ImageUtils.compress_image_to_size(image_bytes, max_image_size_bytes)
    except Exception as exc:
        logger.warning(f"接收{component_name}压缩失败，保持原图: {exc}")
        return
    if len(compressed_bytes) >= len(image_bytes):
        return

    component.binary_data = compressed_bytes
    component.binary_hash = hashlib.sha256(compressed_bytes).hexdigest()
    report.compressed_count += 1
    report.original_bytes += len(image_bytes)
    report.compressed_bytes += len(compressed_bytes)
    logger.info(
        f"接收{component_name}已压缩: "
        f"{len(image_bytes) / 1024:.1f}KB -> {len(compressed_bytes) / 1024:.1f}KB, "
        f"image_hash={component.binary_hash}"
    )
