"""MCP 宿主侧大模型桥接服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import json

from src.common.data_models.llm_service_data_models import LLMGenerationOptions, LLMResponseResult
from src.common.logger import get_logger
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput
from src.services.llm_service import LLMServiceClient

from .hooks import MCPHostCallbacks
from .models import build_tool_content_items

if TYPE_CHECKING:
    from src.llm_models.model_client.base_client import BaseClient

try:
    from mcp import types as mcp_types

    MCP_TYPES_AVAILABLE = True
except ImportError:
    mcp_types = None  # type: ignore[assignment]
    MCP_TYPES_AVAILABLE = False

logger = get_logger("mcp_host_llm_bridge")


class MCPHostLLMBridge:
    """将 MCP Sampling 请求桥接到主程序大模型调用链。"""

    def __init__(self, sampling_task_name: str = "planner") -> None:
        """初始化 MCP 宿主侧大模型桥接服务。

        Args:
            sampling_task_name: 执行 Sampling 请求时使用的模型任务名。
        """

        self._sampling_task_name = sampling_task_name.strip() or "planner"
        self._sampling_client = LLMServiceClient(
            task_name=self._sampling_task_name,
            request_type="mcp_sampling",
        )

    def build_callbacks(self) -> MCPHostCallbacks:
        """构建可注入给 MCP 连接层的宿主回调集合。

        Returns:
            MCPHostCallbacks: 包含 Sampling 回调的宿主回调集合。
        """

        return MCPHostCallbacks(
            sampling_callback=self.handle_sampling_request,
        )

    async def handle_sampling_request(self, context: Any, params: Any) -> Any:
        """处理服务端发起的 MCP Sampling 请求。

        Args:
            context: MCP SDK 传入的请求上下文。
            params: `sampling/createMessage` 请求参数。

        Returns:
            Any: MCP `CreateMessageResult`、`CreateMessageResultWithTools` 或 `ErrorData`。
        """

        del context
        if not MCP_TYPES_AVAILABLE or mcp_types is None:
            raise RuntimeError("当前环境未安装可用的 MCP types 模块")

        try:
            tool_choice_mode = self._get_tool_choice_mode(params)
            tool_definitions = self._build_tool_definitions(
                raw_tools=getattr(params, "tools", None),
                tool_choice_mode=tool_choice_mode,
            )
            message_factory = self._build_message_factory(
                raw_messages=list(getattr(params, "messages", []) or []),
                system_prompt=self._build_system_prompt(
                    raw_system_prompt=str(getattr(params, "systemPrompt", "") or ""),
                    tool_choice_mode=tool_choice_mode,
                    tool_definitions=tool_definitions,
                ),
            )

            generation_result = await self._sampling_client.generate_response_with_messages(
                message_factory=message_factory,
                options=LLMGenerationOptions(
                    temperature=self._coerce_float(getattr(params, "temperature", None)),
                    max_tokens=int(getattr(params, "maxTokens", 1024) or 1024),
                    tool_options=tool_definitions,
                ),
            )

            if tool_choice_mode == "required" and tool_definitions and not generation_result.tool_calls:
                return mcp_types.ErrorData(
                    code=mcp_types.INTERNAL_ERROR,
                    message="Sampling 要求必须调用工具，但模型未返回任何工具调用",
                )

            return self._build_sampling_result(
                generation_result=generation_result,
                tools_enabled=bool(tool_definitions),
            )

        except Exception as exc:
            logger.exception(f"MCP Sampling 调用失败: {exc}")
            return mcp_types.ErrorData(
                code=mcp_types.INTERNAL_ERROR,
                message=f"MCP Sampling 调用失败: {exc}",
            )

    @staticmethod
    def _coerce_float(raw_value: Any) -> float | None:
        """将任意原始值转换为浮点数。

        Args:
            raw_value: 原始输入值。

        Returns:
            float | None: 转换后的浮点数；无法转换时返回 ``None``。
        """

        if raw_value is None:
            return None
        if isinstance(raw_value, int | float):
            return float(raw_value)
        return None

    @staticmethod
    def _get_tool_choice_mode(params: Any) -> str:
        """读取 Sampling 请求中的工具选择模式。

        Args:
            params: Sampling 请求参数对象。

        Returns:
            str: `auto`、`required` 或 `none`；缺省时返回 `auto`。
        """

        tool_choice = getattr(params, "toolChoice", None)
        mode = str(getattr(tool_choice, "mode", "") or "").strip().lower()
        if mode in {"required", "none"}:
            return mode
        return "auto"

    def _build_system_prompt(
        self,
        raw_system_prompt: str,
        tool_choice_mode: str,
        tool_definitions: list[ToolDefinitionInput] | None,
    ) -> str:
        """构建发送给主程序大模型的系统提示词。

        Args:
            raw_system_prompt: 服务端请求中的系统提示词。
            tool_choice_mode: 当前工具选择模式。
            tool_definitions: 参与本次 Sampling 的工具定义。

        Returns:
            str: 最终系统提示词。
        """

        prompt_parts: list[str] = []
        if raw_system_prompt.strip():
            prompt_parts.append(raw_system_prompt.strip())
        if tool_choice_mode == "required" and tool_definitions:
            prompt_parts.append("本轮回答必须至少调用一个工具；不要直接结束回答。")
        return "\n\n".join(part for part in prompt_parts if part).strip()

    def _build_message_factory(
        self,
        raw_messages: list[Any],
        system_prompt: str,
    ) -> Any:
        """构建 MCP Sampling 使用的消息工厂。

        Args:
            raw_messages: MCP Sampling 原始消息列表。
            system_prompt: 规范化后的系统提示词。

        Returns:
            Any: 供 `LLMServiceClient` 使用的消息工厂。
        """

        def _message_factory(client: "BaseClient") -> list[Message]:
            """延迟构建内部消息列表。

            Args:
                client: 当前被选中的底层模型客户端。

            Returns:
                list[Message]: 内部统一消息列表。
            """

            messages: list[Message] = []
            if system_prompt.strip():
                messages.append(
                    MessageBuilder()
                    .set_role(RoleType.System)
                    .add_text_content(system_prompt.strip())
                    .build()
                )

            for raw_message in raw_messages:
                messages.extend(self._convert_sampling_message(raw_message, client))
            return messages

        return _message_factory

    def _convert_sampling_message(self, raw_message: Any, client: "BaseClient") -> list[Message]:
        """将单条 MCP Sampling 消息转换为内部消息列表。

        Args:
            raw_message: MCP Sampling 原始消息对象。
            client: 当前底层模型客户端。

        Returns:
            list[Message]: 转换后的内部消息列表。
        """

        role = str(getattr(raw_message, "role", "") or "").strip().lower()
        content_blocks = self._get_content_blocks(getattr(raw_message, "content", None))

        if role == "assistant":
            assistant_message = self._build_assistant_message(content_blocks, client)
            return [assistant_message] if assistant_message is not None else []

        if role == "user":
            return self._build_user_messages(content_blocks, client)

        raise ValueError(f"不支持的 MCP Sampling 消息角色: {role}")

    @staticmethod
    def _get_content_blocks(raw_content: Any) -> list[Any]:
        """将 MCP Sampling 消息内容统一为列表。

        Args:
            raw_content: 原始内容字段。

        Returns:
            list[Any]: 统一后的内容块列表。
        """

        if raw_content is None:
            return []
        if isinstance(raw_content, list):
            return list(raw_content)
        return [raw_content]

    def _build_assistant_message(self, content_blocks: list[Any], client: "BaseClient") -> Optional[Message]:
        """构建内部 assistant 消息。

        Args:
            content_blocks: MCP assistant 内容块列表。
            client: 当前底层模型客户端。

        Returns:
            Optional[Message]: 转换后的内部 assistant 消息；无有效内容时返回 ``None``。
        """

        message_builder = MessageBuilder().set_role(RoleType.Assistant)
        tool_calls: list[ToolCall] = []
        has_visible_content = False

        for content_block in content_blocks:
            content_type = self._get_content_type(content_block)
            if content_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        call_id=str(getattr(content_block, "id", "") or ""),
                        func_name=str(getattr(content_block, "name", "") or ""),
                        args=self._normalize_tool_call_arguments(getattr(content_block, "input", None)),
                    )
                )
                continue

            has_visible_content = self._append_sampling_content_to_builder(
                message_builder=message_builder,
                content_block=content_block,
                client=client,
            ) or has_visible_content

        if tool_calls:
            message_builder.set_tool_calls(tool_calls)

        if not has_visible_content and not tool_calls:
            return None
        return message_builder.build()

    def _build_user_messages(self, content_blocks: list[Any], client: "BaseClient") -> list[Message]:
        """构建内部 user/tool 消息序列。

        Args:
            content_blocks: MCP user 内容块列表。
            client: 当前底层模型客户端。

        Returns:
            list[Message]: 转换后的内部消息序列。
        """

        messages: list[Message] = []
        message_builder = MessageBuilder().set_role(RoleType.User)
        has_user_content = False

        def flush_user_message() -> None:
            """在当前存在用户可见内容时落盘一条 user 消息。"""

            nonlocal message_builder, has_user_content
            if not has_user_content:
                return
            messages.append(message_builder.build())
            message_builder = MessageBuilder().set_role(RoleType.User)
            has_user_content = False

        for content_block in content_blocks:
            content_type = self._get_content_type(content_block)
            if content_type == "tool_result":
                flush_user_message()
                messages.append(self._build_tool_result_message(content_block))
                continue

            has_user_content = self._append_sampling_content_to_builder(
                message_builder=message_builder,
                content_block=content_block,
                client=client,
            ) or has_user_content

        flush_user_message()
        return messages

    @staticmethod
    def _get_content_type(content_block: Any) -> str:
        """读取 MCP 内容块类型。

        Args:
            content_block: MCP 内容块对象。

        Returns:
            str: 规范化后的内容块类型。
        """

        return str(getattr(content_block, "type", "text") or "text").strip().lower()

    def _append_sampling_content_to_builder(
        self,
        message_builder: MessageBuilder,
        content_block: Any,
        client: "BaseClient",
    ) -> bool:
        """将 MCP 普通内容块追加到内部消息构建器。

        Args:
            message_builder: 内部消息构建器。
            content_block: MCP 内容块对象。
            client: 当前底层模型客户端。

        Returns:
            bool: 是否成功追加了可见内容。
        """

        content_type = self._get_content_type(content_block)
        if content_type == "text":
            text_content = str(getattr(content_block, "text", "") or "")
            if text_content.strip():
                message_builder.add_text_content(text_content)
                return True
            return False

        if content_type == "image":
            image_data = str(getattr(content_block, "data", "") or "")
            image_mime_type = str(getattr(content_block, "mimeType", "") or "")
            image_format = self._normalize_image_format(image_mime_type)
            if image_data and image_format:
                message_builder.add_image_content(
                    image_format=image_format,
                    image_base64=image_data,
                    support_formats=client.get_support_image_formats(),
                )
                return True

            message_builder.add_text_content(
                f"[图片内容：mime_type={image_mime_type or 'unknown'}，当前客户端无法直接透传]"
            )
            return True

        if content_type == "audio":
            audio_mime_type = str(getattr(content_block, "mimeType", "") or "")
            message_builder.add_text_content(f"[音频内容：mime_type={audio_mime_type or 'unknown'}]")
            return True

        return False

    @staticmethod
    def _normalize_image_format(mime_type: str) -> str:
        """将图片 MIME 类型转换为内部图片格式名称。

        Args:
            mime_type: MCP 图片 MIME 类型。

        Returns:
            str: 内部支持的图片格式名；不支持时返回空字符串。
        """

        normalized_mime_type = mime_type.strip().lower()
        if normalized_mime_type == "image/png":
            return "png"
        if normalized_mime_type in {"image/jpeg", "image/jpg"}:
            return "jpeg"
        if normalized_mime_type == "image/webp":
            return "webp"
        if normalized_mime_type == "image/gif":
            return "gif"
        return ""

    def _build_tool_result_message(self, content_block: Any) -> Message:
        """将 MCP `tool_result` 内容块转换为内部 Tool 消息。

        Args:
            content_block: MCP `tool_result` 内容块对象。

        Returns:
            Message: 转换后的内部 Tool 消息。
        """

        message_builder = MessageBuilder().set_role(RoleType.Tool)
        message_builder.set_tool_call_id(str(getattr(content_block, "toolUseId", "") or "tool_result"))
        summary_text = self._summarize_tool_result_content(content_block)
        message_builder.add_text_content(summary_text or "工具执行完成。")
        return message_builder.build()

    def _summarize_tool_result_content(self, content_block: Any) -> str:
        """汇总 MCP `tool_result` 内容块中的结果文本。

        Args:
            content_block: MCP `tool_result` 内容块对象。

        Returns:
            str: 适合发送给主程序模型的工具结果摘要文本。
        """

        raw_contents = list(getattr(content_block, "content", []) or [])
        content_items = build_tool_content_items(raw_contents)
        parts = [item.build_history_text().strip() for item in content_items if item.build_history_text().strip()]

        structured_content = getattr(content_block, "structuredContent", None)
        if structured_content is not None:
            try:
                parts.append(json.dumps(structured_content, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(str(structured_content))

        summary_text = "\n".join(part for part in parts if part).strip()
        if bool(getattr(content_block, "isError", False)) and summary_text:
            return f"工具执行失败：\n{summary_text}"
        if bool(getattr(content_block, "isError", False)):
            return "工具执行失败。"
        return summary_text

    @staticmethod
    def _normalize_tool_call_arguments(raw_arguments: Any) -> dict[str, Any]:
        """将原始工具调用参数规范化为字典。

        Args:
            raw_arguments: 原始工具参数。

        Returns:
            dict[str, Any]: 规范化后的参数字典。
        """

        if isinstance(raw_arguments, dict):
            return dict(raw_arguments)
        if raw_arguments is None:
            return {}
        return {"value": raw_arguments}

    def _build_tool_definitions(
        self,
        raw_tools: Any,
        tool_choice_mode: str,
    ) -> list[ToolDefinitionInput] | None:
        """将 MCP Sampling 工具定义转换为主程序内部工具定义。

        Args:
            raw_tools: MCP Sampling 请求中的工具列表。
            tool_choice_mode: 当前工具选择模式。

        Returns:
            list[ToolDefinitionInput] | None: 可传给主程序模型层的工具定义列表。
        """

        if tool_choice_mode == "none":
            return None
        if not isinstance(raw_tools, list) or not raw_tools:
            return None

        tool_definitions: list[ToolDefinitionInput] = []
        for raw_tool in raw_tools:
            tool_name = str(getattr(raw_tool, "name", "") or "").strip()
            if not tool_name:
                continue

            parameters_schema = (
                dict(getattr(raw_tool, "inputSchema", {}) or {}) if getattr(raw_tool, "inputSchema", None) else {}
            )
            if "$schema" in parameters_schema:
                parameters_schema.pop("$schema")

            title = str(getattr(raw_tool, "title", "") or "").strip()
            description = str(getattr(raw_tool, "description", "") or "").strip()
            tool_description = description or title or f"工具 {tool_name}"

            tool_definitions.append(
                {
                    "name": tool_name,
                    "description": tool_description,
                    "parameters_schema": parameters_schema or {"type": "object", "properties": {}},
                }
            )

        return tool_definitions or None

    def _build_sampling_result(
        self,
        generation_result: LLMResponseResult,
        tools_enabled: bool,
    ) -> Any:
        """将主程序模型响应转换为 MCP Sampling 结果。

        Args:
            generation_result: 主程序统一大模型响应结果。
            tools_enabled: 当前是否允许模型使用工具。

        Returns:
            Any: MCP `CreateMessageResult` 或 `CreateMessageResultWithTools`。
        """

        if not MCP_TYPES_AVAILABLE or mcp_types is None:
            raise RuntimeError("当前环境未安装可用的 MCP types 模块")

        text_content = str(generation_result.response or "")
        tool_calls = list(generation_result.tool_calls or [])
        model_name = generation_result.model_name or self._sampling_task_name

        if tools_enabled:
            content_blocks: list[Any] = []
            if text_content.strip():
                content_blocks.append(
                    mcp_types.TextContent(
                        type="text",
                        text=text_content,
                    )
                )
            for tool_call in tool_calls:
                content_blocks.append(
                    mcp_types.ToolUseContent(
                        type="tool_use",
                        name=tool_call.func_name,
                        id=tool_call.call_id,
                        input=dict(tool_call.args or {}),
                    )
                )

            if not content_blocks:
                content_blocks.append(
                    mcp_types.TextContent(
                        type="text",
                        text="",
                    )
                )

            return mcp_types.CreateMessageResultWithTools(
                role="assistant",
                content=content_blocks[0] if len(content_blocks) == 1 else content_blocks,
                model=model_name,
                stopReason="toolUse" if tool_calls else "endTurn",
            )

        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(
                type="text",
                text=text_content,
            ),
            model=model_name,
            stopReason="endTurn",
        )
