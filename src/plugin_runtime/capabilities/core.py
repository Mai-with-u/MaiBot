from typing import Any, Dict

from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.payload_content.tool_option import ToolCall

logger = get_logger("plugin_runtime.integration")


def _get_nested_config_value(source: Any, key: str, default: Any = None) -> Any:
    current = source
    try:
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                raise KeyError(part)
        return current
    except Exception:
        return default


class RuntimeCoreCapabilityMixin:
    async def _cap_send_text(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import send_service as send_api

        text: str = args.get("text", "")
        stream_id: str = args.get("stream_id", "")
        if not text or not stream_id:
            return {"success": False, "error": "缺少必要参数 text 或 stream_id"}

        try:
            result = await send_api.text_to_stream(
                text=text,
                stream_id=stream_id,
                typing=args.get("typing", False),
                set_reply=args.get("set_reply", False),
                storage_message=args.get("storage_message", True),
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.text] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_send_emoji(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import send_service as send_api

        emoji_base64: str = args.get("emoji_base64", "")
        stream_id: str = args.get("stream_id", "")
        if not emoji_base64 or not stream_id:
            return {"success": False, "error": "缺少必要参数 emoji_base64 或 stream_id"}

        try:
            result = await send_api.emoji_to_stream(
                emoji_base64=emoji_base64,
                stream_id=stream_id,
                storage_message=args.get("storage_message", True),
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.emoji] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_send_image(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import send_service as send_api

        image_base64: str = args.get("image_base64", "")
        stream_id: str = args.get("stream_id", "")
        if not image_base64 or not stream_id:
            return {"success": False, "error": "缺少必要参数 image_base64 或 stream_id"}

        try:
            result = await send_api.image_to_stream(
                image_base64=image_base64,
                stream_id=stream_id,
                storage_message=args.get("storage_message", True),
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.image] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_send_command(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import send_service as send_api

        command = args.get("command", "")
        stream_id: str = args.get("stream_id", "")
        if not command or not stream_id:
            return {"success": False, "error": "缺少必要参数 command 或 stream_id"}

        try:
            result = await send_api.custom_to_stream(
                message_type="command",
                content=command,
                stream_id=stream_id,
                storage_message=args.get("storage_message", True),
                display_message=args.get("display_message", ""),
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.command] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_send_custom(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import send_service as send_api

        message_type: str = args.get("message_type", "") or args.get("custom_type", "")
        content = args.get("content")
        if content is None:
            content = args.get("data", "")
        stream_id: str = args.get("stream_id", "")
        if not message_type or not stream_id:
            return {"success": False, "error": "缺少必要参数 message_type 或 stream_id"}

        try:
            result = await send_api.custom_to_stream(
                message_type=message_type,
                content=content,
                stream_id=stream_id,
                display_message=args.get("display_message", ""),
                typing=args.get("typing", False),
                storage_message=args.get("storage_message", True),
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.custom] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_llm_generate(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import llm_service as llm_api

        prompt: str = args.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少必要参数 prompt"}

        model_name: str = args.get("model", "") or args.get("model_name", "")
        temperature = args.get("temperature")
        max_tokens = args.get("max_tokens")

        try:
            models = llm_api.get_available_models()
            if model_name and model_name in models:
                model_config = models[model_name]
            else:
                if not models:
                    return {"success": False, "error": "没有可用的模型配置"}
                model_config = next(iter(models.values()))

            success, response, reasoning, used_model = await llm_api.generate_with_model(
                prompt=prompt,
                model_config=model_config,
                request_type=f"plugin.{plugin_id}",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "success": success,
                "response": response,
                "reasoning": reasoning,
                "model_name": used_model,
            }
        except Exception as e:
            logger.error(f"[cap.llm.generate] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_llm_generate_with_tools(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import llm_service as llm_api

        prompt: str = args.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少必要参数 prompt"}

        model_name: str = args.get("model", "") or args.get("model_name", "")
        tool_options = args.get("tools") or args.get("tool_options")
        temperature = args.get("temperature")
        max_tokens = args.get("max_tokens")

        try:
            models = llm_api.get_available_models()
            if model_name and model_name in models:
                model_config = models[model_name]
            else:
                if not models:
                    return {"success": False, "error": "没有可用的模型配置"}
                model_config = next(iter(models.values()))

            success, response, reasoning, used_model, tool_calls = await llm_api.generate_with_model_with_tools(
                prompt=prompt,
                model_config=model_config,
                tool_options=tool_options,
                request_type=f"plugin.{plugin_id}",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            serialized_tool_calls = None
            if tool_calls:
                serialized_tool_calls = [
                    {
                        "id": tool_call.call_id,
                        "function": {"name": tool_call.func_name, "arguments": tool_call.args or {}},
                    }
                    for tool_call in tool_calls
                    if isinstance(tool_call, ToolCall)
                ]
            return {
                "success": success,
                "response": response,
                "reasoning": reasoning,
                "model_name": used_model,
                "tool_calls": serialized_tool_calls,
            }
        except Exception as e:
            logger.error(f"[cap.llm.generate_with_tools] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_llm_get_available_models(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.services import llm_service as llm_api

        try:
            models = llm_api.get_available_models()
            return {"success": True, "models": list(models.keys())}
        except Exception as e:
            logger.error(f"[cap.llm.get_available_models] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _cap_config_get(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        key: str = args.get("key", "")
        default = args.get("default")
        if not key:
            return {"success": False, "value": None, "error": "缺少必要参数 key"}

        try:
            value = _get_nested_config_value(global_config, key, default)
            return {"success": True, "value": value}
        except Exception as e:
            return {"success": False, "value": None, "error": str(e)}

    async def _cap_config_get_plugin(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.plugin_runtime.component_query import component_query_service

        plugin_name: str = args.get("plugin_name", plugin_id)
        key: str = args.get("key", "")
        default = args.get("default")

        try:
            config = component_query_service.get_plugin_config(plugin_name)
            if config is None:
                return {"success": False, "value": default, "error": f"未找到插件 {plugin_name} 的配置"}

            if key:
                value = _get_nested_config_value(config, key, default)
                return {"success": True, "value": value}

            return {"success": True, "value": config}
        except Exception as e:
            return {"success": False, "value": default, "error": str(e)}

    async def _cap_config_get_all(self, plugin_id: str, capability: str, args: Dict[str, Any]) -> Any:
        from src.plugin_runtime.component_query import component_query_service

        plugin_name: str = args.get("plugin_name", plugin_id)
        try:
            config = component_query_service.get_plugin_config(plugin_name)
            if config is None:
                return {"success": True, "value": {}}
            return {"success": True, "value": config}
        except Exception as e:
            return {"success": False, "value": {}, "error": str(e)}
