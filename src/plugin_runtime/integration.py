"""插件运行时与主程序的集成层

提供 PluginRuntimeManager 单例，负责：
1. 管理双 PluginSupervisor 的生命周期（内置插件 / 第三方插件各一个子进程）
2. 将 EventType 桥接到运行时的 event dispatch
3. 在运行时的 ComponentRegistry 中查找命令
4. 提供统一的能力实现注册接口，使插件可以调用主程序功能
"""

from __future__ import annotations

from typing import Any

import asyncio
import os

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.integration")

# 旧系统 EventType -> 新系统 event_type 字符串映射
_EVENT_TYPE_MAP: dict[str, str] = {
    "on_start": "on_start",
    "on_stop": "on_stop",
    "on_message_pre_process": "on_message_pre_process",
    "on_message": "on_message",
    "on_plan": "on_plan",
    "post_llm": "post_llm",
    "after_llm": "after_llm",
    "post_send_pre_process": "post_send_pre_process",
    "post_send": "post_send",
    "after_send": "after_send",
}


class PluginRuntimeManager:
    """插件运行时管理器（单例）

    内置插件与第三方插件分别运行在各自的 Supervisor / Runner 子进程中。
    """

    def __init__(self) -> None:
        from src.plugin_runtime.host.supervisor import PluginSupervisor

        self._builtin_supervisor: PluginSupervisor | None = None
        self._thirdparty_supervisor: PluginSupervisor | None = None
        self._started: bool = False

    # ─── 插件目录 ─────────────────────────────────────────────

    @staticmethod
    def _get_builtin_plugin_dirs() -> list[str]:
        """内置插件目录: src/plugins/built_in/"""
        candidate = os.path.abspath(os.path.join("src", "plugins", "built_in"))
        return [candidate] if os.path.isdir(candidate) else []

    @staticmethod
    def _get_thirdparty_plugin_dirs() -> list[str]:
        """第三方插件目录: plugins/"""
        candidate = os.path.abspath("plugins")
        return [candidate] if os.path.isdir(candidate) else []

    # ─── 生命周期 ─────────────────────────────────────────────

    async def start(self) -> None:
        """启动双子进程插件运行时"""
        if self._started:
            logger.warning("PluginRuntimeManager 已在运行中，跳过重复启动")
            return

        from src.plugin_runtime.host.supervisor import PluginSupervisor

        builtin_dirs = self._get_builtin_plugin_dirs()
        thirdparty_dirs = self._get_thirdparty_plugin_dirs()

        if not builtin_dirs and not thirdparty_dirs:
            logger.info("未找到任何插件目录，跳过插件运行时启动")
            return

        # 创建两个 Supervisor，各自拥有独立的 socket / Runner 子进程
        if builtin_dirs:
            self._builtin_supervisor = PluginSupervisor(
                plugin_dirs=builtin_dirs,
                socket_path=None,  # 自动生成
            )
            self._register_capability_impls(self._builtin_supervisor)

        if thirdparty_dirs:
            self._thirdparty_supervisor = PluginSupervisor(
                plugin_dirs=thirdparty_dirs,
                socket_path=None,
            )
            self._register_capability_impls(self._thirdparty_supervisor)

        # 并行启动
        coros = []
        if self._builtin_supervisor:
            coros.append(self._builtin_supervisor.start())
        if self._thirdparty_supervisor:
            coros.append(self._thirdparty_supervisor.start())

        try:
            await asyncio.gather(*coros)
            self._started = True
            logger.info(
                f"插件运行时已启动 — 内置: {builtin_dirs or '无'}, 第三方: {thirdparty_dirs or '无'}"
            )
        except Exception as e:
            logger.error(f"插件运行时启动失败: {e}", exc_info=True)
            self._builtin_supervisor = None
            self._thirdparty_supervisor = None

    async def stop(self) -> None:
        """停止所有插件运行时"""
        if not self._started:
            return

        coros = []
        if self._builtin_supervisor:
            coros.append(self._builtin_supervisor.stop())
        if self._thirdparty_supervisor:
            coros.append(self._thirdparty_supervisor.stop())

        try:
            await asyncio.gather(*coros, return_exceptions=True)
            logger.info("插件运行时已停止")
        except Exception as e:
            logger.error(f"插件运行时停止失败: {e}", exc_info=True)
        finally:
            self._started = False
            self._builtin_supervisor = None
            self._thirdparty_supervisor = None

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def supervisors(self) -> list[Any]:
        """获取所有活跃的 Supervisor"""
        return [s for s in (self._builtin_supervisor, self._thirdparty_supervisor) if s is not None]

    # ─── 事件桥接 ──────────────────────────────────────────────

    async def bridge_event(
        self,
        event_type_value: str,
        message_dict: dict[str, Any] | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """将事件分发到所有 Supervisor

        Returns:
            (continue_flag, modified_message_dict)
        """
        if not self._started:
            return True, None

        new_event_type: str = _EVENT_TYPE_MAP.get(event_type_value, event_type_value)
        modified: dict[str, Any] | None = None

        for sv in self.supervisors:
            try:
                cont, mod = await sv.dispatch_event(
                    event_type=new_event_type,
                    message=message_dict,
                    extra_args=extra_args,
                )
                if mod is not None:
                    modified = mod
                if not cont:
                    return False, modified
            except Exception as e:
                logger.error(f"事件 {new_event_type} 分发失败: {e}", exc_info=True)

        return True, modified

    # ─── 命令查找 ──────────────────────────────────────────────

    def find_command_by_text(self, text: str) -> dict[str, Any] | None:
        """在所有 Supervisor 的 ComponentRegistry 中查找命令"""
        if not self._started:
            return None

        for sv in self.supervisors:
            result = sv.component_registry.find_command_by_text(text)
            if result is not None:
                return result
        return None

    # ─── 能力实现注册 ──────────────────────────────────────────

    def _register_capability_impls(self, supervisor: Any) -> None:
        """向指定 Supervisor 注册主程序提供的能力实现"""
        cap_service = supervisor.capability_service

        # ── send.* ─────────────────────────────────────────
        cap_service.register_capability("send.text", self._cap_send_text)
        cap_service.register_capability("send.emoji", self._cap_send_emoji)
        cap_service.register_capability("send.image", self._cap_send_image)
        cap_service.register_capability("send.command", self._cap_send_command)
        cap_service.register_capability("send.custom", self._cap_send_custom)
        cap_service.register_capability("send.forward", self._cap_send_forward)
        cap_service.register_capability("send.hybrid", self._cap_send_hybrid)

        # ── llm.* ─────────────────────────────────────────
        cap_service.register_capability("llm.generate", self._cap_llm_generate)
        cap_service.register_capability("llm.generate_with_tools", self._cap_llm_generate_with_tools)
        cap_service.register_capability("llm.get_available_models", self._cap_llm_get_available_models)

        # ── config.* ──────────────────────────────────────
        cap_service.register_capability("config.get", self._cap_config_get)
        cap_service.register_capability("config.get_plugin", self._cap_config_get_plugin)
        cap_service.register_capability("config.get_all", self._cap_config_get_all)

        # ── database.* ────────────────────────────────────
        cap_service.register_capability("database.query", self._cap_database_query)
        cap_service.register_capability("database.save", self._cap_database_save)
        cap_service.register_capability("database.get", self._cap_database_get)
        cap_service.register_capability("database.delete", self._cap_database_delete)
        cap_service.register_capability("database.count", self._cap_database_count)

        # ── chat.* ────────────────────────────────────────
        cap_service.register_capability("chat.get_all_streams", self._cap_chat_get_all_streams)
        cap_service.register_capability("chat.get_group_streams", self._cap_chat_get_group_streams)
        cap_service.register_capability("chat.get_private_streams", self._cap_chat_get_private_streams)
        cap_service.register_capability("chat.get_stream_by_group_id", self._cap_chat_get_stream_by_group_id)
        cap_service.register_capability("chat.get_stream_by_user_id", self._cap_chat_get_stream_by_user_id)

        # ── message.* ─────────────────────────────────────
        cap_service.register_capability("message.get_by_time", self._cap_message_get_by_time)
        cap_service.register_capability("message.get_by_time_in_chat", self._cap_message_get_by_time_in_chat)
        cap_service.register_capability("message.get_recent", self._cap_message_get_recent)
        cap_service.register_capability("message.count_new", self._cap_message_count_new)
        cap_service.register_capability("message.build_readable", self._cap_message_build_readable)

        # ── person.* ──────────────────────────────────────
        cap_service.register_capability("person.get_id", self._cap_person_get_id)
        cap_service.register_capability("person.get_value", self._cap_person_get_value)
        cap_service.register_capability("person.get_id_by_name", self._cap_person_get_id_by_name)

        # ── emoji.* ───────────────────────────────────────
        cap_service.register_capability("emoji.get_by_description", self._cap_emoji_get_by_description)
        cap_service.register_capability("emoji.get_random", self._cap_emoji_get_random)
        cap_service.register_capability("emoji.get_count", self._cap_emoji_get_count)
        cap_service.register_capability("emoji.get_emotions", self._cap_emoji_get_emotions)
        cap_service.register_capability("emoji.get_all", self._cap_emoji_get_all)
        cap_service.register_capability("emoji.get_info", self._cap_emoji_get_info)
        cap_service.register_capability("emoji.register", self._cap_emoji_register)
        cap_service.register_capability("emoji.delete", self._cap_emoji_delete)

        # ── frequency.* ───────────────────────────────────
        cap_service.register_capability("frequency.get_current_talk_value", self._cap_frequency_get_current_talk_value)
        cap_service.register_capability("frequency.set_adjust", self._cap_frequency_set_adjust)
        cap_service.register_capability("frequency.get_adjust", self._cap_frequency_get_adjust)

        # ── tool.* ────────────────────────────────────────
        cap_service.register_capability("tool.get_definitions", self._cap_tool_get_definitions)

        # ── component.* ───────────────────────────────────
        cap_service.register_capability("component.get_all_plugins", self._cap_component_get_all_plugins)
        cap_service.register_capability("component.get_plugin_info", self._cap_component_get_plugin_info)
        cap_service.register_capability("component.list_loaded_plugins", self._cap_component_list_loaded_plugins)
        cap_service.register_capability("component.list_registered_plugins", self._cap_component_list_registered_plugins)
        cap_service.register_capability("component.enable", self._cap_component_enable)
        cap_service.register_capability("component.disable", self._cap_component_disable)
        cap_service.register_capability("component.load_plugin", self._cap_component_load_plugin)
        cap_service.register_capability("component.unload_plugin", self._cap_component_unload_plugin)
        cap_service.register_capability("component.reload_plugin", self._cap_component_reload_plugin)

        # ── knowledge.* ───────────────────────────────────
        cap_service.register_capability("knowledge.search", self._cap_knowledge_search)

        # ── logging.* ─────────────────────────────────────
        cap_service.register_capability("logging.log", self._cap_logging_log)

        logger.debug("已注册全部主程序能力实现")

    # ═════════════════════════════════════════════════════════
    #  send.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_send_text(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送文本消息

        args: text, stream_id, typing?, set_reply?, storage_message?
        """
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

    @staticmethod
    async def _cap_send_emoji(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送表情

        args: emoji_base64, stream_id, storage_message?
        """
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

    @staticmethod
    async def _cap_send_image(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送图片

        args: image_base64, stream_id, storage_message?
        """
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

    @staticmethod
    async def _cap_send_command(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送命令

        args: command, stream_id, storage_message?, display_message?
        """
        from src.services import send_service as send_api

        command = args.get("command", "")
        stream_id: str = args.get("stream_id", "")
        if not command or not stream_id:
            return {"success": False, "error": "缺少必要参数 command 或 stream_id"}

        try:
            result = await send_api.command_to_stream(
                command=command,
                stream_id=stream_id,
                storage_message=args.get("storage_message", True),
                display_message=args.get("display_message", ""),
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.command] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_send_custom(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送自定义类型消息

        args: message_type, content, stream_id, display_message?, typing?, storage_message?
        """
        from src.services import send_service as send_api

        message_type: str = args.get("message_type", "")
        content = args.get("content", "")
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

    @staticmethod
    async def _cap_send_forward(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送转发消息

        args: messages, stream_id
        """
        from src.services import send_service as send_api

        messages = args.get("messages", [])
        stream_id: str = args.get("stream_id", "")
        if not messages or not stream_id:
            return {"success": False, "error": "缺少必要参数 messages 或 stream_id"}

        try:
            result = await send_api.forward_to_stream(
                messages=messages,
                stream_id=stream_id,
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.forward] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_send_hybrid(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """发送混合消息（图文混合）

        args: segments, stream_id
        """
        from src.services import send_service as send_api

        segments = args.get("segments", [])
        stream_id: str = args.get("stream_id", "")
        if not segments or not stream_id:
            return {"success": False, "error": "缺少必要参数 segments 或 stream_id"}

        try:
            result = await send_api.hybrid_to_stream(
                segments=segments,
                stream_id=stream_id,
            )
            return {"success": result}
        except Exception as e:
            logger.error(f"[cap.send.hybrid] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  llm.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_llm_generate(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """LLM 生成

        args: prompt, model_name?, temperature?, max_tokens?
        """
        from src.services import llm_service as llm_api

        prompt: str = args.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少必要参数 prompt"}

        model_name: str = args.get("model_name", "")
        temperature = args.get("temperature")
        max_tokens = args.get("max_tokens")

        try:
            models = llm_api.get_available_models()
            if model_name and model_name in models:
                model_config = models[model_name]
            else:
                # 选取第一个可用模型配置
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

    @staticmethod
    async def _cap_llm_generate_with_tools(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """LLM 带工具生成

        args: prompt, model_name?, tool_options?, temperature?, max_tokens?
        """
        from src.services import llm_service as llm_api

        prompt: str = args.get("prompt", "")
        if not prompt:
            return {"success": False, "error": "缺少必要参数 prompt"}

        model_name: str = args.get("model_name", "")
        tool_options = args.get("tool_options")
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
            # 将 ToolCall 对象序列化为 dict
            serialized_tool_calls = None
            if tool_calls:
                serialized_tool_calls = [
                    {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                    if hasattr(tc, "function")
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

    @staticmethod
    async def _cap_llm_get_available_models(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取可用模型列表"""
        from src.services import llm_service as llm_api

        try:
            models = llm_api.get_available_models()
            return {"success": True, "models": list(models.keys())}
        except Exception as e:
            logger.error(f"[cap.llm.get_available_models] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  config.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_config_get(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """读取全局配置

        args: key, default?
        """
        from src.services import config_service as config_api

        key: str = args.get("key", "")
        default = args.get("default")

        if not key:
            return {"success": False, "value": None, "error": "缺少必要参数 key"}

        try:
            value = config_api.get_global_config(key, default)
            return {"success": True, "value": value}
        except Exception as e:
            return {"success": False, "value": None, "error": str(e)}

    @staticmethod
    async def _cap_config_get_plugin(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """读取插件配置

        args: key, default?, plugin_name?
        """
        from src.core.component_registry import component_registry as core_registry

        plugin_name: str = args.get("plugin_name", plugin_id)
        key: str = args.get("key", "")
        default = args.get("default")

        try:
            config = core_registry.get_plugin_config(plugin_name)
            if config is None:
                return {"success": False, "value": default, "error": f"未找到插件 {plugin_name} 的配置"}

            if key:
                from src.services import config_service as config_api

                value = config_api.get_plugin_config(config, key, default)
                return {"success": True, "value": value}

            return {"success": True, "value": config}
        except Exception as e:
            return {"success": False, "value": default, "error": str(e)}

    @staticmethod
    async def _cap_config_get_all(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取当前插件的全部配置"""
        from src.core.component_registry import component_registry as core_registry

        plugin_name: str = args.get("plugin_name", plugin_id)

        try:
            config = core_registry.get_plugin_config(plugin_name)
            if config is None:
                return {"success": True, "value": {}}
            return {"success": True, "value": config}
        except Exception as e:
            return {"success": False, "value": {}, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  database.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_database_query(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """数据库查询

        args: model_name, query_type?, filters?, limit?, order_by?, data?, single_result?
        model_name 应为 src.common.database.database_model 中的类名。
        """
        from src.services import database_service as database_api

        model_name: str = args.get("model_name", "")
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_api.db_query(
                model_class=model_class,
                data=args.get("data"),
                query_type=args.get("query_type", "get"),
                filters=args.get("filters"),
                limit=args.get("limit"),
                order_by=args.get("order_by"),
                single_result=args.get("single_result", False),
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.query] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_database_save(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """数据库保存

        args: model_name, data, key_field?, key_value?
        """
        from src.services import database_service as database_api

        model_name: str = args.get("model_name", "")
        data: dict[str, Any] | None = args.get("data")
        if not model_name or not data:
            return {"success": False, "error": "缺少必要参数 model_name 或 data"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_api.db_save(
                model_class=model_class,
                data=data,
                key_field=args.get("key_field"),
                key_value=args.get("key_value"),
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.save] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_database_get(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """数据库简单查询

        args: model_name, filters?, limit?, order_by?, single_result?
        """
        from src.services import database_service as database_api

        model_name: str = args.get("model_name", "")
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_api.db_get(
                model_class=model_class,
                filters=args.get("filters"),
                limit=args.get("limit"),
                order_by=args.get("order_by"),
                single_result=args.get("single_result", False),
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.get] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_database_delete(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """数据库删除

        args: model_name, filters
        """
        from src.services import database_service as database_api

        model_name: str = args.get("model_name", "")
        filters = args.get("filters", {})
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}
        if not filters:
            return {"success": False, "error": "缺少必要参数 filters（不允许无条件删除）"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_api.db_delete(
                model_class=model_class,
                filters=filters,
            )
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"[cap.database.delete] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_database_count(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """数据库计数

        args: model_name, filters?
        """
        from src.services import database_service as database_api

        model_name: str = args.get("model_name", "")
        if not model_name:
            return {"success": False, "error": "缺少必要参数 model_name"}

        try:
            import src.common.database.database_model as db_models

            model_class = getattr(db_models, model_name, None)
            if model_class is None:
                return {"success": False, "error": f"未找到数据模型: {model_name}"}

            result = await database_api.db_count(
                model_class=model_class,
                filters=args.get("filters"),
            )
            return {"success": True, "count": result}
        except Exception as e:
            logger.error(f"[cap.database.count] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  chat.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    def _serialize_stream(stream: Any) -> dict[str, Any]:
        """将 BotChatSession 序列化为可通过 RPC 传输的字典"""
        return {
            "session_id": getattr(stream, "session_id", ""),
            "platform": getattr(stream, "platform", ""),
            "user_id": getattr(stream, "user_id", ""),
            "group_id": getattr(stream, "group_id", ""),
            "is_group_session": getattr(stream, "is_group_session", False),
        }

    @staticmethod
    async def _cap_chat_get_all_streams(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取所有聊天流

        args: platform?
        """
        from src.services import chat_service as chat_api

        platform: str = args.get("platform", "qq")
        try:
            streams = chat_api.ChatManager.get_all_streams(platform=platform)
            return {
                "success": True,
                "streams": [PluginRuntimeManager._serialize_stream(s) for s in streams],
            }
        except Exception as e:
            logger.error(f"[cap.chat.get_all_streams] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_chat_get_group_streams(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取所有群聊流

        args: platform?
        """
        from src.services import chat_service as chat_api

        platform: str = args.get("platform", "qq")
        try:
            streams = chat_api.ChatManager.get_group_streams(platform=platform)
            return {
                "success": True,
                "streams": [PluginRuntimeManager._serialize_stream(s) for s in streams],
            }
        except Exception as e:
            logger.error(f"[cap.chat.get_group_streams] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_chat_get_private_streams(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取所有私聊流

        args: platform?
        """
        from src.services import chat_service as chat_api

        platform: str = args.get("platform", "qq")
        try:
            streams = chat_api.ChatManager.get_private_streams(platform=platform)
            return {
                "success": True,
                "streams": [PluginRuntimeManager._serialize_stream(s) for s in streams],
            }
        except Exception as e:
            logger.error(f"[cap.chat.get_private_streams] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_chat_get_stream_by_group_id(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """按群 ID 查找聊天流

        args: group_id, platform?
        """
        from src.services import chat_service as chat_api

        group_id: str = args.get("group_id", "")
        if not group_id:
            return {"success": False, "error": "缺少必要参数 group_id"}

        platform: str = args.get("platform", "qq")
        try:
            stream = chat_api.ChatManager.get_group_stream_by_group_id(group_id=group_id, platform=platform)
            if stream is None:
                return {"success": True, "stream": None}
            return {"success": True, "stream": PluginRuntimeManager._serialize_stream(stream)}
        except Exception as e:
            logger.error(f"[cap.chat.get_stream_by_group_id] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_chat_get_stream_by_user_id(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """按用户 ID 查找私聊流

        args: user_id, platform?
        """
        from src.services import chat_service as chat_api

        user_id: str = args.get("user_id", "")
        if not user_id:
            return {"success": False, "error": "缺少必要参数 user_id"}

        platform: str = args.get("platform", "qq")
        try:
            stream = chat_api.ChatManager.get_private_stream_by_user_id(user_id=user_id, platform=platform)
            if stream is None:
                return {"success": True, "stream": None}
            return {"success": True, "stream": PluginRuntimeManager._serialize_stream(stream)}
        except Exception as e:
            logger.error(f"[cap.chat.get_stream_by_user_id] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  message.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    def _serialize_messages(messages: list) -> list[dict[str, Any]]:
        """将 DatabaseMessages 列表序列化为 dict 列表"""
        result: list[dict[str, Any]] = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                result.append(msg.model_dump())
            elif hasattr(msg, "__dict__"):
                result.append(dict(msg.__dict__))
            else:
                result.append(str(msg))
        return result

    @staticmethod
    async def _cap_message_get_by_time(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """按时间范围查询消息

        args: start_time, end_time, limit?, filter_mai?
        """
        from src.services import message_service as message_api

        start_time = args.get("start_time", 0.0)
        end_time = args.get("end_time", 0.0)

        try:
            messages = message_api.get_messages_by_time(
                start_time=float(start_time),
                end_time=float(end_time),
                limit=args.get("limit", 0),
                limit_mode=args.get("limit_mode", "latest"),
                filter_mai=args.get("filter_mai", False),
            )
            return {"success": True, "messages": PluginRuntimeManager._serialize_messages(messages)}
        except Exception as e:
            logger.error(f"[cap.message.get_by_time] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_message_get_by_time_in_chat(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """按时间范围查询指定聊天消息

        args: chat_id, start_time, end_time, limit?, filter_mai?, filter_command?
        """
        from src.services import message_service as message_api

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            messages = message_api.get_messages_by_time_in_chat(
                chat_id=chat_id,
                start_time=float(args.get("start_time", 0.0)),
                end_time=float(args.get("end_time", 0.0)),
                limit=args.get("limit", 0),
                limit_mode=args.get("limit_mode", "latest"),
                filter_mai=args.get("filter_mai", False),
                filter_command=args.get("filter_command", False),
            )
            return {"success": True, "messages": PluginRuntimeManager._serialize_messages(messages)}
        except Exception as e:
            logger.error(f"[cap.message.get_by_time_in_chat] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_message_get_recent(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取最近的消息

        args: chat_id, hours?, limit?, filter_mai?
        """
        from src.services import message_service as message_api

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            messages = message_api.get_recent_messages(
                chat_id=chat_id,
                hours=float(args.get("hours", 24.0)),
                limit=args.get("limit", 100),
                limit_mode=args.get("limit_mode", "latest"),
                filter_mai=args.get("filter_mai", False),
            )
            return {"success": True, "messages": PluginRuntimeManager._serialize_messages(messages)}
        except Exception as e:
            logger.error(f"[cap.message.get_recent] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_message_count_new(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """统计新消息数量

        args: chat_id, start_time?, end_time?
        """
        from src.services import message_service as message_api

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            count = message_api.count_new_messages(
                chat_id=chat_id,
                start_time=float(args.get("start_time", 0.0)),
                end_time=args.get("end_time"),
            )
            return {"success": True, "count": count}
        except Exception as e:
            logger.error(f"[cap.message.count_new] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_message_build_readable(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """将消息列表构建成可读字符串

        args: chat_id, start_time, end_time, limit?, replace_bot_name?, timestamp_mode?
        """
        from src.services import message_service as message_api

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            messages = message_api.get_messages_by_time_in_chat(
                chat_id=chat_id,
                start_time=float(args.get("start_time", 0.0)),
                end_time=float(args.get("end_time", 0.0)),
                limit=args.get("limit", 0),
            )
            readable = message_api.build_readable_messages_to_str(
                messages=messages,
                replace_bot_name=args.get("replace_bot_name", True),
                timestamp_mode=args.get("timestamp_mode", "relative"),
                truncate=args.get("truncate", False),
            )
            return {"success": True, "text": readable}
        except Exception as e:
            logger.error(f"[cap.message.build_readable] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  person.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_person_get_id(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取 person_id

        args: platform, user_id
        """
        from src.services import person_service as person_api

        platform: str = args.get("platform", "")
        user_id = args.get("user_id", "")
        if not platform or not user_id:
            return {"success": False, "error": "缺少必要参数 platform 或 user_id"}

        try:
            pid = person_api.get_person_id(platform=platform, user_id=user_id)
            return {"success": True, "person_id": pid}
        except Exception as e:
            logger.error(f"[cap.person.get_id] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_person_get_value(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取用户字段值

        args: person_id, field_name, default?
        """
        from src.services import person_service as person_api

        person_id: str = args.get("person_id", "")
        field_name: str = args.get("field_name", "")
        if not person_id or not field_name:
            return {"success": False, "error": "缺少必要参数 person_id 或 field_name"}

        try:
            value = await person_api.get_person_value(
                person_id=person_id,
                field_name=field_name,
                default=args.get("default"),
            )
            return {"success": True, "value": value}
        except Exception as e:
            logger.error(f"[cap.person.get_value] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_person_get_id_by_name(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """根据用户名获取 person_id

        args: person_name
        """
        from src.services import person_service as person_api

        person_name: str = args.get("person_name", "")
        if not person_name:
            return {"success": False, "error": "缺少必要参数 person_name"}

        try:
            pid = person_api.get_person_id_by_name(person_name=person_name)
            return {"success": True, "person_id": pid}
        except Exception as e:
            logger.error(f"[cap.person.get_id_by_name] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  emoji.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_emoji_get_by_description(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """根据描述获取表情包

        args: description
        """
        from src.services import emoji_service as emoji_api

        description: str = args.get("description", "")
        if not description:
            return {"success": False, "error": "缺少必要参数 description"}

        try:
            result = await emoji_api.get_by_description(description=description)
            if result is None:
                return {"success": True, "emoji": None}
            emoji_base64, emoji_desc, matched_emotion = result
            return {
                "success": True,
                "emoji": {
                    "base64": emoji_base64,
                    "description": emoji_desc,
                    "emotion": matched_emotion,
                },
            }
        except Exception as e:
            logger.error(f"[cap.emoji.get_by_description] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_get_random(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """随机获取表情包

        args: count?
        """
        from src.services import emoji_service as emoji_api

        count: int = args.get("count", 1)
        try:
            results = await emoji_api.get_random(count=count)
            emojis = [
                {"base64": b64, "description": desc, "emotion": emo} for b64, desc, emo in results
            ]
            return {"success": True, "emojis": emojis}
        except Exception as e:
            logger.error(f"[cap.emoji.get_random] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_get_count(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取表情包数量"""
        from src.services import emoji_service as emoji_api

        try:
            return {"success": True, "count": emoji_api.get_count()}
        except Exception as e:
            logger.error(f"[cap.emoji.get_count] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_get_emotions(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取所有情绪标签"""
        from src.services import emoji_service as emoji_api

        try:
            return {"success": True, "emotions": emoji_api.get_emotions()}
        except Exception as e:
            logger.error(f"[cap.emoji.get_emotions] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_get_all(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取所有表情包"""
        from src.services import emoji_service as emoji_api

        try:
            results = await emoji_api.get_all()
            emojis = [
                {"base64": b64, "description": desc, "emotion": emo} for b64, desc, emo in results
            ] if results else []
            return {"success": True, "emojis": emojis}
        except Exception as e:
            logger.error(f"[cap.emoji.get_all] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_get_info(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取表情包统计信息"""
        from src.services import emoji_service as emoji_api

        try:
            return {"success": True, "info": emoji_api.get_info()}
        except Exception as e:
            logger.error(f"[cap.emoji.get_info] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_register(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """注册表情包

        args: emoji_base64
        """
        from src.services import emoji_service as emoji_api

        emoji_base64: str = args.get("emoji_base64", "")
        if not emoji_base64:
            return {"success": False, "error": "缺少必要参数 emoji_base64"}

        try:
            result = await emoji_api.register_emoji(emoji_base64)
            return result
        except Exception as e:
            logger.error(f"[cap.emoji.register] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_emoji_delete(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """删除表情包

        args: emoji_hash
        """
        from src.services import emoji_service as emoji_api

        emoji_hash: str = args.get("emoji_hash", "")
        if not emoji_hash:
            return {"success": False, "error": "缺少必要参数 emoji_hash"}

        try:
            result = await emoji_api.delete_emoji(emoji_hash)
            return result
        except Exception as e:
            logger.error(f"[cap.emoji.delete] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  frequency.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_frequency_get_current_talk_value(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取当前说话频率值

        args: chat_id
        """
        from src.services import frequency_service as frequency_api

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            value = frequency_api.get_current_talk_value(chat_id)
            return {"success": True, "value": value}
        except Exception as e:
            logger.error(f"[cap.frequency.get_current_talk_value] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_frequency_set_adjust(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """设置说话频率调整值

        args: chat_id, value
        """
        from src.services import frequency_service as frequency_api

        chat_id: str = args.get("chat_id", "")
        value = args.get("value")
        if not chat_id or value is None:
            return {"success": False, "error": "缺少必要参数 chat_id 或 value"}

        try:
            frequency_api.set_talk_frequency_adjust(chat_id, float(value))
            return {"success": True}
        except Exception as e:
            logger.error(f"[cap.frequency.set_adjust] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _cap_frequency_get_adjust(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取说话频率调整值

        args: chat_id
        """
        from src.services import frequency_service as frequency_api

        chat_id: str = args.get("chat_id", "")
        if not chat_id:
            return {"success": False, "error": "缺少必要参数 chat_id"}

        try:
            value = frequency_api.get_talk_frequency_adjust(chat_id)
            return {"success": True, "value": value}
        except Exception as e:
            logger.error(f"[cap.frequency.get_adjust] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  tool.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_tool_get_definitions(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取 LLM 可用的工具定义列表"""
        from src.core.component_registry import component_registry as core_registry

        try:
            tools = core_registry.get_llm_available_tools()
            return {
                "success": True,
                "tools": [{"name": name, "definition": info.get_llm_definition()} for name, info in tools.items()],
            }
        except Exception as e:
            logger.error(f"[cap.tool.get_definitions] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  component.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_component_get_all_plugins(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取所有插件信息（汇总所有 Supervisor 的注册信息）"""
        mgr = get_plugin_runtime_manager()
        result: dict[str, Any] = {}
        for sv in mgr.supervisors:
            for pid, reg in sv._registered_plugins.items():
                result[pid] = {
                    "name": pid,
                    "version": reg.plugin_version,
                    "description": "",
                    "author": "",
                    "enabled": True,
                }
        return {"success": True, "plugins": result}

    @staticmethod
    async def _cap_component_get_plugin_info(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """获取指定插件信息

        args: plugin_name
        """
        plugin_name: str = args.get("plugin_name", plugin_id)
        mgr = get_plugin_runtime_manager()
        for sv in mgr.supervisors:
            reg = sv._registered_plugins.get(plugin_name)
            if reg is not None:
                return {
                    "success": True,
                    "plugin": {
                        "name": plugin_name,
                        "version": reg.plugin_version,
                        "description": "",
                        "author": "",
                        "enabled": True,
                    },
                }
        return {"success": False, "error": f"未找到插件: {plugin_name}"}

    @staticmethod
    async def _cap_component_list_loaded_plugins(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """列出已加载的插件"""
        mgr = get_plugin_runtime_manager()
        plugins: list[str] = []
        for sv in mgr.supervisors:
            plugins.extend(sv._registered_plugins.keys())
        return {"success": True, "plugins": plugins}

    @staticmethod
    async def _cap_component_list_registered_plugins(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """列出已注册的插件（同 list_loaded）"""
        mgr = get_plugin_runtime_manager()
        plugins: list[str] = []
        for sv in mgr.supervisors:
            plugins.extend(sv._registered_plugins.keys())
        return {"success": True, "plugins": plugins}

    @staticmethod
    async def _cap_component_enable(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """启用组件

        args: name, component_type
        """
        name: str = args.get("name", "")
        component_type: str = args.get("component_type", "")
        if not name or not component_type:
            return {"success": False, "error": "缺少必要参数 name 或 component_type"}

        mgr = get_plugin_runtime_manager()
        for sv in mgr.supervisors:
            comp = sv.component_registry.get_component(name)
            if comp is not None:
                comp.enabled = True
                return {"success": True}
        return {"success": False, "error": f"未找到组件: {name}"}

    @staticmethod
    async def _cap_component_disable(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """禁用组件

        args: name, component_type
        """
        name: str = args.get("name", "")
        component_type: str = args.get("component_type", "")
        if not name or not component_type:
            return {"success": False, "error": "缺少必要参数 name 或 component_type"}

        mgr = get_plugin_runtime_manager()
        for sv in mgr.supervisors:
            comp = sv.component_registry.get_component(name)
            if comp is not None:
                comp.enabled = False
                return {"success": True}
        return {"success": False, "error": f"未找到组件: {name}"}

    @staticmethod
    async def _cap_component_load_plugin(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """加载插件（在新运行时中通过热重载实现）

        args: plugin_name
        """
        plugin_name: str = args.get("plugin_name", "")
        if not plugin_name:
            return {"success": False, "error": "缺少必要参数 plugin_name"}

        mgr = get_plugin_runtime_manager()
        for sv in mgr.supervisors:
            try:
                await sv.reload_plugins(reason=f"load {plugin_name}")
                return {"success": True, "count": 1}
            except Exception as e:
                logger.error(f"[cap.component.load_plugin] 热重载失败: {e}")
        return {"success": False, "error": f"无法加载插件: {plugin_name}"}

    @staticmethod
    async def _cap_component_unload_plugin(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """卸载插件（在新运行时中不支持单独卸载）

        args: plugin_name
        """
        return {"success": False, "error": "新运行时不支持单独卸载插件，请使用 reload"}

    @staticmethod
    async def _cap_component_reload_plugin(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """重新加载插件（触发对应 Supervisor 的热重载）

        args: plugin_name
        """
        plugin_name: str = args.get("plugin_name", "")
        if not plugin_name:
            return {"success": False, "error": "缺少必要参数 plugin_name"}

        mgr = get_plugin_runtime_manager()
        for sv in mgr.supervisors:
            if plugin_name in sv._registered_plugins:
                try:
                    await sv.reload_plugins(reason=f"reload {plugin_name}")
                    return {"success": True}
                except Exception as e:
                    logger.error(f"[cap.component.reload_plugin] 热重载失败: {e}")
                    return {"success": False, "error": str(e)}
        return {"success": False, "error": f"未找到插件: {plugin_name}"}

    # ═════════════════════════════════════════════════════════
    #  knowledge.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_knowledge_search(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """从 LPMM 知识库搜索知识

        args: query, limit?
        """
        query: str = args.get("query", "")
        if not query:
            return {"success": False, "error": "缺少必要参数 query"}

        limit = args.get("limit", 5)
        try:
            limit_value = max(1, int(limit))
        except (TypeError, ValueError):
            limit_value = 5

        try:
            from src.chat.knowledge import qa_manager

            if qa_manager is None:
                return {"success": True, "content": "LPMM知识库已禁用"}

            knowledge_info = await qa_manager.get_knowledge(query, limit=limit_value)
            if knowledge_info:
                content = f"你知道这些知识: {knowledge_info}"
            else:
                content = f"你不太了解有关{query}的知识"
            return {"success": True, "content": content}
        except Exception as e:
            logger.error(f"[cap.knowledge.search] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ═════════════════════════════════════════════════════════
    #  logging.* 能力实现
    # ═════════════════════════════════════════════════════════

    @staticmethod
    async def _cap_logging_log(plugin_id: str, capability: str, args: dict[str, Any]) -> Any:
        """插件日志记录

        args: level?, message
        """
        level: str = args.get("level", "info").lower()
        message: str = args.get("message", "")
        if not message:
            return {"success": False, "error": "缺少必要参数 message"}

        plugin_logger = get_logger(f"plugin.{plugin_id}")
        log_fn = getattr(plugin_logger, level, plugin_logger.info)
        log_fn(message)
        return {"success": True}


# ─── 单例 ──────────────────────────────────────────────────

_manager: PluginRuntimeManager | None = None


def get_plugin_runtime_manager() -> PluginRuntimeManager:
    """获取 PluginRuntimeManager 全局单例"""
    global _manager
    if _manager is None:
        _manager = PluginRuntimeManager()
    return _manager
