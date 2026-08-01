# 定义模块颜色映射
import itertools
import os
import sys
from typing import Dict, Optional, Tuple


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
    # A_Memorix 记忆系统
    "a_memorix.host_service": ("#5f5fff", None, False),
    "A_Memorix.SDKMemoryKernel": ("#5f5fd7", None, False),
    "A_Memorix.LifecycleOrchestrator": ("#5f5faf", None, False),
    "A_Memorix.RuntimeSelfCheck": ("#5f7aff", None, False),
    "A_Memorix.MemoryMonitor": ("#5f87ff", None, False),
    "A_Memorix.ModelRouting": ("#5f87d7", None, False),
    "A_Memorix.EmbeddingManager": ("#5f87af", None, False),
    "A_Memorix.VectorStore": ("#5faaff", None, False),
    "A_Memorix.MetadataStore": ("#5faad7", None, False),
    "A_Memorix.MetadataSchema": ("#5fad7f", None, False),
    "A_Memorix.MetadataFTS": ("#5faaa5", None, False),
    "A_Memorix.DualPathRetriever": ("#5fd7aa", None, False),
    "A_Memorix.SearchRuntimeInitializer": ("#5fd7d7", None, False),
    "A_Memorix.SearchExecutionService": ("#5fd7ff", None, False),
    "A_Memorix.SearchHitProcessingService": ("#5fffd7", None, False),
    "A_Memorix.DynamicThresholdFilter": ("#5fffaa", None, False),
    "A_Memorix.SparseBM25": ("#5fff5f", None, False),
    "A_Memorix.GraphRelationRecall": ("#7aff5f", None, False),
    "A_Memorix.Matcher": ("#7affaa", None, False),
    "A_Memorix.AggregateQueryService": ("#7affd7", None, False),
    "A_Memorix.PersonalizedPageRank": ("#7ad7ff", None, False),
    "A_Memorix.Quantization": ("#7aaaff", None, False),
    "A_Memorix.EpisodeService": ("#7a7aff", None, False),
    "A_Memorix.EpisodeSegmentationService": ("#7a5fff", None, False),
    "A_Memorix.EpisodeRetrievalService": ("#9a5fff", None, False),
    "A_Memorix.PersonProfileService": ("#9a7aff", None, False),
    "A_Memorix.RelationWriteService": ("#9aaaff", None, False),
    "A_Memorix.RetrievalTuningManager": ("#9ad7ff", None, False),
    "A_Memorix.SummaryImporter": ("#9affd7", None, False),
    "A_Memorix.WebImportManager": ("#9affaa", None, False),
    "A_Memorix.LPMMImport": ("#9aff5f", None, False),
    "A_Memorix.LPMMConverter": ("#9ad75f", None, False),
    "A_Memorix.FormatMigration": ("#9aaa5f", None, False),
    "A_Memorix.MaiBotMigration": ("#9a7a5f", None, False),
    "A_Memorix.AutoImport": ("#9a5f5f", None, False),
    # Maisaka 麦酒
    "maisaka_chat_history_visual_refresher": ("#0fd5d5", None, False),
    "maisaka_cli_sender": ("#1bb2ed", None, False),
    "maisaka_expression_selector": ("#5fd7af", None, False),
    "maisaka_heuristic_memory": ("#5fd7d7", None, False),
    "maisaka_idle_backoff": ("#5fd5a0", None, False),
    "maisaka_jargon_context": ("#5fafaf", None, False),
    "maisaka_mid_term_memory": ("#5faaaa", None, False),
    "maisaka_monitor": ("#5faa87", None, False),
    "maisaka_monitor_event_store": ("#5faa5f", None, False),
    "maisaka_monitor_message_payload": ("#5fd75f", None, False),
    "maisaka_person_profile_injector": ("#5fd787", None, False),
    "maisaka_tool_post_execution": ("#5fd7aa", None, False),
    "maisaka_visual_mode": ("#5fd7c5", None, False),
    "maisaka_builtin_context": ("#5fd5d5", None, False),
    "maisaka_builtin_query_memory": ("#5fd5af", None, False),
    "maisaka_builtin_reply": ("#5fd587", None, False),
    "maisaka_builtin_send_emoji": ("#5fd55f", None, False),
    "maisaka_builtin_send_image": ("#5fd57a", None, False),
    "maisaka_builtin_view_forward_message": ("#5fd5aa", None, False),
    "maisaka.browser_tool": ("#1bd5c5", None, False),
    # 服务层
    "database": ("#00d700", None, False),
    "database_migration": ("#00d75f", None, False),
    "database_service": ("#00d7af", None, False),
    "embedding_service": ("#00d7d7", None, False),
    "generator_service": ("#00af5f", None, False),
    "global_announcement_manager": ("#00afaf", None, False),
    "image_path_maintenance_service": ("#00af87", None, False),
    "llm_service": ("#00afaa", None, False),
    "mcp_service": ("#00afd7", None, False),
    "memory_flow_service": ("#5faf00", None, False),
    "memory_service": ("#5faf5f", None, False),
    "send_service": ("#5fafaf", None, False),
    "service_task_resolver": ("#5faf87", None, False),
    "services.html_render_service": ("#5fafd7", None, False),
    "statistics_aggregation_service": ("#87af00", None, False),
    "statistics_service": ("#87af5f", None, False),
    "tool_record_cleanup_service": ("#87afaf", None, False),
    # 行为学习
    "behavior_learner": ("#d78700", None, False),
    "behavior_pattern_maintenance": ("#d7875f", None, False),
    "behavior_pattern_store": ("#d7af00", None, False),
    "behavior_scenario": ("#d7af5f", None, False),
    "behavior_scene_cluster": ("#d7af87", None, False),
    "behavior_selector": ("#d7aafa", None, False),
    # LLM/模型
    "ConfigBase": ("#d700d7", None, False),
    "llm_adapter_base": ("#d75fd7", None, False),
    "llm_cache_stats": ("#d75faf", None, False),
    "llm_request_snapshot": ("#af5fd7", None, False),
    "model_client_registry": ("#af5faf", None, False),
    # 黑话
    "jargon_data_model": ("#ffd700", None, False),
    "jargon_explainer": ("#d7af00", None, False),
    "jargon_learner": ("#d7d75f", None, False),
    # 插件系统
    "plugin_runtime.component_query": ("#af5f00", None, False),
    "plugin_runtime.dependency_pipeline": ("#d75f00", None, False),
    "plugin_runtime.host.api_registry": ("#af5f5f", None, False),
    "plugin_runtime.host.circuit_breaker": ("#d75f5f", None, False),
    "plugin_update_compatibility": ("#af7a5f", None, False),
    # 平台/消息
    "platform_io.adapter_policy": ("#5faf87", None, False),
    "platform_io.manager": ("#5fd7af", None, False),
    "message_server": ("#5faa87", None, False),
    "message_utils": ("#5fd7a5", None, False),
    "base_message_component_model": ("#87d7af", None, False),
    # WebUI扩展
    "webui.ai_search": ("#5f87d7", None, False),
    "webui.plugin_stats_proxy": ("#5fafff", None, False),
    "webui.unified_ws": ("#5fd7ff", None, False),
    "webui.websocket": ("#87afff", None, False),
    "webui_data_transfer": ("#87d7ff", None, False),
    "maim_message_api_server": ("#af87ff", None, False),
    # 工具/杂项
    "common_utils": ("#87afaf", None, False),
    "config_utils": ("#afaf87", None, False),
    "core.tooling": ("#87af87", None, False),
    "emoji_maisaka_tool": ("#afafaf", None, False),
    "event_bus": ("#8787af", None, False),
    "event_helpers": ("#87afda", None, False),
    "expression_vector_index": ("#afdaaf", None, False),
    "file_utils": ("#87dada", None, False),
    "file_watcher": ("#af87af", None, False),
    "heartflow": ("#daaf87", None, False),
    "image_receive_compressor": ("#87af5f", None, False),
    "image_utils": ("#afaf5f", None, False),
    "learner_utils": ("#5f87af", None, False),
    "mcp_host_llm_bridge": ("#d7af5f", None, False),
    "person_utils": ("#af5f87", None, False),
    "Prompt": ("#ffaf00", None, False),
    "ReplyerManager": ("#d75f00", None, False),
    "typo_gen": ("#87d7ff", None, False),
    "voice_utils": ("#af87d7", None, False),
}

# 定义模块别名映射 - 将真实的logger名称映射到显示的别名
MODULE_ALIASES = {
    # 示例映射
    "sender": "消息发送",
    "send_api": "消息发送API",
    "replyer": "言语",
    "llm_api": "生成API",
    "image": "图片",
    "image_cache_cleanup": "图片缓存清理",
    "emoji": "表情包",
    "emoji_cache_cleanup": "表情包缓存清理",
    "chat": "所见",
    "maisaka_turn_scheduler": "读空气",
    "chat_image": "识图",
    "action_manager": "动作",
    "memory_activator": "记忆",
    "tool_use": "工具",
    "expressor": "表达方式",
    "expression_utils": "表达方式",
    "database_model": "数据库",
    "tool_executor": "工具",
    "plugin_manager": "插件",
    "llm_models": "模型",
    "person_info": "人物",
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
    # A_Memorix 记忆系统
    "a_memorix.host_service": "记忆宿主服务",
    "A_Memorix.SDKMemoryKernel": "记忆内核",
    "A_Memorix.LifecycleOrchestrator": "生命周期编排",
    "A_Memorix.RuntimeSelfCheck": "运行时自检",
    "A_Memorix.MemoryMonitor": "记忆监控",
    "A_Memorix.ModelRouting": "模型路由",
    "A_Memorix.EmbeddingManager": "嵌入管理",
    "A_Memorix.VectorStore": "向量存储",
    "A_Memorix.MetadataStore": "元数据存储",
    "A_Memorix.MetadataSchema": "元数据Schema",
    "A_Memorix.MetadataFTS": "元数据全文检索",
    "A_Memorix.DualPathRetriever": "双路检索",
    "A_Memorix.SearchRuntimeInitializer": "检索运行时初始化",
    "A_Memorix.SearchExecutionService": "检索执行",
    "A_Memorix.SearchHitProcessingService": "检索命中处理",
    "A_Memorix.DynamicThresholdFilter": "动态阈值",
    "A_Memorix.SparseBM25": "稀疏检索",
    "A_Memorix.GraphRelationRecall": "图关系召回",
    "A_Memorix.Matcher": "匹配器",
    "A_Memorix.AggregateQueryService": "聚合查询",
    "A_Memorix.PersonalizedPageRank": "个性化PageRank",
    "A_Memorix.Quantization": "量化",
    "A_Memorix.EpisodeService": "情景记忆",
    "A_Memorix.EpisodeSegmentationService": "情景切分",
    "A_Memorix.EpisodeRetrievalService": "情景检索",
    "A_Memorix.PersonProfileService": "人物画像",
    "A_Memorix.RelationWriteService": "关系写入",
    "A_Memorix.RetrievalTuningManager": "检索调优",
    "A_Memorix.SummaryImporter": "摘要导入",
    "A_Memorix.WebImportManager": "Web导入",
    "A_Memorix.LPMMImport": "LPMM导入",
    "A_Memorix.LPMMConverter": "LPMM转换",
    "A_Memorix.FormatMigration": "格式迁移",
    "A_Memorix.MaiBotMigration": "MaiBot迁移",
    "A_Memorix.AutoImport": "自动导入",
    # Maisaka 麦酒
    "maisaka_reasoning_engine": "MaiSaka推理引擎",
    "maisaka_chat_loop": "MaiSaka聊天循环",
    "maisaka_chat_history_visual_refresher": "MaiSaka聊天历史刷新",
    "maisaka_cli_sender": "MaiSaka CLI发送器",
    "maisaka_expression_selector": "MaiSaka表达选择器",
    "maisaka_heuristic_memory": "MaiSaka启发式记忆",
    "maisaka_idle_backoff": "MaiSaka空闲退避",
    "maisaka_jargon_context": "MaiSaka黑话上下文",
    "maisaka_mid_term_memory": "MaiSaka中期记忆",
    "maisaka_monitor": "MaiSaka监控",
    "maisaka_monitor_event_store": "MaiSaka监控事件存储",
    "maisaka_monitor_message_payload": "MaiSaka监控消息载荷",
    "maisaka_person_profile_injector": "MaiSaka人物画像注入",
    "maisaka_tool_post_execution": "MaiSaka工具后执行",
    "maisaka_visual_mode": "MaiSaka视觉模式",
    "maisaka_builtin_context": "MaiSaka内置上下文",
    "maisaka_builtin_query_memory": "MaiSaka内置查询记忆",
    "maisaka_builtin_reply": "MaiSaka内置回复",
    "maisaka_builtin_send_emoji": "MaiSaka内置发表情",
    "maisaka_builtin_send_image": "MaiSaka内置发图",
    "maisaka_builtin_view_forward_message": "MaiSaka内置查看转发",
    "maisaka.browser_tool": "MaiSaka浏览器工具",
    # 服务层
    "database": "数据库核心",
    "database_migration": "数据库迁移",
    "database_service": "数据库服务",
    "embedding_service": "嵌入服务",
    "generator_service": "生成服务",
    "global_announcement_manager": "全局公告管理",
    "image_path_maintenance_service": "图片路径维护",
    "llm_service": "LLM服务",
    "mcp_service": "MCP服务",
    "memory_flow_service": "记忆流服务",
    "memory_service": "记忆服务",
    "send_service": "发送服务",
    "service_task_resolver": "服务任务解析",
    "services.html_render_service": "HTML渲染服务",
    "statistics_aggregation_service": "统计聚合服务",
    "statistics_service": "统计服务",
    "tool_record_cleanup_service": "工具记录清理",
    # 行为学习
    "behavior_learner": "行为学习",
    "behavior_pattern_maintenance": "行为模式维护",
    "behavior_pattern_store": "行为模式存储",
    "behavior_scenario": "行为场景",
    "behavior_scene_cluster": "行为场景聚类",
    "behavior_selector": "行为选择器",
    # LLM/模型
    "ConfigBase": "配置基类",
    "llm_adapter_base": "LLM适配器",
    "llm_cache_stats": "LLM缓存统计",
    "llm_request_snapshot": "LLM请求快照",
    "model_client_registry": "模型客户端注册",
    # 黑话
    "jargon_data_model": "黑话数据模型",
    "jargon_explainer": "黑话解释",
    "jargon_learner": "黑话学习",
    # 插件系统
    "plugin_runtime.component_query": "插件组件查询",
    "plugin_runtime.dependency_pipeline": "插件依赖管线",
    "plugin_runtime.host.api_registry": "插件API注册",
    "plugin_runtime.host.circuit_breaker": "插件熔断器",
    "plugin_update_compatibility": "插件更新兼容",
    # 平台/消息
    "platform_io.adapter_policy": "适配器策略",
    "platform_io.manager": "平台管理器",
    "message_server": "消息服务器",
    "message_utils": "消息工具",
    "base_message_component_model": "消息组件基类",
    # WebUI扩展
    "webui.ai_search": "WebUI AI搜索",
    "webui.plugin_stats_proxy": "WebUI插件统计代理",
    "webui.unified_ws": "WebUI统一WS",
    "webui.websocket": "WebUI WebSocket",
    "webui_data_transfer": "WebUI数据传输",
    "maim_message_api_server": "消息API服务器",
    # 有颜色无别名
    "async_task_manager": "异步任务管理",
    "chat_message": "聊天消息",
    "chat_utils": "聊天工具",
    "confirm": "确认",
    "jargon": "黑话",
    "local_storage": "本地存储",
    "logger": "日志系统",
    "maibot_statistic": "麦麦统计",
    "maim_message": "消息总线",
    "model_utils": "模型工具",
    "remote": "远程",
    "update_notice": "更新通知",
    # 工具/杂项
    "common_utils": "通用工具",
    "config_utils": "配置工具",
    "core.tooling": "核心工具",
    "emoji_maisaka_tool": "表情麦酒工具",
    "event_bus": "事件总线",
    "event_helpers": "事件助手",
    "expression_vector_index": "表达向量索引",
    "file_utils": "文件工具",
    "file_watcher": "文件监听",
    "heartflow": "心流",
    "image_receive_compressor": "图片接收压缩",
    "image_utils": "图片工具",
    "learner_utils": "学习器工具",
    "mcp_host_llm_bridge": "MCP桥接",
    "person_utils": "人物工具",
    "Prompt": "提示词",
    "ReplyerManager": "回复管理器",
    "typo_gen": "错别字生成",
    "voice_utils": "语音工具",
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
