"""MCP 连接生命周期测试使用的 stdio 服务器夹具。

用法：``python mcp_stdio_server_fixture.py <状态文件路径> [echo|hang|reject|late]``

- ``echo``：提供 ``echo`` 与 ``wait_for_release`` 工具的最小 FastMCP 服务器。
- ``hang``：不响应任何请求，直到 stdin 被关闭后退出，用于模拟初始化无响应。
- ``reject``：以 JSON-RPC 错误拒绝 ``initialize`` 请求后保持运行，用于模拟建连失败。
- ``late``：收到 ``initialize`` 请求后写入 ``received``，直到 stdin 被关闭才写出响应并退出，
  用于模拟就绪前关闭连接时服务器的响应晚到。

启动时向状态文件写入 ``started``；stdin 被关闭、服务器正常退出时改写为 ``exited``，
供测试确认客户端是否按正常路径关闭了传输并回收子进程。
"""

from pathlib import Path

import asyncio
import json
import sys

REJECT_MESSAGE = "fixture server rejected initialize"


def _serve_echo() -> None:
    """运行提供 ``echo`` 与 ``wait_for_release`` 工具的 FastMCP 服务器。"""

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("lifecycle-fixture", log_level="WARNING")

    @server.tool()
    def echo(text: str) -> str:
        """原样返回文本。"""

        return text

    @server.tool()
    async def wait_for_release(release_file: str) -> str:
        """等待测试创建释放文件后返回，用于模拟进行中的长耗时工具调用。"""

        release_path = Path(release_file)
        while not release_path.exists():
            await asyncio.sleep(0.05)
        return "released"

    server.run("stdio")


def _reject_initialize() -> None:
    """以 JSON-RPC 错误响应 ``initialize``，然后等待 stdin 关闭。"""

    request = json.loads(sys.stdin.readline())
    response = {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": -32603, "message": REJECT_MESSAGE},
    }
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
    sys.stdin.read()


def _reply_after_stdin_closed(state_file: Path) -> None:
    """收到 ``initialize`` 请求后不响应，等 stdin 被关闭后才写出响应。"""

    request = json.loads(sys.stdin.readline())
    state_file.write_text("received", encoding="utf-8")
    sys.stdin.read()
    response = {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": -32603, "message": REJECT_MESSAGE},
    }
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def main() -> None:
    state_file = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "echo"
    state_file.write_text("started", encoding="utf-8")

    if mode == "echo":
        _serve_echo()
    elif mode == "hang":
        sys.stdin.read()
    elif mode == "reject":
        _reject_initialize()
    elif mode == "late":
        _reply_after_stdin_closed(state_file)
    else:
        raise SystemExit(f"未知模式: {mode}")
    state_file.write_text("exited", encoding="utf-8")


if __name__ == "__main__":
    main()
