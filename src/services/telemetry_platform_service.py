"""从已注册驱动采集接入类型，不改变平台、路由或适配器协议。"""

from typing import Dict, List

from src.platform_io import DriverKind, get_platform_io_manager


def collect_telemetry_adapters() -> List[Dict[str, str]]:
    """采集当前注册接入的去重快照；不上报账号、网关名和实例 ID。"""

    adapters = set()
    for driver in get_platform_io_manager().driver_registry.list():
        descriptor = driver.descriptor
        if descriptor.kind == DriverKind.LOCAL:
            continue
        if descriptor.kind == DriverKind.PLUGIN and descriptor.metadata.get("plugin_type") != "adapter":
            continue
        protocol = descriptor.metadata.get("protocol", "")
        if not isinstance(protocol, str):
            raise TypeError("驱动 metadata.protocol 必须是字符串")
        adapters.add((descriptor.platform, protocol))
    return [
        {"platform": platform, "protocol": protocol}
        for platform, protocol in sorted(adapters)
    ]
