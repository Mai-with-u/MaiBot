from datetime import datetime
from typing import Any, Dict, List

import base64

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("plugin_runtime.integration")


def _get_nested_config_value(source: Any, key: str, default: Any = None) -> Any:
    """从嵌套对象或字典中读取配置值。

    Args:
        source: 配置对象或字典。
        key: 以点号分隔的路径。
        default: 未命中时返回的默认值。

    Returns:
        Any: 命中的值；读取失败时返回默认值。
    """
    current = source
    try:
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            if hasattr(current, part):
                current = getattr(current, part)
                continue
            raise KeyError(part)
        return current
    except Exception:
        return default


def _normalize_prompt_arg(prompt: Any) -> str | List[Dict[str, Any]]:
    """校验并规范化插件传入的提示参数。

    Args:
        prompt: 原始提示参数。

    Returns:
        str | List[Dict[str, Any]]: 规范化后的提示输入。

    Raises:
        ValueError: 提示参数缺失或结构不受支持时抛出。
    """
    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError("缺少必要参数 prompt")
        return prompt
    if isinstance(prompt, list) and prompt:
        for index, prompt_message in enumerate(prompt, start=1):
            if not isinstance(prompt_message, dict):
                raise ValueError(f"prompt 第 {index} 项必须为字典")
        return prompt
    raise ValueError("缺少必要参数 prompt")


def _normalize_embedding_text_arg(args: Dict[str, Any]) -> str | None:
    """校验并规范化插件传入的单条嵌入文本。"""

    text = args.get("text")
    if text is None:
        text = args.get("input")
    if not isinstance(text, str):
        return None

    normalized_text = text.strip()
    return normalized_text or None


def _normalize_embedding_texts_arg(args: Dict[str, Any]) -> List[str]:
    """校验并规范化插件传入的批量嵌入文本。"""

    texts = args.get("texts")
    if texts is None:
        texts = args.get("inputs")
    if not isinstance(texts, list):
        return []

    normalized_texts: List[str] = []
    for index, text in enumerate(texts, start=1):
        if not isinstance(text, str):
            raise ValueError(f"texts 第 {index} 项必须为字符串")
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError(f"texts 第 {index} 项不能为空")
        normalized_texts.append(normalized_text)
    return normalized_texts


def _embedding_result_to_payload(result: Any) -> Dict[str, Any]:
    """将 Embedding 服务结果转换为 capability 返回结构。"""

    return {
        "embedding": list(result.embedding),
        "model_name": result.model_name,
    }


def _normalize_audio_base64_arg(args: Dict[str, Any]) -> str | None:
    """校验并规范化插件传入的音频 Base64。"""

    audio_base64 = (
        str(args.get("audio_base64") or "").strip()
        or str(args.get("voice_base64") or "").strip()
        or str(args.get("base64") or "").strip()
    )
    if not audio_base64:
        return None

    if audio_base64.startswith("data:") and ";base64," in audio_base64:
        audio_base64 = audio_base64.split(";base64,", maxsplit=1)[1].strip()

    try:
        base64.b64decode(audio_base64, validate=True)
    except Exception as exc:
        raise ValueError("音频 Base64 数据不合法") from exc
    return audio_base64


def _normalize_context_segment(raw_segment: Any) -> Dict[str, Any] | None:
    """将插件传入的上下文消息段规范化为宿主消息段结构。"""

    if not isinstance(raw_segment, dict):
        return None

    segment = dict(raw_segment)
    segment_type = str(segment.get("type") or "").strip().lower()
    if not segment_type:
        return None
    segment["type"] = segment_type

    if "data" not in segment and "content" in segment:
        segment["data"] = segment.get("content")

    if segment_type in {"image", "emoji", "voice"}:
        binary_base64 = (
            str(segment.get("binary_data_base64") or "").strip()
            or str(segment.get("base64") or "").strip()
            or str(segment.get("image_base64") or "").strip()
            or str(segment.get("emoji_base64") or "").strip()
        )
        if binary_base64:
            segment["binary_data_base64"] = binary_base64
            if "data" not in segment or str(segment.get("data") or "").strip() == binary_base64:
                segment["data"] = str(segment.get("description") or "")

    return segment


def _normalize_context_segments(raw_segments: Any) -> List[Dict[str, Any]]:
    """规范化插件传入的一组上下文消息段。"""

    if not isinstance(raw_segments, list):
        return []

    segments: List[Dict[str, Any]] = []
    for raw_segment in raw_segments:
        segment = _normalize_context_segment(raw_segment)
        if segment is not None:
            segments.append(segment)
    return segments


class RuntimeCoreCapabilityMixin:
    """插件运行时的核心能力混入。"""

    async def _cap_maisaka_context_append(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定 Maisaka 聊天运行时插入一条图文上下文消息。"""

        del capability

        stream_id = str(args.get("stream_id") or args.get("chat_id") or "").strip()
        if not stream_id:
            return {"success": False, "error": "缺少必要参数 stream_id 或 chat_id"}

        segments = _normalize_context_segments(args.get("segments"))
        if not segments:
            return {"success": False, "error": "缺少有效的 segments"}

        try:
            from src.chat.heart_flow.heartflow_manager import heartflow_manager
            from src.maisaka.context.messages import SessionBackedMessage
            from src.maisaka.context.message_adapter import build_visible_text_from_sequence
            from src.plugin_runtime.host.message_utils import PluginMessageUtils

            runtime = await heartflow_manager.get_or_create_heartflow_chat(stream_id)
            message_sequence = PluginMessageUtils._message_sequence_from_dict(segments)
            visible_text = str(args.get("visible_text") or "").strip()
            if not visible_text:
                visible_text = build_visible_text_from_sequence(message_sequence)
            if not visible_text:
                visible_text = "[插件上下文消息]"

            source_kind = str(args.get("source_kind") or f"plugin:{plugin_id}").strip() or f"plugin:{plugin_id}"
            context_message = SessionBackedMessage(
                raw_message=message_sequence,
                visible_text=visible_text,
                timestamp=datetime.now(),
                message_id=str(args.get("message_id") or "").strip() or None,
                source_kind=source_kind,
            )
            runtime._chat_history.append(context_message)
            return {
                "success": True,
                "index": len(runtime._chat_history) - 1,
                "stream_id": stream_id,
                "visible_text": visible_text,
                "source_kind": source_kind,
            }
        except Exception as exc:
            logger.error(f"[cap.maisaka.context.append] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    async def _cap_maisaka_proactive_trigger(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """请求 Maisaka 基于指定聊天流主动处理一轮对话。"""

        del capability

        stream_id = str(args.get("stream_id") or args.get("chat_id") or args.get("session_id") or "").strip()
        intent = str(args.get("intent") or args.get("prompt") or args.get("text") or "").strip()
        if not stream_id:
            return {"success": False, "error": "缺少必要参数 stream_id"}
        if not intent:
            return {"success": False, "error": "缺少必要参数 intent"}

        try:
            from src.chat.heart_flow.heartflow_manager import heartflow_manager
            from src.chat.message_receive.chat_manager import chat_manager

            chat_session = chat_manager.get_existing_session_by_session_id(stream_id)
            if chat_session is None:
                return {"success": False, "error": f"未找到已存在的聊天流: {stream_id}"}

            runtime = await heartflow_manager.get_or_create_heartflow_chat(stream_id)
            result = await runtime.enqueue_proactive_task(
                plugin_id=plugin_id,
                intent=intent,
                reason=str(args.get("reason") or "").strip(),
                priority=str(args.get("priority") or "").strip(),
                metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else None,
            )
            return {"success": True, **result}
        except Exception as exc:
            logger.error(f"[cap.maisaka.proactive.trigger] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _build_send_result(args: Dict[str, Any], sent_message: Any = None, error: str = "") -> Dict[str, Any]:
        """按调用方选择返回兼容布尔结果或包含平台消息 ID 的详细结果。"""

        sent = sent_message is not None
        result: Dict[str, Any] = {"success": sent}
        if bool(args.get("return_details", False)):
            message_id = str(sent_message.platform_message_id or "").strip() if sent else ""
            result.update({"sent": sent, "message_id": message_id or None})
        if error:
            result["error"] = error
        return result

    async def _cap_send_text(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送文本消息。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 能力执行结果。
        """
        del plugin_id, capability
        from src.services import send_service as send_api

        text = str(args.get("text", ""))
        stream_id = str(args.get("stream_id", ""))
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not text or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 text 或 stream_id")

        try:
            sent_message = await send_api.text_to_stream_with_message(
                text=text,
                stream_id=stream_id,
                typing=bool(args.get("typing", False)),
                set_reply=bool(args.get("set_reply", False)),
                storage_message=bool(args.get("storage_message", True)),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.text] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    async def _cap_send_emoji(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送表情图片。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 能力执行结果。
        """
        del plugin_id, capability
        from src.services import send_service as send_api

        emoji_base64 = str(args.get("emoji_base64", ""))
        stream_id = str(args.get("stream_id", ""))
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not emoji_base64 or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 emoji_base64 或 stream_id")

        try:
            sent_message = await send_api.emoji_to_stream_with_message(
                emoji_base64=emoji_base64,
                stream_id=stream_id,
                storage_message=bool(args.get("storage_message", True)),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.emoji] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    async def _cap_send_image(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送图片。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 能力执行结果。
        """
        del plugin_id, capability
        from src.services import send_service as send_api

        image_base64 = str(args.get("image_base64", ""))
        stream_id = str(args.get("stream_id", ""))
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not image_base64 or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 image_base64 或 stream_id")

        try:
            sent_message = await send_api.image_to_stream_with_message(
                image_base64=image_base64,
                stream_id=stream_id,
                storage_message=bool(args.get("storage_message", True)),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.image] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    @staticmethod
    def _normalize_plugin_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
        """将 SDK 侧常见的 content 字段归一化为 Host 消息组件字典。"""

        segment_type = str(segment.get("type") or "").strip().lower()
        if segment_type == "text":
            return {"type": "text", "data": str(segment.get("data") or segment.get("content") or "")}
        if segment_type in {"image", "emoji", "voice"}:
            normalized_segment = dict(segment)
            normalized_segment["type"] = segment_type
            content = str(segment.get("content") or "").strip()
            if content and not normalized_segment.get("binary_data_base64") and not normalized_segment.get("hash"):
                normalized_segment["binary_data_base64"] = content
            normalized_segment.setdefault("data", str(segment.get("data") or ""))
            return normalized_segment
        return dict(segment)

    @staticmethod
    def _normalize_plugin_segments(segments: Any) -> List[Dict[str, Any]]:
        """归一化 SDK 传入的消息段列表。"""

        if not isinstance(segments, list):
            return []
        return [
            RuntimeCoreCapabilityMixin._normalize_plugin_segment(segment)
            for segment in segments
            if isinstance(segment, dict)
        ]

    async def _cap_send_hybrid(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送图文混合消息。"""

        del plugin_id, capability
        from src.plugin_runtime.host.message_utils import PluginMessageUtils
        from src.services import send_service as send_api

        stream_id = str(args.get("stream_id", ""))
        segments = self._normalize_plugin_segments(args.get("segments") or args.get("parts"))
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not segments or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 segments 或 stream_id")

        try:
            message_sequence = PluginMessageUtils._message_sequence_from_dict(segments)
            sent_message = await send_api.custom_reply_set_to_stream_with_message(
                reply_set=message_sequence,
                stream_id=stream_id,
                processed_plain_text=str(args.get("processed_plain_text", "")),
                typing=bool(args.get("typing", False)),
                storage_message=bool(args.get("storage_message", True)),
                show_log=bool(args.get("show_log", True)),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.hybrid] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    async def _cap_send_forward(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送转发消息。"""

        del plugin_id, capability
        from src.plugin_runtime.host.message_utils import PluginMessageUtils
        from src.services import send_service as send_api

        stream_id = str(args.get("stream_id", ""))
        messages = args.get("messages")
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not isinstance(messages, list) or not messages or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 messages 或 stream_id")

        forward_nodes: List[Dict[str, Any]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            raw_segments = message.get("segments") or message.get("content") or []
            segments = self._normalize_plugin_segments(raw_segments)
            if not segments:
                continue
            forward_nodes.append(
                {
                    "user_id": str(message.get("user_id") or ""),
                    "user_nickname": str(message.get("nickname") or message.get("user_nickname") or "插件消息"),
                    "user_cardname": str(message.get("user_cardname") or ""),
                    "message_id": str(message.get("message_id") or f"plugin_forward_{index}"),
                    "content": segments,
                }
            )

        if not forward_nodes:
            return self._build_send_result(args, error="messages 中缺少有效的转发节点")

        try:
            message_sequence = PluginMessageUtils._message_sequence_from_dict(
                [{"type": "forward", "data": forward_nodes}]
            )
            sent_message = await send_api.custom_reply_set_to_stream_with_message(
                reply_set=message_sequence,
                stream_id=stream_id,
                processed_plain_text=str(args.get("processed_plain_text", "[转发消息]")),
                typing=bool(args.get("typing", False)),
                storage_message=bool(args.get("storage_message", True)),
                show_log=bool(args.get("show_log", True)),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.forward] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    async def _cap_send_command(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送命令消息。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 能力执行结果。
        """
        del plugin_id, capability
        from src.services import send_service as send_api

        command = str(args.get("command", ""))
        stream_id = str(args.get("stream_id", ""))
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not command or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 command 或 stream_id")

        try:
            sent_message = await send_api.custom_to_stream_with_message(
                message_type="command",
                content=command,
                stream_id=stream_id,
                storage_message=bool(args.get("storage_message", True)),
                processed_plain_text=str(args.get("processed_plain_text", "")),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.command] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    async def _cap_send_custom(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """向指定流发送自定义消息。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 能力执行结果。
        """
        del plugin_id, capability
        from src.services import send_service as send_api

        message_type = str(args.get("message_type", "") or args.get("custom_type", ""))
        content = args.get("content")
        if content is None:
            content = args.get("data", "")
        stream_id = str(args.get("stream_id", ""))
        sync_to_maisaka_history = bool(args.get("sync_to_maisaka_history", False))
        maisaka_source_kind = str(args.get("maisaka_source_kind", "plugin_send") or "plugin_send")
        if not message_type or not stream_id:
            return self._build_send_result(args, error="缺少必要参数 message_type 或 stream_id")

        try:
            sent_message = await send_api.custom_to_stream_with_message(
                message_type=message_type,
                content=content,
                stream_id=stream_id,
                processed_plain_text=str(args.get("processed_plain_text", "")),
                typing=bool(args.get("typing", False)),
                storage_message=bool(args.get("storage_message", True)),
                sync_to_maisaka_history=sync_to_maisaka_history,
                maisaka_source_kind=maisaka_source_kind,
            )
            return self._build_send_result(args, sent_message)
        except Exception as exc:
            logger.error(f"[cap.send.custom] 执行失败: {exc}", exc_info=True)
            return self._build_send_result(args, error=str(exc))

    async def _cap_llm_generate(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """执行无工具的 LLM 生成能力。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 标准化后的 LLM 响应结构。
        """
        del capability
        from src.services import llm_service as llm_api

        try:
            prompt = _normalize_prompt_arg(args.get("prompt"))
            task_name = llm_api.resolve_task_name(str(args.get("model", "") or args.get("model_name", "")))
            result = await llm_api.generate(
                llm_api.LLMServiceRequest(
                    task_name=task_name,
                    request_type=f"plugin.{plugin_id}",
                    prompt=prompt,
                    temperature=args.get("temperature"),
                    max_tokens=args.get("max_tokens"),
                )
            )
            return result.to_capability_payload()
        except Exception as exc:
            logger.error(f"[cap.llm.generate] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    async def _cap_llm_generate_with_tools(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """执行带工具的 LLM 生成能力。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 标准化后的 LLM 响应结构。
        """
        del capability
        from src.services import llm_service as llm_api

        tool_options = args.get("tools") or args.get("tool_options")
        if tool_options is not None and not isinstance(tool_options, list):
            return {"success": False, "error": "tools 必须为列表"}

        try:
            prompt = _normalize_prompt_arg(args.get("prompt"))
            task_name = llm_api.resolve_task_name(str(args.get("model", "") or args.get("model_name", "")))
            result = await llm_api.generate(
                llm_api.LLMServiceRequest(
                    task_name=task_name,
                    request_type=f"plugin.{plugin_id}",
                    prompt=prompt,
                    tool_options=tool_options,
                    temperature=args.get("temperature"),
                    max_tokens=args.get("max_tokens"),
                )
            )
            return result.to_capability_payload()
        except Exception as exc:
            logger.error(f"[cap.llm.generate_with_tools] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    async def _cap_llm_embed(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """执行文本嵌入能力。"""

        del capability
        from src.services.embedding_service import EmbeddingServiceClient
        from src.services.llm_service import resolve_task_name

        try:
            text = _normalize_embedding_text_arg(args)
            texts = _normalize_embedding_texts_arg(args)
            if text is None and not texts:
                return {"success": False, "error": "缺少必要参数 text 或 texts"}
            if text is not None and texts:
                return {"success": False, "error": "text 与 texts 只能提供一个"}

            task_name = resolve_task_name(
                str(args.get("task_name", "") or args.get("model", "") or args.get("model_name", "") or "embedding")
            )
            embedding_client = EmbeddingServiceClient(
                task_name=task_name,
                request_type=f"plugin.{plugin_id}",
            )

            if text is not None:
                result = await embedding_client.embed_text(text)
                return {"success": True, **_embedding_result_to_payload(result)}

            max_concurrent = args.get("max_concurrent")
            results = await embedding_client.embed_texts(
                texts,
                max_concurrent=int(max_concurrent) if max_concurrent is not None else None,
            )
            return {
                "success": True,
                "results": [_embedding_result_to_payload(result) for result in results],
            }
        except Exception as exc:
            logger.error(f"[cap.llm.embed] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    async def _cap_llm_transcribe_audio(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """执行语音识别能力。"""

        del capability
        from src.services.llm_service import LLMServiceClient, resolve_task_name

        try:
            audio_base64 = _normalize_audio_base64_arg(args)
            if audio_base64 is None:
                return {"success": False, "error": "缺少必要参数 audio_base64 或 voice_base64"}

            task_name = resolve_task_name(
                str(args.get("task_name", "") or args.get("model", "") or args.get("model_name", "") or "voice")
            )
            asr_client = LLMServiceClient(
                task_name=task_name,
                request_type=f"plugin.{plugin_id}.asr",
            )
            result = await asr_client.transcribe_audio(audio_base64)
            text = result.text or ""
            return {
                "success": bool(text),
                "text": text,
                "content": text,
            }
        except Exception as exc:
            logger.error(f"[cap.llm.transcribe_audio] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    async def _cap_llm_get_available_models(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """获取当前宿主可用的模型任务列表。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 可用模型列表。
        """
        del plugin_id, capability, args
        from src.services import llm_service as llm_api

        try:
            models = llm_api.get_available_models()
            return {"success": True, "models": list(models.keys())}
        except Exception as exc:
            logger.error(f"[cap.llm.get_available_models] 执行失败: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    async def _cap_config_get(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """读取宿主全局配置中的单个字段。

        Args:
            plugin_id: 插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 配置读取结果。
        """
        del plugin_id, capability
        key = str(args.get("key", ""))
        default = args.get("default")
        if not key:
            return {"success": False, "value": None, "error": "缺少必要参数 key"}

        try:
            value = _get_nested_config_value(global_config, key, default)
            return {"success": True, "value": value}
        except Exception as exc:
            return {"success": False, "value": None, "error": str(exc)}

    async def _cap_config_get_plugin(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """读取指定插件的配置。

        Args:
            plugin_id: 当前插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 配置读取结果。
        """
        del capability
        from src.plugin_runtime.component_query import component_query_service

        plugin_name = str(args.get("plugin_name", plugin_id))
        key = str(args.get("key", ""))
        default = args.get("default")

        try:
            config = component_query_service.get_plugin_config(plugin_name)
            if config is None:
                return {"success": False, "value": default, "error": f"未找到插件 {plugin_name} 的配置"}
            if key:
                value = _get_nested_config_value(config, key, default)
                return {"success": True, "value": value}
            return {"success": True, "value": config}
        except Exception as exc:
            return {"success": False, "value": default, "error": str(exc)}

    async def _cap_config_get_all(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        """读取指定插件的全部配置。

        Args:
            plugin_id: 当前插件标识。
            capability: 能力名称。
            args: 能力调用参数。

        Returns:
            Any: 配置读取结果。
        """
        del capability
        from src.plugin_runtime.component_query import component_query_service

        plugin_name = str(args.get("plugin_name", plugin_id))
        try:
            config = component_query_service.get_plugin_config(plugin_name)
            if config is None:
                return {"success": True, "value": {}}
            return {"success": True, "value": config}
        except Exception as exc:
            return {"success": False, "value": {}, "error": str(exc)}
