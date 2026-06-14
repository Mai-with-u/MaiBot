from argparse import ArgumentParser, Namespace
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Any, Sequence

import asyncio
import hashlib
import json
import sys

from sqlalchemy import func
from sqlmodel import Session, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.offline_behavior_learning import _chat_display_name  # noqa: E402
from src.chat.message_receive.message import SessionMessage  # noqa: E402
from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.common.database.database import get_db_session  # noqa: E402
from src.common.database.database_model import BehaviorSceneTagCluster, ChatSession, Messages  # noqa: E402
from src.common.prompt_i18n import load_prompt  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.learners.behavior_scenario import (  # noqa: E402
    BehaviorScenarioProfile,
    parse_behavior_scenario_segments_response,
)
from src.learners.behavior_scene_cluster_store import (  # noqa: E402
    _distribution_to_mapping,
    _load_tag_cluster_lookup,
    build_scene_cluster_distribution,
)
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


DEFAULT_OUTPUT = "data/analysis/behavior_scene_summary_field_abtest.md"
DEFAULT_JSON_OUTPUT = "data/analysis/behavior_scene_summary_field_abtest.json"
DEFAULT_CACHE = "data/analysis/behavior_scene_summary_field_abtest_cache.json"


@dataclass(frozen=True)
class SourceWindow:
    session_id: str
    display_name: str
    messages: list[SessionMessage]


@dataclass(frozen=True)
class VariantResult:
    variant: str
    profile: BehaviorScenarioProfile | None
    raw_response: str
    cached: bool
    distribution: dict[str, float]


def _split_values(raw_values: Sequence[str]) -> list[str]:
    values: list[str] = []
    for raw_value in raw_values:
        for item in str(raw_value or "").replace("，", ",").split(","):
            value = item.strip()
            if value and value not in values:
                values.append(value)
    return values


def _load_messages_by_session(
    session: Session,
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
    for record in session.exec(statement).all():
        text = " ".join((record.processed_plain_text or "").split()).strip()
        if len(text) < min_text_length:
            continue
        messages.append(SessionMessage.from_db_instance(record))
    return messages


def _discover_session_ids(session: Session, *, min_messages: int) -> list[str]:
    statement = (
        select(Messages.session_id, func.count(Messages.id))
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .group_by(Messages.session_id)
        .having(func.count(Messages.id) >= min_messages)
        .order_by(func.count(Messages.id).desc())
    )
    return [str(row[0]) for row in session.exec(statement).all() if str(row[0] or "").strip()]


def _select_windows(args: Namespace) -> list[SourceWindow]:
    randomizer = Random(args.seed)
    with get_db_session(auto_commit=False) as session:
        session_ids = _split_values([*args.session_id, *args.chat_id])
        if not session_ids:
            session_ids = _discover_session_ids(session, min_messages=args.window_size)

        candidates: list[SourceWindow] = []
        for session_id in session_ids:
            chat_session = session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
            if chat_session is None:
                continue
            messages = _load_messages_by_session(
                session,
                session_id=session_id,
                min_text_length=args.min_text_length,
                limit=args.limit,
            )
            if len(messages) < args.window_size:
                continue
            display_name = _chat_display_name(chat_session)
            step = max(1, args.step)
            for start in range(0, len(messages) - args.window_size + 1, step):
                candidates.append(
                    SourceWindow(
                        session_id=session_id,
                        display_name=display_name,
                        messages=messages[start : start + args.window_size],
                    )
                )

    randomizer.shuffle(candidates)
    return candidates[: args.samples]


def _window_hash(messages: Sequence[SessionMessage]) -> str:
    payload = "\n".join(str(message.message_id or "") for message in messages)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_no_summary_prompt(prompt: str) -> str:
    no_summary_prompt = prompt.replace(
        "- summary 要写成可复用的场景摘要，不要写一次性细节。",
        "- 不要输出 summary 字段；片段 title 只用于列表标题，不参与 tag 获取。",
    )
    no_summary_prompt = no_summary_prompt.replace(
        '        "summary": "当前片段的一句话可复用摘要",\n',
        "",
    )
    no_summary_prompt = no_summary_prompt.replace(
        "- 不要输出 phase、risk、kind、domain_tags、behavior_need、cluster_key、display_name 或其他字段。",
        "- 不要输出 summary、phase、risk、kind、domain_tags、behavior_need、cluster_key、display_name 或其他字段。",
    )
    return no_summary_prompt


def _prompt_for_variant(variant: str) -> str:
    prompt = load_prompt("behavior_scene_analyze", bot_name=global_config.bot.nickname)
    if variant == "without_summary":
        return _build_no_summary_prompt(prompt)
    return prompt


def _request_messages(messages: Sequence[SessionMessage], *, prompt: str) -> list[Any]:
    request_messages = [MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build()]
    for index, message in enumerate(messages, start=1):
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        request_messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content(
                "\n".join(
                    [
                        f"[source_id:{index}]",
                        "[speaker:USER]",
                        f"[name:{speaker_name}]",
                        f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                        "[content]",
                        str(message.processed_plain_text or "[空消息]"),
                    ]
                )
            )
            .build()
        )
    request_messages.append(MessageBuilder().set_role(RoleType.User).add_text_content("请根据以上聊天消息输出 JSON。").build())
    return request_messages


def _parse_first_profile(raw_response: str) -> BehaviorScenarioProfile | None:
    segments = parse_behavior_scenario_segments_response(raw_response)
    if not segments:
        return None
    profile = segments[0].profile
    return profile if profile.tag_clusters else None


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        parsed = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


async def _analyze_variant(
    window: SourceWindow,
    *,
    variant: str,
    client: LLMServiceClient,
    temperature: float,
    cache: dict[str, Any],
    cache_path: Path,
    tag_lookup: dict[tuple[str, str], str],
) -> VariantResult:
    prompt = _prompt_for_variant(variant)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    cache_key = f"{_window_hash(window.messages)}:{variant}:{prompt_hash}:t{temperature}"
    cached_item = cache.get(cache_key)
    cached = isinstance(cached_item, dict) and isinstance(cached_item.get("raw_response"), str)
    if cached:
        raw_response = str(cached_item.get("raw_response") or "")
    else:
        result = await client.generate_response_with_messages(
            lambda _client: _request_messages(window.messages, prompt=prompt),
            options=LLMGenerationOptions(temperature=temperature),
        )
        raw_response = result.response or ""
        cache[cache_key] = {
            "variant": variant,
            "session_id": window.session_id,
            "message_ids": [str(message.message_id or "") for message in window.messages],
            "raw_response": raw_response,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save_cache(cache_path, cache)

    profile = _parse_first_profile(raw_response)
    distribution = _profile_distribution(profile, tag_lookup=tag_lookup) if profile is not None else {}
    return VariantResult(
        variant=variant,
        profile=profile,
        raw_response=raw_response,
        cached=cached,
        distribution=distribution,
    )


def _profile_distribution(
    profile: BehaviorScenarioProfile,
    *,
    tag_lookup: dict[tuple[str, str], str],
) -> dict[str, float]:
    distribution = build_scene_cluster_distribution(profile, tag_lookup=tag_lookup)
    return _distribution_to_mapping(distribution, tag_lookup=tag_lookup)


def _tags_by_kind(profile: BehaviorScenarioProfile | None, kind: str) -> list[str]:
    if profile is None:
        return []
    values: list[str] = []
    for cluster in profile.tag_clusters:
        if cluster.kind != kind:
            continue
        for value in cluster.all_values():
            if value not in values:
                values.append(value)
    return values


def _cluster_names(profile: BehaviorScenarioProfile | None, kind: str) -> list[str]:
    if profile is None:
        return []
    names: list[str] = []
    for cluster in profile.tag_clusters:
        if cluster.kind != kind:
            continue
        values = cluster.all_values()
        if values:
            names.append(values[0])
    return names


def _all_tag_values(profile: BehaviorScenarioProfile | None) -> set[str]:
    if profile is None:
        return set()
    values: set[str] = set()
    for cluster in profile.tag_clusters:
        values.update(cluster.all_values())
    return values


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return round(len(left & right) / len(union), 4)


def _distribution_overlap(left: dict[str, float], right: dict[str, float]) -> float:
    shared = set(left) & set(right)
    return round(sum(min(float(left[tag]), float(right[tag])) for tag in shared), 4)


def _format_distribution(distribution: dict[str, float], *, label_by_tag: dict[str, str], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "tag": tag,
            "label": label_by_tag.get(tag, tag),
            "probability": round(float(probability), 4),
        }
        for tag, probability in sorted(distribution.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _label_by_tag() -> dict[str, str]:
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(select(BehaviorSceneTagCluster)).all()
    labels: dict[str, str] = {}
    for row in rows:
        key = f"{row.tag_kind}:{row.cluster_key}"
        labels.setdefault(key, row.tag)
    return labels


def _compare_results(with_summary: VariantResult, without_summary: VariantResult) -> dict[str, Any]:
    with_profile = with_summary.profile
    without_profile = without_summary.profile
    with_values = _all_tag_values(with_profile)
    without_values = _all_tag_values(without_profile)
    with_distribution_keys = set(with_summary.distribution)
    without_distribution_keys = set(without_summary.distribution)
    return {
        "parse_ok": {
            "with_summary": with_profile is not None,
            "without_summary": without_profile is not None,
        },
        "summary_text": with_profile.summary if with_profile is not None else "",
        "without_summary_returned_summary": without_profile.summary if without_profile is not None else "",
        "tag_value_jaccard": _jaccard(with_values, without_values),
        "distribution_key_jaccard": _jaccard(with_distribution_keys, without_distribution_keys),
        "distribution_overlap": _distribution_overlap(with_summary.distribution, without_summary.distribution),
        "domain_name_jaccard": _jaccard(
            set(_cluster_names(with_profile, "domain")),
            set(_cluster_names(without_profile, "domain")),
        ),
        "need_name_jaccard": _jaccard(
            set(_cluster_names(with_profile, "need")),
            set(_cluster_names(without_profile, "need")),
        ),
        "attitude_name_jaccard": _jaccard(
            set(_cluster_names(with_profile, "attitude")),
            set(_cluster_names(without_profile, "attitude")),
        ),
        "with_counts": _profile_counts(with_profile),
        "without_counts": _profile_counts(without_profile),
        "added_values_without_summary": sorted(without_values - with_values)[:12],
        "removed_values_without_summary": sorted(with_values - without_values)[:12],
    }


def _profile_counts(profile: BehaviorScenarioProfile | None) -> dict[str, int]:
    if profile is None:
        return {"domain_clusters": 0, "need_clusters": 0, "attitude_clusters": 0, "all_values": 0}
    counter: Counter[str] = Counter(cluster.kind for cluster in profile.tag_clusters)
    return {
        "domain_clusters": counter.get("domain", 0),
        "need_clusters": counter.get("need", 0),
        "attitude_clusters": counter.get("attitude", 0),
        "all_values": len(_all_tag_values(profile)),
    }


def _message_payload(messages: Sequence[SessionMessage]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for index, message in enumerate(messages, start=1):
        user_info = message.message_info.user_info
        speaker_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        payload.append(
            {
                "source_id": str(index),
                "time": message.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "speaker": str(speaker_name),
                "text": " ".join(str(message.processed_plain_text or "").split())[:220],
            }
        )
    return payload


def _profile_payload(profile: BehaviorScenarioProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"summary": "", "tag_clusters": [], "need": {}, "other_traits": []}
    return {
        "summary": profile.summary,
        "tag_clusters": [cluster.to_prompt_payload() for cluster in profile.tag_clusters if cluster.kind == "domain"],
        "need": next((cluster.to_prompt_payload() for cluster in profile.tag_clusters if cluster.kind == "need"), {}),
        "other_traits": [cluster.to_prompt_payload() for cluster in profile.tag_clusters if cluster.kind == "attitude"],
    }


async def build_report(args: Namespace) -> dict[str, Any]:
    cache_path = PROJECT_ROOT / args.cache
    cache = _load_cache(cache_path)
    windows = _select_windows(args)
    client = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
    label_by_tag = _label_by_tag()
    with get_db_session(auto_commit=False) as session:
        tag_lookup = _load_tag_cluster_lookup(session)

    samples: list[dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        with_summary = await _analyze_variant(
            window,
            variant="with_summary",
            client=client,
            temperature=args.temperature,
            cache=cache,
            cache_path=cache_path,
            tag_lookup=tag_lookup,
        )
        without_summary = await _analyze_variant(
            window,
            variant="without_summary",
            client=client,
            temperature=args.temperature,
            cache=cache,
            cache_path=cache_path,
            tag_lookup=tag_lookup,
        )
        compare = _compare_results(with_summary, without_summary)
        samples.append(
            {
                "index": index,
                "session_id": window.session_id,
                "chat_name": window.display_name,
                "message_count": len(window.messages),
                "time_range": {
                    "start": window.messages[0].timestamp.isoformat(timespec="seconds"),
                    "end": window.messages[-1].timestamp.isoformat(timespec="seconds"),
                },
                "context": _message_payload(window.messages),
                "with_summary": {
                    "cached": with_summary.cached,
                    "profile": _profile_payload(with_summary.profile),
                    "distribution": _format_distribution(with_summary.distribution, label_by_tag=label_by_tag),
                },
                "without_summary": {
                    "cached": without_summary.cached,
                    "profile": _profile_payload(without_summary.profile),
                    "distribution": _format_distribution(without_summary.distribution, label_by_tag=label_by_tag),
                },
                "compare": compare,
            }
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requested_samples": args.samples,
        "sample_count": len(samples),
        "window_size": args.window_size,
        "temperature": args.temperature,
        "seed": args.seed,
        "cache": str(cache_path),
        "summary": _summarize(samples),
        "samples": samples,
    }


def _avg(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / float(len(values)), 4)


def _summarize(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {}
    compares = [sample["compare"] for sample in samples]
    parse_with = sum(1 for item in compares if item["parse_ok"]["with_summary"])
    parse_without = sum(1 for item in compares if item["parse_ok"]["without_summary"])
    return {
        "parse_ok_with_summary": parse_with,
        "parse_ok_without_summary": parse_without,
        "avg_tag_value_jaccard": _avg([float(item["tag_value_jaccard"]) for item in compares]),
        "avg_distribution_key_jaccard": _avg([float(item["distribution_key_jaccard"]) for item in compares]),
        "avg_distribution_overlap": _avg([float(item["distribution_overlap"]) for item in compares]),
        "avg_domain_name_jaccard": _avg([float(item["domain_name_jaccard"]) for item in compares]),
        "avg_need_name_jaccard": _avg([float(item["need_name_jaccard"]) for item in compares]),
        "avg_attitude_name_jaccard": _avg([float(item["attitude_name_jaccard"]) for item in compares]),
        "exact_distribution_key_same_count": sum(1 for item in compares if item["distribution_key_jaccard"] == 1.0),
        "high_overlap_count": sum(1 for item in compares if item["distribution_overlap"] >= 0.8),
        "returned_summary_when_forbidden_count": sum(1 for item in compares if item["without_summary_returned_summary"]),
    }


def _tag_list_line(items: Sequence[dict[str, Any]]) -> str:
    values = []
    for item in items:
        tag_name = item.get("tag_name") or ""
        aliases = item.get("tag_aliases") or []
        if aliases:
            values.append(f"{tag_name}({', '.join(str(alias) for alias in aliases[:3])})")
        elif tag_name:
            values.append(str(tag_name))
    return "；".join(values) if values else "-"


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# 行为场景 summary 字段 AB 测试")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 样本数：{report['sample_count']} / 请求 {report['requested_samples']}")
    lines.append(f"- 窗口大小：{report['window_size']}")
    lines.append(f"- temperature：{report['temperature']}")
    lines.append(f"- seed：`{report['seed']}`")
    lines.append(f"- 缓存：`{report['cache']}`")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 解析成功：with_summary={report['summary'].get('parse_ok_with_summary')}，without_summary={report['summary'].get('parse_ok_without_summary')}")
    lines.append(f"- tag 值平均 Jaccard：{report['summary'].get('avg_tag_value_jaccard')}")
    lines.append(f"- 分布 key 平均 Jaccard：{report['summary'].get('avg_distribution_key_jaccard')}")
    lines.append(f"- 分布概率平均 overlap：{report['summary'].get('avg_distribution_overlap')}")
    lines.append(f"- domain 主名平均 Jaccard：{report['summary'].get('avg_domain_name_jaccard')}")
    lines.append(f"- need 主名平均 Jaccard：{report['summary'].get('avg_need_name_jaccard')}")
    lines.append(f"- attitude 主名平均 Jaccard：{report['summary'].get('avg_attitude_name_jaccard')}")
    lines.append(f"- 分布 key 完全相同样本：{report['summary'].get('exact_distribution_key_same_count')} / {report['sample_count']}")
    lines.append(f"- 分布 overlap >= 0.8 样本：{report['summary'].get('high_overlap_count')} / {report['sample_count']}")
    lines.append(f"- 禁止 summary 后仍返回 summary：{report['summary'].get('returned_summary_when_forbidden_count')} / {report['sample_count']}")
    lines.append("")

    for sample in report["samples"]:
        compare = sample["compare"]
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- 时间：{sample['time_range']['start']} ~ {sample['time_range']['end']}")
        lines.append(f"- tag_jaccard={compare['tag_value_jaccard']}，distribution_jaccard={compare['distribution_key_jaccard']}，overlap={compare['distribution_overlap']}")
        lines.append(f"- with counts：`{json.dumps(compare['with_counts'], ensure_ascii=False)}`")
        lines.append(f"- without counts：`{json.dumps(compare['without_counts'], ensure_ascii=False)}`")
        lines.append(f"- without 新增：{', '.join(compare['added_values_without_summary']) or '-'}")
        lines.append(f"- without 缺失：{', '.join(compare['removed_values_without_summary']) or '-'}")
        lines.append("")
        lines.append("### with summary")
        with_profile = sample["with_summary"]["profile"]
        lines.append(f"- summary：{with_profile['summary'] or '-'}")
        lines.append(f"- domain：{_tag_list_line(with_profile['tag_clusters'])}")
        lines.append(f"- need：{_tag_list_line([with_profile['need']] if with_profile['need'] else [])}")
        lines.append(f"- other_traits：{_tag_list_line(with_profile['other_traits'])}")
        lines.append("- 分布：" + "；".join(f"{item['label']}={item['probability']}" for item in sample["with_summary"]["distribution"][:8]))
        lines.append("")
        lines.append("### without summary")
        without_profile = sample["without_summary"]["profile"]
        lines.append(f"- 返回 summary：{without_profile['summary'] or '-'}")
        lines.append(f"- domain：{_tag_list_line(without_profile['tag_clusters'])}")
        lines.append(f"- need：{_tag_list_line([without_profile['need']] if without_profile['need'] else [])}")
        lines.append(f"- other_traits：{_tag_list_line(without_profile['other_traits'])}")
        lines.append("- 分布：" + "；".join(f"{item['label']}={item['probability']}" for item in sample["without_summary"]["distribution"][:8]))
        lines.append("")
        lines.append("### 上下文")
        for message in sample["context"][:12]:
            lines.append(f"- `{message['time']}` **{message['speaker']}**：{message['text']}")
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="对比行为场景分析 prompt 输出 summary 与不输出 summary 对 tag 获取的影响。")
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--window-size", type=int, default=30)
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-text-length", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    args = parse_args()
    report = asyncio.run(build_report(args))
    output_path = PROJECT_ROOT / args.output
    json_output_path = PROJECT_ROOT / args.json_output
    write_markdown_report(report, output_path)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Markdown report: {output_path}")
    print(f"JSON report: {json_output_path}")


if __name__ == "__main__":
    main()
