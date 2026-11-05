"""在旧错字配置从主配置移除前，将其保存到内置插件。"""

from pydantic import TypeAdapter
from tempfile import mkstemp
from typing import Any, Mapping

import os
import tomlkit

from src.common.paths import builtin_plugin_root, resolve_plugin_config_path


def migrate_builtin_typo_config(legacy: Mapping[str, Any]) -> None:
    """首次迁移保留现有插件配置，并允许主配置写入失败后安全重试。"""
    target = resolve_plugin_config_path(builtin_plugin_root() / "chinese_typo")
    if target.exists():
        return
    enabled = TypeAdapter(bool).validate_python(legacy.get("enable", True))
    settings = {key: value for key, value in legacy.items() if key != "enable"}
    content = tomlkit.dumps({"plugin": {"enabled": enabled, "config_version": "1.0.0"}, "typo": settings})
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(content)
        try:
            # 只在目标不存在时发布完整文件，避免覆盖其他启动进程已写入的配置。
            os.link(temp_path, target)
        except FileExistsError:
            pass
    finally:
        os.unlink(temp_path)
