"""核心组件查询层与插件运行时聚合测试。"""

from types import SimpleNamespace
from typing import Any

import pytest

import src.plugin_runtime.integration as integration_module

from src.core.types import ActionInfo, ToolInfo
from src.plugin_runtime.component_query import component_query_service
from src.plugin_runtime.host.supervisor import PluginSupervisor


class _FakeRuntimeManager:
    """测试用插件运行时管理器。"""

    def __init__(self, supervisor: PluginSupervisor, plugin_id: str, plugin_config: dict[str, Any]) -> None:
        """初始化测试用运行时管理器。

        Args:
            supervisor: 持有测试组件的监督器。
            plugin_id: 目标插件 ID。
            plugin_config: 需要返回的插件配置。
        """

        self.supervisors = [supervisor]
        self._plugin_id = plugin_id
        self._plugin_config = plugin_config

    def _get_supervisor_for_plugin(self, plugin_id: str) -> PluginSupervisor | None:
        """按插件 ID 返回对应监督器。

        Args:
            plugin_id: 目标插件 ID。

        Returns:
            PluginSupervisor | None: 命中时返回监督器。
        """

        return self.supervisors[0] if plugin_id == self._plugin_id else None

    def _load_plugin_config_for_supervisor(self, supervisor: Any, plugin_id: str) -> dict[str, Any]:
        """返回测试配置。

        Args:
            supervisor: 监督器实例。
            plugin_id: 目标插件 ID。

        Returns:
            dict[str, Any]: 测试配置内容。
        """

        del supervisor
        if plugin_id != self._plugin_id:
            return {}
        return dict(self._plugin_config)


def _install_runtime_manager(
    monkeypatch: pytest.MonkeyPatch,
    supervisor: PluginSupervisor,
    plugin_id: str,
    plugin_config: dict[str, Any] | None = None,
) -> None:
    """为测试安装假的运行时管理器。

    Args:
        monkeypatch: pytest monkeypatch 对象。
        supervisor: 持有测试组件的监督器。
        plugin_id: 测试插件 ID。
        plugin_config: 可选的测试配置内容。
    """

    fake_manager = _FakeRuntimeManager(supervisor, plugin_id, plugin_config or {"enabled": True})
    monkeypatch.setattr(integration_module, "get_plugin_runtime_manager", lambda: fake_manager)


@pytest.mark.asyncio
async def test_core_component_registry_reads_runtime_action_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心查询层应直接读取运行时 Action，并返回 RPC 执行闭包。"""

    plugin_id = "runtime_action_bridge_plugin"
    action_name = "runtime_action_bridge_test"
    supervisor = PluginSupervisor(plugin_dirs=[])
    captured: dict[str, Any] = {}

    supervisor.component_registry.register_component(
        name=action_name,
        component_type="ACTION",
        plugin_id=plugin_id,
        metadata={
            "description": "发送一个测试回复",
            "enabled": True,
            "activation_type": "keyword",
            "activation_probability": 0.25,
            "activation_keywords": ["测试", "hello"],
            "action_parameters": {"target": "目标对象"},
            "action_require": ["需要发送回复时使用"],
            "associated_types": ["text"],
            "parallel_action": True,
        },
    )
    _install_runtime_manager(monkeypatch, supervisor, plugin_id, {"enabled": True, "mode": "test"})

    async def fake_invoke_plugin(
        method: str,
        plugin_id: str,
        component_name: str,
        args: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """模拟动作 RPC 调用。"""

        captured["method"] = method
        captured["plugin_id"] = plugin_id
        captured["component_name"] = component_name
        captured["args"] = args or {}
        captured["timeout_ms"] = timeout_ms
        return SimpleNamespace(payload={"success": True, "result": (True, "runtime action executed")})

    monkeypatch.setattr(supervisor, "invoke_plugin", fake_invoke_plugin)

    action_info = component_query_service.get_action_info(action_name)
    assert isinstance(action_info, ActionInfo)
    assert action_info.plugin_name == plugin_id
    assert action_info.description == "发送一个测试回复"
    assert action_info.activation_keywords == ["测试", "hello"]
    assert action_info.random_activation_probability == 0.25
    assert action_info.parallel_action is True
    assert action_name in component_query_service.get_default_actions()
    assert component_query_service.get_plugin_config(plugin_id) == {"enabled": True, "mode": "test"}

    executor = component_query_service.get_action_executor(action_name)
    assert executor is not None

    success, reason = await executor(
        action_data={"target": "MaiBot"},
        action_reasoning="当前适合使用这个动作",
        cycle_timers={"planner": 0.1},
        thinking_id="tid-1",
        chat_stream=SimpleNamespace(session_id="stream-1"),
        log_prefix="[test]",
        shutting_down=False,
        plugin_config={"enabled": True},
    )

    assert success is True
    assert reason == "runtime action executed"
    assert captured["method"] == "plugin.invoke_action"
    assert captured["plugin_id"] == plugin_id
    assert captured["component_name"] == action_name
    assert captured["args"]["stream_id"] == "stream-1"
    assert captured["args"]["chat_id"] == "stream-1"
    assert captured["args"]["reasoning"] == "当前适合使用这个动作"
    assert captured["args"]["target"] == "MaiBot"
    assert captured["args"]["action_data"] == {"target": "MaiBot"}


@pytest.mark.asyncio
async def test_core_component_registry_reads_runtime_command_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心查询层应直接使用运行时命令匹配与执行闭包。"""

    plugin_id = "runtime_command_bridge_plugin"
    command_name = "runtime_command_bridge_test"
    supervisor = PluginSupervisor(plugin_dirs=[])
    captured: dict[str, Any] = {}

    supervisor.component_registry.register_component(
        name=command_name,
        component_type="COMMAND",
        plugin_id=plugin_id,
        metadata={
            "description": "测试命令",
            "enabled": True,
            "command_pattern": r"^/test(?:\s+.+)?$",
            "aliases": ["/hello"],
            "intercept_message_level": 1,
        },
    )
    _install_runtime_manager(monkeypatch, supervisor, plugin_id, {"mode": "command"})

    async def fake_invoke_plugin(
        method: str,
        plugin_id: str,
        component_name: str,
        args: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """模拟命令 RPC 调用。"""

        captured["method"] = method
        captured["plugin_id"] = plugin_id
        captured["component_name"] = component_name
        captured["args"] = args or {}
        captured["timeout_ms"] = timeout_ms
        return SimpleNamespace(payload={"success": True, "result": (True, "command ok", True)})

    monkeypatch.setattr(supervisor, "invoke_plugin", fake_invoke_plugin)

    matched = component_query_service.find_command_by_text("/test hello")
    assert matched is not None
    command_executor, matched_groups, command_info = matched

    assert matched_groups == {}
    assert command_info.plugin_name == plugin_id
    assert command_info.command_pattern == r"^/test(?:\s+.+)?$"

    success, response_text, intercept = await command_executor(
        message=SimpleNamespace(processed_plain_text="/test hello", session_id="stream-2"),
        plugin_config={"mode": "command"},
        matched_groups=matched_groups,
    )

    assert success is True
    assert response_text == "command ok"
    assert intercept is True
    assert captured["method"] == "plugin.invoke_command"
    assert captured["plugin_id"] == plugin_id
    assert captured["component_name"] == command_name
    assert captured["args"]["text"] == "/test hello"
    assert captured["args"]["stream_id"] == "stream-2"
    assert captured["args"]["plugin_config"] == {"mode": "command"}


@pytest.mark.asyncio
async def test_core_component_registry_reads_runtime_tools_and_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心查询层应直接读取运行时 Tool，并返回 RPC 执行闭包。"""

    plugin_id = "runtime_tool_bridge_plugin"
    tool_name = "runtime_tool_bridge_test"
    supervisor = PluginSupervisor(plugin_dirs=[])

    supervisor.component_registry.register_component(
        name=tool_name,
        component_type="TOOL",
        plugin_id=plugin_id,
        metadata={
            "description": "测试工具",
            "enabled": True,
            "parameters": [
                {
                    "name": "query",
                    "param_type": "string",
                    "description": "查询词",
                    "required": True,
                }
            ],
        },
    )
    _install_runtime_manager(monkeypatch, supervisor, plugin_id)

    async def fake_invoke_plugin(
        method: str,
        plugin_id: str,
        component_name: str,
        args: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """模拟工具 RPC 调用。"""

        del timeout_ms
        assert method == "plugin.invoke_tool"
        assert plugin_id == "runtime_tool_bridge_plugin"
        assert component_name == "runtime_tool_bridge_test"
        assert args == {"query": "MaiBot"}
        return SimpleNamespace(payload={"success": True, "result": {"content": "tool ok"}})

    monkeypatch.setattr(supervisor, "invoke_plugin", fake_invoke_plugin)

    tool_info = component_query_service.get_tool_info(tool_name)
    assert isinstance(tool_info, ToolInfo)
    assert tool_info.tool_description == "测试工具"
    assert tool_name in component_query_service.get_llm_available_tools()

    executor = component_query_service.get_tool_executor(tool_name)
    assert executor is not None
    assert await executor({"query": "MaiBot"}) == {"content": "tool ok"}
