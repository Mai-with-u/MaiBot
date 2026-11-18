"""统一消息入口：路径指定唯一 Bot，认证信息由该 Bot 的协议服务验证。"""

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, WSMsgType
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from yarl import URL

import asyncio
import re

from src.common.ipc import http_connector

from .supervisor import BotSupervisor


def register_maim_gateway(app: FastAPI, supervisor: BotSupervisor) -> None:
    @app.websocket("/maim/{bot_id}/{protocol}")
    async def connect(websocket: WebSocket, bot_id: str, protocol: str) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", bot_id) is None or protocol not in {"ws", "api"}:
            await websocket.close(code=1008, reason="必须指定有效的 Bot ID 和协议路径")
            return
        try:
            endpoint = supervisor.proxy_endpoint(bot_id)
        except RuntimeError:
            await websocket.close(code=1013, reason="Bot 不存在、未启动或尚未就绪")
            return
        # 不转发浏览器 Cookie、Origin 或任意代理头；协议认证原样交给目标 Bot。
        headers = {key: websocket.headers[key] for key in ("authorization", "platform", "x-apikey", "x-platform", "x-uuid") if key in websocket.headers}
        url = URL(f"http://bot/_maim/{protocol}").with_query(websocket.query_params)
        async with ClientSession(
            connector=http_connector(endpoint), cookie_jar=DummyCookieJar(),
            timeout=ClientTimeout(total=None, sock_connect=5),
        ) as client:
            try:
                upstream = await client.ws_connect(url, headers=headers, max_msg_size=104_857_600)
            except (ClientError, TimeoutError):
                await websocket.close(code=1013, reason="此 Bot 的消息协议未启用或已停止")
                return
            await websocket.accept()

            async def incoming() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send_str(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send_bytes(message["bytes"])

            async def outgoing() -> None:
                async for message in upstream:
                    if message.type == WSMsgType.TEXT:
                        await websocket.send_text(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await websocket.send_bytes(message.data)

            tasks = {asyncio.create_task(incoming()), asyncio.create_task(outgoing())}
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        await task
                    except (ClientError, OSError, WebSocketDisconnect):
                        pass  # 单次连接结束，不重放消息、不改接另一个 Bot。
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                code = upstream.close_code
                await upstream.close()
                try:
                    await websocket.close(code=code if code and code not in {1005, 1006, 1015} else 1001)
                except (RuntimeError, WebSocketDisconnect):
                    pass  # 对端已发送 disconnect。
