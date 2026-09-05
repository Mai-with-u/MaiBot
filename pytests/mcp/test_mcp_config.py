"""MCP 配置校验与模型转换回归测试。"""

from types import SimpleNamespace

import pytest

from src.config.official_configs import (
    MCPAuthorizationConfig,
    MCPClientConfig,
    MCPConfig,
    MCPSamplingConfig,
    MCPServerItemConfig,
)
from src.mcp_module.config import MCPClientRuntimeConfig, build_mcp_client_runtime_config
from src.mcp_module.host_llm_bridge import MCPHostLLMBridge
from src.mcp_module.models import build_tool_annotation


def test_disabled_server_allows_incomplete_draft() -> None:
    """停用服务应允许保留尚未填写完整的草稿。"""

    server = MCPServerItemConfig(
        enabled=False,
        name="",
        transport="streamable_http",
        authorization=MCPAuthorizationConfig(mode="bearer"),
    )

    assert server.enabled is False
    assert server.url == ""


def test_enabled_server_still_requires_transport_fields() -> None:
    """启用服务时应精准暴露缺少连接字段的问题。"""

    with pytest.raises(ValueError, match="必须填写 command"):
        MCPServerItemConfig(name="local", transport="stdio")

    with pytest.raises(ValueError, match="必须填写 bearer_token"):
        MCPServerItemConfig(
            name="remote",
            transport="streamable_http",
            url="https://example.test/mcp",
            authorization=MCPAuthorizationConfig(mode="bearer"),
        )


def test_duplicate_names_only_apply_to_enabled_servers() -> None:
    """停用草稿不应阻止同名启用服务保存。"""

    config = MCPConfig(
        servers=[
            MCPServerItemConfig(name="shared", command="first"),
            MCPServerItemConfig(enabled=False, name="shared"),
        ]
    )

    assert len(config.servers) == 2


def test_tool_annotation_preserves_mcp_safety_hints() -> None:
    """MCP 工具安全注解不能误当作内容 audience 注解丢弃。"""

    annotation = build_tool_annotation(
        SimpleNamespace(
            title="只读查询",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
            audience=None,
            priority=None,
            meta={"source": "server"},
        )
    )

    assert annotation is not None
    assert annotation.title == "只读查询"
    assert annotation.read_only is True
    assert annotation.destructive is False
    assert annotation.idempotent is True
    assert annotation.open_world is False
    assert annotation.metadata == {"source": "server"}


def test_runtime_config_default_sampling_task_name_is_planner() -> None:
    """运行时 dataclass 省略字段时仍默认使用 planner。"""

    assert MCPClientRuntimeConfig().sampling_task_name == "planner"


def test_build_keeps_default_sampling_task_name() -> None:
    """官方配置省略 Sampling 任务名时，运行时应保留默认 planner。"""

    runtime = build_mcp_client_runtime_config(MCPConfig())

    assert runtime.enable_sampling is False
    assert runtime.sampling_task_name == "planner"


def test_build_strips_sampling_task_name() -> None:
    """启用 Sampling 时应保留去空白后的任务名，不能回退到 planner。"""

    runtime = build_mcp_client_runtime_config(
        MCPConfig(
            client=MCPClientConfig(
                sampling=MCPSamplingConfig(enable=True, task_name="  reply  "),
            )
        )
    )

    assert runtime.enable_sampling is True
    assert runtime.sampling_task_name == "reply"


def test_build_keeps_empty_sampling_task_name_when_disabled() -> None:
    """未启用 Sampling 时，空任务名应原样暴露，不能静默改成 planner。"""

    runtime = build_mcp_client_runtime_config(
        MCPConfig(
            client=MCPClientConfig(
                sampling=MCPSamplingConfig(enable=False, task_name="   "),
            )
        )
    )

    assert runtime.enable_sampling is False
    assert runtime.sampling_task_name == ""


def test_build_rejects_empty_sampling_task_name_when_enabled() -> None:
    """启用 Sampling 时，空任务名必须在配置转换阶段完整暴露。"""

    with pytest.raises(ValueError, match="MCP Sampling 已启用，但模型任务名为空"):
        build_mcp_client_runtime_config(
            MCPConfig(
                client=MCPClientConfig(
                    sampling=MCPSamplingConfig(enable=True, task_name=""),
                )
            )
        )

    with pytest.raises(ValueError, match="MCP Sampling 已启用，但模型任务名为空"):
        build_mcp_client_runtime_config(
            MCPConfig(
                client=MCPClientConfig(
                    sampling=MCPSamplingConfig(enable=True, task_name="  "),
                )
            )
        )


def test_host_llm_bridge_rejects_empty_sampling_task_name() -> None:
    """宿主桥接层不能把空任务名静默回退到 planner。"""

    with pytest.raises(ValueError, match="MCP Sampling 任务名不能为空"):
        MCPHostLLMBridge("")

    with pytest.raises(ValueError, match="MCP Sampling 任务名不能为空"):
        MCPHostLLMBridge("   ")


def test_host_llm_bridge_keeps_explicit_and_default_task_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用方省略参数时默认 planner；显式任务名只做 strip。"""

    captured = {}

    class _FakeLLMServiceClient:
        def __init__(self, task_name: str, request_type: str = "") -> None:
            captured["task_name"] = task_name
            captured["request_type"] = request_type

    monkeypatch.setattr(
        "src.mcp_module.host_llm_bridge.LLMServiceClient",
        _FakeLLMServiceClient,
    )

    default_bridge = MCPHostLLMBridge()
    assert default_bridge._sampling_task_name == "planner"
    assert captured["task_name"] == "planner"
    assert captured["request_type"] == "mcp_sampling"

    custom_bridge = MCPHostLLMBridge("  reply  ")
    assert custom_bridge._sampling_task_name == "reply"
    assert captured["task_name"] == "reply"
