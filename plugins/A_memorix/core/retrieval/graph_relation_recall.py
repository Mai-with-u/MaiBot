"""Graph-assisted relation candidate recall for relation-oriented queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

from src.common.logger import get_logger

logger = get_logger("A_Memorix.GraphRelationRecall")


@dataclass
class GraphRelationRecallConfig:
    """Configuration for controlled graph relation recall."""

    enabled: bool = True
    candidate_k: int = 24
    max_hop: int = 1
    allow_two_hop_pair: bool = True
    max_paths: int = 4

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.candidate_k = max(1, int(self.candidate_k))
        self.max_hop = max(1, int(self.max_hop))
        self.allow_two_hop_pair = bool(self.allow_two_hop_pair)
        self.max_paths = max(1, int(self.max_paths))


@dataclass
class GraphRelationCandidate:
    """A graph-derived relation candidate before retriever-side fusion."""

    hash_value: str
    subject: str
    predicate: str
    object: str
    confidence: float
    graph_seed_entities: List[str]
    graph_hops: int
    graph_candidate_type: str
    supporting_paragraph_count: int

    def to_payload(self) -> Dict[str, Any]:
        content = f"{self.subject} {self.predicate} {self.object}"
        return {
            "hash": self.hash_value,
            "content": content,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "confidence": self.confidence,
            "graph_seed_entities": list(self.graph_seed_entities),
            "graph_hops": int(self.graph_hops),
            "graph_candidate_type": self.graph_candidate_type,
            "supporting_paragraph_count": int(self.supporting_paragraph_count),
        }


class GraphRelationRecallService:
    """Collect relation candidates from the entity graph in a controlled way."""

    def __init__(
        self,
        *,
        graph_store: Any,
        metadata_store: Any,
        config: Optional[GraphRelationRecallConfig] = None,
    ) -> None:
        self.graph_store = graph_store
        self.metadata_store = metadata_store
        self.config = config or GraphRelationRecallConfig()

    def recall(
        self,
        *,
        seed_entities: Sequence[str],
    ) -> List[GraphRelationCandidate]:
        if not self.config.enabled:
            return []
        if self.graph_store is None or self.metadata_store is None:
            return []

        seeds = self._normalize_seed_entities(seed_entities)
        if not seeds:
            return []

        seen_hashes: Set[str] = set()
        candidates: List[GraphRelationCandidate] = []

        if len(seeds) >= 2:
            self._collect_direct_pair_candidates(
                seed_a=seeds[0],
                seed_b=seeds[1],
                seen_hashes=seen_hashes,
                out=candidates,
            )
            if (
                len(candidates) < 3
                and self.config.allow_two_hop_pair
                and len(candidates) < self.config.candidate_k
            ):
                self._collect_two_hop_pair_candidates(
                    seed_a=seeds[0],
                    seed_b=seeds[1],
                    seen_hashes=seen_hashes,
                    out=candidates,
                )
        else:
            self._collect_one_hop_seed_candidates(
                seed=seeds[0],
                seen_hashes=seen_hashes,
                out=candidates,
            )

        return candidates[: self.config.candidate_k]

    def _normalize_seed_entities(self, seed_entities: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for raw in list(seed_entities)[:2]:
            resolved = None
            try:
                resolved = self.graph_store.find_node(str(raw), ignore_case=True)
            except Exception:
                resolved = None
            if not resolved:
                continue
            canon = str(resolved).strip().lower()
            if not canon or canon in seen:
                continue
            seen.add(canon)
            out.append(str(resolved))
        return out

    def _collect_direct_pair_candidates(
        self,
        *,
        seed_a: str,
        seed_b: str,
        seen_hashes: Set[str],
        out: List[GraphRelationCandidate],
    ) -> None:
        relation_hashes = []
        relation_hashes.extend(self.graph_store.get_relation_hashes_for_edge(seed_a, seed_b))
        relation_hashes.extend(self.graph_store.get_relation_hashes_for_edge(seed_b, seed_a))
        self._append_relation_hashes(
            relation_hashes=relation_hashes,
            seen_hashes=seen_hashes,
            out=out,
            candidate_type="direct_pair",
            graph_hops=1,
            graph_seed_entities=[seed_a, seed_b],
        )

    def _collect_two_hop_pair_candidates(
        self,
        *,
        seed_a: str,
        seed_b: str,
        seen_hashes: Set[str],
        out: List[GraphRelationCandidate],
    ) -> None:
        try:
            paths = self.graph_store.find_paths(
                seed_a,
                seed_b,
                max_depth=2,
                max_paths=self.config.max_paths,
            )
        except Exception as e:
            logger.debug(f"graph two-hop recall skipped: {e}")
            return

        for path_nodes in paths:
            if len(out) >= self.config.candidate_k:
                break
            if not isinstance(path_nodes, Sequence) or len(path_nodes) < 3:
                continue
            if len(path_nodes) != 3:
                continue
            for idx in range(len(path_nodes) - 1):
                if len(out) >= self.config.candidate_k:
                    break
                u = str(path_nodes[idx])
                v = str(path_nodes[idx + 1])
                relation_hashes = []
                relation_hashes.extend(self.graph_store.get_relation_hashes_for_edge(u, v))
                relation_hashes.extend(self.graph_store.get_relation_hashes_for_edge(v, u))
                self._append_relation_hashes(
                    relation_hashes=relation_hashes,
                    seen_hashes=seen_hashes,
                    out=out,
                    candidate_type="two_hop_pair",
                    graph_hops=2,
                    graph_seed_entities=[seed_a, seed_b],
                )

    def _collect_one_hop_seed_candidates(
        self,
        *,
        seed: str,
        seen_hashes: Set[str],
        out: List[GraphRelationCandidate],
    ) -> None:
        try:
            relation_hashes = self.graph_store.get_incident_relation_hashes(
                seed,
                limit=self.config.candidate_k,
            )
        except Exception as e:
            logger.debug(f"graph one-hop recall skipped: {e}")
            return
        self._append_relation_hashes(
            relation_hashes=relation_hashes,
            seen_hashes=seen_hashes,
            out=out,
            candidate_type="one_hop_seed",
            graph_hops=min(1, self.config.max_hop),
            graph_seed_entities=[seed],
        )

    def _append_relation_hashes(
        self,
        *,
        relation_hashes: Sequence[str],
        seen_hashes: Set[str],
        out: List[GraphRelationCandidate],
        candidate_type: str,
        graph_hops: int,
        graph_seed_entities: Sequence[str],
    ) -> None:
        for relation_hash in sorted({str(h) for h in relation_hashes if str(h).strip()}):
            if len(out) >= self.config.candidate_k:
                break
            if relation_hash in seen_hashes:
                continue
            candidate = self._build_candidate(
                relation_hash=relation_hash,
                candidate_type=candidate_type,
                graph_hops=graph_hops,
                graph_seed_entities=graph_seed_entities,
            )
            if candidate is None:
                continue
            seen_hashes.add(relation_hash)
            out.append(candidate)

    def _build_candidate(
        self,
        *,
        relation_hash: str,
        candidate_type: str,
        graph_hops: int,
        graph_seed_entities: Sequence[str],
    ) -> Optional[GraphRelationCandidate]:
        relation = self.metadata_store.get_relation(relation_hash)
        if relation is None:
            return None
        supporting_paragraphs = self.metadata_store.get_paragraphs_by_relation(relation_hash)
        return GraphRelationCandidate(
            hash_value=relation_hash,
            subject=str(relation.get("subject", "")),
            predicate=str(relation.get("predicate", "")),
            object=str(relation.get("object", "")),
            confidence=float(relation.get("confidence", 1.0) or 1.0),
            graph_seed_entities=[str(x) for x in graph_seed_entities],
            graph_hops=int(graph_hops),
            graph_candidate_type=str(candidate_type),
            supporting_paragraph_count=len(supporting_paragraphs),
        )
