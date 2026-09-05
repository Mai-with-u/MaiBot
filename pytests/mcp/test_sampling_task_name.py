"""MCP Sampling 任务名处理回归测试。

空或空白任务名必须完整暴露，不能静默回退成 planner；显式 planner 仍可使用。
"""

from typing import Dict, List

import pytest

official_configs_module = pytest.importorskip("src.config.official_configs")
config_module = pytest.importorskip("src.mcp_module.config")
host_llm_bridge_module = pytest.importorskip("src.mcp_module.host_llm_bridge")

MCPClientConfig = official_configs_module.MCPClientConfig
MCPConfig = official_configs_module.MCPConfig
MCPSamplingConfig = official_configs_module.MCPSamplingConfig
build_mcp_client_runtime_config = config_module.build_mcp_client_runtime_config
MCPHostLLMBridge = host_llm_bridge_module.MCPHostLLMBridge


def _install_fake_llm_client(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, str]]:
    """拦截宿主桥接层的大模型客户端构造，记录实际任务名。"""

    captured: List[Dict[str, str]] = []

    class _FakeLLMServiceClient:
        def __init__(self, task_name: str, request_type: str = "") -> None:
            captured.append({"task_name": task_name, "request_type": request_type})

    monkeypatch.setattr(host_llm_bridge_module, "LLMServiceClient", _FakeLLMServiceClient)
    return captured


@pytest.mark.parametrize("task_name", ["", " ", "   ", "\t", "\n"])
def test_empty_or_whitespace_sampling_task_name_does_not_become_planner(
    task_name: str,
) -> None:
    """空或空白 Sampling 任务名必须报错，不能静默变成 planner。"""

    with pytest.raises(ValueError, match="MCP Sampling 任务名不能为空"):
        MCPHostLLMBridge(task_name)


def test_explicit_planner_sampling_task_name_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """省略参数、显式 planner 或带空白的 planner 都应继续使用 planner。"""

    captured = _install_fake_llm_client(monkeypatch)

    default_bridge = MCPHostLLMBridge()
    assert default_bridge._sampling_task_name == "planner"
    assert captured[-1]["task_name"] == "planner"
    assert captured[-1]["request_type"] == "mcp_sampling"

    explicit_bridge = MCPHostLLMBridge("planner")
    assert explicit_bridge._sampling_task_name == "planner"
    assert captured[-1]["task_name"] == "planner"

    padded_bridge = MCPHostLLMBridge("  planner  ")
    assert padded_bridge._sampling_task_name == "planner"
    assert captured[-1]["task_name"] == "planner"


@pytest.mark.parametrize("task_name", ["", "   "])
def test_enabled_empty_sampling_task_name_does_not_become_planner(task_name: str) -> None:
    """启用 Sampling 时，空任务名必须在配置转换阶段暴露，不能变成 planner。"""

    with pytest.raises(ValueError, match="MCP Sampling 已启用，但模型任务名为空"):
        build_mcp_client_runtime_config(
            MCPConfig(
                client=MCPClientConfig(
                    sampling=MCPSamplingConfig(enable=True, task_name=task_name),
                )
            )
        )


def test_runtime_config_keeps_explicit_planner_task_name() -> None:
    """启用 Sampling 并显式指定 planner 时，运行时应保留去空白后的任务名。"""

    runtime = build_mcp_client_runtime_config(
        MCPConfig(
            client=MCPClientConfig(
                sampling=MCPSamplingConfig(enable=True, task_name="planner"),
            )
        )
    )
    padded_runtime = build_mcp_client_runtime_config(
        MCPConfig(
            client=MCPClientConfig(
                sampling=MCPSamplingConfig(enable=True, task_name="  planner  "),
            )
        )
    )

    assert runtime.enable_sampling is True
    assert runtime.sampling_task_name == "planner"
    assert padded_runtime.sampling_task_name == "planner"


def test_disabled_empty_sampling_task_name_does_not_become_planner() -> None:
    """未启用 Sampling 时，空白任务名应保持为空，不能静默改成 planner。"""

    runtime = build_mcp_client_runtime_config(
        MCPConfig(
            client=MCPClientConfig(
                sampling=MCPSamplingConfig(enable=False, task_name="   "),
            )
        )
    )

    assert runtime.enable_sampling is False
    assert runtime.sampling_task_name == ""
