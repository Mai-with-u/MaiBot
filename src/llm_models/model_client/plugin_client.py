from datetime import datetime
from typing import Any, Dict, List

import uuid

from src.common.logger import get_logger
from src.config.model_configs import APIProvider
from src.llm_models.generation_diagnostics import sanitize_generation_diagnostic
from src.llm_models.exceptions import RespParseException
from src.llm_models.model_client.base_client import (
    APIResponse,
    AudioTranscriptionRequest,
    BaseClient,
    EmbeddingRequest,
    ResponseRequest,
    UsageRecord,
)
from src.llm_models.payload_content.context_item import (
    CONTEXT_ITEM_SCHEMA_VERSION,
    ContextToolCall,
    build_portable_output_items,
)
from src.llm_models.payload_content.context_protocol import ContextProtocolMode, validate_context_items
from src.llm_models.request_snapshot import (
    deserialize_context_item_snapshot,
    deserialize_tool_calls_snapshot,
    serialize_audio_request_snapshot,
    serialize_embedding_request_snapshot,
    serialize_response_request_snapshot,
)

logger = get_logger("plugin_llm_client")


class PluginLLMClient(BaseClient):
    """通过插件 Runner RPC 调用第三方 LLM Provider 的客户端代理。"""

    def __init__(
        self,
        api_provider: APIProvider,
        supervisor: Any,
        plugin_id: str,
        client_type: str,
    ) -> None:
        """初始化插件 LLM Provider 客户端代理。

        Args:
            api_provider: 当前 API Provider 配置。
            supervisor: 拥有目标插件的 Supervisor。
            plugin_id: 目标插件 ID。
            client_type: 目标客户端类型。
        """
        super().__init__(api_provider)
        self._supervisor = supervisor
        self._plugin_id = plugin_id
        self._client_type = client_type

    async def get_response(self, request: ResponseRequest) -> APIResponse:
        """获取对话响应。

        Args:
            request: 统一响应请求对象。

        Returns:
            APIResponse: 统一响应对象。

        Raises:
            RespParseException: 插件返回内容无法转换为统一响应时抛出。
        """
        if request.stream_response_handler is not None or request.async_response_parser is not None:
            raise RespParseException(message="插件 LLM Provider 暂不支持 Host 侧自定义流式处理器或响应解析器")
        validate_context_items(request.context_items, ContextProtocolMode.REQUEST_CONTEXT)
        payload = serialize_response_request_snapshot(request)
        result = await self._invoke_provider("response", payload)
        response = self._build_api_response(
            result,
            request.model_info.name,
            request.model_info.api_provider,
            logical_turn_id=request.logical_turn_id,
            require_output_items=True,
        )
        validate_context_items(response.output_items, ContextProtocolMode.MODEL_OUTPUT)
        response.bind_logical_turn(request.logical_turn_id)
        response.attach_generation_trace(
            provider=self.api_provider.name,
            endpoint=self.api_provider.base_url,
            model=request.model_info.model_identifier,
            response_id=str(result.get("response_id") or "").strip() or None,
            status=str(result.get("status") or "completed"),
        )
        response.wire_protocol = "plugin_context_items"
        response.request_wire_payload = sanitize_generation_diagnostic(
            {
                **payload,
                "api_provider": self._build_api_provider_snapshot(),
            }
        )
        return response

    async def get_embedding(self, request: EmbeddingRequest) -> APIResponse:
        """获取文本嵌入。

        Args:
            request: 统一嵌入请求对象。

        Returns:
            APIResponse: 嵌入响应。
        """
        result = await self._invoke_provider("embedding", serialize_embedding_request_snapshot(request))
        return self._build_api_response(result, request.model_info.name, request.model_info.api_provider)

    async def get_audio_transcriptions(self, request: AudioTranscriptionRequest) -> APIResponse:
        """获取音频转录。

        Args:
            request: 统一音频转录请求对象。

        Returns:
            APIResponse: 音频转录响应。
        """
        result = await self._invoke_provider("audio_transcription", serialize_audio_request_snapshot(request))
        return self._build_api_response(result, request.model_info.name, request.model_info.api_provider)

    def get_support_image_formats(self) -> List[str]:
        """获取支持的图片格式。

        Returns:
            List[str]: 插件 Provider 默认接收的图片格式列表。
        """
        return ["jpeg", "jpg", "png", "webp"]

    def _build_api_provider_snapshot(self) -> Dict[str, Any]:
        """构建可传给插件 Provider 的 API Provider 配置快照。

        Returns:
            Dict[str, Any]: 包含认证信息的 API Provider 配置字典。
        """
        return self.api_provider.model_dump(mode="json")

    async def _invoke_provider(self, operation: str, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """调用插件 Provider。

        Args:
            operation: 请求操作类型。
            request_payload: 已序列化的内部请求。

        Returns:
            Dict[str, Any]: 插件返回的响应字典。

        Raises:
            RespParseException: 插件调用失败或返回格式不合法时抛出。
        """
        try:
            response = await self._supervisor.invoke_llm_provider(
                plugin_id=self._plugin_id,
                client_type=self._client_type,
                operation=operation,
                request={
                    **request_payload,
                    "api_provider": self._build_api_provider_snapshot(),
                },
                timeout_ms=max(1000, int(self.api_provider.timeout) * 1000),
            )
        except Exception as exc:
            raise RespParseException(message=f"插件 LLM Provider RPC 调用失败: {exc}") from exc
        if response.error:
            raise RespParseException(message=str(response.error.get("message", "插件 LLM Provider 调用失败")))

        payload = response.payload if isinstance(response.payload, dict) else {}
        success = bool(payload.get("success", False))
        result = payload.get("result")
        if not success:
            raise RespParseException(message=str(result or "插件 LLM Provider 返回失败"))
        if not isinstance(result, dict):
            raise RespParseException(message="插件 LLM Provider 返回值必须是字典")
        return result

    @staticmethod
    def _build_usage_record(raw_usage: Any, model_name: str, provider_name: str) -> UsageRecord | None:
        """从插件返回值恢复使用量记录。

        Args:
            raw_usage: 插件返回的使用量字段。
            model_name: 当前模型名称。
            provider_name: 当前 Provider 名称。

        Returns:
            UsageRecord | None: 可挂载到 APIResponse 的使用量记录；缺失时返回 ``None``。
        """
        if not isinstance(raw_usage, dict):
            return None
        return UsageRecord(
            model_name=str(raw_usage.get("model_name") or model_name),
            provider_name=str(raw_usage.get("provider_name") or provider_name),
            prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
            completion_tokens=int(raw_usage.get("completion_tokens") or 0),
            total_tokens=int(raw_usage.get("total_tokens") or 0),
            prompt_cache_hit_tokens=int(raw_usage.get("prompt_cache_hit_tokens") or 0),
            prompt_cache_miss_tokens=int(raw_usage.get("prompt_cache_miss_tokens") or 0),
        )

    @staticmethod
    def _build_api_response(
        result: Dict[str, Any],
        model_name: str,
        provider_name: str,
        *,
        logical_turn_id: str | None = None,
        require_output_items: bool = False,
    ) -> APIResponse:
        """从插件返回值恢复统一 APIResponse。

        Args:
            result: 插件返回的响应字典。
            model_name: 当前模型名称。
            provider_name: 当前 Provider 名称。

        Returns:
            APIResponse: 统一响应对象。
        """
        raw_embedding = result.get("embedding")
        embedding = [float(item) for item in raw_embedding] if isinstance(raw_embedding, list) else None
        active_logical_turn_id = logical_turn_id or uuid.uuid4().hex
        raw_output_items = result.get("output_items")
        if "output_items" in result and not isinstance(raw_output_items, list):
            raise RespParseException(message="插件 LLM Provider output_items 必须是列表")
        if isinstance(raw_output_items, list):
            item_schema_version = result.get("item_schema_version")
            if item_schema_version != CONTEXT_ITEM_SCHEMA_VERSION:
                raise RespParseException(
                    message=(
                        "插件 LLM Provider item_schema_version 不匹配: "
                        f"{item_schema_version!r}（当前为 {CONTEXT_ITEM_SCHEMA_VERSION}）"
                    )
                )
            output_items = []
            timestamp = datetime.now()
            for raw_item in raw_output_items:
                if not isinstance(raw_item, dict):
                    raise RespParseException(message="插件 LLM Provider output_items 项必须是字典")
                item_payload = {
                    **raw_item,
                    "meta": {
                        "item_id": uuid.uuid4().hex,
                        "logical_turn_id": active_logical_turn_id,
                        "timestamp": timestamp.isoformat(),
                    },
                }
                try:
                    output_items.append(deserialize_context_item_snapshot(item_payload))
                except (TypeError, ValueError) as exc:
                    raise RespParseException(message=f"插件 LLM Provider output_items 非法: {exc}") from exc
            try:
                validate_context_items(output_items, ContextProtocolMode.MODEL_OUTPUT)
            except ValueError as exc:
                raise RespParseException(message=f"插件 LLM Provider output_items 关系非法: {exc}") from exc
        else:
            if require_output_items:
                logger.warning(
                    "插件 LLM Provider 返回旧版 content/reasoning/tool_calls 标量协议；"
                    "该兼容入口将在下一个 Item schema 版本删除"
                )
            content = result.get("content")
            if not isinstance(content, str):
                content = result.get("response")
            reasoning_content = result.get("reasoning_content")
            if not isinstance(reasoning_content, str):
                reasoning_content = result.get("reasoning")
            tool_calls = deserialize_tool_calls_snapshot(result.get("tool_calls"))
            context_tool_calls = [
                ContextToolCall.create(
                    call_id=tool_call.call_id,
                    func_name=tool_call.func_name,
                    args=tool_call.args,
                    extra_content=tool_call.extra_content,
                )
                for tool_call in tool_calls
            ]
            output_items = list(
                build_portable_output_items(
                    content=content if isinstance(content, str) else None,
                    reasoning=reasoning_content if isinstance(reasoning_content, str) else None,
                    tool_calls=context_tool_calls,
                    logical_turn_id=active_logical_turn_id,
                )
            )
        return APIResponse(
            output_items=tuple(output_items),
            embedding=embedding,
            usage=PluginLLMClient._build_usage_record(result.get("usage"), model_name, provider_name),
            raw_data=result.get("raw_data", result),
            provider_response=sanitize_generation_diagnostic(result),
        )
