"""基于当前上下文的黑话机械匹配。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlmodel import col, select

import json
import re
import threading
import time

from src.common.data_models.message_component_data_model import TextComponent
from src.common.database.database import get_db_session
from src.common.database.database_model import HighFrequencyTerm, Jargon
from src.common.logger import get_logger
from src.common.utils.utils_config import JargonConfigUtils
from src.chat.utils.utils import is_bot_self
from src.maisaka.context.messages import LLMContextMessage, ReferenceMessage, ReferenceMessageType, SessionBackedMessage

logger = get_logger("maisaka_jargon_context")

MAX_JARGON_REFERENCE_MATCHES = 10
_HIGH_FREQUENCY_TERM_TTL_SECONDS = 60.0
_PLANNER_MESSAGE_PREFIX_RE = re.compile(r"^<message\b[^>]*>\s*", re.IGNORECASE)
_JARGON_REFERENCE_ITEM_RE = re.compile(r"^\s*(?:\d+\.\s*)?(?P<content>.+?)：")
_JARGON_REFERENCE_HEADER = "以下黑话来自当前上下文中其他用户消息的机械匹配，仅作理解聊天语境的参考："
_JARGON_REFERENCE_DISPLAY_PREFIX = "[黑话参考]"


@dataclass(slots=True)
class _JargonCandidate:
    content: str
    meaning: str
    count: int
    is_global: bool
    session_id_dict: str


@dataclass(slots=True)
class _JargonMatch:
    content: str
    meaning: str
    score: float
    first_message_index: int
    jargon_count: int
    high_frequency_term: str
    high_frequency_rank: int
    high_frequency_count: int


@dataclass(slots=True)
class _HighFrequencyTermSummary:
    """高频词参与打分时实际用到的字段。"""

    term: str
    rank: int
    occurrence_count: int


# 高频词是慢变量，而每个规划轮次都会读取一次，因此按会话做短 TTL 缓存，
# 并用锁保护缓存字典，避免缓存被其他线程访问时出现并发写入。
_high_frequency_terms_cache: dict[str, tuple[float, dict[str, _HighFrequencyTermSummary]]] = {}
_high_frequency_terms_lock = threading.Lock()


def build_jargon_reference_message(
    *,
    session_id: str,
    context_messages: Sequence[LLMContextMessage],
    limit: int = MAX_JARGON_REFERENCE_MATCHES,
    excluded_contents: set[str] | None = None,
) -> ReferenceMessage | None:
    """根据当前上下文中其他用户消息，构造一条黑话参考消息。"""

    use_jargon, _ = JargonConfigUtils.get_jargon_config_for_chat(session_id)
    if not use_jargon:
        return None

    matched_jargons = match_jargons_for_context(
        session_id=session_id,
        context_messages=context_messages,
        limit=limit,
    )
    excluded_content_keys = {_normalize_match_text(content) for content in excluded_contents or set()}
    matched_jargons = [
        match for match in matched_jargons if _normalize_match_text(match.content) not in excluded_content_keys
    ]
    if not matched_jargons:
        return None

    reference_lines = [_JARGON_REFERENCE_HEADER]
    for index, match in enumerate(matched_jargons, start=1):
        high_frequency_hint = "，同时命中高频词" if match.high_frequency_term else ""
        reference_lines.append(f"{index}. {match.content}：{match.meaning}{high_frequency_hint}")

    return ReferenceMessage(
        content="\n".join(reference_lines),
        timestamp=datetime.now(),
        reference_type=ReferenceMessageType.JARGON,
        remaining_uses_value=None,
        display_prefix="[黑话参考]",
    )


def extract_jargon_reference_contents(messages: Sequence[LLMContextMessage]) -> set[str]:
    """从已有黑话参考消息中提取已注入过的词条内容。"""

    contents: set[str] = set()
    for message in messages:
        if not isinstance(message, ReferenceMessage) or message.reference_type != ReferenceMessageType.JARGON:
            continue
        contents.update(_extract_jargon_reference_contents_from_text(message.content))
    return contents


def is_jargon_reference_text(content: object) -> bool:
    """判断一段文本是否是黑话参考消息。"""

    text = _PLANNER_MESSAGE_PREFIX_RE.sub("", str(content or "").strip(), count=1).lstrip()
    return text.startswith(_JARGON_REFERENCE_DISPLAY_PREFIX) or text.startswith(_JARGON_REFERENCE_HEADER)


def match_jargons_for_context(
    *,
    session_id: str,
    context_messages: Sequence[LLMContextMessage],
    limit: int = MAX_JARGON_REFERENCE_MATCHES,
) -> list[_JargonMatch]:
    """返回当前上下文机械命中的黑话，按权重取前若干条。"""

    normalized_limit = max(1, int(limit))
    user_texts = _extract_other_user_texts(context_messages)
    if not user_texts:
        return []

    candidates = _load_scoped_jargon_candidates(session_id)
    if not candidates:
        return []

    high_frequency_terms = _load_high_frequency_terms(session_id)
    matches_by_content: dict[str, _JargonMatch] = {}
    for message_index, text in enumerate(user_texts):
        normalized_text = _normalize_match_text(text)
        if not normalized_text:
            continue

        for candidate in candidates:
            content_key = _normalize_match_text(candidate.content)
            if not content_key or content_key in matches_by_content:
                continue
            if not _contains_mechanical_match(normalized_text, content_key):
                continue

            high_frequency_term = high_frequency_terms.get(content_key)
            high_frequency_rank = high_frequency_term.rank if high_frequency_term else 0
            high_frequency_count = high_frequency_term.occurrence_count if high_frequency_term else 0
            matches_by_content[content_key] = _JargonMatch(
                content=candidate.content,
                meaning=candidate.meaning,
                score=_calculate_match_score(
                    candidate_count=candidate.count,
                    first_message_index=message_index,
                    high_frequency_rank=high_frequency_rank,
                    high_frequency_count=high_frequency_count,
                    hit_high_frequency=high_frequency_term is not None,
                ),
                first_message_index=message_index,
                jargon_count=candidate.count,
                high_frequency_term=high_frequency_term.term if high_frequency_term else "",
                high_frequency_rank=high_frequency_rank,
                high_frequency_count=high_frequency_count,
            )

    return sorted(
        matches_by_content.values(),
        key=lambda item: (
            -item.score,
            item.first_message_index,
            -len(item.content),
            item.content,
        ),
    )[:normalized_limit]


def _load_scoped_jargon_candidates(session_id: str) -> list[_JargonCandidate]:
    related_session_ids, _ = JargonConfigUtils.resolve_jargon_group_scope(session_id)
    candidates: list[_JargonCandidate] = []

    with get_db_session(auto_commit=False) as session:
        records = session.exec(
            select(Jargon)
            .where(col(Jargon.is_jargon).is_(True))
            .where(col(Jargon.meaning) != "")
            .order_by(col(Jargon.count).desc(), col(Jargon.id).desc())
        ).all()

    for record in records:
        content = str(record.content or "").strip()
        meaning = str(record.meaning or "").strip()
        if not content or not meaning:
            continue
        if not record.is_global and not _jargon_in_scope(str(record.session_id_dict or "{}"), related_session_ids):
            continue
        candidates.append(
            _JargonCandidate(
                content=content,
                meaning=meaning,
                count=int(record.count or 0),
                is_global=bool(record.is_global),
                session_id_dict=str(record.session_id_dict or "{}"),
            )
        )

    return candidates


def _load_high_frequency_terms(session_id: str) -> dict[str, _HighFrequencyTermSummary]:
    """读取该会话的高频词表，命中短 TTL 缓存时直接返回。

    只取打分实际用到的三个字段，避免为每个词构造完整 ORM 对象。
    """

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return {}

    now = time.monotonic()
    with _high_frequency_terms_lock:
        cached = _high_frequency_terms_cache.get(normalized_session_id)
        if cached is not None and now - cached[0] < _HIGH_FREQUENCY_TERM_TTL_SECONDS:
            return cached[1]

    with get_db_session(auto_commit=False) as session:
        rows = session.exec(
            select(
                HighFrequencyTerm.term,
                HighFrequencyTerm.rank,
                HighFrequencyTerm.occurrence_count,
            ).where(col(HighFrequencyTerm.chat_id) == normalized_session_id)
        ).all()

    terms_by_key: dict[str, _HighFrequencyTermSummary] = {}
    for term, rank, occurrence_count in rows:
        term_key = _normalize_match_text(term)
        if term_key:
            terms_by_key[term_key] = _HighFrequencyTermSummary(
                term=str(term or ""),
                rank=int(rank or 0),
                occurrence_count=int(occurrence_count or 0),
            )

    with _high_frequency_terms_lock:
        _prune_high_frequency_terms_cache_locked(now)
        _high_frequency_terms_cache[normalized_session_id] = (now, terms_by_key)
    return terms_by_key


def _prune_high_frequency_terms_cache_locked(now: float) -> None:
    """清理已过期的会话缓存，避免长期运行后残留无用条目。"""

    expired_session_ids = [
        session_key
        for session_key, (cached_at, _) in _high_frequency_terms_cache.items()
        if now - cached_at >= _HIGH_FREQUENCY_TERM_TTL_SECONDS
    ]
    for session_key in expired_session_ids:
        _high_frequency_terms_cache.pop(session_key, None)


def _extract_jargon_reference_contents_from_text(content: str) -> set[str]:
    contents: set[str] = set()
    for line in str(content or "").splitlines():
        if line.strip() == _JARGON_REFERENCE_HEADER:
            continue
        matched_content = _extract_jargon_reference_content_from_line(line)
        if matched_content:
            contents.add(matched_content)
    return contents


def _extract_jargon_reference_content_from_line(line: str) -> str:
    normalized_line = re.sub(r"^\s*\d+\.\s*", "", str(line or "").strip(), count=1)
    match = _JARGON_REFERENCE_ITEM_RE.match(normalized_line)
    return match.group("content").strip() if match else ""


def _jargon_in_scope(session_id_dict: str, related_session_ids: set[str]) -> bool:
    try:
        parsed_session_counts = json.loads(session_id_dict) if session_id_dict else {}
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(f"解析黑话 session_id_dict 失败，已跳过该条黑话: {exc}")
        return False
    if not isinstance(parsed_session_counts, dict):
        return False
    return bool(related_session_ids.intersection(str(session_id) for session_id in parsed_session_counts))


def _extract_other_user_texts(context_messages: Sequence[LLMContextMessage]) -> list[str]:
    texts: list[str] = []
    seen_message_ids: set[str] = set()
    for message in context_messages:
        if not isinstance(message, SessionBackedMessage):
            continue
        if message.source_kind != "user":
            continue
        original_message = message.original_message
        if original_message is not None and is_bot_self(
            original_message.platform,
            original_message.message_info.user_info.user_id,
        ):
            continue
        message_id = str(message.message_id or "").strip()
        if message_id:
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)

        text = _extract_text_from_message(message)
        if text:
            texts.append(text)
    return texts


def _extract_text_from_message(message: SessionBackedMessage) -> str:
    text_parts: list[str] = []
    is_first_text_component = True
    for component in message.raw_message.components:
        if not isinstance(component, TextComponent):
            continue
        text = component.text
        if is_first_text_component:
            text = _PLANNER_MESSAGE_PREFIX_RE.sub("", text, count=1)
            is_first_text_component = False
        if text.strip():
            text_parts.append(text)
    return " ".join(" ".join(text_parts).split()).strip()


def _normalize_match_text(text: object) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _contains_mechanical_match(normalized_text: str, normalized_term: str) -> bool:
    return normalized_term in normalized_text


def _calculate_match_score(
    *,
    candidate_count: int,
    first_message_index: int,
    high_frequency_rank: int,
    high_frequency_count: int,
    hit_high_frequency: bool,
) -> float:
    high_frequency_score = 0.0
    if hit_high_frequency:
        high_frequency_score = 1000.0 + high_frequency_count * 2.0
        if high_frequency_rank > 0:
            high_frequency_score += max(0.0, 100.0 - high_frequency_rank)
    return candidate_count + high_frequency_score - first_message_index * 0.01
