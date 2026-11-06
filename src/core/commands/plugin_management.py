"""MaiBot 内置插件管理命令。"""

from typing import List

import asyncio
import json
import re

from src.config.config import global_config
from src.plugin_runtime.component_query import component_query_service
from src.plugin_runtime.integration import get_plugin_runtime_manager
from src.services.send_service import image_to_stream, text_to_stream

from .command_catalog import build_command_pages, collect_command_groups, format_command_text
from .pm_aliases import CommandAliases, PM_COMMAND_ID, PM_PATTERN, load_aliases, update_shortcut


_VALID_COMPONENT_TYPES = ("tool", "command", "event_handler")


async def match_pm_command(text: str) -> str | None:
    if not text.startswith("/") or PM_COMMAND_ID in global_config.plugin.disabled_commands:
        return None
    if re.fullmatch(PM_PATTERN, text):
        return text
    resolved = await asyncio.to_thread(CommandAliases().resolve, text)
    return resolved if re.fullmatch(PM_PATTERN, resolved) else None


HELP_ALL = (
    "管理命令帮助\n"
    "/pm help 管理命令提示\n"
    "/pm plugin 插件管理命令\n"
    "/pm component 组件管理命令\n"
    "/pm cmd 查看插件指令列表（支持 pic 图片）\n"
    "/pm alias 管理命令别名\n"
    "使用 /pm plugin help、/pm component help、/pm cmd help、/pm alias help 或 /pm config help 获取具体帮助"
)
HELP_COMMAND = (
    "插件指令帮助\n"
    "/pm cmd 按插件列出所有已注册指令\n"
    "/pm cmd <插件> 列出指定插件的指令\n"
    "/pm cmd pic 按插件生成指令列表图片\n"
    "/pm cmd <插件> pic 生成指定插件的指令列表图片\n"
    "<插件> 可用完整ID或最后一段唯一短名称，例如 builtin.chinese-typo 可写 chinese-typo。\n"
    "短名称重名时请使用完整ID；pic、help 为保留字，同名插件请使用完整ID。\n"
    "兼容旧命令 /pm command list [pic]、/pm command plugin <插件> [pic]。\n"
    "列表包含已禁用指令。"
)
HELP_PLUGIN = (
    "插件管理命令帮助\n"
    "/pm plugin help 插件管理命令提示\n"
    "/pm plugin list 列出所有注册的插件\n"
    "/pm plugin list_enabled 列出所有加载（启用）的插件\n"
    "/pm plugin load <plugin_name> 加载指定插件\n"
    "/pm plugin reload <plugin_name> 重新加载指定插件\n"
)
HELP_CONFIG = (
    "插件配置命令帮助\n"
    "/pm config help 插件配置命令提示\n"
    "/pm config get <plugin_name> [key] 读取指定插件配置或点分隔配置项\n"
    "/pm config set <plugin_name> <key> <value_json> 修改指定插件配置项\n"
    "  - <key> 使用点分隔路径，例如 plugin.enabled\n"
    '  - <value_json> 优先按 JSON 解析，例如 true、123、["文本"]；解析失败时按字符串保存\n'
    "  - 指令别名建议使用 /pm alias add /别名 /pm plugin list"
)
HELP_COMPONENT = (
    "组件管理命令帮助\n"
    "/pm component help 组件管理命令提示\n"
    "/pm component list 列出所有注册的组件\n"
    "/pm component list enabled 列出全局启用的组件\n"
    "/pm component list disabled 列出全局禁用的组件\n"
    "/pm component list type <component_type> 列出已经注册的指定类型的组件\n"
    "/pm component enable global <component_name> <component_type> 全局启用组件\n"
    "/pm component disable global <component_name> <component_type> 全局禁用组件\n"
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


class PluginManagementCommand:
    """解析 /pm 子命令并调用主进程的插件运行时。"""

    async def execute(self, raw_command: str, stream_id: str) -> tuple[bool, str, int]:
        if not stream_id:
            return False, "无法获取聊天流信息", 1

        parts = CommandAliases._split_command(raw_command) if raw_command else ["/pm"]
        n = len(parts)

        if n >= 2 and parts[1] in ("command", "cmd"):
            ok = await self._handle_command_catalog(parts[2:], stream_id, compact=parts[1] == "cmd")
            return ok, "命令执行完成" if ok else "指令列表查询失败", 1

        # /pm
        if n == 1:
            await text_to_stream(HELP_ALL, stream_id)
            return True, "帮助已发送", 1

        # /pm <sub>
        if n == 2:
            sub = parts[1]
            if sub == "plugin":
                await text_to_stream(HELP_PLUGIN, stream_id)
            elif sub == "component":
                await text_to_stream(HELP_COMPONENT, stream_id)
            elif sub == "alias":
                await text_to_stream(HELP_ALIAS, stream_id)
            elif sub == "config":
                await text_to_stream(HELP_CONFIG, stream_id)
            elif sub == "help":
                await text_to_stream(HELP_ALL, stream_id)
            else:
                await text_to_stream("插件管理命令不合法", stream_id)
                return False, "命令不合法", 1
            return True, "帮助已发送", 1

        # /pm plugin <action> / /pm component <action>
        if n == 3:
            if parts[1] == "plugin":
                await self._handle_plugin_3(parts[2], stream_id)
            elif parts[1] == "config":
                if parts[2] == "help":
                    await text_to_stream(HELP_CONFIG, stream_id)
                else:
                    await text_to_stream("插件管理命令不合法", stream_id)
                    return False, "命令不合法", 1
            elif parts[1] == "alias":
                if parts[2] == "help":
                    await text_to_stream(HELP_ALIAS, stream_id)
                elif parts[2] == "list":
                    await self._handle_alias_list(stream_id)
                else:
                    await text_to_stream("插件管理命令不合法", stream_id)
                    return False, "命令不合法", 1
            elif parts[1] == "component":
                if parts[2] == "list":
                    await self._list_all_components(stream_id)
                elif parts[2] == "help":
                    await text_to_stream(HELP_COMPONENT, stream_id)
                else:
                    await text_to_stream("插件管理命令不合法", stream_id)
                    return False, "命令不合法", 1
            else:
                await text_to_stream("插件管理命令不合法", stream_id)
                return False, "命令不合法", 1
            return True, "命令执行完成", 1

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
                    await text_to_stream("插件管理命令不合法", stream_id)
                    return False, "命令不合法", 1
            else:
                await text_to_stream("插件管理命令不合法", stream_id)
                return False, "命令不合法", 1
            return True, "命令执行完成", 1

        if n == 5:
            if parts[1] == "alias" and parts[2] == "add":
                await self._handle_alias_add(parts[3], parts[4], stream_id)
                return True, "命令执行完成", 1
            if parts[1] == "component" and parts[2] == "list":
                await self._handle_component_list_5(parts[3], parts[4], stream_id)
                return True, "命令执行完成", 1
            if parts[1] == "config" and parts[2] == "get":
                await self._handle_config_get(parts[2], parts[3], parts[4], stream_id)
                return True, "命令执行完成", 1
            await text_to_stream("插件管理命令不合法", stream_id)
            return False, "命令不合法", 1

        if n >= 6:
            if parts[1] == "alias" and parts[2] == "add":
                await self._handle_alias_add(parts[3], " ".join(parts[4:]), stream_id)
                return True, "命令执行完成", 1
            if parts[1] == "component" and n == 6:
                await self._handle_component_toggle(parts[2], parts[3], parts[4], parts[5], stream_id)
                return True, "命令执行完成", 1
            if parts[1] == "config" and parts[2] == "set" and n >= 6:
                await self._handle_config_set(parts[3], parts[4], " ".join(parts[5:]), stream_id)
                return True, "命令执行完成", 1
            await text_to_stream("插件管理命令不合法", stream_id)
            return False, "命令不合法", 1

        await text_to_stream("插件管理命令不合法", stream_id)
        return False, "命令不合法", 1

    # ------ command 子命令 ------

    async def _handle_command_catalog(self, args: List[str], stream_id: str, *, compact: bool = False) -> bool:
        """通过现有组件能力查询指令，文本与图片共享同一份数据。"""
        # 简写只转换语法，权限、插件解析与输出均沿用旧命令链路。
        if compact:
            if not args or args == ["pic"]:
                args = ["list", *args]
            elif args != ["help"]:
                if len(args) == 1 or (len(args) == 2 and args[1] == "pic"):
                    args = ["plugin", *args]
                else:
                    await text_to_stream(HELP_COMMAND, stream_id)
                    return False
        if not args or args == ["help"] or args == ["plugin"]:
            await text_to_stream(HELP_COMMAND, stream_id)
            return True

        plugin_id = ""
        picture = False
        if args in (["list"], ["list", "pic"]):
            picture = len(args) == 2
        elif len(args) in (2, 3) and args[0] == "plugin" and (len(args) == 2 or args[2] == "pic"):
            plugin_id = args[1]
            picture = len(args) == 3
        else:
            await text_to_stream(HELP_COMMAND, stream_id)
            return False

        result = await get_plugin_runtime_manager()._cap_component_get_all_plugins("", "", {})
        if not result["success"]:
            await text_to_stream(f"读取插件指令失败: {result['error']}", stream_id)
            return False
        plugins = result["plugins"]
        if plugin_id and plugin_id not in plugins:
            matches = sorted(name for name in plugins if name.rsplit(".", 1)[-1] == plugin_id)
            if not matches:
                await text_to_stream(f"未找到已注册插件: {plugin_id}", stream_id)
                return False
            if len(matches) > 1:
                await text_to_stream(
                    f"插件短名称 {plugin_id} 对应多个插件，请使用完整ID:\n" + "\n".join(matches),
                    stream_id,
                )
                return False
            plugin_id = matches[0]
        groups = collect_command_groups(plugins, plugin_id)
        if not groups:
            await text_to_stream(f"插件 {plugin_id} 没有注册指令" if plugin_id else "没有注册的指令", stream_id)
            return True
        if not picture:
            await text_to_stream(format_command_text(groups), stream_id)
            return True

        webui_style = global_config.webui.webui_style
        if type(webui_style) is not int or webui_style not in (0, 1, 2):
            await text_to_stream(f"无法读取有效的 WebUI 界面风格: {webui_style}", stream_id)
            return False

        # 优先生成单张长图；多页全部渲染成功后打包发送，避免散发图片或只发送部分目录。
        images: List[str] = []
        for page in build_command_pages(groups, webui_style):
            result = await get_plugin_runtime_manager()._cap_render_html2png(
                "",
                "",
                {
                    "html": page,
                    "viewport": {"width": 1000, "height": 600},
                    "full_page": True,
                    "device_scale_factor": 1.5,
                },
            )
            result = result.get("result", {})
            if not isinstance(result, dict) or not result.get("image_base64"):
                await text_to_stream(f"指令列表图片生成失败: {result}", stream_id)
                return False
            images.append(result["image_base64"])
        if len(images) == 1:
            sent = await image_to_stream(images[0], stream_id)
        else:
            messages = [
                {"nickname": "插件指令列表", "segments": [{"type": "image", "content": data}]} for data in images
            ]
            result = await get_plugin_runtime_manager()._cap_send_forward(
                "",
                "",
                {"messages": messages, "stream_id": stream_id},
            )
            sent = result["success"]
        if not sent:
            await text_to_stream("指令列表图片发送失败", stream_id)
            return False
        return True

    # ------ plugin 子命令 ------

    async def _handle_plugin_3(self, action: str, stream_id: str):
        match action:
            case "help":
                await text_to_stream(HELP_PLUGIN, stream_id)
            case "list":
                result = (await get_plugin_runtime_manager()._cap_component_list_registered_plugins("", "", {}))[
                    "plugins"
                ]
                plugins = result if isinstance(result, list) else []
                await text_to_stream(f"已注册的插件: {', '.join(plugins) if plugins else '无'}", stream_id)
            case "list_enabled":
                result = (await get_plugin_runtime_manager()._cap_component_list_loaded_plugins("", "", {}))["plugins"]
                plugins = result if isinstance(result, list) else []
                await text_to_stream(f"已加载的插件: {', '.join(plugins) if plugins else '无'}", stream_id)
            case _:
                await text_to_stream("插件管理命令不合法", stream_id)

    async def _handle_plugin_4(self, action: str, name: str, stream_id: str):
        match action:
            case "load":
                result = await get_plugin_runtime_manager()._cap_component_load_plugin("", "", {"plugin_name": name})
                ok = result.get("success", False) if isinstance(result, dict) else bool(result)
                msg = f"插件加载成功: {name}" if ok else f"插件加载失败: {name}"
                await text_to_stream(msg if ok else f"{msg}（{result.get('error', '未知错误')}）", stream_id)
            case "unload":
                result = await get_plugin_runtime_manager()._cap_component_unload_plugin("", "", {"plugin_name": name})
                ok = result.get("success", False) if isinstance(result, dict) else bool(result)
                msg = f"插件卸载成功: {name}" if ok else f"插件卸载失败: {name}"
                await text_to_stream(msg if ok else f"{msg}（{result.get('error', '未知错误')}）", stream_id)
            case "reload":
                result = await get_plugin_runtime_manager()._cap_component_reload_plugin("", "", {"plugin_name": name})
                ok = result.get("success", False) if isinstance(result, dict) else bool(result)
                msg = f"插件重新加载成功: {name}" if ok else f"插件重新加载失败: {name}"
                await text_to_stream(msg if ok else f"{msg}（{result.get('error', '未知错误')}）", stream_id)
            case _:
                await text_to_stream("插件管理命令不合法", stream_id)

    # ------ config 子命令 ------

    async def _handle_config_get(self, action: str, plugin_name: str, key: str, stream_id: str):
        if action != "get":
            await text_to_stream("插件管理命令不合法", stream_id)
            return
        config = await asyncio.to_thread(component_query_service.get_plugin_config, plugin_name)
        if not config:
            await text_to_stream(f"未读取到插件配置: {plugin_name}", stream_id)
            return
        value = self._get_nested_value(config, key) if key else config
        display_key = f".{key}" if key else ""
        value_text = json.dumps(value, ensure_ascii=False)
        await text_to_stream(f"{plugin_name}{display_key} = {value_text}", stream_id)

    async def _handle_config_set(self, plugin_name: str, key: str, raw_value: str, stream_id: str):
        if not key.strip():
            await text_to_stream("配置键不能为空", stream_id)
            return
        value = self._parse_config_value(raw_value)
        result = await get_plugin_runtime_manager().update_plugin_config_value(plugin_name, key, value)
        ok = result.get("success", False) if isinstance(result, dict) else bool(result)
        if ok:
            await text_to_stream(f"插件配置已更新: {plugin_name}.{key}", stream_id)
            return
        error = result.get("error", "未知错误") if isinstance(result, dict) else "未知错误"
        await text_to_stream(f"插件配置更新失败: {error}", stream_id)

    # ------ alias 子命令 ------

    async def _handle_alias_list(self, stream_id: str) -> None:
        mapping = await asyncio.to_thread(load_aliases)
        lines = [f"{alias} -> {target}" for alias, target in sorted(mapping.items())]
        await text_to_stream("已配置的指令别名:\n" + "\n".join(lines) if lines else "未配置指令别名", stream_id)

    async def _handle_alias_add(self, alias: str, target: str, stream_id: str) -> None:
        alias = alias.strip()
        target = CommandAliases._normalize_alias_target(target)
        error = CommandAliases._validate_alias_shortcut(alias, target)
        if error:
            await text_to_stream(error, stream_id)
            return
        await asyncio.to_thread(update_shortcut, alias, target)
        await text_to_stream(f"指令别名已保存: {alias} -> {target}", stream_id)

    async def _handle_alias_remove(self, alias: str, stream_id: str) -> None:
        removed = await asyncio.to_thread(update_shortcut, alias.strip(), None)
        await text_to_stream(f"指令别名已删除: {alias}" if removed else f"未找到指令别名: {alias}", stream_id)

    # ------ component 子命令 ------

    async def _list_all_components(self, stream_id: str):
        result = (await get_plugin_runtime_manager()._cap_component_get_all_plugins("", "", {})).get("plugins", {})
        if not result:
            await text_to_stream("没有注册的组件", stream_id)
            return
        components = self._extract_components(result)
        if not components:
            await text_to_stream("没有注册的组件", stream_id)
            return
        text = ", ".join(f"{c['name']} ({c['type']})" for c in components)
        await text_to_stream(f"已注册的组件: {text}", stream_id)

    async def _handle_component_list_4(self, sub: str, stream_id: str):
        if sub == "enabled":
            await self._list_filtered_components("enabled", "global", stream_id)
        elif sub == "disabled":
            await self._list_filtered_components("disabled", "global", stream_id)
        else:
            await text_to_stream("插件管理命令不合法", stream_id)

    async def _handle_component_list_5(self, sub: str, arg: str, stream_id: str):
        if sub in ("enabled", "disabled"):
            await self._list_filtered_components(sub, arg, stream_id)
        elif sub == "type":
            if arg not in _VALID_COMPONENT_TYPES:
                await text_to_stream(f"未知组件类型: {arg}", stream_id)
                return
            result = (await get_plugin_runtime_manager()._cap_component_get_all_plugins("", "", {})).get("plugins", {})
            components = [c for c in self._extract_components(result) if c.get("type") == arg]
            if not components:
                await text_to_stream(f"没有注册的 {arg} 组件", stream_id)
                return
            text = ", ".join(f"{c['name']} ({c['type']})" for c in components)
            await text_to_stream(f"注册的 {arg} 组件: {text}", stream_id)
        else:
            await text_to_stream("插件管理命令不合法", stream_id)

    async def _list_filtered_components(self, filter_mode: str, scope: str, stream_id: str):
        if scope != "global":
            await text_to_stream("当前运行时仅支持查询全局组件状态", stream_id)
            return
        result = (await get_plugin_runtime_manager()._cap_component_get_all_plugins("", "", {})).get("plugins", {})
        all_components = self._extract_components(result)
        if not all_components:
            await text_to_stream("没有注册的组件", stream_id)
            return

        if filter_mode == "enabled":
            filtered = [c for c in all_components if c.get("enabled", False)]
            label = "已启用"
        else:
            filtered = [c for c in all_components if not c.get("enabled", False)]
            label = "已禁用"

        scope_label = "全局" if scope == "global" else "本聊天"
        if not filtered:
            await text_to_stream(f"没有满足条件的{label}{scope_label}组件", stream_id)
            return
        text = ", ".join(f"{c['name']} ({c['type']})" for c in filtered)
        await text_to_stream(f"满足条件的{label}{scope_label}组件: {text}", stream_id)

    async def _handle_component_toggle(self, action: str, scope: str, comp_name: str, comp_type: str, stream_id: str):
        if action not in ("enable", "disable"):
            await text_to_stream("插件管理命令不合法", stream_id)
            return
        if scope not in ("global", "local"):
            await text_to_stream("插件管理命令不合法", stream_id)
            return
        if comp_type not in _VALID_COMPONENT_TYPES:
            await text_to_stream(f"未知组件类型: {comp_type}", stream_id)
            return

        args = {"name": comp_name, "component_type": comp_type, "scope": scope}
        if action == "enable":
            result = await get_plugin_runtime_manager()._cap_component_enable("", "", args)
        else:
            result = await get_plugin_runtime_manager()._cap_component_disable("", "", args)

        ok = result.get("success", False) if isinstance(result, dict) else bool(result)
        scope_label = "全局" if scope == "global" else "本地"
        action_label = "启用" if action == "enable" else "禁用"
        status = "成功" if ok else "失败"
        detail = "" if ok else f"（{result.get('error', '未知错误')}）"
        await text_to_stream(f"{scope_label}{action_label}组件{status}: {comp_name}{detail}", stream_id)

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

    @staticmethod
    def _parse_config_value(raw_value: str) -> object:
        value_text = raw_value.strip()
        try:
            return json.loads(value_text)
        except json.JSONDecodeError:
            return value_text

    @staticmethod
    def _get_nested_value(config: dict[str, object], key: str) -> object:
        current: object = config
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current
