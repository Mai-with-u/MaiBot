"""MCP 配置校验与模型转换回归测试。"""

from types import SimpleNamespace

import pytest

from src.config.official_configs import MCPAuthorizationConfig, MCPConfig, MCPServerItemConfig
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
