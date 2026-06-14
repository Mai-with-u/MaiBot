from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal, Sequence

from sqlmodel import select

import jieba
import re

from src.common.database.database import get_db_session
from src.common.database.database_model import HighFrequencyTerm
from src.common.data_models.message_component_data_model import TextComponent

from src.maisaka.context.messages import SessionBackedMessage

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]+\]")
_MENTION_RE = re.compile(r"@\S+")
_PLANNER_MESSAGE_PREFIX_RE = re.compile(r"^<message\b[^>]*>\s*", re.IGNORECASE)
_TECH_TERM_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_+./#-]{1,})(?![A-Za-z0-9_])")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-zA-Z]")
_NUMERIC_RE = re.compile(r"[\d._+-]+")
_PUNCT_ONLY_RE = re.compile(r"[\W_]+", re.UNICODE)
_TOKEN_TRIM_CHARS = " \t\r\n.,;:!?()[]{}<>\"'`~@#$%^&*=|\\/，。！？；：、（）【】《》“”‘’…"

_CN_STOP_WORDS = {
    "一些",
    "一样",
    "一定",
    "一条",
    "一种",
    "一起",
    "一下",
    "一个",
    "一会",
    "一是",
    "不是",
    "不过",
    "不如",
    "不能",
    "不要",
    "不用",
    "不会",
    "但原",
    "为了",
    "为什么",
    "也是",
    "也许",
    "于是",
    "什么",
    "他们",
    "以后",
    "以前",
    "以及",
    "按照",
    "但是",
    "你们",
    "你的",
    "其实",
    "其它",
    "其他",
    "只是",
    "只要",
    "可以",
    "可是",
    "没有",
    "各位",
    "回复",
    "因为",
    "因此",
    "如果",
    "它们",
    "对于",
    "对方",
    "就是",
    "已经",
    "并且",
    "怎么",
    "怎样",
    "总之",
    "我们",
    "我的",
    "所以",
    "所有",
    "是否",
    "是不是",
    "未知",
    "未知用户",
    "有人",
    "有点",
    "有没有",
    "无法访问",
    "然后",
    "现在",
    "关于",
    "根据",
    "由于",
    "的话",
    "直接",
    "真的",
    "真是",
    "自己",
    "虽然",
    "这是",
    "通过",
    "这个",
    "这些",
    "这么",
    "这里",
    "这样",
    "那个",
    "那些",
    "那么",
    "那里",
    "那样",
    "还是",
    "还有",
    "或者",
    "而且",
    "来说",
    "知道",
    "一下子",
    "一大",
    "不了",
    "之前",
    "出来",
    "图片",
    "喜欢",
    "好像",
    "好看",
    "哈哈",
    "哈哈哈",
    "小时",
    "应该",
    "我要",
    "时候",
    "消息",
    "确实",
    "聊天",
    "表情",
    "觉得",
    "需要",
    "感觉",
    "看看",
    "群友",
    "使用",
    "可能",
    "问题",
    "这种",
    "东西",
}
_EN_STOP_WORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "but",
    "by",
    "can",
    "could",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "may",
    "more",
    "not",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "then",
    "there",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
}
_STOP_WORDS = _CN_STOP_WORDS | _EN_STOP_WORDS


@dataclass(frozen=True)
class WordFrequencyItem:
    """消息词频统计项。"""

    term: str
    count: int
    message_count: int
    frequency: float
    message_frequency: float
    term_type: Literal["word", "phrase"] = "word"


def update_high_frequency_terms_from_context_messages(
    context_messages: Sequence[object],
    *,
    limit: int = 1000,
    min_count: int = 2,
    max_terms: int = 1000,
) -> int:
    """从 Maisaka 裁切上下文消息批次中提取格式化用户消息，并增量更新高频词词库。"""

    texts = _extract_user_texts_from_context(context_messages)
    if not texts:
        return 0

    terms = _build_message_word_frequency_terms(
        texts,
        limit=limit,
        min_count=min_count,
    )
    if not terms:
        return 0

    return _merge_high_frequency_terms(
        terms,
        generated_at=datetime.now(),
        max_terms=max_terms,
    )


def _build_message_word_frequency_terms(
    texts: Iterable[str],
    *,
    limit: int = 50,
    min_count: int = 2,
) -> list[WordFrequencyItem]:
    """从 Maisaka 文本组件内容构建词频统计。"""

    normalized_limit = max(1, int(limit))
    normalized_min_count = max(1, int(min_count))
    word_counter: Counter[str] = Counter()
    phrase_counter: Counter[str] = Counter()
    word_message_counter: Counter[str] = Counter()
    phrase_message_counter: Counter[str] = Counter()
    message_count = 0

    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue

        message_count += 1
        tokens = _tokenize_meaningful_terms(text)
        if not tokens:
            continue

        phrases = list(_iter_phrases(tokens))
        word_counter.update(tokens)
        phrase_counter.update(phrases)
        word_message_counter.update(set(tokens))
        phrase_message_counter.update(set(phrases))

    total_term_count = sum(word_counter.values()) + sum(phrase_counter.values())
    terms = _build_frequency_items(
        word_counter=word_counter,
        phrase_counter=phrase_counter,
        word_message_counter=word_message_counter,
        phrase_message_counter=phrase_message_counter,
        message_count=message_count,
        total_term_count=total_term_count,
        min_count=normalized_min_count,
        limit=normalized_limit,
    )

    return terms


def _merge_high_frequency_terms(
    terms: Sequence[WordFrequencyItem],
    *,
    generated_at: datetime,
    max_terms: int = 1000,
) -> int:
    """将一批统计结果合并进当前高频词词库，保持每个归一化词仅一行。"""

    max_term_count = max(1, int(max_terms))
    with get_db_session(auto_commit=False) as session:
        records = list(session.exec(select(HighFrequencyTerm)).all())
        records_by_term = {record.normalized_term: record for record in records if record.normalized_term}
        merged_count = 0

        for item in terms:
            normalized_term = _normalize_term_for_match(item.term)
            if not normalized_term:
                continue

            existing_record = records_by_term.get(normalized_term)
            if existing_record is None:
                existing_record = HighFrequencyTerm(
                    term=item.term,
                    normalized_term=normalized_term,
                    term_type=item.term_type,
                    occurrence_count=0,
                    message_count=0,
                    created_at=generated_at,
                    updated_at=generated_at,
                )
                records.append(existing_record)
                records_by_term[normalized_term] = existing_record

            existing_record.term = item.term
            existing_record.term_type = item.term_type
            existing_record.occurrence_count += item.count
            existing_record.message_count += item.message_count
            existing_record.updated_at = generated_at
            merged_count += 1

        kept_records, removed_records = _rerank_high_frequency_records(records, max_terms=max_term_count)
        session.add_all(kept_records)
        for record in removed_records:
            if record.id is not None:
                session.delete(record)
        session.commit()

    return merged_count


def _extract_user_texts_from_context(context_messages: Sequence[object]) -> list[str]:
    texts: list[str] = []
    seen_message_ids: set[str] = set()

    for context_message in context_messages:
        if not isinstance(context_message, SessionBackedMessage):
            continue
        if context_message.source_kind != "user":
            continue

        message_id = str(context_message.message_id or "").strip()
        if message_id:
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)

        text = _extract_text_from_maisaka_components(context_message.raw_message.components)
        if not text:
            continue

        texts.append(text)

    return texts


def _extract_text_from_maisaka_components(components: Sequence[object]) -> str:
    text_parts: list[str] = []
    is_first_text_component = True
    for component in components:
        if not isinstance(component, TextComponent):
            continue

        text = component.text
        if is_first_text_component:
            text = _PLANNER_MESSAGE_PREFIX_RE.sub("", text, count=1)
            is_first_text_component = False
        if text.strip():
            text_parts.append(text)
    return " ".join(" ".join(text_parts).split()).strip()


def _rerank_high_frequency_records(
    records: list[HighFrequencyTerm],
    *,
    max_terms: int,
) -> tuple[list[HighFrequencyTerm], list[HighFrequencyTerm]]:
    sorted_records = sorted(records, key=_high_frequency_record_sort_key)
    kept_records = sorted_records[:max_terms]
    removed_records = sorted_records[max_terms:]
    total_occurrence_count = sum(record.occurrence_count for record in kept_records)
    total_message_count = sum(record.message_count for record in kept_records)

    for rank, record in enumerate(kept_records, start=1):
        record.rank = rank
        record.frequency = (
            record.occurrence_count / total_occurrence_count if total_occurrence_count > 0 else 0.0
        )
        record.message_frequency = record.message_count / total_message_count if total_message_count > 0 else 0.0

    return kept_records, removed_records


def _high_frequency_record_sort_key(record: HighFrequencyTerm) -> tuple[int, int, int, int, str]:
    phrase_priority = 0 if record.term_type == "phrase" else 1
    return (
        -record.occurrence_count,
        -record.message_count,
        phrase_priority,
        -len(record.term or ""),
        record.normalized_term or "",
    )


def _normalize_term_for_match(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _tokenize_meaningful_terms(text: str) -> list[str]:
    cleaned_text = _remove_tokenization_noise(text)
    terms: list[str] = []

    for raw_token in jieba.cut(cleaned_text):
        token = _normalize_token(raw_token)
        if _is_meaningful_token(token):
            terms.append(token)

    existing_terms = set(terms)
    for match in _TECH_TERM_RE.finditer(cleaned_text):
        token = _normalize_token(match.group(1))
        if token not in existing_terms and _is_meaningful_token(token):
            terms.append(token)
            existing_terms.add(token)

    return terms


def _remove_tokenization_noise(text: str) -> str:
    without_urls = _URL_RE.sub(" ", text)
    without_codes = _CQ_CODE_RE.sub(" ", without_urls)
    return _MENTION_RE.sub(" ", without_codes)


def _normalize_token(raw_token: str) -> str:
    token = raw_token.strip().lower().strip(_TOKEN_TRIM_CHARS)
    return re.sub(r"\s+", " ", token)


def _is_meaningful_token(token: str) -> bool:
    if not token or token in _STOP_WORDS:
        return False
    if _NUMERIC_RE.fullmatch(token) or _PUNCT_ONLY_RE.fullmatch(token):
        return False

    if _contains_cjk(token):
        return _count_cjk_chars(token) >= 2

    if _LATIN_RE.search(token):
        return len(token) >= 2

    return len(token) >= 2


def _iter_phrases(tokens: list[str], *, max_size: int = 3) -> Iterable[str]:
    for phrase_size in range(2, max(2, max_size) + 1):
        if len(tokens) < phrase_size:
            break
        for index in range(0, len(tokens) - phrase_size + 1):
            phrase_tokens = tokens[index : index + phrase_size]
            phrase = _join_phrase(phrase_tokens)
            if _is_meaningful_phrase(phrase, phrase_tokens):
                yield phrase


def _join_phrase(tokens: list[str]) -> str:
    if all(_contains_cjk(token) and not _LATIN_RE.search(token) for token in tokens):
        return "".join(tokens)
    return " ".join(tokens)


def _is_meaningful_phrase(phrase: str, tokens: list[str]) -> bool:
    if phrase in _STOP_WORDS or len(set(tokens)) == 1:
        return False
    if _contains_cjk(phrase):
        return _count_cjk_chars(phrase) >= 4
    return any(_LATIN_RE.search(token) for token in tokens)


def _build_frequency_items(
    *,
    word_counter: Counter[str],
    phrase_counter: Counter[str],
    word_message_counter: Counter[str],
    phrase_message_counter: Counter[str],
    message_count: int,
    total_term_count: int,
    min_count: int,
    limit: int,
) -> list[WordFrequencyItem]:
    items = []
    items.extend(
        _counter_to_items(
            counter=word_counter,
            message_counter=word_message_counter,
            term_type="word",
            message_count=message_count,
            total_term_count=total_term_count,
            min_count=min_count,
        )
    )
    items.extend(
        _counter_to_items(
            counter=phrase_counter,
            message_counter=phrase_message_counter,
            term_type="phrase",
            message_count=message_count,
            total_term_count=total_term_count,
            min_count=min_count,
        )
    )

    return sorted(items, key=_frequency_item_sort_key)[:limit]


def _counter_to_items(
    *,
    counter: Counter[str],
    message_counter: Counter[str],
    term_type: Literal["word", "phrase"],
    message_count: int,
    total_term_count: int,
    min_count: int,
) -> list[WordFrequencyItem]:
    items: list[WordFrequencyItem] = []
    for term, count in counter.items():
        if count < min_count:
            continue
        term_message_count = int(message_counter.get(term, 0))
        items.append(
            WordFrequencyItem(
                term=term,
                count=int(count),
                message_count=term_message_count,
                frequency=count / total_term_count if total_term_count > 0 else 0.0,
                message_frequency=term_message_count / message_count if message_count > 0 else 0.0,
                term_type=term_type,
            )
        )
    return items


def _frequency_item_sort_key(item: WordFrequencyItem) -> tuple[int, int, int, int, str]:
    phrase_priority = 0 if item.term_type == "phrase" else 1
    return (-item.count, -item.message_count, phrase_priority, -len(item.term), item.term)


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _count_cjk_chars(text: str) -> int:
    return len(_CJK_RE.findall(text))
