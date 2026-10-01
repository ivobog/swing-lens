from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CertificationInvariantFailure:
    code: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class CertificationObservation:
    observed_at: datetime
    pipeline_id: int
    active_job_count: int
    failures: tuple[CertificationInvariantFailure, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "pipeline_id": self.pipeline_id,
            "active_job_count": self.active_job_count,
            "failures": [asdict(failure) for failure in self.failures],
        }


def observe_certification_runtime(
    db: Session,
    *,
    pipeline_id: int,
    lock_wait_seconds: float = 5.0,
    transaction_age_seconds: float = 45.0,
    no_progress_seconds: float = 180.0,
    control_heartbeat_seconds: float = 30.0,
) -> CertificationObservation:
    """Inspect durable runtime invariants without taking application row locks."""

    if db.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PRODUCTION_CERTIFICATION_REQUIRES_POSTGRESQL")
    params = {
        "pipeline_id": int(pipeline_id),
        "lock_wait_seconds": float(lock_wait_seconds),
        "transaction_age_seconds": float(transaction_age_seconds),
        "no_progress_seconds": float(no_progress_seconds),
        "control_heartbeat_seconds": float(control_heartbeat_seconds),
    }
    failures: list[CertificationInvariantFailure] = []

    active_jobs = _rows(
        db,
        """
        select j.id as job_id, j.job_type, j.status, j.worker_id,
               j.worker_instance_id, j.progress_stage, j.progress_sequence,
               j.progress_processed, j.progress_total, j.heartbeat_at,
               j.lease_expires_at, j.last_progress_at
        from background_jobs j
        where (j.payload_json->>'pipeline_run_id')::bigint = :pipeline_id
          and j.status in ('RUNNING','QUEUED','RECOVERING','RETRYING','STALLED')
        order by j.id
        """,
        params,
    )
    for row in active_jobs:
        if row["status"] == "RUNNING" and row["lease_expires_at"] is not None:
            if row["lease_expires_at"] < datetime.now(UTC):
                failures.append(_failure("EXPIRED_ACTIVE_JOB_LEASE", row))

    failures.extend(
        _query_failures(
            db,
            "PROGRESS_FROZEN",
            """
            select id as job_id, job_type, progress_stage, progress_sequence,
                   progress_processed, progress_total, last_progress_at,
                   extract(epoch from (now() - coalesce(last_progress_at, started_at, locked_at)))
                       as frozen_seconds
            from background_jobs
            where (payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and status = 'RUNNING'
              and coalesce(last_progress_at, started_at, locked_at) <
                  now() - make_interval(secs => :no_progress_seconds)
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "CONTROL_LOOP_STALE",
            """
            select j.id as job_id, j.worker_id, j.worker_instance_id,
                   w.heartbeat_at as process_heartbeat_at,
                   w.resource_collector_heartbeat_at,
                   w.control_loop_heartbeat_at
            from background_jobs j
            join background_workers w
              on w.worker_id = j.worker_id and w.instance_id = j.worker_instance_id
            where (j.payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and j.status = 'RUNNING'
              and (w.control_loop_heartbeat_at is null or
                   w.control_loop_heartbeat_at <
                       now() - make_interval(secs => :control_heartbeat_seconds))
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "ACTIVE_JOB_OWNER_MISMATCH",
            """
            select j.id as job_id, j.worker_id, j.worker_instance_id,
                   w.instance_id as registered_instance_id, w.stopping_at
            from background_jobs j
            left join background_workers w on w.worker_id = j.worker_id
            where (j.payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and j.status = 'RUNNING'
              and (j.worker_id is null or j.worker_instance_id is null or w.worker_id is null
                   or w.instance_id is distinct from j.worker_instance_id
                   or w.stopping_at is not null)
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "DUPLICATE_ACTIVE_JOB",
            """
            select job_type, request_key, count(*) as active_count,
                   array_agg(id order by id) as job_ids
            from background_jobs
            where (payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and request_key is not null
              and status in ('RUNNING','QUEUED','RECOVERING','RETRYING')
            group by job_type, request_key having count(*) > 1
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "ORPHAN_CHILD_JOB",
            """
            select child.id as job_id, child.parent_job_id, child.job_type
            from background_jobs child
            left join background_jobs parent on parent.id = child.parent_job_id
            where (child.payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and child.parent_job_id is not null and parent.id is null
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "DUPLICATE_CONTINUATION",
            """
            select request_key, count(*) as continuation_count,
                   array_agg(id order by id) as job_ids
            from background_jobs
            where (payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and trigger_source in ('PIPELINE_DEPENDENCY_COMPLETED','CERI_WORKFLOW_COMPLETED')
            group by request_key having count(*) > 1
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "DUPLICATE_CHILD_JOB",
            """
            select parent_job_id, job_type, request_key, count(*) as child_count,
                   array_agg(id order by id) as job_ids
            from background_jobs
            where (payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and parent_job_id is not null and request_key is not null
            group by parent_job_id, job_type, request_key having count(*) > 1
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "CROSS_RUN_JOB_LINEAGE",
            """
            select j.id as job_id, j.related_run_id, p.upload_run_id
            from background_jobs j
            join pipeline_runs p on p.id = :pipeline_id
            where (j.payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and j.related_run_id is distinct from p.upload_run_id
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "UNEXPECTED_RECOVERY_LOOP",
            """
            select id as job_id, job_type, recovery_count
            from background_jobs
            where (payload_json->>'pipeline_run_id')::bigint = :pipeline_id
              and recovery_count > 1
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "PARENT_COMPLETE_WITH_MANDATORY_CHILD_ACTIVE",
            """
            select p.id as pipeline_id, j.id as child_job_id, j.job_type, j.status
            from pipeline_runs p
            join background_jobs j on j.related_run_id = p.upload_run_id
            where p.id = :pipeline_id and p.status in ('COMPLETED','PARTIAL')
              and j.required_for_parent_completion is true
              and j.status in ('RUNNING','QUEUED','RECOVERING','RETRYING','STALLED')
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "MANDATORY_DEPENDENCY_MISSING",
            """
            select p.id as pipeline_id, p.status
            from pipeline_runs p
            where p.id = :pipeline_id
              and p.status in ('WAITING_DEPENDENCY','WAITING_FOR_CERI_COMPLETION')
              and not exists (
                  select 1 from pipeline_dependencies d where d.pipeline_run_id = p.id
              )
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "PARENT_WAITING_WITHOUT_RECOVERABLE_CHILD",
            """
            select p.id as pipeline_id, p.status, d.id as dependency_id,
                   d.state as dependency_state, child.id as child_job_id,
                   child.status as child_status, d.continuation_job_id
            from pipeline_runs p
            join pipeline_dependencies d on d.pipeline_run_id = p.id
            left join background_jobs child on child.id = d.child_job_id
            where p.id = :pipeline_id
              and p.status in ('WAITING_DEPENDENCY','WAITING_FOR_CERI_COMPLETION')
              and d.state in ('PENDING_ENQUEUE','QUEUED','RUNNING')
              and greatest(
                  d.updated_at,
                  coalesce(child.completed_at, d.updated_at)
              ) <
                  now() - make_interval(secs => :control_heartbeat_seconds)
              and not exists (
                  select 1 from background_jobs recoverable
                  where recoverable.status in
                      ('RUNNING','QUEUED','RECOVERING','RETRYING','STALLED')
                    and (
                        recoverable.id = d.child_job_id
                        or recoverable.pipeline_dependency_id = d.id
                        or recoverable.workflow_key = d.result_json->>'workflow_key'
                    )
              )
              and d.continuation_job_id is null
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "PARENT_COMPLETE_WITH_OPEN_DEPENDENCY",
            """
            select p.id as pipeline_id, p.status, d.id as dependency_id,
                   d.state as dependency_state
            from pipeline_runs p
            join pipeline_dependencies d on d.pipeline_run_id = p.id
            where p.id = :pipeline_id and p.status in ('COMPLETED','PARTIAL')
              and d.state not in ('COMPLETED','CANCELLED')
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "POSTGRES_LOCK_WAIT_EXCEEDED",
            """
            select pid as blocked_pid, pg_blocking_pids(pid) as blocking_pids,
                   state, wait_event_type, wait_event,
                   extract(epoch from (now() - query_start)) as wait_seconds,
                   left(query, 500) as query
            from pg_stat_activity
            where datname = current_database() and wait_event_type = 'Lock'
              and query_start < now() - make_interval(secs => :lock_wait_seconds)
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "POSTGRES_TRANSACTION_AGE_EXCEEDED",
            """
            select pid, state,
                   extract(epoch from (now() - xact_start)) as transaction_age_seconds,
                   wait_event_type, wait_event, left(query, 500) as query
            from pg_stat_activity
            where datname = current_database() and pid <> pg_backend_pid()
              and xact_start is not null
              and xact_start < now() - make_interval(secs => :transaction_age_seconds)
            """,
            params,
        )
    )
    failures.extend(
        _query_failures(
            db,
            "POSTGRES_IDLE_IN_TRANSACTION",
            """
            select pid, state,
                   extract(epoch from (now() - xact_start)) as transaction_age_seconds,
                   left(query, 500) as query
            from pg_stat_activity
            where datname = current_database() and pid <> pg_backend_pid()
              and state = 'idle in transaction'
              and xact_start < now() - make_interval(secs => :transaction_age_seconds)
            """,
            params,
        )
    )
    return CertificationObservation(
        observed_at=datetime.now(UTC),
        pipeline_id=int(pipeline_id),
        active_job_count=len(active_jobs),
        failures=tuple(failures),
    )


def _rows(db: Session, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text(statement), params).mappings()]


def _query_failures(
    db: Session,
    code: str,
    statement: str,
    params: dict[str, Any],
) -> list[CertificationInvariantFailure]:
    return [_failure(code, row) for row in _rows(db, statement, params)]


def _failure(code: str, detail: dict[str, Any]) -> CertificationInvariantFailure:
    return CertificationInvariantFailure(code=code, detail=dict(detail))
