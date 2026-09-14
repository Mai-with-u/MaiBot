from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import hashlib
import io
import os
import tempfile


_FORMAT_TO_MIME = {
    "BMP": "image/bmp",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class InspectedImage:
    byte_size: int
    extension: str
    height: int
    mime_type: str
    width: int
    frame_count: int


class ImageAssetStore:
    """在受控目录中按原始入库字节的 SHA-256 保存静态图片。"""

    def __init__(self, assets_root: Path, *, max_bytes: int, max_pixels: int) -> None:
        self.assets_root = Path(assets_root).resolve()
        self.max_bytes = max(1, int(max_bytes))
        self.max_pixels = max(1, int(max_pixels))
        self.assets_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def content_hash(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    def inspect(self, image_bytes: bytes) -> InspectedImage:
        payload = bytes(image_bytes)
        if not payload:
            raise ValueError("图片内容为空")
        if len(payload) > self.max_bytes:
            raise ValueError(f"图片超过大小限制: {len(payload)} > {self.max_bytes}")

        from PIL import Image as PILImage
        from PIL import UnidentifiedImageError

        try:
            with PILImage.open(io.BytesIO(payload)) as image:
                image.verify()
            with PILImage.open(io.BytesIO(payload)) as image:
                image_format = str(image.format or "").upper()
                if image_format not in _FORMAT_TO_MIME:
                    raise ValueError(f"不支持的图片格式: {image_format or 'unknown'}")
                width, height = int(image.width), int(image.height)
                frame_count = int(getattr(image, "n_frames", 1) or 1)
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("无法解析图片内容") from exc
        if width <= 0 or height <= 0 or width * height > self.max_pixels:
            raise ValueError(f"图片像素超过限制: {width}x{height}")
        return InspectedImage(
            byte_size=len(payload),
            extension="jpg" if image_format == "JPEG" else image_format.lower(),
            height=height,
            mime_type=_FORMAT_TO_MIME[image_format],
            width=width,
            frame_count=frame_count,
        )

    def _resolve(self, storage_key: str) -> Path:
        key = str(storage_key or "").strip().replace("\\", "/")
        if not key or Path(key).is_absolute() or ".." in Path(key).parts:
            raise ValueError("图片资产键非法")
        target = (self.assets_root / key).resolve()
        if target.parent != self.assets_root:
            raise ValueError("图片资产键越出存储目录")
        return target

    def publish(self, image_bytes: bytes, inspected: InspectedImage) -> tuple[str, str]:
        payload = bytes(image_bytes)
        digest = self.content_hash(payload)
        storage_key = f"{digest}.{inspected.extension}"
        target = self._resolve(storage_key)
        if target.is_file():
            if target.stat().st_size != len(payload) or self._hash_file(target) != digest:
                raise RuntimeError(f"内容寻址图片资产发生冲突: {storage_key}")
            return digest, storage_key
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", suffix=".tmp", dir=self.assets_root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return digest, storage_key

    @staticmethod
    def _hash_stream(handle: BinaryIO) -> str:
        hasher = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
        return hasher.hexdigest()

    @classmethod
    def _hash_file(cls, path: Path) -> str:
        with path.open("rb") as handle:
            return cls._hash_stream(handle)

    def read_bytes(self, storage_key: str) -> bytes:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise FileNotFoundError(f"图片资产不存在: {storage_key}")
        return path.read_bytes()

    def open(self, storage_key: str) -> BinaryIO:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise FileNotFoundError(f"图片资产不存在: {storage_key}")
        return path.open("rb")

    def path_for_read(self, storage_key: str) -> Path:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise FileNotFoundError(f"图片资产不存在: {storage_key}")
        return path

    def delete(self, storage_key: str) -> bool:
        path = self._resolve(storage_key)
        if not path.exists():
            return False
        path.unlink()
        return True
