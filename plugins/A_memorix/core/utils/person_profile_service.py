"""
人物画像服务

主链路：
person_id -> 用户名/别名 -> 图谱关系 + 向量证据 -> 证据总结画像 -> 快照版本化存储
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlmodel import select

from src.common.logger import get_logger
from src.common.database.database import get_db_session
from src.common.database.database_model import PersonInfo

from ..embedding import EmbeddingAPIAdapter
from ..retrieval import (
    DualPathRetriever,
    RetrievalStrategy,
    DualPathRetrieverConfig,
    SparseBM25Config,
    FusionConfig,
    GraphRelationRecallConfig,
)
from ..storage import MetadataStore, GraphStore, VectorStore

logger = get_logger("A_Memorix.PersonProfileService")


class PersonProfileService:
    """人物画像聚合/刷新服务。"""

    def __init__(
        self,
        metadata_store: MetadataStore,
        graph_store: Optional[GraphStore] = None,
        vector_store: Optional[VectorStore] = None,
        embedding_manager: Optional[EmbeddingAPIAdapter] = None,
        sparse_index: Any = None,
        plugin_config: Optional[dict] = None,
        retriever: Optional[DualPathRetriever] = None,
    ):
        self.metadata_store = metadata_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.embedding_manager = embedding_manager
        self.sparse_index = sparse_index
        self.plugin_config = plugin_config or {}
        self.retriever = retriever or self._build_retriever()

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取嵌套配置。"""
        if not isinstance(self.plugin_config, dict):
            return default
        current: Any = self.plugin_config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _build_retriever(self) -> Optional[DualPathRetriever]:
        """按需构建检索器（无依赖时返回 None）。"""
        if not all(
            [
                self.vector_store is not None,
                self.graph_store is not None,
                self.metadata_store is not None,
                self.embedding_manager is not None,
            ]
        ):
            return None
        try:
            sparse_cfg_raw = self._cfg("retrieval.sparse", {}) or {}
            fusion_cfg_raw = self._cfg("retrieval.fusion", {}) or {}
            graph_recall_cfg_raw = self._cfg("retrieval.search.graph_recall", {}) or {}
            if not isinstance(sparse_cfg_raw, dict):
                sparse_cfg_raw = {}
            if not isinstance(fusion_cfg_raw, dict):
                fusion_cfg_raw = {}
            if not isinstance(graph_recall_cfg_raw, dict):
                graph_recall_cfg_raw = {}

            sparse_cfg = SparseBM25Config(**sparse_cfg_raw)
            fusion_cfg = FusionConfig(**fusion_cfg_raw)
            graph_recall_cfg = GraphRelationRecallConfig(**graph_recall_cfg_raw)
            config = DualPathRetrieverConfig(
                top_k_paragraphs=int(self._cfg("retrieval.top_k_paragraphs", 20)),
                top_k_relations=int(self._cfg("retrieval.top_k_relations", 10)),
                top_k_final=int(self._cfg("retrieval.top_k_final", 10)),
                alpha=float(self._cfg("retrieval.alpha", 0.5)),
                enable_ppr=bool(self._cfg("retrieval.enable_ppr", True)),
                ppr_alpha=float(self._cfg("retrieval.ppr_alpha", 0.85)),
                ppr_concurrency_limit=int(self._cfg("retrieval.ppr_concurrency_limit", 4)),
                enable_parallel=bool(self._cfg("retrieval.enable_parallel", True)),
                retrieval_strategy=RetrievalStrategy.DUAL_PATH,
                debug=bool(self._cfg("advanced.debug", False)),
                sparse=sparse_cfg,
                fusion=fusion_cfg,
                graph_recall=graph_recall_cfg,
            )
            return DualPathRetriever(
                vector_store=self.vector_store,
                graph_store=self.graph_store,
                metadata_store=self.metadata_store,
                embedding_manager=self.embedding_manager,
                sparse_index=self.sparse_index,
                config=config,
            )
        except Exception as e:
            logger.warning(f"初始化人物画像检索器失败，将只使用关系证据: {e}")
            return None

    @staticmethod
    def resolve_person_id(identifier: str) -> str:
        """按 person_id 或姓名/别名解析 person_id。"""
        if not identifier:
            return ""
        key = str(identifier).strip()
        if not key:
            return ""

        try:
            with get_db_session(auto_commit=False) as session:
                record = session.exec(
                    select(PersonInfo.person_id).where(PersonInfo.person_id == key).limit(1)
                ).first()
                if record:
                    return str(record)

                record = session.exec(
                    select(PersonInfo.person_id)
                    .where(
                        or_(
                            PersonInfo.person_name == key,
                            PersonInfo.user_nickname == key,
                        )
                    )
                    .limit(1)
                ).first()
                if record:
                    return str(record)

                record = session.exec(
                    select(PersonInfo.person_id)
                    .where(PersonInfo.group_cardname.contains(key))
                    .limit(1)
                ).first()
                if record:
                    return str(record)
        except Exception as e:
            logger.warning(f"按别名解析 person_id 失败: identifier={key}, err={e}")

        if len(key) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in key):
            return key.lower()

        return ""

    def _parse_group_nicks(self, raw_value: Any) -> List[str]:
        if not raw_value:
            return []
        if isinstance(raw_value, list):
            items = raw_value
        else:
            try:
                items = json.loads(raw_value)
            except Exception:
                return []
        names: List[str] = []
        for item in items:
            if isinstance(item, dict):
                value = str(item.get("group_cardname") or item.get("group_nick_name") or "").strip()
                if value:
                    names.append(value)
            elif isinstance(item, str):
                value = item.strip()
                if value:
                    names.append(value)
        return names

    def _parse_memory_traits(self, raw_value: Any) -> List[str]:
        if not raw_value:
            return []
        try:
            values = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        except Exception:
            return []
        if not isinstance(values, list):
            return []
        traits: List[str] = []
        for item in values:
            text = str(item).strip()
            if not text:
                continue
            if ":" in text:
                parts = text.split(":")
                if len(parts) >= 3:
                    content = ":".join(parts[1:-1]).strip()
                    if content:
                        traits.append(content)
                        continue
            traits.append(text)
        return traits[:10]

    def _recover_aliases_from_memory(self, person_id: str) -> Tuple[List[str], str]:
        """当人物主档案缺失时，从已有记忆证据里回捞可用别名。"""
        if not person_id:
            return [], ""

        aliases: List[str] = []
        primary_name = ""
        seen = set()

        try:
            paragraphs = self.metadata_store.get_paragraphs_by_entity(person_id)
        except Exception as e:
            logger.warning(f"从记忆证据回捞人物别名失败: person_id={person_id}, err={e}")
            return [], ""

        for paragraph in paragraphs[:20]:
            paragraph_hash = str(paragraph.get("hash", "") or "").strip()
            if not paragraph_hash:
                continue
            try:
                paragraph_entities = self.metadata_store.get_paragraph_entities(paragraph_hash)
            except Exception:
                paragraph_entities = []
            for entity in paragraph_entities:
                name = str(entity.get("name", "") or "").strip()
                if not name or name == person_id:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                aliases.append(name)
                if not primary_name:
                    primary_name = name
        return aliases, primary_name

    def get_person_aliases(self, person_id: str) -> Tuple[List[str], str, List[str]]:
        """获取人物别名集合、主展示名、记忆特征。"""
        aliases: List[str] = []
        primary_name = ""
        memory_traits: List[str] = []
        if not person_id:
            return aliases, primary_name, memory_traits
        recovered_aliases, recovered_primary_name = self._recover_aliases_from_memory(person_id)
        try:
            with get_db_session(auto_commit=False) as session:
                record = session.exec(
                    select(PersonInfo).where(PersonInfo.person_id == person_id).limit(1)
                ).first()
                if not record:
                    return recovered_aliases, recovered_primary_name or person_id, memory_traits
            person_name = str(getattr(record, "person_name", "") or "").strip()
            nickname = str(getattr(record, "user_nickname", "") or "").strip()
            group_nicks = self._parse_group_nicks(getattr(record, "group_cardname", None))
            memory_traits = self._parse_memory_traits(getattr(record, "memory_points", None))

            primary_name = (
                person_name
                or nickname
                or recovered_primary_name
                or str(getattr(record, "user_id", "") or "").strip()
                or person_id
            )

            candidates = [person_name, nickname] + group_nicks + recovered_aliases
            seen = set()
            for item in candidates:
                norm = str(item or "").strip()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                aliases.append(norm)
        except Exception as e:
            logger.warning(f"解析人物别名失败: person_id={person_id}, err={e}")
        return aliases, primary_name, memory_traits

    def _collect_relation_evidence(self, aliases: List[str], limit: int = 30) -> List[Dict[str, Any]]:
        relation_by_hash: Dict[str, Dict[str, Any]] = {}
        for alias in aliases:
            for rel in self.metadata_store.get_relations(subject=alias):
                h = str(rel.get("hash", ""))
                if h:
                    relation_by_hash[h] = rel
            for rel in self.metadata_store.get_relations(object=alias):
                h = str(rel.get("hash", ""))
                if h:
                    relation_by_hash[h] = rel

        relations = list(relation_by_hash.values())
        relations.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        relations = relations[: max(1, int(limit))]

        edges: List[Dict[str, Any]] = []
        for rel in relations:
            edges.append(
                {
                    "hash": str(rel.get("hash", "")),
                    "subject": str(rel.get("subject", "")),
                    "predicate": str(rel.get("predicate", "")),
                    "object": str(rel.get("object", "")),
                    "confidence": float(rel.get("confidence", 1.0) or 1.0),
                }
            )
        return edges

    async def _collect_vector_evidence(self, aliases: List[str], top_k: int = 12) -> List[Dict[str, Any]]:
        alias_queries = [a for a in aliases if a]
        if not alias_queries:
            return []

        if self.retriever is None:
            # 回退：无检索器时只做简单内容匹配
            fallback: List[Dict[str, Any]] = []
            seen_hash = set()
            for alias in alias_queries:
                for para in self.metadata_store.search_paragraphs_by_content(alias)[: max(2, top_k // 2)]:
                    h = str(para.get("hash", ""))
                    if not h or h in seen_hash:
                        continue
                    seen_hash.add(h)
                    fallback.append(
                        {
                            "hash": h,
                            "type": "paragraph",
                            "score": 0.0,
                            "content": str(para.get("content", ""))[:180],
                            "metadata": {},
                        }
                    )
            return fallback[:top_k]

        per_alias_top_k = max(2, int(top_k / max(1, len(alias_queries))))
        seen_hash = set()
        evidence: List[Dict[str, Any]] = []
        for alias in alias_queries:
            try:
                results = await self.retriever.retrieve(alias, top_k=per_alias_top_k)
            except Exception as e:
                logger.warning(f"向量证据召回失败: alias={alias}, err={e}")
                continue
            for item in results:
                h = str(getattr(item, "hash_value", "") or "")
                if not h or h in seen_hash:
                    continue
                seen_hash.add(h)
                evidence.append(
                    {
                        "hash": h,
                        "type": str(getattr(item, "result_type", "")),
                        "score": float(getattr(item, "score", 0.0) or 0.0),
                        "content": str(getattr(item, "content", "") or "")[:220],
                        "metadata": dict(getattr(item, "metadata", {}) or {}),
                    }
                )
        evidence.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return evidence[:top_k]

    def _build_profile_text(
        self,
        person_id: str,
        primary_name: str,
        aliases: List[str],
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
    ) -> str:
        """基于证据构建画像文本（供 LLM 上下文注入）。"""
        lines: List[str] = []
        lines.append(f"人物ID: {person_id}")
        if primary_name:
            lines.append(f"主称呼: {primary_name}")
        if aliases:
            lines.append(f"别名: {', '.join(aliases[:8])}")
        if memory_traits:
            lines.append(f"记忆特征: {'; '.join(memory_traits[:6])}")

        if relation_edges:
            lines.append("关系证据:")
            for rel in relation_edges[:6]:
                s = rel.get("subject", "")
                p = rel.get("predicate", "")
                o = rel.get("object", "")
                conf = float(rel.get("confidence", 0.0))
                lines.append(f"- {s} {p} {o} (conf={conf:.2f})")

        if vector_evidence:
            lines.append("向量证据摘要:")
            for item in vector_evidence[:4]:
                content = str(item.get("content", "")).strip()
                if content:
                    lines.append(f"- {content}")

        if len(lines) <= 2:
            lines.append("暂无足够证据形成稳定画像。")

        return "\n".join(lines)

    @staticmethod
    def _is_snapshot_stale(snapshot: Optional[Dict[str, Any]], ttl_seconds: float) -> bool:
        if not snapshot:
            return True
        now = time.time()
        expires_at = snapshot.get("expires_at")
        if expires_at is not None:
            try:
                return now >= float(expires_at)
            except Exception:
                return True
        updated_at = float(snapshot.get("updated_at") or 0.0)
        return (now - updated_at) >= ttl_seconds

    def _apply_manual_override(self, person_id: str, profile_payload: Dict[str, Any]) -> Dict[str, Any]:
        """将手工覆盖并入画像结果（覆盖 profile_text，同时保留 auto_profile_text）。"""
        payload = dict(profile_payload or {})
        auto_text = str(payload.get("profile_text", "") or "")
        payload["auto_profile_text"] = auto_text
        payload["has_manual_override"] = False
        payload["manual_override_text"] = ""
        payload["override_updated_at"] = None
        payload["override_updated_by"] = ""
        payload["profile_source"] = "auto_snapshot"

        if not person_id or self.metadata_store is None:
            return payload

        try:
            override = self.metadata_store.get_person_profile_override(person_id)
        except Exception as e:
            logger.warning(f"读取人物画像手工覆盖失败: person_id={person_id}, err={e}")
            return payload

        if not override:
            return payload

        manual_text = str(override.get("override_text", "") or "").strip()
        if not manual_text:
            return payload

        payload["has_manual_override"] = True
        payload["manual_override_text"] = manual_text
        payload["override_updated_at"] = override.get("updated_at")
        payload["override_updated_by"] = str(override.get("updated_by", "") or "")
        payload["profile_text"] = manual_text
        payload["profile_source"] = "manual_override"
        return payload

    async def query_person_profile(
        self,
        person_id: str = "",
        person_keyword: str = "",
        top_k: int = 12,
        ttl_seconds: float = 6 * 3600,
        force_refresh: bool = False,
        source_note: str = "",
    ) -> Dict[str, Any]:
        """查询或刷新人物画像。"""
        pid = str(person_id or "").strip()
        if not pid and person_keyword:
            pid = self.resolve_person_id(person_keyword)

        if not pid:
            return {
                "success": False,
                "error": "person_id 无效，且未能通过别名解析",
            }

        latest = self.metadata_store.get_latest_person_profile_snapshot(pid)
        if not force_refresh and not self._is_snapshot_stale(latest, ttl_seconds):
            aliases, primary_name, _ = self.get_person_aliases(pid)
            payload = {
                "success": True,
                "person_id": pid,
                "person_name": primary_name,
                "from_cache": True,
                **(latest or {}),
            }
            if aliases and not payload.get("aliases"):
                payload["aliases"] = aliases
            return {
                **self._apply_manual_override(pid, payload),
            }

        aliases, primary_name, memory_traits = self.get_person_aliases(pid)
        if not aliases and person_keyword:
            aliases = [person_keyword.strip()]
            primary_name = person_keyword.strip()
        relation_edges = self._collect_relation_evidence(aliases, limit=max(10, top_k * 2))
        vector_evidence = await self._collect_vector_evidence(aliases, top_k=max(4, top_k))

        evidence_ids = [
            str(item.get("hash", ""))
            for item in (relation_edges + vector_evidence)
            if str(item.get("hash", "")).strip()
        ]
        dedup_ids: List[str] = []
        seen = set()
        for item in evidence_ids:
            if item in seen:
                continue
            seen.add(item)
            dedup_ids.append(item)

        profile_text = self._build_profile_text(
            person_id=pid,
            primary_name=primary_name,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            memory_traits=memory_traits,
        )

        expires_at = time.time() + float(ttl_seconds) if ttl_seconds > 0 else None
        snapshot = self.metadata_store.upsert_person_profile_snapshot(
            person_id=pid,
            profile_text=profile_text,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            evidence_ids=dedup_ids,
            expires_at=expires_at,
            source_note=source_note,
        )
        payload = {
            "success": True,
            "person_id": pid,
            "person_name": primary_name,
            "from_cache": False,
            **snapshot,
        }
        return {
            **self._apply_manual_override(pid, payload),
        }

    @staticmethod
    def format_persona_profile_block(profile: Dict[str, Any]) -> str:
        """格式化给 replyer 的注入块。"""
        if not profile or not profile.get("success"):
            return ""
        text = str(profile.get("profile_text", "") or "").strip()
        if not text:
            return ""
        return (
            "【人物画像-内部参考】\n"
            f"{text}\n"
            "仅供内部推理，不要向用户逐字复述。"
        )
