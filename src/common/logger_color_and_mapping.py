# 定义模块颜色映射
from typing import Optional, Tuple, Dict

import itertools
import os
import sys


MODULE_COLORS: Dict[str, Tuple[str, Optional[str], bool]] = {
    "sender": ("#005f87", None, False),  # 较暗的蓝色，适合不显眼的日志
    "send_api": ("#005f87", None, False),  # 橙色，适合突出显示
    # 生成
    "replyer": ("#ff8700", None, False),
    "llm_api": ("#ff8700", None, False),
    # 消息处理
    "chat": ("#5fff00", None, False),
    "chat_image": ("#5f87d7", None, False),
    # emoji
    "emoji": ("#ffaf00", None, False),  # 橙黄色，偏向橙色
    "emoji_api": ("#ffaf00", None, False),  # 橙黄色，偏向橙色
    # 核心模块
    "main": ("#ffffff", None, True),  # 亮白色 + 粗体 (主程序)
    "memory": ("#00af00", None, False),
    "memory_retrieval": ("#00af00", None, False),
    "config": ("#ffff00", None, False),
    "common": ("#ff00ff", None, False),
    "tools": ("#00ffff", None, False),
    "lpmm": ("#00ffff", None, False),
    "plugin_system": ("#ff0000", None, False),
    "person_info": ("#008000", None, False),
    "manager": ("#800080", None, False),
    "llm_models": ("#008080", None, False),
    "remote": ("#6c6c6c", None, False),  # 深灰色，更不显眼
    "planner": ("#008080", None, False),
    "relation": ("#af87af", None, False),  # 柔和的紫色，不刺眼
    # 聊天相关模块
    "hfc": ("#d787af", None, False),  # 柔和的粉色，不显眼但保持粉色系
    "bc": ("#d787af", None, False),  # 柔和的粉色，不显眼但保持粉色系
    "sub_heartflow": ("#ff5fff", None, False),
    "subheartflow_manager": ("#ff00ff", None, False),
    "background_tasks": ("#585858", None, False),
    "chat_message": ("#00d7ff", None, False),
    "chat_stream": ("#00ffff", None, False),
    "message_storage": ("#0087ff", None, False),
    "expressor": ("#d75f00", None, False),
    # jargon相关
    "jargon": ("#ffd700", None, False),  # 金黄色，突出显示
    # 插件系统
    "plugins": ("#800000", None, False),
    "plugin_api": ("#808000", None, False),
    "plugin_manager": ("#ff8700", None, False),
    "base_plugin": ("#ff5f00", None, False),
    "base_command": ("#ff8700", None, False),
    "component_registry": ("#ffaf00", None, False),
    "plugin_runtime.integration": ("#d75f00", None, False),
    "plugin_runtime.host.supervisor": ("#ff5f00", None, False),
    "plugin_runtime.host.rpc_server": ("#ff8700", None, False),
    "plugin_runtime.host.component_registry": ("#ffaf00", None, False),
    "plugin_runtime.host.capability_service": ("#ffd700", None, False),
    "plugin_runtime.host.event_dispatcher": ("#87d700", None, False),
    "plugin_runtime.host.hook_dispatcher": ("#5fd7af", None, False),
    "plugin_runtime.runner.main": ("#d787ff", None, False),
    "plugin_runtime.runner.rpc_client": ("#8787ff", None, False),
    "plugin_runtime.runner.manifest_validator": ("#5fafff", None, False),
    "plugin_runtime.runner.plugin_loader": ("#00afaf", None, False),
    "webui": ("#5f87ff", None, False),
    "webui.app": ("#5f87d7", None, False),
    "webui.api": ("#5fafff", None, False),
    "webui.auth": ("#87afff", None, False),
    "webui.rate_limiter": ("#5fd7ff", None, False),
    "webui.logs_ws": ("#00afff", None, False),
    "webui.ws_auth": ("#00d7ff", None, False),
    "webui.chat": ("#5fffaf", None, False),
    "webui.emoji": ("#ffd75f", None, False),
    "webui.expression": ("#d7af5f", None, False),
    "webui.jargon": ("#d7d75f", None, False),
    "webui.person": ("#87d787", None, False),
    "webui.statistics": ("#af87ff", None, False),
    "webui.plugin_routes": ("#ffaf00", None, False),
    "webui.plugin_progress": ("#ff8700", None, False),
    "webui.git_mirror": ("#878787", None, False),
    "webui.anti_crawler": ("#ff5f5f", None, False),
    "webui_server": ("#5f87ff", None, False),
    "webui_system": ("#87afff", None, False),
    "stream_api": ("#ffd700", None, False),
    "config_api": ("#ffff00", None, False),
    "heartflow_api": ("#afff00", None, False),
    "action_apis": ("#87ff00", None, False),
    "independent_apis": ("#5fff00", None, False),
    "database_api": ("#00ff00", None, False),
    "utils_api": ("#00ffff", None, False),
    "message_api": ("#008080", None, False),
    # 管理器模块
    "async_task_manager": ("#af00ff", None, False),
    "mood": ("#af5fff", None, False),
    "local_storage": ("#af87ff", None, False),
    "willing": ("#afafff", None, False),
    # 工具模块
    "tool_use": ("#d78700", None, False),
    "tool_executor": ("#d78700", None, False),
    "base_tool": ("#d7af00", None, False),
    # 工具和实用模块
    "prompt_build": ("#8787ff", None, False),
    "chat_utils": ("#87afff", None, False),
    "maibot_statistic": ("#af00ff", None, False),
    # 特殊功能插件
    "mute_plugin": ("#585858", None, False),
    "core_actions": ("#87d7ff", None, False),
    "tts_action": ("#5f5f00", None, False),
    "doubao_pic_plugin": ("#5f8700", None, False),
    # Action组件
    "no_reply_action": ("#ffaf00", None, False),  # 亮橙色，显眼但不像警告
    "reply_action": ("#00ff00", None, False),
    "base_action": ("#bcbcbc", None, False),
    # 数据库和消息
    "database_model": ("#875f00", None, False),
    "maim_message": ("#af87d7", None, False),
    # 日志系统
    "logger": ("#808080", None, False),
    "confirm": ("#ffff00", None, True),  # 黄色 + 粗体
    # 模型相关
    "model_utils": ("#d700d7", None, False),
    "relationship_fetcher": ("#d75fd7", None, False),
    "relationship_builder": ("#8700ff", None, False),
    "conflict_tracker": ("#5fff00", None, False),  # 柔和的粉色，不显眼但保持粉色系
}

# 定义模块别名映射 - 将真实的logger名称映射到显示的别名
MODULE_ALIASES = {
    # 示例映射
    "sender": "消息发送",
    "send_api": "消息发送API",
    "replyer": "言语",
    "llm_api": "生成API",
    "emoji": "表情包",
    "emoji_api": "表情包API",
    "chat": "所见",
    "chat_image": "识图",
    "action_manager": "动作",
    "memory_activator": "记忆",
    "tool_use": "工具",
    "expressor": "表达方式",
    "database_model": "数据库",
    "mood": "情绪",
    "memory": "记忆",
    "memory_retrieval": "回忆",
    "tool_executor": "工具",
    "hfc": "聊天节奏",
    "plugin_manager": "插件",
    "relationship_builder": "关系",
    "llm_models": "模型",
    "person_info": "人物",
    "chat_stream": "聊天流",
    "planner": "规划器",
    "config": "配置",
    "main": "主程序",
    "chat_history_summarizer": "聊天概括器",
    "plugin_runtime.integration": "IPC插件系统",
    "plugin_runtime.host.supervisor": "插件监督器",
    "plugin_runtime.host.rpc_server": "插件RPC服务",
    "plugin_runtime.host.component_registry": "插件组件注册",
    "plugin_runtime.host.capability_service": "插件能力服务",
    "plugin_runtime.host.event_dispatcher": "插件事件分发",
    "plugin_runtime.host.workflow_executor": "插件工作流",
    "plugin_runtime.runner.main": "插件运行器",
    "plugin_runtime.runner.rpc_client": "插件RPC客户端",
    "plugin_runtime.runner.manifest_validator": "插件清单校验",
    "plugin_runtime.runner.plugin_loader": "插件加载器",
    "webui": "WebUI",
    "webui.app": "WebUI应用",
    "webui.api": "WebUI接口",
    "webui.auth": "WebUI鉴权",
    "webui.rate_limiter": "WebUI限流",
    "webui.logs_ws": "WebUI日志WS",
    "webui.ws_auth": "WebUI鉴权WS",
    "webui.chat": "WebUI聊天",
    "webui.emoji": "WebUI表情",
    "webui.expression": "WebUI表达",
    "webui.jargon": "WebUI黑话",
    "webui.person": "WebUI人物",
    "webui.statistics": "WebUI统计",
    "webui.plugin_routes": "WebUI插件",
    "webui.plugin_progress": "WebUI插件进度",
    "webui.git_mirror": "WebUI镜像",
    "webui.anti_crawler": "WebUI反爬",
    "webui_server": "WebUI服务",
    "webui_system": "WebUI系统",
}

RESET_COLOR = "\033[0m"

CONVERTED_MODULE_COLORS = {}


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return int(s[:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def supports_truecolor() -> bool:
    # sourcery skip: assign-if-exp, reintroduce-else
    ct = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in ct or "24bit" in ct:
        return True
    if "WT_SESSION" in os.environ:
        return True
    return sys.stdout.isatty()


def rgb_pair_to_ansi_truecolor(
    fg: Tuple[int, int, int], bg: Optional[Tuple[int, int, int]] = None, bold: bool = False
) -> str:
    prefix = "1;" if bold else ""
    fr, fg_g, fb = fg
    if bg is None:
        return f"\033[{prefix}38;2;{fr};{fg_g};{fb}m"
    br, bg_g, bb = bg
    return f"\033[{prefix}38;2;{fr};{fg_g};{fb};48;2;{br};{bg_g};{bb}m"


def rgb_to_256_index(r: int, g: int, b: int) -> int:
    base16 = [
        (0, 0, 0),
        (128, 0, 0),
        (0, 128, 0),
        (128, 128, 0),
        (0, 0, 128),
        (128, 0, 128),
        (0, 128, 128),
        (192, 192, 192),
        (128, 128, 128),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (0, 0, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ]
    palette = base16[:]
    levels = [0, 95, 135, 175, 215, 255]
    for ri, gi, bi in itertools.product(range(6), range(6), range(6)):
        palette.append((levels[ri], levels[gi], levels[bi]))
    for i in range(24):
        v = 8 + i * 10
        palette.append((v, v, v))
    best_idx = 0
    best_dist = float("inf")
    for idx, (pr, pg, pb) in enumerate(palette):
        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if d < best_dist:
            best_dist = d
            best_idx = idx
    return best_idx


def idx_pair_to_ansi_256(fg_idx: int, bg_idx: Optional[int] = None, bold: bool = False) -> str:
    prefix = "1;" if bold else ""
    if bg_idx is None:
        return f"\033[{prefix}38;5;{fg_idx}m"
    return f"\033[{prefix}38;5;{fg_idx};48;5;{bg_idx}m"


def hex_pair_to_ansi(hex_fg: str, hex_bg: Optional[str] = None, bold: bool = False) -> str:
    """
    返回 escape_str
    背景可选（hex_bg=None 表示只设置前景色）
    """
    fg_rgb = hex_to_rgb(hex_fg)
    bg_rgb = hex_to_rgb(hex_bg) if hex_bg is not None else None
    fg_idx = rgb_to_256_index(*fg_rgb)
    bg_idx = rgb_to_256_index(*bg_rgb) if bg_rgb is not None else None
    return idx_pair_to_ansi_256(fg_idx, bg_idx, bold)


if not supports_truecolor():
    for name, (hex_fore_color, hex_back_color, bold) in MODULE_COLORS.items():
        escape_str = hex_pair_to_ansi(hex_fore_color, hex_back_color, bold)
        CONVERTED_MODULE_COLORS[name] = escape_str
else:
    for name, (hex_fore_color, hex_back_color, bold) in MODULE_COLORS.items():
        escape_str = rgb_pair_to_ansi_truecolor(
            hex_to_rgb(hex_fore_color), hex_to_rgb(hex_back_color) if hex_back_color else None, bold
        )
        CONVERTED_MODULE_COLORS[name] = escape_str
