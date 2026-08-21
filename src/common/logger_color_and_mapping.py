from typing import Dict, Optional, Tuple

import itertools
import os
import sys


# 定义模块颜色映射
MODULE_COLORS: Dict[str, Tuple[str, Optional[str], bool]] = {
    "sender": ("#005f87", None, False),  # 较暗的蓝色，适合不显眼的日志
    "send_api": ("#005f87", None, False),  # 橙色，适合突出显示
    # 生成
    "replyer": ("#ff8700", None, False),
    "llm_api": ("#ff8700", None, False),
    # 消息处理
    "chat": ("#5fff00", None, False),
    "image": ("#5f87d7", None, False),
    "image_cache_cleanup": ("#5f87d7", None, False),
    "emoji": ("#ffaf00", None, False),  # 橙黄色，偏向橙色
    "update_notice": ("#daaf10", None, False),
    "emoji_cache_cleanup": ("#ffaf00", None, False),  # 与表情包模块保持一致
    # 核心模块
    "main": ("#ffffff", None, True),  # 亮白色 + 粗体 (主程序)
    "config": ("#a2ff00", None, False),
    "common": ("#ff00ff", None, False),
    "tools": ("#00ffff", None, False),
    "person_info": ("#008000", None, False),
    "manager": ("#800080", None, False),
    "llm_models": ("#008080", None, False),
    "remote": ("#6c6c6c", None, False),  # 深灰色，更不显眼
    "planner": ("#008080", None, False),
    "maisaka_reasoning_engine": ("#0fd5d5", None, False),
    "maisaka_chat_loop": ("#1bb2ed", None, False),
    "maisaka_turn_scheduler": ("#ff8700", None, False),
    "maisaka_runtime": ("#e5810f", None, False),
    "chat_message": ("#00d7ff", None, False),
    "chat_stream": ("#00ffff", None, False),
    "message_storage": ("#0087ff", None, False),
    "expressor": ("#d75f00", None, False),
    "expression_utils":("#d75f00", None, False),
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
    "plugin_runtime.host.runner_manager": ("#ff5f00", None, False),
    "plugin_runtime.host.rpc_server": ("#ff8700", None, False),
    "plugin_runtime.host.component_registry": ("#ffaf00", None, False),
    "plugin_runtime.host.capability_service": ("#ffd700", None, False),
    "plugin_runtime.host.event_dispatcher": ("#87d700", None, False),
    "plugin_runtime.host.hook_dispatcher": ("#5fd7af", None, False),
    "plugin_runtime.host.message_gateway": ("#5fd7d7", None, False),
    "plugin_runtime.host.message_utils": ("#5faf87", None, False),
    "plugin_runtime.group.core": ("#ff8700", None, False),
    "plugin_runtime.group.extension": ("#d787ff", None, False),
    "plugin_runtime.runner.main": ("#d787ff", None, False),
    "plugin_runtime.runner.rpc_client": ("#8787ff", None, False),
    "plugin_runtime.runner.manifest_validator": ("#5fafff", None, False),
    "plugin_runtime.runner.plugin_loader": ("#00afaf", None, False),
    "plugin.maibot-team.napcat-adapter": ("#00af87", None, False),
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
    "core_actions": ("#87d7ff", None, False),
    # 数据库和消息
    "database_model": ("#875f00", None, False),
    "maim_message": ("#af87d7", None, False),
    # 日志系统
    "logger": ("#808080", None, False),
    "confirm": ("#ffff00", None, True),  # 黄色 + 粗体
    # 模型相关
    "model_utils": ("#d700d7", None, False),
}

# 定义模块别名映射 - 将真实的logger名称映射到显示的别名
MODULE_ALIASES = {
    # 示例映射
    "sender": "消息发送",
    "send_api": "消息发送API",
    "replyer": "言语",
    "llm_api": "生成API",
    "image": "图片",
    "expression_vector_index": "表达向量索引",
    "image_cache_cleanup": "图片缓存清理",
    "emoji": "表情包",
    "emoji_cache_cleanup": "表情包缓存清理",
    "chat": "所见",
    "maisaka_turn_scheduler": "读空气",
    "chat_image": "识图",
    "action_manager": "动作",
    "memory_activator": "记忆",
    "tool_use": "工具",
    "async_task_manager": "异步任务管理",
    "chat_utils": "聊天工具",
    "local_storage": "本地存储",
    "expressor": "表达方式",
    "expression_utils": "表达方式",
    "database_model": "数据库",
    "tool_executor": "工具",
    "plugin_manager": "插件",
    "llm_models": "模型",
    "person_info": "人物",
    "model_utils": "模型工具",
    "chat_stream": "聊天流",
    "planner": "规划器",
    "config": "配置",
    "chat_manager": "聊天管理器",
    "A_Memorix.EmbeddingAPIAdapter": "记忆嵌入",
    "A_Memorix.GraphStore": "记忆图",
    "main": "主程序",
    "plugin_runtime.integration": "IPC插件系统",
    "plugin_runtime.host.supervisor": "插件监督器",
    "plugin_runtime.host.runner_manager": "插件监督器",
    "plugin_runtime.host.rpc_server": "插件RPC服务",
    "plugin_runtime.host.component_registry": "插件组件注册",
    "plugin_runtime.host.capability_service": "插件能力服务",
    "plugin_runtime.host.event_dispatcher": "插件事件分发",
    "plugin_runtime.host.hook_dispatcher": "插件Hook分发",
    "plugin_runtime.host.message_gateway": "插件消息网关",
    "plugin_runtime.host.message_utils": "插件消息工具",
    "plugin_runtime.host.workflow_executor": "插件工作流",
    "plugin_runtime.group.core": "核心插件",
    "plugin_runtime.group.extension": "扩展插件",
    "plugin_runtime.runner.main": "插件运行器",
    "plugin_runtime.runner.rpc_client": "插件RPC客户端",
    "plugin_runtime.runner.manifest_validator": "插件清单校验",
    "plugin_runtime.runner.plugin_loader": "插件加载器",
    "plugin.maibot-team.napcat-adapter": "NapCat内置适配器",
    "webui": "WebUI",
    "webui.app": "WebUI应用",
    "webui.api": "WebUI接口",
    "webui.unified_ws": "WebUI统一连接",
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
    "maisaka_runtime": "MaiSaka",
    "maisaka_monitor_event_store": "麦麦监控事件",
    "watchfiles.main": "文件变更监控",
    # 基础设施与服务层
    "ConfigBase": "配置基础",
    "Prompt": "提示词管理",
    "ReplyerManager": "回复管理",
    "base_message_component_model": "消息组件模型",
    "bot_account_service": "机器人账号服务",
    "common_utils": "通用工具",
    "config_utils": "配置工具",
    "core.tooling": "核心工具系统",
    "database": "数据库",
    "database_migration": "数据库迁移",
    "database_service": "数据库服务",
    "embedding_service": "嵌入服务",
    "event_bus": "事件总线",
    "file_utils": "文件工具",
    "file_watcher": "配置文件监控",
    "generator_service": "生成服务",
    "global_announcement_manager": "全局公告管理",
    "image_path_maintenance_service": "图片路径维护",
    "image_utils": "图片工具",
    "llm_adapter_base": "模型适配器",
    "llm_cache_stats": "模型缓存统计",
    "llm_request_snapshot": "模型请求快照",
    "llm_service": "模型服务",
    "logger": "日志系统",
    "maibot_statistic": "麦麦统计",
    "maim_message": "MaiM消息",
    "maim_message_api_server": "MaiM消息接口",
    "message_server": "消息服务",
    "message_utils": "消息工具",
    "memory_flow_service": "记忆流服务",
    "memory_service": "记忆服务",
    "model_client_registry": "模型客户端注册",
    "person_utils": "人物工具",
    "send_service": "消息发送服务",
    "service_task_resolver": "服务任务解析",
    "services.html_render_service": "HTML渲染服务",
    "statistics_aggregation_service": "统计聚合服务",
    "statistics_service": "统计服务",
    "tool_record_cleanup_service": "工具记录清理",
    "typo_gen": "错别字生成",
    "update_notice": "更新通知",
    "voice_utils": "语音工具",
    # 聊天、学习与表达
    "behavior_learner": "行为学习",
    "behavior_pattern_maintenance": "行为模式维护",
    "behavior_pattern_store": "行为模式存储",
    "behavior_scenario": "行为场景",
    "behavior_scene_cluster": "行为场景聚类",
    "behavior_selector": "行为选择",
    "chat_message": "聊天消息",
    "emoji_maisaka_tool": "表情包工具",
    "event_helpers": "聊天事件工具",
    "heartflow": "心流管理",
    "image_receive_compressor": "接收图片压缩",
    "jargon": "黑话学习",
    "jargon_data_model": "黑话数据模型",
    "jargon_explainer": "黑话解释",
    "jargon_learner": "黑话学习器",
    "learner_utils": "学习工具",
    # MaiSaka
    "maisaka_builtin_query_memory": "记忆查询工具",
    "maisaka_builtin_send_emoji": "表情发送工具",
    "maisaka_builtin_view_forward_message": "转发消息查看工具",
    "maisaka_chat_history_visual_refresher": "聊天历史画面刷新",
    "maisaka_chat_loop": "麦麦聊天循环",
    "maisaka_cli_sender": "麦麦命令行发送",
    "maisaka_heuristic_memory": "启发式记忆",
    "maisaka_idle_backoff": "空闲退避",
    "maisaka_jargon_context": "黑话上下文",
    "maisaka_mid_term_memory": "中期记忆",
    "maisaka_monitor": "麦麦监控",
    "maisaka_monitor_message_payload": "监控消息载荷",
    "maisaka_person_profile_injector": "人物画像注入",
    "maisaka_reasoning_engine": "麦麦推理引擎",
    "maisaka_reply_effect": "回复效果追踪",
    "maisaka_reply_effect_storage": "回复效果存储",
    "maisaka_tool_post_execution": "工具执行后处理",
    "maisaka_visual_mode": "麦麦视觉模式",
    # 平台、MCP 与插件运行时
    "mcp_host_llm_bridge": "MCP模型桥接",
    "mcp_service": "MCP服务",
    "platform_io.adapter_policy": "平台适配策略",
    "platform_io.manager": "平台接入管理",
    "plugin_llm_client": "插件模型客户端",
    "plugin_runtime.component_query": "插件组件查询",
    "plugin_runtime.dependency_pipeline": "插件依赖处理",
    "plugin_runtime.host.api_registry": "插件接口注册",
    "plugin_runtime.host.circuit_breaker": "插件熔断器",
    "plugin_update_compatibility": "插件兼容性检查",
    "maibot_sdk.compat.import_hook": "插件SDK兼容导入",
    "maisaka_expression_selector": "表达方式选择",
    "plugin.example": "示例插件",
    "plugin.github.sengokucola.statistics-chart-plugin": "统计图表插件",
    "plugin.local.replyer-regex-guard": "回复正则保护插件",
    "plugin.maibot-team.mai-statstic-plugin": "Mai统计插件",
    "plugin.maibot-team.maibot-helper": "麦麦助手插件",
    "plugin.maibot-team.snowluma-adapter": "SnowLuma适配器",
    "plugin.self_identity_plugin": "自我认知插件",
    "plugin.sengokucola.deepseek-thinking-marker": "DeepSeek思考标记插件",
    "_maibot_plugin_maibot_team_mai_statstic_plugin.client_statistics_service": "Mai统计客户端服务",
    "_maibot_plugin_maibot_team_mai_statstic_plugin.plugin_store_service": "Mai统计存储服务",
    "remote": "远程服务",
    # WebUI
    "webui.ai_search": "WebUI智能搜索",
    "webui.plugin_stats_proxy": "WebUI插件统计代理",
    "webui.websocket": "WebUI连接管理",
    "webui_data_transfer": "WebUI数据迁移",
    # A-Memorix
    "a_memorix.host_service": "A-Memorix宿主服务",
    "A_Memorix.AggregateQueryService": "记忆聚合查询",
    "A_Memorix.AutoImport": "记忆自动导入",
    "A_Memorix.DualPathRetriever": "记忆双路检索",
    "A_Memorix.DynamicThresholdFilter": "记忆动态阈值",
    "A_Memorix.EmbeddingManager": "记忆嵌入管理",
    "A_Memorix.EpisodeRetrievalService": "情节记忆检索",
    "A_Memorix.EpisodeSegmentationService": "情节记忆分段",
    "A_Memorix.EpisodeService": "情节记忆服务",
    "A_Memorix.FormatMigration": "记忆格式迁移",
    "A_Memorix.GraphRelationRecall": "记忆关系召回",
    "A_Memorix.LPMMConverter": "LPMM记忆转换",
    "A_Memorix.LPMMImport": "LPMM记忆导入",
    "A_Memorix.LifecycleOrchestrator": "记忆生命周期编排",
    "A_Memorix.MaiBotMigration": "麦麦记忆迁移",
    "A_Memorix.Matcher": "记忆匹配",
    "A_Memorix.MemoryMonitor": "记忆监控",
    "A_Memorix.MetadataFTS": "记忆元数据检索",
    "A_Memorix.MetadataSchema": "记忆元数据结构",
    "A_Memorix.MetadataStore": "记忆元数据存储",
    "A_Memorix.ModelRouting": "记忆模型路由",
    "A_Memorix.PersonProfileService": "人物画像服务",
    "A_Memorix.PersonalizedPageRank": "个性化记忆排序",
    "A_Memorix.Quantization": "记忆向量量化",
    "A_Memorix.RelationWriteService": "记忆关系写入",
    "A_Memorix.RetrievalTuningManager": "记忆检索调优",
    "A_Memorix.RuntimeSelfCheck": "记忆运行自检",
    "A_Memorix.SDKMemoryKernel": "记忆内核",
    "A_Memorix.SearchExecutionService": "记忆搜索执行",
    "A_Memorix.SearchHitProcessingService": "记忆命中处理",
    "A_Memorix.SearchRuntimeInitializer": "记忆检索初始化",
    "A_Memorix.SparseBM25": "记忆稀疏检索",
    "A_Memorix.SummaryImporter": "记忆摘要导入",
    "A_Memorix.VectorStore": "记忆向量存储",
    "A_Memorix.WebImportManager": "网页记忆导入",
}

RESET_COLOR = "\033[0m"

CONVERTED_MODULE_COLORS = {}


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return int(s[:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def supports_truecolor() -> bool:
    # Apple 自带终端使用 256 色编码，避免部分 24 位颜色被显示为默认白色。
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return False

    color_term = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in color_term or "24bit" in color_term:
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
