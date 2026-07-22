"""
人物画像服务

主链路：
person_id -> 用户名/别名 -> 图谱关系 + 向量证据 -> 证据总结画像 -> 快照版本化存储
"""

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from json_repair import repair_json
from sqlalchemy import or_
from sqlmodel import select


from src.common.database.database import get_db_session
from src.common.database.database_model import PersonInfo
from src.common.logger import get_logger
from src.config.config import global_config
from src.services import llm_service as llm_api

from ..embedding import EmbeddingAPIAdapter
from ..retrieval import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    FusionConfig,
    GraphRelationRecallConfig,
    PosteriorGraphConfig,
    RetrievalStrategy,
    SparseBM25Config,
    VectorPoolsConfig,
)
from ..storage import MetadataStore, GraphStore, VectorStore
from .metadata import coerce_metadata_dict
from .model_routing import (
    ResolvedLLMModel,
    generate_with_resolved_model,
    get_text_generation_model_tasks,
    pick_text_generation_task,
)
from .profile_text import build_profile_injection_text, build_structured_profile_text

logger = get_logger("A_Memorix.PersonProfileService")

PROFILE_CLASSIFICATION_REQUEST_TYPE = "A_Memorix.PersonProfileEvidenceClassify"
PROFILE_GENERATION_VERSION = 2


class PersonProfileService:
    """人物画像聚合/刷新服务。"""

    def __init__(
        self,
        metadata_store: MetadataStore,
        graph_store: Optional[GraphStore] = None,
        vector_store: Optional[VectorStore] = None,
        paragraph_vector_store: Optional[VectorStore] = None,
        graph_vector_store: Optional[VectorStore] = None,
        embedding_manager: Optional[EmbeddingAPIAdapter] = None,
        sparse_index: Any = None,
        plugin_config: Optional[dict] = None,
        retriever: Optional[DualPathRetriever] = None,
    ):
        self.metadata_store = metadata_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.paragraph_vector_store = paragraph_vector_store
        self.graph_vector_store = graph_vector_store
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

    @classmethod
    def _canonical_profile_value(cls, value: Any) -> Any:
        """规范化画像证据，消除字典顺序、集合顺序和召回排序抖动。"""
        if isinstance(value, dict):
            return {
                str(key): cls._canonical_profile_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple, set)):
            normalized = [cls._canonical_profile_value(item) for item in value]
            return sorted(
                normalized,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            )
        if isinstance(value, float):
            return round(value, 12)
        return value

    def _profile_generation_signature(self) -> Dict[str, Any]:
        """返回会影响画像文本生成结果的实现与模型配置签名。"""
        model = self._resolve_profile_classification_model()
        if model is None:
            model_signature: Dict[str, Any] = {"mode": "rule_based"}
        else:
            model_signature = {
                "mode": "llm",
                "task_name": model.task_name,
                "selected_model_name": model.selected_model_name,
                "model_list": sorted(
                    str(item).strip()
                    for item in getattr(model.task_config, "model_list", [])
                    if str(item).strip()
                ),
            }
        return {
            "generation_version": PROFILE_GENERATION_VERSION,
            "classification_max_tokens": self._profile_classification_max_tokens(),
            "classification_temperature": self._profile_classification_temperature(),
            "model": model_signature,
        }

    def _profile_evidence_fingerprint(
        self,
        *,
        person_id: str,
        primary_name: str,
        aliases: List[str],
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
        fact_claims: List[Dict[str, Any]],
    ) -> str:
        """计算画像输入版本；检索分数不属于证据内容，故不进入指纹。"""
        stable_vector_evidence = [
            {key: value for key, value in item.items() if key != "score"}
            for item in vector_evidence
        ]
        payload = self._canonical_profile_value(
            {
                "person_id": person_id,
                "primary_name": primary_name,
                "aliases": aliases,
                "memory_traits": memory_traits,
                "relation_edges": relation_edges,
                "vector_evidence": stable_vector_evidence,
                "fact_claims": fact_claims,
                "generation": self._profile_generation_signature(),
            }
        )
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _profile_classification_max_tokens(self) -> int:
        """读取人物画像证据分类的最大输出 token 数。"""
        raw_value = self._cfg("person_profile.evidence_classification_max_tokens", 1200)
        try:
            return min(32768, max(128, int(raw_value or 1200)))
        except (TypeError, ValueError):
            return 1200

    def _profile_classification_temperature(self) -> float:
        """读取人物画像证据分类的模型温度。"""
        raw_value = self._cfg("person_profile.evidence_classification_temperature", 0.1)
        try:
            return min(2.0, max(0.0, float(raw_value)))
        except (TypeError, ValueError):
            return 0.1

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
            posterior_graph_cfg_raw = self._cfg("retrieval.search.posterior_graph", {}) or {}
            vector_pools_cfg_raw = self._cfg("retrieval.vector_pools", {}) or {}
            if not isinstance(sparse_cfg_raw, dict):
                sparse_cfg_raw = {}
            if not isinstance(fusion_cfg_raw, dict):
                fusion_cfg_raw = {}
            if not isinstance(graph_recall_cfg_raw, dict):
                graph_recall_cfg_raw = {}
            if not isinstance(posterior_graph_cfg_raw, dict):
                posterior_graph_cfg_raw = {}
            if not isinstance(vector_pools_cfg_raw, dict):
                vector_pools_cfg_raw = {}

            runtime_cfg = self._cfg("runtime", {}) or {}
            if isinstance(runtime_cfg, dict) and "vector_pools_ready" in runtime_cfg:
                vector_pools_ready = bool(runtime_cfg.get("vector_pools_ready", False))
            else:
                vector_pools_ready = self.paragraph_vector_store is not None and self.graph_vector_store is not None
            configured_mode = str(vector_pools_cfg_raw.get("mode", "dual") or "dual").strip().lower()
            if configured_mode == "dual" and not vector_pools_ready:
                vector_pools_cfg_raw = dict(vector_pools_cfg_raw)
                vector_pools_cfg_raw["mode"] = "single"

            sparse_cfg = SparseBM25Config(**sparse_cfg_raw)
            fusion_cfg = FusionConfig(**fusion_cfg_raw)
            graph_recall_cfg = GraphRelationRecallConfig(**graph_recall_cfg_raw)
            posterior_graph_cfg = PosteriorGraphConfig(**posterior_graph_cfg_raw)
            vector_pools_cfg = VectorPoolsConfig(**vector_pools_cfg_raw)
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
                posterior_graph=posterior_graph_cfg,
                vector_pools=vector_pools_cfg,
            )
            return DualPathRetriever(
                vector_store=self.vector_store,
                paragraph_vector_store=self.paragraph_vector_store or self.vector_store,
                graph_vector_store=self.graph_vector_store or self.vector_store,
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
                record = session.exec(select(PersonInfo.person_id).where(PersonInfo.person_id == key).limit(1)).first()
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
                    select(PersonInfo.person_id).where(PersonInfo.group_cardname.contains(key)).limit(1)
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
                record = session.exec(select(PersonInfo).where(PersonInfo.person_id == person_id).limit(1)).first()
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

    def _collect_relation_evidence(
        self,
        aliases: List[str],
        limit: int = 30,
        *,
        person_id: str = "",
    ) -> List[Dict[str, Any]]:
        relation_by_hash: Dict[str, Dict[str, Any]] = {}
        for alias in aliases:
            for rel in self.metadata_store.get_relations(subject=alias, include_inactive=False):
                h = str(rel.get("hash", ""))
                if h:
                    relation_by_hash[h] = rel
            for rel in self.metadata_store.get_relations(object=alias, include_inactive=False):
                h = str(rel.get("hash", ""))
                if h:
                    relation_by_hash[h] = rel

        relations = list(relation_by_hash.values())
        if person_id:
            relations = [rel for rel in relations if self._is_relation_bound_to_person(rel, person_id=person_id)]
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

    def _is_relation_bound_to_person(
        self,
        relation: Dict[str, Any],
        *,
        person_id: str,
    ) -> bool:
        pid = str(person_id or "").strip()
        if not pid:
            return False

        metadata = coerce_metadata_dict(relation.get("metadata"))
        if str(metadata.get("person_id", "") or "").strip() == pid:
            return True
        if pid in self._list_tokens(metadata.get("person_ids")):
            return True

        source_paragraph = str(relation.get("source_paragraph", "") or "").strip()
        if source_paragraph:
            try:
                paragraph = self.metadata_store.get_paragraph(source_paragraph)
            except Exception:
                paragraph = None
            if isinstance(paragraph, dict):
                payload = {
                    "hash": source_paragraph,
                    "source": str(paragraph.get("source", "") or ""),
                    "metadata": coerce_metadata_dict(paragraph.get("metadata")),
                }
                return self._is_evidence_bound_to_person(payload, person_id=pid)

        return False

    def _collect_person_fact_evidence(self, person_id: str, limit: int = 4) -> List[Dict[str, Any]]:
        token = str(person_id or "").strip()
        if not token:
            return []

        source = f"person_fact:{token}"
        paragraphs = [
            row for row in self.metadata_store.get_paragraphs_by_source(source) if not bool(row.get("is_deleted", 0))
        ]
        paragraphs.sort(
            key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0.0),
            reverse=True,
        )

        evidence: List[Dict[str, Any]] = []
        for row in paragraphs[: max(1, int(limit))]:
            paragraph_hash = str(row.get("hash", "") or "")
            content = str(row.get("content", "") or "").strip()
            if not paragraph_hash or not content:
                continue
            evidence.append(
                {
                    "hash": paragraph_hash,
                    "type": "paragraph",
                    "score": 1.1,
                    "content": content[:220],
                    "source": str(row.get("source", "") or source),
                    "metadata": coerce_metadata_dict(row.get("metadata")),
                }
            )
        return self._filter_stale_paragraph_evidence(evidence)

    def _collect_person_fact_claims(self, person_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """读取人物事实账本；该顺序直接决定有界画像投影，不能依赖召回分数。"""

        return self.metadata_store.list_person_profile_fact_claims(
            person_id,
            effective_at=time.time(),
            limit=max(1, int(limit)),
        )

    def _merge_fact_claim_buckets(
        self,
        classified_buckets: Dict[str, List[str]],
        fact_claims: List[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        """将账本事实置于分类证据之前，保证 top-k 抖动不能挤出稳定事实。"""

        merged: Dict[str, List[str]] = {key: [] for key in classified_buckets}
        for claim in fact_claims:
            section = str(claim.get("profile_section", "stable_facts") or "stable_facts").strip()
            if section not in merged:
                raise ValueError(f"事实 claim 使用了未知画像段落: {section}")
            text = str(claim.get("value_text", "") or "").strip()
            if text:
                self._append_profile_bucket(merged, section, text)
        for section, values in classified_buckets.items():
            for value in values:
                self._append_profile_bucket(merged, section, value)
        return merged

    def _confine_untrusted_profile_buckets(
        self,
        classified_buckets: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        """禁止模型分类结果直接成为稳定画像真相。"""

        confined: Dict[str, List[str]] = {key: [] for key in classified_buckets}
        for section, values in classified_buckets.items():
            target = section if section in {"recent_interactions", "uncertain_notes"} else "uncertain_notes"
            for value in values:
                self._append_profile_bucket(confined, target, value)
        return confined

    @staticmethod
    def _list_tokens(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        token = str(value or "").strip()
        return [token] if token else []

    def _is_evidence_bound_to_person(
        self,
        item: Dict[str, Any],
        *,
        person_id: str,
    ) -> bool:
        """画像证据必须显式绑定到 person_id，避免别名全局召回串人。"""
        pid = str(person_id or "").strip()
        if not pid:
            return False

        metadata = coerce_metadata_dict(item.get("metadata"))
        source = str(item.get("source", "") or metadata.get("source", "") or "").strip()
        if source == f"person_fact:{pid}":
            return True

        if str(metadata.get("person_id", "") or "").strip() == pid:
            return True
        if pid in self._list_tokens(metadata.get("person_ids")):
            return True

        return False

    @staticmethod
    def _source_type_from_source(source: str) -> str:
        token = str(source or "").strip()
        if token.startswith("chat_summary:"):
            return "chat_summary"
        if token.startswith("person_fact:"):
            return "person_fact"
        return ""

    def _enrich_paragraph_evidence_metadata(
        self,
        paragraph_hash: str,
        metadata: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        merged = coerce_metadata_dict(metadata)
        source = str(merged.get("source", "") or "").strip()
        try:
            paragraph = self.metadata_store.get_paragraph(paragraph_hash)
        except Exception:
            paragraph = None
        if isinstance(paragraph, dict):
            paragraph_metadata = coerce_metadata_dict(paragraph.get("metadata"))
            if paragraph_metadata:
                merged = {**paragraph_metadata, **merged}
            source = source or str(paragraph.get("source", "") or "").strip()
        source_type = str(merged.get("source_type", "") or "").strip() or self._source_type_from_source(source)
        if source_type:
            merged["source_type"] = source_type
        if source:
            merged["source"] = source
        return merged, source

    @staticmethod
    def _is_chat_summary_evidence(item: Dict[str, Any]) -> bool:
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        source_type = str(metadata.get("source_type", "") or "").strip()
        source = str(item.get("source", "") or metadata.get("source", "") or "").strip()
        return source_type == "chat_summary" or source.startswith("chat_summary:")

    def _filter_stale_paragraph_evidence(
        self,
        evidence: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        memory_cfg = global_config.a_memorix.integration
        if not bool(getattr(memory_cfg, "feedback_correction_paragraph_hard_filter_enabled", True)):
            return evidence
        paragraph_hashes = [
            str(item.get("hash", "") or "").strip()
            for item in evidence
            if str(item.get("type", "") or "").strip() == "paragraph" and str(item.get("hash", "") or "").strip()
        ]
        if not paragraph_hashes:
            return evidence

        marks_by_paragraph = self.metadata_store.get_paragraph_stale_relation_marks_batch(paragraph_hashes)
        relation_hashes: List[str] = []
        seen = set()
        for marks in marks_by_paragraph.values():
            for mark in marks:
                relation_hash = str(mark.get("relation_hash", "") or "").strip()
                if not relation_hash or relation_hash in seen:
                    continue
                seen.add(relation_hash)
                relation_hashes.append(relation_hash)
        status_map = self.metadata_store.get_relation_status_batch(relation_hashes) if relation_hashes else {}

        filtered: List[Dict[str, Any]] = []
        for item in evidence:
            item_type = str(item.get("type", "") or "").strip()
            item_hash = str(item.get("hash", "") or "").strip()
            if item_type != "paragraph" or not item_hash:
                filtered.append(item)
                continue
            marks = marks_by_paragraph.get(item_hash, [])
            should_hide = any(
                status_map.get(str(mark.get("relation_hash", "") or "").strip()) is None
                or bool((status_map.get(str(mark.get("relation_hash", "") or "").strip()) or {}).get("is_inactive"))
                for mark in marks
                if str(mark.get("relation_hash", "") or "").strip()
            )
            if should_hide:
                continue
            filtered.append(item)
        return filtered

    async def _collect_vector_evidence(
        self,
        aliases: List[str],
        top_k: int = 12,
        person_id: str = "",
    ) -> List[Dict[str, Any]]:
        alias_queries = [a for a in aliases if a]
        if not alias_queries and not person_id:
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
                            "source": str(para.get("source", "") or ""),
                            "metadata": coerce_metadata_dict(para.get("metadata")),
                        }
                    )
                    if not self._is_evidence_bound_to_person(fallback[-1], person_id=person_id):
                        fallback.pop()
            return self._filter_stale_paragraph_evidence(fallback[:top_k])

        per_alias_top_k = max(2, int(top_k / max(1, len(alias_queries))))
        seen_hash = set()
        evidence: List[Dict[str, Any]] = []
        for item in self._collect_person_fact_evidence(person_id, limit=max(2, min(4, top_k))):
            h = str(item.get("hash", "") or "")
            if not h or h in seen_hash:
                continue
            seen_hash.add(h)
            evidence.append(item)

        for alias in alias_queries:
            try:
                results = await self.retriever.retrieve(alias, top_k=per_alias_top_k)
            except Exception as e:
                logger.warning(f"向量证据召回失败: alias={alias}, err={e}")
                continue
            for item in results:
                h = str(item.hash_value or "")
                if not h or h in seen_hash:
                    continue
                metadata, source = self._enrich_paragraph_evidence_metadata(
                    h,
                    coerce_metadata_dict(item.metadata),
                )
                payload = {
                    "hash": h,
                    "type": str(item.result_type),
                    "score": float(item.score or 0.0),
                    "content": str(item.content or "")[:220],
                    "source": source,
                    "metadata": metadata,
                }
                if not self._is_evidence_bound_to_person(payload, person_id=person_id):
                    continue
                seen_hash.add(h)
                evidence.append(payload)
        evidence.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return self._filter_stale_paragraph_evidence(evidence[:top_k])

    def _build_profile_text(
        self,
        person_id: str,
        primary_name: str,
        aliases: List[str],
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
        classified_buckets: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        """基于证据构建画像文本（供 LLM 上下文注入）。"""
        buckets = classified_buckets or self._classify_profile_evidence_rule_based(
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            memory_traits=memory_traits,
        )
        return build_structured_profile_text(
            person_id=person_id,
            primary_name=primary_name,
            aliases=aliases[:8],
            identity_settings=buckets.get("identity_settings", []),
            relationship_settings=buckets.get("relationship_settings", []),
            stable_facts=buckets.get("stable_facts", []),
            interaction_preferences=buckets.get("interaction_preferences", []),
            recent_interactions=buckets.get("recent_interactions", []),
            uncertain_notes=buckets.get("uncertain_notes", []),
        )

    def _classify_profile_evidence_rule_based(
        self,
        *,
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
    ) -> Dict[str, List[str]]:
        """规则分桶画像证据，作为 LLM 不可用时的稳定回退。"""
        buckets: Dict[str, List[str]] = {
            "identity_settings": [],
            "relationship_settings": [],
            "stable_facts": [],
            "interaction_preferences": [],
            "recent_interactions": [],
            "uncertain_notes": [],
        }
        for trait in memory_traits[:6]:
            text = str(trait or "").strip()
            if text:
                self._append_profile_bucket(buckets, self._guess_profile_bucket(text), text)

        for rel in relation_edges[:8]:
            text = self._format_relation_evidence_text(rel)
            if not text:
                continue
            bucket = self._guess_profile_bucket(text)
            if bucket == "stable_facts":
                bucket = "relationship_settings"
            self._append_profile_bucket(buckets, bucket, text)

        for item in vector_evidence:
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            if self._is_chat_summary_evidence(item):
                self._append_profile_bucket(buckets, "recent_interactions", content)
                continue
            self._append_profile_bucket(buckets, self._guess_profile_bucket(content), content)
        return buckets

    async def _classify_profile_evidence(
        self,
        *,
        person_id: str,
        primary_name: str,
        aliases: List[str],
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
    ) -> Dict[str, List[str]]:
        """用 LLM 辅助证据分桶，失败时返回规则结果。"""
        fallback = self._classify_profile_evidence_rule_based(
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            memory_traits=memory_traits,
        )
        candidates = self._build_profile_classification_candidates(
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            memory_traits=memory_traits,
        )
        if not candidates:
            return fallback

        model = self._resolve_profile_classification_model()
        if model is None:
            return fallback

        prompt = self._build_profile_classification_prompt(
            person_id=person_id,
            primary_name=primary_name,
            aliases=aliases,
            candidates=candidates,
        )
        try:
            result = await generate_with_resolved_model(
                model,
                PROFILE_CLASSIFICATION_REQUEST_TYPE,
                prompt,
                temperature=self._profile_classification_temperature(),
                max_tokens=self._profile_classification_max_tokens(),
            )
        except Exception as exc:
            logger.debug(f"人物画像证据分类模型调用失败: person_id={person_id}, err={exc}")
            return fallback
        if not bool(getattr(result, "success", False)):
            return fallback
        response = str(getattr(getattr(result, "completion", None), "response", "") or "").strip()
        parsed = self._parse_profile_classification_response(response)
        if not parsed:
            return fallback
        return self._merge_profile_classification(fallback, parsed)

    def _resolve_profile_classification_model(self) -> Optional[ResolvedLLMModel]:
        try:
            available_tasks = get_text_generation_model_tasks(llm_api)
            task_name, task_config = pick_text_generation_task(
                available_tasks,
                preferred=("memory", "utils", "planner", "tool_use", "replyer"),
            )
            if not task_name or task_config is None:
                return None
            return ResolvedLLMModel(task_name=task_name, task_config=task_config)
        except Exception as exc:
            logger.debug(f"解析人物画像分类模型失败: {exc}")
            return None

    @staticmethod
    def _build_profile_classification_prompt(
        *,
        person_id: str,
        primary_name: str,
        aliases: List[str],
        candidates: List[Dict[str, str]],
    ) -> str:
        return (
            "你要把人物画像证据归类到固定段落。只根据证据归类，不要编造。\n"
            f"人物ID: {person_id}\n"
            f"主称呼: {primary_name}\n"
            f"别名: {json.dumps(aliases, ensure_ascii=False)}\n\n"
            "分类定义：\n"
            "- identity_settings: 稳定身份、角色、长期自我描述、重要背景。\n"
            "- relationship_settings: 与麦麦、群友、组织、作品角色等长期关系或称呼关系。\n"
            "- stable_facts: 长期稳定、证据明确的人物事实。\n"
            "- interaction_preferences: 互动偏好、雷点、沟通习惯、喜欢/讨厌的相处方式。\n"
            "- recent_interactions: 最近发生、对当前聊天有帮助但不应上升为长期事实的内容。\n"
            "- uncertain_notes: 证据不足、推测、玩笑、自嘲、临时状态或疑似偏好。\n\n"
            "要求：\n"
            "1. 每条内容必须是简短中文陈述句。\n"
            "2. 不要输出证据编号、hash 或置信度。\n"
            "3. chat_summary 来源通常只能归入 recent_interactions 或 uncertain_notes。\n"
            "4. 临时状态、计划、可能、似乎、玩笑类内容不能归入 stable_facts。\n"
            "5. 只输出 JSON 对象，键为上述六类，值为字符串数组。\n\n"
            f"证据列表：\n{json.dumps(candidates, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _parse_profile_classification_response(raw: str) -> Dict[str, List[str]]:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            repaired = repair_json(text)
            payload = json.loads(repaired) if isinstance(repaired, str) else repaired
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        allowed_keys = (
            "identity_settings",
            "relationship_settings",
            "stable_facts",
            "interaction_preferences",
            "recent_interactions",
            "uncertain_notes",
        )
        parsed: Dict[str, List[str]] = {key: [] for key in allowed_keys}
        for key in allowed_keys:
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            parsed[key] = [str(item or "").strip() for item in values if str(item or "").strip()]
        return parsed

    def _merge_profile_classification(
        self,
        fallback: Dict[str, List[str]],
        llm_result: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        buckets: Dict[str, List[str]] = {key: [] for key in fallback}
        for key in buckets:
            source_values = llm_result.get(key) or fallback.get(key) or []
            for value in source_values:
                target_key = key
                if key == "stable_facts" and self._looks_uncertain_or_temporary(value):
                    target_key = "uncertain_notes"
                self._append_profile_bucket(buckets, target_key, value)
        return buckets

    def _build_profile_classification_candidates(
        self,
        *,
        relation_edges: List[Dict[str, Any]],
        vector_evidence: List[Dict[str, Any]],
        memory_traits: List[str],
    ) -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []
        for index, trait in enumerate(memory_traits[:8], start=1):
            text = str(trait or "").strip()
            if text:
                candidates.append({"id": f"trait-{index}", "source_type": "memory_trait", "text": text})
        for index, rel in enumerate(relation_edges[:12], start=1):
            text = self._format_relation_evidence_text(rel)
            if text:
                candidates.append({"id": f"relation-{index}", "source_type": "relation", "text": text})
        for index, item in enumerate(vector_evidence[:16], start=1):
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            metadata = coerce_metadata_dict(item.get("metadata"))
            source_type = str(metadata.get("source_type", "") or "").strip()
            if not source_type:
                source_type = "chat_summary" if self._is_chat_summary_evidence(item) else "person_fact"
            candidates.append({"id": f"evidence-{index}", "source_type": source_type, "text": content[:260]})
        return candidates

    @staticmethod
    def _append_profile_bucket(buckets: Dict[str, List[str]], bucket: str, text: str) -> None:
        clean = str(text or "").strip().strip("- ")
        if not clean:
            return
        values = buckets.setdefault(bucket, [])
        if clean not in values:
            values.append(clean)

    @staticmethod
    def _format_relation_evidence_text(rel: Dict[str, Any]) -> str:
        subject = str(rel.get("subject", "") or "").strip()
        predicate = str(rel.get("predicate", "") or "").strip()
        obj = str(rel.get("object", "") or "").strip()
        if not (subject and predicate and obj):
            return ""
        return f"{subject}{predicate}{obj}。"

    @classmethod
    def _guess_profile_bucket(cls, text: str) -> str:
        content = str(text or "").strip()
        if not content:
            return "stable_facts"
        if cls._looks_uncertain_or_temporary(content):
            return "uncertain_notes"
        if any(
            token in content
            for token in ("身份", "职业", "工作", "学生", "老师", "作者", "画师", "设定", "角色", "来自")
        ):
            return "identity_settings"
        if any(token in content for token in ("关系", "朋友", "同事", "群友", "主人", "搭档", "称呼", "叫", "认识")):
            return "relationship_settings"
        if any(
            token in content for token in ("喜欢", "讨厌", "偏好", "习惯", "不喜欢", "希望", "雷点", "介意", "更愿意")
        ):
            return "interaction_preferences"
        return "stable_facts"

    @staticmethod
    def _looks_uncertain_or_temporary(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        markers = (
            "可能",
            "似乎",
            "好像",
            "大概",
            "也许",
            "疑似",
            "暂时",
            "今天",
            "现在",
            "刚刚",
            "最近",
            "计划",
            "打算",
            "玩笑",
            "自嘲",
            "临时",
        )
        return any(marker in content for marker in markers)

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
        relation_edges = self._collect_relation_evidence(aliases, limit=max(10, top_k * 2), person_id=pid)
        vector_evidence = await self._collect_vector_evidence(aliases, top_k=max(4, top_k), person_id=pid)
        fact_claims = self._collect_person_fact_claims(pid, limit=max(32, top_k * 8))
        evidence_fingerprint = self._profile_evidence_fingerprint(
            person_id=pid,
            primary_name=primary_name,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            memory_traits=memory_traits,
            fact_claims=fact_claims,
        )
        expires_at = time.time() + float(ttl_seconds) if ttl_seconds > 0 else None
        if latest and str(latest.get("evidence_fingerprint", "")) == evidence_fingerprint:
            snapshot_id = latest.get("snapshot_id")
            if snapshot_id is None:
                raise RuntimeError("人物画像快照缺少 snapshot_id，无法执行证据版本短路")
            refresh_note = source_note if source_note else str(latest.get("source_note", ""))
            refreshed = self.metadata_store.refresh_person_profile_snapshot_cache(
                int(snapshot_id),
                expires_at=expires_at,
                source_note=refresh_note,
            )
            payload = {
                "success": True,
                "person_id": pid,
                "person_name": primary_name,
                "from_cache": True,
                "evidence_unchanged": True,
                **refreshed,
            }
            if aliases and not payload.get("aliases"):
                payload["aliases"] = aliases
            return self._apply_manual_override(pid, payload)

        unstructured_vector_evidence = [
            item for item in vector_evidence if not self._source_type_from_source(str(item.get("source", ""))) == "person_fact"
        ]
        classified_buckets = await self._classify_profile_evidence(
            person_id=pid,
            primary_name=primary_name,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=unstructured_vector_evidence,
            memory_traits=memory_traits,
        )
        classified_buckets = self._confine_untrusted_profile_buckets(classified_buckets)
        classified_buckets = self._merge_fact_claim_buckets(classified_buckets, fact_claims)

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
            classified_buckets=classified_buckets,
        )

        snapshot = self.metadata_store.upsert_person_profile_snapshot(
            person_id=pid,
            profile_text=profile_text,
            aliases=aliases,
            relation_edges=relation_edges,
            vector_evidence=vector_evidence,
            evidence_ids=dedup_ids,
            fact_claim_ids=[str(item.get("claim_id", "")) for item in fact_claims],
            evidence_fingerprint=evidence_fingerprint,
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
        text = build_profile_injection_text(text)
        if not text:
            return ""
        return f"【人物画像-内部参考】\n{text}\n仅供内部推理，不要向用户逐字复述。"
