from argparse import ArgumentParser, Namespace
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator, Iterable, Optional

import asyncio
import hashlib
import json
import random
import sys
import types

from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session, create_engine, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chat.message_receive.message import SessionMessage  # noqa: E402
from src.common.data_models.llm_service_data_models import LLMGenerationOptions  # noqa: E402
from src.common.database.database_model import (  # noqa: E402
    BehaviorExperiencePath,
    ChatSession,
    Messages,
)
from src.common.database.migrations import create_database_migration_bootstrapper  # noqa: E402
from src.learners.behavior_learner import BehaviorLearner  # noqa: E402


SessionFactory = sessionmaker
SessionProvider = Callable[[bool], Generator[Session, None, None]]
MIN_RANDOM_CHAT_MESSAGES = 200


@dataclass(frozen=True)
class ChatLearningTarget:
    session_id: str
    display_name: str
    messages: list[SessionMessage]


@dataclass(frozen=True)
class LearningWindow:
    session_id: str
    display_name: str
    messages: list[SessionMessage]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=1000")
    cursor.close()


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _window_message_ids(messages: list[SessionMessage]) -> list[str]:
    return [str(message.message_id or "").strip() for message in messages]


def _window_hash(messages: list[SessionMessage]) -> str:
    payload = "\n".join(_window_message_ids(messages))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_progress_event(progress_path: Path, payload: dict) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, default=str))
        file.write("\n")


def _progress_payload(
    *,
    status: str,
    run_id: str,
    index: int,
    learning_window: LearningWindow,
    error: str = "",
    wrote_pattern: Optional[bool] = None,
) -> dict:
    messages = learning_window.messages
    message_ids = _window_message_ids(messages)
    payload = {
        "schema_version": 1,
        "status": status,
        "run_id": run_id,
        "window_index": index,
        "recorded_at": _now_iso(),
        "session_id": learning_window.session_id,
        "chat_name": learning_window.display_name,
        "window_hash": _window_hash(messages),
        "message_count": len(messages),
        "start_message_id": message_ids[0] if message_ids else "",
        "end_message_id": message_ids[-1] if message_ids else "",
        "message_ids": message_ids,
        "start_time": messages[0].timestamp.isoformat(timespec="seconds") if messages else "",
        "end_time": messages[-1].timestamp.isoformat(timespec="seconds") if messages else "",
    }
    if wrote_pattern is not None:
        payload["wrote_pattern"] = wrote_pattern
    if error:
        payload["error"] = error
    return payload


def _load_progress_exclusions(progress_path: Path) -> tuple[set[str], set[str]]:
    if not progress_path.exists():
        return set(), set()

    excluded_window_hashes: set[str] = set()
    excluded_message_ids: set[str] = set()
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("status") not in {"started", "completed"}:
            continue
        window_hash = str(payload.get("window_hash") or "").strip()
        if window_hash:
            excluded_window_hashes.add(window_hash)
        for message_id in payload.get("message_ids") or []:
            normalized_id = str(message_id or "").strip()
            if normalized_id:
                excluded_message_ids.add(normalized_id)
    return excluded_window_hashes, excluded_message_ids


def _is_window_excluded(
    messages: list[SessionMessage],
    *,
    excluded_window_hashes: set[str],
    excluded_message_ids: set[str],
) -> bool:
    if not excluded_window_hashes and not excluded_message_ids:
        return False
    if _window_hash(messages) in excluded_window_hashes:
        return True
    message_ids = set(_window_message_ids(messages))
    return bool(message_ids & excluded_message_ids)


def _build_engine(db_path: Path, *, readonly: bool = False):
    if readonly:
        if not db_path.exists():
            raise FileNotFoundError(f"源数据库不存在: {db_path}")
        database_url = f"sqlite:///file:{db_path.as_posix()}?mode=ro&uri=true"
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{db_path.as_posix()}"
    return create_engine(
        database_url,
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )


def _build_session_provider(session_local: SessionFactory) -> SessionProvider:
    @contextmanager
    def get_session(auto_commit: bool = True) -> Generator[Session, None, None]:
        session = session_local()
        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return get_session


def _initialize_target_database(target_engine) -> None:
    import src.common.database.database_model  # noqa: F401

    migration_bootstrapper = create_database_migration_bootstrapper(target_engine)
    migration_state = migration_bootstrapper.prepare_database()
    SQLModel.metadata.create_all(target_engine)
    migration_bootstrapper.finalize_database(migration_state)


def _patch_behavior_storage(get_target_session: SessionProvider) -> None:
    import src.llm_models.utils as llm_model_utils
    import src.learners.behavior_pattern_maintenance as behavior_pattern_maintenance
    import src.learners.behavior_pattern_store as behavior_pattern_store
    import src.learners.behavior_scene_cluster_store as behavior_scene_cluster_store

    behavior_pattern_store.get_db_session = get_target_session
    behavior_scene_cluster_store.get_db_session = get_target_session
    behavior_pattern_maintenance.get_db_session = get_target_session
    llm_model_utils.get_db_session = get_target_session


def _patch_behavior_model_name(model_name: str) -> None:
    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        return

    import src.learners.behavior_learner as behavior_learner

    def patch_client(client) -> None:
        original_generate = client.generate_response_with_messages

        async def generate_response_with_model(message_factory, options=None):
            active_options = options or LLMGenerationOptions()
            active_options.model_name = normalized_model_name
            return await original_generate(message_factory, options=active_options)

        client.generate_response_with_messages = generate_response_with_model

    patch_client(behavior_learner.behavior_learn_model)
    patch_client(behavior_learner.behavior_scene_model)


def _parse_datetime(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value:
        return None
    normalized_value = raw_value.strip()
    try:
        return datetime.fromisoformat(normalized_value)
    except ValueError as exc:
        raise ValueError(f"无法解析时间 {raw_value!r}，请使用 ISO 格式，例如 2026-06-01T12:00:00") from exc


def _copy_chat_session(source_session: Session, target_session: Session, session_id: str) -> ChatSession:
    source_chat = source_session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
    if source_chat is None:
        raise ValueError(f"源数据库中找不到聊天流: {session_id}")

    target_chat = target_session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
    if target_chat is None:
        target_chat = ChatSession(
            session_id=source_chat.session_id,
            created_timestamp=source_chat.created_timestamp,
            last_active_timestamp=source_chat.last_active_timestamp,
            user_id=source_chat.user_id,
            user_nickname=source_chat.user_nickname,
            user_cardname=source_chat.user_cardname,
            group_id=source_chat.group_id,
            group_name=source_chat.group_name,
            platform=source_chat.platform,
            account_id=source_chat.account_id,
            scope=source_chat.scope,
        )
    else:
        target_chat.last_active_timestamp = source_chat.last_active_timestamp
        target_chat.user_id = source_chat.user_id
        target_chat.user_nickname = source_chat.user_nickname
        target_chat.user_cardname = source_chat.user_cardname
        target_chat.group_id = source_chat.group_id
        target_chat.group_name = source_chat.group_name
        target_chat.platform = source_chat.platform
        target_chat.account_id = source_chat.account_id
        target_chat.scope = source_chat.scope

    target_session.add(target_chat)
    target_session.commit()
    target_session.refresh(target_chat)
    return target_chat


def _requested_session_ids(args: Namespace) -> list[str]:
    session_ids: list[str] = []
    for raw_value in [*args.session_id, *args.chat_id]:
        for item in str(raw_value or "").replace("，", ",").split(","):
            session_id = item.strip()
            if session_id and session_id not in session_ids:
                session_ids.append(session_id)
    return session_ids


def _discover_session_ids(source_session: Session, *, min_messages: int) -> list[str]:
    statement = (
        select(Messages.session_id, func.count(Messages.id))
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .group_by(Messages.session_id)
        .having(func.count(Messages.id) >= min_messages)
        .order_by(func.count(Messages.id).desc())
    )
    rows = source_session.exec(statement).all()
    return [str(row[0]) for row in rows if str(row[0] or "").strip()]


def _load_source_messages(
    source_session: Session,
    *,
    session_id: str,
    since: Optional[datetime],
    until: Optional[datetime],
    limit: int,
    min_text_length: int,
) -> list[SessionMessage]:
    statement = (
        select(Messages)
        .where(Messages.session_id == session_id)
        .where(Messages.processed_plain_text.is_not(None))  # type: ignore[attr-defined]
        .order_by(Messages.timestamp.asc())  # type: ignore[attr-defined]
    )
    if since is not None:
        statement = statement.where(Messages.timestamp >= since)
    if until is not None:
        statement = statement.where(Messages.timestamp <= until)
    if limit > 0:
        statement = statement.limit(limit)

    records = source_session.exec(statement).all()
    messages: list[SessionMessage] = []
    for record in records:
        text = " ".join((record.processed_plain_text or "").split()).strip()
        if len(text) < min_text_length:
            continue
        messages.append(SessionMessage.from_db_instance(record))
    return messages


def _iter_message_windows(
    messages: list[SessionMessage],
    *,
    window_size: int,
    step: int,
) -> Iterable[list[SessionMessage]]:
    if window_size <= 0:
        raise ValueError("--window-size 必须大于 0")
    normalized_step = step if step > 0 else window_size
    for start_index in range(0, len(messages), normalized_step):
        window = messages[start_index : start_index + window_size]
        if len(window) < window_size:
            break
        yield window


def _build_non_overlapping_windows(targets: list[ChatLearningTarget], *, window_size: int) -> list[LearningWindow]:
    windows: list[LearningWindow] = []
    for target in targets:
        for window_messages in _iter_message_windows(target.messages, window_size=window_size, step=window_size):
            windows.append(
                LearningWindow(
                    session_id=target.session_id,
                    display_name=target.display_name,
                    messages=window_messages,
                )
            )
    return windows


def _select_random_windows(
    targets: list[ChatLearningTarget],
    *,
    window_size: int,
    random_count: int,
    seed: int,
    uniform_by_chat: bool,
    balanced_by_chat: bool,
    excluded_window_hashes: set[str],
    excluded_message_ids: set[str],
) -> list[LearningWindow]:
    if random_count <= 0:
        return []
    short_targets = [target for target in targets if len(target.messages) < MIN_RANDOM_CHAT_MESSAGES]
    if short_targets:
        details = ", ".join(
            f"{target.session_id}={len(target.messages)}条" for target in short_targets
        )
        raise ValueError(
            f"随机抽样要求每个被学习 chat_id 至少有 {MIN_RANDOM_CHAT_MESSAGES} 条可学习消息，"
            f"不足的 chat: {details}"
        )

    randomizer = random.Random(seed)
    windows_by_session_id: dict[str, list[LearningWindow]] = {}
    for target in targets:
        windows = [
            LearningWindow(target.session_id, target.display_name, window_messages)
            for window_messages in _iter_message_windows(target.messages, window_size=window_size, step=window_size)
            if not _is_window_excluded(
                window_messages,
                excluded_window_hashes=excluded_window_hashes,
                excluded_message_ids=excluded_message_ids,
            )
        ]
        randomizer.shuffle(windows)
        windows_by_session_id[target.session_id] = windows

    if uniform_by_chat:
        if not targets:
            return []
        per_chat_count = random_count // len(targets)
        if per_chat_count <= 0:
            raise ValueError("--uniform-by-chat 需要 --random-windows 至少等于 chat 数")
        selected_windows: list[LearningWindow] = []
        for target in targets:
            windows = windows_by_session_id[target.session_id]
            if len(windows) < per_chat_count:
                raise ValueError(
                    f"chat_id={target.session_id} 可抽窗口不足: 需要 {per_chat_count}，实际 {len(windows)}"
                )
            selected_windows.extend(windows[:per_chat_count])
        randomizer.shuffle(selected_windows)
        return selected_windows

    if balanced_by_chat:
        selected_windows = []
        session_order = [target.session_id for target in targets]
        randomizer.shuffle(session_order)
        while len(selected_windows) < random_count:
            added_in_round = False
            for session_id in session_order:
                windows = windows_by_session_id[session_id]
                if not windows:
                    continue
                selected_windows.append(windows.pop())
                added_in_round = True
                if len(selected_windows) >= random_count:
                    break
            if not added_in_round:
                break
        randomizer.shuffle(selected_windows)
        return selected_windows

    all_windows = [window for windows in windows_by_session_id.values() for window in windows]
    randomizer.shuffle(all_windows)
    return all_windows[:random_count]


def _select_sequential_windows(
    targets: list[ChatLearningTarget],
    *,
    window_size: int,
    step: int,
    max_batches: int,
    excluded_window_hashes: set[str],
    excluded_message_ids: set[str],
) -> list[LearningWindow]:
    selected_windows: list[LearningWindow] = []
    for target in targets:
        for window_messages in _iter_message_windows(target.messages, window_size=window_size, step=step):
            if _is_window_excluded(
                window_messages,
                excluded_window_hashes=excluded_window_hashes,
                excluded_message_ids=excluded_message_ids,
            ):
                continue
            if max_batches > 0 and len(selected_windows) >= max_batches:
                return selected_windows
            selected_windows.append(
                LearningWindow(
                    session_id=target.session_id,
                    display_name=target.display_name,
                    messages=window_messages,
                )
            )
    return selected_windows


def _count_behavior_paths(target_session: Session, session_id: str) -> int:
    statement = select(func.count(BehaviorExperiencePath.id)).where(BehaviorExperiencePath.session_id == session_id)
    return int(target_session.exec(statement).one() or 0)


def _chat_display_name(chat_session: ChatSession) -> str:
    if chat_session.group_name:
        return chat_session.group_name
    if chat_session.user_nickname:
        return f"{chat_session.user_nickname} 的私聊"
    return chat_session.session_id


async def run_learning(args: Namespace) -> None:
    source_db_path = _resolve_path(args.source_db)
    target_db_path = _resolve_path(args.target_db)
    progress_path = _resolve_path(args.progress_jsonl)
    run_id = f"{datetime.now():%Y%m%d%H%M%S}-{random.getrandbits(32):08x}"
    source_engine = _build_engine(source_db_path, readonly=True)
    target_engine = _build_engine(target_db_path)
    _initialize_target_database(target_engine)

    source_session_local = sessionmaker(autocommit=False, autoflush=False, bind=source_engine, class_=Session)
    target_session_local = sessionmaker(autocommit=False, autoflush=False, bind=target_engine, class_=Session)
    get_target_session = _build_session_provider(target_session_local)
    _patch_behavior_storage(get_target_session)
    _patch_behavior_model_name(args.model_name)

    since = _parse_datetime(args.since)
    until = _parse_datetime(args.until)
    excluded_window_hashes, excluded_message_ids = _load_progress_exclusions(progress_path)

    with source_session_local() as source_session, target_session_local() as target_session:
        session_ids = _requested_session_ids(args)
        if not session_ids:
            min_discover_messages = MIN_RANDOM_CHAT_MESSAGES if args.random_windows > 0 else args.window_size
            session_ids = _discover_session_ids(source_session, min_messages=min_discover_messages)
        if not session_ids:
            raise ValueError("没有找到可学习的 chat_id，请使用 --chat-id 指定源库中的 ChatSession.session_id")

        targets: list[ChatLearningTarget] = []
        before_count = 0
        for session_id in session_ids:
            chat_session = _copy_chat_session(source_session, target_session, session_id)
            messages = _load_source_messages(
                source_session,
                session_id=session_id,
                since=since,
                until=until,
                limit=args.limit,
                min_text_length=args.min_text_length,
            )
            targets.append(
                ChatLearningTarget(
                    session_id=session_id,
                    display_name=_chat_display_name(chat_session),
                    messages=messages,
                )
            )
            before_count += _count_behavior_paths(target_session, session_id)

    print(f"源数据库: {source_db_path}")
    print(f"离线行为库: {target_db_path}")
    print(f"进度记录: {progress_path}")
    print(f"进度排除窗口数: {len(excluded_window_hashes)}")
    print(f"进度排除消息数: {len(excluded_message_ids)}")
    print(f"run_id: {run_id}")
    print(f"模型: {args.model_name or '配置任务 learner 默认模型'}")
    print(f"chat 数: {len(targets)}")
    for target in targets:
        candidate_count = len(target.messages) // args.window_size
        print(f"- {target.display_name} ({target.session_id}) 消息数={len(target.messages)} 非重叠候选窗口={candidate_count}")
    print(f"学习前行为路径数: {before_count}")

    if args.random_windows > 0:
        learning_windows = _select_random_windows(
            targets,
            window_size=args.window_size,
            random_count=args.random_windows,
            seed=args.seed,
            uniform_by_chat=args.uniform_by_chat,
            balanced_by_chat=args.balanced_by_chat,
            excluded_window_hashes=excluded_window_hashes,
            excluded_message_ids=excluded_message_ids,
        )
    else:
        learning_windows = _select_sequential_windows(
            targets,
            window_size=args.window_size,
            step=args.step,
            max_batches=args.max_batches,
            excluded_window_hashes=excluded_window_hashes,
            excluded_message_ids=excluded_message_ids,
        )
    print(f"实际学习窗口数: {len(learning_windows)}")
    if args.dry_run:
        for index, learning_window in enumerate(learning_windows[:20], start=1):
            window = learning_window.messages
            start_time = window[0].timestamp.isoformat(timespec="seconds")
            end_time = window[-1].timestamp.isoformat(timespec="seconds")
            print(
                f"  dry-run窗口[{index}]: {learning_window.display_name} "
                f"({learning_window.session_id}) {start_time} ~ {end_time}"
            )
        if len(learning_windows) > 20:
            print(f"  ... 其余 {len(learning_windows) - 20} 个窗口已省略")
        return

    attempted_count = 0
    wrote_count = 0
    for learning_window in learning_windows:
        attempted_count += 1
        learner = BehaviorLearner(learning_window.session_id)
        learner.min_messages_for_extraction = min(args.window_size, max(1, args.min_messages_for_extraction))

        active_session_id = learning_window.session_id

        def resolve_learning_session_id(
            self,
            pending_messages: list[SessionMessage],
            bound_session_id: str = active_session_id,
        ) -> Optional[str]:
            return bound_session_id

        learner._resolve_learning_session_id = types.MethodType(  # type: ignore[method-assign]
            resolve_learning_session_id,
            learner,
        )

        window = learning_window.messages
        start_time = window[0].timestamp.isoformat(timespec="seconds")
        end_time = window[-1].timestamp.isoformat(timespec="seconds")
        print(
            f"\n[{attempted_count}] 学习窗口: {learning_window.display_name} "
            f"({learning_window.session_id}) {start_time} ~ {end_time}, 消息数={len(window)}"
        )
        _write_progress_event(
            progress_path,
            _progress_payload(
                status="started",
                run_id=run_id,
                index=attempted_count,
                learning_window=learning_window,
            ),
        )
        try:
            wrote_pattern = await learner._learn_from_session_messages(window)
        except Exception as exc:
            _write_progress_event(
                progress_path,
                _progress_payload(
                    status="failed",
                    run_id=run_id,
                    index=attempted_count,
                    learning_window=learning_window,
                    error=str(exc),
                ),
            )
            raise
        _write_progress_event(
            progress_path,
            _progress_payload(
                status="completed",
                run_id=run_id,
                index=attempted_count,
                learning_window=learning_window,
                wrote_pattern=wrote_pattern,
            ),
        )
        if wrote_pattern:
            wrote_count += 1
            print(f"[{attempted_count}] 写入成功")
        else:
            print(f"[{attempted_count}] 未写入有效行为")

    with target_session_local() as target_session:
        after_count = sum(_count_behavior_paths(target_session, target.session_id) for target in targets)

    print("\n离线行为学习完成")
    print(f"尝试批次数: {attempted_count}")
    print(f"有写入批次数: {wrote_count}")
    print(f"学习后行为路径数: {after_count}")
    print(f"新增/合并后净变化: {after_count - before_count}")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="从 MaiBot 主库读取聊天记录，离线执行 Maisaka 行为学习并写入独立行为库。")
    parser.add_argument(
        "--chat-id",
        action="append",
        default=[],
        help="只抽指定 chat_id，可重复传入或用逗号分隔；这里的 chat_id 即 ChatSession.session_id。",
    )
    parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="--chat-id 的兼容别名，可重复传入或用逗号分隔。",
    )
    parser.add_argument("--source-db", default="data/MaiBot.db", help="只读源数据库路径，默认 data/MaiBot.db。")
    parser.add_argument(
        "--target-db",
        default="data/behaviro_learn_test/offline_behavior_learning.db",
        help="离线行为学习结果库路径，默认 data/behaviro_learn_test/offline_behavior_learning.db。",
    )
    parser.add_argument(
        "--progress-jsonl",
        default="data/behaviro_learn_test/offline_behavior_learning_progress.jsonl",
        help="每个学习窗口的 JSONL 进度记录路径，用于防中断和后续评测排除训练窗口。",
    )
    parser.add_argument("--since", default="", help="起始时间，ISO 格式，例如 2026-06-01T00:00:00。")
    parser.add_argument("--until", default="", help="结束时间，ISO 格式。")
    parser.add_argument("--limit", type=int, default=0, help="每个 chat 最多读取多少条消息，0 表示不限制。")
    parser.add_argument("--window-size", type=int, default=40, help="每个学习批次的消息窗口大小，默认 40。")
    parser.add_argument("--step", type=int, default=40, help="顺序扫描模式的窗口步长；小于等于 0 时等于 window-size。")
    parser.add_argument("--max-batches", type=int, default=0, help="最多执行多少个学习批次，0 表示不限制。")
    parser.add_argument("--random-windows", type=int, default=0, help="随机抽取多少个不重叠窗口，0 表示关闭随机抽样。")
    parser.add_argument("--seed", type=int, default=20260611, help="随机抽样种子。")
    parser.add_argument(
        "--model-name",
        default="",
        help="强制指定行为学习使用的模型名；留空时使用 model_task_config 中 learner 任务的默认模型。",
    )
    parser.add_argument("--uniform-by-chat", action="store_true", help="严格按 chat_id 均匀抽样。")
    parser.add_argument("--balanced-by-chat", action="store_true", help="按 chat_id 尽量均匀抽样。")
    parser.add_argument("--min-text-length", type=int, default=1, help="过滤过短消息的最小文本长度。")
    parser.add_argument(
        "--min-messages-for-extraction",
        type=int,
        default=10,
        help="行为学习内部最小消息阈值，默认保持主流程的 10。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查聊天流和可学习消息数，不调用模型、不写入行为。")
    args = parser.parse_args()
    if args.uniform_by_chat and args.balanced_by_chat:
        parser.error("--uniform-by-chat 和 --balanced-by-chat 只能选择一个")
    if (args.uniform_by_chat or args.balanced_by_chat) and args.random_windows <= 0:
        parser.error("--uniform-by-chat / --balanced-by-chat 需要同时指定 --random-windows")
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        asyncio.run(run_learning(parse_args()))
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
