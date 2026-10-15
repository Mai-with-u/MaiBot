"""工具返回图片的轻量监控缩略图。"""

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from io import BytesIO
from typing import Dict, List
from urllib.parse import urlsplit

from PIL import Image, ImageOps

from src.common.logger import get_logger
from src.core.tooling import ToolContentItem

logger = get_logger("maisaka_monitor")
THUMBNAIL_SIZE = (192, 192)


def build_tool_result_images(items: List[ToolContentItem]) -> List[Dict[str, str]]:
    """在线程池中生成缩略图；只传小图，避免原始 Base64 进入快照和账本。"""

    images: List[Dict[str, str]] = []
    for item in items:
        if item.content_type != "image" and not (
            item.content_type in {"binary", "resource", "resource_link"}
            and item.mime_type.lower().startswith("image/")
        ):
            continue
        image = {"label": item.name or item.description or "工具返回图片", "thumbnail_url": ""}
        images.append(image)
        raw_data = item.data.strip()
        uri = item.uri.strip()
        if not raw_data and uri.startswith("data:"):
            raw_data = uri
        try:
            if not raw_data:
                # URL 型图片由浏览器懒加载，不携带后端鉴权，也不由后端代为访问。
                if urlsplit(uri).scheme in {"http", "https"}:
                    image["thumbnail_url"] = uri
                else:
                    image["error"] = "图片缺少可显示的数据或链接"
                continue
            if raw_data.startswith("data:"):
                header, raw_data = raw_data.split(",", 1)
                if not header.endswith(";base64"):
                    raise ValueError("工具图片 data URL 必须使用 Base64")
            raw_data = "".join(raw_data.split())
            binary_data = b64decode(raw_data + "=" * (-len(raw_data) % 4), validate=True)
            with Image.open(BytesIO(binary_data)) as original:
                # 动图只取首帧，避免缩略图持续解码；统一格式也避免传递 SVG 等主动内容。
                original.thumbnail(THUMBNAIL_SIZE)
                thumbnail = ImageOps.exif_transpose(original).convert("RGBA")
                with BytesIO() as output:
                    thumbnail.save(output, format="WEBP", quality=65)
                    image["thumbnail_url"] = "data:image/webp;base64," + b64encode(output.getvalue()).decode("ascii")
        except (BinasciiError, ValueError, OSError, Image.DecompressionBombError):
            logger.warning(f"生成工具返回图片缩略图失败: {image['label']}", exc_info=True)
            image["error"] = "图片数据无效，无法生成缩略图"
    return images
