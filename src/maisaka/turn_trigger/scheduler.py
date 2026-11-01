"""Maisaka 消息触发调度。"""

from typing import Any, List, Optional, Sequence, TYPE_CHECKING
import asyncio
import inspect

from src.chat.message_receive.message import SessionMessage
from src.common.logger import get_logger
from src.config.config import global_config
from src.maisaka.context.message_adapter import parse_speaker_content
from src.plugin_runtime.hook_payloads import serialize_session_message

from .gates import DynamicReplyTurnGate, TurnGateResult

if TYPE_CHECKING:
    from src.maisaka.runtime import MaisakaHeartFlowChatting

logger = get_logger("maisaka_turn_scheduler")

TURN_TRIGGER_EVALUATE_HOOK = "maisaka.turn_trigger.evaluate"
RECENT_HISTORY_HOOK_LIMIT = 20


class MessageTurnScheduler:
    """决定外部消息何时进入 Maisaka 内部循环。

    每次检查依次经过：消息防抖 → 快速反应续接 wait → 静默消费 → @ 强制触发 → 空闲退避 → 动态门控（及可选门控 Hook）。
    每一步只返回判定，由 ``schedule_message_turn`` 统一投递或延迟重查。
    """

    def __init__(self, runtime: "MaisakaHeartFlowChatting") -> None:
        self._runtime = runtime
        self._dynamic_reply_gate = DynamicReplyTurnGate(runtime)
        self._hook_eval_task: Optional[asyncio.Task[None]] = None
        self._hook_evaluated_message: Optional[Any] = None

    def _get_evaluated_target_index(self) -> int:
        """根据最后一条已评估消息动态获取其在当前 message_cache 中的下一个处理下标。

        Returns:
            int: 已评估消息在当前缓存中的切片终点下标；若无记录或消息已被裁剪弹出，则返回 0。
        """

        if self._hook_evaluated_message is None:
            return 0
        cache = self._runtime.message_cache
        for idx in range(len(cache) - 1, -1, -1):
            msg = cache[idx]
            if msg is self._hook_evaluated_message or getattr(msg, "message_id", None) == getattr(
                self._hook_evaluated_message, "message_id", ""
            ):
                return idx + 1
        return 0
    @staticmethod
    def _get_runtime_manager() -> Any:
        """获取插件运行时管理器单例。"""

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        return get_plugin_runtime_manager()

    def _has_evaluate_hook_subscribers(self) -> bool:
        """检查当前是否有插件订阅了门控评估 Hook。

        Returns:
            bool: 存在有效订阅者时返回 True。
        """

        try:
            manager = self._get_runtime_manager()
            dispatcher = getattr(manager, "_hook_dispatcher", None)
            if dispatcher is None:
                return False
            if hasattr(dispatcher, "has_hook_subscribers") and callable(dispatcher.has_hook_subscribers):
                return bool(dispatcher.has_hook_subscribers(TURN_TRIGGER_EVALUATE_HOOK))
            supervisors = dispatcher._resolve_supervisors()
            return bool(dispatcher._collect_invocation_targets(TURN_TRIGGER_EVALUATE_HOOK, supervisors))
        except Exception as exc:
            logger.debug(f"{self._runtime.log_prefix} 检查门控 Hook 订阅者失败: {exc}")
            return False

    def cancel_pending_tasks(self) -> None:
        """取消正在进行的异步门控 Hook 评估任务。"""

        if self._hook_eval_task is not None:
            self._hook_eval_task.cancel()
            self._hook_eval_task = None

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
        if self._should_schedule_async_hook(result):
            self._schedule_hook_evaluation(result, pending_count)
            return

        self._apply_turn_gate_result(result, pending_count)

    def _apply_turn_gate_result(self, result: TurnGateResult, pending_count: int) -> None:
        """按门控判定结果执行延迟、静默消费或投递 Planner。"""

        runtime = self._runtime
        if result.decision == "delay":
            logger.debug(f"{runtime.log_prefix} 回复调度 待处理={pending_count} {result.detail}")
            runtime._defer_message_turn_check(result.delay_seconds)
            return

        logger.info(f"{runtime.log_prefix} 回复调度 待处理={pending_count} {result.detail}")
        if getattr(result, "should_consume", False) or result.decision == "consume":
            self._schedule_silent_consume(getattr(result, "consumed_count", None))
            return

        if result.should_trigger:
            if runtime._agent_state == runtime._STATE_WAIT:
                runtime._cancel_wait_timeout_task()
                # 保留挂起状态到消费触发时，避免原工具批次收尾清掉 wait 调用关联。
                runtime._update_stage_status("等待已打断", "收到新消息，准备继续思考")
            runtime._enqueue_message_turn()

    def _schedule_silent_consume(self, consumed_count: Optional[int]) -> None:
        """执行静默已读游标推进。"""

        consume_fn = getattr(self._runtime, "_consume_pending_messages_silently", None)
        if consume_fn is None:
            return
        outcome = consume_fn(consumed_count)
        if inspect.isawaitable(outcome):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(outcome)
            except RuntimeError:
                pass

    def _should_schedule_async_hook(self, result: TurnGateResult) -> bool:
        """判断是否需要将本次动态门控结果交给异步 Hook 进一步评估。"""

        runtime = self._runtime
        if not getattr(runtime.chat_stream, "is_group_session", False):
            return False
        if result.decision not in ("trigger", "wait"):
            return False
        if not result.detail.startswith("频率="):
            return False
        return self._has_evaluate_hook_subscribers()

    def _schedule_hook_evaluation(self, base_result: TurnGateResult, pending_count: int) -> None:
        """安排一次异步门控 Hook 调用，避免对同一批待处理消息重复发起远程请求。

        Args:
            base_result: 本地动态门控产生的基准判定结果。
            pending_count: 当前待处理外部消息数量。
        """

        runtime = self._runtime
        if self._hook_eval_task is not None and not self._hook_eval_task.done():
            logger.debug(f"{runtime.log_prefix} 门控 Hook 评估进行中，跳过重复调度")
            return

        start_index = runtime._last_processed_index
        target_index = len(runtime.message_cache)
        evaluated_index = max(self._get_evaluated_target_index(), start_index)
        if target_index <= evaluated_index:
            logger.debug(f"{runtime.log_prefix} 待处理消息已由门控 Hook 评估过，等待新消息")
            return

        target_message = runtime.message_cache[target_index - 1] if target_index > 0 else None
        self._hook_eval_task = asyncio.create_task(
            self._run_hook_evaluation(
                base_result=base_result,
                start_index=start_index,
                target_index=target_index,
                target_message=target_message,
            )
        )

    async def _run_hook_evaluation(
        self,
        *,
        base_result: TurnGateResult,
        start_index: int,
        target_index: int,
        target_message: Optional[Any] = None,
    ) -> None:
        """在异步任务中调用 maisaka.turn_trigger.evaluate 并应用合并结果。

        Args:
            base_result: 本地动态门控产生的基准判定结果。
            start_index: 本次待评估消息在缓存中的起始下标。
            target_index: 本次待评估消息在缓存中的目标终止下标。
            target_message: 本次评估覆盖的最后一条消息对象引用。
        """

        runtime = self._runtime
        try:
            if not getattr(runtime, "_running", True) or runtime._message_turn_scheduled:
                return

            pending_messages = runtime.message_cache[runtime._last_processed_index : target_index]
            if not pending_messages:
                return

            frequency = runtime._get_effective_reply_frequency()
            base_prob = float(getattr(base_result, "probability", 0.0) or 0.0)
            base_threshold = float(getattr(base_result, "threshold", 0.0) or 0.0)
            pending_count = max(0, target_index - start_index)

            hook_result = await self._get_runtime_manager().invoke_hook(
                TURN_TRIGGER_EVALUATE_HOOK,
                session_id=str(getattr(runtime, "session_id", "") or ""),
                is_group_session=bool(runtime.chat_stream.is_group_session),
                frequency=frequency,
                probability=base_prob,
                threshold=base_threshold,
                decision=base_result.decision,
                detail=base_result.detail,
                delay_seconds=float(getattr(base_result, "delay_seconds", 0.0) or 0.0),
                consumed_count=pending_count,
                pending_messages=self._serialize_pending_messages(pending_messages),
                recent_messages=self._serialize_recent_history(),
            )
            self._hook_evaluated_message = target_message

            if not getattr(runtime, "_running", True) or runtime._message_turn_scheduled:
                return
            if runtime._has_forced_turn_trigger():
                self.schedule_message_turn()
                return

            final_result = self._resolve_hook_result(
                base_result=base_result,
                hook_result=hook_result,
                frequency=frequency,
                default_consumed_count=pending_count,
            )
            current_pending_count = runtime._get_pending_message_count()
            if final_result.should_consume:
                logger.info(f"{runtime.log_prefix} 回复调度 待处理={current_pending_count} {final_result.detail}")
                consume_fn = getattr(runtime, "_consume_pending_messages_silently", None)
                if consume_fn is not None:
                    outcome = consume_fn(final_result.consumed_count)
                    if inspect.isawaitable(outcome):
                        await outcome
            else:
                self._apply_turn_gate_result(final_result, current_pending_count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"{runtime.log_prefix} 门控 Hook 评估异常，回退本地判定: {exc}", exc_info=True)
            self._hook_evaluated_message = target_message
            if getattr(runtime, "_running", True) and not runtime._message_turn_scheduled:
                self._apply_turn_gate_result(base_result, runtime._get_pending_message_count())
        finally:
            self._hook_eval_task = None
            if (
                getattr(runtime, "_running", True)
                and not runtime._message_turn_scheduled
                and len(runtime.message_cache) > self._get_evaluated_target_index()
                and runtime._get_pending_message_count() > 0
            ):
                self.schedule_message_turn()
    def _resolve_hook_result(
        self,
        *,
        base_result: TurnGateResult,
        hook_result: Any,
        frequency: float,
        default_consumed_count: int,
    ) -> TurnGateResult:
        """将 Hook 返回的 kwargs 合并为最终的 TurnGateResult。"""

        if getattr(hook_result, "aborted", False):
            return TurnGateResult(
                decision="wait",
                detail=f"{base_result.detail} [Hook中止={getattr(hook_result, 'stopped_by', '') or 'unknown'}]",
                probability=getattr(base_result, "probability", 0.0),
                threshold=getattr(base_result, "threshold", 0.0),
            )

        kwargs = getattr(hook_result, "kwargs", {}) or {}
        raw_decision = str(kwargs.get("decision", base_result.decision) or base_result.decision).strip().lower()
        if raw_decision not in {"trigger", "wait", "delay", "consume"}:
            raw_decision = base_result.decision

        base_prob = float(getattr(base_result, "probability", 0.0) or 0.0)
        base_threshold = float(getattr(base_result, "threshold", 0.0) or 0.0)
        new_prob = self._coerce_float(kwargs.get("probability"), base_prob)
        new_threshold = self._coerce_float(kwargs.get("threshold"), base_threshold)
        extra_detail = str(kwargs.get("detail", base_result.detail) or base_result.detail).strip()

        prob_changed = abs(new_prob - base_prob) > 1e-6
        threshold_changed = abs(new_threshold - base_threshold) > 1e-6
        decision_explicitly_changed = raw_decision != base_result.decision

        if raw_decision == "consume":
            raw_count = self._coerce_int(kwargs.get("consumed_count"), default_consumed_count)
            consumed_count = min(default_consumed_count, raw_count) if raw_count > 0 else default_consumed_count
            self._dynamic_reply_gate.close_round()
            return TurnGateResult(
                decision="consume",
                detail=f"{extra_detail} [Hook判定=静默已读 消费={consumed_count}条]",
                probability=new_prob,
                threshold=new_threshold,
                consumed_count=consumed_count,
            )

        if raw_decision == "delay":
            delay_seconds = max(0.0, self._coerce_float(kwargs.get("delay_seconds"), base_result.delay_seconds))
            return TurnGateResult(
                decision="delay",
                detail=extra_detail,
                delay_seconds=delay_seconds,
                probability=new_prob,
                threshold=new_threshold,
            )

        if (prob_changed or threshold_changed) and not decision_explicitly_changed:
            recomputed = self._dynamic_reply_gate.apply_external_probability(
                probability=new_prob,
                frequency=frequency,
                threshold_override=new_threshold if threshold_changed else None,
            )
            suffix = f" {extra_detail}" if extra_detail and extra_detail != base_result.detail else ""
            return TurnGateResult(
                decision=recomputed.decision,
                detail=f"频率={frequency:.3f} {recomputed.detail}{suffix}",
                probability=recomputed.probability,
                threshold=recomputed.threshold,
            )

        if decision_explicitly_changed and raw_decision == "trigger":
            self._dynamic_reply_gate.close_round()

        return TurnGateResult(
            decision=raw_decision,  # type: ignore[arg-type]
            detail=extra_detail,
            probability=new_prob,
            threshold=new_threshold,
        )

    @staticmethod
    def _serialize_pending_messages(messages: Sequence[SessionMessage]) -> List[dict[str, Any]]:
        """序列化待处理消息列表供 Hook 消费。"""

        serialized: List[dict[str, Any]] = []
        for msg in messages:
            try:
                payload = serialize_session_message(msg)
            except Exception:
                user_info = getattr(getattr(msg, "message_info", None), "user_info", None)
                sender = (
                    getattr(user_info, "user_cardname", None)
                    or getattr(user_info, "user_nickname", None)
                    or getattr(user_info, "user_id", "")
                )
                payload = {
                    "message_id": str(getattr(msg, "message_id", "") or ""),
                    "processed_plain_text": str(getattr(msg, "processed_plain_text", "") or ""),
                    "sender_name": str(sender or ""),
                }
            payload["is_at"] = bool(getattr(msg, "is_at", False))
            payload["is_mentioned"] = bool(getattr(msg, "is_mentioned", False))
            serialized.append(payload)
        return serialized

    def _serialize_recent_history(self, limit: int = RECENT_HISTORY_HOOK_LIMIT) -> List[dict[str, Any]]:
        """提取近期聊天历史供 Hook 构建上下文。"""

        chat_history = getattr(self._runtime, "_chat_history", None) or []
        bot_name = str(getattr(getattr(global_config, "bot", None), "nickname", "") or "").strip()
        recent: List[dict[str, Any]] = []
        for item in list(chat_history)[-limit:]:
            if not getattr(item, "count_in_context", True):
                continue
            raw_text = str(getattr(item, "processed_plain_text", "") or getattr(item, "visible_text", "") or "").strip()
            if not raw_text:
                continue
            speaker, content = parse_speaker_content(raw_text)
            is_self = getattr(item, "source", "") == "guided_reply" or getattr(item, "source_kind", "") == "guided_reply"
            recent.append(
                {
                    "sender": speaker or (bot_name if is_self else ""),
                    "text": (content or raw_text).strip(),
                    "is_self": bool(is_self),
                }
            )
        return recent

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        """将任意输入安全转换为浮点数。

        Args:
            value: 待转换的值。
            default: 转换失败时的默认值。

        Returns:
            float: 转换后的浮点数值。
        """

        if value is None or isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        """将任意输入安全转换为整数。

        Args:
            value: 待转换的值。
            default: 转换失败时的默认值。

        Returns:
            int: 转换后的整数值。
        """

        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
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
            probability=getattr(dynamic_result, "probability", 0.0),
            threshold=getattr(dynamic_result, "threshold", 0.0),
        )
