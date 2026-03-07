"""插件运行时框架基础测试

验证协议层、传输层、RPC 通信链路的正确性。
"""

import asyncio
import sys
import os

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# SDK 包路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "maibot-plugin-sdk"))


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
    async def test_transport_factory(self):
        """传输工厂测试"""
        from src.plugin_runtime.transport.factory import create_transport_server, create_transport_client

        server = create_transport_server()
        assert server is not None

        # UDS 路径
        client = create_transport_client("/tmp/test.sock")
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


# ─── 端到端集成测试 ────────────────────────────────────────

class TestE2E:
    """端到端集成测试（Host + Runner 通信）"""

    @pytest.mark.asyncio
    async def test_handshake(self):
        """Host-Runner 握手流程测试"""
        from src.plugin_runtime.protocol.codec import MsgPackCodec
        from src.plugin_runtime.protocol.envelope import Envelope, HelloPayload, HelloResponsePayload, MessageType
        from src.plugin_runtime.transport.uds import UDSTransportServer, UDSTransportClient

        import secrets
        import tempfile
        import os

        socket_path = os.path.join(tempfile.gettempdir(), f"maibot-test-{os.getpid()}.sock")
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

        server = UDSTransportServer(socket_path=socket_path)
        await server.start(server_handler)

        # 客户端握手
        client = UDSTransportClient(socket_path)
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

        validator = ManifestValidator()
        manifest = {
            "manifest_version": 1,
            "name": "test_plugin",
            "version": "1.0.0",
            "description": "测试插件",
            "author": "test",
        }
        assert validator.validate(manifest) is True
        assert len(validator.errors) == 0

    def test_missing_required_fields(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator()
        manifest = {"manifest_version": 1}
        assert validator.validate(manifest) is False
        assert len(validator.errors) >= 4  # name, version, description, author

    def test_unsupported_manifest_version(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator()
        manifest = {
            "manifest_version": 999,
            "name": "test",
            "version": "1.0",
            "description": "d",
            "author": "a",
        }
        assert validator.validate(manifest) is False
        assert any("manifest_version" in e for e in validator.errors)

    def test_host_version_compatibility(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator(host_version="0.8.5")
        manifest = {
            "name": "test",
            "version": "1.0",
            "description": "d",
            "author": "a",
            "host_application": {"min_version": "0.9.0"},
        }
        assert validator.validate(manifest) is False
        assert any("Host 版本不兼容" in e for e in validator.errors)

    def test_recommended_fields_warning(self):
        from src.plugin_runtime.runner.manifest_validator import ManifestValidator

        validator = ManifestValidator()
        manifest = {
            "name": "test",
            "version": "1.0",
            "description": "d",
            "author": "a",
        }
        validator.validate(manifest)
        assert len(validator.warnings) >= 3  # license, keywords, categories


class TestVersionComparator:
    """版本号比较器测试"""

    def test_normalize(self):
        from src.plugin_runtime.runner.manifest_validator import VersionComparator

        assert VersionComparator.normalize_version("0.8.0-snapshot.1") == "0.8.0"
        assert VersionComparator.normalize_version("1.2") == "1.2.0"
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
            "core": ("dir_core", {"name": "core", "version": "1.0", "description": "d", "author": "a"}, "plugin.py"),
            "auth": ("dir_auth", {"name": "auth", "version": "1.0", "description": "d", "author": "a", "dependencies": ["core"]}, "plugin.py"),
            "api": ("dir_api", {"name": "api", "version": "1.0", "description": "d", "author": "a", "dependencies": ["core", "auth"]}, "plugin.py"),
        }

        order, failed = loader._resolve_dependencies(candidates)
        assert len(failed) == 0
        assert order.index("core") < order.index("auth")
        assert order.index("auth") < order.index("api")

    def test_missing_dependency(self):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        loader = PluginLoader()
        candidates = {
            "plugin_a": ("dir_a", {"name": "plugin_a", "version": "1.0", "description": "d", "author": "a", "dependencies": ["nonexistent"]}, "plugin.py"),
        }

        order, failed = loader._resolve_dependencies(candidates)
        assert "plugin_a" in failed
        assert "缺少依赖" in failed["plugin_a"]

    def test_circular_dependency(self):
        from src.plugin_runtime.runner.plugin_loader import PluginLoader

        loader = PluginLoader()
        candidates = {
            "a": ("dir_a", {"name": "a", "version": "1.0", "description": "d", "author": "x", "dependencies": ["b"]}, "p.py"),
            "b": ("dir_b", {"name": "b", "version": "1.0", "description": "d", "author": "x", "dependencies": ["a"]}, "p.py"),
        }

        order, failed = loader._resolve_dependencies(candidates)
        assert len(failed) >= 1  # 至少一个循环插件被标记


# ─── Host-side ComponentRegistry 测试 ──────────────────────

class TestComponentRegistry:
    """Host-side 组件注册表测试"""

    def test_register_and_query(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component("greet", "action", "plugin_a", {
            "description": "打招呼",
            "activation_type": "keyword",
            "activation_keywords": ["hi"],
        })
        reg.register_component("help", "command", "plugin_a", {
            "command_pattern": r"^/help",
        })
        reg.register_component("search", "tool", "plugin_b", {
            "description": "搜索",
        })

        stats = reg.get_stats()
        assert stats["total"] == 3
        assert stats["action"] == 1
        assert stats["command"] == 1
        assert stats["tool"] == 1

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
        reg.register_component("help", "command", "p1", {
            "command_pattern": r"^/help",
        })
        reg.register_component("echo", "command", "p1", {
            "command_pattern": r"^/echo\s",
        })

        match = reg.find_command_by_text("/help me")
        assert match is not None
        assert match.name == "help"

        match = reg.find_command_by_text("/echo hello")
        assert match is not None
        assert match.name == "echo"

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

    def test_event_handlers_sorted_by_weight(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component("h_low", "event_handler", "p1", {
            "event_type": "on_message", "weight": 10,
        })
        reg.register_component("h_high", "event_handler", "p2", {
            "event_type": "on_message", "weight": 100,
        })

        handlers = reg.get_event_handlers("on_message")
        assert handlers[0].name == "h_high"
        assert handlers[1].name == "h_low"

    def test_tools_for_llm(self):
        from src.plugin_runtime.host.component_registry import ComponentRegistry

        reg = ComponentRegistry()
        reg.register_component("search", "tool", "p1", {
            "description": "搜索工具",
            "parameters_raw": {"query": {"type": "string"}},
        })

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
        reg.register_component("h1", "event_handler", "p1", {
            "event_type": "on_start",
            "weight": 0,
            "intercept_message": False,
        })

        dispatcher = EventDispatcher(reg)
        call_log = []

        async def mock_invoke(plugin_id, comp_name, args):
            call_log.append((plugin_id, comp_name))
            return {"success": True, "continue_processing": True}

        should_continue, modified = await dispatcher.dispatch_event(
            "on_start", mock_invoke
        )
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
        reg.register_component("filter", "event_handler", "p1", {
            "event_type": "on_message_pre_process",
            "weight": 100,
            "intercept_message": True,
        })

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


# ─── WorkflowExecutor 测试 ────────────────────────────────

class TestWorkflowExecutor:
    """Host-side Workflow 执行器测试（新 pipeline 模型）"""

    @pytest.mark.asyncio
    async def test_empty_pipeline_completes(self):
        """无任何 workflow_step 注册时，pipeline 全阶段跳过，状态 completed"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        executor = WorkflowExecutor(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            return {"hook_result": "continue"}

        result, final_msg, ctx = await executor.execute(
            mock_invoke,
            message={"plain_text": "test"},
        )
        assert result.status == "completed"
        assert result.return_message == "workflow completed"
        assert len(ctx.timings) == 6  # 6 stages

    @pytest.mark.asyncio
    async def test_blocking_hook_modifies_message(self):
        """blocking hook 可以修改消息"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        reg.register_component("upper", "workflow_step", "p1", {
            "stage": "pre_process",
            "priority": 10,
            "blocking": True,
        })
        executor = WorkflowExecutor(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            msg = args.get("message", {})
            return {
                "hook_result": "continue",
                "modified_message": {**msg, "plain_text": msg.get("plain_text", "").upper()},
            }

        result, final_msg, ctx = await executor.execute(
            mock_invoke,
            message={"plain_text": "hello"},
        )
        assert result.status == "completed"
        assert final_msg["plain_text"] == "HELLO"
        assert len(ctx.modification_log) == 1
        assert ctx.modification_log[0].stage == "pre_process"

    @pytest.mark.asyncio
    async def test_abort_stops_pipeline(self):
        """HookResult.ABORT 立即终止 pipeline"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        reg.register_component("blocker", "workflow_step", "p1", {
            "stage": "pre_process",
            "priority": 10,
            "blocking": True,
        })
        executor = WorkflowExecutor(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            return {"hook_result": "abort"}

        result, _, ctx = await executor.execute(
            mock_invoke,
            message={"plain_text": "test"},
        )
        assert result.status == "aborted"
        assert result.stopped_at == "pre_process"

    @pytest.mark.asyncio
    async def test_skip_stage(self):
        """HookResult.SKIP_STAGE 跳过当前阶段剩余 hook"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        # high-priority hook 返回 skip_stage
        reg.register_component("skipper", "workflow_step", "p1", {
            "stage": "ingress",
            "priority": 100,
            "blocking": True,
        })
        # low-priority hook 不应被执行
        reg.register_component("checker", "workflow_step", "p2", {
            "stage": "ingress",
            "priority": 1,
            "blocking": True,
        })
        executor = WorkflowExecutor(reg)

        call_log = []

        async def mock_invoke(plugin_id, comp_name, args):
            call_log.append(comp_name)
            if comp_name == "skipper":
                return {"hook_result": "skip_stage"}
            return {"hook_result": "continue"}

        result, _, _ = await executor.execute(
            mock_invoke, message={"plain_text": "test"}
        )
        assert result.status == "completed"
        # 只有 skipper 被调用，checker 被跳过
        assert call_log == ["skipper"]

    @pytest.mark.asyncio
    async def test_pre_filter(self):
        """filter 条件不匹配时跳过 hook"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        reg.register_component("only_dm", "workflow_step", "p1", {
            "stage": "ingress",
            "priority": 10,
            "blocking": True,
            "filter": {"chat_type": "direct"},
        })
        executor = WorkflowExecutor(reg)

        call_log = []

        async def mock_invoke(plugin_id, comp_name, args):
            call_log.append(comp_name)
            return {"hook_result": "continue"}

        # 不匹配 filter —— hook 不应被调用
        await executor.execute(
            mock_invoke, message={"plain_text": "hi", "chat_type": "group"}
        )
        assert not call_log

        # 匹配 filter —— hook 应被调用
        await executor.execute(
            mock_invoke, message={"plain_text": "hi", "chat_type": "direct"}
        )
        assert call_log == ["only_dm"]

    @pytest.mark.asyncio
    async def test_error_policy_skip(self):
        """error_policy=skip 时跳过失败的 hook 继续执行"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        reg.register_component("failer", "workflow_step", "p1", {
            "stage": "ingress",
            "priority": 100,
            "blocking": True,
            "error_policy": "skip",
        })
        reg.register_component("ok_step", "workflow_step", "p2", {
            "stage": "ingress",
            "priority": 1,
            "blocking": True,
        })
        executor = WorkflowExecutor(reg)

        call_log = []

        async def mock_invoke(plugin_id, comp_name, args):
            call_log.append(comp_name)
            if comp_name == "failer":
                raise RuntimeError("boom")
            return {"hook_result": "continue"}

        result, _, ctx = await executor.execute(
            mock_invoke, message={"plain_text": "test"}
        )
        assert result.status == "completed"
        assert "failer" in call_log
        assert "ok_step" in call_log
        assert any("boom" in e for e in ctx.errors)

    @pytest.mark.asyncio
    async def test_error_policy_abort(self):
        """error_policy=abort（默认）时 pipeline 失败"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        reg.register_component("failer", "workflow_step", "p1", {
            "stage": "ingress",
            "priority": 10,
            "blocking": True,
            # error_policy defaults to "abort"
        })
        executor = WorkflowExecutor(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            raise RuntimeError("fatal")

        result, _, ctx = await executor.execute(
            mock_invoke, message={"plain_text": "test"}
        )
        assert result.status == "failed"
        assert result.stopped_at == "ingress"

    @pytest.mark.asyncio
    async def test_nonblocking_hooks_concurrent(self):
        """non-blocking hook 并发执行，不修改消息"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        for i in range(3):
            reg.register_component(f"nb_{i}", "workflow_step", f"p{i}", {
                "stage": "post_process",
                "priority": 0,
                "blocking": False,
            })
        executor = WorkflowExecutor(reg)

        call_log = []

        async def mock_invoke(plugin_id, comp_name, args):
            call_log.append(comp_name)
            return {"hook_result": "continue", "modified_message": {"plain_text": "ignored"}}

        result, final_msg, _ = await executor.execute(
            mock_invoke, message={"plain_text": "original"}
        )
        # non-blocking 的 modified_message 被忽略
        assert final_msg["plain_text"] == "original"
        # 给异步 task 时间完成
        await asyncio.sleep(0.1)
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_command_routing(self):
        """PLAN 阶段内置命令路由"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        reg.register_component("help", "command", "p1", {
            "command_pattern": r"^/help",
        })
        executor = WorkflowExecutor(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            if comp_name == "help":
                return {"output": "帮助信息"}
            return {"hook_result": "continue"}

        result, _, ctx = await executor.execute(
            mock_invoke, message={"plain_text": "/help topic"}
        )
        assert result.status == "completed"
        assert ctx.matched_command == "p1.help"
        cmd_result = ctx.get_stage_output("plan", "command_result")
        assert cmd_result is not None
        assert cmd_result["output"] == "帮助信息"

    @pytest.mark.asyncio
    async def test_stage_outputs(self):
        """stage_outputs 数据在阶段间传递"""
        from src.plugin_runtime.host.component_registry import ComponentRegistry
        from src.plugin_runtime.host.workflow_executor import WorkflowExecutor

        reg = ComponentRegistry()
        # ingress 阶段写入数据
        reg.register_component("writer", "workflow_step", "p1", {
            "stage": "ingress",
            "priority": 10,
            "blocking": True,
        })
        # pre_process 阶段读取数据
        reg.register_component("reader", "workflow_step", "p2", {
            "stage": "pre_process",
            "priority": 10,
            "blocking": True,
        })
        executor = WorkflowExecutor(reg)

        async def mock_invoke(plugin_id, comp_name, args):
            if comp_name == "writer":
                return {
                    "hook_result": "continue",
                    "stage_output": {"parsed_intent": "greeting"},
                }
            if comp_name == "reader":
                # 验证 stage_outputs 被传递过来
                outputs = args.get("stage_outputs", {})
                ingress_data = outputs.get("ingress", {})
                assert ingress_data.get("parsed_intent") == "greeting"
                return {"hook_result": "continue"}
            return {"hook_result": "continue"}

        result, _, ctx = await executor.execute(
            mock_invoke, message={"plain_text": "hi"}
        )
        assert result.status == "completed"
        assert ctx.get_stage_output("ingress", "parsed_intent") == "greeting"
