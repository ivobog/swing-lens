"""Native claims, leases and PostgreSQL broker handoff across real processes."""

import time
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, BackgroundWorker
from app.services.background_job_service import (
    JobLeaseLost,
    claim_next_job,
    record_job_progress,
)
from app.services.background_worker import run_worker_once
from app.services.ib_acquisition_gate import (
    AcquisitionCancelled,
    HistoricalSlot,
    coordinate_fetch,
    historical_request,
)
from app.services.worker_registry import register_worker
from app.settings import Settings


@pytest.fixture
def gate_engine(disposable_postgres_database):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    yield engine
    engine.dispose()


def test_old_generation_can_retire_before_guarded_upgrade(disposable_postgres_database):
    from app.services.worker_registry import retire_worker_registration

    config = Config("alembic.ini")
    run_guarded_alembic_upgrade(config, disposable_postgres_database, "0093_market_sector_rotation")
    engine = create_engine(disposable_postgres_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO background_workers (worker_id,queues_json,hostname,process_id,"
                    "instance_id,process_started_at,generation,started_at,heartbeat_at) VALUES "
                    "('old-worker','[]','fixture',99999999,'old-instance',CURRENT_TIMESTAMP,1,"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
        with Session(engine) as db:
            worker = retire_worker_registration(
                db,
                worker_id="old-worker",
                expected_instance_id="old-instance",
                expected_generation=1,
                expected_process_id=99999999,
            )
            assert worker is not None and worker.stopping_at is not None
            assert "operation_profile" not in worker.__dict__
            db.commit()
        run_guarded_alembic_upgrade(config, disposable_postgres_database)
        with Session(engine) as db:
            assert db.get(BackgroundWorker, "old-worker").operation_profile == "general"
    finally:
        engine.dispose()


def _process_worker(url, role, acquired, release, later, finished):
    settings = Settings(
        _env_file=None,
        database_url=url,
        job_worker_profile=role,
        ib_min_seconds_between_requests=0.1,
        ib_intelligence_historical_min_spacing_seconds=0.1,
    )
    engine = create_engine(url)

    def handler(db, job):
        def progress(progress_db, **values):
            record_job_progress(
                progress_db, job_id=job.id, execution_token=job.execution_token, **values
            )

        @coordinate_fetch
        def acquire(
            db, plan, settings, should_cancel, progress_callback, execution_job_id, execution_token
        ):
            with historical_request(settings):
                acquired.set()
                if role == "general":
                    assert release.wait(60)
            return {}

        acquire(
            db,
            SimpleNamespace(items=[None]),
            settings,
            lambda: job._heartbeat() or False,
            progress,
            job.id,
            job.execution_token,
        )
        if role == "general":
            progress(db, stage="CALCULATING_TECHNICALS", processed=0, total=1)
            db.commit()
            later.set()
            assert finished.wait(60)
        else:
            finished.set()
        return {"status": "COMPLETED"}

    try:
        assert run_worker_once(
            worker_id=role,
            operation_profile=role,
            worker_instance_id=role + "-instance",
            queues=("broker",)
            if role == "sector_refresh"
            else ("interactive", "broker", "background"),
            stale_after_seconds=90,
            heartbeat_interval_seconds=0.2,
            session_factory=sessionmaker(bind=engine),
            handlers={"FULL_PIPELINE": handler, "IB_FETCH": handler},
            fairness_enabled=True,
        )
    finally:
        engine.dispose()


def test_original_ordering_sector_runs_while_pipeline_calculates_and_no_wait_locks(gate_engine):
    now = datetime.now(UTC)
    with Session(gate_engine) as db:
        db.add_all(
            [
                BackgroundJob(
                    id=1645,
                    job_type="SEC_READINESS_REPAIR",
                    status="COMPLETED",
                    payload_json={},
                    created_at=now - timedelta(seconds=200),
                ),
                BackgroundJob(
                    id=1646,
                    job_type="IB_FETCH",
                    status="QUEUED",
                    priority=100,
                    payload_json={"msi_refresh": "msi-sector-refresh-v1"},
                    created_at=now - timedelta(seconds=175),
                ),
                BackgroundJob(
                    id=1647,
                    job_type="FULL_PIPELINE",
                    status="QUEUED",
                    priority=50,
                    payload_json={},
                    created_at=now,
                ),
            ]
        )
        from app.services.configuration_delivery import (
            anchor_enqueue_payload,
            bind_job_configuration,
        )

        db.flush()
        for identity in (1646, 1647):
            job = db.get(BackgroundJob, identity)
            job.payload_json = anchor_enqueue_payload(db, job.job_type, job.payload_json)
            bind_job_configuration(db, job)
        db.commit()
    ctx = get_context("spawn")
    broker, sector, release, later, finished = [ctx.Event() for _ in range(5)]
    url = gate_engine.url.render_as_string(hide_password=False)
    normal = ctx.Process(
        target=_process_worker, args=(url, "general", broker, release, later, finished)
    )
    constrained = ctx.Process(
        target=_process_worker, args=(url, "sector_refresh", sector, release, later, finished)
    )
    normal.start()
    try:
        assert broker.wait(60)
        constrained.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with Session(gate_engine) as db:
                job = db.get(BackgroundJob, 1646)
                if job.progress_stage == "WAITING_FOR_IB_ACQUISITION":
                    assert job.worker_id == "sector_refresh" and job.execution_token
                    sequence, last_progress = job.progress_sequence, job.last_progress_at
                    break
            time.sleep(0.05)
        else:
            pytest.fail("sector did not publish its broker wait")
        assert not sector.is_set()
        time.sleep(0.5)
        with Session(gate_engine) as db:
            job = db.get(BackgroundJob, 1646)
            assert (job.progress_sequence, job.last_progress_at) == (sequence, last_progress)
            assert job.lease_expires_at > datetime.now(UTC)
            # A wait cannot own the job-row lock or retain a domain transaction.
            db.execute(text("SET LOCAL lock_timeout='2s'"))
            db.execute(text("SELECT id FROM background_jobs WHERE id IN (1646,1647) FOR UPDATE"))
            db.rollback()
        release.set()
        assert later.wait(60) and sector.wait(60) and finished.wait(60)
        normal.join(60)
        constrained.join(60)
        assert normal.exitcode == constrained.exitcode == 0
        with Session(gate_engine) as db:
            assert [db.get(BackgroundJob, value).status for value in (1646, 1647)] == [
                "COMPLETED"
            ] * 2
            assert db.get(BackgroundWorker, "sector_refresh").operation_profile == "sector_refresh"
            requests = db.execute(
                text("SELECT requests_json FROM ib_historical_gate WHERE id=1")
            ).scalar_one()
            assert len(requests) == 2 and requests[1] - requests[0] >= 0.1
    finally:
        release.set()
        finished.set()
        for process in (normal, constrained):
            if process.pid:
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)


def test_exact_marker_routing_preserves_unrelated_ib_fetch_and_activation(gate_engine):
    with Session(gate_engine) as db:
        for name, profile in (("sector", "sector_refresh"), ("normal", "general")):
            register_worker(
                db,
                worker_id=name,
                operation_profile=profile,
                queues=("broker", "interactive", "background"),
                heartbeat_timeout_seconds=90,
            )
        jobs = [
            BackgroundJob(job_type=kind, status="QUEUED", payload_json=payload)
            for kind, payload in (
                ("IB_FETCH", {"msi_refresh": "msi-sector-refresh-v1"}),
                ("IB_FETCH", {}),
                ("IB_FETCH", {"msi_refresh": "other"}),
                ("FULL_PIPELINE", {"msi_refresh": "msi-sector-refresh-v1"}),
            )
        ]
        db.add_all(jobs)
        db.commit()
        first = claim_next_job(db, "sector")
        assert first.id == jobs[0].id
        db.commit()
        assert claim_next_job(db, "sector") is None
        db.commit()
        remaining = []
        for _ in range(3):
            job = claim_next_job(db, "normal")
            remaining.append(job.id)
            db.commit()
        assert set(remaining) == {job.id for job in jobs[1:]}


def test_ownership_loss_and_cancellation_never_steal_live_socket_or_reset_pacing(gate_engine):
    settings = Settings(
        _env_file=None,
        ib_acquisition_wait_seconds=1,
        ib_min_seconds_between_requests=0.1,
        ib_intelligence_historical_min_spacing_seconds=0.1,
    )
    with Session(gate_engine) as db:
        db.add(
            BackgroundJob(
                id=90,
                job_type="IB_FETCH",
                status="RUNNING",
                payload_json={},
                execution_token="old",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=90),
            )
        )
        db.commit()
    old = HistoricalSlot(gate_engine, settings, job_id=90, execution_token="old")
    old.acquire_historical_slot()
    try:
        old.pace()
        with gate_engine.begin() as connection:
            connection.execute(text("UPDATE background_jobs SET execution_token='new' WHERE id=90"))
        with pytest.raises(JobLeaseLost):
            old.pace()
        with pytest.raises(AcquisitionCancelled):
            HistoricalSlot(gate_engine, settings, check=lambda: True).acquire_historical_slot()
        with pytest.raises(TimeoutError):
            HistoricalSlot(gate_engine, settings).acquire_historical_slot()
    finally:
        old.release_historical_slot()  # models the native disconnect acknowledgement
    new = HistoricalSlot(gate_engine, settings)
    new.acquire_historical_slot()
    try:
        new.pace()
        with gate_engine.connect() as connection:
            ledger = connection.scalar(
                text("SELECT requests_json FROM ib_historical_gate WHERE id=1")
            )
        assert len(ledger) == 2 and ledger[1] - ledger[0] >= 0.1
    finally:
        new.release_historical_slot()


def _hold_slot_until_death(url, entered):
    engine = create_engine(url)
    slot = HistoricalSlot(engine, Settings(_env_file=None))
    slot.acquire_historical_slot()
    slot.pace()
    entered.set()
    while True:
        time.sleep(0.1)


def test_process_death_releases_capacity_but_preserves_request_budget(gate_engine):
    context = get_context("spawn")
    entered = context.Event()
    child = context.Process(
        target=_hold_slot_until_death,
        args=(gate_engine.url.render_as_string(hide_password=False), entered),
    )
    child.start()
    try:
        assert entered.wait(60)
        blocked = HistoricalSlot(
            gate_engine, Settings(_env_file=None, ib_acquisition_wait_seconds=1)
        )
        with pytest.raises(TimeoutError):
            blocked.acquire_historical_slot()
        child.terminate()
        child.join(10)
        assert not child.is_alive()
        recovered = HistoricalSlot(gate_engine, Settings(_env_file=None))
        recovered.acquire_historical_slot()
        try:
            with gate_engine.connect() as connection:
                retained = connection.scalar(
                    text("SELECT requests_json FROM ib_historical_gate WHERE id=1")
                )
            assert len(retained) == 1
        finally:
            recovered.release_historical_slot()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)


def test_ibmi_transport_uses_same_gate_and_weighted_ledger(gate_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.services.ib_market_intelligence.adapters import IBHistoricalMetricClient

    settings = Settings(
        _env_file=None,
        ib_min_seconds_between_requests=0,
        ib_intelligence_historical_min_spacing_seconds=0,
    )
    sent = Event()

    class IB:
        def reqHistoricalData(self, *_args, **_kwargs):
            sent.set()
            return []

    holder = HistoricalSlot(gate_engine, settings)
    holder.acquire_historical_slot()
    holder.pace()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                IBHistoricalMetricClient(
                    IB(), settings=settings, acquisition_engine=gate_engine
                ).fetch,
                SimpleNamespace(symbol="SPY", conId=1),
                "BID_ASK",
                duration="1 D",
            )
            assert not sent.wait(0.5)
            holder.release_historical_slot()
            assert future.result(10) == [] and sent.is_set()
        with gate_engine.connect() as connection:
            ledger = connection.scalar(
                text("SELECT requests_json FROM ib_historical_gate WHERE id=1")
            )
        assert len(ledger) == 3 and ledger[-1] == ledger[-2]
    finally:
        holder.release_historical_slot()
