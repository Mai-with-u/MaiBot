"""Maisaka 对话循环服务。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence

import asyncio
import time

from rich.console import RenderableType

from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.common.i18n import get_locale
from src.common.logger import get_logger
from src.common.prompt_i18n import load_prompt
from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import global_config
from src.core.tooling import ToolAvailabilityContext, ToolRegistry
from src.llm_models.model_client.base_client import BaseClient
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput, ToolOption, normalize_tool_options
from src.plugin_runtime.hook_payloads import (
    deserialize_prompt_messages,
    deserialize_tool_calls,
    serialize_prompt_messages,
    serialize_tool_calls,
    serialize_tool_definitions,
)
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.services.llm_service import LLMServiceClient

from src.maisaka.builtin_tool import get_builtin_tools
from src.maisaka.context.history import normalize_tool_call_result_pairs
from src.maisaka.context.messages import (
    AssistantMessage,
    LLMContextMessage,
    ReferenceMessage,
    ReferenceMessageType,
    SessionBackedMessage,
    ToolResultMessage,
    build_llm_message_from_context,
)
from src.maisaka.display.prompt_cli_renderer import PromptCLIVisualizer
from src.maisaka.memory.mid_term import is_mid_term_memory_message
from src.maisaka.focus import focus_mode_manager
from src.maisaka.visual.message_limiter import limit_latest_images_in_messages
from src.maisaka.visual.mode_utils import resolve_enable_visual_planner

PLANNER_TOOL_HINT_SOURCE = "planner_tool_hint"
REQUEST_TYPE_BY_REQUEST_KIND = {
    "behavior_scenario_analyzer": "behavior.scenario_analyzer",
    "emotion": "emoji.selector",
    "expression_selector": "expression.selector",
    "planner": "maisaka.planner",
    "reply_effect_judge": "reply.effect_judge",
    "sub_agent": "maisaka.sub_agent",
}
MODEL_TASK_NAME_BY_REQUEST_KIND: dict[str, str] = {
    "expression_selector": "expression_use",
}
PROMPT_PREVIEW_CATEGORY_BY_REQUEST_KIND = {
    "planner": "planner",
    "reply_effect_judge": "reply_effect_judge",
    "expression_selector": "expression_selector",
    "behavior_scenario_analyzer": "behavior_scenario_analyzer",
    "emotion": "emotion",
    "sub_agent": "sub_agent",
}
CONTEXT_SELECTION_CACHE_STABILITY_RATIO = 2.0
PLANNER_FINAL_ASSISTANT_REMINDER_TEMPLATE = (
    "我需要输出对{bot_name}发言的分析，视情况输出文本内容的分析，思考是否进行工具调用"
)


@dataclass(slots=True)
class ChatResponse:
    """LLM 对话循环单步响应。"""

    content: Optional[str]
    tool_calls: List[ToolCall]
    request_messages: List[Message]
    raw_message: AssistantMessage
    selected_history_count: int
    tool_count: int
    prompt_tokens: int
    built_message_count: int
    completion_tokens: int
    total_tokens: int
    model_name: str = ""
    duration_ms: float = 0.0
    prompt_section: Optional[RenderableType] = None
    prompt_html_uri: Optional[str] = None
    reasoning: str = ""


logger = get_logger("maisaka_chat_loop")


def register_maisaka_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """注册 Maisaka 规划器与 replyer 内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 实际注册的 Hook 规格列表。
    """

    return registry.register_hook_specs(
        [
            HookSpec(
                name="maisaka.planner.before_request",
                description="在 Maisaka 向模型发起规划请求前触发，可改写消息窗口与工具定义。",
                parameters_schema=build_object_schema(
                    {
                        "messages": {
                            "type": "array",
                            "description": "即将发给模型的 PromptMessage 列表。",
                        },
                        "tool_definitions": {
                            "type": "array",
                            "description": "当前候选工具定义列表。",
                        },
                        "selected_history_count": {
                            "type": "integer",
                            "description": "当前选中的上下文消息数量。",
                        },
                        "built_message_count": {
                            "type": "integer",
                            "description": "实际发送给模型的消息数量。",
                        },
                        "selection_reason": {
                            "type": "string",
                            "description": "上下文选择说明。",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "当前会话 ID。",
                        },
                    },
                    required=[
                        "messages",
                        "tool_definitions",
                        "selected_history_count",
                        "built_message_count",
                        "selection_reason",
                        "session_id",
                    ],
                ),
                default_timeout_ms=6000,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="maisaka.planner.after_response",
                description="在 Maisaka 收到模型响应后触发，可调整文本结果与工具调用列表。",
                parameters_schema=build_object_schema(
                    {
                        "response": {
                            "type": "string",
                            "description": "模型返回的文本内容。",
                        },
                        "tool_calls": {
                            "type": "array",
                            "description": "模型返回的工具调用列表。",
                        },
                        "selected_history_count": {
                            "type": "integer",
                            "description": "当前选中的上下文消息数量。",
                        },
                        "built_message_count": {
                            "type": "integer",
                            "description": "实际发送给模型的消息数量。",
                        },
                        "selection_reason": {
                            "type": "string",
                            "description": "上下文选择说明。",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "当前会话 ID。",
                        },
                        "prompt_tokens": {
                            "type": "integer",
                            "description": "输入 Token 数。",
                        },
                        "completion_tokens": {
                            "type": "integer",
                            "description": "输出 Token 数。",
                        },
                        "total_tokens": {
                            "type": "integer",
                            "description": "总 Token 数。",
                        },
                    },
                    required=[
                        "response",
                        "tool_calls",
                        "selected_history_count",
                        "built_message_count",
                        "selection_reason",
                        "session_id",
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                    ],
                ),
                default_timeout_ms=6000,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="maisaka.replyer.before_request",
                description="在 Maisaka replyer 向模型发起请求前触发，可读取或改写本次 reply 工具透传参数。",
                parameters_schema=build_object_schema(
                    {
                        "session_id": {
                            "type": "string",
                            "description": "当前会话 ID。",
                        },
                        "request_type": {
                            "type": "string",
                            "description": "当前 replyer 请求类型。",
                        },
                        "task_name": {
                            "type": "string",
                            "description": "本次 replyer 请求使用的模型任务名；Hook 可改写该值。",
                        },
                        "model_name": {
                            "type": "string",
                            "description": "本次 replyer 请求指定使用的具体模型名；留空时按任务策略选择。",
                        },
                        "extra_prompt": {
                            "type": "string",
                            "description": "Hook 可追加到本次 replyer 提示词中的额外回复要求。",
                        },
                        "attempt": {
                            "type": "integer",
                            "description": "当前生成尝试序号，从 1 开始。",
                        },
                        "retry_count": {
                            "type": "integer",
                            "description": "当前已经重新生成的次数。",
                        },
                        "max_retries": {
                            "type": "integer",
                            "description": "本轮 replyer 最多允许重新生成多少次。",
                        },
                        "reply_message_id": {
                            "type": "string",
                            "description": "被回复消息 ID；无目标消息时为空字符串。",
                        },
                        "reply_reason": {
                            "type": "string",
                            "description": "本次 replyer 生成的回复理由。",
                        },
                        "selected_expression_ids": {
                            "type": "array",
                            "description": "本次 replyer 选中的表达方式编号列表。",
                        },
                        "reply_tool_args": {
                            "type": "object",
                            "description": "reply 工具里除 msg_id、set_quote 外透传给 replyer 的额外参数。",
                        },
                    },
                    required=[
                        "session_id",
                        "request_type",
                        "task_name",
                        "model_name",
                        "extra_prompt",
                        "attempt",
                        "retry_count",
                        "max_retries",
                        "reply_message_id",
                        "reply_reason",
                        "selected_expression_ids",
                        "reply_tool_args",
                    ],
                ),
                default_timeout_ms=6000,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="maisaka.replyer.before_model_request",
                description="在 Maisaka replyer 构造完本次模型请求消息后触发，可改写实际发送给模型的 messages。",
                parameters_schema=build_object_schema(
                    {
                        "messages": {
                            "type": "array",
                            "description": "即将发给模型的 PromptMessage 列表。",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "当前会话 ID。",
                        },
                        "request_type": {
                            "type": "string",
                            "description": "当前 replyer 请求类型。",
                        },
                        "task_name": {
                            "type": "string",
                            "description": "本次 replyer 实际使用的模型任务名。",
                        },
                        "requested_model_name": {
                            "type": "string",
                            "description": "before_request Hook 请求指定的具体模型名；留空表示按任务策略选择。",
                        },
                        "selected_model_name": {
                            "type": "string",
                            "description": "当前尝试实际选中的模型名；未进入具体模型尝试时为空字符串。",
                        },
                        "selected_model_visual": {
                            "type": "boolean",
                            "description": "当前尝试选中的模型是否启用 visual 能力。",
                        },
                        "attempt": {
                            "type": "integer",
                            "description": "当前生成尝试序号，从 1 开始。",
                        },
                        "retry_count": {
                            "type": "integer",
                            "description": "当前已经重新生成的次数。",
                        },
                        "max_retries": {
                            "type": "integer",
                            "description": "本轮 replyer 最多允许重新生成多少次。",
                        },
                        "reply_message_id": {
                            "type": "string",
                            "description": "被回复消息 ID；无目标消息时为空字符串。",
                        },
                        "reply_reason": {
                            "type": "string",
                            "description": "本次 replyer 生成的回复理由。",
                        },
                        "selected_expression_ids": {
                            "type": "array",
                            "description": "本次 replyer 选中的表达方式编号列表。",
                        },
                        "reply_tool_args": {
                            "type": "object",
                            "description": "reply 工具里除 msg_id、set_quote 外透传给 replyer 的额外参数。",
                        },
                    },
                    required=[
                        "messages",
                        "session_id",
                        "request_type",
                        "task_name",
                        "requested_model_name",
                        "selected_model_name",
                        "selected_model_visual",
                        "attempt",
                        "retry_count",
                        "max_retries",
                        "reply_message_id",
                        "reply_reason",
                        "selected_expression_ids",
                        "reply_tool_args",
                    ],
                ),
                default_timeout_ms=6000,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="maisaka.replyer.after_response",
                description="在 Maisaka replyer 收到模型响应后触发，可要求重新生成或改写回复文本。",
                parameters_schema=build_object_schema(
                    {
                        "response": {
                            "type": "string",
                            "description": "replyer 模型返回的文本内容。",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "当前会话 ID。",
                        },
                        "request_type": {
                            "type": "string",
                            "description": "当前 replyer 请求类型。",
                        },
                        "task_name": {
                            "type": "string",
                            "description": "本次 replyer 实际使用的模型任务名。",
                        },
                        "requested_model_name": {
                            "type": "string",
                            "description": "Hook 请求指定的具体模型名；留空表示按任务策略选择。",
                        },
                        "attempt": {
                            "type": "integer",
                            "description": "当前生成尝试序号，从 1 开始。",
                        },
                        "retry_count": {
                            "type": "integer",
                            "description": "当前已经重新生成的次数。",
                        },
                        "max_retries": {
                            "type": "integer",
                            "description": "本轮 replyer 最多允许重新生成多少次。",
                        },
                        "reply_message_id": {
                            "type": "string",
                            "description": "被回复消息 ID；无目标消息时为空字符串。",
                        },
                        "selected_expression_ids": {
                            "type": "array",
                            "description": "本次 replyer 选中的表达方式编号列表。",
                        },
                        "reply_tool_args": {
                            "type": "object",
                            "description": "reply 工具里除 msg_id、set_quote 外透传给 replyer 的额外参数。",
                        },
                        "prompt_tokens": {
                            "type": "integer",
                            "description": "输入 Token 数。",
                        },
                        "completion_tokens": {
                            "type": "integer",
                            "description": "输出 Token 数。",
                        },
                        "total_tokens": {
                            "type": "integer",
                            "description": "总 Token 数。",
                        },
                        "retry": {
                            "type": "boolean",
                            "description": "Hook 处理器可置为 true，要求 replyer 重新生成。",
                        },
                        "retry_reason": {
                            "type": "string",
                            "description": "可选的重新生成约束原因；留空时只重新生成，不追加下一轮 replyer 提示词。",
                        },
                        "matched_regex": {
                            "type": "string",
                            "description": "触发重新生成的正则或规则名称。",
                        },
                        "matched_regex_pattern": {
                            "type": "string",
                            "description": "触发重新生成的正则文本。",
                        },
                        "matched_regex_description": {
                            "type": "string",
                            "description": "触发重新生成的规则说明。",
                        },
                    },
                    required=[
                        "response",
                        "session_id",
                        "request_type",
                        "task_name",
                        "requested_model_name",
                        "attempt",
                        "retry_count",
                        "max_retries",
                        "reply_message_id",
                        "selected_expression_ids",
                        "reply_tool_args",
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                    ],
                ),
                default_timeout_ms=6000,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="maisaka.reply.before_post_process",
                description="在 Maisaka 对最终可见回复执行文本后处理前触发，可按本次回复调整后处理策略。",
                parameters_schema=build_object_schema(
                    {
                        "response": {
                            "type": "string",
                            "description": "即将执行文本后处理的最终回复正文。",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "当前会话 ID。",
                        },
                        "reply_message_id": {
                            "type": "string",
                            "description": "被回复消息 ID。",
                        },
                        "reply_tool_args": {
                            "type": "object",
                            "description": "本次 reply 工具除内部参数外的透传参数。",
                        },
                        "skip_post_process": {
                            "type": "boolean",
                            "description": "是否跳过本次回复的全部文本后处理。",
                        },
                        "enable_splitter": {
                            "type": "boolean",
                            "description": "本次回复是否允许按全局配置进行文本拆分。",
                        },
                        "enable_chinese_typo": {
                            "type": "boolean",
                            "description": "本次回复是否允许按全局配置注入中文错别字。",
                        },
                    },
                    required=[
                        "response",
                        "session_id",
                        "reply_message_id",
                        "reply_tool_args",
                        "skip_post_process",
                        "enable_splitter",
                        "enable_chinese_typo",
                    ],
                ),
                default_timeout_ms=6000,
                allow_abort=False,
                allow_kwargs_mutation=True,
            ),
        ]
    )


class MaisakaChatLoopService:
    """负责 Maisaka 主对话循环、系统提示词和终端渲染。"""

    def __init__(
        self,
        chat_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        is_group_chat: Optional[bool] = None,
        model_task_name: str = "planner",
    ) -> None:
        """初始化 Maisaka 对话循环服务。

        Args:
            chat_system_prompt: 可选的系统提示词。
            session_id: 当前会话 ID，用于匹配会话级额外提示。
            is_group_chat: 当前会话是否为群聊。
        """
        self._model_task_name = model_task_name.strip() or "planner"
        self._is_group_chat = is_group_chat
        self._session_id = session_id or ""
        self._extra_tools: List[ToolOption] = []
        self._interrupt_flag: asyncio.Event | None = None
        self._tool_registry: ToolRegistry | None = None
        self._custom_chat_system_prompt = chat_system_prompt
        self._prompt_load_lock = asyncio.Lock()
        self._llm_chat_clients: dict[str, LLMServiceClient] = {}

    @property
    def behavior_style_prompt(self) -> str:
        """返回 Planner 使用的行为风格提示词。"""

        return global_config.personality.behavior_style.strip()

    @staticmethod
    def _resolve_llm_request_type(request_kind: str) -> str:
        """根据 Maisaka 请求类型解析 LLM 统计口径。"""

        normalized_request_kind = str(request_kind or "").strip()
        if not normalized_request_kind:
            normalized_request_kind = "planner"
        request_type = REQUEST_TYPE_BY_REQUEST_KIND.get(normalized_request_kind)
        if request_type is None:
            raise ValueError(f"未注册的 Maisaka LLM request_kind: {normalized_request_kind}")
        return request_type

    @staticmethod
    def _resolve_prompt_preview_category(request_kind: str) -> str:
        """根据请求类型决定 Prompt 预览落盘目录，避免子代理混入 planner。"""

        normalized_request_kind = str(request_kind or "").strip().lower()
        if not normalized_request_kind:
            return "planner"
        return PROMPT_PREVIEW_CATEGORY_BY_REQUEST_KIND.get(normalized_request_kind, normalized_request_kind)

    def _resolve_model_task_name(self, request_kind: str) -> str:
        """根据请求类型解析模型任务配置名。"""

        normalized_request_kind = str(request_kind or "").strip().lower()
        return MODEL_TASK_NAME_BY_REQUEST_KIND.get(normalized_request_kind, self._model_task_name)

    def _get_llm_chat_client(self, request_kind: str) -> LLMServiceClient:
        """获取当前请求类型对应的 LLM 客户端。"""

        request_type = self._resolve_llm_request_type(request_kind)
        model_task_name = self._resolve_model_task_name(request_kind)
        client_key = f"{model_task_name}:{request_type}"
        llm_client = self._llm_chat_clients.get(client_key)
        if llm_client is None:
            llm_client = LLMServiceClient(
                task_name=model_task_name,
                request_type=request_type,
                session_id=self._session_id,
            )
            self._llm_chat_clients[client_key] = llm_client
        return llm_client

    @staticmethod
    def _resolve_planner_response_content(response: str, reasoning: str) -> str:
        """在模型只把思考放入原生 reasoning 字段时，仍保留可传给工具的 planner 文本。"""

        normalized_response = str(response or "").strip()
        if normalized_response:
            return response
        return str(reasoning or "").strip()

    @staticmethod
    def _get_runtime_manager() -> Any:
        """获取插件运行时管理器。

        Returns:
            Any: 插件运行时管理器单例。
        """

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        """将任意值安全转换为整数。

        Args:
            value: 待转换的输入值。
            default: 转换失败时的默认值。

        Returns:
            int: 转换后的整数结果。
        """

        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _log_prompt_cache_usage(
        *,
        request_kind: str,
        prompt_tokens: int,
        prompt_cache_hit_tokens: int,
        prompt_cache_miss_tokens: int,
    ) -> None:
        """记录模型 KV cache 命中情况。"""

        if prompt_cache_miss_tokens == 0 and prompt_cache_hit_tokens > 0:
            prompt_cache_miss_tokens = max(prompt_tokens - prompt_cache_hit_tokens, 0)
        prompt_cache_total_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens
        prompt_cache_hit_rate = (
            prompt_cache_hit_tokens / prompt_cache_total_tokens * 100
            if prompt_cache_total_tokens > 0
            else 0
        )
        logger.info(
            "Planner缓存："
            f"{request_kind}, "
            f"命中={prompt_cache_hit_tokens}, "
            f"miss_tokens={prompt_cache_miss_tokens}, "
            f"hit_rate={prompt_cache_hit_rate:.2f}%, "
            f"prompt_tokens={prompt_tokens}"
        )

    async def ensure_chat_prompt_loaded(self, tools_section: str = "") -> None:
        """确保主聊天提示词已经加载完成。

        Args:
            tools_section: 额外注入到提示词中的工具说明片段。
        """
        async with self._prompt_load_lock:
            self._build_chat_system_prompt(tools_section)

    def _build_chat_system_prompt(self, tools_section: str = "") -> str:
        """基于当前配置实时构造主聊天系统提示词。"""

        return load_prompt(self._get_chat_prompt_name(), **self.build_prompt_template_context(tools_section))

    @staticmethod
    def _build_planner_final_assistant_reminder() -> str:
        """构造每轮 Planner 请求末尾的一次性 assistant 提醒。"""

        return PLANNER_FINAL_ASSISTANT_REMINDER_TEMPLATE.format(bot_name=global_config.bot.nickname.strip())

    def _get_chat_prompt_name(self) -> str:
        """选择当前聊天使用的 Planner 模板。"""

        if focus_mode_manager.is_enabled_for_session(self._session_id, is_group_chat=self._is_group_chat):
            return "maisaka_chat_focus"
        return "maisaka_chat"

    def build_prompt_template_context(self, tools_section: str = "") -> dict[str, str]:
        """构造 Maisaka prompt 模板的公共渲染参数。"""

        return {
            "bot_name": global_config.bot.nickname,
            "behavior_style": self.behavior_style_prompt,
            "file_tools_section": tools_section,
            "group_chat_attention_block": self._build_group_chat_attention_block(),
            "planner_idle_focus_rule": self._build_planner_idle_focus_rule(),
            "query_memory_rule": self._build_query_memory_rule(),
        }


    @staticmethod
    def _build_time_user_message(timestamp: datetime) -> str:
        """构建统一格式的时间提示消息。"""

        return f"时间：{timestamp.strftime('%Y-%m-%d %H:%M:%S')}"

    @staticmethod
    def _build_current_time_user_message() -> str:
        """构建追加到请求末尾的当前时间消息。"""

        return MaisakaChatLoopService._build_time_user_message(datetime.now())

    @staticmethod
    def _append_time_user_message(messages: List[Message], timestamp: datetime) -> None:
        """向请求消息列表追加一条时间提示。"""

        messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content(MaisakaChatLoopService._build_time_user_message(timestamp))
            .build()
        )

    def _build_group_chat_attention_block(self) -> str:
        """构建当前聊天场景下的额外注意事项块。"""

        prompt_lines: List[str] = []

        if self._is_group_chat is True:
            if group_chat_prompt := str(global_config.chat.reply_style.group_chat_prompt or "").strip():
                prompt_lines.append(f"通用注意事项：\n{group_chat_prompt}")
        elif self._is_group_chat is False:
            if private_chat_prompt := str(global_config.chat.reply_style.private_chat_prompts or "").strip():
                prompt_lines.append(f"通用注意事项：\n{private_chat_prompt}")

        if not prompt_lines:
            return ""

        return "在该聊天中的注意事项：\n" + "\n\n".join(prompt_lines) + "\n"

    @staticmethod
    def _localized_text(texts: dict[str, str]) -> str:
        """按当前语言读取文案，默认中文。"""

        return texts.get(get_locale(), texts["zh-CN"])

    def _build_planner_idle_focus_rule(self) -> str:
        """构造 Focus 模式下空闲等待动作提示。"""

        return self._localized_text({
            "en-US": "If the current chat has nothing worth acting on, prefer using `switch_chat` to check another chat. Use `wait` only when you need to wait before judging again; otherwise end this thought without calling a tool.",
            "ja-JP": "現在チャットに行動すべき内容がない場合は、`switch_chat` で別チャットを確認することを優先してください。待ってから再判断すべき場合だけ `wait` を使い、それ以外はツールを呼ばずにこの思考を終了してください。",
            "zh-CN": "如果当前聊天没有值得行动的内容，应优先考虑使用 `switch_chat` 去其他聊天看看；只有需要等待后重新判断时才使用 `wait`，否则不调用工具结束这轮思考。",
        })

    def _build_query_memory_rule(self) -> str:
        """按当前聊天类型构造记忆检索提示。"""

        if self._is_group_chat:
            return self._localized_text({
                "en-US": "- query_memory(): Use it only when the reply clearly depends on past group conversation, shared experiences, public agreements, task progress, or recent clues. Do not retrieve memory for greetings, immediate emotional responses, light banter, or content that can be answered from recent messages alone. Do not bring private-chat or personal-privacy memories into a group chat.",
                "ja-JP": "- query_memory()：返信がグループ内の過去会話、共有した経験、公開された約束、タスクの進捗、最近の手がかりに明確に依存する場合だけ使ってください。挨拶、その場の感情への反応、軽いやり取り、最近のメッセージだけで答えられる内容では検索しないでください。個人チャットや私的な記憶をグループチャットに持ち込まないでください。",
                "zh-CN": "- query_memory()：只有回复明显依赖群内过去对话、共同经历、公开约定、任务进展或近期线索时使用；不要为了寒暄、即时情绪回应、轻松接话、只看最近消息就能回答的内容而检索。不要把私聊或个人隐私记忆带到群聊里。",
            })

        return self._localized_text({
            "en-US": "- query_memory(): Consider retrieval more actively when the other person mentions signals like \"before\", \"last time\", \"recently\", \"do you remember\", \"I like\", or \"I said\", or when the reply depends on long-term preferences, prior promises, shared experiences, or long-term information about a person.",
            "ja-JP": "- query_memory()：相手が「前に」「この前」「最近」「覚えてる？」「好き」「言った」などの合図を出した場合、または返信が長期的な好み、以前の約束、共有した経験、人物の長期的な情報に依存する場合は、より積極的に検索を検討できます。",
            "zh-CN": "- query_memory()：当对方提到“之前”“上次”“最近”“还记得吗”“我喜欢”“我说过”等信号，或回复依赖长期偏好、先前承诺、共同经历、人物长期信息时，可以更积极检索。",
        })

    def _build_current_chat_attention_tail_message(self) -> str:
        """构建追加到请求末尾的当前聊天专属注意事项。"""

        if not self._session_id:
            return ""
        chat_prompt = self._get_chat_prompt_for_chat(self._session_id, self._is_group_chat).strip()
        if not chat_prompt:
            return ""
        return f"当前聊天额外注意事项：\n{chat_prompt}"

    @staticmethod
    def _get_chat_prompt_for_chat(chat_id: str, is_group_chat: Optional[bool]) -> str:
        """根据聊天流 ID 获取匹配的额外提示。"""
        return ChatConfigUtils.get_chat_prompt_for_chat(chat_id, is_group_chat)

    def set_extra_tools(self, tools: Sequence[ToolDefinitionInput]) -> None:
        """设置额外工具定义。

        Args:
            tools: 兼容旧接口的额外工具定义列表。
        """

        self._extra_tools = normalize_tool_options(list(tools)) or []

    def set_tool_registry(self, tool_registry: ToolRegistry | None) -> None:
        """设置统一工具注册表。

        Args:
            tool_registry: 统一工具注册表；传入 ``None`` 时退回旧工具列表模式。
        """

        self._tool_registry = tool_registry

    def set_interrupt_flag(self, interrupt_flag: asyncio.Event | None) -> None:
        """设置当前 planner 请求使用的中断标记。"""
        self._interrupt_flag = interrupt_flag

    def _build_request_messages(
        self,
        selected_history: List[LLMContextMessage],
        *,
        enable_visual_message: bool,
        include_day_boundary_time_messages: bool = False,
        injected_user_messages: Sequence[str] | None = None,
        tail_user_messages: Sequence[str] | None = None,
        final_assistant_message: str | None = None,
        system_prompt: Optional[str] = None,
    ) -> List[Message]:
        """构造发给大模型的消息列表。

        Args:
            selected_history: 已选中的上下文消息列表。

        Returns:
            List[Message]: 发送给大模型的消息列表。
        """

        messages: List[Message] = []
        system_msg = MessageBuilder().set_role(RoleType.System)
        if system_prompt is not None:
            resolved_system_prompt = system_prompt
        elif self._custom_chat_system_prompt is not None:
            resolved_system_prompt = self._custom_chat_system_prompt
        else:
            resolved_system_prompt = self._build_chat_system_prompt()
        system_msg.add_text_content(resolved_system_prompt)
        messages.append(system_msg.build())

        previous_context_timestamp: datetime | None = None
        deferred_boundary_timestamps: List[datetime] = []
        for msg in selected_history:
            llm_message = build_llm_message_from_context(
                msg,
                enable_visual_message=enable_visual_message,
            )
            if llm_message is None:
                continue

            # assistant tool_calls 与其连续 tool 结果是协议原子段，跨日时间提示必须延后到整个结果段之后。
            if llm_message.role != RoleType.Tool and deferred_boundary_timestamps:
                for boundary_timestamp in deferred_boundary_timestamps:
                    self._append_time_user_message(messages, boundary_timestamp)
                deferred_boundary_timestamps.clear()

            if (
                include_day_boundary_time_messages
                and previous_context_timestamp is not None
                and previous_context_timestamp.date() != msg.timestamp.date()
            ):
                if llm_message.role == RoleType.Tool:
                    deferred_boundary_timestamps.append(msg.timestamp)
                else:
                    self._append_time_user_message(messages, msg.timestamp)

            messages.append(llm_message)
            previous_context_timestamp = msg.timestamp

        for boundary_timestamp in deferred_boundary_timestamps:
            self._append_time_user_message(messages, boundary_timestamp)

        normalized_injected_messages: List[Message] = []
        current_chat_attention = self._build_current_chat_attention_tail_message()
        final_user_messages = [
            *(injected_user_messages or []),
            self._build_current_time_user_message(),
            *(tail_user_messages or []),
            current_chat_attention,
        ]
        for injected_message in final_user_messages:
            normalized_message = str(injected_message or "").strip()
            if not normalized_message:
                continue
            normalized_injected_messages.append(
                MessageBuilder()
                .set_role(RoleType.User)
                .add_text_content(normalized_message)
                .build()
            )

        if normalized_injected_messages:
            messages.extend(normalized_injected_messages)

        normalized_final_assistant_message = str(final_assistant_message or "").strip()
        if normalized_final_assistant_message:
            messages.append(
                MessageBuilder()
                .set_role(RoleType.Assistant)
                .add_text_content(normalized_final_assistant_message)
                .build()
            )

        return messages

    async def chat_loop_step(
        self,
        chat_history: List[LLMContextMessage],
        *,
        injected_user_messages: Sequence[str] | None = None,
        request_kind: str = "planner",
        response_format: RespFormat | None = None,
        tool_definitions: Sequence[ToolDefinitionInput] | None = None,
        max_context_size: Optional[int] = None,
        system_prompt: Optional[str] = None,
        tail_user_messages: Sequence[str] | None = None,
    ) -> ChatResponse:
        """执行一轮 Maisaka 规划器请求。

        Args:
            chat_history: 当前对话历史。

        Returns:
            ChatResponse: 本轮规划器返回结果。
        """

        enable_visual_message = self._resolve_enable_visual_message(request_kind)
        selected_history, selection_reason = self.select_llm_context_messages(
            chat_history,
            request_kind=request_kind,
            enable_visual_message=enable_visual_message,
            max_context_size=max_context_size,
            is_group_chat=self._is_group_chat,
        )
        built_messages = self._build_request_messages(
            selected_history,
            enable_visual_message=enable_visual_message,
            include_day_boundary_time_messages=request_kind == "planner",
            injected_user_messages=injected_user_messages,
            tail_user_messages=tail_user_messages,
            final_assistant_message=(
                self._build_planner_final_assistant_reminder() if request_kind == "planner" else None
            ),
            system_prompt=system_prompt,
        )
        if enable_visual_message:
            built_messages = limit_latest_images_in_messages(
                built_messages,
                max_image_num=global_config.visual.max_image_num,
            )

        def message_factory(_client: BaseClient) -> List[Message]:
            """返回当前轮次已经构建好的请求消息。

            Args:
                _client: 当前模型客户端；此处不依赖客户端能力。

            Returns:
                List[Message]: 已经构建好的消息列表。
            """

            del _client
            return built_messages

        all_tools: List[ToolDefinitionInput]
        if tool_definitions is not None:
            all_tools = list(tool_definitions)
        elif self._tool_registry is not None:
            availability_context = ToolAvailabilityContext(
                session_id=self._session_id,
                stream_id=self._session_id,
                is_group_chat=self._is_group_chat,
            )
            tool_specs = await self._tool_registry.list_tools(availability_context)
            all_tools = [tool_spec.to_llm_definition() for tool_spec in tool_specs]
        else:
            availability_context = ToolAvailabilityContext(
                session_id=self._session_id,
                stream_id=self._session_id,
                is_group_chat=self._is_group_chat,
            )
            all_tools = [*get_builtin_tools(availability_context), *self._extra_tools]

        before_request_result = await self._get_runtime_manager().invoke_hook(
            "maisaka.planner.before_request",
            messages=serialize_prompt_messages(built_messages),
            tool_definitions=serialize_tool_definitions(all_tools),
            selected_history_count=len(selected_history),
            built_message_count=len(built_messages),
            selection_reason=selection_reason,
            session_id=self._session_id,
        )
        before_request_kwargs = before_request_result.kwargs
        raw_messages = before_request_kwargs.get("messages")
        if isinstance(raw_messages, list):
            try:
                built_messages = deserialize_prompt_messages(raw_messages)
            except Exception as exc:
                logger.warning(f"Hook maisaka.planner.before_request 返回的 messages 无法反序列化，已忽略: {exc}")
        if enable_visual_message:
            built_messages = limit_latest_images_in_messages(
                built_messages,
                max_image_num=global_config.visual.max_image_num,
            )
        raw_tool_definitions = before_request_kwargs.get("tool_definitions")
        if isinstance(raw_tool_definitions, list):
            all_tools = [item for item in raw_tool_definitions if isinstance(item, dict)]

        prompt_section: RenderableType | None = None
        prompt_html_uri: str | None = None

        llm_chat = self._get_llm_chat_client(request_kind)
        llm_started_at = time.perf_counter()
        generation_result = await llm_chat.generate_response_with_messages(
            message_factory=message_factory,
            options=LLMGenerationOptions(
                tool_options=all_tools if all_tools else None,
                response_format=response_format,
                interrupt_flag=self._interrupt_flag,
            ),
        )
        llm_duration_ms = round((time.perf_counter() - llm_started_at) * 1000, 2)
        self._log_prompt_cache_usage(
            request_kind=request_kind,
            prompt_tokens=generation_result.prompt_tokens,
            prompt_cache_hit_tokens=getattr(generation_result, "prompt_cache_hit_tokens", 0) or 0,
            prompt_cache_miss_tokens=getattr(generation_result, "prompt_cache_miss_tokens", 0) or 0,
        )

        final_reasoning = generation_result.reasoning or ""
        final_response = self._resolve_planner_response_content(generation_result.response or "", final_reasoning)
        final_tool_calls = list(generation_result.tool_calls or [])
        after_response_result = await self._get_runtime_manager().invoke_hook(
            "maisaka.planner.after_response",
            response=final_response,
            tool_calls=serialize_tool_calls(final_tool_calls),
            selected_history_count=len(selected_history),
            built_message_count=len(built_messages),
            selection_reason=selection_reason,
            session_id=self._session_id,
            prompt_tokens=generation_result.prompt_tokens,
            completion_tokens=generation_result.completion_tokens,
            total_tokens=generation_result.total_tokens,
        )
        after_response_kwargs = after_response_result.kwargs
        if "response" in after_response_kwargs:
            final_response = str(after_response_kwargs.get("response") or "")
        raw_tool_calls = after_response_kwargs.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            try:
                final_tool_calls = deserialize_tool_calls(raw_tool_calls)
            except Exception as exc:
                logger.warning(f"Hook maisaka.planner.after_response 返回的 tool_calls 无法反序列化，已忽略: {exc}")
        prompt_tokens = self._coerce_int(after_response_kwargs.get("prompt_tokens"), generation_result.prompt_tokens)
        completion_tokens = self._coerce_int(
            after_response_kwargs.get("completion_tokens"),
            generation_result.completion_tokens,
        )
        total_tokens = self._coerce_int(after_response_kwargs.get("total_tokens"), generation_result.total_tokens)
        display_model_name = (generation_result.model_name or "").strip()
        prompt_selection_reason = selection_reason
        if display_model_name:
            prompt_selection_reason = f"{selection_reason}\n请求模型：{display_model_name}"
        prompt_metadata = {
            "model_name": display_model_name,
            "duration_ms": llm_duration_ms,
        }

        prompt_section_result = PromptCLIVisualizer.build_prompt_section_result(
            built_messages,
            category=self._resolve_prompt_preview_category(request_kind),
            chat_id=self._session_id,
            request_kind=request_kind,
            selection_reason=prompt_selection_reason,
            tool_definitions=list(all_tools),
            output_content=final_response.strip(),
            output_tool_calls=final_tool_calls,
            metadata=prompt_metadata,
        )
        prompt_html_uri = prompt_section_result.preview_access.preview_web_uri
        if global_config.debug.show_maisaka_thinking:
            prompt_section = prompt_section_result.panel

        raw_message = AssistantMessage(
            content=final_response,
            timestamp=datetime.now(),
            tool_calls=final_tool_calls,
        )
        return ChatResponse(
            content=final_response or None,
            tool_calls=final_tool_calls,
            request_messages=list(built_messages),
            raw_message=raw_message,
            selected_history_count=len(selected_history),
            tool_count=len(all_tools),
            prompt_tokens=prompt_tokens,
            built_message_count=len(built_messages),
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model_name=display_model_name,
            duration_ms=llm_duration_ms,
            prompt_section=prompt_section,
            prompt_html_uri=prompt_html_uri,
            reasoning=final_reasoning,
        )

    @staticmethod
    def select_llm_context_messages(
        chat_history: List[LLMContextMessage],
        *,
        enable_visual_message: Optional[bool] = None,
        request_kind: str = "planner",
        max_context_size: Optional[int] = None,
        is_group_chat: Optional[bool] = None,
    ) -> tuple[List[LLMContextMessage], str]:
        """选择LLM上下文消息"""

        filtered_history = MaisakaChatLoopService._filter_history_for_request_kind(
            chat_history,
            request_kind=request_kind,
        )
        base_context_size = max(1, int(max_context_size or global_config.chat.max_context_size))
        effective_context_size = max(
            base_context_size,
            int(base_context_size * CONTEXT_SELECTION_CACHE_STABILITY_RATIO),
        )
        selected_indices: List[int] = []
        counted_message_count = 0

        active_enable_visual_message = (
            enable_visual_message
            if enable_visual_message is not None
            else MaisakaChatLoopService._resolve_enable_visual_message(request_kind)
        )
        always_selected_indices = MaisakaChatLoopService._collect_always_selected_reference_indices(
            filtered_history,
            enable_visual_message=active_enable_visual_message,
        )

        for index in range(len(filtered_history) - 1, -1, -1):
            message = filtered_history[index]
            if (
                build_llm_message_from_context(
                    message,
                    enable_visual_message=active_enable_visual_message,
                )
                is None
            ):
                continue

            selected_indices.append(index)
            if message.count_in_context:
                counted_message_count += 1
                if counted_message_count >= effective_context_size:
                    break

        selected_indices = sorted({*always_selected_indices, *selected_indices})

        if not selected_indices:
            return [], "实际发送 0 条消息（tool 0 条，普通消息 0 条）"

        selected_history = [filtered_history[index] for index in selected_indices]
        selected_history, _ = normalize_tool_call_result_pairs(selected_history)
        tool_message_count = sum(1 for message in selected_history if isinstance(message, ToolResultMessage))
        normal_message_count = len(selected_history) - tool_message_count
        stability_text = f"|cache_window {base_context_size}->{effective_context_size}"
        selection_reason = (
            f"实际发送 {len(selected_history)} 条消息"
            f"|消息 {normal_message_count} 条|tool {tool_message_count} 条"
            f"{stability_text}"
        )
        return (
            selected_history,
            selection_reason,
        )

    @staticmethod
    def _collect_always_selected_reference_indices(
        chat_history: List[LLMContextMessage],
        *,
        enable_visual_message: bool,
    ) -> List[int]:
        """收集需要长期随请求发送的参考消息索引。"""

        selected_indices: List[int] = []
        for index, message in enumerate(chat_history):
            if not (
                isinstance(message, ReferenceMessage)
                and message.reference_type == ReferenceMessageType.CONTEXT_RESTORE
            ):
                continue
            if build_llm_message_from_context(message, enable_visual_message=enable_visual_message) is None:
                continue
            selected_indices.append(index)
        return selected_indices

    @staticmethod
    def _filter_history_for_request_kind(
        selected_history: List[LLMContextMessage],
        *,
        request_kind: str,
    ) -> List[LLMContextMessage]:
        """按请求类型过滤不应暴露的历史工具链。"""

        if request_kind == "expression_selector":
            return [
                message
                for message in selected_history
                if isinstance(message, SessionBackedMessage)
            ]

        if request_kind == "planner":
            return [
                message
                for message in selected_history
                if not is_mid_term_memory_message(message)
            ]

        if request_kind != "planner":
            return [
                message
                for message in selected_history
                if message.source != "behavior_pattern" and not is_mid_term_memory_message(message)
            ]

        return selected_history

    @staticmethod
    def _resolve_enable_visual_message(request_kind: str) -> bool:
        if request_kind == "planner":
            return resolve_enable_visual_planner()
        if request_kind in {"expression_selector", "reply_effect_judge", "behavior_scenario_analyzer"}:
            return False
        return True
