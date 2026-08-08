"""从 planner prompt 日志抽取完整上下文，离线触发黑话学习。"""

# ruff: noqa: E402

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from datetime import datetime
from json import dumps, loads
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.logger import get_logger
from src.config.config import global_config
from src.learners.jargon_learner import (
    FILTERED_TOOL_NAMES,
    JargonLearner,
    JargonLearningSourceItem,
    jargon_learn_model,
)
from src.learners.jargon_miner import JargonMiner
from src.maisaka.context.messages import AssistantMessage, ToolResultMessage
from src.maisaka.jargon_context_matcher import is_jargon_reference_text
from src.prompt.prompt_manager import prompt_manager

logger = get_logger("jargon_planner_log_script")

MESSAGE_ID_PATTERN = re.compile(r'<message\b[^>]*\bmsg_id="([^"]+)"', re.IGNORECASE)
MESSAGE_HEADER_PATTERN = re.compile(r"^<message\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL)
MESSAGE_ATTR_PATTERN = re.compile(r'\b(?P<name>[a-zA-Z_][\w:-]*)="(?P<value>[^"]*)"')
PLANNER_RECORD_PATH = PROJECT_ROOT / "logs" / "maisaka_prompt" / "jargon_offline_learning_records" / "planners.jsonl"


@dataclass(frozen=True)
class PlannerCandidate:
    """一次离线学习使用的 planner 日志。"""

    chat_id: str
    planner_path: Path
    planner_timestamp: datetime
    index_in_chat: int
    message_ids: set[str]
    source_items: List[JargonLearningSourceItem]


def _configure_stdout() -> None:
    """让 Windows 控制台输出遇到 emoji 时不打断脚本。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _planner_timestamp_from_path(path: Path) -> datetime:
    timestamp_ms = int(path.stem)
    return datetime.fromtimestamp(timestamp_ms / 1000)


def _message_text(message: Dict[str, Any]) -> str:
    text = message.get("content_text")
    if isinstance(text, str):
        return text
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


def _parse_message_attrs(text: str) -> dict[str, str]:
    match = MESSAGE_HEADER_PATTERN.match(text.strip())
    if not match:
        return {}
    return {attr.group("name"): attr.group("value") for attr in MESSAGE_ATTR_PATTERN.finditer(match.group("attrs"))}


def _message_body(text: str) -> str:
    return MESSAGE_HEADER_PATTERN.sub("", text.strip(), count=1).strip()


def _is_sticker_message(text: str) -> bool:
    body = _message_body(text)
    if not body:
        return False
    return body.startswith("[表情包") or body.startswith("[消息类型]表情包")


def _is_message_summary(text: str) -> bool:
    body = _message_body(text)
    return body.startswith("[消息类型]复杂消息") or body.startswith("聊天记录摘要")


def _is_allowed_planner_user_message(text: str) -> bool:
    content = text.strip()
    if not content.startswith("<message"):
        return False

    attrs = _parse_message_attrs(content)
    if not attrs.get("msg_id") or not attrs.get("time") or not attrs.get("user"):
        return False
    return not _is_message_summary(content)


def _is_person_profile_content(text: str) -> bool:
    return "【人物画像-内部参考】" in text or "query_person_profile" in text


def _extract_message_ids(text: str) -> set[str]:
    return {match.group(1).strip() for match in MESSAGE_ID_PATTERN.finditer(text) if match.group(1).strip()}


def _build_assistant_content(raw_message: Dict[str, Any], timestamp: datetime, source_kind: str) -> str:
    assistant_message = AssistantMessage(
        content=_message_text(raw_message).strip(),
        timestamp=timestamp,
        source_kind=source_kind,
    )
    return JargonLearner._render_assistant_context_text(assistant_message)


def _build_tool_result_content(raw_message: Dict[str, Any], timestamp: datetime) -> str:
    tool_call_id = raw_message.get("tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        tool_call_id = "planner_tool_result"
    tool_name = raw_message.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip() in FILTERED_TOOL_NAMES:
        return ""

    raw_content = _message_text(raw_message).strip()
    if _is_person_profile_content(raw_content):
        return ""

    tool_result_message = ToolResultMessage(
        content=raw_content,
        timestamp=timestamp,
        tool_call_id=tool_call_id.strip(),
        tool_name=tool_name.strip() if isinstance(tool_name, str) else "",
        success=True,
    )
    return JargonLearner._render_tool_result_context_text(tool_result_message)


def _build_user_source_item(text: str, timestamp: datetime) -> Optional[JargonLearningSourceItem]:
    if not _is_allowed_planner_user_message(text):
        return None
    if _is_sticker_message(text):
        return None
    if is_jargon_reference_text(text):
        return None
    if _is_person_profile_content(text):
        return None

    content = text.strip()
    if not content:
        return None

    attrs = _parse_message_attrs(content)
    speaker_name = attrs.get("user", "未知用户").strip() or "未知用户"
    speaker_kind = "ASSISTANT" if speaker_name == global_config.bot.nickname else "USER"
    source_kind = "planner_assistant_visible" if speaker_kind == "ASSISTANT" else "planner_user"
    return JargonLearningSourceItem(
        source_kind=source_kind,
        speaker_kind=speaker_kind,
        speaker_name=speaker_name,
        content=content,
        timestamp=timestamp,
    )


def build_planner_candidate(chat_id: str, planner_path: Path, index_in_chat: int) -> Optional[PlannerCandidate]:
    """把单个 planner 日志转换为线上学习器可消费的素材。"""

    raw_data = loads(planner_path.read_text(encoding="utf-8"))
    timestamp = _planner_timestamp_from_path(planner_path)
    message_ids: set[str] = set()
    source_items: List[JargonLearningSourceItem] = []

    raw_messages = raw_data.get("messages")
    if not isinstance(raw_messages, list):
        raise TypeError(f"planner messages 必须是列表: {planner_path}")

    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            raise TypeError(f"planner message 必须是字典: {planner_path}")

        role = raw_message.get("role")
        if role == "system":
            continue

        if role == "user":
            text = _message_text(raw_message)
            source_item = _build_user_source_item(text, timestamp)
            if source_item is not None:
                message_ids.update(_extract_message_ids(source_item.content))
                source_items.append(source_item)
            continue

        if role == "assistant":
            text = _message_text(raw_message)
            if _is_person_profile_content(text):
                continue
            content = _build_assistant_content(raw_message, timestamp, "planner_assistant")
            if content:
                source_items.append(
                    JargonLearningSourceItem(
                        source_kind="planner_assistant",
                        speaker_kind="ASSISTANT",
                        speaker_name=global_config.bot.nickname,
                        content=content,
                        timestamp=timestamp,
                    )
                )
            continue

        if role == "tool":
            text = _message_text(raw_message)
            if _is_person_profile_content(text):
                continue
            content = _build_tool_result_content(raw_message, timestamp)
            if content:
                source_items.append(
                    JargonLearningSourceItem(
                        source_kind="planner_tool_result",
                        speaker_kind="TOOL_RESULT",
                        speaker_name="tool_result",
                        content=content,
                        timestamp=timestamp,
                    )
                )

    if not message_ids or not source_items:
        return None

    return PlannerCandidate(
        chat_id=chat_id,
        planner_path=planner_path,
        planner_timestamp=timestamp,
        index_in_chat=index_in_chat,
        message_ids=message_ids,
        source_items=source_items,
    )


def ensure_planner_record_store() -> None:
    """创建离线 planner 学习本地记录目录。"""

    PLANNER_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_recorded_planners() -> tuple[set[str], set[str]]:
    """读取已经学习过的 planner 文件和消息 ID，用于跨运行去重。"""

    recorded_paths: set[str] = set()
    recorded_message_ids: set[str] = set()
    if not PLANNER_RECORD_PATH.exists():
        return recorded_paths, recorded_message_ids

    for line in PLANNER_RECORD_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = loads(line)
        planner_path = str(payload["planner_path"])
        recorded_paths.add(planner_path)
        message_ids = payload["message_ids"]
        if not isinstance(message_ids, list):
            raise TypeError(f"message_ids_json 必须是列表: planner_path={planner_path}")
        recorded_message_ids.update(str(message_id) for message_id in message_ids)
    return recorded_paths, recorded_message_ids


def record_planner(candidate: PlannerCandidate) -> None:
    """登记已经成功处理过的 planner。"""

    ensure_planner_record_store()
    recorded_paths, _recorded_message_ids = get_recorded_planners()
    if str(candidate.planner_path) in recorded_paths:
        return

    payload = {
        "chat_id": candidate.chat_id,
        "planner_path": str(candidate.planner_path),
        "planner_timestamp": candidate.planner_timestamp.isoformat(sep=" "),
        "planner_index": candidate.index_in_chat,
        "source_item_count": len(candidate.source_items),
        "message_ids": sorted(candidate.message_ids),
    }
    with PLANNER_RECORD_PATH.open("a", encoding="utf-8") as file:
        file.write(dumps(payload, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def get_top_chat_dirs(logs_root: Path, top_chats: int) -> list[tuple[str, Path, int]]:
    """按 planner 文件数量选出最多的 chat_id。"""

    if not logs_root.exists():
        raise FileNotFoundError(f"planner 日志目录不存在: {logs_root}")
    chat_dirs: list[tuple[str, Path, int]] = []
    for path in logs_root.iterdir():
        if not path.is_dir():
            continue
        file_count = sum(1 for _ in path.glob("*.json"))
        if file_count:
            chat_dirs.append((path.name, path, file_count))
    chat_dirs.sort(key=lambda item: item[2], reverse=True)
    return chat_dirs[:top_chats]


def select_balanced_planners(args: Namespace) -> List[PlannerCandidate]:
    """从 planner 最多的 chat_id 中轮询抽取互不重叠的 planner。"""

    logs_root = Path(args.logs_root)
    top_chat_dirs = get_top_chat_dirs(logs_root, args.top_chats)
    if not top_chat_dirs:
        return []

    recorded_paths, recorded_message_ids = get_recorded_planners()
    candidates_by_chat: dict[str, List[PlannerCandidate]] = {}

    for chat_id, chat_dir, file_count in top_chat_dirs:
        skip_counts = {"recorded": 0, "overlap": 0, "invalid": 0}
        candidates: List[PlannerCandidate] = []
        used_message_ids_in_chat = set(recorded_message_ids)
        planner_paths = sorted(chat_dir.glob("*.json"), key=lambda path: int(path.stem))

        for index_in_chat, planner_path in enumerate(planner_paths):
            planner_path_key = str(planner_path)
            if planner_path_key in recorded_paths:
                skip_counts["recorded"] += 1
                continue

            try:
                candidate = build_planner_candidate(chat_id, planner_path, index_in_chat)
            except Exception as exc:
                skip_counts["invalid"] += 1
                logger.warning(f"planner 解析失败，已跳过: path={planner_path}, error={exc}")
                continue
            if candidate is None:
                skip_counts["invalid"] += 1
                continue
            if candidate.message_ids & used_message_ids_in_chat:
                skip_counts["overlap"] += 1
                continue

            candidates.append(candidate)
            used_message_ids_in_chat.update(candidate.message_ids)

        candidates_by_chat[chat_id] = candidates
        print(
            f"候选聊天: {chat_id} planner文件={file_count} 可用={len(candidates)} "
            f"已记录={skip_counts['recorded']} 重叠={skip_counts['overlap']} 无效={skip_counts['invalid']}"
        )

    selected_planners: List[PlannerCandidate] = []
    selected_message_ids = set(recorded_message_ids)
    cursors_by_chat = {chat_id: 0 for chat_id, _chat_dir, _file_count in top_chat_dirs}
    skipped_selected_overlap_count = 0

    while len(selected_planners) < args.limit:
        had_candidate_in_round = False
        for chat_id, _chat_dir, _file_count in top_chat_dirs:
            candidates = candidates_by_chat.get(chat_id, [])
            cursor = cursors_by_chat[chat_id]
            while cursor < len(candidates) and candidates[cursor].message_ids & selected_message_ids:
                skipped_selected_overlap_count += 1
                cursor += 1
            cursors_by_chat[chat_id] = cursor

            if cursor >= len(candidates):
                continue
            had_candidate_in_round = True
            candidate = candidates[cursor]
            selected_planners.append(candidate)
            selected_message_ids.update(candidate.message_ids)
            cursors_by_chat[chat_id] = cursor + 1
            if len(selected_planners) >= args.limit:
                break
        if not had_candidate_in_round:
            break

    if skipped_selected_overlap_count:
        print(f"本轮额外跳过跨聊天重叠 planner={skipped_selected_overlap_count}")
    return selected_planners


async def run_learning(planners: List[PlannerCandidate]) -> None:
    """顺序执行离线黑话学习，并直接写入运行库。"""

    for index, planner in enumerate(planners, start=1):
        learner = JargonLearner(session_id=planner.chat_id)
        miner = JargonMiner(session_id=planner.chat_id, session_name=planner.chat_id)
        print(
            f"[{index}/{len(planners)}] 学习 chat_id={planner.chat_id} "
            f"planner序号={planner.index_in_chat + 1} source_items={len(planner.source_items)} "
            f"msg_id数={len(planner.message_ids)} 文件={planner.planner_path.name}"
        )
        try:
            learned = await learner._run_learning_batch(
                planner.source_items,
                learning_session_id=planner.chat_id,
                jargon_miner=miner,
            )
        except Exception as exc:
            logger.error(f"planner 黑话学习失败: chat_id={planner.chat_id}, path={planner.planner_path}, error={exc}")
            continue
        record_planner(planner)
        print(f"    结果: {'写入或更新了黑话' if learned else '没有可学习黑话'}")


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="从 planner prompt 日志抽取完整上下文，执行黑话学习并写回运行库。")
    parser.add_argument("--limit", type=int, default=20, help="最多抽取多少个 planner，默认 20。")
    parser.add_argument("--top-chats", type=int, default=5, help="选择 planner 数量最多的多少个 chat_id，默认 5。")
    parser.add_argument(
        "--logs-root",
        default=str(PROJECT_ROOT / "logs" / "maisaka_prompt" / "planner"),
        help="planner 日志根目录。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印抽样结果，不执行学习、不写库。")
    return parser


async def async_main(args: Namespace) -> int:
    if args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    if args.top_chats <= 0:
        raise ValueError("--top-chats 必须大于 0")

    _configure_stdout()
    print(
        f"抽样配置: top_chats={args.top_chats}, limit={args.limit}, "
        f"dry_run={args.dry_run}, logs_root={args.logs_root}"
    )
    print(f"当前 bot 昵称: {global_config.bot.nickname}")
    print(f"黑话学习任务: task_name={jargon_learn_model.task_name}, request_type={jargon_learn_model.request_type}")
    ensure_planner_record_store()
    prompt_manager.load_prompts()

    planners = select_balanced_planners(args)
    if not planners:
        print("没有抽取到可用 planner。")
        return 1

    print(f"已抽取 {len(planners)} 个 planner。")
    for index, planner in enumerate(planners, start=1):
        first_message_id = sorted(planner.message_ids)[0]
        print(
            f"  #{index}: chat_id={planner.chat_id} planner序号={planner.index_in_chat + 1} "
            f"source_items={len(planner.source_items)} msg_id数={len(planner.message_ids)} "
            f"首个msg_id={first_message_id} 文件={planner.planner_path.name}"
        )

    if args.dry_run:
        return 0

    await run_learning(planners)
    return 0


def main() -> int:
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
