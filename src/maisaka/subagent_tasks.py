"""后台子代理任务的登记与生命周期管理。

开启 ``capabilities.advanced_sub_agent`` 后，subagent 工具不再等子代理跑完才返回：
每次调用登记一个任务并在后台运行，Planner 拿到任务 id 后可以继续处理聊天；
任务结束时由运行时把结论作为一条新的 user 消息追加到上下文末尾并唤醒 Planner。
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Literal, Optional

import asyncio
import time

from src.common.logger import get_logger

logger = get_logger("maisaka_subagent_tasks")

SubAgentTaskStatus = Literal["running", "completed", "failed", "stopped"]

# 同一会话里同时运行的后台子代理上限
SUBAGENT_MAX_RUNNING_TASKS = 3
# 已结束任务的保留条数，供 subagent_status 回看
SUBAGENT_FINISHED_TASK_KEEP_COUNT = 20


@dataclass(slots=True)
class SubAgentOutcome:
    """子代理一次完整运行的产出。"""

    conclusion: str
    tool_usage: Dict[str, int]


@dataclass(slots=True)
class SubAgentTask:
    """一个后台子代理任务。"""

    task_id: str
    description: str
    granted_tools: List[str]
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    status: SubAgentTaskStatus = "running"
    tool_usage: Dict[str, int] = field(default_factory=dict)
    conclusion: str = ""
    error_message: str = ""
    asyncio_task: Optional[asyncio.Task[None]] = None

    @property
    def elapsed_seconds(self) -> float:
        """任务已经运行（或总共运行）的秒数。"""

        return (self.finished_at or time.time()) - self.started_at


def format_tool_usage(tool_usage: Dict[str, int]) -> str:
    """把工具调用次数整理成一行说明。"""

    return "、".join(f"{tool_name}×{count}" for tool_name, count in tool_usage.items()) or "未调用工具"


SubAgentRunner = Callable[[SubAgentTask], Awaitable[SubAgentOutcome]]
SubAgentFinishedCallback = Callable[[SubAgentTask], None]


class SubAgentTaskManager:
    """登记并管理一个会话内的后台子代理任务。"""

    def __init__(self) -> None:
        self._tasks: Dict[str, SubAgentTask] = {}
        self._task_counter = 0

    @property
    def running_count(self) -> int:
        """当前仍在运行的任务数量。"""

        return sum(1 for task in self._tasks.values() if task.status == "running")

    def get(self, task_id: str) -> Optional[SubAgentTask]:
        """按 id 取任务。"""

        return self._tasks.get(task_id)

    def list_tasks(self) -> List[SubAgentTask]:
        """按启动顺序返回全部已登记任务。"""

        return list(self._tasks.values())

    def allocate_task_id(self) -> str:
        """分配下一个任务 id；同步运行的子代理也用它在观察事件里标识自己。"""

        self._task_counter += 1
        return f"subagent-{self._task_counter}"

    def start(
        self,
        *,
        task_id: str,
        description: str,
        granted_tools: List[str],
        runner: SubAgentRunner,
        on_finished: SubAgentFinishedCallback,
    ) -> SubAgentTask:
        """登记一个任务并在后台开始运行。

        Args:
            task_id: 由 allocate_task_id 分配的任务 id。
            description: Planner 写给子代理的任务说明。
            granted_tools: 授权给子代理的工具名。
            runner: 实际运行子代理并返回结论的协程函数。
            on_finished: 任务自然结束（完成或失败）时的回调；被 stop 的任务不回调。
        """

        task = SubAgentTask(
            task_id=task_id,
            description=description,
            granted_tools=list(granted_tools),
        )
        self._tasks[task.task_id] = task
        task.asyncio_task = asyncio.create_task(self._run(task, runner, on_finished))
        self._drop_old_finished_tasks()
        return task

    async def _run(self, task: SubAgentTask, runner: SubAgentRunner, on_finished: SubAgentFinishedCallback) -> None:
        """运行任务并记录结果；失败原因原样记录，交给 Planner 知晓。"""

        try:
            outcome = await runner(task)
        except asyncio.CancelledError:
            task.status = "stopped"
            task.finished_at = time.time()
            raise
        except Exception as exc:
            logger.exception(f"后台子代理任务执行失败: task_id={task.task_id}")
            task.status = "failed"
            task.error_message = f"{type(exc).__name__}: {exc}"
        else:
            task.conclusion = outcome.conclusion
            task.tool_usage = dict(outcome.tool_usage)
            task.status = "completed" if outcome.conclusion else "failed"
            if not outcome.conclusion:
                task.error_message = "子代理没有给出结论"
        task.finished_at = time.time()
        on_finished(task)

    def stop(self, task_id: str) -> bool:
        """终止一个仍在运行的任务；任务不存在或已结束时返回 False。"""

        task = self._tasks.get(task_id)
        if task is None or task.status != "running" or task.asyncio_task is None:
            return False
        task.asyncio_task.cancel()
        task.status = "stopped"
        task.finished_at = time.time()
        return True

    async def cancel_all(self) -> None:
        """运行时停止时终止全部后台任务。"""

        pending_tasks = [task.asyncio_task for task in self._tasks.values() if task.asyncio_task is not None]
        for task in self._tasks.values():
            self.stop(task.task_id)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    def _drop_old_finished_tasks(self) -> None:
        """只保留最近若干条已结束任务，避免登记表无限增长。"""

        finished_task_ids = [task_id for task_id, task in self._tasks.items() if task.status != "running"]
        for task_id in finished_task_ids[: max(0, len(finished_task_ids) - SUBAGENT_FINISHED_TASK_KEEP_COUNT)]:
            del self._tasks[task_id]
