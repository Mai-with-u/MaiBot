"""群聊运行行为。所有配置在使用时读取，保持热重载。"""

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from src.common.utils.utils_config import ChatConfigUtils
from src.config.config import global_config
from src.maisaka.scenes.base import ChatScene, DebounceMode, QuoteFrequency

if TYPE_CHECKING:
    from src.chat.message_receive.chat_manager import BotChatSession
    from src.chat.message_receive.message import SessionMessage
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from src.maisaka.memory.person_profile import PersonProfileCandidate
    from src.maisaka.quote.counter import ReplyQuoteContext
    from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler

from .prompting import GroupPromptProfile


class GroupChatScene(ChatScene):
    is_group = True
    silent_reason = "回复频率为 0"
    prompts = GroupPromptProfile()

    def create_scheduler(self, runtime: "MaisakaHeartFlowChatting") -> "MessageTurnScheduler":
        from .scheduling import GroupTurnScheduler

        return GroupTurnScheduler(runtime)

    @property
    def max_context_size(self) -> int:
        return max(1, int(global_config.chat.max_context_size))

    @property
    def debounce_mode(self) -> DebounceMode:
        return global_config.chat.reply_timing.message_debounce_mode

    @property
    def quote_frequency(self) -> QuoteFrequency:
        return global_config.chat.reply_style.group_quote_frequency

    @property
    def allow_reply_at(self) -> bool:
        return global_config.chat.enable_reply_at

    def base_reply_frequency(self, session_id: str) -> float:
        return float(global_config.chat.reply_timing.talk_value)

    def effective_reply_frequency(self, runtime: "MaisakaHeartFlowChatting") -> float:
        if runtime._is_focus_mode_active_for_current_chat():
            return 1.0
        if self.base_reply_frequency(runtime.session_id) <= 0 or runtime._talk_frequency_adjust <= 0:
            return 0.0
        talk_value = float(ChatConfigUtils.get_talk_value(runtime.session_id, is_group_chat=True))
        if talk_value <= 0:
            return 0.0
        return max(0.0, talk_value * runtime._talk_frequency_adjust)

    def update_message_trigger(self, runtime: "MaisakaHeartFlowChatting", message: "SessionMessage") -> None:
        detected_mentioned, detected_at, boost = self.detect_mentions(message)
        if detected_at:
            message.is_at = True
        if detected_mentioned:
            message.is_mentioned = True
        if runtime._focus_group is not None:
            return
        timing = global_config.chat.reply_timing
        should_force = (
            boost >= 1.0
            or (message.is_at and timing.inevitable_at_reply)
            or (message.is_mentioned and timing.mentioned_bot_reply)
        )
        if should_force and (message.is_at or message.is_mentioned):
            runtime._arm_forced_turn(message, is_at=message.is_at, is_mentioned=message.is_mentioned)

    def finish_debounce(self, runtime: "MaisakaHeartFlowChatting", message: "SessionMessage", reason: str) -> str:
        return reason

    def history_target(self, session: "BotChatSession") -> Dict[str, str]:
        return {"platform": session.platform, "group_id": session.group_id}

    def allows_focus(self, session_id: str) -> bool:
        return True

    def allows_memory_from(self, source_is_group: bool) -> bool:
        return source_is_group or global_config.a_memorix.integration.heuristic_memory_private_to_group_enabled

    def quote_probability(
        self, context: "ReplyQuoteContext", bot_user_id: str, text: str, frequency: QuoteFrequency
    ) -> float:
        from .quoting import quote_probability

        return quote_probability(
            text_length=len(text.strip()),
            previous_sender_self=context.previous_sender_id == bot_user_id,
            previous_seconds=context.previous_seconds,
            target_distance=context.target_distance,
            frequency=frequency,
        )

    def collect_person_profiles(
        self,
        anchor_message: "SessionMessage",
        pending_messages: Optional[Sequence["SessionMessage"]],
        *,
        max_profiles: int,
    ) -> List["PersonProfileCandidate"]:
        from .memory import collect_person_profiles

        return collect_person_profiles(anchor_message, pending_messages, max_profiles=max_profiles)

    def detect_mentions(self, message: "SessionMessage") -> Tuple[bool, bool, float]:
        from .mentions import detect_mentions

        return detect_mentions(message)
