from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta


def process_started_at(process_id: int | None = None) -> datetime:
    """Return the OS process creation time, falling back safely for the current process."""
    pid = int(process_id or os.getpid())
    try:
        return _windows_started_at(pid) if sys.platform == "win32" else _proc_started_at(pid)
    except Exception:
        if pid == os.getpid():
            return datetime.now(UTC)
        raise


def process_is_alive(
    process_id: int | None,
    expected_started_at: datetime | None = None,
) -> bool:
    """Check PID plus creation time. Inspection failures are contained as not-alive."""
    if process_id is None or int(process_id) <= 0:
        return False
    try:
        actual_started_at = process_started_at(int(process_id))
    except Exception:
        return False
    if expected_started_at is None:
        return True
    expected = _as_utc(expected_started_at)
    return abs(actual_started_at - expected) <= timedelta(seconds=2)


def _windows_started_at(pid: int) -> datetime:
    import ctypes
    from ctypes import wintypes

    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        raise OSError(f"Unable to inspect process {pid}")
    try:
        exit_code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            raise OSError(f"Unable to read process state for {pid}")
        if exit_code.value != 259:  # STILL_ACTIVE
            raise ProcessLookupError(pid)
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetProcessTimes(
            process,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise OSError(f"Unable to read process creation time for {pid}")
    finally:
        ctypes.windll.kernel32.CloseHandle(process)
    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks / 10)


def _proc_started_at(pid: int) -> datetime:
    stat = open(f"/proc/{pid}/stat", encoding="utf-8").read()
    start_ticks = int(stat[stat.rfind(")") + 2 :].split()[19])
    clock_ticks = int(os.sysconf("SC_CLK_TCK"))
    boot_seconds = None
    with open("/proc/stat", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("btime "):
                boot_seconds = int(line.split()[1])
                break
    if boot_seconds is None:
        raise OSError("Linux boot time is unavailable")
    return datetime.fromtimestamp(boot_seconds + start_ticks / clock_ticks, tz=UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
