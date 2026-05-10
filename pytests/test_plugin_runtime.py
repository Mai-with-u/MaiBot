"""插件运行时框架基础测试

验证协议层、传输层、RPC 通信链路的正确性。
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportIndexIssue=false, reportMissingImports=false, reportOptionalMemberAccess=false

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import asyncio
import json
import logging
import os
import sys

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# SDK 包路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "maibot-plugin-sdk"))


def build_test_manifest(
    plugin_id: str,
    *,
    version: str = "1.0.0",
    name: str = "测试插件",
    description: str = "测试插件描述",
    dependencies: list[dict[str, str]] | None = None,
    llm_providers: list[dict[str, str]] | None = None,
    capabilities: list[str] | None = None,
    host_min_version: str = "0.12.0",
    host_max_version: str = "1.0.0",
    sdk_min_version: str = "2.0.0",
    sdk_max_version: str = "2.99.99",
) -> dict[str, object]:
    """构造一个合法的 Manifest v2 测试样例。

    Args:
        plugin_id: 插件 ID。
        version: 插件版本。
        name: 展示名称。
        description: 插件描述。
        dependencies: 依赖声明列表。
        llm_providers: LLM Provider 静态声明列表。
        capabilities: 能力声明列表。
        host_min_version: Host 最低支持版本。
        host_max_version: Host 最高支持版本。
        sdk_min_version: SDK 最低支持版本。
        sdk_max_version: SDK 最高支持版本。

    Returns:
        dict[str, object]: 可直接序列化为 ``_manifest.json`` 的字典。
    """
    return {
        "manifest_version": 2,
        "version": version,
        "name": name,
        "description": description,
        "author": {
            "name": "tester",
            "url": "https://example.com/tester",
        },
        "license": "MIT",
        "urls": {
            "repository": f"https://example.com/{plugin_id}",
        },
        "host_application": {
            "min_version": host_min_version,
            "max_version": host_max_version,
        },
        "sdk": {
            "min_version": sdk_min_version,
            "max_version": sdk_max_version,
        },
        "dependencies": dependencies or [],
        "llm_providers": llm_providers or [],
        "capabilities": capabilities or [],
        "i18n": {
            "default_locale": "zh-CN",
            "supported_locales": ["zh-CN"],
        },
        "id": plugin_id,
    }


def build_test_manifest_model(
    plugin_id: str,
    *,
    version: str = "1.0.0",
    dependencies: list[dict[str, str]] | None = None,
    llm_providers: list[dict[str, str]] | None = None,
    capabilities: list[str] | None = None,
    host_version: str = "1.0.0",
    sdk_version: str = "2.0.1",
) -> object:
    """构造一个已经通过校验的强类型 Manifest 测试对象。

    Args:
        plugin_id: 插件 ID。
        version: 插件版本。
        dependencies: 依赖声明列表。
        llm_providers: LLM Provider 静态声明列表。
        capabilities: 能力声明列表。
        host_version: 当前测试使用的 Host 版本。
        sdk_version: 当前测试使用的 SDK 版本。

    Returns:
        object: ``PluginManifest`` 实例。
    """
    from src.plugin_runtime.runner.manifest_validator import ManifestValidator

    validator = ManifestValidator(host_version=host_version, sdk_version=sdk_version)
    manifest = validator.parse_manifest(
        build_test_manifest(
            plugin_id,
            version=version,
            dependencies=dependencies,
            llm_providers=llm_providers,
            capabilities=capabilities,
        )
    )
    assert manifest is not None
    return manifest


# ─── 协议层测试 ───────────────────────────────────────────


class TestProtocol:
    """协议层测试"""

    def test_envelope_create_and_serialize(self):
        """Envelope 创建与序列化"""
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType

        env = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="plugin.invoke_command",
            plugin_id="test_plugin",
            payload={"component_name": "greet", "args": {}},
        )

        assert env.request_id == 1
        assert env.is_request()
        assert env.method == "plugin.invoke_command"

        # 测试 make_response
        resp = env.make_response(payload={"success": True})
        assert resp.is_response()
        assert resp.request_id == 1
        assert resp.payload["success"] is True

    def test_envelope_make_error_response(self):
        """错误响应生成"""
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType

        env = Envelope(
            request_id=42,
            message_type=MessageType.REQUEST,
            method="cap.request",
        )

        err_resp = env.make_error_response("E_UNAUTHORIZED", "没有权限")
        assert err_resp.error is not None
        assert err_resp.error["code"] == "E_UNAUTHORIZED"
        assert err_resp.error["message"] == "没有权限"

    def test_msgpack_codec(self):
        """MsgPack 编解码"""
        from src.plugin_runtime.protocol.codec import MsgPackCodec
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType

        codec = MsgPackCodec()
        env = Envelope(
            request_id=100,
            message_type=MessageType.REQUEST,
            method="test.method",
            payload={"key": "value", "number": 42},
        )

        # 编码
        data = codec.encode_envelope(env)
        assert isinstance(data, bytes)

        # 解码
        decoded = codec.decode_envelope(data)
        assert decoded.request_id == 100
        assert decoded.method == "test.method"
        assert decoded.payload["key"] == "value"
        assert decoded.payload["number"] == 42

    def test_json_codec(self):
        """JSON 编解码已移除，仅保留 MsgPack"""
        pass

    def test_request_id_generator(self):
        """请求 ID 生成器单调递增"""
        from src.plugin_runtime.protocol.envelope import RequestIdGenerator

        gen = RequestIdGenerator()
        ids = [gen.next() for _ in range(100)]
        assert ids == list(range(1, 101))

    def test_error_codes(self):
        """错误码枚举"""
        from src.plugin_runtime.protocol.errors import ErrorCode, RPCError

        err = RPCError(ErrorCode.E_TIMEOUT, "请求超时")
        assert err.code == ErrorCode.E_TIMEOUT
        assert "E_TIMEOUT" in str(err)

        # 序列化/反序列化
        d = err.to_dict()
        err2 = RPCError.from_dict(d)
        assert err2.code == ErrorCode.E_TIMEOUT


# ─── 传输层测试 ───────────────────────────────────────────


class TestTransport:
    """传输层测试"""

    @pytest.mark.asyncio
    async def test_uds_connection_framing(self):
        """UDS 分帧协议测试"""
        from src.plugin_runtime.transport.uds import UDSTransportServer, UDSTransportClient

        server = UDSTransportServer()
        received = asyncio.Event()
        received_data = []

        async def handler(conn):
            data = await conn.recv_frame()
            received_data.append(data)
            await conn.send_frame(b"pong")
            received.set()

        await server.start(handler)
        address = server.get_address()

        client = UDSTransportClient(address)
        conn = await client.connect()
        await conn.send_frame(b"ping")

        # 等待服务端处理
        await asyncio.wait_for(received.wait(), timeout=5.0)
        assert received_data[0] == b"ping"

        # 接收服务端回复
        resp = await conn.recv_frame()
        assert resp == b"pong"

        await conn.close()
        await server.stop()

    @pytest.mark.asyncio
    async def test_tcp_connection_framing(self):
        """TCP 分帧协议测试"""
        from src.plugin_runtime.transport.tcp import TCPTransportServer, TCPTransportClient

        server = TCPTransportServer()
        received = asyncio.Event()
        received_data = []

        async def handler(conn):
            data = await conn.recv_frame()
            received_data.append(data)
            await conn.send_frame(b"tcp_pong")
            received.set()

        await server.start(handler)
        address = server.get_address()
        host, port = address.split(":")

        client = TCPTransportClient(host, int(port))
        conn = await client.connect()
        await conn.send_frame(b"tcp_ping")

        await asyncio.wait_for(received.wait(), timeout=5.0)
        assert received_data[0] == b"tcp_ping"

        resp = await conn.recv_frame()
        assert resp == b"tcp_pong"

        await conn.close()
        await server.stop()

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    async def test_named_pipe_connection_framing(self):
        """Windows Named Pipe 分帧协议测试"""
        from src.plugin_runtime.transport.named_pipe import NamedPipeTransportClient, NamedPipeTransportServer

        server = NamedPipeTransportServer()
        received = asyncio.Event()
        received_data = []

        async def handler(conn):
            data = await conn.recv_frame()
            received_data.append(data)
            await conn.send_frame(b"pipe_pong")
            received.set()

        await server.start(handler)
        client = NamedPipeTransportClient(server.get_address())
        conn = await client.connect()
        await conn.send_frame(b"pipe_ping")

        await asyncio.wait_for(received.wait(), timeout=5.0)
        assert received_data[0] == b"pipe_ping"

        resp = await conn.recv_frame()
        assert resp == b"pipe_pong"

        await conn.close()
        await server.stop()

    @pytest.mark.asyncio
    async def test_transport_factory(self):
        """传输工厂测试"""
        from src.plugin_runtime.transport.factory import create_transport_server, create_transport_client

        server = create_transport_server()
        assert server is not None

        # UDS 路径
        client = create_transport_client("/tmp/test.sock")
        assert client is not None

        # Windows Named Pipe 地址
        client = create_transport_client(r"\\.\pipe\maibot-test")
        assert client is not None

        # TCP 地址
        client = create_transport_client("127.0.0.1:9999")
        assert client is not None


# ─── Host 层测试 ──────────────────────────────────────────


class TestHost:
    """Host 端基础设施测试"""

    def test_policy_engine(self):
        """策略引擎测试"""
        from src.plugin_runtime.host.policy_engine import PolicyEngine

        engine = PolicyEngine()

        # 注册插件
        token = engine.register_plugin(
            plugin_id="test_plugin",
            generation=1,
            capabilities=["send.text", "db.query"],
        )

        assert token.plugin_id == "test_plugin"
        assert "send.text" in token.capabilities

        # 能力检查
        ok, _ = engine.check_capability("test_plugin", "send.text")
        assert ok

        ok, reason = engine.check_capability("test_plugin", "llm.generate")
        assert not ok
        assert "未获授权" in reason

        # 未注册插件
        ok, reason = engine.check_capability("unknown", "send.text")
        assert not ok

        ok, reason = engine.check_capability("test_plugin", "send.text", generation=2)
        assert not ok
        assert "generation 不匹配" in reason

    def test_policy_engine_allows_parallel_generations(self):
        """同一插件在热重载期间应允许 active/staged 两代并行持有能力令牌。"""
        from src.plugin_runtime.host.policy_engine import PolicyEngine

        engine = PolicyEngine()
        engine.register_plugin("test_plugin", generation=1, capabilities=["send.text"])
        engine.register_plugin("test_plugin", generation=2, capabilities=["send.text", "llm.generate"])

        ok, _ = engine.check_capability("test_plugin", "send.text", generation=1)
        assert ok is True

        ok, _ = engine.check_capability("test_plugin", "llm.generate", generation=2)
        assert ok is True

        ok, reason = engine.check_capability("test_plugin", "llm.generate", generation=1)
        assert ok is False
        assert "未获授权" in reason

    def test_circuit_breaker_removed(self):
        """熔断器已移除，验证 supervisor 不依赖它"""
        pass

    def test_circuit_breaker_registry_removed(self):
        """熔断器注册表已移除"""
        pass


# ─── SDK 测试 ─────────────────────────────────────────────


class TestSDK:
    """SDK 框架测试"""

    def test_component_decorators(self):
        """组件装饰器测试"""
        from maibot_sdk import MaiBotPlugin, Action, Command, Tool, EventHandler
        from maibot_sdk.types import ActivationType, EventType

        class TestPlugin(MaiBotPlugin):
            @Action("greet", activation_type=ActivationType.KEYWORD, activation_keywords=["hi"])
            async def handle_greet(self, **kwargs):
                return True, "ok"

            @Command("echo", pattern=r"^/echo")
            async def handle_echo(self, **kwargs):
                return True, "echoed", 2

            @Tool("search", parameters={"query": {"type": "string"}})
            async def handle_search(self, **kwargs):
                return {"result": "found"}

            @EventHandler("on_start", event_type=EventType.ON_START)
            async def handle_start(self, **kwargs):
                return True, False, "started"

        plugin = TestPlugin()
        components = plugin.get_components()

        assert len(components) == 4

        names = {c["name"] for c in components}
        assert "greet" in names
        assert "echo" in names
        assert "search" in names
        assert "on_start" in names

        types = {c["type"] for c in components}
        assert "action" in types
        assert "command" in types
        assert "tool" in types
        assert "event_handler" in types

    def test_plugin_context_not_initialized(self):
        """未初始化上下文时应报错"""
        from maibot_sdk import MaiBotPlugin

        plugin = MaiBotPlugin()
        with pytest.raises(RuntimeError, match="尚未初始化"):
            _ = plugin.ctx

    def test_plugin_context_injection(self):
        """上下文注入测试"""
        from maibot_sdk import MaiBotPlugin
        from maibot_sdk.context import PluginContext

        plugin = MaiBotPlugin()
        ctx = PluginContext(plugin_id="test")
        plugin._set_context(ctx)

        assert plugin.ctx.plugin_id == "test"
        assert plugin.ctx.send is not None
        assert plugin.ctx.db is not None
        assert plugin.ctx.llm is not None
        assert plugin.ctx.config is not None

    @pytest.mark.asyncio
    async def test_runner_injected_context_binds_plugin_identity(self):
        """Runner 注入的上下文应忽略调用方伪造的 plugin_id。"""
        from src.plugin_runtime.runner.runner_main import PluginRunner

        class DummyRPCClient:
            def __init__(self):
                self.calls = []

            async def send_request(self, method, plugin_id="", payload=None, timeout_ms=30000):
                self.calls.append(
                    {
                        "method": method,
                        "plugin_id": plugin_id,
                        "payload": payload,
                        "timeout_ms": timeout_ms,
                    }
                )
                return SimpleNamespace(
                    error=None,
                    payload={"success": True, "result": {"success": True, "result": {"ok": True}}},
                )

        class DummyPlugin:
            def _set_context(self, ctx):
                self.ctx = ctx

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        runner._rpc_client = DummyRPCClient()

        plugin = DummyPlugin()
        runner._inject_context("owner_plugin", plugin)

        plugin.ctx._plugin_id = "forged_plugin"
        result = await plugin.ctx.call_capability("send.text", text="hello", stream_id="stream-1")

        assert result is True
        assert runner._rpc_client.calls[0]["plugin_id"] == "owner_plugin"
        assert runner._rpc_client.calls[0]["method"] == "cap.call"

    @pytest.mark.asyncio
    async def test_runner_injected_context_unwraps_llm_available_models(self):
        """Runner 应为 SDK 解开 cap.call 响应外层，避免模型列表被规整成空列表。"""
        from src.plugin_runtime.runner.runner_main import PluginRunner

        class DummyRPCClient:
            async def send_request(self, method, plugin_id="", payload=None, timeout_ms=30000):
                assert method == "cap.call"
                assert plugin_id == "owner_plugin"
                assert payload == {"capability": "llm.get_available_models", "args": {}}
                return SimpleNamespace(
                    error=None,
                    payload={"success": True, "result": {"success": True, "models": ["utils", "replyer"]}},
                )

        class DummyPlugin:
            def _set_context(self, ctx):
                self.ctx = ctx

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        runner._rpc_client = DummyRPCClient()

        plugin = DummyPlugin()
        runner._inject_context("owner_plugin", plugin)

        assert await plugin.ctx.llm.get_available_models() == ["utils", "replyer"]

    @pytest.mark.asyncio
    async def test_runner_applies_initial_plugin_config(self, tmp_path):
        """Runner 应在 on_load 前为支持的插件实例注入 config.toml。"""
        from src.plugin_runtime.runner.runner_main import PluginRunner

        class DummyPlugin:
            def __init__(self):
                self.configs = []

            def set_plugin_config(self, config):
                self.configs.append(config)

        plugin_dir = tmp_path / "demo_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "config.toml").write_text("[section]\nvalue = 1\n", encoding="utf-8")

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        plugin = DummyPlugin()
        meta = SimpleNamespace(plugin_id="demo_plugin", plugin_dir=str(plugin_dir), instance=plugin)

        runner._apply_plugin_config(meta)

        assert plugin.configs == [{"section": {"value": 1}}]

    @pytest.mark.asyncio
    async def test_runner_config_update_refreshes_plugin_config_before_callback(self):
        """配置更新时应先刷新插件配置，再调用 on_config_update。"""
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType
        from src.plugin_runtime.runner.runner_main import PluginRunner

        class DummyPlugin:
            def __init__(self):
                self.configs = []
                self.updates = []

            def set_plugin_config(self, config):
                self.configs.append(config)

            async def on_config_update(self, scope, config, version):
                self.updates.append((scope, config, version, list(self.configs)))

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        plugin = DummyPlugin()
        runner._loader._loaded_plugins["demo_plugin"] = SimpleNamespace(instance=plugin)

        envelope = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="plugin.config_updated",
            plugin_id="demo_plugin",
            payload={
                "plugin_id": "demo_plugin",
                "config_scope": "self",
                "config_data": {"enabled": True},
                "config_version": "v2",
            },
        )

        response = await runner._handle_config_updated(envelope)

        assert response.payload["acknowledged"] is True
        assert plugin.configs == [{"enabled": True}]
        assert plugin.updates == [("self", {"enabled": True}, "v2", [{"enabled": True}])]

    @pytest.mark.asyncio
    async def test_runner_global_config_update_does_not_override_plugin_config(self):
        """bot/model 广播不应覆盖插件自身配置缓存。"""
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType
        from src.plugin_runtime.runner.runner_main import PluginRunner

        class DummyPlugin:
            def __init__(self):
                self.configs = []
                self.updates = []

            def set_plugin_config(self, config):
                self.configs.append(config)

            async def on_config_update(self, scope, config, version):
                self.updates.append((scope, config, version, list(self.configs)))

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        plugin = DummyPlugin()
        runner._loader._loaded_plugins["demo_plugin"] = SimpleNamespace(instance=plugin)
        plugin.set_plugin_config({"plugin_enabled": True})

        envelope = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="plugin.config_updated",
            plugin_id="demo_plugin",
            payload={
                "plugin_id": "demo_plugin",
                "config_scope": "model",
                "config_data": {"models": []},
                "config_version": "",
            },
        )

        response = await runner._handle_config_updated(envelope)

        assert response.payload["acknowledged"] is True
        assert plugin.configs == [{"plugin_enabled": True}]
        assert plugin.updates == [("model", {"models": []}, "", [{"plugin_enabled": True}])]

    @pytest.mark.asyncio
    async def test_host_logs_runner_ready_plugin_failures(self, caplog):
        """Host 收到 runner.ready 时应明确记录插件注册失败。"""
        from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType

        supervisor = PluginRunnerSupervisor(plugin_dirs=[], runner_spawn_timeout_sec=1)
        envelope = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="runner.ready",
            plugin_id="",
            payload={
                "loaded_plugins": ["ok_plugin"],
                "failed_plugins": ["bad_plugin"],
                "inactive_plugins": ["disabled_plugin"],
            },
        )

        with caplog.at_level(logging.INFO, logger="plugin_runtime.host.runner_manager"):
            response = await supervisor._handle_runner_ready(envelope)

        assert response.payload["accepted"] is True
        assert "插件注册失败: bad_plugin" in caplog.text
        assert "插件未激活: disabled_plugin" in caplog.text
        assert "Runner 插件初始化完成: loaded=1 failed=1 inactive=1" in caplog.text

    @pytest.mark.asyncio
    async def test_runner_bootstraps_capabilities_before_on_load(self, monkeypatch):
        """on_load 期间的 capability 调用应在 bootstrap 后生效。"""
        from src.plugin_runtime.runner.runner_main import PluginRunner

        class DummyRPCClient:
            def __init__(self):
                self.calls = []

            async def connect_and_handshake(self):
                return True

            def register_method(self, method, handler):
                return None

            async def send_request(self, method, plugin_id="", payload=None, timeout_ms=30000):
                self.calls.append(
                    {
                        "method": method,
                        "plugin_id": plugin_id,
                        "payload": payload,
                        "timeout_ms": timeout_ms,
                    }
                )
                if method == "cap.call":
                    bootstrap_methods = [call["method"] for call in self.calls[:-1]]
                    assert "plugin.bootstrap" in bootstrap_methods
                    return SimpleNamespace(error=None, payload={"success": True, "result": {"success": True}})
                return SimpleNamespace(error=None, payload={"accepted": True})

            async def disconnect(self):
                return None

        class DummyPlugin:
            def __init__(self, runner):
                self.runner = runner

            def _set_context(self, ctx):
                self.ctx = ctx

            def get_components(self):
                return [{"name": "handler", "type": "command", "metadata": {}}]

            async def on_load(self):
                result = await self.ctx.call_capability("send.text", text="hello", stream_id="stream-1")
                assert result is True
                self.runner._shutting_down = True

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        runner._rpc_client = DummyRPCClient()

        plugin = DummyPlugin(runner)
        meta = SimpleNamespace(
            plugin_id="demo_plugin",
            plugin_dir="/tmp/demo_plugin",
            instance=plugin,
            version="1.0.0",
            capabilities_required=["send.text"],
            dependencies=[],
            manifest=SimpleNamespace(plugin_dependencies=[], llm_provider_client_types=[]),
            component_handlers={},
            llm_provider_handlers={},
        )

        monkeypatch.setattr(runner, "_install_log_handler", lambda: None)
        monkeypatch.setattr(runner, "_uninstall_log_handler", lambda: asyncio.sleep(0))
        monkeypatch.setattr(runner._loader, "discover_and_load", lambda plugin_dirs, **kwargs: [meta])

        await runner.run()

        methods = [call["method"] for call in runner._rpc_client.calls]
        assert methods == ["plugin.bootstrap", "plugin.register_components", "cap.call", "runner.ready"]

    @pytest.mark.asyncio
    async def test_runner_batch_reload_merges_overlapping_reverse_dependents(self, monkeypatch):
        """批量重载应只对重叠依赖闭包执行一次 unload/load。"""
        from src.plugin_runtime.runner.runner_main import PluginRunner

        runner = PluginRunner(host_address="dummy", session_token="token", plugin_dirs=[])
        plugin_a_id = "test.plugin-a"
        plugin_b_id = "test.plugin-b"
        plugin_c_id = "test.plugin-c"

        def build_meta(plugin_id: str, dependencies: list[str]) -> SimpleNamespace:
            return SimpleNamespace(
                plugin_id=plugin_id,
                dependencies=dependencies,
                plugin_dir=f"/tmp/{plugin_id}",
                version="1.0.0",
                instance=SimpleNamespace(),
            )

        loaded_metas = {
            plugin_a_id: build_meta(plugin_a_id, []),
            plugin_b_id: build_meta(plugin_b_id, [plugin_a_id]),
            plugin_c_id: build_meta(plugin_c_id, [plugin_b_id]),
        }
        reloaded_metas = {
            plugin_id: build_meta(plugin_id, list(meta.dependencies))
            for plugin_id, meta in loaded_metas.items()
        }
        candidates = {
            plugin_a_id: (
                "dir_plugin_a",
                build_test_manifest_model(plugin_a_id),
                "plugin_a/plugin.py",
            ),
            plugin_b_id: (
                "dir_plugin_b",
                build_test_manifest_model(
                    plugin_b_id,
                    dependencies=[{"type": "plugin", "id": plugin_a_id, "version_spec": ">=1.0.0,<2.0.0"}],
                ),
                "plugin_b/plugin.py",
            ),
            plugin_c_id: (
                "dir_plugin_c",
                build_test_manifest_model(
                    plugin_c_id,
                    dependencies=[{"type": "plugin", "id": plugin_b_id, "version_spec": ">=1.0.0,<2.0.0"}],
                ),
                "plugin_c/plugin.py",
            ),
        }
        unloaded_plugins: list[str] = []
        activated_plugins: list[str] = []

        monkeypatch.setattr(runner._loader, "discover_candidates", lambda plugin_dirs: (candidates, {}))
        monkeypatch.setattr(runner._loader, "list_plugins", lambda: sorted(loaded_metas.keys()))
        monkeypatch.setattr(runner._loader, "get_plugin", lambda plugin_id: loaded_metas.get(plugin_id))
        monkeypatch.setattr(
            runner._loader,
            "remove_loaded_plugin",
            lambda plugin_id: loaded_metas.pop(plugin_id, None),
        )
        monkeypatch.setattr(runner._loader, "purge_plugin_modules", lambda plugin_id, plugin_dir: [])
        monkeypatch.setattr(
            runner._loader,
            "resolve_dependencies",
            lambda reload_candidates, extra_available=None: (sorted(reload_candidates.keys()), {}),
        )
        monkeypatch.setattr(
            runner._loader,
            "load_candidate",
            lambda plugin_id, candidate: reloaded_metas[plugin_id],
        )

        async def fake_unload_plugin(meta, reason, purge_modules=False):
            del reason, purge_modules
            unloaded_plugins.append(meta.plugin_id)
            loaded_metas.pop(meta.plugin_id, None)

        async def fake_activate_plugin(meta):
            activated_plugins.append(meta.plugin_id)
            loaded_metas[meta.plugin_id] = meta
            return True

        monkeypatch.setattr(runner, "_unload_plugin", fake_unload_plugin)
        monkeypatch.setattr(runner, "_activate_plugin", fake_activate_plugin)

        result = await runner._reload_plugins_by_ids([plugin_a_id, plugin_b_id], reason="manual")

        assert result.success is True
        assert result.requested_plugin_ids == [plugin_a_id, plugin_b_id]
        assert unloaded_plugins == [plugin_c_id, plugin_b_id, plugin_a_id]
        assert activated_plugins == [plugin_a_id, plugin_b_id, plugin_c_id]
        assert result.reloaded_plugins == [plugin_a_id, plugin_b_id, plugin_c_id]


class TestPluginSdkUsage:
    """验证仓库内插件按新 SDK 归一化返回值工作。"""

    def test_runner_skips_signal_handler_registration_on_windows(self, monkeypatch):
        """Windows 下不应尝试注册 add_signal_handler。"""
        from src.plugin_runtime.runner import runner_main

        registered_signals = []

        class DummyLoop:
            def add_signal_handler(self, sig, callback):
                registered_signals.append((sig, callback))

        monkeypatch.setattr(runner_main.sys, "platform", "win32")

        runner_main._install_shutdown_signal_handlers(lambda: None, DummyLoop())

        assert not registered_signals

    @pytest.mark.asyncio
    async def test_builtin_emoji_plugin_handles_normalized_results(self):
        from maibot_sdk.context import PluginContext
        from src.plugins.built_in.emoji_plugin.plugin import EmojiPlugin

        async def fake_rpc_call(method: str, plugin_id: str = "", payload: dict | None = None):
            assert method == "cap.request"
            assert payload is not None
            capability = payload["capability"]
            return {
                "emoji.get_random": {
                    "success": True,
                    "emojis": [{"base64": "img-1", "emotion": "happy"}],
                },
                "message.get_recent": {"success": True, "messages": [{"id": 1}]},
                "message.build_readable": {"success": True, "text": "最近消息"},
                "llm.generate": {"success": True, "response": "happy", "reasoning": "", "model_name": "m"},
                "send.emoji": {"success": True},
            }[capability]

        plugin = EmojiPlugin()
        plugin._set_context(PluginContext(plugin_id="emoji", rpc_call=fake_rpc_call))

        success, message = await plugin.handle_emoji(stream_id="stream-1", reasoning="测试", chat_id="chat-1")

        assert success is True
        assert "成功发送表情包" in message

    @pytest.mark.asyncio
    async def test_tts_plugin_uses_send_custom_bool_result(self):
        from maibot_sdk.context import PluginContext
        from src.plugins.built_in.tts_plugin.plugin import TTSPlugin

        async def fake_rpc_call(method: str, plugin_id: str = "", payload: dict | None = None):
            assert method == "cap.request"
            assert payload is not None
            assert payload["capability"] == "send.custom"
            return {"success": True}

        plugin = TTSPlugin()
        plugin._set_context(PluginContext(plugin_id="tts", rpc_call=fake_rpc_call))

        success, message = await plugin.handle_tts_action(
            stream_id="stream-1",
            action_data={"voice_text": "你好！！！"},
        )

        assert success is True
        assert message == "TTS动作执行成功"

    @pytest.mark.asyncio
    async def test_hello_world_plugin_handles_random_emoji_list(self):
        from maibot_sdk.context import PluginContext
        from plugins.hello_world_plugin.plugin import HelloWorldPlugin

        async def fake_rpc_call(method: str, plugin_id: str = "", payload: dict | None = None):
            assert method == "cap.request"
            assert payload is not None
            capability = payload["capability"]
            return {
                "emoji.get_random": {"success": True, "emojis": [{"base64": "img-1"}, {"base64": "img-2"}]},
                "send.forward": {"success": True},
            }[capability]

        plugin = HelloWorldPlugin()
        plugin._set_context(PluginContext(plugin_id="hello", rpc_call=fake_rpc_call))

        success, message, should_continue = await plugin.handle_random_emojis(stream_id="stream-1")

        assert success is True
        assert message == "已发送随机表情包"
        assert should_continue is True


# ─── 端到端集成测试 ────────────────────────────────────────


class TestE2E:
    """端到端集成测试（Host + Runner 通信）"""

    @pytest.mark.asyncio
    async def test_handshake(self):
        """Host-Runner 握手流程测试"""
        from src.plugin_runtime.protocol.codec import MsgPackCodec
        from src.plugin_runtime.protocol.envelope import Envelope, HelloPayload, HelloResponsePayload, MessageType
        from src.plugin_runtime.transport.factory import create_transport_client, create_transport_server

        import secrets

        session_token = secrets.token_hex(16)
        codec = MsgPackCodec()
        handshake_done = asyncio.Event()
        server_result = {}

        async def server_handler(conn):
            # 接收握手
            data = await conn.recv_frame()
            env = codec.decode_envelope(data)
            assert env.method == "runner.hello"

            hello = HelloPayload.model_validate(env.payload)
            assert hello.session_token == session_token

            # 发送响应
            resp_payload = HelloResponsePayload(
                accepted=True,
                host_version="1.0",
                assigned_generation=1,
            )
            resp = env.make_response(payload=resp_payload.model_dump())
            await conn.send_frame(codec.encode_envelope(resp))

            server_result["runner_id"] = hello.runner_id
            handshake_done.set()

            # 保持连接一会儿
            await asyncio.sleep(1.0)

        server = create_transport_server()
        await server.start(server_handler)

        # 客户端握手
        client = create_transport_client(server.get_address())
        conn = await client.connect()

        hello = HelloPayload(
            runner_id="test-runner",
            sdk_version="1.0.0",
            session_token=session_token,
        )
        env = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="runner.hello",
            payload=hello.model_dump(),
        )
        await conn.send_frame(codec.encode_envelope(env))

        resp_data = await conn.recv_frame()
        resp = codec.decode_envelope(resp_data)
        resp_payload = HelloResponsePayload.model_validate(resp.payload)

        assert resp_payload.accepted
        assert resp_payload.assigned_generation == 1

        await asyncio.wait_for(handshake_done.wait(), timeout=5.0)
        assert server_result["runner_id"] == "test-runner"

        await conn.close()
        await server.stop()


# ─── Manifest 校验测试 ─────────────────────────────────────


class TestManifestValidator:
    """Manifest 校验器测试"""

    def test_valid_manifest(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest("test.valid-plugin", capabilities=["send.text"])
        assert validator.validate(manifest) is True
        assert len(validator.errors) == 0
        assert validator.warnings == []

    def test_manifest_id_allows_uppercase_and_underscore(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest("XXXxx7258.google_search_plugin", capabilities=["send.text"])
        assert validator.validate(manifest) is True
        assert validator.errors == []

    def test_missing_required_fields(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = {"manifest_version": 2}
        assert validator.validate(manifest) is False
        assert len(validator.errors) >= 6
        assert any("缺少必需字段" in error for error in validator.errors)

    def test_unsupported_manifest_version(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest("test.invalid-version")
        manifest["manifest_version"] = 999
        assert validator.validate(manifest) is False
        assert any("manifest_version" in e for e in validator.errors)

    def test_host_version_compatibility(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="0.8.5", sdk_version="2.0.1")
        manifest = build_test_manifest(
            "test.host-check",
            host_min_version="0.9.0",
            host_max_version="1.0.0",
        )
        assert validator.validate(manifest) is False
        assert any("Host 版本不兼容" in e for e in validator.errors)

    def test_sdk_version_compatibility(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="1.9.9")
        manifest = build_test_manifest("test.sdk-check")
        assert validator.validate(manifest) is False
        assert any("SDK 版本不兼容" in e for e in validator.errors)

    def test_extra_fields_are_rejected(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest("test.extra-field")
        manifest["unexpected"] = True

        assert validator.validate(manifest) is False
        assert any("存在未声明字段" in error for error in validator.errors)

    def test_python_package_conflict_rejects_manifest(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest(
            "test.numpy-conflict",
            dependencies=[
                {
                    "type": "python_package",
                    "name": "numpy",
                    "version_spec": ">=999.0.0",
                }
            ],
        )

        assert validator.validate(manifest) is False
        assert any("Python 包依赖冲突" in error for error in validator.errors)

    def test_llm_provider_manifest_declaration(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest(
            "test.llm-provider",
            llm_providers=[
                {
                    "client_type": "example.provider",
                    "name": "Example Provider",
                    "description": "测试 Provider",
                    "version": "1.0.0",
                }
            ],
        )

        parsed_manifest = validator.parse_manifest(manifest)

        assert parsed_manifest is not None
        assert parsed_manifest.llm_provider_client_types == ["example.provider"]

    def test_duplicate_llm_provider_manifest_declaration_is_rejected(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="1.0.0", sdk_version="2.0.1")
        manifest = build_test_manifest(
            "test.llm-provider-duplicate",
            llm_providers=[
                {"client_type": "example.provider"},
                {"client_type": "example.provider"},
            ],
        )

        assert validator.validate(manifest) is False
        assert any("重复的 LLM Provider" in error for error in validator.errors)


def test_llm_provider_conflict_blocks_all_conflicting_plugins(tmp_path: Path):
    from src.plugin_runtime.integration import PluginRuntimeManager

    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    for plugin_id in ["test.provider-alpha", "test.provider-beta"]:
        plugin_dir = plugin_root / plugin_id
        plugin_dir.mkdir()
        manifest = build_test_manifest(
            plugin_id,
            llm_providers=[{"client_type": "example.provider"}],
        )
        (plugin_dir / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (plugin_dir / "plugin.py").write_text("def create_plugin():\n    return None\n", encoding="utf-8")

    blocked_reasons = PluginRuntimeManager._discover_llm_provider_conflicts([plugin_root])

    assert set(blocked_reasons) == {"test.provider-alpha", "test.provider-beta"}
    assert all("example.provider" in reason for reason in blocked_reasons.values())


class TestVersionComparator:
    """版本号比较器测试"""

    def test_normalize(self):
        from src.plugin_runtime.runner.manifest_validator import VersionComparator

        assert VersionComparator.normalize_version("0.8.0-snapshot.1") == "0.8.0"
        assert VersionComparator.normalize_version("1.2") == "1.2.0"
        assert VersionComparator.normalize_version("1.0.0rc16") == "1.0.0"
        assert VersionComparator.normalize_version("1.0.0-pre.16") == "1.0.0"
        assert VersionComparator.normalize_version("") == "0.0.0"

    def test_compare(self):
        from src.plugin_runtime.runner.manifest_validator import VersionComparator

        assert VersionComparator.compare("0.8.0", "0.8.0") == 0
        assert VersionComparator.compare("0.8.0", "0.9.0") == -1
        assert VersionComparator.compare("1.0.0", "0.9.0") == 1

    def test_is_in_range(self):
        from src.plugin_runtime.runner.manifest_validator import VersionComparator

        ok, _ = VersionComparator.is_in_range("0.8.5", "0.8.0", "0.9.0")
        assert ok
        ok, _ = VersionComparator.is_in_range("0.7.0", "0.8.0", "0.9.0")
        assert not ok
        ok, _ = VersionComparator.is_in_range("1.0.0", "0.8.0", "0.9.0")
        assert not ok


# ─── 依赖解析测试 ──────────────────────────────────────────


class TestDependencyResolution:
    """插件依赖解析测试"""

    def test_topological_sort(self):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        loader = PluginLoader()
        candidates = {
            "test.core": (
                "dir_core",
                build_test_manifest_model("test.core"),
                "plugin.py",
            ),
            "test.auth": (
                "dir_auth",
                build_test_manifest_model(
                    "test.auth",
                    dependencies=[
                        {"type": "plugin", "id": "test.core", "version_spec": ">=1.0.0,<2.0.0"},
                    ],
                ),
                "plugin.py",
            ),
            "test.api": (
                "dir_api",
                build_test_manifest_model(
                    "test.api",
                    dependencies=[
                        {"type": "plugin", "id": "test.core", "version_spec": ">=1.0.0,<2.0.0"},
                        {"type": "plugin", "id": "test.auth", "version_spec": ">=1.0.0,<2.0.0"},
                    ],
                ),
                "plugin.py",
            ),
        }

        order, failed = loader._resolve_dependencies(candidates)
        assert len(failed) == 0
        assert order.index("test.core") < order.index("test.auth")
        assert order.index("test.auth") < order.index("test.api")

    def test_missing_dependency(self):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        loader = PluginLoader()
        candidates = {
            "test.plugin-a": (
                "dir_a",
                build_test_manifest_model(
                    "test.plugin-a",
                    dependencies=[
                        {"type": "plugin", "id": "test.nonexistent", "version_spec": ">=1.0.0,<2.0.0"},
                    ],
                ),
                "plugin.py",
            ),
        }

        order, failed = loader._resolve_dependencies(candidates)
        assert "test.plugin-a" in failed
        assert "依赖未满足" in failed["test.plugin-a"]

    def test_circular_dependency(self):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        loader = PluginLoader()
        candidates = {
            "test.a": (
                "dir_a",
                build_test_manifest_model(
                    "test.a",
                    dependencies=[
                        {"type": "plugin", "id": "test.b", "version_spec": ">=1.0.0,<2.0.0"},
                    ],
                ),
                "p.py",
            ),
            "test.b": (
                "dir_b",
                build_test_manifest_model(
                    "test.b",
                    dependencies=[
                        {"type": "plugin", "id": "test.a", "version_spec": ">=1.0.0,<2.0.0"},
                    ],
                ),
                "p.py",
            ),
        }

        order, failed = loader._resolve_dependencies(candidates)
        assert len(failed) >= 1  # 至少一个循环插件被标记

    def test_loader_supports_package_imports_inside_create_plugin(self, tmp_path):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        plugin_root = tmp_path / "plugins"
        plugin_root.mkdir()
        plugin_dir = plugin_root / "grok_search_plugin"
        plugin_dir.mkdir()

        (plugin_dir / "_manifest.json").write_text(
            json.dumps(
                build_test_manifest(
                    "test.grok-search-plugin",
                    name="grok_search_plugin",
                    description="demo",
                )
            ),
            encoding="utf-8",
        )
        (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (plugin_dir / "services.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        (plugin_dir / "plugin.py").write_text(
            "class DemoPlugin:\n"
            "    pass\n\n"
            "def create_plugin():\n"
            "    from grok_search_plugin.services import answer\n"
            "    plugin = DemoPlugin()\n"
            "    plugin.answer = answer\n"
            "    return plugin\n",
            encoding="utf-8",
        )

        loader = PluginLoader()
        loaded = loader.discover_and_load([str(plugin_root)])

        assert [meta.plugin_id for meta in loaded] == ["test.grok-search-plugin"]
        assert loader.failed_plugins == {}
        assert loaded[0].instance.answer() == 42

    def test_loader_requires_sdk_plugin_to_override_on_config_update(self, tmp_path):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        plugin_root = tmp_path / "plugins"
        plugin_root.mkdir()
        plugin_dir = plugin_root / "demo_plugin"
        plugin_dir.mkdir()

        (plugin_dir / "_manifest.json").write_text(
            json.dumps(
                build_test_manifest(
                    "test.demo-plugin",
                    name="demo_plugin",
                    description="demo",
                )
            ),
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(
            "from maibot_sdk import MaiBotPlugin\n\n"
            "class DemoPlugin(MaiBotPlugin):\n"
            "    async def on_load(self):\n"
            "        pass\n\n"
            "    async def on_unload(self):\n"
            "        pass\n\n"
            "def create_plugin():\n"
            "    return DemoPlugin()\n",
            encoding="utf-8",
        )

        loader = PluginLoader()
        loaded = loader.discover_and_load([str(plugin_root)])

        assert loaded == []
        assert "test.demo-plugin" in loader.failed_plugins
        assert "on_config_update" in loader.failed_plugins["test.demo-plugin"]

    def test_loader_requires_sdk_plugin_to_override_on_load(self, tmp_path):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        plugin_root = tmp_path / "plugins"
        plugin_root.mkdir()
        plugin_dir = plugin_root / "demo_plugin"
        plugin_dir.mkdir()

        (plugin_dir / "_manifest.json").write_text(
            json.dumps(
                build_test_manifest(
                    "test.demo-plugin",
                    name="demo_plugin",
                    description="demo",
                )
            ),
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(
            "from maibot_sdk import MaiBotPlugin\n\n"
            "class DemoPlugin(MaiBotPlugin):\n"
            "    async def on_unload(self):\n"
            "        pass\n\n"
            "    async def on_config_update(self, scope, config_data, version):\n"
            "        pass\n\n"
            "def create_plugin():\n"
            "    return DemoPlugin()\n",
            encoding="utf-8",
        )

        loader = PluginLoader()
        loaded = loader.discover_and_load([str(plugin_root)])

        assert loaded == []
        assert "test.demo-plugin" in loader.failed_plugins
        assert "on_load" in loader.failed_plugins["test.demo-plugin"]

    def test_loader_requires_sdk_plugin_to_override_on_unload(self, tmp_path):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        plugin_root = tmp_path / "plugins"
        plugin_root.mkdir()
        plugin_dir = plugin_root / "demo_plugin"
        plugin_dir.mkdir()

        (plugin_dir / "_manifest.json").write_text(
            json.dumps(
                build_test_manifest(
                    "test.demo-plugin",
                    name="demo_plugin",
                    description="demo",
                )
            ),
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(
            "from maibot_sdk import MaiBotPlugin\n\n"
            "class DemoPlugin(MaiBotPlugin):\n"
            "    async def on_load(self):\n"
            "        pass\n\n"
            "    async def on_config_update(self, scope, config_data, version):\n"
            "        pass\n\n"
            "def create_plugin():\n"
            "    return DemoPlugin()\n",
            encoding="utf-8",
        )

        loader = PluginLoader()
        loaded = loader.discover_and_load([str(plugin_root)])

        assert loaded == []
        assert "test.demo-plugin" in loader.failed_plugins
        assert "on_unload" in loader.failed_plugins["test.demo-plugin"]

    @pytest.mark.asyncio
    async def test_async_main_removes_sensitive_runtime_env_vars(self, monkeypatch):
        from src.plugin_runtime.runner import runner_main

        captured = {}
        original_path = list(sys.path)

        class FakeRunner:
            def __init__(
                self,
                host_address: str,
                session_token: str,
                plugin_dirs: list[str],
                external_available_plugins: dict[str, str] | None = None,
            ) -> None:
                captured["host_address"] = host_address
                captured["session_token"] = session_token
                captured["plugin_dirs"] = plugin_dirs
                captured["external_available_plugins"] = external_available_plugins or {}

            async def run(self) -> None:
                assert os.environ.get(runner_main.ENV_IPC_ADDRESS) is None
                assert os.environ.get(runner_main.ENV_SESSION_TOKEN) is None

        monkeypatch.setenv(runner_main.ENV_IPC_ADDRESS, "tcp://127.0.0.1:9999")
        monkeypatch.setenv(runner_main.ENV_SESSION_TOKEN, "secret-token")
        monkeypatch.setenv(runner_main.ENV_PLUGIN_DIRS, "/tmp/plugins")
        monkeypatch.setenv(runner_main.ENV_EXTERNAL_PLUGIN_IDS, '{"demo.plugin":"1.0.0"}')
        monkeypatch.setattr(runner_main, "_install_shutdown_signal_handlers", lambda callback: None)
        monkeypatch.setattr(runner_main, "PluginRunner", FakeRunner)

        await runner_main._async_main()

        assert captured["host_address"] == "tcp://127.0.0.1:9999"
        assert captured["session_token"] == "secret-token"
        assert captured["plugin_dirs"] == ["/tmp/plugins"]
        assert captured["external_available_plugins"] == {"demo.plugin": "1.0.0"}
        assert sys.path == original_path


# ─── Host-side ComponentRegistry 测试 ──────────────────────


class TestComponentRegistry:
    """Host-side 组件注册表测试"""

    def test_register_and_query(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component(
            "greet",
            "action",
            "plugin_a",
            {
                "description": "打招呼",
                "activation_type": "keyword",
                "activation_keywords": ["hi"],
            },
        )
        reg.register_component(
            "help",
            "command",
            "plugin_a",
            {
                "command_pattern": r"^/help",
            },
        )
        reg.register_component(
            "search",
            "tool",
            "plugin_b",
            {
                "description": "搜索",
            },
        )

        stats = reg.get_stats()
        assert stats["total"] == 3
        assert stats["action"] == 1
        assert stats["command"] == 1
        assert stats["tool"] == 1

    def test_register_command_with_invalid_regex_only_warns(self, monkeypatch):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        warnings: list[str] = []
        monkeypatch.setattr(
            "src.plugin_runtime.host.component_registry.logger.warning",
            lambda message: warnings.append(str(message)),
        )

        success = reg.register_component(
            "broken",
            "command",
            "plugin_a",
            {
                "command_pattern": "[",
            },
        )

        assert success is True
        assert reg.get_component("plugin_a.broken") is not None
        assert warnings
        assert "plugin_a.broken" in warnings[0]

    def test_register_hook_handler_rejects_unknown_hook(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistrationError, ComponentRegistry
        from src.plugin_runtime.host.hook_spec_registry import HookSpecRegistry

        reg = ComponentRegistry(hook_spec_registry=HookSpecRegistry())

        with pytest.raises(ComponentRegistrationError, match="未注册的 Hook"):
            reg.register_component(
                "broken_hook",
                "hook_handler",
                "plugin_a",
                {
                    "hook": "chat.receive.unknown",
                    "mode": "blocking",
                },
            )

    def test_register_plugin_components_is_atomic_when_hook_invalid(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistrationError, ComponentRegistry
        from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry

        hook_spec_registry = HookSpecRegistry()
        hook_spec_registry.register_hook_spec(HookSpec(name="chat.receive.before_process"))
        reg = ComponentRegistry(hook_spec_registry=hook_spec_registry)
        reg.register_plugin_components(
            "plugin_a",
            [
                {"name": "cmd_old", "component_type": "command", "metadata": {"command_pattern": r"^/old"}},
            ],
        )

        with pytest.raises(ComponentRegistrationError, match="未注册的 Hook"):
            reg.register_plugin_components(
                "plugin_a",
                [
                    {
                        "name": "hook_ok",
                        "component_type": "hook_handler",
                        "metadata": {"hook": "chat.receive.before_process", "mode": "blocking"},
                    },
                    {
                        "name": "hook_bad",
                        "component_type": "hook_handler",
                        "metadata": {"hook": "chat.receive.missing", "mode": "blocking"},
                    },
                ],
            )

        assert reg.get_component("plugin_a.cmd_old") is not None
        assert reg.get_component("plugin_a.hook_ok") is None

    def test_query_by_type(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component("a1", "action", "p1", {})
        reg.register_component("a2", "action", "p2", {})

        actions = reg.get_components_by_type("action")
        assert len(actions) == 2

    def test_find_command_by_text(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component(
            "help",
            "command",
            "p1",
            {
                "command_pattern": r"^/help",
            },
        )
        reg.register_component(
            "echo",
            "command",
            "p1",
            {
                "command_pattern": r"^/echo\s",
            },
        )

        match = reg.find_command_by_text("/help me")
        assert match is not None
        comp, groups = match
        assert comp.name == "help"

        match = reg.find_command_by_text("/echo hello")
        assert match is not None
        comp, groups = match
        assert comp.name == "echo"

        match = reg.find_command_by_text("no match")
        assert match is None

    def test_enable_disable(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component("a1", "action", "p1", {})
        reg.set_component_enabled("p1.a1", False)

        actions = reg.get_components_by_type("action", enabled_only=True)
        assert len(actions) == 0

        actions = reg.get_components_by_type("action", enabled_only=False)
        assert len(actions) == 1

    def test_remove_by_plugin(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component("a1", "action", "p1", {})
        reg.register_component("c1", "command", "p1", {})
        reg.register_component("a2", "action", "p2", {})

        removed = reg.remove_components_by_plugin("p1")
        assert removed == 2
        assert reg.get_stats()["total"] == 1

    def test_reregister_same_plugin_replaces_component_set(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_plugin_components(
            "p1",
            [
                {"name": "a1", "component_type": "action", "metadata": {}},
                {"name": "a2", "component_type": "action", "metadata": {}},
            ],
        )
        reg.remove_components_by_plugin("p1")
        reg.register_plugin_components(
            "p1",
            [
                {"name": "a1", "component_type": "action", "metadata": {}},
            ],
        )

        assert reg.get_component("p1.a1") is not None
        assert reg.get_component("p1.a2") is None

    def test_event_handlers_sorted_by_weight(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component(
            "h_low",
            "event_handler",
            "p1",
            {
                "event_type": "on_message",
                "weight": 10,
            },
        )
        reg.register_component(
            "h_high",
            "event_handler",
            "p2",
            {
                "event_type": "on_message",
                "weight": 100,
            },
        )

        handlers = reg.get_event_handlers("on_message")
        assert handlers[0].name == "h_high"
        assert handlers[1].name == "h_low"

    def test_tools_for_llm(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component(
            "search",
            "tool",
            "p1",
            {
                "description": "搜索工具",
                "parameters_raw": {"query": {"type": "string"}},
            },
        )

        tools = reg.get_tools_for_llm()
        assert len(tools) == 1
        assert tools[0]["name"] == "p1.search"
        assert tools[0]["parameters"]["query"]["type"] == "string"


# ─── EventDispatcher 测试 ─────────────────────────────────


class TestEventDispatcher:
    """Host-side 事件分发器测试"""

    @pytest.mark.asyncio
    async def test_dispatch_non_blocking(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.event_dispatcher import EventDispatcher

        reg = ComponentRegistry()
        reg.register_component(
            "h1",
            "event_handler",
            "p1",
            {
                "event_type": "on_start",
                "weight": 0,
                "intercept_message": False,
            },
        )

        dispatcher = EventDispatcher(reg)
        call_log = []

        async def mock_invoke(plugin_id, comp_name, args):
            call_log.append((plugin_id, comp_name))
            return {"success": True, "continue_processing": True}

        should_continue, modified = await dispatcher.dispatch_event("on_start", mock_invoke)
        assert should_continue
        # 非阻塞分发是异步的，等一下让 task 完成
        await asyncio.sleep(0.1)
        assert len(call_log) == 1
        assert call_log[0] == ("p1", "h1")

    @pytest.mark.asyncio
    async def test_dispatch_intercepting(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.event_dispatcher import EventDispatcher

        reg = ComponentRegistry()
        reg.register_component(
            "filter",
            "event_handler",
            "p1",
            {
                "event_type": "on_message_pre_process",
                "weight": 100,
                "intercept_message": True,
            },
        )

        dispatcher = EventDispatcher(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            return {
                "success": True,
                "continue_processing": False,
                "modified_message": {"plain_text": "filtered"},
            }

        should_continue, modified = await dispatcher.dispatch_event(
            "on_message_pre_process", mock_invoke, message={"plain_text": "hello"}
        )
        assert not should_continue
        assert modified is not None
        assert modified["plain_text"] == "filtered"


class TestEventBus:
    """核心事件总线与 IPC 桥接测试"""

    @pytest.mark.asyncio
    async def test_bridge_preserves_modified_message(self, monkeypatch):
        import types

        fake_message_data_model = types.ModuleType("src.common.data_models.message_data_model")
        fake_message_data_model.ReplyContentType = object
        fake_message_data_model.ReplyContent = object
        fake_message_data_model.ForwardNode = object
        fake_message_data_model.ReplySetModel = object
        monkeypatch.setitem(sys.modules, "src.common.data_models.message_data_model", fake_message_data_model)

        from src.core.event_bus import EventBus
        from src.core.types import EventType, MaiMessages
        from src.plugin_runtime import integration as integration_module

        bus = EventBus()

        async def noop_handler(message):
            return True, message

        bus.subscribe(EventType.ON_MESSAGE, noop_handler, name="noop", intercept=True)

        class FakeManager:
            is_running = True

            async def bridge_event(self, event_type_value, message_dict=None, extra_args=None):
                assert event_type_value == EventType.ON_MESSAGE.value
                return True, {"plain_text": "modified by ipc"}

        monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: FakeManager())

        original = MaiMessages(plain_text="original")
        continue_flag, modified = await bus.emit(EventType.ON_MESSAGE, original)

        assert continue_flag is True
        assert modified is not None
        assert modified.plain_text == "modified by ipc"
        assert original.plain_text == "original"


# ─── MaiMessages 测试 ─────────────────────────────────────


class TestMaiMessages:
    """统一消息模型测试"""

    def test_create_and_serialize(self):
        from maibot_sdk.messages import MaiMessages, MessageSegment

        msg = MaiMessages(
            message_segments=[MessageSegment(type="text", data={"text": "hello"})],
            plain_text="hello",
            stream_id="stream_1",
        )

        d = msg.to_rpc_dict()
        assert d["plain_text"] == "hello"
        assert len(d["message_segments"]) == 1

        msg2 = MaiMessages.from_rpc_dict(d)
        assert msg2.plain_text == "hello"

    def test_deepcopy(self):
        from maibot_sdk.messages import MaiMessages

        msg = MaiMessages(plain_text="original")
        msg2 = msg.deepcopy()
        msg2.plain_text = "modified"
        assert msg.plain_text == "original"

    def test_modify_flags(self):
        from maibot_sdk.messages import MaiMessages
        from maibot_sdk.types import ModifyFlag

        msg = MaiMessages(plain_text="hello")
        assert msg.can_modify(ModifyFlag.CAN_MODIFY_PROMPT)

        msg.set_modify_flag(ModifyFlag.CAN_MODIFY_PROMPT, False)
        assert not msg.modify_prompt("new prompt")
        assert msg.llm_prompt is None

        assert msg.modify_response("new response")
        assert msg.llm_response_content == "new response"


class _FakeHookSupervisor:
    """用于 Hook 分发测试的简化 Supervisor。"""

    def __init__(
        self,
        group_name: str,
        component_registry: Any,
        handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]],
        call_log: List[tuple[str, str]],
    ) -> None:
        """初始化测试用 Supervisor。

        Args:
            group_name: 运行时分组名称。
            component_registry: 组件注册表实例。
            handlers: 处理器映射，键为 `plugin_id.component_name`。
            call_log: 记录调用顺序的列表。
        """

        self._group_name = group_name
        self.component_registry = component_registry
        self._handlers = handlers
        self._call_log = call_log

    @property
    def group_name(self) -> str:
        """返回当前测试 Supervisor 的分组名称。"""

        return self._group_name

    async def invoke_plugin(
        self,
        method: str,
        plugin_id: str,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        timeout_ms: int = 30000,
    ) -> SimpleNamespace:
        """模拟调用插件组件。

        Args:
            method: RPC 方法名。
            plugin_id: 目标插件 ID。
            component_name: 目标组件名称。
            args: 调用参数。
            timeout_ms: 超时配置，测试中仅用于保持接口一致。

        Returns:
            SimpleNamespace: 仅包含 `payload` 字段的简化响应对象。
        """

        del method
        del timeout_ms

        full_name = f"{plugin_id}.{component_name}"
        handler = self._handlers[full_name]
        self._call_log.append((plugin_id, component_name))
        result = handler(dict(args or {}))
        if asyncio.iscoroutine(result):
            result = await result
        return SimpleNamespace(payload=result)


# ─── HookDispatcher 测试 ────────────────────────────────


class TestHookDispatcher:
    """命名 Hook 分发器测试。"""

    @staticmethod
    def _import_dispatcher_modules(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
        """导入 Hook 分发相关模块，并屏蔽配置初始化触发的退出。

        Args:
            monkeypatch: pytest 的 monkeypatch 工具。

        Returns:
            tuple[Any, Any]: `ComponentRegistry` 与 `HookDispatcher` 类型。
        """

        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.hook_dispatcher import HookDispatcher

        return ComponentRegistry, HookDispatcher

    @pytest.mark.asyncio
    async def test_empty_hook_returns_original_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未注册处理器时应直接返回原始参数。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        dispatcher = HookDispatcher()
        supervisor = _FakeHookSupervisor("builtin", ComponentRegistry(), {}, [])

        result = await dispatcher.invoke_hook("heart_fc.cycle_start", [supervisor], session_id="s-1")

        assert result.hook_name == "heart_fc.cycle_start"
        assert result.kwargs == {"session_id": "s-1"}
        assert result.aborted is False

    @pytest.mark.asyncio
    async def test_blocking_hook_modifies_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """blocking 处理器可以修改参数。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        registry = ComponentRegistry()
        registry.register_component(
            "upper",
            "HOOK_HANDLER",
            "p1",
            {
                "hook": "heart_fc.cycle_start",
                "mode": "blocking",
                "order": "normal",
            },
        )
        dispatcher = HookDispatcher()
        supervisor = _FakeHookSupervisor(
            "builtin",
            registry,
            {
                "p1.upper": lambda args: {
                    "success": True,
                    "action": "continue",
                    "modified_kwargs": {
                        "session_id": args["session_id"],
                        "text": str(args["text"]).upper(),
                    },
                }
            },
            [],
        )

        result = await dispatcher.invoke_hook("heart_fc.cycle_start", [supervisor], session_id="s-1", text="hello")

        assert result.kwargs["session_id"] == "s-1"
        assert result.kwargs["text"] == "HELLO"
        assert result.aborted is False

    @pytest.mark.asyncio
    async def test_abort_stops_following_blocking_handlers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """blocking 处理器的 abort 应阻止后续 blocking 处理器执行。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        registry = ComponentRegistry()
        registry.register_component(
            "stopper",
            "HOOK_HANDLER",
            "p1",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "early"},
        )
        registry.register_component(
            "after_stop",
            "HOOK_HANDLER",
            "p2",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "normal"},
        )
        call_log: List[tuple[str, str]] = []
        dispatcher = HookDispatcher()
        supervisor = _FakeHookSupervisor(
            "builtin",
            registry,
            {
                "p1.stopper": lambda args: {"success": True, "action": "abort"},
                "p2.after_stop": lambda args: {"success": True, "action": "continue"},
            },
            call_log,
        )

        result = await dispatcher.invoke_hook("heart_fc.cycle_start", [supervisor], cycle_id="c-1")

        assert result.aborted is True
        assert result.stopped_by == "p1.stopper"
        assert call_log == [("p1", "stopper")]

    @pytest.mark.asyncio
    async def test_observe_handler_runs_in_background_without_mutation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """observe 处理器应后台执行且不能影响主流程参数。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        registry = ComponentRegistry()
        registry.register_component(
            "observer",
            "HOOK_HANDLER",
            "p1",
            {"hook": "heart_fc.cycle_start", "mode": "observe", "order": "normal"},
        )
        started = asyncio.Event()
        release = asyncio.Event()
        call_log: List[tuple[str, str]] = []

        async def observe_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            """模拟耗时观察型处理器。"""

            started.set()
            await release.wait()
            return {
                "success": True,
                "action": "abort",
                "modified_kwargs": {"session_id": "changed"},
                "custom_result": args["session_id"],
            }

        dispatcher = HookDispatcher()
        supervisor = _FakeHookSupervisor(
            "builtin",
            registry,
            {"p1.observer": observe_handler},
            call_log,
        )

        result = await dispatcher.invoke_hook("heart_fc.cycle_start", [supervisor], session_id="s-1")

        await asyncio.sleep(0)
        assert result.aborted is False
        assert result.kwargs["session_id"] == "s-1"
        assert started.is_set()
        assert len(dispatcher._background_tasks) == 1

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert call_log == [("p1", "observer")]
        assert not dispatcher._background_tasks

    @pytest.mark.asyncio
    async def test_global_order_prefers_order_slot_then_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全局排序应先看 order，再看内置/第三方来源。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        builtin_registry = ComponentRegistry()
        third_registry = ComponentRegistry()
        builtin_registry.register_component(
            "builtin_early",
            "HOOK_HANDLER",
            "b1",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "early"},
        )
        builtin_registry.register_component(
            "builtin_normal",
            "HOOK_HANDLER",
            "b1",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "normal"},
        )
        third_registry.register_component(
            "third_early",
            "HOOK_HANDLER",
            "t1",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "early"},
        )
        third_registry.register_component(
            "third_normal",
            "HOOK_HANDLER",
            "t1",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "normal"},
        )

        call_log: List[tuple[str, str]] = []
        dispatcher = HookDispatcher()
        builtin_supervisor = _FakeHookSupervisor(
            "builtin",
            builtin_registry,
            {
                "b1.builtin_early": lambda args: {"success": True, "action": "continue"},
                "b1.builtin_normal": lambda args: {"success": True, "action": "continue"},
            },
            call_log,
        )
        third_supervisor = _FakeHookSupervisor(
            "third_party",
            third_registry,
            {
                "t1.third_early": lambda args: {"success": True, "action": "continue"},
                "t1.third_normal": lambda args: {"success": True, "action": "continue"},
            },
            call_log,
        )

        await dispatcher.invoke_hook(
            "heart_fc.cycle_start",
            [third_supervisor, builtin_supervisor],
            cycle_id="c-1",
        )

        assert call_log == [
            ("b1", "builtin_early"),
            ("t1", "third_early"),
            ("b1", "builtin_normal"),
            ("t1", "third_normal"),
        ]

    @pytest.mark.asyncio
    async def test_error_policy_abort_stops_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """error_policy=abort 时应中止本次 Hook 调用。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        registry = ComponentRegistry()
        registry.register_component(
            "failer",
            "HOOK_HANDLER",
            "p1",
            {
                "hook": "heart_fc.cycle_start",
                "mode": "blocking",
                "order": "normal",
                "error_policy": "abort",
            },
        )
        call_log: List[tuple[str, str]] = []

        async def fail_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            """抛出异常以触发 abort 策略。"""

            del args
            raise RuntimeError("boom")

        dispatcher = HookDispatcher()
        supervisor = _FakeHookSupervisor("builtin", registry, {"p1.failer": fail_handler}, call_log)

        result = await dispatcher.invoke_hook("heart_fc.cycle_start", [supervisor], session_id="s-1")

        assert result.aborted is True
        assert result.stopped_by == "p1.failer"
        assert any("boom" in error for error in result.errors)
        assert call_log == [("p1", "failer")]

    @pytest.mark.asyncio
    async def test_timeout_respects_handler_timeout_ms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """处理器超时应被记录为错误并继续。"""

        ComponentRegistry, HookDispatcher = self._import_dispatcher_modules(monkeypatch)

        registry = ComponentRegistry()
        registry.register_component(
            "slow",
            "HOOK_HANDLER",
            "p1",
            {
                "hook": "heart_fc.cycle_start",
                "mode": "blocking",
                "order": "normal",
                "timeout_ms": 10,
            },
        )
        call_log: List[tuple[str, str]] = []

        async def slow_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            """模拟超时处理器。"""

            del args
            await asyncio.sleep(0.05)
            return {"success": True, "action": "continue"}

        dispatcher = HookDispatcher()
        supervisor = _FakeHookSupervisor("builtin", registry, {"p1.slow": slow_handler}, call_log)

        result = await dispatcher.invoke_hook("heart_fc.cycle_start", [supervisor], session_id="s-1")

        assert result.aborted is False
        assert any("超时" in error for error in result.errors)
        assert call_log == [("p1", "slow")]


class TestPluginRuntimeHookEntry:
    """PluginRuntimeManager 命名 Hook 入口测试。"""

    @staticmethod
    def _import_manager_modules(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
        """导入运行时管理器相关模块，并屏蔽配置初始化触发的退出。

        Args:
            monkeypatch: pytest 的 monkeypatch 工具。

        Returns:
            tuple[Any, Any]: `ComponentRegistry` 与 `PluginRuntimeManager` 类型。
        """

        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.integration import PluginRuntimeManager

        return ComponentRegistry, PluginRuntimeManager

    @pytest.mark.asyncio
    async def test_manager_invoke_hook_dispatches_across_supervisors(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PluginRuntimeManager.invoke_hook() 应调用全局 Hook 分发器。"""

        ComponentRegistry, PluginRuntimeManager = self._import_manager_modules(monkeypatch)

        builtin_registry = ComponentRegistry()
        builtin_registry.register_component(
            "builtin_guard",
            "HOOK_HANDLER",
            "b1",
            {"hook": "heart_fc.cycle_start", "mode": "blocking", "order": "early"},
        )
        third_registry = ComponentRegistry()
        third_registry.register_component(
            "observer",
            "HOOK_HANDLER",
            "t1",
            {"hook": "heart_fc.cycle_start", "mode": "observe", "order": "normal"},
        )

        call_log: List[tuple[str, str]] = []
        manager = PluginRuntimeManager()
        manager._started = True
        manager._builtin_supervisor = _FakeHookSupervisor(
            "builtin",
            builtin_registry,
            {"b1.builtin_guard": lambda args: {"success": True, "action": "continue"}},
            call_log,
        )
        manager._third_party_supervisor = _FakeHookSupervisor(
            "third_party",
            third_registry,
            {"t1.observer": lambda args: {"success": True, "action": "continue"}},
            call_log,
        )

        result = await manager.invoke_dispatcher.invoke_hook("heart_fc.cycle_start", session_id="s-1")

        await asyncio.sleep(0)
        assert manager.invoke_dispatcher is manager.hook_dispatcher
        assert result.aborted is False
        assert result.kwargs["session_id"] == "s-1"
        assert ("b1", "builtin_guard") in call_log

    def test_manager_lists_builtin_hook_specs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PluginRuntimeManager 应暴露内置 Hook 规格清单。"""

        _ComponentRegistry, PluginRuntimeManager = self._import_manager_modules(monkeypatch)

        manager = PluginRuntimeManager()
        hook_names = {spec.name for spec in manager.list_hook_specs()}

        assert "chat.receive.before_process" in hook_names
        assert "send_service.before_send" in hook_names
        assert "maisaka.planner.after_response" in hook_names


class TestRPCServer:
    """RPC Server 代际保护测试"""

    @pytest.mark.asyncio
    async def test_reject_second_active_runner_connection(self):
        from src.plugin_runtime.host.rpc_server import RPCServer
        from src.plugin_runtime.protocol.codec import MsgPackCodec
        from src.plugin_runtime.protocol.envelope import Envelope, HelloPayload, HelloResponsePayload, MessageType

        class DummyTransport:
            async def start(self, handler):
                return None

            async def stop(self):
                return None

            def get_address(self):
                return "dummy"

        class FakeConnection:
            def __init__(self, incoming_frames: list[bytes]):
                self._incoming_frames = list(incoming_frames)
                self.sent_frames: list[bytes] = []
                self.is_closed = False

            async def recv_frame(self):
                return self._incoming_frames.pop(0)

            async def send_frame(self, data):
                self.sent_frames.append(data)

            async def close(self):
                self.is_closed = True

        codec = MsgPackCodec()
        server = RPCServer(transport=DummyTransport(), session_token="session-token")
        active_conn = SimpleNamespace(is_closed=False)
        server._connection = active_conn

        hello = HelloPayload(
            runner_id="runner-b",
            sdk_version="1.0.0",
            session_token="session-token",
        )
        envelope = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="runner.hello",
            payload=hello.model_dump(),
        )
        incoming_conn = FakeConnection([codec.encode_envelope(envelope)])

        await server._handle_connection(incoming_conn)

        assert incoming_conn.is_closed is True
        assert server._connection is active_conn
        assert server.last_handshake_rejection_reason == "已有活跃 Runner 连接，拒绝新的握手"
        assert len(incoming_conn.sent_frames) == 1

        response = codec.decode_envelope(incoming_conn.sent_frames[0])
        response_payload = HelloResponsePayload.model_validate(response.payload)
        assert response_payload.accepted is False
        assert response_payload.reason == "已有活跃 Runner 连接，拒绝新的握手"

    def test_ignore_stale_generation_response(self):
        from src.plugin_runtime.host.rpc_server import RPCServer
        from src.plugin_runtime.protocol.envelope import Envelope, MessageType

        class DummyTransport:
            async def start(self, handler):
                return None

            async def stop(self):
                return None

            def get_address(self):
                return "dummy"

        server = RPCServer(transport=DummyTransport())
        server._runner_generation = 2

        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            server._pending_requests[1] = (future, 2)

            stale_response = Envelope(
                request_id=1,
                message_type=MessageType.RESPONSE,
                method="plugin.health",
                generation=1,
                payload={"healthy": True},
            )
            server._handle_response(stale_response)

            assert not future.done()
            assert 1 in server._pending_requests
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_send_queue_backpressure_is_enforced(self):
        from src.plugin_runtime.host.rpc_server import RPCServer
        from src.plugin_runtime.protocol.errors import ErrorCode, RPCError

        class DummyTransport:
            async def start(self, handler):
                return None

            async def stop(self):
                return None

            def get_address(self):
                return "dummy"

        class BlockingConnection:
            def __init__(self):
                self.is_closed = False
                self.release = asyncio.Event()

            async def send_frame(self, data):
                await self.release.wait()

            async def close(self):
                self.is_closed = True

        server = RPCServer(transport=DummyTransport(), send_queue_size=1)
        await server.start()

        conn = BlockingConnection()
        server._connection = conn
        server._runner_generation = 1

        first_send = asyncio.create_task(server.send_event("runner.log_batch"))
        await asyncio.sleep(0)
        second_send = asyncio.create_task(server.send_event("runner.log_batch"))
        await asyncio.sleep(0)

        with pytest.raises(RPCError) as exc_info:
            await server.send_event("runner.log_batch")

        assert exc_info.value.code == ErrorCode.E_BACKPRESSURE

        conn.release.set()
        await asyncio.gather(first_send, second_send)
        await server.stop()


class TestRPCClient:
    """Runner RPCClient 后台任务生命周期测试"""

    @pytest.mark.asyncio
    async def test_background_tasks_retained_and_cancelled_on_disconnect(self):
        from src.plugin_runtime.runner.rpc_client import RPCClient

        client = RPCClient(host_address="dummy", session_token="token")
        release = asyncio.Event()

        async def pending_task():
            await release.wait()

        task = asyncio.create_task(pending_task())
        client._track_background_task(task)

        assert task in client._background_tasks

        await asyncio.sleep(0)
        assert task in client._background_tasks

        await client.disconnect()

        assert task.cancelled() is True
        assert not client._background_tasks


class TestSupervisor:
    """Supervisor 生命周期边界测试"""

    @staticmethod
    def _build_register_payload(plugin_id: str = "plugin_a", component_names=None):
        from src.plugin_runtime.protocol.envelope import ComponentDeclaration, RegisterComponentsPayload

        component_names = component_names or ["handler"]

        return RegisterComponentsPayload(
            plugin_id=plugin_id,
            plugin_version="1.0.0",
            components=[
                ComponentDeclaration(
                    name=name,
                    component_type="event_handler",
                    plugin_id=plugin_id,
                    metadata={"event_type": "on_message"},
                )
                for name in component_names
            ],
            capabilities_required=["send.text"],
        )

    @staticmethod
    def _make_process(pid: int):
        class FakeProcess:
            def __init__(self):
                self.pid = pid
                self.returncode = None
                self.stdout = None
                self.stderr = None
                self.terminated = False
                self.killed = False

            def terminate(self):
                self.terminated = True
                self.returncode = 0

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        return FakeProcess()

    @pytest.mark.asyncio
    async def test_reload_waits_for_target_generation(self, monkeypatch):
        from src.plugin_runtime.host.supervisor import PluginSupervisor
        from src.plugin_runtime.protocol.envelope import HealthPayload

        supervisor = PluginSupervisor(plugin_dirs=[])
        old_process = self._make_process(1)
        new_process = self._make_process(2)

        class FakeRPCServer:
            def __init__(self):
                self.runner_generation = 1
                self.staged_generation = 0
                self.is_connected = True
                self.session_token = "fake-token"
                self.committed = False
                self.staging_started = False

            def reset_session_token(self):
                self.session_token = "new-fake-token"
                return self.session_token

            def restore_session_token(self, token):
                self.session_token = token

            def begin_staged_takeover(self):
                self.staging_started = True
                self.staged_generation = 2

            async def commit_staged_takeover(self):
                self.runner_generation = self.staged_generation
                self.staged_generation = 0
                self.committed = True

            async def rollback_staged_takeover(self):
                self.staged_generation = 0

            def has_generation(self, generation):
                return generation in {self.runner_generation, self.staged_generation}

            async def send_request(self, method, timeout_ms=5000, target_generation=None, **kwargs):
                assert target_generation == 2
                return SimpleNamespace(payload=HealthPayload(healthy=True).model_dump())

        supervisor._rpc_server = FakeRPCServer()
        supervisor._runner_process = old_process

        async def fake_spawn_runner():
            supervisor._runner_process = new_process
            supervisor._staged_registered_plugins["plugin_a"] = self._build_register_payload("plugin_a")
            supervisor._runner_ready_payloads[2] = SimpleNamespace(loaded_plugins=["plugin_a"], failed_plugins=[])
            supervisor._runner_ready_events[2] = asyncio.Event()
            supervisor._runner_ready_events[2].set()

        monkeypatch.setattr(supervisor, "_spawn_runner", fake_spawn_runner)

        reloaded = await supervisor.reload_plugins("test")

        assert reloaded is True
        assert supervisor._runner_process is new_process
        assert supervisor._rpc_server.committed is True
        assert old_process.terminated is True

    @pytest.mark.asyncio
    async def test_reload_restores_runtime_state_on_failure(self, monkeypatch):
        from src.plugin_runtime.host.supervisor import PluginSupervisor

        supervisor = PluginSupervisor(plugin_dirs=[])
        old_process = self._make_process(1)
        new_process = self._make_process(2)
        old_reg = self._build_register_payload()

        supervisor._runner_process = old_process
        supervisor._registered_plugins[old_reg.plugin_id] = old_reg
        supervisor._rebuild_runtime_state()

        class FakeRPCServer:
            def __init__(self):
                self.runner_generation = 1
                self.staged_generation = 0
                self.is_connected = True
                self.session_token = "fake-token"
                self.rolled_back = False

            def reset_session_token(self):
                self.session_token = "new-fake-token"
                return self.session_token

            def restore_session_token(self, token):
                self.session_token = token

            def begin_staged_takeover(self):
                self.staged_generation = 2

            async def commit_staged_takeover(self):
                self.runner_generation = self.staged_generation
                self.staged_generation = 0

            async def rollback_staged_takeover(self):
                self.rolled_back = True
                self.staged_generation = 0

            def has_generation(self, generation):
                return generation in {self.runner_generation, self.staged_generation}

            async def send_request(self, method, timeout_ms=5000, target_generation=None, **kwargs):
                raise RuntimeError("new runner unhealthy")

        supervisor._rpc_server = FakeRPCServer()

        async def fake_spawn_runner():
            supervisor._runner_process = new_process
            supervisor._staged_registered_plugins["plugin_a"] = self._build_register_payload("plugin_a")
            supervisor._runner_ready_payloads[2] = SimpleNamespace(loaded_plugins=["plugin_a"], failed_plugins=[])
            supervisor._runner_ready_events[2] = asyncio.Event()
            supervisor._runner_ready_events[2].set()

        monkeypatch.setattr(supervisor, "_spawn_runner", fake_spawn_runner)

        reloaded = await supervisor.reload_plugins("test")

        assert reloaded is False
        assert supervisor._runner_process is old_process
        assert supervisor._rpc_server.rolled_back is True
        assert old_reg.plugin_id in supervisor._registered_plugins
        assert supervisor.component_registry.get_component("plugin_a.handler") is not None

    @pytest.mark.asyncio
    async def test_reload_rebuilds_exact_component_set(self, monkeypatch):
        from src.plugin_runtime.host.supervisor import PluginSupervisor
        from src.plugin_runtime.protocol.envelope import HealthPayload

        supervisor = PluginSupervisor(plugin_dirs=[])
        old_process = self._make_process(1)
        new_process = self._make_process(2)
        old_reg = self._build_register_payload("plugin_a", component_names=["handler", "obsolete"])
        new_reg = self._build_register_payload("plugin_a", component_names=["handler"])

        supervisor._runner_process = old_process
        supervisor._registered_plugins[old_reg.plugin_id] = old_reg
        supervisor._rebuild_runtime_state()

        class FakeRPCServer:
            def __init__(self):
                self.runner_generation = 1
                self.staged_generation = 0
                self.is_connected = True
                self.session_token = "fake-token"

            def reset_session_token(self):
                self.session_token = "new-fake-token"
                return self.session_token

            def restore_session_token(self, token):
                self.session_token = token

            def begin_staged_takeover(self):
                self.staged_generation = 2

            async def commit_staged_takeover(self):
                self.runner_generation = self.staged_generation
                self.staged_generation = 0

            async def rollback_staged_takeover(self):
                self.staged_generation = 0

            def has_generation(self, generation):
                return generation in {self.runner_generation, self.staged_generation}

            async def send_request(self, method, timeout_ms=5000, target_generation=None, **kwargs):
                return SimpleNamespace(payload=HealthPayload(healthy=True).model_dump())

        supervisor._rpc_server = FakeRPCServer()

        async def fake_spawn_runner():
            supervisor._runner_process = new_process
            supervisor._staged_registered_plugins[new_reg.plugin_id] = new_reg
            supervisor._runner_ready_payloads[2] = SimpleNamespace(loaded_plugins=["plugin_a"], failed_plugins=[])
            supervisor._runner_ready_events[2] = asyncio.Event()
            supervisor._runner_ready_events[2].set()

        monkeypatch.setattr(supervisor, "_spawn_runner", fake_spawn_runner)

        reloaded = await supervisor.reload_plugins("test")

        assert reloaded is True
        assert supervisor.component_registry.get_component("plugin_a.handler") is not None
        assert supervisor.component_registry.get_component("plugin_a.obsolete") is None

    @pytest.mark.asyncio
    async def test_reload_plugins_uses_batch_rpc_for_multiple_roots(self):
        from src.plugin_runtime.host.supervisor import PluginSupervisor
        from src.plugin_runtime.protocol.envelope import ReloadPluginsResultPayload

        supervisor = PluginSupervisor(plugin_dirs=[])
        sent_requests: list[tuple[str, dict[str, object], int]] = []

        class FakeRPCServer:
            async def send_request(self, method, payload, timeout_ms=5000, **kwargs):
                del kwargs
                sent_requests.append((method, payload, timeout_ms))
                return SimpleNamespace(
                    payload=ReloadPluginsResultPayload(
                        success=True,
                        requested_plugin_ids=["plugin_a", "plugin_b"],
                        reloaded_plugins=["plugin_a", "plugin_b", "plugin_c"],
                        unloaded_plugins=["plugin_c", "plugin_b", "plugin_a"],
                    ).model_dump()
                )

        supervisor._rpc_server = FakeRPCServer()

        reloaded = await supervisor.reload_plugins(["plugin_a", "plugin_b", "plugin_a"], reason="manual")

        assert reloaded is True
        assert len(sent_requests) == 1
        method, payload, timeout_ms = sent_requests[0]
        assert method == "plugin.reload_batch"
        assert payload["plugin_ids"] == ["plugin_a", "plugin_b"]
        assert payload["reason"] == "manual"
        assert timeout_ms >= 10000

    @pytest.mark.asyncio
    async def test_reload_rolls_back_when_runner_ready_not_received(self, monkeypatch):
        from src.plugin_runtime.host.supervisor import PluginSupervisor

        supervisor = PluginSupervisor(plugin_dirs=[], runner_spawn_timeout_sec=0.01)
        old_process = self._make_process(1)
        new_process = self._make_process(2)
        old_reg = self._build_register_payload()

        supervisor._runner_process = old_process
        supervisor._registered_plugins[old_reg.plugin_id] = old_reg
        supervisor._rebuild_runtime_state()

        class FakeRPCServer:
            def __init__(self):
                self.runner_generation = 1
                self.staged_generation = 0
                self.is_connected = True
                self.session_token = "fake-token"
                self.rolled_back = False

            def reset_session_token(self):
                self.session_token = "new-fake-token"
                return self.session_token

            def restore_session_token(self, token):
                self.session_token = token

            def begin_staged_takeover(self):
                self.staged_generation = 2

            async def commit_staged_takeover(self):
                raise AssertionError("runner.ready 未到达前不应提交 staged takeover")

            async def rollback_staged_takeover(self):
                self.rolled_back = True
                self.staged_generation = 0

            def has_generation(self, generation):
                return generation in {self.runner_generation, self.staged_generation}

            async def send_request(self, method, timeout_ms=5000, target_generation=None, **kwargs):
                raise AssertionError("runner.ready 未到达前不应执行健康检查")

        supervisor._rpc_server = FakeRPCServer()

        async def fake_spawn_runner():
            supervisor._runner_process = new_process
            supervisor._staged_registered_plugins["plugin_a"] = self._build_register_payload("plugin_a")

        monkeypatch.setattr(supervisor, "_spawn_runner", fake_spawn_runner)

        reloaded = await supervisor.reload_plugins("test")

        assert reloaded is False
        assert supervisor._runner_process is old_process
        assert supervisor._rpc_server.rolled_back is True

    @pytest.mark.asyncio
    async def test_attach_stderr_drain_drains_stream(self):
        """_attach_stderr_drain 为 stderr 创建排空任务，读完后任务自动完成。"""
        from src.plugin_runtime.host.supervisor import PluginSupervisor

        supervisor = PluginSupervisor(plugin_dirs=[])

        stderr = asyncio.StreamReader()
        stderr.feed_data(b"fatal startup error\n")
        stderr.feed_eof()

        # stdout=None 模拟新架构（不再捕获 stdout）
        process = SimpleNamespace(pid=99, stdout=None, stderr=stderr)
        supervisor._attach_stderr_drain(process)

        # 给 drain task 足够时间消费完数据
        await asyncio.sleep(0.05)

        assert supervisor._stderr_drain_task is None or supervisor._stderr_drain_task.done()


class TestIntegration:
    """运行时集成层启动/清理测试"""

    @pytest.mark.asyncio
    async def test_cap_database_get_with_filters_does_not_reference_unbound_key_value(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module
        import src.common.database.database_model as real_db_models
        from src.services import database_service as real_database_service

        captured: dict[str, object] = {}

        class DummyModel:
            pass

        async def fake_db_get(model_class, filters=None, limit=None, order_by=None, single_result=False):
            captured["model_class"] = model_class
            captured["filters"] = filters
            captured["limit"] = limit
            captured["order_by"] = order_by
            captured["single_result"] = single_result
            return [{"id": 1}]

        monkeypatch.setattr(real_database_service, "db_get", fake_db_get)
        monkeypatch.setattr(real_db_models, "DemoTable", DummyModel, raising=False)

        manager = object.__new__(integration_module.PluginRuntimeManager)
        result = await manager._cap_database_get(
            "plugin_a",
            "database.get",
            {
                "model_name": "DemoTable",
                "filters": {"status": "active"},
                "limit": 5,
            },
        )

        assert result == [{"id": 1}]
        assert captured["model_class"] is DummyModel
        assert captured["filters"] == {"status": "active"}
        assert captured["limit"] == 5
        assert captured["single_result"] is False

    @pytest.mark.asyncio
    async def test_cap_database_get_response_is_not_double_wrapped(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module
        import src.common.database.database_model as real_db_models
        from src.plugin_runtime.host.capability_service import CapabilityService
        from src.plugin_runtime.protocol.envelope import CapabilityRequestPayload, Envelope, MessageType
        from src.services import database_service as real_database_service

        class AllowAllAuthorization:
            def check_capability(self, plugin_id, capability):
                return True, ""

        class DummyModel:
            pass

        async def fake_db_get(model_class, filters=None, limit=None, order_by=None, single_result=False):
            return {"id": 1, "full_path": "E:\\test.png"}

        monkeypatch.setattr(real_database_service, "db_get", fake_db_get)
        monkeypatch.setattr(real_db_models, "DemoTable", DummyModel, raising=False)

        manager = object.__new__(integration_module.PluginRuntimeManager)
        service = CapabilityService(AllowAllAuthorization())
        service.register_capability("database.get", manager._cap_database_get)

        request = Envelope(
            request_id=1,
            message_type=MessageType.REQUEST,
            method="cap.call",
            plugin_id="plugin_a",
            payload=CapabilityRequestPayload(
                capability="database.get",
                args={"model_name": "DemoTable", "single_result": True},
            ).model_dump(),
        )

        response = await service.handle_capability_request(request)

        assert response.payload == {
            "success": True,
            "result": {"id": 1, "full_path": "E:\\test.png"},
        }

    @pytest.mark.asyncio
    async def test_cap_database_success_handlers_return_raw_results(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module
        import src.common.database.database_model as real_db_models
        from src.services import database_service as real_database_service

        class DummyModel:
            pass

        async def fake_db_get(**kwargs):
            return [{"id": 1}]

        async def fake_db_save(**kwargs):
            return {"id": 2}

        async def fake_db_delete(**kwargs):
            return 3

        async def fake_db_count(**kwargs):
            return 4

        monkeypatch.setattr(real_database_service, "db_get", fake_db_get)
        monkeypatch.setattr(real_database_service, "db_save", fake_db_save)
        monkeypatch.setattr(real_database_service, "db_delete", fake_db_delete)
        monkeypatch.setattr(real_database_service, "db_count", fake_db_count)
        monkeypatch.setattr(real_db_models, "DemoTable", DummyModel, raising=False)

        manager = object.__new__(integration_module.PluginRuntimeManager)
        base_args = {"model_name": "DemoTable"}

        assert await manager._cap_database_query("plugin_a", "database.query", base_args) == [{"id": 1}]
        assert await manager._cap_database_save(
            "plugin_a", "database.save", {**base_args, "data": {"name": "demo"}}
        ) == {"id": 2}
        assert await manager._cap_database_delete(
            "plugin_a", "database.delete", {**base_args, "filters": {"id": 2}}
        ) == 3
        assert await manager._cap_database_count("plugin_a", "database.count", base_args) == 4

    @pytest.mark.asyncio
    async def test_component_enable_rejects_ambiguous_short_name(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        class FakeSupervisor:
            def __init__(self, plugin_id: str):
                self.component_registry = ComponentRegistry()
                self.component_registry.register_component(
                    name="shared",
                    component_type="tool",
                    plugin_id=plugin_id,
                    metadata={},
                )

        class FakeManager:
            def __init__(self):
                self.supervisors = [FakeSupervisor("plugin_a"), FakeSupervisor("plugin_b")]

        monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: FakeManager())
        manager = integration_module.PluginRuntimeManager()
        manager._builtin_supervisor = FakeSupervisor("plugin_a")
        manager._third_party_supervisor = FakeSupervisor("plugin_b")

        result = await manager._cap_component_enable(
            "plugin_a",
            "component.enable",
            {"name": "shared", "component_type": "tool", "scope": "global", "stream_id": ""},
        )

        assert result["success"] is False
        assert "组件名不唯一" in result["error"]

    @pytest.mark.asyncio
    async def test_component_disable_rejects_non_global_scope(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        class FakeSupervisor:
            def __init__(self):
                self.component_registry = ComponentRegistry()
                self.component_registry.register_component(
                    name="handler",
                    component_type="tool",
                    plugin_id="plugin_a",
                    metadata={},
                )

        class FakeManager:
            def __init__(self):
                self.supervisors = [FakeSupervisor()]

        monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: FakeManager())
        manager = integration_module.PluginRuntimeManager()
        manager._builtin_supervisor = FakeSupervisor()

        result = await manager._cap_component_disable(
            "plugin_a",
            "component.disable",
            {"name": "plugin_a.handler", "component_type": "tool", "scope": "stream", "stream_id": "s1"},
        )

        assert result["success"] is False
        assert "仅支持全局组件禁用" in result["error"]

    @pytest.mark.asyncio
    async def test_start_cleans_up_started_supervisors_on_failure(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module

        instances = []
        builtin_dir = Path("builtin")
        thirdparty_dir = Path("thirdparty")

        class FakeCapabilityService:
            def register_capability(self, name, impl):
                return None

        class FakeSupervisor:
            def __init__(self, plugin_dirs=None, socket_path=None):
                self._plugin_dirs = plugin_dirs or []
                self.capability_service = FakeCapabilityService()
                self.external_plugin_versions = {}
                self.stopped = False
                instances.append(self)

            def set_external_available_plugins(self, plugin_versions):
                self.external_plugin_versions = dict(plugin_versions)

            def get_loaded_plugin_ids(self):
                return []

            def get_loaded_plugin_versions(self):
                return {}

            async def start(self):
                if len(instances) == 2 and self is instances[1]:
                    raise RuntimeError("boom")

            async def stop(self):
                self.stopped = True

        monkeypatch.setattr(
            integration_module.PluginRuntimeManager, "_get_builtin_plugin_dirs", staticmethod(lambda: [builtin_dir])
        )
        monkeypatch.setattr(
            integration_module.PluginRuntimeManager, "_get_third_party_plugin_dirs", staticmethod(lambda: [thirdparty_dir])
        )

        import src.plugin_runtime.host.supervisor as supervisor_module

        monkeypatch.setattr(supervisor_module, "PluginSupervisor", FakeSupervisor)

        manager = integration_module.PluginRuntimeManager()
        await manager.start()

        assert manager.is_running is False
        assert len(instances) == 2
        assert instances[0].stopped is True

    @pytest.mark.asyncio
    async def test_handle_plugin_source_changes_restarts_supervisors_after_dependency_sync(self, monkeypatch, tmp_path):
        from src.config.file_watcher import FileChange
        from src.plugin_runtime import integration as integration_module
        import json

        builtin_root = tmp_path / "src" / "plugins" / "built_in"
        thirdparty_root = tmp_path / "plugins"
        alpha_dir = builtin_root / "alpha"
        beta_dir = thirdparty_root / "beta"
        alpha_dir.mkdir(parents=True)
        beta_dir.mkdir(parents=True)
        (alpha_dir / "config.toml").write_text("enabled = true\n", encoding="utf-8")
        (beta_dir / "config.toml").write_text("enabled = false\n", encoding="utf-8")
        (alpha_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (beta_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (alpha_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.alpha")), encoding="utf-8")
        (beta_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.beta")), encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        class FakeSupervisor:
            def __init__(self, plugin_dirs, registered_plugins):
                self._plugin_dirs = plugin_dirs
                self._registered_plugins = registered_plugins
                self.config_updates = []

            def get_loaded_plugin_ids(self):
                return sorted(self._registered_plugins.keys())

            def get_loaded_plugin_versions(self):
                return {plugin_id: "1.0.0" for plugin_id in self._registered_plugins}

            async def notify_plugin_config_updated(self, plugin_id, config_data, config_version=""):
                self.config_updates.append((plugin_id, config_data, config_version))
                return True

        manager = integration_module.PluginRuntimeManager()
        manager._started = True
        manager._builtin_supervisor = FakeSupervisor([builtin_root], {"test.alpha": object()})
        manager._third_party_supervisor = FakeSupervisor([thirdparty_root], {"test.beta": object()})
        dependency_sync_calls = []
        restart_calls = []

        async def fake_sync(plugin_dirs: Sequence[Path]) -> Any:
            """记录依赖同步调用。"""

            dependency_sync_calls.append(list(plugin_dirs))
            return integration_module.DependencySyncState(
                blocked_changed_plugin_ids={"test.beta"},
                environment_changed=False,
            )

        async def fake_restart(reason: str) -> bool:
            """记录 Supervisor 重启调用。"""

            restart_calls.append(reason)
            return True

        monkeypatch.setattr(manager, "_sync_plugin_dependencies", fake_sync)
        monkeypatch.setattr(manager, "_restart_supervisors", fake_restart)

        changes = [
            FileChange(change_type=1, path=beta_dir / "plugin.py"),
        ]

        await manager._handle_plugin_source_changes(changes)

        assert dependency_sync_calls == [[builtin_root, thirdparty_root]]
        assert restart_calls == ["file_watcher_blocklist_changed"]
        assert manager._builtin_supervisor.config_updates == []
        assert manager._third_party_supervisor.config_updates == []

    @pytest.mark.asyncio
    async def test_reload_plugins_globally_warns_and_skips_cross_supervisor_dependents(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module

        class FakeRegistration:
            def __init__(self, dependencies):
                self.dependencies = dependencies

        class FakeSupervisor:
            def __init__(self, registrations):
                self._registered_plugins = registrations
                self.reload_calls = []

            def get_loaded_plugin_ids(self):
                return sorted(self._registered_plugins.keys())

            def get_loaded_plugin_versions(self):
                return {plugin_id: "1.0.0" for plugin_id in self._registered_plugins}

            async def reload_plugins(self, plugin_ids=None, reason="manual", external_available_plugins=None):
                self.reload_calls.append((plugin_ids, reason, dict(sorted((external_available_plugins or {}).items()))))
                return True

        builtin_supervisor = FakeSupervisor({"test.alpha": FakeRegistration([])})
        third_party_supervisor = FakeSupervisor(
            {
                "test.beta": FakeRegistration(["test.alpha"]),
                "test.gamma": FakeRegistration(["test.beta"]),
            }
        )

        manager = integration_module.PluginRuntimeManager()
        manager._builtin_supervisor = builtin_supervisor
        manager._third_party_supervisor = third_party_supervisor
        warning_messages = []

        monkeypatch.setattr(
            integration_module.logger,
            "warning",
            lambda message: warning_messages.append(message),
        )

        reloaded = await manager.reload_plugins_globally(["test.alpha"], reason="manual")

        assert reloaded is True
        assert builtin_supervisor.reload_calls == [
            (["test.alpha"], "manual", {"test.beta": "1.0.0", "test.gamma": "1.0.0"})
        ]
        assert third_party_supervisor.reload_calls == []
        assert len(warning_messages) == 1
        assert "test.beta, test.gamma" in warning_messages[0]
        assert "跨 Supervisor API 调用仍然可用" in warning_messages[0]

    @pytest.mark.asyncio
    async def test_handle_plugin_config_changes_only_notify_target_plugin(self, monkeypatch, tmp_path):
        from src.plugin_runtime import integration as integration_module
        from src.config.file_watcher import FileChange
        import json

        builtin_root = tmp_path / "src" / "plugins" / "built_in"
        thirdparty_root = tmp_path / "plugins"
        alpha_dir = builtin_root / "alpha"
        beta_dir = thirdparty_root / "beta"
        alpha_dir.mkdir(parents=True)
        beta_dir.mkdir(parents=True)
        (alpha_dir / "config.toml").write_text("enabled = true\n", encoding="utf-8")
        (beta_dir / "config.toml").write_text("enabled = false\n", encoding="utf-8")
        (alpha_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (beta_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (alpha_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.alpha")), encoding="utf-8")
        (beta_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.beta")), encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        class FakeSupervisor:
            def __init__(self, plugin_dirs, plugins):
                self._plugin_dirs = plugin_dirs
                self._registered_plugins = {plugin_id: object() for plugin_id in plugins}
                self.config_updates = []

            async def inspect_plugin_config(
                self,
                plugin_id: str,
                config_data: Optional[Dict[str, Any]] = None,
                use_provided_config: bool = False,
            ) -> SimpleNamespace:
                """返回测试用的配置解析结果。"""
                del config_data, use_provided_config
                return SimpleNamespace(enabled=True, normalized_config={"enabled": True}, plugin_id=plugin_id)

            async def notify_plugin_config_updated(
                self,
                plugin_id,
                config_data,
                config_version="",
                config_scope="self",
            ):
                self.config_updates.append((plugin_id, config_data, config_version, config_scope))
                return True

        manager = integration_module.PluginRuntimeManager()
        manager._started = True
        manager._builtin_supervisor = FakeSupervisor([builtin_root], ["test.alpha"])
        manager._third_party_supervisor = FakeSupervisor([thirdparty_root], ["test.beta"])

        await manager._handle_plugin_config_changes(
            "test.alpha",
            [FileChange(change_type=1, path=alpha_dir / "config.toml")],
        )

        assert manager._builtin_supervisor.config_updates == [("test.alpha", {"enabled": True}, "", "self")]
        assert manager._third_party_supervisor.config_updates == []

    @pytest.mark.asyncio
    async def test_handle_plugin_config_changes_loads_unloaded_enabled_plugin(self, monkeypatch, tmp_path):
        from src.plugin_runtime import integration as integration_module
        from src.config.file_watcher import FileChange
        import json

        thirdparty_root = tmp_path / "plugins"
        alpha_dir = thirdparty_root / "alpha"
        alpha_dir.mkdir(parents=True)
        (alpha_dir / "config.toml").write_text("[plugin]\nenabled = true\n", encoding="utf-8")
        (alpha_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (alpha_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.alpha")), encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        class FakeSupervisor:
            def __init__(self, plugin_dirs):
                self._plugin_dirs = plugin_dirs
                self._registered_plugins = {}

            async def inspect_plugin_config(
                self,
                plugin_id: str,
                config_data: Optional[Dict[str, Any]] = None,
                use_provided_config: bool = False,
            ) -> SimpleNamespace:
                """返回测试用的启用配置快照。"""
                del config_data, use_provided_config
                return SimpleNamespace(enabled=True, normalized_config={"plugin": {"enabled": True}}, plugin_id=plugin_id)

        manager = integration_module.PluginRuntimeManager()
        manager._started = True
        manager._third_party_supervisor = FakeSupervisor([thirdparty_root])

        load_calls = []

        async def fake_load_plugin_globally(plugin_id: str, reason: str = "manual") -> bool:
            """记录自动加载调用。"""
            load_calls.append((plugin_id, reason))
            return True

        monkeypatch.setattr(manager, "load_plugin_globally", fake_load_plugin_globally)

        await manager._handle_plugin_config_changes(
            "test.alpha",
            [FileChange(change_type=1, path=alpha_dir / "config.toml")],
        )

        assert load_calls == [("test.alpha", "config_enabled")]

    @pytest.mark.asyncio
    async def test_handle_plugin_config_changes_unloads_loaded_disabled_plugin(self, monkeypatch, tmp_path):
        from src.plugin_runtime import integration as integration_module
        from src.config.file_watcher import FileChange
        import json

        builtin_root = tmp_path / "src" / "plugins" / "built_in"
        alpha_dir = builtin_root / "alpha"
        alpha_dir.mkdir(parents=True)
        (alpha_dir / "config.toml").write_text("[plugin]\nenabled = false\n", encoding="utf-8")
        (alpha_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (alpha_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.alpha")), encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        class FakeSupervisor:
            def __init__(self, plugin_dirs, plugins):
                self._plugin_dirs = plugin_dirs
                self._registered_plugins = {plugin_id: object() for plugin_id in plugins}

            async def inspect_plugin_config(
                self,
                plugin_id: str,
                config_data: Optional[Dict[str, Any]] = None,
                use_provided_config: bool = False,
            ) -> SimpleNamespace:
                """返回测试用的禁用配置快照。"""
                del config_data, use_provided_config
                return SimpleNamespace(
                    enabled=False,
                    normalized_config={"plugin": {"enabled": False}},
                    plugin_id=plugin_id,
                )

        manager = integration_module.PluginRuntimeManager()
        manager._started = True
        manager._builtin_supervisor = FakeSupervisor([builtin_root], ["test.alpha"])

        reload_calls = []

        async def fake_reload_plugins_globally(plugin_ids: Sequence[str], reason: str = "manual") -> bool:
            """记录自动卸载调用。"""
            reload_calls.append((list(plugin_ids), reason))
            return True

        monkeypatch.setattr(manager, "reload_plugins_globally", fake_reload_plugins_globally)

        await manager._handle_plugin_config_changes(
            "test.alpha",
            [FileChange(change_type=1, path=alpha_dir / "config.toml")],
        )

        assert reload_calls == [(["test.alpha"], "config_disabled")]

    @pytest.mark.asyncio
    async def test_handle_main_config_reload_only_notifies_subscribers(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module

        class FakeRegistration:
            def __init__(self, subscriptions):
                self.config_reload_subscriptions = subscriptions

        class FakeSupervisor:
            def __init__(self, registrations):
                self._registered_plugins = registrations
                self.config_updates = []

            def get_config_reload_subscribers(self, scope):
                matched_plugins = []
                for plugin_id, registration in self._registered_plugins.items():
                    if scope in registration.config_reload_subscriptions:
                        matched_plugins.append(plugin_id)
                return matched_plugins

            async def notify_plugin_config_updated(
                self,
                plugin_id,
                config_data,
                config_version="",
                config_scope="self",
            ):
                self.config_updates.append((plugin_id, config_data, config_version, config_scope))
                return True

        fake_global = SimpleNamespace(plugin_runtime=SimpleNamespace(enabled=True))
        monkeypatch.setattr(
            integration_module.config_manager,
            "get_global_config",
            lambda: SimpleNamespace(model_dump=lambda: {"bot": {"name": "MaiBot"}}, plugin_runtime=fake_global.plugin_runtime),
        )
        monkeypatch.setattr(
            integration_module.config_manager,
            "get_model_config",
            lambda: SimpleNamespace(model_dump=lambda: {"models": [{"name": "demo"}]}),
        )

        manager = integration_module.PluginRuntimeManager()
        manager._started = True
        manager._builtin_supervisor = FakeSupervisor(
            {
                "test.alpha": FakeRegistration(["bot"]),
                "test.beta": FakeRegistration([]),
            }
        )
        manager._third_party_supervisor = FakeSupervisor(
            {
                "test.gamma": FakeRegistration(["model"]),
            }
        )

        await manager._handle_main_config_reload(["bot", "model"])

        assert manager._builtin_supervisor.config_updates == [
            ("test.alpha", {"bot": {"name": "MaiBot"}}, "", "bot")
        ]
        assert manager._third_party_supervisor.config_updates == [
            ("test.gamma", {"models": [{"name": "demo"}]}, "", "model")
        ]

    def test_refresh_plugin_config_watch_subscriptions_registers_per_plugin(self, tmp_path):
        from src.plugin_runtime import integration as integration_module
        import json

        builtin_root = tmp_path / "src" / "plugins" / "built_in"
        thirdparty_root = tmp_path / "plugins"
        alpha_dir = builtin_root / "alpha"
        beta_dir = thirdparty_root / "beta"
        alpha_dir.mkdir(parents=True)
        beta_dir.mkdir(parents=True)
        (alpha_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (beta_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (alpha_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.alpha")), encoding="utf-8")
        (beta_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.beta")), encoding="utf-8")

        class FakeWatcher:
            def __init__(self):
                self.subscriptions = []
                self.unsubscribed = []

            def subscribe(self, callback, *, paths=None, change_types=None):
                subscription_id = f"sub-{len(self.subscriptions) + 1}"
                self.subscriptions.append({"id": subscription_id, "callback": callback, "paths": tuple(paths or ())})
                return subscription_id

            def unsubscribe(self, subscription_id):
                self.unsubscribed.append(subscription_id)
                return True

        class FakeSupervisor:
            def __init__(self, plugin_dirs, plugins):
                self._plugin_dirs = plugin_dirs
                self._registered_plugins = {plugin_id: object() for plugin_id in plugins}

        manager = integration_module.PluginRuntimeManager()
        manager._plugin_file_watcher = FakeWatcher()
        manager._builtin_supervisor = FakeSupervisor([builtin_root], ["test.alpha"])
        manager._third_party_supervisor = FakeSupervisor([thirdparty_root], ["test.beta"])

        manager._refresh_plugin_config_watch_subscriptions()

        assert set(manager._plugin_config_watcher_subscriptions.keys()) == {"test.alpha", "test.beta"}
        assert {
            subscription["paths"][0] for subscription in manager._plugin_file_watcher.subscriptions
        } == {alpha_dir / "config.toml", beta_dir / "config.toml"}

    def test_refresh_plugin_config_watch_subscriptions_includes_unloaded_plugins(self, tmp_path):
        from src.plugin_runtime import integration as integration_module
        import json

        thirdparty_root = tmp_path / "plugins"
        alpha_dir = thirdparty_root / "alpha"
        beta_dir = thirdparty_root / "beta"
        alpha_dir.mkdir(parents=True)
        beta_dir.mkdir(parents=True)
        (alpha_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (beta_dir / "plugin.py").write_text("def create_plugin():\n    return object()\n", encoding="utf-8")
        (alpha_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.alpha")), encoding="utf-8")
        (beta_dir / "_manifest.json").write_text(json.dumps(build_test_manifest("test.beta")), encoding="utf-8")

        class FakeWatcher:
            def __init__(self):
                self.subscriptions = []

            def subscribe(
                self,
                callback: Any,
                *,
                paths: Optional[Sequence[Path]] = None,
                change_types: Any = None,
            ) -> str:
                """记录新的监听订阅。"""
                del callback, change_types
                subscription_id = f"sub-{len(self.subscriptions) + 1}"
                self.subscriptions.append({"id": subscription_id, "paths": tuple(paths or ())})
                return subscription_id

            def unsubscribe(self, subscription_id: str) -> bool:
                """兼容 watcher 取消订阅接口。"""
                del subscription_id
                return True

        class FakeSupervisor:
            def __init__(self, plugin_dirs, plugins):
                self._plugin_dirs = plugin_dirs
                self._registered_plugins = {plugin_id: object() for plugin_id in plugins}

        manager = integration_module.PluginRuntimeManager()
        manager._plugin_file_watcher = FakeWatcher()
        manager._third_party_supervisor = FakeSupervisor([thirdparty_root], ["test.alpha"])

        manager._refresh_plugin_config_watch_subscriptions()

        assert set(manager._plugin_config_watcher_subscriptions.keys()) == {"test.alpha", "test.beta"}

    @pytest.mark.asyncio
    async def test_component_reload_plugin_returns_failure_when_reload_rolls_back(self, monkeypatch):
        from src.plugin_runtime import integration as integration_module

        manager = integration_module.PluginRuntimeManager()
        monkeypatch.setattr(manager, "reload_plugins_globally", lambda plugin_ids, reason="manual": asyncio.sleep(0, False))

        result = await manager._cap_component_reload_plugin(
            "plugin_a",
            "component.reload_plugin",
            {"plugin_name": "alpha"},
        )

        assert result["success"] is False
        assert result["error"] == "插件 alpha 热重载失败"

    @pytest.mark.asyncio
    async def test_component_load_plugin_returns_failure_when_reload_rolls_back(self, monkeypatch, tmp_path):
        from src.plugin_runtime import integration as integration_module

        manager = integration_module.PluginRuntimeManager()
        monkeypatch.setattr(manager, "load_plugin_globally", lambda plugin_id, reason="manual": asyncio.sleep(0, False))

        result = await manager._cap_component_load_plugin(
            "plugin_a",
            "component.load_plugin",
            {"plugin_name": "alpha"},
        )

        assert result["success"] is False
        assert result["error"] == "插件 alpha 热重载失败"
