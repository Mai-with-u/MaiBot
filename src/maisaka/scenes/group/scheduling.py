"""群聊调度：频率门控、强制触发、空闲退避与插件门控评估。"""

from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

import asyncio
import math
import random
import time

from src.chat.message_receive.message import SessionMessage
from src.maisaka.context.message_adapter import parse_speaker_content
from src.maisaka.turn_trigger.gates import DynamicReplyTurnGate, TurnGateResult
from src.maisaka.turn_trigger.idle_backoff import IdleBackoffController
from src.maisaka.turn_trigger.messages import filter_external_messages
from src.maisaka.turn_trigger.scheduler import MessageTurnScheduler
from src.plugin_runtime.hook_payloads import serialize_session_message

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting


TURN_TRIGGER_EVALUATE_HOOK = "maisaka.turn_trigger.evaluate"
_EXTERNAL_DECISIONS = ("trigger", "wait", "delay", "consume")
_RECENT_HISTORY_LIMIT = 20


def _finite_unit(value: Any, field: str) -> float:
    """校验并返回一个 [0,1] 内的有限数值，拒绝 bool。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Hook 返回 {field} 必须是 number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"Hook 返回 {field} 必须落在 [0,1]")
    return number


def _finite_nonnegative(value: Any, field: str) -> float:
    """校验并返回一个有限的非负数值，拒绝 bool。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Hook 返回 {field} 必须是 number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"Hook 返回 {field} 必须是有限非负数")
    return number


def _non_bool_int(value: Any, field: str) -> int:
    """校验并返回一个整数，拒绝 bool。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Hook 返回 {field} 必须是 integer")
    return value


class GroupTurnScheduler(MessageTurnScheduler):
    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        super().__init__(runtime)
        self._dynamic_reply_gate = DynamicReplyTurnGate(runtime)
        self._idle_backoff = IdleBackoffController(runtime)
        self._last_wait_batch = None

    def _resume_wait_on_message(self) -> bool:
        runtime = self._runtime
        batch = (runtime._pending_wait_started_at, len(runtime.message_cache))
        if batch == self._last_wait_batch:
            return False
        self._last_wait_batch = batch
        duration = runtime._pending_wait_seconds
        started = runtime._pending_wait_started_at
        if duration is None or started is None:
            return False
        progress = 1.0 if duration <= 0 else min(1.0, max(0.0, (time.time() - started) / duration))
        # p(t)=0.16t²+0.08t：起点 0%，半程 8%，到期 24%；定时到期仍必定唤醒。
        probability = 0.16 * progress * progress + 0.08 * progress
        return random.random() < probability

    def record_reply(self) -> None:
        self._dynamic_reply_gate.record_reply()

    def record_cycle_result(self, cycle_end_reason: str) -> None:
        self._idle_backoff.record_cycle_result(cycle_end_reason)

    def reset_backoff(self) -> None:
        self._idle_backoff.reset()

    def _evaluate_scene(self, pending_count: int, frequency: float) -> TurnGateResult:
        runtime = self._runtime
        if runtime._has_forced_turn_trigger():
            # @ 强制触发的回复同样占用动态门控的回复额度
            self._dynamic_reply_gate.record_forced_turn(runtime.message_cache[runtime._last_processed_index :])
            return TurnGateResult(decision="trigger", detail=f"调度=强制触发 频率={frequency:.3f}")

        backoff_seconds = self._idle_backoff.get_delay_seconds(pending_count)
        if backoff_seconds > 0:
            return TurnGateResult(
                decision="delay",
                detail=f"空闲退避中 剩余={backoff_seconds:.2f}s",
                delay_seconds=backoff_seconds,
            )

        # 普通动态门控分支交由内部循环的轻量评估处理，调度线程不发起远程请求。
        return TurnGateResult(decision="review", detail="调度=插件门控评估")

    async def review_message_turn(self, pending_messages: Sequence[SessionMessage]) -> Optional[TurnGateResult]:
        """在 Planner 前对整批消息做一次可取消的插件门控评估。"""

        runtime = self._runtime
        if not pending_messages:
            return TurnGateResult(decision="wait", detail="插件门控评估: 空批次")

        # 复核强制触发、退避与当前频率，避免异步评估期间状态漂移。
        frequency = runtime._get_effective_reply_frequency()
        if runtime._has_forced_turn_trigger():
            self._dynamic_reply_gate.record_forced_turn(runtime.message_cache[runtime._last_processed_index :])
            return TurnGateResult(decision="trigger", detail=f"插件门控评估=强制触发 频率={frequency:.3f}")
        backoff_seconds = self._idle_backoff.get_delay_seconds(len(pending_messages))
        if backoff_seconds > 0:
            return TurnGateResult(
                decision="delay",
                detail=f"插件门控评估=空闲退避 剩余={backoff_seconds:.2f}s",
                delay_seconds=backoff_seconds,
            )
        if frequency <= 0:
            return TurnGateResult(decision="delay", detail="插件门控评估=频率降为零", delay_seconds=0.0)

        snapshot: Tuple[SessionMessage, ...] = tuple(pending_messages)
        external_messages = filter_external_messages(snapshot, log_prefix=runtime.log_prefix)
        if not external_messages:
            return TurnGateResult(decision="wait", detail="插件门控评估: 没有新的外部消息")

        baseline = self._dynamic_reply_gate.evaluate(
            pending_messages=snapshot,
            frequency=frequency,
            finalize=False,
        )
        hook_input = self._build_evaluate_payload(snapshot, baseline, frequency)

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        runtime._message_turn_changed.clear()
        hook_task = asyncio.create_task(
            get_plugin_runtime_manager().invoke_hook(TURN_TRIGGER_EVALUATE_HOOK, **hook_input)
        )
        change_task = asyncio.create_task(runtime._message_turn_changed.wait())
        try:
            done, _ = await asyncio.wait(
                {hook_task, change_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            invalidated = (
                change_task in done
                or runtime._message_turn_changed.is_set()
                or not runtime._running
                or runtime._focus_group is not None
                or runtime._has_forced_turn_trigger()
                or not self._batch_matches(snapshot)
            )
            if invalidated:
                # 输入变化或停止/Focus 时整批失效，交由原有静默/调度路径重查。
                return TurnGateResult(decision="delay", detail="插件门控评估: 批次已失效", delay_seconds=0.0)

            hook_result = hook_task.result()
            final_kwargs: Dict[str, Any] = dict(hook_input)
            final_kwargs.update(dict(hook_result.kwargs or {}))
        finally:
            for task in (hook_task, change_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(hook_task, change_task, return_exceptions=True)

        return self._resolve_review_result(final_kwargs, baseline, snapshot)

    def _build_evaluate_payload(
        self,
        snapshot: Tuple[SessionMessage, ...],
        baseline: TurnGateResult,
        frequency: float,
    ) -> Dict[str, Any]:
        """构造通用 Hook 载荷，不携带任何 Jev 专用字段。"""

        runtime = self._runtime
        pending_payload: List[Dict[str, Any]] = []
        for message in snapshot:
            item = serialize_session_message(message)
            item["is_at"] = bool(message.is_at)
            item["is_mentioned"] = bool(message.is_mentioned)
            pending_payload.append(item)

        recent: List[Dict[str, Any]] = []
        for message in runtime._chat_history:
            if not message.count_in_context:
                continue
            speaker, text = parse_speaker_content(message.processed_plain_text or "")
            recent.append(
                {
                    "sender": speaker or "",
                    "text": text,
                    "is_self": message.source == "guided_reply",
                }
            )
        recent = recent[-_RECENT_HISTORY_LIMIT:]

        return {
            "session_id": runtime.session_id,
            "is_group_session": True,
            "frequency": frequency,
            "probability": baseline.probability,
            "threshold": baseline.threshold,
            "decision": baseline.decision,
            "detail": baseline.detail,
            "delay_seconds": 0.0,
            "consumed_count": len(snapshot),
            "pending_messages": pending_payload,
            "recent_messages": recent,
        }

    def _batch_matches(self, snapshot: Tuple[SessionMessage, ...]) -> bool:
        """用对象身份与顺序比对当前待处理批次，忽略缓存头部裁剪。"""

        current = tuple(self._runtime.message_cache[self._runtime._last_processed_index :])
        return len(current) == len(snapshot) and all(a is b for a, b in zip(current, snapshot, strict=False))

    def _resolve_review_result(
        self,
        final_kwargs: Dict[str, Any],
        baseline: TurnGateResult,
        snapshot: Tuple[SessionMessage, ...],
    ) -> TurnGateResult:
        """校验插件返回的补丁并交回本地动态门控重新裁决。"""

        runtime = self._runtime
        probability = _finite_unit(final_kwargs["probability"], "probability")
        threshold = _finite_unit(final_kwargs["threshold"], "threshold")
        decision = final_kwargs["decision"]
        if decision not in _EXTERNAL_DECISIONS:
            raise ValueError(f"Hook 返回 decision 非法: {decision!r}")
        delay_seconds = _finite_nonnegative(final_kwargs["delay_seconds"], "delay_seconds")
        consumed_count = _non_bool_int(final_kwargs["consumed_count"], "consumed_count")

        # 评估完成后重新读取频率，降为零时沿用原有静默路径。
        current_frequency = runtime._get_effective_reply_frequency()
        if current_frequency <= 0:
            return TurnGateResult(decision="delay", detail="插件门控评估=频率降为零", delay_seconds=0.0)

        if consumed_count < 0:
            raise ValueError("Hook 返回 consumed_count 不能为负")
        if consumed_count == 0:
            effective_consumed = len(snapshot)
        else:
            effective_consumed = min(consumed_count, len(snapshot))

        if decision in ("consume", "delay"):
            decision_override: Optional[str] = decision
        elif decision != baseline.decision:
            decision_override = decision
        else:
            decision_override = None

        threshold_override: Optional[float] = None
        if threshold != baseline.threshold:
            threshold_override = threshold

        return self._dynamic_reply_gate.apply_external_probability(
            probability=probability,
            frequency=current_frequency,
            evaluated_at=baseline.evaluated_at,
            threshold_override=threshold_override,
            decision_override=decision_override,
            consumed_count=effective_consumed,
            delay_seconds=delay_seconds,
            detail=str(final_kwargs.get("detail") or ""),
        )

    def record_message_consumed(self) -> None:
        """静默消费成功后结束当前虚拟轮次。"""

        self._dynamic_reply_gate.close_round()
