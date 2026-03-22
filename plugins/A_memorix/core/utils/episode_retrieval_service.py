"""Episode hybrid retrieval service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.common.logger import get_logger

from ..retrieval import DualPathRetriever, TemporalQueryOptions

logger = get_logger("A_Memorix.EpisodeRetrievalService")


class EpisodeRetrievalService:
    """Hybrid episode retrieval backed by lexical rows and evidence projection."""

    _RRF_K = 60.0
    _BRANCH_WEIGHTS = {
        "lexical": 1.0,
        "paragraph_evidence": 1.0,
        "relation_evidence": 0.85,
    }

    def __init__(
        self,
        *,
        metadata_store: Any,
        retriever: Optional[DualPathRetriever] = None,
    ) -> None:
        self.metadata_store = metadata_store
        self.retriever = retriever

    async def query(
        self,
        *,
        query: str = "",
        top_k: int = 5,
        time_from: Optional[float] = None,
        time_to: Optional[float] = None,
        person: Optional[str] = None,
        source: Optional[str] = None,
        include_paragraphs: bool = False,
    ) -> List[Dict[str, Any]]:
        clean_query = str(query or "").strip()
        safe_top_k = max(1, int(top_k))
        candidate_k = max(30, safe_top_k * 6)

        branches: Dict[str, List[Dict[str, Any]]] = {
            "lexical": self.metadata_store.query_episodes(
                query=clean_query,
                time_from=time_from,
                time_to=time_to,
                person=person,
                source=source,
                limit=(candidate_k if clean_query else safe_top_k),
            )
        }

        if clean_query and self.retriever is not None:
            try:
                temporal = TemporalQueryOptions(
                    time_from=time_from,
                    time_to=time_to,
                    person=person,
                    source=source,
                )
                results = await self.retriever.retrieve(
                    query=clean_query,
                    top_k=candidate_k,
                    temporal=temporal,
                )
            except Exception as exc:
                logger.warning(f"episode evidence retrieval failed, fallback to lexical only: {exc}")
            else:
                paragraph_rank_map: Dict[str, int] = {}
                relation_rank_map: Dict[str, int] = {}
                for rank, item in enumerate(results, start=1):
                    hash_value = str(getattr(item, "hash_value", "") or "").strip()
                    result_type = str(getattr(item, "result_type", "") or "").strip().lower()
                    if not hash_value:
                        continue
                    if result_type == "paragraph" and hash_value not in paragraph_rank_map:
                        paragraph_rank_map[hash_value] = rank
                    elif result_type == "relation" and hash_value not in relation_rank_map:
                        relation_rank_map[hash_value] = rank

                if paragraph_rank_map:
                    paragraph_rows = self.metadata_store.get_episode_rows_by_paragraph_hashes(
                        list(paragraph_rank_map.keys()),
                        source=source,
                    )
                    if paragraph_rows:
                        branches["paragraph_evidence"] = self._rank_projected_rows(
                            paragraph_rows,
                            rank_map=paragraph_rank_map,
                            support_key="matched_paragraph_hashes",
                        )

                if relation_rank_map:
                    relation_rows = self.metadata_store.get_episode_rows_by_relation_hashes(
                        list(relation_rank_map.keys()),
                        source=source,
                    )
                    if relation_rows:
                        branches["relation_evidence"] = self._rank_projected_rows(
                            relation_rows,
                            rank_map=relation_rank_map,
                            support_key="matched_relation_hashes",
                        )

        fused = self._fuse_branches(branches, top_k=safe_top_k)
        if include_paragraphs:
            for item in fused:
                item["paragraphs"] = self.metadata_store.get_episode_paragraphs(
                    episode_id=str(item.get("episode_id") or ""),
                    limit=50,
                )
        return fused

    @staticmethod
    def _rank_projected_rows(
        rows: List[Dict[str, Any]],
        *,
        rank_map: Dict[str, int],
        support_key: str,
    ) -> List[Dict[str, Any]]:
        sentinel = 10**9
        ranked = [dict(item) for item in rows]

        def _first_support_rank(item: Dict[str, Any]) -> int:
            support_hashes = [str(x or "").strip() for x in (item.get(support_key) or [])]
            ranks = [int(rank_map[h]) for h in support_hashes if h in rank_map]
            return min(ranks) if ranks else sentinel

        ranked.sort(
            key=lambda item: (
                _first_support_rank(item),
                -int(item.get("matched_paragraph_count") or 0),
                -float(item.get("updated_at") or 0.0),
                str(item.get("episode_id") or ""),
            )
        )
        return ranked

    def _fuse_branches(
        self,
        branches: Dict[str, List[Dict[str, Any]]],
        *,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        bucket: Dict[str, Dict[str, Any]] = {}
        for branch_name, rows in branches.items():
            weight = float(self._BRANCH_WEIGHTS.get(branch_name, 0.0) or 0.0)
            if weight <= 0.0:
                continue
            for rank, row in enumerate(rows, start=1):
                episode_id = str(row.get("episode_id", "") or "").strip()
                if not episode_id:
                    continue
                if episode_id not in bucket:
                    payload = dict(row)
                    payload.pop("matched_paragraph_hashes", None)
                    payload.pop("matched_relation_hashes", None)
                    payload.pop("matched_paragraph_count", None)
                    payload.pop("matched_relation_count", None)
                    payload["_fusion_score"] = 0.0
                    bucket[episode_id] = payload
                bucket[episode_id]["_fusion_score"] = float(
                    bucket[episode_id].get("_fusion_score", 0.0)
                ) + weight / (self._RRF_K + float(rank))

        out = list(bucket.values())
        out.sort(
            key=lambda item: (
                -float(item.get("_fusion_score", 0.0)),
                -float(item.get("updated_at") or 0.0),
                str(item.get("episode_id") or ""),
            )
        )
        for item in out:
            item.pop("_fusion_score", None)
        return out[: max(1, int(top_k))]
