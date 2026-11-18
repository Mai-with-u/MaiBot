"""Windows Job Object：宿主句柄关闭时终止该 Bot 的整个进程树。"""

from ctypes import wintypes
from typing import Dict, List, Optional

import ctypes
import time


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsJob:
    def __init__(self) -> None:
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self._kernel.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ]
        self._kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel.TerminateJobObject.restype = wintypes.BOOL
        self._kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel.OpenProcess.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self._kernel.IsProcessInJob.restype = wintypes.BOOL
        self._kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._pending_processes: Dict[int, int] = {}
        self._handle: Optional[int] = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid: int) -> None:
        # 子进程仍在启动门闩等待，绑定 Job 成功之前不会运行 Bot 或派生其他进程。
        process = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel.CloseHandle(process)

    def _retain_processes(self) -> None:
        # 保留属于本 Job 的实际进程句柄，防止 PID 复用，并等待端口与文件释放。
        for pid in self._process_ids():
            if pid in self._pending_processes:
                continue
            process = self._kernel.OpenProcess(0x100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
            if not process:
                error = ctypes.get_last_error()
                if error == 87:
                    continue  # 查询 Job 后，进程已经退出。
                raise ctypes.WinError(error)
            in_job = wintypes.BOOL()
            if not self._kernel.IsProcessInJob(process, self._handle, ctypes.byref(in_job)):
                error = ctypes.WinError(ctypes.get_last_error())
                self._kernel.CloseHandle(process)
                raise error
            if in_job.value:
                self._pending_processes[pid] = process
            else:
                self._kernel.CloseHandle(process)

    def live_processes(self) -> Dict[int, str]:
        """用实际句柄核实存活状态，避免把滞后的 Job 计数当成残留进程。"""
        self._retain_processes()
        live: Dict[int, str] = {}
        for pid, process in self._pending_processes.items():
            result = self._kernel.WaitForSingleObject(process, 0)
            if result == 0:
                continue
            if result != 258:  # WAIT_TIMEOUT：进程尚未退出。
                raise ctypes.WinError(ctypes.get_last_error())
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            if not self._kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                error = ctypes.get_last_error()
                if self._kernel.WaitForSingleObject(process, 0) == 0:
                    continue  # 核实状态与读取路径之间正常退出。
                raise ctypes.WinError(error)
            live[pid] = path.value
        return live

    def terminate(self) -> None:
        self._retain_processes()
        if not self._kernel.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def _process_ids(self) -> List[int]:
        capacity = 16
        while True:
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if self._kernel.QueryInformationJobObject(self._handle, 3, buffer, ctypes.sizeof(buffer), None):
                count = wintypes.DWORD.from_buffer(buffer, 4).value
                return list((ctypes.c_size_t * count).from_buffer(buffer, 8))
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, wintypes.DWORD.from_buffer(buffer).value)

    def wait_terminated(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for process in self._pending_processes.values():
            result = self._kernel.WaitForSingleObject(process, max(0, int((deadline - time.monotonic()) * 1000)))
            if result == 258:  # WAIT_TIMEOUT
                raise TimeoutError("Bot 子进程终止后仍未释放运行资源")
            if result != 0:
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle is not None:
            handle = self._handle
            self._handle = None
            if not self._kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        for process in self._pending_processes.values():
            self._kernel.CloseHandle(process)
        self._pending_processes.clear()
