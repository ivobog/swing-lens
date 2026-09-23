"""Read-only effective-config check for the process-local release window."""

from __future__ import annotations

import json

from app.settings import RuntimeMode, Settings


def controlled_window_report(settings: Settings) -> dict[str, object]:
    checks = {
        "normal_runtime_mode": settings.runtime_mode is RuntimeMode.NORMAL,
        "durable_pipeline": settings.use_durable_pipeline,
        "standalone_durable_worker": settings.durable_worker_process_enabled,
        "no_embedded_worker": not settings.embedded_job_worker_enabled,
        "winner_auto_maturation_disabled": not settings.winner_probability_auto_maturation_enabled,
        "winner_auto_cohort_refresh_disabled": (
            not settings.winner_probability_auto_cohort_refresh_enabled
        ),
        "market_data_prewarm_disabled": not settings.market_data_prewarm_enabled,
        "winner_pipeline_capture_preserved": (
            settings.winner_probability_enabled and settings.winner_probability_capture_in_pipeline
        ),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


if __name__ == "__main__":
    report = controlled_window_report(Settings())
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
