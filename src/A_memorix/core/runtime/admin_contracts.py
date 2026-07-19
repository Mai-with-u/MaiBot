from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdminContractError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_response(self) -> Dict[str, Any]:
        return {"success": False, "error": self.message}


class AdminCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    component_name: str
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("component_name", "action", mode="before")
    @classmethod
    def _normalize_token(cls, value: Any) -> str:
        return str(value or "").strip().lower()


AdminDispatcher = Callable[[Any, AdminCommand], Awaitable[Any]]


class AdminComponentSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    component_name: str
    actions: frozenset[str]
    dispatcher: AdminDispatcher

    @field_validator("component_name", mode="before")
    @classmethod
    def _normalize_component_name(cls, value: Any) -> str:
        return str(value or "").strip().lower()

    @field_validator("actions", mode="before")
    @classmethod
    def _normalize_actions(cls, value: Any) -> frozenset[str]:
        return frozenset(str(item or "").strip().lower() for item in value if str(item or "").strip())


async def _dispatch_memory_graph_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_graph_admin(action=command.action, **command.payload)


async def _dispatch_memory_source_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_source_admin(action=command.action, **command.payload)


async def _dispatch_memory_episode_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_episode_admin(action=command.action, **command.payload)


async def _dispatch_memory_profile_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_profile_admin(action=command.action, **command.payload)


async def _dispatch_memory_feedback_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_feedback_admin(action=command.action, **command.payload)


async def _dispatch_memory_runtime_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_runtime_admin(action=command.action, **command.payload)


async def _dispatch_memory_import_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_import_admin(action=command.action, **command.payload)


async def _dispatch_memory_tuning_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_tuning_admin(action=command.action, **command.payload)


async def _dispatch_memory_v5_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_v5_admin(action=command.action, **command.payload)


async def _dispatch_memory_delete_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_delete_admin(action=command.action, **command.payload)


async def _dispatch_memory_correction_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_correction_admin(action=command.action, **command.payload)


async def _dispatch_memory_fuzzy_modify_admin(kernel: Any, command: AdminCommand) -> Any:
    return await kernel.memory_fuzzy_modify_admin(action=command.action, **command.payload)


_GRAPH_ACTIONS = {
    "get_graph",
    "search",
    "node_detail",
    "edge_detail",
    "create_node",
    "delete_node",
    "rename_node",
    "create_edge",
    "delete_edge",
    "update_edge_weight",
}
_SOURCE_ACTIONS = {"list", "delete", "batch_delete"}
_EPISODE_ACTIONS = {"query", "list", "get", "status", "rebuild", "process_sources"}
_PROFILE_ACTIONS = {
    "query",
    "evidence",
    "correct_evidence",
    "status",
    "process_pending",
    "list",
    "set_override",
    "delete_override",
}
_FEEDBACK_ACTIONS = {"list", "get", "rollback"}
_RUNTIME_ACTIONS = {
    "save",
    "get_config",
    "self_check",
    "refresh_self_check",
    "set_auto_save",
    "recover_embedding",
    "rebuild_all_vectors",
    "paragraph_backfill_once",
}
_IMPORT_ACTIONS = {
    "settings",
    "get_settings",
    "get_guide",
    "path_aliases",
    "get_path_aliases",
    "resolve_path",
    "resolve",
    "create_upload",
    "create_paste",
    "create_raw_scan",
    "create_lpmm_openie",
    "create_lpmm_convert",
    "create_temporal_backfill",
    "create_maibot_migration",
    "list",
    "get",
    "chunks",
    "get_chunks",
    "cancel",
    "retry_failed",
}
_TUNING_ACTIONS = {
    "settings",
    "get_settings",
    "get_profile",
    "apply_profile",
    "rollback_profile",
    "export_profile",
    "create_task",
    "list_tasks",
    "get_task",
    "get_rounds",
    "cancel",
    "apply_best",
    "get_report",
}
_V5_ACTIONS = {"recycle_bin", "status", "restore", "reinforce", "weaken", "remember_forever", "forget"}
_DELETE_ACTIONS = {"preview", "execute", "restore", "get_operation", "list_operations", "purge"}
_CORRECTION_ACTIONS = {"preview", "plan", "execute", "get", "list", "rollback"}


ADMIN_COMPONENT_SPECS: dict[str, AdminComponentSpec] = {
    "memory_graph_admin": AdminComponentSpec(
        component_name="memory_graph_admin",
        actions=frozenset(_GRAPH_ACTIONS),
        dispatcher=_dispatch_memory_graph_admin,
    ),
    "memory_source_admin": AdminComponentSpec(
        component_name="memory_source_admin",
        actions=frozenset(_SOURCE_ACTIONS),
        dispatcher=_dispatch_memory_source_admin,
    ),
    "memory_episode_admin": AdminComponentSpec(
        component_name="memory_episode_admin",
        actions=frozenset(_EPISODE_ACTIONS),
        dispatcher=_dispatch_memory_episode_admin,
    ),
    "memory_profile_admin": AdminComponentSpec(
        component_name="memory_profile_admin",
        actions=frozenset(_PROFILE_ACTIONS),
        dispatcher=_dispatch_memory_profile_admin,
    ),
    "memory_feedback_admin": AdminComponentSpec(
        component_name="memory_feedback_admin",
        actions=frozenset(_FEEDBACK_ACTIONS),
        dispatcher=_dispatch_memory_feedback_admin,
    ),
    "memory_runtime_admin": AdminComponentSpec(
        component_name="memory_runtime_admin",
        actions=frozenset(_RUNTIME_ACTIONS),
        dispatcher=_dispatch_memory_runtime_admin,
    ),
    "memory_import_admin": AdminComponentSpec(
        component_name="memory_import_admin",
        actions=frozenset(_IMPORT_ACTIONS),
        dispatcher=_dispatch_memory_import_admin,
    ),
    "memory_tuning_admin": AdminComponentSpec(
        component_name="memory_tuning_admin",
        actions=frozenset(_TUNING_ACTIONS),
        dispatcher=_dispatch_memory_tuning_admin,
    ),
    "memory_v5_admin": AdminComponentSpec(
        component_name="memory_v5_admin",
        actions=frozenset(_V5_ACTIONS),
        dispatcher=_dispatch_memory_v5_admin,
    ),
    "memory_delete_admin": AdminComponentSpec(
        component_name="memory_delete_admin",
        actions=frozenset(_DELETE_ACTIONS),
        dispatcher=_dispatch_memory_delete_admin,
    ),
    "memory_correction_admin": AdminComponentSpec(
        component_name="memory_correction_admin",
        actions=frozenset(_CORRECTION_ACTIONS),
        dispatcher=_dispatch_memory_correction_admin,
    ),
    "memory_fuzzy_modify_admin": AdminComponentSpec(
        component_name="memory_fuzzy_modify_admin",
        actions=frozenset(_CORRECTION_ACTIONS),
        dispatcher=_dispatch_memory_fuzzy_modify_admin,
    ),
}

ADMIN_COMPONENT_NAMES = frozenset(ADMIN_COMPONENT_SPECS)


def is_admin_component(component_name: str) -> bool:
    return str(component_name or "").strip().lower() in ADMIN_COMPONENT_SPECS


def parse_admin_command(component_name: str, payload: Mapping[str, Any] | None) -> AdminCommand:
    normalized_component = str(component_name or "").strip().lower()
    spec = ADMIN_COMPONENT_SPECS.get(normalized_component)
    if spec is None:
        raise AdminContractError(f"不支持的 A_Memorix admin component: {component_name}")
    if payload is not None and not isinstance(payload, Mapping):
        raise AdminContractError(f"{normalized_component} 参数必须为对象")

    kwargs = dict(payload or {})
    action = str(kwargs.pop("action", "") or "").strip().lower()
    if not action:
        raise AdminContractError(f"{normalized_component} action 不能为空")
    if action not in spec.actions:
        allowed = ", ".join(sorted(spec.actions))
        raise AdminContractError(f"不支持的 {normalized_component} action: {action}。允许值: {allowed}")

    return AdminCommand(component_name=normalized_component, action=action, payload=kwargs)


async def dispatch_admin_command(kernel: Any, command: AdminCommand) -> Any:
    spec = ADMIN_COMPONENT_SPECS.get(command.component_name)
    if spec is None:
        raise AdminContractError(f"不支持的 A_Memorix admin component: {command.component_name}")
    return await spec.dispatcher(kernel, command)
