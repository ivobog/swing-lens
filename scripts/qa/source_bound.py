"""Run importing verification commands against one explicit checkout."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import runpy
import sys
import uuid
from pathlib import Path


def assert_origins(checkout: Path, modules: tuple[str, ...]) -> dict:
    checkout = checkout.resolve()
    if Path.cwd().resolve() != checkout:
        raise RuntimeError("verification working directory differs from its checkout")
    origins = {}
    for name in modules:
        module = importlib.import_module(name)
        origin = Path(module.__file__).resolve()
        expected = checkout.joinpath(*name.split(".")).with_suffix(".py")
        if not expected.is_file():
            expected = checkout.joinpath(*name.split("."), "__init__.py")
        if origin != expected.resolve():
            raise RuntimeError(f"verification imported {name} from {origin}; expected {expected}")
        origins[name] = str(origin)
    return {
        "checkout": str(checkout),
        "cwd": str(Path.cwd()),
        "interpreter": str(Path(sys.executable).resolve()),
        "origins": origins,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
    }


def bound_environment(checkout: Path, environment: dict | None = None) -> dict:
    value = dict(os.environ if environment is None else environment)
    value["PYTHONPATH"] = str(checkout.resolve())
    value["SWINGLENS_VERIFY_SOURCE_ROOT"] = str(checkout.resolve())
    return value


def record_origins(checkout: Path, directory: Path, modules: tuple[str, ...]) -> dict:
    record = assert_origins(checkout, modules)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"source-{os.getpid()}-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    modules = ["scripts.qa.source_bound", "app.main", "app.settings", "app.database_safety"]
    if args.module.startswith(("app.", "scripts.")):
        modules.append(args.module)
    record = assert_origins(args.checkout, tuple(dict.fromkeys(modules)))
    record["target_module"] = args.module
    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    path = args.receipt_dir / f"source-{os.getpid()}-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.environ.update(bound_environment(args.checkout))
    os.environ["SWINGLENS_SOURCE_PROVENANCE_DIR"] = str(args.receipt_dir.resolve())
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    sys.argv = [args.module, *arguments]
    runpy.run_module(args.module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
