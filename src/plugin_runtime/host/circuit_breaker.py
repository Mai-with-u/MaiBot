"""插件运行时熔断器。"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal

import time

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.host.circuit_breaker")

CircuitStateName = Literal["closed", "open", "half_open"]

_FAILURE_THRESHOLD = 3
_BASE_COOLDOWN_SEC = 60.0
_MAX_COOLDOWN_SEC = 300.0
_STABLE_RESET_SEC = 600.0
_SKIP_LOG_INTERVAL_SEC = 30.0


@dataclass(slots=True)
class CircuitPermit:
    """一次插件调用的熔断许可。"""

    plugin_id: str
    component_name: str
    operation: str
    allowed: bool
    half_open: bool = False
    reason: str = ""


@dataclass(slots=True)
class _CircuitState:
    """单个插件的熔断状态。"""

    state: CircuitStateName = "closed"
    consecutive_failures: int = 0
    cooldown_level: int = 0
    opened_until: float = 0.0
    half_open_inflight: bool = False
    last_success_at: float = 0.0
    last_recovered_at: float = 0.0
    last_skip_log_at: float = 0.0


class PluginCircuitBreaker:
    """按插件 ID 隔离的运行时熔断器。"""

    def __init__(
        self,
        *,
        failure_threshold: int = _FAILURE_THRESHOLD,
        base_cooldown_sec: float = _BASE_COOLDOWN_SEC,
        max_cooldown_sec: float = _MAX_COOLDOWN_SEC,
        stable_reset_sec: float = _STABLE_RESET_SEC,
    ) -> None:
        self._failure_threshold = max(int(failure_threshold), 1)
        self._base_cooldown_sec = max(float(base_cooldown_sec), 1.0)
        self._max_cooldown_sec = max(float(max_cooldown_sec), self._base_cooldown_sec)
        self._stable_reset_sec = max(float(stable_reset_sec), 1.0)
        self._states: Dict[str, _CircuitState] = {}

    def try_acquire(self, plugin_id: str, component_name: str, operation: str) -> CircuitPermit:
        """尝试为一次插件调用取得执行许可。"""

        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            raise ValueError("plugin_id 不能为空")

        now = time.monotonic()
        state = self._states.setdefault(normalized_plugin_id, _CircuitState())
        if state.state == "open" and now < state.opened_until:
            remaining_sec = max(state.opened_until - now, 0.0)
            self._log_skip_if_needed(normalized_plugin_id, component_name, operation, remaining_sec, state, now)
            return CircuitPermit(
                plugin_id=normalized_plugin_id,
                component_name=component_name,
                operation=operation,
                allowed=False,
                reason=f"插件熔断中，剩余 {remaining_sec:.1f}s",
            )

        if state.state == "open" and now >= state.opened_until:
            state.state = "half_open"
            state.half_open_inflight = False
            logger.info(f"插件 {normalized_plugin_id} 熔断冷却结束，进入半开测试")

        if state.state == "half_open":
            if state.half_open_inflight:
                return CircuitPermit(
                    plugin_id=normalized_plugin_id,
                    component_name=component_name,
                    operation=operation,
                    allowed=False,
                    reason="插件半开测试已有进行中的调用",
                )
            state.half_open_inflight = True
            return CircuitPermit(
                plugin_id=normalized_plugin_id,
                component_name=component_name,
                operation=operation,
                allowed=True,
                half_open=True,
            )

        return CircuitPermit(
            plugin_id=normalized_plugin_id,
            component_name=component_name,
            operation=operation,
            allowed=True,
        )

    def record_success(self, permit: CircuitPermit) -> None:
        """记录一次插件调用成功。"""

        if not permit.allowed:
            return

        now = time.monotonic()
        state = self._states.setdefault(permit.plugin_id, _CircuitState())
        was_half_open = state.state == "half_open" or permit.half_open
        self._reset_cooldown_if_stable(state, now)
        state.consecutive_failures = 0
        state.half_open_inflight = False
        state.last_success_at = now

        if was_half_open:
            state.state = "closed"
            state.last_recovered_at = now
            logger.info(f"插件 {permit.plugin_id} 半开测试成功，熔断已恢复")
            return

    def record_failure(self, permit: CircuitPermit, reason: str) -> None:
        """记录一次可熔断失败。"""

        if not permit.allowed:
            return

        now = time.monotonic()
        state = self._states.setdefault(permit.plugin_id, _CircuitState())
        self._reset_cooldown_if_stable(state, now)
        state.half_open_inflight = False

        if permit.half_open or state.state == "half_open":
            self._open_circuit(permit, reason, state, now)
            return

        state.consecutive_failures += 1
        if state.consecutive_failures < self._failure_threshold:
            logger.warning(
                f"插件 {permit.plugin_id} 调用失败，熔断计数 "
                f"{state.consecutive_failures}/{self._failure_threshold}: {reason}"
            )
            return

        self._open_circuit(permit, reason, state, now)

    def _open_circuit(self, permit: CircuitPermit, reason: str, state: _CircuitState, now: float) -> None:
        """打开指定插件的熔断状态。"""

        cooldown_sec = min(self._base_cooldown_sec * (2**state.cooldown_level), self._max_cooldown_sec)
        state.state = "open"
        state.consecutive_failures = 0
        state.cooldown_level += 1
        state.opened_until = now + cooldown_sec
        state.half_open_inflight = False
        state.last_skip_log_at = 0.0
        logger.warning(
            f"插件 {permit.plugin_id} 已熔断 {cooldown_sec:.0f}s: "
            f"component={permit.component_name} operation={permit.operation} reason={reason}"
        )

    def _reset_cooldown_if_stable(self, state: _CircuitState, now: float) -> None:
        """插件恢复后稳定足够久时清零退避等级。"""

        if state.last_recovered_at <= 0:
            return
        if now - state.last_recovered_at < self._stable_reset_sec:
            return
        state.cooldown_level = 0
        state.last_recovered_at = 0.0

    def forget_plugins(self, plugin_ids: Iterable[str]) -> int:
        """清除指定插件（例如已卸载）的熔断状态，避免状态按插件 ID 无限累积。

        Args:
            plugin_ids: 待清除状态的插件 ID 列表。

        Returns:
            int: 实际清除的插件状态数量。
        """
        removed_count = 0
        for plugin_id in plugin_ids:
            normalized_plugin_id = str(plugin_id or "").strip()
            if not normalized_plugin_id:
                continue
            if self._states.pop(normalized_plugin_id, None) is not None:
                removed_count += 1
        return removed_count

    def get_plugin_statuses(self) -> Dict[str, Dict[str, Any]]:
        """返回当前存在熔断状态的插件快照。"""

        now = time.monotonic()
        statuses: Dict[str, Dict[str, Any]] = {}
        for plugin_id, state in self._states.items():
            if state.state == "open":
                remaining_sec = max(state.opened_until - now, 0.0)
                if remaining_sec <= 0:
                    statuses[plugin_id] = {
                        "state": "half_open",
                        "remaining_sec": 0.0,
                        "cooldown_level": state.cooldown_level,
                        "half_open_inflight": state.half_open_inflight,
                    }
                    continue
                statuses[plugin_id] = {
                    "state": "open",
                    "remaining_sec": remaining_sec,
                    "cooldown_level": state.cooldown_level,
                    "half_open_inflight": state.half_open_inflight,
                }
                continue

            if state.state == "half_open":
                statuses[plugin_id] = {
                    "state": "half_open",
                    "remaining_sec": 0.0,
                    "cooldown_level": state.cooldown_level,
                    "half_open_inflight": state.half_open_inflight,
                }
        return statuses

    @staticmethod
    def _log_skip_if_needed(
        plugin_id: str,
        component_name: str,
        operation: str,
        remaining_sec: float,
        state: _CircuitState,
        now: float,
    ) -> None:
        """限制熔断跳过日志频率，避免刷屏。"""

        if now - state.last_skip_log_at < _SKIP_LOG_INTERVAL_SEC:
            return
        state.last_skip_log_at = now
        logger.warning(
            f"插件 {plugin_id} 熔断中，跳过调用: "
            f"component={component_name} operation={operation} remaining={remaining_sec:.1f}s"
        )


_plugin_circuit_breaker = PluginCircuitBreaker()


def get_plugin_circuit_breaker() -> PluginCircuitBreaker:
    """返回插件运行时全局熔断器。"""

    return _plugin_circuit_breaker
