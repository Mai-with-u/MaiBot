import asyncio
import contextlib

import pytest

from src.plugin_runtime.protocol.errors import RPCError
from src.plugin_runtime.runner.rpc_client import RPCClient
from src.plugin_runtime.transport.base import Connection


async def _connected_client():
    peer_connections: list = []
    ready = asyncio.Event()

    async def on_connect(reader, writer):
        peer_connections.append((reader, writer))
        ready.set()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await ready.wait()
    client = RPCClient("unused", "token")
    client._connection = Connection(reader, writer)
    client._running = True
    client._recv_task = asyncio.create_task(client._recv_loop())
    return client, server, peer_connections[0]


def test_peer_close_fails_pending_request_and_disconnects():
    async def scenario():
        client, server, (peer_reader, peer_writer) = await _connected_client()
        try:
            task = asyncio.create_task(client.send_request("m", timeout_ms=30000))
            await peer_reader.readexactly(4)
            peer_writer.close()
            with pytest.raises(RPCError):
                await asyncio.wait_for(task, timeout=2)
            assert not client.is_connected
            assert client._pending_requests == {}
        finally:
            await client.disconnect()
            server.close()

    asyncio.run(scenario())


def test_cancelled_request_removes_pending_entry():
    async def scenario():
        client, server, _peer = await _connected_client()
        try:
            task = asyncio.create_task(client.send_request("m", timeout_ms=30000))
            await asyncio.sleep(0.05)
            assert len(client._pending_requests) == 1
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            assert client._pending_requests == {}
        finally:
            await client.disconnect()
            server.close()

    asyncio.run(scenario())
