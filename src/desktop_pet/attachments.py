"""桌宠图文请求文件：大附件不经过按行读取的进程管道。"""

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List
from PIL import Image
import base64
import json
import re

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 48 * 1024 * 1024


def read_request(directory: Path, request_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("无效的桌宠请求 ID")
    path = directory / f"{request_id}.json"
    try:
        if path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("图文消息过大")
        request = json.loads(path.read_text("utf-8"))
        text = request["text"].strip()
        images: List[Dict[str, str]] = request["images"]
        if len(text) > 10000 or len(images) > 4 or not (text or images):
            raise ValueError("最多 10000 字、4 张图片，消息不能为空")
        for item in images:
            data = base64.b64decode(item["base64"], validate=True)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("每张图片不能超过 8 MiB")
            with Image.open(BytesIO(data)) as picture:
                if picture.width * picture.height > 25_000_000:
                    raise ValueError("图片不能超过 2500 万像素")
                item["mime_type"] = Image.MIME[picture.format]
                picture.verify()
        return {"text": text, "images": images}
    finally:
        path.unlink(missing_ok=True)
