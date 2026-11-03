"""聊天场景的行为边界；共享引擎只依赖此接口。"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Tuple

from .prompting import ChatPromptProfile

if TYPE_CHECKING:
    from src.chat.message_receive.chat_manager import BotChatSession
    from src.chat.message_receive.message import SessionMessage
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from src.maisaka.memory.person_profile import PersonProfileCandidate
    from src.maisaka.quote.counter import ReplyQuoteContext
    from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler

DebounceMode = Literal["off", "fast", "natural", "steady"]
QuoteFrequency = Literal["never", "occasionally", "often"]


class ChatScene(ABC):
    is_group: bool
    silent_reason: str
    prompts: ChatPromptProfile

    @abstractmethod
    def create_scheduler(self, runtime: "MaisakaHeartFlowChatting") -> "MessageTurnScheduler": ...

    @property
    @abstractmethod
    def max_context_size(self) -> int: ...

    @property
    @abstractmethod
    def debounce_mode(self) -> DebounceMode: ...

    @property
    @abstractmethod
    def quote_frequency(self) -> QuoteFrequency: ...

    @property
    @abstractmethod
    def allow_reply_at(self) -> bool: ...

    @abstractmethod
    def base_reply_frequency(self, session_id: str) -> float: ...

    @abstractmethod
    def effective_reply_frequency(self, runtime: "MaisakaHeartFlowChatting") -> float: ...

    @abstractmethod
    def update_message_trigger(self, runtime: "MaisakaHeartFlowChatting", message: "SessionMessage") -> None: ...

    @abstractmethod
    def finish_debounce(self, runtime: "MaisakaHeartFlowChatting", message: "SessionMessage", reason: str) -> str: ...

    @abstractmethod
    def history_target(self, session: "BotChatSession") -> Dict[str, str]: ...

    @abstractmethod
    def allows_focus(self, session_id: str) -> bool: ...

    @abstractmethod
    def allows_memory_from(self, source_is_group: bool) -> bool: ...

    @abstractmethod
    def quote_probability(
        self, context: "ReplyQuoteContext", bot_user_id: str, text: str, frequency: QuoteFrequency
    ) -> float: ...

    @abstractmethod
    def collect_person_profiles(
        self,
        anchor_message: "SessionMessage",
        pending_messages: Optional[Sequence["SessionMessage"]],
        *,
        max_profiles: int,
    ) -> List["PersonProfileCandidate"]: ...

    @abstractmethod
    def detect_mentions(self, message: "SessionMessage") -> Tuple[bool, bool, float]: ...
