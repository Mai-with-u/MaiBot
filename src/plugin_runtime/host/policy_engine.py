"""策略引擎

负责能力授权校验。
每个插件在 manifest 中声明能力需求，Host 启动时签发能力令牌。
"""

from dataclasses import dataclass, field


@dataclass
class CapabilityToken:
    """能力令牌"""
    plugin_id: str
    generation: int
    capabilities: set[str] = field(default_factory=set)


class PolicyEngine:
    """策略引擎

    管理所有插件的能力令牌，提供授权校验。
    """

    def __init__(self):
        self._tokens: dict[str, CapabilityToken] = {}

    def register_plugin(
        self,
        plugin_id: str,
        generation: int,
        capabilities: list[str],
    ) -> CapabilityToken:
        """为插件签发能力令牌"""
        token = CapabilityToken(
            plugin_id=plugin_id,
            generation=generation,
            capabilities=set(capabilities),
        )
        self._tokens[plugin_id] = token
        return token

    def revoke_plugin(self, plugin_id: str) -> None:
        """撤销插件的能力令牌"""
        self._tokens.pop(plugin_id, None)

    def check_capability(self, plugin_id: str, capability: str) -> tuple[bool, str]:
        """检查插件是否有权调用某项能力

        Returns:
            (allowed, reason)
        """
        token = self._tokens.get(plugin_id)
        if token is None:
            return False, f"插件 {plugin_id} 未注册能力令牌"

        if capability not in token.capabilities:
            return False, f"插件 {plugin_id} 未获授权能力: {capability}"

        return True, ""

    def get_token(self, plugin_id: str) -> CapabilityToken | None:
        """获取插件的能力令牌"""
        return self._tokens.get(plugin_id)

    def list_plugins(self) -> list[str]:
        """列出所有已注册的插件"""
        return list(self._tokens.keys())
