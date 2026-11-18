"""私聊人物画像只取当前对话用户。"""

from typing import List, Optional, Sequence

from src.chat.message_receive.message import SessionMessage
from src.maisaka.memory.person_profile import PersonProfileCandidate, _sender_candidate


def collect_person_profiles(
    anchor_message: SessionMessage,
    pending_messages: Optional[Sequence[SessionMessage]],
    *,
    max_profiles: int,
) -> List[PersonProfileCandidate]:
    candidate = _sender_candidate(anchor_message, "private_current_user")
    return [candidate] if candidate is not None else []
