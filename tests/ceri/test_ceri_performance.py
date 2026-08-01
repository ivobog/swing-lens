from __future__ import annotations

import time
from datetime import UTC, date, datetime

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.ceri.export_service import CeriExportService
from app.services.ceri.observability import (
    METRIC_FAMILIES,
    CeriMetricRegistry,
    ceri_log_payload,
)


def test_run_scoped_ceri_export_for_500_tickers_stays_under_two_seconds() -> None:
    snapshots = [_snapshot(index) for index in range(500)]
    start = time.perf_counter()

    result = CeriExportService().current_view(FakeDb(), run_id=7, snapshots=snapshots)

    elapsed = time.perf_counter() - start
    assert len(result.rows) == 500
    assert elapsed < 2.0


def test_metric_registry_exposes_required_phase_10_families() -> None:
    registry = CeriMetricRegistry()

    registry.increment("ceri_processing_retries_total", job_type="CERI_PROVIDER_INGEST")
    registry.observe("ceri_scores_capture_duration_ms", 12.5, scope="run")
    snapshot = registry.snapshot()

    assert set(snapshot["families"]) >= set(METRIC_FAMILIES)
    assert "ceri_processing_retries_total|job_type=CERI_PROVIDER_INGEST" in snapshot["counters"]
    assert snapshot["samples"][1]["name"] == "ceri_scores_capture_duration_ms"


def test_structured_log_payload_redacts_secrets_and_keeps_required_keys() -> None:
    payload = ceri_log_payload(
        "provider_quota_degraded",
        job_id=1,
        processing_run_id=2,
        ingestion_run_id=3,
        provider="primary",
        dataset="estimates",
        ticker="MSFT",
        calculation_version="ceri-1.0.0",
        config_hash="hash",
        request_key="request",
        execution_token="token-123",
        authorization="Bearer secret-token",
        local_path=r"C:\Users\Ivica\Downloads\vendor.csv",
    )

    assert payload["job_id"] == 1
    assert payload["provider"] == "primary"
    assert payload["authorization"] == "<restricted:authorization>"
    assert payload["local_path"] == "<restricted:path>"
    assert "secret-token" not in str(payload)


def _snapshot(index: int) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=index + 1,
        run_id=7,
        company_id=index + 1,
        ticker=f"T{index:04d}",
        as_of_session=date(2026, 8, 1),
        cutoff_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        opportunity_score=7.0,
        event_risk_score=2.0,
        data_confidence="High",
        coverage_pct=100.0,
        posture="Positive",
        config_version="2026-07-31",
        config_hash="hash",
        calculation_version="ceri-1.0.0",
        evidence_hash=f"evidence-{index}",
    )


class FakeDb:
    pass
