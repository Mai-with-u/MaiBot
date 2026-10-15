"""WebUI 响应压缩中间件。

只压缩一次性返回的文本类响应（JSON、HTML 等）；流式响应原样透传，
避免 gzip 缓冲改变流式接口逐块到达的行为。
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import asyncio
import gzip

# 小于该体积的响应压缩收益抵不上 gzip 头部开销
MINIMUM_COMPRESS_SIZE = 1024
# 超过该体积的响应放到线程里压缩，避免占用 WebUI 事件循环
THREAD_COMPRESS_SIZE = 64 * 1024
COMPRESS_LEVEL = 6
COMPRESSIBLE_CONTENT_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/xml",
        "image/svg+xml",
        "text/css",
        "text/html",
        "text/javascript",
        "text/plain",
        "text/xml",
    }
)


def accepts_gzip(accept_encoding: str) -> bool:
    """判断请求头 Accept-Encoding 是否允许 gzip。"""

    return "gzip" in accept_encoding.lower()


class TextGZipMiddleware:
    """对非流式的文本类响应做 gzip 压缩。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not accepts_gzip(Headers(scope=scope).get("Accept-Encoding", "")):
            await self.app(scope, receive, send)
            return

        # 文本类响应的 start 消息先扣下，等拿到第一块 body 确认不是流式后再决定是否改写响应头
        pending_start: Message | None = None

        async def send_with_compression(message: Message) -> None:
            nonlocal pending_start

            if message["type"] == "http.response.start":
                headers = Headers(raw=message["headers"])
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type in COMPRESSIBLE_CONTENT_TYPES and "content-encoding" not in headers:
                    pending_start = message
                    return
                await send(message)
                return

            if pending_start is None:
                await send(message)
                return

            start_message = pending_start
            pending_start = None
            body: bytes = message.get("body", b"") if message["type"] == "http.response.body" else b""
            is_single_body = message["type"] == "http.response.body" and not message.get("more_body", False)
            if is_single_body and len(body) >= MINIMUM_COMPRESS_SIZE:
                if len(body) >= THREAD_COMPRESS_SIZE:
                    compressed = await asyncio.to_thread(gzip.compress, body, COMPRESS_LEVEL)
                else:
                    compressed = gzip.compress(body, COMPRESS_LEVEL)

                response_headers = MutableHeaders(raw=start_message["headers"])
                response_headers["Content-Encoding"] = "gzip"
                response_headers["Content-Length"] = str(len(compressed))
                response_headers.add_vary_header("Accept-Encoding")
                message = {**message, "body": compressed}

            await send(start_message)
            await send(message)

        await self.app(scope, receive, send_with_compression)
