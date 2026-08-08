"""从运行库抽取连续消息段，离线触发黑话学习。"""

# ruff: noqa: E402

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from json import dumps, loads
from pathlib import Path
from typing import List

import asyncio
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func
from sqlmodel import col, select

from src.chat.message_receive.message import SessionMessage
from src.common.database.database import get_db_session
from src.common.database.database_model import Messages
from src.common.logger import get_logger
from src.config.config import global_config
from src.learners.jargon_learner import JargonLearner, jargon_learn_model
from src.learners.jargon_miner import JargonMiner
from src.llm_models.payload_content.message import Message, MessageBuilder, RoleType
from src.prompt.prompt_manager import prompt_manager

logger = get_logger("jargon_segment_script")

SEGMENT_RECORD_PATH = PROJECT_ROOT / "logs" / "maisaka_prompt" / "jargon_offline_learning_records" / "message_segments.jsonl"


@dataclass(frozen=True)
class MessageSegment:
    """一次离线学习使用的连续消息段。"""

    session_id: str
    index_in_chat: int
    segment_length: int
    first_message_db_id: int
    last_message_db_id: int
    first_message_id: str
    last_message_id: str
    messages: List[SessionMessage]


class PlainTextJargonLearner(JargonLearner):
    """离线黑话学习器：直接使用数据库中的 processed_plain_text，避免重跑媒体解析。"""

    async def _build_multi_learning_messages(
        self,
        messages: List[SessionMessage],
        system_prompt: str,
    ) -> List[Message]:
        learning_messages = [
            MessageBuilder()
            .set_role(RoleType.System)
            .add_text_content(
                f"{system_prompt}\n\n"
                "注意：聊天记录会在后续多条 user message 中给出。每条消息内的 source_id "
                "是本轮学习的来源编号；speaker=SELF 的消息只作为上下文，不要从 SELF 的发言中学习。"
            )
            .build()
        ]

        for index, message in enumerate(messages, start=1):
            user_info = message.message_info.user_info
            speaker_name = user_info.user_cardname or user_info.user_nickname or "未知用户"
            speaker_kind = "SELF" if self._is_self_message(message) else "USER"
            content = (message.processed_plain_text or "").strip() or "[空消息]"
            learning_messages.append(
                MessageBuilder()
                .set_role(RoleType.User)
                .add_text_content(
                    "\n".join(
                        [
                            f"[source_id:{index}]",
                            f"[speaker:{speaker_kind}]",
                            f"[name:{speaker_name}]",
                            f"[time:{message.timestamp.strftime('%H:%M:%S')}]",
                            "[content]",
                            content,
                        ]
                    )
                )
                .build()
            )

        learning_messages.append(
            MessageBuilder().set_role(RoleType.User).add_text_content("请根据以上聊天消息输出 JSON。").build()
        )
        return learning_messages

    @staticmethod
    def _is_self_message(message: SessionMessage) -> bool:
        from src.chat.utils.utils import is_bot_self

        return is_bot_self(message.platform, message.message_info.user_info.user_id)


def _message_filters() -> list[object]:
    return [
        Messages.message_id != "notice",
        col(Messages.session_id).is_not(None),
        func.trim(col(Messages.session_id)) != "",
        col(Messages.processed_plain_text).is_not(None),
        func.trim(col(Messages.processed_plain_text)) != "",
    ]


def ensure_segment_record_store() -> None:
    """创建离线脚本本地记录目录。"""

    SEGMENT_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)


def _require_message_db_id(message: Messages) -> int:
    if message.id is None:
        raise ValueError(f"消息缺少数据库自增 ID，无法登记段指纹: message_id={message.message_id}")
    return message.id


def _segment_record_key(
    session_id: str,
    segment_length: int,
    db_messages: List[Messages],
) -> tuple[str, int, int, int]:
    if not db_messages:
        raise ValueError("消息段不能为空")
    return (
        session_id,
        segment_length,
        _require_message_db_id(db_messages[0]),
        _require_message_db_id(db_messages[-1]),
    )


def get_recorded_segment_keys(segment_length: int) -> set[tuple[str, int, int, int]]:
    """读取已经成功学习过的消息段指纹。"""

    if not SEGMENT_RECORD_PATH.exists():
        return set()

    recorded_keys: set[tuple[str, int, int, int]] = set()
    for line in SEGMENT_RECORD_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = loads(line)
        if int(payload["segment_length"]) != segment_length:
            continue
        recorded_keys.add(
            (
                str(payload["session_id"]),
                int(payload["segment_length"]),
                int(payload["first_message_db_id"]),
                int(payload["last_message_db_id"]),
            )
        )
    return recorded_keys


def record_segment(segment: MessageSegment) -> None:
    """登记已经成功处理过的消息段。"""

    ensure_segment_record_store()
    segment_key = (
        segment.session_id,
        segment.segment_length,
        segment.first_message_db_id,
        segment.last_message_db_id,
    )
    if segment_key in get_recorded_segment_keys(segment.segment_length):
        return

    payload = {
        "session_id": segment.session_id,
        "segment_length": segment.segment_length,
        "segment_index": segment.index_in_chat,
        "first_message_db_id": segment.first_message_db_id,
        "last_message_db_id": segment.last_message_db_id,
        "first_message_id": segment.first_message_id,
        "last_message_id": segment.last_message_id,
    }
    with SEGMENT_RECORD_PATH.open("a", encoding="utf-8") as file:
        file.write(dumps(payload, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def build_message_segment(session_id: str, index_in_chat: int, db_messages: List[Messages]) -> MessageSegment:
    """把数据库消息段转换为学习器使用的消息段，同时保留段指纹。"""

    if not db_messages:
        raise ValueError("消息段不能为空")
    return MessageSegment(
        session_id=session_id,
        index_in_chat=index_in_chat,
        segment_length=len(db_messages),
        first_message_db_id=_require_message_db_id(db_messages[0]),
        last_message_db_id=_require_message_db_id(db_messages[-1]),
        first_message_id=db_messages[0].message_id,
        last_message_id=db_messages[-1].message_id,
        messages=[SessionMessage.from_db_instance(message) for message in db_messages],
    )


def get_top_session_ids(top_chats: int) -> list[tuple[str, int]]:
    """按可学习文本消息数量选出最多的聊天流。"""

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(Messages.session_id, func.count(Messages.id).label("message_count"))
            .where(*_message_filters())
            .group_by(Messages.session_id)
            .order_by(func.count(Messages.id).desc())
            .limit(top_chats)
        ).all()
    return [(str(session_id), int(message_count or 0)) for session_id, message_count in rows]


def load_session_messages(session_id: str) -> List[Messages]:
    """读取某个聊天流的可学习消息，并按时间升序返回。"""

    with get_db_session(auto_commit=False) as session:
        return list(
            session.exec(
            select(Messages)
            .where(*_message_filters())
            .where(Messages.session_id == session_id)
            .order_by(Messages.timestamp, Messages.id)
            ).all()
        )


def build_non_overlapping_segments(messages: List[Messages], segment_length: int) -> List[List[Messages]]:
    """按固定长度切出连续且互不重叠的消息段。"""

    return [
        messages[start : start + segment_length]
        for start in range(0, len(messages) - segment_length + 1, segment_length)
    ]


def mark_first_rounds_as_recorded(args: Namespace) -> int:
    """补登记每个候选聊天流前 N 个段，用于承接历史手动运行结果。"""

    top_sessions = get_top_session_ids(args.top_chats)
    marked_count = 0
    for session_id, message_count in top_sessions:
        messages = load_session_messages(session_id)
        segments = build_non_overlapping_segments(messages, args.segment_length)
        target_segments = segments[: args.mark_first_rounds]
        print(
            f"补登记聊天: {session_id} 可学习消息={message_count} "
            f"可用段={len(segments)} 补登记段={len(target_segments)}"
        )
        if args.dry_run:
            marked_count += len(target_segments)
            continue
        for index_in_chat, db_messages in enumerate(target_segments):
            record_segment(build_message_segment(session_id, index_in_chat, db_messages))
            marked_count += 1
    return marked_count


def select_balanced_segments(args: Namespace) -> List[MessageSegment]:
    """从消息最多的聊天流中轮询抽取消息段，使 chat_id 尽量均匀分布。"""

    top_sessions = get_top_session_ids(args.top_chats)
    if not top_sessions:
        return []

    recorded_keys = get_recorded_segment_keys(args.segment_length)
    segments_by_session: dict[str, List[List[Messages]]] = {}
    for session_id, message_count in top_sessions:
        messages = load_session_messages(session_id)
        segments = build_non_overlapping_segments(messages, args.segment_length)
        segments_by_session[session_id] = segments
        recorded_count = sum(
            1 for segment in segments if _segment_record_key(session_id, args.segment_length, segment) in recorded_keys
        )
        print(
            f"候选聊天: {session_id} 可学习消息={message_count} "
            f"可用段={len(segments)} 已记录段={recorded_count}"
        )

    selected_segments: List[MessageSegment] = []
    round_index = 0
    skipped_recorded_count = 0
    while len(selected_segments) < args.limit:
        had_candidate_in_round = False
        for session_id, _message_count in top_sessions:
            segments = segments_by_session.get(session_id, [])
            if round_index >= len(segments):
                continue
            had_candidate_in_round = True
            db_messages = segments[round_index]
            if _segment_record_key(session_id, args.segment_length, db_messages) in recorded_keys:
                skipped_recorded_count += 1
                continue
            selected_segments.append(build_message_segment(session_id, round_index, db_messages))
            if len(selected_segments) >= args.limit:
                break
        if not had_candidate_in_round:
            break
        round_index += 1

    print(f"本次抽样跳过已记录段={skipped_recorded_count}")
    return selected_segments


async def run_learning(segments: List[MessageSegment]) -> None:
    """顺序执行离线黑话学习，并直接写入运行库。"""

    for index, segment in enumerate(segments, start=1):
        session_name = segment.session_id
        learner = PlainTextJargonLearner(session_id=segment.session_id)
        miner = JargonMiner(session_id=segment.session_id, session_name=session_name)
        start_time = segment.messages[0].timestamp
        end_time = segment.messages[-1].timestamp
        print(
            f"[{index}/{len(segments)}] 学习 session_id={segment.session_id} "
            f"段序号={segment.index_in_chat + 1} 消息数={len(segment.messages)} "
            f"时间={start_time} -> {end_time}"
        )
        try:
            learned = await learner._run_learning_batch(
                segment.messages,
                learning_session_id=segment.session_id,
                jargon_miner=miner,
            )
        except Exception as exc:
            logger.error(f"离线黑话学习失败: session_id={segment.session_id}, segment={segment.index_in_chat + 1}, error={exc}")
            continue
        record_segment(segment)
        print(f"    结果: {'写入或更新了黑话' if learned else '没有可学习黑话'}")


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="从运行库抽取连续消息段，执行黑话学习并写回运行库。")
    parser.add_argument("--limit", type=int, default=20, help="最多抽取多少个消息段，默认 20。")
    parser.add_argument("--segment-length", type=int, default=30, help="每段连续消息数量，默认 30。")
    parser.add_argument("--top-chats", type=int, default=5, help="选择消息最多的多少个 chat_id/session_id，默认 5。")
    parser.add_argument("--dry-run", action="store_true", help="只打印抽样结果，不执行学习、不写库。")
    parser.add_argument(
        "--mark-first-rounds",
        type=int,
        default=0,
        help="只把每个候选聊天流前 N 个段登记为已学习，用于补登记历史运行结果。",
    )
    return parser


async def async_main(args: Namespace) -> int:
    if args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    if args.segment_length <= 0:
        raise ValueError("--segment-length 必须大于 0")
    if args.top_chats <= 0:
        raise ValueError("--top-chats 必须大于 0")
    if args.mark_first_rounds < 0:
        raise ValueError("--mark-first-rounds 不能小于 0")

    print(
        f"抽样配置: top_chats={args.top_chats}, segment_length={args.segment_length}, "
        f"limit={args.limit}, dry_run={args.dry_run}, mark_first_rounds={args.mark_first_rounds}"
    )
    print(f"当前 bot 昵称: {global_config.bot.nickname}")
    print(f"黑话学习任务: task_name={jargon_learn_model.task_name}, request_type={jargon_learn_model.request_type}")
    ensure_segment_record_store()

    if args.mark_first_rounds:
        marked_count = mark_first_rounds_as_recorded(args)
        print(f"{'预计' if args.dry_run else '已'}补登记 {marked_count} 个消息段。")
        return 0

    prompt_manager.load_prompts()

    segments = select_balanced_segments(args)
    if not segments:
        print("没有抽取到可用消息段。")
        return 1

    print(f"已抽取 {len(segments)} 个消息段。")
    for index, segment in enumerate(segments, start=1):
        print(
            f"  #{index}: session_id={segment.session_id} 段序号={segment.index_in_chat + 1} "
            f"消息数={len(segment.messages)} 起止={segment.messages[0].timestamp} -> {segment.messages[-1].timestamp}"
        )

    if args.dry_run:
        return 0

    await run_learning(segments)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
