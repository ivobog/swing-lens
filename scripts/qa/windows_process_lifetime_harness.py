from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

if os.name == "nt":
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
    JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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


    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _windows_containment() -> dict[str, object]:
    if os.name != "nt":
        return {"platform": os.name}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.GetConsoleProcessList.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    kernel32.GetConsoleProcessList.restype = wintypes.DWORD
    current_process = kernel32.GetCurrentProcess()
    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(current_process, None, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())

    console_pids = (wintypes.DWORD * 256)()
    console_count = kernel32.GetConsoleProcessList(console_pids, len(console_pids))
    report: dict[str, object] = {
        "inJob": bool(in_job.value),
        "consoleAttached": bool(console_count),
        "consoleProcessIds": list(console_pids[: min(console_count, len(console_pids))]),
    }
    if in_job.value:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        returned = wintypes.DWORD()
        ok = kernel32.QueryInformationJobObject(
            None,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
            ctypes.byref(returned),
        )
        if ok:
            flags = int(limits.BasicLimitInformation.LimitFlags)
            report.update(
                {
                    "jobLimitFlags": flags,
                    "jobKillOnClose": bool(flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
                    "jobBreakawayOk": bool(flags & JOB_OBJECT_LIMIT_BREAKAWAY_OK),
                    "jobSilentBreakawayOk": bool(
                        flags & JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
                    ),
                }
            )
        else:
            report["jobLimitQueryError"] = ctypes.get_last_error()
    return report


def _identity() -> dict[str, object]:
    return {
        "pid": os.getpid(),
        "parentPid": os.getppid(),
        "timestamp": _utc_now(),
        "containment": _windows_containment(),
    }


def _creation_flags(candidate: str) -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if candidate == "breakaway":
        flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
    elif candidate == "detached":
        flags |= subprocess.DETACHED_PROCESS
    elif candidate == "detached-breakaway":
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_BREAKAWAY_FROM_JOB
    elif candidate == "new-console":
        flags |= subprocess.CREATE_NEW_CONSOLE
    elif candidate == "new-console-breakaway":
        flags |= subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB
    elif candidate != "current":
        raise ValueError(f"unknown candidate: {candidate}")
    return flags


def _child(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    identity = _identity()
    _atomic_write(directory / "child.json", identity)
    sequence = 0
    while not (directory / "stop").exists():
        sequence += 1
        _atomic_write(
            directory / "heartbeat.json",
            {**identity, "heartbeatAt": _utc_now(), "sequence": sequence},
        )
        sleep(1)
    _atomic_write(
        directory / "stopped.json",
        {**identity, "stoppedAt": _utc_now(), "sequence": sequence},
    )


def _launch(directory: Path, candidate: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / "child-output.log"
    with output_path.open("ab", buffering=0) as output:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "child", str(directory)],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=_creation_flags(candidate),
        )
    deadline = monotonic() + 10
    while monotonic() < deadline and not (directory / "child.json").exists():
        if process.poll() is not None:
            raise RuntimeError(f"child exited during launch: {process.returncode}")
        sleep(0.05)
    if not (directory / "child.json").exists():
        raise TimeoutError("child did not publish its identity")
    report = {
        **_identity(),
        "candidate": candidate,
        "creationFlags": _creation_flags(candidate),
        "childPid": process.pid,
        "child": json.loads((directory / "child.json").read_text(encoding="utf-8")),
    }
    _atomic_write(directory / "launcher.json", report)
    print(json.dumps(report, sort_keys=True))


def _inspect(directory: Path) -> None:
    launcher = json.loads((directory / "launcher.json").read_text(encoding="utf-8"))
    heartbeat_path = directory / "heartbeat.json"
    heartbeat = (
        json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if heartbeat_path.exists()
        else None
    )
    pid = int(launcher["child"]["pid"])
    alive = False
    if os.name == "nt":
        process = ctypes.WinDLL("kernel32", use_last_error=True).OpenProcess(
            0x1000, False, pid
        )
        alive = bool(process)
        if process:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(process)
    else:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            pass
    print(
        json.dumps(
            {
                "inspector": _identity(),
                "candidate": launcher["candidate"],
                "launcherPid": launcher["childPid"],
                "childPid": pid,
                "childAlive": alive,
                "heartbeat": heartbeat,
            },
            sort_keys=True,
        )
    )


def _stop(directory: Path) -> None:
    (directory / "stop").touch()
    deadline = monotonic() + 10
    while monotonic() < deadline and not (directory / "stopped.json").exists():
        sleep(0.05)
    print(json.dumps({"stopped": (directory / "stopped.json").exists()}))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    launch = subparsers.add_parser("launch")
    launch.add_argument("directory", type=Path)
    launch.add_argument(
        "--candidate",
        choices=(
            "current",
            "breakaway",
            "detached",
            "detached-breakaway",
            "new-console",
            "new-console-breakaway",
        ),
        default="current",
    )
    child = subparsers.add_parser("child")
    child.add_argument("directory", type=Path)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("directory", type=Path)
    stop = subparsers.add_parser("stop")
    stop.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "launch":
        _launch(args.directory, args.candidate)
    elif args.command == "child":
        _child(args.directory)
    elif args.command == "inspect":
        _inspect(args.directory)
    else:
        _stop(args.directory)


if __name__ == "__main__":
    main()
