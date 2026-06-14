from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

import json
import sys

from sqlmodel import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.database.database import get_db_session  # noqa: E402
from src.common.database.database_model import (  # noqa: E402
    BehaviorAction,
    BehaviorExperiencePath,
    BehaviorOutcome,
    BehaviorSceneCluster,
)
from src.learners.behavior_scenario import BehaviorScenarioProfile, parse_behavior_scenario_response  # noqa: E402
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    DEFAULT_BEHAVIOR_SCENE_RETRIEVAL_MODE,
    _load_cluster_distribution,
    build_scene_cluster_distribution,
    debug_retrieve_behavior_scores_from_scene_clusters,
    format_scene_cluster_distribution,
)


def _split_values(raw_value: str) -> list[str]:
    values: list[str] = []
    for item in str(raw_value or "").replace("，", ",").split(","):
        value = " ".join(item.split()).strip()
        if value and value not in values:
            values.append(value)
    return values


def _build_profile(args: Namespace) -> BehaviorScenarioProfile:
    if not args.profile_json:
        raise ValueError("请提供 --profile-json，且其中必须包含 tag_clusters")
    raw_profile = json.loads(args.profile_json)
    if not isinstance(raw_profile, dict):
        raise ValueError("--profile-json 必须是 JSON 对象")
    profile = parse_behavior_scenario_response(json.dumps(raw_profile, ensure_ascii=False))
    if not profile.tag_clusters:
        raise ValueError("--profile-json 必须包含非空 tag_clusters")
    return profile


def _session_ids(args: Namespace) -> set[str]:
    values: set[str] = set()
    for raw_item in args.session_id:
        values.update(_split_values(raw_item))
    return values


def _path_payload(session: Session, path_id: int, score: float) -> dict[str, Any]:
    path = session.get(BehaviorExperiencePath, path_id)
    if path is None:
        return {"id": path_id, "score": round(score, 4), "missing": True}

    cluster = session.get(BehaviorSceneCluster, path.scene_cluster_id)
    action = session.get(BehaviorAction, path.action_id)
    outcome = session.get(BehaviorOutcome, path.outcome_id)
    return {
        "id": path.id,
        "score": round(score, 4),
        "session_id": path.session_id,
        "cluster_id": path.scene_cluster_id,
        "cluster": format_scene_cluster_distribution(_load_cluster_distribution(cluster.tag_distribution))
        if cluster is not None
        else "",
        "action": action.action if action is not None else "",
        "outcome": outcome.outcome if outcome is not None else "",
        "count": path.count,
        "success_count": path.success_count,
        "failure_count": path.failure_count,
        "enabled": path.enabled,
    }


def _cluster_payload(session: Session, cluster_id: int, score: float) -> dict[str, Any]:
    cluster = session.get(BehaviorSceneCluster, cluster_id)
    if cluster is None:
        return {"id": cluster_id, "score": round(score, 4), "missing": True}
    return {
        "id": cluster.id,
        "score": round(score, 4),
        "session_id": cluster.session_id,
        "name": format_scene_cluster_distribution(_load_cluster_distribution(cluster.tag_distribution)),
        "tags": _load_cluster_distribution(cluster.tag_distribution),
        "source_count": cluster.source_count,
        "cluster_score": cluster.score,
    }


def compare_matching(args: Namespace) -> dict[str, Any]:
    profile = _build_profile(args)
    if not profile.has_signal:
        raise ValueError("请提供包含 tag_clusters 的场景画像")

    session_ids = _session_ids(args)
    target_distribution = build_scene_cluster_distribution(profile)
    retrieval_mode = str(getattr(args, "retrieval_mode", DEFAULT_BEHAVIOR_SCENE_RETRIEVAL_MODE) or "").strip()
    if not retrieval_mode:
        retrieval_mode = DEFAULT_BEHAVIOR_SCENE_RETRIEVAL_MODE
    debug_result = debug_retrieve_behavior_scores_from_scene_clusters(
        session_ids=session_ids,
        include_global=args.include_global,
        profile=profile,
        max_count=args.max_count,
        retrieval_mode=retrieval_mode,
    )

    with get_db_session(auto_commit=False) as session:
        matched_clusters = [
            _cluster_payload(session, int(cluster["cluster_id"]), float(cluster["score"]))
            for cluster in debug_result.get("matched_clusters", [])[: args.max_count]
            if cluster.get("cluster_id") is not None
        ]
        paths = [
            _path_payload(session, int(candidate["behavior_id"]), float(candidate["score"]))
            for candidate in debug_result.get("candidate_scores", [])[: args.max_count]
            if candidate.get("behavior_id") is not None
        ]

    return {
        "scope": {
            "session_ids": sorted(session_ids),
            "include_global": args.include_global,
            "include_global_note": "include_global=True 会跳过 session_id 过滤，用于观察全库行为。",
        },
        "profile": {
            "summary": profile.summary,
            "tag_clusters": profile.domain_prompt_payloads(),
            "need": profile.need_prompt_payload(),
            "other_traits": profile.other_traits_prompt_payloads(),
            "confidence": profile.confidence,
            "tag_key": profile.tag_cluster_text(),
        },
        "input_cluster_distribution": target_distribution,
        "scene_cluster": {
            "retrieval_mode": debug_result.get("retrieval_mode", retrieval_mode),
            "debug": debug_result.get("retrieval_debug", {}),
            "matched_clusters": matched_clusters,
            "behavior_candidates": paths,
        },
    }


def print_report(result: dict[str, Any]) -> None:
    print("行为场景簇匹配")
    print(f"scope session_ids={result['scope']['session_ids']} include_global={result['scope']['include_global']}")
    print(f"retrieval_mode={result['scene_cluster'].get('retrieval_mode')}")
    print(f"debug={json.dumps(result['scene_cluster'].get('debug') or {}, ensure_ascii=False)}")
    print("\n输入场景簇分布:")
    for tag in result["input_cluster_distribution"]:
        print(f"  {tag['tag']} = {tag['probability']}")

    print("\n场景簇命中:")
    for cluster in result["scene_cluster"]["matched_clusters"]:
        print(
            f"  #{cluster.get('id')} score={cluster.get('score')} "
            f"session={cluster.get('session_id') or '__global__'} name={cluster.get('name')}"
        )

    print("\n候选行为:")
    paths = result["scene_cluster"]["behavior_candidates"]
    if not paths:
        print("  无命中")
        return
    for path in paths:
        print(
            "  "
            f"#{path.get('id')} score={path.get('score')} session={path.get('session_id') or '__global__'} "
            f"cluster=#{path.get('cluster_id')} count={path.get('count')} "
            f"success={path.get('success_count')} failure={path.get('failure_count')}"
        )
        print(f"    场景簇: {path.get('cluster')}")
        print(f"    行为: {path.get('action')}")
        print(f"    结果: {path.get('outcome')}")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="预览行为学习的场景簇匹配。")
    parser.add_argument("--session-id", action="append", default=[], help="限定聊天流 session_id，可重复或用逗号分隔。")
    parser.add_argument(
        "--include-global",
        action="store_true",
        help="开启全库匹配；注意这会跳过 session_id 过滤。",
    )
    parser.add_argument("--profile-json", required=True, help="直接传入包含 tag_clusters 的场景画像 JSON 对象。")
    parser.add_argument("--max-count", type=int, default=10, help="输出的最大数量。")
    parser.add_argument(
        "--retrieval-mode",
        choices=["direct_domain_overlap", "tag_cluster_spread_1", "tag_cluster_spread_2"],
        default=DEFAULT_BEHAVIOR_SCENE_RETRIEVAL_MODE,
        help="场景检索模式，默认使用主线一次扩散。",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = compare_matching(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print_report(result)


if __name__ == "__main__":
    main()
