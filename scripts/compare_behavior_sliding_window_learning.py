from argparse import ArgumentParser, Namespace
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Any, Sequence

import asyncio
import json
import math
import sys
import types

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.offline_behavior_learning import (  # noqa: E402
    _build_engine,
    _build_session_provider,
    _chat_display_name,
    _copy_chat_session,
    _initialize_target_database,
    _patch_behavior_model_name,
    _patch_behavior_storage,
    _resolve_path,
    _window_hash,
)
from src.chat.message_receive.message import SessionMessage  # noqa: E402
from src.common.database.database_model import (  # noqa: E402
    BehaviorAction,
    BehaviorExperiencePath,
    BehaviorOutcome,
    BehaviorSceneCluster,
    BehaviorSceneTagCluster,
    ChatSession,
    Messages,
)
from src.learners.behavior_learner import BehaviorLearner  # noqa: E402
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    _cluster_distribution_overlap,
    _load_cluster_distribution,
)


DEFAULT_RUN_DIR = "data/analysis/behavior_sliding_window_learning_abtest"
DEFAULT_WINDOW_SIZE = 30
DEFAULT_HALF_WINDOW = 15


@dataclass(frozen=True)
class SourceMessageWindow:
    session_id: str
    display_name: str
    center_start: int
    current_messages: list[SessionMessage]
    previous_shift_messages: list[SessionMessage]
    next_shift_messages: list[SessionMessage]


@dataclass(frozen=True)
class LearningTask:
    method: str
    base_index: int
    repeat_index: int
    session_id: str
    display_name: str
    variant: str
    messages: list[SessionMessage]


def _now_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _split_values(raw_values: Sequence[str]) -> list[str]:
    values: list[str] = []
    for raw_value in raw_values:
        for item in str(raw_value or "").replace("，", ",").split(","):
            value = item.strip()
            if value and value not in values:
                values.append(value)
    return values


def _load_messages_by_session(
    source_session: Session,
    *,
    session_id: str,
    min_text_length: int,
    limit: int,
) -> list[SessionMessage]:
    statement = (
        select(Messages)
        .where(Messages.session_id == session_id)
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .order_by(Messages.timestamp.asc())  # type: ignore[attr-defined]
    )
    if limit > 0:
        statement = statement.limit(limit)

    messages: list[SessionMessage] = []
    for record in source_session.exec(statement).all():
        text = " ".join((record.processed_plain_text or "").split()).strip()
        if len(text) < min_text_length:
            continue
        messages.append(SessionMessage.from_db_instance(record))
    return messages


def _discover_session_ids(source_session: Session, *, min_messages: int) -> list[str]:
    statement = (
        select(Messages.session_id, func.count(Messages.id))
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .group_by(Messages.session_id)
        .having(func.count(Messages.id) >= min_messages)
        .order_by(func.count(Messages.id).desc())
    )
    return [
        str(row[0])
        for row in source_session.exec(statement).all()
        if str(row[0] or "").strip()
    ]


def _build_candidate_windows_for_session(
    *,
    session_id: str,
    display_name: str,
    messages: list[SessionMessage],
    window_size: int,
    half_window: int,
    step: int,
) -> list[SourceMessageWindow]:
    candidates: list[SourceMessageWindow] = []
    start_min = half_window
    start_max_exclusive = len(messages) - window_size - half_window + 1
    normalized_step = max(1, step)
    for center_start in range(start_min, max(start_min, start_max_exclusive), normalized_step):
        previous_shift_messages = messages[center_start - half_window : center_start + half_window]
        current_messages = messages[center_start : center_start + window_size]
        next_shift_messages = messages[center_start + half_window : center_start + window_size + half_window]
        if (
            len(previous_shift_messages) == window_size
            and len(current_messages) == window_size
            and len(next_shift_messages) == window_size
        ):
            candidates.append(
                SourceMessageWindow(
                    session_id=session_id,
                    display_name=display_name,
                    center_start=center_start,
                    current_messages=current_messages,
                    previous_shift_messages=previous_shift_messages,
                    next_shift_messages=next_shift_messages,
                )
            )
    return candidates


def _select_base_windows(
    source_session: Session,
    *,
    session_ids: list[str],
    sample_count: int,
    seed: int,
    window_size: int,
    half_window: int,
    step: int,
    min_text_length: int,
    limit: int,
) -> list[SourceMessageWindow]:
    randomizer = Random(seed)
    all_candidates: list[SourceMessageWindow] = []
    for session_id in session_ids:
        chat_session = source_session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
        if chat_session is None:
            continue
        display_name = _chat_display_name(chat_session)
        messages = _load_messages_by_session(
            source_session,
            session_id=session_id,
            min_text_length=min_text_length,
            limit=limit,
        )
        all_candidates.extend(
            _build_candidate_windows_for_session(
                session_id=session_id,
                display_name=display_name,
                messages=messages,
                window_size=window_size,
                half_window=half_window,
                step=step,
            )
        )
    randomizer.shuffle(all_candidates)
    return all_candidates[:sample_count]


def _build_learning_tasks(method: str, base_windows: Sequence[SourceMessageWindow]) -> list[LearningTask]:
    tasks: list[LearningTask] = []
    for base_index, window in enumerate(base_windows, start=1):
        if method == "single_repeat":
            variants = [
                ("current_repeat_1", window.current_messages),
                ("current_repeat_2", window.current_messages),
                ("current_repeat_3", window.current_messages),
            ]
        elif method == "sliding":
            variants = [
                ("previous15_current_first15", window.previous_shift_messages),
                ("current30", window.current_messages),
                ("current_last15_next15", window.next_shift_messages),
            ]
        else:
            raise ValueError(f"未知方法: {method}")

        for repeat_index, (variant, messages) in enumerate(variants, start=1):
            tasks.append(
                LearningTask(
                    method=method,
                    base_index=base_index,
                    repeat_index=repeat_index,
                    session_id=window.session_id,
                    display_name=window.display_name,
                    variant=variant,
                    messages=messages,
                )
            )
    return tasks


def _message_time_range(messages: Sequence[SessionMessage]) -> dict[str, str]:
    if not messages:
        return {"start": "", "end": ""}
    return {
        "start": messages[0].timestamp.isoformat(timespec="seconds"),
        "end": messages[-1].timestamp.isoformat(timespec="seconds"),
    }


async def _run_learning_tasks(
    *,
    method: str,
    target_db_path: Path,
    source_session: Session,
    target_session_local,
    get_target_session,
    base_windows: Sequence[SourceMessageWindow],
    args: Namespace,
) -> dict[str, Any]:
    _patch_behavior_storage(get_target_session)
    _patch_behavior_model_name(args.model_name)

    with target_session_local() as target_session:
        copied_session_ids: set[str] = set()
        for base_window in base_windows:
            if base_window.session_id in copied_session_ids:
                continue
            _copy_chat_session(source_session, target_session, base_window.session_id)
            copied_session_ids.add(base_window.session_id)

    tasks = _build_learning_tasks(method, base_windows)
    progress: list[dict[str, Any]] = []
    wrote_count = 0
    for task_index, task in enumerate(tasks, start=1):
        learner = BehaviorLearner(task.session_id)
        learner.min_messages_for_extraction = min(args.window_size, max(1, args.min_messages_for_extraction))
        task_session_id = task.session_id

        def resolve_learning_session_id(
            self,
            pending_messages: list[SessionMessage],
            learning_session_id: str = task_session_id,
        ) -> str:
            del pending_messages
            return learning_session_id

        learner._resolve_learning_session_id = types.MethodType(  # type: ignore[method-assign]
            resolve_learning_session_id,
            learner,
        )

        print(
            f"[{method} {task_index}/{len(tasks)}] base={task.base_index} "
            f"variant={task.variant} chat={task.display_name} "
            f"{_message_time_range(task.messages)['start']} ~ {_message_time_range(task.messages)['end']}"
        )
        try:
            wrote_pattern = await learner._learn_from_session_messages(task.messages)
        except Exception as exc:
            progress.append(_task_payload(task, status="failed", error=str(exc), wrote_pattern=False))
            raise
        if wrote_pattern:
            wrote_count += 1
        progress.append(_task_payload(task, status="completed", error="", wrote_pattern=wrote_pattern))

    return {
        "method": method,
        "target_db": str(target_db_path),
        "attempted_count": len(tasks),
        "wrote_count": wrote_count,
        "progress": progress,
        "quality": _analyze_target_database(target_session_local),
    }


def _task_payload(
    task: LearningTask,
    *,
    status: str,
    error: str,
    wrote_pattern: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "method": task.method,
        "base_index": task.base_index,
        "repeat_index": task.repeat_index,
        "variant": task.variant,
        "session_id": task.session_id,
        "chat_name": task.display_name,
        "message_count": len(task.messages),
        "window_hash": _window_hash(task.messages),
        "time_range": _message_time_range(task.messages),
        "wrote_pattern": wrote_pattern,
        "error": error,
    }


def _distribution_metrics(raw_distribution: Any) -> dict[str, Any]:
    distribution = _load_cluster_distribution(raw_distribution)
    probs: list[float] = []
    tags: list[str] = []
    for item in distribution:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip()
        try:
            probability = float(item.get("probability") or 0.0)
        except (TypeError, ValueError):
            probability = 0.0
        if tag and probability > 0:
            tags.append(tag)
            probs.append(probability)
    total_probability = sum(probs)
    if total_probability > 0:
        probs = [probability / total_probability for probability in probs]
    tag_count = len(probs)
    max_probability = max(probs) if probs else 0.0
    entropy = -sum(probability * math.log(probability) for probability in probs if probability > 0)
    normalized_entropy = entropy / math.log(tag_count) if tag_count > 1 else 0.0
    uniform_gap = max(abs(probability - 1.0 / tag_count) for probability in probs) if tag_count > 0 else 0.0
    return {
        "tags": tags,
        "tag_count": tag_count,
        "max_probability": max_probability,
        "normalized_entropy": normalized_entropy,
        "uniform_gap": uniform_gap,
    }


def _source_bucket(source_count: int) -> str:
    if source_count <= 1:
        return "1"
    if source_count <= 2:
        return "2"
    if source_count <= 3:
        return "3"
    if source_count <= 5:
        return "4-5"
    if source_count <= 10:
        return "6-10"
    return "11+"


def _analyze_target_database(target_session_local) -> dict[str, Any]:
    with target_session_local() as session:
        clusters = list(session.exec(select(BehaviorSceneCluster)).all())
        paths = list(session.exec(select(BehaviorExperiencePath)).all())
        actions = list(session.exec(select(BehaviorAction)).all())
        outcomes = list(session.exec(select(BehaviorOutcome)).all())
        tag_rows = list(session.exec(select(BehaviorSceneTagCluster)).all())

        cluster_metrics: list[dict[str, Any]] = []
        tag_df_counter: Counter[str] = Counter()
        for cluster in clusters:
            metrics = _distribution_metrics(cluster.tag_distribution)
            for tag in set(metrics["tags"]):
                tag_df_counter[tag] += 1
            cluster_metrics.append(
                {
                    "id": cluster.id,
                    "session_id": cluster.session_id,
                    "source_count": int(cluster.source_count or 0),
                    "score": float(cluster.score or 0.0),
                    **metrics,
                }
            )

        source_buckets = Counter(_source_bucket(item["source_count"]) for item in cluster_metrics)
        path_count_by_cluster = Counter(int(path.scene_cluster_id) for path in paths if path.scene_cluster_id is not None)
        duplicate_pairs = _count_duplicate_like_cluster_pairs(clusters)
        top_tags = [
            {"tag": tag, "cluster_count": count}
            for tag, count in tag_df_counter.most_common(15)
        ]

        return {
            "cluster_count": len(clusters),
            "path_count": len(paths),
            "action_count": len(actions),
            "outcome_count": len(outcomes),
            "tag_cluster_count": len({(row.tag_kind, row.cluster_key) for row in tag_rows}),
            "avg_source_count": _avg([item["source_count"] for item in cluster_metrics]),
            "max_source_count": max((item["source_count"] for item in cluster_metrics), default=0),
            "source_count_buckets": dict(sorted(source_buckets.items())),
            "singleton_cluster_ratio": _ratio(sum(1 for item in cluster_metrics if item["source_count"] <= 1), len(cluster_metrics)),
            "avg_tag_count": _avg([item["tag_count"] for item in cluster_metrics]),
            "avg_max_probability": _avg([item["max_probability"] for item in cluster_metrics]),
            "avg_normalized_entropy": _avg([item["normalized_entropy"] for item in cluster_metrics]),
            "near_uniform_ratio": _ratio(
                sum(1 for item in cluster_metrics if item["tag_count"] > 1 and item["uniform_gap"] <= 0.02),
                len(cluster_metrics),
            ),
            "low_peak_ratio": _ratio(
                sum(1 for item in cluster_metrics if item["tag_count"] > 1 and item["max_probability"] <= 0.34),
                len(cluster_metrics),
            ),
            "duplicate_like_pair_count": duplicate_pairs["count"],
            "duplicate_like_pair_ratio": duplicate_pairs["ratio"],
            "avg_paths_per_cluster": _avg(list(path_count_by_cluster.values())),
            "unused_cluster_count": sum(1 for cluster in clusters if int(cluster.id or 0) not in path_count_by_cluster),
            "top_tags": top_tags,
            "clusters": sorted(cluster_metrics, key=lambda item: (item["source_count"], item["id"] or 0), reverse=True)[:20],
        }


def _count_duplicate_like_cluster_pairs(clusters: Sequence[BehaviorSceneCluster]) -> dict[str, Any]:
    if len(clusters) < 2:
        return {"count": 0, "ratio": 0.0}
    duplicate_count = 0
    pair_count = 0
    distributions = [(cluster.id, _load_cluster_distribution(cluster.tag_distribution)) for cluster in clusters]
    for left_index, (_, left_distribution) in enumerate(distributions):
        for _, right_distribution in distributions[left_index + 1 :]:
            pair_count += 1
            if _cluster_distribution_overlap(left_distribution, right_distribution) >= 0.72:
                duplicate_count += 1
    return {
        "count": duplicate_count,
        "ratio": round(float(duplicate_count) / float(pair_count), 4) if pair_count else 0.0,
    }


def _avg(values: Sequence[float | int]) -> float:
    if not values:
        return 0.0
    return round(sum(float(value) for value in values) / float(len(values)), 4)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _base_window_payload(base_window: SourceMessageWindow, *, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "session_id": base_window.session_id,
        "chat_name": base_window.display_name,
        "center_start": base_window.center_start,
        "current": {
            "time_range": _message_time_range(base_window.current_messages),
            "message_count": len(base_window.current_messages),
            "window_hash": _window_hash(base_window.current_messages),
        },
        "previous_shift": {
            "time_range": _message_time_range(base_window.previous_shift_messages),
            "message_count": len(base_window.previous_shift_messages),
            "window_hash": _window_hash(base_window.previous_shift_messages),
        },
        "next_shift": {
            "time_range": _message_time_range(base_window.next_shift_messages),
            "message_count": len(base_window.next_shift_messages),
            "window_hash": _window_hash(base_window.next_shift_messages),
        },
    }


async def run_experiment(args: Namespace) -> dict[str, Any]:
    source_db_path = _resolve_path(args.source_db)
    run_dir = _resolve_path(args.run_dir)
    if run_dir.exists() and any(run_dir.iterdir()) and not args.allow_existing_run_dir:
        run_dir = run_dir.parent / f"{run_dir.name}_{_now_run_id()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    source_engine = _build_engine(source_db_path, readonly=True)
    source_session_local = sessionmaker(autocommit=False, autoflush=False, bind=source_engine, class_=Session)

    single_db_path = run_dir / "single_repeat.db"
    sliding_db_path = run_dir / "sliding.db"
    single_engine = _build_engine(single_db_path)
    sliding_engine = _build_engine(sliding_db_path)
    _initialize_target_database(single_engine)
    _initialize_target_database(sliding_engine)
    single_session_local = sessionmaker(autocommit=False, autoflush=False, bind=single_engine, class_=Session)
    sliding_session_local = sessionmaker(autocommit=False, autoflush=False, bind=sliding_engine, class_=Session)
    single_provider = _build_session_provider(single_session_local)
    sliding_provider = _build_session_provider(sliding_session_local)

    with source_session_local() as source_session:
        requested_session_ids = _split_values([*args.session_id, *args.chat_id])
        if requested_session_ids:
            session_ids = requested_session_ids
        else:
            min_messages = args.window_size + args.half_window * 2
            session_ids = _discover_session_ids(source_session, min_messages=min_messages)
        base_windows = _select_base_windows(
            source_session,
            session_ids=session_ids,
            sample_count=args.samples,
            seed=args.seed,
            window_size=args.window_size,
            half_window=args.half_window,
            step=args.step,
            min_text_length=args.min_text_length,
            limit=args.limit,
        )
        if len(base_windows) < args.samples:
            raise ValueError(f"可用中心窗口不足: 需要 {args.samples}，实际 {len(base_windows)}")

        print(f"源数据库: {source_db_path}")
        print(f"运行目录: {run_dir}")
        print(f"中心窗口数: {len(base_windows)}")
        for index, base_window in enumerate(base_windows, start=1):
            print(
                f"- base[{index}] {base_window.display_name} "
                f"{_message_time_range(base_window.current_messages)['start']} ~ "
                f"{_message_time_range(base_window.current_messages)['end']}"
            )

        single_result = await _run_learning_tasks(
            method="single_repeat",
            target_db_path=single_db_path,
            source_session=source_session,
            target_session_local=single_session_local,
            get_target_session=single_provider,
            base_windows=base_windows,
            args=args,
        )
        sliding_result = await _run_learning_tasks(
            method="sliding",
            target_db_path=sliding_db_path,
            source_session=source_session,
            target_session_local=sliding_session_local,
            get_target_session=sliding_provider,
            base_windows=base_windows,
            args=args,
        )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_db": str(source_db_path),
        "run_dir": str(run_dir),
        "samples": args.samples,
        "window_size": args.window_size,
        "half_window": args.half_window,
        "seed": args.seed,
        "model_name": args.model_name or "配置任务 learner 默认模型",
        "base_windows": [
            _base_window_payload(base_window, index=index)
            for index, base_window in enumerate(base_windows, start=1)
        ],
        "methods": {
            "single_repeat": single_result,
            "sliding": sliding_result,
        },
        "comparison": _compare_quality(single_result["quality"], sliding_result["quality"]),
    }
    report_path = run_dir / "report.json"
    markdown_path = run_dir / "report.md"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    print(f"JSON report: {report_path}")
    print(f"Markdown report: {markdown_path}")
    return report


def _compare_quality(single_quality: dict[str, Any], sliding_quality: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "cluster_count",
        "path_count",
        "avg_source_count",
        "max_source_count",
        "singleton_cluster_ratio",
        "avg_tag_count",
        "avg_max_probability",
        "avg_normalized_entropy",
        "near_uniform_ratio",
        "low_peak_ratio",
        "duplicate_like_pair_ratio",
        "avg_paths_per_cluster",
    ]
    return {
        key: {
            "single_repeat": single_quality.get(key),
            "sliding": sliding_quality.get(key),
        }
        for key in keys
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 行为学习滑动窗口 AB 测试")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 源数据库：`{report['source_db']}`")
    lines.append(f"- 运行目录：`{report['run_dir']}`")
    lines.append(f"- 中心窗口数：{report['samples']}")
    lines.append(f"- 当前窗口大小：{report['window_size']}")
    lines.append(f"- 前后滑动：{report['half_window']}")
    lines.append(f"- 模型：{report['model_name']}")
    lines.append("")
    lines.append("## 方法")
    lines.append("")
    lines.append("- 原版：当前 30 条窗口重复学习 3 次。")
    lines.append("- 滑动：前 15 + 当前前 15、当前 30、当前后 15 + 后 15 各学习 1 次。")
    lines.append("")
    lines.append("## 汇总对比")
    lines.append("")
    lines.append("| 指标 | 原版 | 滑动 |")
    lines.append("| --- | ---: | ---: |")
    for key, values in report["comparison"].items():
        lines.append(f"| {key} | {values['single_repeat']} | {values['sliding']} |")

    for method_key, title in [("single_repeat", "原版单窗口三次"), ("sliding", "滑动窗口三次")]:
        method = report["methods"][method_key]
        quality = method["quality"]
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"- 数据库：`{method['target_db']}`")
        lines.append(f"- 学习调用：{method['attempted_count']}")
        lines.append(f"- 有写入调用：{method['wrote_count']}")
        lines.append(f"- 场景簇数：{quality['cluster_count']}")
        lines.append(f"- 行为路径数：{quality['path_count']}")
        lines.append(f"- source_count 分布：`{json.dumps(quality['source_count_buckets'], ensure_ascii=False)}`")
        lines.append(f"- 单例簇比例：{quality['singleton_cluster_ratio']}")
        lines.append(f"- 近似均匀比例：{quality['near_uniform_ratio']}")
        lines.append(f"- 低峰值比例：{quality['low_peak_ratio']}")
        lines.append(f"- 相似簇重复比例：{quality['duplicate_like_pair_ratio']}")
        lines.append("")
        lines.append("Top tags：")
        for item in quality["top_tags"][:10]:
            lines.append(f"- {item['tag']}：{item['cluster_count']}")

    lines.append("")
    lines.append("## 中心窗口")
    lines.append("")
    for base_window in report["base_windows"]:
        lines.append(
            f"- #{base_window['index']} {base_window['chat_name']} "
            f"`{base_window['session_id']}` "
            f"{base_window['current']['time_range']['start']} ~ {base_window['current']['time_range']['end']}"
        )
    return "\n".join(lines)


def parse_args() -> Namespace:
    parser = ArgumentParser(description="比较行为学习单窗口三次与滑动窗口三次的场景簇质量。")
    parser.add_argument("--source-db", default="data/MaiBot.db")
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--half-window", type=int, default=DEFAULT_HALF_WINDOW)
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-text-length", type=int, default=1)
    parser.add_argument("--min-messages-for-extraction", type=int, default=10)
    parser.add_argument("--model-name", default="")
    parser.add_argument("--allow-existing-run-dir", action="store_true")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        asyncio.run(run_experiment(parse_args()))
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
