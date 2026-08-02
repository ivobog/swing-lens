from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriCompany,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.query_service import (
    CeriListQuery,
    CeriQueryError,
    CeriQueryFilters,
    CeriQueryService,
)

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def test_latest_filters_sorts_pages_and_preserves_nulls() -> None:
    db = FakeDb(
        {
            CeriScoreSnapshot: [
                _snapshot(1, "MSFT", opportunity=8.0, risk=2.0, confidence="High"),
                _snapshot(2, "AAPL", opportunity=None, risk=None, confidence="Insufficient"),
                _snapshot(3, "AMZN", opportunity=8.0, risk=1.0, confidence="High"),
            ]
        }
    )

    payload = CeriQueryService().latest(
        db,
        CeriListQuery(
            filters=CeriQueryFilters(opportunity_min=7.0, risk_max=2.0),
            sort="opportunity_score",
            direction="desc",
        ),
    )

    assert payload["total"] == 2
    assert [item["ticker"] for item in payload["items"]] == ["AMZN", "MSFT"]

    all_payload = CeriQueryService().latest(
        db,
        CeriListQuery(filters=CeriQueryFilters(), sort="ticker", direction="asc"),
    )
    aapl = next(item for item in all_payload["items"] if item["ticker"] == "AAPL")
    assert aapl["opportunity_score"] is None
    assert aapl["event_risk_score"] is None


def test_ticker_history_requires_stored_snapshot_mode_and_as_of_cutoff() -> None:
    service = CeriQueryService()
    db = FakeDb({CeriScoreSnapshot: [_snapshot(1, "MSFT")]})

    with pytest.raises(CeriQueryError) as exc:
        service.ticker_history(db, "MSFT", CeriListQuery(filters=CeriQueryFilters()))

    assert exc.value.code == "INVALID_FILTER"

    with pytest.raises(CeriQueryError, match="STORED_SNAPSHOT"):
        service.ticker_history(
            db,
            "MSFT",
            CeriListQuery(
                filters=CeriQueryFilters(mode="AS_KNOWN", as_of=NOW + timedelta(hours=1)),
                sort="cutoff_at",
            ),
        )

    payload = service.ticker_history(
        db,
        "MSFT",
        CeriListQuery(
            filters=CeriQueryFilters(
                mode="STORED_SNAPSHOT",
                as_of=NOW + timedelta(hours=1),
            ),
            sort="cutoff_at",
        ),
    )

    assert payload["total"] == 1
    assert payload["mode"] == "STORED_SNAPSHOT"
    assert payload["source_correction_policy"] == "stored_score_snapshots_only"
    assert payload["evidence_hash"]


def test_revision_detail_exposes_lineage_and_raw_breadth_counts() -> None:
    feature = CeriRevisionFeature(
        id=4,
        company_id=1,
        metric="EPS_DILUTED",
        period_key="FY2026",
        as_of_session=date(2026, 8, 2),
        window_days=30,
        baseline_snapshot_id=10,
        current_snapshot_id=11,
        actual_elapsed_days=29,
        absolute_change=Decimal("0.50"),
        pct_change=Decimal("5.00"),
        upward_count=6,
        downward_count=1,
        net_breadth=Decimal("0.714286"),
        source_observation_ids_json=[101, 102],
        provider_selection_reason="provider_priority",
        evidence_hash="feature-hash",
        config_version="2026-07-31",
        config_hash="config-hash",
        calculation_version="ceri-1.0.0",
    )
    db = FakeDb({CeriRevisionFeature: [feature], CeriCompany: [_company()]})

    payload = CeriQueryService().revision_detail(db, 4)

    assert payload["ticker"] == "MSFT"
    assert payload["raw_breadth_counts"] == {"upward_count": 6, "downward_count": 1}
    assert payload["lineage"]["baseline_snapshot_id"] == 10
    assert payload["lineage"]["source_observation_ids"] == [101, 102]
    assert payload["lineage"]["stored_values"]["net_breadth"] == pytest.approx(0.714286)


def test_events_changes_alerts_and_operations_payloads_are_queryable() -> None:
    source = CeriSourceRecord(
        id=7,
        ingestion_run_id=1,
        provider="manual",
        provider_terms_version="manual-fixture-1.0",
        dataset="estimates",
        provider_record_id="q-1",
        observed_at=NOW - timedelta(days=30),
        ingested_at=NOW - timedelta(days=30),
        content_hash="hash",
        idempotency_key="idem",
        export_policy="exportable",
        quarantine_reason="missing_provider_record_id",
    )
    event = CeriCatalystEvent(
        id=8,
        company_id=1,
        category="REGULATORY",
        subject_key="fda-pdufa",
        canonical_text="FDA decision",
        first_seen_at=NOW,
    )
    revision = CeriCatalystEventRevision(
        id=9,
        catalyst_event_id=8,
        revision_number=1,
        is_current=True,
        expected_date=date(2026, 8, 15),
        status="SCHEDULED",
        direction="NEGATIVE",
        conflict_flags_json=["provider_disagreement"],
    )
    change = CeriChangeEvent(
        id=10,
        company_id=1,
        change_type="NEW_BINARY_EVENT",
        severity="RISK",
        dedup_key="dedup",
        created_at=NOW,
    )
    alert = CeriAlertEvent(
        id=11,
        event_key="alert",
        ticker="MSFT",
        severity="RISK",
        status="UNREAD",
        created_at=NOW,
    )
    db = FakeDb(
        {
            CeriCompany: [_company()],
            CeriCatalystEvent: [event],
            CeriCatalystEventRevision: [revision],
            CeriChangeEvent: [change],
            CeriAlertEvent: [alert],
            CeriSourceRecord: [source],
        }
    )
    service = CeriQueryService()

    assert (
        service.events(
            db,
            CeriListQuery(
                CeriQueryFilters(catalyst_category="REGULATORY"),
                sort="event_date",
            ),
        )["total"]
        == 1
    )
    assert (
        service.changes(
            db,
            CeriListQuery(CeriQueryFilters(ticker="MSFT"), sort="created_at"),
        )["items"][0]["ticker"]
        == "MSFT"
    )
    assert (
        service.alerts(
            db,
            CeriListQuery(CeriQueryFilters(ticker="MSFT"), sort="created_at"),
        )["total"]
        == 1
    )
    assert (
        service.operations_quarantine(
            db,
            CeriListQuery(CeriQueryFilters(), sort="ingested_at"),
        )["total"]
        == 1
    )
    assert (
        service.operations_conflicts(
            db,
            CeriListQuery(CeriQueryFilters(), sort="id"),
        )["total"]
        == 1
    )
    assert (
        service.operations_stale(
            db,
            CeriListQuery(CeriQueryFilters(), sort="stale_days"),
        )["total"]
        == 1
    )


def test_run_missing_raises_stable_error_code() -> None:
    with pytest.raises(CeriQueryError) as exc:
        CeriQueryService().run(FakeDb(), 404, CeriListQuery(CeriQueryFilters()))

    assert exc.value.code == "RUN_NOT_FOUND"
    assert exc.value.status_code == 404


def _snapshot(
    row_id: int,
    ticker: str,
    *,
    opportunity: float | None = 7.0,
    risk: float | None = 2.0,
    confidence: str = "High",
) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=row_id,
        run_id=1,
        company_id=1,
        ticker=ticker,
        as_of_session=date(2026, 8, 2),
        cutoff_at=NOW,
        opportunity_score=opportunity,
        event_risk_score=risk,
        data_confidence=confidence,
        coverage_pct=100.0,
        posture="Positive",
        alignment_flags_json={"technicals": True},
        config_version="2026-07-31",
        config_hash="config-hash",
        calculation_version="ceri-1.0.0",
        evidence_hash=f"evidence-{ticker}",
    )


def _company() -> CeriCompany:
    return CeriCompany(id=1, ticker="MSFT", exchange="NASDAQ", company_name="Microsoft")


class FakeDb:
    def __init__(self, collections=None) -> None:
        self.collections = collections or {}

    def get(self, model, row_id):
        for row in self.collections.get(model, []):
            if row.id == row_id:
                return row
        return None
