from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Set, Tuple, Type

import asyncio

from src.common.logger import get_logger
from src.config.config import config_manager
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.payload_content.message import Message
from src.llm_models.payload_content.resp_format import RespFormat
from src.llm_models.payload_content.tool_option import ToolCall, ToolOption

logger = get_logger("model_client_registry")


@dataclass
class UsageRecord:
    """
    使用记录类
    """

    model_name: str
    """模型名称"""

    provider_name: str
    """提供商名称"""

    prompt_tokens: int
    """提示token数"""

    completion_tokens: int
    """完成token数"""

    total_tokens: int
    """总token数"""

    prompt_cache_hit_tokens: int = 0
    """输入中缓存命中的 token 数"""

    prompt_cache_miss_tokens: int = 0
    """输入中缓存未命中的 token 数"""


@dataclass
class APIResponse:
    """
    API响应类
    """

    content: str | None = None
    """响应内容"""

    reasoning_content: str | None = None
    """推理内容"""

    tool_calls: List[ToolCall] | None = None
    """工具调用 [(工具名称, 工具参数), ...]"""

    embedding: List[float] | None = None
    """嵌入向量"""

    usage: UsageRecord | None = None
    """使用情况 (prompt_tokens, completion_tokens, total_tokens)"""

    raw_data: Any = None
    """响应原始数据"""


UsageTuple = Tuple[int, ...]
"""统一的使用量元组，顺序为 `(prompt_tokens, completion_tokens, total_tokens, prompt_cache_hit_tokens, prompt_cache_miss_tokens)`。"""

StreamResponseHandler = Callable[
    [Any, asyncio.Event | None],
    Coroutine[Any, Any, Tuple["APIResponse", UsageTuple | None]],
]
"""统一的流式响应处理函数类型。"""

ResponseParser = Callable[[Any], Tuple["APIResponse", UsageTuple | None]]
"""统一的非流式响应解析函数类型。"""


@dataclass(slots=True)
class ResponseRequest:
    """统一的文本/多模态响应请求。"""

    model_info: ModelInfo
    message_list: List[Message]
    tool_options: List[ToolOption] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    response_format: RespFormat | None = None
    stream_response_handler: StreamResponseHandler | None = None
    async_response_parser: ResponseParser | None = None
    interrupt_flag: asyncio.Event | None = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def copy_with(self, **changes: Any) -> "ResponseRequest":
        """基于当前请求创建一个带局部变更的新请求。

        Args:
            **changes: 需要覆盖的字段值。

        Returns:
            ResponseRequest: 复制后的请求对象。
        """
        payload = {
            "model_info": self.model_info,
            "message_list": list(self.message_list),
            "tool_options": None if self.tool_options is None else list(self.tool_options),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "response_format": self.response_format,
            "stream_response_handler": self.stream_response_handler,
            "async_response_parser": self.async_response_parser,
            "interrupt_flag": self.interrupt_flag,
            "extra_params": dict(self.extra_params),
        }
        payload.update(changes)
        return ResponseRequest(**payload)


@dataclass(slots=True)
class EmbeddingRequest:
    """统一的嵌入请求。"""

    model_info: ModelInfo
    embedding_input: str
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AudioTranscriptionRequest:
    """统一的音频转录请求。"""

    model_info: ModelInfo
    audio_base64: str
    max_tokens: int | None = None
    extra_params: Dict[str, Any] = field(default_factory=dict)


ClientRequest = ResponseRequest | EmbeddingRequest | AudioTranscriptionRequest
"""统一客户端请求类型。"""


class BaseClient(ABC):
    """
    基础客户端
    """

    api_provider: APIProvider

    def __init__(self, api_provider: APIProvider) -> None:
        """初始化基础客户端。

        Args:
            api_provider: API 提供商配置。
        """
        self.api_provider = api_provider

    @abstractmethod
    async def get_response(self, request: ResponseRequest) -> APIResponse:
        """获取对话响应。

        Args:
            request: 统一响应请求对象。

        Returns:
            APIResponse: 统一响应对象。
        """
        raise NotImplementedError("'get_response' method should be overridden in subclasses")

    @abstractmethod
    async def get_embedding(self, request: EmbeddingRequest) -> APIResponse:
        """获取文本嵌入。

        Args:
            request: 统一嵌入请求对象。

        Returns:
            APIResponse: 嵌入响应。
        """
        raise NotImplementedError("'get_embedding' method should be overridden in subclasses")

    @abstractmethod
    async def get_audio_transcriptions(self, request: AudioTranscriptionRequest) -> APIResponse:
        """获取音频转录。

        Args:
            request: 统一音频转录请求对象。

        Returns:
            APIResponse: 音频转录响应。
        """
        raise NotImplementedError("'get_audio_transcriptions' method should be overridden in subclasses")

    @abstractmethod
    def get_support_image_formats(self) -> List[str]:
        """获取支持的图片格式。

        Returns:
            List[str]: 支持的图片格式列表。
        """
        raise NotImplementedError("'get_support_image_formats' method should be overridden in subclasses")


ClientFactory = Callable[[APIProvider], BaseClient]
"""根据 APIProvider 创建客户端实例的工厂函数。"""


@dataclass(slots=True)
class ClientProviderRegistration:
    """LLM Provider 客户端类型注册信息。"""

    client_type: str
    """客户端类型标识，对应模型配置中的 `api_providers[].client_type`。"""

    factory: ClientFactory
    """客户端实例工厂。"""

    owner_plugin_id: str | None = None
    """拥有该客户端类型的插件 ID；主程序内置类型为 ``None``。"""

    version: str = "1.0.0"
    """Provider 实现版本。"""

    description: str = ""
    """Provider 描述文本。"""

    builtin: bool = False
    """是否为主程序内置 Provider。"""


class ClientRegistry:
    """客户端注册表。"""

    def __init__(self) -> None:
        """初始化注册表并绑定配置重载回调。"""
        self.client_registry: Dict[str, ClientProviderRegistration] = {}
        """APIProvider.client_type -> Provider 注册信息映射表。"""
        self.client_instance_cache: Dict[Tuple[asyncio.AbstractEventLoop | None, str], BaseClient] = {}
        """(事件循环, APIProvider.name) -> BaseClient 的映射表。"""
        self._owner_client_types: Dict[str, Set[str]] = {}
        """插件 ID -> 该插件拥有的 client_type 集合。"""
        config_manager.register_reload_callback(self.clear_client_instance_cache)

    def register_client_class(self, client_type: str) -> Callable[[Type[BaseClient]], Type[BaseClient]]:
        """注册主程序内置 API 客户端类。

        Args:
            client_type: 客户端类型标识。

        Returns:
            Callable[[Type[BaseClient]], Type[BaseClient]]: 装饰器函数。
        """

        def decorator(cls: Type[BaseClient]) -> Type[BaseClient]:
            """将内置客户端类注册到全局客户端注册表。

            Args:
                cls: 待注册的客户端类。

            Returns:
                Type[BaseClient]: 原始客户端类。
            """
            if not issubclass(cls, BaseClient):
                raise TypeError(f"{cls.__name__} is not a subclass of BaseClient")
            self.register_provider(
                ClientProviderRegistration(
                    client_type=client_type,
                    factory=cls,
                    builtin=True,
                )
            )
            return cls

        return decorator

    @staticmethod
    def _normalize_client_type(client_type: str) -> str:
        """规范化客户端类型标识。

        Args:
            client_type: 原始客户端类型标识。

        Returns:
            str: 去除首尾空白后的客户端类型标识。

        Raises:
            ValueError: 当客户端类型为空时抛出。
        """
        normalized_client_type = str(client_type or "").strip()
        if not normalized_client_type:
            raise ValueError("client_type 不能为空")
        return normalized_client_type

    def register_provider(self, registration: ClientProviderRegistration) -> None:
        """注册单个客户端类型。

        Args:
            registration: Provider 注册信息。

        Raises:
            ValueError: 当客户端类型冲突时抛出。
        """
        client_type = self._normalize_client_type(registration.client_type)
        existing = self.client_registry.get(client_type)
        if existing is not None and existing.owner_plugin_id != registration.owner_plugin_id:
            raise ValueError(
                f"LLM Provider client_type 冲突: {client_type} 已由 {existing.owner_plugin_id or 'host'} 注册"
            )

        self.client_registry[client_type] = ClientProviderRegistration(
            client_type=client_type,
            factory=registration.factory,
            owner_plugin_id=registration.owner_plugin_id,
            version=registration.version,
            description=registration.description,
            builtin=registration.builtin,
        )
        if registration.owner_plugin_id:
            self._owner_client_types.setdefault(registration.owner_plugin_id, set()).add(client_type)
        self.clear_client_instance_cache_by_client_type(client_type)

    def validate_plugin_provider_replacement(self, plugin_id: str, client_types: List[str]) -> None:
        """校验插件 Provider 替换是否会造成运行时冲突。

        Args:
            plugin_id: 目标插件 ID。
            client_types: 插件即将注册的客户端类型列表。

        Raises:
            ValueError: 当客户端类型为空、重复或与其他 owner 冲突时抛出。
        """
        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            raise ValueError("plugin_id 不能为空")

        normalized_client_types = [self._normalize_client_type(client_type) for client_type in client_types]
        duplicate_client_types = sorted(
            {
                client_type
                for client_type in normalized_client_types
                if normalized_client_types.count(client_type) > 1
            }
        )
        if duplicate_client_types:
            raise ValueError(f"插件 {normalized_plugin_id} 重复声明 LLM Provider: {', '.join(duplicate_client_types)}")

        for client_type in normalized_client_types:
            existing = self.client_registry.get(client_type)
            if existing is None or existing.owner_plugin_id == normalized_plugin_id:
                continue
            raise ValueError(
                f"LLM Provider client_type 冲突: {client_type} 已由 {existing.owner_plugin_id or 'host'} 注册"
            )

    def replace_plugin_providers(
        self,
        plugin_id: str,
        registrations: List[ClientProviderRegistration],
    ) -> None:
        """原子替换一个插件拥有的全部 Provider 注册。

        Args:
            plugin_id: 目标插件 ID。
            registrations: 插件当前上报的 Provider 注册列表。

        Raises:
            ValueError: 当注册信息不合法或存在冲突时抛出。
        """
        normalized_plugin_id = str(plugin_id or "").strip()
        self.validate_plugin_provider_replacement(
            normalized_plugin_id,
            [registration.client_type for registration in registrations],
        )
        self.unregister_plugin_providers(normalized_plugin_id)
        for registration in registrations:
            self.register_provider(
                ClientProviderRegistration(
                    client_type=registration.client_type,
                    factory=registration.factory,
                    owner_plugin_id=normalized_plugin_id,
                    version=registration.version,
                    description=registration.description,
                    builtin=False,
                )
            )

    def unregister_plugin_providers(self, plugin_id: str) -> int:
        """注销一个插件拥有的全部 Provider 注册。

        Args:
            plugin_id: 目标插件 ID。

        Returns:
            int: 被注销的客户端类型数量。
        """
        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            return 0

        client_types = self._owner_client_types.pop(normalized_plugin_id, set())
        removed_count = 0
        for client_type in client_types:
            registration = self.client_registry.get(client_type)
            if registration is None or registration.owner_plugin_id != normalized_plugin_id:
                continue
            self.client_registry.pop(client_type, None)
            self.clear_client_instance_cache_by_client_type(client_type)
            removed_count += 1
        return removed_count

    def clear_client_instance_cache_by_client_type(self, client_type: str) -> None:
        """清理指定客户端类型对应的客户端实例缓存。

        Args:
            client_type: 需要清理缓存的客户端类型。
        """
        normalized_client_type = str(client_type or "").strip()
        if not normalized_client_type:
            return

        stale_cache_keys = [
            cache_key
            for cache_key, client in self.client_instance_cache.items()
            if client.api_provider.client_type == normalized_client_type
        ]
        for cache_key in stale_cache_keys:
            self.client_instance_cache.pop(cache_key, None)

    @staticmethod
    def _get_client_cache_key(api_provider: APIProvider) -> Tuple[asyncio.AbstractEventLoop | None, str]:
        """生成按事件循环隔离的客户端缓存键。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        return loop, api_provider.name

    def get_client_class_instance(self, api_provider: APIProvider, force_new: bool = False) -> BaseClient:
        """获取注册的 API 客户端实例。

        Args:
            api_provider: APIProvider 实例。
            force_new: 是否强制创建新实例。

        Returns:
            BaseClient: 注册的 API 客户端实例。
        """
        from . import ensure_client_type_loaded

        ensure_client_type_loaded(api_provider.client_type)

        # 如果强制创建新实例，直接创建不使用缓存
        if force_new:
            if registration := self.client_registry.get(api_provider.client_type):
                return registration.factory(api_provider)
            raise KeyError(f"'{api_provider.client_type}' 类型的 Client 未注册")

        # 异步 HTTP 客户端绑定创建它的事件循环，同一循环内按 Provider 复用。
        cache_key = self._get_client_cache_key(api_provider)
        if cache_key not in self.client_instance_cache:
            if registration := self.client_registry.get(api_provider.client_type):
                self.client_instance_cache[cache_key] = registration.factory(api_provider)
            else:
                raise KeyError(f"'{api_provider.client_type}' 类型的 Client 未注册")
        return self.client_instance_cache[cache_key]

    def clear_client_instance_cache(self) -> None:
        """清空客户端实例缓存。"""
        self.client_instance_cache.clear()
        logger.info("检测到配置重载，已清空LLM客户端实例缓存")


client_registry = ClientRegistry()
