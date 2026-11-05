"""Own certification descendants, then snapshot drained evidence before sealing."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from threading import Event
from time import monotonic

import psutil

from scripts.qa import release_evidence as release
from scripts.qa.source_bound import assert_origins, bound_environment

TOKEN_ENV = "SWINGLENS_CERTIFICATION_PROCESS_OWNER"
OWNER_FILE = "process-owner.json"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, indent=2), encoding="utf-8")
    pending.replace(path)


def _identity(process: psutil.Process) -> dict:
    return {"pid": process.pid, "created_at": process.create_time()}


def _same(identity: dict) -> psutil.Process | None:
    try:
        process = psutil.Process(identity["pid"])
        if process.create_time() == identity["created_at"] and process.is_running():
            if process.status() != psutil.STATUS_ZOMBIE:
                return process
    except psutil.NoSuchProcess:
        pass
    return None


def _members(
    owner: dict, *, recover_orphans: bool = False
) -> tuple[list[psutil.Process], list[int]]:
    """An inherited invocation token or a previously verified PID/start pair owns a child."""
    members, inaccessible = {}, []
    for identity in owner.get("members", []):
        try:
            process = _same(identity)
            if process is not None:
                members[process.pid] = process
        except psutil.AccessDenied:
            inaccessible.append(identity["pid"])
    # Ordinary polling follows only verified PID/start identities and their actual
    # descendants. Reading every service's environment on every poll is costly on
    # Windows and is unnecessary while the owner tree is still observable.
    roots = []
    for process in list(members.values()):
        try:
            if process.ppid() not in members:
                roots.append(process)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            inaccessible.append(process.pid)
    # One recursive walk per live root also covers remembered descendants; do
    # not repeatedly enumerate the same subtree for each remembered child.
    for process in roots:
        try:
            for child in process.children(recursive=True):
                if child.status() != psutil.STATUS_ZOMBIE:
                    members[child.pid] = child
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            inaccessible.append(process.pid)
    if not recover_orphans:
        return list(members.values()), inaccessible
    for process in psutil.process_iter():
        if process.pid in members:
            continue
        try:
            # A descendant cannot predate its guardian. On Windows, inspecting
            # environments of old unrelated services is especially expensive;
            # PID/start filtering preserves orphan recovery without reading them.
            if process.create_time() < owner["guardian"]["created_at"]:
                continue
            if process.environ().get(TOKEN_ENV) == owner["token"]:
                if process.status() != psutil.STATUS_ZOMBIE:
                    members[process.pid] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Unrelated services are never signalled. Previously owned inaccessible
            # identities above prevent a positive quiescence claim.
            continue
    return list(members.values()), inaccessible


def _remember(owner: dict, members: list[psutil.Process]) -> None:
    known = {(item["pid"], item["created_at"]): item for item in owner.get("members", [])}
    for process in members:
        try:
            item = _identity(process)
            known[(item["pid"], item["created_at"])] = item
        except psutil.NoSuchProcess:
            pass
    owner["members"] = list(known.values())


def drain(owner: dict, grace_seconds: float = 2.0) -> dict:
    """Bounded graceful interruption, termination and kill of this invocation only."""
    members, inaccessible = _members(owner)
    _remember(owner, members)
    initial = [process.pid for process in members]
    root = owner.get("child")
    if members and root:
        try:
            process = _same(root)
            if os.name != "nt":
                if any(os.getpgid(member.pid) == root["pid"] for member in members):
                    os.killpg(root["pid"], signal.SIGINT)
            elif process is not None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    for action in (None, "terminate", "kill"):
        deadline = monotonic() + grace_seconds
        members, denied = _members(owner)
        inaccessible = sorted(set(inaccessible + denied))
        _remember(owner, members)
        if action:
            for process in reversed(members):
                try:
                    getattr(process, action)()
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    inaccessible.append(process.pid)
        while members and monotonic() < deadline:
            psutil.wait_procs(members, timeout=min(0.1, max(0, deadline - monotonic())))
            members, denied = _members(owner)
            inaccessible = sorted(set(inaccessible + denied))
            _remember(owner, members)
    known = {item["pid"] for item in owner.get("members", [])}
    members, denied = _members(owner, recover_orphans=True)
    _remember(owner, members)
    # A hard-killed guardian can leave token-owned orphans outside the remembered
    # tree. Drain those newly recovered children once, then verify their absence.
    if any(process.pid not in known for process in members):
        for action in ("terminate", "kill"):
            for process in reversed(members):
                try:
                    getattr(process, action)()
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    inaccessible.append(process.pid)
            psutil.wait_procs(members, timeout=grace_seconds)
            members, more_denied = _members(owner)
            denied += more_denied
            _remember(owner, members)
        members, more_denied = _members(owner, recover_orphans=True)
        denied += more_denied
    inaccessible = sorted(set(inaccessible + denied))
    return {
        "quiescent": not members and not inaccessible,
        "initial_pids": initial,
        "alive": [_identity(process) for process in members],
        "inaccessible_owned_pids": inaccessible,
    }


def run_owned(
    live: Path, plan: dict, lane: str, command: list[str], cancel: Event | None = None
) -> int:
    release.validate_plan(plan, current=True)
    live.mkdir(parents=True, exist_ok=False)
    checkout = Path(__file__).resolve().parents[2]
    provenance = assert_origins(
        checkout,
        ("scripts.qa.owned_certification", "scripts.qa.release_evidence",
         "scripts.production_runtime_certification", "app.settings", "app.database_safety"),
    )
    owner = {
        "context": release.context(plan, lane),
        "token": uuid.uuid4().hex,
        "guardian": _identity(psutil.Process()),
        "members": [],
        "state": "STARTING",
        "source_origins": provenance,
        "command": command,
        "interpreter": sys.executable,
    }
    path = live / OWNER_FILE
    _write(path, owner)
    environment = bound_environment(checkout)
    environment[TOKEN_ENV] = owner["token"]
    environment["SWINGLENS_SOURCE_PROVENANCE_DIR"] = str(live.resolve() / "origins")
    cancellation = cancel if cancel is not None else Event()
    previous = {}
    if cancel is None:
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, lambda *_: cancellation.set())
    child = None
    try:
        child = subprocess.Popen(
            command,
            cwd=checkout,
            env=environment,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        owner.update(child=_identity(psutil.Process(child.pid)), state="RUNNING")
        owner["members"].append(owner["child"])
        _write(path, owner)
        while child.poll() is None and not cancellation.wait(0.2):
            members, _ = _members(owner)
            _remember(owner, members)
            _write(path, owner)
        owner["cancelled"] = cancellation.is_set()
    finally:
        owner["state"] = "DRAINING"
        _write(path, owner)
        owner["cleanup"] = drain(owner)
        owner["state"] = "QUIESCENT" if owner["cleanup"]["quiescent"] else "UNSEALED"
        if child is not None:
            try:
                child.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            owner["exit_code"] = child.poll()
        _write(path, owner)
        for number, handler in previous.items():
            signal.signal(number, handler)
    if owner.get("cancelled"):
        return 130
    return owner.get("exit_code") or (0 if owner["cleanup"]["quiescent"] else 1)


def snapshot_and_record(live: Path, snapshot: Path, plan: dict, lane: str, job_result: str) -> dict:
    release.validate_plan(plan, current=True)
    if snapshot.exists() or live.resolve() == snapshot.resolve():
        raise release.EvidenceError("refuse to replace a snapshot or seal live evidence")
    if snapshot.resolve().is_relative_to(live.resolve()) or live.resolve().is_relative_to(
        snapshot.resolve()
    ):
        raise release.EvidenceError("live evidence and snapshot must have separate ownership")
    try:
        owner = release.read_json(live / OWNER_FILE)
    except (release.EvidenceError, OSError, ValueError):
        live.mkdir(parents=True, exist_ok=True)
        cleanup = {"quiescent": False, "reason": "ownership metadata unavailable"}
        release.write_json(
            live / "UNSEALED.json", {"context": release.context(plan, lane), "cleanup": cleanup}
        )
        snapshot.mkdir(parents=True)
        release.write_json(
            snapshot / "incomplete.json",
            {
                "context": release.context(plan, lane),
                "sealed_live_data": False,
                "live_diagnostics": str(live),
                "cleanup": cleanup,
            },
        )
        receipt = release.record_lane(
            snapshot, plan, lane, "cancelled" if job_result == "cancelled" else "failure"
        )
        for path in snapshot.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        return {"receipt": receipt, "cleanup": cleanup}
    if owner["context"] != release.context(plan, lane):
        raise release.EvidenceError("foreign process owner context")
    try:
        guardian = _same(owner["guardian"])
        guardian_unknown = False
    except psutil.AccessDenied:
        guardian = None
        guardian_unknown = True
    # The recorder cannot race the still-running guardian, including its telemetry.
    cleanup = (
        drain(owner)
        if guardian is None and not guardian_unknown
        else {"quiescent": False, "reason": "guardian still running"}
    )
    owner["cleanup"] = cleanup
    owner["state"] = "QUIESCENT" if cleanup["quiescent"] else "UNSEALED"
    if guardian is None and not guardian_unknown:
        _write(live / OWNER_FILE, owner)
    if owner.get("cancelled") or job_result == "cancelled":
        job_result = "cancelled"
    elif owner.get("exit_code") != 0:
        job_result = "failure"
    if cleanup["quiescent"]:
        for path in live.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise release.EvidenceError("linked live evidence cannot become a snapshot")
        shutil.copytree(live, snapshot)
    else:
        snapshot.mkdir(parents=True)
        release.write_json(
            live / "UNSEALED.json",
            {
                "context": owner["context"],
                "reason": "producer quiescence not established",
                "cleanup": cleanup,
            },
        )
        release.write_json(
            snapshot / "incomplete.json",
            {
                "context": owner["context"],
                "live_diagnostics": str(live),
                "sealed_live_data": False,
                "cleanup": cleanup,
            },
        )
        job_result = "cancelled" if job_result == "cancelled" else "failure"
    receipt = release.record_lane(snapshot, plan, lane, job_result)
    for path in snapshot.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return {"receipt": receipt, "cleanup": cleanup}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "record"))
    parser.add_argument("--release-plan", type=Path, required=True)
    parser.add_argument("--lane", default="a04")
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path)
    parser.add_argument("--job-result")
    raw = sys.argv[1:]
    separator = raw.index("--") if "--" in raw else len(raw)
    args = parser.parse_args(raw[:separator])
    command = raw[separator + 1 :]
    plan = release.read_json(args.release_plan)
    if args.action == "run":
        expected = [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.production_runtime_certification",
            "--provider-mode",
            "deterministic",
            "--report-dir",
            str(args.live_root / "a04"),
        ]
        if (
            command[:-1] != expected[:-1]
            or not command
            or Path(command[-1]).resolve() != (args.live_root / "a04").resolve()
        ):
            parser.error("run requires the unchanged A04 command and owned live report directory")
        return run_owned(args.live_root, plan, args.lane, command)
    if args.snapshot_root is None or args.job_result is None:
        parser.error("record requires snapshot-root and job-result")
    result = snapshot_and_record(
        args.live_root, args.snapshot_root, plan, args.lane, args.job_result
    )
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"quiescent={str(result['cleanup']['quiescent']).lower()}\n")
    print("Release evidence:", result["receipt"]["verdict"])
    return 0 if result["receipt"]["verdict"] == "CERTIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
