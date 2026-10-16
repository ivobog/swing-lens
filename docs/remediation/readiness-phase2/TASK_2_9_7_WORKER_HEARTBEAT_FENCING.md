# Task 2.9.7 — Worker Heartbeat Child Transaction and Fencing

## Verdict

`TASK 2.9.7 WORKER HEARTBEAT FENCING: CLOSED`

The exact target passed against disposable PostgreSQL. The defect was a stale test assumption: the fixture queried execution ownership in a new Session but did not explicitly bind that Session as the domain-commit owner. Production correctly returned no fenced Session and failed closed.

## Timing and retained failure

- Start: `2026-10-14T12:56:15.5557174+02:00`
- Finish: `2026-10-14T12:58:49.1822455+02:00`
- Elapsed: 2 minutes 34 seconds
- Baseline rerun: not used; the retained failures were conclusive.
- Exact node: `tests/integration/test_t14c_decision_writer_postgresql.py::test_worker_heartbeat_uses_child_transaction_retaining_fence`
- Retained assertion: `current_fenced_domain_session(job.id, job.execution_token) is child` observed `None`; the handler failure then left the retryable job `QUEUED` instead of `COMPLETED`.

## Root cause

The domain-write fence evolved from implicit Session registration during `assert_current_execution_ownership()` to explicit Session ownership through `fence_domain_commits()` / `bounded_domain_session()`. This separation is intentional:

- an ownership read proves that a token currently matches;
- it must not silently grant the querying Session authority to publish financial mutations;
- the Session that may commit domain data must be explicitly bound;
- its outer commit revalidates and locks the job row.

The target still opened an arbitrary child Session and expected the ownership assertion to bind it implicitly. Production therefore returned `None` from `current_fenced_domain_session()`. The subsequent assertion raised before the heartbeat path ran. This was a stale fixture contract, not a failure of detached control progress or worker-generation identity.

## Transaction boundary and correction

Only the target fixture changed. It now wraps the child business transaction with:

```python
with fence_domain_commits(
    child,
    job_id=job.id,
    execution_token=job.execution_token,
):
    ...
```

The certified ownership sequence is:

1. The worker claim is committed by the parent worker Session.
2. `execute_job()` supplies the immutable job ID and execution token while deferring the row lock until publication.
3. The fixture explicitly binds the child Session as the domain transaction owner.
4. The child validates the same token and stages an `UploadRun` business row.
5. `job._heartbeat()` detects the explicitly fenced child Session and writes heartbeat/worker control fields in that same transaction; it performs no commit.
6. A separate observer cannot see the staged business row after the heartbeat.
7. A separate Session using a wrong execution token receives `JobLeaseLost`.
8. The child’s explicit `commit()` invokes the outer commit fence, revalidates exact ownership, and publishes its staged business and heartbeat state.
9. Once the transaction ends, `current_fenced_domain_session()` returns `None`.
10. The worker completes the job, clears its execution token, and persists `{"child_heartbeat": true}`.

This preserves the useful distinction between a bounded child transaction retaining the job-row fence and detached pipeline/CERI control progress. No global detached-progress behavior was disabled.

## Files changed

- `tests/integration/test_t14c_decision_writer_postgresql.py` — explicit child Session binding plus isolation and stale-token assertions.
- `docs/remediation/readiness-phase2/TASK_2_9_7_CERTIFICATION.xml`
- this report

No production file changed for Task 2.9.7.

## Certification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/integration/test_t14c_decision_writer_postgresql.py::test_worker_heartbeat_uses_child_transaction_retaining_fence' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_7_CERTIFICATION.xml
```

Result: **1 passed, 4 warnings in 8.39 seconds**.

JUnit: `docs/remediation/readiness-phase2/TASK_2_9_7_CERTIFICATION.xml`

No baseline or second certification execution was performed.

## Fencing, liveness, and compatibility

- The heartbeat does not commit or roll back the child’s staged business row; its invisibility from an observer before `child.commit()` is asserted.
- The wrong execution token is rejected with `JobLeaseLost`.
- The child Session remains the sole explicit domain owner before and after the heartbeat, and loses that identity when its transaction ends.
- The test engine enforces `statement_timeout=3000` ms and `lock_timeout=1500` ms; no unbounded self-lock occurred.
- `run_worker_once()` retained the registered worker instance/generation identity from Task 2.9.4b; no production code in that path changed here.
- Task 2.9.6 automatic retry authority and Task 2.9.5e superseded-root fencing were untouched.
- No additional worker, job, retry policy, or transaction-ownership mechanism was introduced.

## Source pins and safety

Task 2.9.7 modified no reviewed production source, so no source-pin regeneration was necessary. The final validator result was:

```json
{"stale": []}
```

The target used only the disposable `contextual_engine`, created a bounded derivative engine against that same disposable database, and disposed it in `finally`. It constructs no provider, opens no live connection, and cannot access the normal SwingLens database or historical Runs 22–33. No test-owned worker process or thread was created.

No Task 2.9.7 blocker remains.
