from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import hashlib
import json
import re
import time
import zipfile

import numpy as np

from ...storage.knowledge_types import resolve_stored_knowledge_type
from ...utils.hash import compute_hash, normalize_text
from .base import KernelServiceBase


BUNDLE_FORMAT = "a-memorix-memory-bundle"
BUNDLE_FORMAT_VERSION = 2
BUNDLE_LEGACY_FORMAT_VERSION = 1
BUNDLE_EXTENSION = ".amembundle"
MAX_BUNDLE_MEMBERS = 4096
MAX_BUNDLE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
FULL_STATE_TABLE_ORDER = (
    "paragraphs",
    "entities",
    "relations",
    "deleted_relations",
    "paragraph_entities",
    "paragraph_relations",
    "external_memory_refs",
    "episodes",
    "episode_paragraphs",
    "fact_claims",
    "fact_evidence",
    "fact_transitions",
    "person_profile_snapshots",
    "person_profile_overrides",
    "person_profile_alias_overrides",
)
RESOURCE_PRIMARY_KEYS: Dict[str, Tuple[str, ...]] = {
    "paragraphs": ("hash",),
    "entities": ("hash",),
    "relations": ("hash",),
    "deleted_relations": ("hash",),
    "paragraph_entities": ("paragraph_hash", "entity_hash"),
    "paragraph_relations": ("paragraph_hash", "relation_hash"),
    "external_memory_refs": ("external_id",),
    "episodes": ("episode_id",),
    "episode_paragraphs": ("episode_id", "paragraph_hash"),
    "fact_claims": ("claim_id",),
    "fact_evidence": ("claim_id", "evidence_type", "evidence_id", "stance"),
    "fact_transitions": ("transition_id",),
    "person_profile_snapshots": ("snapshot_id",),
    "person_profile_overrides": ("person_id",),
    "person_profile_alias_overrides": ("person_id",),
}
FULL_STATE_EXCLUSIONS = (
    "FTS、N-gram、关系图快照会在目标实例重建",
    "后台队列、运行时任务、反馈和删除审计不属于可移植语义状态",
    "聊天流开关、活跃人物集合等实例配置不会随包迁移",
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _string_tokens(value: Any) -> set[str]:
    if isinstance(value, str):
        token = value.strip()
        return {token} if token else set()
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(_string_tokens(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = set()
        for item in value:
            result.update(_string_tokens(item))
        return result
    return set()


def _replace_json_tokens(value: Any, replacements: Dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_json_tokens(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_json_tokens(item, replacements) for key, item in value.items()}
    return value


def _canonical_image_evidence(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        evidence = dict(value)
    else:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError):
            return {}
        evidence = dict(parsed) if isinstance(parsed, dict) else {}
    for key in ("chat_id", "external_ref", "imported", "message_id", "source_link_id"):
        evidence.pop(key, None)
    return evidence


def _batched(values: Sequence[str], size: int = 400) -> Iterable[List[str]]:
    for offset in range(0, len(values), size):
        yield list(values[offset : offset + size])


def _stable_installation_id(package_id: str, version: str, scope_type: str, scope_key: str) -> str:
    digest = hashlib.sha256(
        f"{package_id}\n{version}\n{scope_type}\n{scope_key}".encode("utf-8")
    ).hexdigest()
    return f"kpi_{digest[:32]}"


def _resource_key(resource_type: str, row: Dict[str, Any]) -> str:
    primary_keys = RESOURCE_PRIMARY_KEYS[resource_type]
    values = [row[key] for key in primary_keys]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _resource_key_values(resource_type: str, resource_key: str) -> List[Any]:
    values = json.loads(resource_key)
    primary_keys = RESOURCE_PRIMARY_KEYS[resource_type]
    if not isinstance(values, list) or len(values) != len(primary_keys):
        raise ValueError(f"知识包资源主键无效: {resource_type}")
    return values


def _canonical_image_semantics(records: Dict[str, Any]) -> Dict[str, Any]:
    """构造不受实例 UUID、安装时间和运行状态影响的图片语义摘要。"""

    assets_by_id = {str(item.get("asset_id") or ""): item for item in records.get("assets") or []}
    occurrences = sorted(
        (dict(item) for item in records.get("occurrences") or []),
        key=lambda item: (
            str(assets_by_id.get(str(item.get("asset_id") or ""), {}).get("content_hash") or ""),
            str(item.get("component_path") or ""),
            str(item.get("occurrence_id") or ""),
        ),
    )
    occurrence_keys = {
        str(item.get("occurrence_id") or ""): f"occurrence-{index}"
        for index, item in enumerate(occurrences)
    }
    return {
        "assets": sorted(
            [
                {
                    "byte_size": int(item.get("byte_size") or 0),
                    "content_hash": str(item.get("content_hash") or ""),
                    "height": int(item.get("height") or 0),
                    "mime_type": str(item.get("mime_type") or ""),
                    "width": int(item.get("width") or 0),
                }
                for item in records.get("assets") or []
            ],
            key=lambda item: item["content_hash"],
        ),
        "occurrences": [
            {
                "asset_hash": str(assets_by_id.get(str(item.get("asset_id") or ""), {}).get("content_hash") or ""),
                "component_path": str(item.get("component_path") or ""),
            }
            for item in occurrences
        ],
        "observations": sorted(
            [
                {
                    "confirm_status": str(item.get("confirm_status") or ""),
                    "occurrence": occurrence_keys.get(str(item.get("occurrence_id") or ""), ""),
                    "source_kind": str(item.get("source_kind") or ""),
                    "text": str(item.get("text") or ""),
                    "evidence": _canonical_image_evidence(item.get("evidence_json")),
                }
                for item in records.get("observations") or []
            ],
            key=lambda item: (item["occurrence"], item["source_kind"], item["text"]),
        ),
        "links": sorted(
            [
                {
                    "link_kind": str(item.get("link_kind") or ""),
                    "occurrence": occurrence_keys.get(str(item.get("occurrence_id") or ""), ""),
                    "target_id": str(item.get("target_id") or ""),
                    "target_type": str(item.get("target_type") or ""),
                    "evidence": _canonical_image_evidence(item.get("evidence_json")),
                }
                for item in records.get("links") or []
            ],
            key=lambda item: (item["occurrence"], item["target_type"], item["target_id"]),
        ),
    }


class MemoryBundleAdminService(KernelServiceBase):
    """导出、检查和安装 A_Memorix 可分享记忆包。"""

    def _image_runtime(self):
        """兼容不装配图片通道的轻量内核与历史测试内核。"""

        return getattr(self._kernel, "image_memory_runtime", None)

    async def memory_bundle_admin(self, *, action: str, **kwargs: Any) -> Dict[str, Any]:
        await self.initialize()
        if self.metadata_store is None:
            return {"success": False, "error": "metadata store 未初始化"}

        act = str(action or "").strip().lower()
        if act == "export":
            return await self._export_bundle(kwargs)
        if act == "inspect":
            loaded = self._load_bundle(self._resolve_bundle_path(str(kwargs.get("path", "") or "")))
            image_runtime = self._image_runtime()
            current = image_runtime.status() if image_runtime is not None else {}
            declared = loaded["manifest"].get("image_embedding_fingerprint")
            compatible = bool(declared and current.get("fingerprint") == declared)
            return {
                "success": True, **self._public_bundle_summary(loaded),
                "images": {"vector_compatible": compatible,
                           "has_vectors": "vectors/images.npz" in loaded["member_names"],
                           "runtime_status": current.get("status", "disabled"),
                           "missing_resources": 0},
            }
        if act == "import":
            async with self._storage_cleanup_lock:
                return await self._import_bundle(kwargs)
        if act == "list":
            return self._list_installed(limit=max(1, min(200, int(kwargs.get("limit", 50) or 50))))
        if act == "resolve_file":
            path = self._resolve_bundle_path(str(kwargs.get("file_name", "") or ""), bundles_only=True)
            return {"success": True, "path": str(path), "file_name": path.name}
        if act == "uninstall":
            async with self._storage_cleanup_lock:
                return await self._uninstall(str(kwargs.get("installation_id", "") or ""))
        return {"success": False, "error": f"不支持的 bundle action: {act}"}

    def _bundles_root(self) -> Path:
        root = (Path(self.data_dir) / "imports" / "bundles").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _imports_root(self) -> Path:
        root = (Path(self.data_dir) / "imports").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _resolve_bundle_path(self, raw_path: str, *, bundles_only: bool = False) -> Path:
        token = str(raw_path or "").strip()
        if not token:
            raise ValueError("记忆包路径不能为空")
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = self._bundles_root() / candidate
        resolved = candidate.resolve()
        allowed_root = self._bundles_root() if bundles_only else self._imports_root()
        if resolved != allowed_root and allowed_root not in resolved.parents:
            raise ValueError("记忆包路径超出 A_Memorix imports 目录")
        if resolved.suffix.lower() != BUNDLE_EXTENSION:
            raise ValueError(f"记忆包必须使用 {BUNDLE_EXTENSION} 扩展名")
        if not resolved.is_file():
            raise ValueError(f"记忆包不存在: {resolved.name}")
        return resolved

    @staticmethod
    def _safe_file_stem(value: str) -> str:
        normalized = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "-", str(value or "").strip())
        return normalized.strip(".-")[:80] or "a-memorix-memory"

    async def _select_paragraphs(
        self,
        selector: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        selector_type = str(selector.get("type", "all") or "all").strip().lower()
        precision = "exact"
        resolved_values: List[str] = []

        if selector_type == "all":
            rows = self.metadata_store.query(
                """
                SELECT * FROM paragraphs
                WHERE COALESCE(is_deleted, 0) = 0
                ORDER BY created_at ASC, hash ASC
                """
            )
        elif selector_type == "source":
            raw_values = selector.get("values") or selector.get("sources") or [selector.get("value") or selector.get("source")]
            resolved_values = list(
                dict.fromkeys(str(item or "").strip() for item in raw_values if str(item or "").strip())
            )
            if not resolved_values:
                raise ValueError("source 选择器至少需要一个来源")
            rows = []
            for batch in _batched(resolved_values):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    self.metadata_store.query(
                        f"""
                        SELECT * FROM paragraphs
                        WHERE COALESCE(is_deleted, 0) = 0 AND source IN ({placeholders})
                        ORDER BY created_at ASC, hash ASC
                        """,
                        tuple(batch),
                    )
                )
        elif selector_type == "chat":
            chat_id = str(selector.get("chat_id") or selector.get("value") or "").strip()
            if not chat_id:
                raise ValueError("chat 选择器缺少 chat_id")
            resolved_values = [chat_id]
            candidates = self.metadata_store.query(
                """
                SELECT * FROM paragraphs
                WHERE COALESCE(is_deleted, 0) = 0
                ORDER BY created_at ASC, hash ASC
                """
            )
            rows = [row for row in candidates if self._paragraph_matches_chat(row, chat_id)]
        elif selector_type == "package":
            token = str(
                selector.get("installation_id") or selector.get("package_id") or selector.get("value") or ""
            ).strip()
            if not token:
                raise ValueError("package 选择器缺少 installation_id 或 package_id")
            resolved_values = [token]
            rows = self.metadata_store.query(
                """
                SELECT DISTINCT p.*
                FROM paragraphs AS p
                JOIN knowledge_package_paragraphs AS kpp ON kpp.paragraph_hash = p.hash
                JOIN knowledge_packages AS kp ON kp.installation_id = kpp.installation_id
                WHERE COALESCE(p.is_deleted, 0) = 0
                  AND (kp.installation_id = ? OR kp.package_id = ?)
                ORDER BY p.created_at ASC, p.hash ASC
                """,
                (token, token),
            )
        elif selector_type == "import_task":
            task_id = str(selector.get("task_id") or selector.get("value") or "").strip()
            if not task_id:
                raise ValueError("import_task 选择器缺少 task_id")
            if self.import_task_manager is None:
                raise RuntimeError("import manager 未初始化")
            task = await self.import_task_manager.get_task(task_id, include_chunks=False)
            if task is None:
                if not re.fullmatch(r"[0-9A-Za-z_-]+", task_id):
                    raise ValueError("导入任务 ID 格式无效")
                report_path = Path(self.data_dir) / "imports" / "reports" / f"{task_id}_summary.json"
                if report_path.is_file():
                    try:
                        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise ValueError("导入任务历史报告无法读取") from exc
                    task = report_payload if isinstance(report_payload, dict) else None
            if task is None:
                raise ValueError("导入任务不存在，或其历史报告已经不可用")
            sources: List[str] = []
            for file_item in task.get("files", []) if isinstance(task.get("files"), list) else []:
                if not isinstance(file_item, dict):
                    continue
                sources.extend(
                    str(item or "").strip()
                    for item in file_item.get("imported_sources", []) or []
                    if str(item or "").strip()
                )
            resolved_values = list(dict.fromkeys(sources))
            if not resolved_values:
                raise ValueError("该导入任务没有记录已写入的来源")
            rows = []
            for batch in _batched(resolved_values):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    self.metadata_store.query(
                        f"""
                        SELECT * FROM paragraphs
                        WHERE COALESCE(is_deleted, 0) = 0 AND source IN ({placeholders})
                        ORDER BY created_at ASC, hash ASC
                        """,
                        tuple(batch),
                    )
                )
            precision = "source_set"
        else:
            raise ValueError(f"不支持的导出选择器: {selector_type}")

        deduped = {str(row.get("hash", "")): row for row in rows if str(row.get("hash", ""))}
        ordered = sorted(deduped.values(), key=lambda row: (float(row.get("created_at") or 0), str(row["hash"])))
        return ordered, {
            "type": selector_type,
            "values": resolved_values,
            "precision": precision,
        }

    @staticmethod
    def _paragraph_matches_chat(row: Dict[str, Any], chat_id: str) -> bool:
        return chat_id in MemoryBundleAdminService._paragraph_chat_ids(row)

    @staticmethod
    def _paragraph_chat_ids(row: Dict[str, Any]) -> set[str]:
        """从新旧段落字段中提取真实聊天流 ID。"""

        metadata = _json_object(row.get("metadata"))
        chat_ids: set[str] = set()
        for key in ("chat_ids", "session_ids", "stream_ids"):
            chat_ids.update(_string_tokens(metadata.get(key)))
        for key in ("chat_id", "session_id", "stream_id"):
            value = str(metadata.get(key, "") or "").strip()
            if value:
                chat_ids.add(value)
        source = str(row.get("source", "") or "").strip()
        if source.startswith("chat_summary:"):
            source_chat_id = source.removeprefix("chat_summary:").strip()
            if source_chat_id:
                chat_ids.add(source_chat_id)
        return chat_ids

    def _query_by_values(self, table: str, column: str, values: Sequence[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        ordered_values = list(dict.fromkeys(str(item) for item in values if str(item)))
        for batch in _batched(ordered_values):
            placeholders = ",".join("?" for _ in batch)
            rows.extend(
                self.metadata_store.query(
                    f"SELECT * FROM {table} WHERE {column} IN ({placeholders})",
                    tuple(batch),
                )
            )
        return rows

    def _collect_related_rows(self, paragraphs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        paragraph_hashes = [str(row["hash"]) for row in paragraphs]
        paragraph_set = set(paragraph_hashes)
        paragraph_entities = self._query_by_values("paragraph_entities", "paragraph_hash", paragraph_hashes)
        paragraph_relations = self._query_by_values("paragraph_relations", "paragraph_hash", paragraph_hashes)
        entity_hashes = list(
            dict.fromkeys(str(row.get("entity_hash", "")) for row in paragraph_entities if row.get("entity_hash"))
        )
        relation_hashes = list(
            dict.fromkeys(str(row.get("relation_hash", "")) for row in paragraph_relations if row.get("relation_hash"))
        )
        relations = self._query_by_values("relations", "hash", relation_hashes)
        entities = self._query_by_values("entities", "hash", entity_hashes)

        known_entity_names = {str(row.get("name", "") or "").strip().casefold() for row in entities}
        endpoint_names = list(
            dict.fromkeys(
                str(value or "").strip()
                for relation in relations
                for value in (relation.get("subject"), relation.get("object"))
                if str(value or "").strip().casefold() not in known_entity_names
            )
        )
        for batch in _batched(endpoint_names):
            placeholders = ",".join("?" for _ in batch)
            entities.extend(
                self.metadata_store.query(
                    f"SELECT * FROM entities WHERE name COLLATE NOCASE IN ({placeholders})",
                    tuple(batch),
                )
            )
        entities = list({str(row.get("hash", "")): row for row in entities if row.get("hash")}.values())
        entity_hashes = [str(row["hash"]) for row in entities]

        episode_links = self._query_by_values("episode_paragraphs", "paragraph_hash", paragraph_hashes)
        candidate_episode_ids = list(
            dict.fromkeys(str(row.get("episode_id", "")) for row in episode_links if row.get("episode_id"))
        )
        closed_episode_ids: List[str] = []
        closed_episode_links: List[Dict[str, Any]] = []
        for episode_id in candidate_episode_ids:
            links = self.metadata_store.query(
                "SELECT * FROM episode_paragraphs WHERE episode_id = ? ORDER BY position ASC",
                (episode_id,),
            )
            linked_hashes = {str(row.get("paragraph_hash", "")) for row in links}
            if linked_hashes and linked_hashes.issubset(paragraph_set):
                closed_episode_ids.append(episode_id)
                closed_episode_links.extend(links)
        episodes = self._query_by_values("episodes", "episode_id", closed_episode_ids)

        semantic_ids = paragraph_set | set(entity_hashes) | set(relation_hashes) | set(closed_episode_ids)
        external_refs = self._query_by_values("external_memory_refs", "paragraph_hash", paragraph_hashes)
        fact_evidence_candidates = self._query_by_values("fact_evidence", "evidence_id", list(semantic_ids))
        fact_claim_ids = {
            str(row.get("claim_id", "")) for row in fact_evidence_candidates if str(row.get("claim_id", ""))
        }

        person_ids: set[str] = set()
        for paragraph in paragraphs:
            metadata = _json_object(paragraph.get("metadata"))
            person_ids.update(_string_tokens(metadata.get("person_ids")))
            person_id = str(metadata.get("person_id", "") or "").strip()
            if person_id:
                person_ids.add(person_id)
        selected_chat_ids = {
            chat_id
            for row in paragraphs
            for chat_id in self._paragraph_chat_ids(row)
        }

        all_claims = self.metadata_store.query("SELECT * FROM fact_claims")
        claims_by_id = {str(row.get("claim_id", "")): row for row in all_claims if row.get("claim_id")}
        for row in all_claims:
            scope_type = str(row.get("scope_type", "") or "").strip().lower()
            scope_id = str(row.get("scope_id", "") or "").strip()
            if (scope_type == "person" and scope_id in person_ids) or (
                scope_type == "chat" and scope_id in selected_chat_ids
            ):
                fact_claim_ids.add(str(row.get("claim_id", "")))

        profile_snapshots: List[Dict[str, Any]] = []
        for snapshot in self.metadata_store.query("SELECT * FROM person_profile_snapshots ORDER BY updated_at ASC"):
            evidence_ids = _string_tokens(_json_list(snapshot.get("evidence_ids_json")))
            snapshot_fact_ids = _string_tokens(_json_list(snapshot.get("fact_claim_ids_json")))
            person_id = str(snapshot.get("person_id", "") or "").strip()
            evidence_closed = not evidence_ids or evidence_ids.issubset(semantic_ids)
            facts_closed = not snapshot_fact_ids or snapshot_fact_ids.issubset(set(claims_by_id))
            if evidence_closed and facts_closed and (
                person_id in person_ids or bool(evidence_ids & semantic_ids) or bool(snapshot_fact_ids & fact_claim_ids)
            ):
                profile_snapshots.append(snapshot)
                person_ids.add(person_id)
                fact_claim_ids.update(snapshot_fact_ids)

        fact_claims = [claims_by_id[claim_id] for claim_id in sorted(fact_claim_ids) if claim_id in claims_by_id]
        fact_evidence = self._query_by_values("fact_evidence", "claim_id", sorted(fact_claim_ids))
        fact_transitions = [
            row
            for row in self.metadata_store.query("SELECT * FROM fact_transitions ORDER BY created_at ASC")
            if str(row.get("old_claim_id", "") or "") in fact_claim_ids
            or str(row.get("new_claim_id", "") or "") in fact_claim_ids
        ]

        profile_overrides = self._query_by_values("person_profile_overrides", "person_id", sorted(person_ids))
        profile_alias_overrides = self._query_by_values(
            "person_profile_alias_overrides", "person_id", sorted(person_ids)
        )
        deleted_relations = self._query_by_values("deleted_relations", "source_paragraph", paragraph_hashes)

        return {
            "paragraphs": paragraphs,
            "entities": sorted(entities, key=lambda row: str(row.get("hash", ""))),
            "relations": sorted(relations, key=lambda row: str(row.get("hash", ""))),
            "deleted_relations": sorted(deleted_relations, key=lambda row: str(row.get("hash", ""))),
            "paragraph_entities": sorted(
                paragraph_entities,
                key=lambda row: (str(row.get("paragraph_hash", "")), str(row.get("entity_hash", ""))),
            ),
            "paragraph_relations": sorted(
                paragraph_relations,
                key=lambda row: (str(row.get("paragraph_hash", "")), str(row.get("relation_hash", ""))),
            ),
            "external_memory_refs": sorted(external_refs, key=lambda row: str(row.get("external_id", ""))),
            "episodes": sorted(episodes, key=lambda row: str(row.get("episode_id", ""))),
            "episode_paragraphs": sorted(
                closed_episode_links,
                key=lambda row: (str(row.get("episode_id", "")), int(row.get("position", 0) or 0)),
            ),
            "fact_claims": fact_claims,
            "fact_evidence": sorted(
                fact_evidence,
                key=lambda row: (
                    str(row.get("claim_id", "")),
                    str(row.get("evidence_type", "")),
                    str(row.get("evidence_id", "")),
                ),
            ),
            "fact_transitions": fact_transitions,
            "person_profile_snapshots": profile_snapshots,
            "person_profile_overrides": profile_overrides,
            "person_profile_alias_overrides": profile_alias_overrides,
        }

    @staticmethod
    def _build_knowledge_docs(state_tables: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        entities_by_paragraph: Dict[str, List[str]] = defaultdict(list)
        entity_by_hash = {
            str(row.get("hash", "")): row for row in state_tables.get("entities", []) if row.get("hash")
        }
        for link in state_tables.get("paragraph_entities", []):
            entity = entity_by_hash.get(str(link.get("entity_hash", "")))
            name = str(entity.get("name", "") if entity else "").strip()
            if name:
                entities_by_paragraph[str(link.get("paragraph_hash", ""))].append(name)

        relations_by_paragraph: Dict[str, List[List[str]]] = defaultdict(list)
        relation_by_hash = {
            str(row.get("hash", "")): row for row in state_tables.get("relations", []) if row.get("hash")
        }
        for link in state_tables.get("paragraph_relations", []):
            relation = relation_by_hash.get(str(link.get("relation_hash", "")))
            if relation is None or int(relation.get("is_inactive", 0) or 0):
                continue
            triple = [
                str(relation.get("subject", "") or "").strip(),
                str(relation.get("predicate", "") or "").strip(),
                str(relation.get("object", "") or "").strip(),
            ]
            if all(triple):
                relations_by_paragraph[str(link.get("paragraph_hash", ""))].append(triple)

        docs: List[Dict[str, Any]] = []
        for paragraph in state_tables.get("paragraphs", []):
            paragraph_hash = str(paragraph.get("hash", ""))
            metadata = _json_object(paragraph.get("metadata"))
            docs.append(
                {
                    "id": paragraph_hash,
                    "passage": str(paragraph.get("content", "") or ""),
                    "extracted_triples": relations_by_paragraph.get(paragraph_hash, []),
                    "extracted_entities": list(dict.fromkeys(entities_by_paragraph.get(paragraph_hash, []))),
                    "a_memorix": {
                        "knowledge_type": str(paragraph.get("knowledge_type", "mixed") or "mixed"),
                        "source": str(paragraph.get("source", "") or ""),
                        "time_meta": {
                            key: paragraph.get(key)
                            for key in (
                                "event_time",
                                "event_time_start",
                                "event_time_end",
                                "time_granularity",
                                "time_confidence",
                            )
                            if paragraph.get(key) is not None
                        },
                        "metadata": metadata,
                    },
                }
            )
        return docs

    def _export_vector_member(
        self,
        store: Any,
        ids: Sequence[str],
        *,
        archive_ids: Optional[Sequence[str]] = None,
    ) -> Optional[bytes]:
        if store is None or not ids:
            return None
        exported_ids = list(archive_ids) if archive_ids is not None else list(ids)
        if len(exported_ids) != len(ids):
            raise ValueError("导出向量 ID 映射数量不一致")
        vector_map = store.get_vectors(ids)
        ordered_pairs = [
            (stored_id, exported_id)
            for stored_id, exported_id in zip(ids, exported_ids, strict=True)
            if stored_id in vector_map
        ]
        if not ordered_pairs:
            return None
        vectors = np.stack([vector_map[stored_id] for stored_id, _ in ordered_pairs]).astype(
            np.float32,
            copy=False,
        )
        buffer = BytesIO()
        np.savez_compressed(
            buffer,
            ids=np.asarray([exported_id for _, exported_id in ordered_pairs]),
            vectors=vectors,
        )
        return buffer.getvalue()

    def _select_image_occurrence_ids(
        self,
        *,
        selector: Dict[str, Any],
        paragraph_ids: Sequence[str],
    ) -> List[str]:
        if self._image_runtime() is None:
            return []
        selector_type = str(selector.get("type", "all") or "all").strip().lower()
        if selector_type in {"image", "images"}:
            occurrence_ids = [str(item) for item in selector.get("occurrence_ids") or [] if str(item).strip()]
            content_hashes = [str(item) for item in selector.get("content_hashes") or selector.get("values") or [] if str(item).strip()]
            rows: List[Dict[str, Any]] = []
            if occurrence_ids:
                rows.extend(self._query_by_values("image_occurrences", "occurrence_id", occurrence_ids))
            if content_hashes:
                for batch in _batched(content_hashes):
                    placeholders = ",".join("?" for _ in batch)
                    rows.extend(
                        self.metadata_store.query(
                            f"""
                            SELECT occurrence.* FROM image_occurrences AS occurrence
                            JOIN image_assets AS asset ON asset.asset_id=occurrence.asset_id
                            WHERE occurrence.status='active' AND asset.status='active'
                              AND asset.content_hash IN ({placeholders})
                            """,
                            tuple(batch),
                        )
                    )
            return list(dict.fromkeys(str(row["occurrence_id"]) for row in rows if row.get("status") == "active"))
        if selector_type == "chat":
            chat_id = str(selector.get("chat_id") or selector.get("value") or "").strip()
            rows = self.metadata_store.list_visible_image_occurrences(chat_ids=[chat_id])
            return [str(row["occurrence_id"]) for row in rows if str(row.get("chat_id") or "") == chat_id]
        if selector_type == "all":
            return [
                str(row["occurrence_id"])
                for row in self.metadata_store.list_visible_image_occurrences(chat_ids=None)
            ]
        if not paragraph_ids:
            return []
        placeholders = ",".join("?" for _ in paragraph_ids)
        rows = self.metadata_store.query(
            f"""
            SELECT DISTINCT occurrence_id FROM image_memory_links
            WHERE status='active' AND target_type='paragraph' AND target_id IN ({placeholders})
            """,
            tuple(paragraph_ids),
        )
        return [str(row["occurrence_id"]) for row in rows]

    async def _export_bundle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        content_level = str(payload.get("content_level", "knowledge") or "knowledge").strip().lower()
        if content_level not in {"knowledge", "full"}:
            raise ValueError("content_level 只支持 knowledge 或 full")
        selector_payload = payload.get("selector")
        selector = dict(selector_payload) if isinstance(selector_payload, dict) else {"type": "all"}
        selector_type = str(selector.get("type", "all") or "all").strip().lower()
        if selector_type in {"image", "images"}:
            paragraphs = []
            resolved_selector = {"type": "image", "values": list(selector.get("content_hashes") or selector.get("values") or []), "precision": "exact"}
        else:
            paragraphs, resolved_selector = await self._select_paragraphs(selector)

        selected_occurrences = self._select_image_occurrence_ids(
            selector=selector, paragraph_ids=[str(row["hash"]) for row in paragraphs],
        ) if bool(payload.get("include_images", True)) else []
        selected_links = self.metadata_store.list_image_memory_links(selected_occurrences)
        include_image_related = bool(payload.get("include_image_related", False))
        if include_image_related:
            resolved_selector["include_image_related"] = True
        paragraph_ids = {str(row["hash"]) for row in paragraphs}
        # 只有显式选择携带关联知识时才扩展文字范围，不从新增段落继续收集其他图片。
        for link in selected_links if include_image_related else []:
            target_type, target_id = str(link["target_type"]), str(link["target_id"])
            if target_type == "paragraph":
                paragraph_ids.add(target_id)
            elif target_type in {"entity", "relation", "episode"}:
                table, column = {
                    "entity": ("paragraph_entities", "entity_hash"),
                    "relation": ("paragraph_relations", "relation_hash"),
                    "episode": ("episode_paragraphs", "episode_id"),
                }[target_type]
                paragraph_ids.update(str(row["paragraph_hash"]) for row in self._query_by_values(table, column, [target_id]))
        paragraphs = [row for row in self._query_by_values("paragraphs", "hash", sorted(paragraph_ids)) if not row.get("is_deleted")]
        state_tables = self._collect_related_rows(paragraphs)
        docs = self._build_knowledge_docs(state_tables)
        knowledge = {
            "format": "lpmm_openie",
            "format_version": 1,
            "docs": docs,
        }
        state = {
            "schema": "a-memorix-semantic-state",
            "schema_version": 1,
            "tables": state_tables,
            "excluded": list(FULL_STATE_EXCLUSIONS),
        }
        paragraph_ids = [str(row["hash"]) for row in state_tables["paragraphs"]]
        image_occurrence_ids = selected_occurrences
        image_records = (
            self._image_runtime().export_records(image_occurrence_ids)
            if self._image_runtime() is not None and image_occurrence_ids
            else {"assets": [], "occurrences": [], "observations": [], "links": [], "fingerprint": {}}
        )
        has_images = bool(image_records.get("assets"))
        exported_targets = {
            "paragraph": {str(row["hash"]) for row in state_tables["paragraphs"]},
            "entity": {str(row["hash"]) for row in state_tables["entities"]},
            "relation": {str(row["hash"]) for row in state_tables["relations"]},
            "episode": {str(row["episode_id"]) for row in state_tables["episodes"]} if content_level == "full" else set(),
        }
        for link in image_records.get("links") or []:
            if str(link["target_id"]) not in exported_targets.get(str(link["target_type"]), set()):
                if include_image_related:
                    raise ValueError("图片关联目标不在可迁移知识闭包内；Episode关联请使用完整包，孤立目标需先补齐来源知识")
        image_records["links"] = [link for link in image_records.get("links") or []
                                  if str(link["target_id"]) in exported_targets.get(str(link["target_type"]), set())]
        if not paragraphs and not has_images:
            return {"success": False, "error": "当前选择范围没有可导出的文字或图片记忆"}
        content_payload = {"knowledge": knowledge, "state": state if content_level == "full" else None}
        if has_images:
            content_payload["images"] = _canonical_image_semantics(image_records)
        content_digest = hashlib.sha256(_canonical_json_bytes(content_payload)).hexdigest()

        package_payload = payload.get("package")
        package = dict(package_payload) if isinstance(package_payload, dict) else {}
        package_id = str(package.get("id") or package.get("package_id") or "").strip()
        if not package_id:
            package_id = f"amx.{content_digest[:16]}"
        version = str(package.get("version") or "1.0.0").strip()
        name = str(package.get("name") or "A_Memorix 知识包").strip()
        description = str(package.get("description") or "").strip()
        author = str(package.get("author") or "").strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        if content_level == "knowledge":
            counts = {
                table: len(state_tables[table])
                for table in ("paragraphs", "entities", "relations")
            }
        else:
            counts = {table: len(rows) for table, rows in state_tables.items()}
        if has_images:
            counts.update(
                {
                    "image_assets": len(image_records.get("assets") or []),
                    "image_occurrences": len(image_records.get("occurrences") or []),
                    "image_observations": len(image_records.get("observations") or []),
                    "image_links": len(image_records.get("links") or []),
                }
            )

        entity_ids = [str(row["hash"]) for row in state_tables["entities"]]
        relation_ids = [str(row["hash"]) for row in state_tables["relations"]]
        graph_ids = [self._graph_vector_id("entity", item_id) for item_id in entity_ids] + [
            self._graph_vector_id("relation", item_id) for item_id in relation_ids
        ]
        graph_storage_ids = graph_ids if self._dual_vector_pools_enabled() else entity_ids + relation_ids
        members: Dict[str, bytes] = {
            "knowledge.json": _canonical_json_bytes(knowledge),
        }
        if content_level == "full":
            members["state.json"] = _canonical_json_bytes(state)
        if has_images and self._image_runtime() is not None:
            portable_image_records = {
                key: value
                for key, value in image_records.items()
                if key in {"assets", "occurrences", "observations", "links", "fingerprint"}
            }
            for asset in portable_image_records["assets"]:
                storage_key = str(asset.get("storage_key") or "")
                suffix = Path(storage_key).suffix.lower() or ".bin"
                member_name = f"images/assets/{asset['content_hash']}{suffix}"
                asset["member"] = member_name
                members[member_name] = self._image_runtime().asset_store.read_bytes(storage_key)
            members["images.json"] = _canonical_json_bytes(portable_image_records)

        embedding_fingerprint: Optional[Dict[str, Any]] = None
        if bool(payload.get("include_vectors", True)):
            paragraph_store = self._paragraph_store()
            graph_store = self._graph_vector_store()
            embedding_fingerprint = self._stored_embedding_fingerprint(paragraph_store)
            if embedding_fingerprint is not None:
                paragraph_vectors = self._export_vector_member(paragraph_store, paragraph_ids)
                graph_vectors = self._export_vector_member(
                    graph_store,
                    graph_storage_ids,
                    archive_ids=graph_ids,
                )
                if paragraph_vectors is not None:
                    members["vectors/paragraphs.npz"] = paragraph_vectors
                if graph_vectors is not None:
                    members["vectors/graph.npz"] = graph_vectors
            if has_images and self._image_runtime() is not None:
                image_vectors = self._image_runtime().export_vectors(
                    [str(item.get("asset_id") or "") for item in image_records.get("assets") or []]
                )
                if image_vectors is not None:
                    members["vectors/images.npz"] = image_vectors

        manifest = {
            "format": BUNDLE_FORMAT,
            "format_version": BUNDLE_FORMAT_VERSION if has_images else BUNDLE_LEGACY_FORMAT_VERSION,
            "created_at": now_iso,
            "content_level": content_level,
            "content_digest": content_digest,
            "package": {
                "id": package_id,
                "version": version,
                "name": name,
                "description": description,
                "author": author,
            },
            "selector": resolved_selector,
            "counts": counts,
            "components": sorted(members),
            "embedding_fingerprint": embedding_fingerprint,
            "image_embedding_fingerprint": image_records.get("fingerprint") if has_images else None,
            "compatibility": {
                "knowledge_semantics": "lpmm_openie",
                "minimum_a_memorix_bundle_version": 1,
            },
        }
        members["manifest.json"] = _canonical_json_bytes(manifest)
        checksums = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(members.items())}
        members["checksums.json"] = _canonical_json_bytes({"algorithm": "sha256", "files": checksums})

        if bool(payload.get("preview", False)):
            return {"success": True, "preview": True, "manifest": manifest, "counts": counts,
                    "uncompressed_size": sum(len(content) for content in members.values())}

        filename = f"{self._safe_file_stem(name)}-{self._safe_file_stem(version)}{BUNDLE_EXTENSION}"
        target = self._bundles_root() / filename
        if target.exists():
            target = self._bundles_root() / (
                f"{self._safe_file_stem(name)}-{self._safe_file_stem(version)}-{content_digest[:8]}{BUNDLE_EXTENSION}"
            )
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for member_name, member_content in members.items():
                archive.writestr(member_name, member_content)

        return {
            "success": True,
            "file_name": target.name,
            "path": str(target),
            "manifest": manifest,
            "counts": counts,
            "size": target.stat().st_size,
        }

    @staticmethod
    def _read_archive_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[bytes, str]:
        """逐块读取单个成员，同时限制真实解压长度并计算校验和。"""

        chunks: List[bytes] = []
        digest = hashlib.sha256()
        read_size = 0
        with archive.open(info, "r") as member:
            while chunk := member.read(1024 * 1024):
                read_size += len(chunk)
                if read_size > int(info.file_size) or read_size > MAX_BUNDLE_MEMBER_BYTES:
                    raise ValueError(f"记忆包成员解压长度异常或超过限制: {info.filename}")
                digest.update(chunk)
                chunks.append(chunk)
        if read_size != int(info.file_size):
            raise ValueError(f"记忆包成员解压长度异常: {info.filename}")
        return b"".join(chunks), digest.hexdigest()

    @staticmethod
    def _hash_archive_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
        """流式校验大成员，不在内存中保留其内容。"""

        digest = hashlib.sha256()
        read_size = 0
        with archive.open(info, "r") as member:
            while chunk := member.read(1024 * 1024):
                read_size += len(chunk)
                if read_size > int(info.file_size) or read_size > MAX_BUNDLE_MEMBER_BYTES:
                    raise ValueError(f"记忆包成员解压长度异常或超过限制: {info.filename}")
                digest.update(chunk)
        if read_size != int(info.file_size):
            raise ValueError(f"记忆包成员解压长度异常: {info.filename}")
        return digest.hexdigest()

    def _load_bundle(self, path: Path) -> Dict[str, Any]:
        try:
            archive = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as exc:
            raise ValueError("记忆包不是有效的 ZIP 文件") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_BUNDLE_MEMBERS:
                raise ValueError("记忆包文件项过多")
            if any(int(info.file_size) > MAX_BUNDLE_MEMBER_BYTES for info in infos):
                raise ValueError("记忆包单个文件解压后超过 256 MiB 限制")
            if sum(int(info.file_size) for info in infos) > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise ValueError("记忆包解压后超过 1 GiB 限制")
            info_by_name = {info.filename: info for info in infos}
            names = set(info_by_name)
            if len(names) != len(infos):
                raise ValueError("记忆包包含重复文件项")
            for name in names:
                member_path = PurePosixPath(name)
                if not name or "\\" in name or member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"记忆包包含非法文件路径: {name}")
            required = {"manifest.json", "knowledge.json", "checksums.json"}
            if not required.issubset(names):
                raise ValueError("记忆包缺少 manifest.json、knowledge.json 或 checksums.json")

            json_names = required | ({"state.json", "images.json"} & names)
            json_members = {
                name: self._read_archive_member(archive, info_by_name[name])[0]
                for name in json_names
            }

            try:
                manifest = json.loads(json_members["manifest.json"])
                knowledge = json.loads(json_members["knowledge.json"])
                checksums = json.loads(json_members["checksums.json"])
                state = json.loads(json_members["state.json"]) if "state.json" in json_members else None
                images = json.loads(json_members["images.json"]) if "images.json" in json_members else None
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("记忆包中的 JSON 无法解析") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
            raise ValueError("不是 A_Memorix 记忆包")
        format_version = int(manifest.get("format_version", 0) or 0)
        if format_version not in {BUNDLE_LEGACY_FORMAT_VERSION, BUNDLE_FORMAT_VERSION}:
            raise ValueError(f"暂不支持记忆包版本: {manifest.get('format_version')}")
        if format_version == BUNDLE_LEGACY_FORMAT_VERSION and images is not None:
            raise ValueError("v1 记忆包不能包含图片扩展")
        if format_version == BUNDLE_FORMAT_VERSION and not isinstance(images, dict):
            raise ValueError("v2 记忆包缺少 images.json")
        if not isinstance(knowledge, dict) or not isinstance(knowledge.get("docs"), list):
            raise ValueError("knowledge.json 缺少 docs 数组")
        content_level = str(manifest.get("content_level", "") or "").strip().lower()
        if content_level not in {"knowledge", "full"}:
            raise ValueError("manifest.content_level 无效")
        if content_level == "full" and not isinstance(state, dict):
            raise ValueError("完整记忆包缺少 state.json")

        checksum_files = checksums.get("files") if isinstance(checksums, dict) else None
        checksum_algorithm = str(checksums.get("algorithm", "") if isinstance(checksums, dict) else "").strip().lower()
        if checksum_algorithm != "sha256" or not isinstance(checksum_files, dict):
            raise ValueError("checksums.json 格式无效")
        expected_checksum_members = names - {"checksums.json"}
        declared_checksum_members = {str(member_name) for member_name in checksum_files}
        if declared_checksum_members != expected_checksum_members:
            missing = sorted(expected_checksum_members - declared_checksum_members)
            unexpected = sorted(declared_checksum_members - expected_checksum_members)
            detail_parts = []
            if missing:
                detail_parts.append(f"缺少={','.join(missing)}")
            if unexpected:
                detail_parts.append(f"未知={','.join(unexpected)}")
            raise ValueError(f"checksums.json 未完整覆盖记忆包成员: {'; '.join(detail_parts)}")
        try:
            checksum_archive = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as exc:
            raise ValueError("记忆包在校验期间已损坏") from exc
        with checksum_archive:
            checksum_infos = checksum_archive.infolist()
            checksum_info_by_name = {info.filename: info for info in checksum_infos}
            if len(checksum_info_by_name) != len(checksum_infos) or set(checksum_info_by_name) != names:
                raise ValueError("记忆包成员在校验期间发生变化")
            for member_name, expected in checksum_files.items():
                name = str(member_name)
                content = json_members.get(name)
                actual = (
                    hashlib.sha256(content).hexdigest()
                    if content is not None
                    else self._hash_archive_member(checksum_archive, checksum_info_by_name[name])
                )
                if actual != str(expected):
                    raise ValueError(f"记忆包校验失败: {member_name}")

        if isinstance(images, dict):
            declared_asset_members: set[str] = set()
            for asset in images.get("assets") or []:
                if not isinstance(asset, dict):
                    raise ValueError("images.json 包含无效资产记录")
                member_name = str(asset.get("member") or "")
                content_hash = str(asset.get("content_hash") or "")
                if member_name in declared_asset_members or member_name not in names:
                    raise ValueError("图片资产成员缺失或被重复声明")
                declared_asset_members.add(member_name)
                if not member_name.startswith("images/assets/"):
                    raise ValueError("图片资产不在 images/assets 目录")
                if str(checksum_files.get(member_name) or "") != content_hash:
                    raise ValueError(f"图片资产内容哈希不一致: {member_name}")
            actual_asset_members = {name for name in names if name.startswith("images/assets/")}
            if actual_asset_members != declared_asset_members:
                raise ValueError("记忆包包含未声明或缺失的图片资产")

        content_payload = {"knowledge": knowledge, "state": state if content_level == "full" else None}
        if isinstance(images, dict):
            content_payload["images"] = _canonical_image_semantics(images)
        content_digest = hashlib.sha256(_canonical_json_bytes(content_payload)).hexdigest()
        if content_digest != str(manifest.get("content_digest", "") or ""):
            raise ValueError("记忆包内容摘要不一致")
        return {
            "path": path,
            "manifest": manifest,
            "knowledge": knowledge,
            "state": state,
            "images": images,
            "member_names": names,
            "member_checksums": {str(name): str(value) for name, value in checksum_files.items()},
        }

    def _read_validated_bundle_member(self, loaded: Dict[str, Any], member_name: str) -> bytes:
        """按需读取已检查成员，并防止检查后文件被替换。"""

        name = str(member_name)
        if name not in loaded["member_names"]:
            raise ValueError(f"记忆包缺少成员: {name}")
        try:
            with zipfile.ZipFile(Path(loaded["path"]), "r") as archive:
                infos = [info for info in archive.infolist() if info.filename == name]
                if len(infos) != 1 or int(infos[0].file_size) > MAX_BUNDLE_MEMBER_BYTES:
                    raise ValueError(f"记忆包成员已发生变化: {name}")
                content, actual = self._read_archive_member(archive, infos[0])
        except zipfile.BadZipFile as exc:
            raise ValueError("记忆包在安装前已损坏") from exc
        if actual != str(loaded["member_checksums"].get(name) or ""):
            raise ValueError(f"记忆包成员在检查后发生变化: {name}")
        return content

    @staticmethod
    def _public_bundle_summary(loaded: Dict[str, Any]) -> Dict[str, Any]:
        manifest = loaded["manifest"]
        return {
            "manifest": manifest,
            "package": manifest.get("package", {}),
            "content_level": manifest.get("content_level"),
            "counts": manifest.get("counts", {}),
            "file_name": Path(loaded["path"]).name,
            "size": Path(loaded["path"]).stat().st_size,
        }

    @staticmethod
    def _validate_install_scope(payload: Dict[str, Any]) -> Tuple[str, str, str]:
        scope_type = str(payload.get("scope_type", "global") or "global").strip().lower()
        chat_id = str(payload.get("chat_id", "") or "").strip()
        if scope_type == "global" and not chat_id:
            return "global", "", "global"
        if scope_type == "chat" and chat_id:
            return "chat", chat_id, chat_id
        raise ValueError("安装范围的 scope_type 与 chat_id 不一致")

    def _existing_installation(
        self,
        package_id: str,
        version: str,
        scope_type: str,
        scope_key: str,
    ) -> Optional[Dict[str, Any]]:
        rows = self.metadata_store.query(
            """
            SELECT * FROM knowledge_packages
            WHERE package_id = ? AND version = ? AND scope_type = ? AND scope_key = ?
            LIMIT 1
            """,
            (package_id, version, scope_type, scope_key),
        )
        return rows[0] if rows else None

    def _assert_restore_target_empty(self) -> None:
        tables = ("paragraphs", "entities", "relations", "episodes", "fact_claims", "person_profile_snapshots")
        counts = {
            table: int(self.metadata_store.query(f"SELECT COUNT(*) AS count FROM {table}")[0]["count"])
            for table in tables
        }
        occupied = {table: count for table, count in counts.items() if count > 0}
        if self._image_runtime() is not None:
            image_count = self.metadata_store.image_memory_stats()["asset_count"]
            if image_count:
                occupied["image_assets"] = image_count
        if occupied:
            detail = ", ".join(f"{table}={count}" for table, count in occupied.items())
            raise ValueError(f"restore 模式要求目标记忆库为空，当前存在: {detail}")

    def _import_vector_member(
        self,
        content: bytes,
        store: Any,
        *,
        allowed_ids: set[str],
        store_id_by_archive_id: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, set[str]]:
        if store is None:
            return 0, set()
        with np.load(BytesIO(content), allow_pickle=False) as payload:
            ids = [str(item) for item in payload["ids"].tolist()]
            vectors = np.asarray(payload["vectors"], dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(ids):
            raise ValueError("记忆包向量数量与 ID 数量不一致")
        accepted_indexes = [index for index, item_id in enumerate(ids) if item_id in allowed_ids]
        accepted_ids = {ids[index] for index in accepted_indexes}
        resolved_store_ids = [
            (
                index,
                store_id_by_archive_id.get(ids[index], ids[index])
                if store_id_by_archive_id is not None
                else ids[index],
            )
            for index in accepted_indexes
        ]
        missing_items = [
            (index, store_id)
            for index, store_id in resolved_store_ids
            if store_id not in store
        ]
        if not missing_items:
            return 0, accepted_ids
        store.add(
            vectors[[index for index, _ in missing_items]],
            [store_id for _, store_id in missing_items],
        )
        return len(missing_items), accepted_ids

    def _bundle_vectors_compatible(self, manifest: Dict[str, Any]) -> bool:
        packaged = manifest.get("embedding_fingerprint")
        current = self._current_embedding_fingerprint_for_validation()
        if not isinstance(packaged, dict) or not isinstance(current, dict):
            return False
        return bool(packaged.get("hash")) and str(packaged.get("hash")) == str(current.get("hash"))

    @staticmethod
    def _knowledge_doc(doc: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(doc, dict):
            return None
        passage = str(doc.get("passage", "") or "").strip()
        if not passage:
            return None
        entities = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in doc.get("extracted_entities", []) or []
                if str(item or "").strip()
            )
        )
        triples: List[Tuple[str, str, str]] = []
        for item in doc.get("extracted_triples", []) or []:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                triple = tuple(str(value or "").strip() for value in item)
                if all(triple):
                    triples.append(triple)
        extension = doc.get("a_memorix") if isinstance(doc.get("a_memorix"), dict) else {}
        return {
            "doc_id": str(doc.get("id", "") or "").strip(),
            "passage": passage,
            "entities": entities,
            "triples": triples,
            "extension": dict(extension),
        }

    def _insert_knowledge_docs(
        self,
        docs: List[Dict[str, Any]],
        *,
        package_id: str,
        installation_id: str,
        scope_type: str,
        chat_id: str,
    ) -> Tuple[List[Tuple[str, str]], set[str], set[str], List[Dict[str, Any]]]:
        now = time.time()
        mappings: List[Tuple[str, str]] = []
        entity_hashes: set[str] = set()
        relation_hashes: set[str] = set()
        resources: List[Dict[str, Any]] = []
        source = f"knowledge_pack:{package_id}"
        with self.metadata_store.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            for index, doc in enumerate(docs):
                content = str(doc["passage"])
                paragraph_hash = compute_hash(normalize_text(content))
                extension = doc["extension"]
                metadata = _json_object(extension.get("metadata"))
                metadata.update(
                    {
                        "scope_type": scope_type,
                        "knowledge_package_id": package_id,
                        "knowledge_package_installation_id": installation_id,
                    }
                )
                if scope_type == "chat":
                    metadata["chat_id"] = chat_id
                else:
                    metadata.pop("chat_id", None)
                knowledge_type = resolve_stored_knowledge_type(
                    extension.get("knowledge_type"),
                    content=content,
                ).value
                time_meta = extension.get("time_meta") if isinstance(extension.get("time_meta"), dict) else {}
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO paragraphs (
                        hash, content, vector_index, created_at, updated_at, metadata, source, word_count,
                        event_time, event_time_start, event_time_end, time_granularity, time_confidence,
                        knowledge_type, is_deleted
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        paragraph_hash,
                        content,
                        now,
                        now,
                        json.dumps(metadata, ensure_ascii=False),
                        source,
                        len(normalize_text(content).split()),
                        time_meta.get("event_time"),
                        time_meta.get("event_time_start"),
                        time_meta.get("event_time_end"),
                        time_meta.get("time_granularity"),
                        float(time_meta.get("time_confidence", 1.0) or 1.0),
                        knowledge_type,
                    ),
                )
                resources.append(
                    {
                        "resource_type": "paragraphs",
                        "resource_key": _resource_key("paragraphs", {"hash": paragraph_hash}),
                        "created_by_installation": int(cursor.rowcount > 0),
                    }
                )
                doc_id = str(doc.get("doc_id") or f"doc-{index}")
                mappings.append((doc_id, paragraph_hash))

                names = list(doc["entities"])
                names.extend(value for triple in doc["triples"] for value in (triple[0], triple[2]))
                for name in dict.fromkeys(names):
                    entity_hash = compute_hash(str(name).strip().lower())
                    entity_hashes.add(entity_hash)
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO entities (
                            hash, name, vector_index, appearance_count, created_at, metadata, is_deleted
                        ) VALUES (?, ?, NULL, 1, ?, NULL, 0)
                        """,
                        (entity_hash, name, now),
                    )
                    resources.append(
                        {
                            "resource_type": "entities",
                            "resource_key": _resource_key("entities", {"hash": entity_hash}),
                            "created_by_installation": int(cursor.rowcount > 0),
                        }
                    )
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO paragraph_entities (paragraph_hash, entity_hash, mention_count)
                        VALUES (?, ?, 1)
                        """,
                        (paragraph_hash, entity_hash),
                    )
                    resources.append(
                        {
                            "resource_type": "paragraph_entities",
                            "resource_key": _resource_key(
                                "paragraph_entities",
                                {"paragraph_hash": paragraph_hash, "entity_hash": entity_hash},
                            ),
                            "created_by_installation": int(cursor.rowcount > 0),
                        }
                    )
                for subject, predicate, obj in doc["triples"]:
                    relation_hash = self.metadata_store.compute_relation_hash(subject, predicate, obj)
                    relation_hashes.add(relation_hash)
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO relations (
                            hash, subject, predicate, object, vector_index, confidence, vector_state,
                            created_at, source_paragraph, metadata, retention_strength,
                            retention_anchor_at, next_lifecycle_at, reinforcement_count,
                            lifecycle_revision, is_inactive
                        ) VALUES (?, ?, ?, ?, NULL, 1.0, 'none', ?, ?, NULL, 1.0, ?, ?, 0, 0, 0)
                        """,
                        (relation_hash, subject, predicate, obj, now, paragraph_hash, now, now),
                    )
                    resources.append(
                        {
                            "resource_type": "relations",
                            "resource_key": _resource_key("relations", {"hash": relation_hash}),
                            "created_by_installation": int(cursor.rowcount > 0),
                        }
                    )
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO paragraph_relations (paragraph_hash, relation_hash)
                        VALUES (?, ?)
                        """,
                        (paragraph_hash, relation_hash),
                    )
                    resources.append(
                        {
                            "resource_type": "paragraph_relations",
                            "resource_key": _resource_key(
                                "paragraph_relations",
                                {"paragraph_hash": paragraph_hash, "relation_hash": relation_hash},
                            ),
                            "created_by_installation": int(cursor.rowcount > 0),
                        }
                    )
        return mappings, entity_hashes, relation_hashes, resources

    @staticmethod
    def _remap_full_state(
        tables: Dict[str, List[Dict[str, Any]]],
        *,
        package_id: str,
        installation_id: str,
        scope_type: str,
        chat_id: str,
        original_chat_ids: set[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        copied = {table: [dict(row) for row in rows] for table, rows in tables.items()}
        for paragraph in copied.get("paragraphs", []):
            paragraph["vector_index"] = None
            metadata = _json_object(paragraph.get("metadata"))
            metadata["scope_type"] = scope_type
            metadata["knowledge_package_id"] = package_id
            metadata["knowledge_package_installation_id"] = installation_id
            if scope_type == "chat":
                metadata["chat_id"] = chat_id
            else:
                metadata.pop("chat_id", None)
            paragraph["metadata"] = json.dumps(metadata, ensure_ascii=False)
            source = str(paragraph.get("source", "") or "")
            if scope_type == "chat" and source.startswith("chat_summary:"):
                paragraph["source"] = f"chat_summary:{chat_id}"
        for table in ("entities", "relations", "deleted_relations"):
            for row in copied.get(table, []):
                row["vector_index"] = None

        episode_id_map: Dict[str, str] = {}
        for episode in copied.get("episodes", []):
            source = str(episode.get("source", "") or "")
            if scope_type == "chat" and source.startswith("chat_summary:"):
                old_episode_id = str(episode.get("episode_id", "") or "")
                new_source = f"chat_summary:{chat_id}"
                evidence_ids = [str(item) for item in _json_list(episode.get("evidence_ids_json"))]
                seed = json.dumps(
                    {
                        "source": new_source,
                        "hashes": evidence_ids,
                        "version": str(episode.get("segmentation_version", "") or ""),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                new_episode_id = compute_hash(seed)
                episode_id_map[old_episode_id] = new_episode_id
                episode["episode_id"] = new_episode_id
                episode["source"] = new_source
                episode["input_fingerprint"] = ""
        for link in copied.get("episode_paragraphs", []):
            old_episode_id = str(link.get("episode_id", "") or "")
            link["episode_id"] = episode_id_map.get(old_episode_id, old_episode_id)

        claim_id_map: Dict[str, str] = {}
        if scope_type == "chat":
            for claim in copied.get("fact_claims", []):
                if str(claim.get("scope_type", "") or "").strip().lower() == "chat" and str(
                    claim.get("scope_id", "") or ""
                ).strip() in original_chat_ids:
                    old_claim_id = str(claim.get("claim_id", "") or "")
                    claim["scope_id"] = chat_id
                    claim["claim_id"] = hashlib.sha256(
                        _canonical_json_bytes(
                            {
                                "scope_type": "chat",
                                "scope_id": chat_id,
                                "fact_key": str(claim.get("fact_key", "") or ""),
                                "value": str(claim.get("value_normalized", "") or ""),
                                "polarity": str(claim.get("polarity", "") or ""),
                            }
                        )
                    ).hexdigest()
                    conflict_payload = {
                        "scope_type": "chat",
                        "scope_id": chat_id,
                        "fact_key": str(claim.get("fact_key", "") or ""),
                    }
                    if str(claim.get("cardinality", "") or "") == "set":
                        conflict_payload["value"] = str(claim.get("value_normalized", "") or "")
                    claim["conflict_group"] = hashlib.sha256(
                        _canonical_json_bytes(conflict_payload)
                    ).hexdigest()
                    claim_id_map[old_claim_id] = str(claim["claim_id"])

        for evidence in copied.get("fact_evidence", []):
            old_claim_id = str(evidence.get("claim_id", "") or "")
            evidence["claim_id"] = claim_id_map.get(old_claim_id, old_claim_id)
            old_evidence_id = str(evidence.get("evidence_id", "") or "")
            evidence["evidence_id"] = episode_id_map.get(old_evidence_id, old_evidence_id)
        for transition in copied.get("fact_transitions", []):
            for key in ("old_claim_id", "new_claim_id"):
                old_claim_id = str(transition.get(key, "") or "")
                if old_claim_id:
                    transition[key] = claim_id_map.get(old_claim_id, old_claim_id)
            old_evidence_id = str(transition.get("evidence_id", "") or "")
            transition["evidence_id"] = episode_id_map.get(old_evidence_id, old_evidence_id)
        for snapshot in copied.get("person_profile_snapshots", []):
            evidence_ids = _json_list(snapshot.get("evidence_ids_json"))
            fact_claim_ids = _json_list(snapshot.get("fact_claim_ids_json"))
            remapped_evidence_ids = _replace_json_tokens(evidence_ids, episode_id_map)
            remapped_fact_claim_ids = _replace_json_tokens(fact_claim_ids, claim_id_map)
            if remapped_evidence_ids != evidence_ids or remapped_fact_claim_ids != fact_claim_ids:
                snapshot["evidence_fingerprint"] = ""
            snapshot["evidence_ids_json"] = json.dumps(remapped_evidence_ids, ensure_ascii=False)
            snapshot["fact_claim_ids_json"] = json.dumps(remapped_fact_claim_ids, ensure_ascii=False)
        return copied

    def _insert_full_state(
        self,
        tables: Dict[str, List[Dict[str, Any]]],
    ) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
        inserted: Dict[str, int] = {}
        resources: List[Dict[str, Any]] = []
        with self.metadata_store.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            for table in FULL_STATE_TABLE_ORDER:
                rows = tables.get(table, [])
                if not rows:
                    inserted[table] = 0
                    continue
                cursor.execute(f"PRAGMA table_info({table})")
                target_columns = {str(row[1]) for row in cursor.fetchall()}
                count = 0
                for source_row in rows:
                    row = dict(source_row)
                    if table == "person_profile_snapshots":
                        row.pop("snapshot_id", None)
                    elif table == "fact_transitions":
                        row.pop("transition_id", None)
                    columns = [column for column in row if column in target_columns]
                    if not columns:
                        continue
                    placeholders = ",".join("?" for _ in columns)
                    cursor.execute(
                        f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                        tuple(row[column] for column in columns),
                    )
                    created = int(cursor.rowcount or 0) > 0
                    count += int(created)

                    identity_row = dict(source_row)
                    if table == "person_profile_snapshots":
                        if created:
                            identity_row["snapshot_id"] = int(cursor.lastrowid)
                        else:
                            cursor.execute(
                                """
                                SELECT snapshot_id FROM person_profile_snapshots
                                WHERE person_id = ? AND profile_version = ?
                                LIMIT 1
                                """,
                                (source_row.get("person_id"), source_row.get("profile_version")),
                            )
                            existing = cursor.fetchone()
                            if existing is None:
                                raise RuntimeError("无法解析完整记忆包中的人物画像快照主键")
                            identity_row["snapshot_id"] = int(existing[0])
                    elif table == "fact_transitions":
                        if not created:
                            raise RuntimeError("完整记忆包中的事实迁移记录未能写入")
                        identity_row["transition_id"] = int(cursor.lastrowid)

                    resources.append(
                        {
                            "resource_type": table,
                            "resource_key": _resource_key(table, identity_row),
                            "created_by_installation": int(created),
                        }
                    )
                inserted[table] = count
        return inserted, resources

    def _register_installation(
        self,
        manifest: Dict[str, Any],
        *,
        installation_id: str,
        scope_type: str,
        scope_key: str,
        chat_id: str,
        mappings: Sequence[Tuple[str, str]],
        resources: Sequence[Dict[str, Any]],
        status: str = "installed",
    ) -> None:
        package = manifest["package"]
        now = time.time()
        with self.metadata_store.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO knowledge_packages (
                    installation_id, package_id, version, name, content_level, content_digest,
                    manifest_json, scope_type, scope_key, chat_id, status, installed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    installation_id,
                    str(package["id"]),
                    str(package["version"]),
                    str(package["name"]),
                    str(manifest["content_level"]),
                    str(manifest["content_digest"]),
                    json.dumps(manifest, ensure_ascii=False),
                    scope_type,
                    scope_key,
                    chat_id or None,
                    str(status),
                    now,
                    now,
                ),
            )
            cursor.executemany(
                """
                INSERT OR REPLACE INTO knowledge_package_paragraphs (
                    installation_id, doc_id, paragraph_hash
                ) VALUES (?, ?, ?)
                """,
                [(installation_id, doc_id, paragraph_hash) for doc_id, paragraph_hash in mappings],
            )
            resource_ownership: Dict[Tuple[str, str], int] = {}
            for resource in resources:
                resource_type = str(resource["resource_type"])
                resource_key = str(resource["resource_key"])
                ownership_key = (resource_type, resource_key)
                resource_ownership[ownership_key] = max(
                    resource_ownership.get(ownership_key, 0),
                    int(bool(resource.get("created_by_installation"))),
                )
            cursor.executemany(
                """
                INSERT INTO knowledge_package_resources (
                    installation_id, resource_type, resource_key, created_by_installation
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (installation_id, resource_type, resource_key, created)
                    for (resource_type, resource_key), created in resource_ownership.items()
                ],
            )

    def _set_installation_status(self, installation_id: str, status: str) -> None:
        now = time.time()
        with self.metadata_store.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_packages
                SET status = ?, updated_at = ?
                WHERE installation_id = ?
                """,
                (str(status), now, str(installation_id)),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError(f"无法更新知识包安装状态: {installation_id}")

    def _install_bundle_metadata(
        self,
        loaded: Dict[str, Any],
        normalized_docs: List[Dict[str, Any]],
        *,
        package_id: str,
        installation_id: str,
        scope_type: str,
        scope_key: str,
        chat_id: str,
    ) -> Tuple[List[Tuple[str, str]], List[str], set[str], set[str], Dict[str, int]]:
        """原子写入语义状态、资源归属和可恢复的安装记录。"""

        manifest = loaded["manifest"]
        with self.metadata_store.transaction(immediate=True):
            if str(manifest["content_level"]) == "full":
                state = loaded["state"]
                raw_tables = state.get("tables") if isinstance(state, dict) else None
                if not isinstance(raw_tables, dict):
                    raise ValueError("state.json 缺少 tables")
                tables = {
                    table: [dict(row) for row in raw_tables.get(table, []) if isinstance(row, dict)]
                    for table in FULL_STATE_TABLE_ORDER
                }
                for paragraph in tables["paragraphs"]:
                    expected_hash = compute_hash(normalize_text(str(paragraph.get("content", "") or "")))
                    if str(paragraph.get("hash", "") or "") != expected_hash:
                        raise ValueError("完整包包含段落内容与稳定哈希不一致的记录")
                for entity in tables["entities"]:
                    expected_hash = compute_hash(str(entity.get("name", "") or "").strip().lower())
                    if str(entity.get("hash", "") or "") != expected_hash:
                        raise ValueError("完整包包含实体名称与稳定哈希不一致的记录")
                for relation in tables["relations"]:
                    expected_hash = self.metadata_store.compute_relation_hash(
                        str(relation.get("subject", "") or ""),
                        str(relation.get("predicate", "") or ""),
                        str(relation.get("object", "") or ""),
                    )
                    if str(relation.get("hash", "") or "") != expected_hash:
                        raise ValueError("完整包包含关系内容与稳定哈希不一致的记录")
                original_chat_ids = {
                    original_chat_id
                    for row in tables["paragraphs"]
                    for original_chat_id in self._paragraph_chat_ids(row)
                }
                tables = self._remap_full_state(
                    tables,
                    package_id=package_id,
                    installation_id=installation_id,
                    scope_type=scope_type,
                    chat_id=chat_id,
                    original_chat_ids=original_chat_ids,
                )
                inserted, resources = self._insert_full_state(tables)
                paragraph_hashes = [
                    str(row.get("hash", "")) for row in tables["paragraphs"] if row.get("hash")
                ]
                entity_hashes = {
                    str(row.get("hash", "")) for row in tables["entities"] if row.get("hash")
                }
                relation_hashes = {
                    str(row.get("hash", "")) for row in tables["relations"] if row.get("hash")
                }
                mappings = [
                    (
                        str(doc.get("doc_id") or f"doc-{index}"),
                        compute_hash(normalize_text(str(doc["passage"]))),
                    )
                    for index, doc in enumerate(normalized_docs)
                ]
            else:
                mappings, entity_hashes, relation_hashes, resources = self._insert_knowledge_docs(
                    normalized_docs,
                    package_id=package_id,
                    installation_id=installation_id,
                    scope_type=scope_type,
                    chat_id=chat_id,
                )
                paragraph_hashes = [paragraph_hash for _, paragraph_hash in mappings]
                inserted = {
                    resource_type: sum(
                        1
                        for resource in resources
                        if str(resource.get("resource_type", "")) == resource_type
                        and bool(resource.get("created_by_installation"))
                    )
                    for resource_type in ("paragraphs", "entities", "relations")
                }

            valid_mappings = [
                (doc_id, paragraph_hash)
                for doc_id, paragraph_hash in mappings
                if self.metadata_store.query(
                    "SELECT 1 AS ok FROM paragraphs WHERE hash = ? LIMIT 1",
                    (paragraph_hash,),
                )
            ]
            for paragraph_hash in paragraph_hashes:
                self.metadata_store.fts_upsert_tokenized_paragraph(paragraph_hash)
                paragraph = self.metadata_store.query(
                    "SELECT content FROM paragraphs WHERE hash = ? LIMIT 1",
                    (paragraph_hash,),
                )
                if paragraph:
                    self.metadata_store._upsert_paragraph_ngram_if_ready(
                        paragraph_hash,
                        str(paragraph[0].get("content", "") or ""),
                        count_delta=0,
                    )

            self._register_installation(
                manifest,
                installation_id=installation_id,
                scope_type=scope_type,
                scope_key=scope_key,
                chat_id=chat_id,
                mappings=valid_mappings,
                resources=resources,
                status="installing",
            )

        return valid_mappings, paragraph_hashes, entity_hashes, relation_hashes, inserted

    async def _ensure_imported_vectors(
        self,
        *,
        paragraph_hashes: Sequence[str],
        entity_hashes: Sequence[str],
        relation_hashes: Sequence[str],
        packaged_vector_ids: set[str],
    ) -> Dict[str, int]:
        generated = {"paragraphs": 0, "entities": 0, "relations": 0}
        for paragraph in self._query_by_values("paragraphs", "hash", paragraph_hashes):
            paragraph_hash = str(paragraph.get("hash", ""))
            if paragraph_hash in packaged_vector_ids:
                continue
            if await self._ensure_paragraph_vector(paragraph):
                generated["paragraphs"] += 1
        for entity in self._query_by_values("entities", "hash", entity_hashes):
            vector_id = self._graph_vector_id("entity", str(entity.get("hash", "")))
            if vector_id in packaged_vector_ids:
                continue
            if await self._ensure_entity_vector(entity):
                generated["entities"] += 1
        if self.relation_vectors_enabled:
            for relation in self._query_by_values("relations", "hash", relation_hashes):
                vector_id = self._graph_vector_id("relation", str(relation.get("hash", "")))
                if vector_id in packaged_vector_ids:
                    continue
                if await self._ensure_relation_vector(relation):
                    generated["relations"] += 1
        return generated

    async def _import_bundle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        loaded = self._load_bundle(self._resolve_bundle_path(str(payload.get("path", "") or "")))
        manifest = loaded["manifest"]
        package = manifest.get("package")
        if not isinstance(package, dict):
            raise ValueError("manifest.package 缺失")
        package_id = str(package.get("id", "") or "").strip()
        version = str(package.get("version", "") or "").strip()
        name = str(package.get("name", "") or "").strip()
        if not package_id or not version or not name:
            raise ValueError("manifest.package 的 id、version、name 不能为空")
        scope_type, chat_id, scope_key = self._validate_install_scope(payload)
        installation_id = _stable_installation_id(package_id, version, scope_type, scope_key)
        existing = self._existing_installation(package_id, version, scope_type, scope_key)
        if existing is not None:
            if str(existing.get("content_digest", "")) != str(manifest.get("content_digest", "")):
                raise ValueError("同一包版本在该范围已安装，但内容摘要不同，请提升包版本")
            existing_status = str(existing.get("status", "installed") or "installed").strip().lower()
            if existing_status == "installed":
                return {
                    "success": True,
                    "already_installed": True,
                    "installation_id": str(existing["installation_id"]),
                    "package": package,
                    "content_level": manifest["content_level"],
                }

        mode = str(payload.get("mode", "merge") or "merge").strip().lower()
        if mode not in {"merge", "restore"}:
            raise ValueError("mode 只支持 merge 或 restore")
        if mode == "restore":
            if str(manifest["content_level"]) != "full":
                raise ValueError("restore 模式只接受 full 完整记忆包")

        if existing is not None:
            existing_status = str(existing.get("status", "") or "").strip().lower()
            if existing_status not in {"installing", "failed"}:
                raise RuntimeError(f"知识包安装记录状态无法恢复: {existing_status or 'empty'}")
            rollback_result = await self._uninstall(str(existing["installation_id"]))
            if not bool(rollback_result.get("success", False)):
                raise RuntimeError(
                    f"无法清理未完成的知识包安装: {rollback_result.get('error', '未知错误')}"
                )

        if mode == "restore":
            self._assert_restore_target_empty()

        normalized_docs = [
            item
            for item in (self._knowledge_doc(doc) for doc in loaded["knowledge"].get("docs", []))
            if item is not None
        ]
        image_records = loaded.get("images") if isinstance(loaded.get("images"), dict) else None
        if not normalized_docs and image_records is None:
            raise ValueError("记忆包没有有效知识段落或图片记忆")
        if image_records is not None and self._image_runtime() is None:
            raise ValueError("当前运行时未启用图片记忆，无法安装含图片的记忆包")

        valid_mappings, paragraph_hashes, entity_hashes, relation_hashes, inserted = (
            self._install_bundle_metadata(
                loaded,
                normalized_docs,
                package_id=package_id,
                installation_id=installation_id,
                scope_type=scope_type,
                scope_key=scope_key,
                chat_id=chat_id,
            )
        )

        try:
            imported_images: Dict[str, Any] = {"assets": 0, "occurrences": 0, "asset_map": {}}
            if image_records is not None and self._image_runtime() is not None:
                target_id_map = {
                    "paragraph": {paragraph_hash: paragraph_hash for paragraph_hash in paragraph_hashes},
                    "entity": {entity_hash: entity_hash for entity_hash in entity_hashes},
                    "relation": {relation_hash: relation_hash for relation_hash in relation_hashes},
                    "episode": {
                        str(item.get("episode_id") or ""): str(item.get("episode_id") or "")
                        for item in ((loaded.get("state") or {}).get("tables") or {}).get("episodes", [])
                    },
                }
                imported_images = await self._image_runtime().import_records(
                    records=image_records,
                    asset_loader=lambda asset: self._read_validated_bundle_member(
                        loaded,
                        str(asset.get("member") or ""),
                    ),
                    installation_id=installation_id,
                    scope_type=scope_type,
                    chat_id=chat_id,
                    target_id_map=target_id_map,
                )
            graph_result = self._graph_admin_service._rebuild_graph_from_metadata()
            self.metadata_store.rebuild_relation_hash_aliases()

            packaged_vector_ids: set[str] = set()
            vector_imported = {"paragraphs": 0, "graph": 0}
            vector_reused = self._bundle_vectors_compatible(manifest)
            if vector_reused:
                member_names = loaded["member_names"]
                if "vectors/paragraphs.npz" in member_names:
                    count, ids = self._import_vector_member(
                        self._read_validated_bundle_member(loaded, "vectors/paragraphs.npz"),
                        self._paragraph_store(),
                        allowed_ids=set(paragraph_hashes),
                    )
                    vector_imported["paragraphs"] = count
                    packaged_vector_ids.update(ids)
                if "vectors/graph.npz" in member_names:
                    allowed_graph_ids = {
                        self._graph_vector_id("entity", entity_hash) for entity_hash in entity_hashes
                    }
                    if self.relation_vectors_enabled:
                        allowed_graph_ids.update(
                            self._graph_vector_id("relation", relation_hash)
                            for relation_hash in relation_hashes
                        )
                    graph_store_ids = None
                    if not self._dual_vector_pools_enabled():
                        graph_store_ids = {
                            self._graph_vector_id("entity", entity_hash): entity_hash
                            for entity_hash in entity_hashes
                        }
                        if self.relation_vectors_enabled:
                            graph_store_ids.update(
                                {
                                self._graph_vector_id("relation", relation_hash): relation_hash
                                for relation_hash in relation_hashes
                                }
                            )
                    count, ids = self._import_vector_member(
                        self._read_validated_bundle_member(loaded, "vectors/graph.npz"),
                        self._graph_vector_store(),
                        allowed_ids=allowed_graph_ids,
                        store_id_by_archive_id=graph_store_ids,
                    )
                    vector_imported["graph"] = count
                    packaged_vector_ids.update(ids)
            image_vectors_imported = 0
            if (
                image_records is not None
                and self._image_runtime() is not None
                and "vectors/images.npz" in loaded["member_names"]
            ):
                image_vectors_imported = await self._image_runtime().import_vectors(
                    self._read_validated_bundle_member(loaded, "vectors/images.npz"),
                    asset_map=dict(imported_images.get("asset_map") or {}),
                    fingerprint=dict(manifest.get("image_embedding_fingerprint") or {}),
                )
            generated_vectors = await self._ensure_imported_vectors(
                paragraph_hashes=paragraph_hashes,
                entity_hashes=sorted(entity_hashes),
                relation_hashes=sorted(relation_hashes),
                packaged_vector_ids=packaged_vector_ids,
            )
            self._persist(force_vectors=True)
            self._set_installation_status(installation_id, "installed")
        except Exception as install_error:
            try:
                rollback_result = await self._uninstall(installation_id)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"知识包安装失败且自动清理失败: 安装错误={install_error}; 清理错误={rollback_error}"
                ) from install_error
            if not bool(rollback_result.get("success", False)):
                raise RuntimeError(
                    "知识包安装失败且自动清理未完成: "
                    f"安装错误={install_error}; 清理错误={rollback_result.get('error', '未知错误')}"
                ) from install_error
            raise

        vector_result = {
            "bundle_compatible": vector_reused,
            "imported": vector_imported,
            "generated": generated_vectors,
        }
        if image_records is not None:
            vector_result["images_imported"] = image_vectors_imported
        response = {
            "success": True,
            "already_installed": False,
            "installation_id": installation_id,
            "package": package,
            "content_level": manifest["content_level"],
            "mode": mode,
            "scope_type": scope_type,
            "chat_id": chat_id,
            "inserted": inserted,
            "mapped_paragraphs": len(valid_mappings),
            "vectors": vector_result,
            "graph": graph_result,
        }
        if image_records is not None:
            image_runtime_status = self._image_runtime().status() if self._image_runtime() is not None else {}
            imported_asset_count = int(imported_images.get("assets") or 0)
            image_retrieval_status = str(image_runtime_status.get("status") or "unavailable")
            if image_retrieval_status == "ready" and image_vectors_imported < imported_asset_count:
                image_retrieval_status = "pending"
            response["images"] = {
                "assets": imported_asset_count,
                "occurrences": int(imported_images.get("occurrences") or 0),
                "content_status": "installed",
                "retrieval_status": image_retrieval_status,
                "retrieval_message": str(image_runtime_status.get("message") or ""),
            }
        return response

    def _list_installed(self, *, limit: int) -> Dict[str, Any]:
        rows = self.metadata_store.query(
            """
            SELECT kp.*, COUNT(kpp.doc_id) AS paragraph_count
            FROM knowledge_packages AS kp
            LEFT JOIN knowledge_package_paragraphs AS kpp ON kpp.installation_id = kp.installation_id
            GROUP BY kp.installation_id
            ORDER BY kp.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        for row in rows:
            row.pop("manifest_json", None)
            image_runtime = self._image_runtime()
            fp = str(image_runtime.fingerprint.get("hash", "")) if image_runtime is not None else ""
            counts = self.metadata_store.query(
                "SELECT COUNT(DISTINCT o.asset_id) AS assets, COUNT(DISTINCT o.occurrence_id) AS occurrences, "
                "COUNT(DISTINCT e.asset_id) AS ready FROM image_occurrences o "
                "LEFT JOIN image_embeddings e ON e.asset_id=o.asset_id AND e.fingerprint_hash=? AND e.status='ready' "
                "WHERE o.installation_id=? AND o.status='active'", (fp, row["installation_id"]),
            )[0]
            runtime_status = image_runtime.status()["status"] if image_runtime is not None else "disabled"
            row["images"] = {**counts, "content_status": row["status"],
                             "retrieval_status": runtime_status if runtime_status != "ready" else (
                                 "ready" if counts["assets"] == counts["ready"] else "pending"
                             )}
        return {"success": True, "items": rows, "count": len(rows)}

    async def _uninstall(self, installation_id: str) -> Dict[str, Any]:
        token = str(installation_id or "").strip()
        if not token:
            raise ValueError("installation_id 不能为空")
        rows = self.metadata_store.query(
            "SELECT * FROM knowledge_packages WHERE installation_id = ? LIMIT 1",
            (token,),
        )
        if not rows:
            return {"success": False, "error": "知识包安装记录不存在"}
        resources = self.metadata_store.query(
            """
            SELECT resource_type, resource_key, created_by_installation
            FROM knowledge_package_resources
            WHERE installation_id = ?
            """,
            (token,),
        )
        image_occurrence_count = int(
            self.metadata_store.query(
                "SELECT COUNT(*) AS count FROM image_occurrences WHERE installation_id=? AND status='active'",
                (token,),
            )[0]["count"]
        )
        if not resources and image_occurrence_count <= 0:
            return {
                "success": False,
                "error": "该安装记录缺少资源归属信息，无法安全卸载，请重新安装后再试",
            }

        released_image_assets: List[Dict[str, str]] = []
        if self._image_runtime() is not None and image_occurrence_count > 0:
            released_image_assets = await self._image_runtime().release_installation_atomic(token)

        order = {resource_type: index for index, resource_type in enumerate(reversed(FULL_STATE_TABLE_ORDER))}
        resources.sort(key=lambda item: order.get(str(item["resource_type"]), len(order)))
        removed: Dict[str, int] = defaultdict(int)
        retained_shared: Dict[str, int] = defaultdict(int)
        removed_vector_ids = {"paragraphs": [], "entities": [], "relations": []}

        with self.metadata_store.transaction(immediate=True) as connection:
            cursor = connection.cursor()
            for resource in resources:
                resource_type = str(resource["resource_type"])
                if resource_type not in RESOURCE_PRIMARY_KEYS:
                    raise RuntimeError(f"知识包包含未知资源类型: {resource_type}")
                if not int(resource["created_by_installation"] or 0):
                    continue
                resource_key = str(resource["resource_key"])
                cursor.execute(
                    """
                    SELECT installation_id, created_by_installation
                    FROM knowledge_package_resources
                    WHERE resource_type = ? AND resource_key = ? AND installation_id != ?
                    ORDER BY installation_id ASC
                    """,
                    (resource_type, resource_key, token),
                )
                shared_installations = cursor.fetchall()
                if shared_installations:
                    if not any(int(row["created_by_installation"] or 0) for row in shared_installations):
                        cursor.execute(
                            """
                            UPDATE knowledge_package_resources
                            SET created_by_installation = 1
                            WHERE installation_id = ? AND resource_type = ? AND resource_key = ?
                            """,
                            (str(shared_installations[0]["installation_id"]), resource_type, resource_key),
                        )
                    retained_shared[resource_type] += 1
                    continue

                values = _resource_key_values(resource_type, resource_key)
                primary_keys = RESOURCE_PRIMARY_KEYS[resource_type]
                where_clause = " AND ".join(f"{column} = ?" for column in primary_keys)
                if resource_type == "paragraphs":
                    paragraph_hash = str(values[0])
                    self.metadata_store.detach_fact_evidence_for_paragraphs(
                        [paragraph_hash],
                        reason="knowledge_package_uninstalled",
                        conn=connection,
                    )
                    self.metadata_store._delete_paragraph_ngrams_if_ready([paragraph_hash], count_delta=-1)
                    self.metadata_store.fts_delete_tokenized_paragraph(paragraph_hash)
                cursor.execute(f"DELETE FROM {resource_type} WHERE {where_clause}", tuple(values))
                deleted = max(0, int(cursor.rowcount or 0))
                removed[resource_type] += deleted
                if deleted and resource_type == "paragraphs":
                    removed_vector_ids["paragraphs"].append(str(values[0]))
                elif deleted and resource_type == "entities":
                    removed_vector_ids["entities"].append(str(values[0]))
                elif deleted and resource_type == "relations":
                    removed_vector_ids["relations"].append(str(values[0]))

            connection.execute("DELETE FROM knowledge_packages WHERE installation_id = ?", (token,))
            if removed.get("paragraphs"):
                self.metadata_store._refresh_paragraph_tokenized_fts_meta(connection)

        paragraph_store = self._paragraph_store()
        removed_paragraph_vectors = 0
        if paragraph_store is not None and removed_vector_ids["paragraphs"]:
            removed_paragraph_vectors = int(paragraph_store.delete(removed_vector_ids["paragraphs"]) or 0)
        graph_store = self._graph_vector_store()
        if self._dual_vector_pools_enabled():
            graph_vector_ids = [
                self._graph_vector_id("entity", entity_hash)
                for entity_hash in removed_vector_ids["entities"]
            ]
            graph_vector_ids.extend(
                self._graph_vector_id("relation", relation_hash)
                for relation_hash in removed_vector_ids["relations"]
            )
        else:
            graph_vector_ids = removed_vector_ids["entities"] + removed_vector_ids["relations"]
        removed_graph_vectors = 0
        if graph_store is not None and graph_vector_ids:
            removed_graph_vectors = int(graph_store.delete(graph_vector_ids) or 0)
        graph_result = self._graph_admin_service._rebuild_graph_from_metadata()
        self.metadata_store.rebuild_relation_hash_aliases()
        self._persist(force_vectors=True)
        if self._image_runtime() is not None:
            self._image_runtime().finalize_released_assets(released_image_assets)

        return {
            "success": True,
            "installation_id": token,
            "removed_memories": bool(sum(removed.values())),
            "removed": dict(removed),
            "retained_shared": dict(retained_shared),
            "removed_vectors": {
                "paragraphs": removed_paragraph_vectors,
                "graph": removed_graph_vectors,
            },
            "removed_images": len(released_image_assets),
            "graph": graph_result,
            "message": "知识包及其独占记忆已卸载，共享记忆已保留",
        }
