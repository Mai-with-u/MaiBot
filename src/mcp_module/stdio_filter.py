"""容错版 MCP stdio 客户端包装。

MCP stdio 协议规定 stdout 仅可承载 JSON-RPC 消息，所有日志/状态/调试输出
必须写到 stderr（参见 https://modelcontextprotocol.io/specification/server/transports#stdio）。

但部分第三方 MCP 服务器（典型如 ``moegirl-wiki-mcp`` 等 npm 包）会把启动横幅
直接打印到 stdout，污染协议流。``mcp.client.stdio.stdio_client`` 遇到这类
非 JSON 行时会把 ``Exception`` 对象写回 read_stream，使 ``ClientSession``
将其视为致命错误并终止 ``initialize`` 协商。

本模块提供 :func:`tolerant_stdio_client`：行为与官方 ``stdio_client`` 基本一致，
主要差异在于在解析层先按 JSON 起始符做廉价预筛、解析失败的行只记录告警后丢弃，
不向上层流注入异常。这样违规服务器仍可正常握手，合规服务器行为不变。

注意：本模块依赖 ``mcp.client.stdio`` 中的若干非公开符号
(``_create_platform_compatible_process``、``_get_executable_command``、
``_terminate_process_tree``、``PROCESS_TERMINATION_TIMEOUT``)。
SDK 升级若调整这些符号，此处会在首次连接时立刻报错暴露问题。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TextIO
import logging
import sys

from anyio.streams.text import TextReceiveStream
import anyio
import anyio.lowlevel

from mcp import types
from mcp.client.stdio import (
    PROCESS_TERMINATION_TIMEOUT,
    StdioServerParameters,
    _create_platform_compatible_process,
    _get_executable_command,
    _terminate_process_tree,
    get_default_environment,
)
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

_MAX_GARBAGE_PREVIEW = 200


@asynccontextmanager
async def tolerant_stdio_client(
    server: StdioServerParameters,
    errlog: TextIO = sys.stderr,
):
    """官方 ``stdio_client`` 的容错替代实现。

    Args:
        server: stdio 子进程启动参数。
        errlog: 子进程 stderr 透传目标，默认 ``sys.stderr``。

    Yields:
        tuple[Any, Any]: ``(read_stream, write_stream)``，与官方实现接口一致。

    实现差异：
        - 仅对以 ``{`` 或 ``[`` 开头的非空行尝试 JSON-RPC 解析。
        - 预筛失败或 pydantic 校验失败的行通过 ``logger.warning`` 记录后直接丢弃，
          不会以 ``Exception`` 对象的形式注入 read_stream。
        - ``ClientSession`` 退出、接收端已关闭后服务器才写出的消息按正常关停丢弃，
          不会让读取任务以 ``BrokenResourceError`` 结束并导致子进程被强制终止。
        - 进程生命周期、stdin 关闭流程、平台兼容的进程组终止策略与官方实现完全一致。
    """

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    try:
        command = _get_executable_command(server.command)
        process = await _create_platform_compatible_process(
            command=command,
            args=server.args,
            env=(
                {**get_default_environment(), **server.env}
                if server.env is not None
                else get_default_environment()
            ),
            errlog=errlog,
            cwd=server.cwd,
        )
    except OSError:
        await read_stream.aclose()
        await write_stream.aclose()
        await read_stream_writer.aclose()
        await write_stream_reader.aclose()
        raise

    async def stdout_reader() -> None:
        assert process.stdout, "Opened process is missing stdout"
        try:
            async with read_stream_writer:
                buffer = ""
                async for chunk in TextReceiveStream(
                    process.stdout,
                    encoding=server.encoding,
                    errors=server.encoding_error_handler,
                ):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # JSON-RPC 2.0 消息总是 JSON 对象（'{'）或批量数组（'['）；
                        # 任何其他起始字符都可以无歧义判定为协议违规噪声。
                        if stripped[0] not in ("{", "["):
                            logger.warning(
                                "Dropped non-JSON line from MCP stdio server "
                                "(violates MCP spec — stdout must carry JSON-RPC only): %r",
                                line[:_MAX_GARBAGE_PREVIEW],
                            )
                            continue
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception:
                            logger.warning(
                                "Dropped malformed JSON-RPC line from MCP stdio server: %r",
                                line[:_MAX_GARBAGE_PREVIEW],
                            )
                            continue
                        await read_stream_writer.send(SessionMessage(message))
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # BrokenResourceError：ClientSession 已退出并关闭了接收端（如就绪前关闭连接，或关闭时在途请求的
            # 响应晚到），之后服务器写出的消息已无人接收，按正常关停处理，不能让它打断子进程的正常退出
            await anyio.lowlevel.checkpoint()

    async def stdin_writer() -> None:
        assert process.stdin, "Opened process is missing stdin"
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    json_payload = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                    await process.stdin.send(
                        (json_payload + "\n").encode(
                            encoding=server.encoding,
                            errors=server.encoding_error_handler,
                        )
                    )
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async with (
        anyio.create_task_group() as tg,
        process,
    ):
        tg.start_soon(stdout_reader)
        tg.start_soon(stdin_writer)
        try:
            yield read_stream, write_stream
        finally:
            if process.stdin:
                try:
                    await process.stdin.aclose()
                except Exception:
                    pass
            try:
                with anyio.fail_after(PROCESS_TERMINATION_TIMEOUT):
                    await process.wait()
            except TimeoutError:
                await _terminate_process_tree(process)
            except ProcessLookupError:
                pass
            await read_stream.aclose()
            await write_stream.aclose()
            await read_stream_writer.aclose()
            await write_stream_reader.aclose()
