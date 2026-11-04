"""Worker 向宿主提交共享环境维护请求；回执不代表安装完成。"""

import asyncio
import json
import os

from src.common.runtime_context import runtime_context
from src.plugin_runtime.transport.factory import create_transport_client


async def request_dependency_preparation() -> None:
    endpoint = os.environ.get("MAIBOT_HOST_CONTROL_ENDPOINT")
    if not endpoint:
        raise RuntimeError("运行契约缺少宿主依赖维护通道")
    connection = await asyncio.wait_for(create_transport_client(endpoint).connect(), timeout=5)
    try:
        await connection.send_frame(json.dumps({
            "action": "prepare_plugin_dependencies", "bot_id": runtime_context().bot_id,
        }).encode("utf-8"))
        response = json.loads(await asyncio.wait_for(connection.recv_frame(), timeout=10))
        if not response["ok"]:
            raise RuntimeError(response["error"])
    finally:
        await connection.close()
