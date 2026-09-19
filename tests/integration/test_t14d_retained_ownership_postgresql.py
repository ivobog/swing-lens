"""Actual PostgreSQL lease locks remain required by batch reuse."""

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import bindparam, event, update
from sqlalchemy.orm import Session
from test_ceri_batched_workflow_v2 import _new_job

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobLeaseLost, JobStatus
from app.services.domain_write_fence import (
    assert_current_execution_ownership,
    fence_domain_commits,
    retained_execution_job_scope,
    retained_execution_ownership_scope,
)

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def _attempt(db):
    return _new_job(
        db,
        job_type="IB_FETCH",
        payload_json={},
        status=JobStatus.RUNNING,
        request_key="t14d-retained-ownership",
    )


def test_batch_reuses_one_actual_outer_lock_across_savepoints(contextual_engine):
    selects = []

    def record(_conn, _cursor, sql, _parameters, _context, _many):
        if (
            sql.lstrip().upper().startswith("SELECT")
            and "background_jobs" in sql
            and "FOR UPDATE" in sql
        ):
            selects.append(sql)

    with Session(contextual_engine, expire_on_commit=False) as db:
        job = _attempt(db)
        job_id, execution_token = job.id, job.execution_token
        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            with fence_domain_commits(job_id=job_id, execution_token=execution_token):
                with retained_execution_ownership_scope(db):
                    for _ in range(50):
                        savepoint = db.begin_nested()
                        from app.services.decision_mutation_authority import lock_decision_scope

                        lock_decision_scope(db, ("t14d-native-lock-retention", job_id))
                        db.execute(
                            update(BackgroundJob)
                            .where(BackgroundJob.id == job_id)
                            .values(progress_processed=1)
                        )
                        assert_current_execution_ownership(
                            db, job_id=job_id, execution_token=execution_token
                        )
                        savepoint.rollback()
                    with retained_execution_ownership_scope(db):
                        assert_current_execution_ownership(
                            db, job_id=job_id, execution_token=execution_token
                        )
                    db.commit()
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        assert len(selects) == 1
        assert "FOR UPDATE" in selects[0]


@pytest.mark.parametrize(
    "change",
    ["token", "status", "nested_token", "aliased_token", "physical_commit", "cte_token"],
)
def test_changed_attempt_cannot_reuse_a_prior_lock(contextual_engine, change):
    with Session(contextual_engine, expire_on_commit=False) as db:
        job = _attempt(db)
        job_id, execution_token = job.id, job.execution_token
        with fence_domain_commits(job_id=job_id, execution_token=execution_token):
            with retained_execution_ownership_scope(db):
                if change == "physical_commit":
                    db.connection().commit()
                    with contextual_engine.begin() as other:
                        other.execute(
                            update(BackgroundJob)
                            .where(BackgroundJob.id == job_id)
                            .values(execution_token="reclaimed-attempt")
                        )
                elif change == "cte_token":
                    reclaimed = (
                        update(BackgroundJob)
                        .where(BackgroundJob.id == job_id)
                        .values(execution_token="cte-reclaimed-attempt")
                        .returning(BackgroundJob.id)
                        .cte("reclaimed")
                    )
                    db.execute(
                        update(BackgroundJob)
                        .where(BackgroundJob.id == -1)
                        .values(progress_processed=1)
                        .add_cte(reclaimed)
                    )
                elif change == "status":
                    db.execute(
                        update(BackgroundJob)
                        .where(BackgroundJob.id == job_id)
                        .values(status=JobStatus.FAILED)
                    )
                elif change == "aliased_token":
                    target = BackgroundJob.__table__.alias("leased_attempt")
                    db.execute(
                        update(target)
                        .where(target.c.id == job_id)
                        .values(execution_token="aliased-replacement")
                    )
                else:
                    with retained_execution_ownership_scope(db):
                        db.execute(
                            update(BackgroundJob)
                            .where(BackgroundJob.id == job_id)
                            .values(execution_token=bindparam("replacement_attempt")),
                            {"replacement_attempt": "changed-own-SQL-attempt"},
                        )
                with pytest.raises(JobLeaseLost):
                    assert_current_execution_ownership(
                        db, job_id=job_id, execution_token=execution_token
                    )
        db.rollback()


def test_retained_lock_is_not_authority_for_another_session(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        job = _attempt(db)
        with fence_domain_commits(job_id=job.id, execution_token=job.execution_token):
            with retained_execution_ownership_scope(db):
                with Session(contextual_engine) as other:
                    with pytest.raises(JobLeaseLost):
                        assert_current_execution_ownership(
                            other, job_id=job.id + 1000, execution_token=job.execution_token
                        )


def test_returned_execution_scope_cannot_mutate_the_locked_witness(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        job = _attempt(db)
        job_id, token = job.id, job.execution_token
        with fence_domain_commits(job_id=job_id, execution_token=token):
            with retained_execution_ownership_scope(db):
                body = retained_execution_job_scope(db, job_id=job_id, execution_token=token)
                original_payload = dict(body["payload_json"])
                body["payload_json"]["pipeline_run_id"] = 999999
                body["execution_token"] = "forged"
                fresh = retained_execution_job_scope(db, job_id=job_id, execution_token=token)
                assert fresh["payload_json"] == original_payload
                assert fresh["execution_token"] == token
                db.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job_id)
                    .values(payload_json={**original_payload, "pipeline_run_id": 123456})
                )
                fresh = retained_execution_job_scope(db, job_id=job_id, execution_token=token)
                assert fresh is None  # Scope-changing SQL invalidated the retained witness.
                resolved = assert_current_execution_ownership(
                    db, job_id=job_id, execution_token=token
                )
                assert resolved["payload_json"]["pipeline_run_id"] == 123456
        db.rollback()
