from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import (
    MarketCalculationContext,
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SetupSignalSnapshotSelectionEvent,
    TechnicalScore,
    TransitionPreflightPlan,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.market_calculation_context_service import (
    cutoff_from_row,
    market_calculation_context_fingerprint,
)
from app.services.pipeline_service import start_pipeline
from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateDiscoveryService,
    TransitionCandidateResult,
)
from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    create_transition_preflight_plan,
    expire_abandoned_preflights,
    verify_transition_preflight_for_enqueue,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CUTOFF = datetime(2026, 9, 8, 20, 30, tzinfo=UTC)


class MutableDiscovery:
    def __init__(self) -> None:
        self.pointer_revision = 1
        self.evidence_revision = 1

    def discover_for_run(self, _db, _run_id, *, market_cutoff, tickers=None):
        ticker = sorted(tickers or {"MSFT"})[0]
        evidence = CanonicalEvidenceSerializer.fingerprint(
            {
                "context_id": market_cutoff.context_id,
                "cutoff_at": market_cutoff.cutoff_at,
                "pointer_revision": self.pointer_revision,
                "evidence_revision": self.evidence_revision,
            }
        )
        return [
            TransitionCandidateResult(
                ticker=ticker,
                prospective_cutoff=market_cutoff.cutoff_at,
                prospective_latest_completed_session=market_cutoff.latest_completed_session,
                latest_reconstructable_session=market_cutoff.latest_completed_session,
                current_pointer_key=f"{ticker}/1d/2026-09-04",
                current_pointer_snapshot_id=10,
                current_pointer_target_session=date(2026, 9, 4),
                prospective_new_key=(
                    f"{ticker}/1d/{market_cutoff.latest_completed_session.isoformat()}"
                ),
                can_compete_with_existing_pointer=False,
                would_initialize_new_key=True,
                predicted_pointer_advance=False,
                predicted_current_state_advance=True,
                reason="NEW_SESSION_CANONICAL_INITIALIZATION_ADVANCES_CURRENT_STATE",
                confidence="HIGH",
                market_calculation_context_id=market_cutoff.context_id,
                expected_latest_pointer_revision=self.pointer_revision,
                technical_reconstruction_fingerprint=f"technical-{self.evidence_revision}",
                evidence_fingerprint=evidence,
            )
        ]


def test_pointer_change_after_preflight_rejects_enqueue(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="pointer-cas",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            discovery=discovery,
        )
        db.commit()
        plan_id = plan.id

    discovery.pointer_revision = 2
    with Session(engine) as db:
        with pytest.raises(TransitionPreflightError, match="PRECONDITION_CHANGED"):
            verify_transition_preflight_for_enqueue(
                db,
                plan_id=plan_id,
                upload_run_id=run_id,
                discovery=discovery,
            )
        db.rollback()
        assert db.scalar(select(text("count(*)")).select_from(SetupSignalSnapshot)) == 0


def test_preflight_idempotency_key_rejects_materially_different_request(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine) as db:
        run_id = _insert_upload(db)
        first = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="semantic-idempotency",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            discovery=discovery,
        )
        db.commit()
        first_id = first.id

    with Session(engine) as db:
        equivalent = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="semantic-idempotency",
            cutoff_at=CUTOFF.astimezone(ZoneInfo("Europe/Zurich")),
            tickers={"msft"},
            discovery=discovery,
        )
        assert equivalent.id == first_id
        with pytest.raises(TransitionPreflightError) as caught:
            create_transition_preflight_plan(
                db,
                upload_run_id=run_id,
                idempotency_key="semantic-idempotency",
                cutoff_at=CUTOFF + timedelta(microseconds=1),
                tickers={"MSFT"},
                discovery=discovery,
            )
        assert caught.value.code == "IDEMPOTENCY_CONFLICT"


def test_session_boundary_reuses_reserved_context_and_duplicate_enqueue_is_idempotent(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="boundary-reuse",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            expires_in=timedelta(days=2),
            discovery=discovery,
        )
        db.commit()
        plan_id = plan.id
        context_id = plan.market_calculation_context_id

    with Session(engine) as db:
        first = start_pipeline(
            db,
            run_id,
            transition_preflight_plan_id=plan_id,
            transition_candidate_discovery=discovery,
        )
        db.commit()
        first_id = first.id

    with Session(engine) as db:
        duplicate = start_pipeline(
            db,
            run_id,
            transition_preflight_plan_id=plan_id,
            transition_candidate_discovery=discovery,
        )
        assert duplicate.id == first_id
        context = db.get(MarketCalculationContext, context_id)
        assert context.pipeline_run_id == first_id
        assert context.cutoff_at == CUTOFF
        plan = db.get(TransitionPreflightPlan, plan_id)
        assert plan.status == "CONSUMED"
        assert plan.pipeline_run_id == first_id


@pytest.mark.parametrize("session_timezone", ["UTC", "Europe/Zurich", "America/New_York"])
def test_preflight_fingerprints_survive_postgresql_session_timezone_roundtrip(
    disposable_postgres_database: str,
    session_timezone: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    instant = datetime.fromisoformat("2026-09-09T19:55:33.682959+00:00")
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key=f"timezone-{session_timezone}",
            cutoff_at=instant,
            tickers={"MSFT"},
            expires_in=timedelta(days=2),
            discovery=discovery,
        )
        original_context = cutoff_from_row(
            db.get(MarketCalculationContext, plan.market_calculation_context_id)
        )
        original_fingerprint = market_calculation_context_fingerprint(original_context)
        plan_id = plan.id
        db.commit()

    with Session(engine) as db:
        db.execute(text(f"SET LOCAL TIME ZONE '{session_timezone}'"))
        plan = db.get(TransitionPreflightPlan, plan_id)
        reloaded = cutoff_from_row(
            db.get(MarketCalculationContext, plan.market_calculation_context_id)
        )
        assert reloaded.cutoff_at == instant.astimezone(ZoneInfo(session_timezone))
        assert reloaded.cutoff_at.tzinfo == UTC
        assert market_calculation_context_fingerprint(reloaded) == original_fingerprint
        verified = verify_transition_preflight_for_enqueue(
            db,
            plan_id=plan_id,
            upload_run_id=run_id,
            discovery=discovery,
        )
        assert verified.plan.evidence_fingerprint == plan.evidence_fingerprint


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("CANCELLED", "PLAN_CANCELLED"),
        ("EXPIRED", "PLAN_EXPIRED"),
        ("STALE", "STALE_PREFLIGHT"),
    ],
)
def test_terminal_plan_states_have_deterministic_rejection_codes(
    disposable_postgres_database: str,
    status: str,
    code: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key=f"terminal-{status}",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            discovery=discovery,
        )
        plan.status = status
        db.commit()
        plan_id = plan.id

    with Session(engine) as db:
        with pytest.raises(TransitionPreflightError) as caught:
            start_pipeline(
                db,
                run_id,
                transition_preflight_plan_id=plan_id,
                transition_candidate_discovery=discovery,
            )
        assert caught.value.code == code
        assert db.execute(text("SELECT count(*) FROM pipeline_runs")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM background_jobs")).scalar_one() == 0


def test_two_concurrent_plan_consumers_create_exactly_one_pipeline(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="concurrent-consume",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            expires_in=timedelta(days=2),
            discovery=discovery,
        )
        db.commit()
        plan_id = plan.id

    barrier = Barrier(2)

    def consume() -> tuple[str, int | str]:
        with Session(engine) as db:
            barrier.wait(timeout=10)
            try:
                pipeline = start_pipeline(
                    db,
                    run_id,
                    transition_preflight_plan_id=plan_id,
                    transition_candidate_discovery=discovery,
                )
                db.commit()
                return "pipeline", pipeline.id
            except TransitionPreflightError as exc:
                db.rollback()
                return "error", exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            future.result(timeout=30) for future in (pool.submit(consume), pool.submit(consume))
        ]

    with Session(engine) as db:
        assert db.execute(text("SELECT count(*) FROM pipeline_runs")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM background_jobs")).scalar_one() == 1
        plan = db.get(TransitionPreflightPlan, plan_id)
        assert plan.status == "CONSUMED"
        pipeline_ids = {value for kind, value in outcomes if kind == "pipeline"}
        assert pipeline_ids == {plan.pipeline_run_id}
        assert all(kind == "pipeline" or value == "DUPLICATE_ENQUEUE" for kind, value in outcomes)


def test_pointer_and_ledger_roll_back_together_on_injected_failure(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        snapshot = _snapshot(run_id, 1, date(2026, 9, 8), "1d", hour=10)
        db.add(snapshot)
        db.commit()
        snapshot_id = snapshot.id

    with Session(engine) as db:
        try:
            SetupLifecycleCanonicalizer().canonicalize_run(
                db,
                run_id=run_id,
                snapshot_ids=(snapshot_id,),
            )
            raise RuntimeError("injected after pointer and ledger flush, before commit")
        except RuntimeError:
            db.rollback()

    with Session(engine) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM setup_signal_snapshot_current_selections")
            ).scalar_one()
            == 0
        )
        assert (
            db.execute(
                text("SELECT count(*) FROM setup_signal_snapshot_selection_events")
            ).scalar_one()
            == 0
        )

    with Session(engine) as db:
        SetupLifecycleCanonicalizer().canonicalize_run(
            db,
            run_id=run_id,
            snapshot_ids=(snapshot_id,),
        )
        db.commit()
    with Session(engine) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM setup_signal_snapshot_current_selections")
            ).scalar_one()
            == 1
        )
        assert (
            db.execute(
                text("SELECT count(*) FROM setup_signal_snapshot_selection_events")
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "failure_point", ["before_job_enqueue", "after_job_enqueue", "before_commit"]
)
def test_pipeline_enqueue_failure_injection_rolls_back_every_half_applied_state(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key=f"failure-{failure_point}",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            expires_in=timedelta(days=2),
            discovery=discovery,
        )
        db.commit()
        plan_id = plan.id
        context_id = plan.market_calculation_context_id

    def injected(*_args, **_kwargs):
        raise RuntimeError(f"injected {failure_point}")

    if failure_point == "before_job_enqueue":
        monkeypatch.setattr("app.services.pipeline_service.enqueue_job", injected)
    elif failure_point == "after_job_enqueue":
        monkeypatch.setattr(
            "app.services.pipeline_service.request_active_prewarm_preemption",
            injected,
        )

    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="injected"):
            start_pipeline(
                db,
                run_id,
                transition_preflight_plan_id=plan_id,
                transition_candidate_discovery=discovery,
            )
            if failure_point == "before_commit":
                raise RuntimeError("injected before_commit")
        db.rollback()

    with Session(engine) as db:
        plan = db.get(TransitionPreflightPlan, plan_id)
        context = db.get(MarketCalculationContext, context_id)
        assert plan.status == "RESERVED"
        assert plan.pipeline_run_id is None
        assert context.pipeline_run_id is None
        assert db.execute(text("SELECT count(*) FROM pipeline_runs")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM background_jobs")).scalar_one() == 0


@pytest.mark.parametrize("failure_point", ["after_context_reserved", "after_plan_persisted"])
def test_preflight_creation_failure_injection_leaves_no_orphan(
    disposable_postgres_database: str,
    failure_point: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()

    class FailingDiscovery:
        def discover_for_run(self, *_args, **_kwargs):
            raise RuntimeError("injected after_context_reserved")

    with Session(engine) as db:
        run_id = _insert_upload(db)
        db.commit()
        with pytest.raises(RuntimeError, match="injected"):
            create_transition_preflight_plan(
                db,
                upload_run_id=run_id,
                idempotency_key=f"creation-{failure_point}",
                cutoff_at=CUTOFF,
                tickers={"MSFT"},
                discovery=(
                    FailingDiscovery() if failure_point == "after_context_reserved" else discovery
                ),
            )
            raise RuntimeError("injected after_plan_persisted")
        db.rollback()

    with Session(engine) as db:
        assert db.execute(text("SELECT count(*) FROM transition_preflight_plans")).scalar_one() == 0
        assert (
            db.execute(text("SELECT count(*) FROM market_calculation_contexts")).scalar_one() == 0
        )


def test_failure_between_pointer_update_and_ledger_insert_rolls_back_both(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        snapshot = _snapshot(run_id, 1, date(2026, 9, 8), "1d", hour=10)
        db.add(snapshot)
        db.commit()
        snapshot_id = snapshot.id

    repository = SetupLifecycleRepository()

    def fail_event_key(*_parts: str) -> str:
        raise RuntimeError("injected before ledger insert")

    monkeypatch.setattr(repository, "stable_key", fail_event_key)
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="before ledger"):
            SetupLifecycleCanonicalizer(repository=repository).canonicalize_run(
                db,
                run_id=run_id,
                snapshot_ids=(snapshot_id,),
            )
        db.rollback()

    with Session(engine) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM setup_signal_snapshot_current_selections")
            ).scalar_one()
            == 0
        )
        assert (
            db.execute(
                text("SELECT count(*) FROM setup_signal_snapshot_selection_events")
            ).scalar_one()
            == 0
        )


def test_post_cutoff_evidence_change_rejects_instead_of_creating_c2(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="revision-guard",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            discovery=discovery,
        )
        db.commit()
        plan_id = plan.id
        context_id = plan.market_calculation_context_id

    discovery.evidence_revision = 2
    with Session(engine) as db:
        with pytest.raises(TransitionPreflightError, match="PRECONDITION_CHANGED"):
            start_pipeline(
                db,
                run_id,
                transition_preflight_plan_id=plan_id,
                transition_candidate_discovery=discovery,
            )
        db.rollback()
        context = db.get(MarketCalculationContext, context_id)
        assert context.pipeline_run_id is None
        assert db.execute(text("SELECT count(*) FROM pipeline_runs")).scalar_one() == 0


def test_abandoned_preflight_expires_without_business_state_or_lock_leak(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    discovery = MutableDiscovery()
    with Session(engine) as db:
        run_id = _insert_upload(db)
        plan = create_transition_preflight_plan(
            db,
            upload_run_id=run_id,
            idempotency_key="abandoned",
            cutoff_at=CUTOFF,
            tickers={"MSFT"},
            expires_in=timedelta(seconds=1),
            discovery=discovery,
        )
        db.commit()
        plan_id = plan.id
        context_id = plan.market_calculation_context_id
        expires_at = plan.expires_at

    with Session(engine) as db:
        assert expire_abandoned_preflights(db, now=expires_at + timedelta(minutes=1)) == 1
        db.commit()
        plan = db.get(TransitionPreflightPlan, plan_id)
        context = db.get(MarketCalculationContext, context_id)
        assert plan.status == "EXPIRED"
        assert context.pipeline_run_id is None
        assert db.scalar(select(text("count(*)")).select_from(TechnicalScore)) == 0
        assert db.scalar(select(text("count(*)")).select_from(SetupSignalSnapshot)) == 0


def test_session_canonical_keys_and_cross_session_current_state_are_distinct(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    first_session = date(2026, 9, 4)
    next_session = date(2026, 9, 8)
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        first = _snapshot(run_id, 1, first_session, "1d", hour=10)
        db.add(first)
        db.flush()
        canonicalizer = SetupLifecycleCanonicalizer()
        canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(first.id,))
        replacement = _snapshot(run_id, 2, first_session, "1d", hour=11)
        next_day = _snapshot(run_id, 3, next_session, "1d", hour=12)
        weekly = _snapshot(run_id, 4, next_session, "1w", hour=12)
        db.add_all([replacement, next_day, weekly])
        db.flush()
        canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(replacement.id,))
        canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(next_day.id,))
        canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(weekly.id,))
        db.commit()

    repository = SetupLifecycleRepository()
    with Session(engine) as db:
        assert (
            repository.historical_session_canonical_snapshot(
                db, ticker="MSFT", timeframe="1d", data_as_of_date=first_session
            ).id
            == replacement.id
        )
        assert (
            repository.current_cross_session_snapshot(db, ticker="MSFT", timeframe="1d").id
            == next_day.id
        )
        assert (
            repository.current_cross_session_snapshot(db, ticker="MSFT", timeframe="1w").id
            == weekly.id
        )
        assert (
            db.scalar(select(text("count(*)")).select_from(SetupSignalSnapshotCurrentSelection))
            == 3
        )
        assert set(db.scalars(select(SetupSignalSnapshotSelectionEvent.reason))) == {
            "NEW_KEY_INITIALIZATION",
            "SAME_SESSION_REPLACEMENT",
            "NEW_SESSION_CANONICAL_INITIALIZATION",
        }


def test_pointer_precondition_reads_current_admin_state_not_frozen_pit_cutoff(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    earlier = date(2026, 9, 4)
    later = date(2026, 9, 8)
    with Session(engine, expire_on_commit=False) as db:
        run_id = _insert_upload(db)
        first = _snapshot(run_id, 1, earlier, "1d", hour=10)
        second = _snapshot(run_id, 2, later, "1d", hour=11)
        db.add_all([first, second])
        db.flush()
        canonicalizer = SetupLifecycleCanonicalizer()
        canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(first.id,))
        canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(second.id,))
        db.commit()

    with Session(engine) as db:
        latest, exact, latest_revision, exact_revision = (
            TransitionCandidateDiscoveryService._pointers(
                db,
                ticker="MSFT",
                timeframe="1d",
                data_as_of_date=later,
            )
        )
        assert latest.id == second.id
        assert exact.id == second.id
        assert latest_revision == exact_revision == 1


def _insert_upload(db: Session) -> int:
    return int(
        db.execute(
            text(
                "INSERT INTO upload_runs (filename, status) "
                "VALUES ('preflight.csv', 'COMPLETED') RETURNING id"
            )
        ).scalar_one()
    )


def _snapshot(
    run_id: int,
    ordinal: int,
    as_of: date,
    timeframe: str,
    *,
    hour: int,
) -> SetupSignalSnapshot:
    calculated_at = datetime(2026, 9, 9, hour, tzinfo=UTC)
    return SetupSignalSnapshot(
        run_id=run_id,
        source_run_id_text=str(run_id),
        ticker="MSFT",
        timeframe=timeframe,
        data_as_of_date=as_of,
        calculated_at=calculated_at,
        captured_at=calculated_at,
        origin_type="LIVE_RUN",
        engine_version="test",
        config_version="test",
        config_hash="config-hash",
        source_data_hash=f"source-{ordinal}",
        schema_version="test",
        data_quality_label="HIGH",
        required_feature_coverage=1,
        warning_flags_json=[],
        source_lineage_json={"latest_bar": {"bar_date": as_of.isoformat()}},
    )


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")
