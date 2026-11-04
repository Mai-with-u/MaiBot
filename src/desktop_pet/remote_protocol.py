"""远端桌面能力的数据边界；不导入主程序配置或窗口库。"""

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Protocol
from PIL import Image
import base64
import hashlib

from .screen import DesktopScreenshot


class RemotePeer(Protocol):
    client_id: str
    capabilities: Dict[str, bool]

    async def send(self, command: Dict[str, Any]) -> None: ...

    async def close(self, reason: str) -> None: ...


def decode_screenshot(payload: Dict[str, Any]) -> DesktopScreenshot:
    """校验真实图片与捕获时间，摘要和尺寸由服务端从内容重新计算。"""
    encoded = payload["base64"]
    if not isinstance(encoded, str) or len(encoded) > 24 * 1024 * 1024:
        raise ValueError("远端截图超过大小限制")
    data = base64.b64decode(encoded, validate=True)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("远端截图不能超过 16 MiB")
    captured_at = payload["captured_at"]
    if not isinstance(captured_at, str) or len(captured_at) > 64:
        raise ValueError("远端截图时间无效")
    captured = datetime.fromisoformat(captured_at)
    if captured.tzinfo is None:
        raise ValueError("远端截图时间必须包含时区")
    with Image.open(BytesIO(data)) as picture:
        if picture.format != "PNG" or max(picture.size) > 1920:
            raise ValueError("远端截图必须为最长边不超过 1920 像素的 PNG")
        width, height = picture.size
        picture.verify()
    return DesktopScreenshot(data, captured_at, width, height, hashlib.sha256(data).hexdigest())
