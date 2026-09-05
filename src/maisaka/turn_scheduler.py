"""Maisaka 消息触发调度。"""

from typing import Sequence, TYPE_CHECKING

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.maisaka.focus import focus_mode_manager

from .turn_policy import TurnPolicy, TurnSnapshot

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")


class MessageTurnScheduler:
    """决定外部消息何时进入 Maisaka 内部循环。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._turn_policy = TurnPolicy(runtime)

    def score_reply_necessity(
        self,
        *,
        pending_messages: Sequence[SessionMessage],
        trigger_threshold: int,
    ) -> tuple[int, str]:
        """按当前 runtime 快照为待处理消息计算回复必要性评分。"""

        return self._turn_policy.score_reply_necessity(
            pending_messages=pending_messages,
            trigger_threshold=trigger_threshold,
        )

    def schedule_message_turn(self) -> None:
        runtime = self._runtime
        if not focus_mode_manager.can_decide(
            runtime.session_id,
            is_group_chat=runtime.chat_stream.is_group_session,
        ):
            logger.debug(f"{runtime.log_prefix} 当前不在 focus 状态，跳过 Maisaka 决策调度")
            return

        if runtime._agent_state == runtime._STATE_WAIT:
            if not runtime._is_reply_frequency_silent():
                if runtime.chat_stream.is_group_session:
                    return
                logger.info(f"{runtime.log_prefix} 私聊 wait 期间收到新消息，结束等待并进入 Planner")
                runtime._enter_running_state()
            else:
                runtime._enter_stop_state()

        if runtime._message_turn_scheduled:
            return

        pending_count = runtime._get_pending_message_count()
        if pending_count <= 0:
            return

        decision = self._turn_policy.decide(
            TurnSnapshot(
                pending_count=pending_count,
                pending_messages=runtime.message_cache[runtime._last_processed_index :],
                effective_frequency=runtime._get_effective_reply_frequency(),
                is_silent=runtime._is_reply_frequency_silent(),
                has_forced_trigger=runtime._has_forced_turn_trigger(),
                trigger_threshold=runtime._get_message_trigger_threshold(),
                log_prefix=runtime.log_prefix,
            )
        )
        if decision.action == "enqueue":
            runtime._enqueue_message_turn()
            return
        if decision.action == "delay" and decision.delay_seconds is not None:
            runtime._defer_message_turn_check(decision.delay_seconds)
