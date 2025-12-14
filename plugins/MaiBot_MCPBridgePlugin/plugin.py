"""
MCP 桥接插件 v1.7.0
将 MCP (Model Context Protocol) 服务器的工具桥接到 MaiBot

v1.7.0 稳定性与易用性优化:
- 断路器模式：故障服务器快速失败，避免拖慢整体响应
- 状态实时刷新：WebUI 每 10 秒自动更新连接状态
- 断路器状态显示：在状态面板显示熔断/试探状态

v1.6.0 配置导入导出:
- 新增 /mcp import 命令，支持从 Claude Desktop 格式导入配置
- 新增 /mcp export 命令，导出为 Claude Desktop / Kiro / MaiBot 格式
- 支持 stdio、sse、http、streamable_http 全部传输类型
- 自动跳过同名服务器，防止重复导入

v1.5.4 易用性优化:
- 新增 MCP 服务器获取快捷入口（魔搭、Smithery、Glama 等）
- 优化快速入门指南，提供配置示例
- 帮助新用户快速上手 MCP

v1.5.3 配置优化:
- 新增智能心跳 WebUI 配置项：启用开关、最大间隔倍数
- 支持在 WebUI 中开启/关闭智能心跳功能

v1.5.2 性能优化:
- 智能心跳间隔：根据服务器稳定性动态调整心跳频率
- 稳定服务器逐渐增加间隔，减少不必要的网络请求
- 断开的服务器使用较短间隔快速重连

v1.5.1 易用性优化:
- 新增「快速添加服务器」表单式配置，无需手写 JSON
- 支持填写名称、类型、URL、命令、参数、鉴权头
- 保存后自动合并到服务器列表

v1.5.0 性能优化:
- 服务器并行连接：多个服务器同时连接，大幅减少启动时间
- 连接耗时统计：日志显示并行连接总耗时

v1.4.4 修复:
- 修复首次生成默认配置文件时多行字符串导致 TOML 解析失败的问题
- 简化 config_schema 默认值，避免主程序 json.dumps 产生无效 TOML

v1.4.3 修复:
- 修复 WebUI 保存配置后多行字符串格式错误导致配置文件无法读取的问题
- 清理未使用的导入

v1.4.0 新增功能:
- 工具禁用管理
- 调用链路追踪
- 工具调用缓存
- 工具权限控制
"""

import asyncio
import fnmatch
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from src.common.logger import get_logger
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseTool,
    BaseCommand,
    ComponentInfo,
    ConfigField,
    ToolParamType,
)
from src.plugin_system.base.component_types import ToolInfo, ComponentType, EventType
from src.plugin_system.base.base_events_handler import BaseEventHandler

from .mcp_client import (
    MCPServerConfig,
    MCPToolInfo,
    MCPResourceInfo,
    MCPPromptInfo,
    TransportType,
    mcp_manager,
)
from .config_converter import ConfigConverter

logger = get_logger("mcp_bridge_plugin")


# ============================================================================
# v1.4.0: 调用链路追踪
# ============================================================================


@dataclass
class ToolCallRecord:
    """工具调用记录"""

    call_id: str
    timestamp: float
    tool_name: str
    server_name: str
    chat_id: str = ""
    user_id: str = ""
    user_query: str = ""
    arguments: Dict = field(default_factory=dict)
    raw_result: str = ""
    processed_result: str = ""
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""
    post_processed: bool = False
    cache_hit: bool = False


class ToolCallTracer:
    """工具调用追踪器"""

    def __init__(self, max_records: int = 100):
        self._records: deque[ToolCallRecord] = deque(maxlen=max_records)
        self._enabled: bool = True
        self._log_enabled: bool = False
        self._log_path: Optional[Path] = None

    def configure(self, enabled: bool, max_records: int, log_enabled: bool, log_path: Optional[Path] = None) -> None:
        """配置追踪器"""
        self._enabled = enabled
        self._records = deque(self._records, maxlen=max_records)
        self._log_enabled = log_enabled
        self._log_path = log_path

    def record(self, record: ToolCallRecord) -> None:
        """添加调用记录"""
        if not self._enabled:
            return

        self._records.append(record)

        if self._log_enabled and self._log_path:
            self._write_to_log(record)

    def get_recent(self, n: int = 10) -> List[ToolCallRecord]:
        """获取最近 N 条记录"""
        return list(self._records)[-n:]

    def get_by_tool(self, tool_name: str) -> List[ToolCallRecord]:
        """按工具名筛选记录"""
        return [r for r in self._records if r.tool_name == tool_name]

    def get_by_server(self, server_name: str) -> List[ToolCallRecord]:
        """按服务器名筛选记录"""
        return [r for r in self._records if r.server_name == server_name]

    def clear(self) -> None:
        """清空记录"""
        self._records.clear()

    def _write_to_log(self, record: ToolCallRecord) -> None:
        """写入 JSONL 日志文件"""
        try:
            if self._log_path:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写入追踪日志失败: {e}")

    @property
    def total_records(self) -> int:
        return len(self._records)


# 全局追踪器实例
tool_call_tracer = ToolCallTracer()


# ============================================================================
# v1.4.0: 工具调用缓存
# ============================================================================


@dataclass
class CacheEntry:
    """缓存条目"""

    tool_name: str
    args_hash: str
    result: str
    created_at: float
    expires_at: float
    hit_count: int = 0


class ToolCallCache:
    """工具调用缓存（LRU）"""

    def __init__(self, max_entries: int = 200, ttl: int = 300):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl
        self._enabled = False
        self._exclude_patterns: List[str] = []
        self._stats = {"hits": 0, "misses": 0}

    def configure(self, enabled: bool, ttl: int, max_entries: int, exclude_tools: str) -> None:
        """配置缓存"""
        self._enabled = enabled
        self._ttl = ttl
        self._max_entries = max_entries
        self._exclude_patterns = [p.strip() for p in exclude_tools.strip().split("\n") if p.strip()]

    def get(self, tool_name: str, args: Dict) -> Optional[str]:
        """获取缓存"""
        if not self._enabled:
            return None

        if self._is_excluded(tool_name):
            return None

        key = self._generate_key(tool_name, args)

        if key not in self._cache:
            self._stats["misses"] += 1
            return None

        entry = self._cache[key]

        # 检查是否过期
        if time.time() > entry.expires_at:
            del self._cache[key]
            self._stats["misses"] += 1
            return None

        # LRU: 移到末尾
        self._cache.move_to_end(key)
        entry.hit_count += 1
        self._stats["hits"] += 1

        return entry.result

    def set(self, tool_name: str, args: Dict, result: str) -> None:
        """设置缓存"""
        if not self._enabled:
            return

        if self._is_excluded(tool_name):
            return

        key = self._generate_key(tool_name, args)
        now = time.time()

        entry = CacheEntry(
            tool_name=tool_name,
            args_hash=key,
            result=result,
            created_at=now,
            expires_at=now + self._ttl,
        )

        # 如果已存在，更新
        if key in self._cache:
            self._cache[key] = entry
            self._cache.move_to_end(key)
        else:
            # 检查容量
            self._evict_if_needed()
            self._cache[key] = entry

    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._stats = {"hits": 0, "misses": 0}

    def _generate_key(self, tool_name: str, args: Dict) -> str:
        """生成缓存键"""
        args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
        content = f"{tool_name}:{args_str}"
        return hashlib.md5(content.encode()).hexdigest()

    def _is_excluded(self, tool_name: str) -> bool:
        """检查是否在排除列表中"""
        for pattern in self._exclude_patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                return True
        return False

    def _evict_if_needed(self) -> None:
        """必要时淘汰条目"""
        # 先清理过期的
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
        for k in expired_keys:
            del self._cache[k]

        # LRU 淘汰
        while len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
        return {
            "enabled": self._enabled,
            "entries": len(self._cache),
            "max_entries": self._max_entries,
            "ttl": self._ttl,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": f"{hit_rate:.1f}%",
        }


# 全局缓存实例
tool_call_cache = ToolCallCache()


# ============================================================================
# v1.4.0: 工具权限控制
# ============================================================================


class PermissionChecker:
    """工具权限检查器"""

    def __init__(self):
        self._enabled = False
        self._default_mode = "allow_all"  # allow_all 或 deny_all
        self._rules: List[Dict] = []
        self._quick_deny_groups: set = set()
        self._quick_allow_users: set = set()

    def configure(
        self,
        enabled: bool,
        default_mode: str,
        rules_json: str,
        quick_deny_groups: str = "",
        quick_allow_users: str = "",
    ) -> None:
        """配置权限检查器"""
        self._enabled = enabled
        self._default_mode = default_mode if default_mode in ("allow_all", "deny_all") else "allow_all"

        # 解析快捷配置
        self._quick_deny_groups = {g.strip() for g in quick_deny_groups.strip().split("\n") if g.strip()}
        self._quick_allow_users = {u.strip() for u in quick_allow_users.strip().split("\n") if u.strip()}

        try:
            self._rules = json.loads(rules_json) if rules_json.strip() else []
        except json.JSONDecodeError as e:
            logger.warning(f"权限规则 JSON 解析失败: {e}")
            self._rules = []

    def check(self, tool_name: str, chat_id: str, user_id: str, is_group: bool) -> bool:
        """检查权限

        Args:
            tool_name: 工具名称
            chat_id: 聊天 ID（群号或私聊 ID）
            user_id: 用户 ID
            is_group: 是否为群聊

        Returns:
            True 表示允许，False 表示拒绝
        """
        if not self._enabled:
            return True

        # 快捷配置优先级最高
        # 1. 管理员白名单（始终允许）
        if user_id and user_id in self._quick_allow_users:
            return True

        # 2. 禁用群列表（始终拒绝）
        if is_group and chat_id and chat_id in self._quick_deny_groups:
            return False

        # 查找匹配的规则
        for rule in self._rules:
            tool_pattern = rule.get("tool", "")
            if not self._match_tool(tool_pattern, tool_name):
                continue

            # 找到匹配的规则
            mode = rule.get("mode", "")
            allowed = rule.get("allowed", [])
            denied = rule.get("denied", [])

            # 构建当前上下文的 ID 列表
            context_ids = self._build_context_ids(chat_id, user_id, is_group)

            # 检查 denied 列表（优先级最高）
            if denied:
                for ctx_id in context_ids:
                    if self._match_id_list(denied, ctx_id):
                        return False

            # 检查 allowed 列表
            if allowed:
                for ctx_id in context_ids:
                    if self._match_id_list(allowed, ctx_id):
                        return True
                # 如果是 whitelist 模式且不在 allowed 中，拒绝
                if mode == "whitelist":
                    return False

            # 规则匹配但没有明确允许/拒绝，继续检查下一条规则

        # 没有匹配的规则，使用默认模式
        return self._default_mode == "allow_all"

    def _match_tool(self, pattern: str, tool_name: str) -> bool:
        """工具名通配符匹配"""
        if not pattern:
            return False
        return fnmatch.fnmatch(tool_name, pattern)

    def _build_context_ids(self, chat_id: str, user_id: str, is_group: bool) -> List[str]:
        """构建上下文 ID 列表"""
        ids = []

        # 用户级别（任何场景生效）
        if user_id:
            ids.append(f"qq:{user_id}:user")

        # 场景级别
        if is_group and chat_id:
            ids.append(f"qq:{chat_id}:group")
        elif chat_id:
            ids.append(f"qq:{chat_id}:private")

        return ids

    def _match_id_list(self, id_list: List[str], context_id: str) -> bool:
        """检查 ID 是否在列表中"""
        for rule_id in id_list:
            if fnmatch.fnmatch(context_id, rule_id):
                return True
        return False

    def get_rules_for_tool(self, tool_name: str) -> List[Dict]:
        """获取特定工具的权限规则"""
        return [r for r in self._rules if self._match_tool(r.get("tool", ""), tool_name)]


# 全局权限检查器实例
permission_checker = PermissionChecker()


# ============================================================================
# 工具类型转换
# ============================================================================


def convert_json_type_to_tool_param_type(json_type: str) -> ToolParamType:
    """将 JSON Schema 类型转换为 MaiBot 的 ToolParamType"""
    type_mapping = {
        "string": ToolParamType.STRING,
        "integer": ToolParamType.INTEGER,
        "number": ToolParamType.FLOAT,
        "boolean": ToolParamType.BOOLEAN,
        "array": ToolParamType.STRING,
        "object": ToolParamType.STRING,
    }
    return type_mapping.get(json_type, ToolParamType.STRING)


def parse_mcp_parameters(
    input_schema: Dict[str, Any],
) -> List[Tuple[str, ToolParamType, str, bool, Optional[List[str]]]]:
    """解析 MCP 工具的参数 schema，转换为 MaiBot 的参数格式"""
    parameters = []

    if not input_schema:
        return parameters

    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    for param_name, param_info in properties.items():
        json_type = param_info.get("type", "string")
        param_type = convert_json_type_to_tool_param_type(json_type)
        description = param_info.get("description", f"参数 {param_name}")

        if json_type == "array":
            description = f"{description} (JSON 数组格式)"
        elif json_type == "object":
            description = f"{description} (JSON 对象格式)"

        is_required = param_name in required
        enum_values = param_info.get("enum")

        if enum_values is not None:
            enum_values = [str(v) for v in enum_values]

        parameters.append((param_name, param_type, description, is_required, enum_values))

    return parameters


# ============================================================================
# MCP 工具代理
# ============================================================================


class MCPToolProxy(BaseTool):
    """MCP 工具代理基类"""

    name: str = ""
    description: str = ""
    parameters: List[Tuple[str, ToolParamType, str, bool, Optional[List[str]]]] = []
    available_for_llm: bool = True

    _mcp_tool_key: str = ""
    _mcp_original_name: str = ""
    _mcp_server_name: str = ""

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行 MCP 工具调用"""
        global _plugin_instance

        call_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # 移除 MaiBot 内部标记
        args = {k: v for k, v in function_args.items() if k != "llm_called"}

        # 解析 JSON 字符串参数
        parsed_args = {}
        for key, value in args.items():
            if isinstance(value, str):
                try:
                    if value.startswith(("[", "{")):
                        parsed_args[key] = json.loads(value)
                    else:
                        parsed_args[key] = value
                except json.JSONDecodeError:
                    parsed_args[key] = value
            else:
                parsed_args[key] = value

        # 获取上下文信息
        chat_id, user_id, is_group, user_query = self._get_context_info()

        # v1.4.0: 权限检查
        if not permission_checker.check(self.name, chat_id, user_id, is_group):
            logger.warning(f"权限拒绝: 工具 {self.name}, chat={chat_id}, user={user_id}")
            return {"name": self.name, "content": f"⛔ 权限不足：工具 {self.name} 在当前场景下不可用"}

        logger.debug(f"调用 MCP 工具: {self._mcp_tool_key}, 参数: {parsed_args}")

        # v1.4.0: 检查缓存
        cache_hit = False
        cached_result = tool_call_cache.get(self.name, parsed_args)

        if cached_result is not None:
            cache_hit = True
            content = cached_result
            raw_result = cached_result
            success = True
            error = ""
            logger.debug(f"MCP 工具 {self.name} 命中缓存")
        else:
            # 调用 MCP
            result = await mcp_manager.call_tool(self._mcp_tool_key, parsed_args)

            if result.success:
                content = result.content
                raw_result = content
                success = True
                error = ""

                # 存入缓存
                tool_call_cache.set(self.name, parsed_args, content)
            else:
                content = self._format_error_message(result.error, result.duration_ms)
                raw_result = result.error
                success = False
                error = result.error
                logger.warning(f"MCP 工具 {self.name} 调用失败: {result.error}")

        # v1.3.0: 后处理
        post_processed = False
        processed_result = content
        if success:
            processed_content = await self._post_process_result(content)
            if processed_content != content:
                post_processed = True
                processed_result = processed_content
                content = processed_content

        duration_ms = (time.time() - start_time) * 1000

        # v1.4.0: 记录调用追踪
        record = ToolCallRecord(
            call_id=call_id,
            timestamp=start_time,
            tool_name=self.name,
            server_name=self._mcp_server_name,
            chat_id=chat_id,
            user_id=user_id,
            user_query=user_query,
            arguments=parsed_args,
            raw_result=raw_result[:1000] if raw_result else "",
            processed_result=processed_result[:1000] if processed_result else "",
            duration_ms=duration_ms,
            success=success,
            error=error,
            post_processed=post_processed,
            cache_hit=cache_hit,
        )
        tool_call_tracer.record(record)

        return {"name": self.name, "content": content}

    def _get_context_info(self) -> Tuple[str, str, bool, str]:
        """获取上下文信息"""
        chat_id = ""
        user_id = ""
        is_group = False
        user_query = ""

        if self.chat_stream and hasattr(self.chat_stream, "context") and self.chat_stream.context:
            try:
                ctx = self.chat_stream.context
                if hasattr(ctx, "chat_id"):
                    chat_id = str(ctx.chat_id) if ctx.chat_id else ""
                if hasattr(ctx, "user_id"):
                    user_id = str(ctx.user_id) if ctx.user_id else ""
                if hasattr(ctx, "is_group"):
                    is_group = bool(ctx.is_group)

                last_message = ctx.get_last_message()
                if last_message and hasattr(last_message, "processed_plain_text"):
                    user_query = last_message.processed_plain_text or ""
            except Exception as e:
                logger.debug(f"获取上下文信息失败: {e}")

        return chat_id, user_id, is_group, user_query

    async def _post_process_result(self, content: str) -> str:
        """v1.3.0: 对工具返回结果进行后处理（摘要提炼）"""
        global _plugin_instance

        if _plugin_instance is None:
            return content

        settings = _plugin_instance.config.get("settings", {})

        if not settings.get("post_process_enabled", False):
            return content

        server_post_config = self._get_server_post_process_config()

        if server_post_config is not None:
            if not server_post_config.get("enabled", True):
                return content

        threshold = settings.get("post_process_threshold", 500)
        if server_post_config and "threshold" in server_post_config:
            threshold = server_post_config["threshold"]

        content_length = len(content) if content else 0
        if content_length <= threshold:
            return content

        user_query = self._get_context_info()[3]
        if not user_query:
            return content

        max_tokens = settings.get("post_process_max_tokens", 500)
        if server_post_config and "max_tokens" in server_post_config:
            max_tokens = server_post_config["max_tokens"]

        prompt_template = settings.get("post_process_prompt", "")
        if server_post_config and "prompt" in server_post_config:
            prompt_template = server_post_config["prompt"]

        if not prompt_template:
            prompt_template = """用户问题：{query}

工具返回内容：
{result}

请从上述内容中提取与用户问题最相关的关键信息，简洁准确地输出："""

        try:
            prompt = prompt_template.format(query=user_query, result=content)
        except KeyError as e:
            logger.warning(f"后处理 prompt 模板格式错误: {e}")
            return content

        try:
            processed_content = await self._call_post_process_llm(prompt, max_tokens, settings, server_post_config)
            if processed_content:
                logger.info(f"MCP 工具 {self.name} 后处理完成: {content_length} -> {len(processed_content)} 字符")
                return processed_content
            return content
        except Exception as e:
            logger.error(f"MCP 工具 {self.name} 后处理失败: {e}")
            return content

    def _get_server_post_process_config(self) -> Optional[Dict[str, Any]]:
        """获取当前服务器的后处理配置"""
        global _plugin_instance

        if _plugin_instance is None:
            return None

        servers_section = _plugin_instance.config.get("servers", {})
        if isinstance(servers_section, dict):
            servers_list = servers_section.get("list", "[]")
            if isinstance(servers_list, str):
                try:
                    servers = json.loads(servers_list) if servers_list.strip() else []
                except json.JSONDecodeError:
                    return None
            elif isinstance(servers_list, list):
                servers = servers_list
            else:
                return None
        else:
            servers = servers_section if isinstance(servers_section, list) else []

        for server_conf in servers:
            if server_conf.get("name") == self._mcp_server_name:
                return server_conf.get("post_process")

        return None

    async def _call_post_process_llm(
        self, prompt: str, max_tokens: int, settings: Dict[str, Any], server_config: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """调用 LLM 进行后处理"""
        from src.config.config import model_config
        from src.config.api_ada_configs import TaskConfig
        from src.llm_models.utils_model import LLMRequest

        model_name = settings.get("post_process_model", "")
        if server_config and "model" in server_config:
            model_name = server_config["model"]

        if model_name:
            task_config = TaskConfig(
                model_list=[model_name],
                max_tokens=max_tokens,
                temperature=0.3,
                slow_threshold=30.0,
            )
        else:
            task_config = model_config.model_task_config.utils

        llm_request = LLMRequest(model_set=task_config, request_type="mcp_post_process")

        response, (reasoning, model_used, _) = await llm_request.generate_response_async(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.3,
        )

        return response.strip() if response else None

    def _format_error_message(self, error: str, duration_ms: float) -> str:
        """格式化友好的错误消息"""
        if not error:
            return "工具调用失败（未知错误）"

        error_lower = error.lower()

        if "未连接" in error or "not connected" in error_lower:
            return f"⚠️ MCP 服务器 [{self._mcp_server_name}] 未连接，请检查服务器状态或等待自动重连"

        if "超时" in error or "timeout" in error_lower:
            return f"⏱️ 工具调用超时（耗时 {duration_ms:.0f}ms），服务器响应过慢，请稍后重试"

        if "connection" in error_lower and ("closed" in error_lower or "reset" in error_lower):
            return f"🔌 与 MCP 服务器 [{self._mcp_server_name}] 的连接已断开，正在尝试重连..."

        if "invalid" in error_lower and "argument" in error_lower:
            return f"❌ 参数错误: {error}"

        return f"❌ 工具调用失败: {error}"

    async def direct_execute(self, **function_args) -> Dict[str, Any]:
        """直接执行（供其他插件调用）"""
        return await self.execute(function_args)


def create_mcp_tool_class(
    tool_key: str, tool_info: MCPToolInfo, tool_prefix: str, disabled: bool = False
) -> Type[MCPToolProxy]:
    """根据 MCP 工具信息动态创建 BaseTool 子类"""
    parameters = parse_mcp_parameters(tool_info.input_schema)

    class_name = f"MCPTool_{tool_info.server_name}_{tool_info.name}".replace("-", "_").replace(".", "_")
    tool_name = tool_key.replace("-", "_").replace(".", "_")

    description = tool_info.description
    if not description.endswith(f"[来自 MCP 服务器: {tool_info.server_name}]"):
        description = f"{description} [来自 MCP 服务器: {tool_info.server_name}]"

    tool_class = type(
        class_name,
        (MCPToolProxy,),
        {
            "name": tool_name,
            "description": description,
            "parameters": parameters,
            "available_for_llm": not disabled,  # v1.4.0: 禁用的工具不可被 LLM 调用
            "_mcp_tool_key": tool_key,
            "_mcp_original_name": tool_info.name,
            "_mcp_server_name": tool_info.server_name,
        },
    )

    return tool_class


class MCPToolRegistry:
    """MCP 工具注册表"""

    def __init__(self):
        self._tool_classes: Dict[str, Type[MCPToolProxy]] = {}
        self._tool_infos: Dict[str, ToolInfo] = {}

    def register_tool(
        self, tool_key: str, tool_info: MCPToolInfo, tool_prefix: str, disabled: bool = False
    ) -> Tuple[ToolInfo, Type[MCPToolProxy]]:
        """注册 MCP 工具"""
        tool_class = create_mcp_tool_class(tool_key, tool_info, tool_prefix, disabled)

        self._tool_classes[tool_key] = tool_class

        info = ToolInfo(
            name=tool_class.name,
            tool_description=tool_class.description,
            enabled=True,
            tool_parameters=tool_class.parameters,
            component_type=ComponentType.TOOL,
        )
        self._tool_infos[tool_key] = info

        return info, tool_class

    def unregister_tool(self, tool_key: str) -> bool:
        """注销工具"""
        if tool_key in self._tool_classes:
            del self._tool_classes[tool_key]
            del self._tool_infos[tool_key]
            return True
        return False

    def get_all_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """获取所有工具组件"""
        return [(self._tool_infos[key], self._tool_classes[key]) for key in self._tool_classes.keys()]

    def clear(self) -> None:
        """清空所有注册"""
        self._tool_classes.clear()
        self._tool_infos.clear()


# 全局工具注册表
mcp_tool_registry = MCPToolRegistry()

# 全局插件实例引用
_plugin_instance: Optional["MCPBridgePlugin"] = None


# ============================================================================
# 内置工具
# ============================================================================


class MCPReadResourceTool(BaseTool):
    """v1.2.0: MCP 资源读取工具"""

    name = "mcp_read_resource"
    description = "读取 MCP 服务器提供的资源内容（如文件、数据库记录等）。使用前请先用 mcp_status 查看可用资源。"
    parameters = [
        ("uri", ToolParamType.STRING, "资源 URI（如 file:///path/to/file 或自定义 URI）", True, None),
        ("server_name", ToolParamType.STRING, "指定服务器名称（可选，不指定则自动查找）", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        uri = function_args.get("uri", "")
        server_name = function_args.get("server_name")

        if not uri:
            return {"name": self.name, "content": "❌ 请提供资源 URI"}

        result = await mcp_manager.read_resource(uri, server_name)

        if result.success:
            return {"name": self.name, "content": result.content}
        else:
            return {"name": self.name, "content": f"❌ 读取资源失败: {result.error}"}

    async def direct_execute(self, **function_args) -> Dict[str, Any]:
        return await self.execute(function_args)


class MCPGetPromptTool(BaseTool):
    """v1.2.0: MCP 提示模板工具"""

    name = "mcp_get_prompt"
    description = "获取 MCP 服务器提供的提示模板内容。使用前请先用 mcp_status 查看可用模板。"
    parameters = [
        ("name", ToolParamType.STRING, "提示模板名称", True, None),
        ("arguments", ToolParamType.STRING, "模板参数（JSON 对象格式）", False, None),
        ("server_name", ToolParamType.STRING, "指定服务器名称（可选）", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        prompt_name = function_args.get("name", "")
        arguments_str = function_args.get("arguments", "")
        server_name = function_args.get("server_name")

        if not prompt_name:
            return {"name": self.name, "content": "❌ 请提供提示模板名称"}

        arguments = None
        if arguments_str:
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                return {"name": self.name, "content": "❌ 参数格式错误，请使用 JSON 对象格式"}

        result = await mcp_manager.get_prompt(prompt_name, arguments, server_name)

        if result.success:
            return {"name": self.name, "content": result.content}
        else:
            return {"name": self.name, "content": f"❌ 获取提示模板失败: {result.error}"}

    async def direct_execute(self, **function_args) -> Dict[str, Any]:
        return await self.execute(function_args)


class MCPStatusTool(BaseTool):
    """MCP 状态查询工具"""

    name = "mcp_status"
    description = (
        "查询 MCP 桥接插件的状态，包括服务器连接状态、可用工具列表、资源列表、提示模板列表、调用统计、追踪记录等信息"
    )
    parameters = [
        (
            "query_type",
            ToolParamType.STRING,
            "查询类型",
            False,
            ["status", "tools", "resources", "prompts", "stats", "trace", "cache", "all"],
        ),
        ("server_name", ToolParamType.STRING, "指定服务器名称（可选）", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        query_type = function_args.get("query_type", "status")
        server_name = function_args.get("server_name")

        result_parts = []

        if query_type in ("status", "all"):
            result_parts.append(self._format_status(server_name))

        if query_type in ("tools", "all"):
            result_parts.append(self._format_tools(server_name))

        if query_type in ("resources", "all"):
            result_parts.append(self._format_resources(server_name))

        if query_type in ("prompts", "all"):
            result_parts.append(self._format_prompts(server_name))

        if query_type in ("stats", "all"):
            result_parts.append(self._format_stats(server_name))

        # v1.4.0: 追踪记录
        if query_type in ("trace",):
            result_parts.append(self._format_trace())

        # v1.4.0: 缓存状态
        if query_type in ("cache",):
            result_parts.append(self._format_cache())

        return {"name": self.name, "content": "\n\n".join(result_parts) if result_parts else "未知的查询类型"}

    def _format_status(self, server_name: Optional[str] = None) -> str:
        status = mcp_manager.get_status()
        lines = ["📊 MCP 桥接插件状态"]
        lines.append(f"  总服务器数: {status['total_servers']}")
        lines.append(f"  已连接: {status['connected_servers']}")
        lines.append(f"  已断开: {status['disconnected_servers']}")
        lines.append(f"  可用工具数: {status['total_tools']}")
        lines.append(f"  心跳检测: {'运行中' if status['heartbeat_running'] else '已停止'}")

        lines.append("\n🔌 服务器详情:")
        for name, info in status["servers"].items():
            if server_name and name != server_name:
                continue
            status_icon = "✅" if info["connected"] else "❌"
            enabled_text = "" if info["enabled"] else " (已禁用)"
            lines.append(f"  {status_icon} {name}{enabled_text}")
            lines.append(f"     传输: {info['transport']}, 工具数: {info['tools_count']}")
            if info["consecutive_failures"] > 0:
                lines.append(f"     ⚠️ 连续失败: {info['consecutive_failures']} 次")

        return "\n".join(lines)

    def _format_tools(self, server_name: Optional[str] = None) -> str:
        tools = mcp_manager.all_tools
        lines = ["🔧 可用 MCP 工具"]

        by_server: Dict[str, List[str]] = {}
        for tool_key, (tool_info, _) in tools.items():
            if server_name and tool_info.server_name != server_name:
                continue
            if tool_info.server_name not in by_server:
                by_server[tool_info.server_name] = []
            by_server[tool_info.server_name].append(f"  • {tool_key}: {tool_info.description[:50]}...")

        for srv_name, tool_list in by_server.items():
            lines.append(f"\n📦 {srv_name} ({len(tool_list)} 个工具):")
            lines.extend(tool_list)

        if not by_server:
            lines.append("  (无可用工具)")

        return "\n".join(lines)

    def _format_stats(self, server_name: Optional[str] = None) -> str:
        stats = mcp_manager.get_all_stats()
        lines = ["📈 调用统计"]

        g = stats["global"]
        lines.append(f"  总调用次数: {g['total_tool_calls']}")
        lines.append(f"  成功: {g['successful_calls']}, 失败: {g['failed_calls']}")
        if g["total_tool_calls"] > 0:
            success_rate = (g["successful_calls"] / g["total_tool_calls"]) * 100
            lines.append(f"  成功率: {success_rate:.1f}%")
        lines.append(f"  运行时间: {g['uptime_seconds']:.0f} 秒")

        return "\n".join(lines)

    def _format_resources(self, server_name: Optional[str] = None) -> str:
        resources = mcp_manager.all_resources
        if not resources:
            return "📦 当前没有可用的 MCP 资源"

        lines = ["📦 可用 MCP 资源"]
        by_server: Dict[str, List[MCPResourceInfo]] = {}
        for key, (resource_info, _) in resources.items():
            if server_name and resource_info.server_name != server_name:
                continue
            if resource_info.server_name not in by_server:
                by_server[resource_info.server_name] = []
            by_server[resource_info.server_name].append(resource_info)

        for srv_name, resource_list in by_server.items():
            lines.append(f"\n🔌 {srv_name} ({len(resource_list)} 个资源):")
            for res in resource_list:
                lines.append(f"  • {res.name}: {res.uri}")

        return "\n".join(lines)

    def _format_prompts(self, server_name: Optional[str] = None) -> str:
        prompts = mcp_manager.all_prompts
        if not prompts:
            return "📝 当前没有可用的 MCP 提示模板"

        lines = ["📝 可用 MCP 提示模板"]
        by_server: Dict[str, List[MCPPromptInfo]] = {}
        for key, (prompt_info, _) in prompts.items():
            if server_name and prompt_info.server_name != server_name:
                continue
            if prompt_info.server_name not in by_server:
                by_server[prompt_info.server_name] = []
            by_server[prompt_info.server_name].append(prompt_info)

        for srv_name, prompt_list in by_server.items():
            lines.append(f"\n🔌 {srv_name} ({len(prompt_list)} 个模板):")
            for prompt in prompt_list:
                lines.append(f"  • {prompt.name}")

        return "\n".join(lines)

    def _format_trace(self) -> str:
        """v1.4.0: 格式化追踪记录"""
        records = tool_call_tracer.get_recent(10)
        if not records:
            return "🔍 暂无调用追踪记录"

        lines = ["🔍 最近调用追踪记录"]
        for r in reversed(records):
            status = "✅" if r.success else "❌"
            cache = "📦" if r.cache_hit else ""
            post = "🔄" if r.post_processed else ""
            lines.append(f"  {status}{cache}{post} {r.tool_name} ({r.duration_ms:.0f}ms)")
            if r.error:
                lines.append(f"     错误: {r.error[:50]}")

        return "\n".join(lines)

    def _format_cache(self) -> str:
        """v1.4.0: 格式化缓存状态"""
        stats = tool_call_cache.get_stats()
        lines = ["🗄️ 缓存状态"]
        lines.append(f"  启用: {'是' if stats['enabled'] else '否'}")
        lines.append(f"  条目数: {stats['entries']}/{stats['max_entries']}")
        lines.append(f"  TTL: {stats['ttl']}秒")
        lines.append(f"  命中: {stats['hits']}, 未命中: {stats['misses']}")
        lines.append(f"  命中率: {stats['hit_rate']}")
        return "\n".join(lines)

    async def direct_execute(self, **function_args) -> Dict[str, Any]:
        return await self.execute(function_args)


# ============================================================================
# 命令处理
# ============================================================================


class MCPStatusCommand(BaseCommand):
    """MCP 状态查询命令 - 通过 /mcp 命令查看服务器状态"""

    command_name = "mcp_status_command"
    command_description = "查看 MCP 服务器连接状态和统计信息"
    command_pattern = r"^[/／]mcp(?:\s+(?P<subcommand>status|tools|stats|reconnect|trace|cache|perm|export|search))?(?:\s+(?P<arg>.+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        """执行命令"""
        subcommand = self.matched_groups.get("subcommand", "status") or "status"
        arg = self.matched_groups.get("arg")

        if subcommand == "reconnect":
            return await self._handle_reconnect(arg)

        # v1.4.0: 追踪命令
        if subcommand == "trace":
            return await self._handle_trace(arg)

        # v1.4.0: 缓存命令
        if subcommand == "cache":
            return await self._handle_cache(arg)

        # v1.4.0: 权限命令
        if subcommand == "perm":
            return await self._handle_perm(arg)

        # v1.6.0: 导出命令
        if subcommand == "export":
            return await self._handle_export(arg)

        # v1.7.0: 工具搜索命令
        if subcommand == "search":
            return await self._handle_search(arg)

        result = self._format_output(subcommand, arg)
        await self.send_text(result)
        return (True, None, True)

    def _find_similar_servers(self, name: str, max_results: int = 3) -> List[str]:
        """查找相似的服务器名称"""
        name_lower = name.lower()
        all_servers = list(mcp_manager._clients.keys())

        # 简单的相似度匹配：包含关系或前缀匹配
        similar = []
        for srv in all_servers:
            srv_lower = srv.lower()
            if name_lower in srv_lower or srv_lower in name_lower:
                similar.append(srv)
            elif srv_lower.startswith(name_lower[:3]) if len(name_lower) >= 3 else False:
                similar.append(srv)

        return similar[:max_results]

    async def _handle_reconnect(self, server_name: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """处理重连请求"""
        if server_name:
            if server_name not in mcp_manager._clients:
                # 提示相似的服务器名
                similar = self._find_similar_servers(server_name)
                msg = f"❌ 服务器 '{server_name}' 不存在"
                if similar:
                    msg += f"\n💡 你是不是想找: {', '.join(similar)}"
                await self.send_text(msg)
                return (True, None, True)

            await self.send_text(f"🔄 正在重连服务器 {server_name}...")
            success = await mcp_manager.reconnect_server(server_name)
            if success:
                await self.send_text(f"✅ 服务器 {server_name} 重连成功")
            else:
                await self.send_text(f"❌ 服务器 {server_name} 重连失败")
        else:
            disconnected = mcp_manager.disconnected_servers
            if not disconnected:
                await self.send_text("✅ 所有服务器都已连接")
                return (True, None, True)

            await self.send_text(f"🔄 正在重连 {len(disconnected)} 个断开的服务器...")
            for srv in disconnected:
                success = await mcp_manager.reconnect_server(srv)
                status = "✅" if success else "❌"
                await self.send_text(f"{status} {srv}")

        return (True, None, True)

    async def _handle_trace(self, arg: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """v1.4.0: 处理追踪命令"""
        if arg and arg.isdigit():
            # /mcp trace 20 - 最近 N 条
            n = int(arg)
            records = tool_call_tracer.get_recent(n)
        elif arg:
            # /mcp trace <tool_name> - 特定工具
            records = tool_call_tracer.get_by_tool(arg)
        else:
            # /mcp trace - 最近 10 条
            records = tool_call_tracer.get_recent(10)

        if not records:
            await self.send_text("🔍 暂无调用追踪记录\n\n用法: /mcp trace [数量|工具名]")
            return (True, None, True)

        lines = [f"🔍 调用追踪记录 ({len(records)} 条)"]
        lines.append("-" * 30)
        for i, r in enumerate(reversed(records)):
            status_icon = "✅" if r.success else "❌"
            cache_tag = " [缓存]" if r.cache_hit else ""
            post_tag = " [后处理]" if r.post_processed else ""
            ts = time.strftime("%H:%M:%S", time.localtime(r.timestamp))
            lines.append(f"{status_icon} [{ts}] {r.tool_name}")
            lines.append(f"   {r.duration_ms:.0f}ms | {r.server_name}{cache_tag}{post_tag}")
            if r.error:
                lines.append(f"   错误: {r.error[:50]}")
            if i < len(records) - 1:
                lines.append("")

        await self.send_text("\n".join(lines))
        return (True, None, True)

    async def _handle_cache(self, arg: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """v1.4.0: 处理缓存命令"""
        if arg == "clear":
            tool_call_cache.clear()
            await self.send_text("✅ 缓存已清空")
            return (True, None, True)

        stats = tool_call_cache.get_stats()
        lines = ["🗄️ 缓存状态"]
        lines.append(f"├ 启用: {'是' if stats['enabled'] else '否'}")
        lines.append(f"├ 条目: {stats['entries']}/{stats['max_entries']}")
        lines.append(f"├ TTL: {stats['ttl']}秒")
        lines.append(f"├ 命中: {stats['hits']}")
        lines.append(f"├ 未命中: {stats['misses']}")
        lines.append(f"└ 命中率: {stats['hit_rate']}")

        await self.send_text("\n".join(lines))
        return (True, None, True)

    async def _handle_perm(self, arg: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """v1.4.0: 处理权限命令"""
        global _plugin_instance

        if _plugin_instance is None:
            await self.send_text("❌ 插件未初始化")
            return (True, None, True)

        perm_config = _plugin_instance.config.get("permissions", {})
        enabled = perm_config.get("perm_enabled", False)
        default_mode = perm_config.get("perm_default_mode", "allow_all")

        if arg:
            # 查看特定工具的权限
            rules = permission_checker.get_rules_for_tool(arg)
            if not rules:
                await self.send_text(f"🔐 工具 {arg} 无特定权限规则\n默认模式: {default_mode}")
            else:
                lines = [f"🔐 工具 {arg} 的权限规则:"]
                for r in rules:
                    lines.append(f"  • 模式: {r.get('mode', 'default')}")
                    if r.get("allowed"):
                        lines.append(f"    允许: {', '.join(r['allowed'][:3])}...")
                    if r.get("denied"):
                        lines.append(f"    拒绝: {', '.join(r['denied'][:3])}...")
                await self.send_text("\n".join(lines))
        else:
            # 查看权限配置概览
            lines = ["🔐 权限控制配置"]
            lines.append(f"├ 启用: {'是' if enabled else '否'}")
            lines.append(f"├ 默认模式: {default_mode}")
            # 快捷配置
            deny_count = len(permission_checker._quick_deny_groups)
            allow_count = len(permission_checker._quick_allow_users)
            if deny_count > 0:
                lines.append(f"├ 禁用群: {deny_count} 个")
            if allow_count > 0:
                lines.append(f"├ 管理员白名单: {allow_count} 人")
            lines.append(f"└ 高级规则: {len(permission_checker._rules)} 条")
            await self.send_text("\n".join(lines))

        return (True, None, True)

    async def _handle_export(self, format_type: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """v1.6.0: 处理导出命令"""
        global _plugin_instance

        if _plugin_instance is None:
            await self.send_text("❌ 插件未初始化")
            return (True, None, True)

        # 获取当前服务器列表
        servers_section = _plugin_instance.config.get("servers", {})
        servers_list_str = servers_section.get("list", "[]") if isinstance(servers_section, dict) else "[]"

        try:
            servers = json.loads(servers_list_str) if servers_list_str.strip() else []
        except json.JSONDecodeError:
            await self.send_text("❌ 当前服务器配置格式错误，无法导出")
            return (True, None, True)

        if not servers:
            await self.send_text("📤 当前没有配置任何服务器")
            return (True, None, True)

        # 确定导出格式
        format_type = (format_type or "claude").lower()
        if format_type not in ("claude", "kiro", "maibot"):
            format_type = "claude"

        # 导出
        try:
            exported = ConfigConverter.export_to_string(servers, format_type, pretty=True)

            format_name = {"claude": "Claude Desktop", "kiro": "Kiro MCP", "maibot": "MaiBot"}.get(
                format_type, format_type
            )
            lines = [f"📤 导出为 {format_name} 格式 ({len(servers)} 个服务器):"]
            lines.append("")
            lines.append(exported)

            await self.send_text("\n".join(lines))
        except Exception as e:
            logger.error(f"导出配置失败: {e}")
            await self.send_text(f"❌ 导出失败: {str(e)}")

        return (True, None, True)

    async def _handle_search(self, query: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """v1.7.0: 处理工具搜索命令"""
        if not query or not query.strip():
            # 显示使用帮助
            help_text = """🔍 工具搜索

用法: /mcp search <关键词>

示例:
  /mcp search time     搜索包含 time 的工具
  /mcp search fetch    搜索包含 fetch 的工具
  /mcp search *        列出所有工具

支持模糊匹配工具名称和描述"""
            await self.send_text(help_text)
            return (True, None, True)

        query = query.strip().lower()
        tools = mcp_manager.all_tools

        if not tools:
            await self.send_text("🔍 当前没有可用的 MCP 工具")
            return (True, None, True)

        # 搜索匹配的工具
        matched = []
        for tool_key, (tool_info, client) in tools.items():
            tool_name = tool_key.lower()
            tool_desc = (tool_info.description or "").lower()

            # * 表示列出所有
            if query == "*":
                matched.append((tool_key, tool_info, client))
            elif query in tool_name or query in tool_desc:
                matched.append((tool_key, tool_info, client))

        if not matched:
            await self.send_text(f"🔍 未找到匹配 '{query}' 的工具")
            return (True, None, True)

        # 按服务器分组显示
        by_server: Dict[str, List[Tuple[str, Any]]] = {}
        for tool_key, tool_info, client in matched:
            server_name = tool_info.server_name
            if server_name not in by_server:
                by_server[server_name] = []
            by_server[server_name].append((tool_key, tool_info))

        # 如果只有一个服务器或结果较少，显示全部；否则折叠
        single_server = len(by_server) == 1
        lines = [f"🔍 搜索结果: {len(matched)} 个工具匹配 '{query}'"]

        for srv_name, tool_list in by_server.items():
            lines.append(f"\n📦 {srv_name} ({len(tool_list)} 个):")

            # 单服务器或结果少于 15 个时显示全部
            show_all = single_server or len(matched) <= 15
            display_limit = len(tool_list) if show_all else 5

            for tool_key, tool_info in tool_list[:display_limit]:
                desc = tool_info.description[:40] + "..." if len(tool_info.description) > 40 else tool_info.description
                lines.append(f"  • {tool_key}")
                lines.append(f"    {desc}")
            if len(tool_list) > display_limit:
                lines.append(f"  ... 还有 {len(tool_list) - display_limit} 个，用 /mcp search {query} {srv_name} 筛选")

        await self.send_text("\n".join(lines))
        return (True, None, True)

    def _format_output(self, subcommand: str, server_name: str = None) -> str:
        """格式化输出"""
        status = mcp_manager.get_status()
        stats = mcp_manager.get_all_stats()
        lines = []

        if subcommand in ("status", "all"):
            lines.append("📊 MCP 桥接插件状态")
            lines.append(f"├ 服务器: {status['connected_servers']}/{status['total_servers']} 已连接")
            lines.append(f"├ 工具数: {status['total_tools']}")
            lines.append(f"└ 心跳: {'运行中' if status['heartbeat_running'] else '已停止'}")

            if status["servers"]:
                lines.append("\n🔌 服务器列表:")
                for name, info in status["servers"].items():
                    if server_name and name != server_name:
                        continue
                    icon = "✅" if info["connected"] else "❌"
                    enabled = "" if info["enabled"] else " (禁用)"
                    lines.append(f"  {icon} {name}{enabled}")
                    lines.append(f"     {info['transport']} | {info['tools_count']} 工具")
                    # 显示断路器状态
                    cb = info.get("circuit_breaker", {})
                    cb_state = cb.get("state", "closed")
                    if cb_state == "open":
                        lines.append("     ⚡ 断路器熔断中")
                    elif cb_state == "half_open":
                        lines.append("     ⚡ 断路器试探中")
                    if info["consecutive_failures"] > 0:
                        lines.append(f"     ⚠️ 连续失败 {info['consecutive_failures']} 次")

        if subcommand in ("tools", "all"):
            tools = mcp_manager.all_tools
            if tools:
                lines.append("\n🔧 可用工具:")
                by_server = {}
                for key, (info, _) in tools.items():
                    if server_name and info.server_name != server_name:
                        continue
                    by_server.setdefault(info.server_name, []).append(info.name)

                # 如果指定了服务器名，显示全部工具；否则折叠显示
                show_all = server_name is not None

                for srv, tool_list in by_server.items():
                    lines.append(f"  📦 {srv} ({len(tool_list)})")
                    if show_all:
                        # 指定服务器时显示全部
                        for t in tool_list:
                            lines.append(f"     • {t}")
                    else:
                        # 未指定时折叠显示
                        for t in tool_list[:5]:
                            lines.append(f"     • {t}")
                        if len(tool_list) > 5:
                            lines.append(f"     ... 还有 {len(tool_list) - 5} 个，用 /mcp tools {srv} 查看全部")

        if subcommand in ("stats", "all"):
            g = stats["global"]
            lines.append("\n📈 调用统计:")
            lines.append(f"  总调用: {g['total_tool_calls']}")
            if g["total_tool_calls"] > 0:
                rate = (g["successful_calls"] / g["total_tool_calls"]) * 100
                lines.append(f"  成功率: {rate:.1f}%")
            lines.append(f"  运行: {g['uptime_seconds']:.0f}秒")

        if not lines:
            lines.append("📖 MCP 桥接插件命令帮助")
            lines.append("")
            lines.append("状态查询:")
            lines.append("  /mcp              查看连接状态")
            lines.append("  /mcp tools        查看所有工具")
            lines.append("  /mcp tools <服务器> 查看指定服务器工具")
            lines.append("  /mcp stats        查看调用统计")
            lines.append("")
            lines.append("工具搜索:")
            lines.append("  /mcp search <关键词>  搜索工具")
            lines.append("  /mcp search *         列出所有工具")
            lines.append("")
            lines.append("服务器管理:")
            lines.append("  /mcp reconnect        重连断开的服务器")
            lines.append("  /mcp reconnect <名称> 重连指定服务器")
            lines.append("")
            lines.append("配置导入导出:")
            lines.append("  /mcp import <json>    导入配置")
            lines.append("  /mcp export [格式]    导出配置")
            lines.append("")
            lines.append("其他:")
            lines.append("  /mcp trace   查看调用追踪")
            lines.append("  /mcp cache   查看缓存状态")
            lines.append("  /mcp perm    查看权限配置")

        return "\n".join(lines)


class MCPImportCommand(BaseCommand):
    """v1.6.0: MCP 配置导入命令 - 支持从 Claude Desktop 格式导入"""

    command_name = "mcp_import_command"
    command_description = "从 Claude Desktop 或其他格式导入 MCP 服务器配置"
    # 匹配 /mcp import 后面的所有内容（包括多行 JSON）
    command_pattern = r"^[/／]mcp\s+import(?:\s+(?P<content>.+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        """执行导入命令"""
        global _plugin_instance

        if _plugin_instance is None:
            await self.send_text("❌ 插件未初始化")
            return (True, None, True)

        content = self.matched_groups.get("content", "")

        if not content or not content.strip():
            # 显示使用帮助
            help_text = """📥 MCP 配置导入

用法: /mcp import <JSON配置>

支持的格式:
• Claude Desktop 格式 (mcpServers 对象)
• Kiro MCP 格式
• MaiBot 格式 (数组)

示例:
/mcp import {"mcpServers":{"time":{"command":"uvx","args":["mcp-server-time"]}}}

/mcp import {"mcpServers":{"api":{"url":"https://example.com/mcp","transport":"sse"}}}"""
            await self.send_text(help_text)
            return (True, None, True)

        # 获取现有服务器名称
        servers_section = _plugin_instance.config.get("servers", {})
        servers_list_str = servers_section.get("list", "[]") if isinstance(servers_section, dict) else "[]"

        try:
            existing_servers = json.loads(servers_list_str) if servers_list_str.strip() else []
        except json.JSONDecodeError:
            existing_servers = []

        existing_names = {srv.get("name", "") for srv in existing_servers if isinstance(srv, dict)}

        # 执行导入
        result = ConfigConverter.import_from_string(content.strip(), existing_names)

        # 构建响应
        lines = []

        if not result.success:
            lines.append("❌ 导入失败:")
            for err in result.errors:
                lines.append(f"  • {err}")
            await self.send_text("\n".join(lines))
            return (True, None, True)

        if not result.servers:
            lines.append("⚠️ 没有新服务器可导入")
            if result.skipped:
                lines.append("\n跳过的服务器:")
                for s in result.skipped:
                    lines.append(f"  • {s}")
            if result.warnings:
                lines.append("\n警告:")
                for w in result.warnings:
                    lines.append(f"  • {w}")
            await self.send_text("\n".join(lines))
            return (True, None, True)

        # 合并到现有列表
        new_servers = existing_servers + result.servers
        new_list_str = json.dumps(new_servers, ensure_ascii=False, indent=2)

        # 更新配置
        if "servers" not in _plugin_instance.config:
            _plugin_instance.config["servers"] = {}
        _plugin_instance.config["servers"]["list"] = new_list_str

        # 保存到配置文件
        _plugin_instance._save_servers_list(new_list_str)

        # 构建成功响应
        lines.append(f"✅ 成功导入 {len(result.servers)} 个服务器:")
        for srv in result.servers:
            transport = srv.get("transport", "stdio")
            lines.append(f"  • {srv.get('name')} ({transport})")

        if result.skipped:
            lines.append(f"\n⏭️ 跳过 {len(result.skipped)} 个:")
            for s in result.skipped[:5]:
                lines.append(f"  • {s}")
            if len(result.skipped) > 5:
                lines.append(f"  ... 还有 {len(result.skipped) - 5} 个")

        if result.warnings:
            lines.append("\n⚠️ 警告:")
            for w in result.warnings[:3]:
                lines.append(f"  • {w}")

        if result.errors:
            lines.append("\n❌ 部分失败:")
            for e in result.errors[:3]:
                lines.append(f"  • {e}")

        lines.append("\n💡 发送 /mcp reconnect 使配置生效")

        await self.send_text("\n".join(lines))
        return (True, None, True)


# ============================================================================
# 事件处理器
# ============================================================================


class MCPStartupHandler(BaseEventHandler):
    """MCP 启动事件处理器"""

    event_type = EventType.ON_START
    handler_name = "mcp_startup_handler"
    handler_description = "MCP 桥接插件启动处理器"
    weight = 0
    intercept_message = False

    async def execute(self, message: Optional[Any]) -> Tuple[bool, bool, Optional[str], None, None]:
        """处理启动事件"""
        global _plugin_instance

        if _plugin_instance is None:
            logger.warning("MCP 桥接插件实例未初始化")
            return (False, True, None, None, None)

        logger.info("MCP 桥接插件收到 ON_START 事件，开始连接 MCP 服务器...")
        await _plugin_instance._async_connect_servers()

        await mcp_manager.start_heartbeat()

        # v1.6.0: 启动配置文件监控（用于 WebUI 导入）
        await _plugin_instance._start_config_watcher()

        return (True, True, None, None, None)


class MCPStopHandler(BaseEventHandler):
    """MCP 停止事件处理器"""

    event_type = EventType.ON_STOP
    handler_name = "mcp_stop_handler"
    handler_description = "MCP 桥接插件停止处理器"
    weight = 0
    intercept_message = False

    async def execute(self, message: Optional[Any]) -> Tuple[bool, bool, Optional[str], None, None]:
        """处理停止事件"""
        global _plugin_instance

        logger.info("MCP 桥接插件收到 ON_STOP 事件，正在关闭...")

        # v1.6.0: 停止配置文件监控
        if _plugin_instance:
            await _plugin_instance._stop_config_watcher()

        await mcp_manager.shutdown()
        mcp_tool_registry.clear()

        logger.info("MCP 桥接插件已关闭所有连接")
        return (True, True, None, None, None)


# ============================================================================
# 主插件类
# ============================================================================


@register_plugin
class MCPBridgePlugin(BasePlugin):
    """MCP 桥接插件 v1.4.0 - 将 MCP 服务器的工具桥接到 MaiBot"""

    plugin_name: str = "mcp_bridge_plugin"
    enable_plugin: bool = False  # 默认禁用，用户需在 WebUI 手动启用
    dependencies: List[str] = []
    python_dependencies: List[str] = ["mcp"]
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "guide": "📖 快速入门",
        "plugin": "🔘 插件开关",
        "import_export": "📥 导入导出",
        "quick_add": "➕ 快速添加服务器",
        "servers": "🔌 服务器列表",
        "status": "📊 运行状态",
        "settings": "⚙️ 高级设置",
        "tools": "🔧 工具管理",
        "permissions": "🔐 权限控制",
    }

    config_schema: dict = {
        # 新手引导区（只读）
        "guide": {
            "quick_start": ConfigField(
                type=str,
                default="1. 从下方链接获取 MCP 服务器  2. 在「快速添加」填写信息  3. 保存后发送 /mcp reconnect",
                description="三步开始使用",
                label="🚀 快速入门",
                disabled=True,
                order=1,
            ),
            "mcp_sources": ConfigField(
                type=str,
                default="https://modelscope.cn/mcp (魔搭·推荐) | https://smithery.ai | https://glama.ai | https://mcp.so",
                description="复制链接到浏览器打开，获取免费 MCP 服务器",
                label="🌐 获取 MCP 服务器",
                disabled=True,
                hint="魔搭 ModelScope 国内免费推荐，复制服务器 URL 到「快速添加」即可",
                order=2,
            ),
            "example_config": ConfigField(
                type=str,
                default='{"name": "time", "enabled": true, "transport": "streamable_http", "url": "https://mcp.api-inference.modelscope.cn/server/mcp-server-time"}',
                description="复制到服务器列表可直接使用（免费时间服务器）",
                label="📝 配置示例",
                disabled=True,
                order=3,
            ),
        },
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=False,
                description="是否启用插件",
                label="启用插件",
            ),
        },
        # v1.6.0: 导入导出配置
        "import_export": {
            "import_config": ConfigField(
                type=str,
                default="",
                description="粘贴 Claude Desktop 或其他格式的 MCP 配置 JSON",
                label="📥 导入配置",
                input_type="textarea",
                rows=8,
                placeholder='{"mcpServers":{"time":{"command":"uvx","args":["mcp-server-time"]}}}',
                hint="粘贴配置后点击保存，2秒内自动导入。查看下方「导入结果」确认状态",
                order=1,
            ),
            "import_result": ConfigField(
                type=str,
                default="",
                description="导入结果（只读）",
                label="📋 导入结果",
                input_type="textarea",
                disabled=True,
                rows=4,
                order=2,
            ),
            "export_format": ConfigField(
                type=str,
                default="claude",
                description="导出格式",
                label="📤 导出格式",
                choices=["claude", "kiro", "maibot"],
                hint="claude: Claude Desktop 格式 | kiro: Kiro MCP 格式 | maibot: 本插件格式",
                order=3,
            ),
            "export_result": ConfigField(
                type=str,
                default="(点击保存后生成)",
                description="导出的配置（只读，可复制）",
                label="📤 导出结果",
                input_type="textarea",
                disabled=True,
                rows=10,
                hint="复制此内容到 Claude Desktop 或其他支持 MCP 的应用",
                order=4,
            ),
        },
        "settings": {
            "tool_prefix": ConfigField(
                type=str,
                default="mcp",
                description="🏷️ 工具前缀 - 生成的工具名格式: {前缀}_{服务器名}_{工具名}",
                label="🏷️ 工具前缀",
                placeholder="mcp",
                order=1,
            ),
            "connect_timeout": ConfigField(
                type=float,
                default=30.0,
                description="⏱️ 连接超时（秒）",
                label="⏱️ 连接超时（秒）",
                min=5.0,
                max=120.0,
                step=5.0,
                order=2,
            ),
            "call_timeout": ConfigField(
                type=float,
                default=60.0,
                description="⏱️ 调用超时（秒）",
                label="⏱️ 调用超时（秒）",
                min=10.0,
                max=300.0,
                step=10.0,
                order=3,
            ),
            "auto_connect": ConfigField(
                type=bool,
                default=True,
                description="🔄 启动时自动连接所有已启用的服务器",
                label="🔄 自动连接",
                order=4,
            ),
            "retry_attempts": ConfigField(
                type=int,
                default=3,
                description="🔁 连接失败时的重试次数",
                label="🔁 重试次数",
                min=0,
                max=10,
                order=5,
            ),
            "retry_interval": ConfigField(
                type=float,
                default=5.0,
                description="⏳ 重试间隔（秒）",
                label="⏳ 重试间隔（秒）",
                min=1.0,
                max=60.0,
                step=1.0,
                order=6,
            ),
            "heartbeat_enabled": ConfigField(
                type=bool,
                default=True,
                description="💓 定期检测服务器连接状态",
                label="💓 启用心跳检测",
                order=7,
            ),
            "heartbeat_interval": ConfigField(
                type=float,
                default=60.0,
                description="💓 基准心跳间隔（秒）",
                label="💓 心跳间隔（秒）",
                min=10.0,
                max=300.0,
                step=10.0,
                hint="智能心跳会根据服务器稳定性自动调整",
                order=8,
            ),
            "heartbeat_adaptive": ConfigField(
                type=bool,
                default=True,
                description="🧠 根据服务器稳定性自动调整心跳间隔",
                label="🧠 智能心跳",
                hint="稳定服务器逐渐增加间隔，断开的服务器缩短间隔",
                order=9,
            ),
            "heartbeat_max_multiplier": ConfigField(
                type=float,
                default=3.0,
                description="稳定服务器的最大间隔倍数",
                label="📈 最大间隔倍数",
                min=1.5,
                max=5.0,
                step=0.5,
                hint="稳定服务器心跳间隔最高可达 基准间隔 × 此值",
                order=10,
            ),
            "auto_reconnect": ConfigField(
                type=bool,
                default=True,
                description="🔄 检测到断开时自动尝试重连",
                label="🔄 自动重连",
                order=11,
            ),
            "max_reconnect_attempts": ConfigField(
                type=int,
                default=3,
                description="🔄 连续重连失败后暂停重连",
                label="🔄 最大重连次数",
                min=1,
                max=10,
                order=12,
            ),
            # v1.7.0: 状态刷新配置
            "status_refresh_enabled": ConfigField(
                type=bool,
                default=True,
                description="📊 定期更新 WebUI 状态显示",
                label="📊 启用状态实时刷新",
                hint="关闭后 WebUI 状态仅在启动时更新",
                order=13,
            ),
            "status_refresh_interval": ConfigField(
                type=float,
                default=10.0,
                description="📊 状态刷新间隔（秒）",
                label="📊 状态刷新间隔（秒）",
                min=5.0,
                max=60.0,
                step=5.0,
                hint="值越小刷新越频繁，但会增加少量磁盘写入",
                order=14,
            ),
            "enable_resources": ConfigField(
                type=bool,
                default=False,
                description="📦 允许读取 MCP 服务器提供的资源",
                label="📦 启用 Resources（实验性）",
                order=11,
            ),
            "enable_prompts": ConfigField(
                type=bool,
                default=False,
                description="📝 允许使用 MCP 服务器提供的提示模板",
                label="📝 启用 Prompts（实验性）",
                order=12,
            ),
            # v1.3.0 后处理配置
            "post_process_enabled": ConfigField(
                type=bool,
                default=False,
                description="🔄 使用 LLM 对长结果进行摘要提炼",
                label="🔄 启用结果后处理",
                order=20,
            ),
            "post_process_threshold": ConfigField(
                type=int,
                default=500,
                description="📏 结果长度超过此值才触发后处理",
                label="📏 后处理阈值（字符）",
                min=100,
                max=5000,
                step=100,
                order=21,
            ),
            "post_process_max_tokens": ConfigField(
                type=int,
                default=500,
                description="📝 LLM 摘要输出的最大 token 数",
                label="📝 后处理最大输出 token",
                min=100,
                max=2000,
                step=50,
                order=22,
            ),
            "post_process_model": ConfigField(
                type=str,
                default="",
                description="🤖 指定用于后处理的模型名称",
                label="🤖 后处理模型（可选）",
                placeholder="留空则使用 Utils 模型组",
                order=23,
            ),
            "post_process_prompt": ConfigField(
                type=str,
                default="用户问题：{query}\\n\\n工具返回内容：\\n{result}\\n\\n请从上述内容中提取与用户问题最相关的关键信息，简洁准确地输出：",
                description="📋 后处理提示词模板",
                label="📋 后处理提示词模板",
                input_type="textarea",
                rows=8,
                order=24,
            ),
            # v1.4.0 追踪配置
            "trace_enabled": ConfigField(
                type=bool,
                default=True,
                description="🔍 记录工具调用详情",
                label="🔍 启用调用追踪",
                order=30,
            ),
            "trace_max_records": ConfigField(
                type=int,
                default=100,
                description="内存中保留的最大记录数",
                label="📊 追踪记录上限",
                min=10,
                max=1000,
                order=31,
            ),
            "trace_log_enabled": ConfigField(
                type=bool,
                default=False,
                description="是否将追踪记录写入日志文件",
                label="📝 追踪日志文件",
                hint="启用后记录写入 plugins/MaiBot_MCPBridgePlugin/logs/trace.jsonl",
                order=32,
            ),
            # v1.4.0 缓存配置
            "cache_enabled": ConfigField(
                type=bool,
                default=False,
                description="🗄️ 缓存相同参数的调用结果",
                label="🗄️ 启用调用缓存",
                hint="相同参数的调用会返回缓存结果，减少重复请求",
                order=40,
            ),
            "cache_ttl": ConfigField(
                type=int,
                default=300,
                description="缓存有效期（秒）",
                label="⏱️ 缓存有效期（秒）",
                min=60,
                max=3600,
                order=41,
            ),
            "cache_max_entries": ConfigField(
                type=int,
                default=200,
                description="最大缓存条目数（超出后 LRU 淘汰）",
                label="📦 最大缓存条目",
                min=50,
                max=1000,
                order=42,
            ),
            "cache_exclude_tools": ConfigField(
                type=str,
                default="",
                description="不缓存的工具（每行一个，支持通配符 *）",
                label="🚫 缓存排除列表",
                input_type="textarea",
                rows=4,
                hint="时间类、随机类工具建议排除，如 mcp_time_*",
                order=43,
            ),
        },
        # v1.4.0 工具管理
        "tools": {
            "tool_list": ConfigField(
                type=str,
                default="(启动后自动生成)",
                description="当前已注册的 MCP 工具列表（只读）",
                label="📋 工具清单",
                input_type="textarea",
                disabled=True,
                rows=12,
                hint="从此处复制工具名到下方禁用列表",
                order=1,
            ),
            "disabled_tools": ConfigField(
                type=str,
                default="",
                description="要禁用的工具名（每行一个）",
                label="🚫 禁用工具列表",
                input_type="textarea",
                rows=6,
                hint="从上方工具清单复制工具名，每行一个。禁用后该工具不会被 LLM 调用",
                order=2,
            ),
        },
        # v1.4.0 权限控制
        "permissions": {
            "perm_enabled": ConfigField(
                type=bool,
                default=False,
                description="🔐 按群/用户限制工具使用",
                label="🔐 启用权限控制",
                order=1,
            ),
            "perm_default_mode": ConfigField(
                type=str,
                default="allow_all",
                description="默认模式：allow_all（默认允许）或 deny_all（默认禁止）",
                label="📋 默认模式",
                placeholder="allow_all",
                hint="allow_all: 未配置的默认允许；deny_all: 未配置的默认禁止",
                order=2,
            ),
            # 快捷配置（简化版）
            "quick_deny_groups": ConfigField(
                type=str,
                default="",
                description="禁止使用所有 MCP 工具的群号（每行一个）",
                label="🚫 禁用群列表（快捷）",
                input_type="textarea",
                rows=4,
                hint="填入群号，该群将无法使用任何 MCP 工具",
                order=3,
            ),
            "quick_allow_users": ConfigField(
                type=str,
                default="",
                description="始终允许使用所有工具的用户 QQ 号（管理员白名单，每行一个）",
                label="✅ 管理员白名单（快捷）",
                input_type="textarea",
                rows=3,
                hint="填入 QQ 号，该用户在任何场景都可使用 MCP 工具",
                order=4,
            ),
            # 高级配置
            "perm_rules": ConfigField(
                type=str,
                default="[]",
                description="高级权限规则（JSON 格式，可针对特定工具配置）",
                label="📜 高级权限规则（可选）",
                input_type="textarea",
                rows=10,
                placeholder="""[
  {"tool": "mcp_*_delete_*", "denied": ["qq:123456:group"]}
]""",
                hint="格式: qq:ID:group/private/user，工具名支持通配符 *",
                order=10,
            ),
        },
        # v1.5.1: 快速添加服务器（表单式配置）
        "quick_add": {
            "server_name": ConfigField(
                type=str,
                default="",
                description="服务器唯一名称（英文，如 time-server）",
                label="📛 服务器名称",
                placeholder="my-mcp-server",
                hint="必填，用于标识服务器",
                order=1,
            ),
            "server_type": ConfigField(
                type=str,
                default="streamable_http",
                description="传输类型",
                label="📡 传输类型",
                choices=["streamable_http", "http", "sse", "stdio"],
                hint="远程服务器选 streamable_http/http/sse，本地选 stdio",
                order=2,
            ),
            "server_url": ConfigField(
                type=str,
                default="",
                description="服务器 URL（远程服务器必填）",
                label="🌐 服务器 URL",
                placeholder="https://mcp.api-inference.modelscope.cn/server/xxx",
                hint="streamable_http/http/sse 类型必填",
                order=3,
            ),
            "server_command": ConfigField(
                type=str,
                default="",
                description="启动命令（stdio 类型必填）",
                label="⌨️ 启动命令",
                placeholder="uvx 或 npx",
                hint="stdio 类型必填，如 uvx、npx、python",
                order=4,
            ),
            "server_args": ConfigField(
                type=str,
                default="",
                description="命令参数（每行一个）",
                label="📝 命令参数",
                input_type="textarea",
                rows=3,
                placeholder="mcp-server-fetch",
                hint="stdio 类型使用，每行一个参数",
                order=5,
            ),
            "server_headers": ConfigField(
                type=str,
                default="",
                description="鉴权头（JSON 格式，可选）",
                label="🔑 鉴权头（可选）",
                placeholder='{"Authorization": "Bearer xxx"}',
                hint="需要鉴权的服务器填写，如 ModelScope 的 API Key",
                order=6,
            ),
            "add_button": ConfigField(
                type=str,
                default="填写上方信息后，点击保存将自动添加到服务器列表",
                description="",
                label="💡 使用说明",
                disabled=True,
                hint="保存配置后，新服务器会自动添加到下方列表。重启 MaiBot 或发送 /mcp reconnect 生效",
                order=7,
            ),
        },
        "servers": {
            "list": ConfigField(
                type=str,
                default="[]",
                description="MCP 服务器列表（JSON 格式，高级用户可直接编辑）",
                label="🔌 服务器列表（高级）",
                input_type="textarea",
                rows=15,
                hint="⚠️ JSON 数组格式。新手建议使用上方「快速添加」",
                order=1,
            ),
        },
        "status": {
            "connection_status": ConfigField(
                type=str,
                default="未初始化",
                description="当前 MCP 服务器连接状态和工具列表",
                label="📊 连接状态",
                input_type="textarea",
                disabled=True,
                rows=15,
                hint="此状态仅在插件启动时更新。查询实时状态请发送 /mcp 命令",
                order=1,
            ),
        },
    }

    @staticmethod
    def _fix_config_multiline_strings(config_path: Path) -> bool:
        """修复配置文件中的多行字符串格式问题

        处理两种情况：
        1. 带转义 \\n 的单行字符串（json.dumps 生成）
        2. 跨越多行但使用普通双引号的字符串（控制字符错误）

        Returns:
            bool: 是否进行了修复
        """
        if not config_path.exists():
            return False

        try:
            content = config_path.read_text(encoding="utf-8")

            # 情况1: 修复带转义 \n 的单行字符串
            # 匹配: key = "内容包含\n的字符串"
            pattern1 = r'^(\s*\w+\s*=\s*)"((?:[^"\\]|\\.)*\\n(?:[^"\\]|\\.)*)"(\s*)$'

            # 情况2: 修复跨越多行的普通双引号字符串
            # 匹配: key = "第一行
            #       第二行
            #       第三行"
            pattern2_start = r'^(\s*\w+\s*=\s*)"([^"]*?)$'  # 开始行
            pattern2_end = r'^([^"]*)"(\s*)$'  # 结束行

            lines = content.split("\n")
            fixed_lines = []
            modified = False

            i = 0
            while i < len(lines):
                line = lines[i]

                # 情况1: 单行带转义换行符
                match1 = re.match(pattern1, line)
                if match1:
                    prefix = match1.group(1)
                    value = match1.group(2)
                    suffix = match1.group(3)
                    # 将转义的换行符还原为实际换行符
                    unescaped = (
                        value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
                    )
                    fixed_line = f'{prefix}"""{unescaped}"""{suffix}'
                    fixed_lines.append(fixed_line)
                    modified = True
                    i += 1
                    continue

                # 情况2: 跨越多行的字符串
                match2_start = re.match(pattern2_start, line)
                if match2_start:
                    prefix = match2_start.group(1)
                    first_part = match2_start.group(2)

                    # 收集后续行直到找到结束引号
                    multiline_parts = [first_part]
                    j = i + 1
                    found_end = False

                    while j < len(lines):
                        next_line = lines[j]
                        match2_end = re.match(pattern2_end, next_line)
                        if match2_end:
                            multiline_parts.append(match2_end.group(1))
                            suffix = match2_end.group(2)
                            found_end = True
                            j += 1
                            break
                        else:
                            multiline_parts.append(next_line)
                            j += 1

                    if found_end and len(multiline_parts) > 1:
                        # 合并为三引号字符串
                        full_value = "\n".join(multiline_parts)
                        fixed_line = f'{prefix}"""{full_value}"""{suffix}'
                        fixed_lines.append(fixed_line)
                        modified = True
                        i = j
                        continue

                fixed_lines.append(line)
                i += 1

            if modified:
                config_path.write_text("\n".join(fixed_lines), encoding="utf-8")
                logger.info("已自动修复配置文件中的多行字符串格式")
                return True

            return False
        except Exception as e:
            logger.warning(f"修复配置文件格式失败: {e}")
            return False

    def __init__(self, *args, **kwargs):
        global _plugin_instance

        # 在父类初始化前尝试修复配置文件格式
        config_path = Path(__file__).parent / "config.toml"
        self._fix_config_multiline_strings(config_path)

        super().__init__(*args, **kwargs)
        self._initialized = False
        _plugin_instance = self

        # 配置 MCP 管理器
        settings = self.config.get("settings", {})
        mcp_manager.configure(settings)

        # v1.4.0: 配置追踪器
        trace_log_path = Path(__file__).parent / "logs" / "trace.jsonl"
        tool_call_tracer.configure(
            enabled=settings.get("trace_enabled", True),
            max_records=settings.get("trace_max_records", 100),
            log_enabled=settings.get("trace_log_enabled", False),
            log_path=trace_log_path,
        )

        # v1.4.0: 配置缓存
        tool_call_cache.configure(
            enabled=settings.get("cache_enabled", False),
            ttl=settings.get("cache_ttl", 300),
            max_entries=settings.get("cache_max_entries", 200),
            exclude_tools=settings.get("cache_exclude_tools", ""),
        )

        # v1.4.0: 配置权限检查器
        perm_config = self.config.get("permissions", {})
        permission_checker.configure(
            enabled=perm_config.get("perm_enabled", False),
            default_mode=perm_config.get("perm_default_mode", "allow_all"),
            rules_json=perm_config.get("perm_rules", "[]"),
            quick_deny_groups=perm_config.get("quick_deny_groups", ""),
            quick_allow_users=perm_config.get("quick_allow_users", ""),
        )

        # 注册状态变化回调
        mcp_manager.set_status_change_callback(self._update_status_display)

        # v1.6.0: 处理 WebUI 导入导出
        self._process_webui_import_export()

        # v1.5.1: 处理快速添加服务器
        self._process_quick_add_server()

    def _process_webui_import_export(self) -> None:
        """v1.6.0: 处理 WebUI 导入导出"""
        import_export = self.config.get("import_export", {})
        import_config = import_export.get("import_config", "").strip()
        export_format = import_export.get("export_format", "claude")

        # 处理导入
        if import_config:
            self._do_webui_import(import_config)

        # 处理导出（每次都更新）
        self._do_webui_export(export_format)

    def _do_webui_import(self, import_config: str) -> None:
        """执行 WebUI 导入"""
        # 获取现有服务器
        servers_section = self.config.get("servers", {})
        servers_list_str = servers_section.get("list", "[]") if isinstance(servers_section, dict) else "[]"

        try:
            existing_servers = json.loads(servers_list_str) if servers_list_str.strip() else []
        except json.JSONDecodeError:
            existing_servers = []

        existing_names = {srv.get("name", "") for srv in existing_servers if isinstance(srv, dict)}

        # 执行导入
        result = ConfigConverter.import_from_string(import_config, existing_names)

        # 构建结果消息
        lines = []

        if not result.success:
            lines.append("❌ 导入失败:")
            for err in result.errors:
                lines.append(f"  • {err}")
        elif not result.servers:
            lines.append("⚠️ 没有新服务器可导入")
            if result.skipped:
                lines.append(f"跳过: {', '.join(result.skipped[:5])}")
        else:
            # 合并到现有列表
            new_servers = existing_servers + result.servers
            new_list_str = json.dumps(new_servers, ensure_ascii=False, indent=2)

            # 更新配置
            if "servers" not in self.config:
                self.config["servers"] = {}
            self.config["servers"]["list"] = new_list_str

            # 保存到配置文件
            self._save_servers_list(new_list_str)

            lines.append(f"✅ 成功导入 {len(result.servers)} 个服务器:")
            for srv in result.servers[:5]:
                lines.append(f"  • {srv.get('name')} ({srv.get('transport', 'stdio')})")
            if len(result.servers) > 5:
                lines.append(f"  ... 还有 {len(result.servers) - 5} 个")

            if result.skipped:
                lines.append(f"跳过: {len(result.skipped)} 个已存在")

            lines.append("")
            lines.append("💡 发送 /mcp reconnect 生效")

        # 更新导入结果显示
        if "import_export" not in self.config:
            self.config["import_export"] = {}
        self.config["import_export"]["import_result"] = "\n".join(lines)

        # 清空导入框
        self.config["import_export"]["import_config"] = ""

        # 保存结果到配置文件
        self._save_import_export_result("\n".join(lines))

    def _do_webui_export(self, export_format: str) -> None:
        """执行 WebUI 导出"""
        # 获取当前服务器列表
        servers_section = self.config.get("servers", {})
        servers_list_str = servers_section.get("list", "[]") if isinstance(servers_section, dict) else "[]"

        try:
            servers = json.loads(servers_list_str) if servers_list_str.strip() else []
        except json.JSONDecodeError:
            servers = []

        if not servers:
            export_result = "(当前没有配置任何服务器)"
        else:
            try:
                export_result = ConfigConverter.export_to_string(servers, export_format, pretty=True)
            except Exception as e:
                export_result = f"(导出失败: {e})"

        # 更新导出结果
        if "import_export" not in self.config:
            self.config["import_export"] = {}
        self.config["import_export"]["export_result"] = export_result

    def _save_import_export_result(self, result: str) -> None:
        """保存导入导出结果到配置文件"""
        import tomlkit
        from tomlkit.items import String, StringType, Trivia

        try:
            config_path = Path(__file__).parent / "config.toml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    doc = tomlkit.load(f)

                if "import_export" not in doc:
                    doc["import_export"] = tomlkit.table()

                # 清空导入框
                doc["import_export"]["import_config"] = ""

                # 更新结果
                if "\n" in result:
                    ml_string = String(StringType.MLB, result, result, Trivia())
                    doc["import_export"]["import_result"] = ml_string
                else:
                    doc["import_export"]["import_result"] = result

                with open(config_path, "w", encoding="utf-8") as f:
                    tomlkit.dump(doc, f)
        except Exception as e:
            logger.warning(f"保存导入结果失败: {e}")

    async def _start_config_watcher(self) -> None:
        """v1.6.0: 启动配置文件监控（用于 WebUI 实时导入）"""
        self._config_watcher_running = True
        self._config_watcher_task = asyncio.create_task(self._config_watcher_loop())
        logger.info("配置文件监控已启动")

    async def _stop_config_watcher(self) -> None:
        """v1.6.0: 停止配置文件监控"""
        self._config_watcher_running = False
        if hasattr(self, "_config_watcher_task") and self._config_watcher_task:
            self._config_watcher_task.cancel()
            try:
                await self._config_watcher_task
            except asyncio.CancelledError:
                pass
            self._config_watcher_task = None
        logger.info("配置文件监控已停止")

    async def _config_watcher_loop(self) -> None:
        """v1.6.0: 配置文件监控循环 + v1.7.0: 状态实时刷新"""
        import tomlkit

        config_path = Path(__file__).parent / "config.toml"
        last_mtime = config_path.stat().st_mtime if config_path.exists() else 0
        last_status_update = time.time()

        while self._config_watcher_running:
            try:
                await asyncio.sleep(2)  # 每 2 秒检查一次

                # v1.7.0: 定期更新状态显示（从配置读取）
                settings = self.config.get("settings", {})
                status_refresh_enabled = settings.get("status_refresh_enabled", True)
                status_refresh_interval = settings.get("status_refresh_interval", 10.0)

                current_time = time.time()
                if status_refresh_enabled and current_time - last_status_update >= status_refresh_interval:
                    self._update_status_display()
                    last_status_update = current_time

                if not config_path.exists():
                    continue

                current_mtime = config_path.stat().st_mtime
                if current_mtime <= last_mtime:
                    continue

                last_mtime = current_mtime
                logger.debug("检测到配置文件变化，检查是否有导入请求...")

                # 读取配置文件
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        doc = tomlkit.load(f)
                except Exception as e:
                    logger.warning(f"读取配置文件失败: {e}")
                    continue

                # 检查是否有导入配置
                import_export = doc.get("import_export", {})
                import_config = import_export.get("import_config", "")

                if not import_config or not str(import_config).strip():
                    continue

                import_config_str = str(import_config).strip()
                logger.info("检测到 WebUI 导入请求，开始处理...")

                # 执行导入
                await self._execute_webui_import(import_config_str, doc, config_path)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"配置监控循环出错: {e}")
                await asyncio.sleep(5)

    async def _execute_webui_import(self, import_config: str, doc, config_path: Path) -> None:
        """v1.6.0: 执行 WebUI 导入"""
        import tomlkit
        from tomlkit.items import String, StringType, Trivia

        # 获取现有服务器
        servers_section = doc.get("servers", {})
        servers_list_str = str(servers_section.get("list", "[]"))

        try:
            existing_servers = json.loads(servers_list_str) if servers_list_str.strip() else []
        except json.JSONDecodeError:
            existing_servers = []

        existing_names = {srv.get("name", "") for srv in existing_servers if isinstance(srv, dict)}

        # 执行导入
        result = ConfigConverter.import_from_string(import_config, existing_names)

        # 构建结果消息
        lines = []

        if not result.success:
            lines.append("❌ 导入失败:")
            for err in result.errors:
                lines.append(f"  • {err}")
        elif not result.servers:
            lines.append("⚠️ 没有新服务器可导入")
            if result.skipped:
                lines.append(f"跳过: {', '.join(result.skipped[:5])}")
        else:
            # 合并到现有列表
            new_servers = existing_servers + result.servers
            new_list_str = json.dumps(new_servers, ensure_ascii=False, indent=2)

            # 更新 servers.list
            if "servers" not in doc:
                doc["servers"] = tomlkit.table()
            ml_string = String(StringType.MLB, new_list_str, new_list_str, Trivia())
            doc["servers"]["list"] = ml_string

            lines.append(f"✅ 成功导入 {len(result.servers)} 个服务器:")
            for srv in result.servers[:5]:
                lines.append(f"  • {srv.get('name')} ({srv.get('transport', 'stdio')})")
            if len(result.servers) > 5:
                lines.append(f"  ... 还有 {len(result.servers) - 5} 个")

            if result.skipped:
                lines.append(f"跳过: {len(result.skipped)} 个已存在")

            lines.append("")
            lines.append("💡 发送 /mcp reconnect 使新服务器生效")

            logger.info(f"WebUI 导入成功: {len(result.servers)} 个服务器")

        # 更新导入结果并清空导入框
        if "import_export" not in doc:
            doc["import_export"] = tomlkit.table()

        doc["import_export"]["import_config"] = ""
        result_text = "\n".join(lines)
        if "\n" in result_text:
            ml_result = String(StringType.MLB, result_text, result_text, Trivia())
            doc["import_export"]["import_result"] = ml_result
        else:
            doc["import_export"]["import_result"] = result_text

        # 保存配置文件
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                tomlkit.dump(doc, f)
            logger.info("WebUI 导入结果已保存")
        except Exception as e:
            logger.error(f"保存导入结果失败: {e}")

    def _process_quick_add_server(self) -> None:
        """v1.5.1: 处理快速添加服务器表单，将新服务器合并到列表"""
        quick_add = self.config.get("quick_add", {})
        server_name = quick_add.get("server_name", "").strip()

        if not server_name:
            return  # 没有填写名称，跳过

        server_type = quick_add.get("server_type", "streamable_http")
        server_url = quick_add.get("server_url", "").strip()
        server_command = quick_add.get("server_command", "").strip()
        server_args_str = quick_add.get("server_args", "").strip()
        server_headers_str = quick_add.get("server_headers", "").strip()

        # 构建新服务器配置
        new_server = {
            "name": server_name,
            "enabled": True,
            "transport": server_type,
        }

        if server_type == "stdio":
            if not server_command:
                logger.warning(f"快速添加: stdio 类型需要填写命令，跳过 {server_name}")
                return
            new_server["command"] = server_command
            if server_args_str:
                new_server["args"] = [arg.strip() for arg in server_args_str.split("\n") if arg.strip()]
        else:
            if not server_url:
                logger.warning(f"快速添加: {server_type} 类型需要填写 URL，跳过 {server_name}")
                return
            new_server["url"] = server_url

        # 解析鉴权头
        if server_headers_str:
            try:
                headers = json.loads(server_headers_str)
                if isinstance(headers, dict):
                    new_server["headers"] = headers
            except json.JSONDecodeError:
                logger.warning("快速添加: 鉴权头 JSON 格式错误，已忽略")

        # 获取现有服务器列表
        servers_section = self.config.get("servers", {})
        servers_list_str = servers_section.get("list", "[]") if isinstance(servers_section, dict) else "[]"

        try:
            servers_list = json.loads(servers_list_str) if servers_list_str.strip() else []
        except json.JSONDecodeError:
            servers_list = []

        # 检查是否已存在同名服务器
        for existing in servers_list:
            if existing.get("name") == server_name:
                logger.info(f"快速添加: 服务器 {server_name} 已存在，跳过")
                self._clear_quick_add_fields()
                return

        # 添加新服务器
        servers_list.append(new_server)
        logger.info(f"快速添加: 已添加服务器 {server_name} ({server_type})")

        # 更新配置
        new_list_str = json.dumps(servers_list, ensure_ascii=False, indent=2)
        if "servers" not in self.config:
            self.config["servers"] = {}
        self.config["servers"]["list"] = new_list_str

        # 清空快速添加字段
        self._clear_quick_add_fields()

        # 保存到配置文件
        self._save_servers_list(new_list_str)

    def _clear_quick_add_fields(self) -> None:
        """清空快速添加表单字段"""
        if "quick_add" not in self.config:
            self.config["quick_add"] = {}
        self.config["quick_add"]["server_name"] = ""
        self.config["quick_add"]["server_url"] = ""
        self.config["quick_add"]["server_command"] = ""
        self.config["quick_add"]["server_args"] = ""
        self.config["quick_add"]["server_headers"] = ""

    def _save_servers_list(self, servers_json: str) -> None:
        """保存服务器列表到配置文件"""
        import tomlkit
        from tomlkit.items import String, StringType, Trivia

        try:
            config_path = Path(__file__).parent / "config.toml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    doc = tomlkit.load(f)

                if "servers" not in doc:
                    doc["servers"] = tomlkit.table()

                # 使用多行字符串
                ml_string = String(StringType.MLB, servers_json, servers_json, Trivia())
                doc["servers"]["list"] = ml_string

                # 清空快速添加字段
                if "quick_add" in doc:
                    doc["quick_add"]["server_name"] = ""
                    doc["quick_add"]["server_url"] = ""
                    doc["quick_add"]["server_command"] = ""
                    doc["quick_add"]["server_args"] = ""
                    doc["quick_add"]["server_headers"] = ""

                with open(config_path, "w", encoding="utf-8") as f:
                    tomlkit.dump(doc, f)

                logger.info("服务器列表已保存到配置文件")
        except Exception as e:
            logger.warning(f"保存服务器列表失败: {e}")

    def _get_disabled_tools(self) -> set:
        """v1.4.0: 获取禁用的工具列表"""
        tools_config = self.config.get("tools", {})
        disabled_str = tools_config.get("disabled_tools", "")
        return {t.strip() for t in disabled_str.strip().split("\n") if t.strip()}

    async def _async_connect_servers(self) -> None:
        """异步连接所有配置的 MCP 服务器（v1.5.0: 并行连接优化）"""
        import asyncio

        settings = self.config.get("settings", {})

        servers_section = self.config.get("servers", [])

        if isinstance(servers_section, dict):
            servers_list = servers_section.get("list", [])
            if isinstance(servers_list, str):
                servers_config = self._parse_servers_json(servers_list)
            elif isinstance(servers_list, list):
                servers_config = servers_list
            else:
                servers_config = []
        else:
            servers_config = servers_section

        if not servers_config:
            logger.warning("未配置任何 MCP 服务器")
            self._initialized = True
            return

        auto_connect = settings.get("auto_connect", True)
        if not auto_connect:
            logger.info("auto_connect 已禁用，跳过自动连接")
            self._initialized = True
            return

        tool_prefix = settings.get("tool_prefix", "mcp")
        disabled_tools = self._get_disabled_tools()
        enable_resources = settings.get("enable_resources", False)
        enable_prompts = settings.get("enable_prompts", False)

        # 解析所有服务器配置
        enabled_configs: List[MCPServerConfig] = []
        for idx, server_conf in enumerate(servers_config):
            server_name = server_conf.get("name", f"unknown_{idx}")

            if not server_conf.get("enabled", True):
                logger.info(f"服务器 {server_name} 已禁用，跳过")
                continue

            try:
                config = self._parse_server_config(server_conf)
                enabled_configs.append(config)
            except Exception as e:
                logger.error(f"解析服务器 {server_name} 配置失败: {e}")

        if not enabled_configs:
            logger.warning("没有已启用的 MCP 服务器")
            self._initialized = True
            return

        logger.info(f"准备并行连接 {len(enabled_configs)} 个 MCP 服务器")

        # v1.5.0: 并行连接所有服务器
        async def connect_single_server(config: MCPServerConfig) -> Tuple[MCPServerConfig, bool]:
            """连接单个服务器"""
            logger.info(f"正在连接服务器: {config.name} ({config.transport.value})")
            try:
                success = await mcp_manager.add_server(config)
                if success:
                    logger.info(f"✅ 服务器 {config.name} 连接成功")
                    # 获取资源和提示模板
                    if enable_resources:
                        try:
                            await mcp_manager.fetch_resources_for_server(config.name)
                        except Exception as e:
                            logger.warning(f"服务器 {config.name} 获取资源列表失败: {e}")
                    if enable_prompts:
                        try:
                            await mcp_manager.fetch_prompts_for_server(config.name)
                        except Exception as e:
                            logger.warning(f"服务器 {config.name} 获取提示模板列表失败: {e}")
                else:
                    logger.warning(f"❌ 服务器 {config.name} 连接失败")
                return config, success
            except Exception as e:
                logger.error(f"❌ 服务器 {config.name} 连接异常: {e}")
                return config, False

        # 并行执行所有连接
        start_time = time.time()
        results = await asyncio.gather(*[connect_single_server(cfg) for cfg in enabled_configs], return_exceptions=True)
        connect_duration = time.time() - start_time

        # 统计连接结果
        success_count = 0
        failed_count = 0
        for result in results:
            if isinstance(result, Exception):
                failed_count += 1
                logger.error(f"连接任务异常: {result}")
            elif isinstance(result, tuple):
                _, success = result
                if success:
                    success_count += 1
                else:
                    failed_count += 1

        logger.info(f"并行连接完成: {success_count} 成功, {failed_count} 失败, 耗时 {connect_duration:.2f}s")

        # 注册所有工具
        from src.plugin_system.core.component_registry import component_registry

        registered_count = 0

        for tool_key, (tool_info, _) in mcp_manager.all_tools.items():
            tool_name = tool_key.replace("-", "_").replace(".", "_")
            is_disabled = tool_name in disabled_tools

            info, tool_class = mcp_tool_registry.register_tool(tool_key, tool_info, tool_prefix, disabled=is_disabled)
            info.plugin_name = self.plugin_name

            if component_registry.register_component(info, tool_class):
                registered_count += 1
                status = "🚫" if is_disabled else "✅"
                logger.info(f"{status} 注册 MCP 工具: {tool_class.name}")
            else:
                logger.warning(f"❌ 注册 MCP 工具失败: {tool_class.name}")

        self._initialized = True
        logger.info(f"MCP 桥接插件初始化完成，已注册 {registered_count} 个工具")

        # 更新状态显示
        self._update_status_display()
        self._update_tool_list_display()

    def _parse_servers_json(self, servers_list: str) -> List[Dict]:
        """解析服务器列表 JSON 字符串"""
        if not servers_list.strip():
            return []

        content = servers_list.strip()

        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict):
                logger.warning("服务器配置是单个对象，已自动转换为数组")
                return [parsed]
            else:
                logger.error("服务器配置格式错误: 期望数组或对象")
                return []
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")

            if content.startswith("{") and not content.startswith("["):
                try:
                    fixed_content = f"[{content}]"
                    parsed = json.loads(fixed_content)
                    if isinstance(parsed, list):
                        logger.warning("✅ 自动修复成功！请修正配置格式")
                        return parsed
                except json.JSONDecodeError:
                    pass

            logger.error("❌ 服务器配置 JSON 格式错误")
            return []

    def _parse_server_config(self, conf: Dict) -> MCPServerConfig:
        """解析服务器配置字典"""
        transport_str = conf.get("transport", "stdio").lower()

        transport_map = {
            "stdio": TransportType.STDIO,
            "sse": TransportType.SSE,
            "http": TransportType.HTTP,
            "streamable_http": TransportType.STREAMABLE_HTTP,
        }
        transport = transport_map.get(transport_str, TransportType.STDIO)

        return MCPServerConfig(
            name=conf.get("name", "unnamed"),
            enabled=conf.get("enabled", True),
            transport=transport,
            command=conf.get("command", ""),
            args=conf.get("args", []),
            env=conf.get("env", {}),
            url=conf.get("url", ""),
            headers=conf.get("headers", {}),  # v1.4.2: 鉴权头支持
        )

    def _update_tool_list_display(self) -> None:
        """v1.4.0: 更新工具列表显示"""
        import tomlkit

        tools = mcp_manager.all_tools
        disabled_tools = self._get_disabled_tools()

        lines = []
        by_server: Dict[str, List[str]] = {}

        for tool_key, (tool_info, _) in tools.items():
            tool_name = tool_key.replace("-", "_").replace(".", "_")
            if tool_info.server_name not in by_server:
                by_server[tool_info.server_name] = []

            is_disabled = tool_name in disabled_tools
            status = " ❌" if is_disabled else ""
            by_server[tool_info.server_name].append(f"  • {tool_name}{status}")

        for srv_name, tool_list in by_server.items():
            lines.append(f"📦 {srv_name} ({len(tool_list)}个工具):")
            lines.extend(tool_list)
            lines.append("")

        if not by_server:
            lines.append("(无已注册工具)")

        tool_list_text = "\n".join(lines)

        # 更新内存配置
        if "tools" not in self.config:
            self.config["tools"] = {}
        self.config["tools"]["tool_list"] = tool_list_text

        # 写入配置文件
        try:
            config_path = Path(__file__).parent / "config.toml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    doc = tomlkit.load(f)

                if "tools" not in doc:
                    doc["tools"] = tomlkit.table()
                # 使用 tomlkit 多行字符串避免控制字符问题
                from tomlkit.items import String, StringType, Trivia

                ml_string = String(StringType.MLB, tool_list_text, tool_list_text, Trivia())
                doc["tools"]["tool_list"] = ml_string

                with open(config_path, "w", encoding="utf-8") as f:
                    tomlkit.dump(doc, f)
        except Exception as e:
            logger.warning(f"更新工具列表显示失败: {e}")

    def _update_status_display(self) -> None:
        """更新配置文件中的状态显示字段"""
        import tomlkit

        status = mcp_manager.get_status()
        settings = self.config.get("settings", {})
        lines = []

        lines.append(f"服务器: {status['connected_servers']}/{status['total_servers']} 已连接")
        lines.append(f"工具数: {status['total_tools']}")
        if settings.get("enable_resources", False):
            lines.append(f"资源数: {status.get('total_resources', 0)}")
        if settings.get("enable_prompts", False):
            lines.append(f"模板数: {status.get('total_prompts', 0)}")
        lines.append(f"心跳: {'运行中' if status['heartbeat_running'] else '已停止'}")
        lines.append("")

        tools = mcp_manager.all_tools

        for name, info in status.get("servers", {}).items():
            icon = "✅" if info["connected"] else "❌"
            lines.append(f"{icon} {name} ({info['transport']})")

            # v1.7.0: 显示断路器状态
            cb_status = info.get("circuit_breaker", {})
            cb_state = cb_status.get("state", "closed")
            if cb_state == "open":
                lines.append("   ⚡ 断路器: 熔断中")
            elif cb_state == "half_open":
                lines.append("   ⚡ 断路器: 试探中")

            server_tools = [t.name for key, (t, _) in tools.items() if t.server_name == name]
            if server_tools:
                for tool_name in server_tools:
                    lines.append(f"   • {tool_name}")
            else:
                lines.append("   (无工具)")

        if not status.get("servers"):
            lines.append("(无服务器)")

        status_text = "\n".join(lines)

        if "status" not in self.config:
            self.config["status"] = {}
        self.config["status"]["connection_status"] = status_text

        try:
            config_path = Path(__file__).parent / "config.toml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    doc = tomlkit.load(f)

                if "status" not in doc:
                    doc["status"] = tomlkit.table()
                # 使用 tomlkit 多行字符串避免控制字符问题
                from tomlkit.items import String, StringType, Trivia

                ml_string = String(StringType.MLB, status_text, status_text, Trivia())
                doc["status"]["connection_status"] = ml_string

                with open(config_path, "w", encoding="utf-8") as f:
                    tomlkit.dump(doc, f)
        except Exception as e:
            logger.warning(f"更新配置文件状态失败: {e}")

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件的所有组件"""
        components: List[Tuple[ComponentInfo, Type]] = []

        # 事件处理器
        components.append((MCPStartupHandler.get_handler_info(), MCPStartupHandler))
        components.append((MCPStopHandler.get_handler_info(), MCPStopHandler))

        # 命令
        components.append((MCPStatusCommand.get_command_info(), MCPStatusCommand))
        components.append((MCPImportCommand.get_command_info(), MCPImportCommand))

        # 内置工具
        status_tool_info = ToolInfo(
            name=MCPStatusTool.name,
            tool_description=MCPStatusTool.description,
            enabled=True,
            tool_parameters=MCPStatusTool.parameters,
            component_type=ComponentType.TOOL,
        )
        components.append((status_tool_info, MCPStatusTool))

        settings = self.config.get("settings", {})

        if settings.get("enable_resources", False):
            read_resource_info = ToolInfo(
                name=MCPReadResourceTool.name,
                tool_description=MCPReadResourceTool.description,
                enabled=True,
                tool_parameters=MCPReadResourceTool.parameters,
                component_type=ComponentType.TOOL,
            )
            components.append((read_resource_info, MCPReadResourceTool))

        if settings.get("enable_prompts", False):
            get_prompt_info = ToolInfo(
                name=MCPGetPromptTool.name,
                tool_description=MCPGetPromptTool.description,
                enabled=True,
                tool_parameters=MCPGetPromptTool.parameters,
                component_type=ComponentType.TOOL,
            )
            components.append((get_prompt_info, MCPGetPromptTool))

        return components

    def get_status(self) -> Dict[str, Any]:
        """获取插件状态"""
        return {
            "initialized": self._initialized,
            "mcp_manager": mcp_manager.get_status(),
            "registered_tools": len(mcp_tool_registry._tool_classes),
            "trace_records": tool_call_tracer.total_records,
            "cache_stats": tool_call_cache.get_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取详细统计信息"""
        return mcp_manager.get_all_stats()
