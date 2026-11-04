import ctypes
import sys

import pytest

from src.desktop_pet import pet_window


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 原生窗口绑定")
def test_pet_bindings_do_not_change_shared_pywebview_functions():
    shared = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea
    original_argtypes = shared.argtypes
    original_restype = shared.restype
    pet_window._bind_win32()
    assert pet_window.dwmapi.DwmExtendFrameIntoClientArea is not shared
    assert shared.argtypes == original_argtypes
    assert shared.restype == original_restype
    assert pet_window.dwmapi.DwmExtendFrameIntoClientArea.argtypes[1] == ctypes.POINTER(pet_window.MARGINS)
