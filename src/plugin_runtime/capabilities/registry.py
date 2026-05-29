from typing import TYPE_CHECKING

from src.common.logger import get_logger
from src.plugin_runtime.host.capability_service import CapabilityImpl
from src.plugin_runtime.host.supervisor import PluginSupervisor

if TYPE_CHECKING:
    from src.plugin_runtime.integration import PluginRuntimeManager

logger = get_logger("plugin_runtime.integration")


def register_capability_impls(manager: "PluginRuntimeManager", supervisor: PluginSupervisor) -> None:
    """向指定 Supervisor 注册主程序提供的能力实现。"""
    cap_service = supervisor.capability_service

    def _register(name: str, impl: CapabilityImpl) -> None:
        """注册单个能力实现。

        Args:
            name: 能力名称。
            impl: 能力实现函数。
        """
        cap_service.register_capability(name, impl)

    _register("send.text", manager._cap_send_text)
    _register("send.emoji", manager._cap_send_emoji)
    _register("send.image", manager._cap_send_image)
    _register("send.forward", manager._cap_send_forward)
    _register("send.hybrid", manager._cap_send_hybrid)
    _register("send.command", manager._cap_send_command)
    _register("send.custom", manager._cap_send_custom)

    _register("llm.generate", manager._cap_llm_generate)
    _register("llm.generate_with_tools", manager._cap_llm_generate_with_tools)
    _register("llm.embed", manager._cap_llm_embed)
    _register("llm.get_available_models", manager._cap_llm_get_available_models)

    _register("config.get", manager._cap_config_get)
    _register("config.get_plugin", manager._cap_config_get_plugin)
    _register("config.get_all", manager._cap_config_get_all)

    _register("database.query", manager._cap_database_query)
    _register("database.save", manager._cap_database_save)
    _register("database.get", manager._cap_database_get)
    _register("database.delete", manager._cap_database_delete)
    _register("database.count", manager._cap_database_count)

    _register("chat.get_all_streams", manager._cap_chat_get_all_streams)
    _register("chat.get_group_streams", manager._cap_chat_get_group_streams)
    _register("chat.get_private_streams", manager._cap_chat_get_private_streams)
    _register("chat.open_session", manager._cap_chat_open_session)
    _register("chat.get_stream_by_group_id", manager._cap_chat_get_stream_by_group_id)
    _register("chat.get_stream_by_user_id", manager._cap_chat_get_stream_by_user_id)

    _register("message.get_by_time", manager._cap_message_get_by_time)
    _register("message.get_by_time_in_chat", manager._cap_message_get_by_time_in_chat)
    _register("message.get_by_id", manager._cap_message_get_by_id)
    _register("message.get_recent", manager._cap_message_get_recent)
    _register("message.count_new", manager._cap_message_count_new)
    _register("message.build_readable", manager._cap_message_build_readable)

    _register("maisaka.context.append", manager._cap_maisaka_context_append)
    _register("maisaka.proactive.trigger", manager._cap_maisaka_proactive_trigger)

    _register("person.get_id", manager._cap_person_get_id)
    _register("person.get_value", manager._cap_person_get_value)
    _register("person.get_id_by_name", manager._cap_person_get_id_by_name)

    _register("emoji.get_by_description", manager._cap_emoji_get_by_description)
    _register("emoji.get_random", manager._cap_emoji_get_random)
    _register("emoji.get_count", manager._cap_emoji_get_count)
    _register("emoji.get_emotions", manager._cap_emoji_get_emotions)
    _register("emoji.get_all", manager._cap_emoji_get_all)
    _register("emoji.get_info", manager._cap_emoji_get_info)
    _register("emoji.register", manager._cap_emoji_register)
    _register("emoji.delete", manager._cap_emoji_delete)

    _register("frequency.get_current_talk_value", manager._cap_frequency_get_current_talk_value)
    _register("frequency.set_adjust", manager._cap_frequency_set_adjust)
    _register("frequency.get_adjust", manager._cap_frequency_get_adjust)

    _register("tool.get_definitions", manager._cap_tool_get_definitions)

    _register("api.call", manager._cap_api_call)
    _register("api.get", manager._cap_api_get)
    _register("api.list", manager._cap_api_list)
    _register("api.replace_dynamic", manager._cap_api_replace_dynamic)

    _register("component.get_all_plugins", manager._cap_component_get_all_plugins)
    _register("component.get_plugin_info", manager._cap_component_get_plugin_info)
    _register("component.get_plugin_config_schema", manager._cap_component_get_plugin_config_schema)
    _register("component.update_plugin_config", manager._cap_component_update_plugin_config)
    _register("component.list_loaded_plugins", manager._cap_component_list_loaded_plugins)
    _register("component.list_registered_plugins", manager._cap_component_list_registered_plugins)
    _register("component.enable", manager._cap_component_enable)
    _register("component.disable", manager._cap_component_disable)
    _register("component.load_plugin", manager._cap_component_load_plugin)
    _register("component.unload_plugin", manager._cap_component_unload_plugin)
    _register("component.reload_plugin", manager._cap_component_reload_plugin)

    _register("knowledge.search", manager._cap_knowledge_search)
    _register("render.html2png", manager._cap_render_html2png)
    logger.debug("已注册全部主程序能力实现")
