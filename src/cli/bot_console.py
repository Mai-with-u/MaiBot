"""Bot 主程序的交互式本地管理终端。"""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4
from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import DynamicCompleter, WordCompleter
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout

import sys

from src.chat.heart_flow.heartflow_manager import heartflow_manager
from src.chat.message_receive.chat_manager import chat_manager
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.logger import redirect_console_logs
from src.config.config import global_config
from src.core.local_operator import (
    BOT_CONSOLE_PLATFORM,
    BOT_CONSOLE_USER_ID,
    LOCAL_OPERATOR_CONFIG_KEY,
)
from src.platform_io import DeliveryReceipt, DeliveryStatus, DriverDescriptor, DriverKind, RouteBinding, RouteKey
from src.platform_io.drivers.base import PlatformIODriver
from src.platform_io.manager import PlatformIOManager, get_platform_io_manager

from .console import console


class BotConsoleDriver(PlatformIODriver):
    """将 Bot 控制台平台的出站消息渲染到当前终端。"""

    DRIVER_ID = "local.bot_console"

    def __init__(self) -> None:
        super().__init__(
            DriverDescriptor(
                driver_id=self.DRIVER_ID,
                kind=DriverKind.LOCAL,
                platform=BOT_CONSOLE_PLATFORM,
            )
        )

    async def send_message(
        self,
        message: SessionMessage,
        route_key: RouteKey,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryReceipt:
        """渲染消息并返回标准 Platform IO 回执。"""

        content = message.processed_plain_text.strip() if message.processed_plain_text else ""
        if not content:
            component_names = [component.format_name for component in message.raw_message.components]
            content = f"[{', '.join(component_names)}]"

        console.print(
            Panel(
                Markdown(content),
                title=global_config.bot.nickname.strip() or "MaiBot",
                border_style="magenta",
                padding=(1, 2),
            )
        )
        return DeliveryReceipt(
            internal_message_id=message.message_id,
            route_key=route_key,
            status=DeliveryStatus.SENT,
            driver_id=self.driver_id,
            driver_kind=self.descriptor.kind,
        )


class BotConsole:
    """读取本地终端输入，并交给 Bot 的正式消息处理链。"""

    _COMMAND_COMPLETIONS = [
        "/help",
        "/offline",
        "/online",
        "/pm",
        "/pm alias",
        "/pm alias add",
        "/pm alias help",
        "/pm alias list",
        "/pm alias remove",
        "/pm component",
        "/pm component disable global",
        "/pm component disable local",
        "/pm component enable global",
        "/pm component enable local",
        "/pm component help",
        "/pm component list",
        "/pm component list disabled",
        "/pm component list enabled",
        "/pm component list type",
        "/pm config",
        "/pm config get",
        "/pm config help",
        "/pm config set",
        "/pm help",
        "/pm plugin",
        "/pm plugin help",
        "/pm plugin list",
        "/pm plugin list_enabled",
        "/pm plugin load",
        "/pm plugin reload",
        "/pm plugin unload",
        "exit()",
    ]

    def __init__(self) -> None:
        self._driver = BotConsoleDriver()
        self._platform_io: PlatformIOManager = get_platform_io_manager()
        self._session_id: Optional[str] = None
        self._prompt_session: PromptSession[str] = PromptSession(
            completer=DynamicCompleter(self._create_completer),
            complete_while_typing=False,
            history=InMemoryHistory(),
        )

    def _create_completer(self) -> WordCompleter:
        """根据当前真实聊天流动态构造终端补全器。"""

        chat_completions = [
            f"/clear {chat_name}"
            for chat_name in chat_manager.get_named_session_options(
                excluded_platforms={BOT_CONSOLE_PLATFORM},
            )
        ]
        return WordCompleter(
            [*self._COMMAND_COMPLETIONS, *chat_completions],
            ignore_case=True,
            sentence=True,
        )

    @staticmethod
    def _build_message(text: str) -> SessionMessage:
        """构造具有本地操作员身份的终端消息。"""

        message = SessionMessage(
            message_id=f"bot_console_{uuid4().hex}",
            timestamp=datetime.now(),
            platform=BOT_CONSOLE_PLATFORM,
        )
        message.message_info = MessageInfo(
            user_info=UserInfo(
                user_id=BOT_CONSOLE_USER_ID,
                user_nickname="本地操作员",
            ),
            group_info=None,
            additional_config={LOCAL_OPERATOR_CONFIG_KEY: True},
        )
        message.raw_message = MessageSequence([TextComponent(text=text)])
        return message

    async def _register_output_driver(self) -> None:
        """注册终端平台驱动及其精确发送路由。"""

        if self._platform_io.is_started:
            await self._platform_io.add_driver(self._driver)
        else:
            self._platform_io.register_driver(self._driver)

        try:
            self._platform_io.bind_send_route(
                RouteBinding(
                    route_key=self._driver.descriptor.route_key,
                    driver_id=self._driver.driver_id,
                    driver_kind=self._driver.descriptor.kind,
                )
            )
        except Exception:
            if self._platform_io.is_started:
                await self._platform_io.remove_driver(self._driver.driver_id)
            else:
                self._platform_io.unregister_driver(self._driver.driver_id)
            raise

    async def _unregister_output_driver(self) -> None:
        """注销终端平台驱动及其发送路由。"""

        self._platform_io.unbind_send_route(self._driver.descriptor.route_key, self._driver.driver_id)
        if self._platform_io.is_started:
            await self._platform_io.remove_driver(self._driver.driver_id)
        else:
            self._platform_io.unregister_driver(self._driver.driver_id)

    @staticmethod
    def _show_banner() -> None:
        """显示本地管理终端说明。"""

        banner = Text()
        banner.append("MaiBot 管理终端\n", style="bold cyan")
        banner.append("输入消息或管理指令，使用 /help 查看帮助\n", style="muted")
        banner.append("Tab 补全 | ↑↓ 历史 | exit() 关闭终端输入 | Ctrl+C 退出 Bot", style="muted")
        console.print(Panel(banner, box=box.DOUBLE_EDGE, border_style="cyan", padding=(1, 2)))
        console.print()

    @staticmethod
    def _show_help() -> None:
        """显示管理终端指令和快捷键帮助。"""

        help_text = """\
### 管理指令

- `/help`：显示本帮助
- `/clear <聊天名>`：清空指定真实聊天的 Maisaka 历史；输入 `/clear ` 后按 Tab 选择
- `/offline`：关闭当前所有适配器插件
- `/online`：恢复本次运行中由 `/offline` 关闭的适配器插件
- `/pm help`：查看插件与组件管理指令
- `exit()`：关闭终端输入，Bot 继续运行

### 输入操作

- `Tab`：补全指令或聊天名
- `↑` / `↓`：浏览本次运行的输入历史
- `Ctrl+C`：退出 Bot
- 其他文本：作为本地操作员消息进入正式聊天处理链
"""
        console.print(
            Panel(
                Markdown(help_text),
                title="MaiBot 管理终端帮助",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    async def _dispatch_input(self, text: str) -> None:
        """将一行输入交给正式消息处理链。"""

        from src.chat.message_receive.bot import chat_bot

        message = self._build_message(text)
        await chat_bot.receive_message(message)
        self._session_id = message.session_id

    async def run(self) -> None:
        """运行管理终端，直至任务取消、stdin 关闭或收到 ``exit()``。"""

        await self._register_output_driver()
        try:
            self._show_banner()

            with patch_stdout(raw=True), redirect_console_logs(sys.stdout):
                while True:
                    try:
                        raw_input = await self._prompt_session.prompt_async(
                            ANSI("\x1b[1;36m> \x1b[0m")
                        )
                    except EOFError:
                        console.print("\n[muted]终端输入已关闭[/muted]")
                        return

                    text = raw_input.strip()
                    if not text:
                        continue
                    if text.lower() == "exit()":
                        console.print("[muted]终端输入已关闭，Bot 将继续运行[/muted]")
                        return
                    if text == "/help":
                        self._show_help()
                        continue

                    await self._dispatch_input(text)
        finally:
            await self._unregister_output_driver()
            if self._session_id is not None:
                runtime = heartflow_manager.heartflow_chat_list.pop(self._session_id, None)
                if runtime is not None:
                    await runtime.stop()
