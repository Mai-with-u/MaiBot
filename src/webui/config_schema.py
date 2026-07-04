import inspect
from functools import lru_cache
from typing import Any, Dict, List, get_args, get_origin

from pydantic_core import PydanticUndefined

from src.config.config_base import ConfigBase

AMEMORIX_BASIC_FIELD_PATHS: set[str] = {
    "a_memorix.filter.chats",
    "a_memorix.filter.enabled",
    "a_memorix.filter.mode",
    "a_memorix.global_memory_sharing_enabled",
    "a_memorix.integration.chat_summary_writeback_enabled",
    "a_memorix.integration.enable_memory_query_tool",
    "a_memorix.integration.enable_person_profile_query_tool",
    "a_memorix.integration.heuristic_memory_recall_enabled",
    "a_memorix.integration.memory_query_default_limit",
    "a_memorix.person_profile.enabled",
    "a_memorix.plugin.enabled",
    "a_memorix.shared_memory_groups",
    "a_memorix.shared_memory_groups[].targets",
    "a_memorix.shared_memory_groups[].targets[].item_id",
    "a_memorix.shared_memory_groups[].targets[].platform",
    "a_memorix.shared_memory_groups[].targets[].rule_type",
}

AMEMORIX_ADVANCED_FIELD_PATHS: set[str] = {
    "a_memorix.advanced.debug",
    "a_memorix.embedding.batch_size",
    "a_memorix.embedding.dimension_request_mode",
    "a_memorix.embedding.enable_cache",
    "a_memorix.embedding.fallback.enabled",
    "a_memorix.embedding.fallback.probe_interval_seconds",
    "a_memorix.embedding.max_concurrent",
    "a_memorix.embedding.model_name",
    "a_memorix.embedding.paragraph_vector_backfill.batch_size",
    "a_memorix.embedding.paragraph_vector_backfill.enabled",
    "a_memorix.embedding.paragraph_vector_backfill.max_retry",
    "a_memorix.episode.disabled_source_types",
    "a_memorix.episode.enabled",
    "a_memorix.episode.generation_enabled",
    "a_memorix.episode.max_chars_per_call",
    "a_memorix.episode.max_paragraphs_per_call",
    "a_memorix.episode.segmentation_model",
    "a_memorix.episode.source_time_window_hours",
    "a_memorix.filter.retrieval.chat_stream.chats",
    "a_memorix.filter.retrieval.chat_stream.enabled",
    "a_memorix.filter.retrieval.chat_stream.mode",
    "a_memorix.filter.retrieval.chat_summary.chats",
    "a_memorix.filter.retrieval.chat_summary.enabled",
    "a_memorix.filter.retrieval.chat_summary.mode",
    "a_memorix.filter.retrieval.episode.chats",
    "a_memorix.filter.retrieval.episode.enabled",
    "a_memorix.filter.retrieval.episode.mode",
    "a_memorix.integration.chat_summary_writeback_context_length",
    "a_memorix.integration.chat_summary_writeback_message_threshold",
    "a_memorix.integration.enable_person_profile_injection",
    "a_memorix.integration.feedback_correction_episode_rebuild_enabled",
    "a_memorix.integration.feedback_correction_max_feedback_messages",
    "a_memorix.integration.feedback_correction_profile_refresh_enabled",
    "a_memorix.integration.feedback_correction_window_hours",
    "a_memorix.integration.fuzzy_modify_candidate_limit",
    "a_memorix.integration.fuzzy_modify_enabled",
    "a_memorix.integration.heuristic_memory_recall_cache_ttl_seconds",
    "a_memorix.integration.heuristic_memory_recall_limit",
    "a_memorix.integration.heuristic_memory_recall_max_chars",
    "a_memorix.integration.heuristic_memory_recall_min_interval_seconds",
    "a_memorix.integration.heuristic_memory_recall_min_new_messages",
    "a_memorix.integration.heuristic_memory_recall_window_size",
    "a_memorix.integration.person_fact_writeback_enabled",
    "a_memorix.integration.person_profile_injection_max_profiles",
    "a_memorix.memory.enabled",
    "a_memorix.person_profile.active_window_hours",
    "a_memorix.person_profile.max_refresh_per_cycle",
    "a_memorix.person_profile.refresh_interval_minutes",
    "a_memorix.person_profile.top_k_evidence",
    "a_memorix.retrieval.enable_parallel",
    "a_memorix.retrieval.enable_ppr",
    "a_memorix.retrieval.ppr_timeout_seconds",
    "a_memorix.retrieval.relation_vectorization.write_on_import",
    "a_memorix.retrieval.search.smart_fallback.enabled",
    "a_memorix.retrieval.sparse.candidate_k",
    "a_memorix.retrieval.sparse.enabled",
    "a_memorix.retrieval.sparse.mode",
    "a_memorix.retrieval.sparse.relation_candidate_k",
    "a_memorix.retrieval.sparse.tokenizer_mode",
    "a_memorix.retrieval.top_k_final",
    "a_memorix.retrieval.top_k_paragraphs",
    "a_memorix.retrieval.top_k_relations",
    "a_memorix.retrieval.vector_pools.graph_expand_paragraph_k",
    "a_memorix.retrieval.vector_pools.graph_top_k",
    "a_memorix.retrieval.vector_pools.paragraph_top_k",
    "a_memorix.retrieval.vector_pools.relation_intent.graph_top_k",
    "a_memorix.threshold.enable_auto_adjust",
    "a_memorix.threshold.min_results",
    "a_memorix.web.import_config.enabled",
    "a_memorix.web.import_config.default_factual_target_size",
    "a_memorix.web.import_config.default_narrative_overlap",
    "a_memorix.web.import_config.default_narrative_window_size",
    "a_memorix.web.import_config.max_file_size_mb",
    "a_memorix.web.import_config.max_files_per_task",
    "a_memorix.web.import_config.max_paste_chars",
    "a_memorix.web.import_config.timeout.convert_preflight_seconds",
    "a_memorix.web.import_config.timeout.llm_call_seconds",
    "a_memorix.web.tuning.default_intensity",
    "a_memorix.web.tuning.default_objective",
    "a_memorix.web.tuning.default_sample_size",
    "a_memorix.web.tuning.default_top_k_eval",
    "a_memorix.web.tuning.enabled",
    "a_memorix.web.tuning.poll_interval_ms",
}

AMEMORIX_EXCLUDED_FIELD_PATHS: set[str] = {
    "a_memorix.advanced.auto_save_interval_minutes",
    "a_memorix.advanced.enable_auto_save",
    "a_memorix.embedding.dimension",
    "a_memorix.embedding.fallback.allow_metadata_only_write",
    "a_memorix.embedding.paragraph_vector_backfill.interval_seconds",
    "a_memorix.embedding.quantization_type",
    "a_memorix.episode.pending_batch_size",
    "a_memorix.episode.pending_max_retry",
    "a_memorix.integration.feedback_correction_auto_apply_threshold",
    "a_memorix.integration.feedback_correction_batch_size",
    "a_memorix.integration.feedback_correction_check_interval_minutes",
    "a_memorix.integration.feedback_correction_enabled",
    "a_memorix.integration.feedback_correction_episode_query_block_enabled",
    "a_memorix.integration.feedback_correction_paragraph_hard_filter_enabled",
    "a_memorix.integration.feedback_correction_paragraph_mark_enabled",
    "a_memorix.integration.feedback_correction_prefilter_enabled",
    "a_memorix.integration.feedback_correction_profile_force_refresh_on_read",
    "a_memorix.integration.feedback_correction_reconcile_batch_size",
    "a_memorix.integration.feedback_correction_reconcile_interval_minutes",
    "a_memorix.integration.fuzzy_modify_allow_global_scope",
    "a_memorix.integration.fuzzy_modify_auto_execute_enabled",
    "a_memorix.integration.fuzzy_modify_confirm_threshold",
    "a_memorix.integration.fuzzy_modify_max_targets",
    "a_memorix.integration.heuristic_memory_cross_chat_enabled",
    "a_memorix.integration.heuristic_memory_group_to_private_enabled",
    "a_memorix.integration.heuristic_memory_private_to_group_enabled",
    "a_memorix.memory.freeze_duration_hours",
    "a_memorix.memory.half_life_hours",
    "a_memorix.memory.prune_threshold",
    "a_memorix.person_profile.evidence_classification_max_tokens",
    "a_memorix.person_profile.evidence_classification_temperature",
    "a_memorix.person_profile.max_retry",
    "a_memorix.person_profile.refresh_debounce_seconds",
    "a_memorix.person_profile.refresh_queue_batch_size",
    "a_memorix.person_profile.refresh_queue_interval_seconds",
    "a_memorix.person_profile.refresh_retry_backoff_seconds",
    "a_memorix.retrieval.alpha",
    "a_memorix.retrieval.fusion.bm25_weight",
    "a_memorix.retrieval.fusion.method",
    "a_memorix.retrieval.fusion.rrf_k",
    "a_memorix.retrieval.fusion.vector_weight",
    "a_memorix.retrieval.ppr_alpha",
    "a_memorix.retrieval.ppr_concurrency_limit",
    "a_memorix.retrieval.relation_vectorization.backfill_enabled",
    "a_memorix.retrieval.relation_vectorization.enabled",
    "a_memorix.retrieval.sparse.backend",
    "a_memorix.retrieval.vector_pools.entity_evidence_weight",
    "a_memorix.retrieval.vector_pools.entity_expand_per_hit",
    "a_memorix.retrieval.vector_pools.graph_weight",
    "a_memorix.retrieval.vector_pools.mode",
    "a_memorix.retrieval.vector_pools.relation_evidence_weight",
    "a_memorix.retrieval.vector_pools.relation_expand_per_hit",
    "a_memorix.retrieval.vector_pools.relation_intent.graph_weight",
    "a_memorix.retrieval.vector_pools.relation_intent.return_relation_items",
    "a_memorix.retrieval.vector_pools.relation_intent.semantic_weight",
    "a_memorix.retrieval.vector_pools.relation_intent.sparse_weight",
    "a_memorix.retrieval.vector_pools.semantic_weight",
    "a_memorix.retrieval.vector_pools.sparse_weight",
    "a_memorix.storage.data_dir",
    "a_memorix.threshold.max_threshold",
    "a_memorix.threshold.min_threshold",
    "a_memorix.threshold.percentile",
    "a_memorix.web.import_config.default_chunk_concurrency",
    "a_memorix.web.import_config.default_file_concurrency",
    "a_memorix.web.import_config.max_chunk_chars",
    "a_memorix.web.import_config.max_queue_size",
    "a_memorix.web.import_config.timeout.process_kill_seconds",
    "a_memorix.web.import_config.timeout.process_poll_seconds",
    "a_memorix.web.import_config.timeout.process_terminate_seconds",
    "a_memorix.web.tuning.max_queue_size",
}

AMEMORIX_CLASS_PATH_PREFIX: Dict[str, str] = {
    "AMemorixAdvancedConfig": "a_memorix.advanced",
    "AMemorixConfig": "a_memorix",
    "AMemorixEmbeddingConfig": "a_memorix.embedding",
    "AMemorixEmbeddingFallbackConfig": "a_memorix.embedding.fallback",
    "AMemorixEpisodeConfig": "a_memorix.episode",
    "AMemorixFilterConfig": "a_memorix.filter",
    "AMemorixFusionRetrievalConfig": "a_memorix.retrieval.fusion",
    "AMemorixIntegrationConfig": "a_memorix.integration",
    "AMemorixMemoryEvolutionConfig": "a_memorix.memory",
    "AMemorixParagraphVectorBackfillConfig": "a_memorix.embedding.paragraph_vector_backfill",
    "AMemorixPersonProfileConfig": "a_memorix.person_profile",
    "AMemorixPluginConfig": "a_memorix.plugin",
    "AMemorixRelationIntentVectorPoolConfig": "a_memorix.retrieval.vector_pools.relation_intent",
    "AMemorixRelationVectorizationConfig": "a_memorix.retrieval.relation_vectorization",
    "AMemorixRetrievalConfig": "a_memorix.retrieval",
    "AMemorixRetrievalFilterConfig": "a_memorix.filter.retrieval",
    "AMemorixRetrievalSearchConfig": "a_memorix.retrieval.search",
    "AMemorixRetrievalSubtypeFilterConfig": "a_memorix.filter.retrieval.chat_stream",
    "AMemorixSmartFallbackConfig": "a_memorix.retrieval.search.smart_fallback",
    "AMemorixSparseRetrievalConfig": "a_memorix.retrieval.sparse",
    "AMemorixStorageConfig": "a_memorix.storage",
    "AMemorixThresholdConfig": "a_memorix.threshold",
    "AMemorixVectorPoolsConfig": "a_memorix.retrieval.vector_pools",
    "AMemorixWebConfig": "a_memorix.web",
    "AMemorixWebImportConfig": "a_memorix.web.import_config",
    "AMemorixWebImportTimeoutConfig": "a_memorix.web.import_config.timeout",
    "AMemorixWebTuningConfig": "a_memorix.web.tuning",
}


class ConfigSchemaGenerator:
    @staticmethod
    @lru_cache(maxsize=None)
    def _get_class_field_docs(config_class: type[ConfigBase]) -> Dict[str, str]:
        return config_class.get_class_field_docs()

    @staticmethod
    def _build_label(label: str) -> Dict[str, str]:
        return {"zh_CN": label}

    @classmethod
    def generate_schema(cls, config_class: type[ConfigBase], include_nested: bool = True) -> Dict[str, Any]:
        return cls.generate_config_schema(config_class, include_nested=include_nested)

    @classmethod
    def generate_config_schema(
        cls,
        config_class: type[ConfigBase],
        include_nested: bool = True,
        path_prefix: str = "",
    ) -> Dict[str, Any]:
        fields: List[Dict[str, Any]] = []
        nested: Dict[str, Dict[str, Any]] = {}
        field_docs = cls._get_class_field_docs(config_class)
        resolved_path_prefix = path_prefix or cls._default_path_prefix(config_class)

        for field_name, field_info in config_class.model_fields.items():
            if field_name in {"field_docs", "_validate_any", "suppress_any_warning"}:
                continue

            field_path = cls._join_field_path(resolved_path_prefix, field_name)
            nested_schema = cls._build_nested_schema(field_info.annotation, field_path) if include_nested else None
            visibility_schema = nested_schema
            visibility = cls._get_a_memorix_visibility(field_path)

            if visibility == "excluded":
                continue
            if nested_schema is not None and not cls._schema_has_visible_content(nested_schema):
                continue
            if visibility is None and cls._is_a_memorix_path(field_path):
                if visibility_schema is None and not include_nested:
                    visibility_schema = cls._build_nested_schema(field_info.annotation, field_path)
                if visibility_schema is None or not cls._schema_has_visible_content(visibility_schema):
                    continue
                visibility = "advanced" if cls._schema_has_only_advanced_content(visibility_schema) else "basic"

            field_schema = cls._build_field_schema(
                config_class,
                field_name,
                field_info.annotation,
                field_info,
                field_docs,
                field_path,
                visibility,
            )
            fields.append(field_schema)
            if nested_schema is not None:
                nested[field_name] = nested_schema

        schema: Dict[str, Any] = {
            "className": config_class.__name__,
            "classDoc": (config_class.__doc__ or "").strip(),
            "fields": fields,
            "nested": nested,
        }

        # 将 UI 分组元数据写入 schema
        ui_parent = getattr(config_class, "__ui_parent__", "")
        ui_label = getattr(config_class, "__ui_label__", "")
        ui_advanced = bool(getattr(config_class, "__ui_advanced__", False))
        ui_order = int(getattr(config_class, "__ui_order__", 0))
        ui_use_subtabs = bool(getattr(config_class, "__ui_use_subtabs__", False))
        ui_sub_label = getattr(config_class, "__ui_sub_label__", "")
        ui_root_sub_label = getattr(config_class, "__ui_root_sub_label__", "")
        if ui_parent:
            schema["uiParent"] = ui_parent
        if ui_label:
            schema["uiLabel"] = ui_label
        if ui_use_subtabs:
            schema["uiUseSubTabs"] = ui_use_subtabs
        if ui_sub_label:
            schema["uiSubLabel"] = ui_sub_label
        if ui_root_sub_label:
            schema["uiRootSubLabel"] = ui_root_sub_label
        schema["uiAdvanced"] = ui_advanced
        if ui_order:
            schema["uiOrder"] = ui_order

        return schema

    @classmethod
    def _build_nested_schema(cls, annotation: Any, path_prefix: str) -> Dict[str, Any] | None:
        origin = get_origin(annotation)
        args = get_args(annotation)

        if inspect.isclass(annotation) and issubclass(annotation, ConfigBase):
            return cls.generate_config_schema(annotation, path_prefix=path_prefix)

        if origin in {list, set, tuple} and args:
            first = args[0]
            if inspect.isclass(first) and issubclass(first, ConfigBase):
                return cls.generate_config_schema(first, path_prefix=f"{path_prefix}[]")

        return None

    @classmethod
    def _build_field_schema(
        cls,
        config_class: type[ConfigBase],
        field_name: str,
        annotation: Any,
        field_info: Any,
        field_docs: Dict[str, str],
        field_path: str,
        visibility: str | None,
    ) -> Dict[str, Any]:
        field_type = cls._map_field_type(annotation)
        raw_description = field_docs.get(field_name, field_info.description or "")
        # `_wrap_` 标记在配置类 docstring 中表示该说明应作为块级注释（独立成行）
        # 在前端展示时把它转为换行符，使描述以新行起始或在中间换行
        description = raw_description.replace("_wrap_", "\n").strip("\n")
        schema: Dict[str, Any] = {
            "name": field_name,
            "type": field_type,
            "label": cls._build_label(field_name),
            "description": description,
            "required": field_info.is_required(),
        }

        if field_info.default is not PydanticUndefined:
            schema["default"] = field_info.default

        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in {list, set} and args:
            schema["items"] = {"type": cls._map_field_type(args[0])}

        if options := cls._extract_options(annotation):
            schema["options"] = options

        # Task 1c: Merge json_schema_extra (x-widget, x-icon, step, etc.)
        if hasattr(field_info, "json_schema_extra") and field_info.json_schema_extra:
            schema.update(field_info.json_schema_extra)

        # Task 1d: Map Pydantic constraints to minValue/maxValue (frontend naming convention)
        if hasattr(field_info, "metadata") and field_info.metadata:
            for constraint in field_info.metadata:
                if hasattr(constraint, "ge"):
                    schema["minValue"] = constraint.ge
                if hasattr(constraint, "le"):
                    schema["maxValue"] = constraint.le

        cls._apply_a_memorix_visibility_policy(field_path, visibility, schema)

        return schema

    @staticmethod
    def _default_path_prefix(config_class: type[ConfigBase]) -> str:
        return AMEMORIX_CLASS_PATH_PREFIX.get(config_class.__name__, "")

    @staticmethod
    def _join_field_path(path_prefix: str, field_name: str) -> str:
        return f"{path_prefix}.{field_name}" if path_prefix else field_name

    @staticmethod
    def _is_a_memorix_path(field_path: str) -> bool:
        return field_path == "a_memorix" or field_path.startswith("a_memorix.")

    @staticmethod
    def _get_a_memorix_visibility(field_path: str) -> str | None:
        if field_path in AMEMORIX_BASIC_FIELD_PATHS:
            return "basic"
        if field_path in AMEMORIX_ADVANCED_FIELD_PATHS:
            return "advanced"
        if field_path in AMEMORIX_EXCLUDED_FIELD_PATHS:
            return "excluded"
        return None

    @staticmethod
    def _schema_has_visible_content(schema: Dict[str, Any]) -> bool:
        return bool(schema.get("fields") or schema.get("nested"))

    @classmethod
    def _schema_has_only_advanced_content(cls, schema: Dict[str, Any]) -> bool:
        for field in schema.get("fields", []):
            if not field.get("advanced", False):
                return False
        return all(cls._schema_has_only_advanced_content(nested) for nested in schema.get("nested", {}).values())

    @staticmethod
    def _apply_a_memorix_visibility_policy(
        field_path: str,
        visibility: str | None,
        schema: Dict[str, Any],
    ) -> None:
        if not ConfigSchemaGenerator._is_a_memorix_path(field_path):
            return

        if visibility == "advanced":
            schema["advanced"] = True

    @staticmethod
    def _extract_options(annotation: Any) -> List[str] | None:
        origin = get_origin(annotation)
        if origin is None:
            return None
        if str(origin) != "typing.Literal":
            return None

        args = get_args(annotation)
        options = [str(item) for item in args]
        return options or None

    @classmethod
    def _map_field_type(cls, annotation: Any) -> str:
        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in {list, set, tuple}:
            return "array"
        if inspect.isclass(annotation) and issubclass(annotation, ConfigBase):
            return "object"
        if annotation is bool:
            return "boolean"
        if annotation is int:
            return "integer"
        if annotation is float:
            return "number"
        if annotation is str:
            return "string"

        if origin in {list, set, tuple} and args:
            return "array"

        if origin in {dict}:
            return "object"

        if origin is not None and str(origin) == "typing.Literal":
            return "select"

        return "string"
