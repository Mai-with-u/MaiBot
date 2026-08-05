"""LLM 服务层。

该模块负责在宿主侧收口统一的 LLM 服务请求模型，并将其转发到
`src.llm_models` 中的底层请求调度器。
"""

from dataclasses import replace
from enum import Enum
from typing import Any, Dict, List, Mapping, Tuple

import hashlib
import inspect
import json
import uuid

from src.common.data_models.embedding_service_data_models import EmbeddingResult
from src.common.data_models.llm_service_data_models import (
    ContextFactory,
    LLMAudioTranscriptionResult,
    LLMGenerationOptions,
    LLMImageOptions,
    LLMResponseResult,
    LLMServiceRequest,
    LLMServiceResult,
    PromptInput,
    PromptMessage,
)
from src.common.logger import get_logger
from src.common.utils.utils_image import ImageUtils
from src.llm_models.model_client.base_client import BaseClient
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextImagePart,
    ContextItem,
    ContextItemBuilder,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    ReasoningItem,
    ReasoningRepresentation,
    RoleType,
    get_item_text,
)
from src.llm_models.payload_content.tool_option import ToolCall
from src.llm_models.utils_model import LLMOrchestrator
from src.services.embedding_service import EmbeddingServiceClient
from src.services.llm_cache_stats import record_llm_cache_usage
from src.services.service_task_resolver import (
    get_available_models as _get_available_models,
    resolve_task_name as _resolve_task_name,
    resolve_task_name_from_model_config as _resolve_task_name_from_model_config,
)

logger = get_logger("llm_service")

CACHE_STATS_BINARY_KEYS = frozenset({"base64", "encrypted_content", "image_base64", "thought_signature"})


class LLMServiceClient:
    """面向上层模块的 LLM 服务对象式门面。

    当前推荐优先使用以下正式接口：
    - `generate_response`
    - `generate_response_with_context`
    - `generate_response_for_image`
    - `transcribe_audio`
    - `embed_text`（兼容入口，推荐改用 `EmbeddingServiceClient`）
    """

    def __init__(self, task_name: str, request_type: str = "", session_id: str = "") -> None:
        """初始化 LLM 服务门面。

        Args:
            task_name: 任务配置名称，对应 `model_task_config` 下的字段名。
            request_type: 当前请求的业务类型标识。
        """
        self.task_name = _resolve_task_name(task_name)
        self.request_type = request_type
        self.session_id = str(session_id or "").strip()
        self._orchestrator = LLMOrchestrator(
            task_name=self.task_name,
            request_type=request_type,
            session_id=self.session_id,
        )

    def _resolve_effective_session_id(self, session_id: str = "") -> str:
        """解析本次请求用于统计归属的聊天流 ID。"""

        return str(session_id or self.session_id or "").strip()

    @staticmethod
    def _normalize_generation_options(options: LLMGenerationOptions | None = None) -> LLMGenerationOptions:
        """规范化文本生成选项。

        Args:
            options: 原始生成选项。

        Returns:
            LLMGenerationOptions: 可直接用于执行请求的完整选项对象。
        """
        if options is None:
            return LLMGenerationOptions()
        return options

    @staticmethod
    def _normalize_image_options(options: LLMImageOptions | None = None) -> LLMImageOptions:
        """规范化图像理解选项。

        Args:
            options: 原始图像理解选项。

        Returns:
            LLMImageOptions: 可直接用于执行请求的完整选项对象。
        """
        if options is None:
            return LLMImageOptions()
        return options

    @staticmethod
    def _serialize_item_for_cache_stats(item: ContextItem) -> Dict[str, Any]:
        """序列化 Context Item 的可移植投影，媒体仅记录摘要。"""

        parts: list[dict[str, Any]] = []
        for part in getattr(item, "parts", ()):
            if isinstance(part, ContextTextPart):
                parts.append({"type": "text", "text": part.text})
                continue
            if not isinstance(part, ContextImagePart):
                continue
            image_base64 = part.image_base64
            image_digest = hashlib.sha256(image_base64.encode("utf-8")).hexdigest() if image_base64 else ""
            parts.append(
                {
                    "type": "image",
                    "format": part.image_format,
                    "size": len(image_base64),
                    "sha256": image_digest,
                }
            )

        payload: Dict[str, Any] = {
            "item_id": item.meta.item_id,
            "item_type": item.__class__.__name__,
            "parts": parts,
        }
        if isinstance(item, ReasoningItem):
            payload["reasoning"] = get_item_text(item)
            payload["representation"] = item.representation.value
        elif isinstance(item, FunctionCallItem):
            payload["tool_call"] = {
                "id": item.tool_call.call_id,
                "name": item.tool_call.func_name,
                "arguments": item.tool_call.materialize_args(),
            }
        elif isinstance(item, FunctionCallOutputItem):
            payload["call_id"] = item.call_id
            payload["output"] = item.output
            payload["tool_name"] = item.tool_name
        return payload

    @staticmethod
    def _summarize_wire_binary_for_cache_stats(
        value: str | bytes,
        *,
        media_type: str = "",
    ) -> Dict[str, Any]:
        """将 wire payload 中的二进制或 Base64 内容压缩为稳定摘要。"""

        raw_bytes = value.encode("utf-8") if isinstance(value, str) else value
        payload: Dict[str, Any] = {
            "type": "omitted_binary",
            "size_bytes": len(raw_bytes),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        }
        if isinstance(value, str):
            payload["encoded_chars"] = len(value)
        if media_type:
            payload["media_type"] = media_type
        return payload

    @classmethod
    def _sanitize_wire_value_for_cache_stats(
        cls,
        value: Any,
        *,
        key: str = "",
    ) -> Any:
        """保留 wire 请求结构，同时移除不适合进入 prompt cache 统计池的大块数据。"""

        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, Enum):
            return cls._sanitize_wire_value_for_cache_stats(value.value, key=key)
        if isinstance(value, bytes):
            return cls._summarize_wire_binary_for_cache_stats(value)
        if isinstance(value, str):
            normalized_value = value.strip()
            if normalized_value.startswith("data:") and ";base64," in normalized_value:
                header, encoded_value = normalized_value.split(",", maxsplit=1)
                media_type = header.removeprefix("data:").split(";", maxsplit=1)[0]
                return cls._summarize_wire_binary_for_cache_stats(
                    encoded_value,
                    media_type=media_type,
                )
            normalized_key = key.strip().lower().replace("-", "_")
            if normalized_key in CACHE_STATS_BINARY_KEYS:
                return cls._summarize_wire_binary_for_cache_stats(value)
            return value
        if isinstance(value, Mapping):
            return {
                str(item_key): cls._sanitize_wire_value_for_cache_stats(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_wire_value_for_cache_stats(item, key=key) for item in value]

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return cls._sanitize_wire_value_for_cache_stats(model_dump(exclude_none=True), key=key)
        return {
            "type": "wire_object",
            "class": f"{value.__class__.__module__}.{value.__class__.__qualname__}",
        }

    @classmethod
    def _build_cache_stats_prompt_text(
        cls,
        *,
        context_items: List[ContextItem],
        tool_options: Any,
        response_format: Any,
    ) -> str:
        payload = {
            "context_items": [cls._serialize_item_for_cache_stats(item) for item in context_items],
            "tool_options": tool_options or [],
            "response_format": response_format,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _record_cache_stats(
        self,
        result: LLMResponseResult,
        prompt_text: str | None = None,
        *,
        session_id: str = "",
    ) -> None:
        """记录当前调用的 prompt cache 统计。"""

        effective_prompt_text = prompt_text
        if result.request_wire_payload is not None:
            effective_prompt_text = json.dumps(
                {
                    "wire_protocol": result.wire_protocol,
                    "request": self._sanitize_wire_value_for_cache_stats(result.request_wire_payload),
                },
                ensure_ascii=False,
                sort_keys=True,
            )

        record_llm_cache_usage(
            task_name=self.task_name,
            request_type=self.request_type,
            model_name=result.model_name,
            session_id=self._resolve_effective_session_id(session_id),
            prompt_tokens=result.prompt_tokens,
            prompt_cache_hit_tokens=result.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=result.prompt_cache_miss_tokens,
            prompt_text=effective_prompt_text,
        )

    async def generate_response(
        self,
        prompt: str,
        options: LLMGenerationOptions | None = None,
        *,
        session_id: str = "",
    ) -> LLMResponseResult:
        """生成单轮文本响应。

        Args:
            prompt: 文本提示词。
            options: 文本生成选项。

        Returns:
            LLMResponseResult: 统一文本生成结果。
        """
        active_options = self._normalize_generation_options(options)
        prompt_text = self._build_cache_stats_prompt_text(
            context_items=[ContextItemBuilder().add_text_content(prompt).build()],
            tool_options=active_options.tool_options,
            response_format=active_options.response_format,
        )
        result = await self._orchestrator.generate_response_async(
            prompt=prompt,
            temperature=active_options.temperature,
            max_tokens=active_options.max_tokens,
            model_name=active_options.model_name,
            tools=active_options.tool_options,
            response_format=active_options.response_format,
            raise_when_empty=active_options.raise_when_empty,
            interrupt_flag=active_options.interrupt_flag,
            session_id=self._resolve_effective_session_id(session_id),
        )
        self._record_cache_stats(result, prompt_text=prompt_text, session_id=session_id)
        return result

    async def generate_response_with_context(
        self,
        context_factory: ContextFactory,
        options: LLMGenerationOptions | None = None,
        *,
        session_id: str = "",
    ) -> LLMResponseResult:
        """基于消息工厂生成响应。

        Args:
            context_factory: Context Item 工厂，会根据客户端能力构建上下文。
            options: 文本生成选项。

        Returns:
            LLMResponseResult: 统一文本生成结果。
        """
        active_options = self._normalize_generation_options(options)
        prompt_text_holder: dict[str, str] = {}

        async def cache_stats_context_factory(client: BaseClient, model_info: Any = None) -> List[ContextItem]:
            if len(inspect.signature(context_factory).parameters) >= 2:
                context_result = context_factory(client, model_info)
            else:
                context_result = context_factory(client)
            if inspect.isawaitable(context_result):
                context_items = await context_result
            else:
                context_items = context_result
            prompt_text_holder["prompt_text"] = self._build_cache_stats_prompt_text(
                context_items=context_items,
                tool_options=active_options.tool_options,
                response_format=active_options.response_format,
            )
            return context_items

        result = await self._orchestrator.generate_response_with_context_async(
            context_factory=cache_stats_context_factory,
            temperature=active_options.temperature,
            max_tokens=active_options.max_tokens,
            model_name=active_options.model_name,
            tools=active_options.tool_options,
            response_format=active_options.response_format,
            raise_when_empty=active_options.raise_when_empty,
            interrupt_flag=active_options.interrupt_flag,
            session_id=self._resolve_effective_session_id(session_id),
        )
        self._record_cache_stats(result, prompt_text=prompt_text_holder.get("prompt_text"), session_id=session_id)
        return result

    async def generate_response_for_image(
        self,
        prompt: str,
        image_base64: str,
        image_format: str,
        options: LLMImageOptions | None = None,
        *,
        session_id: str = "",
    ) -> LLMResponseResult:
        """为图像内容生成响应。

        Args:
            prompt: 文本提示词。
            image_base64: 图像的 Base64 编码字符串。
            image_format: 图像格式，例如 ``png``、``jpeg``。
            options: 图像理解选项。

        Returns:
            LLMResponseResult: 统一文本生成结果。
        """
        active_options = self._normalize_image_options(options)
        original_image_base64_size = len(image_base64)
        image_base64, image_format, resized_for_model = ImageUtils.normalize_image_base64_for_model(
            image_base64,
            image_format,
        )
        if resized_for_model:
            logger.info(
                "图片尺寸低于视觉模型常见最小识别限制，已使用最近邻整数放大后再请求模型: "
                f"request_type={self.request_type}, "
                f"original_base64_size={original_image_base64_size}, normalized_base64_size={len(image_base64)}"
            )

        image_digest = hashlib.sha256(image_base64.encode("utf-8")).hexdigest() if image_base64 else ""
        prompt_text = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "parts": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "format": image_format,
                                "size": len(image_base64),
                                "sha256": image_digest,
                            },
                        ],
                    }
                ],
                "tool_options": [],
                "response_format": None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        result = await self._orchestrator.generate_response_for_image(
            prompt=prompt,
            image_base64=image_base64,
            image_format=image_format,
            temperature=active_options.temperature,
            max_tokens=active_options.max_tokens,
            interrupt_flag=active_options.interrupt_flag,
            session_id=self._resolve_effective_session_id(session_id),
        )
        self._record_cache_stats(result, prompt_text=prompt_text, session_id=session_id)
        return result

    async def transcribe_audio(self, voice_base64: str, *, session_id: str = "") -> LLMAudioTranscriptionResult:
        """执行音频转写请求。

        Args:
            voice_base64: 音频的 Base64 编码字符串。

        Returns:
            LLMAudioTranscriptionResult: 音频转写结果对象。
        """
        return await self._orchestrator.generate_response_for_voice(
            voice_base64,
            session_id=self._resolve_effective_session_id(session_id),
        )

    async def embed_text(self, embedding_input: str, *, session_id: str = "") -> EmbeddingResult:
        """兼容旧调用的文本嵌入入口。

        Args:
            embedding_input: 待编码的文本。

        Returns:
            EmbeddingResult: 向量生成结果对象。
        """
        embedding_client = EmbeddingServiceClient(
            task_name=self.task_name,
            request_type=self.request_type,
            session_id=self._resolve_effective_session_id(session_id),
        )
        return await embedding_client.embed_text(embedding_input, session_id=session_id)


def get_available_models() -> Dict[str, Any]:
    """获取所有可用模型配置。

    Returns:
        Dict[str, Any]: 以模型任务名为键的配置映射。
    """
    return _get_available_models()


def resolve_task_name(task_name: str = "") -> str:
    """根据名称解析任务配置名。

    Args:
        task_name: 目标任务配置名；为空时返回首个可用任务名。

    Returns:
        str: 解析得到的任务配置名。
    """
    return _resolve_task_name(task_name)


def resolve_task_name_from_model_config(model_config: Any, preferred_task_name: str = "") -> str:
    """根据旧版 `TaskConfig` 风格参数解析可用任务名。

    Args:
        model_config: 旧调用方持有的任务配置对象。
        preferred_task_name: 候选任务名（可选）。

    Returns:
        str: 可用于 `LLMServiceRequest.task_name` 的任务名。
    """
    return _resolve_task_name_from_model_config(
        model_config=model_config,
        preferred_task_name=preferred_task_name,
    )


def _normalize_role(role_name: str) -> RoleType:
    """将原始角色字符串转换为内部角色枚举。

    Args:
        role_name: 原始角色名称。

    Returns:
        RoleType: 规范化后的角色枚举。

    Raises:
        ValueError: 角色类型不受支持时抛出。
    """
    normalized_role_name = role_name.strip().lower()
    try:
        return RoleType(normalized_role_name)
    except ValueError as exc:
        raise ValueError(f"不支持的消息角色: {role_name}") from exc


def _parse_data_url_image(image_url: str) -> Tuple[str, str]:
    """解析 Data URL 形式的图片内容。

    Args:
        image_url: 图片 URL。

    Returns:
        Tuple[str, str]: `(图片格式, Base64 数据)`。

    Raises:
        ValueError: 输入不是受支持的 Data URL 时抛出。
    """
    if not image_url.startswith("data:image/") or ";base64," not in image_url:
        raise ValueError("仅支持 Data URL 形式的图片输入")
    prefix, image_base64 = image_url.split(";base64,", maxsplit=1)
    image_format = prefix.removeprefix("data:image/")
    if not image_format or not image_base64:
        raise ValueError("图片 Data URL 不完整")
    return image_format, image_base64


def _append_image_content(item_builder: ContextItemBuilder, content_item: Any) -> bool:
    """向消息构建器追加图片片段。

    兼容两种输入格式：
    1. 旧序列化格式中的 `(image_format, image_base64)` 元组。
    2. 标准字典片段中的 Data URL 或 `image_format`/`image_base64` 字段。
    """

    if isinstance(content_item, (tuple, list)) and len(content_item) == 2:
        image_format, image_base64 = content_item
        if not isinstance(image_format, str) or not isinstance(image_base64, str):
            raise ValueError("图片元组片段必须包含字符串类型的 image_format 和 image_base64")

        item_builder.add_image_content(image_format=image_format, image_base64=image_base64)
        return True

    if not isinstance(content_item, dict):
        return False

    part_type = str(content_item.get("type", "text")).strip().lower()
    if part_type not in {"image", "image_url", "input_image"}:
        return False

    image_url = content_item.get("image_url")
    if isinstance(image_url, dict):
        image_url = image_url.get("url")
    if isinstance(image_url, str):
        image_format, image_base64 = _parse_data_url_image(image_url)
        item_builder.add_image_content(image_format=image_format, image_base64=image_base64)
        return True

    image_format = content_item.get("image_format")
    image_base64 = content_item.get("image_base64")
    if isinstance(image_format, str) and isinstance(image_base64, str):
        item_builder.add_image_content(image_format=image_format, image_base64=image_base64)
        return True

    raise ValueError("图片片段缺少可识别的图片数据")


def _append_content_parts(item_builder: ContextItemBuilder, content: Any) -> None:
    """将原始消息内容追加到内部消息构建器。

    Args:
        item_builder: 目标 Item 构建器。
        content: 原始消息内容。

    Raises:
        ValueError: 消息内容结构不受支持时抛出。
    """
    if isinstance(content, str):
        item_builder.add_text_content(content)
        return

    content_items: List[Any]
    if isinstance(content, list):
        content_items = content
    elif isinstance(content, dict):
        content_items = [content]
    else:
        raise ValueError("消息内容必须为字符串、字典或列表")

    for content_item in content_items:
        if isinstance(content_item, str):
            item_builder.add_text_content(content_item)
            continue
        if _append_image_content(item_builder, content_item):
            continue
        if not isinstance(content_item, dict):
            raise ValueError("消息内容列表中仅支持字符串、图片元组或字典片段")

        part_type = str(content_item.get("type", "text")).strip().lower()
        if part_type == "text":
            text_content = content_item.get("text")
            if not isinstance(text_content, str):
                raise ValueError("文本片段缺少 `text` 字段")
            item_builder.add_text_content(text_content)
            continue

        raise ValueError(f"不支持的消息片段类型: {part_type}")


def _normalize_tool_arguments(arguments: Any) -> Dict[str, Any] | None:
    """将原始工具参数规范化为字典。

    Args:
        arguments: 原始工具参数。

    Returns:
        Dict[str, Any] | None: 规范化后的参数字典。
    """
    if arguments is None:
        return None
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        stripped_arguments = arguments.strip()
        if not stripped_arguments:
            return {}
        try:
            parsed_arguments = json.loads(stripped_arguments)
        except json.JSONDecodeError:
            return {"raw_arguments": arguments}
        if isinstance(parsed_arguments, dict):
            return parsed_arguments
        return {"value": parsed_arguments}
    return {"value": arguments}


def _build_tool_calls(raw_tool_calls: Any) -> List[ToolCall] | None:
    """从原始消息中提取工具调用列表。

    Args:
        raw_tool_calls: 原始工具调用结构。

    Returns:
        List[ToolCall] | None: 规范化后的工具调用列表。

    Raises:
        ValueError: 工具调用结构缺失必要字段时抛出。
    """
    if raw_tool_calls is None:
        return None
    if not isinstance(raw_tool_calls, list):
        raise ValueError("`tool_calls` 必须为列表")

    tool_calls: List[ToolCall] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, dict):
            raise ValueError("工具调用项必须为字典")

        function_info = raw_tool_call.get("function")
        if isinstance(function_info, dict):
            func_name = function_info.get("name")
            arguments = function_info.get("arguments")
        else:
            func_name = raw_tool_call.get("name") or raw_tool_call.get("func_name")
            arguments = raw_tool_call.get("arguments") or raw_tool_call.get("args")

        call_id = raw_tool_call.get("id") or raw_tool_call.get("call_id")
        if not isinstance(call_id, str) or not isinstance(func_name, str):
            raise ValueError("工具调用缺少 `id` 或函数名称")

        extra_content = raw_tool_call.get("extra_content")
        tool_calls.append(
            ToolCall(
                call_id=call_id,
                func_name=func_name,
                args=_normalize_tool_arguments(arguments),
                extra_content=extra_content if isinstance(extra_content, dict) else None,
            )
        )

    return tool_calls or None


def _build_context_items_from_dict(
    raw_message: PromptMessage,
    logical_turn_by_call_id: dict[str, str],
) -> List[ContextItem]:
    """将原始 Chat 风格消息字典转换为独立 Context Items。

    Args:
        raw_message: 原始消息字典。

    Returns:
        List[ContextItem]: 规范化后的 Context Items。

    Raises:
        ValueError: 原始消息结构不合法时抛出。
    """
    raw_role = raw_message.get("role")
    if not isinstance(raw_role, str):
        raise ValueError("消息缺少字符串类型的 `role` 字段")

    role = _normalize_role(raw_role)
    item_builder = ContextItemBuilder().set_role(role)

    tool_call_id = raw_message.get("tool_call_id")
    if isinstance(tool_call_id, str) and role == RoleType.Tool:
        item_builder.set_tool_call_id(tool_call_id)
        item_builder.set_meta(
            ContextItemMeta.create(logical_turn_id=logical_turn_by_call_id.get(tool_call_id))
        )

    if "content" in raw_message and raw_message["content"] not in (None, "", []):
        _append_content_parts(item_builder, raw_message["content"])

    if role != RoleType.Assistant:
        return [item_builder.build()]

    logical_turn_id = uuid.uuid4().hex
    ungrouped_items: List[ContextItem] = []
    reasoning_content = raw_message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        ungrouped_items.append(
            ReasoningItem(
                meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
                text_parts=(reasoning_content,),
                representation=ReasoningRepresentation.RAW_TEXT,
            )
        )
    if raw_message.get("content") not in (None, "", []):
        assistant_item = item_builder.build()
        if not isinstance(assistant_item, AssistantMessageItem):
            raise TypeError("Assistant 输入未构建为 AssistantMessageItem")
        ungrouped_items.append(assistant_item)
    for tool_call in _build_tool_calls(raw_message.get("tool_calls")) or []:
        ungrouped_items.append(
            FunctionCallItem(
                meta=ContextItemMeta.create(logical_turn_id=logical_turn_id),
                tool_call=ContextToolCall.create(
                    call_id=tool_call.call_id,
                    func_name=tool_call.func_name,
                    args=tool_call.args,
                    extra_content=tool_call.extra_content,
                ),
            )
        )
    if not ungrouped_items:
        raise ValueError("Assistant 消息至少需要正文、reasoning 或 tool_calls")

    output_items: List[ContextItem] = []
    for item in ungrouped_items:
        bound_item = replace(item, meta=replace(item.meta, logical_turn_id=logical_turn_id))
        output_items.append(bound_item)
        if isinstance(bound_item, FunctionCallItem):
            logical_turn_by_call_id[bound_item.tool_call.call_id] = logical_turn_id
    return output_items


def _build_prompt_context_factory(prompt: PromptInput) -> ContextFactory:
    """将统一提示输入转换为 Context Item 工厂。

    Args:
        prompt: 原始提示输入。

    Returns:
        ContextFactory: 惰性构建 Context Items 的工厂函数。
    """
    if isinstance(prompt, str):

        def build_context(_: BaseClient) -> List[ContextItem]:
            """构建单条用户消息 Item。"""
            item_builder = ContextItemBuilder()
            item_builder.add_text_content(prompt)
            return [item_builder.build()]

        return build_context

    def build_context(_: BaseClient) -> List[ContextItem]:
        """构建多 Item 对话输入。"""
        logical_turn_by_call_id: dict[str, str] = {}
        return [
            item
            for raw_message in prompt
            for item in _build_context_items_from_dict(raw_message, logical_turn_by_call_id)
        ]

    return build_context


async def generate(request: LLMServiceRequest) -> LLMServiceResult:
    """执行统一的 LLM 服务请求。

    Args:
        request: 服务层统一请求对象。

    Returns:
        LLMServiceResult: 统一响应对象；失败时 `success=False`。
    """
    llm_client = LLMServiceClient(
        task_name=request.task_name,
        request_type=request.request_type,
        session_id=request.session_id,
    )
    if request.context_factory is not None:
        active_context_factory = request.context_factory
    else:
        prompt = request.prompt
        if prompt is None:
            raise ValueError("`prompt` 与 `context_factory` 必须且只能提供一个")
        active_context_factory = _build_prompt_context_factory(prompt)

    try:
        generation_result = await llm_client.generate_response_with_context(
            context_factory=active_context_factory,
            options=LLMGenerationOptions(
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                model_name=request.model_name,
                tool_options=request.tool_options,
                response_format=request.response_format,
                interrupt_flag=request.interrupt_flag,
            ),
        )
        return LLMServiceResult.from_response_result(generation_result)
    except Exception as exc:
        error_message = f"生成内容时出错: {exc}"
        logger.error(f"[LLMService] {error_message}")
        return LLMServiceResult.from_error(error_message, str(exc))
