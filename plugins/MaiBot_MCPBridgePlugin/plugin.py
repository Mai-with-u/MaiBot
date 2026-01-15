"""
MCP 桥接插件 v2.0.0
将 MCP (Model Context Protocol) 服务器的工具桥接到 MaiBot

v2.0.0 配置与架构精简（功能保持不变）:
- MCP 服务器配置统一为 Claude Desktop 的 mcpServers JSON（WebUI / config.toml 同一入口）
- 兼容迁移：检测到旧版 servers.list 时自动迁移为 mcpServers（仅迁移，避免多入口混淆）
- 移除 WebUI 导入导出/快速添加服务器的旧实现（避免 tomlkit 依赖与格式混乱）

v1.9.0 双轨制架构:
- 软流程 (ReAct): LLM 自主决策，动态多轮调用 MCP 工具，灵活应对复杂场景
- 硬流程 (Workflow): 用户预定义的工作流，固定执行顺序，可靠可控
- 工具链重命名为 Workflow，更清晰地表达其"预定义流程"的本质
- 命令更新：/mcp workflow 替代 /mcp chain

v1.8.1 工具链易用性优化:
- 快速添加工具链：WebUI 表单式配置，无需手写 JSON
- 工具链模板：提供常用工具链配置模板参考
- 使用指南：内置变量语法和命令说明
- 状态显示优化：详细展示工具链步骤和参数信息

v1.8.0 工具链支持:
- 工具链：将多个工具按顺序执行，后续工具可使用前序工具的输出
- 自定义工具链：在 WebUI 配置工具链，自动注册为组合工具供 LLM 调用
- 变量替换：支持 ${input.参数}、${step.输出键}、${prev} 变量
- 工具链命令：/mcp chain 查看、测试、管理工具链

v1.7.0 稳定性与易用性优化:
- 断路器模式：故障服务器快速失败，避免拖慢整体响应
- 状态实时刷新：WebUI 每 10 秒自动更新连接状态
- 断路器状态显示：在状态面板显示熔断/试探状态

v1.6.0 配置导入导出:
- 新增 /mcp import 命令，支持从 Claude Desktop 格式导入配置
- 新增 /mcp export 命令，导出为 Claude Desktop (mcpServers) 格式
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

v1.5.1 易用性优化（v2.0.0 起已移除）:
- 「快速添加服务器」表单式配置（已统一为 Claude mcpServers JSON，避免多入口混淆）

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
from src.plugin_system.base.config_types import section_meta
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
from .core.claude_config import (
    ClaudeConfigError,
    legacy_servers_list_to_claude_config,
    parse_claude_mcp_config,
)
from .tool_chain import (
    ToolChainDefinition,
    tool_chain_manager,
)

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


def parse_mcp_parameters(input_schema: Dict[str, Any]) -> List[Tuple[str, ToolParamType, str, bool, Optional[List[str]]]]:
    """解析 MCP 工具的参数 schema，转换为 MaiBot 的参数格式"""
    parameters = []
    
    if not input_schema:
        # 为无参数的工具添加占位参数，避免某些模型报错
        parameters.append(("_placeholder", ToolParamType.STRING, "占位参数，无需填写", False, None))
        return parameters
    
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    
    # 如果没有任何参数，添加占位参数
    if not properties:
        parameters.append(("_placeholder", ToolParamType.STRING, "占位参数，无需填写", False, None))
        return parameters
    
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
            return {
                "name": self.name,
                "content": f"⛔ 权限不足：工具 {self.name} 在当前场景下不可用"
            }
        
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

        servers = _plugin_instance._load_mcp_servers_config()
        for server_conf in servers:
            if server_conf.get("name") == self._mcp_server_name:
                return server_conf.get("post_process")
        
        return None
    
    async def _call_post_process_llm(
        self,
        prompt: str,
        max_tokens: int,
        settings: Dict[str, Any],
        server_config: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """调用 LLM 进行后处理"""
        from src.config.config import model_config
        from src.config.model_configs import TaskConfig
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
    tool_key: str,
    tool_info: MCPToolInfo,
    tool_prefix: str,
    disabled: bool = False
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
        }
    )
    
    return tool_class


class MCPToolRegistry:
    """MCP 工具注册表"""
    
    def __init__(self):
        self._tool_classes: Dict[str, Type[MCPToolProxy]] = {}
        self._tool_infos: Dict[str, ToolInfo] = {}
    
    def register_tool(
        self,
        tool_key: str,
        tool_info: MCPToolInfo,
        tool_prefix: str,
        disabled: bool = False
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


# ============================================================================
# v1.8.0: 工具链代理工具
# ============================================================================

class ToolChainProxyBase(BaseTool):
    """工具链代理基类"""
    
    name: str = ""
    description: str = ""
    parameters: List[Tuple[str, ToolParamType, str, bool, Optional[List[str]]]] = []
    available_for_llm: bool = True
    
    _chain_name: str = ""
    
    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具链"""
        # 移除内部标记
        args = {k: v for k, v in function_args.items() if k != "llm_called"}
        
        logger.debug(f"执行工具链 {self._chain_name}，参数: {args}")
        
        result = await tool_chain_manager.execute_chain(self._chain_name, args)
        
        if result.success:
            # 构建输出
            output_parts = []
            output_parts.append(result.final_output)
            
            # 可选：添加执行摘要
            # output_parts.append(f"\n\n---\n执行摘要:\n{result.to_summary()}")
            
            return {"name": self.name, "content": "\n".join(output_parts)}
        else:
            error_msg = f"⚠️ 工具链执行失败: {result.error}"
            if result.step_results:
                error_msg += f"\n\n执行详情:\n{result.to_summary()}"
            return {"name": self.name, "content": error_msg}
    
    async def direct_execute(self, **function_args) -> Dict[str, Any]:
        return await self.execute(function_args)


def create_chain_tool_class(chain: ToolChainDefinition) -> Type[ToolChainProxyBase]:
    """根据工具链定义动态创建工具类"""
    # 构建参数列表
    parameters = []
    for param_name, param_desc in chain.input_params.items():
        parameters.append((param_name, ToolParamType.STRING, param_desc, True, None))
    
    # 生成类名和工具名
    class_name = f"ToolChain_{chain.name}".replace("-", "_").replace(".", "_")
    tool_name = f"chain_{chain.name}".replace("-", "_").replace(".", "_")
    
    # 构建描述
    description = chain.description
    if chain.steps:
        step_names = [s.tool_name.split("_")[-1] for s in chain.steps[:3]]
        description += f" (执行流程: {' → '.join(step_names)}{'...' if len(chain.steps) > 3 else ''})"
    
    tool_class = type(
        class_name,
        (ToolChainProxyBase,),
        {
            "name": tool_name,
            "description": description,
            "parameters": parameters,
            "available_for_llm": True,
            "_chain_name": chain.name,
        }
    )
    
    return tool_class


class ToolChainRegistry:
    """工具链注册表"""
    
    def __init__(self):
        self._tool_classes: Dict[str, Type[ToolChainProxyBase]] = {}
        self._tool_infos: Dict[str, ToolInfo] = {}
    
    def register_chain(self, chain: ToolChainDefinition) -> Tuple[ToolInfo, Type[ToolChainProxyBase]]:
        """注册工具链为组合工具"""
        tool_class = create_chain_tool_class(chain)
        
        self._tool_classes[chain.name] = tool_class
        
        info = ToolInfo(
            name=tool_class.name,
            tool_description=tool_class.description,
            enabled=True,
            tool_parameters=tool_class.parameters,
            component_type=ComponentType.TOOL,
        )
        self._tool_infos[chain.name] = info
        
        return info, tool_class
    
    def unregister_chain(self, chain_name: str) -> bool:
        """注销工具链"""
        if chain_name in self._tool_classes:
            del self._tool_classes[chain_name]
            del self._tool_infos[chain_name]
            return True
        return False
    
    def get_all_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """获取所有工具链组件"""
        return [(self._tool_infos[key], self._tool_classes[key]) for key in self._tool_classes.keys()]
    
    def clear(self) -> None:
        """清空所有注册"""
        self._tool_classes.clear()
        self._tool_infos.clear()


# 全局工具链注册表
tool_chain_registry = ToolChainRegistry()


class MCPStatusTool(BaseTool):
    """MCP 状态查询工具"""
    
    name = "mcp_status"
    description = "查询 MCP 桥接插件的状态，包括服务器连接状态、可用工具列表、工具链列表、资源列表、提示模板列表、调用统计、追踪记录等信息"
    parameters = [
        ("query_type", ToolParamType.STRING, "查询类型", False, ["status", "tools", "chains", "resources", "prompts", "stats", "trace", "cache", "all"]),
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
        
        if query_type in ("chains", "all"):
            result_parts.append(self._format_chains())
        
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
        
        return {
            "name": self.name,
            "content": "\n\n".join(result_parts) if result_parts else "未知的查询类型"
        }
    
    def _format_status(self, server_name: Optional[str] = None) -> str:
        status = mcp_manager.get_status()
        lines = ["📊 MCP 桥接插件状态"]
        lines.append(f"  总服务器数: {status['total_servers']}")
        lines.append(f"  已连接: {status['connected_servers']}")
        lines.append(f"  已断开: {status['disconnected_servers']}")
        lines.append(f"  可用工具数: {status['total_tools']}")
        lines.append(f"  心跳检测: {'运行中' if status['heartbeat_running'] else '已停止'}")
        
        lines.append("\n🔌 服务器详情:")
        for name, info in status['servers'].items():
            if server_name and name != server_name:
                continue
            status_icon = "✅" if info['connected'] else "❌"
            enabled_text = "" if info['enabled'] else " (已禁用)"
            lines.append(f"  {status_icon} {name}{enabled_text}")
            lines.append(f"     传输: {info['transport']}, 工具数: {info['tools_count']}")
            if info['consecutive_failures'] > 0:
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
        
        g = stats['global']
        lines.append(f"  总调用次数: {g['total_tool_calls']}")
        lines.append(f"  成功: {g['successful_calls']}, 失败: {g['failed_calls']}")
        if g['total_tool_calls'] > 0:
            success_rate = (g['successful_calls'] / g['total_tool_calls']) * 100
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
    
    def _format_chains(self) -> str:
        """v1.8.0: 格式化工具链列表"""
        chains = tool_chain_manager.get_all_chains()
        if not chains:
            return "🔗 当前没有配置工具链"
        
        lines = ["🔗 工具链列表"]
        for name, chain in chains.items():
            status = "✅" if chain.enabled else "❌"
            lines.append(f"\n{status} {name}")
            lines.append(f"   描述: {chain.description[:50]}...")
            lines.append(f"   步骤: {len(chain.steps)} 个")
            for i, step in enumerate(chain.steps[:3]):
                lines.append(f"     {i+1}. {step.tool_name}")
            if len(chain.steps) > 3:
                lines.append(f"     ... 还有 {len(chain.steps) - 3} 个步骤")
            if chain.input_params:
                params = ", ".join(chain.input_params.keys())
                lines.append(f"   参数: {params}")
        
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
    command_pattern = r"^[/／]mcp(?:\s+(?P<subcommand>status|tools|stats|reconnect|trace|cache|perm|export|search|chain))?(?:\s+(?P<arg>.+))?$"

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
        
        # v1.8.0: 工具链命令
        if subcommand == "chain":
            return await self._handle_chain(arg)

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
        
        servers_section = _plugin_instance.config.get("servers", {})
        if not isinstance(servers_section, dict):
            servers_section = {}

        claude_json = str(servers_section.get("claude_config_json", "") or "")
        if not claude_json.strip():
            legacy_list = str(servers_section.get("list", "") or "")
            claude_json = legacy_servers_list_to_claude_config(legacy_list) or ""

        if not claude_json.strip():
            await self.send_text("📤 当前没有配置任何服务器")
            return (True, None, True)

        try:
            pretty = json.dumps(json.loads(claude_json), ensure_ascii=False, indent=2)
        except Exception:
            pretty = claude_json

        lines = ["📤 导出为 Claude Desktop 格式（mcpServers）:"]
        if format_type and format_type.strip() and format_type.strip().lower() != "claude":
            lines.append("（v2.0 已精简为仅 Claude 格式，忽略其他格式参数）")
        lines.append("")
        lines.append(pretty)
        await self.send_text("\n".join(lines))
        
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

    async def _handle_chain(self, arg: Optional[str] = None) -> Tuple[bool, Optional[str], bool]:
        """v1.8.0: 处理工具链命令"""
        if not arg or not arg.strip():
            # 显示工具链列表和帮助
            chains = tool_chain_manager.get_all_chains()
            
            lines = ["🔗 工具链管理"]
            lines.append("")
            
            if chains:
                lines.append(f"已配置 {len(chains)} 个工具链:")
                for name, chain in chains.items():
                    status = "✅" if chain.enabled else "❌"
                    steps_count = len(chain.steps)
                    lines.append(f"  {status} {name} ({steps_count} 步)")
            else:
                lines.append("当前没有配置工具链")
            
            lines.append("")
            lines.append("命令:")
            lines.append("  /mcp chain list       查看所有工具链")
            lines.append("  /mcp chain <名称>     查看工具链详情")
            lines.append("  /mcp chain test <名称> <参数JSON>  测试执行")
            lines.append("  /mcp chain reload     重新加载配置")
            lines.append("")
            lines.append("💡 在 WebUI「工具链」配置区编辑工具链")
            
            await self.send_text("\n".join(lines))
            return (True, None, True)
        
        parts = arg.strip().split(maxsplit=2)
        sub_action = parts[0].lower()
        
        if sub_action == "list":
            # 列出所有工具链
            chains = tool_chain_manager.get_all_chains()
            if not chains:
                await self.send_text("🔗 当前没有配置工具链")
                return (True, None, True)
            
            lines = [f"🔗 工具链列表 ({len(chains)} 个)"]
            for name, chain in chains.items():
                status = "✅" if chain.enabled else "❌"
                lines.append(f"\n{status} {name}")
                lines.append(f"   {chain.description[:60]}...")
                lines.append(f"   步骤: {' → '.join([s.tool_name.split('_')[-1] for s in chain.steps[:4]])}")
                if chain.input_params:
                    lines.append(f"   参数: {', '.join(chain.input_params.keys())}")
            
            await self.send_text("\n".join(lines))
            return (True, None, True)
        
        elif sub_action == "reload":
            # 重新加载工具链配置
            global _plugin_instance
            if _plugin_instance:
                _plugin_instance._load_tool_chains()
                chains = tool_chain_manager.get_all_chains()
                from src.plugin_system.core.component_registry import component_registry
                registered = 0
                for name, chain in tool_chain_manager.get_enabled_chains().items():
                    tool_name = f"chain_{name}".replace("-", "_").replace(".", "_")
                    if component_registry.get_component_info(tool_name, ComponentType.TOOL):
                        registered += 1
                lines = ["✅ 已重新加载工具链配置"]
                lines.append(f"📋 配置数: {len(chains)} 个")
                lines.append(f"🔧 已注册: {registered} 个（可被 LLM 调用）")
                if chains:
                    lines.append("")
                    lines.append("工具链列表:")
                    for name, chain in chains.items():
                        status = "✅" if chain.enabled else "❌"
                        lines.append(f"  {status} chain_{name}")
                await self.send_text("\n".join(lines))
            else:
                await self.send_text("❌ 插件未初始化")
            return (True, None, True)
        
        elif sub_action == "test" and len(parts) >= 2:
            # 测试执行工具链
            chain_name = parts[1]
            args_json = parts[2] if len(parts) > 2 else "{}"
            
            chain = tool_chain_manager.get_chain(chain_name)
            if not chain:
                await self.send_text(f"❌ 工具链 '{chain_name}' 不存在")
                return (True, None, True)
            
            try:
                input_args = json.loads(args_json)
            except json.JSONDecodeError:
                await self.send_text("❌ 参数 JSON 格式错误")
                return (True, None, True)
            
            await self.send_text(f"🔄 正在执行工具链 {chain_name}...")
            
            result = await tool_chain_manager.execute_chain(chain_name, input_args)
            
            lines = []
            if result.success:
                lines.append(f"✅ 工具链执行成功 ({result.total_duration_ms:.0f}ms)")
                lines.append("")
                lines.append("执行详情:")
                lines.append(result.to_summary())
                lines.append("")
                lines.append("最终输出:")
                output_preview = result.final_output[:500]
                if len(result.final_output) > 500:
                    output_preview += "..."
                lines.append(output_preview)
            else:
                lines.append("❌ 工具链执行失败")
                lines.append(f"错误: {result.error}")
                if result.step_results:
                    lines.append("")
                    lines.append("执行详情:")
                    lines.append(result.to_summary())
            
            await self.send_text("\n".join(lines))
            return (True, None, True)
        
        else:
            # 查看特定工具链详情
            chain_name = sub_action
            chain = tool_chain_manager.get_chain(chain_name)
            
            if not chain:
                # 尝试模糊匹配
                all_chains = tool_chain_manager.get_all_chains()
                similar = [n for n in all_chains.keys() if chain_name.lower() in n.lower()]
                msg = f"❌ 工具链 '{chain_name}' 不存在"
                if similar:
                    msg += f"\n💡 你是不是想找: {', '.join(similar[:3])}"
                await self.send_text(msg)
                return (True, None, True)
            
            lines = [f"🔗 工具链: {chain.name}"]
            lines.append(f"状态: {'✅ 启用' if chain.enabled else '❌ 禁用'}")
            lines.append(f"描述: {chain.description}")
            lines.append("")
            
            if chain.input_params:
                lines.append("📥 输入参数:")
                for param, desc in chain.input_params.items():
                    lines.append(f"  • {param}: {desc}")
                lines.append("")
            
            lines.append(f"📋 执行步骤 ({len(chain.steps)} 个):")
            for i, step in enumerate(chain.steps):
                optional_tag = " (可选)" if step.optional else ""
                lines.append(f"  {i+1}. {step.tool_name}{optional_tag}")
                if step.description:
                    lines.append(f"     {step.description}")
                if step.output_key:
                    lines.append(f"     输出键: {step.output_key}")
                if step.args_template:
                    args_preview = json.dumps(step.args_template, ensure_ascii=False)[:60]
                    lines.append(f"     参数: {args_preview}...")
            
            lines.append("")
            lines.append(f"💡 测试: /mcp chain test {chain.name} " + '{"参数": "值"}')
            
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
            lines.append("服务器配置（Claude）:")
            lines.append("  /mcp import <json>    合并 Claude mcpServers 配置")
            lines.append("  /mcp export           导出当前 mcpServers 配置")
            lines.append("")
            lines.append("工具链:")
            lines.append("  /mcp chain            查看工具链列表")
            lines.append("  /mcp chain <名称>     查看工具链详情")
            lines.append("  /mcp chain test <名称> <参数>  测试执行")
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
• 兼容旧版：MaiBot servers 列表数组（将自动迁移为 mcpServers）

示例:
/mcp import {"mcpServers":{"time":{"command":"uvx","args":["mcp-server-time"]}}}

/mcp import {"mcpServers":{"api":{"url":"https://example.com/mcp","transport":"sse"}}}"""
            await self.send_text(help_text)
            return (True, None, True)

        raw_text = content.strip()

        # 解析输入：支持 Claude mcpServers 或旧版 servers 列表数组
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            await self.send_text(f"❌ JSON 解析失败: {e}")
            return (True, None, True)

        if isinstance(data, list):
            migrated = legacy_servers_list_to_claude_config(raw_text)
            if not migrated:
                await self.send_text("❌ 旧版 servers 列表解析失败，无法迁移")
                return (True, None, True)
            data = json.loads(migrated)

        if not isinstance(data, dict):
            await self.send_text("❌ 配置必须是 JSON 对象（包含 mcpServers）")
            return (True, None, True)

        incoming_mapping = data.get("mcpServers", data)
        if not isinstance(incoming_mapping, dict):
            await self.send_text("❌ mcpServers 必须是 JSON 对象")
            return (True, None, True)

        # 校验输入配置
        try:
            parse_claude_mcp_config(json.dumps({"mcpServers": incoming_mapping}, ensure_ascii=False))
        except ClaudeConfigError as e:
            await self.send_text(f"❌ 配置校验失败: {e}")
            return (True, None, True)

        servers_section = _plugin_instance.config.get("servers", {})
        if not isinstance(servers_section, dict):
            servers_section = {}

        existing_json = str(servers_section.get("claude_config_json", "") or "")
        if not existing_json.strip():
            legacy_list = str(servers_section.get("list", "") or "")
            existing_json = legacy_servers_list_to_claude_config(legacy_list) or ""

        existing_mapping: Dict[str, Any] = {}
        if existing_json.strip():
            try:
                parsed = json.loads(existing_json)
                mapping = parsed.get("mcpServers", parsed)
                if isinstance(mapping, dict):
                    existing_mapping = mapping
            except Exception:
                existing_mapping = {}

        added: List[str] = []
        skipped: List[str] = []

        for name, conf in incoming_mapping.items():
            if name in existing_mapping:
                skipped.append(str(name))
                continue
            existing_mapping[str(name)] = conf
            added.append(str(name))

        if "servers" not in _plugin_instance.config:
            _plugin_instance.config["servers"] = {}

        _plugin_instance.config["servers"]["claude_config_json"] = json.dumps(
            {"mcpServers": existing_mapping}, ensure_ascii=False, indent=2
        )

        # 持久化到配置文件（使用插件基类的写入逻辑）
        try:
            config_path = Path(_plugin_instance.plugin_dir) / _plugin_instance.config_file_name
            _plugin_instance._save_config_to_file(_plugin_instance.config, str(config_path))
        except Exception as e:
            logger.warning(f"保存配置文件失败: {e}")

        lines = []
        if added:
            lines.append(f"✅ 成功导入 {len(added)} 个服务器:")
            for n in added[:20]:
                lines.append(f"  • {n}")
            if len(added) > 20:
                lines.append(f"  ... 还有 {len(added) - 20} 个")
        else:
            lines.append("⚠️ 没有新服务器可导入")

        if skipped:
            lines.append(f"\n⏭️ 跳过 {len(skipped)} 个已存在的服务器")

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

        if _plugin_instance is not None:
            await _plugin_instance._stop_status_refresher()
        
        await mcp_manager.shutdown()
        mcp_tool_registry.clear()
        
        logger.info("MCP 桥接插件已关闭所有连接")
        return (True, True, None, None, None)


# ============================================================================
# 主插件类
# ============================================================================

@register_plugin
class MCPBridgePlugin(BasePlugin):
    """MCP 桥接插件 v2.0.0 - 将 MCP 服务器的工具桥接到 MaiBot"""
    
    plugin_name: str = "mcp_bridge_plugin"
    enable_plugin: bool = False  # 默认禁用，用户需在 WebUI 手动启用
    dependencies: List[str] = []
    python_dependencies: List[str] = ["mcp"]
    config_file_name: str = "config.toml"
    
    config_section_descriptions = {
        "guide": section_meta("📖 快速入门", order=1),
        "plugin": section_meta("🔘 插件开关", order=2),
        "servers": section_meta("🔌 MCP Servers（Claude）", order=3),
        "tool_chains": section_meta("🔗 Workflow（硬流程/工具链）", order=4),
        "react": section_meta("🔄 ReAct（软流程）", collapsed=True, order=5),
        "status": section_meta("📊 运行状态", order=10),
        "tools": section_meta("🔧 工具管理", collapsed=True, order=20),
        "permissions": section_meta("🔐 权限控制", collapsed=True, order=21),
        "settings": section_meta("⚙️ 高级设置", collapsed=True, order=30),
    }
    
    config_schema: dict = {
        # 新手引导区（只读）
        "guide": {
            "quick_start": ConfigField(
                type=str,
                default="1. 获取 MCP 服务器  2. 在「MCP Servers（Claude）」粘贴 mcpServers 配置  3. 保存后发送 /mcp reconnect  4. （可选）在「Workflow/ ReAct」配置流程",
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
                hint="魔搭 ModelScope 国内免费推荐，将 mcpServers 配置粘贴到「MCP Servers（Claude）」即可",
                order=2,
            ),
            "example_config": ConfigField(
                type=str,
                default='{"mcpServers":{"time":{"url":"https://mcp.api-inference.modelscope.cn/server/mcp-server-time"}}}',
                description="复制到 MCP Servers（Claude）可直接使用（免费时间服务器）",
                label="📝 配置示例",
                disabled=True,
                order=3,
            ),
        },
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=False,
                description="是否启用插件（默认关闭）",
                label="启用插件",
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
                hint="值越小刷新越频繁，但会增加少量 CPU 消耗",
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
                hint="从此处复制工具名到下方禁用列表或工具链配置",
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
        # v1.8.0 工具链配置
        "tool_chains": {
            "chains_enabled": ConfigField(
                type=bool,
                default=True,
                description="🔗 启用工具链功能",
                label="🔗 启用工具链",
                hint="工具链可将多个工具按顺序执行，后续工具可使用前序工具的输出",
                order=1,
            ),
            # 工具链使用指南
            "chains_guide": ConfigField(
                type=str,
                default="""工具链将多个 MCP 工具串联执行，后续步骤可使用前序步骤的输出

📌 变量语法:
  ${input.参数名}  - 用户输入的参数
  ${step.输出键}   - 某步骤的输出（需设置 output_key）
  ${prev}         - 上一步的输出
  ${prev.字段}    - 上一步输出(JSON)的某字段
  ${step.输出键.0.字段} / ${step.输出键[0].字段} - 访问数组下标
  ${step.输出键['return'][0]['location']}       - 支持 bracket 写法

📌 测试命令:
  /mcp chain list          - 查看所有工具链
  /mcp chain 链名 {"参数":"值"}  - 测试执行""",
                description="工具链使用说明",
                label="📖 使用指南",
                input_type="textarea",
                disabled=True,
                rows=10,
                order=2,
            ),
            # 快速添加工具链（表单式）
            "quick_chain_name": ConfigField(
                type=str,
                default="",
                description="工具链名称（英文，如 search_and_summarize）",
                label="➕ 快速添加 - 名称",
                placeholder="my_tool_chain",
                hint="必填，将作为 LLM 可调用的工具名",
                order=10,
            ),
            "quick_chain_desc": ConfigField(
                type=str,
                default="",
                description="工具链描述（供 LLM 理解何时使用）",
                label="➕ 快速添加 - 描述",
                placeholder="先搜索内容，再获取详情并总结",
                hint="必填，清晰描述工具链的用途",
                order=11,
            ),
            "quick_chain_params": ConfigField(
                type=str,
                default="",
                description="输入参数（每行一个，格式: 参数名=描述）",
                label="➕ 快速添加 - 输入参数",
                input_type="textarea",
                rows=3,
                placeholder="query=搜索关键词\nmax_results=最大结果数",
                hint="定义用户需要提供的参数",
                order=12,
            ),
            "quick_chain_steps": ConfigField(
                type=str,
                default="",
                description="执行步骤（每行一个，格式: 工具名|参数JSON|输出键）",
                label="➕ 快速添加 - 执行步骤",
                input_type="textarea",
                rows=5,
                placeholder='mcp_server_search|{"keyword":"${input.query}"}|search_result\nmcp_server_detail|{"id":"${prev}"}|\n# 访问数组示例:\n# mcp_geo|{"q":"${input.query}"}|geo\n# mcp_next|{"location":"${step.geo.return.0.location}"}|',
                hint="格式: 工具名|参数模板|输出键（输出键可选，用于后续步骤引用 ${step.xxx}）",
                order=13,
            ),
            "quick_chain_add": ConfigField(
                type=str,
                default="",
                description="填写上方信息后，在此输入 ADD 并保存即可添加",
                label="➕ 确认添加",
                placeholder="输入 ADD 并保存",
                hint="添加后会自动合并到下方工具链列表",
                order=14,
            ),
            # 工具链模板
            "chains_templates": ConfigField(
                type=str,
                default="""📋 常用工具链模板（复制到下方列表使用）:

1️⃣ 搜索+详情模板:
{
  "name": "search_and_detail",
  "description": "搜索内容并获取详情",
  "input_params": {"query": "搜索关键词"},
  "steps": [
    {"tool_name": "搜索工具名", "args_template": {"keyword": "${input.query}"}, "output_key": "results"},
    {"tool_name": "详情工具名", "args_template": {"id": "${prev}"}}
  ]
}

2️⃣ 获取+处理模板:
{
  "name": "fetch_and_process",
  "description": "获取数据并处理",
  "input_params": {"url": "目标URL"},
  "steps": [
    {"tool_name": "获取工具名", "args_template": {"url": "${input.url}"}, "output_key": "data"},
    {"tool_name": "处理工具名", "args_template": {"content": "${step.data}"}}
  ]
}

3️⃣ 多步骤可选模板:
{
  "name": "multi_step_chain",
  "description": "多步骤处理，部分可选",
  "input_params": {"input": "输入内容"},
  "steps": [
    {"tool_name": "步骤1工具", "args_template": {"data": "${input.input}"}, "output_key": "step1"},
    {"tool_name": "步骤2工具", "args_template": {"data": "${prev}"}, "output_key": "step2", "optional": true},
    {"tool_name": "步骤3工具", "args_template": {"data": "${step.step1}"}}
  ]
}""",
                description="工具链配置模板参考",
                label="📝 配置模板",
                input_type="textarea",
                disabled=True,
                rows=15,
                order=20,
            ),
            "chains_list": ConfigField(
                type=str,
                default="[]",
                description="工具链配置（JSON 数组格式）",
                label="📋 工具链列表",
                input_type="textarea",
                rows=20,
                placeholder='''[
  {
    "name": "search_and_detail",
    "description": "先搜索再获取详情",
    "input_params": {"query": "搜索关键词"},
    "steps": [
      {"tool_name": "mcp_server_search", "args_template": {"keyword": "${input.query}"}, "output_key": "search_result"},
      {"tool_name": "mcp_server_get_detail", "args_template": {"id": "${step.search_result}"}}
    ]
  }
]''',
                hint="每个工具链包含 name、description、input_params、steps",
                order=30,
            ),
            "chains_status": ConfigField(
                type=str,
                default="(启动后自动生成)",
                description="当前已注册的工具链状态（只读）",
                label="📊 工具链状态",
                input_type="textarea",
                disabled=True,
                rows=8,
                order=40,
            ),
        },
        # v1.9.0 ReAct 软流程配置
        "react": {
            "react_enabled": ConfigField(
                type=bool,
                default=False,
                description="🔄 将 MCP 工具注册到记忆检索 ReAct 系统",
                label="🔄 启用 ReAct 集成",
                hint="启用后，MaiBot 的 ReAct Agent 可在记忆检索时调用 MCP 工具",
                order=1,
            ),
            "react_guide": ConfigField(
                type=str,
                default="""ReAct 软流程说明:

📌 什么是 ReAct?
ReAct (Reasoning + Acting) 是 LLM 自主决策的多轮工具调用模式。
与 Workflow 硬流程不同，ReAct 由 LLM 动态决定调用哪些工具。

📌 工作原理:
1. 用户提问 → LLM 分析需要什么信息
2. LLM 选择调用工具 → 获取结果
3. LLM 观察结果 → 决定是否需要更多信息
4. 重复 2-3 直到信息足够 → 生成最终回答

📌 与 Workflow 的区别:
- ReAct (软流程): LLM 自主决策，灵活但不可预测
- Workflow (硬流程): 用户预定义，固定流程，可靠可控

📌 使用场景:
- 复杂问题需要多步推理
- 不确定需要调用哪些工具
- 需要根据中间结果动态调整""",
                description="ReAct 软流程使用说明",
                label="📖 使用指南",
                input_type="textarea",
                disabled=True,
                rows=15,
                order=2,
            ),
            "filter_mode": ConfigField(
                type=str,
                default="whitelist",
                description="过滤模式",
                label="📋 过滤模式",
                choices=["whitelist", "blacklist"],
                hint="whitelist: 只注册列出的工具；blacklist: 排除列出的工具",
                order=3,
            ),
            "tool_filter": ConfigField(
                type=str,
                default="",
                description="工具过滤列表（每行一个，支持通配符 * 和精确匹配）",
                label="🔍 工具过滤列表",
                input_type="textarea",
                rows=6,
                placeholder="""# 精确匹配示例:
mcp_bing_web_search_bing_search
mcp_mcmod_search_mod

# 通配符示例:
mcp_*_search_*
mcp_bing_*""",
                hint="白名单模式: 只注册列出的工具；黑名单模式: 排除列出的工具。支持 # 注释",
                order=4,
            ),
            "react_status": ConfigField(
                type=str,
                default="(启动后自动生成)",
                description="当前已注册到 ReAct 的工具状态（只读）",
                label="📊 ReAct 工具状态",
                input_type="textarea",
                disabled=True,
                rows=6,
                order=10,
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
                placeholder='''[
  {"tool": "mcp_*_delete_*", "denied": ["qq:123456:group"]}
]''',
                hint="格式: qq:ID:group/private/user，工具名支持通配符 *",
                order=10,
            ),
        },
        # v2.0: 服务器配置统一为 Claude Desktop mcpServers 规范（JSON）
        "servers": {
            "claude_config_json": ConfigField(
                type=str,
                default='{"mcpServers":{}}',
                description="Claude Desktop 规范的 MCP 配置（JSON）",
                label="🔌 MCP Servers（Claude 规范）",
                input_type="textarea",
                rows=18,
                hint="仅支持 Claude Desktop 的 mcpServers JSON。每个服务器需包含 command(stdio) 或 url(remote)。",
                order=1,
            ),
            "claude_config_guide": ConfigField(
                type=str,
                default="""示例：
{
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
    "time": { "url": "https://mcp.api-inference.modelscope.cn/server/mcp-server-time" }
  }
}

可选字段：
- enabled: true/false
- headers: {"Authorization":"Bearer ..."}
- env: {"KEY":"VALUE"}
- transport/type: "streamable_http" | "http" | "sse"（remote 可选，默认 streamable_http）
""",
                description="配置说明（只读）",
                label="📖 配置说明",
                input_type="textarea",
                disabled=True,
                rows=12,
                order=2,
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
                    unescaped = value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
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
        self._status_refresh_running = False
        self._status_refresh_task: Optional[asyncio.Task] = None
        self._last_persisted_display_hash: str = ""
        self._last_servers_config_error: str = ""
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
        
        # v2.0: 服务器配置统一由 servers.claude_config_json 提供（不再通过 WebUI 导入/快速添加写入旧 servers.list）
        
        # v1.8.0: 初始化工具链管理器
        tool_chain_manager.set_executor(mcp_manager)
        self._load_tool_chains()

    def _persist_runtime_displays(self) -> None:
        """将 WebUI 只读展示字段写回配置文件，使 WebUI 能正确显示运行状态。"""
        try:
            config_path = Path(self.plugin_dir) / self.config_file_name

            payload = {
                "status.connection_status": str(self.config.get("status", {}).get("connection_status", "") or ""),
                "tools.tool_list": str(self.config.get("tools", {}).get("tool_list", "") or ""),
                "tool_chains.chains_status": str(self.config.get("tool_chains", {}).get("chains_status", "") or ""),
                "react.react_status": str(self.config.get("react", {}).get("react_status", "") or ""),
            }
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()
            if digest == self._last_persisted_display_hash:
                return

            self._save_config_to_file(self.config, str(config_path))
            self._last_persisted_display_hash = digest
        except Exception as e:
            logger.debug(f"写回运行状态到配置文件失败: {e}")
    
    def _process_quick_add_chain(self) -> None:
        """v1.8.0: 处理快速添加工具链表单"""
        chains_config = self.config.get("tool_chains", {})
        
        # 检查是否触发添加
        add_trigger = chains_config.get("quick_chain_add", "").strip().upper()
        if add_trigger != "ADD":
            return
        
        # 获取表单数据
        chain_name = chains_config.get("quick_chain_name", "").strip()
        chain_desc = chains_config.get("quick_chain_desc", "").strip()
        params_str = chains_config.get("quick_chain_params", "").strip()
        steps_str = chains_config.get("quick_chain_steps", "").strip()
        
        # 验证必填字段
        if not chain_name:
            logger.warning("快速添加工具链: 名称不能为空")
            self._clear_quick_chain_fields()
            return
        
        if not chain_desc:
            logger.warning("快速添加工具链: 描述不能为空")
            self._clear_quick_chain_fields()
            return
        
        if not steps_str:
            logger.warning("快速添加工具链: 步骤不能为空")
            self._clear_quick_chain_fields()
            return
        
        # 解析输入参数
        input_params = {}
        if params_str:
            for line in params_str.split("\n"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                parts = line.split("=", 1)
                param_name = parts[0].strip()
                param_desc = parts[1].strip() if len(parts) > 1 else param_name
                input_params[param_name] = param_desc
        
        # 解析步骤
        steps = []
        for line in steps_str.split("\n"):
            line = line.strip()
            if not line:
                continue
            
            parts = line.split("|")
            if len(parts) < 2:
                logger.warning(f"快速添加工具链: 步骤格式错误: {line}")
                continue
            
            tool_name = parts[0].strip()
            args_str = parts[1].strip() if len(parts) > 1 else "{}"
            output_key = parts[2].strip() if len(parts) > 2 else ""
            
            # 解析参数 JSON
            try:
                args_template = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                logger.warning(f"快速添加工具链: 参数 JSON 格式错误: {args_str}")
                args_template = {}
            
            steps.append({
                "tool_name": tool_name,
                "args_template": args_template,
                "output_key": output_key,
            })
        
        if not steps:
            logger.warning("快速添加工具链: 没有有效的步骤")
            self._clear_quick_chain_fields()
            return
        
        # 构建新工具链
        new_chain = {
            "name": chain_name,
            "description": chain_desc,
            "input_params": input_params,
            "steps": steps,
            "enabled": True,
        }
        
        # 获取现有工具链列表
        chains_json = chains_config.get("chains_list", "[]")
        try:
            chains_list = json.loads(chains_json) if chains_json.strip() else []
        except json.JSONDecodeError:
            chains_list = []
        
        # 检查是否已存在同名工具链
        for existing in chains_list:
            if existing.get("name") == chain_name:
                logger.info(f"快速添加: 工具链 {chain_name} 已存在，将更新")
                chains_list.remove(existing)
                break
        
        # 添加新工具链
        chains_list.append(new_chain)
        new_chains_json = json.dumps(chains_list, ensure_ascii=False, indent=2)
        
        # 更新配置
        self.config["tool_chains"]["chains_list"] = new_chains_json
        
        # 清空表单字段
        self._clear_quick_chain_fields()
        
        # 保存到配置文件
        self._save_chains_list(new_chains_json)
        
        logger.info(f"快速添加: 已添加工具链 {chain_name} ({len(steps)} 个步骤)")
    
    def _clear_quick_chain_fields(self) -> None:
        """清空快速添加工具链表单字段"""
        if "tool_chains" not in self.config:
            self.config["tool_chains"] = {}
        self.config["tool_chains"]["quick_chain_name"] = ""
        self.config["tool_chains"]["quick_chain_desc"] = ""
        self.config["tool_chains"]["quick_chain_params"] = ""
        self.config["tool_chains"]["quick_chain_steps"] = ""
        self.config["tool_chains"]["quick_chain_add"] = ""
    
    def _save_chains_list(self, chains_json: str) -> None:
        """保存工具链列表到配置文件"""
        try:
            config_path = Path(self.plugin_dir) / self.config_file_name
            self._save_config_to_file(self.config, str(config_path))
            logger.info("工具链列表已保存到配置文件")
        except Exception as e:
            logger.warning(f"保存工具链列表失败: {e}")
    
    def _load_tool_chains(self) -> None:
        """v1.8.0: 加载工具链配置"""
        # 先处理快速添加
        self._process_quick_add_chain()
        
        chains_config = self.config.get("tool_chains", {})
        if not isinstance(chains_config, dict):
            chains_config = {}

        # 兼容旧版本：部分版本可能使用 tool_chain 或其他字段名
        if not chains_config:
            legacy_section = self.config.get("tool_chain")
            if isinstance(legacy_section, dict):
                chains_config = legacy_section
                self.config["tool_chains"] = legacy_section

        # 兼容旧版本：chains_list 字段名变化
        chains_json = str(chains_config.get("chains_list", "") or "")
        if not chains_json.strip():
            for legacy_key in ("list", "chains", "workflow_list", "workflows", "toolchains"):
                legacy_val = chains_config.get(legacy_key)
                if legacy_val is None:
                    continue

                if isinstance(legacy_val, str) and legacy_val.strip():
                    chains_json = legacy_val
                    break

                if isinstance(legacy_val, list):
                    chains_json = json.dumps(legacy_val, ensure_ascii=False, indent=2)
                    break

                if isinstance(legacy_val, dict):
                    chains_json = json.dumps([legacy_val], ensure_ascii=False, indent=2)
                    break

            if chains_json.strip():
                if "tool_chains" not in self.config or not isinstance(self.config.get("tool_chains"), dict):
                    self.config["tool_chains"] = {}
                self.config["tool_chains"]["chains_list"] = chains_json
                logger.info("检测到旧版 Workflow 配置字段，已自动迁移为 tool_chains.chains_list（请在 WebUI 保存一次以固化）")

        chains_config = self.config.get("tool_chains", {})
        if not isinstance(chains_config, dict):
            chains_config = {}

        if not chains_config.get("chains_enabled", True):
            logger.info("工具链功能已禁用")
            return
        
        chains_json = str(chains_config.get("chains_list", "[]") or "")
        if not chains_json or not chains_json.strip():
            return
        
        # 清空现有工具链
        tool_chain_manager.clear()
        tool_chain_registry.clear()
        
        # 加载新配置
        loaded, errors = tool_chain_manager.load_from_json(chains_json)
        
        if errors:
            for err in errors:
                logger.warning(f"工具链配置错误: {err}")
        
        if loaded > 0:
            logger.info(f"已加载 {loaded} 个工具链")
            # 注册工具链到组件系统
            self._register_tool_chains()
            self._update_chains_status_display()
    
    def _register_tool_chains(self) -> None:
        """v1.8.1: 将工具链注册到 MaiBot 组件系统，使 LLM 可调用"""
        from src.plugin_system.core.component_registry import component_registry
        
        chain_count = 0
        for chain_name, chain in tool_chain_manager.get_enabled_chains().items():
            try:
                expected_tool_name = f"chain_{chain.name}".replace("-", "_").replace(".", "_")
                if component_registry.get_component_info(expected_tool_name, ComponentType.TOOL):
                    chain_count += 1
                    logger.debug(f"🔗 工具链已存在，跳过重复注册: {expected_tool_name}")
                    continue

                info, tool_class = tool_chain_registry.register_chain(chain)
                info.plugin_name = self.plugin_name

                if component_registry.register_component(info, tool_class):
                    chain_count += 1
                    logger.info(f"🔗 注册工具链: {tool_class.name}")
                else:
                    logger.warning(f"⚠️ 工具链注册被跳过（可能已存在）: {tool_class.name}")
            except Exception as e:
                logger.error(f"注册工具链 {chain_name} 失败: {e}")
        
        if chain_count > 0:
            logger.info(f"已注册 {chain_count} 个工具链到组件系统")
    
    def _register_tools_to_react(self) -> int:
        """v1.9.0: 将 MCP 工具注册到记忆检索 ReAct 系统（软流程）
        
        这样 MaiBot 的 ReAct Agent 在检索记忆时可以调用 MCP 工具，
        实现 LLM 自主决策的多轮工具调用。
        
        Returns:
            int: 成功注册的工具数量
        """
        try:
            from src.memory_system.retrieval_tools import register_memory_retrieval_tool
        except ImportError:
            logger.warning("无法导入记忆检索工具注册模块，跳过 ReAct 工具注册")
            return 0
        
        react_config = self.config.get("react", {})
        filter_mode = react_config.get("filter_mode", "whitelist")
        tool_filter = react_config.get("tool_filter", "").strip()
        
        # 解析过滤列表（支持 # 注释）
        filter_patterns = []
        for line in tool_filter.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                filter_patterns.append(line)
        
        registered_count = 0
        disabled_tools = self._get_disabled_tools()
        registered_tools = []  # 记录已注册的工具名
        
        for tool_key, (tool_info, _) in mcp_manager.all_tools.items():
            tool_name = tool_key.replace("-", "_").replace(".", "_")
            
            # 跳过禁用的工具
            if tool_name in disabled_tools:
                continue
            
            # 应用过滤器
            if filter_patterns:
                matched = any(
                    fnmatch.fnmatch(tool_name, p) or tool_name == p
                    for p in filter_patterns
                )
                
                if filter_mode == "whitelist":
                    # 白名单模式：只注册匹配的
                    if not matched:
                        continue
                else:
                    # 黑名单模式：排除匹配的
                    if matched:
                        continue
            
            try:
                # 转换参数格式
                parameters = self._convert_mcp_params_to_react_format(tool_info.input_schema)
                
                # 创建异步执行函数（使用闭包捕获 tool_key）
                def make_execute_func(tk: str):
                    async def execute_func(**kwargs) -> str:
                        result = await mcp_manager.call_tool(tk, kwargs)
                        if result.success:
                            return result.content or "(无返回内容)"
                        else:
                            return f"工具调用失败: {result.error}"
                    return execute_func
                
                execute_func = make_execute_func(tool_key)
                
                # 注册到 ReAct 系统
                register_memory_retrieval_tool(
                    name=f"mcp_{tool_name}",
                    description=f"{tool_info.description} [MCP: {tool_info.server_name}]",
                    parameters=parameters,
                    execute_func=execute_func,
                )
                
                registered_count += 1
                registered_tools.append(f"mcp_{tool_name}")
                logger.debug(f"🔄 注册 ReAct 工具: mcp_{tool_name}")
                
            except Exception as e:
                logger.warning(f"注册 ReAct 工具 {tool_name} 失败: {e}")
        
        if registered_count > 0:
            mode_str = "白名单" if filter_mode == "whitelist" else "黑名单"
            logger.info(f"已注册 {registered_count} 个 MCP 工具到 ReAct 系统 (过滤模式: {mode_str})")
        
        # 更新状态显示
        self._update_react_status_display(registered_tools, filter_mode, filter_patterns)
        
        return registered_count
    
    def _update_react_status_display(self, registered_tools: List[str], filter_mode: str, filter_patterns: List[str]) -> None:
        """更新 ReAct 工具状态显示"""
        if not registered_tools:
            status_text = "(未注册任何工具)"
        else:
            mode_str = "白名单" if filter_mode == "whitelist" else "黑名单"
            lines = [f"📊 已注册 {len(registered_tools)} 个工具 (模式: {mode_str})"]
            if filter_patterns:
                lines.append(f"过滤规则: {len(filter_patterns)} 条")
            lines.append("")
            for tool in registered_tools[:20]:
                lines.append(f"  • {tool}")
            if len(registered_tools) > 20:
                lines.append(f"  ... 还有 {len(registered_tools) - 20} 个")
            status_text = "\n".join(lines)
        
        # 更新内存配置
        if "react" not in self.config:
            self.config["react"] = {}
        self.config["react"]["react_status"] = status_text
    
    def _convert_mcp_params_to_react_format(self, input_schema: Dict) -> List[Dict[str, Any]]:
        """将 MCP 工具参数转换为 ReAct 工具参数格式"""
        parameters = []
        
        if not input_schema:
            return parameters
        
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        
        for param_name, param_info in properties.items():
            param_type = param_info.get("type", "string")
            description = param_info.get("description", f"参数 {param_name}")
            is_required = param_name in required
            
            parameters.append({
                "name": param_name,
                "type": param_type,
                "description": description,
                "required": is_required,
            })
        
        return parameters
    
    def _update_chains_status_display(self) -> None:
        """v1.8.0: 更新工具链状态显示"""
        chains = tool_chain_manager.get_all_chains()
        
        if not chains:
            status_text = "(无工具链配置)"
        else:
            lines = [f"📊 已配置 {len(chains)} 个工具链:\n"]
            for name, chain in chains.items():
                status = "✅" if chain.enabled else "❌"
                # 显示工具链基本信息
                lines.append(f"{status} chain_{name}")
                lines.append(f"   描述: {chain.description[:40]}{'...' if len(chain.description) > 40 else ''}")
                
                # 显示输入参数
                if chain.input_params:
                    params = ", ".join(chain.input_params.keys())
                    lines.append(f"   参数: {params}")
                
                # 显示步骤
                lines.append(f"   步骤: {len(chain.steps)} 个")
                for i, step in enumerate(chain.steps):
                    opt = " (可选)" if step.optional else ""
                    out = f" → {step.output_key}" if step.output_key else ""
                    lines.append(f"      {i+1}. {step.tool_name}{out}{opt}")
                lines.append("")
            
            status_text = "\n".join(lines)
        
        # 更新内存配置
        if "tool_chains" not in self.config:
            self.config["tool_chains"] = {}
        self.config["tool_chains"]["chains_status"] = status_text
    
    def _get_disabled_tools(self) -> set:
        """v1.4.0: 获取禁用的工具列表"""
        tools_config = self.config.get("tools", {})
        disabled_str = tools_config.get("disabled_tools", "")
        return {t.strip() for t in disabled_str.strip().split("\n") if t.strip()}
    
    async def _async_connect_servers(self) -> None:
        """异步连接所有配置的 MCP 服务器（v1.5.0: 并行连接优化）"""
        import asyncio
        settings = self.config.get("settings", {})

        servers_config = self._load_mcp_servers_config()
        
        if not servers_config:
            logger.warning("未配置任何 MCP 服务器")
            self._initialized = True
            self._update_status_display()
            self._update_tool_list_display()
            self._update_chains_status_display()
            self._start_status_refresher()
            self._persist_runtime_displays()
            return
        
        auto_connect = settings.get("auto_connect", True)
        if not auto_connect:
            logger.info("auto_connect 已禁用，跳过自动连接")
            self._initialized = True
            self._update_status_display()
            self._update_tool_list_display()
            self._update_chains_status_display()
            self._start_status_refresher()
            self._persist_runtime_displays()
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
            self._update_status_display()
            self._update_tool_list_display()
            self._update_chains_status_display()
            self._start_status_refresher()
            self._persist_runtime_displays()
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
        results = await asyncio.gather(
            *[connect_single_server(cfg) for cfg in enabled_configs],
            return_exceptions=True
        )
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
            
            info, tool_class = mcp_tool_registry.register_tool(
                tool_key, tool_info, tool_prefix, disabled=is_disabled
            )
            info.plugin_name = self.plugin_name
            
            if component_registry.register_component(info, tool_class):
                registered_count += 1
                status = "🚫" if is_disabled else "✅"
                logger.info(f"{status} 注册 MCP 工具: {tool_class.name}")
            else:
                logger.warning(f"❌ 注册 MCP 工具失败: {tool_class.name}")
        
        chains_config = self.config.get("tool_chains", {})
        chains_enabled = bool(chains_config.get("chains_enabled", True)) if isinstance(chains_config, dict) else True
        chain_count = len(tool_chain_manager.get_enabled_chains()) if chains_enabled else 0
        
        # v1.9.0: 注册 MCP 工具到记忆检索 ReAct 系统（软流程）
        react_count = 0
        react_config = self.config.get("react", {})
        if react_config.get("react_enabled", False):
            react_count = self._register_tools_to_react()
        
        self._initialized = True
        logger.info(f"MCP 桥接插件初始化完成，已注册 {registered_count} 个工具，{chain_count} 个工具链，{react_count} 个 ReAct 工具")
        
        # 更新状态显示
        self._update_status_display()
        self._update_tool_list_display()
        self._update_chains_status_display()
        self._start_status_refresher()
        self._persist_runtime_displays()
    
    def _start_status_refresher(self) -> None:
        """启动 WebUI 状态刷新任务（不写入磁盘）"""
        task = getattr(self, "_status_refresh_task", None)
        if task and not task.done():
            return

        self._status_refresh_running = True
        self._status_refresh_task = asyncio.create_task(self._status_refresh_loop())

    async def _stop_status_refresher(self) -> None:
        """停止 WebUI 状态刷新任务"""
        self._status_refresh_running = False
        task = getattr(self, "_status_refresh_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._status_refresh_task = None

    async def _status_refresh_loop(self) -> None:
        """定期刷新 WebUI 展示字段（状态/工具列表/工具链状态）"""
        while getattr(self, "_status_refresh_running", False):
            try:
                settings = self.config.get("settings", {})
                enabled = bool(settings.get("status_refresh_enabled", True))
                interval = float(settings.get("status_refresh_interval", 10.0) or 10.0)
                interval = max(5.0, min(interval, 60.0))

                if enabled and self._initialized:
                    self._update_status_display()
                    self._update_tool_list_display()
                    self._update_chains_status_display()
                    self._persist_runtime_displays()

                await asyncio.sleep(interval if enabled else 5.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"状态刷新任务异常: {e}")
                await asyncio.sleep(5.0)

    def _load_mcp_servers_config(self) -> List[Dict[str, Any]]:
        """v2.0: 从 Claude mcpServers JSON 加载服务器配置。

        - 唯一主入口：config.servers.claude_config_json
        - 兼容：若旧版 servers.list 存在且 claude_config_json 为空，会自动迁移并写回内存配置
        """
        servers_section = self.config.get("servers", {})
        if not isinstance(servers_section, dict):
            servers_section = {}

        claude_json = str(servers_section.get("claude_config_json", "") or "")

        if not claude_json.strip():
            legacy_list = str(servers_section.get("list", "") or "")
            migrated = legacy_servers_list_to_claude_config(legacy_list)
            if migrated:
                claude_json = migrated
                if "servers" not in self.config:
                    self.config["servers"] = {}
                self.config["servers"]["claude_config_json"] = migrated
                logger.info("检测到旧版 servers.list，已自动迁移为 Claude mcpServers（请在 WebUI 保存一次以固化）")

        if not claude_json.strip():
            self._last_servers_config_error = "未配置任何 MCP 服务器（请在 WebUI 的「MCP Servers（Claude）」粘贴 mcpServers JSON）"
            return []

        try:
            servers = parse_claude_mcp_config(claude_json)
        except ClaudeConfigError as e:
            self._last_servers_config_error = str(e)
            logger.error(f"Claude mcpServers 配置解析失败: {e}")
            return []
        except Exception as e:
            self._last_servers_config_error = str(e)
            logger.error(f"Claude mcpServers 配置解析异常: {e}")
            return []

        self._last_servers_config_error = ""

        # 保留未知字段（如 post_process）供旧功能使用
        raw_mapping: Dict[str, Any] = {}
        try:
            parsed = json.loads(claude_json)
            mapping = parsed.get("mcpServers", parsed)
            if isinstance(mapping, dict):
                raw_mapping = mapping
        except Exception:
            raw_mapping = {}

        configs: List[Dict[str, Any]] = []
        for srv in servers:
            raw = raw_mapping.get(srv.name, {})
            cfg: Dict[str, Any] = raw.copy() if isinstance(raw, dict) else {}
            cfg.update(
                {
                    "name": srv.name,
                    "enabled": srv.enabled,
                    "transport": srv.transport,
                    "command": srv.command,
                    "args": srv.args,
                    "env": srv.env,
                    "url": srv.url,
                    "headers": srv.headers,
                }
            )
            configs.append(cfg)

        return configs
    
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
    
    def _update_status_display(self) -> None:
        """更新配置文件中的状态显示字段"""
        status = mcp_manager.get_status()
        settings = self.config.get("settings", {})
        lines = []

        cfg_err = str(getattr(self, "_last_servers_config_error", "") or "").strip()
        if cfg_err:
            lines.append(f"⚠️ 配置: {cfg_err}")
            lines.append("")
        
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
