"""Maisaka 消息触发调度。"""

from typing import TYPE_CHECKING

from src.common.logger import get_logger
from src.config.config import global_config

from .gates import DynamicReplyTurnGate, TurnGateResult

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")


class MessageTurnScheduler:
    """决定外部消息何时进入 Maisaka 内部循环。

    每次检查依次经过：消息防抖 → 快速反应续接 wait → 静默消费 → @ 强制触发 → 空闲退避 → 动态门控。
    每一步只返回判定，由 ``schedule_message_turn`` 统一投递或延迟重查。
    """

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._dynamic_reply_gate = DynamicReplyTurnGate(runtime)

    def record_reply(self) -> None:
        """记录 Planner 实际调用了一次 reply，供动态门控统计回复次数。"""

        self._dynamic_reply_gate.record_reply()

    def schedule_message_turn(self) -> None:
        runtime = self._runtime
        if runtime._agent_state == runtime._STATE_WAIT:
            if runtime._is_reply_frequency_silent():
                runtime._enter_stop_state()
            elif not global_config.chat.reply_timing.planner_message_steering:
                if runtime.chat_stream.is_group_session:
                    return
                logger.info(f"{runtime.log_prefix} 私聊 wait 期间收到新消息，结束等待并进入 Planner")
                runtime._enter_running_state()

        if runtime._message_turn_scheduled:
            return

        pending_count = runtime._get_pending_message_count()
        if pending_count <= 0:
            return

        result = self._evaluate(pending_count)
        if result.decision == "delay":
            logger.debug(f"{runtime.log_prefix} 回复调度 待处理={pending_count} {result.detail}")
            runtime._defer_message_turn_check(result.delay_seconds)
            return

        logger.info(f"{runtime.log_prefix} 回复调度 待处理={pending_count} {result.detail}")
        if result.should_trigger:
            if runtime._agent_state == runtime._STATE_WAIT:
                runtime._cancel_wait_timeout_task()
                # 保留挂起状态到消费触发时，避免原工具批次收尾清掉 wait 调用关联。
                runtime._update_stage_status("等待已打断", "收到新消息，准备继续思考")
            runtime._enqueue_message_turn()

    def _evaluate(self, pending_count: int) -> TurnGateResult:
        """按流水线顺序返回第一个给出结论的判定。"""

        runtime = self._runtime
        # 先收齐本批：后续门控按整批消息判断，防止只凭半批消息估计回复概率。
        quiet_seconds = runtime._get_message_quiet_period_remaining_seconds()
        if quiet_seconds > 0:
            return TurnGateResult(
                decision="delay",
                detail=f"等待消息防抖收齐本批 剩余={quiet_seconds:.3f}s",
                delay_seconds=quiet_seconds,
            )

        # 走到这里仍处于 wait 的只有快速反应模式：直接续接挂起的 wait，不再经过群聊频率与退避门控。
        if runtime._agent_state == runtime._STATE_WAIT:
            return TurnGateResult(decision="trigger", detail="快速反应模式收到新消息，提前结束 wait 并进入 Planner")

        frequency = runtime._get_effective_reply_frequency()
        if runtime._is_reply_frequency_silent():
            return TurnGateResult(decision="trigger", detail=f"调度=静默消费 频率={frequency:.3f}")

        if runtime._has_forced_turn_trigger():
            # @ 强制触发的回复同样占用动态门控的回复额度
            self._dynamic_reply_gate.record_forced_turn(runtime.message_cache[runtime._last_processed_index :])
            return TurnGateResult(decision="trigger", detail=f"调度=强制触发 频率={frequency:.3f}")

        backoff_seconds = runtime._idle_backoff.get_delay_seconds(pending_count)
        if backoff_seconds > 0:
            return TurnGateResult(
                decision="delay",
                detail=f"空闲退避中 剩余={backoff_seconds:.2f}s",
                delay_seconds=backoff_seconds,
            )

        dynamic_result = self._dynamic_reply_gate.evaluate(
            pending_messages=runtime.message_cache[runtime._last_processed_index :],
            frequency=frequency,
        )
        return TurnGateResult(
            decision=dynamic_result.decision,
            detail=f"频率={frequency:.3f} {dynamic_result.detail}",
        )
