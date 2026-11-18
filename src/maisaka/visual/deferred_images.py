"""请求构建期间暂存图片编码任务，只编码最终保留的图片。

内部占位仅存在于同步构建作用域；返回请求前必须解析，不能交给序列化或模型客户端。
ContextVar 隔离并发构建，不改写历史消息、原始图片或公共 Context Item 协议。
"""

from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from typing import Callable, Dict, List, Optional, ParamSpec, Tuple, TypeVar
import uuid

from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextContentPart,
    ContextImagePart,
    ContextItem,
    ContextItemBuilder,
    SystemMessageItem,
    UserMessageItem,
)

P = ParamSpec("P")
R = TypeVar("R")
ImageEncoder = Callable[[], Tuple[str, str]]
_pending_images: ContextVar[Optional[Dict[str, ImageEncoder]]] = ContextVar("pending_context_images", default=None)


def append_context_image(builder: ContextItemBuilder, encode: ImageEncoder) -> None:
    """无构建作用域时保持立即编码；作用域内仅登记一次图片出现位置。"""
    pending = _pending_images.get()
    if pending is None:
        builder.add_image_content(*encode())
        return
    marker = f"deferred-image:{uuid.uuid4().hex}"
    pending[marker] = encode
    builder.add_image_content("png", marker)


def transform_context_image(
    part: ContextImagePart,
    transform: Callable[[ContextImagePart], ContextImagePart],
) -> ContextImagePart:
    """对已编码图片立即变换；延迟图片将变换组合进编码任务，仍只处理最终保留项。"""
    pending = _pending_images.get()
    if pending is None or part.image_base64 not in pending:
        return transform(part)

    encode = pending[part.image_base64]

    def encode_transformed() -> Tuple[str, str]:
        image_format, image_base64 = encode()
        transformed = transform(ContextImagePart(image_format, image_base64))
        return transformed.image_format, transformed.image_base64

    # 为变换结果创建独立任务，不改写原片段及其编码器；同图的不同尺寸可共存。
    marker = f"deferred-image:{uuid.uuid4().hex}"
    pending[marker] = encode_transformed
    return replace(part, image_base64=marker)


def defer_context_image_encoding(
    select_items: Callable[[R], List[ContextItem]],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """在现有构建/裁剪逻辑返回后解析保留图片，丢弃其他编码任务。

    select_items 必须返回构建函数新创建的请求列表；列表内的历史 Item 仍按不可变对象处理。
    既支持直接返回请求列表，也支持同时返回历史计数的规划器。
    """
    def decorate(build: Callable[P, R]) -> Callable[P, R]:
        @wraps(build)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            pending: Dict[str, ImageEncoder] = {}
            token = _pending_images.set(pending)
            try:
                result = build(*args, **kwargs)
            except BaseException:
                pending.clear()
                raise
            finally:
                _pending_images.reset(token)
            try:
                items = select_items(result)
                for index, item in enumerate(items):
                    if not isinstance(item, (SystemMessageItem, UserMessageItem, AssistantMessageItem)):
                        continue
                    parts: List[ContextContentPart] = []
                    changed = False
                    for part in item.parts:
                        if isinstance(part, ContextImagePart) and part.image_base64 in pending:
                            image_format, image_base64 = pending[part.image_base64]()
                            parts.append(ContextImagePart(image_format=image_format, image_base64=image_base64))
                            changed = True
                        else:
                            parts.append(part)
                    if changed:
                        items[index] = replace(item, parts=tuple(parts))
                return result
            finally:
                # 被裁掉的任务也可能引用大图片；不能让闭包跨请求保留。
                pending.clear()
        return wrapped
    return decorate
