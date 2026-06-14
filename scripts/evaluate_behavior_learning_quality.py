from argparse import ArgumentParser, Namespace
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

import json
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_DB_PATH = "data/MaiBot.db"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_learning_quality_report.json"
DEFAULT_MD_OUTPUT = "data/analysis/behavior_learning_quality_report.md"


def _load_json_list(raw_value: Any) -> list[Any]:
    if isinstance(raw_value, list):
        return raw_value
    if not isinstance(raw_value, str) or not raw_value.strip():
        return []
    try:
        parsed_value = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    return parsed_value if isinstance(parsed_value, list) else []


def _compact_text(text: str, *, max_length: int = 140) -> str:
    compacted_text = " ".join(str(text or "").split()).strip()
    if len(compacted_text) <= max_length:
        return compacted_text
    return compacted_text[:max_length].rstrip() + "..."


def _percent(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 4)


def _round_float(value: float) -> float:
    return round(value, 4)


def _path_to_sample(row: sqlite3.Row, *, reason: str = "") -> dict[str, Any]:
    sample = {
        "id": int(row["id"]),
        "session_id": str(row["session_id"] or ""),
        "chat_name": str(row["chat_name"] or ""),
        "scene_cluster_id": int(row["scene_cluster_id"]),
        "actor_type": str(row["actor_type"] or ""),
        "learning_type": str(row["learning_type"] or ""),
        "count": int(row["count"] or 0),
        "activation_count": int(row["activation_count"] or 0),
        "success_count": int(row["success_count"] or 0),
        "failure_count": int(row["failure_count"] or 0),
        "score": float(row["score"] or 0.0),
        "action": str(row["action"] or ""),
        "outcome": str(row["outcome"] or ""),
    }
    if reason:
        sample["reason"] = reason
    return sample


def _load_behavior_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        select p.id, p.session_id, p.scene_cluster_id, p.actor_type, p.learning_type,
               p.evidence_list, p.feedback_list,
               p.count, p.activation_count, p.success_count, p.failure_count, p.score, p.enabled,
               a.action, o.outcome, c.session_id as scene_session_id,
               c.source_count as scene_source_count, c.score as scene_score,
               c.tag_distribution,
               coalesce(cs.group_name, cs.user_nickname, cs.user_cardname, p.session_id) as chat_name
        from behavior_experience_paths p
        join behavior_actions a on a.id = p.action_id
        join behavior_outcomes o on o.id = p.outcome_id
        join behavior_scene_clusters c on c.id = p.scene_cluster_id
        left join chat_sessions cs on cs.session_id = p.session_id
        where p.enabled = 1
        order by p.id
        """
    ).fetchall()


def _load_scene_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        select c.id, c.session_id, c.source_count, c.score, c.tag_distribution,
               count(p.id) as path_count,
               coalesce(cs.group_name, cs.user_nickname, cs.user_cardname, c.session_id) as chat_name
        from behavior_scene_clusters c
        left join behavior_experience_paths p on p.scene_cluster_id = c.id and p.enabled = 1
        left join chat_sessions cs on cs.session_id = c.session_id
        group by c.id
        order by c.id
        """
    ).fetchall()


def _summarize_distribution(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"min": 0, "median": 0, "mean": 0, "p90": 0, "max": 0}
    sorted_values = sorted(values)
    p90_index = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * 0.9) - 1))
    return {
        "min": min(values),
        "median": median(values),
        "mean": _round_float(mean(values)),
        "p90": sorted_values[p90_index],
        "max": max(values),
    }


def _top_counter(counter: Counter[Any], *, limit: int) -> list[dict[str, Any]]:
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def _evaluate_quality(rows: list[sqlite3.Row], *, sample_limit: int) -> dict[str, Any]:
    path_samples: list[dict[str, Any]] = []
    reinforced_samples: list[dict[str, Any]] = []

    for row in rows:
        if len(path_samples) < sample_limit:
            path_samples.append(_path_to_sample(row))
        if (int(row["success_count"] or 0) > 0 or int(row["activation_count"] or 0) > 0) and len(
            reinforced_samples
        ) < sample_limit:
            reinforced_samples.append(_path_to_sample(row))

    total_count = len(rows)
    return {
        "keyword_quality_gate_enabled": False,
        "total_count": total_count,
        "passed_count": total_count,
        "blocked_count": 0,
        "blocked_ratio": 0.0,
        "reason_counts": {},
        "samples_by_reason": {},
        "path_samples": path_samples,
        "reinforced_samples": reinforced_samples,
    }


def _evaluate_chat_scope(rows: list[sqlite3.Row], *, sample_limit: int) -> dict[str, Any]:
    by_session: dict[str, list[sqlite3.Row]] = defaultdict(list)
    exact_keys: dict[tuple[str, str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
    same_action_keys: dict[tuple[str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
    same_outcome_keys: dict[tuple[str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
    mismatch_rows: list[sqlite3.Row] = []

    for row in rows:
        session_id = str(row["session_id"] or "")
        by_session[session_id].append(row)
        exact_keys[
            (
                session_id,
                str(row["actor_type"] or ""),
                str(row["learning_type"] or ""),
                str(row["action"] or ""),
                str(row["outcome"] or ""),
            )
        ].append(row)
        same_action_keys[
            (
                session_id,
                str(row["actor_type"] or ""),
                str(row["learning_type"] or ""),
                str(row["action"] or ""),
            )
        ].append(row)
        same_outcome_keys[
            (
                session_id,
                str(row["actor_type"] or ""),
                str(row["learning_type"] or ""),
                str(row["outcome"] or ""),
            )
        ].append(row)
        if str(row["session_id"] or "") != str(row["scene_session_id"] or ""):
            mismatch_rows.append(row)

    path_counts = [len(session_rows) for session_rows in by_session.values()]
    exact_groups = [group for group in exact_keys.values() if len(group) > 1]
    same_action_groups = [group for group in same_action_keys.values() if len(group) > 1]
    same_outcome_groups = [group for group in same_outcome_keys.values() if len(group) > 1]

    return {
        "session_count": len(by_session),
        "paths_per_session": _summarize_distribution(path_counts),
        "top_sessions": [
            {
                "session_id": session_id,
                "chat_name": str(session_rows[0]["chat_name"] or ""),
                "path_count": len(session_rows),
            }
            for session_id, session_rows in sorted(by_session.items(), key=lambda item: len(item[1]), reverse=True)[
                :sample_limit
            ]
        ],
        "exact_same_action_outcome_groups": len(exact_groups),
        "exact_same_action_outcome_path_count": sum(len(group) for group in exact_groups),
        "same_action_groups": len(same_action_groups),
        "same_action_path_count": sum(len(group) for group in same_action_groups),
        "same_outcome_groups": len(same_outcome_groups),
        "same_outcome_path_count": sum(len(group) for group in same_outcome_groups),
        "path_scene_session_mismatch_count": len(mismatch_rows),
        "path_scene_session_mismatch_cluster_count": len({int(row["scene_cluster_id"]) for row in mismatch_rows}),
        "path_scene_session_mismatch_samples": [
            {
                "id": int(row["id"]),
                "path_session_id": str(row["session_id"] or ""),
                "scene_session_id": str(row["scene_session_id"] or ""),
                "chat_name": str(row["chat_name"] or ""),
                "scene_cluster_id": int(row["scene_cluster_id"]),
                "action": str(row["action"] or ""),
            }
            for row in mismatch_rows[:sample_limit]
        ],
    }


def _evaluate_path_lifecycle(rows: list[sqlite3.Row]) -> dict[str, Any]:
    total_count = len(rows)
    return {
        "count_distribution": _top_counter(Counter(int(row["count"] or 0) for row in rows), limit=12),
        "count_le_1": sum(1 for row in rows if int(row["count"] or 0) <= 1),
        "count_le_1_ratio": _percent(sum(1 for row in rows if int(row["count"] or 0) <= 1), total_count),
        "no_activation": sum(1 for row in rows if int(row["activation_count"] or 0) == 0),
        "no_activation_ratio": _percent(sum(1 for row in rows if int(row["activation_count"] or 0) == 0), total_count),
        "no_feedback": sum(
            1 for row in rows if int(row["success_count"] or 0) == 0 and int(row["failure_count"] or 0) == 0
        ),
        "no_feedback_ratio": _percent(
            sum(1 for row in rows if int(row["success_count"] or 0) == 0 and int(row["failure_count"] or 0) == 0),
            total_count,
        ),
        "reinforced_success": sum(1 for row in rows if int(row["success_count"] or 0) > 0),
        "reinforced_success_ratio": _percent(sum(1 for row in rows if int(row["success_count"] or 0) > 0), total_count),
        "failed": sum(1 for row in rows if int(row["failure_count"] or 0) > 0),
        "failed_ratio": _percent(sum(1 for row in rows if int(row["failure_count"] or 0) > 0), total_count),
    }


def _evaluate_scene_density(
    scene_rows: list[sqlite3.Row],
    path_rows: list[sqlite3.Row],
    *,
    sample_limit: int,
) -> dict[str, Any]:
    paths_by_scene: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in path_rows:
        paths_by_scene[int(row["scene_cluster_id"])].append(row)

    source_counts = [int(row["source_count"] or 0) for row in scene_rows]
    path_counts = [int(row["path_count"] or 0) for row in scene_rows]
    dense_scenes = sorted(scene_rows, key=lambda row: int(row["path_count"] or 0), reverse=True)[:sample_limit]
    dense_samples: list[dict[str, Any]] = []
    for scene in dense_scenes:
        scene_id = int(scene["id"])
        tag_distribution = _load_json_list(scene["tag_distribution"])
        dense_samples.append(
            {
                "scene_cluster_id": scene_id,
                "session_id": str(scene["session_id"] or ""),
                "chat_name": str(scene["chat_name"] or ""),
                "source_count": int(scene["source_count"] or 0),
                "path_count": int(scene["path_count"] or 0),
                "tags": [
                    {
                        "tag": str(item.get("tag") or ""),
                        "probability": float(item.get("probability") or 0.0),
                    }
                    for item in tag_distribution[:8]
                    if isinstance(item, dict)
                ],
                "paths": [
                    _path_to_sample(path_row)
                    for path_row in sorted(paths_by_scene.get(scene_id, []), key=lambda item: int(item["id"]))[:6]
                ],
            }
        )

    return {
        "scene_count": len(scene_rows),
        "source_count_distribution": _summarize_distribution(source_counts),
        "source_count_le_1": sum(1 for value in source_counts if value <= 1),
        "source_count_le_1_ratio": _percent(sum(1 for value in source_counts if value <= 1), len(source_counts)),
        "path_count_distribution": _summarize_distribution(path_counts),
        "path_count_ge_3": sum(1 for value in path_counts if value >= 3),
        "dense_scene_samples": dense_samples,
    }


def build_report(db_path: Path, *, sample_limit: int) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        path_rows = _load_behavior_rows(connection)
        scene_rows = _load_scene_rows(connection)
    finally:
        connection.close()

    return {
        "db_path": str(db_path),
        "path_count": len(path_rows),
        "quality_gate": _evaluate_quality(path_rows, sample_limit=sample_limit),
        "path_lifecycle": _evaluate_path_lifecycle(path_rows),
        "chat_scope": _evaluate_chat_scope(path_rows, sample_limit=sample_limit),
        "scene_density": _evaluate_scene_density(scene_rows, path_rows, sample_limit=sample_limit),
    }


def _append_sample_lines(lines: list[str], samples: list[dict[str, Any]], *, limit: int) -> None:
    for sample in samples[:limit]:
        reason = sample.get("reason")
        suffix = f" reason={reason}" if reason else ""
        lines.append(
            f"- #{sample['id']} {sample['chat_name']} "
            f"act={sample['activation_count']} succ={sample['success_count']} score={sample['score']}{suffix}"
        )
        lines.append(f"  - action: {_compact_text(sample['action'])}")
        lines.append(f"  - outcome: {_compact_text(sample['outcome'])}")


def write_reports(report: dict[str, Any], *, json_output: Path, md_output: Path, sample_limit: int) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    quality = report["quality_gate"]
    lifecycle = report["path_lifecycle"]
    chat_scope = report["chat_scope"]
    scene_density = report["scene_density"]

    lines = [
        "# 行为学习质量评估",
        "",
        f"- db: `{report['db_path']}`",
        f"- enabled path count: {report['path_count']}",
        "",
        "## 关键结论",
        "",
        "- 关键词式行为候选质量门已移除；代码层不再按具体词汇拦截 action/outcome。",
        f"- `count <= 1` 的路径有 {lifecycle['count_le_1']} 条，比例 {lifecycle['count_le_1_ratio']:.2%}。",
        f"- 未被检索激活过的路径有 {lifecycle['no_activation']} 条，比例 {lifecycle['no_activation_ratio']:.2%}。",
        f"- 无反馈路径有 {lifecycle['no_feedback']} 条，比例 {lifecycle['no_feedback_ratio']:.2%}。",
        f"- 同 chat 内精确相同 action+outcome 组数：{chat_scope['exact_same_action_outcome_groups']}。",
        "- 已移除写入阶段的 action/outcome 相似路径复用；当前只保留精确 action+outcome upsert。",
        f"- path.session_id 与 scene_cluster.session_id 不一致路径："
        f"{chat_scope['path_scene_session_mismatch_count']} 条，涉及 "
        f"{chat_scope['path_scene_session_mismatch_cluster_count']} 个场景簇。",
        "",
        "## 生命周期分布",
        "",
        f"- count_distribution: `{json.dumps(lifecycle['count_distribution'], ensure_ascii=False)}`",
        f"- reinforced_success: {lifecycle['reinforced_success']} ({lifecycle['reinforced_success_ratio']:.2%})",
        f"- failed: {lifecycle['failed']} ({lifecycle['failed_ratio']:.2%})",
        "",
        "## Chat 维度",
        "",
        f"- session_count: {chat_scope['session_count']}",
        f"- paths_per_session: `{json.dumps(chat_scope['paths_per_session'], ensure_ascii=False)}`",
        "",
        "### Top Chat",
    ]
    for item in chat_scope["top_sessions"]:
        lines.append(f"- {item['path_count']} paths: {item['chat_name']} (`{item['session_id']}`)")

    lines.extend(
        [
            "",
            "## 行为路径内容样例",
            "",
            "- 说明：这里只展示内容，不再根据关键词给出 blocked/reason。",
        ]
    )
    _append_sample_lines(lines, quality["path_samples"], limit=sample_limit)

    lines.extend(["", "## 已强化/激活样例"])
    _append_sample_lines(lines, quality["reinforced_samples"], limit=sample_limit)

    lines.extend(
        [
            "",
            "## 稠密场景样例",
            "",
            f"- scene_count: {scene_density['scene_count']}",
            f"- source_count_distribution: "
            f"`{json.dumps(scene_density['source_count_distribution'], ensure_ascii=False)}`",
            f"- source_count_le_1: {scene_density['source_count_le_1']} "
            f"({scene_density['source_count_le_1_ratio']:.2%})",
        ]
    )
    for scene in scene_density["dense_scene_samples"][:sample_limit]:
        lines.append("")
        lines.append(
            f"### scene #{scene['scene_cluster_id']} {scene['chat_name']} "
            f"source_count={scene['source_count']} path_count={scene['path_count']}"
        )
        lines.append(
            "- tags: "
            + ", ".join(f"{tag['tag']}={tag['probability']:.3f}" for tag in scene["tags"][:6])
        )
        _append_sample_lines(lines, scene["paths"], limit=3)

    lines.extend(["", "## Chat/场景归属不一致样例"])
    for sample in chat_scope["path_scene_session_mismatch_samples"]:
        lines.append(
            f"- #{sample['id']} scene #{sample['scene_cluster_id']} "
            f"path_session=`{sample['path_session_id']}` scene_session=`{sample['scene_session_id']}`"
        )
        lines.append(f"  - action: {_compact_text(sample['action'])}")

    md_output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="评估行为学习产物的复用质量、冗余和 chat_id 归属。")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="要评估的行为数据库，默认 data/MaiBot.db。")
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--sample-limit", type=int, default=8, help="每类样例最多输出多少条。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    report = build_report(db_path, sample_limit=max(1, args.sample_limit))
    write_reports(
        report,
        json_output=Path(args.json_output),
        md_output=Path(args.md_output),
        sample_limit=max(1, args.sample_limit),
    )
    print(
        json.dumps(
            {
                "path_count": report["path_count"],
                "keyword_quality_gate_enabled": report["quality_gate"]["keyword_quality_gate_enabled"],
                "exact_same_action_outcome_groups": report["chat_scope"]["exact_same_action_outcome_groups"],
                "json_output": args.json_output,
                "md_output": args.md_output,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
