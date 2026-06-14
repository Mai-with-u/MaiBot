from argparse import ArgumentParser, Namespace
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from random import Random
from typing import Dict, List, Sequence

import asyncio
import json
import sys

from sqlmodel import col, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.compare_behavior_scene_matching import compare_matching  # noqa: E402
from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.common.database.database import get_db_session  # noqa: E402
from src.common.database.database_model import ChatSession, Messages  # noqa: E402
from src.common.prompt_i18n import load_prompt  # noqa: E402
from src.config.config import global_config  # noqa: E402
from src.learners.behavior_scenario import parse_behavior_scenario_segments_response  # noqa: E402
from src.llm_models.payload_content.message import MessageBuilder, RoleType  # noqa: E402
from src.services.llm_service import LLMServiceClient  # noqa: E402


@dataclass(frozen=True)
class MessageSample:
    message_id: str
    timestamp: datetime
    session_id: str
    chat_name: str
    speaker: str
    text: str


def _chat_display_name(session: ChatSession | None, session_id: str) -> str:
    if session is None:
        return session_id
    if session.group_name:
        return session.group_name
    if session.user_nickname:
        return f"{session.user_nickname} 的私聊"
    return session.session_id


def _clean_text(text: str, *, max_length: int = 160) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip() + "..."


def _load_recent_messages(days: int, *, min_text_length: int = 4) -> Dict[str, List[MessageSample]]:
    cutoff = datetime.now() - timedelta(days=days)
    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(Messages)
            .where(Messages.timestamp >= cutoff)
            .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
            .order_by(Messages.timestamp.asc())  # type: ignore[attr-defined]
        ).all()
        session_ids = {row.session_id for row in rows if row.session_id}
        chat_sessions = {}
        if session_ids:
            chat_sessions = {
                chat.session_id: chat
                for chat in session.exec(select(ChatSession).where(col(ChatSession.session_id).in_(session_ids))).all()
            }

    grouped_messages: Dict[str, List[MessageSample]] = defaultdict(list)
    for row in rows:
        text = _clean_text(row.processed_plain_text or "", max_length=220)
        if len(text) < min_text_length:
            continue
        grouped_messages[row.session_id].append(
            MessageSample(
                message_id=row.message_id,
                timestamp=row.timestamp,
                session_id=row.session_id,
                chat_name=_chat_display_name(chat_sessions.get(row.session_id), row.session_id),
                speaker=row.user_cardname or row.user_nickname or row.user_id,
                text=text,
            )
        )
    return dict(grouped_messages)


def _sample_windows(
    grouped_messages: Dict[str, List[MessageSample]],
    *,
    sample_count: int,
    window_size: int,
    seed: int,
    max_gap_minutes: int,
) -> List[List[MessageSample]]:
    random = Random(seed)
    candidates: List[List[MessageSample]] = []
    max_gap = timedelta(minutes=max_gap_minutes)
    for messages in grouped_messages.values():
        chunk: List[MessageSample] = []
        for message in messages:
            if chunk and message.timestamp - chunk[-1].timestamp > max_gap:
                if len(chunk) >= max(3, min(window_size, 6)):
                    candidates.append(chunk)
                chunk = []
            chunk.append(message)
        if len(chunk) >= max(3, min(window_size, 6)):
            candidates.append(chunk)

    random.shuffle(candidates)
    windows: List[List[MessageSample]] = []
    for messages in candidates:
        if len(windows) >= sample_count:
            break
        upper_bound = max(0, len(messages) - window_size)
        start = random.randint(0, upper_bound) if upper_bound > 0 else 0
        window = messages[start : start + window_size]
        if len(window) >= 3:
            windows.append(window)
    return windows


async def _analyze_profile_with_llm(
    window: Sequence[MessageSample],
    *,
    client: LLMServiceClient,
) -> tuple[dict[str, object] | None, str]:
    prompt = load_prompt("behavior_scene_analyze", bot_name=global_config.bot.nickname)
    messages = [MessageBuilder().set_role(RoleType.System).add_text_content(prompt).build()]
    for index, message in enumerate(window, start=1):
        messages.append(
            MessageBuilder()
            .set_role(RoleType.User)
            .add_text_content(
                "\n".join(
                    [
                        f"[source_id:{index}]",
                        "[speaker:USER]",
                        f"[name:{message.speaker}]",
                        f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                        "[content]",
                        message.text or "[空消息]",
                    ]
                )
            )
            .build()
        )
    messages.append(
        MessageBuilder()
        .set_role(RoleType.User)
        .add_text_content("请根据以上聊天消息输出 JSON。")
        .build()
    )
    result = await client.generate_response_with_messages(
        lambda _client: messages,
        options=LLMGenerationOptions(temperature=0.2),
    )
    raw_response = result.response or ""
    segments = parse_behavior_scenario_segments_response(raw_response)
    if not segments:
        return None, raw_response
    profile = segments[0].profile
    if not profile.tag_clusters:
        return None, raw_response
    return {
        "summary": profile.summary,
        "tag_clusters": profile.domain_prompt_payloads(),
        "need": profile.need_prompt_payload(),
        "other_traits": profile.other_traits_prompt_payloads(),
        "confidence": profile.confidence,
    }, raw_response


def _namespace_for_compare(window: Sequence[MessageSample], profile: dict[str, object], args: Namespace) -> Namespace:
    return Namespace(
        session_id=[] if args.all_sessions else [window[0].session_id],
        include_global=args.all_sessions or args.include_global,
        profile_json=json.dumps(profile, ensure_ascii=False),
        max_count=args.max_count,
        retrieval_mode=args.retrieval_mode,
        json=False,
    )


def _message_payload(window: Sequence[MessageSample]) -> List[dict[str, str]]:
    return [
        {
            "time": message.timestamp.isoformat(timespec="seconds"),
            "speaker": message.speaker,
            "text": message.text,
        }
        for message in window
    ]


def _brief_candidates(candidates: Sequence[dict[str, object]]) -> List[dict[str, object]]:
    return [
        {
            "id": candidate.get("id"),
            "score": candidate.get("score"),
            "cluster_id": candidate.get("cluster_id"),
            "action": candidate.get("action"),
            "outcome": candidate.get("outcome"),
        }
        for candidate in candidates
    ]


async def build_report(args: Namespace) -> dict[str, object]:
    grouped_messages = _load_recent_messages(args.days)
    windows = _sample_windows(
        grouped_messages,
        sample_count=args.samples,
        window_size=args.window_size,
        seed=args.seed,
        max_gap_minutes=args.max_gap_minutes,
    )
    samples = []
    llm_client = LLMServiceClient(task_name="learner", request_type="behavior.scene_analyzer")
    for index, window in enumerate(windows, start=1):
        raw_llm_response = ""
        profile, raw_llm_response = await _analyze_profile_with_llm(window, client=llm_client)
        if profile is None:
            continue
        compare_result = compare_matching(_namespace_for_compare(window, profile, args))
        samples.append(
            {
                "index": index,
                "session_id": window[0].session_id,
                "chat_name": window[0].chat_name,
                "time_range": {
                    "start": window[0].timestamp.isoformat(timespec="seconds"),
                    "end": window[-1].timestamp.isoformat(timespec="seconds"),
                },
                "context": _message_payload(window),
                "profile": compare_result["profile"],
                "raw_llm_response": raw_llm_response,
                "scene_cluster": {
                    "retrieval_mode": compare_result["scene_cluster"]["retrieval_mode"],
                    "debug": compare_result["scene_cluster"]["debug"],
                    "matched_clusters": compare_result["scene_cluster"]["matched_clusters"],
                    "candidates": _brief_candidates(compare_result["scene_cluster"]["behavior_candidates"]),
                },
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": args.days,
        "sample_count": len(samples),
        "window_size": args.window_size,
        "scope": "all_sessions" if args.all_sessions else "sample_session_only",
        "profile_source": "online_llm_behavior_scene_analyzer",
        "note": (
            "profile 来自线上 LLM 场景分析器；匹配范围为所有 session_id。"
            if args.all_sessions
            else "profile 来自线上 LLM 场景分析器；匹配范围为样本所在 session_id。"
        ),
        "samples": samples,
    }


def write_markdown_report(report: dict[str, object], output_path: Path) -> None:
    lines: List[str] = []
    lines.append("# 行为场景匹配抽样报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 抽样范围：近 {report['days']} 天")
    lines.append(f"- 样本数：{report['sample_count']}")
    lines.append(f"- 画像来源：{report['profile_source']}")
    lines.append(f"- 匹配范围：{report['scope']}")
    lines.append(f"- 说明：{report['note']}")
    lines.append("")
    for sample in report["samples"]:  # type: ignore[index]
        lines.append(f"## 样本 {sample['index']}：{sample['chat_name']}")
        lines.append("")
        lines.append(f"- session_id：`{sample['session_id']}`")
        lines.append(f"- 时间范围：{sample['time_range']['start']} ~ {sample['time_range']['end']}")
        lines.append("")
        lines.append("### 上下文")
        for message in sample["context"]:
            lines.append(f"- `{message['time']}` **{message['speaker']}**：{message['text']}")
        lines.append("")
        profile = sample["profile"]
        lines.append("### 画像")
        lines.append(f"- summary：{profile['summary']}")
        if profile.get("tag_clusters"):
            lines.append("- tag_clusters：")
            for cluster in profile["tag_clusters"]:
                values = [cluster.get("tag_name") or "", *(cluster.get("tag_aliases") or [])]
                lines.append(f"  - {', '.join(str(value) for value in values if value)}")
        if profile.get("need"):
            need = profile["need"]
            values = [need.get("tag_name") or "", *(need.get("tag_aliases") or [])]
            lines.append(f"- need：{', '.join(str(value) for value in values if value)}")
        if profile.get("other_traits"):
            lines.append("- other_traits：")
            for cluster in profile["other_traits"]:
                values = [cluster.get("tag_name") or "", *(cluster.get("tag_aliases") or [])]
                lines.append(f"  - {', '.join(str(value) for value in values if value)}")
        if sample.get("raw_llm_response"):
            lines.append("")
            lines.append("LLM 原始输出：")
            lines.append("```json")
            lines.append(str(sample["raw_llm_response"]).strip())
            lines.append("```")
        lines.append("")
        lines.append("### 检索情况")
        lines.append(f"- 检索模式：`{sample['scene_cluster'].get('retrieval_mode')}`")
        lines.append(
            f"- 调试信息：`{json.dumps(sample['scene_cluster'].get('debug') or {}, ensure_ascii=False)}`"
        )
        lines.append(f"- 场景簇命中数：{len(sample['scene_cluster']['matched_clusters'])}")
        lines.append("场景簇候选：")
        _append_candidate_lines(lines, sample["scene_cluster"]["candidates"])
        lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _append_candidate_lines(lines: List[str], candidates: Sequence[dict[str, object]]) -> None:
    if not candidates:
        lines.append("- 无命中")
        return
    for candidate in candidates:
        lines.append(
            f"- #{candidate.get('id')} score={candidate.get('score')} cluster=#{candidate.get('cluster_id')} "
            f"行为：{candidate.get('action')}；结果：{candidate.get('outcome')}"
        )


def parse_args() -> Namespace:
    parser = ArgumentParser(description="抽取近几天聊天上下文并对比两套行为场景匹配。")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--window-size", type=int, default=8)
    parser.add_argument("--max-gap-minutes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260609)
    parser.add_argument("--max-count", type=int, default=5)
    parser.add_argument(
        "--retrieval-mode",
        choices=["direct_domain_overlap", "tag_cluster_spread_1", "tag_cluster_spread_2"],
        default="tag_cluster_spread_1",
        help="行为场景检索模式，默认与主线一致：一次扩散。",
    )
    parser.add_argument("--include-global", action="store_true")
    parser.add_argument("--all-sessions", action="store_true", help="匹配所有 session_id 的聊天簇/行为数据。")
    parser.add_argument("--output", default="data/analysis/behavior_matching_sample_report.md")
    parser.add_argument("--json-output", default="data/analysis/behavior_matching_sample_report.json")
    return parser.parse_args()


def main() -> None:
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
