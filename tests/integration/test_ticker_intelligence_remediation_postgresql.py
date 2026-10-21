"""Original review counterexamples on migrated PostgreSQL, retaining every FK."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session
from test_ticker_intelligence import seed_intelligence
from test_ticker_search import CUTOFF, insert

from app.database_safety import run_guarded_alembic_upgrade
from app.db import get_db
from app.main import create_app
from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriGuidanceEvent,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import (
    MarketCalculationContext,
    PipelineRun,
    WinnerOutcomeDefinition,
    WinnerProbabilityEstimate,
)
from app.services.ceri.query_service import (
    CeriListQuery,
    CeriQueryFilters,
    CeriQueryService,
    _guidance_summary,
)
from app.services.pipeline_outcome import derive_pipeline_outcome
from app.services.ticker_intelligence_service import ticker_intelligence
from app.services.winner_probability.api_service import WinnerProbabilityApiService
from app.services.winner_probability.dtos import WinnerProbabilityApiQuery
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_db(disposable_postgres_database):
    run_guarded_alembic_upgrade(
        Config(str(ROOT / "alembic.ini")), disposable_postgres_database, "head"
    )
    engine = create_engine(disposable_postgres_database)
    assert inspect(engine).get_foreign_keys("ceri_revision_features")
    assert inspect(engine).get_foreign_keys("winner_probability_estimates")
    with Session(engine) as db:
        seed_intelligence(db)
        yield db
    engine.dispose()


def settings():
    return Settings(
        _env_file=None,
        job_worker_enabled=False,
        db_monitor_enabled=False,
        observability_metrics_enabled=False,
        ceri_enabled=True,
        ceri_ui_enabled=True,
    )


def data(db, section="ceri", run=7):
    return ticker_intelligence(
        db, "EOG", "ceri:8000", run, run, section, detail=True, settings=settings()
    )


def html(db, section="ceri"):
    app = create_app(settings())
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        response = client.get(
            f"/api/tickers/EOG/intelligence/{section}",
            params={"identity": "ceri:8000", "run_id": 7, "evidence_id": 7, "detail": "true"},
        )
        assert response.status_code == 200
        return response.json()["html"]


def clone(db, model, source_id, **overrides):
    row = db.get(model, source_id)
    values = {c.name: deepcopy(getattr(row, c.name)) for c in model.__table__.columns}
    insert(db, model, **(values | overrides))


def test_f01_rejected_snapshot_cannot_supply_current_activity_and_audit_is_retained(migrated_db):
    db = migrated_db
    # Eligible older activity remains useful when the newer owner is rejected.
    clone(
        db,
        CeriChangeEvent,
        9100,
        id=9101,
        from_snapshot_id=None,
        to_snapshot_id=8999,
        change_type="POSTURE_CHANGED",
        dedup_key="older-eligible",
    )
    clone(db, CeriAlertEvent, 9300, id=9301, source_change_event_id=9101, event_key="older-alert")
    assert "Alert #9300" in html(db)
    for status in ("ACTIVE", "UNREAD", "ACKNOWLEDGED"):
        with db.begin_nested() as transaction:
            db.execute(
                CeriAlertEvent.__table__.update()
                .where(CeriAlertEvent.id == 9300)
                .values(status=status)
            )
            assert 9300 in [r["id"] for r in data(db)["alerts"]["items"]]
            transaction.rollback()
    with db.begin_nested() as transaction:
        db.execute(
            CeriAlertEvent.__table__.update()
            .where(CeriAlertEvent.id == 9300)
            .values(validity_classification="INVALID_LEGACY")
        )
        assert 9300 not in [r["id"] for r in data(db)["alerts"]["items"]]
        transaction.rollback()
    previous = next(r for r in data(db)["changes"]["items"] if r["id"] == 9101)
    assert previous["history_scope"] == "HISTORICAL"
    for authority, status in (
        ("QUARANTINED", "COMPLETED"),
        ("RETIRED", "COMPLETED"),
        ("ACTIVE", "FAILED"),
        ("ACTIVE", "CANCELLED"),
        ("ACTIVE", "BLOCKED"),
    ):
        with db.begin_nested() as transaction:
            db.execute(
                PipelineRun.__table__.update()
                .where(PipelineRun.id == 7)
                .values(execution_authority_state=authority, status=status)
            )
            result = data(db)
            assert result["latest"]["id"] == 8999
            assert 9100 not in [r["id"] for r in result["changes"]["items"]]
            assert 9300 not in [r["id"] for r in result["alerts"]["items"]]
            assert 9101 in [r["id"] for r in result["changes"]["items"]]
            assert 9301 in [r["id"] for r in result["alerts"]["items"]]
            audit = CeriQueryService().changes(
                db,
                CeriListQuery(
                    CeriQueryFilters(
                        ticker="EOG", include_ineligible=True, include_non_comparable=True
                    ),
                    sort="created_at",
                ),
            )
            assert 9100 in [r["id"] for r in audit["items"]]
            transaction.rollback()
    for safe in (True, False):
        with db.begin_nested() as transaction:
            summary = derive_pipeline_outcome(
                "PARTIAL", {"combined_results": 1, "incomplete_rows": 1}
            )
            db.execute(
                PipelineRun.__table__.update()
                .where(PipelineRun.id == 7)
                .values(
                    status="PARTIAL",
                    result_json={"pipeline_outcome_summary": summary} if safe else {},
                )
            )
            result = data(db)
            assert result["latest"]["id"] == (9000 if safe else 8999)
            assert (9300 in [r["id"] for r in result["alerts"]["items"]]) is safe
            transaction.rollback()
    # Missing certification is rejected by headline and detail, not only the score.
    db.execute(
        CeriScoreSnapshot.__table__.update()
        .where(CeriScoreSnapshot.id == 9000)
        .values(evidence_id=None)
    )
    assert data(db)["latest"]["id"] == 8999
    assert "Alert #9300" not in html(db)


def context(db, context_id, *, pipeline=None):
    insert(
        db,
        MarketCalculationContext,
        id=context_id,
        pipeline_run_id=pipeline,
        upload_run_id=7,
        cutoff_at=CUTOFF + timedelta(days=2),
        exchange_timezone="America/New_York",
        latest_completed_session=(CUTOFF + timedelta(days=2)).date(),
        calendar_version="test-calendar",
        bar_readiness_version="v1",
        cutoff_reason="test",
    )


def revision(db, feature_id, *, context_id=8701, **overrides):
    values = dict(
        id=feature_id,
        company_id=8000,
        metric="EPS_DILUTED",
        period_key=f"FY{feature_id}",
        period_slot="CURRENT_FY",
        as_of_session=(CUTOFF + timedelta(days=2)).date(),
        calculation_context_id=context_id,
        calculation_cutoff_at=CUTOFF + timedelta(days=2),
        calendar_version="test-calendar",
        ownership_mode="PIPELINE",
        window_days=30,
        pct_change=5,
        known_at=CUTOFF,
        config_version="frozen-ceri-v1",
        config_hash="ceri-9000",
        calculation_version="ceri-1",
        revision_confidence_label="High",
    )
    insert(db, CeriRevisionFeature, **(values | overrides))


def pin(db, ids):
    db.execute(
        CeriScoreSnapshot.__table__.update()
        .where(CeriScoreSnapshot.id == 9000)
        .values(
            calculation_context_id=8701,
            calendar_version="test-calendar",
            opportunity_ledger_json={
                "components": [{"name": "revision_magnitude", "evidence_ids": ids}]
            },
        )
    )
    db.expire_all()


def test_f02_only_verified_pinned_context_and_cutoff_can_supply_revisions(migrated_db):
    db = migrated_db
    context(db, 8701, pipeline=7)
    context(db, 8702)
    revision(db, 88801)
    revision(db, 88802, context_id=8702, pct_change=88)
    revision(db, 88803, known_at=CUTOFF + timedelta(days=3), pct_change=99)
    revision(db, 88804, config_hash="other-config")
    revision(db, 88805, calculation_cutoff_at=CUTOFF + timedelta(days=3))
    revision(db, 88806)  # Valid but not selected by this snapshot.
    pin(db, [88801, 88802, 88803, 88804, 88805])
    result = data(db)
    assert [r["id"] for r in result["revisions"]] == [88801]
    assert result["revision_provenance"]["state"] == "partial"
    assert result["revision_provenance"]["rejected_count"] == 4
    content = html(db)
    for label in ("Fiscal period", "FY88801", "Confidence", "High", "Source cutoff"):
        assert label in content
    assert "+99.00%" not in content


def test_f02_missing_lineage_is_explicit_and_does_not_borrow_company_history(migrated_db):
    db = migrated_db
    context(db, 8701)
    revision(db, 88801)
    assert data(db)["revisions"] == []
    assert data(db)["revision_provenance"]["state"] == "unavailable"
    pin(db, [999999])
    assert data(db)["revisions"] == []
    assert data(db)["revision_provenance"]["rejected_count"] == 1


def test_f02_many_pinned_revisions_have_explicit_bounded_continuation(migrated_db):
    db = migrated_db
    context(db, 8701)
    context(db, 8702)
    for i in range(35):
        revision(db, 88000 + i)
        revision(db, 87000 + i, context_id=8702)  # Earlier IDs cannot consume the limit.
    pin(db, list(range(88000, 88035)))
    result = data(db)
    assert len(result["revisions"]) == 30
    assert all(r["id"] >= 88000 for r in result["revisions"])
    assert result["revision_provenance"]["verified_count"] == 35
    assert result["revision_provenance"]["truncated"] is True
    assert "Showing 30 of 35 verified pinned revisions" in html(db)


def test_f03_selected_estimates_equal_canonical_by_outcome_and_original_view(migrated_db):
    db = migrated_db
    clone(
        db,
        WinnerProbabilityEstimate,
        81007,
        id=89999,
        source_version="published-v2",
        created_at=db.get(WinnerProbabilityEstimate, 81007).created_at + timedelta(hours=1),
        point_probability=".81",
        lower_bound=".77",
        upper_bound=".85",
        interval_width=".08",
    )
    # Larger IDs/created times alone cannot override an earlier training cutoff.
    clone(
        db,
        WinnerProbabilityEstimate,
        89999,
        id=90000,
        source_version="older-training",
        training_cutoff_at=CUTOFF - timedelta(days=3),
        point_probability=".91",
    )
    clone(
        db,
        WinnerProbabilityEstimate,
        81007,
        id=90002,
        source_version="older-created",
        point_probability=".92",
    )
    clone(
        db,
        WinnerProbabilityEstimate,
        89999,
        id=90001,
        source_version="current-rescore",
        estimate_kind="LATEST_RESCORE",
        point_probability=".97",
    )
    clone(
        db,
        WinnerOutcomeDefinition,
        8000,
        id=8001,
        definition_id="OTHER_H10",
        is_primary=False,
        horizon_sessions=10,
    )
    clone(
        db,
        WinnerProbabilityEstimate,
        81007,
        id=89998,
        outcome_definition_id=8001,
        source_version="other-outcome",
        point_probability=".22",
    )
    result = data(db, "winner")
    entries = result["items"][0]["estimates"]
    assert len(entries) == 2  # One canonical selection per outcome, no unlabelled alternatives.
    for entry in entries:
        canonical = WinnerProbabilityApiService()._selected_estimate(
            db,
            prediction_id=80007,
            outcome_definition_id=entry["outcome"]["id"],
            query=WinnerProbabilityApiQuery(),
        )
        assert entry["estimate"]["id"] == canonical.id
        assert entry["estimate"]["point_probability"] == float(canonical.point_probability)
    assert entries[0]["estimate"]["id"] == 89999
    assert entries[0]["estimate"]["lower_bound"] == 0.77
    assert entries[0]["estimate"]["upper_bound"] == 0.85
    assert "81%" in result["summary"]
    assert "41%" in data(db, "winner", 6)["summary"]


def guidance(db, guidance_id, *, source_id=88880, **overrides):
    if not db.get(CeriSourceRecord, source_id):
        insert(
            db,
            CeriSourceRecord,
            id=source_id,
            provider="review",
            dataset="guidance",
            provider_record_id=str(source_id),
            content_hash=str(source_id),
            idempotency_key=str(source_id),
            observed_at=CUTOFF,
            ingested_at=CUTOFF,
        )
    insert(
        db,
        CeriGuidanceEvent,
        **(
            dict(
                id=guidance_id,
                source_record_id=source_id,
                company_id=8000,
                action="RAISED",
                metric="EPS_DILUTED",
                period_type="FY",
                confidence="High",
                effective_at=CUTOFF,
                effective_session=None,
                accepted_for_scoring=True,
            )
            | overrides
        ),
    )


def test_f05_guidance_matches_established_reader_with_visibility_and_supersession(migrated_db):
    db = migrated_db
    cutoff = CUTOFF + timedelta(days=2)
    guidance(db, 88881)
    result = data(db)
    assert result["guidance"]["selected"]["id"] == 88881
    canonical_page = CeriQueryService().latest(db, CeriListQuery(CeriQueryFilters(ticker="EOG")))
    assert canonical_page["items"][0]["guidance"]["selected"]["id"] == 88881
    assert result["guidance"] == _guidance_summary(db, 8000, cutoff, include_rejected=False)
    guidance(
        db,
        88882,
        effective_session=cutoff.date(),
        effective_at=CUTOFF + timedelta(days=1),
        supersedes_id=88881,
    )
    assert data(db)["guidance"]["selected"]["id"] == 88882
    guidance(db, 88883, effective_at=cutoff + timedelta(seconds=1), supersedes_id=88882)
    guidance(db, 88884, effective_session=(cutoff + timedelta(days=1)).date())
    guidance(
        db, 88885, effective_at=cutoff, accepted_for_scoring=False, rejection_reason="REJECTED"
    )
    guidance(db, 88886, effective_at=cutoff, expires_at=cutoff - timedelta(seconds=1))
    guidance(db, 88887, effective_at=cutoff, source_id=88888)
    db.execute(
        CeriSourceRecord.__table__.update()
        .where(CeriSourceRecord.id == 88888)
        .values(quarantine_reason="rejected source")
    )
    assert data(db)["guidance"]["selected"]["id"] == 88882
    # At the earlier historical source cutoff, the superseding version is not yet effective.
    assert _guidance_summary(db, 8000, CUTOFF, include_rejected=False)["selected"]["id"] == 88881
    db.execute(
        CeriGuidanceEvent.__table__.update()
        .where(CeriGuidanceEvent.id == 88882)
        .values(expires_at=cutoff - timedelta(seconds=1))
    )
    assert data(db)["guidance"]["selected"] is None  # Do not resurrect superseded 88881.


def test_f06_recent_detail_skips_aggregates_and_preserves_full_contract(migrated_db):
    db = migrated_db
    statements = []

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.upper().split()))

    event.listen(db.get_bind(), "before_cursor_execute", before)
    try:
        result = data(db)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", before)
    assert result["changes"]["items"][0]["id"] == 9100
    assert result["alerts"]["items"][0]["id"] == 9300
    assert not [
        s
        for s in statements
        if s.startswith("SELECT COUNT(") and ("CERI_CHANGE_EVENTS" in s or "CERI_ALERT_EVENTS" in s)
    ]
    assert not [s for s in statements if "PAIR_COUNT" in s]
    query = CeriListQuery(CeriQueryFilters(ticker="EOG"), sort="created_at")
    full = CeriQueryService().changes(db, query)
    assert full["total"] == 1 and "comparison_context" in full
    assert CeriQueryService().alerts(db, query)["total"] == 1
