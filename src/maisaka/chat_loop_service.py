"""Maisaka 对话循环服务。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence

import asyncio

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

from .builtin_tool import get_builtin_tools
from .context_messages import (
    AssistantMessage,
    LLMContextMessage,
    TIMING_GATE_INVALID_TOOL_HINT_SOURCE,
    ToolResultMessage,
    build_llm_message_from_context,
)
from .history_utils import drop_orphan_tool_results, normalize_tool_result_order
from .display.prompt_cli_renderer import PromptCLIVisualizer
from .visual_mode_utils import resolve_enable_visual_planner

TIMING_GATE_TOOL_NAMES = {"continue", "no_reply", "wait"}
REQUEST_TYPE_BY_REQUEST_KIND = {
    "planner": "maisaka_planner",
    "timing_gate": "maisaka_timing_gate",
}
PROMPT_PREVIEW_CATEGORY_BY_REQUEST_KIND = {
    "planner": "planner",
    "timing_gate": "timing_gate",
    "reply_effect_judge": "reply_effect_judge",
    "expression_selector": "expression_selector",
    "emotion": "emotion",
    "sub_agent": "sub_agent",
}
CONTEXT_SELECTION_CACHE_STABILITY_RATIO = 2.0


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
    prompt_section: Optional[RenderableType] = None
    prompt_html_uri: Optional[str] = None


logger = get_logger("maisaka_chat_loop")


def register_maisaka_hook_specs(registry: HookSpecRegistry) -> List[HookSpec]:
    """注册 Maisaka 规划器内置 Hook 规格。

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
    def personality_prompt(self) -> str:
        """返回当前人格提示词。"""

        return self._build_personality_prompt()

    @staticmethod
    def _resolve_llm_request_type(request_kind: str) -> str:
        """根据 Maisaka 请求类型解析 LLM 统计口径。"""

        normalized_request_kind = str(request_kind or "").strip()
        return REQUEST_TYPE_BY_REQUEST_KIND.get(
            normalized_request_kind,
            f"maisaka_{normalized_request_kind}" if normalized_request_kind else "maisaka_planner",
        )

    @staticmethod
    def _resolve_prompt_preview_category(request_kind: str) -> str:
        """根据请求类型决定 Prompt 预览落盘目录，避免子代理混入 planner。"""

        normalized_request_kind = str(request_kind or "").strip().lower()
        if not normalized_request_kind:
            return "planner"
        return PROMPT_PREVIEW_CATEGORY_BY_REQUEST_KIND.get(normalized_request_kind, normalized_request_kind)

    def _get_llm_chat_client(self, request_kind: str) -> LLMServiceClient:
        """获取当前请求类型对应的 LLM 客户端。"""

        request_type = self._resolve_llm_request_type(request_kind)
        client_key = f"{self._model_task_name}:{request_type}"
        llm_client = self._llm_chat_clients.get(client_key)
        if llm_client is None:
            llm_client = LLMServiceClient(
                task_name=self._model_task_name,
                request_type=request_type,
                session_id=self._session_id,
            )
            self._llm_chat_clients[client_key] = llm_client
        return llm_client

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
            "Maisaka KV cache usage - "
            f"request_kind={request_kind}, "
            f"hit_tokens={prompt_cache_hit_tokens}, "
            f"miss_tokens={prompt_cache_miss_tokens}, "
            f"hit_rate={prompt_cache_hit_rate:.2f}%, "
            f"prompt_tokens={prompt_tokens}"
        )

    def _build_personality_prompt(self) -> str:
        """构造人格提示词。"""

        try:
            bot_name = global_config.bot.nickname
            if global_config.bot.alias_names:
                bot_nickname = f"，也有人叫你{','.join(global_config.bot.alias_names)}"
            else:
                bot_nickname = ""

            prompt_personality = global_config.personality.personality.strip()
            if not prompt_personality:
                prompt_personality = "是人类。"

            return f"你的名字是{bot_name}{bot_nickname}。\n{prompt_personality}"
        except Exception:
            return "你的名字是麦麦。\n是人类。"

    async def ensure_chat_prompt_loaded(self, tools_section: str = "") -> None:
        """确保主聊天提示词已经加载完成。

        Args:
            tools_section: 额外注入到提示词中的工具说明片段。
        """
        async with self._prompt_load_lock:
            self._build_chat_system_prompt(tools_section)

    def _build_chat_system_prompt(self, tools_section: str = "") -> str:
        """基于当前配置实时构造主聊天系统提示词。"""

        try:
            return load_prompt("maisaka_chat", **self.build_prompt_template_context(tools_section))
        except Exception:
            return f"{self.personality_prompt}\n\nYou are a helpful AI assistant."

    def build_prompt_template_context(self, tools_section: str = "") -> dict[str, str]:
        """构造 Maisaka prompt 模板的公共渲染参数。"""

        return {
            "bot_name": global_config.bot.nickname,
            "file_tools_section": tools_section,
            "group_chat_attention_block": self._build_group_chat_attention_block(),
            "identity": self.personality_prompt,
            "timing_gate_wait_rule": self._build_timing_gate_wait_rule(),
        }



    @staticmethod
    def _build_current_time_user_message() -> str:
        """构建追加到请求末尾的当前时间消息。"""

        return f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    def _build_group_chat_attention_block(self) -> str:
        """构建当前聊天场景下的额外注意事项块。"""

        prompt_lines: List[str] = []

        if self._is_group_chat is True:
            if group_chat_prompt := str(global_config.chat.group_chat_prompt or "").strip():
                prompt_lines.append(f"通用注意事项：\n{group_chat_prompt}")
        elif self._is_group_chat is False:
            if private_chat_prompt := str(global_config.chat.private_chat_prompts or "").strip():
                prompt_lines.append(f"通用注意事项：\n{private_chat_prompt}")

        if self._session_id:
            if chat_prompt := self._get_chat_prompt_for_chat(self._session_id, self._is_group_chat).strip():
                prompt_lines.append(f"当前聊天额外注意事项：\n{chat_prompt}")

        if not prompt_lines:
            return ""

        return "在该聊天中的注意事项：\n" + "\n\n".join(prompt_lines) + "\n"

    def _build_timing_gate_wait_rule(self) -> str:
        """构造 Timing Gate 中 wait 工具的场景说明。"""

        locale = get_locale()
        if locale == "en-US":
            if self._is_group_chat is True:
                return ""
            if self._is_group_chat is False:
                return "- wait: wait for a fixed period, then judge again"
            return "- wait: available only in private chats; if this is a group chat, do not call it"
        if locale == "ja-JP":
            if self._is_group_chat is True:
                return ""
            if self._is_group_chat is False:
                return "- wait：一定時間待ってから再判断する"
            return "- wait：個人チャットでのみ利用できます。現在がグループチャットなら呼び出さないでください"

        if self._is_group_chat is True:
            return ""
        if self._is_group_chat is False:
            return "- wait：固定再等待一段时间，时间到后再重新判断"
        return "- wait：仅私聊可用；如果当前是群聊，不要调用"

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
        injected_user_messages: Sequence[str] | None = None,
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

        for msg in selected_history:
            llm_message = build_llm_message_from_context(
                msg,
                enable_visual_message=enable_visual_message,
            )
            if llm_message is not None:
                messages.append(llm_message)

        normalized_injected_messages: List[Message] = []
        final_user_messages = [
            *(injected_user_messages or []),
            self._build_current_time_user_message(),
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

        return messages

    async def chat_loop_step(
        self,
        chat_history: List[LLMContextMessage],
        *,
        injected_user_messages: Sequence[str] | None = None,
        request_kind: str = "planner",
        response_format: RespFormat | None = None,
        tool_definitions: Sequence[ToolDefinitionInput] | None = None,
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
        )
        built_messages = self._build_request_messages(
            selected_history,
            enable_visual_message=enable_visual_message,
            injected_user_messages=injected_user_messages,
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
            tool_specs = await self._tool_registry.list_tools(
                ToolAvailabilityContext(
                    session_id=self._session_id,
                    stream_id=self._session_id,
                    is_group_chat=self._is_group_chat,
                )
            )
            all_tools = [tool_spec.to_llm_definition() for tool_spec in tool_specs]
        else:
            all_tools = [*get_builtin_tools(), *self._extra_tools]

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
        raw_tool_definitions = before_request_kwargs.get("tool_definitions")
        if isinstance(raw_tool_definitions, list):
            all_tools = [item for item in raw_tool_definitions if isinstance(item, dict)]

        prompt_section: RenderableType | None = None
        prompt_html_uri: str | None = None
        if global_config.debug.show_maisaka_thinking:
            prompt_section_result = PromptCLIVisualizer.build_prompt_section_result(
                built_messages,
                category=self._resolve_prompt_preview_category(request_kind),
                chat_id=self._session_id,
                request_kind=request_kind,
                selection_reason=selection_reason,
                folded=global_config.debug.fold_maisaka_thinking,
                tool_definitions=list(all_tools),
            )
            prompt_section = prompt_section_result.panel
            if prompt_section_result.preview_access is not None:
                prompt_html_uri = prompt_section_result.preview_access.viewer_web_uri

        llm_chat = self._get_llm_chat_client(request_kind)
        generation_result = await llm_chat.generate_response_with_messages(
            message_factory=message_factory,
            options=LLMGenerationOptions(
                tool_options=all_tools if all_tools else None,
                response_format=response_format,
                interrupt_flag=self._interrupt_flag,
            ),
        )
        self._log_prompt_cache_usage(
            request_kind=request_kind,
            prompt_tokens=generation_result.prompt_tokens,
            prompt_cache_hit_tokens=getattr(generation_result, "prompt_cache_hit_tokens", 0) or 0,
            prompt_cache_miss_tokens=getattr(generation_result, "prompt_cache_miss_tokens", 0) or 0,
        )

        final_response = generation_result.response or ""
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
            prompt_section=prompt_section,
            prompt_html_uri=prompt_html_uri,
        )

    @staticmethod
    def select_llm_context_messages(
        chat_history: List[LLMContextMessage],
        *,
        enable_visual_message: Optional[bool] = None,
        request_kind: str = "planner",
        max_context_size: Optional[int] = None,
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

        if not selected_indices:
            return [], "实际发送 0 条消息（tool 0 条，普通消息 0 条）"

        selected_indices.reverse()
        selected_history = [filtered_history[index] for index in selected_indices]
        selected_history, _ = drop_orphan_tool_results(selected_history)
        selected_history, _ = normalize_tool_result_order(selected_history)
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
    def _filter_history_for_request_kind(
        selected_history: List[LLMContextMessage],
        *,
        request_kind: str,
    ) -> List[LLMContextMessage]:
        """按请求类型过滤不应暴露的历史工具链。"""

        if request_kind == "timing_gate":
            return selected_history

        selected_history = [
            message
            for message in selected_history
            if message.source != TIMING_GATE_INVALID_TOOL_HINT_SOURCE
        ]

        if request_kind != "planner":
            return selected_history

        filtered_history: List[LLMContextMessage] = []
        for message in selected_history:
            if isinstance(message, ToolResultMessage) and message.tool_name in TIMING_GATE_TOOL_NAMES:
                continue

            if isinstance(message, AssistantMessage) and message.tool_calls:
                kept_tool_calls = [
                    tool_call
                    for tool_call in message.tool_calls
                    if tool_call.func_name not in TIMING_GATE_TOOL_NAMES
                ]
                if not kept_tool_calls:
                    continue
                if len(kept_tool_calls) != len(message.tool_calls):
                    filtered_history.append(
                        AssistantMessage(
                            content=message.content,
                            timestamp=message.timestamp,
                            tool_calls=kept_tool_calls,
                            source_kind=message.source_kind,
                        )
                    )
                    continue

            filtered_history.append(message)

        return filtered_history

    @staticmethod
    def _resolve_enable_visual_message(request_kind: str) -> bool:
        if request_kind in {"planner", "timing_gate"}:
            return resolve_enable_visual_planner()
        return True

