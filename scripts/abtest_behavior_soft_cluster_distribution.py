from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections import defaultdict
from math import exp, log
from pathlib import Path
from typing import Any

import json
import sqlite3


DEFAULT_SOURCE_REPORT = "data/analysis/behavior_scene_enrichment_abtest.json"
DEFAULT_CONTINUITY_REPORT = "data/analysis/behavior_scene_continuity_reuse.json"
DEFAULT_DB_PATH = "data/MaiBot.db"
DEFAULT_OUTPUT = "data/analysis/behavior_soft_cluster_distribution_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_soft_cluster_distribution_abtest.json"

VARIANT_ORDER = [
    "previous15_current_first15",
    "current30",
    "current_last15_next15",
]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _distribution_from_items(items: list[dict[str, Any]]) -> dict[str, float]:
    distribution: dict[str, float] = {}
    for item in items:
        tag = str(item.get("tag") or "").strip()
        if not tag:
            continue
        probability = float(item.get("probability") or 0.0)
        if probability > 0:
            distribution[tag] = distribution.get(tag, 0.0) + probability
    return _normalize_distribution(distribution)


def _normalize_distribution(distribution: dict[str, float]) -> dict[str, float]:
    total = sum(max(probability, 0.0) for probability in distribution.values())
    if total <= 0:
        return {}
    return {
        tag: max(probability, 0.0) / total
        for tag, probability in distribution.items()
        if probability > 0
    }


def _overlap_distribution(left: dict[str, float], right: dict[str, float]) -> float:
    return sum(min(left[tag], right[tag]) for tag in set(left) & set(right))


def _weighted_overlap_distribution(
    left: dict[str, float],
    right: dict[str, float],
    *,
    idf_by_tag: dict[str, float],
) -> float:
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    numerator = sum(min(left[tag], right[tag]) * idf_by_tag.get(tag, 1.0) for tag in shared)
    denominator = sum(left[tag] * idf_by_tag.get(tag, 1.0) for tag in left)
    if denominator <= 0:
        return 0.0
    return min(numerator / denominator, 1.0)


def _build_tag_adjacency(clusters: list[dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for cluster in clusters:
        tag_names = sorted(cluster["distribution"])
        if len(tag_names) < 2:
            for tag_name in tag_names:
                adjacency.setdefault(tag_name, set())
            continue
        for left_index, left_tag in enumerate(tag_names):
            adjacency.setdefault(left_tag, set())
            for right_tag in tag_names[left_index + 1 :]:
                adjacency.setdefault(right_tag, set())
                adjacency[left_tag].add(right_tag)
                adjacency[right_tag].add(left_tag)
    return adjacency


def _expand_distribution(
    distribution: dict[str, float],
    *,
    adjacency: dict[str, set[str]],
    max_depth: int,
    decay: float,
) -> dict[str, float]:
    if not distribution or max_depth <= 0:
        return distribution

    expanded: dict[str, float] = dict(distribution)
    visited_tags = set(distribution)
    frontier_weights = dict(distribution)
    for _depth in range(1, max_depth + 1):
        next_frontier_weights: dict[str, float] = {}
        for source_tag, source_probability in frontier_weights.items():
            connected_tags = adjacency.get(source_tag, set()) - visited_tags
            if not connected_tags:
                continue
            spread_probability = source_probability * decay / float(len(connected_tags))
            for target_tag in connected_tags:
                next_frontier_weights[target_tag] = next_frontier_weights.get(target_tag, 0.0) + spread_probability
        if not next_frontier_weights:
            break
        for tag, probability in next_frontier_weights.items():
            expanded[tag] = expanded.get(tag, 0.0) + probability
        visited_tags.update(next_frontier_weights)
        frontier_weights = next_frontier_weights
    return _normalize_distribution(expanded)


def _load_tag_lookup(connection: sqlite3.Connection, cluster_keys: set[str]) -> dict[tuple[str, str], str]:
    if not cluster_keys:
        return {}
    placeholders = ",".join("?" for _ in cluster_keys)
    rows = connection.execute(
        f"""
        select tag_kind, cluster_key, tag, source_count
        from behavior_scene_tag_clusters
        where cluster_key in ({placeholders})
        order by source_count desc, tag
        """,
        tuple(cluster_keys),
    ).fetchall()
    labels: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        key = (str(row["tag_kind"]), str(row["cluster_key"]))
        tag = str(row["tag"])
        if tag not in labels[key]:
            labels[key].append(tag)
    return {key: "/".join(value[:3]) for key, value in labels.items()}


def _tag_label(tag: str, labels: dict[tuple[str, str], str]) -> str:
    if ":" not in tag:
        return tag
    tag_kind, cluster_key = tag.split(":", 1)
    return labels.get((tag_kind, cluster_key), cluster_key)


def _load_scene_clusters(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    include_global: bool,
    all_sessions: bool,
) -> list[dict[str, Any]]:
    if all_sessions:
        rows = connection.execute(
            """
            select id, session_id, source_count, tag_distribution
            from behavior_scene_clusters
            """
        ).fetchall()
    elif include_global:
        rows = connection.execute(
            """
            select id, session_id, source_count, tag_distribution
            from behavior_scene_clusters
            where session_id = ? or session_id is null
            """,
            (session_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            select id, session_id, source_count, tag_distribution
            from behavior_scene_clusters
            where session_id = ?
            """,
            (session_id,),
        ).fetchall()

    clusters: list[dict[str, Any]] = []
    for row in rows:
        raw_distribution = json.loads(row["tag_distribution"] or "[]")
        distribution = _distribution_from_items(raw_distribution)
        if not distribution:
            continue
        clusters.append(
            {
                "cluster_id": int(row["id"]),
                "session_id": row["session_id"],
                "source_count": int(row["source_count"] or 0),
                "distribution": distribution,
            }
        )
    return clusters


def _build_idf_by_tag(clusters: list[dict[str, Any]]) -> dict[str, float]:
    df_by_tag: dict[str, int] = defaultdict(int)
    for cluster in clusters:
        for tag in cluster["distribution"]:
            df_by_tag[tag] += 1
    cluster_count = max(len(clusters), 1)
    return {
        tag: 1.0 + log((float(cluster_count) + 1.0) / (float(count) + 1.0))
        for tag, count in df_by_tag.items()
    }


def _build_frequency_weight_by_tag(clusters: list[dict[str, Any]]) -> dict[str, float]:
    df_by_tag: dict[str, int] = defaultdict(int)
    for cluster in clusters:
        for tag in cluster["distribution"]:
            df_by_tag[tag] += 1

    cluster_count = max(len(clusters), 1)
    raw_weights: dict[str, float] = {}
    for tag, count in df_by_tag.items():
        df_ratio = float(count) / float(cluster_count)
        idf = 1.0 + log((float(cluster_count) + 1.0) / (float(count) + 1.0))
        idf_soft = 1.0 + log(idf)
        rare_reliability = 1.0 - exp(-float(count) / 2.0)
        common_gate = 1.0 / (1.0 + (df_ratio / 0.08) ** 1.8)
        raw_weights[tag] = max(0.05, idf_soft * rare_reliability * common_gate)

    if not raw_weights:
        return {}
    average_weight = sum(raw_weights.values()) / float(len(raw_weights))
    if average_weight <= 0:
        return raw_weights
    return {tag: weight / average_weight for tag, weight in raw_weights.items()}


def _score_clusters(
    scene_distributions: list[dict[str, float]],
    clusters: list[dict[str, Any]],
    *,
    weight_by_tag: dict[str, float],
) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    for cluster in clusters:
        overlaps = [
            _weighted_overlap_distribution(distribution, cluster["distribution"], idf_by_tag=weight_by_tag)
            if weight_by_tag
            else _overlap_distribution(distribution, cluster["distribution"])
            for distribution in scene_distributions
        ]
        overlaps = [overlap for overlap in overlaps if overlap > 0]
        if not overlaps:
            continue
        max_overlap = max(overlaps)
        avg_overlap = sum(overlaps) / float(len(scene_distributions))
        hit_count = sum(1 for overlap in overlaps if overlap >= 0.02)
        score = 0.6 * max_overlap + 0.3 * avg_overlap + 0.1 * (float(hit_count) / float(len(scene_distributions)))
        scores.append(
            {
                "cluster_id": cluster["cluster_id"],
                "session_id": cluster["session_id"],
                "source_count": cluster["source_count"],
                "distribution": cluster["distribution"],
                "score": score,
                "max_overlap": max_overlap,
                "avg_overlap": avg_overlap,
                "hit_count": hit_count,
            }
        )
    scores.sort(key=lambda item: item["score"], reverse=True)
    return scores


def _soft_cluster_distribution(
    cluster_scores: list[dict[str, Any]],
    *,
    top_k: int,
    temperature: float,
    full_mass_score: float,
    min_score: float,
) -> dict[str, Any]:
    selected = [item for item in cluster_scores if item["score"] >= min_score][:top_k]
    if not selected:
        return {
            "known_mass": 0.0,
            "unknown_probability": 1.0,
            "top_probability": 0.0,
            "entropy": 0.0,
            "clusters": [],
        }

    max_score = max(float(item["score"]) for item in selected)
    known_mass = min(1.0, max_score / max(full_mass_score, 1e-6))
    logits = [float(item["score"]) / max(temperature, 1e-6) for item in selected]
    max_logit = max(logits)
    exp_values = [exp(logit - max_logit) for logit in logits]
    exp_total = sum(exp_values)
    clusters: list[dict[str, Any]] = []
    entropy = 0.0
    for item, exp_value in zip(selected, exp_values, strict=True):
        probability = known_mass * exp_value / exp_total
        if probability > 0:
            entropy -= probability * log(probability)
        clusters.append(
            {
                **item,
                "probability": probability,
            }
        )

    return {
        "known_mass": known_mass,
        "unknown_probability": 1.0 - known_mass,
        "top_probability": max(item["probability"] for item in clusters),
        "entropy": entropy,
        "clusters": clusters,
    }


def _score_behavior_paths(
    connection: sqlite3.Connection,
    cluster_distribution: dict[str, Any],
) -> list[dict[str, Any]]:
    cluster_probabilities = {
        int(item["cluster_id"]): float(item["probability"])
        for item in cluster_distribution["clusters"]
        if float(item["probability"]) > 0
    }
    if not cluster_probabilities:
        return []
    placeholders = ",".join("?" for _ in cluster_probabilities)
    rows = connection.execute(
        f"""
        select
            p.id,
            p.scene_cluster_id,
            p.count,
            p.score,
            p.activation_count,
            p.success_count,
            p.failure_count,
            a.action,
            o.outcome
        from behavior_experience_paths p
        left join behavior_actions a on a.id = p.action_id
        left join behavior_outcomes o on o.id = p.outcome_id
        where p.enabled = 1 and p.scene_cluster_id in ({placeholders})
        """,
        tuple(cluster_probabilities),
    ).fetchall()

    scored_paths: list[dict[str, Any]] = []
    for row in rows:
        cluster_probability = cluster_probabilities.get(int(row["scene_cluster_id"]), 0.0)
        if cluster_probability <= 0:
            continue
        history_bonus = 1.0 + min(float(row["count"] or 0), 20.0) * 0.02
        feedback_bonus = 1.0 + min(float(row["success_count"] or 0), 10.0) * 0.04
        penalty = 1.0 + min(float(row["failure_count"] or 0), 10.0) * 0.04
        path_score = cluster_probability * history_bonus * feedback_bonus / penalty
        scored_paths.append(
            {
                "path_id": int(row["id"]),
                "scene_cluster_id": int(row["scene_cluster_id"]),
                "score": path_score,
                "cluster_probability": cluster_probability,
                "count": int(row["count"] or 0),
                "path_score": float(row["score"] or 0.0),
                "activation_count": int(row["activation_count"] or 0),
                "success_count": int(row["success_count"] or 0),
                "failure_count": int(row["failure_count"] or 0),
                "action": row["action"] or "",
                "outcome": row["outcome"] or "",
            }
        )
    scored_paths.sort(key=lambda item: item["score"], reverse=True)
    return scored_paths


def _sample_run_distributions(sample: dict[str, Any]) -> dict[str, dict[str, float]]:
    distributions: dict[str, dict[str, float]] = {}
    for run in sample["runs"]:
        distributions[run["variant"]] = _distribution_from_items(run["distribution"])
    return distributions


def _usable_variants_by_sample(continuity_report: dict[str, Any]) -> dict[int, list[str]]:
    usable_by_sample: dict[int, list[str]] = {}
    for sample in continuity_report.get("samples", []):
        judgement = sample.get("judgement") or {}
        usable = [
            str(variant)
            for variant in judgement.get("usable_variants", [])
            if str(variant) in set(VARIANT_ORDER)
        ]
        if usable:
            usable_by_sample[int(sample["index"])] = usable
    return usable_by_sample


def _format_cluster(
    connection: sqlite3.Connection,
    cluster: dict[str, Any],
    *,
    max_tags: int = 6,
) -> dict[str, Any]:
    cluster_keys = {tag.split(":", 1)[1] for tag in cluster["distribution"] if ":" in tag}
    labels = _load_tag_lookup(connection, cluster_keys)
    tags = [
        {
            "tag": tag,
            "label": _tag_label(tag, labels),
            "probability": round(probability, 4),
        }
        for tag, probability in sorted(cluster["distribution"].items(), key=lambda item: item[1], reverse=True)[:max_tags]
    ]
    return {
        "cluster_id": cluster["cluster_id"],
        "probability": round(float(cluster.get("probability", 0.0)), 4),
        "score": round(float(cluster["score"]), 4),
        "max_overlap": round(float(cluster["max_overlap"]), 4),
        "avg_overlap": round(float(cluster["avg_overlap"]), 4),
        "hit_count": int(cluster["hit_count"]),
        "source_count": int(cluster["source_count"]),
        "tags": tags,
    }


def _evaluate_sample(
    connection: sqlite3.Connection,
    sample: dict[str, Any],
    *,
    usable_variants: list[str],
    args: Namespace,
) -> dict[str, Any]:
    run_distributions = _sample_run_distributions(sample)
    selected_distributions = [
        run_distributions[variant]
        for variant in usable_variants
        if variant in run_distributions and run_distributions[variant]
    ]
    if not selected_distributions:
        selected_distributions = [run_distributions["current30"]]

    clusters = _load_scene_clusters(
        connection,
        session_id=sample["session_id"],
        include_global=args.include_global,
        all_sessions=args.all_sessions,
    )
    adjacency = _build_tag_adjacency(clusters)
    direct_tag_count = len(set().union(*(distribution.keys() for distribution in selected_distributions)))
    if args.spread_depth > 0:
        selected_distributions = [
            _expand_distribution(
                distribution,
                adjacency=adjacency,
                max_depth=args.spread_depth,
                decay=args.spread_decay,
            )
            for distribution in selected_distributions
        ]
    expanded_tag_count = len(set().union(*(distribution.keys() for distribution in selected_distributions)))
    weight_by_tag: dict[str, float] = {}
    if args.weight_mode == "idf":
        weight_by_tag = _build_idf_by_tag(clusters)
    elif args.weight_mode == "frequency":
        weight_by_tag = _build_frequency_weight_by_tag(clusters)
    cluster_scores = _score_clusters(
        selected_distributions,
        clusters,
        weight_by_tag=weight_by_tag,
    )
    cluster_distribution = _soft_cluster_distribution(
        cluster_scores,
        top_k=args.top_k,
        temperature=args.temperature,
        full_mass_score=args.full_mass_score,
        min_score=args.min_score,
    )
    behavior_paths = _score_behavior_paths(connection, cluster_distribution)

    return {
        "index": sample["index"],
        "session_id": sample["session_id"],
        "chat_name": sample["chat_name"],
        "usable_variants": usable_variants,
        "direct_tag_count": direct_tag_count,
        "expanded_tag_count": expanded_tag_count,
        "known_mass": round(cluster_distribution["known_mass"], 4),
        "unknown_probability": round(cluster_distribution["unknown_probability"], 4),
        "top_probability": round(cluster_distribution["top_probability"], 4),
        "entropy": round(cluster_distribution["entropy"], 4),
        "candidate_count": len(behavior_paths),
        "top_path_score": round(float(behavior_paths[0]["score"]), 4) if behavior_paths else 0.0,
        "top_clusters": [
            _format_cluster(connection, cluster, max_tags=6)
            for cluster in cluster_distribution["clusters"][: args.report_top_k]
        ],
        "top_paths": [
            {
                **path,
                "score": round(float(path["score"]), 4),
                "cluster_probability": round(float(path["cluster_probability"]), 4),
                "action": path["action"][:180],
                "outcome": path["outcome"][:180],
            }
            for path in behavior_paths[: args.report_top_k]
        ],
    }


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sample_count": len(samples),
        "avg_direct_tag_count": _avg([sample["direct_tag_count"] for sample in samples]),
        "avg_expanded_tag_count": _avg([sample["expanded_tag_count"] for sample in samples]),
        "avg_known_mass": _avg([sample["known_mass"] for sample in samples]),
        "avg_unknown_probability": _avg([sample["unknown_probability"] for sample in samples]),
        "avg_top_probability": _avg([sample["top_probability"] for sample in samples]),
        "avg_entropy": _avg([sample["entropy"] for sample in samples]),
        "avg_candidate_count": _avg([sample["candidate_count"] for sample in samples]),
        "avg_top_path_score": _avg([sample["top_path_score"] for sample in samples]),
        "non_empty_candidate_count": sum(1 for sample in samples if sample["candidate_count"] > 0),
    }


def _avg(values: list[float | int]) -> float:
    if not values:
        return 0.0
    return round(sum(float(value) for value in values) / float(len(values)), 4)


def _write_markdown(report: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# 行为场景簇概率分布检索 AB 测试")
    lines.append("")
    lines.append(f"- source_report：`{report['source_report']}`")
    lines.append(f"- continuity_report：`{report['continuity_report']}`")
    lines.append(f"- top_k：{report['top_k']}")
    lines.append(f"- temperature：{report['temperature']}")
    lines.append(f"- full_mass_score：{report['full_mass_score']}")
    lines.append(f"- min_score：{report['min_score']}")
    lines.append(f"- weight_mode：{report['weight_mode']}")
    lines.append(f"- spread_depth：{report['spread_depth']}")
    lines.append(f"- spread_decay：{report['spread_decay']}")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"`{json.dumps(report['summary'], ensure_ascii=False)}`")
    for sample in report["samples"]:
        lines.append("")
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- usable_variants：{sample['usable_variants']}")
        lines.append(f"- tags：direct={sample['direct_tag_count']} expanded={sample['expanded_tag_count']}")
        lines.append(
            f"- known_mass={sample['known_mass']} unknown={sample['unknown_probability']} "
            f"top_prob={sample['top_probability']} entropy={sample['entropy']}"
        )
        lines.append(f"- candidate_count={sample['candidate_count']} top_path_score={sample['top_path_score']}")
        lines.append("")
        lines.append("### Top 场景簇")
        for cluster in sample["top_clusters"]:
            tag_text = "；".join(f"{tag['label']}:{tag['probability']}" for tag in cluster["tags"])
            lines.append(
                f"- #{cluster['cluster_id']} p={cluster['probability']} score={cluster['score']} "
                f"hit={cluster['hit_count']} src={cluster['source_count']}｜{tag_text}"
            )
        if sample["top_paths"]:
            lines.append("")
            lines.append("### Top 行为路径")
            for path in sample["top_paths"]:
                lines.append(
                    f"- path#{path['path_id']} cluster=#{path['scene_cluster_id']} "
                    f"p={path['cluster_probability']} score={path['score']}"
                )
                lines.append(f"  - action：{path['action']}")
                lines.append(f"  - outcome：{path['outcome']}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="复用已有场景画像，测试当前场景对所有场景簇的稀疏概率分布检索。")
    parser.add_argument("--source-report", default=DEFAULT_SOURCE_REPORT)
    parser.add_argument("--continuity-report", default=DEFAULT_CONTINUITY_REPORT)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--report-top-k", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.12)
    parser.add_argument("--full-mass-score", type=float, default=0.42)
    parser.add_argument("--min-score", type=float, default=0.05)
    parser.add_argument("--spread-depth", type=int, default=0)
    parser.add_argument("--spread-decay", type=float, default=0.5)
    parser.add_argument("--include-global", action="store_true")
    parser.add_argument("--all-sessions", action="store_true")
    parser.add_argument("--idf", action="store_true")
    parser.add_argument(
        "--weight-mode",
        choices=["plain", "idf", "frequency"],
        default="plain",
        help="plain 为原始 overlap；idf 为纯 IDF；frequency 为纯频率自适应权重。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.idf:
        args.weight_mode = "idf"
    source_report_path = Path(args.source_report)
    continuity_report_path = Path(args.continuity_report)
    source_report = _load_json(source_report_path)
    continuity_report = _load_json(continuity_report_path)
    usable_by_sample = _usable_variants_by_sample(continuity_report)

    connection = sqlite3.connect(args.db_path)
    connection.row_factory = sqlite3.Row
    samples: list[dict[str, Any]] = []
    for sample in source_report.get("samples", []):
        usable_variants = usable_by_sample.get(int(sample["index"]), ["current30"])
        samples.append(_evaluate_sample(connection, sample, usable_variants=usable_variants, args=args))

    report = {
        "source_report": str(source_report_path),
        "continuity_report": str(continuity_report_path),
        "db_path": args.db_path,
        "top_k": args.top_k,
        "temperature": args.temperature,
        "full_mass_score": args.full_mass_score,
        "min_score": args.min_score,
        "weight_mode": args.weight_mode,
        "spread_depth": args.spread_depth,
        "spread_decay": args.spread_decay,
        "include_global": bool(args.include_global),
        "all_sessions": bool(args.all_sessions),
        "summary": _summarize(samples),
        "samples": samples,
    }

    output_path = Path(args.output)
    json_output_path = Path(args.json_output)
    _write_markdown(report, output_path)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Markdown report: {output_path}")
    print(f"JSON report: {json_output_path}")


if __name__ == "__main__":
    main()
