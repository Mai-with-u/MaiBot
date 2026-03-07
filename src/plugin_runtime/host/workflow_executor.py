"""Host-side WorkflowExecutor

6 阶段线性流转（INGRESS → PRE_PROCESS → PLAN → TOOL_EXECUTE → POST_PROCESS → EGRESS）

每个阶段执行顺序:
1. Host-side pre-filter: 根据 hook filter 条件过滤不相关的 hook
2. 按 priority 降序排列
3. 串行执行 blocking hook（可修改 message，返回 HookResult）
4. 并发执行 non-blocking hook（只读）
5. 检查是否有 SKIP_STAGE 或 ABORT
6. PLAN 阶段内置 Command 匹配路由

支持:
- HookResult: CONTINUE / SKIP_STAGE / ABORT
- ErrorPolicy: ABORT / SKIP / LOG (per-hook)
- stage_outputs: 阶段间带命名空间的数据传递
- modification_log: 消息修改审计
"""

from typing import Any, Awaitable, Callable

import asyncio
import time
import uuid

from src.common.logger import get_logger
from src.plugin_runtime.host.component_registry import ComponentRegistry, RegisteredComponent

logger = get_logger("plugin_runtime.host.workflow_executor")

# 阶段顺序
STAGE_SEQUENCE: list[str] = [
    "ingress",
    "pre_process",
    "plan",
    "tool_execute",
    "post_process",
    "egress",
]

# HookResult 常量（与 SDK HookResult enum 值对应）
HOOK_CONTINUE = "continue"
HOOK_SKIP_STAGE = "skip_stage"
HOOK_ABORT = "abort"


class ModificationRecord:
    """消息修改记录"""
    __slots__ = ("stage", "hook_name", "timestamp", "fields_changed")

    def __init__(self, stage: str, hook_name: str, fields_changed: list[str]):
        self.stage = stage
        self.hook_name = hook_name
        self.timestamp = time.perf_counter()
        self.fields_changed = fields_changed


class WorkflowContext:
    """Workflow 执行上下文"""

    def __init__(self, trace_id: str | None = None, stream_id: str | None = None):
        self.trace_id = trace_id or uuid.uuid4().hex
        self.stream_id = stream_id
        self.timings: dict[str, float] = {}
        self.errors: list[str] = []
        # 阶段间数据传递（按 stage 命名空间隔离）
        self.stage_outputs: dict[str, dict[str, Any]] = {}
        # 消息修改审计日志
        self.modification_log: list[ModificationRecord] = []
        # PLAN 阶段命令匹配结果
        self.matched_command: str | None = None

    def set_stage_output(self, stage: str, key: str, value: Any) -> None:
        self.stage_outputs.setdefault(stage, {})[key] = value

    def get_stage_output(self, stage: str, key: str, default: Any = None) -> Any:
        return self.stage_outputs.get(stage, {}).get(key, default)


class WorkflowResult:
    """Workflow 执行结果"""

    def __init__(
        self,
        status: str = "completed",  # completed / aborted / failed
        return_message: str = "",
        stopped_at: str = "",
        diagnostics: dict[str, Any] | None = None,
    ):
        self.status = status
        self.return_message = return_message
        self.stopped_at = stopped_at
        self.diagnostics = diagnostics or {}


# invoke_fn 签名
InvokeFn = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


class WorkflowExecutor:
    """Host-side Workflow 执行器

    实现 stage-based pipeline + per-stage hook chain with priority + early return。
    """

    def __init__(self, registry: ComponentRegistry):
        self._registry = registry

    async def execute(
        self,
        invoke_fn: InvokeFn,
        message: dict[str, Any] | None = None,
        stream_id: str | None = None,
        context: WorkflowContext | None = None,
    ) -> tuple[WorkflowResult, dict[str, Any] | None, WorkflowContext]:
        """执行 workflow pipeline。

        Returns:
            (result, final_message, context)
        """
        ctx = context or WorkflowContext(stream_id=stream_id)
        current_message = dict(message) if message else None

        for stage in STAGE_SEQUENCE:
            stage_start = time.perf_counter()

            try:
                # PLAN 阶段: 先做 Command 路由
                if stage == "plan" and current_message:
                    cmd_result = await self._route_command(
                        invoke_fn, current_message, ctx
                    )
                    if cmd_result is not None:
                        # 命令匹配成功，跳过 PLAN 阶段的 hook，直接存结果进 stage_outputs
                        ctx.set_stage_output("plan", "command_result", cmd_result)
                        ctx.timings[stage] = time.perf_counter() - stage_start
                        continue

                # 获取该阶段所有 hook（已按 priority 降序排列）
                all_steps = self._registry.get_workflow_steps(stage)
                if not all_steps:
                    ctx.timings[stage] = time.perf_counter() - stage_start
                    continue

                # 1. Pre-filter
                filtered_steps = self._pre_filter(all_steps, current_message)

                # 2. 分离 blocking 和 non-blocking
                blocking_steps = [s for s in filtered_steps if s.metadata.get("blocking", True)]
                nonblocking_steps = [s for s in filtered_steps if not s.metadata.get("blocking", True)]

                # 3. 串行执行 blocking hook
                skip_stage = False
                for step in blocking_steps:
                    hook_result, modified, step_error = await self._invoke_step(
                        invoke_fn, step, stage, ctx, current_message
                    )

                    if step_error:
                        error_policy = step.metadata.get("error_policy", "abort")
                        ctx.errors.append(f"{step.full_name}: {step_error}")

                        if error_policy == "abort":
                            ctx.timings[stage] = time.perf_counter() - stage_start
                            return (
                                WorkflowResult(
                                    status="failed",
                                    return_message=step_error,
                                    stopped_at=stage,
                                    diagnostics={"step": step.full_name, "trace_id": ctx.trace_id},
                                ),
                                current_message,
                                ctx,
                            )
                        elif error_policy == "skip":
                            logger.warning(f"[{ctx.trace_id}] hook {step.full_name} 异常(skip): {step_error}")
                            continue
                        else:  # log
                            logger.warning(f"[{ctx.trace_id}] hook {step.full_name} 异常(log): {step_error}")
                            continue

                    # 更新消息（仅 blocking hook 有权修改）
                    if modified:
                        changed_fields = _diff_keys(current_message, modified) if current_message else list(modified.keys())
                        ctx.modification_log.append(
                            ModificationRecord(stage, step.full_name, changed_fields)
                        )
                        current_message = modified

                    if hook_result == HOOK_ABORT:
                        ctx.timings[stage] = time.perf_counter() - stage_start
                        return (
                            WorkflowResult(
                                status="aborted",
                                return_message=f"aborted by {step.full_name}",
                                stopped_at=stage,
                                diagnostics={"step": step.full_name, "trace_id": ctx.trace_id},
                            ),
                            current_message,
                            ctx,
                        )

                    if hook_result == HOOK_SKIP_STAGE:
                        skip_stage = True
                        break

                # 4. 并发执行 non-blocking hook（只读，忽略返回值中的 modified_message）
                if nonblocking_steps and not skip_stage:
                    nb_tasks = [
                        self._invoke_step_fire_and_forget(
                            invoke_fn, step, stage, ctx, current_message
                        )
                        for step in nonblocking_steps
                    ]
                    # 并发执行但不阻塞 pipeline
                    for task in [asyncio.create_task(t) for t in nb_tasks]:
                        task.add_done_callback(lambda _: None)

                ctx.timings[stage] = time.perf_counter() - stage_start

            except Exception as e:
                ctx.timings[stage] = time.perf_counter() - stage_start
                ctx.errors.append(f"{stage}: {e}")
                logger.error(f"[{ctx.trace_id}] 阶段 {stage} 未捕获异常: {e}", exc_info=True)
                return (
                    WorkflowResult(
                        status="failed",
                        return_message=str(e),
                        stopped_at=stage,
                        diagnostics={"trace_id": ctx.trace_id},
                    ),
                    current_message,
                    ctx,
                )

        return (
            WorkflowResult(
                status="completed",
                return_message="workflow completed",
                diagnostics={"trace_id": ctx.trace_id},
            ),
            current_message,
            ctx,
        )

    # ─── 内部方法 ──────────────────────────────────────────────

    def _pre_filter(
        self,
        steps: list[RegisteredComponent],
        message: dict[str, Any] | None,
    ) -> list[RegisteredComponent]:
        """根据 hook 声明的 filter 条件预过滤，避免无意义的 IPC 调用。"""
        if not message:
            return steps

        result = []
        for step in steps:
            filter_cond = step.metadata.get("filter", {})
            if not filter_cond:
                result.append(step)
                continue
            if self._match_filter(filter_cond, message):
                result.append(step)
        return result

    @staticmethod
    def _match_filter(filter_cond: dict[str, Any], message: dict[str, Any]) -> bool:
        """简单 key-value 匹配过滤。

        filter 中的每个 key 必须在 message 中存在且值相等，
        全部匹配才通过。
        """
        for key, expected in filter_cond.items():
            actual = message.get(key)
            if (isinstance(expected, list) and actual not in expected) or (
                not isinstance(expected, list) and actual != expected
            ):
                return False
        return True

    async def _invoke_step(
        self,
        invoke_fn: InvokeFn,
        step: RegisteredComponent,
        stage: str,
        ctx: WorkflowContext,
        message: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        """调用单个 blocking hook。

        Returns:
            (hook_result, modified_message, error_string_or_None)
        """
        timeout_ms = step.metadata.get("timeout_ms", 0)
        timeout_sec = timeout_ms / 1000 if timeout_ms > 0 else None
        step_key = f"{stage}:{step.full_name}"
        step_start = time.perf_counter()

        try:
            coro = invoke_fn(step.plugin_id, step.name, {
                "stage": stage,
                "trace_id": ctx.trace_id,
                "message": message,
                "stage_outputs": ctx.stage_outputs,
            })
            resp = await asyncio.wait_for(coro, timeout=timeout_sec) if timeout_sec else await coro
            ctx.timings[step_key] = time.perf_counter() - step_start

            hook_result = resp.get("hook_result", HOOK_CONTINUE)
            modified_message = resp.get("modified_message")
            # 存 stage output（如果 hook 提供了）
            stage_out = resp.get("stage_output")
            if isinstance(stage_out, dict):
                for k, v in stage_out.items():
                    ctx.set_stage_output(stage, k, v)

            return hook_result, modified_message, None

        except asyncio.TimeoutError:
            ctx.timings[step_key] = time.perf_counter() - step_start
            return HOOK_CONTINUE, None, f"timeout after {timeout_ms}ms"

        except Exception as e:
            ctx.timings[step_key] = time.perf_counter() - step_start
            return HOOK_CONTINUE, None, str(e)

    async def _invoke_step_fire_and_forget(
        self,
        invoke_fn: InvokeFn,
        step: RegisteredComponent,
        stage: str,
        ctx: WorkflowContext,
        message: dict[str, Any] | None,
    ) -> None:
        """Non-blocking hook 调用，只读，忽略结果。"""
        timeout_ms = step.metadata.get("timeout_ms", 0)
        timeout_sec = timeout_ms / 1000 if timeout_ms > 0 else None

        try:
            coro = invoke_fn(step.plugin_id, step.name, {
                "stage": stage,
                "trace_id": ctx.trace_id,
                "message": message,
                "stage_outputs": ctx.stage_outputs,
            })
            if timeout_sec:
                await asyncio.wait_for(coro, timeout=timeout_sec)
            else:
                await coro
        except Exception as e:
            logger.debug(f"[{ctx.trace_id}] non-blocking hook {step.full_name}: {e}")

    async def _route_command(
        self,
        invoke_fn: InvokeFn,
        message: dict[str, Any],
        ctx: WorkflowContext,
    ) -> dict[str, Any] | None:
        """PLAN 阶段内置 Command 路由。

        在 registry 中查找匹配的 command 组件，
        匹配到则直接路由到对应 command handler，返回执行结果。
        不匹配则返回 None，让 PLAN 阶段的 hook 继续执行。
        """
        plain_text = message.get("plain_text", "")
        if not plain_text:
            return None

        matched = self._registry.find_command_by_text(plain_text)
        if matched is None:
            return None

        ctx.matched_command = matched.full_name
        logger.info(f"[{ctx.trace_id}] 命令匹配: {matched.full_name}")

        try:
            return await invoke_fn(matched.plugin_id, matched.name, {
                "text": plain_text,
                "message": message,
                "trace_id": ctx.trace_id,
            })
        except Exception as e:
            logger.error(f"[{ctx.trace_id}] 命令 {matched.full_name} 执行失败: {e}", exc_info=True)
            ctx.errors.append(f"command:{matched.full_name}: {e}")
            return None


def _diff_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """返回 new 中与 old 不同的 key 列表。"""
    return [k for k, v in new.items() if k not in old or old[k] != v]
