"""为 URL 型宿主头像协议提供本机临时图片资源，不传递 Telegram Token。"""

from io import BytesIO
from typing import Dict, Optional, Tuple

from aiohttp import web
from PIL import Image

import secrets
import time

AVATAR_TTL = 600
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_RELAY_BYTES = 20 * 1024 * 1024


def avatar_mime(binary: bytes) -> str:
    if not binary or len(binary) > MAX_AVATAR_BYTES:
        raise ValueError("头像必须非空且不能超过 5 MB")
    with Image.open(BytesIO(binary)) as image:
        mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.format)
        if mime is None:
            raise ValueError("Telegram 头像必须为 JPEG、PNG 或 WEBP")
        image.verify()
    return mime


class AvatarRelay:
    def __init__(self) -> None:
        self._runner: Optional[web.AppRunner] = None
        self._port = 0
        self._images: Dict[str, Tuple[float, bytes, str]] = {}

    async def publish(self, binary: bytes, mime: str) -> str:
        if self._runner is None:
            app = web.Application()
            app.router.add_get("/avatar/{key}", self._serve)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            try:
                await web.TCPSite(runner, "127.0.0.1", 0).start()
            except BaseException:
                await runner.cleanup()
                raise
            self._runner = runner
            self._port = runner.addresses[0][1]
        now = time.monotonic()
        self._images = {key: item for key, item in self._images.items() if item[0] > now}
        total = sum(len(item[1]) for item in self._images.values())
        while self._images and total + len(binary) > MAX_RELAY_BYTES:
            key = next(iter(self._images))
            total -= len(self._images.pop(key)[1])
        key = secrets.token_urlsafe(24)
        self._images[key] = (now + AVATAR_TTL, binary, mime)
        return f"http://127.0.0.1:{self._port}/avatar/{key}"

    async def _serve(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        entry = self._images.get(key)
        if entry is None or entry[0] <= time.monotonic():
            self._images.pop(key, None)
            raise web.HTTPNotFound()
        return web.Response(body=entry[1], content_type=entry[2], headers={"Cache-Control": "private, no-store"})

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._images.clear()
        self._port = 0
