from __future__ import annotations

import asyncio
import gc
import os
import sys
import threading
import tracemalloc
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProcessMemorySnapshot:
    process_id: int
    rss_bytes: int
    private_bytes: int | None
    total_physical_bytes: int | None
    available_physical_bytes: int | None

    @property
    def rss_mb(self) -> float:
        return round(self.rss_bytes / (1024 * 1024), 3)

    @property
    def private_mb(self) -> float | None:
        if self.private_bytes is None:
            return None
        return round(self.private_bytes / (1024 * 1024), 3)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rss_mb": self.rss_mb, "private_mb": self.private_mb}


class WorkerMemoryCritical(RuntimeError):
    pass


def process_memory_snapshot(process_id: int | None = None) -> ProcessMemorySnapshot:
    pid = int(process_id or os.getpid())
    if sys.platform == "win32":
        return _windows_snapshot(pid)
    return _proc_snapshot(pid)


def start_memory_tracing(*, frames: int = 15) -> None:
    if not tracemalloc.is_tracing():
        tracemalloc.start(frames)


def runtime_memory_diagnostics(
    session: Any | None = None,
    *,
    top_allocation_count: int = 10,
) -> dict[str, Any]:
    snapshot = process_memory_snapshot()
    try:
        outstanding_tasks = len([task for task in asyncio.all_tasks() if not task.done()])
    except RuntimeError:
        outstanding_tasks = 0
    identity_map = getattr(session, "identity_map", None)
    relevant_types = {
        "IBFetchItem",
        "IBFetchRun",
        "DataFrame",
        "Task",
        "Future",
    }
    domain_object_counts: dict[str, int] = {}
    for value in gc.get_objects():
        name = type(value).__name__
        if name in relevant_types:
            domain_object_counts[name] = domain_object_counts.get(name, 0) + 1
    diagnostics = {
        **snapshot.as_dict(),
        "identity_map_size": len(identity_map) if identity_map is not None else None,
        "gc_generation_counts": list(gc.get_count()),
        "outstanding_async_tasks": outstanding_tasks,
        "thread_count": threading.active_count(),
        "domain_object_counts": domain_object_counts,
    }
    if tracemalloc.is_tracing():
        snapshot_stats = tracemalloc.take_snapshot().statistics("lineno")
        diagnostics["python_heap_current_bytes"] = tracemalloc.get_traced_memory()[0]
        diagnostics["python_heap_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        diagnostics["top_allocations"] = [
            {"location": str(stat.traceback[0]), "bytes": stat.size, "count": stat.count}
            for stat in snapshot_stats[:top_allocation_count]
        ]
    return diagnostics


def memory_status(snapshot: ProcessMemorySnapshot, warning_mb: int, critical_mb: int) -> str:
    measured = snapshot.private_bytes if snapshot.private_bytes is not None else snapshot.rss_bytes
    measured_mb = measured / (1024 * 1024)
    if measured_mb >= critical_mb:
        return "CRITICAL"
    if measured_mb >= warning_mb:
        return "WARNING"
    return "OK"


def _windows_snapshot(pid: int) -> ProcessMemorySnapshot:
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    process = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not process:
        raise OSError(f"Unable to inspect process {pid}")
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        if not ok:
            raise OSError(f"Unable to read process memory for {pid}")
    finally:
        ctypes.windll.kernel32.CloseHandle(process)
    memory = MEMORYSTATUSEX()
    memory.dwLength = ctypes.sizeof(memory)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    return ProcessMemorySnapshot(
        process_id=pid,
        rss_bytes=int(counters.WorkingSetSize),
        private_bytes=int(counters.PrivateUsage),
        total_physical_bytes=int(memory.ullTotalPhys),
        available_physical_bytes=int(memory.ullAvailPhys),
    )


def _proc_snapshot(pid: int) -> ProcessMemorySnapshot:
    rss = 0
    private: int | None = None
    status_path = f"/proc/{pid}/status"
    try:
        values: dict[str, int] = {}
        with open(status_path, encoding="utf-8") as stream:
            for line in stream:
                name, _, raw = line.partition(":")
                if name in {"VmRSS", "RssAnon"}:
                    values[name] = int(raw.strip().split()[0]) * 1024
        rss = values.get("VmRSS", 0)
        private = values.get("RssAnon")
    except OSError:
        import resource

        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    total = available = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as stream:
            memory = {
                name: int(raw.strip().split()[0]) * 1024
                for line in stream
                for name, _, raw in [line.partition(":")]
                if name in {"MemTotal", "MemAvailable"}
            }
        total = memory.get("MemTotal")
        available = memory.get("MemAvailable")
    except OSError:
        pass
    return ProcessMemorySnapshot(pid, rss, private, total, available)
