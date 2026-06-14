from argparse import ArgumentParser, Namespace
from collections import Counter
from datetime import datetime
from pathlib import Path
from shutil import copy2
from typing import Any

import json
import sqlite3


DEFAULT_DB_PATH = "data/MaiBot.db"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_feedback_cleanup_report.json"
DEFAULT_MD_OUTPUT = "data/analysis/behavior_feedback_cleanup_report.md"
CORE_MISSING_MARKERS = ("没有执行", "没有采用", "没有截图")
WEAK_POSITIVE_MARKERS = ("虽未", "未完全", "未直接", "未追问", "用户未直接回应")
POSITIVE_STATUSES = {"success", "succeeded", "completed"}
PARTIAL_STATUS = "partial_success"
RETRACTED_STATUS = "legacy_retracted"
NEGATIVE_STATUSES = {"failed", "blocked", "abandoned"}
MIN_BEHAVIOR_SCORE = -6.0
MAX_BEHAVIOR_SCORE = 8.0
MIN_SCENE_SCORE = -4.0
MAX_SCENE_SCORE = 6.0


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


def _dump_json_list(items: list[Any]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _status_counts(feedback_items: list[Any]) -> tuple[int, int]:
    success_count = 0
    failure_count = 0
    for item in feedback_items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in POSITIVE_STATUSES:
            success_count += 1
        elif status in NEGATIVE_STATUSES:
            failure_count += 1
    return success_count, failure_count


def _feedback_delta(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score_delta") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cleanup_feedback_item(item: dict[str, Any]) -> tuple[dict[str, Any], float, str]:
    reason = str(item.get("reason") or "")
    old_delta = _feedback_delta(item)
    if old_delta <= 0:
        return item, 0.0, ""

    core_missing = any(marker in reason for marker in CORE_MISSING_MARKERS)
    weak_positive = any(marker in reason for marker in WEAK_POSITIVE_MARKERS)
    if not core_missing and not weak_positive:
        return item, 0.0, ""

    updated_item = dict(item)
    cleanup_note = {
        "cleaned_at": datetime.now().isoformat(timespec="seconds"),
        "old_status": str(item.get("status") or ""),
        "old_score_delta": old_delta,
        "reason": "core_action_missing" if core_missing else "weak_result_evidence",
    }
    updated_item["legacy_cleanup"] = cleanup_note
    if core_missing:
        updated_item["status"] = RETRACTED_STATUS
        updated_item["score_delta"] = 0.0
        return updated_item, -old_delta, "core_action_missing"

    new_delta = min(0.25, old_delta)
    updated_item["status"] = PARTIAL_STATUS
    updated_item["score_delta"] = new_delta
    return updated_item, new_delta - old_delta, "weak_result_evidence"


def _load_feedback_paths(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        select p.id, p.scene_cluster_id, p.feedback_list, p.score, p.success_count, p.failure_count,
               a.action, o.outcome
        from behavior_experience_paths p
        join behavior_actions a on a.id = p.action_id
        join behavior_outcomes o on o.id = p.outcome_id
        where p.feedback_list is not null and p.feedback_list != '' and p.feedback_list != '[]'
        order by p.last_feedback_time desc, p.id
        """
    ).fetchall()


def build_cleanup_plan(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = _load_feedback_paths(connection)
    path_plans: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    missing_source_ids = 0
    total_feedback_items = 0
    total_score_adjustment = 0.0
    scene_score_adjustments: Counter[int] = Counter()

    for row in rows:
        feedback_items = _load_json_list(row["feedback_list"])
        if not feedback_items:
            continue
        updated_items: list[Any] = []
        path_score_adjustment = 0.0
        changes: list[dict[str, Any]] = []
        for index, item in enumerate(feedback_items):
            total_feedback_items += 1
            if not isinstance(item, dict):
                updated_items.append(item)
                continue
            if not item.get("source_ids"):
                missing_source_ids += 1
            updated_item, delta_adjustment, cleanup_reason = _cleanup_feedback_item(item)
            updated_items.append(updated_item)
            if cleanup_reason:
                reason_counts[cleanup_reason] += 1
                path_score_adjustment += delta_adjustment
                changes.append(
                    {
                        "feedback_index": index,
                        "cleanup_reason": cleanup_reason,
                        "old_status": item.get("status"),
                        "old_score_delta": _feedback_delta(item),
                        "new_status": updated_item.get("status"),
                        "new_score_delta": _feedback_delta(updated_item),
                        "reason": str(item.get("reason") or "")[:240],
                    }
                )

        if not changes:
            continue
        success_count, failure_count = _status_counts(updated_items)
        old_score = float(row["score"] or 0.0)
        new_score = _clamp(old_score + path_score_adjustment, MIN_BEHAVIOR_SCORE, MAX_BEHAVIOR_SCORE)
        score_adjustment = new_score - old_score
        total_score_adjustment += score_adjustment
        scene_score_adjustments[int(row["scene_cluster_id"])] += score_adjustment * 0.08
        path_plans.append(
            {
                "path_id": int(row["id"]),
                "scene_cluster_id": int(row["scene_cluster_id"]),
                "old_score": round(old_score, 4),
                "new_score": round(new_score, 4),
                "score_adjustment": round(score_adjustment, 4),
                "old_success_count": int(row["success_count"] or 0),
                "new_success_count": success_count,
                "old_failure_count": int(row["failure_count"] or 0),
                "new_failure_count": failure_count,
                "action": str(row["action"] or "")[:240],
                "outcome": str(row["outcome"] or "")[:240],
                "changes": changes,
                "updated_feedback_list": _dump_json_list(updated_items),
            }
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "paths_with_feedback": len(rows),
        "feedback_items": total_feedback_items,
        "feedback_items_missing_source_ids": missing_source_ids,
        "planned_path_changes": len(path_plans),
        "planned_feedback_changes": sum(len(plan["changes"]) for plan in path_plans),
        "reason_counts": dict(reason_counts),
        "total_score_adjustment": round(total_score_adjustment, 4),
        "scene_score_adjustments": {str(key): round(value, 4) for key, value in scene_score_adjustments.items()},
        "path_plans": path_plans,
    }


def apply_cleanup_plan(connection: sqlite3.Connection, plan: dict[str, Any]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for path_plan in plan["path_plans"]:
        connection.execute(
            """
            update behavior_experience_paths
            set feedback_list = ?,
                score = ?,
                success_count = ?,
                failure_count = ?,
                update_time = ?
            where id = ?
            """,
            (
                path_plan["updated_feedback_list"],
                path_plan["new_score"],
                path_plan["new_success_count"],
                path_plan["new_failure_count"],
                now,
                path_plan["path_id"],
            ),
        )

    for scene_cluster_id, raw_adjustment in plan["scene_score_adjustments"].items():
        adjustment = float(raw_adjustment)
        row = connection.execute(
            "select score from behavior_scene_clusters where id = ?",
            (int(scene_cluster_id),),
        ).fetchone()
        if row is None:
            continue
        old_score = float(row["score"] or 0.0)
        new_score = _clamp(old_score + adjustment, MIN_SCENE_SCORE, MAX_SCENE_SCORE)
        connection.execute(
            "update behavior_scene_clusters set score = ?, update_time = ? where id = ?",
            (new_score, now, int(scene_cluster_id)),
        )
    connection.commit()


def _write_reports(plan: dict[str, Any], *, json_output: Path, md_output: Path, applied: bool, backup_path: str) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(plan)
    report["applied"] = applied
    report["backup_path"] = backup_path
    slim_report = dict(report)
    slim_report["path_plans"] = [
        {key: value for key, value in path_plan.items() if key != "updated_feedback_list"}
        for path_plan in plan["path_plans"]
    ]
    json_output.write_text(json.dumps(slim_report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 行为反馈历史清洗报告",
        "",
        f"- applied: {applied}",
        f"- backup_path: `{backup_path}`" if backup_path else "- backup_path: 无",
        f"- paths_with_feedback: {plan['paths_with_feedback']}",
        f"- feedback_items: {plan['feedback_items']}",
        f"- feedback_items_missing_source_ids: {plan['feedback_items_missing_source_ids']}",
        f"- planned_path_changes: {plan['planned_path_changes']}",
        f"- planned_feedback_changes: {plan['planned_feedback_changes']}",
        f"- reason_counts: `{json.dumps(plan['reason_counts'], ensure_ascii=False)}`",
        f"- total_score_adjustment: {plan['total_score_adjustment']}",
        "",
        "## 变更样例",
    ]
    for path_plan in plan["path_plans"][:20]:
        lines.append("")
        lines.append(f"### path #{path_plan['path_id']}")
        lines.append(f"- score: {path_plan['old_score']} -> {path_plan['new_score']}")
        lines.append(f"- success_count: {path_plan['old_success_count']} -> {path_plan['new_success_count']}")
        lines.append(f"- action: {path_plan['action']}")
        for change in path_plan["changes"]:
            lines.append(
                "- feedback: "
                f"{change['old_status']}({change['old_score_delta']}) -> "
                f"{change['new_status']}({change['new_score_delta']}), "
                f"reason={change['cleanup_reason']}"
            )
            lines.append(f"  - evidence: {change['reason']}")
    md_output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _backup_database(db_path: Path) -> Path:
    backup_dir = db_path.parent / "db_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.stem}_before_behavior_feedback_cleanup_{datetime.now():%Y%m%d_%H%M%S}{db_path.suffix}"
    copy2(db_path, backup_path)
    return backup_path


def parse_args() -> Namespace:
    parser = ArgumentParser(description="审计并清洗历史行为反馈。默认 dry-run，不修改数据库。")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--md-output", default=DEFAULT_MD_OUTPUT)
    parser.add_argument("--apply", action="store_true", help="实际修改目标数据库。默认只输出报告。")
    parser.add_argument("--backup", action="store_true", help="apply 前复制数据库到 data/db_backups。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    backup_path = ""
    if args.apply and args.backup:
        backup_path = str(_backup_database(db_path))

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        plan = build_cleanup_plan(connection)
        if args.apply:
            apply_cleanup_plan(connection, plan)
    finally:
        connection.close()

    _write_reports(
        plan,
        json_output=Path(args.json_output),
        md_output=Path(args.md_output),
        applied=bool(args.apply),
        backup_path=backup_path,
    )
    print(
        json.dumps(
            {
                "db": str(db_path),
                "applied": bool(args.apply),
                "backup_path": backup_path,
                "planned_path_changes": plan["planned_path_changes"],
                "planned_feedback_changes": plan["planned_feedback_changes"],
                "json_output": args.json_output,
                "md_output": args.md_output,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
