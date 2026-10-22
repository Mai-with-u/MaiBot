"""模型上下文使用的表情包缩略图缓存，不修改原始组件或磁盘文件。"""

from concurrent.futures import Future
from io import BytesIO
from typing import Dict, OrderedDict, Tuple

from PIL import Image, ImageOps

import base64
import sys
import threading

from src.llm_models.payload_content.context_item import ContextImagePart, SUPPORTED_IMAGE_FORMATS

EMOJI_MAX_SIDE = 256
EMOJI_CACHE_MAX_BYTES = 32 * 1024 * 1024
EMOJI_CACHE_MAX_ENTRIES = 512
_ENCODING_VERSION = 1
CacheKey = Tuple[str, int, int]


def _prepare_emoji_image(image_bytes: bytes) -> ContextImagePart:
    """小图保持原样；大图取首帧、摆正方向并等比例缩小，保留透明度。"""
    with Image.open(BytesIO(image_bytes)) as image:
        image_format = image.format.lower() if image.format else ""
        if max(image.size) <= EMOJI_MAX_SIDE and image_format in SUPPORTED_IMAGE_FORMATS:
            return ContextImagePart(image_format, base64.b64encode(image_bytes).decode("ascii"))

        image.seek(0)
        frame = ImageOps.exif_transpose(image)
        frame = frame.convert("RGBA" if frame.has_transparency_data else "RGB")
        scale = min(1.0, EMOJI_MAX_SIDE / max(frame.size))
        if scale < 1.0:
            frame = frame.resize(
                (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
                Image.Resampling.LANCZOS,
            )
        output = BytesIO()
        if frame.has_transparency_data:
            frame.save(output, format="PNG")
            image_format = "png"
        else:
            frame.save(output, format="JPEG", quality=95)
            image_format = "jpeg"
        return ContextImagePart(image_format, base64.b64encode(output.getvalue()).decode("ascii"))


class EmojiImageCache:
    """线程间共享的 LRU，同时约束缓存字节数、条目数和图片转换并发。"""

    def __init__(
        self,
        max_bytes: int = EMOJI_CACHE_MAX_BYTES,
        max_entries: int = EMOJI_CACHE_MAX_ENTRIES,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_entries = max_entries
        self._entries: OrderedDict[CacheKey, Tuple[ContextImagePart, int]] = OrderedDict()
        self._size_bytes = 0
        self._lock = threading.Lock()
        self._conversion_slots = threading.Semaphore(2)
        self._pending: Dict[CacheKey, Future[ContextImagePart]] = {}

    def get(self, binary_hash: str, image_bytes: bytes) -> ContextImagePart:
        """返回最终编码结果；调用方应在线程池中执行未命中的转换工作。"""
        key = (binary_hash, EMOJI_MAX_SIDE, _ENCODING_VERSION)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key][0]

        # 只在获得转换槽后登记任务，保证待生成记录也有界；同一图片共享转换结果。
        with self._conversion_slots:
            with self._lock:
                if key in self._entries:
                    self._entries.move_to_end(key)
                    return self._entries[key][0]
                pending = self._pending.get(key)
                owns_conversion = pending is None
                if pending is None:
                    pending = Future()
                    self._pending[key] = pending
            if not owns_conversion:
                return pending.result()

            try:
                part = _prepare_emoji_image(image_bytes)
                # 包括 Base64 字符串和键/值对象的开销；索引额外开销由条目上限约束。
                size = (
                    sys.getsizeof(key)
                    + sum(sys.getsizeof(value) for value in key)
                    + sys.getsizeof(part)
                    + sys.getsizeof(part.image_format)
                    + sys.getsizeof(part.image_base64)
                )
                with self._lock:
                    if self._max_entries > 0 and size <= self._max_bytes:
                        while self._entries and (
                            len(self._entries) >= self._max_entries or self._size_bytes + size > self._max_bytes
                        ):
                            _, (_, removed_size) = self._entries.popitem(last=False)
                            self._size_bytes -= removed_size
                        self._entries[key] = (part, size)
                        self._size_bytes += size
                pending.set_result(part)
                return part
            except BaseException as exc:
                pending.set_exception(exc)
                raise
            finally:
                with self._lock:
                    del self._pending[key]


emoji_image_cache = EmojiImageCache()
