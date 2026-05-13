"""内置命名 Hook 目录注册器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import List

from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry


HookSpecRegistrar = Callable[[HookSpecRegistry], List[HookSpec]]
"""单个业务模块向注册中心写入 Hook 规格的注册器签名。"""


def _get_builtin_hook_spec_registrars() -> List[HookSpecRegistrar]:
    """返回当前内置 Hook 规格注册器列表。

    Returns:
        List[HookSpecRegistrar]: 已启用的内置 Hook 注册器列表。
    """

    from src.chat.message_receive.bot import register_chat_hook_specs
    from src.emoji_system.emoji_manager import register_emoji_hook_specs
    from src.learners.expression_learner import register_expression_hook_specs
    from src.learners.jargon_miner import register_jargon_hook_specs
    from src.maisaka.chat_loop_service import register_maisaka_hook_specs
    from src.services.memory_flow_service import register_memory_automation_hook_specs
    from src.services.send_service import register_send_service_hook_specs

    return [
        register_chat_hook_specs,
        register_emoji_hook_specs,
        register_jargon_hook_specs,
        register_expression_hook_specs,
        register_memory_automation_hook_specs,
        register_send_service_hook_specs,
        register_maisaka_hook_specs,
    ]


def register_builtin_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """向注册中心写入全部内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 本次完成注册后的全部内置 Hook 规格。
    """

    registered_specs: List[HookSpec] = []
    for registrar in _get_builtin_hook_spec_registrars():
        registered_specs.extend(registrar(registry))
    return registered_specs
