"""插件配置契约的 Runner 侧回归测试。"""

import pytest

from src.plugin_runtime.runner.runner_main import PluginConfigVersionError, PluginRunner


class _InvalidConfigPlugin:
    """模拟 SDK 默认配置构建失败的插件。"""

    def get_default_config(self) -> dict[str, object]:
        """抛出配置版本契约错误。"""

        raise PluginConfigVersionError("插件配置文件缺少 [plugin] 配置节")


def test_runner_does_not_swallow_plugin_config_contract_error() -> None:
    """Runner 获取默认配置时应立即暴露配置契约错误。"""

    with pytest.raises(PluginConfigVersionError, match=r"\[plugin\]"):
        PluginRunner._get_plugin_default_config(_InvalidConfigPlugin())
