from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from ...utils.hash import compute_hash
from ...utils.runtime_payloads import tokens
from .base import KernelServiceBase


class MemoryGraphAdminService(KernelServiceBase):
    async def memory_graph_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        await self.initialize()
        assert self.metadata_store is not None
        assert self.graph_store is not None

        act = str(action or "").strip().lower()
        if act == "get_graph":
            return {"success": True, **self._serialize_graph(limit=max(1, int(kwargs.get("limit", 200) or 200)))}
        if act == "search":
            return self._search_graph(
                query=str(kwargs.get("query", "") or "").strip(),
                limit=max(1, min(200, int(kwargs.get("limit", 50) or 50))),
            )
        if act == "node_detail":
            detail = self._build_graph_node_detail(
                node_id=str(kwargs.get("node_id", "") or kwargs.get("node", "") or "").strip(),
                relation_limit=max(1, int(kwargs.get("relation_limit", 20) or 20)),
                paragraph_limit=max(1, int(kwargs.get("paragraph_limit", 20) or 20)),
                evidence_node_limit=max(12, int(kwargs.get("evidence_node_limit", 80) or 80)),
            )
            return detail
        if act == "edge_detail":
            detail = self._build_graph_edge_detail(
                source=str(kwargs.get("source", "") or "").strip(),
                target=str(kwargs.get("target", "") or kwargs.get("object", "") or "").strip(),
                paragraph_limit=max(1, int(kwargs.get("paragraph_limit", 20) or 20)),
                evidence_node_limit=max(12, int(kwargs.get("evidence_node_limit", 80) or 80)),
            )
            return detail

        if act == "create_node":
            name = str(kwargs.get("name", "") or kwargs.get("node", "") or "").strip()
            if not name:
                return {"success": False, "error": "node name 不能为空"}
            entity_hash = self.metadata_store.add_entity(name=name, metadata=kwargs.get("metadata") or {})
            self._rebuild_graph_from_metadata()
            self._persist()
            return {"success": True, "node": {"name": name, "hash": entity_hash}}

        if act == "delete_node":
            name = str(kwargs.get("name", "") or kwargs.get("node", "") or kwargs.get("hash_or_name", "") or "").strip()
            if not name:
                return {"success": False, "error": "node name 不能为空"}
            result = await self._execute_delete_action(
                mode="entity",
                selector={"query": name},
                requested_by=str(kwargs.get("requested_by", "") or "memory_graph_admin"),
                reason=str(kwargs.get("reason", "") or "graph_delete_node"),
            )
            return {
                **result,
                "deleted": bool(result.get("deleted_entity_count", 0) or result.get("deleted_count", 0)),
                "node": name,
            }

        if act == "rename_node":
            old_name = str(kwargs.get("name", "") or kwargs.get("old_name", "") or kwargs.get("node", "") or "").strip()
            new_name = str(kwargs.get("new_name", "") or kwargs.get("target_name", "") or "").strip()
            return self._rename_node(old_name, new_name)

        if act == "create_edge":
            subject = str(kwargs.get("subject", "") or kwargs.get("source", "") or "").strip()
            predicate = str(kwargs.get("predicate", "") or kwargs.get("label", "") or "").strip()
            obj = str(kwargs.get("object", "") or kwargs.get("target", "") or "").strip()
            if not all([subject, predicate, obj]):
                return {"success": False, "error": "subject/predicate/object 不能为空"}
            if self.relation_write_service is not None:
                result = await self.relation_write_service.upsert_relation_with_vector(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    confidence=float(kwargs.get("confidence", 1.0) or 1.0),
                    source_paragraph=str(kwargs.get("source_paragraph", "") or "") or None,
                    metadata=kwargs.get("metadata") or {},
                    write_vector=self.relation_vectors_enabled,
                )
                relation_hash = result.hash_value
            else:
                relation_hash = self.metadata_store.add_relation(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    confidence=float(kwargs.get("confidence", 1.0) or 1.0),
                    source_paragraph=kwargs.get("source_paragraph"),
                    metadata=kwargs.get("metadata") or {},
                )
            self._rebuild_graph_from_metadata()
            self._persist()
            return {
                "success": True,
                "edge": {
                    "hash": relation_hash,
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "weight": float(kwargs.get("confidence", 1.0) or 1.0),
                },
            }

        if act == "delete_edge":
            relation_hash = str(kwargs.get("hash", "") or kwargs.get("relation_hash", "") or "").strip()
            if relation_hash:
                result = await self._execute_delete_action(
                    mode="relation",
                    selector={"query": relation_hash},
                    requested_by=str(kwargs.get("requested_by", "") or "memory_graph_admin"),
                    reason=str(kwargs.get("reason", "") or "graph_delete_edge"),
                )
                return {
                    **result,
                    "deleted": int(result.get("deleted_relation_count", 0) or result.get("deleted_count", 0)),
                    "hash": relation_hash,
                }

            subject = str(kwargs.get("subject", "") or kwargs.get("source", "") or "").strip()
            obj = str(kwargs.get("object", "") or kwargs.get("target", "") or "").strip()
            deleted_hashes = [
                str(row.get("hash", "") or "")
                for row in self.metadata_store.get_relations(subject=subject)
                if str(row.get("object", "") or "").strip() == obj
            ]
            result = await self._execute_delete_action(
                mode="relation",
                selector={"hashes": deleted_hashes, "subject": subject, "object": obj},
                requested_by=str(kwargs.get("requested_by", "") or "memory_graph_admin"),
                reason=str(kwargs.get("reason", "") or "graph_delete_edge"),
            )
            return {
                **result,
                "deleted": int(result.get("deleted_relation_count", 0) or result.get("deleted_count", 0)),
                "subject": subject,
                "object": obj,
            }

        if act == "update_edge_weight":
            return self._update_edge_weight(
                relation_hash=str(kwargs.get("hash", "") or kwargs.get("relation_hash", "") or "").strip(),
                subject=str(kwargs.get("subject", "") or kwargs.get("source", "") or "").strip(),
                obj=str(kwargs.get("object", "") or kwargs.get("target", "") or "").strip(),
                weight=float(kwargs.get("weight", kwargs.get("confidence", 1.0)) or 1.0),
            )

        return {"success": False, "error": f"不支持的 graph action: {act}"}

    def _serialize_graph(self, *, limit: int = 200) -> Dict[str, Any]:
        assert self.graph_store is not None
        assert self.metadata_store is not None
        nodes = self.graph_store.get_nodes()
        if limit > 0:
            nodes = nodes[:limit]
        node_set = set(nodes)
        node_payload = []
        for name in nodes:
            attrs = self.graph_store.get_node_attributes(name) or {}
            node_payload.append({"id": name, "name": name, "attributes": attrs})

        edge_payload = []
        for source, target, relation_hashes in self.graph_store.iter_edge_hash_entries():
            if source not in node_set or target not in node_set:
                continue
            relation_hash_tokens = sorted(str(item) for item in relation_hashes if str(item).strip())
            relation_rows = self._query_relation_rows_by_hashes(relation_hash_tokens)
            predicates = self._dedupe_strings(row.get("predicate", "") for row in relation_rows)
            evidence_hashes = self._query_distinct_paragraph_hashes_for_relations(relation_hash_tokens)
            edge_payload.append(
                {
                    "source": source,
                    "target": target,
                    "weight": float(self.graph_store.get_edge_weight(source, target)),
                    "relation_hashes": relation_hash_tokens,
                    "predicates": predicates,
                    "relation_count": len(relation_hash_tokens),
                    "evidence_count": len(evidence_hashes),
                    "label": self._build_graph_edge_label(predicates),
                }
            )
        return {
            "nodes": node_payload,
            "edges": edge_payload,
            "total_nodes": int(self.graph_store.num_nodes),
            "total_edges": int(self.graph_store.num_edges),
        }

    @staticmethod
    def _graph_search_match_rank(value: str, keyword: str) -> Optional[int]:
        token = str(value or "").strip().lower()
        if not token or not keyword:
            return None
        if token == keyword:
            return 0
        if token.startswith(keyword):
            return 1
        if keyword in token:
            return 2
        return None

    @classmethod
    def _pick_graph_search_match(
        cls,
        fields: Sequence[tuple[str, str]],
        keyword: str,
    ) -> Optional[tuple[str, str, int]]:
        best_match: Optional[tuple[str, str, int]] = None
        for field, raw_value in fields:
            value = str(raw_value or "").strip()
            if not value:
                continue
            rank = cls._graph_search_match_rank(value, keyword)
            if rank is None:
                continue
            if best_match is None or rank < best_match[2]:
                best_match = (field, value, rank)
        return best_match

    def _search_graph(self, *, query: str, limit: int) -> Dict[str, Any]:
        assert self.metadata_store is not None
        token = str(query or "").strip()
        normalized_query = token.lower()
        safe_limit = max(1, int(limit or 50))
        if not token:
            return {
                "success": False,
                "query": token,
                "limit": safe_limit,
                "count": 0,
                "items": [],
                "error": "query 不能为空",
            }

        like_keyword = f"%{normalized_query}%"
        entity_rows = self.metadata_store.query(
            """
            SELECT hash, name, appearance_count, created_at
            FROM entities
            WHERE (is_deleted IS NULL OR is_deleted = 0)
              AND (
                LOWER(COALESCE(name, '')) LIKE ?
                OR LOWER(COALESCE(hash, '')) LIKE ?
              )
            """,
            (like_keyword, like_keyword),
        )

        relation_rows = self.metadata_store.query(
            """
            SELECT hash, subject, predicate, object, confidence, created_at
            FROM relations
            WHERE (is_inactive IS NULL OR is_inactive = 0)
              AND (
                LOWER(COALESCE(subject, '')) LIKE ?
                OR LOWER(COALESCE(object, '')) LIKE ?
                OR LOWER(COALESCE(predicate, '')) LIKE ?
                OR LOWER(COALESCE(hash, '')) LIKE ?
              )
            """,
            (like_keyword, like_keyword, like_keyword, like_keyword),
        )

        entity_items: List[Dict[str, Any]] = []
        seen_entity_keys: set[str] = set()
        for row in entity_rows:
            name = str(row.get("name", "") or "").strip()
            hash_value = str(row.get("hash", "") or "").strip()
            match = self._pick_graph_search_match(
                [("name", name), ("hash", hash_value)],
                normalized_query,
            )
            if match is None:
                continue
            dedupe_key = hash_value or f"name:{name.lower()}"
            if dedupe_key in seen_entity_keys:
                continue
            seen_entity_keys.add(dedupe_key)
            matched_field, matched_value, rank = match
            entity_items.append(
                {
                    "type": "entity",
                    "title": name or hash_value,
                    "matched_field": matched_field,
                    "matched_value": matched_value,
                    "entity_name": name or hash_value,
                    "entity_hash": hash_value,
                    "appearance_count": int(row.get("appearance_count", 0) or 0),
                    "_rank": rank,
                }
            )

        relation_items: List[Dict[str, Any]] = []
        seen_relation_keys: set[str] = set()
        for row in relation_rows:
            subject = str(row.get("subject", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            relation_hash = str(row.get("hash", "") or "").strip()
            match = self._pick_graph_search_match(
                [
                    ("subject", subject),
                    ("object", obj),
                    ("predicate", predicate),
                    ("hash", relation_hash),
                ],
                normalized_query,
            )
            if match is None:
                continue
            dedupe_key = relation_hash or f"{subject.lower()}|{predicate.lower()}|{obj.lower()}"
            if dedupe_key in seen_relation_keys:
                continue
            seen_relation_keys.add(dedupe_key)
            matched_field, matched_value, rank = match
            relation_items.append(
                {
                    "type": "relation",
                    "title": self._format_relation_text(subject, predicate, obj),
                    "matched_field": matched_field,
                    "matched_value": matched_value,
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "relation_hash": relation_hash,
                    "confidence": float(row.get("confidence", 0.0) or 0.0),
                    "created_at": float(row.get("created_at", 0.0) or 0.0),
                    "_rank": rank,
                }
            )

        items = entity_items + relation_items
        items.sort(
            key=lambda item: (
                int(item["_rank"]) if item.get("_rank") is not None else 99,
                0 if str(item.get("type", "") or "") == "entity" else 1,
                -int(item.get("appearance_count", 0) or 0)
                if str(item.get("type", "") or "") == "entity"
                else -float(item.get("confidence", 0.0) or 0.0),
                0.0 if str(item.get("type", "") or "") == "entity" else -float(item.get("created_at", 0.0) or 0.0),
                str(item.get("entity_name", item.get("subject", "")) or "").lower(),
                str(item.get("predicate", "") or "").lower(),
                str(item.get("object", "") or "").lower(),
                str(item.get("entity_hash", item.get("relation_hash", "")) or "").lower(),
            )
        )

        normalized_items: List[Dict[str, Any]] = []
        for item in items[:safe_limit]:
            normalized = dict(item)
            normalized.pop("_rank", None)
            normalized_items.append(normalized)

        return {
            "success": True,
            "query": token,
            "limit": safe_limit,
            "count": len(normalized_items),
            "items": normalized_items,
        }

    @staticmethod
    def _dedupe_strings(values: Iterable[Any]) -> List[str]:
        deduped: List[str] = []
        for value in values:
            token = str(value or "").strip()
            if token and token not in deduped:
                deduped.append(token)
        return deduped

    @staticmethod
    def _build_graph_edge_label(predicates: Sequence[str]) -> str:
        labels = [str(item or "").strip() for item in predicates if str(item or "").strip()]
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        return f"{labels[0]} +{len(labels) - 1}"

    @staticmethod
    def _trim_text(value: str, limit: int = 220) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    @staticmethod
    def _format_relation_text(subject: Any, predicate: Any, obj: Any) -> str:
        return " ".join(
            [
                str(subject or "").strip(),
                str(predicate or "").strip(),
                str(obj or "").strip(),
            ]
        ).strip()

    def _query_relation_rows_by_hashes(
        self,
        relation_hashes: Sequence[str],
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        hashes = [str(item or "").strip() for item in relation_hashes if str(item or "").strip()]
        if not hashes:
            return []
        placeholders = ",".join(["?"] * len(hashes))
        inactive_clause = "" if include_inactive else "AND (is_inactive IS NULL OR is_inactive = 0)"
        rows = self.metadata_store.query(
            f"""
            SELECT hash, subject, predicate, object, confidence, created_at, source_paragraph
            FROM relations
            WHERE hash IN ({placeholders})
              {inactive_clause}
            """,
            tuple(hashes),
        )
        order = {hash_value: index for index, hash_value in enumerate(hashes)}
        rows.sort(key=lambda row: order.get(str(row.get("hash", "") or ""), len(order)))
        return rows

    def _query_distinct_paragraph_hashes_for_relations(
        self,
        relation_hashes: Sequence[str],
        *,
        limit: Optional[int] = None,
    ) -> List[str]:
        assert self.metadata_store is not None
        hashes = [str(item or "").strip() for item in relation_hashes if str(item or "").strip()]
        if not hashes:
            return []
        placeholders = ",".join(["?"] * len(hashes))
        sql = f"""
            SELECT DISTINCT p.hash, p.updated_at, p.created_at
            FROM paragraphs p
            JOIN paragraph_relations pr ON p.hash = pr.paragraph_hash
            WHERE pr.relation_hash IN ({placeholders})
              AND (p.is_deleted IS NULL OR p.is_deleted = 0)
            ORDER BY p.updated_at DESC, p.created_at DESC, p.hash ASC
        """
        params: List[Any] = list(hashes)
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.metadata_store.query(sql, tuple(params))
        return [str(row.get("hash", "") or "").strip() for row in rows if str(row.get("hash", "") or "").strip()]

    def _load_paragraph_rows(self, paragraph_hashes: Sequence[str]) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        hashes = [str(item or "").strip() for item in paragraph_hashes if str(item or "").strip()]
        if not hashes:
            return []
        rows: List[Dict[str, Any]] = []
        for hash_value in hashes:
            row = self.metadata_store.get_paragraph(hash_value)
            if row is None:
                continue
            if bool(row.get("is_deleted", 0)):
                continue
            rows.append(row)
        return rows

    def _resolve_graph_node_name(self, node_id: str) -> str:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        token = str(node_id or "").strip()
        if not token:
            return ""
        graph_nodes = self.graph_store.get_nodes()
        for candidate in graph_nodes:
            if str(candidate or "").strip().lower() == token.lower():
                return str(candidate)
        entity_rows = self.metadata_store.query(
            """
            SELECT name
            FROM entities
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
               OR hash = ?
            ORDER BY appearance_count DESC, created_at ASC
            LIMIT 1
            """,
            (token, token),
        )
        if entity_rows:
            return str(entity_rows[0].get("name", "") or token)
        relation_rows = self.metadata_store.query(
            """
            SELECT subject, object
            FROM relations
            WHERE (LOWER(TRIM(subject)) = LOWER(TRIM(?)) OR LOWER(TRIM(object)) = LOWER(TRIM(?)))
              AND (is_inactive IS NULL OR is_inactive = 0)
            LIMIT 1
            """,
            (token, token),
        )
        if relation_rows:
            subject = str(relation_rows[0].get("subject", "") or "").strip()
            obj = str(relation_rows[0].get("object", "") or "").strip()
            if subject.lower() == token.lower():
                return subject
            if obj.lower() == token.lower():
                return obj
        return token

    def _get_related_relation_rows_for_entity(self, entity_name: str, *, limit: int) -> List[Dict[str, Any]]:
        assert self.metadata_store is not None
        rows = self.metadata_store.query(
            """
            SELECT hash, subject, predicate, object, confidence, created_at, source_paragraph
            FROM relations
            WHERE (LOWER(TRIM(subject)) = LOWER(TRIM(?)) OR LOWER(TRIM(object)) = LOWER(TRIM(?)))
              AND (is_inactive IS NULL OR is_inactive = 0)
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
            """,
            (entity_name, entity_name, limit),
        )
        return rows

    def _build_relation_summary(self, row: Dict[str, Any], paragraph_hashes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        relation_hash = str(row.get("hash", "") or "").strip()
        hashes = [str(item or "").strip() for item in (paragraph_hashes or []) if str(item or "").strip()]
        if not hashes and relation_hash:
            hashes = self._query_distinct_paragraph_hashes_for_relations([relation_hash])
        return {
            "hash": relation_hash,
            "subject": str(row.get("subject", "") or "").strip(),
            "predicate": str(row.get("predicate", "") or "").strip(),
            "object": str(row.get("object", "") or "").strip(),
            "text": self._format_relation_text(row.get("subject"), row.get("predicate"), row.get("object")),
            "confidence": float(row.get("confidence", 0.0) or 0.0),
            "paragraph_count": len(hashes),
            "paragraph_hashes": hashes,
            "source_paragraph": str(row.get("source_paragraph", "") or "").strip(),
        }

    def _build_paragraph_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        assert self.metadata_store is not None
        paragraph_hash = str(row.get("hash", "") or "").strip()
        entities = self.metadata_store.get_paragraph_entities(paragraph_hash)
        relations = self.metadata_store.get_paragraph_relations(paragraph_hash)
        stale_marks_map, stale_status_map = self._load_paragraph_stale_marks([paragraph_hash])
        stale_marks = [
            {
                **mark,
                "relation_inactive": self._relation_status_is_inactive(
                    stale_status_map.get(str(mark.get("relation_hash", "") or "").strip())
                ),
            }
            for mark in stale_marks_map.get(paragraph_hash, [])
        ]
        return {
            "hash": paragraph_hash,
            "content": str(row.get("content", "") or ""),
            "preview": self._trim_text(str(row.get("content", "") or "")),
            "source": str(row.get("source", "") or ""),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "entity_count": len(entities),
            "relation_count": len(relations),
            "entities": self._dedupe_strings(entity.get("name", "") for entity in entities),
            "relations": [
                self._format_relation_text(
                    relation.get("subject", ""),
                    relation.get("predicate", ""),
                    relation.get("object", ""),
                )
                for relation in relations
            ],
            "is_stale": bool(stale_marks),
            "stale_relation_marks": stale_marks,
        }

    @staticmethod
    def _evidence_entity_node_id(name: str) -> str:
        return f"entity:{name}"

    @staticmethod
    def _evidence_relation_node_id(hash_value: str) -> str:
        return f"relation:{hash_value}"

    @staticmethod
    def _evidence_paragraph_node_id(hash_value: str) -> str:
        return f"paragraph:{hash_value}"

    def _build_evidence_graph(
        self,
        *,
        focus_entities: Sequence[str],
        relation_rows: Sequence[Dict[str, Any]],
        paragraph_rows: Sequence[Dict[str, Any]],
        node_limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        edge_keys: set[tuple[str, str, str]] = set()
        relation_hash_set = {str(row.get("hash", "") or "").strip() for row in relation_rows if str(row.get("hash", "") or "").strip()}

        def add_node(node_id: str, *, node_type: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
            if not node_id or node_id in nodes:
                return
            nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "content": content,
                "metadata": metadata or {},
            }

        def add_edge(source: str, target: str, *, kind: str, label: str, weight: float = 1.0) -> None:
            key = (source, target, kind)
            if not source or not target or key in edge_keys:
                return
            edge_keys.add(key)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "label": label,
                    "weight": float(weight or 1.0),
                }
            )

        for entity_name in self._dedupe_strings(focus_entities):
            add_node(
                self._evidence_entity_node_id(entity_name),
                node_type="entity",
                content=entity_name,
                metadata={"entity_name": entity_name},
            )

        for row in relation_rows:
            relation_hash = str(row.get("hash", "") or "").strip()
            if not relation_hash:
                continue
            subject = str(row.get("subject", "") or "").strip()
            obj = str(row.get("object", "") or "").strip()
            predicate = str(row.get("predicate", "") or "").strip()
            paragraph_hashes = self._query_distinct_paragraph_hashes_for_relations([relation_hash])
            add_node(
                self._evidence_relation_node_id(relation_hash),
                node_type="relation",
                content=self._format_relation_text(subject, predicate, obj),
                metadata={
                    "hash": relation_hash,
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "confidence": float(row.get("confidence", 0.0) or 0.0),
                    "paragraph_count": len(paragraph_hashes),
                    "paragraph_hashes": paragraph_hashes,
                    "text": self._format_relation_text(subject, predicate, obj),
                },
            )
            add_node(
                self._evidence_entity_node_id(subject),
                node_type="entity",
                content=subject,
                metadata={"entity_name": subject},
            )
            add_node(
                self._evidence_entity_node_id(obj),
                node_type="entity",
                content=obj,
                metadata={"entity_name": obj},
            )
            add_edge(
                self._evidence_relation_node_id(relation_hash),
                self._evidence_entity_node_id(subject),
                kind="subject",
                label="主语",
            )
            add_edge(
                self._evidence_relation_node_id(relation_hash),
                self._evidence_entity_node_id(obj),
                kind="object",
                label="宾语",
            )

        for paragraph in paragraph_rows:
            paragraph_hash = str(paragraph.get("hash", "") or "").strip()
            if not paragraph_hash:
                continue
            paragraph_entities = self.metadata_store.get_paragraph_entities(paragraph_hash)
            paragraph_relations = self.metadata_store.get_paragraph_relations(paragraph_hash)
            add_node(
                self._evidence_paragraph_node_id(paragraph_hash),
                node_type="paragraph",
                content=str(paragraph.get("content", "") or ""),
                metadata={
                    "hash": paragraph_hash,
                    "source": str(paragraph.get("source", "") or ""),
                    "updated_at": paragraph.get("updated_at"),
                    "entity_count": len(paragraph_entities),
                    "relation_count": len(paragraph_relations),
                    "preview": self._trim_text(str(paragraph.get("content", "") or "")),
                },
            )
            for entity in paragraph_entities:
                entity_name = str(entity.get("name", "") or "").strip()
                if not entity_name:
                    continue
                mention_count = int(entity.get("mention_count", 1) or 1)
                add_node(
                    self._evidence_entity_node_id(entity_name),
                    node_type="entity",
                    content=entity_name,
                    metadata={"entity_name": entity_name},
                )
                add_edge(
                    self._evidence_paragraph_node_id(paragraph_hash),
                    self._evidence_entity_node_id(entity_name),
                    kind="mentions",
                    label=f"提及 ×{mention_count}" if mention_count > 1 else "提及",
                    weight=float(max(1, mention_count)),
                )
            for relation in paragraph_relations:
                relation_hash = str(relation.get("hash", "") or "").strip()
                if relation_hash not in relation_hash_set:
                    continue
                add_edge(
                    self._evidence_paragraph_node_id(paragraph_hash),
                    self._evidence_relation_node_id(relation_hash),
                    kind="supports",
                    label="支撑",
                )

        if len(nodes) > node_limit:
            priority = {"entity": 0, "relation": 1, "paragraph": 2}
            kept_ids = {
                node["id"]
                for node in sorted(
                    nodes.values(),
                    key=lambda node: (
                        priority.get(str(node.get("type", "")), 9),
                        str(node.get("id", "")),
                    ),
                )[:node_limit]
            }
            nodes = {node_id: node for node_id, node in nodes.items() if node_id in kept_ids}
            edges = [edge for edge in edges if edge["source"] in nodes and edge["target"] in nodes]

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "focus_entities": self._dedupe_strings(focus_entities),
        }

    def _build_graph_node_detail(
        self,
        *,
        node_id: str,
        relation_limit: int,
        paragraph_limit: int,
        evidence_node_limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        resolved_name = self._resolve_graph_node_name(node_id)
        if not resolved_name:
            return {"success": False, "error": "node_id 不能为空"}

        entity_row = None
        entity_matches = self.metadata_store.query(
            """
            SELECT *
            FROM entities
            WHERE (LOWER(TRIM(name)) = LOWER(TRIM(?))
               OR hash = ?)
              AND (is_deleted IS NULL OR is_deleted = 0)
            ORDER BY appearance_count DESC, created_at ASC
            LIMIT 1
            """,
            (resolved_name, resolved_name),
        )
        if entity_matches and hasattr(self.metadata_store, "_row_to_dict"):
            entity_row = self.metadata_store._row_to_dict(entity_matches[0], "entity")

        relation_rows = self._get_related_relation_rows_for_entity(resolved_name, limit=relation_limit)
        if not relation_rows and entity_row is None:
            return {"success": False, "error": f"未找到节点: {resolved_name}"}

        relation_hashes = [str(row.get("hash", "") or "").strip() for row in relation_rows if str(row.get("hash", "") or "").strip()]
        direct_paragraph_rows = self.metadata_store.get_paragraphs_by_entity(resolved_name)
        relation_paragraph_hashes = self._query_distinct_paragraph_hashes_for_relations(relation_hashes)
        relation_paragraph_rows = self._load_paragraph_rows(relation_paragraph_hashes)
        paragraph_rows_map: Dict[str, Dict[str, Any]] = {}
        for row in direct_paragraph_rows + relation_paragraph_rows:
            paragraph_hash = str(row.get("hash", "") or "").strip()
            if paragraph_hash and not bool(row.get("is_deleted", 0)):
                paragraph_rows_map[paragraph_hash] = row
        paragraph_rows = list(paragraph_rows_map.values())
        paragraph_rows.sort(key=lambda row: (float(row.get("updated_at", 0) or 0), float(row.get("created_at", 0) or 0)), reverse=True)
        paragraph_rows = paragraph_rows[:paragraph_limit]

        relation_summaries = []
        for row in relation_rows:
            relation_hash = str(row.get("hash", "") or "").strip()
            relation_summaries.append(
                self._build_relation_summary(
                    row,
                    paragraph_hashes=self._query_distinct_paragraph_hashes_for_relations([relation_hash]),
                )
            )

        paragraph_summaries = [self._build_paragraph_summary(row) for row in paragraph_rows]
        evidence_graph = self._build_evidence_graph(
            focus_entities=[resolved_name],
            relation_rows=relation_rows,
            paragraph_rows=paragraph_rows,
            node_limit=evidence_node_limit,
        )

        return {
            "success": True,
            "node": {
                "id": resolved_name,
                "type": "entity",
                "content": resolved_name,
                "hash": str(entity_row.get("hash", "") or "") if isinstance(entity_row, dict) else "",
                "appearance_count": int(entity_row.get("appearance_count", 0) or 0) if isinstance(entity_row, dict) else 0,
            },
            "relations": relation_summaries,
            "paragraphs": paragraph_summaries,
            "evidence_graph": evidence_graph,
        }

    def _build_graph_edge_detail(
        self,
        *,
        source: str,
        target: str,
        paragraph_limit: int,
        evidence_node_limit: int,
    ) -> Dict[str, Any]:
        assert self.metadata_store is not None
        source_name = self._resolve_graph_node_name(source)
        target_name = self._resolve_graph_node_name(target)
        if not source_name or not target_name:
            return {"success": False, "error": "source/target 不能为空"}

        relation_rows = self.metadata_store.query(
            """
            SELECT hash, subject, predicate, object, confidence, created_at, source_paragraph
            FROM relations
            WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))
              AND LOWER(TRIM(object)) = LOWER(TRIM(?))
              AND (is_inactive IS NULL OR is_inactive = 0)
            ORDER BY confidence DESC, created_at DESC
            """,
            (source_name, target_name),
        )
        if not relation_rows:
            return {"success": False, "error": f"未找到边: {source_name} -> {target_name}"}

        relation_hashes = [str(row.get("hash", "") or "").strip() for row in relation_rows if str(row.get("hash", "") or "").strip()]
        paragraph_hashes = self._query_distinct_paragraph_hashes_for_relations(relation_hashes, limit=paragraph_limit)
        paragraph_rows = self._load_paragraph_rows(paragraph_hashes)
        relation_summaries = [
            self._build_relation_summary(
                row,
                paragraph_hashes=self._query_distinct_paragraph_hashes_for_relations([str(row.get("hash", "") or "").strip()]),
            )
            for row in relation_rows
        ]
        paragraph_summaries = [self._build_paragraph_summary(row) for row in paragraph_rows]
        predicates = self._dedupe_strings(row.get("predicate", "") for row in relation_rows)
        evidence_graph = self._build_evidence_graph(
            focus_entities=[source_name, target_name],
            relation_rows=relation_rows,
            paragraph_rows=paragraph_rows,
            node_limit=evidence_node_limit,
        )
        return {
            "success": True,
            "edge": {
                "source": source_name,
                "target": target_name,
                "weight": float(self.graph_store.get_edge_weight(source_name, target_name)) if self.graph_store is not None else 0.0,
                "relation_hashes": relation_hashes,
                "predicates": predicates,
                "relation_count": len(relation_hashes),
                "evidence_count": len(paragraph_hashes),
                "label": self._build_graph_edge_label(predicates),
            },
            "relations": relation_summaries,
            "paragraphs": paragraph_summaries,
            "evidence_graph": evidence_graph,
        }

    def _delete_sources(self, sources: Iterable[Any]) -> Dict[str, Any]:
        assert self.metadata_store
        source_tokens = tokens(sources)
        if not source_tokens:
            return {"success": False, "error": "source 不能为空"}

        deleted_paragraphs = 0
        deleted_sources: List[str] = []
        for source in source_tokens:
            paragraphs = self.metadata_store.get_paragraphs_by_source(source)
            if not paragraphs:
                self.metadata_store.replace_episodes_for_source(source, [])
                continue
            for row in paragraphs:
                paragraph_hash = str(row.get("hash", "") or "").strip()
                if not paragraph_hash:
                    continue
                cleanup = self.metadata_store.delete_paragraph_atomic(paragraph_hash)
                self._apply_cleanup_plan(cleanup)
                deleted_paragraphs += 1
            self.metadata_store.replace_episodes_for_source(source, [])
            deleted_sources.append(source)

        self._rebuild_graph_from_metadata()
        self._persist()
        return {
            "success": True,
            "sources": deleted_sources,
            "deleted_source_count": len(deleted_sources),
            "deleted_paragraph_count": deleted_paragraphs,
        }

    def _apply_cleanup_plan(self, cleanup: Dict[str, Any]) -> None:
        if not isinstance(cleanup, dict):
            return
        paragraph_hash = str(cleanup.get("vector_id_to_remove", "") or "").strip()
        relation_hashes = [
            str(relation_hash or "").strip()
            for _, _, relation_hash in cleanup.get("relation_prune_ops", []) or []
            if str(relation_hash or "").strip()
        ]
        self._delete_vectors_by_type(
            paragraph_hashes=[paragraph_hash] if paragraph_hash else [],
            relation_hashes=relation_hashes,
        )

    def _rebuild_graph_from_metadata(self) -> Dict[str, int]:
        assert self.metadata_store is not None
        assert self.graph_store is not None
        entity_rows = self.metadata_store.query(
            """
            SELECT name
            FROM entities
            WHERE is_deleted IS NULL OR is_deleted = 0
            ORDER BY name ASC
            """
        )
        raw_relation_rows = self.metadata_store.query(
            """
            SELECT subject, object, confidence, hash
            FROM relations
            WHERE is_inactive IS NULL OR is_inactive = 0
            """
        )
        relation_rows = [
            row
            for row in raw_relation_rows
            if str(row.get("subject", "") or "").strip() and str(row.get("object", "") or "").strip()
        ]

        names = list(
            dict.fromkeys(
                [
                    str(row.get("name", "") or "").strip()
                    for row in entity_rows
                    if str(row.get("name", "") or "").strip()
                ]
                + [
                    str(row.get("subject", "") or "").strip()
                    for row in relation_rows
                    if str(row.get("subject", "") or "").strip()
                ]
                + [
                    str(row.get("object", "") or "").strip()
                    for row in relation_rows
                    if str(row.get("object", "") or "").strip()
                ]
            )
        )
        self.graph_store.clear()
        if names:
            self.graph_store.add_nodes(names)
        if relation_rows:
            self.graph_store.add_edges(
                [
                    (
                        str(row.get("subject", "") or "").strip(),
                        str(row.get("object", "") or "").strip(),
                    )
                    for row in relation_rows
                ],
                weights=[float(row.get("confidence", 1.0) or 1.0) for row in relation_rows],
                relation_hashes=[str(row.get("hash", "") or "") for row in relation_rows],
            )
        return {"node_count": int(self.graph_store.num_nodes), "edge_count": int(self.graph_store.num_edges)}

    def _rename_node(self, old_name: str, new_name: str) -> Dict[str, Any]:
        assert self.metadata_store
        source = str(old_name or "").strip()
        target = str(new_name or "").strip()
        if not source or not target:
            return {"success": False, "error": "old_name/new_name 不能为空"}
        if source == target:
            return {"success": True, "renamed": False, "old_name": source, "new_name": target}

        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        old_hash = compute_hash(source.lower())
        target_hash = compute_hash(target.lower())

        cursor.execute(
            """
            SELECT hash, name, vector_index, appearance_count, created_at, metadata
            FROM entities
            WHERE hash = ?
               OR LOWER(TRIM(name)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (old_hash, source),
        )
        old_row = cursor.fetchone()
        if old_row is None:
            return {"success": False, "error": "原节点不存在"}

        cursor.execute(
            """
            SELECT hash, appearance_count
            FROM entities
            WHERE hash = ?
               OR LOWER(TRIM(name)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (target_hash, target),
        )
        target_row = cursor.fetchone()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            if target_row is None:
                cursor.execute(
                    """
                    INSERT INTO entities (hash, name, vector_index, appearance_count, created_at, metadata, is_deleted, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        target_hash,
                        target,
                        old_row["vector_index"],
                        old_row["appearance_count"],
                        old_row["created_at"],
                        old_row["metadata"],
                    ),
                )
                resolved_target_hash = target_hash
            else:
                resolved_target_hash = str(target_row["hash"] or "").strip()
                cursor.execute(
                    """
                    UPDATE entities
                    SET name = ?,
                        appearance_count = COALESCE(appearance_count, 0) + ?,
                        is_deleted = 0,
                        deleted_at = NULL
                    WHERE hash = ?
                    """,
                    (
                        target,
                        int(old_row["appearance_count"] or 0),
                        resolved_target_hash,
                    ),
                )

            cursor.execute(
                "UPDATE OR IGNORE paragraph_entities SET entity_hash = ? WHERE entity_hash = ?",
                (resolved_target_hash, old_row["hash"]),
            )
            cursor.execute("DELETE FROM paragraph_entities WHERE entity_hash = ?", (old_row["hash"],))
            cursor.execute(
                "UPDATE relations SET subject = ? WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))",
                (target, old_row["name"]),
            )
            cursor.execute(
                "UPDATE relations SET object = ? WHERE LOWER(TRIM(object)) = LOWER(TRIM(?))",
                (target, old_row["name"]),
            )
            cursor.execute("DELETE FROM entities WHERE hash = ?", (old_row["hash"],))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return {"success": False, "error": f"rename failed: {exc}"}

        self._rebuild_graph_from_metadata()
        self._persist()
        return {"success": True, "renamed": True, "old_name": source, "new_name": target}

    def _update_edge_weight(
        self,
        *,
        relation_hash: str,
        subject: str,
        obj: str,
        weight: float,
    ) -> Dict[str, Any]:
        assert self.metadata_store
        conn = self.metadata_store.get_connection()
        cursor = conn.cursor()
        target_weight = max(0.0, float(weight or 0.0))
        if relation_hash:
            cursor.execute("UPDATE relations SET confidence = ? WHERE hash = ?", (target_weight, relation_hash))
            updated = cursor.rowcount
        else:
            cursor.execute(
                """
                UPDATE relations
                SET confidence = ?
                WHERE LOWER(TRIM(subject)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(object)) = LOWER(TRIM(?))
                """,
                (target_weight, subject, obj),
            )
            updated = cursor.rowcount
        conn.commit()
        if updated <= 0:
            return {"success": False, "error": "未找到可更新的关系"}
        self._rebuild_graph_from_metadata()
        self._persist()
        return {
            "success": True,
            "updated": int(updated),
            "weight": target_weight,
            "hash": relation_hash,
            "subject": subject,
            "object": obj,
        }

