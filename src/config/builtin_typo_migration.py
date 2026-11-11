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


def _legacy_keyword_rules(legacy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """合并旧的关键词规则和正则规则，保持原有匹配顺序，并丢弃无效规则。"""
    rules: list[dict[str, Any]] = []
    for key in ("keyword_rules", "regex_rules"):
        items = legacy.get(key) or []
        if not isinstance(items, list):
            raise TypeError(f"旧关键词反应配置 keyword_reaction.{key} 必须是数组")
        for item in items:
            if not isinstance(item, Mapping):
                continue
            keywords = [str(value) for value in item.get("keywords") or [] if str(value).strip()]
            patterns = [str(value) for value in item.get("regex") or [] if str(value).strip()]
            reaction = str(item.get("reaction") or "").strip()
            if reaction and (keywords or patterns):
                rules.append({"keywords": keywords, "regex": patterns, "reaction": reaction})
    return rules


def migrate_builtin_keyword_reaction_config(legacy: Mapping[str, Any]) -> None:
    """把主配置里的旧关键词反应写入内置插件；插件已有该配置时不覆盖。"""
    rules = _legacy_keyword_rules(legacy)
    if not rules:
        return
    target = resolve_plugin_config_path(builtin_plugin_root() / "chinese_typo")
    if target.exists():
        document = tomlkit.parse(target.read_text(encoding="utf-8"))
        if "keyword_reaction" in document:
            return
    else:
        document = tomlkit.document()
        document["plugin"] = {"enabled": True, "config_version": "1.0.0"}
    document["keyword_reaction"] = {"enabled": True, "rules": rules}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(tomlkit.dumps(document))
        os.replace(temp_path, target)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
