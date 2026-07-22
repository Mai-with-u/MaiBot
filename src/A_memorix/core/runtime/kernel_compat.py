from __future__ import annotations

from typing import Any, Optional

from ..utils.runtime_payloads import optional_float, optional_int


class KernelCompatibilityMixin:
    """显式保留历史私有入口，避免运行时动态修改 SDKMemoryKernel。"""

    def _selector_dict(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._selector_dict(*args, **kwargs)

    def _resolve_paragraph_targets(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._resolve_paragraph_targets(*args, **kwargs)

    def _resolve_entity_targets(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._resolve_entity_targets(*args, **kwargs)

    def _resolve_source_targets(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._resolve_source_targets(*args, **kwargs)

    def _snapshot_relation_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._snapshot_relation_item(*args, **kwargs)

    def _snapshot_paragraph_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._snapshot_paragraph_item(*args, **kwargs)

    def _snapshot_entity_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._snapshot_entity_item(*args, **kwargs)

    def _relation_has_remaining_paragraphs(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._relation_has_remaining_paragraphs(*args, **kwargs)

    def _build_delete_preview_item(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._build_delete_preview_item(*args, **kwargs)

    def _build_standard_delete_result(self, *args: Any, **kwargs: Any) -> Any:
        return self._delete_admin_service._build_standard_delete_result(*args, **kwargs)

    async def _build_delete_plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._build_delete_plan(*args, **kwargs)

    async def _preview_delete_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._preview_delete_action(*args, **kwargs)

    async def _execute_delete_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._execute_delete_action(*args, **kwargs)

    async def _invalidate_import_manifest_for_sources(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._invalidate_import_manifest_for_sources(*args, **kwargs)

    async def _restore_delete_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._restore_delete_action(*args, **kwargs)

    async def _restore_delete_operation(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._restore_delete_operation(*args, **kwargs)

    async def _purge_deleted_memory(self, *args: Any, **kwargs: Any) -> Any:
        return await self._delete_admin_service._purge_deleted_memory(*args, **kwargs)

    def _resolve_feedback_related_person_ids(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._resolve_feedback_related_person_ids(*args, **kwargs)

    def _mark_feedback_stale_paragraphs(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._mark_feedback_stale_paragraphs(*args, **kwargs)

    def _enqueue_feedback_episode_rebuilds(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._enqueue_feedback_episode_rebuilds(*args, **kwargs)

    def _enqueue_feedback_profile_refreshes(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._enqueue_feedback_profile_refreshes(*args, **kwargs)

    def _feedback_affected_counts(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._feedback_affected_counts(*args, **kwargs)

    def _build_feedback_rollback_plan_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._build_feedback_rollback_plan_summary(*args, **kwargs)

    def _build_feedback_task_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._build_feedback_task_summary(*args, **kwargs)

    def _build_feedback_task_detail(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._build_feedback_task_detail(*args, **kwargs)

    def _soft_delete_feedback_correction_paragraphs(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._soft_delete_feedback_correction_paragraphs(*args, **kwargs)

    def _extract_feedback_messages(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._extract_feedback_messages(*args, **kwargs)

    def _build_feedback_hit_briefs(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._build_feedback_hit_briefs(*args, **kwargs)

    def _should_invoke_feedback_classifier(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._should_invoke_feedback_classifier(*args, **kwargs)

    def _normalize_feedback_decision(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._normalize_feedback_decision(*args, **kwargs)

    def _feedback_apply_result_status(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._feedback_apply_result_status(*args, **kwargs)

    def _restore_feedback_relations_from_snapshots(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._restore_feedback_relations_from_snapshots(*args, **kwargs)

    def _resolve_feedback_relation_hashes(self, *args: Any, **kwargs: Any) -> Any:
        return self._feedback_service._resolve_feedback_relation_hashes(*args, **kwargs)

    async def _rollback_feedback_task(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._rollback_feedback_task(*args, **kwargs)

    async def _process_feedback_profile_refresh_batch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._process_feedback_profile_refresh_batch(*args, **kwargs)

    async def _process_feedback_episode_rebuild_batch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._process_feedback_episode_rebuild_batch(*args, **kwargs)

    async def _feedback_correction_reconcile_loop(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._feedback_correction_reconcile_loop(*args, **kwargs)

    async def enqueue_feedback_task(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service.enqueue_feedback_task(*args, **kwargs)

    async def _classify_feedback(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._classify_feedback(*args, **kwargs)

    async def _ingest_feedback_relations(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._ingest_feedback_relations(*args, **kwargs)

    async def _apply_feedback_decision(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._apply_feedback_decision(*args, **kwargs)

    async def _process_feedback_task(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._process_feedback_task(*args, **kwargs)

    async def _feedback_correction_loop(self, *args: Any, **kwargs: Any) -> Any:
        return await self._feedback_service._feedback_correction_loop(*args, **kwargs)

    def _serialize_graph(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._serialize_graph(*args, **kwargs)

    def _graph_search_match_rank(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._graph_search_match_rank(*args, **kwargs)

    def _pick_graph_search_match(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._pick_graph_search_match(*args, **kwargs)

    def _search_graph(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._search_graph(*args, **kwargs)

    def _dedupe_strings(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._dedupe_strings(*args, **kwargs)

    def _build_graph_edge_label(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._build_graph_edge_label(*args, **kwargs)

    def _trim_text(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._trim_text(*args, **kwargs)

    def _format_relation_text(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._format_relation_text(*args, **kwargs)

    def _query_relation_rows_by_hashes(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._query_relation_rows_by_hashes(*args, **kwargs)

    def _query_distinct_paragraph_hashes_for_relations(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._query_distinct_paragraph_hashes_for_relations(*args, **kwargs)

    def _load_paragraph_rows(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._load_paragraph_rows(*args, **kwargs)

    def _resolve_graph_node_name(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._resolve_graph_node_name(*args, **kwargs)

    def _get_related_relation_rows_for_entity(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._get_related_relation_rows_for_entity(*args, **kwargs)

    def _build_relation_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._build_relation_summary(*args, **kwargs)

    def _build_paragraph_summary(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._build_paragraph_summary(*args, **kwargs)

    def _evidence_entity_node_id(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._evidence_entity_node_id(*args, **kwargs)

    def _evidence_relation_node_id(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._evidence_relation_node_id(*args, **kwargs)

    def _evidence_paragraph_node_id(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._evidence_paragraph_node_id(*args, **kwargs)

    def _build_evidence_graph(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._build_evidence_graph(*args, **kwargs)

    def _build_graph_node_detail(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._build_graph_node_detail(*args, **kwargs)

    def _build_graph_edge_detail(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._build_graph_edge_detail(*args, **kwargs)

    async def _delete_sources(self, *args: Any, **kwargs: Any) -> Any:
        return await self._graph_admin_service._delete_sources(*args, **kwargs)

    def _rebuild_graph_from_metadata(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._rebuild_graph_from_metadata(*args, **kwargs)

    def _rename_node(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._rename_node(*args, **kwargs)

    def _update_edge_weight(self, *args: Any, **kwargs: Any) -> Any:
        return self._graph_admin_service._update_edge_weight(*args, **kwargs)

    def _is_fuzzy_modify_candidate_mutable(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._is_fuzzy_modify_candidate_mutable(*args, **kwargs)

    def _normalize_fuzzy_modify_plan(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._normalize_fuzzy_modify_plan(*args, **kwargs)

    def _normalize_fuzzy_modify_candidate(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._normalize_fuzzy_modify_candidate(*args, **kwargs)

    def _normalize_fuzzy_modify_relations(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._normalize_fuzzy_modify_relations(*args, **kwargs)

    def _build_fuzzy_modify_cascade_preview(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._build_fuzzy_modify_cascade_preview(*args, **kwargs)

    def _build_fuzzy_modify_paragraph_cascade(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._build_fuzzy_modify_paragraph_cascade(*args, **kwargs)

    def _fuzzy_modify_stale_source_operation_id(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._fuzzy_modify_stale_source_operation_id(*args, **kwargs)

    def _execute_fuzzy_modify_paragraph_cascade(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._execute_fuzzy_modify_paragraph_cascade(*args, **kwargs)

    def _mark_fuzzy_modify_target_superseded(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._mark_fuzzy_modify_target_superseded(*args, **kwargs)

    def _normalize_fuzzy_modify_scope(self, *args: Any, **kwargs: Any) -> Any:
        return self._correction_admin_service._normalize_fuzzy_modify_scope(*args, **kwargs)

    async def _preview_fuzzy_modify_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service._preview_fuzzy_modify_action(*args, **kwargs)

    async def _execute_fuzzy_modify_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service._execute_fuzzy_modify_action(*args, **kwargs)

    async def _rollback_fuzzy_modify_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service._rollback_fuzzy_modify_action(*args, **kwargs)

    async def _collect_fuzzy_modify_candidates(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service._collect_fuzzy_modify_candidates(*args, **kwargs)

    async def _build_fuzzy_modify_llm_plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service._build_fuzzy_modify_llm_plan(*args, **kwargs)

    async def _apply_fuzzy_modify_plan(self, *args: Any, **kwargs: Any) -> Any:
        return await self._correction_admin_service._apply_fuzzy_modify_plan(*args, **kwargs)

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        return optional_float(value)

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        return optional_int(value)
