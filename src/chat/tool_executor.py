"""
工具执行器

独立的工具执行组件，可以直接输入聊天消息内容，
自动判断并执行相应的工具，返回结构化的工具执行结果。

从 src.plugin_system.core.tool_use 迁移，使用新的核心组件注册表。
"""

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.core.announcement_manager import global_announcement_manager
from src.core.component_registry import component_registry
from src.llm_models.payload_content import ToolCall
from src.llm_models.utils_model import LLMRequest
from src.prompt.prompt_manager import prompt_manager

logger = get_logger("tool_use")


class ToolExecutor:
    """独立的工具执行器组件

    可以直接输入聊天消息内容，自动判断并执行相应的工具，返回结构化的工具执行结果。
    """

    def __init__(self, chat_id: str, enable_cache: bool = True, cache_ttl: int = 3):
        from src.chat.message_receive.chat_manager import chat_manager as _chat_manager

        self.chat_id = chat_id
        self.chat_stream = _chat_manager.get_session_by_session_id(self.chat_id)
        self.log_prefix = f"[{_chat_manager.get_session_name(self.chat_id) or self.chat_id}]"

        self.llm_model = LLMRequest(model_set=model_config.model_task_config.tool_use, request_type="tool_executor")

        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl
        self.tool_cache: Dict[str, dict] = {}

        logger.info(f"{self.log_prefix}工具执行器初始化完成，缓存{'启用' if enable_cache else '禁用'}，TTL={cache_ttl}")

    async def execute_from_chat_message(
        self, target_message: str, chat_history: str, sender: str, return_details: bool = False
    ) -> Tuple[List[Dict[str, Any]], List[str], str]:
        """从聊天消息执行工具"""

        cache_key = self._generate_cache_key(target_message, chat_history, sender)
        if cached_result := self._get_from_cache(cache_key):
            logger.info(f"{self.log_prefix}使用缓存结果，跳过工具执行")
            if not return_details:
                return cached_result, [], ""
            used_tools = [result.get("tool_name", "unknown") for result in cached_result]
            return cached_result, used_tools, ""

        tools = self._get_tool_definitions()
        if not tools:
            logger.debug(f"{self.log_prefix}没有可用工具，直接返回空内容")
            return [], [], ""

        prompt_template = prompt_manager.get_prompt("tool_executor")
        prompt_template.add_context("target_message", target_message)
        prompt_template.add_context("chat_history", chat_history)
        prompt_template.add_context("sender", sender)
        prompt_template.add_context("bot_name", global_config.bot.nickname)
        prompt_template.add_context("time_now", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
        prompt = await prompt_manager.render_prompt(prompt_template)

        logger.debug(f"{self.log_prefix}开始LLM工具调用分析")

        response, (reasoning_content, model_name, tool_calls) = await self.llm_model.generate_response_async(
            prompt=prompt, tools=tools, raise_when_empty=False
        )

        tool_results, used_tools = await self.execute_tool_calls(tool_calls)

        if tool_results:
            self._set_cache(cache_key, tool_results)

        if used_tools:
            logger.info(f"{self.log_prefix}工具执行完成，共执行{len(used_tools)}个工具: {used_tools}")

        if return_details:
            return tool_results, used_tools, prompt
        return tool_results, [], ""

    def _get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取 LLM 可用的工具定义列表"""
        all_tools = component_registry.get_llm_available_tools()
        user_disabled_tools = global_announcement_manager.get_disabled_chat_tools(self.chat_id)
        return [info.get_llm_definition() for name, info in all_tools.items() if name not in user_disabled_tools]

    async def execute_tool_calls(self, tool_calls: Optional[List[ToolCall]]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """执行工具调用列表"""
        tool_results: List[Dict[str, Any]] = []
        used_tools: List[str] = []

        if not tool_calls:
            logger.debug(f"{self.log_prefix}无需执行工具")
            return [], []

        func_names = [call.func_name for call in tool_calls if call.func_name]
        logger.info(f"{self.log_prefix}开始执行工具调用: {func_names}")

        for tool_call in tool_calls:
            tool_name = tool_call.func_name
            try:
                logger.debug(f"{self.log_prefix}执行工具: {tool_name}")
                result = await self.execute_tool_call(tool_call)

                if result:
                    tool_info = {
                        "type": result.get("type", "unknown_type"),
                        "id": result.get("id", f"tool_exec_{time.time()}"),
                        "content": result.get("content", ""),
                        "tool_name": tool_name,
                        "timestamp": time.time(),
                    }
                    content = tool_info["content"]
                    if not isinstance(content, (str, list, tuple)):
                        tool_info["content"] = str(content)
                    content_check = tool_info["content"]
                    if (isinstance(content_check, str) and not content_check.strip()) or (
                        isinstance(content_check, (list, tuple)) and len(content_check) == 0
                    ):
                        logger.debug(f"{self.log_prefix}工具{tool_name}无有效内容，跳过展示")
                        continue

                    tool_results.append(tool_info)
                    used_tools.append(tool_name)
                    preview = str(content)[:200]
                    logger.debug(f"{self.log_prefix}工具{tool_name}结果内容: {preview}...")
            except Exception as e:
                logger.error(f"{self.log_prefix}工具{tool_name}执行失败: {e}")
                error_info = {
                    "type": "tool_error",
                    "id": f"tool_error_{time.time()}",
                    "content": f"工具{tool_name}执行失败: {str(e)}",
                    "tool_name": tool_name,
                    "timestamp": time.time(),
                }
                tool_results.append(error_info)

        return tool_results, used_tools

    async def execute_tool_call(self, tool_call: ToolCall) -> Optional[Dict[str, Any]]:
        """执行单个工具调用"""
        function_name = tool_call.func_name
        function_args = tool_call.args or {}
        function_args["llm_called"] = True

        executor = component_registry.get_tool_executor(function_name)
        if not executor:
            logger.warning(f"未知工具名称: {function_name}")
            return None

        result = await executor(function_args)
        if result:
            return {
                "tool_call_id": tool_call.call_id,
                "role": "tool",
                "name": function_name,
                "type": "function",
                "content": result["content"],
            }
        return None

    async def execute_specific_tool_simple(self, tool_name: str, tool_args: Dict) -> Optional[Dict]:
        """直接执行指定工具"""
        try:
            tool_call = ToolCall(
                call_id=f"direct_tool_{time.time()}",
                func_name=tool_name,
                args=tool_args,
            )

            logger.info(f"{self.log_prefix}直接执行工具: {tool_name}")
            result = await self.execute_tool_call(tool_call)

            if result:
                tool_info = {
                    "type": result.get("type", "unknown_type"),
                    "id": result.get("id", f"direct_tool_{time.time()}"),
                    "content": result.get("content", ""),
                    "tool_name": tool_name,
                    "timestamp": time.time(),
                }
                logger.info(f"{self.log_prefix}直接工具执行成功: {tool_name}")
                return tool_info

        except Exception as e:
            logger.error(f"{self.log_prefix}直接工具执行失败 {tool_name}: {e}")

        return None

    # === 缓存方法 ===

    def _generate_cache_key(self, target_message: str, chat_history: str, sender: str) -> str:
        content = f"{target_message}_{chat_history}_{sender}"
        return hashlib.md5(content.encode()).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[List[Dict]]:
        if not self.enable_cache or cache_key not in self.tool_cache:
            return None
        cache_item = self.tool_cache[cache_key]
        if cache_item["ttl"] <= 0:
            del self.tool_cache[cache_key]
            return None
        cache_item["ttl"] -= 1
        return cache_item["result"]

    def _set_cache(self, cache_key: str, result: List[Dict]):
        if not self.enable_cache:
            return
        self.tool_cache[cache_key] = {"result": result, "ttl": self.cache_ttl, "timestamp": time.time()}

    def _cleanup_expired_cache(self):
        if not self.enable_cache:
            return
        expired = [k for k, v in self.tool_cache.items() if v["ttl"] <= 0]
        for key in expired:
            del self.tool_cache[key]

    def clear_cache(self):
        if self.enable_cache:
            self.tool_cache.clear()

    def get_cache_status(self) -> Dict:
        if not self.enable_cache:
            return {"enabled": False, "cache_count": 0}
        self._cleanup_expired_cache()
        ttl_distribution: Dict[int, int] = {}
        for item in self.tool_cache.values():
            ttl = item["ttl"]
            ttl_distribution[ttl] = ttl_distribution.get(ttl, 0) + 1
        return {
            "enabled": True,
            "cache_count": len(self.tool_cache),
            "cache_ttl": self.cache_ttl,
            "ttl_distribution": ttl_distribution,
        }

    def set_cache_config(self, enable_cache: Optional[bool] = None, cache_ttl: int = -1):
        if enable_cache is not None:
            self.enable_cache = enable_cache
        if cache_ttl > 0:
            self.cache_ttl = cache_ttl
