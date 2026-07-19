"""插件和组件管理 — 新 SDK 版本

通过 /pm 命令管理插件和组件的生命周期。
"""

from typing import Any, ClassVar, Dict, List

import json
import shlex

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase

from src.core.local_operator import has_plugin_management_permission


_VALID_COMPONENT_TYPES = ("tool", "command", "event_handler")
_PLUGIN_MANAGEMENT_ID = "builtin.plugin-management"


_COMMAND_ALIAS_TARGETS: dict[str, str] = {
    "help": "/pm help",
    "plugin": "/pm plugin",
    "plugin.help": "/pm plugin help",
    "plugin.list": "/pm plugin list",
    "plugin.list_enabled": "/pm plugin list_enabled",
    "plugin.load": "/pm plugin load",
    "plugin.unload": "/pm plugin unload",
    "plugin.reload": "/pm plugin reload",
    "component": "/pm component",
    "component.help": "/pm component help",
    "component.list": "/pm component list",
    "component.enable": "/pm component enable",
    "component.disable": "/pm component disable",
    "config": "/pm config",
    "config.help": "/pm config help",
    "config.get": "/pm config get",
    "config.set": "/pm config set",
}


HELP_ALL = (
    "管理命令帮助\n"
    "/pm help 管理命令提示\n"
    "/pm plugin 插件管理命令\n"
    "/pm component 组件管理命令\n"
    "/pm alias 管理命令别名\n"
    "使用 /pm plugin help、/pm component help、/pm alias help 或 /pm config help 获取具体帮助"
)
HELP_PLUGIN = (
    "插件管理命令帮助\n"
    "/pm plugin help 插件管理命令提示\n"
    "/pm plugin list 列出所有注册的插件\n"
    "/pm plugin list_enabled 列出所有加载（启用）的插件\n"
    "/pm plugin load <plugin_name> 加载指定插件\n"
    "/pm plugin unload <plugin_name> 卸载指定插件\n"
    "/pm plugin reload <plugin_name> 重新加载指定插件\n"
)
HELP_CONFIG = (
    "插件配置命令帮助\n"
    "/pm config help 插件配置命令提示\n"
    "/pm config get <plugin_name> [key] 读取指定插件配置或点分隔配置项\n"
    "/pm config set <plugin_name> <key> <value_json> 修改指定插件配置项\n"
    "  - <key> 使用点分隔路径，例如 plugin.enabled\n"
    "  - <value_json> 优先按 JSON 解析，例如 true、123、[\"文本\"]；解析失败时按字符串保存\n"
    "  - 指令别名建议使用 /pm alias add /别名 /pm plugin list"
)
HELP_COMPONENT = (
    "组件管理命令帮助\n"
    "/pm component help 组件管理命令提示\n"
    "/pm component list 列出所有注册的组件\n"
    "/pm component list enabled <可选: type> 列出所有启用的组件\n"
    "/pm component list disabled <可选: type> 列出所有禁用的组件\n"
    "  - <type> 可选项: local，代表当前聊天中的；global，代表全局的\n"
    "  - <type> 不填时为 global\n"
    "/pm component list type <component_type> 列出已经注册的指定类型的组件\n"
    "/pm component enable global <component_name> <component_type> 全局启用组件\n"
    "/pm component enable local <component_name> <component_type> 本聊天启用组件\n"
    "/pm component disable global <component_name> <component_type> 全局禁用组件\n"
    "/pm component disable local <component_name> <component_type> 本聊天禁用组件\n"
    "  - <component_type> 可选项: tool, command, event_handler\n"
)
HELP_ALIAS = (
    "指令别名命令帮助\n"
    "/pm alias list 查看已配置的指令别名\n"
    "/pm alias add <alias> <pm_command> 添加别名，例如 /pm alias add /插件列表 /pm plugin list\n"
    "/pm alias remove <alias> 删除别名，例如 /pm alias remove /插件列表\n"
    "  - <alias> 必须以 / 开头，且不能是 /pm\n"
    "  - <pm_command> 必须以 /pm 开头；如需给 /pm 本身起别名，可使用 /pm alias add /插件管理 /pm"
)


class PluginSectionConfig(PluginConfigBase):
    """插件自身配置。"""

    __ui_label__: ClassVar[str] = "基础设置"
    config_version: str = Field(default="1.2.0", description="配置版本号")
    enabled: bool = Field(default=True, description="是否启用插件管理内置插件")


class AliasConfig(PluginConfigBase):
    """插件管理命令别名配置。"""

    __ui_label__: ClassVar[str] = "指令别名"
    management_prefixes: List[str] = Field(default_factory=lambda: ["/插件管理"], description="/pm 主命令前缀别名")
    shortcuts: Dict[str, str] = Field(
        default_factory=dict,
        description="快捷别名映射，键为别名，值为目标 /pm 命令，例如 {'/插件列表': '/pm plugin list'}",
    )
    command_aliases: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="兼容旧版的子命令别名映射；新配置建议使用 shortcuts",
    )


class PluginManagementConfig(PluginConfigBase):
    """插件管理插件配置模型。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig, description="插件基础配置")
    aliases: AliasConfig = Field(default_factory=AliasConfig, description="指令别名配置")


class PluginManagementPlugin(MaiBotPlugin):
    """插件和组件管理插件"""

    config_model = PluginManagementConfig

    async def on_load(self) -> None:
        """处理插件加载。"""

    async def on_unload(self) -> None:
        """处理插件卸载。"""

    @Command(
        "management",
        description="管理插件和组件的生命周期",
        pattern=r"(?P<manage_command>^/pm(?:\s+.+)?\s*$)",
    )
    async def handle_management(
        self, stream_id: str = "", platform: str = "", user_id: str = "", matched_groups: dict | None = None, **kwargs
    ):
        """处理 /pm 命令"""
        # 权限检查
        permission_result = await self.ctx.config.get("plugin.permission")
        permission_list = permission_result if isinstance(permission_result, list) else []
        is_local_operator = kwargs.get("is_local_operator") is True
        if not has_plugin_management_permission(
            platform,
            user_id,
            permission_list,
            local_operator=is_local_operator,
        ):
            await self.ctx.send.text("你没有权限使用插件管理命令", stream_id)
            return False, "没有权限", True

        if not stream_id:
            return False, "无法获取聊天流信息", True

        raw_text = str(kwargs.get("text", "") or "").strip()
        raw_command = (matched_groups or {}).get("manage_command", "").strip()
        raw_command = self._resolve_alias_command(raw_command or raw_text)
        parts = self._split_command(raw_command) if raw_command else ["/pm"]
        n = len(parts)

        # /pm
        if n == 1:
            await self.ctx.send.text(HELP_ALL, stream_id)
            return True, "帮助已发送", True

        # /pm <sub>
        if n == 2:
            sub = parts[1]
            if sub == "plugin":
                await self.ctx.send.text(HELP_PLUGIN, stream_id)
            elif sub == "component":
                await self.ctx.send.text(HELP_COMPONENT, stream_id)
            elif sub == "alias":
                await self.ctx.send.text(HELP_ALIAS, stream_id)
            elif sub == "config":
                await self.ctx.send.text(HELP_CONFIG, stream_id)
            elif sub == "help":
                await self.ctx.send.text(HELP_ALL, stream_id)
            else:
                await self.ctx.send.text("插件管理命令不合法", stream_id)
                return False, "命令不合法", True
            return True, "帮助已发送", True

        # /pm plugin <action> / /pm component <action>
        if n == 3:
            if parts[1] == "plugin":
                await self._handle_plugin_3(parts[2], stream_id)
            elif parts[1] == "config":
                if parts[2] == "help":
                    await self.ctx.send.text(HELP_CONFIG, stream_id)
                else:
                    await self.ctx.send.text("插件管理命令不合法", stream_id)
                    return False, "命令不合法", True
            elif parts[1] == "alias":
                if parts[2] == "help":
                    await self.ctx.send.text(HELP_ALIAS, stream_id)
                elif parts[2] == "list":
                    await self._handle_alias_list(stream_id)
                else:
                    await self.ctx.send.text("插件管理命令不合法", stream_id)
                    return False, "命令不合法", True
            elif parts[1] == "component":
                if parts[2] == "list":
                    await self._list_all_components(stream_id)
                elif parts[2] == "help":
                    await self.ctx.send.text(HELP_COMPONENT, stream_id)
                else:
                    await self.ctx.send.text("插件管理命令不合法", stream_id)
                    return False, "命令不合法", True
            else:
                await self.ctx.send.text("插件管理命令不合法", stream_id)
                return False, "命令不合法", True
            return True, "命令执行完成", True

        if n == 4:
            if parts[1] == "plugin":
                await self._handle_plugin_4(parts[2], parts[3], stream_id)
            elif parts[1] == "config":
                await self._handle_config_get(parts[2], parts[3], "", stream_id)
            elif parts[1] == "alias" and parts[2] == "remove":
                await self._handle_alias_remove(parts[3], stream_id)
            elif parts[1] == "component":
                if parts[2] == "list":
                    await self._handle_component_list_4(parts[3], stream_id)
                else:
                    await self.ctx.send.text("插件管理命令不合法", stream_id)
                    return False, "命令不合法", True
            else:
                await self.ctx.send.text("插件管理命令不合法", stream_id)
                return False, "命令不合法", True
            return True, "命令执行完成", True

        if n == 5:
            if parts[1] == "alias" and parts[2] == "add":
                await self._handle_alias_add(parts[3], parts[4], stream_id)
                return True, "命令执行完成", True
            if parts[1] == "component" and parts[2] == "list":
                await self._handle_component_list_5(parts[3], parts[4], stream_id)
                return True, "命令执行完成", True
            if parts[1] == "config" and parts[2] == "get":
                await self._handle_config_get(parts[2], parts[3], parts[4], stream_id)
                return True, "命令执行完成", True
            await self.ctx.send.text("插件管理命令不合法", stream_id)
            return False, "命令不合法", True

        if n >= 6:
            if parts[1] == "alias" and parts[2] == "add":
                await self._handle_alias_add(parts[3], " ".join(parts[4:]), stream_id)
                return True, "命令执行完成", True
            if parts[1] == "component" and n == 6:
                await self._handle_component_toggle(parts[2], parts[3], parts[4], parts[5], stream_id)
                return True, "命令执行完成", True
            if parts[1] == "config" and parts[2] == "set" and n >= 6:
                await self._handle_config_set(parts[3], parts[4], " ".join(parts[5:]), stream_id)
                return True, "命令执行完成", True
            await self.ctx.send.text("插件管理命令不合法", stream_id)
            return False, "命令不合法", True

        await self.ctx.send.text("插件管理命令不合法", stream_id)
        return False, "命令不合法", True

    # ------ plugin 子命令 ------

    async def _handle_plugin_3(self, action: str, stream_id: str):
        match action:
            case "help":
                await self.ctx.send.text(HELP_PLUGIN, stream_id)
            case "list":
                result = await self.ctx.component.list_registered_plugins()
                plugins = result if isinstance(result, list) else []
                await self.ctx.send.text(f"已注册的插件: {', '.join(plugins) if plugins else '无'}", stream_id)
            case "list_enabled":
                result = await self.ctx.component.list_loaded_plugins()
                plugins = result if isinstance(result, list) else []
                await self.ctx.send.text(f"已加载的插件: {', '.join(plugins) if plugins else '无'}", stream_id)
            case _:
                await self.ctx.send.text("插件管理命令不合法", stream_id)

    async def _handle_plugin_4(self, action: str, name: str, stream_id: str):
        match action:
            case "load":
                result = await self.ctx.component.load_plugin(name)
                ok = result.get("success", False) if isinstance(result, dict) else bool(result)
                msg = f"插件加载成功: {name}" if ok else f"插件加载失败: {name}"
                await self.ctx.send.text(msg, stream_id)
            case "unload":
                result = await self.ctx.component.unload_plugin(name)
                ok = result.get("success", False) if isinstance(result, dict) else bool(result)
                msg = f"插件卸载成功: {name}" if ok else f"插件卸载失败: {name}"
                await self.ctx.send.text(msg, stream_id)
            case "reload":
                result = await self.ctx.component.reload_plugin(name)
                ok = result.get("success", False) if isinstance(result, dict) else bool(result)
                msg = f"插件重新加载成功: {name}" if ok else f"插件重新加载失败: {name}"
                await self.ctx.send.text(msg, stream_id)
            case _:
                await self.ctx.send.text("插件管理命令不合法", stream_id)

    # ------ config 子命令 ------

    async def _handle_config_get(self, action: str, plugin_name: str, key: str, stream_id: str):
        if action != "get":
            await self.ctx.send.text("插件管理命令不合法", stream_id)
            return
        config = await self.ctx.config.get_plugin(plugin_name)
        if not config:
            await self.ctx.send.text(f"未读取到插件配置: {plugin_name}", stream_id)
            return
        value = self._get_nested_value(config, key) if key else config
        display_key = f".{key}" if key else ""
        value_text = json.dumps(value, ensure_ascii=False)
        await self.ctx.send.text(f"{plugin_name}{display_key} = {value_text}", stream_id)

    async def _handle_config_set(self, plugin_name: str, key: str, raw_value: str, stream_id: str):
        if not key.strip():
            await self.ctx.send.text("配置键不能为空", stream_id)
            return
        value = self._parse_config_value(raw_value)
        result = await self.ctx.call_capability(
            "component.update_plugin_config",
            plugin_name=plugin_name,
            key=key,
            value=value,
        )
        ok = result.get("success", False) if isinstance(result, dict) else bool(result)
        if ok:
            await self.ctx.send.text(f"插件配置已更新: {plugin_name}.{key}", stream_id)
            return
        error = result.get("error", "未知错误") if isinstance(result, dict) else "未知错误"
        await self.ctx.send.text(f"插件配置更新失败: {error}", stream_id)

    # ------ alias 子命令 ------

    async def _handle_alias_list(self, stream_id: str):
        config = await self.ctx.config.get_plugin(_PLUGIN_MANAGEMENT_ID)
        mapping = self._build_alias_mapping_from_config(config if isinstance(config, dict) else {})
        if not mapping:
            await self.ctx.send.text("未配置指令别名", stream_id)
            return
        lines = [f"{alias} -> {target}" for alias, target in sorted(mapping.items())]
        await self.ctx.send.text("已配置的指令别名:\n" + "\n".join(lines), stream_id)

    async def _handle_alias_add(self, alias: str, target: str, stream_id: str):
        normalized_alias = alias.strip()
        normalized_target = self._normalize_alias_target(target)
        error = self._validate_alias_shortcut(normalized_alias, normalized_target)
        if error:
            await self.ctx.send.text(error, stream_id)
            return

        result = await self.ctx.call_capability(
            "component.update_plugin_config",
            plugin_name=_PLUGIN_MANAGEMENT_ID,
            key=f"aliases.shortcuts.{normalized_alias}",
            value=normalized_target,
        )
        ok = result.get("success", False) if isinstance(result, dict) else bool(result)
        if ok:
            await self.ctx.send.text(
                f"指令别名已保存: {normalized_alias} -> {normalized_target}\n重载插件管理插件后生效。",
                stream_id,
            )
            return
        error_text = result.get("error", "未知错误") if isinstance(result, dict) else "未知错误"
        await self.ctx.send.text(f"指令别名保存失败: {error_text}", stream_id)

    async def _handle_alias_remove(self, alias: str, stream_id: str):
        normalized_alias = alias.strip()
        if not normalized_alias:
            await self.ctx.send.text("别名不能为空", stream_id)
            return

        config = await self.ctx.config.get_plugin(_PLUGIN_MANAGEMENT_ID)
        aliases_config = config.get("aliases") if isinstance(config, dict) else None
        shortcuts = aliases_config.get("shortcuts", {}) if isinstance(aliases_config, dict) else {}
        if not isinstance(shortcuts, dict) or normalized_alias not in shortcuts:
            await self.ctx.send.text(f"未找到指令别名: {normalized_alias}", stream_id)
            return

        updated_shortcuts = dict(shortcuts)
        updated_shortcuts.pop(normalized_alias, None)
        result = await self.ctx.call_capability(
            "component.update_plugin_config",
            plugin_name=_PLUGIN_MANAGEMENT_ID,
            key="aliases.shortcuts",
            value=updated_shortcuts,
        )
        ok = result.get("success", False) if isinstance(result, dict) else bool(result)
        if ok:
            await self.ctx.send.text(f"指令别名已删除: {normalized_alias}\n重载插件管理插件后生效。", stream_id)
            return
        error_text = result.get("error", "未知错误") if isinstance(result, dict) else "未知错误"
        await self.ctx.send.text(f"指令别名删除失败: {error_text}", stream_id)

    # ------ component 子命令 ------

    async def _list_all_components(self, stream_id: str):
        result = await self.ctx.component.get_all_plugins()
        if not result:
            await self.ctx.send.text("没有注册的组件", stream_id)
            return
        components = self._extract_components(result)
        if not components:
            await self.ctx.send.text("没有注册的组件", stream_id)
            return
        text = ", ".join(f"{c['name']} ({c['type']})" for c in components)
        await self.ctx.send.text(f"已注册的组件: {text}", stream_id)

    async def _handle_component_list_4(self, sub: str, stream_id: str):
        if sub == "enabled":
            await self._list_filtered_components("enabled", "global", stream_id)
        elif sub == "disabled":
            await self._list_filtered_components("disabled", "global", stream_id)
        else:
            await self.ctx.send.text("插件管理命令不合法", stream_id)

    async def _handle_component_list_5(self, sub: str, arg: str, stream_id: str):
        if sub in ("enabled", "disabled"):
            await self._list_filtered_components(sub, arg, stream_id)
        elif sub == "type":
            if arg not in _VALID_COMPONENT_TYPES:
                await self.ctx.send.text(f"未知组件类型: {arg}", stream_id)
                return
            result = await self.ctx.component.get_all_plugins()
            components = [c for c in self._extract_components(result) if c.get("type") == arg]
            if not components:
                await self.ctx.send.text(f"没有注册的 {arg} 组件", stream_id)
                return
            text = ", ".join(f"{c['name']} ({c['type']})" for c in components)
            await self.ctx.send.text(f"注册的 {arg} 组件: {text}", stream_id)
        else:
            await self.ctx.send.text("插件管理命令不合法", stream_id)

    async def _list_filtered_components(self, filter_mode: str, scope: str, stream_id: str):
        result = await self.ctx.component.get_all_plugins()
        all_components = self._extract_components(result)
        if not all_components:
            await self.ctx.send.text("没有注册的组件", stream_id)
            return

        if filter_mode == "enabled":
            filtered = [c for c in all_components if c.get("enabled", False)]
            label = "已启用"
        else:
            filtered = [c for c in all_components if not c.get("enabled", False)]
            label = "已禁用"

        scope_label = "全局" if scope == "global" else "本聊天"
        if not filtered:
            await self.ctx.send.text(f"没有满足条件的{label}{scope_label}组件", stream_id)
            return
        text = ", ".join(f"{c['name']} ({c['type']})" for c in filtered)
        await self.ctx.send.text(f"满足条件的{label}{scope_label}组件: {text}", stream_id)

    async def _handle_component_toggle(self, action: str, scope: str, comp_name: str, comp_type: str, stream_id: str):
        if action not in ("enable", "disable"):
            await self.ctx.send.text("插件管理命令不合法", stream_id)
            return
        if scope not in ("global", "local"):
            await self.ctx.send.text("插件管理命令不合法", stream_id)
            return
        if comp_type not in _VALID_COMPONENT_TYPES:
            await self.ctx.send.text(f"未知组件类型: {comp_type}", stream_id)
            return

        if action == "enable":
            result = await self.ctx.component.enable_component(comp_name, comp_type, scope=scope, stream_id=stream_id)
        else:
            result = await self.ctx.component.disable_component(comp_name, comp_type, scope=scope, stream_id=stream_id)

        ok = result.get("success", False) if isinstance(result, dict) else bool(result)
        scope_label = "全局" if scope == "global" else "本地"
        action_label = "启用" if action == "enable" else "禁用"
        status = "成功" if ok else "失败"
        await self.ctx.send.text(f"{scope_label}{action_label}组件{status}: {comp_name}", stream_id)

    # ------ helpers ------

    @staticmethod
    def _extract_components(result) -> list[dict]:
        """从 get_all_plugins 结果中提取所有组件列表"""
        if not result:
            return []
        if isinstance(result, dict):
            components = []
            for plugin_info in result.values():
                if isinstance(plugin_info, dict):
                    components.extend(plugin_info.get("components", []))
            return components
        return []

    def get_components(self) -> list[dict[str, Any]]:
        """收集组件声明，并把配置中的命令别名写入管理命令。"""

        components = super().get_components()
        aliases = self._collect_configured_aliases()
        for component in components:
            if component.get("name") != "management":
                continue
            metadata = component.get("metadata")
            if not isinstance(metadata, dict):
                continue
            existing_aliases = [str(alias).strip() for alias in metadata.get("aliases", []) if str(alias).strip()]
            metadata["aliases"] = list(dict.fromkeys([*existing_aliases, *aliases]))
        return components

    def _collect_configured_aliases(self) -> list[str]:
        return list(self._build_alias_mapping().keys())

    def _resolve_alias_command(self, raw_command: str) -> str:
        normalized_command = raw_command.strip()
        if not normalized_command or normalized_command.startswith("/pm"):
            return normalized_command

        alias_mapping = self._build_alias_mapping()
        for alias, target in sorted(alias_mapping.items(), key=lambda item: len(item[0]), reverse=True):
            if not self._command_startswith_alias(normalized_command, alias):
                continue
            suffix = normalized_command[len(alias) :].strip()
            return f"{target} {suffix}".strip()
        return normalized_command

    def _build_alias_mapping(self) -> dict[str, str]:
        config = self.get_plugin_config_data()
        return self._build_alias_mapping_from_config(config if isinstance(config, dict) else {})

    @staticmethod
    def _build_alias_mapping_from_config(config: dict[str, object]) -> dict[str, str]:
        aliases_config = config.get("aliases") if isinstance(config, dict) else None
        if not isinstance(aliases_config, dict):
            return {}

        mapping: dict[str, str] = {}
        prefixes = aliases_config.get("management_prefixes", [])
        if isinstance(prefixes, list):
            for alias in prefixes:
                normalized_alias = str(alias).strip()
                if normalized_alias:
                    mapping[normalized_alias] = "/pm"

        shortcuts = aliases_config.get("shortcuts", {})
        if isinstance(shortcuts, dict):
            for alias, target in shortcuts.items():
                normalized_alias = str(alias).strip()
                normalized_target = PluginManagementPlugin._normalize_alias_target(str(target))
                if normalized_alias and PluginManagementPlugin._is_pm_command(normalized_target):
                    mapping[normalized_alias] = normalized_target

        command_aliases = aliases_config.get("command_aliases", {})
        if isinstance(command_aliases, dict):
            for command_key, alias_list in command_aliases.items():
                target = _COMMAND_ALIAS_TARGETS.get(str(command_key).strip())
                if not target or not isinstance(alias_list, list):
                    continue
                for alias in alias_list:
                    normalized_alias = str(alias).strip()
                    if normalized_alias:
                        mapping[normalized_alias] = target
        return mapping

    @staticmethod
    def _command_startswith_alias(command: str, alias: str) -> bool:
        if not command.startswith(alias):
            return False
        return len(command) == len(alias) or command[len(alias)].isspace()

    @staticmethod
    def _normalize_alias_target(raw_target: str) -> str:
        return " ".join(PluginManagementPlugin._split_command(str(raw_target).strip()))

    @staticmethod
    def _is_pm_command(command: str) -> bool:
        return PluginManagementPlugin._command_startswith_alias(command, "/pm")

    @staticmethod
    def _validate_alias_shortcut(alias: str, target: str) -> str:
        if not alias:
            return "别名不能为空"
        if not alias.startswith("/"):
            return "别名必须以 / 开头"
        if any(char.isspace() for char in alias):
            return "别名不能包含空格"
        if alias == "/pm":
            return "不能把 /pm 本身设置为别名"
        if not target:
            return "目标命令不能为空"
        if not PluginManagementPlugin._is_pm_command(target):
            return "目标命令必须以 /pm 开头"
        return ""

    @staticmethod
    def _split_command(raw_command: str) -> list[str]:
        try:
            return shlex.split(raw_command)
        except ValueError:
            return raw_command.split()

    @staticmethod
    def _parse_config_value(raw_value: str) -> object:
        value_text = raw_value.strip()
        try:
            return json.loads(value_text)
        except json.JSONDecodeError:
            return value_text

    @staticmethod
    def _get_nested_value(config: dict[str, object], key: str) -> object:
        if key.startswith("aliases.shortcuts."):
            aliases = config.get("aliases")
            if not isinstance(aliases, dict):
                return None
            shortcuts = aliases.get("shortcuts")
            if not isinstance(shortcuts, dict):
                return None
            return shortcuts.get(key.removeprefix("aliases.shortcuts."))

        if key.startswith("aliases.command_aliases."):
            aliases = config.get("aliases")
            if not isinstance(aliases, dict):
                return None
            command_aliases = aliases.get("command_aliases")
            if not isinstance(command_aliases, dict):
                return None
            return command_aliases.get(key.removeprefix("aliases.command_aliases."))

        current: object = config
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """处理配置热重载事件。

        Args:
            scope: 配置变更范围。
            config_data: 最新配置数据。
            version: 配置版本号。
        """

        del scope
        del config_data
        del version


def create_plugin() -> PluginManagementPlugin:
    """创建插件管理插件实例。

    Returns:
        PluginManagementPlugin: 新的插件管理插件实例。
    """

    return PluginManagementPlugin()
