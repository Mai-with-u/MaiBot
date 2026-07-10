from __future__ import annotations

from typing import Sequence

from ...utils.runtime_payloads import merge_tokens
from .base import KernelServiceBase


class MemoryVectorDeleteService(KernelServiceBase):
    def _delete_vectors_by_type(
        self,
        *,
        paragraph_hashes: Sequence[str] = (),
        entity_hashes: Sequence[str] = (),
        relation_hashes: Sequence[str] = (),
    ) -> int:
        deleted = 0
        legacy_ids = merge_tokens(paragraph_hashes, entity_hashes, relation_hashes)
        if self.vector_store is not None and legacy_ids:
            deleted += int(self.vector_store.delete(legacy_ids) or 0)
        if not self._dual_vector_pools_enabled():
            return deleted
        paragraph_ids = merge_tokens(paragraph_hashes)
        if self.paragraph_vector_store is not None and paragraph_ids:
            deleted += int(self.paragraph_vector_store.delete(paragraph_ids) or 0)
        graph_ids = [
            self._graph_vector_id("entity", hash_value)
            for hash_value in merge_tokens(entity_hashes)
        ]
        graph_ids.extend(
            self._graph_vector_id("relation", hash_value)
            for hash_value in merge_tokens(relation_hashes)
        )
        if self.graph_vector_store is not None and graph_ids:
            deleted += int(self.graph_vector_store.delete(graph_ids) or 0)
        return deleted

