"""Bounded PostgreSQL migration, retained-ledger and backlog containment contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_market_sector_intelligence_rotation_postgresql import retained_schema

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import (
    BackgroundJob,
    ConsumerEligibilityDecisionRecord,
    MarketCalculationContext,
    PipelineRun,
    UploadRun,
)
from app.services.alembic_heads import schema_is_at_head
from app.services.background_worker import (
    _certification_pipeline_run_ids,
    run_worker_once,
)
from app.services.consumer_eligibility_ledger import (
    EligibilityShadowEvaluation,
    WinnerAcquisitionDecision,
    WinnerAcquisitionOutcome,
    persist_consumer_decision,
    persist_shadow_evaluation,
    persist_winner_acquisition_decision,
)
from app.services.eligibility_policy import (
    AnalyticalConfidence,
    CompletenessStatus,
    ConsumerEligibilityOutcome,
    ExecutionOutcome,
    IntegrityStatus,
    OrthogonalConsumerDecision,
)
from app.services.pipeline_dependency_service import reconcile_safe_pipeline_invariants
from app.services.warning_taxonomy import classify_warnings

pytestmark = pytest.mark.integration
HEAD = "0093_market_sector_rotation"
PRIOR = "0092_phase2_eligibility_ledgers"
NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def decision():
    return OrthogonalConsumerDecision(
        producer="COMBINED", consumer="WINNER", consumer_edge="COMBINED_TO_WINNER",
        policy_version="operational-fixture-v1", source_identity_fingerprint="a" * 64,
        source_evidence_id=None, decision_cutoff=NOW,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        integrity_status=IntegrityStatus.CERTIFIED,
        completeness_status=CompletenessStatus.COMPLETE,
        analytical_confidence=AnalyticalConfidence.LOW,
        warnings=classify_warnings(["high_leverage"]),
        eligibility=ConsumerEligibilityOutcome.INCLUDED,
        reason_codes=("classified_risk_warning",),
    )


def test_complete_chain_preserves_ledger_data_and_recovers_transactional_failure(
    disposable_postgres_database,
):
    cfg = Config("alembic.ini")
    revisions = ScriptDirectory.from_config(cfg)
    assert revisions.get_heads() == [HEAD]
    assert revisions.get_revision(HEAD).down_revision == PRIOR
    assert revisions.get_revision(PRIOR).down_revision == "0091_readiness_constraints"
    run_guarded_alembic_upgrade(cfg, disposable_postgres_database, PRIOR)
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine) as db:
            db.add(UploadRun(id=1, filename="representative.csv", status="COMPLETED"))
            db.flush()
            d = decision()
            persist_consumer_decision(
                db, decision=d, source_artifact_kind="COMBINED",
                pipeline_run_id=None, upload_run_id=1,
            )
            persist_winner_acquisition_decision(db, WinnerAcquisitionDecision(
                upload_run_id=1, ticker="ACME", decision_cutoff=NOW,
                outcome=WinnerAcquisitionOutcome.EXCLUDED,
                source_identities={"combined": "a" * 64},
                source_decisions=(d.canonical_payload(),),
            ))
            persist_shadow_evaluation(db, EligibilityShadowEvaluation(
                consumer_edge=d.consumer_edge, policy_version="shadow-fixture-v1",
                source_identity_fingerprint=d.source_identity_fingerprint,
                decision_cutoff=NOW, legacy_outcome=ConsumerEligibilityOutcome.UNDECIDED,
                proposed_decision=d, frozen_input={"representative": True}, upload_run_id=1,
            ))
            db.commit()
        with engine.connect() as c:
            tables = set(inspect(c).get_table_names())
            schema = retained_schema(c, tables)
            ledger = {t: list(c.execute(text(f"SELECT * FROM {t} ORDER BY id"))) for t in (
                "consumer_eligibility_decisions", "winner_acquisition_decisions",
                "eligibility_shadow_evaluations",
            )}
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE market_sector_rotation_rows (failure_fixture int)")
        with pytest.raises(DBAPIError, match="already exists"):
            run_guarded_alembic_upgrade(cfg, disposable_postgres_database, HEAD)
        with engine.begin() as c:
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == PRIOR
            assert "market_sector_rotation_snapshots" not in inspect(c).get_table_names()
            c.exec_driver_sql("DROP TABLE market_sector_rotation_rows")
        run_guarded_alembic_upgrade(cfg, disposable_postgres_database, HEAD)
        run_guarded_alembic_upgrade(cfg, disposable_postgres_database, HEAD)
        with engine.connect() as c:
            assert schema_is_at_head(c)
            assert retained_schema(c, tables) == schema
            for t, rows in ledger.items():
                assert list(c.execute(text(f"SELECT * FROM {t} ORDER BY id"))) == rows
        for t in ledger:
            for mutation in (f"UPDATE {t} SET id=id", f"DELETE FROM {t}"):
                with pytest.raises(DBAPIError, match="APPEND_ONLY"), engine.begin() as c:
                    c.exec_driver_sql(mutation)
        with Session(engine) as db:
            row = db.scalar(select(ConsumerEligibilityDecisionRecord))
            row.eligibility = "UNDECIDED"
            with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
                db.flush()
    finally:
        engine.dispose()


def test_certification_poll_does_not_recover_claim_or_reconcile_legacy_backlog(
    disposable_postgres_database,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database, HEAD)
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory() as db:
            db.add(UploadRun(id=1, filename="old.csv", status="COMPLETED"))
            db.flush()
            db.add(PipelineRun(
                id=1, upload_run_id=1, status="CANCEL_REQUESTED",
                execution_authority_state="ACTIVE",
            ))
            db.flush()
            db.add(MarketCalculationContext(
                pipeline_run_id=1, upload_run_id=1, cutoff_at=NOW - timedelta(days=22),
                exchange_timezone="America/New_York",
                latest_completed_session=datetime(2026, 9, 30).date(),
                calendar_version="swinglens-us-equities-v1",
                bar_readiness_version="daily-close-plus-15m-v1", cutoff_reason="FROZEN_OLD",
            ))
            for index in range(50):
                db.add(BackgroundJob(
                    id=index + 1, job_type="CERI_PROVIDER_INGEST_BATCH", related_run_id=1,
                    status="RUNNING" if index == 0 else "QUEUED",
                    worker_id="old-worker" if index == 0 else None,
                    worker_instance_id="old-generation" if index == 0 else None,
                    lease_expires_at=NOW - timedelta(days=22) if index == 0 else None,
                    heartbeat_at=NOW - timedelta(days=22) if index == 0 else None,
                    run_after=NOW - timedelta(days=22), payload_json={"run_id": 1},
                ))
            db.commit()
        with engine.connect() as c:
            before = {t: list(c.execute(text(f"SELECT * FROM {t} ORDER BY id"))) for t in (
                "background_jobs", "pipeline_runs", "market_calculation_contexts",
            )}

        def provider_forbidden(*_args, **_kwargs):
            raise AssertionError("A legacy provider handler was reached")

        for _ in range(2):
            assert run_worker_once(
                worker_id="new-contained-worker", worker_instance_id="new-generation",
                stale_after_seconds=60, session_factory=factory,
                certification_mode=True, certification_session_id="fresh-epoch",
                sec_capability_required=False, schedule_winner_probability=False,
                handlers={"CERI_PROVIDER_INGEST_BATCH": provider_forbidden},
            ) is False
        with engine.connect() as c:
            for t, rows in before.items():
                assert list(c.execute(text(f"SELECT * FROM {t} ORDER BY id"))) == rows
        with factory() as db:
            assert _certification_pipeline_run_ids(db, "fresh-epoch") == ()
            assert reconcile_safe_pipeline_invariants(db, pipeline_run_ids=()) == {
                "children_enqueued": (), "continuations_enqueued": (), "cancellations": (),
            }
    finally:
        engine.dispose()


def test_scoped_reconciliation_and_roots_cannot_borrow_another_epoch(
    disposable_postgres_database,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database, HEAD)
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine) as db:
            db.add(UploadRun(id=1, filename="scope.csv", status="COMPLETED"))
            db.flush()
            db.add_all([PipelineRun(
                id=i, upload_run_id=1, status="CANCEL_REQUESTED",
                execution_authority_state="ACTIVE",
            ) for i in (1, 2)])
            db.flush()
            db.add_all([BackgroundJob(
                job_type="FULL_PIPELINE", status="QUEUED", related_run_id=1,
                payload_json={"pipeline_run_id": i, "certification_authorized": True,
                              "certification_session_id": epoch},
            ) for i, epoch in [(1, "old-epoch"), (2, "new-epoch")]])
            db.commit()
            assert _certification_pipeline_run_ids(db, "new-epoch") == (2,)
            report = reconcile_safe_pipeline_invariants(db, pipeline_run_ids=(2,))
            assert report["cancellations"] == (2,)
            db.commit()
            assert db.get(PipelineRun, 1).status == "CANCEL_REQUESTED"
            assert db.get(PipelineRun, 2).status == "CANCELLED"
    finally:
        engine.dispose()
