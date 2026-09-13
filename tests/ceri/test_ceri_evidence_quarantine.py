from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.dialects import postgresql

from app.models.ceri_tables import CeriEvidenceDisposition, CeriScoreSnapshot
from app.services.ceri.change_semantics import select_prior_comparison
from app.services.ceri.evidence_eligibility import (
    ELIGIBLE,
    EXCLUDED,
    EvidenceDispositionRequest,
    effective_disposition_by_snapshot,
    eligible_snapshot_predicate,
    eligible_snapshot_select,
    filter_eligible_snapshots,
)
from app.services.ceri.export_service import CeriExportService
from app.services.ceri.outcome_feature_export import CeriOutcomeFeatureExportService
from app.services.ceri.query_service import CeriListQuery, CeriQueryFilters, CeriQueryService

NOW = datetime(2026, 9, 13, 18, tzinfo=UTC)


def test_snapshot_without_disposition_remains_eligible() -> None:
    snapshot = _snapshot(1)
    assert filter_eligible_snapshots(FakeDb({CeriScoreSnapshot: [snapshot]}), [snapshot]) == [
        snapshot
    ]


def test_latest_selection_filters_before_ranking_and_falls_back() -> None:
    prior = _snapshot(1)
    excluded = _snapshot(2)
    db = FakeDb(
        {
            CeriScoreSnapshot: [prior, excluded],
            CeriEvidenceDisposition: [_disposition(1, excluded.id, EXCLUDED)],
        }
    )
    result = CeriQueryService().latest(
        db,
        CeriListQuery(filters=CeriQueryFilters(), sort="ticker", direction="asc"),
    )
    assert [row["id"] for row in result["items"]] == [prior.id]


def test_all_snapshots_excluded_returns_no_latest() -> None:
    rows = [_snapshot(1), _snapshot(2)]
    db = FakeDb(
        {
            CeriScoreSnapshot: rows,
            CeriEvidenceDisposition: [
                _disposition(1, 1, EXCLUDED),
                _disposition(2, 2, EXCLUDED),
            ],
        }
    )
    result = CeriQueryService().latest(db, CeriListQuery(filters=CeriQueryFilters()))
    assert result["total"] == 0


def test_prior_selection_skips_multiple_consecutive_exclusions() -> None:
    rows = [_snapshot(value) for value in range(1, 5)]
    db = FakeDb(
        {
            CeriScoreSnapshot: rows,
            CeriEvidenceDisposition: [
                _disposition(1, 2, EXCLUDED),
                _disposition(2, 3, EXCLUDED),
            ],
        }
    )
    candidates = filter_eligible_snapshots(db, rows[:-1])
    prior, _, _ = select_prior_comparison(rows[-1], candidates)
    assert prior.id == 1


def test_only_one_eligible_snapshot_has_no_prior() -> None:
    rows = [_snapshot(value) for value in range(1, 4)]
    db = FakeDb(
        {
            CeriScoreSnapshot: rows,
            CeriEvidenceDisposition: [
                _disposition(1, 1, EXCLUDED),
                _disposition(2, 2, EXCLUDED),
            ],
        }
    )
    eligible = filter_eligible_snapshots(db, rows)
    prior, _, _ = select_prior_comparison(eligible[0], [])
    assert [row.id for row in eligible] == [3]
    assert prior is None


def test_effective_disposition_uses_created_at_then_id_and_supports_reversal() -> None:
    db = FakeDb(
        {
            CeriEvidenceDisposition: [
                _disposition(1, 5, EXCLUDED),
                _disposition(2, 5, ELIGIBLE),
            ]
        }
    )
    assert effective_disposition_by_snapshot(db) == {5: ELIGIBLE}


def test_exact_duplicate_requests_have_same_idempotency_fingerprint() -> None:
    request = EvidenceDispositionRequest(
        ceri_snapshot_id=7,
        disposition=EXCLUDED,
        reason_code="TEST",
        incident_reference="INCIDENT",
        actor_source="pytest",
        metadata_json={"b": 2, "a": 1},
    )
    duplicate = EvidenceDispositionRequest(
        ceri_snapshot_id=7,
        disposition=EXCLUDED,
        reason_code="TEST",
        incident_reference="INCIDENT",
        actor_source="pytest",
        metadata_json={"a": 1, "b": 2},
    )
    assert request.values()["event_fingerprint"] == duplicate.values()["event_fingerprint"]


def test_screening_and_opportunity_classification_ignore_excluded_snapshot() -> None:
    older = _snapshot(1, opportunity=8.0, posture="Positive")
    newer = _snapshot(2, opportunity=9.0, posture="Positive")
    db = FakeDb(
        {
            CeriScoreSnapshot: [older, newer],
            CeriEvidenceDisposition: [_disposition(1, 2, EXCLUDED)],
        }
    )
    result = CeriQueryService().latest(
        db,
        CeriListQuery(filters=CeriQueryFilters(opportunity_min=7.0)),
    )
    assert [(row["id"], row["opportunity_score"]) for row in result["items"]] == [(1, 8.0)]


def test_current_export_and_feature_extraction_ignore_excluded_snapshot() -> None:
    eligible = _snapshot(1)
    excluded = _snapshot(2)
    db = FakeDb(
        {
            CeriScoreSnapshot: [eligible, excluded],
            CeriEvidenceDisposition: [_disposition(1, 2, EXCLUDED)],
        }
    )
    export = CeriExportService().current_view(db, snapshots=[eligible, excluded])
    features = CeriOutcomeFeatureExportService().export_snapshots(
        db,
        snapshots=[eligible, excluded],
        cutoff_at=NOW + timedelta(days=10),
    )
    assert [row["evidence_hash"] for row in export.rows] == [eligible.evidence_hash]
    assert [row["evidence_hash"] for row in features.rows] == [eligible.evidence_hash]


def test_sql_eligibility_is_a_single_correlated_lookup() -> None:
    sql = str(
        eligible_snapshot_predicate().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ceri_evidence_dispositions" in sql
    assert "ORDER BY ceri_evidence_dispositions.created_at DESC" in sql
    assert "LIMIT 1" in sql


def test_canonical_selector_uses_one_set_based_effective_disposition_join() -> None:
    sql = str(
        eligible_snapshot_select(name="effective_test_dispositions").compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN" in sql
    assert "DISTINCT ON" in sql
    assert sql.count("FROM ceri_evidence_dispositions") == 1


def _snapshot(
    snapshot_id: int,
    *,
    opportunity: float = 5.0,
    posture: str = "Mixed",
) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=snapshot_id,
        run_id=100 + snapshot_id,
        company_id=1,
        ticker="MSFT",
        as_of_session=date(2026, 9, snapshot_id),
        cutoff_at=NOW + timedelta(days=snapshot_id),
        opportunity_score=opportunity,
        opportunity_coverage_pct=100.0,
        event_risk_score=1.0,
        data_confidence="Normal",
        coverage_pct=100.0,
        posture=posture,
        event_risk_ledger_json={"accepted_evidence": True},
        component_json={"source_ids": [snapshot_id]},
        config_version="test",
        config_hash="test",
        calculation_version="test",
        evidence_contract_version="test",
        comparison_state="COMPARABLE",
        evidence_hash=f"evidence-{snapshot_id}",
    )


def _disposition(
    disposition_id: int,
    snapshot_id: int,
    disposition: str,
) -> CeriEvidenceDisposition:
    return CeriEvidenceDisposition(
        id=disposition_id,
        ceri_snapshot_id=snapshot_id,
        disposition=disposition,
        reason_code="TEST",
        incident_reference="TEST",
        actor_source="pytest",
        metadata_json={},
        event_fingerprint=f"event-{disposition_id}",
        created_at=NOW,
    )


class FakeDb:
    def __init__(self, collections) -> None:
        self.collections = collections

    def get(self, model, row_id):
        return next(
            (row for row in self.collections.get(model, []) if row.id == row_id),
            None,
        )
