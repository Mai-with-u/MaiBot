"""命名 Hook 分发系统。

主程序可以在任意执行点触发一个命名 Hook，Host 会收集所有订阅该 Hook 的
插件处理器，并按照固定的全局顺序调度执行。

排序规则如下：

1. `blocking` 先于 `observe`
2. `early` 先于 `normal` 先于 `late`
3. 内置插件先于第三方插件
4. `plugin_id`
5. `handler_name`

其中：

- `blocking` 处理器串行执行，可修改 `kwargs`，也可中止本次 Hook 调用。
- `observe` 处理器后台并发执行，只允许旁路观察，不参与主流程控制。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set

import asyncio
import contextlib

from src.common.logger import get_logger
from src.common.shutdown import is_shutdown_requested
from src.config.config import global_config
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError

from .circuit_breaker import get_plugin_circuit_breaker
from .hook_spec_registry import HookSpec, HookSpecRegistry

if TYPE_CHECKING:
    from .component_registry import HookHandlerEntry
    from .supervisor import PluginRunnerSupervisor

logger = get_logger("plugin_runtime.host.hook_dispatcher")


@dataclass(slots=True)
class HookHandlerExecutionResult:
    """单个 HookHandler 的执行结果。

    Attributes:
        handler_name: 完整处理器名称，格式通常为 `plugin_id.component_name`。
        plugin_id: 处理器所属插件 ID。
        success: 本次调用是否成功。
        action: 当前处理器要求的控制动作，仅支持 `continue` 或 `abort`。
        modified_kwargs: 处理器返回的修改后参数字典。
        custom_result: 处理器返回的附加结果。
        error_message: 失败时的错误描述。
    """

    handler_name: str
    plugin_id: str
    success: bool = True
    action: str = "continue"
    modified_kwargs: Optional[Dict[str, Any]] = None
    custom_result: Any = None
    error_message: str = ""


@dataclass(slots=True)
class HookDispatchResult:
    """一次命名 Hook 调用的聚合结果。

    Attributes:
        hook_name: 本次调用的 Hook 名称。
        kwargs: 经阻塞处理器串行处理后的最终参数字典。
        aborted: 是否被某个处理器中止。
        stopped_by: 若被中止，记录触发中止的完整处理器名称。
        custom_results: 阻塞处理器返回的附加结果列表。
        errors: 本次调用中记录到的错误信息列表。
    """

    hook_name: str
    kwargs: Dict[str, Any] = field(default_factory=dict)
    aborted: bool = False
    stopped_by: Optional[str] = None
    custom_results: List[Any] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass(slots=True)
class _HookInvocationTarget:
    """内部使用的 Hook 调度目标。

    Attributes:
        supervisor: 负责该处理器的 Supervisor。
        entry: Hook 处理器条目。
        source_rank: 插件来源权重，内置插件为 `0`，第三方插件为 `1`。
    """

    supervisor: "PluginRunnerSupervisor"
    entry: "HookHandlerEntry"
    source_rank: int


class HookDispatcher:
    """命名 Hook 分发器。"""

    def __init__(
        self,
        supervisors_provider: Optional[Callable[[], Sequence["PluginRunnerSupervisor"]]] = None,
        hook_spec_registry: Optional[HookSpecRegistry] = None,
    ) -> None:
        """初始化 Hook 分发器。

        Args:
            supervisors_provider: 可选的 Supervisor 提供器。若调用 `invoke_hook()`
                时未显式传入 `supervisors`，则使用该回调获取目标 Supervisor 列表。
            hook_spec_registry: 可选的 Hook 规格注册中心；留空时使用独立注册中心。
        """

        self._background_tasks: Set[asyncio.Task[Any]] = set()
        self._supervisors_provider = supervisors_provider
        self._hook_spec_registry = hook_spec_registry or HookSpecRegistry()

    async def stop(self) -> None:
        """停止分发器并取消所有未完成的观察任务。"""

        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    def register_hook_spec(self, spec: HookSpec) -> None:
        """注册单个命名 Hook 规格。

        Args:
            spec: 需要注册的 Hook 规格。
        """

        self._hook_spec_registry.register_hook_spec(spec)

    def register_hook_specs(self, specs: Sequence[HookSpec]) -> None:
        """批量注册命名 Hook 规格。

        Args:
            specs: 需要注册的 Hook 规格序列。
        """

        for spec in specs:
            self.register_hook_spec(spec)

    def get_hook_spec(self, hook_name: str) -> HookSpec:
        """获取指定 Hook 的规格定义。

        Args:
            hook_name: Hook 名称。

        Returns:
            HookSpec: 若未显式注册，则返回按系统默认值生成的运行时规格。
        """

        normalized_name = self._normalize_hook_name(hook_name)
        registered_spec = self._hook_spec_registry.get_hook_spec(normalized_name)
        if registered_spec is not None:
            return registered_spec

        return HookSpec(
            name=normalized_name,
            parameters_schema={},
            default_timeout_ms=self._get_default_timeout_ms(),
        )

    def unregister_hook_spec(self, hook_name: str) -> bool:
        """注销指定命名 Hook 规格。

        Args:
            hook_name: 目标 Hook 名称。

        Returns:
            bool: 是否成功注销。
        """

        return self._hook_spec_registry.unregister_hook_spec(hook_name)

    def list_hook_specs(self) -> List[HookSpec]:
        """返回当前全部显式注册的 Hook 规格。

        Returns:
            List[HookSpec]: 已注册 Hook 规格列表。
        """

        return self._hook_spec_registry.list_hook_specs()

    async def invoke_hook(
        self,
        hook_name: str,
        supervisors: Optional[Sequence["PluginRunnerSupervisor"]] = None,
        **kwargs: Any,
    ) -> HookDispatchResult:
        """触发一次命名 Hook 调用。

        Args:
            hook_name: 本次触发的 Hook 名称。
            supervisors: 当前运行时中所有可参与分发的 Supervisor；留空时使用绑定的提供器。
            **kwargs: 传递给 Hook 处理器的关键字参数。

        Returns:
            HookDispatchResult: 聚合后的 Hook 调用结果。
        """

        normalized_hook_name = self._normalize_hook_name(hook_name)
        current_kwargs: Dict[str, Any] = dict(kwargs)
        dispatch_result = HookDispatchResult(hook_name=normalized_hook_name, kwargs=dict(current_kwargs))
        if is_shutdown_requested():
            return dispatch_result

        resolved_supervisors = list(supervisors) if supervisors is not None else list(self._resolve_supervisors())
        hook_spec = self.get_hook_spec(normalized_hook_name)
        invocation_targets = self._collect_invocation_targets(normalized_hook_name, resolved_supervisors)

        if not invocation_targets:
            return dispatch_result

        for target in invocation_targets:
            if is_shutdown_requested():
                return dispatch_result

            if target.entry.is_observe:
                self._schedule_observe_handler(
                    hook_name=normalized_hook_name,
                    hook_spec=hook_spec,
                    target=target,
                    kwargs=current_kwargs,
                )
                continue

            if not hook_spec.allow_blocking:
                error_message = (
                    f"Hook {normalized_hook_name} 不允许 blocking 处理器，"
                    f"已跳过 {target.entry.full_name}"
                )
                logger.warning(error_message)
                dispatch_result.errors.append(error_message)
                continue

            execution_result = await self._invoke_handler(
                hook_name=normalized_hook_name,
                hook_spec=hook_spec,
                target=target,
                kwargs=current_kwargs,
            )
            self._merge_blocking_result(
                hook_spec=hook_spec,
                target=target,
                execution_result=execution_result,
                dispatch_result=dispatch_result,
            )

            current_kwargs = dict(dispatch_result.kwargs)
            if dispatch_result.aborted:
                break

        return dispatch_result

    def _resolve_supervisors(self) -> Sequence["PluginRunnerSupervisor"]:
        """解析当前调用应使用的 Supervisor 列表。

        Returns:
            Sequence[PluginRunnerSupervisor]: 可参与本次 Hook 调度的 Supervisor 序列。

        Raises:
            ValueError: 当未传入 `supervisors` 且分发器也未绑定提供器时抛出。
        """

        if self._supervisors_provider is None:
            raise ValueError("当前 HookDispatcher 未绑定 supervisors_provider，请显式传入 supervisors")
        return self._supervisors_provider()

    def _collect_invocation_targets(
        self,
        hook_name: str,
        supervisors: Sequence["PluginRunnerSupervisor"],
    ) -> List[_HookInvocationTarget]:
        """收集并排序本次 Hook 调用的全部处理器目标。

        Args:
            hook_name: 目标 Hook 名称。
            supervisors: 当前参与调度的 Supervisor 序列。

        Returns:
            List[_HookInvocationTarget]: 已完成全局排序的处理器目标列表。
        """

        invocation_targets: List[_HookInvocationTarget] = []
        for supervisor in supervisors:
            source_rank = self._get_supervisor_source_rank(supervisor)
            for entry in supervisor.component_registry.get_hook_handlers(hook_name):
                invocation_targets.append(
                    _HookInvocationTarget(
                        supervisor=supervisor,
                        entry=entry,
                        source_rank=source_rank,
                    )
                )

        invocation_targets.sort(key=self._build_sort_key)
        return invocation_targets

    @staticmethod
    def _build_sort_key(target: _HookInvocationTarget) -> tuple[int, int, int, str, str]:
        """构造 Hook 处理器的全局排序键。

        Args:
            target: 待排序的处理器目标。

        Returns:
            tuple[int, int, int, str, str]: 全局排序键。
        """

        return (
            HookDispatcher._get_mode_rank(target.entry.mode),
            HookDispatcher._get_order_rank(target.entry.order),
            target.source_rank,
            target.entry.plugin_id,
            target.entry.name,
        )

    @staticmethod
    def _get_default_timeout_ms() -> int:
        """读取系统级默认 Hook 超时。

        Returns:
            int: 默认超时毫秒数。
        """

        timeout_seconds = float(global_config.plugin_runtime.hook_blocking_timeout_sec or 60.0)
        return max(int(timeout_seconds * 1000), 1)

    @staticmethod
    def _get_mode_rank(mode: str) -> int:
        """返回 Hook 模式的排序权重。

        Args:
            mode: Hook 模式。

        Returns:
            int: 越小表示越靠前。
        """

        return {"blocking": 0, "observe": 1}.get(mode, 99)

    @staticmethod
    def _get_order_rank(order: str) -> int:
        """返回 Hook 顺序槽位的排序权重。

        Args:
            order: Hook 顺序槽位。

        Returns:
            int: 越小表示越靠前。
        """

        return {"early": 0, "normal": 1, "late": 2}.get(order, 99)

    @staticmethod
    def _get_supervisor_source_rank(supervisor: "PluginRunnerSupervisor") -> int:
        """返回 Supervisor 的来源排序权重。

        Args:
            supervisor: 目标 Supervisor。

        Returns:
            int: 内置插件返回 `0`，第三方插件返回 `1`。
        """

        return 0 if supervisor.group_name == "builtin" else 1

    @staticmethod
    def _normalize_hook_name(hook_name: str) -> str:
        """规范化命名 Hook 名称。

        Args:
            hook_name: 原始 Hook 名称。

        Returns:
            str: 规范化后的 Hook 名称。

        Raises:
            ValueError: 当 Hook 名称为空时抛出。
        """

        normalized_name = str(hook_name or "").strip()
        if not normalized_name:
            raise ValueError("Hook 名称不能为空")
        return normalized_name

    def _resolve_timeout_ms(self, hook_spec: HookSpec, target: _HookInvocationTarget) -> int:
        """计算单个处理器的实际超时。

        Args:
            hook_spec: 当前 Hook 的规格定义。
            target: 当前执行目标。

        Returns:
            int: 最终生效的超时毫秒数。
        """

        if target.entry.timeout_ms > 0:
            return target.entry.timeout_ms
        if hook_spec.default_timeout_ms > 0:
            return hook_spec.default_timeout_ms
        return self._get_default_timeout_ms()

    async def _invoke_hook_handler_if_registered(
        self,
        hook_name: str,
        hook_spec: HookSpec,
        target: _HookInvocationTarget,
        kwargs: Dict[str, Any],
    ) -> HookHandlerExecutionResult:
        """校验处理器条目仍注册后执行；供后台观察任务使用。

        任务入队到真正启动之间插件可能已被卸载/重载：
        若不校验，旧任务会通过 try_acquire 的 setdefault 重建已清理的熔断状态，
        或把结果写入同 ID 重载后插件的新状态。
        """
        current_entries = target.supervisor.component_registry.get_hook_handlers(hook_name)
        if target.entry not in current_entries:
            logger.debug(f"HookHandler {target.entry.full_name} 已注销或被禁用，跳过观察执行")
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
            )
        return await self._invoke_handler(
            hook_name=hook_name,
            hook_spec=hook_spec,
            target=target,
            kwargs=kwargs,
        )

    async def _invoke_handler(
        self,
        hook_name: str,
        hook_spec: HookSpec,
        target: _HookInvocationTarget,
        kwargs: Dict[str, Any],
    ) -> HookHandlerExecutionResult:
        """执行单个 Hook 处理器。

        Args:
            hook_name: 当前 Hook 名称。
            hook_spec: 当前 Hook 规格。
            target: 当前执行目标。
            kwargs: 当前参数字典。

        Returns:
            HookHandlerExecutionResult: 处理器执行结果。
        """

        timeout_ms = self._resolve_timeout_ms(hook_spec, target)
        request_args: Dict[str, Any] = {"hook_name": hook_name, **dict(kwargs)}
        if is_shutdown_requested():
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
            )

        circuit_permit = get_plugin_circuit_breaker().try_acquire(
            target.entry.plugin_id,
            target.entry.full_name,
            f"hook:{hook_name}",
        )
        if not circuit_permit.allowed:
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
            )

        try:
            response_envelope = await asyncio.wait_for(
                target.supervisor.invoke_plugin(
                    "plugin.invoke_hook",
                    target.entry.plugin_id,
                    target.entry.name,
                    request_args,
                    timeout_ms=timeout_ms,
                ),
                timeout=max(timeout_ms / 1000.0, 0.001),
            )
        except asyncio.TimeoutError:
            get_plugin_circuit_breaker().record_failure(circuit_permit, f"HookHandler 超时 {timeout_ms}ms")
            error_message = (
                f"HookHandler {target.entry.full_name} 执行超时，已超过 {timeout_ms}ms"
            )
            logger.error(error_message)
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
                success=False,
                error_message=error_message,
            )
        except RPCError as exc:
            if self._should_record_circuit_failure(exc):
                get_plugin_circuit_breaker().record_failure(circuit_permit, str(exc))
            error_message = f"HookHandler {target.entry.full_name} 执行失败: {exc}"
            logger.error(error_message, exc_info=True)
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
                success=False,
                error_message=error_message,
            )
        except Exception as exc:
            error_message = f"HookHandler {target.entry.full_name} 执行失败: {exc}"
            logger.error(error_message, exc_info=True)
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
                success=False,
                error_message=error_message,
            )

        get_plugin_circuit_breaker().record_success(circuit_permit)
        response_payload = response_envelope.payload
        if not isinstance(response_payload, dict):
            return HookHandlerExecutionResult(
                handler_name=target.entry.full_name,
                plugin_id=target.entry.plugin_id,
                custom_result=response_payload,
            )

        return HookHandlerExecutionResult(
            handler_name=target.entry.full_name,
            plugin_id=target.entry.plugin_id,
            success=bool(response_payload.get("success", True)),
            action=self._normalize_action(response_payload.get("action", "continue")),
            modified_kwargs=self._extract_modified_kwargs(response_payload.get("modified_kwargs")),
            custom_result=response_payload.get("custom_result"),
            error_message=str(response_payload.get("error_message", "") or ""),
        )

    @staticmethod
    def _should_record_circuit_failure(exc: RPCError) -> bool:
        """判断 RPC 异常是否应计入插件熔断。"""

        return exc.code in {ErrorCode.E_TIMEOUT, ErrorCode.E_PLUGIN_CRASHED}

    @staticmethod
    def _extract_modified_kwargs(raw_value: Any) -> Optional[Dict[str, Any]]:
        """提取并校验处理器返回的 `modified_kwargs`。

        Args:
            raw_value: 原始返回值。

        Returns:
            Optional[Dict[str, Any]]: 合法时返回字典，否则返回 `None`。
        """

        if raw_value is None:
            return None
        if isinstance(raw_value, dict):
            return dict(raw_value)
        logger.warning("HookHandler 返回的 modified_kwargs 不是字典，已忽略")
        return None

    @staticmethod
    def _normalize_action(raw_value: Any) -> str:
        """规范化处理器动作返回值。

        Args:
            raw_value: 原始动作值。

        Returns:
            str: 规范化后的动作值，仅支持 `continue` 或 `abort`。
        """

        normalized_value = str(raw_value or "").strip().lower() or "continue"
        if normalized_value not in {"continue", "abort"}:
            logger.warning(f"未知的 Hook action: {raw_value}，已按 continue 处理")
            return "continue"
        return normalized_value

    def _merge_blocking_result(
        self,
        hook_spec: HookSpec,
        target: _HookInvocationTarget,
        execution_result: HookHandlerExecutionResult,
        dispatch_result: HookDispatchResult,
    ) -> None:
        """合并阻塞处理器结果到聚合结果。

        Args:
            hook_spec: 当前 Hook 规格。
            target: 当前执行目标。
            execution_result: 当前处理器执行结果。
            dispatch_result: 当前聚合结果对象。
        """

        if execution_result.custom_result is not None:
            dispatch_result.custom_results.append(execution_result.custom_result)

        if not execution_result.success:
            error_message = execution_result.error_message or f"HookHandler {target.entry.full_name} 执行失败"
            dispatch_result.errors.append(error_message)
            self._apply_error_policy(target, hook_spec, dispatch_result, error_message)
            return

        if execution_result.modified_kwargs is not None:
            if hook_spec.allow_kwargs_mutation:
                dispatch_result.kwargs = dict(execution_result.modified_kwargs)
            else:
                error_message = (
                    f"Hook {dispatch_result.hook_name} 不允许修改 kwargs，"
                    f"已忽略 {target.entry.full_name} 的 modified_kwargs"
                )
                logger.warning(error_message)
                dispatch_result.errors.append(error_message)

        if execution_result.action == "abort":
            if hook_spec.allow_abort:
                dispatch_result.aborted = True
                dispatch_result.stopped_by = target.entry.full_name
                logger.info(f"HookHandler {target.entry.full_name} 中止了 Hook {dispatch_result.hook_name}")
            else:
                error_message = (
                    f"Hook {dispatch_result.hook_name} 不允许 abort，"
                    f"已忽略 {target.entry.full_name} 的 abort 请求"
                )
                logger.warning(error_message)
                dispatch_result.errors.append(error_message)

    def _apply_error_policy(
        self,
        target: _HookInvocationTarget,
        hook_spec: HookSpec,
        dispatch_result: HookDispatchResult,
        error_message: str,
    ) -> None:
        """根据错误策略处理阻塞处理器失败。

        Args:
            target: 触发错误的处理器目标。
            hook_spec: 当前 Hook 规格。
            dispatch_result: 当前聚合结果对象。
            error_message: 需要记录的错误描述。
        """

        if target.entry.error_policy != "abort":
            return
        if not hook_spec.allow_abort:
            logger.warning(
                f"Hook {dispatch_result.hook_name} 禁止 abort，"
                f"已将 {target.entry.full_name} 的错误策略按 skip 处理"
            )
            return

        dispatch_result.aborted = True
        dispatch_result.stopped_by = target.entry.full_name
        logger.warning(
            f"HookHandler {target.entry.full_name} 因错误策略 abort "
            f"中止了 Hook {dispatch_result.hook_name}: {error_message}"
        )

    def _schedule_observe_handler(
        self,
        hook_name: str,
        hook_spec: HookSpec,
        target: _HookInvocationTarget,
        kwargs: Dict[str, Any],
    ) -> None:
        """后台调度观察型处理器。

        Args:
            hook_name: 当前 Hook 名称。
            hook_spec: 当前 Hook 规格。
            target: 当前观察型处理器目标。
            kwargs: 调用参数快照。
        """

        if not hook_spec.allow_observe:
            logger.warning(f"Hook {hook_name} 不允许 observe 处理器，已跳过 {target.entry.full_name}")
            return
        if is_shutdown_requested():
            return

        task = asyncio.create_task(
            self._run_observe_handler(
                hook_name=hook_name,
                hook_spec=hook_spec,
                target=target,
                kwargs=dict(kwargs),
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._handle_background_task_done)

    async def _run_observe_handler(
        self,
        hook_name: str,
        hook_spec: HookSpec,
        target: _HookInvocationTarget,
        kwargs: Dict[str, Any],
    ) -> None:
        """执行观察型处理器并吞掉控制流副作用。

        Args:
            hook_name: 当前 Hook 名称。
            hook_spec: 当前 Hook 规格。
            target: 当前观察型处理器目标。
            kwargs: 调用参数快照。
        """

        execution_result = await self._invoke_hook_handler_if_registered(
            hook_name=hook_name,
            hook_spec=hook_spec,
            target=target,
            kwargs=kwargs,
        )

        if not execution_result.success:
            logger.warning(
                f"观察型 HookHandler {target.entry.full_name} 执行失败: "
                f"{execution_result.error_message or '未知错误'}"
            )
            return

        if execution_result.modified_kwargs is not None:
            logger.warning(f"观察型 HookHandler {target.entry.full_name} 返回了 modified_kwargs，已忽略")
        if execution_result.action == "abort":
            logger.warning(f"观察型 HookHandler {target.entry.full_name} 请求 abort，已忽略")

    def _handle_background_task_done(self, task: asyncio.Task[Any]) -> None:
        """处理观察任务完成回调。

        Args:
            task: 已完成的后台任务。
        """

        self._background_tasks.discard(task)
        with contextlib.suppress(asyncio.CancelledError):
            exception = task.exception()
            if exception is not None:
                logger.error(f"观察型 Hook 后台任务执行失败: {exception}")
