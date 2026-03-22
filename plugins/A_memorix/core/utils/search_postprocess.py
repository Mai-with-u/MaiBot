"""Post-processing helpers for unified search execution."""

from __future__ import annotations

from typing import Any, List, Tuple

from .path_fallback_service import find_paths_from_query, to_retrieval_results


def apply_safe_content_dedup(results: List[Any]) -> Tuple[List[Any], int]:
    """Deduplicate results by hash/content while preserving at least one entry."""
    if not results:
        return [], 0

    unique_results: List[Any] = []
    seen_hashes = set()
    seen_contents = set()

    for item in results:
        content = str(getattr(item, "content", "") or "").strip()
        if not content:
            continue

        hash_value = str(getattr(item, "hash_value", "") or "").strip() or str(hash(content))
        if hash_value in seen_hashes:
            continue

        is_dup = False
        for seen in seen_contents:
            if content in seen or seen in content:
                is_dup = True
                break
        if is_dup:
            continue

        seen_hashes.add(hash_value)
        seen_contents.add(content)
        unique_results.append(item)

    if not unique_results:
        unique_results.append(results[0])

    removed_count = max(0, len(results) - len(unique_results))
    return unique_results, removed_count


def maybe_apply_smart_path_fallback(
    *,
    query: str,
    results: List[Any],
    graph_store: Any,
    metadata_store: Any,
    enabled: bool,
    threshold: float,
    max_depth: int = 3,
    max_paths: int = 5,
) -> Tuple[List[Any], bool, int]:
    """Append indirect relation paths when semantic results are weak."""
    if not enabled or not str(query or "").strip():
        return results, False, 0
    if graph_store is None or metadata_store is None:
        return results, False, 0

    max_score = 0.0
    if results:
        try:
            max_score = float(getattr(results[0], "score", 0.0) or 0.0)
        except Exception:
            max_score = 0.0

    if max_score >= float(threshold):
        return results, False, 0

    paths = find_paths_from_query(
        query=query,
        graph_store=graph_store,
        metadata_store=metadata_store,
        max_depth=max_depth,
        max_paths=max_paths,
    )
    if not paths:
        return results, False, 0

    path_results = to_retrieval_results(paths)
    if not path_results:
        return results, False, 0

    merged = list(path_results) + list(results)
    return merged, True, len(path_results)

