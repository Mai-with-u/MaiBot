"""插件和组件管理 — 新 SDK 版本

通过 /pm 命令管理插件和组件的生命周期。
"""

from maibot_sdk import MaiBotPlugin, Command


_VALID_COMPONENT_TYPES = ("action", "command", "event_handler")

HELP_ALL = (
    "管理命令帮助\n"
    "/pm help 管理命令提示\n"
    "/pm plugin 插件管理命令\n"
    "/pm component 组件管理命令\n"
    "使用 /pm plugin help 或 /pm component help 获取具体帮助"
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
    "  - <component_type> 可选项: action, command, event_handler\n"
)


class PluginManagementPlugin(MaiBotPlugin):
    """插件和组件管理插件"""

    @Command(
        "management",
        description="管理插件和组件的生命周期",
        pattern=r"(?P<manage_command>^/pm(\s[a-zA-Z0-9_]+)*\s*$)",
    )
    async def handle_management(
        self, stream_id: str = "", user_id: str = "", matched_groups: dict | None = None, **kwargs
    ):
        """处理 /pm 命令"""
        # 权限检查
        permission_result = await self.ctx.config.get("plugin.permission")
        permission_list = permission_result if isinstance(permission_result, list) else []
        if str(user_id) not in permission_list:
            await self.ctx.send.text("你没有权限使用插件管理命令", stream_id)
            return False, "没有权限", True

        if not stream_id:
            return False, "无法获取聊天流信息", True

        raw_command = (matched_groups or {}).get("manage_command", "").strip()
        parts = raw_command.split(" ") if raw_command else ["/pm"]
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
            if parts[1] != "component" or parts[2] != "list":
                await self.ctx.send.text("插件管理命令不合法", stream_id)
                return False, "命令不合法", True
            await self._handle_component_list_5(parts[3], parts[4], stream_id)
            return True, "命令执行完成", True

        if n == 6:
            if parts[1] != "component":
                await self.ctx.send.text("插件管理命令不合法", stream_id)
                return False, "命令不合法", True
            await self._handle_component_toggle(parts[2], parts[3], parts[4], parts[5], stream_id)
            return True, "命令执行完成", True

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

    async def _handle_component_toggle(
        self, action: str, scope: str, comp_name: str, comp_type: str, stream_id: str
    ):
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
            result = await self.ctx.component.enable_component(
                comp_name, comp_type, scope=scope, stream_id=stream_id
            )
        else:
            result = await self.ctx.component.disable_component(
                comp_name, comp_type, scope=scope, stream_id=stream_id
            )

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


def create_plugin():
    return PluginManagementPlugin()
