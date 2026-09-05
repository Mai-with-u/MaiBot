"""Maisaka 空闲退避状态。"""

from typing import Optional, TYPE_CHECKING
import time

from src.common.logger import get_logger
from src.config.config import global_config

from .mode_policy import is_idle_cycle_reason

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_idle_backoff")


class IdleBackoffController:
    """维护连续空闲结束后的消息触发退避。"""

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._count = 0
        self._until = 0.0

    def _get_backoff_seconds(self) -> float:
        base_seconds = max(0.0, float(global_config.chat.reply_timing.no_action_backoff_base_seconds))
        cap_seconds = max(0.0, float(global_config.chat.reply_timing.no_action_backoff_cap_seconds))
        if base_seconds <= 0 or cap_seconds <= 0:
            return 0.0
        start_count = max(1, int(global_config.chat.reply_timing.no_action_backoff_start_count))
        if self._count < start_count:
            return 0.0

        exponent = max(0, self._count - start_count)
        return min(cap_seconds, base_seconds * (2**exponent))

    def reset(self) -> None:
        """清理连续空闲退避状态。"""
        self._count = 0
        self._until = 0.0

    def record_cycle_result(self, cycle_end_reason: str) -> None:
        """按整轮结束原因维护空闲退避状态。"""
        normalized_reason = str(cycle_end_reason).strip()
        if not is_idle_cycle_reason(normalized_reason):
            self.reset()
            return

        runtime = self._runtime
        if not runtime.chat_stream.is_group_session:
            self.reset()
            return

        self._count += 1
        backoff_seconds = self._get_backoff_seconds()
        if backoff_seconds <= 0:
            self._until = 0.0
            return

        self._until = time.time() + backoff_seconds
        logger.info(
            f"{runtime.log_prefix} 连续空闲退避已更新: "
            "来源=planner "
            f"连续次数={self._count} "
            f"退避={backoff_seconds:.2f} 秒"
        )

    def should_delay(self, pending_count: int) -> Optional[float]:
        """判断当前消息触发是否应被空闲退避延迟。

        若应延迟，返回剩余秒数；调用方负责按该秒数延迟后再检查。
        """
        runtime = self._runtime
        if runtime._is_focus_mode_active_for_current_chat():
            self.reset()
            return None

        if not runtime.chat_stream.is_group_session:
            return None

        if self._until <= 0:
            return None

        remaining_seconds = self._until - time.time()
        if remaining_seconds <= 0:
            self._until = 0.0
            return None

        bypass_pending_count = max(0, int(global_config.chat.reply_timing.no_action_backoff_bypass_pending_count))
        if bypass_pending_count > 0 and pending_count >= bypass_pending_count:
            logger.info(
                f"{runtime.log_prefix} 空闲退避被待处理消息数绕过: "
                f"待处理={pending_count} 阈值={bypass_pending_count}"
            )
            return None

        logger.debug(f"{runtime.log_prefix} 空闲退避中，延迟 {remaining_seconds:.2f} 秒后再检查")
        return remaining_seconds
