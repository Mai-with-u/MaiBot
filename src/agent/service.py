"""外接 Agent 接口：任务队列、执行记录和双向传话。"""

from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any, Dict, Optional
import asyncio
import json

from src.common.async_work import run_sync_work
from src.common.json_storage import read_json, write_json
from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.prompt_i18n import load_prompt
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config

from .acp_agent import AcpAgent, AgentTurnError
from .config import AgentConfig

logger = get_logger("agent")


async def _cancel_task(task: Optional[asyncio.Task[None]]) -> None:
    if task is not None and task is not asyncio.current_task():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class AgentService:
    name = "麦麦的 Agent"
    relay_tool = "agent_talk"
    relay_kind = "agent_relay"

    def __init__(self, root: Optional[Path] = None) -> None:
        instance = get_instance_root()
        self.root = root if root is not None else instance / "data" / "agent"
        self.agent: Optional[AcpAgent] = None
        self._closing = False
        self.agent_id = "__maibot_agent__"
        self.paused = False
        self._manual_pause = False
        self.error = ""
        self._task: Optional[asyncio.Task[None]] = None
        self._reload_registered = False
        self._lifecycle_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._turn_task: Optional[asyncio.Task[None]] = None
        self._state_lock = asyncio.Lock()
        self._settings: Optional[Dict[str, Any]] = None

    @property
    def config(self) -> AgentConfig:
        return global_config.agent

    @property
    def workspace(self) -> Path:
        return Path(self.config.acp_cwd).expanduser().absolute() if self.config.acp_cwd else self.root / "workspace"

    def policy(self) -> str:
        profile = {"nickname": global_config.bot.nickname, "personality": global_config.personality.personality}
        return load_prompt("agent_acp", maibot_profile=json.dumps(profile, ensure_ascii=False))

    def image_path(self, reference: str) -> Path:
        workspace = self.workspace.resolve()
        path = (workspace / Path(reference).expanduser()).resolve()
        if not path.is_relative_to(workspace):
            raise ValueError("附图必须位于当前 Agent 工作目录。")
        return path

    async def persist(self) -> None:
        async with self._state_lock:
            state = {"paused": self.paused, "error": self.error, "manual_pause": self._manual_pause}
            def save() -> None:
                self.root.mkdir(parents=True, exist_ok=True)
                write_json(self.root / "runtime.json", state)
            await run_sync_work(save)

    async def start(self) -> None:
        if not self._reload_registered:
            config_manager.register_reload_callback(self.reload, sections=("agent",))
            self._reload_registered = True
        await self.reload()

    async def reload(self) -> None:
        await run_on_main_loop(self._reload())

    async def _reload(self) -> None:
        async with self._lifecycle_lock:
            settings = self.config.model_dump()
            if self.agent is not None and self._settings == settings:
                return
            await self.stop()
            if not self.config.enabled:
                return
            state_path = self.root / "runtime.json"
            if await asyncio.to_thread(state_path.exists):
                state = await asyncio.to_thread(read_json, state_path)
                self.paused, self.error = state["paused"], state["error"]
                # 旧格式只有 paused/error；保留旧故障暂停，收到新请求时才继续。
                self._manual_pause = state.get("manual_pause", self.paused and not self.error)
            agent = AcpAgent(self)
            await agent.load()
            self.agent, self._settings = agent, settings
            if self.paused:
                if self.error:
                    logger.warning("Agent 恢复了上次的暂停状态：%s；故障暂停可由新请求或 WebUI 恢复，手动暂停须在 WebUI 恢复。", self.error)
                else:
                    logger.warning("Agent 保持上次的手动暂停状态；需要继续处理排队任务时，请在 WebUI 恢复。")
            self._task = asyncio.create_task(self._run(), name="agent")

    async def stop(self) -> None:
        self._closing = True
        try:
            # 按依赖逆序清理；前一步失败时仍执行后续清理，异常继续向上传播。
            async with AsyncExitStack() as cleanup:
                if self.agent is not None:
                    cleanup.push_async_callback(self.persist)
                    cleanup.push_async_callback(self.agent.close)
                cleanup.push_async_callback(_cancel_task, self._task)
                await _cancel_task(self._turn_task)
        finally:
            self.agent = None
            self._task = None
            self._settings = None
            self._closing = False

    def require_agent(self) -> AcpAgent:
        if self.agent is None:
            raise RuntimeError("Agent 未启动，请在设置的“Agent连接”中启用连接。")
        return self.agent

    def require_owner(self, agent_id: str) -> None:
        self.require_agent()
        if agent_id != self.agent_id:
            raise PermissionError("跨聊天读取由独立 Agent 执行；普通聊天请使用 agent_talk。")

    async def context(self) -> str:
        """仅提供任务所需的目录信息；能力由接入说明声明，运行状态通过工具按需查询。"""
        def render() -> str:
            lines = load_prompt("agent_acp_environment").splitlines()
            # 本机连接只显示一次目录；远程连接必须保留麦麦侧附图路径的边界。
            if not self.config.acp_remote_cwd:
                lines = [line for line in lines if "{maibot_workspace}" not in line]
            return "\n".join(lines).format(
                workspace=self.config.acp_remote_cwd or str(self.workspace),
                maibot_workspace=str(self.workspace),
            ) + "\n\n"

        return await asyncio.to_thread(render)

    async def _run(self) -> None:
        while True:
            try:
                agent = self.require_agent()
                if self.paused:
                    await self._wake.wait()
                    self._wake.clear()
                    continue
                if not agent.inbox:
                    await self._wake.wait()
                self._wake.clear()
                if self.paused or not agent.inbox:
                    continue
                self._turn_task = asyncio.create_task(agent.run_turn(), name="agent.turn")
                try:
                    await self._turn_task
                    self.error = ""
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                finally:
                    self._turn_task = None
                if not agent.inbox:
                    self._wake.clear()
                await self.persist()
            except asyncio.CancelledError:
                raise
            except AgentTurnError as exc:
                self.error = str(exc)
                logger.exception("Agent 本轮执行失败，等待后续请求，不重发原任务")
                await self.persist()
                from .messaging import report_failure

                for target, task_ids in exc.reply_targets.items():
                    try:
                        await report_failure(self, target, task_ids, self.error)
                    except Exception:
                        # 通知失败必须可诊断，但不能覆盖原始执行错误或阻止其他来源收到通知。
                        logger.exception("Agent 失败通知未送达：target=%s tasks=%s", target, task_ids)
            except Exception as exc:
                self.error, self.paused = str(exc), True
                logger.exception("Agent 执行失败，已暂停")
                await self.persist()

    async def resume_after_error(self) -> None:
        """新用户输入可重新尝试故障连接，系统输入不能解除暂停。"""
        if self.paused and self.error and not self._manual_pause:
            self.paused = False
            await self.persist()

    async def control(self, operation: str) -> Dict[str, Any]:
        self.require_agent()
        if operation not in {"pause", "resume"}:
            raise ValueError("Agent control 只接受 pause/resume，电脑控制请使用独立电脑接口。")
        if operation == "pause":
            self.paused = True
            self._manual_pause = True
            await _cancel_task(self._turn_task)
        else:
            self.paused, self.error = False, ""
            self._manual_pause = False
        self._wake.set()
        await self.persist()
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {"enabled": self.config.enabled, "name": self.name, "agent_id": self.agent_id,
                "backend": "acp",
                "external_session_id": self.agent.backend_state.get("session_id") if self.agent is not None else None,
                "paused": self.paused, "error": self.error,
                "agent_state": self.agent.state if self.agent is not None else "stopped"}


agent_service = AgentService()
