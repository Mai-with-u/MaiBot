"""在 Windows Job Object 内启动 MaiBot，并限制、观测整个 MaiBot 进程树的内存。"""

from __future__ import annotations

from ctypes import (
    POINTER,
    Structure,
    WinDLL,
    WinError,
    addressof,
    byref,
    c_int,
    c_longlong,
    c_size_t,
    c_ulong,
    c_ulonglong,
    c_void_p,
    cast,
    create_string_buffer,
    create_unicode_buffer,
    get_last_error,
    sizeof,
)
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any, Dict, List, Optional, Set, TextIO, Tuple

import argparse
import json
import os
import runpy
import sys


ERROR_MORE_DATA = 234
JOB_OBJECT_BASIC_PROCESS_ID_LIST_CLASS = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010


class JobObjectBasicLimitInformation(Structure):
    """Win32 ``JOBOBJECT_BASIC_LIMIT_INFORMATION``。"""

    _fields_ = [
        ("PerProcessUserTimeLimit", c_longlong),
        ("PerJobUserTimeLimit", c_longlong),
        ("LimitFlags", c_ulong),
        ("MinimumWorkingSetSize", c_size_t),
        ("MaximumWorkingSetSize", c_size_t),
        ("ActiveProcessLimit", c_ulong),
        ("Affinity", c_size_t),
        ("PriorityClass", c_ulong),
        ("SchedulingClass", c_ulong),
    ]


class IoCounters(Structure):
    """Win32 ``IO_COUNTERS``。"""

    _fields_ = [
        ("ReadOperationCount", c_ulonglong),
        ("WriteOperationCount", c_ulonglong),
        ("OtherOperationCount", c_ulonglong),
        ("ReadTransferCount", c_ulonglong),
        ("WriteTransferCount", c_ulonglong),
        ("OtherTransferCount", c_ulonglong),
    ]


class JobObjectExtendedLimitInformation(Structure):
    """Win32 ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``。"""

    _fields_ = [
        ("BasicLimitInformation", JobObjectBasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", c_size_t),
        ("JobMemoryLimit", c_size_t),
        ("PeakProcessMemoryUsed", c_size_t),
        ("PeakJobMemoryUsed", c_size_t),
    ]


class JobObjectBasicProcessIdListHeader(Structure):
    """Win32 ``JOBOBJECT_BASIC_PROCESS_ID_LIST`` 的定长头部。"""

    _fields_ = [
        ("NumberOfAssignedProcesses", c_ulong),
        ("NumberOfProcessIdsInList", c_ulong),
    ]


class ProcessMemoryCountersEx(Structure):
    """Win32 ``PROCESS_MEMORY_COUNTERS_EX``。"""

    _fields_ = [
        ("cb", c_ulong),
        ("PageFaultCount", c_ulong),
        ("PeakWorkingSetSize", c_size_t),
        ("WorkingSetSize", c_size_t),
        ("QuotaPeakPagedPoolUsage", c_size_t),
        ("QuotaPagedPoolUsage", c_size_t),
        ("QuotaPeakNonPagedPoolUsage", c_size_t),
        ("QuotaNonPagedPoolUsage", c_size_t),
        ("PagefileUsage", c_size_t),
        ("PeakPagefileUsage", c_size_t),
        ("PrivateUsage", c_size_t),
    ]


kernel32 = WinDLL("kernel32", use_last_error=True)
psapi = WinDLL("psapi", use_last_error=True)

kernel32.CreateJobObjectW.argtypes = [c_void_p, c_void_p]
kernel32.CreateJobObjectW.restype = c_void_p

kernel32.SetInformationJobObject.argtypes = [c_void_p, c_int, c_void_p, c_ulong]
kernel32.SetInformationJobObject.restype = c_int

kernel32.QueryInformationJobObject.argtypes = [c_void_p, c_int, c_void_p, c_ulong, c_void_p]
kernel32.QueryInformationJobObject.restype = c_int

kernel32.AssignProcessToJobObject.argtypes = [c_void_p, c_void_p]
kernel32.AssignProcessToJobObject.restype = c_int

kernel32.GetCurrentProcess.restype = c_void_p

kernel32.OpenProcess.argtypes = [c_ulong, c_int, c_ulong]
kernel32.OpenProcess.restype = c_void_p

kernel32.QueryFullProcessImageNameW.argtypes = [c_void_p, c_ulong, c_void_p, POINTER(c_ulong)]
kernel32.QueryFullProcessImageNameW.restype = c_int

kernel32.CloseHandle.argtypes = [c_void_p]
kernel32.CloseHandle.restype = c_int

psapi.GetProcessMemoryInfo.argtypes = [c_void_p, POINTER(ProcessMemoryCountersEx), c_ulong]
psapi.GetProcessMemoryInfo.restype = c_int


def bytes_to_mib(value: int) -> float:
    """把字节数转换成便于写入探针日志的 MiB。"""

    return round(value / 1024 / 1024, 2)


def create_memory_limited_job(memory_limit_bytes: int) -> int:
    """创建 Job Object，并把当前进程及其后续子进程纳入总内存限制。"""

    job_handle = kernel32.CreateJobObjectW(None, None)
    if not job_handle:
        raise WinError(get_last_error())

    limits = JobObjectExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    limits.JobMemoryLimit = memory_limit_bytes

    if not kernel32.SetInformationJobObject(
        job_handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        byref(limits),
        sizeof(limits),
    ):
        raise WinError(get_last_error())

    if not kernel32.AssignProcessToJobObject(job_handle, kernel32.GetCurrentProcess()):
        raise WinError(get_last_error())

    return int(job_handle)


def query_job_process_ids(job_handle: int) -> List[int]:
    """读取当前属于 Job Object 的全部进程 ID。"""

    capacity = 16
    header_size = sizeof(JobObjectBasicProcessIdListHeader)
    for _ in range(8):
        buffer_size = header_size + capacity * sizeof(c_size_t)
        buffer = create_string_buffer(buffer_size)
        succeeded = kernel32.QueryInformationJobObject(
            job_handle,
            JOB_OBJECT_BASIC_PROCESS_ID_LIST_CLASS,
            buffer,
            buffer_size,
            None,
        )
        header = cast(buffer, POINTER(JobObjectBasicProcessIdListHeader)).contents
        if succeeded:
            process_id_array_type = c_size_t * header.NumberOfProcessIdsInList
            process_ids = process_id_array_type.from_address(addressof(buffer) + header_size)
            return [int(process_id) for process_id in process_ids]

        error_code = get_last_error()
        if error_code != ERROR_MORE_DATA:
            raise WinError(error_code)
        capacity = max(capacity * 2, int(header.NumberOfAssignedProcesses))

    raise RuntimeError("Job Object 进程数量持续变化，无法取得稳定快照")


def query_job_peak_memory(job_handle: int) -> Tuple[int, int]:
    """读取 Job Object 和其中单进程的历史提交内存峰值。"""

    information = JobObjectExtendedLimitInformation()
    if not kernel32.QueryInformationJobObject(
        job_handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        byref(information),
        sizeof(information),
        None,
    ):
        raise WinError(get_last_error())
    return int(information.PeakJobMemoryUsed), int(information.PeakProcessMemoryUsed)


def query_process_memory(process_id: int) -> Optional[Dict[str, Any]]:
    """读取单进程的 Private Bytes、工作集和可执行文件路径。"""

    access = PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    process_handle = kernel32.OpenProcess(access, False, process_id)
    if not process_handle:
        # 进程可能恰好在取得 Job PID 列表后退出，这种竞态不应中断探针。
        return None

    try:
        counters = ProcessMemoryCountersEx()
        counters.cb = sizeof(counters)
        if not psapi.GetProcessMemoryInfo(process_handle, byref(counters), sizeof(counters)):
            return None

        image_buffer = create_unicode_buffer(32768)
        image_length = c_ulong(len(image_buffer))
        image_path = ""
        if kernel32.QueryFullProcessImageNameW(
            process_handle,
            0,
            image_buffer,
            byref(image_length),
        ):
            image_path = image_buffer.value

        return {
            "pid": process_id,
            "private_bytes": int(counters.PrivateUsage),
            "working_set_bytes": int(counters.WorkingSetSize),
            "image": image_path,
        }
    finally:
        kernel32.CloseHandle(process_handle)


class MemoryProbe:
    """低开销采样 Job 内存，并将关键变化独立写入 JSONL。"""

    def __init__(
        self,
        job_handle: int,
        memory_limit_bytes: int,
        log_path: Path,
        interval_seconds: float,
        heartbeat_seconds: float,
        spike_bytes: int,
    ) -> None:
        self.job_handle = job_handle
        self.memory_limit_bytes = memory_limit_bytes
        self.log_path = log_path
        self.interval_seconds = interval_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.spike_bytes = spike_bytes
        self.core_process_id = os.getpid()
        self._stop_event = Event()
        self._thread = Thread(target=self._run, name="MaiBotMemoryProbe", daemon=True)

    def start(self) -> None:
        """启动后台采样线程。"""

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread.start()

    def stop(self) -> None:
        """停止后台采样线程，并等待最后一行日志刷出。"""

        self._stop_event.set()
        self._thread.join(timeout=max(5.0, self.interval_seconds * 2))

    @staticmethod
    def _write_record(log_file: TextIO, record: Dict[str, Any]) -> None:
        log_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        log_file.flush()

    def _collect_snapshot(self, previous_private_by_pid: Dict[int, int]) -> Dict[str, Any]:
        process_ids = query_job_process_ids(self.job_handle)
        job_peak_bytes, process_peak_bytes = query_job_peak_memory(self.job_handle)
        processes: List[Dict[str, Any]] = []
        unreadable_process_ids: List[int] = []
        total_private_bytes = 0
        total_working_set_bytes = 0

        for process_id in process_ids:
            process = query_process_memory(process_id)
            if process is None:
                unreadable_process_ids.append(process_id)
                continue

            private_bytes = process["private_bytes"]
            working_set_bytes = process["working_set_bytes"]
            private_delta_bytes = private_bytes - previous_private_by_pid.get(process_id, 0)
            total_private_bytes += private_bytes
            total_working_set_bytes += working_set_bytes
            processes.append(
                {
                    "pid": process_id,
                    "role": "maibot_core" if process_id == self.core_process_id else "maibot_child",
                    "image": Path(process["image"]).name if process["image"] else "",
                    "private_mib": bytes_to_mib(private_bytes),
                    "private_delta_mib": bytes_to_mib(private_delta_bytes),
                    "working_set_mib": bytes_to_mib(working_set_bytes),
                    "_private_bytes": private_bytes,
                }
            )

        processes.sort(key=lambda item: item["_private_bytes"], reverse=True)
        current_private_by_pid = {
            process["pid"]: process["_private_bytes"] for process in processes
        }
        for process in processes:
            del process["_private_bytes"]

        return {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "summed_private_bytes": total_private_bytes,
            "summed_working_set_bytes": total_working_set_bytes,
            "job_peak_bytes": job_peak_bytes,
            "process_peak_bytes": process_peak_bytes,
            "processes": processes,
            "process_ids": set(process_ids),
            "unreadable_process_ids": unreadable_process_ids,
            "current_private_by_pid": current_private_by_pid,
        }

    def _format_record(
        self,
        snapshot: Dict[str, Any],
        reasons: List[str],
        private_delta_bytes: int,
        peak_delta_bytes: int,
    ) -> Dict[str, Any]:
        usage_ratio = snapshot["summed_private_bytes"] / self.memory_limit_bytes
        return {
            "timestamp": snapshot["timestamp"],
            "event": "memory_probe_sample",
            "reasons": reasons,
            "limit_mib": bytes_to_mib(self.memory_limit_bytes),
            "estimated_limit_usage_percent": round(usage_ratio * 100, 2),
            "summed_private_mib": bytes_to_mib(snapshot["summed_private_bytes"]),
            "summed_private_delta_mib": bytes_to_mib(private_delta_bytes),
            "summed_working_set_mib": bytes_to_mib(snapshot["summed_working_set_bytes"]),
            "job_peak_commit_mib": bytes_to_mib(snapshot["job_peak_bytes"]),
            "job_peak_delta_mib": bytes_to_mib(peak_delta_bytes),
            "largest_process_peak_commit_mib": bytes_to_mib(snapshot["process_peak_bytes"]),
            "active_process_ids": sorted(snapshot["process_ids"]),
            "new_process_ids": snapshot["new_process_ids"],
            "exited_process_ids": snapshot["exited_process_ids"],
            "unreadable_process_ids": snapshot["unreadable_process_ids"],
            "processes": snapshot["processes"],
        }

    def _run(self) -> None:
        previous_total_private_bytes = 0
        previous_job_peak_bytes = 0
        previous_private_by_pid: Dict[int, int] = {}
        previous_process_ids: Set[int] = set()
        previous_pressure_level = "normal"
        last_write_time = 0.0
        last_error_write_time = 0.0
        first_sample = True

        with self.log_path.open("a", encoding="utf-8", buffering=1) as log_file:
            while not self._stop_event.is_set():
                sample_started = monotonic()
                try:
                    snapshot = self._collect_snapshot(previous_private_by_pid)
                    total_private_bytes = snapshot["summed_private_bytes"]
                    job_peak_bytes = snapshot["job_peak_bytes"]
                    process_ids = snapshot["process_ids"]
                    snapshot["new_process_ids"] = sorted(process_ids - previous_process_ids)
                    snapshot["exited_process_ids"] = sorted(previous_process_ids - process_ids)
                    private_delta_bytes = (
                        0 if first_sample else total_private_bytes - previous_total_private_bytes
                    )
                    peak_delta_bytes = 0 if first_sample else job_peak_bytes - previous_job_peak_bytes
                    usage_ratio = total_private_bytes / self.memory_limit_bytes
                    pressure_level = (
                        "critical" if usage_ratio >= 0.90 else "warning" if usage_ratio >= 0.75 else "normal"
                    )
                    reasons: List[str] = []

                    if first_sample:
                        reasons.append("probe_started")
                    if not first_sample and process_ids != previous_process_ids:
                        reasons.append("process_tree_changed")
                    if private_delta_bytes >= self.spike_bytes:
                        reasons.append("private_memory_spike")
                    elif private_delta_bytes <= -self.spike_bytes:
                        reasons.append("private_memory_release")
                    if peak_delta_bytes >= self.spike_bytes:
                        reasons.append("job_peak_jump")
                    if pressure_level != previous_pressure_level:
                        reasons.append(f"pressure_{pressure_level}")
                    if pressure_level == "critical":
                        reasons.append("critical_sample")
                    if sample_started - last_write_time >= self.heartbeat_seconds:
                        reasons.append("heartbeat")

                    if reasons:
                        record = self._format_record(
                            snapshot,
                            reasons,
                            private_delta_bytes,
                            peak_delta_bytes,
                        )
                        self._write_record(log_file, record)
                        last_write_time = sample_started

                    previous_total_private_bytes = total_private_bytes
                    previous_job_peak_bytes = job_peak_bytes
                    previous_private_by_pid = snapshot["current_private_by_pid"]
                    previous_process_ids = process_ids
                    previous_pressure_level = pressure_level
                    first_sample = False
                except Exception as error:
                    # 探针异常单独落盘，但按心跳频率节流，避免诊断代码形成日志风暴。
                    if sample_started - last_error_write_time >= self.heartbeat_seconds:
                        self._write_record(
                            log_file,
                            {
                                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                                "event": "memory_probe_error",
                                "error_type": type(error).__name__,
                                "error": str(error),
                            },
                        )
                        last_error_write_time = sample_started

                elapsed_seconds = monotonic() - sample_started
                self._stop_event.wait(max(0.0, self.interval_seconds - elapsed_seconds))

            self._write_record(
                log_file,
                {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "event": "memory_probe_stopped",
                },
            )


def parse_args() -> Tuple[argparse.Namespace, List[str]]:
    """解析限制和探针参数，并保留需要继续传给 ``bot.py`` 的参数。"""

    parser = argparse.ArgumentParser(description="在受限 Windows Job Object 中启动 MaiBot")
    parser.add_argument("--memory-mib", type=int, default=2048, help="MaiBot 进程树总提交内存上限")
    parser.add_argument("--check-only", action="store_true", help="只验证 Job Object，不启动 MaiBot")
    parser.add_argument(
        "--disable-memory-probe",
        action="store_true",
        help="关闭独立内存探针日志",
    )
    parser.add_argument(
        "--probe-interval-seconds",
        type=float,
        default=1.0,
        help="内存探针采样间隔，默认 1 秒",
    )
    parser.add_argument(
        "--probe-heartbeat-seconds",
        type=float,
        default=10.0,
        help="没有明显变化时的日志间隔，默认 10 秒",
    )
    parser.add_argument(
        "--probe-spike-mib",
        type=int,
        default=32,
        help="立即记录的单次内存变化阈值，默认 32 MiB",
    )
    return parser.parse_known_args()


def main() -> None:
    args, bot_args = parse_args()
    if args.memory_mib <= 0:
        raise ValueError("内存上限必须大于 0 MiB")
    if args.probe_interval_seconds <= 0:
        raise ValueError("探针采样间隔必须大于 0 秒")
    if args.probe_heartbeat_seconds <= 0:
        raise ValueError("探针心跳间隔必须大于 0 秒")
    if args.probe_spike_mib <= 0:
        raise ValueError("探针突增阈值必须大于 0 MiB")

    memory_limit_bytes = args.memory_mib * 1024 * 1024

    # 保持句柄存活，确保 bot.py 拉起的 Worker 和插件 Runner 都继承同一个 Job。
    job_handle = create_memory_limited_job(memory_limit_bytes)
    if not job_handle:
        raise RuntimeError("Windows Job Object 创建失败")

    print(f"MaiBot 进程树内存上限：{args.memory_mib} MiB", flush=True)
    workspace_root = Path(__file__).resolve().parents[1]
    bot_path = workspace_root / "bot.py"
    if not bot_path.is_file():
        raise FileNotFoundError(bot_path)

    os.chdir(workspace_root)
    workspace_root_text = str(workspace_root)
    if workspace_root_text not in sys.path:
        sys.path.insert(0, workspace_root_text)

    if args.check_only:
        __import__("src")
        print("Windows Job Object 与项目导入路径自检通过。", flush=True)
        return

    memory_probe: Optional[MemoryProbe] = None
    if not args.disable_memory_probe:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        probe_log_path = workspace_root / "logs" / f"memory_probe_{timestamp}_{os.getpid()}.jsonl"
        memory_probe = MemoryProbe(
            job_handle=job_handle,
            memory_limit_bytes=memory_limit_bytes,
            log_path=probe_log_path,
            interval_seconds=args.probe_interval_seconds,
            heartbeat_seconds=args.probe_heartbeat_seconds,
            spike_bytes=args.probe_spike_mib * 1024 * 1024,
        )
        memory_probe.start()
        print(f"独立内存探针日志：{probe_log_path}", flush=True)

    sys.argv = [str(bot_path), *bot_args]
    try:
        runpy.run_path(str(bot_path), run_name="__main__")
    finally:
        if memory_probe is not None:
            memory_probe.stop()


if __name__ == "__main__":
    main()
