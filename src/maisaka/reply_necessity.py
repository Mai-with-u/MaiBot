"""Maisaka 回复必要性评分规则。"""

from dataclasses import dataclass
from math import log1p
from typing import Sequence
import re

REPLY_NECESSITY_TRIGGER_SCORE = 80
REPLY_NECESSITY_PRESSURE_STANDARD_SCORE = 50
REPLY_NECESSITY_PRESSURE_MAX_SCORE = 100
REPLY_NECESSITY_PRESSURE_FULL_RATIO = 5.0
REPLY_NECESSITY_IDLE_PRESSURE_BONUS = 15
REPLY_NECESSITY_RECENT_SELF_RATIO_FREE = 0.25
REPLY_NECESSITY_RECENT_SELF_RATIO_FULL = 0.60
REPLY_NECESSITY_RECENT_SELF_PENALTY_MAX = 25
DIRECT_REQUEST_TERMS = ("帮我", "帮忙", "能不能", "可以吗", "要不要")
WEAK_REQUEST_TERMS = ("需要", "求", "看看", "试试")
QUESTION_TERMS = ("怎么", "如何", "为什么", "有没有")
OPINION_TERMS = ("你觉得", "你认为", "咋看", "有什么建议")
SHORT_REACTIONS = {"哈哈", "哈哈哈", "草", "笑死", "好", "嗯", "啊", "哦", "6", "666", "？", "?"}
MEDIA_PLACEHOLDER_PREFIXES = ("[CQ:image", "[图片：", "[表情包:", "[文件]", "[语音:", "[卡片:")
IGNORED_TEXT_PREFIXES = ("【合并转发消息:", *MEDIA_PLACEHOLDER_PREFIXES, "本群发言榜")
OTHER_ASSISTANT_ADDRESSEE_PATTERN = re.compile(r"^(?:DeepSeek|ChatGPT|Grok|豆包|千问|元宝|通义|Kimi|Claude)[，,、\s]")


@dataclass(frozen=True, slots=True)
class ReplyNecessityInput:
    """回复必要性评分所需的运行时快照。"""

    texts: Sequence[str]
    pending_count: int
    trigger_threshold: int
    has_at: bool
    has_mention: bool
    is_group_chat: bool
    focus_active: bool
    recent_self_replies: int
    recent_window_messages: int
    effective_frequency: float
    idle_seconds: float
    idle_reached_average: bool


@dataclass(frozen=True, slots=True)
class ReplyNecessityScore:
    """回复必要性评分结果。"""

    score: int
    detail: str
    pressure_score: int


def strip_reply_necessity_noise(text: str) -> str:
    """清理引用、媒体描述和合并转发，保留用户当前发言主体。"""
    normalized_text = " ".join((text or "").split()).strip()
    if normalized_text.startswith("@all"):
        return ""
    if normalized_text.startswith("[回复了") and "【合并转发消息:" in normalized_text:
        return ""
    if normalized_text.startswith(IGNORED_TEXT_PREFIXES):
        return ""

    normalized_text = re.sub(r"^\[CQ:reply[^\]]*\]\s*", "", normalized_text)
    normalized_text = re.sub(r"^\[reply\]\s*", "", normalized_text)
    normalized_text = re.sub(r"^\[回复了.+?的消息: .+?\]\s*", "", normalized_text)
    legacy_reply_match = re.search(r"\]，说：\s*(.+)$", normalized_text)
    if legacy_reply_match:
        normalized_text = legacy_reply_match.group(1)
    normalized_text = re.sub(r"^\[回复消息\]\s*", "", normalized_text)
    normalized_text = re.sub(r"^\[回复了一条消息，但原消息已无法访问\]\s*", "", normalized_text)
    normalized_text = re.sub(r"@<[^>]+>|@\S+", "", normalized_text).strip()
    if normalized_text.startswith(MEDIA_PLACEHOLDER_PREFIXES):
        return ""
    return normalized_text.strip()


def is_short_reaction_batch(texts: Sequence[str]) -> bool:
    """判断待处理消息是否基本都是短反应或纯占位内容。"""
    normalized_texts = [" ".join(text.split()).strip() for text in texts if text.strip()]
    if not normalized_texts:
        return True
    if any(len(text) > 8 for text in normalized_texts):
        return False
    return all(text in SHORT_REACTIONS for text in normalized_texts)


def has_reply_necessity_question(text: str) -> bool:
    """判断当前发言是否像一个真实问题。"""
    if not text:
        return False
    if re.fullmatch(r"[？?！!~～…\s]+[\w\u4e00-\u9fff]{1,4}[？?！!~～…\s]+", text):
        return False
    if any(term in text for term in QUESTION_TERMS):
        return True
    if re.search(r"(?<![这那没])什么", text):
        return True
    if re.search(r"[吗呢](?:[？?。！!~～…]*$)", text) and 4 <= len(text) <= 80:
        return True
    return bool(re.search(r"[？?](?:$|[。！!~～…])", text) and 4 <= len(text) <= 120)


def get_reply_necessity_request_reason(text: str, *, is_direct_context: bool) -> str:
    """返回请求类命中原因；弱请求只在直接上下文中生效。"""
    if not is_direct_context and OTHER_ASSISTANT_ADDRESSEE_PATTERN.search(text):
        return ""
    direct_hits = [term for term in DIRECT_REQUEST_TERMS if term in text]
    if "能不能" in direct_hits and not (is_direct_context or text.startswith("能不能")):
        direct_hits.remove("能不能")
    if not is_direct_context:
        for weak_direct_term in ("可以吗", "要不要"):
            if weak_direct_term in direct_hits:
                direct_hits.remove(weak_direct_term)
    if direct_hits:
        return "/".join(direct_hits)
    if is_direct_context:
        weak_hits = [term for term in WEAK_REQUEST_TERMS if term in text]
        if weak_hits:
            return "/".join(weak_hits)
    return ""


def get_reply_necessity_opinion_reason(text: str, *, is_direct_context: bool) -> str:
    """返回征询意见类命中原因。"""
    if "不怎么看" in text:
        return ""
    if not is_direct_context and "麦麦" not in text:
        return ""
    hits = [term for term in OPINION_TERMS if term in text]
    if hits:
        return "/".join(hits)
    if re.search(r"(?:你|麦麦).{0,6}怎么看|怎么看.{0,6}(?:你|麦麦)", text):
        return "怎么看"
    return ""


def score_reply_necessity(score_input: ReplyNecessityInput) -> ReplyNecessityScore:
    """计算回复必要性评分。"""
    normalized_threshold = max(1, score_input.trigger_threshold)
    if score_input.has_at:
        relevance_score = 100
        relevance_reason = "@"
    elif score_input.has_mention:
        relevance_score = 80
        relevance_reason = "提及"
    elif not score_input.is_group_chat:
        relevance_score = 40
        relevance_reason = "私聊"
    elif score_input.focus_active:
        relevance_score = 40
        relevance_reason = "focus"
    else:
        relevance_score = 0
        relevance_reason = "普通"

    is_direct_context = relevance_score > 0
    cleaned_texts = [strip_reply_necessity_noise(text) for text in score_input.texts]
    combined_clean_text = "\n".join(text for text in cleaned_texts if text)
    content_score, content_reasons = _score_content(
        cleaned_texts,
        combined_clean_text,
        is_direct_context=is_direct_context,
    )
    pressure_score = _calculate_pressure_score(
        pending_count=score_input.pending_count,
        normalized_threshold=normalized_threshold,
        idle_reached_average=score_input.idle_reached_average,
    )

    recent_presence_penalty = _calculate_recent_presence_penalty(
        recent_self_replies=score_input.recent_self_replies,
        recent_window_messages=score_input.recent_window_messages,
    )
    presence_penalty = recent_presence_penalty
    raw_score = relevance_score + content_score + pressure_score - presence_penalty
    effective_frequency = min(1.0, score_input.effective_frequency)
    frequency_factor = 0.5 + 0.5 * effective_frequency
    final_score = max(0, int(round(raw_score * frequency_factor)))
    detail_parts = [f"最终={final_score}", f"原始={raw_score}"]
    if relevance_score != 0:
        detail_parts.append(f"强相关={relevance_score}({relevance_reason})")
    if content_score != 0:
        detail_parts.append(f"内容={content_score}({','.join(content_reasons)})")
    if presence_penalty != 0:
        detail_parts.append(
            f"存在感=-{presence_penalty}(5min={score_input.recent_self_replies}/"
            f"{score_input.recent_window_messages})"
        )
    detail_parts.extend(
        [
            f"频率={effective_frequency:.3f}",
            f"倍率={frequency_factor:.2f}",
        ]
    )
    return ReplyNecessityScore(score=final_score, detail=" ".join(detail_parts), pressure_score=pressure_score)


def _calculate_recent_presence_penalty(*, recent_self_replies: int, recent_window_messages: int) -> int:
    """按最近窗口内麦麦发言占比计算存在感惩罚。"""

    if recent_self_replies <= 0 or recent_window_messages <= 0:
        return 0

    self_ratio = min(1.0, recent_self_replies / recent_window_messages)
    if self_ratio <= REPLY_NECESSITY_RECENT_SELF_RATIO_FREE:
        return 0

    ratio_span = REPLY_NECESSITY_RECENT_SELF_RATIO_FULL - REPLY_NECESSITY_RECENT_SELF_RATIO_FREE
    ratio_progress = min(1.0, (self_ratio - REPLY_NECESSITY_RECENT_SELF_RATIO_FREE) / ratio_span)
    return int(round(REPLY_NECESSITY_RECENT_SELF_PENALTY_MAX * ratio_progress))


def _calculate_pressure_score(
    *,
    pending_count: int,
    normalized_threshold: int,
    idle_reached_average: bool,
) -> int:
    """按积压消息量计算压力分，阈值内二次增长，超过阈值后对数增长。"""

    pending_ratio = max(0.0, pending_count / normalized_threshold)
    if pending_ratio <= 1.0:
        pressure_score = int(round(REPLY_NECESSITY_PRESSURE_STANDARD_SCORE * pending_ratio * pending_ratio))
        if idle_reached_average:
            pressure_score += REPLY_NECESSITY_IDLE_PRESSURE_BONUS
        return min(REPLY_NECESSITY_PRESSURE_STANDARD_SCORE, pressure_score)

    overflow_ratio = pending_ratio - 1.0
    full_overflow_ratio = REPLY_NECESSITY_PRESSURE_FULL_RATIO - 1.0
    overflow_factor = min(1.0, log1p(overflow_ratio) / log1p(full_overflow_ratio))
    pressure_score = REPLY_NECESSITY_PRESSURE_STANDARD_SCORE + int(
        round(
            (REPLY_NECESSITY_PRESSURE_MAX_SCORE - REPLY_NECESSITY_PRESSURE_STANDARD_SCORE)
            * overflow_factor
        )
    )
    return min(REPLY_NECESSITY_PRESSURE_MAX_SCORE, pressure_score)


def _score_content(
    cleaned_texts: Sequence[str],
    combined_clean_text: str,
    *,
    is_direct_context: bool,
) -> tuple[int, list[str]]:
    content_score = 0
    content_reasons: list[str] = []
    if any(has_reply_necessity_question(text) for text in cleaned_texts):
        content_score += 15
        content_reasons.append("问题")

    request_reason = get_reply_necessity_request_reason(
        combined_clean_text,
        is_direct_context=is_direct_context,
    )
    if request_reason:
        content_score += 20
        content_reasons.append(f"请求:{request_reason}")

    opinion_reason = get_reply_necessity_opinion_reason(
        combined_clean_text,
        is_direct_context=is_direct_context,
    )
    if opinion_reason:
        content_score += 20
        content_reasons.append(f"征询:{opinion_reason}")

    total_text_length = len(combined_clean_text)
    if total_text_length >= 40:
        content_score += 5
        content_reasons.append("长文本")
    if total_text_length >= 120:
        content_score += 10
        content_reasons.append("较长文本")
    if is_short_reaction_batch(cleaned_texts):
        content_score -= 25
        content_reasons.append("短反应")
    return content_score, content_reasons
