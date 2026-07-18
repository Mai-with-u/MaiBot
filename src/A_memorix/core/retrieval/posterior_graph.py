from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Set, Tuple

import re

import jieba

if TYPE_CHECKING:
    from .dual_path import DualPathRetriever, RetrievalResult


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_BROAD_PREDICATES = {
    "contains_fact",
    "describe",
    "describes",
    "description",
    "mention",
    "mentions",
    "summary",
    "summarizes",
}


@dataclass
class PosteriorGraphConfig:
    """双重方案中的后验图补位配置。"""

    enabled: bool = True
    drop_ratio: float = 0.15
    min_core_results: int = 2
    max_graph_slots: int = 2
    gate_scan_top_k: int = 5
    grounded_confidence_threshold: float = 0.48
    incidental_confidence_threshold: float = 0.22
    min_query_token_coverage: float = 0.78
    incidental_query_relevance_threshold: float = 0.68
    incidental_core_overlap_threshold: float = 0.34
    incidental_specificity_threshold: float = 0.42
    query_weight: float = 0.28
    novelty_weight: float = 0.18
    complementarity_weight: float = 0.16
    specificity_weight: float = 0.12
    gap_fill_weight: float = 0.26
    max_candidate_tokens: int = 48

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.drop_ratio = max(0.0, float(self.drop_ratio))
        self.min_core_results = max(1, int(self.min_core_results))
        self.max_graph_slots = max(0, int(self.max_graph_slots))
        self.gate_scan_top_k = max(1, int(self.gate_scan_top_k))
        self.grounded_confidence_threshold = _clip_score(self.grounded_confidence_threshold)
        self.incidental_confidence_threshold = _clip_score(self.incidental_confidence_threshold)
        self.min_query_token_coverage = _clip_score(self.min_query_token_coverage)
        self.incidental_query_relevance_threshold = _clip_score(self.incidental_query_relevance_threshold)
        self.incidental_core_overlap_threshold = _clip_score(self.incidental_core_overlap_threshold)
        self.incidental_specificity_threshold = _clip_score(self.incidental_specificity_threshold)
        self.query_weight = max(0.0, float(self.query_weight))
        self.novelty_weight = max(0.0, float(self.novelty_weight))
        self.complementarity_weight = max(0.0, float(self.complementarity_weight))
        self.specificity_weight = max(0.0, float(self.specificity_weight))
        self.gap_fill_weight = max(0.0, float(self.gap_fill_weight))
        self.max_candidate_tokens = max(8, int(self.max_candidate_tokens))


@dataclass
class _CompetitionProfile:
    text: str
    tokens: Set[str]
    entities: Set[str]


@dataclass
class _SeedEvidence:
    name: str
    strength: str
    support_count: int
    rank_hint: int


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _clip_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _is_cjk_chunk(token: str) -> bool:
    return bool(token) and all("\u4e00" <= char <= "\u9fff" for char in token)


def _tokenize_for_competition(text: str, *, max_tokens: int) -> List[str]:
    normalized = str(text or "").lower().strip()
    if not normalized:
        return []

    tokens: List[str] = []
    for chunk in _TOKEN_PATTERN.findall(normalized):
        if _is_cjk_chunk(chunk):
            tokens.extend(item.strip().lower() for item in jieba.lcut_for_search(chunk) if item.strip())
        else:
            tokens.append(chunk)

    filtered: List[str] = []
    for token in tokens:
        if len(token) <= 1:
            continue
        filtered.append(token)
        if len(filtered) >= max_tokens:
            break
    return filtered


def _result_text_for_entity_match(result: RetrievalResult) -> str:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    parts = [
        str(result.content or ""),
        str(metadata.get("subject", "") or ""),
        str(metadata.get("object", "") or ""),
        str(metadata.get("context_title", "") or ""),
        str(metadata.get("benchmark_title", "") or ""),
    ]
    return "\n".join(part for part in parts if part)


def _candidate_text_for_competition(result: RetrievalResult) -> str:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    parts = [
        _result_text_for_entity_match(result),
        str(metadata.get("predicate", "") or ""),
    ]
    return "\n".join(part for part in parts if part)


def _extract_candidate_entities(
    retriever: DualPathRetriever,
    result: RetrievalResult,
) -> Set[str]:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    entities: Set[str] = set()

    for name in retriever._extract_entities(_candidate_text_for_competition(result)).keys():
        normalized = str(name or "").strip().lower()
        if normalized:
            entities.add(normalized)

    for key in ("benchmark_title", "context_title", "object", "subject"):
        normalized = str(metadata.get(key, "") or "").strip().lower()
        if normalized:
            entities.add(normalized)

    return entities


def _build_query_profile(
    retriever: DualPathRetriever,
    query: str,
    *,
    max_tokens: int,
) -> _CompetitionProfile:
    text = str(query or "")
    tokens = set(_tokenize_for_competition(text, max_tokens=max_tokens))
    entities = {
        str(name or "").strip().lower() for name in retriever._extract_entities(text).keys() if str(name or "").strip()
    }
    return _CompetitionProfile(text=text, tokens=tokens, entities=entities)


def _build_candidate_profile(
    retriever: DualPathRetriever,
    result: RetrievalResult,
    *,
    max_tokens: int,
) -> _CompetitionProfile:
    text = _candidate_text_for_competition(result)
    return _CompetitionProfile(
        text=text,
        tokens=set(_tokenize_for_competition(text, max_tokens=max_tokens)),
        entities=_extract_candidate_entities(retriever, result),
    )


def _build_core_profile(
    retriever: DualPathRetriever,
    results: Sequence[RetrievalResult],
    *,
    max_tokens: int,
) -> _CompetitionProfile:
    parts: List[str] = []
    tokens: Set[str] = set()
    entities: Set[str] = set()

    for result in results:
        profile = _build_candidate_profile(retriever, result, max_tokens=max_tokens)
        parts.append(profile.text)
        tokens.update(profile.tokens)
        entities.update(profile.entities)

    return _CompetitionProfile(text="\n".join(parts), tokens=tokens, entities=entities)


def _compute_query_relevance(candidate: _CompetitionProfile, query: _CompetitionProfile) -> float:
    entity_hit = _safe_ratio(len(candidate.entities & query.entities), len(query.entities))
    token_hit = _safe_ratio(len(candidate.tokens & query.tokens), len(query.tokens))
    if query.entities:
        return _clip_score(0.65 * entity_hit + 0.35 * token_hit)
    return _clip_score(max(entity_hit, token_hit))


def _compute_novelty(candidate: _CompetitionProfile, core: _CompetitionProfile) -> float:
    entity_novelty = _safe_ratio(len(candidate.entities - core.entities), len(candidate.entities))
    token_novelty = _safe_ratio(len(candidate.tokens - core.tokens), len(candidate.tokens))
    return _clip_score(0.5 * entity_novelty + 0.5 * token_novelty)


def _compute_complementarity(
    candidate: _CompetitionProfile,
    core: _CompetitionProfile,
    query_relevance: float,
) -> float:
    if not core.tokens and not core.entities:
        return _clip_score(query_relevance)

    entity_overlap = _safe_ratio(len(candidate.entities & core.entities), len(candidate.entities))
    token_overlap = _safe_ratio(len(candidate.tokens & core.tokens), len(candidate.tokens))
    core_overlap = 0.5 * entity_overlap + 0.5 * token_overlap
    sweet_spot = 1.0 - abs(core_overlap - 0.4) / 0.4
    return _clip_score(max(0.0, sweet_spot) * max(query_relevance, 0.2))


def _compute_specificity(candidate: _CompetitionProfile, result: RetrievalResult) -> float:
    token_count = max(1, len(candidate.tokens))
    entity_density = _clip_score(_safe_ratio(len(candidate.entities), token_count) * 4.0)
    brevity = 1.0 - min(1.0, max(0, token_count - 16) / 16.0)
    predicate_bonus = 0.0

    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    predicate = str(metadata.get("predicate", "") or "").strip().lower()
    if predicate:
        if predicate in _BROAD_PREDICATES:
            predicate_bonus = -0.25
        elif result.result_type == "relation":
            predicate_bonus = 0.10

    return _clip_score(0.6 * entity_density + 0.4 * brevity + predicate_bonus)


def _compute_gap_fill(
    candidate: _CompetitionProfile,
    query: _CompetitionProfile,
    core: _CompetitionProfile,
) -> float:
    missing_entities = query.entities - core.entities
    missing_tokens = query.tokens - core.tokens

    entity_fill = _safe_ratio(len(candidate.entities & missing_entities), len(missing_entities))
    token_fill = _safe_ratio(len(candidate.tokens & missing_tokens), len(missing_tokens))

    if missing_entities:
        return _clip_score(0.7 * entity_fill + 0.3 * token_fill)
    return _clip_score(max(entity_fill, token_fill))


def _core_overlap(candidate: _CompetitionProfile, core: _CompetitionProfile) -> float:
    entity_overlap = _safe_ratio(len(candidate.entities & core.entities), len(candidate.entities))
    token_overlap = _safe_ratio(len(candidate.tokens & core.tokens), len(candidate.tokens))
    return _clip_score(0.5 * entity_overlap + 0.5 * token_overlap)


def _compute_competition_score(
    retriever: DualPathRetriever,
    candidate: RetrievalResult,
    *,
    query_profile: _CompetitionProfile,
    core_profile: _CompetitionProfile,
    cfg: PosteriorGraphConfig,
) -> Tuple[float, Dict[str, float]]:
    candidate_profile = _build_candidate_profile(
        retriever,
        candidate,
        max_tokens=cfg.max_candidate_tokens,
    )
    query_relevance = _compute_query_relevance(candidate_profile, query_profile)
    novelty = _compute_novelty(candidate_profile, core_profile)
    complementarity = _compute_complementarity(candidate_profile, core_profile, query_relevance)
    specificity = _compute_specificity(candidate_profile, candidate)
    gap_fill = _compute_gap_fill(candidate_profile, query_profile, core_profile)

    final_score = (
        cfg.query_weight * query_relevance
        + cfg.novelty_weight * novelty
        + cfg.complementarity_weight * complementarity
        + cfg.specificity_weight * specificity
        + cfg.gap_fill_weight * gap_fill
    )
    breakdown = {
        "query_relevance": round(query_relevance, 4),
        "novelty": round(novelty, 4),
        "complementarity": round(complementarity, 4),
        "specificity": round(specificity, 4),
        "gap_fill": round(gap_fill, 4),
        "competition_score": round(_clip_score(final_score), 4),
    }
    return _clip_score(final_score), breakdown


def _top_score(results: Sequence[RetrievalResult]) -> float:
    if not results:
        return 0.0
    return max(float(item.score) for item in results)


def find_score_cliff(
    results: Sequence[RetrievalResult],
    *,
    drop_ratio: float,
    min_core_results: int,
) -> int:
    ranked = list(results)
    if not ranked:
        return 0
    if len(ranked) <= min_core_results:
        return len(ranked)

    for index in range(1, len(ranked)):
        prev_score = max(float(ranked[index - 1].score), 1e-8)
        current_score = float(ranked[index].score)
        score_drop = prev_score - current_score
        if score_drop / prev_score > float(drop_ratio):
            return max(min_core_results, index)

    fallback = max(min_core_results, len(ranked) // 2)
    return min(len(ranked), fallback)


def _extract_seed_evidence(
    retriever: DualPathRetriever,
    query_profile: _CompetitionProfile,
    results: Sequence[RetrievalResult],
    *,
    scan_top_k: int,
    max_tokens: int,
) -> List[_SeedEvidence]:
    score_map: Dict[Tuple[str, str], _SeedEvidence] = {}
    top_results = list(results)[: max(1, int(scan_top_k))]

    for rank, item in enumerate(top_results, start=1):
        profile = _build_candidate_profile(retriever, item, max_tokens=max_tokens)
        for entity in profile.entities:
            strength = "grounded" if entity in query_profile.entities else "incidental"
            key = (entity, strength)
            existing = score_map.get(key)
            if existing is None:
                score_map[key] = _SeedEvidence(
                    name=entity,
                    strength=strength,
                    support_count=1,
                    rank_hint=rank,
                )
            else:
                existing.support_count += 1
                existing.rank_hint = min(existing.rank_hint, rank)

    return sorted(
        score_map.values(),
        key=lambda item: (
            0 if item.strength == "grounded" else 1,
            -int(item.support_count),
            int(item.rank_hint),
            -len(item.name),
            item.name,
        ),
    )


def _grounded_seed_names(seed_evidence: Sequence[_SeedEvidence]) -> List[str]:
    return [item.name for item in seed_evidence if item.strength == "grounded"]


def _incidental_seed_names(seed_evidence: Sequence[_SeedEvidence]) -> List[str]:
    return [item.name for item in seed_evidence if item.strength == "incidental"]


def _need_for_graph(
    *,
    query_profile: _CompetitionProfile,
    core_profile: _CompetitionProfile,
    core_profiles: Sequence[_CompetitionProfile],
    grounded_seeds: Sequence[str],
    rag_confidence: float,
    cfg: PosteriorGraphConfig,
) -> Tuple[bool, str]:
    uncovered_query_entities = query_profile.entities - core_profile.entities
    if uncovered_query_entities:
        return True, "uncovered_query_entities"

    if len(grounded_seeds) >= 2:
        bridge_targets = set(list(grounded_seeds)[:2])
        same_core_hit = any(len(profile.entities & bridge_targets) >= 2 for profile in core_profiles)
        if not same_core_hit:
            return True, "grounded_bridge_gap"

    token_coverage = _safe_ratio(len(core_profile.tokens & query_profile.tokens), len(query_profile.tokens))
    if grounded_seeds and token_coverage < float(cfg.min_query_token_coverage):
        return True, "low_core_query_coverage"

    if grounded_seeds and float(rag_confidence) < float(cfg.grounded_confidence_threshold):
        return True, "low_confidence_grounded"

    return False, "core_already_sufficient"


def _passes_incidental_high_bar(
    retriever: DualPathRetriever,
    candidate: RetrievalResult,
    *,
    query_profile: _CompetitionProfile,
    core_profile: _CompetitionProfile,
    cfg: PosteriorGraphConfig,
) -> bool:
    candidate_profile = _build_candidate_profile(
        retriever,
        candidate,
        max_tokens=cfg.max_candidate_tokens,
    )
    uncovered_query_entities = query_profile.entities - core_profile.entities
    if candidate_profile.entities & uncovered_query_entities:
        return True

    query_relevance = _compute_query_relevance(candidate_profile, query_profile)
    specificity = _compute_specificity(candidate_profile, candidate)
    overlap = _core_overlap(candidate_profile, core_profile)
    gap_fill = _compute_gap_fill(candidate_profile, query_profile, core_profile)

    return bool(
        query_relevance >= float(cfg.incidental_query_relevance_threshold)
        and specificity >= float(cfg.incidental_specificity_threshold)
        and overlap <= float(cfg.incidental_core_overlap_threshold)
        and gap_fill > 0.0
    )


def _linked_core_paragraph_hashes(
    retriever: DualPathRetriever,
    relation_hash: str,
) -> Set[str]:
    rows = retriever.metadata_store.query(
        """
        SELECT paragraph_hash FROM paragraph_relations
        WHERE relation_hash = ?
        """,
        (relation_hash,),
    )
    return {
        str(row.get("paragraph_hash", "") or "").strip()
        for row in rows
        if str(row.get("paragraph_hash", "") or "").strip()
    }


def _build_graph_results_from_seeds(
    retriever: DualPathRetriever,
    *,
    seed_entities: Sequence[str],
    temporal: Any,
    alpha: float,
) -> List[RetrievalResult]:
    from .dual_path import RetrievalResult

    service = getattr(retriever, "_graph_relation_recall", None)
    if service is None:
        return []

    payloads = service.recall(seed_entities=seed_entities)
    if not payloads:
        return []

    graph_results: List[RetrievalResult] = []
    for payload in payloads:
        meta = payload.to_payload()
        graph_results.append(
            RetrievalResult(
                hash_value=str(meta["hash"]),
                content=str(meta["content"]),
                score=0.0,
                result_type="relation",
                source="posterior_graph_recall",
                metadata={
                    "subject": meta["subject"],
                    "predicate": meta["predicate"],
                    "object": meta["object"],
                    "confidence": float(meta["confidence"]),
                    "graph_seed_entities": list(meta["graph_seed_entities"]),
                    "graph_hops": int(meta["graph_hops"]),
                    "graph_candidate_type": str(meta["graph_candidate_type"]),
                    "supporting_paragraph_count": int(meta["supporting_paragraph_count"]),
                },
            )
        )

    graph_results = retriever._apply_temporal_filter_to_relations(graph_results, temporal)
    graph_results = retriever._merge_relation_results_graph_enhanced([], [], graph_results)
    relation_weight = max(0.0, 1.0 - float(alpha))
    for item in graph_results:
        item.score = float(item.score) * relation_weight
        item.source = "posterior_graph_competition"
    return graph_results


def _competition_merge(
    retriever: DualPathRetriever,
    *,
    query: str,
    base_results: Sequence[RetrievalResult],
    graph_results: Sequence[RetrievalResult],
    top_k: int,
    cfg: PosteriorGraphConfig,
) -> List[RetrievalResult]:
    ranked = list(base_results)[: max(1, int(top_k))]
    if not ranked or not graph_results:
        return ranked

    cliff = find_score_cliff(
        ranked,
        drop_ratio=cfg.drop_ratio,
        min_core_results=cfg.min_core_results,
    )
    core_results = ranked[:cliff]
    replaceable_slots = min(
        max(0, int(top_k) - len(core_results)),
        int(cfg.max_graph_slots),
    )
    if replaceable_slots <= 0:
        return ranked[:top_k]

    core_paragraph_hashes = {item.hash_value for item in core_results if item.result_type == "paragraph"}
    selected_hashes = {item.hash_value for item in core_results}
    filtered_graph_results: List[RetrievalResult] = []
    for item in graph_results:
        if item.hash_value in selected_hashes:
            continue
        linked_hashes = _linked_core_paragraph_hashes(retriever, item.hash_value)
        if core_paragraph_hashes & linked_hashes:
            continue
        filtered_graph_results.append(item)

    tail_candidates: List[RetrievalResult] = []
    for item in ranked[cliff:top_k]:
        if item.hash_value not in selected_hashes:
            tail_candidates.append(item)
    tail_candidates.extend(filtered_graph_results)

    query_profile = _build_query_profile(
        retriever,
        query,
        max_tokens=cfg.max_candidate_tokens,
    )
    core_profile = _build_core_profile(
        retriever,
        core_results,
        max_tokens=cfg.max_candidate_tokens,
    )

    scored_candidates: List[Tuple[RetrievalResult, float]] = []
    for item in tail_candidates:
        competition_score, breakdown = _compute_competition_score(
            retriever,
            item,
            query_profile=query_profile,
            core_profile=core_profile,
            cfg=cfg,
        )
        metadata = dict(item.metadata) if isinstance(item.metadata, dict) else {}
        metadata["posterior_original_score"] = round(float(item.score), 4)
        metadata["posterior_competition_breakdown"] = breakdown
        metadata["posterior_competition_source"] = "posterior_graph_gate"
        item.metadata = metadata
        scored_candidates.append((item, competition_score))

    scored_candidates.sort(
        key=lambda payload: (
            float(payload[1]),
            1 if payload[0].result_type == "relation" else 0,
        ),
        reverse=True,
    )

    tail_winners: List[RetrievalResult] = []
    seen_hashes = set(selected_hashes)
    for item, _ in scored_candidates:
        if item.hash_value in seen_hashes:
            continue
        tail_winners.append(item)
        seen_hashes.add(item.hash_value)
        if len(tail_winners) >= replaceable_slots:
            break

    return (core_results + tail_winners)[:top_k]


def apply_posterior_graph_gate(
    retriever: DualPathRetriever,
    *,
    query: str,
    base_results: Sequence[RetrievalResult],
    top_k: int,
    temporal: Any,
    relation_intent: Dict[str, Any],
) -> List[RetrievalResult]:
    cfg = getattr(retriever.config, "posterior_graph", None)
    if not isinstance(cfg, PosteriorGraphConfig) or not cfg.enabled:
        return list(base_results)[:top_k]
    if not base_results:
        retriever._posterior_graph_gate_last_decision = {
            "scheme": "posterior_graph_gate",
            "query": str(query or ""),
            "enabled": False,
            "bucket": "posterior_gate_empty",
        }
        return []

    top_k_int = max(1, int(top_k))
    alpha_override = relation_intent.get("alpha_override") if isinstance(relation_intent, dict) else None
    alpha = float(alpha_override) if alpha_override is not None else float(retriever.config.alpha)
    rag_confidence = _top_score(list(base_results)[:top_k_int])

    query_profile = _build_query_profile(
        retriever,
        query,
        max_tokens=cfg.max_candidate_tokens,
    )
    seed_evidence = _extract_seed_evidence(
        retriever,
        query_profile,
        base_results,
        scan_top_k=cfg.gate_scan_top_k,
        max_tokens=cfg.max_candidate_tokens,
    )
    grounded_seeds = _grounded_seed_names(seed_evidence)[:2]
    incidental_seeds = _incidental_seed_names(seed_evidence)[:2]

    core_results = list(base_results)[
        : find_score_cliff(
            list(base_results)[:top_k_int],
            drop_ratio=cfg.drop_ratio,
            min_core_results=cfg.min_core_results,
        )
    ]
    core_profile = _build_core_profile(
        retriever,
        core_results,
        max_tokens=cfg.max_candidate_tokens,
    )
    core_profiles = [
        _build_candidate_profile(retriever, item, max_tokens=cfg.max_candidate_tokens) for item in core_results
    ]
    need_for_graph, need_reason = _need_for_graph(
        query_profile=query_profile,
        core_profile=core_profile,
        core_profiles=core_profiles,
        grounded_seeds=grounded_seeds,
        rag_confidence=rag_confidence,
        cfg=cfg,
    )

    seed_type = "none"
    seed_names: List[str] = []
    if grounded_seeds and need_for_graph:
        seed_type = "grounded"
        seed_names = grounded_seeds
    elif not grounded_seeds and incidental_seeds and rag_confidence < float(cfg.incidental_confidence_threshold):
        seed_type = "incidental"
        seed_names = incidental_seeds

    if not seed_names:
        retriever._posterior_graph_gate_last_decision = {
            "scheme": "posterior_graph_gate",
            "query": str(query or ""),
            "enabled": False,
            "bucket": "posterior_gate_none",
            "grounded_seeds": list(grounded_seeds),
            "incidental_seeds": list(incidental_seeds),
            "selected_seed_type": seed_type,
            "need_for_graph": bool(need_for_graph),
            "need_reason": str(need_reason),
            "rag_confidence": round(float(rag_confidence), 4),
        }
        return list(base_results)[:top_k_int]

    graph_results = _build_graph_results_from_seeds(
        retriever,
        seed_entities=seed_names,
        temporal=temporal,
        alpha=alpha,
    )
    raw_graph_count = len(graph_results)
    if seed_type == "incidental":
        graph_results = [
            item
            for item in graph_results
            if _passes_incidental_high_bar(
                retriever,
                item,
                query_profile=query_profile,
                core_profile=core_profile,
                cfg=cfg,
            )
        ]

    if not graph_results:
        retriever._posterior_graph_gate_last_decision = {
            "scheme": "posterior_graph_gate",
            "query": str(query or ""),
            "enabled": False,
            "bucket": "posterior_gate_graph_filtered",
            "grounded_seeds": list(grounded_seeds),
            "incidental_seeds": list(incidental_seeds),
            "selected_seed_type": seed_type,
            "need_for_graph": bool(need_for_graph),
            "need_reason": str(need_reason),
            "rag_confidence": round(float(rag_confidence), 4),
            "graph_result_count": int(raw_graph_count),
        }
        return list(base_results)[:top_k_int]

    final_results = _competition_merge(
        retriever,
        query=query,
        base_results=base_results,
        graph_results=graph_results,
        top_k=top_k_int,
        cfg=cfg,
    )
    selected_hashes = {item.hash_value for item in final_results}
    graph_selected = any(item.hash_value in selected_hashes for item in graph_results)
    retriever._posterior_graph_gate_last_decision = {
        "scheme": "posterior_graph_gate",
        "query": str(query or ""),
        "enabled": bool(graph_selected),
        "bucket": "posterior_gate_enabled" if graph_selected else "posterior_gate_tail_rejected",
        "grounded_seeds": list(grounded_seeds),
        "incidental_seeds": list(incidental_seeds),
        "selected_seed_type": seed_type,
        "need_for_graph": bool(need_for_graph),
        "need_reason": str(need_reason),
        "rag_confidence": round(float(rag_confidence), 4),
        "graph_result_count": int(raw_graph_count),
        "filtered_graph_count": max(0, raw_graph_count - len(graph_results)),
        "base_top_k_count": min(len(base_results), top_k_int),
    }
    return final_results[:top_k_int]
