from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.background_performance_baseline import (  # noqa: E402
    capture_background_performance_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture a read-only background performance baseline from PostgreSQL."
    )
    parser.add_argument("--target-size", action="append", type=int, dest="target_sizes")
    parser.add_argument("--fetch-run-id", action="append", type=int, dest="fetch_run_ids")
    parser.add_argument("--physical-cpu-cores", type=int, default=2)
    parser.add_argument("--memory-gib", type=float, default=16.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    captured_at = datetime.now(UTC)
    output_path = args.output or (
        ROOT
        / "output"
        / f"background_performance_baseline_{captured_at:%Y%m%dT%H%M%SZ}.json"
    )
    with SessionLocal() as db:
        report = capture_background_performance_baseline(
            db,
            target_sizes=args.target_sizes or (175, 402),
            now=captured_at,
            physical_cpu_cores=args.physical_cpu_cores,
            memory_gib=args.memory_gib,
            investigate_fetch_run_ids=args.fetch_run_ids or (),
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
