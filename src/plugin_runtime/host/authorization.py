"""授权管理器

负责管理插件的能力授权以及校验
每个插件在 manifest 中声明能力需求，Host 启动时签发能力令牌。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class CapabilityPermissionToken:
    """能力令牌"""

    plugin_id: str
    capabilities: Set[str] = field(default_factory=set)


class AuthorizationManager:
    """授权管理器

    管理所有插件的能力令牌，提供授权校验。
    """

    def __init__(self) -> None:
        self._permission_tokens: Dict[str, CapabilityPermissionToken] = {}

    def register_plugin(self, plugin_id: str, capabilities: List[str]) -> CapabilityPermissionToken:
        """为插件签发能力令牌"""
        token = CapabilityPermissionToken(plugin_id=plugin_id, capabilities=set(capabilities))
        self._permission_tokens[plugin_id] = token
        return token

    def revoke_permission_token(self, plugin_id: str):
        """移除插件的能力令牌。"""
        self._permission_tokens.pop(plugin_id, None)

    def clear(self) -> None:
        """清空所有能力令牌。"""
        self._permission_tokens.clear()

    def check_capability(self, plugin_id: str, capability: str) -> Tuple[bool, str]:
        # sourcery skip: assign-if-exp, reintroduce-else, swap-if-else-branches, use-named-expression
        """检查插件是否有权调用某项能力

        Returns:
            return (bool, str): (是否有此能力, 原因)
        """
        token = self._permission_tokens.get(plugin_id)
        if not token:
            return False, f"插件 {plugin_id} 未注册能力令牌"
        if capability not in token.capabilities:
            return False, f"插件 {plugin_id} 未获授权能力: {capability}"
        return True, ""

    def get_token(self, plugin_id: str) -> Optional[CapabilityPermissionToken]:
        """获取插件的能力令牌"""
        return self._permission_tokens.get(plugin_id)

    def list_plugins(self) -> List[str]:
        """列出所有已注册的插件"""
        return list(self._permission_tokens.keys())
