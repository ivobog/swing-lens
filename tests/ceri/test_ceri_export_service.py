from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriScoreSnapshot, CeriSourceRecord
from app.services.ceri.export_service import CeriExportService

UTC = ZoneInfo("UTC")


def test_current_view_export_filters_run_and_ticker_with_audit_fields() -> None:
    snapshot = _snapshot("MSFT", run_id=7)
    other = _snapshot("AAPL", run_id=7)

    result = CeriExportService().current_view(
        FakeDb(),
        run_id=7,
        tickers=["MSFT"],
        snapshots=[snapshot, other],
    )

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "MSFT"
    assert result.rows[0]["evidence_hash"] == "evidence-MSFT"
    assert "opportunity_score" in result.to_csv()


def test_full_evidence_export_omits_restricted_provider_fields() -> None:
    source = CeriSourceRecord(
        id=10,
        ingestion_run_id=2,
        provider="manual",
        dataset="estimates",
        provider_record_id="est-1",
        raw_json={"ticker": "MSFT", "raw_payload": "secret", "provider_secret": "x"},
        source_url="https://vendor.example/source",
        content_hash="hash",
        idempotency_key="key",
        export_policy="restricted",
    )

    result = CeriExportService().full_evidence(FakeDb(), source_records=[source])

    row = result.rows[0]
    assert row["source_url"] == "<restricted:source_url>"
    assert row["raw_payload"] == "<restricted:raw_payload>"
    assert row["permitted_fields"] == {"ticker": "MSFT"}
    assert "provider_secret" not in result.to_json()


def _snapshot(ticker: str, *, run_id: int) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=1,
        run_id=run_id,
        company_id=42,
        ticker=ticker,
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
        evidence_hash=f"evidence-{ticker}",
    )


class FakeDb:
    pass
