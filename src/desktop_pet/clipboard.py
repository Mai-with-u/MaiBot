"""Windows 原生 CF_DIB 图片剪贴板，无需浏览器权限。"""

from ctypes import wintypes
from io import BytesIO
from PIL import Image
import ctypes


def copy_image(image: Image.Image, hwnd: int) -> None:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, "BMP")
    dib = buffer.getvalue()[14:]
    _copy_data(dib, 8, hwnd)


def copy_text(text: str, hwnd: int) -> None:
    _copy_data((text + "\0").encode("utf-16-le"), 13, hwnd)


def _copy_data(dib: bytes, format_id: int, hwnd: int) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    handle = kernel.GlobalAlloc(0x0002, len(dib))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, dib, len(dib))
        finally:
            kernel.GlobalUnlock(handle)
        if not user.OpenClipboard(hwnd):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not user.EmptyClipboard() or not user.SetClipboardData(format_id, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None  # 成功后内存归剪贴板所有，不能提前释放。
        finally:
            user.CloseClipboard()
    finally:
        if handle:
            kernel.GlobalFree(handle)
