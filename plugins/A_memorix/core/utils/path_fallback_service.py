"""Shared path-fallback helpers for search post-processing."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..retrieval.dual_path import RetrievalResult


def extract_entities(query: str, graph_store: Any) -> List[str]:
    """Extract up to two graph nodes from a query using n-gram matching."""
    if not graph_store:
        return []

    text = str(query or "").strip()
    if not text:
        return []

    # Keep the heuristic aligned with previous legacy behavior.
    tokens = (
        text.replace("?", " ")
        .replace("!", " ")
        .replace(".", " ")
        .split()
    )
    if not tokens:
        return []

    found_entities = set()
    skip_indices = set()
    max_n = min(4, len(tokens))

    for size in range(max_n, 0, -1):
        for i in range(len(tokens) - size + 1):
            if any(idx in skip_indices for idx in range(i, i + size)):
                continue
            span = " ".join(tokens[i : i + size])
            matched_node = graph_store.find_node(span, ignore_case=True)
            if not matched_node:
                continue
            found_entities.add(matched_node)
            for idx in range(i, i + size):
                skip_indices.add(idx)

    return list(found_entities)


def find_paths_between_entities(
    start_node: str,
    end_node: str,
    graph_store: Any,
    metadata_store: Any,
    *,
    max_depth: int = 3,
    max_paths: int = 5,
) -> List[Dict[str, Any]]:
    """Find and enrich indirect paths between two nodes."""
    if not graph_store or not metadata_store:
        return []

    try:
        paths = graph_store.find_paths(
            start_node,
            end_node,
            max_depth=max_depth,
            max_paths=max_paths,
        )
    except Exception:
        return []

    if not paths:
        return []

    edge_cache: Dict[Tuple[str, str], Tuple[str, str]] = {}
    formatted_paths: List[Dict[str, Any]] = []

    for path_nodes in paths:
        if not isinstance(path_nodes, Sequence) or len(path_nodes) < 2:
            continue

        path_desc: List[str] = []
        for i in range(len(path_nodes) - 1):
            u = str(path_nodes[i])
            v = str(path_nodes[i + 1])

            cache_key = tuple(sorted((u, v)))
            if cache_key in edge_cache:
                pred, direction = edge_cache[cache_key]
            else:
                pred = "related"
                direction = "->"
                rels = metadata_store.get_relations(subject=u, object=v)
                if not rels:
                    rels = metadata_store.get_relations(subject=v, object=u)
                    direction = "<-"
                if rels:
                    best_rel = max(rels, key=lambda x: x.get("confidence", 1.0))
                    pred = str(best_rel.get("predicate", "related") or "related")
                edge_cache[cache_key] = (pred, direction)

            step_str = f"-[{pred}]->" if direction == "->" else f"<-[{pred}]-"
            path_desc.append(step_str)

        full_path_str = str(path_nodes[0])
        for i, step in enumerate(path_desc):
            full_path_str += f" {step} {path_nodes[i + 1]}"

        formatted_paths.append(
            {
                "nodes": list(path_nodes),
                "description": full_path_str,
            }
        )

    return formatted_paths


def find_paths_from_query(
    query: str,
    graph_store: Any,
    metadata_store: Any,
    *,
    max_depth: int = 3,
    max_paths: int = 5,
) -> List[Dict[str, Any]]:
    """Extract entities from query and resolve indirect paths."""
    entities = extract_entities(query, graph_store)
    if len(entities) != 2:
        return []
    return find_paths_between_entities(
        entities[0],
        entities[1],
        graph_store,
        metadata_store,
        max_depth=max_depth,
        max_paths=max_paths,
    )


def to_retrieval_results(paths: Sequence[Dict[str, Any]]) -> List[RetrievalResult]:
    """Convert path results into retrieval results for the unified pipeline."""
    converted: List[RetrievalResult] = []
    for item in paths:
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        hash_seed = description.encode("utf-8")
        path_hash = f"path_{hashlib.sha1(hash_seed).hexdigest()}"
        converted.append(
            RetrievalResult(
                hash_value=path_hash,
                content=f"[Indirect Relation] {description}",
                score=0.95,
                result_type="relation",
                source="graph_path",
                metadata={
                    "source": "graph_path",
                    "is_indirect": True,
                    "nodes": list(item.get("nodes", [])),
                },
            )
        )
    return converted

