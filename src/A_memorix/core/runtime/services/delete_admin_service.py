from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from src.common.logger import get_logger

from ...utils.hash import compute_hash
from ...utils.metadata import coerce_metadata_dict
from ...utils.runtime_payloads import merge_tokens, optional_float, tokens
from .base import KernelServiceBase

logger = get_logger("A_Memorix.SDKMemoryKernel")


class MemoryDeleteAdminService(KernelServiceBase):
    async def memory_delete_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        act = str(action or "").strip().lower()
        mode = str(kwargs.get("mode", "") or "").strip().lower()
        selector = kwargs.get("selector")
        if selector is None:
            selector = {
                key: value
                for key, value in kwargs.items()
                if key
                not in {
                    "action",
                    "mode",
                    "dry_run",
                    "cascade",
                    "operation_id",
                    "reason",
                    "requested_by",
                }
            }
        reason = str(kwargs.get("reason", "") or "").strip()
        requested_by = str(kwargs.get("requested_by", "") or "").strip()

        if act == "preview":
            return await self._preview_delete_action(mode=mode, selector=selector)
        if act == "execute":
            result = await self._execute_delete_action(
                mode=mode,
                selector=selector,
                requested_by=requested_by,
                reason=reason,
            )
            await self._invalidate_import_manifest_for_sources(result)
            return result
        if act == "restore":
            return await self._restore_delete_action(
                mode=mode,
                selector=selector,
                operation_id=str(kwargs.get("operation_id", "") or "").strip(),
                requested_by=requested_by,
                reason=reason,
            )
        if act == "get_operation":
            operation = self.metadata_store.get_delete_operation(str(kwargs.get("operation_id", "") or "").strip())
            return {
                "success": operation is not None,
                "operation": operation,
                "error": "" if operation is not None else "operation 不存在",
            }
        if act == "list_operations":
            items = self.metadata_store.list_delete_operations(
                limit=max(1, int(kwargs.get("limit", 50) or 50)),
                mode=mode,
            )
            return {"success": True, "items": items, "count": len(items)}
        if act == "purge":
            return await self._purge_deleted_memory(
                grace_hours=optional_float(kwargs.get("grace_hours")),
                limit=max(1, int(kwargs.get("limit", 1000) or 1000)),
            )
        return {"success": False, "error": f"不支持的 delete action: {act}"}

    @staticmethod
    def _selector_dict(selector: Any) -> Dict[str, Any]:
        if isinstance(selector, dict):
            return dict(selector)
        if isinstance(selector, (list, tuple)):
            return {"items": list(selector)}
        token = str(selector or "").strip()
        return {"query": token} if token else {}

    def _resolve_paragraph_targets(self, selector: Any, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        assert self.metadata_store
        raw = self._selector_dict(selector)
        rows: List[Dict[str, Any]] = []
        hashes = merge_tokens(raw.get("hashes"), raw.get("items"), [raw.get("hash")])
        for hash_value in hashes:
            row = self.metadata_store.get_paragraph(hash_value)
            if row is None:
                continue
            if not include_deleted and bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        if rows:
            return rows
        query = str(raw.get("query", "") or raw.get("content", "") or "").strip()
        if not query:
            return []
        if len(query) == 64 and all(ch in "0123456789abcdef" for ch in query.lower()):
            row = self.metadata_store.get_paragraph(query)
            if row is None:
                return []
            if not include_deleted and bool(row.get("is_deleted", 0)):
                return []
            return [row]
        matches = self.metadata_store.search_paragraphs_by_content(query)
        return [row for row in matches if include_deleted or not bool(row.get("is_deleted", 0))]

    def _resolve_entity_targets(self, selector: Any, *, include_deleted: bool = False) -> List[Dict[str, Any]]:
        assert self.metadata_store
        raw = self._selector_dict(selector)
        rows: List[Dict[str, Any]] = []
        hashes = merge_tokens(raw.get("hashes"), raw.get("items"), [raw.get("hash")])
        for hash_value in hashes:
            row = self.metadata_store.get_entity(hash_value)
            if row is None:
                continue
            if not include_deleted and bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        names = merge_tokens(raw.get("names"), [raw.get("name")], [raw.get("query")])
        for name in names:
            if not name:
                continue
            matches = self.metadata_store.query(
                """
                SELECT *
                FROM entities
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
                   OR hash = ?
                ORDER BY appearance_count DESC, created_at ASC
                """,
                (name, compute_hash(str(name).strip().lower())),
            )
            for row in matches:
                if not include_deleted and bool(row.get("is_deleted", 0)):
                    continue
                rows.append(
                    self.metadata_store._row_to_dict(row, "entity")
                    if hasattr(self.metadata_store, "_row_to_dict")
                    else row
                )
        dedup: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            token = str(row.get("hash", "") or "").strip()
            if token and token not in dedup:
                dedup[token] = row
        return list(dedup.values())

    def _resolve_source_targets(self, selector: Any) -> List[str]:
        raw = self._selector_dict(selector)
        return merge_tokens(raw.get("sources"), [raw.get("source")], [raw.get("query")], raw.get("items"))

    def _snapshot_relation_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        relation = self.metadata_store.get_relation(hash_value)
        if relation is None:
            relation = self.metadata_store.get_deleted_relation(hash_value)
        if relation is None:
            return None
        paragraph_hashes = [
            str(row.get("paragraph_hash", "") or "").strip()
            for row in self.metadata_store.query(
                "SELECT paragraph_hash FROM paragraph_relations WHERE relation_hash = ? ORDER BY paragraph_hash ASC",
                (hash_value,),
            )
            if str(row.get("paragraph_hash", "") or "").strip()
        ]
        return {
            "item_type": "relation",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "relation": relation,
                "paragraph_hashes": paragraph_hashes,
            },
        }

    def _snapshot_paragraph_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        paragraph = self.metadata_store.get_paragraph(hash_value)
        if paragraph is None:
            return None
        entity_links = [
            {
                "paragraph_hash": hash_value,
                "entity_hash": str(row.get("entity_hash", "") or ""),
                "mention_count": int(row.get("mention_count", 1) or 1),
            }
            for row in self.metadata_store.query(
                """
                SELECT paragraph_hash, entity_hash, mention_count
                FROM paragraph_entities
                WHERE paragraph_hash = ?
                ORDER BY entity_hash ASC
                """,
                (hash_value,),
            )
        ]
        relation_hashes = [
            str(row.get("relation_hash", "") or "").strip()
            for row in self.metadata_store.query(
                """
                SELECT relation_hash
                FROM paragraph_relations
                WHERE paragraph_hash = ?
                ORDER BY relation_hash ASC
                """,
                (hash_value,),
            )
            if str(row.get("relation_hash", "") or "").strip()
        ]
        return {
            "item_type": "paragraph",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "paragraph": paragraph,
                "entity_links": entity_links,
                "relation_hashes": relation_hashes,
                "external_refs": self.metadata_store.list_external_memory_refs_by_paragraphs([hash_value]),
            },
        }

    def _snapshot_entity_item(self, hash_value: str) -> Optional[Dict[str, Any]]:
        assert self.metadata_store
        entity = self.metadata_store.get_entity(hash_value)
        if entity is None:
            return None
        paragraph_links = [
            {
                "paragraph_hash": str(row.get("paragraph_hash", "") or ""),
                "entity_hash": hash_value,
                "mention_count": int(row.get("mention_count", 1) or 1),
            }
            for row in self.metadata_store.query(
                """
                SELECT paragraph_hash, mention_count
                FROM paragraph_entities
                WHERE entity_hash = ?
                ORDER BY paragraph_hash ASC
                """,
                (hash_value,),
            )
        ]
        return {
            "item_type": "entity",
            "item_hash": hash_value,
            "item_key": hash_value,
            "payload": {
                "entity": entity,
                "paragraph_links": paragraph_links,
            },
        }

    def _relation_has_remaining_paragraphs(self, relation_hash: str, removing_hashes: Sequence[str]) -> bool:
        assert self.metadata_store
        excluded = [str(item or "").strip() for item in removing_hashes if str(item or "").strip()]
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        if excluded:
            placeholders = ",".join(["?"] * len(excluded))
            cursor.execute(
                f"""
                SELECT p.hash, p.metadata
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash = ?
                  AND pr.paragraph_hash NOT IN ({placeholders})
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                """,
                tuple([relation_hash] + excluded),
            )
        else:
            cursor.execute(
                """
                SELECT p.hash, p.metadata
                FROM paragraph_relations pr
                JOIN paragraphs p ON p.hash = pr.paragraph_hash
                WHERE pr.relation_hash = ?
                  AND (p.is_deleted IS NULL OR p.is_deleted = 0)
                """,
                (relation_hash,),
            )
        now = time.time()
        for row in cursor.fetchall():
            paragraph = self.metadata_store._row_to_dict(row, "paragraph")
            metadata = coerce_metadata_dict(paragraph.get("metadata"))
            memory_change = metadata.get("memory_change") if isinstance(metadata.get("memory_change"), dict) else {}
            valid_to = optional_float(memory_change.get("valid_to"))
            if valid_to is None or valid_to > now:
                return True
        return False

    def _build_delete_preview_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        item_type = str(item.get("item_type", "") or "").strip()
        item_hash = str(item.get("item_hash", "") or "").strip()
        item_key = str(item.get("item_key", "") or item_hash).strip()
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        preview = {
            "item_type": item_type,
            "item_hash": item_hash,
            "item_key": item_key,
        }
        if item_type == "entity":
            entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
            name = str(entity.get("name", "") or item_key).strip()
            preview["label"] = name
            preview["preview"] = name
        elif item_type == "relation":
            relation = payload.get("relation") if isinstance(payload.get("relation"), dict) else {}
            subject = str(relation.get("subject", "") or "").strip()
            predicate = str(relation.get("predicate", "") or "").strip()
            obj = str(relation.get("object", "") or "").strip()
            text = self._format_relation_text(subject, predicate, obj)
            preview["label"] = text or item_key
            preview["preview"] = text or item_key
        elif item_type == "paragraph":
            paragraph = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
            content = str(paragraph.get("content", "") or "").strip()
            source = str(paragraph.get("source", "") or "").strip()
            preview["label"] = source or item_key
            preview["preview"] = self._trim_text(content)
            preview["source"] = source
        return preview

    def _build_standard_delete_result(
        self,
        *,
        mode: str,
        operation_id: str = "",
        counts: Optional[Dict[str, Any]] = None,
        sources: Optional[Sequence[str]] = None,
        deleted_entity_count: int = 0,
        deleted_relation_count: int = 0,
        deleted_paragraph_count: int = 0,
        deleted_source_count: int = 0,
        deleted_vector_count: int = 0,
        requested_source_count: int = 0,
        matched_source_count: int = 0,
        error: str = "",
    ) -> Dict[str, Any]:
        normalized_counts = dict(counts or {})
        normalized_counts.setdefault("entities", int(normalized_counts.get("entities", 0) or 0))
        normalized_counts.setdefault("relations", int(normalized_counts.get("relations", 0) or 0))
        normalized_counts.setdefault("paragraphs", int(normalized_counts.get("paragraphs", 0) or 0))
        normalized_counts.setdefault("sources", int(normalized_counts.get("sources", 0) or 0))
        if requested_source_count:
            normalized_counts["requested_sources"] = int(requested_source_count or 0)
        if matched_source_count:
            normalized_counts["matched_sources"] = int(matched_source_count or 0)

        deleted_count = (
            int(deleted_entity_count or 0)
            + int(deleted_relation_count or 0)
            + int(deleted_paragraph_count or 0)
            + int(deleted_source_count or 0)
        )
        return {
            "success": bool(not error and deleted_count > 0),
            "mode": str(mode or "").strip().lower(),
            "operation_id": str(operation_id or "").strip(),
            "counts": normalized_counts,
            "sources": [str(item or "").strip() for item in (sources or []) if str(item or "").strip()],
            "deleted_count": deleted_count,
            "deleted_entity_count": int(deleted_entity_count or 0),
            "deleted_relation_count": int(deleted_relation_count or 0),
            "deleted_paragraph_count": int(deleted_paragraph_count or 0),
            "deleted_source_count": int(deleted_source_count or 0),
            "deleted_vector_count": int(deleted_vector_count or 0),
            "requested_source_count": int(requested_source_count or 0),
            "matched_source_count": int(matched_source_count or 0),
            "error": str(error or ""),
        }

    async def _build_delete_plan(self, *, mode: str, selector: Any) -> Dict[str, Any]:
        """解析删除选择器并生成不执行写操作的完整删除计划。

        计划同时收集快照、向量 ID、来源以及因段落或实体失去引用而需要级联处理的
        关系。预览和正式执行共用该结果，避免两条路径对删除范围作出不同判断。
        """
        assert self.metadata_store
        act_mode = str(mode or "").strip().lower()
        normalized_selector = self._selector_dict(selector)
        items: List[Dict[str, Any]] = []
        counts = {"relations": 0, "paragraphs": 0, "entities": 0, "sources": 0}
        vector_ids: List[str] = []
        sources: List[str] = []
        target_hashes: Dict[str, List[str]] = {
            "relations": [],
            "paragraphs": [],
            "entities": [],
            "sources": [],
            "matched_sources": [],
        }
        seen_items: set[tuple[str, str]] = set()
        relation_hashes: List[str] = []
        paragraph_hashes: List[str] = []
        entity_hashes: List[str] = []
        paragraph_relation_candidates: List[str] = []

        def append_item(snapshot: Optional[Dict[str, Any]]) -> None:
            if not isinstance(snapshot, dict):
                return
            item_type = str(snapshot.get("item_type", "") or "").strip()
            item_hash = str(snapshot.get("item_hash", "") or snapshot.get("item_key", "") or "").strip()
            if not item_type or not item_hash:
                return
            key = (item_type, item_hash)
            if key in seen_items:
                return
            seen_items.add(key)
            items.append(snapshot)

        def append_relation_hash(hash_value: str) -> None:
            token = str(hash_value or "").strip()
            if not token or token in relation_hashes:
                return
            row = self.metadata_store.get_relation(token)
            if row is None:
                return
            relation_hashes.append(token)
            append_item(self._snapshot_relation_item(token))
            vector_ids.append(token)

        def append_paragraph_row(row: Optional[Dict[str, Any]]) -> None:
            if not isinstance(row, dict):
                return
            paragraph_hash = str(row.get("hash", "") or "").strip()
            if not paragraph_hash or paragraph_hash in paragraph_hashes or bool(row.get("is_deleted", 0)):
                return
            paragraph_hashes.append(paragraph_hash)
            snapshot = self._snapshot_paragraph_item(paragraph_hash)
            append_item(snapshot)
            vector_ids.append(paragraph_hash)
            paragraph = (
                (snapshot or {}).get("payload", {}).get("paragraph")
                if isinstance((snapshot or {}).get("payload"), dict)
                else {}
            )
            source = str((paragraph or {}).get("source", "") or "").strip()
            if source:
                sources.append(source)
            paragraph_relation_candidates.extend(tokens(((snapshot or {}).get("payload") or {}).get("relation_hashes")))

        def append_entity_row(row: Optional[Dict[str, Any]]) -> None:
            if not isinstance(row, dict):
                return
            entity_hash = str(row.get("hash", "") or "").strip()
            if not entity_hash or entity_hash in entity_hashes or bool(row.get("is_deleted", 0)):
                return
            entity_hashes.append(entity_hash)
            append_item(self._snapshot_entity_item(entity_hash))
            vector_ids.append(entity_hash)

        if act_mode == "relation":
            direct_hashes = merge_tokens(
                normalized_selector.get("hashes"),
                normalized_selector.get("items"),
                [normalized_selector.get("hash")],
            )
            query_hashes = self._resolve_relation_hashes(str(normalized_selector.get("query", "") or ""))
            for hash_value in direct_hashes or query_hashes:
                append_relation_hash(hash_value)
            counts["relations"] = len(relation_hashes)
            target_hashes["relations"] = list(relation_hashes)

        elif act_mode in {"paragraph", "source"}:
            paragraph_rows: List[Dict[str, Any]] = []
            if act_mode == "source":
                source_tokens = self._resolve_source_targets(normalized_selector)
                target_hashes["sources"] = source_tokens
                counts["requested_sources"] = len(source_tokens)
                matched_source_tokens: List[str] = []
                for source in source_tokens:
                    source_rows = self.metadata_store.query(
                        """
                        SELECT *
                        FROM paragraphs
                        WHERE source = ?
                          AND (is_deleted IS NULL OR is_deleted = 0)
                        ORDER BY created_at ASC
                        """,
                        (source,),
                    )
                    if source_rows:
                        matched_source_tokens.append(source)
                        sources.append(source)
                        paragraph_rows.extend(source_rows)
                target_hashes["matched_sources"] = matched_source_tokens
                counts["sources"] = len(matched_source_tokens)
                counts["matched_sources"] = len(matched_source_tokens)
            else:
                paragraph_rows = self._resolve_paragraph_targets(normalized_selector, include_deleted=False)
            for row in paragraph_rows:
                append_paragraph_row(row)
            target_hashes["paragraphs"] = list(paragraph_hashes)
            counts["paragraphs"] = len(paragraph_hashes)

            for relation_hash in tokens(paragraph_relation_candidates):
                if not self._relation_has_remaining_paragraphs(relation_hash, paragraph_hashes):
                    append_relation_hash(relation_hash)
            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)

        elif act_mode == "entity":
            entity_rows = self._resolve_entity_targets(normalized_selector, include_deleted=False)
            for row in entity_rows:
                append_entity_row(row)
            target_hashes["entities"] = list(entity_hashes)
            counts["entities"] = len(entity_hashes)
            entity_names = [
                str(row.get("name", "") or "").strip() for row in entity_rows if str(row.get("name", "") or "").strip()
            ]
            for entity_name in entity_names:
                for relation in self.metadata_store.get_relations(
                    subject=entity_name
                ) + self.metadata_store.get_relations(object=entity_name):
                    append_relation_hash(str(relation.get("hash", "") or "").strip())
            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)
        elif act_mode == "mixed":
            source_tokens = merge_tokens(normalized_selector.get("sources"), [normalized_selector.get("source")])
            target_hashes["sources"] = list(source_tokens)
            counts["requested_sources"] = len(source_tokens)
            matched_source_tokens: List[str] = []

            for row in self._resolve_entity_targets(
                {"hashes": normalized_selector.get("entity_hashes")}, include_deleted=False
            ):
                append_entity_row(row)
            target_hashes["entities"] = list(entity_hashes)
            counts["entities"] = len(entity_hashes)

            for row in self._resolve_paragraph_targets(
                {"hashes": normalized_selector.get("paragraph_hashes")}, include_deleted=False
            ):
                append_paragraph_row(row)

            for source in source_tokens:
                source_rows = self.metadata_store.query(
                    """
                    SELECT *
                    FROM paragraphs
                    WHERE source = ?
                      AND (is_deleted IS NULL OR is_deleted = 0)
                    ORDER BY created_at ASC
                    """,
                    (source,),
                )
                if source_rows:
                    matched_source_tokens.append(source)
                    sources.append(source)
                    for row in source_rows:
                        append_paragraph_row(row)

            target_hashes["paragraphs"] = list(paragraph_hashes)
            counts["paragraphs"] = len(paragraph_hashes)
            target_hashes["matched_sources"] = matched_source_tokens
            counts["sources"] = len(matched_source_tokens)
            counts["matched_sources"] = len(matched_source_tokens)

            for hash_value in tokens(normalized_selector.get("relation_hashes")):
                append_relation_hash(hash_value)

            entity_names = [
                str(row.get("name", "") or "").strip()
                for row in self._resolve_entity_targets({"hashes": entity_hashes}, include_deleted=False)
                if str(row.get("name", "") or "").strip()
            ]
            for entity_name in entity_names:
                for relation in self.metadata_store.get_relations(
                    subject=entity_name
                ) + self.metadata_store.get_relations(object=entity_name):
                    append_relation_hash(str(relation.get("hash", "") or "").strip())

            for relation_hash in tokens(paragraph_relation_candidates):
                if not self._relation_has_remaining_paragraphs(relation_hash, paragraph_hashes):
                    append_relation_hash(relation_hash)

            target_hashes["relations"] = list(relation_hashes)
            counts["relations"] = len(relation_hashes)
        else:
            return {"success": False, "error": f"不支持的 delete mode: {act_mode}"}

        sources = tokens(sources)
        vector_ids = tokens(vector_ids)
        primary_count = (
            counts.get(f"{act_mode}s", 0) if act_mode not in {"source", "mixed"} else counts.get("matched_sources", 0)
        )
        success = (
            primary_count > 0
            or counts.get("paragraphs", 0) > 0
            or counts.get("relations", 0) > 0
            or counts.get("entities", 0) > 0
            if act_mode != "source"
            else (counts.get("matched_sources", 0) > 0 and counts.get("paragraphs", 0) > 0)
        )
        return {
            "success": success,
            "mode": act_mode,
            "selector": normalized_selector,
            "items": items,
            "counts": counts,
            "vector_ids": vector_ids,
            "sources": sources,
            "target_hashes": target_hashes,
            "requested_source_count": counts.get("requested_sources", 0) if act_mode == "source" else 0,
            "matched_source_count": counts.get("matched_sources", 0) if act_mode == "source" else 0,
            "error": "" if success else "未命中可删除内容",
        }

    async def _preview_delete_action(self, *, mode: str, selector: Any) -> Dict[str, Any]:
        plan = await self._build_delete_plan(mode=mode, selector=selector)
        if not plan.get("success", False):
            return {"success": False, "error": plan.get("error", "未命中可删除内容")}
        preview_items = [self._build_delete_preview_item(item) for item in plan.get("items", [])[:100]]
        return {
            "success": True,
            "mode": plan.get("mode"),
            "selector": plan.get("selector"),
            "counts": plan.get("counts", {}),
            "requested_source_count": int(plan.get("requested_source_count", 0) or 0),
            "matched_source_count": int(plan.get("matched_source_count", 0) or 0),
            "sources": plan.get("sources", []),
            "vector_ids": plan.get("vector_ids", []),
            "items": preview_items,
            "item_count": len(plan.get("items", [])),
            "dry_run": True,
        }

    async def _execute_delete_action(
        self,
        *,
        mode: str,
        selector: Any,
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        """执行删除计划，并记录后续恢复所需的操作快照。

        元数据事务先于关系、向量和图存储处理提交，整个流程不是跨存储原子事务。
        因此失败响应只表示流程未完整完成，不表示此前阶段一定没有产生变更。
        """
        assert self.metadata_store
        plan = await self._build_delete_plan(mode=mode, selector=selector)
        if not plan.get("success", False):
            return {"success": False, "error": plan.get("error", "未命中可删除内容")}

        act_mode = str(plan.get("mode", "") or "").strip().lower()
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        paragraph_hashes = tokens((plan.get("target_hashes") or {}).get("paragraphs"))
        entity_hashes = tokens((plan.get("target_hashes") or {}).get("entities"))
        relation_hashes = tokens((plan.get("target_hashes") or {}).get("relations"))
        requested_source_tokens = tokens((plan.get("target_hashes") or {}).get("sources"))
        matched_source_tokens = tokens((plan.get("target_hashes") or {}).get("matched_sources"))
        operation = self.metadata_store.create_delete_operation(
            mode=act_mode,
            selector=plan.get("selector"),
            items=plan.get("items", []),
            reason=reason,
            requested_by=requested_by,
            summary={
                "counts": plan.get("counts", {}),
                "sources": plan.get("sources", []),
                "vector_ids": plan.get("vector_ids", []),
                "state": "prepared",
            },
        )
        operation_id = str(operation.get("operation_id", "") or "")

        try:
            if paragraph_hashes:
                self.metadata_store.mark_as_deleted(paragraph_hashes, "paragraph")
                cursor.execute(
                    f"DELETE FROM paragraph_entities WHERE paragraph_hash IN ({','.join(['?'] * len(paragraph_hashes))})",
                    tuple(paragraph_hashes),
                )
                cursor.execute(
                    f"DELETE FROM paragraph_relations WHERE paragraph_hash IN ({','.join(['?'] * len(paragraph_hashes))})",
                    tuple(paragraph_hashes),
                )
                self.metadata_store.delete_external_memory_refs_by_paragraphs(paragraph_hashes)
            if act_mode == "source" and matched_source_tokens:
                for source in matched_source_tokens:
                    self.metadata_store.replace_episodes_for_source(source, [])

            if entity_hashes:
                self.metadata_store.mark_as_deleted(entity_hashes, "entity")
                cursor.execute(
                    f"DELETE FROM paragraph_entities WHERE entity_hash IN ({','.join(['?'] * len(entity_hashes))})",
                    tuple(entity_hashes),
                )

            conn.commit()

            self.metadata_store.backup_and_delete_relations(relation_hashes)
            deleted_vectors = self._delete_vectors_by_type(
                paragraph_hashes=paragraph_hashes,
                entity_hashes=entity_hashes,
                relation_hashes=relation_hashes,
            )

            if plan.get("sources"):
                self.metadata_store._enqueue_episode_source_rebuilds(
                    list(plan.get("sources") or []), reason="delete_admin_execute"
                )
            self._rebuild_graph_from_metadata()
            self._persist()
            return self._build_standard_delete_result(
                mode=act_mode,
                operation_id=operation_id,
                counts=plan.get("counts", {}),
                sources=plan.get("sources", []),
                deleted_entity_count=len(entity_hashes),
                deleted_relation_count=len(relation_hashes),
                deleted_paragraph_count=len(paragraph_hashes),
                deleted_source_count=len(matched_source_tokens),
                deleted_vector_count=int(deleted_vectors or 0),
                requested_source_count=len(requested_source_tokens),
                matched_source_count=len(matched_source_tokens),
                error=""
                if (entity_hashes or relation_hashes or paragraph_hashes or matched_source_tokens)
                else "未命中可删除内容",
            )
        except Exception as exc:
            conn.rollback()
            logger.warning(f"delete_admin execute 失败: {exc}")
            return self._build_standard_delete_result(mode=act_mode, operation_id=operation_id, error=str(exc))

    async def _invalidate_import_manifest_for_sources(self, result: Dict[str, Any]) -> None:
        if not isinstance(result, dict) or not result.get("success"):
            return
        manager = self.import_task_manager
        if manager is None:
            return
        sources = tokens(result.get("sources"))
        if not sources:
            return
        try:
            manifest_result = await manager.invalidate_manifest_for_sources(sources)
        except Exception as exc:
            logger.warning(f"删除来源后清理导入清单失败: sources={sources}, err={exc}")
            result["manifest_invalidation"] = {"success": False, "error": str(exc), "sources": sources}
            return
        result["manifest_invalidation"] = manifest_result

    async def _restore_delete_action(
        self,
        *,
        mode: str,
        selector: Any,
        operation_id: str = "",
        requested_by: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        del requested_by
        del reason
        assert self.metadata_store

        op_id = str(operation_id or "").strip()
        if op_id:
            operation = self.metadata_store.get_delete_operation(op_id)
            if operation is None:
                return {"success": False, "error": "operation 不存在"}
            return await self._restore_delete_operation(operation)

        act_mode = str(mode or "").strip().lower()
        if act_mode != "relation":
            return {"success": False, "error": "paragraph/entity/source 恢复必须提供 operation_id"}

        raw = self._selector_dict(selector)
        target = str(raw.get("query", "") or raw.get("target", "") or raw.get("hash", "") or "").strip()
        hashes = self._resolve_deleted_relation_hashes(target)
        if not hashes:
            return {"success": False, "error": "未命中可恢复关系"}
        result = await self._restore_relation_hashes(hashes)
        return {"success": bool(result.get("restored_count", 0) > 0), **result}

    async def _restore_delete_operation(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        assert self.metadata_store
        items = operation.get("items") if isinstance(operation.get("items"), list) else []
        entity_payloads: Dict[str, Dict[str, Any]] = {}
        paragraph_payloads: Dict[str, Dict[str, Any]] = {}
        relation_payloads: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("item_type", "") or "").strip()
            item_hash = str(item.get("item_hash", "") or "").strip()
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if item_type == "entity" and item_hash:
                entity_payloads[item_hash] = payload
            elif item_type == "paragraph" and item_hash:
                paragraph_payloads[item_hash] = payload
            elif item_type == "relation" and item_hash:
                relation_payloads[item_hash] = payload

        restored_entities: List[str] = []
        restored_paragraphs: List[str] = []
        for hash_value, payload in entity_payloads.items():
            entity_row = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
            if entity_row:
                self.metadata_store.restore_entity_by_hash(hash_value)
                await self._ensure_entity_vector(entity_row)
                restored_entities.append(hash_value)
        for hash_value, payload in paragraph_payloads.items():
            paragraph_row = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
            if paragraph_row:
                self.metadata_store.restore_paragraph_by_hash(hash_value)
                await self._ensure_paragraph_vector(paragraph_row)
                restored_paragraphs.append(hash_value)

        restored_relations = await self._restore_relation_hashes(
            list(relation_payloads.keys()), payloads=relation_payloads, rebuild_graph=False, persist=False
        )

        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        for payload in entity_payloads.values():
            for link in payload.get("paragraph_links") or []:
                paragraph_hash = str(link.get("paragraph_hash", "") or "").strip()
                entity_hash = str(link.get("entity_hash", "") or "").strip()
                mention_count = max(1, int(link.get("mention_count", 1) or 1))
                if not paragraph_hash or not entity_hash:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                    VALUES (?, ?, ?)
                    """,
                    (paragraph_hash, entity_hash, mention_count),
                )
        for payload in paragraph_payloads.values():
            for link in payload.get("entity_links") or []:
                paragraph_hash = str(link.get("paragraph_hash", "") or "").strip()
                entity_hash = str(link.get("entity_hash", "") or "").strip()
                mention_count = max(1, int(link.get("mention_count", 1) or 1))
                if not paragraph_hash or not entity_hash:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                    VALUES (?, ?, ?)
                    """,
                    (paragraph_hash, entity_hash, mention_count),
                )
            for relation_hash in tokens(payload.get("relation_hashes")):
                paragraph_hash = str((payload.get("paragraph") or {}).get("hash", "") or "").strip()
                if not paragraph_hash or not relation_hash:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                    VALUES (?, ?)
                    """,
                    (paragraph_hash, relation_hash),
                )
            self.metadata_store.restore_external_memory_refs(list(payload.get("external_refs") or []))
        conn.commit()

        sources = tokens(
            [
                str(((payload.get("paragraph") or {}).get("source", "") or "")).strip()
                for payload in paragraph_payloads.values()
            ]
        )
        if sources:
            self.metadata_store._enqueue_episode_source_rebuilds(sources, reason="delete_admin_restore")
        self._rebuild_graph_from_metadata()
        self._persist()
        summary = {
            "restored_entities": restored_entities,
            "restored_paragraphs": restored_paragraphs,
            "restored_relations": restored_relations.get("restored_hashes", []),
            "sources": sources,
        }
        relation_failures = restored_relations.get("failures", [])
        if not relation_failures:
            self.metadata_store.mark_delete_operation_restored(
                str(operation.get("operation_id", "") or ""),
                summary=summary,
            )
        return {
            "success": not relation_failures,
            "operation_id": str(operation.get("operation_id", "") or ""),
            **summary,
            "restored_relation_count": restored_relations.get("restored_count", 0),
            "relation_failures": relation_failures,
        }

    async def _purge_deleted_memory(self, *, grace_hours: Optional[float], limit: int) -> Dict[str, Any]:
        assert self.metadata_store
        orphan_cfg = self._cfg("memory.orphan", {}) or {}
        grace = (
            float(grace_hours)
            if grace_hours is not None
            else max(
                1.0,
                float(orphan_cfg.get("sweep_grace_hours", 24.0) or 24.0),
            )
        )
        cutoff = time.time() - grace * 3600.0
        deleted_relation_hashes = self.metadata_store.purge_deleted_relations(cutoff_time=cutoff, limit=limit)
        dead_paragraphs = self.metadata_store.sweep_deleted_items("paragraph", grace * 3600.0)
        paragraph_hashes = [str(item[0] or "").strip() for item in dead_paragraphs if str(item[0] or "").strip()]
        dead_entities = self.metadata_store.sweep_deleted_items("entity", grace * 3600.0)
        entity_hashes = [str(item[0] or "").strip() for item in dead_entities if str(item[0] or "").strip()]
        entity_names = [str(item[1] or "").strip() for item in dead_entities if str(item[1] or "").strip()]

        if paragraph_hashes:
            self.metadata_store.physically_delete_paragraphs(paragraph_hashes)
        if entity_hashes:
            self.metadata_store.physically_delete_entities(entity_hashes)
        if entity_names:
            self.graph_store.delete_nodes(entity_names)
        self._delete_vectors_by_type(
            paragraph_hashes=paragraph_hashes,
            entity_hashes=entity_hashes,
            relation_hashes=deleted_relation_hashes,
        )
        self._rebuild_graph_from_metadata()
        self._persist()
        return {
            "success": True,
            "grace_hours": grace,
            "purged_deleted_relations": deleted_relation_hashes,
            "purged_paragraph_hashes": paragraph_hashes,
            "purged_entity_hashes": entity_hashes,
            "purged_counts": {
                "relations": len(deleted_relation_hashes),
                "paragraphs": len(paragraph_hashes),
                "entities": len(entity_hashes),
            },
        }
