"""Owned test output and immutable repository-evidence write boundaries."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_ROOT: Path | None = None
_INSTALLED = False
_OWNED: list[Path] = []
PROTECTED_ROOTS = tuple(
    REPO_ROOT / name
    for name in (
        "app",
        "config",
        "alembic",
        "tests",
        "scripts",
        ".github",
        "docs",
        "output",
        ".qa_work",
    )
)


class EvidenceWriteError(PermissionError):
    """A test tried to mutate historical evidence or replace a screenshot."""


def output_root() -> Path:
    global _ROOT
    if _ROOT is not None:
        return _ROOT
    inherited = os.environ.get("SWINGLENS_TEST_OUTPUT_ROOT")
    token = os.environ.get("SWINGLENS_TEST_OUTPUT_TOKEN")
    if inherited and token:
        root = Path(inherited).resolve()
        owner = json.loads((root / ".test-output-owner.json").read_text())
        if owner != {
            "owner": "SwingLens pytest",
            "token": token,
            "task_id": os.environ.get("SWINGLENS_TEST_OUTPUT_TASK_ID", "local-pytest"),
        }:
            raise EvidenceWriteError("Test output ownership does not match")
    else:
        parent = REPO_ROOT / ".qa_work" / "test-executions"
        parent.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="execution-", dir=parent)).resolve()
        token = uuid.uuid4().hex
        with (root / ".test-output-owner.json").open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "owner": "SwingLens pytest",
                    "token": token,
                    "task_id": os.environ.get("SWINGLENS_TEST_OUTPUT_TASK_ID", "local-pytest"),
                },
                stream,
            )
        os.environ["SWINGLENS_TEST_OUTPUT_ROOT"] = str(root)
        os.environ["SWINGLENS_TEST_OUTPUT_TOKEN"] = token
    if (
        root.is_symlink()
        or root.is_junction()
        or any(root.is_relative_to(REPO_ROOT / name) for name in ("docs", "output", "tests"))
    ):
        raise EvidenceWriteError("Historical evidence cannot be an output root")
    _ROOT = root
    _OWNED.append(root)
    return root


def output_directory(group: str) -> Path:
    relative = Path(group)
    if relative.is_absolute() or ".." in relative.parts:
        raise EvidenceWriteError("Output group must stay inside its owned directory")
    case = os.environ.get("PYTEST_CURRENT_TEST", "collection").rsplit(" (", 1)[0]
    case += os.environ.get("PYTEST_XDIST_WORKER", "")
    identity = hashlib.sha256(case.encode()).hexdigest()[:16]
    directory = output_root() / "outputs" / identity / relative
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def artifact_path(requested: str | Path) -> Path:
    """Keep the descriptive filename, with an invocation and case owner."""
    path = Path(requested)
    if path.is_absolute():
        path = (
            path.relative_to(output_root())
            if path.is_relative_to(output_root())
            else path.relative_to(REPO_ROOT)
        )
    if ".." in path.parts:
        raise EvidenceWriteError("Artifact path escapes its owner")
    target = output_directory("artifacts") / path
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def new_owned_directory(parent: Path, name: str) -> Path:
    """Native certification already has an execution ID; require it to be new."""
    directory = (parent / name).resolve()
    check_write_destination(directory)
    directory.mkdir(parents=True, exist_ok=False)
    with (directory / ".test-output-owner.json").open("x", encoding="utf-8") as stream:
        json.dump({"owner": "SwingLens pytest", "execution_id": name}, stream)
    _OWNED.append(directory)
    return directory


def check_write_destination(path: str | bytes | Path, *, protected=None, allowed=None) -> Path:
    target = Path(os.fsdecode(path)).resolve()
    boundaries = PROTECTED_ROOTS if protected is None else protected
    owners = _OWNED if allowed is None else allowed
    if any(target.is_relative_to(Path(root).resolve()) for root in owners):
        return target
    if any(target.is_relative_to(Path(root).resolve()) for root in boundaries):
        raise EvidenceWriteError(f"HISTORICAL_EVIDENCE_WRITE_REFUSED: {target}")
    return target


def _directory_descriptor_path(descriptor: int) -> Path:
    # Linux shutil.rmtree uses relative names with dir_fd. Resolving these
    # against cwd both blocks unrelated cleanup and misclassifies real writes.
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            target = (Path(directory) / str(descriptor)).resolve(strict=True)
            if target.is_dir():
                return target
        except (OSError, RuntimeError):
            continue
    raise EvidenceWriteError("UNRESOLVED_WRITE_DIRECTORY_DESCRIPTOR")


def _audit_write_path(path, descriptor=None):
    if isinstance(path, int):
        return path
    target = Path(os.fsdecode(path))
    if not target.is_absolute() and descriptor not in (None, -1):
        target = _directory_descriptor_path(descriptor) / target
    return target


def history_write_guard(*, protected=None, allowed=None):
    def audit(event, args):
        paths = ()
        if event == "open" and not isinstance(args[0], int):
            mode, flags = args[1], args[2]
            if (mode and any(char in mode for char in "wax+")) or (
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            ):
                paths = (args[0],)
        elif event in {"os.remove", "os.rmdir"}:
            paths = (_audit_write_path(args[0], args[1]),)
        elif event in {"os.mkdir", "os.chmod"}:
            paths = (_audit_write_path(args[0], args[2]),)
        elif event == "os.utime":
            paths = (_audit_write_path(args[0], args[3]),)
        elif event == "os.truncate":
            paths = (args[0],)
        elif event in {"os.rename", "os.link"}:
            paths = tuple(_audit_write_path(args[i], args[i + 2]) for i in (0, 1))
        elif event == "os.symlink":
            paths = (_audit_write_path(args[1], args[2]),)
        for path in paths:
            if not isinstance(path, int):
                check_write_destination(path, protected=protected, allowed=allowed)

    return audit


def check_screenshot_destination(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    target = check_write_destination(path)
    if not any(target.is_relative_to(owner) for owner in _OWNED):
        raise EvidenceWriteError(f"SCREENSHOT_REQUIRES_OWNED_EXECUTION_DIRECTORY: {target}")
    if target.exists():
        raise EvidenceWriteError(f"SCREENSHOT_OVERWRITE_REFUSED: {target}")
    return target


def _guard_screenshot(method):
    if inspect.iscoroutinefunction(method):

        @functools.wraps(method)
        async def asynchronous(self, *args, **kwargs):
            path = check_screenshot_destination(kwargs.get("path"))
            result = await method(self, *args, **kwargs)
            if path is not None and not path.is_file():
                raise AssertionError("Requested screenshot was not created")
            return result

        return asynchronous

    @functools.wraps(method)
    def synchronous(self, *args, **kwargs):
        path = check_screenshot_destination(kwargs.get("path"))
        result = method(self, *args, **kwargs)
        if path is not None and not path.is_file():
            raise AssertionError("Requested screenshot was not created")
        return result

    return synchronous


def install_evidence_write_protection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    output_root()
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.addaudithook(history_write_guard())
    from playwright import async_api, sync_api

    for api in (sync_api, async_api):
        for cls in (api.Page, api.Locator, api.ElementHandle):
            cls.screenshot = _guard_screenshot(cls.screenshot)
    _INSTALLED = True
