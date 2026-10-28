"""桌宠窗口进程。

由主程序以子进程方式启动：stdout 每行一条 JSON 事件发往主程序，stdin 每行一条 JSON 指令来自主程序。
窗口透明、无边框、置顶且不进任务栏；窗口区域按页面上报的可见元素裁剪，透明处的点击交还给桌面。
窗口位置与尺寸一律按物理像素处理，页面缩放（系统 DPI 与“文本大小”设置）由页面上报的 devicePixelRatio 决定。
"""

from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TextIO, Tuple

import argparse
import ctypes
import io
import json
import os
import sys
import threading
import webview

if TYPE_CHECKING:
    from .todos import TodoStore

# 页面设计尺寸（CSS 像素），与 index.html 布局一致。
PAGE_WIDTH = 260
PAGE_HEIGHT = 460
PAGE = Path(__file__).parent / "web" / "index.html"

# 页面就绪前先放行桌宠所在的大致区域，与 index.html 中 #pet 的位置一致。
INITIAL_HIT_RECT = [47.0, PAGE_HEIGHT - 4 - 107 - 24.0, 166.0, 139.0, 42.0]
# 默认位置距工作区右边缘的留白（CSS 像素）。
EDGE_MARGIN = 8

GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
RGN_OR = 2
DCX_WINDOW = 0x0001
DCX_CACHE = 0x0002
BLACK_BRUSH = 4
SPI_GETWORKAREA = 0x0030
MONITOR_DEFAULTTONULL = 0

user32 = ctypes.windll.user32 if sys.platform == "win32" else None
gdi32 = ctypes.windll.gdi32 if sys.platform == "win32" else None
dwmapi = ctypes.windll.dwmapi if sys.platform == "win32" else None


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("right", ctypes.c_int),
        ("top", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


def _bind_win32() -> None:
    dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
    dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    user32.SetProcessDPIAware.restype = wintypes.BOOL
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDCEx.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.DWORD]
    user32.GetDCEx.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
    user32.FillRect.restype = ctypes.c_int
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    gdi32.GetStockObject.restype = wintypes.HGDIOBJ
    gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi32.CreateRectRgn.restype = wintypes.HRGN
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
    gdi32.CombineRgn.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL


def apply_region(hwnd: int, rects: List[List[float]], scale: float) -> None:
    """把窗口可见/可点击区域设为若干圆角矩形的并集。

    Args:
        hwnd: 窗口句柄。
        rects: ``[x, y, 宽, 高, 圆角半径]`` 列表，单位为 CSS 像素。
        scale: CSS 像素到物理像素的缩放比例。
    """
    region = gdi32.CreateRectRgn(0, 0, 0, 0)
    for x, y, width, height, radius in rects:
        diameter = round(radius * 2 * scale)
        part = gdi32.CreateRoundRectRgn(
            round(x * scale),
            round(y * scale),
            round((x + width) * scale) + 1,
            round((y + height) * scale) + 1,
            diameter,
            diameter,
        )
        gdi32.CombineRgn(region, region, part, RGN_OR)
        gdi32.DeleteObject(part)
    # 设置成功后区域归系统所有，不能再释放。
    if not user32.SetWindowRgn(hwnd, region, True):
        gdi32.DeleteObject(region)
        raise ctypes.WinError()


def clear_gdi_layer(hwnd: int) -> None:
    """把窗口的 GDI 重定向层整体涂黑；在 DWM 玻璃下黑色即全透明。

    WebView2 启动较慢时，首帧会先以白底画进这一层，之后页面改由 DirectComposition 呈现。
    这层旧内容被浏览器子窗口挡住、各级窗口都会裁掉子窗口，所以再也没人重绘它，
    在透明处显示成白块。这里用不裁剪子窗口的窗口 DC 直接覆盖整层。
    """
    hdc = user32.GetDCEx(hwnd, None, DCX_WINDOW | DCX_CACHE)
    if not hdc:
        raise ctypes.WinError()
    try:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
        layer = wintypes.RECT(0, 0, rect.right - rect.left, rect.bottom - rect.top)
        if not user32.FillRect(hdc, ctypes.byref(layer), gdi32.GetStockObject(BLACK_BRUSH)):
            raise ctypes.WinError()
    finally:
        user32.ReleaseDC(hwnd, hdc)


def cursor_pos() -> Tuple[int, int]:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return point.x, point.y


def default_anchor(scale: float) -> Tuple[int, int]:
    """主屏工作区右下角，返回桌宠站立点（窗口底边中点，物理像素）。"""
    work_area = wintypes.RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work_area), 0):
        raise ctypes.WinError()
    return work_area.right - round((PAGE_WIDTH / 2 + EDGE_MARGIN) * scale), work_area.bottom


def saved_anchor(state_path: Path) -> Optional[Tuple[int, int]]:
    """读取上次拖到的位置；显示器布局变化后已不在任何屏幕上的位置作废。"""
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text("utf-8"))
    anchor = (state["anchor_x"], state["anchor_y"])
    if not user32.MonitorFromPoint(wintypes.POINT(anchor[0], anchor[1] - 40), MONITOR_DEFAULTTONULL):
        return None
    return anchor


class Outbox:
    """独占进程原 stdout 作为协议通道。"""

    def __init__(self) -> None:
        # Python 与原生库的其他输出一律改走 stderr，避免混入 JSON 行。
        protocol_fd = os.dup(1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        self._stream = open(protocol_fd, "w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def emit(self, event: Dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


class PetApi:
    """暴露给页面的 js_api；下划线开头的成员不会暴露。"""

    def __init__(self, outbox: Outbox, todos: "TodoStore", state_path: Path, request_dir: Path) -> None:
        self._outbox = outbox
        self._todos = todos
        self._state_path = state_path
        self._request_dir = request_dir
        request_dir.mkdir(parents=True, exist_ok=True)
        self._window: Optional[webview.Window] = None
        self._hwnd = 0
        self._size = (0, 0)
        self._placed_size = (0, 0)
        self._anchor: Optional[Tuple[int, int]] = saved_anchor(state_path)
        self._placed_by_user = self._anchor is not None
        self._drag_start: Optional[Tuple[int, int, int, int]] = None
        self._closing = False

    def _attach(self, window: webview.Window) -> None:
        """在窗口首次显示前（GUI 线程）调整样式与位置。"""
        # pythonnet 由 pywebview 的 WinForms 后端加载，只能在窗口创建后导入。
        from System.Drawing import Color

        self._window = window
        self._hwnd = window.native.Handle.ToInt64()
        # 工具窗口不出现在任务栏和 Alt+Tab 中。
        style = user32.GetWindowLongPtrW(self._hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(self._hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        # pywebview 的透明只在窗体从未绘制背景时成立，一旦改尺寸就会露出灰底。
        # 把整个客户区交给 DWM 按 alpha 合成，黑色背景即全透明，改尺寸后依旧透明。
        window.native.BackColor = Color.Black
        result = dwmapi.DwmExtendFrameIntoClientArea(self._hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))
        if result != 0:
            raise OSError(f"DwmExtendFrameIntoClientArea 失败: 0x{result & 0xFFFFFFFF:08X}")
        # 页面缩放要等页面加载后才知道，先按系统 DPI 摆放，就绪后由 fit() 校正。
        scale = user32.GetDpiForWindow(self._hwnd) / 96
        self.fit(scale)
        apply_region(self._hwnd, [INITIAL_HIT_RECT], scale)

    def _place(self) -> None:
        width, height = self._size
        anchor_x, anchor_y = self._anchor
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        if self._size == self._placed_size:
            flags |= SWP_NOSIZE
        else:
            # 带着窗口区域改尺寸会在区域内残留白底，先撤掉区域，由页面在 fit() 之后重新上报。
            user32.SetWindowRgn(self._hwnd, None, True)
        if not user32.SetWindowPos(self._hwnd, None, anchor_x - width // 2, anchor_y - height, width, height, flags):
            raise ctypes.WinError()
        self._placed_size = self._size

    def _allow_close(self) -> bool:
        # 桌宠随主程序启停，Alt+F4 等用户关闭操作一律忽略。
        return self._closing

    def _shutdown(self) -> None:
        self._closing = True
        self._window.destroy()

    def fit(self, scale: float) -> None:
        """按页面实际缩放调整窗口尺寸，保持桌宠站立点不动。"""
        self._size = (round(PAGE_WIDTH * scale), round(PAGE_HEIGHT * scale))
        if not self._placed_by_user:
            self._anchor = default_anchor(scale)
        self._place()

    def ready(self) -> None:
        self._outbox.emit({"type": "ready"})

    def send(self, request_id: str, text: str, images: List[Dict[str, str]]) -> None:
        from attachments import MAX_REQUEST_BYTES
        import re

        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("无效请求 ID")
        payload = json.dumps({"text": text, "images": images}).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("图文消息过大")
        path = self._request_dir / f"{request_id}.json"
        path.write_bytes(payload)
        self._outbox.emit({"type": "chat", "request_id": request_id})

    def todos(self) -> List[Dict[str, Any]]:
        return self._todos.list()

    def add_todo(self, text: str) -> Dict[str, Any]:
        return self._todos.add(text)

    def done_todo(self, todo_id: str) -> None:
        self._todos.done(todo_id)

    def drag_begin(self) -> None:
        self._drag_start = (*cursor_pos(), *self._anchor)

    def drag_move(self) -> None:
        start_x, start_y, anchor_x, anchor_y = self._drag_start
        x, y = cursor_pos()
        self._anchor = (anchor_x + x - start_x, anchor_y + y - start_y)
        self._place()

    def drag_end(self) -> None:
        self._drag_start = None
        self._placed_by_user = True
        anchor_x, anchor_y = self._anchor
        self._state_path.write_text(json.dumps({"anchor_x": anchor_x, "anchor_y": anchor_y}), "utf-8")

    def region(self, rects: List[List[float]], scale: float) -> None:
        apply_region(self._hwnd, rects, scale)
        # 页面每次上报区域（含就绪、fit 改尺寸之后）都清一次 GDI 层，启动时残留的白底首帧不会留到可见区域里。
        clear_gdi_layer(self._hwnd)


def pump_commands(api: PetApi, window: webview.Window, commands: TextIO) -> None:
    """执行主程序指令并回执；管道关闭即主程序已停止，桌宠随之退出。"""
    try:
        for line in commands:
            if not line.strip():
                continue
            command = json.loads(line)
            ack = {"type": "ack", "id": command["id"]}
            try:
                if command["type"] == "show":
                    window.show()
                    window.evaluate_js("window.petSkin.visibility(true)")
                elif command["type"] == "hide":
                    window.hide()
                    window.evaluate_js("window.petSkin.visibility(false)")
                elif command["type"] == "skin":
                    completed = threading.Event()
                    results = []
                    def loaded(result, results=results, completed=completed):
                        results.append(result)
                        completed.set()
                    window.evaluate_js(
                        f"window.petSkin.install({json.dumps(command['skin'])}).catch(e => ({{error: String(e.message || e)}}))",
                        callback=loaded,
                    )
                    if not completed.wait(10):
                        window.evaluate_js("window.petSkin.cancelInstall()")
                        raise TimeoutError("皮肤资源解码超时")
                    if results[0].get("error"):
                        raise ValueError(results[0]["error"])
                    ack["result"] = results[0]
                elif command["type"] == "animation":
                    ack["result"] = window.evaluate_js(
                        f"window.petSkin.command({json.dumps(command['action'])}, {json.dumps(command['args'])}, {json.dumps(command['owner'])})"
                    )
                elif command["type"] == "todo":
                    action = command["action"]
                    if action == "list":
                        result = {"items": api._todos.list()}
                    elif action == "add":
                        result = {"item": api._todos.add(command["text"])}
                    elif action == "update":
                        result = {"item": api._todos.update(command["todo_id"], command["text"])}
                    elif action == "delete":
                        api._todos.done(command["todo_id"])
                        result = {"deleted": command["todo_id"]}
                    else:
                        raise ValueError("未知待办操作")
                    ack["result"] = result
                    if action != "list":
                        try:
                            window.evaluate_js("window.pet.refreshTodos()")
                        except Exception as exc:
                            # 数据已保存；界面刷新失败不能误报为写入失败并诱发重复新增。
                            result["display_error"] = str(exc)
                elif command["type"] in {"reply", "notice", "thinking", "idle", "failed", "accepted"}:
                    window.evaluate_js(f"window.pet.receive({json.dumps(command)})")
                else:
                    raise ValueError(f"未知的桌宠指令: {command['type']}")
            except Exception as exc:
                ack["error"] = str(exc)
            api._outbox.emit(ack)
    finally:
        api._shutdown()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("桌宠窗口目前仅支持 Windows")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()

    from todos import TodoStore

    _bind_win32()
    # 显式声明 DPI 感知，窗口位置、光标与窗口区域统一使用物理像素。
    user32.SetProcessDPIAware()
    outbox = Outbox()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    api = PetApi(outbox, TodoStore(args.data_dir / "todos.json"), args.data_dir / "window.json", args.runtime_dir / "requests")
    window = webview.create_window(
        "MaiBot Desktop Pet",
        url=PAGE.as_uri(),
        js_api=api,
        width=PAGE_WIDTH,
        height=PAGE_HEIGHT,
        resizable=False,
        frameless=True,
        easy_drag=False,
        transparent=True,
        on_top=True,
        shadow=False,
    )
    window.events.before_show += api._attach
    window.events.closing += api._allow_close
    commands = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    webview.start(
        pump_commands,
        (api, window, commands),
        private_mode=True,
        storage_path=str(args.runtime_dir / "webview"),
    )


if __name__ == "__main__":
    main()
