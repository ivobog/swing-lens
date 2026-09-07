from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select

from app.db import SessionLocal
from app.models.tables import BackgroundJob, BackgroundJobEnqueueAttempt
from app.observability.correlation import root_action_scope, worker_job_scope
from app.services.background_job_service import enqueue_job

pytestmark = pytest.mark.integration


def test_postgresql_root_child_coalescing_fixture() -> None:
    suffix = uuid4().hex
    db = SessionLocal()
    try:
        with root_action_scope("HTTP", "POST /fixture", request_id=f"request-{suffix}"):
            parent = enqueue_job(
                db,
                "OBSERVABILITY_FIXTURE_ROOT",
                {},
                request_key=f"root-{suffix}",
                coalesce=True,
            )
            with worker_job_scope(parent):
                child = enqueue_job(
                    db,
                    "OBSERVABILITY_FIXTURE_CHILD",
                    {},
                    request_key=f"child-{suffix}",
                    coalesce=True,
                )
                authoritative = enqueue_job(
                    db,
                    "OBSERVABILITY_FIXTURE_CHILD",
                    {},
                    request_key=f"child-{suffix}",
                    coalesce=True,
                )
        db.flush()
        attempts = db.scalars(
            select(BackgroundJobEnqueueAttempt)
            .where(BackgroundJobEnqueueAttempt.root_correlation_id == parent.root_correlation_id)
            .order_by(BackgroundJobEnqueueAttempt.id)
        ).all()

        assert authoritative.id == child.id
        assert child.parent_job_id == parent.id
        assert child.triggered_by_job_id == parent.id
        assert child.root_correlation_id == parent.root_correlation_id
        assert [row.result for row in attempts] == ["CREATED", "CREATED", "COALESCED"]
        assert attempts[-1].authoritative_job_id == child.id
    finally:
        db.rollback()
        db.close()


def test_concurrent_enqueue_coalesces_to_one_authoritative_job() -> None:
    suffix = uuid4().hex
    request_key = f"concurrent-{suffix}"
    barrier = Barrier(2)
    job_ids: list[int] = []
    errors: list[BaseException] = []

    def enqueue() -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            with root_action_scope("ADMINISTRATIVE", "concurrency-fixture"):
                job = enqueue_job(
                    db,
                    "OBSERVABILITY_CONCURRENT_FIXTURE",
                    {},
                    request_key=request_key,
                    run_after=datetime.now(UTC) + timedelta(days=1),
                )
            db.commit()
            job_ids.append(int(job.id))
        except BaseException as exc:  # pragma: no cover - reported in parent thread
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    threads = [Thread(target=enqueue), Thread(target=enqueue)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(job_ids) == 2
    assert len(set(job_ids)) == 1

    with SessionLocal.begin() as cleanup:
        cleanup.execute(
            delete(BackgroundJobEnqueueAttempt).where(
                BackgroundJobEnqueueAttempt.request_key == request_key
            )
        )
        cleanup.execute(delete(BackgroundJob).where(BackgroundJob.id == job_ids[0]))


def test_causality_migration_columns_foreign_keys_and_indexes_exist() -> None:
    inspector = inspect(SessionLocal.kw["bind"])
    columns = {row["name"] for row in inspector.get_columns("background_jobs")}
    assert {
        "root_correlation_id",
        "causation_id",
        "parent_job_id",
        "trigger_kind",
        "trigger_name",
        "triggered_by_request_id",
        "triggered_by_job_id",
        "fanout_group_id",
        "coalesced_into_job_id",
    } <= columns
    indexes = {row["name"] for row in inspector.get_indexes("background_jobs")}
    assert {
        "idx_background_jobs_root_correlation_id",
        "idx_background_jobs_parent_job_id",
        "idx_background_jobs_triggered_by_job_id",
        "idx_background_jobs_fanout_group_id",
    } <= indexes
    assert inspector.has_table("background_job_enqueue_attempts")

    for table_name, index_name in (
        ("ceri_processing_runs", "ix_ceri_processing_runs_root"),
        ("ceri_provider_request_telemetry", "ix_ceri_provider_telemetry_root"),
    ):
        downstream_columns = {row["name"] for row in inspector.get_columns(table_name)}
        assert {
            "root_correlation_id",
            "causation_id",
            "background_job_id",
            "triggered_by_request_id",
        } <= downstream_columns
        downstream_indexes = {row["name"] for row in inspector.get_indexes(table_name)}
        assert index_name in downstream_indexes
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert any(
            key["referred_table"] == "background_jobs"
            and key["constrained_columns"] == ["background_job_id"]
            for key in foreign_keys
        )
