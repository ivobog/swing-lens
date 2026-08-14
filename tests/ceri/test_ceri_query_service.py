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
    CeriEstimateSnapshot,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.query_service import (
    CeriListQuery,
    CeriQueryError,
    CeriQueryFilters,
    CeriQueryService,
    _dataset_evidence_state,
    _format_signed,
    _score_snapshot_payload,
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


def test_descending_opportunity_sort_places_nulls_last_across_pages() -> None:
    db = FakeDb(
        {
            CeriScoreSnapshot: [
                _snapshot(1, "NULL1", opportunity=None),
                _snapshot(2, "HIGH", opportunity=9.0),
                _snapshot(3, "MID", opportunity=5.0),
                _snapshot(4, "NULL2", opportunity=None),
            ]
        }
    )
    service = CeriQueryService()

    first = service.latest(
        db,
        CeriListQuery(CeriQueryFilters(), limit=2, sort="opportunity_score", direction="desc"),
    )
    second = service.latest(
        db,
        CeriListQuery(
            CeriQueryFilters(),
            limit=2,
            offset=2,
            sort="opportunity_score",
            direction="desc",
        ),
    )

    assert [row["ticker"] for row in first["items"]] == ["HIGH", "MID"]
    assert {row["ticker"] for row in second["items"]} == {"NULL1", "NULL2"}


def test_pagination_metadata_and_all_rows_are_complete_without_duplicates() -> None:
    snapshots = [
        _snapshot(index, f"T{index:03d}", opportunity=float(200 - index))
        for index in range(1, 178)
    ]
    service = CeriQueryService()
    db = FakeDb({CeriScoreSnapshot: snapshots})
    seen: list[str] = []

    for offset in (0, 50, 100, 150):
        payload = service.latest(
            db,
            CeriListQuery(CeriQueryFilters(), limit=50, offset=offset),
        )
        seen.extend(row["ticker"] for row in payload["items"])
        assert payload["total_items"] == 177
        assert payload["page_size"] == 50
        assert payload["total_pages"] == 4

    assert len(seen) == 177
    assert len(set(seen)) == 177


def test_all_177_snapshot_api_values_remain_identical_across_pages() -> None:
    snapshots = []
    expected = {}
    confidence_cycle = ("High", "Normal", "Low", "Insufficient")
    posture_cycle = ("Positive", "Improving", "Mixed", "Deteriorating")
    for index in range(177):
        snapshot = _snapshot(
            index + 1,
            f"T{index:03d}",
            opportunity=None if index >= 173 else float(index % 11),
            risk=float(index % 5),
            confidence=confidence_cycle[index % len(confidence_cycle)],
        )
        snapshot.posture = posture_cycle[index % len(posture_cycle)]
        snapshots.append(snapshot)
        expected[snapshot.ticker] = (
            snapshot.opportunity_score,
            snapshot.event_risk_score,
            snapshot.data_confidence,
            snapshot.posture,
        )

    service = CeriQueryService()
    actual = {}
    for offset in (0, 50, 100, 150):
        page = service.latest(
            FakeDb({CeriScoreSnapshot: snapshots}),
            CeriListQuery(CeriQueryFilters(), limit=50, offset=offset),
        )
        for row in page["items"]:
            actual[row["ticker"]] = (
                row["opportunity_score"],
                row["event_risk_score"],
                row["data_confidence"],
                row["posture"],
            )

    assert actual == expected


def test_confidence_direction_edge_cases_do_not_change_summary_predicate() -> None:
    ktb = _snapshot(1, "KTB", opportunity=4.238087, risk=0.0, confidence="High")
    ktb.posture = "Mixed"
    pke = _snapshot(2, "PKE", opportunity=9.19643, risk=0.0, confidence="Low")
    dbrg = _snapshot(3, "DBRG", opportunity=9.214286, risk=0.0, confidence="Low")
    for snapshot in (ktb, pke, dbrg):
        snapshot.event_risk_ledger_json = {"accepted_evidence": True}

    payload = CeriQueryService().latest(
        FakeDb({CeriScoreSnapshot: [ktb, pke, dbrg]}),
        CeriListQuery(CeriQueryFilters()),
    )

    by_ticker = {row["ticker"]: row for row in payload["items"]}
    assert by_ticker["KTB"]["data_confidence"] == "High"
    assert by_ticker["KTB"]["posture"] == "Mixed"
    assert by_ticker["PKE"]["data_confidence"] == "Low"
    assert by_ticker["DBRG"]["data_confidence"] == "Low"
    assert payload["summary"]["matching_tickers"] == ["DBRG", "PKE"]


def test_snapshot_presentation_reconciles_coverage_risk_warnings_and_lineage() -> None:
    snapshot = _snapshot(1, "XPEL", opportunity=None, risk=0.0, confidence="Insufficient")
    snapshot.opportunity_coverage_pct = 99.0
    snapshot.opportunity_unrated_reason = "INSUFFICIENT_COMPONENT_COVERAGE"
    snapshot.warnings_json = [
        "estimate_coverage_low",
        "opportunity_component_coverage_insufficient",
    ]
    snapshot.opportunity_ledger_json = {
        "minimum_required_coverage_pct": 60.0,
        "components": [
            {
                "name": "revision_breadth",
                "available": True,
                "weight": 0.15,
                "value": 3.75,
                "evidence_ids": [77],
            },
            {
                "name": "surprise_trend",
                "available": True,
                "weight": 0.15,
                "value": 6.0,
                "evidence_ids": [],
            },
        ],
    }
    snapshot.event_risk_ledger_json = {
        "accepted_evidence": False,
        "components": [
            {
                "component": "earnings_proximity_risk",
                "score": 0.0,
                "reason": "earnings_proximity:unknown",
            }
        ],
        "selected_event_ids": [],
        "rejected_event_ids": [],
    }
    snapshot.evidence_lineage_json = {
        "evidence_states": [
            {
                "evidence_type": "REVISION_FEATURE",
                "evidence_id": 77,
                "states": ["PERSISTED", "CONSIDERED", "REJECTED"],
            }
        ]
    }

    payload = _score_snapshot_payload(snapshot)

    assert payload["opportunity"]["coverage_pct"] == pytest.approx(30.0)
    assert payload["opportunity"]["coverage_matches_ledger"] is False
    assert payload["event_risk"]["evidence_state"] == "UNAVAILABLE"
    assert payload["event_risk"]["low_risk_eligible"] is False
    assert payload["warning_summary"] == {
        "count": 2,
        "severity": "BLOCKER",
        "dominant_warning": "opportunity_component_coverage_insufficient",
    }
    assert payload["lineage_reconciliation"]["valid"] is True
    assert payload["lineage_reconciliation"]["selected_evidence_count"] == 1
    breadth = next(
        row
        for row in payload["lineage_reconciliation"]["components"]
        if row["component"] == "revision_breadth"
    )
    surprise = next(
        row
        for row in payload["lineage_reconciliation"]["components"]
        if row["component"] == "surprise_trend"
    )
    assert breadth["selected_lineage_ids"] == [77]
    assert surprise["lineage_exemption_reason"] == "AGGREGATE_COMPONENT_NO_DIRECT_EVIDENCE_IDS"


def test_list_detects_selected_revision_value_lineage_mismatch() -> None:
    snapshot = _snapshot(1, "MSGE", opportunity=8.2, risk=0.0, confidence="Normal")
    snapshot.opportunity_ledger_json = {
        "coverage_pct": 60.0,
        "minimum_required_coverage_pct": 60.0,
        "components": [
            {
                "name": "revision_magnitude",
                "available": True,
                "weight": 0.25,
                "value": 10.0,
                "evidence_ids": [4],
            }
        ],
    }
    feature = CeriRevisionFeature(
        id=4,
        company_id=1,
        metric="EPS_DILUTED",
        period_key="MSGE:CURRENT_QUARTER",
        period_slot="CURRENT_QUARTER",
        as_of_session=snapshot.as_of_session,
        window_days=30,
        current_snapshot_id=10,
        baseline_snapshot_id=11,
        pct_change=Decimal("50.909091"),
        config_version="test",
        config_hash="hash",
        calculation_version=snapshot.calculation_version,
        evidence_hash="feature",
    )
    current = CeriEstimateSnapshot(
        id=10,
        source_record_id=100,
        company_id=1,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        consensus=Decimal("-0.475"),
        canonical_observation_key="current",
    )
    baseline = CeriEstimateSnapshot(
        id=11,
        source_record_id=101,
        company_id=1,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        consensus=Decimal("-0.475"),
        canonical_observation_key="baseline",
    )
    db = FakeDb(
        {
            CeriScoreSnapshot: [snapshot],
            CeriRevisionFeature: [feature],
            CeriEstimateSnapshot: [current, baseline],
        }
    )

    row = CeriQueryService().latest(db, CeriListQuery(CeriQueryFilters()))["items"][0]

    assert row["lineage_reconciliation"]["valid"] is False
    assert row["lineage_reconciliation"]["revision_value_mismatches"] == [4]
    assert row["lineage_reconciliation"]["requires_rebuild"] is True
    assert "revision_feature_lineage_mismatch" in row["warnings"]
    assert row["warning_summary"]["severity"] == "BLOCKER"


def test_snapshot_api_distinguishes_source_normalized_eligible_and_selected() -> None:
    snapshot = _snapshot(1, "MSFT", opportunity=None, confidence="Insufficient")
    snapshot.calculation_version = "ceri-1.2.0"
    snapshot.component_json = {"source_ids": [101]}
    snapshot.opportunity_ledger_json = {
        "components": [
            {
                "name": "revision_magnitude",
                "available": False,
                "unavailable_reason": "SAME_PROVIDER_BASELINE_INELIGIBLE",
                "evidence_ids": [],
            }
        ]
    }
    source = CeriSourceRecord(
        id=101,
        provider="eodhd",
        dataset="estimates",
        provider_record_id="MSFT:estimate",
        content_hash="hash",
        idempotency_key="key",
        retrieved_at=NOW,
    )
    estimate = CeriEstimateSnapshot(
        id=201,
        source_record_id=101,
        company_id=1,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 9, 30),
        canonical_observation_key="estimate-201",
    )
    revision = CeriRevisionFeature(
        id=301,
        company_id=1,
        metric="EPS_DILUTED",
        period_key="MSFT:CURRENT_QUARTER",
        period_slot="CURRENT_QUARTER",
        as_of_session=date(2026, 8, 2),
        window_days=30,
        unavailable_reason="SAME_PROVIDER_BASELINE_INELIGIBLE",
        config_version="test",
        config_hash="hash",
        calculation_version="ceri-1.2.0",
    )
    db = FakeDb(
        {
            CeriSourceRecord: [source],
            CeriEstimateSnapshot: [estimate],
            CeriRevisionFeature: [revision],
        }
    )

    payload = _score_snapshot_payload(snapshot, db=db)
    estimates = payload["evidence_diagnostics"]["estimates"]

    assert estimates["source_status"] == "FRESH"
    assert estimates["normalized_count"] == 1
    assert estimates["eligible_count"] == 0
    assert estimates["selected_count"] == 0
    assert estimates["dominant_blocker"] == "SAME_PROVIDER_BASELINE_INELIGIBLE"


@pytest.mark.parametrize(
    ("source_status", "normalized", "eligible", "selected", "expected"),
    [
        ("ABSENT", 0, 0, 0, "CATALYST_SOURCE_UNAVAILABLE"),
        ("STALE", 2, 1, 0, "CATALYST_SOURCE_STALE"),
        ("FRESH", 0, 0, 0, "CATALYST_NONE_ELIGIBLE"),
        ("FRESH", 3, 0, 0, "CATALYST_EVIDENCE_INELIGIBLE"),
        ("FRESH", 3, 2, 1, "CATALYST_SELECTED"),
    ],
)
def test_catalyst_source_and_evidence_states_are_not_conflated(
    source_status: str,
    normalized: int,
    eligible: int,
    selected: int,
    expected: str,
) -> None:
    assert (
        _dataset_evidence_state(
            "catalysts",
            source_status=source_status,
            normalized_count=normalized,
            eligible_count=eligible,
            selected_count=selected,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("value", "suffix", "expected"),
    [
        (Decimal("12.15"), "%", "+12.15%"),
        (Decimal("-3.126"), "%", "-3.13%"),
        (Decimal("0"), "%", "0.00%"),
        (Decimal("1"), "", "+1.00"),
        (Decimal("0.333"), "", "+0.33"),
        (Decimal("-1"), "", "-1.00"),
    ],
)
def test_revision_and_breadth_display_formatting(
    value: Decimal,
    suffix: str,
    expected: str,
) -> None:
    assert _format_signed(value, suffix=suffix) == expected


def test_production_dto_does_not_expose_raw_component_storage_json() -> None:
    snapshot = _snapshot(1, "MSFT", opportunity=None, risk=0.0, confidence="Insufficient")
    snapshot.opportunity_coverage_pct = 0.0
    snapshot.opportunity_unrated_reason = "INSUFFICIENT_COMPONENT_COVERAGE"
    snapshot.component_json = {"private_storage_shape": {"should_not_render": True}}
    snapshot.opportunity_ledger_json = {
        "coverage_pct": 0.0,
        "minimum_required_coverage_pct": 60.0,
        "components": [],
    }
    snapshot.confidence_ledger_json = {
        "score": 0.0,
        "gates": ["ZERO_USABLE_CORE_REVISION_COVERAGE"],
    }
    snapshot.event_risk_ledger_json = {
        "score": 0.0,
        "dominant_component": "earnings_proximity_risk",
    }

    payload = _score_snapshot_payload(snapshot)

    assert "component_json" not in payload
    assert "components" not in payload
    assert payload["opportunity"] == {
        "score": None,
        "rated": False,
        "coverage_pct": 0.0,
        "minimum_required_coverage_pct": 60.0,
        "unrated_reason": "INSUFFICIENT_COMPONENT_COVERAGE",
        "reweighted": False,
        "coverage_matches_ledger": True,
    }
    assert payload["confidence"]["gates"] == ["ZERO_USABLE_CORE_REVISION_COVERAGE"]


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
        comparison_mode="SAME_PROVIDER_RELATIVE",
        warnings_json=["canonical_currency_unavailable_relative_only"],
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
    assert payload["comparison_mode"] == "SAME_PROVIDER_RELATIVE"
    assert "canonical_currency_unavailable_relative_only" in payload["warnings"]
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
    status = service.operations_status(db)
    assert status["conflicted_count"] == 1
    assert status["stale_count"] == 1
    assert status["quarantined_count"] == 1


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
