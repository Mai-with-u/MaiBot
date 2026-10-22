"""外接 Agent 接口：任务队列、执行记录、双向传话及可选沙盒桌面。"""

from contextlib import AsyncExitStack, suppress
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional
import asyncio
import json

from src.common.async_work import run_sync_work
from src.common.i18n import get_locale
from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.runtime_loop import run_on_main_loop
from src.config.config import config_manager, global_config

from src.computer.bridge import SandboxBridge
from .persistence import read_json, write_json
from .acp_agent import AcpAgent
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

    def __init__(self, root: Optional[Path] = None, bridge: Optional[SandboxBridge] = None) -> None:
        instance = get_instance_root()
        self.root = root if root is not None else instance / "data" / "agent"
        self.bridge = bridge if bridge is not None else SandboxBridge(
            instance / "temp" / "computer", instance / "data" / "computer" / "workspace", global_config.agent)
        self.agent: Optional[AcpAgent] = None
        self._closing = False
        self.agent_id = "__maibot_agent__"
        self.paused = False
        self.error = ""
        self._task: Optional[asyncio.Task[None]] = None
        self._reload_registered = False
        self._lifecycle_lock = asyncio.Lock()
        self.activity: Dict[str, Any] = {}
        self._wake = asyncio.Event()
        self._turn_task: Optional[asyncio.Task[None]] = None
        self.takeover = False
        self._state_lock = asyncio.Lock()
        self._settings: Optional[Dict[str, Any]] = None

    @property
    def config(self) -> AgentConfig:
        return global_config.agent

    @property
    def desktop_enabled(self) -> bool:
        return self.config.computer_tools

    @property
    def workspace(self) -> Path:
        return Path(self.config.acp_cwd).expanduser().absolute() if self.config.acp_cwd else self.root / "workspace"

    def policy(self) -> str:
        path = Path(__file__).parent / "prompts" / "acp" / f"{get_locale()}.txt"
        return path.read_text(encoding="utf-8")

    def require_desktop(self) -> None:
        self.require_agent()
        if not self.desktop_enabled:
            raise RuntimeError("当前 ACP 后端未启用电脑工具。")

    def image_path(self, reference: str) -> Path:
        guest = PureWindowsPath(reference)
        if guest.is_relative_to(PureWindowsPath(r"C:\MaiWorkspace")):
            self.require_desktop()
            guest = guest.relative_to(PureWindowsPath(r"C:\MaiWorkspace"))
            workspace = self.bridge.workspace.resolve()
            path = (workspace / str(guest)).resolve()
        else:
            workspace = self.workspace.resolve()
            raw = Path(reference).expanduser()
            path = (raw if raw.is_absolute() else workspace / raw).resolve()
        if not path.is_relative_to(workspace):
            raise ValueError("附图必须位于当前 Agent 工作目录或已启用的电脑工作目录。")
        return path

    async def observe(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        self.require_desktop()
        return await self.bridge.observe(timeout)

    async def persist(self) -> None:
        async with self._state_lock:
            state = {"paused": self.paused, "takeover": self.takeover, "error": self.error,
                     "activity": dict(self.activity)}
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
            self.bridge.config = self.config
            if not self.config.enabled:
                return
            state_path = self.root / "runtime.json"
            if await asyncio.to_thread(state_path.exists):
                state = await asyncio.to_thread(read_json, state_path)
                self.activity, self.paused, self.takeover, self.error = state["activity"], state["paused"], state["takeover"], state["error"]
            if self.desktop_enabled:
                try:
                    await self.bridge.restore()
                except ConnectionError as exc:
                    self.error, self.paused = str(exc), True
            agent = AcpAgent(self)
            await agent.load()
            self.agent, self._settings = agent, settings
            self._task = asyncio.create_task(self._run(), name="agent")

    async def stop(self) -> None:
        self._closing = True
        try:
            # 按依赖逆序清理；前一步失败时仍执行后续清理，异常继续向上传播。
            async with AsyncExitStack() as cleanup:
                cleanup.push_async_callback(self.bridge.detach)
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
            raise PermissionError("桌面和跨聊天读取由独立电脑 Agent 执行；普通聊天请使用 agent_talk。")

    async def context(self) -> str:
        """电脑专用环境上下文，每轮替换注入，不重复追加固定说明到聊天历史。"""
        frame = self.bridge.last_frame
        observation = {key: value for key, value in frame.items() if key != "image"} if frame else None
        state, activity = self.status(), dict(self.activity)
        return json.dumps({"state": state, "last_activity": activity, "last_observation": observation,
                           "workspace": str(self.workspace)}, ensure_ascii=False)

    async def _run(self) -> None:
        while True:
            try:
                agent = self.require_agent()
                if self.paused or self.takeover:
                    await self._wake.wait()
                    self._wake.clear()
                    continue
                if not agent.inbox:
                    await self._wake.wait()
                self._wake.clear()
                if self.paused or self.takeover or not agent.inbox:
                    continue
                self._turn_task = asyncio.create_task(agent.run_turn(), name="agent.turn")
                try:
                    await self._turn_task
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
            except Exception as exc:
                self.error, self.paused = str(exc), True
                logger.exception("Agent 执行失败，已暂停")
                await self.persist()

    async def desktop(self, actions: List[Dict[str, Any]], timeout: Optional[float]) -> Dict[str, Any]:
        self.require_desktop()
        if self.paused or self.takeover:
            raise RuntimeError("Agent 已暂停或正在人工接管，请在 WebUI 恢复。")
        try:
            result = await self.bridge.perform(actions, timeout)
            self.error = ""
            return result
        except ConnectionError as exc:
            # 连接丢失才暂停自主探索；软件报错由代理原样返回，不影响后续操作。
            self.error = str(exc)
            self.paused = True
            raise

    async def control(self, operation: str) -> Dict[str, Any]:
        self.require_agent()
        if operation not in {"pause", "resume", "takeover", "stop", "restart"}:
            raise ValueError("operation 只接受 pause/resume/takeover/stop/restart。")
        if operation not in {"pause", "resume"}:
            self.require_desktop()
        if operation in {"pause", "takeover", "stop", "restart"}:
            self.paused = True
            await _cancel_task(self._turn_task)
        if operation == "takeover":
            self.takeover = True
            if self.bridge.pending is not None:
                cancelled = await self.bridge.cancel(self.bridge.pending)
                if cancelled["status"] == "running":
                    await self.bridge.poll(cancelled["operation_id"], 0)
            # 即使 key_down 已经完成，也可能仍有持续按键；交接前统一释放。
            released = await self.bridge.perform([{"action": "release"}])
            if released["status"] == "running":
                await self.bridge.poll(released["operation_id"], 0)
        elif operation == "resume":
            self.paused = False
            self.takeover = False
            self.error = ""
        elif operation in {"stop", "restart"}:
            await self.bridge.close()
            if operation == "restart":
                self.paused = False
                self.takeover = False
                self.error = ""
        self._wake.set()
        await self.persist()
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {"enabled": global_config.agent.enabled, "name": self.name, "agent_id": self.agent_id,
                "backend": "acp", "desktop_enabled": self.desktop_enabled,
                "external_session_id": self.agent.backend_state.get("session_id") if self.agent is not None else None,
                "paused": self.paused, "error": self.error, "pending_request": self.bridge.pending,
                "takeover": self.takeover, "connected": self.bridge.connected, "sandbox_id": self.bridge.sandbox_id,
                "agent_state": self.agent.state if self.agent is not None else "stopped",
                "pause_scope": "decision_only; in-flight guest operation may still run",
                "sandbox_running": self.bridge.running}


agent_service = AgentService()
