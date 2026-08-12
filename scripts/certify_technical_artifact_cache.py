from __future__ import annotations

import argparse
import hashlib
import json
import os
from time import perf_counter

for thread_variable in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(thread_variable, "1")

CACHE_MODES = ("OFF", "WRITE_ONLY", "SHADOW_VALIDATE", "ACTIVE")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Certify technical artifacts without rewriting historical technical scores."
        )
    )
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--mode", choices=CACHE_MODES, required=True)
    parser.add_argument("--worker-processes", type=int, default=2)
    parser.add_argument(
        "--commit-cache-state",
        action="store_true",
        help="Commit artifact writes and shadow validation counters only.",
    )
    args = parser.parse_args()
    if not 1 <= args.worker_processes <= 2:
        parser.error("certification worker processes must be one or two on this laptop")

    os.environ["TECHNICAL_ARTIFACT_CACHE_MODE"] = args.mode
    os.environ["TECHNICAL_ARTIFACT_CACHE_ENABLED"] = "false"
    os.environ["TECHNICAL_ARTIFACT_CACHE_WRITE_ENABLED"] = "false"
    os.environ["TECHNICAL_ARTIFACT_CACHE_SHADOW_READ_ENABLED"] = "false"
    os.environ["TECHNICAL_SERIES_VERSION_MAINTENANCE_ENABLED"] = "true"
    os.environ["TECHNICAL_PROCESS_POOL_ENABLED"] = "true"
    os.environ["TECHNICAL_WORKER_PROCESSES"] = str(args.worker_processes)
    os.environ["TECHNICAL_MAX_IN_FLIGHT"] = str(args.worker_processes * 2)

    from app.db import SessionLocal
    from app.services import technical_score_service as service
    from app.services.operational_metrics import operational_metrics

    class SinkDb:
        def __init__(self) -> None:
            self.rows = []

        def execute(self, _statement):
            return None

        def add_all(self, rows) -> None:
            self.rows = list(rows)

        def flush(self) -> None:
            return None

    original_finalize = service.finalize_technical_scores
    sink = SinkDb()

    def sink_finalize(_db, run_id, score_results, **kwargs):
        return original_finalize(sink, run_id, score_results, **kwargs)

    service.finalize_technical_scores = sink_finalize
    operational_metrics.reset()
    started = perf_counter()
    with SessionLocal() as db:
        scores = service.score_run_technicals(db, args.run_id)
        if args.commit_cache_state:
            db.commit()
        else:
            db.rollback()
    elapsed_seconds = perf_counter() - started
    score_fingerprints = sorted(
        service._technical_score_fingerprint(score) for score in scores
    )
    output_fingerprint = hashlib.sha256(
        json.dumps(score_fingerprints, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = {
        "run_id": args.run_id,
        "mode": args.mode,
        "worker_processes": args.worker_processes,
        "score_count": len(scores),
        "error_score_count": sum(
            getattr(score, "technical_confidence", None) == "error" for score in scores
        ),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "output_fingerprint": output_fingerprint,
        "cache_state_committed": args.commit_cache_state,
        "cache": {
            result: int(
                operational_metrics.total(
                    "swinglens_technical_artifact_cache_total", result=result
                )
            )
            for result in (
                "hit",
                "miss",
                "invalid",
                "shadow_candidate",
                "shadow_miss",
            )
        },
        "shadow": {
            result: int(
                operational_metrics.total(
                    "swinglens_technical_artifact_cache_shadow_validations_total",
                    result=result,
                )
            )
            for result in ("match", "mismatch")
        },
        "process_pool_fallbacks": int(
            operational_metrics.total(
                "swinglens_technical_process_pool_fallback_total"
            )
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
