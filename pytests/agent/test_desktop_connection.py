from pathlib import Path

import asyncio
import json

import pytest

from src.agent.acp import ACPConnection


def test_desktop_auth_rpc_and_close_do_not_own_host(tmp_path: Path) -> None:
    async def scenario():
        disconnected = asyncio.Event()

        async def handle(reader, writer):
            try:
                assert json.loads(await reader.readline()) == {"token": "test-token"}
                writer.write(b'{"authenticated":true}\n')
                request = json.loads(await reader.readline())
                writer.write((json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}) + "\n").encode())
                await writer.drain()
                assert await reader.read() == b""
            finally:
                writer.close()
                await writer.wait_closed()
                disconnected.set()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        manifest = tmp_path / "desktop.json"
        manifest.write_text(json.dumps({"version": 1, "host": "127.0.0.1", "port": server.sockets[0].getsockname()[1], "token": "test-token"}))
        connection = ACPConnection(lambda update: asyncio.sleep(0))
        try:
            await connection.connect_desktop(manifest, 2)
            assert connection.process is None
            assert await connection.request("initialize", {}, 2) == {"ok": True}
            await connection.close(1)
            await asyncio.wait_for(disconnected.wait(), 2)
            assert server.is_serving()
        finally:
            await connection.close(1)
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_desktop_rejects_remote_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "desktop.json"
    manifest.write_text(json.dumps({"version": 1, "host": "example.com", "port": 1234, "token": "test-token"}))
    connection = ACPConnection(lambda update: asyncio.sleep(0))
    with pytest.raises(ValueError, match="本机"):
        asyncio.run(connection.connect_desktop(manifest, 2))
