"""独立 Windows 桌宠：本地窗口和桌面能力通过宿主连接远端 MaiBot。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4
import argparse
import asyncio
import base64
import getpass
import hashlib
import json
import math
import os
import queue
import re
import sys
import threading

import aiohttp

from .app_conditions import running_applications
from .attachments import MAX_REQUEST_BYTES
from .pet_window import PAGE, PAGE_HEIGHT, PAGE_WIDTH, PetApi, _bind_win32, execute_command, user32
from .screen import DesktopScreenshot, capture_desktop, screen_change_ratio

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
HTTP_TIMEOUT = 60


class SessionExpiredError(ConnectionError):
    """宿主已接受 WebSocket 后，用关闭码 4001 拒绝失效的登录会话。"""


def normalize_server(value: str) -> str:
    """服务器地址只包含宿主地址与可选反代前缀，凭据始终通过登录请求发送。"""
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("服务器地址必须以 http:// 或 https:// 开头")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("服务器地址不能包含用户名、密码、查询参数或锚点")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def remote_base(server: str, bot_id: str) -> str:
    if not bot_id.strip() or len(bot_id) > 128:
        raise ValueError("请填写有效的 Bot 实例 ID")
    return f"{normalize_server(server)}/api/host/bots/{quote(bot_id.strip(), safe='')}/proxy/api/webui/desktop-pet/remote"


@dataclass(frozen=True)
class ClientOptions:
    server: str
    bot_id: str
    password: str
    allow_screen_read: bool = False
    bot_name: str = ""


async def authenticate_session(session: aiohttp.ClientSession, server: str, password: str) -> None:
    async with session.post(normalize_server(server) + "/api/webui/auth/verify",
                            json={"token": password}, allow_redirects=False) as response:
        if response.status in {401, 403, 429}:
            raise PermissionError("登录失败或尝试过多，请检查 WebUI 密码后重新启动桌宠")
        if response.status != 200:
            raise RuntimeError(f"登录失败：HTTP {response.status}")
        result = await response.json()
        if not result.get("valid"):
            raise PermissionError("登录失败，请检查 WebUI 密码并重新启动桌宠")


async def discover_bots(server: str, password: str) -> List[Dict[str, Any]]:
    """临时登录读取实例名称，结束时撤销此次发现使用的会话。"""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                     timeout=aiohttp.ClientTimeout(total=15)) as session:
        await authenticate_session(session, server, password)
        try:
            async with session.get(normalize_server(server) + "/api/host/bots", allow_redirects=False) as response:
                if response.status != 200:
                    raise RuntimeError(f"读取机器人失败：HTTP {response.status}")
                return (await response.json())["bots"]
        finally:
            async with session.post(normalize_server(server) + "/api/webui/auth/logout", allow_redirects=False):
                pass


class RemoteTodoStore:
    """窗口使用的待办接口；唯一持久化副本保存在当前连接的服务器。"""

    def __init__(self, connection: "RemoteConnection") -> None:
        self._connection = connection

    def _request(self, action: str, **data: Any) -> Dict[str, Any]:
        payload = {"action": action, **data}
        if action != "list":
            payload["request_id"] = uuid4().hex
        return self._connection.request("POST", "/todos", payload)

    def list(self, include_completed: bool = False) -> List[Dict[str, Any]]:
        return self._request("list", include_completed=include_completed)["items"]

    def add(self, text: str, **options: Any) -> Dict[str, Any]:
        return self._request("add", text=text, options=options)["item"]

    def update(self, todo_id: str, text: Optional[str] = None, **options: Any) -> Dict[str, Any]:
        return self._request("update", todo_id=todo_id, text=text, options=options)["item"]

    def done(self, todo_id: str) -> None:
        self._request("complete", todo_id=todo_id)

    def image(self, image_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", image_id):
            raise ValueError("无效的待办图片 ID")
        return self._connection.request("GET", "/todos/images/" + image_id)["data_url"]


class RemoteConnection:
    """网络循环独立于 GUI；线程桥接不把文件和网络等待放在事件循环中。"""

    def __init__(self, options: ClientOptions, client_id: str) -> None:
        self.options = options
        self.client_id = client_id
        self.base = remote_base(options.server, options.bot_id)
        self.page_ready = threading.Event()
        self.stopped = threading.Event()
        self.connected = threading.Event()
        self.api: Optional[RemotePetApi] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[asyncio.Task[None]] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._connection_id = ""
        self._send_lock = asyncio.Lock()
        self._sample: Optional[DesktopScreenshot] = None

    def _display(self, message: Dict[str, Any]) -> None:
        if self.stopped.is_set() or self.api is None or self.api._window is None:
            return
        self.api._window.evaluate_js(f"window.pet.receive({json.dumps(message)})")

    def _show(self) -> None:
        self.api._window.show()
        self.api._window.evaluate_js("window.petSkin.visibility(true)")

    async def _state(self, connected: bool, text: str) -> None:
        await asyncio.to_thread(self._display, {"type": "connection", "connected": connected, "text": text})

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.connected.is_set() or self.loop is None:
            raise ConnectionError("桌宠未连接服务器，请等待连接恢复")
        future = asyncio.run_coroutine_threadsafe(self._request(method, path, payload), self.loop)
        try:
            return future.result(HTTP_TIMEOUT + 5)
        except TimeoutError as exc:
            future.cancel()
            raise TimeoutError("请求超时，结果尚未确认；请刷新记录后再操作") from exc

    async def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.connected.is_set() or self._session is None:
            raise ConnectionError("桌宠已离线")
        connection_id = self._connection_id
        encoded = await asyncio.to_thread(json.dumps, payload) if payload is not None else None
        if encoded is not None and len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("图文请求过大")
        async with self._session.request(
            method, self.base + path, data=encoded,
            headers={"X-Desktop-Pet-Connection": connection_id, "Content-Type": "application/json"},
            allow_redirects=False,
        ) as response:
            result = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(str(result.get("detail", f"服务器返回 HTTP {response.status}")))
            if response.status >= 300:
                raise RuntimeError("服务器返回重定向，请填写最终的宿主地址")
            return result

    def emit(self, event: Dict[str, Any]) -> None:
        if event["type"] == "ready":
            self.page_ready.set()
            return
        if not self.connected.is_set() or self.loop is None:
            # 断线期间仍可拖动和点击本地窗口，不积压过期的观察事件。
            if event["type"] in {"clicked", "drag_started", "drag_ended", "buttons_refresh"}:
                return
            raise ConnectionError("桌宠未连接服务器")
        future = asyncio.run_coroutine_threadsafe(self._send(event), self.loop)
        try:
            future.result(10)
        except TimeoutError as exc:
            future.cancel()
            raise TimeoutError("服务器通信超时，请等待连接恢复") from exc

    async def _send(self, event: Dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("桌宠已离线")
        payload = await asyncio.to_thread(json.dumps, event)
        async with self._send_lock:
            await ws.send_str(payload)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(20)
            await self._send({"type": "ping"})

    def _execute(self, command: Dict[str, Any]) -> Dict[str, Any]:
        ack: Dict[str, Any] = {"type": "ack", "id": command["id"]}
        try:
            if command["type"] in {"capture_screen", "sample_screen"}:
                if not self.options.allow_screen_read:
                    raise PermissionError("客户端未授权读取桌面，请勾选允许读取桌面后重新连接")
                shot = capture_desktop()
                if command["type"] == "sample_screen":
                    threshold = command["threshold"]
                    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0.001 <= threshold <= 1:
                        raise ValueError("无效的屏幕变化阈值")
                    if command.get("reset", False):
                        self._sample = None
                    if self._sample is not None and screen_change_ratio(self._sample, shot) < threshold:
                        ack["result"] = {"unchanged": True}
                        return ack
                    self._sample = shot
                ack["result"] = {
                    "base64": base64.b64encode(shot.data).decode("ascii"), "captured_at": shot.captured_at,
                    "width": shot.width, "height": shot.height, "digest": shot.digest,
                }
            elif command["type"] == "applications":
                ack["result"] = {"items": running_applications()}
            elif command["type"] == "todo_refresh":
                self.api._window.evaluate_js("window.pet.refreshTodos().catch(e => window.pet.todoError(String(e)))")
            elif command["type"] == "todo":
                raise ValueError("远端待办必须由服务器执行")
            else:
                ack = execute_command(self.api, self.api._window, command)
        except Exception as exc:
            ack["error"] = str(exc)
        return ack

    async def _connect(self) -> None:
        parts = urlsplit(self.base + "/ws")
        url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, parts.path, "", ""))
        async with self._session.ws_connect(url, heartbeat=20, max_msg_size=MAX_MESSAGE_BYTES) as ws:
            self._ws = ws
            await self._send({"type": "hello", "version": PROTOCOL_VERSION, "client_id": self.client_id,
                              "capabilities": {"screen_read": self.options.allow_screen_read}})
            greeting = await ws.receive(timeout=15)
            if greeting.type != aiohttp.WSMsgType.TEXT:
                if ws.close_code == 4001 or greeting.data == 4001:
                    raise SessionExpiredError("宿主登录会话已失效")
                raise ConnectionError("服务器在协议协商完成前关闭了连接")
            hello = await asyncio.to_thread(json.loads, greeting.data)
            if hello.get("type") != "hello" or hello.get("version") != PROTOCOL_VERSION:
                raise ValueError(str(hello.get("error", "桌宠协议版本不兼容，请同步更新客户端和服务器")))
            self._connection_id = hello["connection_id"]
            await self._send({"type": "ready"})
            confirmation = await ws.receive(timeout=15)
            if confirmation.type != aiohttp.WSMsgType.TEXT:
                if ws.close_code == 4001 or confirmation.data == 4001:
                    raise SessionExpiredError("宿主登录会话已失效")
                raise ConnectionError("服务器未确认桌宠就绪")
            acknowledged = await asyncio.to_thread(json.loads, confirmation.data)
            if acknowledged.get("type") != "ready" or acknowledged.get("version") != PROTOCOL_VERSION:
                raise ValueError(str(acknowledged.get("error", "服务器未确认桌宠就绪，请同步更新本体和客户端")))
            self.connected.set()
            await asyncio.to_thread(self._show)
            await self._state(True, f"已连接 · {self.options.bot_name or '远端麦麦'}")
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                while not self.stopped.is_set():
                    message = await ws.receive(timeout=65)
                    if message.type == aiohttp.WSMsgType.TEXT:
                        command = await asyncio.to_thread(json.loads, message.data)
                        if command.get("type") == "pong":
                            continue
                        if command.get("type") == "error":
                            raise RuntimeError(str(command.get("error", "服务器拒绝桌宠连接")))
                        if not isinstance(command.get("id"), str):
                            raise ValueError("服务器指令缺少回执 ID")
                        await self._send(await asyncio.to_thread(self._execute, command))
                    elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
                        if ws.close_code == 4001 or message.data == 4001:
                            raise SessionExpiredError("宿主登录会话已失效")
                        raise ConnectionError("服务器连接已关闭")
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._runner = asyncio.current_task()
        while not self.page_ready.is_set() and not self.stopped.is_set():
            await asyncio.sleep(0.05)
        # IP 地址部署同样需要保存宿主 Cookie；Cookie 仅保存在本次客户端内存中。
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT, connect=15)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True), timeout=timeout) as session:
                self._session = session
                delay = 1
                authenticated = False
                while not self.stopped.is_set():
                    error = ""
                    try:
                        if not authenticated:
                            await self._state(False, "正在登录服务器…")
                            await authenticate_session(session, self.options.server, self.options.password)
                            authenticated = True
                        await self._connect()
                    except asyncio.CancelledError:
                        raise
                    except PermissionError:
                        # 错误口令不能自动重试，避免触发宿主防暴力破解限制。
                        raise
                    except SessionExpiredError:
                        authenticated = False
                        error = "登录会话已失效，正在重新登录"
                    except aiohttp.WSServerHandshakeError as exc:
                        if exc.status in {401, 403}:
                            # 宿主重启或会话过期后，使用本次启动内存中的口令重新登录。
                            authenticated = False
                            error = "登录会话已失效，正在重新登录"
                        else:
                            error = f"服务器拒绝连接：HTTP {exc.status}"
                    except Exception as exc:
                        error = str(exc)
                    finally:
                        if self.connected.is_set():
                            delay = 1
                        self.connected.clear()
                        self._connection_id = ""
                        self._ws = None
                        self._sample = None
                    await self._state(False, f"已离线：{error}；{delay} 秒后重连")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._state(False, str(exc))
        finally:
            self.connected.clear()
            self._session = None
            self.loop = None

    def start(self) -> None:
        asyncio.run(self.run())

    def stop(self) -> None:
        self.stopped.set()
        if self.loop is not None and self._runner is not None:
            self.loop.call_soon_threadsafe(self._runner.cancel)


class RemotePetApi(PetApi):
    def __init__(self, connection: RemoteConnection, data_dir: Path) -> None:
        super().__init__(connection, RemoteTodoStore(connection), data_dir / "window.json", data_dir / "requests")
        self._connection = connection

    def send(self, request_id: str, text: str, images: List[Dict[str, str]]) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("无效请求 ID")
        self._connection.request("POST", "/chat/" + request_id, {"text": text, "images": images})
        self._connection._display({"type": "accepted", "request_id": request_id})

    def _allow_close(self) -> bool:
        # 独立客户端由用户关闭，不受本机 Bot 子进程生命周期约束。
        self._closing = True
        self._connection.stop()
        if self._exploration_window is not None:
            self._exploration_window.destroy()
        return True

    def exit_client(self) -> None:
        self._connection.stop()
        self._shutdown()


def login_dialog(data_dir: Path, allow_screen_read: bool) -> Optional[ClientOptions]:
    """启动表单按名称选择实例；只保存地址，密码与桌面授权每次重新输入。"""
    from tkinter import BooleanVar, StringVar, Tk, messagebox, ttk

    saved_path = data_dir / "connection.json"
    saved = json.loads(saved_path.read_text("utf-8")) if saved_path.exists() else {}
    root = Tk()
    root.title("连接远端麦麦")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=20)
    frame.grid()
    address = StringVar(value=saved.get("server", ""))
    password = StringVar()
    for index, (label, value) in enumerate([("服务器地址", address), ("WebUI 登录密码", password)]):
        ttk.Label(frame, text=label).grid(row=index * 2, column=0, columnspan=2, sticky="w", pady=(7, 2))
        ttk.Entry(frame, textvariable=value, width=46, show="●" if index == 1 else "").grid(
            row=index * 2 + 1, column=0, columnspan=2, sticky="ew")
    ttk.Label(frame, text="机器人").grid(row=4, column=0, sticky="w", pady=(12, 2))
    chooser = ttk.Combobox(frame, state="readonly", width=43)
    chooser.grid(row=5, column=0, columnspan=2, sticky="ew")
    bots: List[Dict[str, Any]] = []
    if saved.get("bot_id"):
        bots.append({"bot_id": saved["bot_id"], "name": saved.get("bot_name") or "上次连接的麦麦"})
        chooser["values"] = [bots[0]["name"]]
        chooser.current(0)
    feedback = StringVar(value="填写地址和密码后，读取机器人列表。密码不会保存。")
    ttk.Label(frame, textvariable=feedback, wraplength=350).grid(row=6, column=0, columnspan=2, pady=(10, 5))
    screen = BooleanVar(value=allow_screen_read)
    ttk.Checkbutton(frame, text="允许此麦麦读取本机桌面", variable=screen).grid(row=7, column=0, columnspan=2, sticky="w")
    result: List[ClientOptions] = []
    results: queue.Queue = queue.Queue()

    def address_changed(*_args: str) -> None:
        bots.clear()
        chooser["values"] = []
        chooser.set("")
        feedback.set("服务器地址已改变，请重新读取机器人。")

    address.trace_add("write", address_changed)

    def apply_discovery() -> None:
        try:
            original_address, original_password, found, error = results.get_nowait()
        except queue.Empty:
            root.after(100, apply_discovery)
            return
        fetch_button["state"] = start_button["state"] = "normal"
        if address.get() != original_address or password.get() != original_password:
            feedback.set("连接参数已改变，请重新读取机器人。")
            return
        if error:
            feedback.set(error)
            return
        bots[:] = found
        chooser["values"] = [bot["name"] + (" · 运行中" if bot.get("status") == "running" else " · 未启动") for bot in bots]
        if bots:
            selected = next((index for index, bot in enumerate(bots) if bot["bot_id"] == saved.get("bot_id")), 0)
            chooser.current(selected)
            feedback.set("请选择机器人，并确保其桌宠配置已启用远端模式。")
        else:
            chooser.set("")
            feedback.set("此宿主还没有机器人，请先在 WebUI 创建并启动。")

    def fetch() -> None:
        original_address, original_password = address.get(), password.get()
        try:
            server = normalize_server(original_address)
            if not original_password:
                raise ValueError("请输入 WebUI 登录密码")
        except ValueError as exc:
            feedback.set(str(exc))
            return
        fetch_button["state"] = start_button["state"] = "disabled"
        feedback.set("正在登录并读取机器人…")

        def read() -> None:
            found, error = [], ""
            try:
                found = asyncio.run(discover_bots(server, original_password))
            except Exception as exc:
                error = str(exc)
            results.put((original_address, original_password, found, error))

        threading.Thread(target=read, name="desktop-pet-discovery", daemon=True).start()
        root.after(100, apply_discovery)

    def connect() -> None:
        try:
            server = normalize_server(address.get())
            if chooser.current() < 0 or not bots:
                raise ValueError("请先读取并选择机器人")
            bot = bots[chooser.current()]
            remote_base(server, bot["bot_id"])
            if not password.get():
                raise ValueError("请输入 WebUI 登录密码")
            saved_path.write_text(json.dumps({"server": server, "bot_id": bot["bot_id"], "bot_name": bot["name"]}, ensure_ascii=False), "utf-8")
            result.append(ClientOptions(server, bot["bot_id"], password.get(), screen.get(), bot["name"]))
            root.destroy()
        except (ValueError, OSError) as exc:
            messagebox.showerror("无法连接", str(exc), parent=root)

    fetch_button = ttk.Button(frame, text="读取机器人", command=fetch)
    fetch_button.grid(row=4, column=1, sticky="e", pady=(10, 2))
    start_button = ttk.Button(frame, text="启动桌宠", command=connect)
    start_button.grid(row=8, column=1, pady=(16, 0), sticky="e")
    root.bind("<Return>", lambda _event: connect())
    root.mainloop()
    return result[0] if result else None


def parse_options(argv: Optional[List[str]] = None) -> Tuple[Optional[ClientOptions], Path]:
    parser = argparse.ArgumentParser(description="连接远端 MaiBot 的独立桌宠客户端")
    parser.add_argument("--server", help="WebUI 宿主地址，例如 https://mai.example.com")
    parser.add_argument("--bot-id", help="WebUI 中的 Bot 实例 ID")
    parser.add_argument("--token-env", default="MAIBOT_DESKTOP_PET_TOKEN", help="保存 WebUI 登录密码的环境变量名")
    parser.add_argument("--allow-screen-read", action="store_true", help="允许服务器请求读取本机主屏")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MaiBotDesktopPet")
    args = parser.parse_args(argv)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.server is None:
        return login_dialog(args.data_dir, args.allow_screen_read), args.data_dir
    if not args.bot_id:
        parser.error("使用 --server 时必须提供 --bot-id")
    password = os.environ.get(args.token_env)
    if not password:
        password = getpass.getpass("WebUI 登录密码：")
    return ClientOptions(normalize_server(args.server), args.bot_id, password, args.allow_screen_read), args.data_dir


def main() -> None:
    options, data_dir = parse_options()
    if options is None:
        return
    if sys.platform != "win32":
        raise SystemExit("桌宠客户端需要 Windows 桌面环境")
    import webview

    # 每个服务器实例独立保存窗口位置与 WebView 缓存，避免跨服务器混用本地状态。
    identity = hashlib.sha256(remote_base(options.server, options.bot_id).encode()).hexdigest()[:24]
    instance_dir = data_dir / identity
    instance_dir.mkdir(parents=True, exist_ok=True)
    client_path = instance_dir / "client-id"
    client_id = client_path.read_text("ascii") if client_path.exists() else uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", client_id):
        raise ValueError("客户端标识文件损坏，请检查 client-id")
    client_path.write_text(client_id, "ascii")
    connection = RemoteConnection(options, client_id)
    _bind_win32()
    user32.SetProcessDPIAware()
    api = RemotePetApi(connection, instance_dir)
    connection.api = api
    window = webview.create_window(
        "MaiBot 远端桌宠", url=PAGE.as_uri(), js_api=api, width=PAGE_WIDTH, height=PAGE_HEIGHT,
        resizable=False, frameless=True, easy_drag=False, transparent=True, on_top=True, shadow=False,
    )
    window.events.before_show += api._attach
    window.events.closing += api._allow_close
    window.events.closed += connection.stop
    try:
        webview.start(connection.start, private_mode=True, storage_path=str(instance_dir / "webview"))
    finally:
        connection.stop()


if __name__ == "__main__":
    main()
