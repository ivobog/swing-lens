from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text

from app.db import SessionLocal
from app.services.pipeline_baseline import (
    run_baseline_benchmark,
    write_baseline_report,
    write_sequential_parity_fixture,
)
from app.services.pipeline_executor import execute_full_pipeline

DEFAULT_MANIFEST = Path("tests/fixtures/pipeline_performance/run_78_baseline_manifest.json")
DEFAULT_OUTPUT = Path("data/performance/pipeline_run_70_baseline.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the sequential SwingLens pipeline path."
    )
    parser.add_argument("--pipeline-run-id", type=int, default=70)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--parity-output", type=Path)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    outputs: list[dict[str, object]] = []
    database_version = _database_version()

    def operation():
        with SessionLocal() as db:
            result = execute_full_pipeline(db, pipeline_run_id=args.pipeline_run_id)
            output = asdict(result)
            output.pop("performance", None)
            outputs.append(output)
            return result

    report = run_baseline_benchmark(
        operation,
        warmup_iterations=args.warmup,
        measured_iterations=args.repetitions,
        metadata={"fixture": manifest, "database_version": database_version},
        result_serializer=asdict,
    )
    write_baseline_report(args.output, report)

    if args.parity_output:
        write_sequential_parity_fixture(
            args.parity_output,
            outputs,
            metadata={
                "upload_run_id": manifest.get("upload_run_id"),
                "pipeline_run_id": args.pipeline_run_id,
                "fixture_schema_version": manifest.get("fixture_schema_version"),
            },
        )

    print(json.dumps(report["summary"], indent=2, sort_keys=True))


def _database_version() -> str:
    with SessionLocal() as db:
        return str(db.scalar(text("SELECT version()")) or "unknown")


if __name__ == "__main__":
    main()
