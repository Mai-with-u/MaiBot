from __future__ import annotations

from typing import Any, Dict, Optional

import copy

from .base import KernelServiceBase


class MemoryRuntimeConfigService(KernelServiceBase):
    def _build_runtime_config(self, base_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runtime_config = dict(base_config if isinstance(base_config, dict) else self.config)
        runtime_cfg = runtime_config.get("runtime")
        runtime_config["runtime"] = dict(runtime_cfg) if isinstance(runtime_cfg, dict) else {}
        runtime_config["runtime"]["vector_pools_ready"] = self._dual_vector_pools_enabled()
        runtime_config.update(
            {
                "vector_store": self.vector_store,
                "paragraph_vector_store": self.paragraph_vector_store or self.vector_store,
                "graph_vector_store": self.graph_vector_store or self.vector_store,
                "graph_store": self.graph_store,
                "metadata_store": self.metadata_store,
                "embedding_manager": self.embedding_manager,
                "sparse_index": self.sparse_index,
                "relation_write_service": self.relation_write_service,
                "plugin_instance": self._runtime_facade,
            }
        )
        return runtime_config

    @staticmethod
    def _merge_runtime_config_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = MemoryRuntimeConfigService._merge_runtime_config_patch(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    async def apply_retrieval_tuning_profile(
        self,
        profile: Dict[str, Any],
        *,
        validate: bool = True,
    ) -> Dict[str, Any]:
        from .. import sdk_memory_kernel as kernel_module

        if not isinstance(profile, dict):
            return {
                "success": False,
                "runtime_rebuilt": False,
                "validation_passed": False,
                "error": "profile 必须是字典",
            }

        next_config = self._merge_runtime_config_patch(self.config, profile)
        runtime_bundle = kernel_module.build_search_runtime(
            plugin_config=self._build_runtime_config(next_config),
            logger_obj=kernel_module.logger,
            owner_tag="sdk_kernel_tuning_apply",
            log_prefix="[sdk]",
        )
        if validate and not runtime_bundle.ready:
            return {
                "success": False,
                "runtime_rebuilt": False,
                "validation_passed": False,
                "error": runtime_bundle.error or "检索运行时热重建失败",
            }
        if runtime_bundle.ready:
            self.config.clear()
            self.config.update(next_config)
            self._runtime_bundle = runtime_bundle
            self.retriever = runtime_bundle.retriever
            self.threshold_filter = runtime_bundle.threshold_filter
            self.sparse_index = runtime_bundle.sparse_index
            self._refresh_runtime_dependents(preserve_managers=True)
            self._apply_runtime_sparse_mode()
            return {
                "success": True,
                "runtime_rebuilt": True,
                "validation_passed": True,
                "error": "",
            }
        return {
            "success": False,
            "runtime_rebuilt": False,
            "validation_passed": False,
            "error": runtime_bundle.error or "检索运行时热重建失败",
        }
