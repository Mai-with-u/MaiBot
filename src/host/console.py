"""宿主独占终端输入；选择目标后经私有 IPC 交给 Bot 的本地操作员链路。"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout

import asyncio
import sys
import tomllib

from src.common.ipc import http_connector

from .supervisor import BotSupervisor


class HostConsole:
    def __init__(self, supervisor: BotSupervisor, stopping: asyncio.Event) -> None:
        self.supervisor = supervisor
        self.stopping = stopping
        self.bot_id: Optional[str] = None
        self.bot_name = "未选择 Bot"
        self._commands: List[str] = []

    @staticmethod
    def _input_enabled(bot: Dict[str, Any]) -> bool:
        if bot["initialization"] != "initialized":
            return False
        path = Path(bot["directory"]) / "config" / "bot_config.toml"
        with path.open("rb") as stream:
            # 与 DebugConfig 的默认值保持一致；宿主不导入任何 Bot 的全局配置。
            return tomllib.load(stream).get("debug", {}).get("enable_console_input", True) is True

    def _prompt(self, session: PromptSession[str]) -> str:
        # 空闲时只保留输入位置，目标名称在实际输入时显示，日志底部不常驻状态文字。
        return f"[{self.bot_name}] > " if session.default_buffer.text else ""

    async def request(self, method: str, path: str = "", text: Optional[str] = None) -> Any:
        if self.bot_id is None:
            raise ValueError("请先用 /bots 查看，再用 /use 名称或ID前缀选择 Bot")
        endpoint = self.supervisor.proxy_endpoint(self.bot_id)
        # 不重试 POST：断线时消息可能已经执行，自动重发会重复执行管理命令。
        async with ClientSession(
            connector=http_connector(endpoint), cookie_jar=DummyCookieJar(),
            timeout=ClientTimeout(total=None, sock_connect=5),
        ) as client:
            async with client.request(method, "http://bot/_host/console" + path, json={"text": text} if text is not None else None) as response:
                if response.status != 200:
                    raise RuntimeError(f"Bot 拒绝终端请求（{response.status}）：{await response.text()}")
                return await response.json()

    async def select(self, selector: str) -> None:
        bots = await self.supervisor.statuses()
        exact = [bot for bot in bots if bot["bot_id"] == selector]
        matches = exact or [bot for bot in bots if bot["name"] == selector or bot["bot_id"].startswith(selector)]
        if not selector or len(matches) != 1:
            raise ValueError("Bot 不存在或名称/ID前缀不唯一，请用 /bots 查看完整 ID")
        bot = matches[0]
        self.bot_id, self.bot_name = bot["bot_id"], bot["name"]
        self._commands = []
        print(f"终端目标：{self.bot_name}（{self.bot_id}）", flush=True)

    async def handle(self, text: str) -> bool:
        """返回 False 仅表示停止输入，不停止宿主或 Bot。"""
        text = text.strip()
        if not text:
            return True
        if text.lower() == "exit()":
            return False
        if text == "/bots":
            for bot in await self.supervisor.statuses():
                marker = "*" if bot["bot_id"] == self.bot_id else " "
                print(f"{marker} {bot['name']}  {bot['bot_id']}  {bot['status']}", flush=True)
        elif text == "/use" or text.startswith("/use "):
            await self.select(text[4:].strip())
        elif text == "/host-help":
            print("/bots 列出 Bot；/use 名称或ID前缀 切换；/help 查看 Bot 指令；exit() 关闭输入；Ctrl+C 关闭宿主。", flush=True)
        else:
            await self.request("POST", text=text)
        return True

    async def run(self) -> None:
        # 服务、重定向及 Docker 非交互启动不读取 stdin，也不占住默认线程池。
        if sys.stdin is None or not sys.stdin.isatty():
            return
        bots = await self.supervisor.statuses()
        enabled = []
        for bot in bots:
            try:
                if await asyncio.to_thread(self._input_enabled, bot):
                    enabled.append(bot)
            except (OSError, ValueError) as exc:
                print(f"无法读取 {bot['name']} 的终端输入配置，未启用其输入：{exc}", file=sys.stderr, flush=True)
        if not enabled:
            return
        session: PromptSession[str] = PromptSession(history=InMemoryHistory())
        with patch_stdout(raw=True):
            if len(enabled) == 1:
                await self.select(enabled[0]["bot_id"])
            await self.handle("/host-help")
            while not self.stopping.is_set():
                try:
                    # 补全失败不能把旧 Bot 的聊天名带到新目标；发送仍会报告具体错误。
                    self._commands = []
                    if self.bot_id is not None:
                        try:
                            self._commands = await asyncio.wait_for(self.request("GET", "/completions"), timeout=3)
                        except (ClientError, TimeoutError, RuntimeError):
                            pass
                    bots = await self.supervisor.statuses()
                    completer = WordCompleter(
                        ["/bots", "/use", "/host-help", "exit()", *self._commands,
                         *[f"/use {bot['name']}" for bot in bots]], sentence=True,
                    )
                    text = await session.prompt_async(lambda: self._prompt(session), completer=completer)
                    if not await self.handle(text):
                        break
                except EOFError:
                    break
                except KeyboardInterrupt:
                    self.stopping.set()
                    return
                except (ClientError, TimeoutError, ValueError, RuntimeError) as exc:
                    print(f"终端输入失败：{exc}（未自动重发）", file=sys.stderr, flush=True)
            print("终端输入已关闭，Bot 继续运行。", flush=True)
