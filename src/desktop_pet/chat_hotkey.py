"""本机桌宠的 Windows 全局聊天快捷键。"""

from concurrent.futures import Future
from ctypes import wintypes
from typing import Callable, Optional, Tuple

import ctypes
import sys
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None


def parse_chat_hotkey(value: str) -> Tuple[int, int]:
    modifiers = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}
    *prefix, key = (part.strip().lower() for part in value.split("+"))
    if not prefix or len(set(prefix)) != len(prefix) or any(part not in modifiers for part in prefix):
        raise ValueError("请使用 Ctrl、Alt、Shift 或 Win 加一个主键，例如 Ctrl+Alt+M")
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key in {f"f{i}" for i in range(1, 25)}:
        vk = 0x70 + int(key[1:]) - 1
    elif key == "space":
        vk = 0x20
    else:
        raise ValueError("快捷键主键支持 A–Z、0–9、F1–F24 或 Space")
    return sum(modifiers[part] for part in prefix), vk


class ChatHotkey:
    """由独立消息线程接收 WM_HOTKEY；所有配置更改由窗口命令线程串行执行。"""

    def __init__(self, pressed: Callable[[], None]) -> None:
        self._pressed = pressed
        self._keys: Optional[Tuple[int, int]] = None
        self._thread: Optional[threading.Thread] = None

    def configure(self, value: str) -> None:
        keys = parse_chat_hotkey(value) if value else None
        if keys == self._keys:
            return
        thread = None
        if keys:
            registered: Future[None] = Future()
            thread = threading.Thread(target=self._listen, args=(*keys, registered), daemon=True)
            thread.start()
            try:
                registered.result()
            except OSError as exc:
                raise ValueError(f"无法注册聊天快捷键 {value}：{exc}") from exc
        if self._thread:
            if not user32.PostThreadMessageW(self._thread.native_id, 0x0012, 0, 0):  # WM_QUIT
                raise ctypes.WinError(ctypes.get_last_error())
            self._thread.join()
        self._keys, self._thread = keys, thread

    def _listen(self, modifiers: int, vk: int, registered: Future[None]) -> None:
        msg = wintypes.MSG()
        # 创建线程消息队列后才通知调用方，确保立即关闭时也能收到 WM_QUIT。
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        if not user32.RegisterHotKey(None, 1, modifiers | 0x4000, vk):  # MOD_NOREPEAT
            registered.set_exception(ctypes.WinError(ctypes.get_last_error()))
            return
        try:
            registered.set_result(None)
            while (status := user32.GetMessageW(ctypes.byref(msg), None, 0, 0)) > 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    self._pressed()
            if status == -1:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            user32.UnregisterHotKey(None, 1)
