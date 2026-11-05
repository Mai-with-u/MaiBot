"""将私有 IPC 上的消息协议 ASGI 请求切回 Bot 主循环，读写仍在 HTTP 循环。"""

from starlette.types import Message, Receive, Scope, Send

import asyncio

from src.common.runtime_loop import get_main_loop, run_on_main_loop


class MaimApplication:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        http_loop = asyncio.get_running_loop()

        async def main_receive() -> Message:
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(receive(), http_loop))

        async def main_send(message: Message) -> None:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(send(message), http_loop))

        async def dispatch() -> None:
            from src.common.message_server.api import global_api, message_app

            if global_api is None or not global_api._running:
                await main_send({"type": "websocket.close", "code": 1013, "reason": "消息服务未就绪或已关闭"})
                return
            await message_app(scope, main_receive, main_send)

        if scope["type"] != "websocket":
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        loop = get_main_loop()
        if loop is None or not loop.is_running():
            await send({"type": "websocket.close", "code": 1013})
            return
        await run_on_main_loop(dispatch())
