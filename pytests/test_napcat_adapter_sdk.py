"""NapCat 插件与新 SDK 对接测试。"""

from pathlib import Path
from typing import Any, Dict, List

import importlib
import logging
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PROJECT_ROOT / "plugins"
SDK_ROOT = PROJECT_ROOT / "packages" / "maibot-plugin-sdk"

for import_path in (str(PLUGINS_ROOT), str(SDK_ROOT)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)


class _FakeGatewayCapability:
    """用于捕获消息网关状态上报的测试替身。"""

    def __init__(self) -> None:
        """初始化测试替身。"""

        self.calls: List[Dict[str, Any]] = []

    async def update_state(
        self,
        gateway_name: str,
        *,
        ready: bool,
        platform: str = "",
        account_id: str = "",
        scope: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """记录一次状态上报请求。

        Args:
            gateway_name: 网关组件名称。
            ready: 当前是否就绪。
            platform: 平台名称。
            account_id: 账号 ID。
            scope: 路由作用域。
            metadata: 附加元数据。

        Returns:
            bool: 始终返回 ``True``，模拟 Host 接受状态更新。
        """

        self.calls.append(
            {
                "gateway_name": gateway_name,
                "ready": ready,
                "platform": platform,
                "account_id": account_id,
                "scope": scope,
                "metadata": metadata or {},
            }
        )
        return True


def _load_napcat_sdk_symbols() -> tuple[Any, Any, Any, Any]:
    """动态加载 NapCat 插件测试所需的符号。

    Returns:
        tuple[Any, Any, Any, Any]:
            依次返回网关名常量、配置类、插件类和运行时状态管理器类。
    """

    constants_module = importlib.import_module("napcat_adapter.constants")
    config_module = importlib.import_module("napcat_adapter.config")
    plugin_module = importlib.import_module("napcat_adapter.plugin")
    runtime_state_module = importlib.import_module("napcat_adapter.runtime_state")
    return (
        constants_module.NAPCAT_GATEWAY_NAME,
        config_module.NapCatServerConfig,
        plugin_module.NapCatAdapterPlugin,
        runtime_state_module.NapCatRuntimeStateManager,
    )


def test_napcat_plugin_collects_duplex_message_gateway() -> None:
    """NapCat 插件应声明新的双工消息网关组件。"""

    napcat_gateway_name, _napcat_server_config, napcat_plugin_cls, _runtime_state_cls = _load_napcat_sdk_symbols()
    plugin = napcat_plugin_cls()
    components = plugin.get_components()
    gateway_components = [
        component
        for component in components
        if component.get("type") == "MESSAGE_GATEWAY"
    ]

    assert len(gateway_components) == 1
    gateway_component = gateway_components[0]
    assert gateway_component["name"] == napcat_gateway_name
    assert gateway_component["metadata"]["route_type"] == "duplex"
    assert gateway_component["metadata"]["platform"] == "qq"
    assert gateway_component["metadata"]["protocol"] == "napcat"


@pytest.mark.asyncio
async def test_runtime_state_reports_via_gateway_capability() -> None:
    """NapCat 运行时状态应通过新的消息网关能力上报。"""

    napcat_gateway_name, napcat_server_config_cls, _napcat_plugin_cls, runtime_state_cls = _load_napcat_sdk_symbols()
    gateway_capability = _FakeGatewayCapability()
    runtime_state_manager = runtime_state_cls(
        gateway_capability=gateway_capability,
        logger=logging.getLogger("test.napcat_adapter"),
        gateway_name=napcat_gateway_name,
    )

    connected = await runtime_state_manager.report_connected(
        "10001",
        napcat_server_config_cls(connection_id="primary"),
    )
    await runtime_state_manager.report_disconnected()

    assert connected is True
    assert gateway_capability.calls[0]["gateway_name"] == napcat_gateway_name
    assert gateway_capability.calls[0]["ready"] is True
    assert gateway_capability.calls[0]["platform"] == "qq"
    assert gateway_capability.calls[0]["account_id"] == "10001"
    assert gateway_capability.calls[0]["scope"] == "primary"
    assert gateway_capability.calls[1]["gateway_name"] == napcat_gateway_name
    assert gateway_capability.calls[1]["ready"] is False
    assert gateway_capability.calls[1]["platform"] == "qq"
