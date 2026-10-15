"""核验自动人物事实是否能由目标用户原始发言直接支持。"""

from typing import Optional

import re
import unicodedata

from src.chat.utils.utils import is_bot_self
from src.common.message_repository import find_messages
from src.person_info.person_info import get_person_id


_TEMPORARY_MARKERS = ("今天", "昨天", "明天", "刚才", "暂时", "可能", "似乎", "也许", "打算", "计划")
_OTHER_SUBJECT_PREFIXES = ("我们", "我朋友", "我的朋友", "我同事", "我的同事", "我妈", "我爸", "我家人")
_SELF_PREFIXES = ("我", "本人", "自己")
_THIRD_PERSON_PREFIXES = ("他", "她", "用户", "该用户")


def _normalized_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]", "", text)


def _predicate(value: str, *, person_name: str, first_person: bool) -> Optional[str]:
    normalized = _normalized_text(value)
    if first_person and normalized.startswith(_OTHER_SUBJECT_PREFIXES):
        return None
    prefixes = _SELF_PREFIXES if first_person else (person_name, *_THIRD_PERSON_PREFIXES)
    for prefix in sorted((item for item in prefixes if item), key=len, reverse=True):
        normalized_prefix = _normalized_text(prefix)
        if normalized.startswith(normalized_prefix):
            predicate = normalized[len(normalized_prefix) :]
            return predicate if len(predicate) >= 2 else None
    return None


def verify_direct_person_fact(
    *,
    fact: str,
    evidence_message_id: str,
    evidence_quote: str,
    person_id: str,
    person_name: str,
    session_id: str,
) -> bool:
    """只接受可定位到同一聊天流、同一人物的直接自述和称谓改写。"""

    if not all((fact, evidence_message_id, evidence_quote, person_id, session_id)):
        return False
    quote = _normalized_text(evidence_quote)
    if not quote or any(marker in quote for marker in _TEMPORARY_MARKERS):
        return False
    source_predicate = _predicate(evidence_quote, person_name=person_name, first_person=True)
    fact_predicate = _predicate(fact, person_name=person_name, first_person=False)
    if not source_predicate or source_predicate != fact_predicate:
        return False

    messages = find_messages(session_id=session_id, message_id=evidence_message_id, limit=2)
    if len(messages) != 1:
        return False
    message = messages[0]
    platform = str(message.platform or "").strip()
    user_info = message.message_info.user_info if message.message_info else None
    user_id = str(user_info.user_id if user_info else message.user_id or "").strip()
    if not platform or not user_id or is_bot_self(platform, user_id) or get_person_id(platform, user_id) != person_id:
        return False
    message_text = unicodedata.normalize("NFKC", str(message.processed_plain_text or "")).casefold()
    quoted_text = unicodedata.normalize("NFKC", evidence_quote).casefold().strip()
    position = message_text.find(quoted_text)
    if position < 0:
        return False
    if quoted_text[-1] in "？?":
        return False
    prefix = message_text[:position].rstrip()
    if prefix and prefix[-1] not in "。！？；\n":
        return False
    suffix = message_text[position + len(quoted_text) :].strip()
    # 引述之后只允许一个句末标点；同句省略或后文反转都不能晋升为稳定事实。
    return not suffix or suffix in "。！；"
