"""
MaiBot 插件系统

提供统一的插件开发和管理框架
"""

# 导出主要的公共接口
from .base import (
    BasePlugin,
    BaseAction,
    BaseCommand,
    BaseTool,
    ConfigField,
    ConfigSection,
    ConfigLayout,
    ConfigTab,
    ComponentType,
    ActionActivationType,
    ChatMode,
    ComponentInfo,
    ActionInfo,
    CommandInfo,
    PluginInfo,
    ToolInfo,
    PythonDependency,
    BaseEventHandler,
    EventHandlerInfo,
    EventType,
    MaiMessages,
    ToolParamType,
    CustomEventHandlerResult,
    ReplyContentType,
    ReplyContent,
    ForwardNode,
    ReplySetModel,
)

# 导入工具模块
from .utils import (
    ManifestValidator,
    # ManifestGenerator,
    # validate_plugin_manifest,
    # generate_plugin_manifest,
)

from .apis import (
    chat_api,
    tool_api,
    component_manage_api,
    config_api,
    database_api,
    emoji_api,
    generator_api,
    llm_api,
    message_api,
    person_api,
    plugin_manage_api,
    send_api,
    auto_talk_api,
    register_plugin,
    get_logger,
)

from src.common.data_models.database_data_model import (
    DatabaseMessages,
    DatabaseUserInfo,
    DatabaseGroupInfo,
    DatabaseChatInfo,
)
from src.common.data_models.info_data_model import TargetPersonInfo, ActionPlannerInfo
from src.common.data_models.llm_data_model import LLMGenerationDataModel


__version__ = "2.0.0"

__all__ = [
    # API 模块
    "chat_api",
    "tool_api",
    "component_manage_api",
    "config_api",
    "database_api",
    "emoji_api",
    "generator_api",
    "llm_api",
    "message_api",
    "person_api",
    "plugin_manage_api",
    "send_api",
    "auto_talk_api",
    "register_plugin",
    "get_logger",
    # 基础类
    "BasePlugin",
    "BaseAction",
    "BaseCommand",
    "BaseTool",
    "BaseEventHandler",
    # 类型定义
    "ComponentType",
    "ActionActivationType",
    "ChatMode",
    "ComponentInfo",
    "ActionInfo",
    "CommandInfo",
    "PluginInfo",
    "ToolInfo",
    "PythonDependency",
    "EventHandlerInfo",
    "EventType",
    "ToolParamType",
    # 消息
    "ReplyContentType",
    "ReplyContent",
    "ForwardNode",
    "ReplySetModel",
    "MaiMessages",
    "CustomEventHandlerResult",
    # 装饰器
    "register_plugin",
    "ConfigField",
    "ConfigSection",
    "ConfigLayout",
    "ConfigTab",
    # 工具函数
    "ManifestValidator",
    "get_logger",
    # "ManifestGenerator",
    # "validate_plugin_manifest",
    # "generate_plugin_manifest",
    # 数据模型
    "DatabaseMessages",
    "DatabaseUserInfo",
    "DatabaseGroupInfo",
    "DatabaseChatInfo",
    "TargetPersonInfo",
    "ActionPlannerInfo",
    "LLMGenerationDataModel",
]
