"""私聊运行行为，不读取群聊频率、门控或退避设置。"""

from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple
import time

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

from .prompting import PrivatePromptProfile


class PrivateChatScene(ChatScene):
    is_group = False
    silent_reason = "私聊回复已暂停"
    prompts = PrivatePromptProfile()

    def create_scheduler(self, runtime: "MaisakaHeartFlowChatting") -> "MessageTurnScheduler":
        from .scheduling import PrivateTurnScheduler

        return PrivateTurnScheduler(runtime)

    @property
    def max_context_size(self) -> int:
        return max(1, int(global_config.chat.max_private_context_size))

    @property
    def debounce_mode(self) -> DebounceMode:
        return global_config.chat.reply_timing.private_message_debounce_mode

    @property
    def quote_frequency(self) -> QuoteFrequency:
        return global_config.chat.reply_style.private_quote_frequency

    @property
    def allow_reply_at(self) -> bool:
        return False

    def base_reply_frequency(self, session_id: str) -> float:
        return 0.0 if ChatConfigUtils.is_private_reply_paused(session_id) else 1.0

    def effective_reply_frequency(self, runtime: "MaisakaHeartFlowChatting") -> float:
        # 私聊只使用暂停开关，忽略旧频率、动态频率规则与插件频率倍率。
        return self.base_reply_frequency(runtime.session_id)

    def update_message_trigger(self, runtime: "MaisakaHeartFlowChatting", message: "SessionMessage") -> None:
        mentioned, _, _ = self.detect_mentions(message)
        # 私聊没有 @ 触发；提及独立于群聊开关，用于立即结束等待。
        message.is_at = False
        message.is_mentioned = mentioned
        if mentioned and runtime._focus_group is None:
            runtime._arm_forced_turn(message, is_at=False, is_mentioned=True)

    def finish_debounce(self, runtime: "MaisakaHeartFlowChatting", message: "SessionMessage", reason: str) -> str:
        if message.is_mentioned:
            runtime._fixed_message_debouncer.finish(now=time.monotonic())
            return "私聊提及，立即结束防抖等待"
        return reason

    def history_target(self, session: "BotChatSession") -> Dict[str, str]:
        return {"platform": session.platform, "user_id": session.user_id}

    def allows_focus(self, session_id: str) -> bool:
        return global_config.experimental.focus_on_private and not ChatConfigUtils.is_private_reply_paused(session_id)

    def allows_memory_from(self, source_is_group: bool) -> bool:
        return not source_is_group or global_config.a_memorix.integration.heuristic_memory_group_to_private_enabled

    def quote_probability(
        self, context: "ReplyQuoteContext", bot_user_id: str, text: str, frequency: QuoteFrequency
    ) -> float:
        from .quoting import private_quote_probability

        return private_quote_probability(
            text_length=len(text.strip()),
            previous_sender_self=context.previous_sender_id == bot_user_id,
            previous_seconds=context.previous_seconds,
            target_distance=context.target_distance,
            frequency=frequency,
            previous_burst=context.previous_burst,
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
