"""按 Platform IO 路由调用适配器统一头像 API。"""

from typing import Any, Dict

from src.platform_io import RouteKey, get_platform_io_manager
from src.platform_io.avatar import AvatarTargetType
from src.plugin_runtime.host.component_timeout import resolve_component_rpc_timeout_ms


async def query_adapter_avatar(
    platform: str,
    target_id: str,
    target_type: AvatarTargetType,
    *,
    account_id: str = "",
    scope: str = "",
) -> Dict[str, Any]:
    """头像获取只使用当前路由的适配器，不依赖消息内的头像字段。"""
    from src.plugin_runtime.integration import get_plugin_runtime_manager

    broker = get_platform_io_manager()
    route = RouteKey(platform=platform, account_id=account_id, scope=scope)
    # legacy/local 发送驱动没有插件 API，不能让它们遮蔽账号级头像提供方。
    drivers = [driver for driver in broker.resolve_drivers(route) if driver.descriptor.plugin_id]
    if not drivers:
        # 无平台级发送绑定时，仍允许仅接收的适配器提供头像。
        drivers = [
            driver
            for driver in broker.driver_registry.list(platform=platform)
            if driver.descriptor.plugin_id
            and (not account_id or driver.descriptor.account_id == account_id)
            and (not scope or driver.descriptor.scope == scope)
        ]
        plugin_ids = {driver.descriptor.plugin_id for driver in drivers if driver.descriptor.plugin_id}
        if len(plugin_ids) > 1:
            raise ValueError("头像路由存在多个适配器，请指定 account_id 和 scope")
    if not drivers or not drivers[0].descriptor.plugin_id:
        return {"status": "unsupported"}
    descriptor = drivers[0].descriptor
    plugin_id = descriptor.plugin_id
    runtime = get_plugin_runtime_manager()
    for supervisor in runtime.supervisors:
        entry = supervisor.api_registry.get_api(plugin_id, "adapter.avatar.get", version="1")
        if entry is None or not entry.public:
            continue
        response = await supervisor.invoke_api(
            plugin_id=plugin_id,
            component_name=entry.handler_name,
            args={
                "platform": platform,
                "target_id": target_id,
                "target_type": target_type,
                "account_id": account_id or descriptor.account_id or "",
                "scope": scope or descriptor.scope or "",
            },
            timeout_ms=resolve_component_rpc_timeout_ms(entry.timeout_ms),
        )
        if response.error:
            raise RuntimeError(f"适配器头像查询失败: {response.error}")
        payload = response.payload
        if not isinstance(payload, dict) or not payload.get("success") or not isinstance(payload.get("result"), dict):
            raise RuntimeError(f"适配器头像查询返回无效结果: {payload}")
        return payload["result"]
    return {"status": "unsupported"}
