"""LLM 服务层与编排层共享数据模型。

该模块集中定义 LLM 服务层与底层编排器共同使用的请求、选项与结果对象，
用于替代散落在各层之间的复杂元组返回值。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, TypeAlias

import asyncio

from src.common.data_models import BaseDataModel
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import ToolCall, ToolDefinitionInput

if TYPE_CHECKING:
    from src.llm_models.payload_content.message import Message


PromptMessage: TypeAlias = Dict[str, Any]
"""统一的原始提示消息结构。"""

PromptInput: TypeAlias = str | List[PromptMessage]
"""统一的提示输入类型。"""

MessageFactory: TypeAlias = Callable[..., List["Message"] | Awaitable[List["Message"]]]
"""统一的消息工厂类型。"""


@dataclass(slots=True)
class LLMServiceRequest(BaseDataModel):
    """LLM 服务层统一请求对象。"""

    task_name: str
    request_type: str
    session_id: str = ""
    prompt: PromptInput | None = None
    message_factory: MessageFactory | None = None
    model_name: str | None = None
    tool_options: List[ToolDefinitionInput] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: RespFormat | None = None
    interrupt_flag: asyncio.Event | None = None

    def __post_init__(self) -> None:
        """校验请求对象的必要字段。

        Raises:
            ValueError: 当 `task_name` 为空，或 `prompt` 与 `message_factory`
                的组合非法时抛出。
        """
        self.task_name = self.task_name.strip()
        self.session_id = str(self.session_id or "").strip()
        if not self.task_name:
            raise ValueError("`task_name` 不能为空")
        has_prompt = self.prompt is not None
        has_message_factory = self.message_factory is not None
        if has_prompt == has_message_factory:
            raise ValueError("`prompt` 与 `message_factory` 必须且只能提供一个")


@dataclass(slots=True)
class LLMResponseResult(BaseDataModel):
    """单次 LLM 响应结果。"""

    response: str = field(default_factory=str)
    reasoning: str = field(default_factory=str)
    model_name: str = field(default_factory=str)
    tool_calls: List[ToolCall] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0


@dataclass(slots=True)
class LLMServiceResult(BaseDataModel):
    """LLM 服务层统一响应对象。"""

    success: bool = False
    completion: LLMResponseResult = field(default_factory=LLMResponseResult)
    error: str | None = None

    @classmethod
    def from_response_result(cls, completion: LLMResponseResult) -> "LLMServiceResult":
        """从单次 LLM 响应结果构建服务响应。

        Args:
            completion: 单次 LLM 响应结果。

        Returns:
            LLMServiceResult: 标记为成功的服务响应对象。
        """
        return cls(
            success=True,
            completion=completion,
            error=None,
        )

    @classmethod
    def from_error(cls, error_message: str, error_detail: str | None = None) -> "LLMServiceResult":
        """构建失败的服务响应对象。

        Args:
            error_message: 对上层展示的错误消息。
            error_detail: 底层错误详情。

        Returns:
            LLMServiceResult: 标记为失败的服务响应对象。
        """
        return cls(
            success=False,
            completion=LLMResponseResult(response=error_message),
            error=error_detail or error_message,
        )

    def to_capability_payload(self) -> Dict[str, Any]:
        """转换为插件能力层可直接返回的结构。

        Returns:
            Dict[str, Any]: 标准化后的能力返回值。
        """
        payload: Dict[str, Any] = {
            "success": self.success,
            "response": self.completion.response,
            "reasoning": self.completion.reasoning,
            "model_name": self.completion.model_name,
            "prompt_tokens": self.completion.prompt_tokens,
            "completion_tokens": self.completion.completion_tokens,
            "total_tokens": self.completion.total_tokens,
            "prompt_cache_hit_tokens": self.completion.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.completion.prompt_cache_miss_tokens,
        }
        if self.completion.tool_calls is not None:
            payload["tool_calls"] = [
                {
                    "id": tool_call.call_id,
                    "function": {
                        "name": tool_call.func_name,
                        "arguments": tool_call.args or {},
                    },
                    **({"extra_content": tool_call.extra_content} if tool_call.extra_content else {}),
                }
                for tool_call in self.completion.tool_calls
            ]
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(slots=True)
class LLMGenerationOptions(BaseDataModel):
    """LLM 文本生成选项。"""

    temperature: float | None = None
    max_tokens: int | None = None
    model_name: str | None = None
    tool_options: List[ToolDefinitionInput] | None = None
    response_format: RespFormat | None = None
    interrupt_flag: asyncio.Event | None = None
    raise_when_empty: bool = True


@dataclass(slots=True)
class LLMImageOptions(BaseDataModel):
    """LLM 图像理解选项。"""

    temperature: float | None = None
    max_tokens: int | None = None
    interrupt_flag: asyncio.Event | None = None


@dataclass(slots=True)
class LLMAudioTranscriptionResult(BaseDataModel):
    """LLM 音频转写结果。"""

    text: str | None = None


@dataclass(slots=True)
class LLMEmbeddingResult(BaseDataModel):
    """LLM 向量生成结果。"""

    embedding: List[float] = field(default_factory=list)
    model_name: str = field(default_factory=str)
    model_identifier: str = field(default_factory=str)
    api_provider: str = field(default_factory=str)


__all__ = [
    "LLMAudioTranscriptionResult",
    "LLMEmbeddingResult",
    "LLMGenerationOptions",
    "LLMImageOptions",
    "LLMResponseResult",
    "LLMServiceRequest",
    "LLMServiceResult",
    "MessageFactory",
    "PromptInput",
    "PromptMessage",
]
