"""群聊人物画像候选：最近发言者、@ 对象与回复对象。"""

from typing import List, Optional, Sequence, Set

from src.chat.message_receive.message import SessionMessage
from src.maisaka.memory.person_profile import PersonProfileCandidate, _sender_candidate

from src.common.data_models.message_component_data_model import AtComponent, ReplyComponent
from src.maisaka.memory.person_profile import _at_candidate, _messages_current_first, _reply_candidate


def collect_person_profiles(
    anchor_message: SessionMessage,
    pending_messages: Optional[Sequence[SessionMessage]],
    *,
    max_profiles: int,
) -> List[PersonProfileCandidate]:
    limit = max(1, int(max_profiles or 1))
    candidates: List[PersonProfileCandidate] = []
    seen_person_ids: Set[str] = set()

    def add(candidate: Optional[PersonProfileCandidate]) -> bool:
        if candidate is None or candidate.person_id in seen_person_ids:
            return len(candidates) >= limit
        seen_person_ids.add(candidate.person_id)
        candidates.append(candidate)
        return len(candidates) >= limit

    for message in _messages_current_first(anchor_message, pending_messages):
        if add(_sender_candidate(message, "recent_speaker")):
            break
        for component in message.raw_message.components:
            if isinstance(component, AtComponent):
                if add(_at_candidate(message, component)):
                    break
            elif isinstance(component, ReplyComponent) and component.target_message_sender_id:
                if add(_reply_candidate(message, component)):
                    break
        if len(candidates) >= limit:
            break

    return candidates[:limit]
