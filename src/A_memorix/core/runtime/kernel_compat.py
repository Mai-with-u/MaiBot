from __future__ import annotations

from typing import Any, Callable, Optional

from ..utils.runtime_payloads import optional_float, optional_int

_DELETE_ADMIN_SYNC_METHODS = (
    "_selector_dict",
    "_resolve_paragraph_targets",
    "_resolve_entity_targets",
    "_resolve_source_targets",
    "_snapshot_relation_item",
    "_snapshot_paragraph_item",
    "_snapshot_entity_item",
    "_relation_has_remaining_paragraphs",
    "_build_delete_preview_item",
    "_build_standard_delete_result",
)

_DELETE_ADMIN_ASYNC_METHODS = (
    "_build_delete_plan",
    "_preview_delete_action",
    "_execute_delete_action",
    "_invalidate_import_manifest_for_sources",
    "_restore_delete_action",
    "_restore_delete_operation",
    "_purge_deleted_memory",
)

_FEEDBACK_SYNC_METHODS = (
    "_resolve_feedback_related_person_ids",
    "_mark_feedback_stale_paragraphs",
    "_enqueue_feedback_episode_rebuilds",
    "_enqueue_feedback_profile_refreshes",
    "_feedback_affected_counts",
    "_build_feedback_rollback_plan_summary",
    "_build_feedback_task_summary",
    "_build_feedback_task_detail",
    "_soft_delete_feedback_correction_paragraphs",
    "_extract_feedback_messages",
    "_build_feedback_hit_briefs",
    "_should_invoke_feedback_classifier",
    "_normalize_feedback_decision",
    "_feedback_apply_result_status",
    "_restore_feedback_relations_from_snapshots",
    "_resolve_feedback_relation_hashes",
)

_FEEDBACK_ASYNC_METHODS = (
    "_rollback_feedback_task",
    "_process_feedback_profile_refresh_batch",
    "_process_feedback_episode_rebuild_batch",
    "_feedback_correction_reconcile_loop",
    "enqueue_feedback_task",
    "_classify_feedback",
    "_ingest_feedback_relations",
    "_apply_feedback_decision",
    "_process_feedback_task",
    "_feedback_correction_loop",
)

_GRAPH_ADMIN_SYNC_METHODS = (
    "_serialize_graph",
    "_graph_search_match_rank",
    "_pick_graph_search_match",
    "_search_graph",
    "_dedupe_strings",
    "_build_graph_edge_label",
    "_trim_text",
    "_format_relation_text",
    "_query_relation_rows_by_hashes",
    "_query_distinct_paragraph_hashes_for_relations",
    "_load_paragraph_rows",
    "_resolve_graph_node_name",
    "_get_related_relation_rows_for_entity",
    "_build_relation_summary",
    "_build_paragraph_summary",
    "_evidence_entity_node_id",
    "_evidence_relation_node_id",
    "_evidence_paragraph_node_id",
    "_build_evidence_graph",
    "_build_graph_node_detail",
    "_build_graph_edge_detail",
    "_delete_sources",
    "_apply_cleanup_plan",
    "_rebuild_graph_from_metadata",
    "_rename_node",
    "_update_edge_weight",
)

_CORRECTION_ADMIN_SYNC_METHODS = (
    "_is_fuzzy_modify_candidate_mutable",
    "_normalize_fuzzy_modify_plan",
    "_normalize_fuzzy_modify_candidate",
    "_normalize_fuzzy_modify_relations",
    "_build_fuzzy_modify_cascade_preview",
    "_build_fuzzy_modify_paragraph_cascade",
    "_fuzzy_modify_stale_source_operation_id",
    "_execute_fuzzy_modify_paragraph_cascade",
    "_mark_fuzzy_modify_target_superseded",
    "_normalize_fuzzy_modify_scope",
)

_CORRECTION_ADMIN_ASYNC_METHODS = (
    "_preview_fuzzy_modify_action",
    "_execute_fuzzy_modify_action",
    "_rollback_fuzzy_modify_action",
    "_collect_fuzzy_modify_candidates",
    "_build_fuzzy_modify_llm_plan",
    "_apply_fuzzy_modify_plan",
)


def _service_sync_proxy(service_attr: str, method_name: str) -> Callable[..., Any]:
    def proxy(self: Any, *args: Any, **kwargs: Any) -> Any:
        service = getattr(self, service_attr)
        service_method = getattr(service, method_name)
        return service_method(*args, **kwargs)

    proxy.__name__ = method_name
    proxy.__qualname__ = f"SDKMemoryKernel.{method_name}"
    return proxy


def _service_async_proxy(service_attr: str, method_name: str) -> Callable[..., Any]:
    async def proxy(self: Any, *args: Any, **kwargs: Any) -> Any:
        service = getattr(self, service_attr)
        service_method = getattr(service, method_name)
        return await service_method(*args, **kwargs)

    proxy.__name__ = method_name
    proxy.__qualname__ = f"SDKMemoryKernel.{method_name}"
    return proxy


def _optional_float_compat(value: Any) -> Optional[float]:
    return optional_float(value)


def _optional_int_compat(value: Any) -> Optional[int]:
    return optional_int(value)


def install_kernel_compat_methods(kernel_cls: type) -> None:
    """补回历史私有入口，让 SDKMemoryKernel 本体保持轻一些。"""

    for method_name in _DELETE_ADMIN_SYNC_METHODS:
        setattr(kernel_cls, method_name, _service_sync_proxy("_delete_admin_service", method_name))
    for method_name in _DELETE_ADMIN_ASYNC_METHODS:
        setattr(kernel_cls, method_name, _service_async_proxy("_delete_admin_service", method_name))
    for method_name in _FEEDBACK_SYNC_METHODS:
        setattr(kernel_cls, method_name, _service_sync_proxy("_feedback_service", method_name))
    for method_name in _FEEDBACK_ASYNC_METHODS:
        setattr(kernel_cls, method_name, _service_async_proxy("_feedback_service", method_name))
    for method_name in _GRAPH_ADMIN_SYNC_METHODS:
        setattr(kernel_cls, method_name, _service_sync_proxy("_graph_admin_service", method_name))
    for method_name in _CORRECTION_ADMIN_SYNC_METHODS:
        setattr(kernel_cls, method_name, _service_sync_proxy("_correction_admin_service", method_name))
    for method_name in _CORRECTION_ADMIN_ASYNC_METHODS:
        setattr(kernel_cls, method_name, _service_async_proxy("_correction_admin_service", method_name))
    kernel_cls._optional_float = staticmethod(_optional_float_compat)
    kernel_cls._optional_int = staticmethod(_optional_int_compat)
