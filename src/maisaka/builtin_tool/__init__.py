"""Maisaka 内置工具聚合入口。"""

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from src.config.config import global_config, model_config
from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.llm_models.payload_content.tool_option import ToolDefinitionInput

from .context import BuiltinToolRuntimeContext
from .query_memory import get_tool_spec as get_query_memory_tool_spec
from .query_memory import handle_tool as handle_query_memory_tool
from .query_image_memory import get_tool_spec as get_query_image_memory_tool_spec
from .query_image_memory import handle_tool as handle_query_image_memory_tool
from .query_person_profile import get_tool_spec as get_query_person_profile_tool_spec
from .query_person_profile import handle_tool as handle_query_person_profile_tool
from .read_tool_result import get_tool_spec as get_read_tool_result_tool_spec
from .read_tool_result import handle_tool as handle_read_tool_result_tool
from .reply import get_tool_spec as get_reply_tool_spec
from .reply import handle_tool as handle_reply_tool
from .send_emoji import get_tool_spec as get_send_emoji_tool_spec
from .send_emoji import handle_tool as handle_send_emoji_tool
from .show_emoji_list import get_tool_spec as get_show_emoji_list_tool_spec
from .show_emoji_list import handle_tool as handle_show_emoji_list_tool
from .stop import get_tool_spec as get_stop_tool_spec
from .stop import handle_tool as handle_stop_tool
from .subagent import get_status_tool_spec as get_subagent_status_tool_spec
from .subagent import get_stop_tool_spec as get_subagent_stop_tool_spec
from .subagent import get_tool_spec as get_subagent_tool_spec
from .subagent import handle_status_tool as handle_subagent_status_tool
from .subagent import handle_stop_tool as handle_subagent_stop_tool
from .subagent import handle_tool as handle_subagent_tool
from .tool_search import get_tool_spec as get_tool_search_tool_spec
from .tool_search import handle_tool as handle_tool_search_tool
from .view_forward_message import get_tool_spec as get_view_forward_message_tool_spec
from .view_forward_message import handle_tool as handle_view_forward_message_tool
from .wait import get_tool_spec as get_wait_tool_spec
from .wait import handle_tool as handle_wait_tool

BuiltinToolHandler = Callable[[ToolInvocation, Optional[ToolExecutionContext]], Awaitable[ToolExecutionResult]]
BuiltinToolRawHandler = Callable[
    [BuiltinToolRuntimeContext, ToolInvocation, Optional[ToolExecutionContext]],
    Awaitable[ToolExecutionResult],
]
BuiltinToolStage = Literal["action", "both"]
BuiltinToolVisibility = Literal["visible", "deferred", "hidden"]
BuiltinToolChatScope = Literal["all", "group", "private"]


@dataclass(frozen=True)
class BuiltinToolEntry:
    """内置工具目录项，集中声明工具所属阶段与默认可见性。"""

    name: str
    get_spec: Callable[[], ToolSpec]
    handle_tool: BuiltinToolRawHandler
    stage: BuiltinToolStage
    visibility: BuiltinToolVisibility = "visible"
    chat_scope: BuiltinToolChatScope = "all"

    def build_spec(self, context: Optional[ToolAvailabilityContext] = None) -> ToolSpec:
        """生成带统一可见性元数据的工具声明。"""

        tool_spec = deepcopy(get_reply_tool_spec(context) if self.name == "reply" else self.get_spec())
        tool_spec.metadata["builtin_stage"] = self.stage
        tool_spec.metadata["visibility"] = self.visibility
        return tool_spec


def _get_query_memory_tool_spec() -> ToolSpec:
    """根据配置生成 query_memory 工具声明。"""

    return get_query_memory_tool_spec(enabled=bool(global_config.a_memorix.integration.enable_memory_query_tool))


def _get_query_person_profile_tool_spec() -> ToolSpec:
    """根据配置生成 query_person_profile 工具声明。"""

    return get_query_person_profile_tool_spec(
        enabled=bool(global_config.a_memorix.integration.enable_person_profile_query_tool)
    )


def _get_query_image_memory_tool_spec() -> ToolSpec:
    return get_query_image_memory_tool_spec(enabled=bool(global_config.a_memorix.image_memory.enabled))


BUILTIN_TOOL_ENTRIES: List[BuiltinToolEntry] = [
    BuiltinToolEntry("wait", get_wait_tool_spec, handle_wait_tool, stage="both"),
    BuiltinToolEntry("stop", get_stop_tool_spec, handle_stop_tool, stage="action"),
    BuiltinToolEntry("reply", get_reply_tool_spec, handle_reply_tool, stage="action"),
    BuiltinToolEntry(
        "view_forward_message",
        get_view_forward_message_tool_spec,
        handle_view_forward_message_tool,
        stage="action",
        visibility="deferred",
    ),
    BuiltinToolEntry("query_memory", _get_query_memory_tool_spec, handle_query_memory_tool, stage="action"),
    BuiltinToolEntry(
        "query_image_memory",
        _get_query_image_memory_tool_spec,
        handle_query_image_memory_tool,
        stage="action",
    ),
    BuiltinToolEntry(
        "query_person_profile",
        _get_query_person_profile_tool_spec,
        handle_query_person_profile_tool,
        stage="action",
    ),
    BuiltinToolEntry("send_emoji", get_send_emoji_tool_spec, handle_send_emoji_tool, stage="action"),
    BuiltinToolEntry("show_emoji_list", get_show_emoji_list_tool_spec, handle_show_emoji_list_tool, stage="action"),
    BuiltinToolEntry("tool_search", get_tool_search_tool_spec, handle_tool_search_tool, stage="action"),
    BuiltinToolEntry("subagent", get_subagent_tool_spec, handle_subagent_tool, stage="action"),
    BuiltinToolEntry("subagent_status", get_subagent_status_tool_spec, handle_subagent_status_tool, stage="action"),
    BuiltinToolEntry("subagent_stop", get_subagent_stop_tool_spec, handle_subagent_stop_tool, stage="action"),
    BuiltinToolEntry(
        "read_tool_result",
        get_read_tool_result_tool_spec,
        handle_read_tool_result_tool,
        stage="action",
    ),
]


def _get_builtin_tool_entries(
    *,
    stage: Optional[BuiltinToolStage] = None,
    visibility: Optional[BuiltinToolVisibility] = None,
    context: Optional[ToolAvailabilityContext] = None,
) -> List[BuiltinToolEntry]:
    """按阶段与可见性筛选内置工具目录项。"""

    entries = BUILTIN_TOOL_ENTRIES
    entries = [entry for entry in entries if _is_builtin_tool_enabled_by_config(entry)]
    if stage is not None:
        entries = [entry for entry in entries if entry.stage in {stage, "both"}]
    if visibility is not None:
        entries = [entry for entry in entries if entry.visibility == visibility]
    if context is not None:
        entries = [entry for entry in entries if _is_builtin_tool_available(entry, context)]
    return entries


def _is_builtin_tool_enabled_by_config(entry: BuiltinToolEntry) -> bool:
    """根据全局配置判断内置工具是否应暴露。"""

    if entry.name == "show_emoji_list":
        return global_config.emoji.use_new_send_logic
    if entry.name == "send_emoji":
        return not global_config.emoji.use_new_send_logic
    if entry.name == "subagent":
        return global_config.capabilities.planner_subagent
    if entry.name in {"subagent_status", "subagent_stop"}:
        # 只有后台子代理才有需要查看和终止的任务
        return global_config.capabilities.planner_subagent and global_config.capabilities.advanced_sub_agent
    if entry.name == "read_tool_result":
        # 长结果整理和统一联网工具均会暂存原文，任一开启都需要回查入口。
        return global_config.capabilities.tool_result_condense or bool(model_config.model_task_config.web_search.model_list)
    return True


def _is_builtin_tool_available(entry: BuiltinToolEntry, context: ToolAvailabilityContext) -> bool:
    """判断内置工具是否适用于当前聊天。"""

    if entry.chat_scope == "all":
        return True
    if entry.chat_scope == "group":
        return context.is_group_chat is True
    if entry.chat_scope == "private":
        return context.is_group_chat is False
    return True


def get_builtin_tool_visibility(tool_spec: ToolSpec) -> BuiltinToolVisibility:
    """读取工具声明里的可见性。"""

    raw_visibility = str(tool_spec.metadata.get("visibility") or "").strip()
    if raw_visibility == "deferred":
        return "deferred"
    if raw_visibility == "hidden":
        return "hidden"
    return "visible"


def is_builtin_tool_in_action_stage(tool_spec: ToolSpec) -> bool:
    """判断内置工具是否属于 Action Loop 阶段。"""

    return str(tool_spec.metadata.get("builtin_stage") or "").strip() in {"action", "both"}


def get_all_builtin_tool_specs(context: Optional[ToolAvailabilityContext] = None) -> List[ToolSpec]:
    """获取全部内置工具声明。"""

    return [entry.build_spec(context) for entry in _get_builtin_tool_entries(context=context)]


def get_builtin_tools(context: Optional[ToolAvailabilityContext] = None) -> List[ToolDefinitionInput]:
    """获取默认暴露给模型层的内置工具定义。"""

    tool_specs = [
        entry.build_spec(context)
        for entry in _get_builtin_tool_entries(stage="action", visibility="visible", context=context)
    ]
    return [tool_spec.to_llm_definition() for tool_spec in tool_specs if tool_spec.enabled]


def build_builtin_tool_handlers(tool_ctx: BuiltinToolRuntimeContext) -> Dict[str, BuiltinToolHandler]:
    """构建内置工具处理器映射。"""

    return {
        entry.name: lambda invocation, context=None, entry=entry: entry.handle_tool(tool_ctx, invocation, context)
        for entry in BUILTIN_TOOL_ENTRIES
    }
