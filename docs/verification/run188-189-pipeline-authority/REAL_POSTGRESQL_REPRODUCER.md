# Real PostgreSQL Reproducer

## Result

`REPRODUCED: YES`

The focused test
`tests/integration/test_sec_readiness_repair_postgresql.py::test_full_pipeline_sec_repair_handoff_does_not_self_block`
uses a freshly migrated `swinglens_pytest_*` PostgreSQL database and the production
worker, pipeline executor, SEC-readiness diagnostic, automatic repair scheduler,
heartbeat, and domain-write fence paths. It does not mock PostgreSQL, ORM commits,
row locking, or the worker heartbeat. No external provider is called.

The database sessions use bounded settings:

- `lock_timeout=1000ms`
- `statement_timeout=5000ms`

The test's observer polls `pg_stat_activity` and `pg_blocking_pids()` from a third
connection while SQLAlchemy instrumentation records the backend PID and inherited
domain ownership for the child insert and ownership-fence statements.

## Current-HEAD failure

Run against source commit `e5217fdee706fa1fe7f1e72ddcee0546f56ebc2c` after the
forensic baseline commit:

```text
PIPELINE_AUTHORITY_REPRODUCER={
  elapsed_seconds: 2.016,
  child_insert_backend_pid: 20468,
  root_status: QUEUED,
  pipeline_status: RUNNING,
  repair_job_ids: [],
  blocked_edges: [{
    waiter_pid: 14112,
    wait_event_type: Lock,
    wait_event: transactionid,
    blocker_pids: [20468],
    query: SELECT ... FROM background_jobs WHERE id = $1 FOR UPDATE
  }]
}
```

The worker log ends at the same production call chain:

```text
execute_full_pipeline
  -> _schedule_sec_prerequisite_repair
  -> _save_progress
  -> lease_guard
  -> heartbeat
  -> control_db.commit
  -> Session.before_commit
  -> assert_current_execution_ownership
  -> SELECT background_jobs ... FOR UPDATE
  -> LockNotAvailable: canceling statement due to lock timeout
```

The blocked control backend inherits ownership for root job 1. The blocker is the
exact backend that inserted the automatic SEC-repair child inside the uncommitted
pipeline transaction. PostgreSQL reports a `transactionid` wait from the control
backend to that child-insert backend. After the bounded failure, the root is requeued,
the pipeline remains `RUNNING`, and no repair child is durable. This matches the
structural incident in Runs 188 and 189.

## Invocation

The test requires `SWINGLENS_TEST_POSTGRES_ADMIN_URL` to identify the local
PostgreSQL admin database. In this workspace it was derived in-process from the
configured application database URL without printing credentials, then invoked as:

```text
.venv\Scripts\python.exe -m pytest \
  tests/integration/test_sec_readiness_repair_postgresql.py::test_full_pipeline_sec_repair_handoff_does_not_self_block \
  -q -s
```

Expected before remediation: one failed test with the diagnostic above.

