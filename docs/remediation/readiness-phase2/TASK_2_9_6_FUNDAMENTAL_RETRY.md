# Task 2.9.6 — Fundamental Retry and Frozen C1

## Verdict

`TASK 2.9.6 FUNDAMENTAL RETRY: CLOSED`

The exact disposable-PostgreSQL certification passed. The same durable root job retried under its original frozen C1 configuration, completed on attempt 2, and retained the byte-for-byte same Fundamental evidence projection after every current YAML file had been replaced with invalid C2 content.

## Timing and retained failure

- Start: `2026-10-14T12:48:18.5139442+02:00`
- Finish: `2026-10-14T12:55:12.4583814+02:00`
- Elapsed: 6 minutes 54 seconds
- Baseline rerun: not used; the retained Phase 2.9 failure was sufficient.
- Original failure: `scripts/qa/t13e_deployed_probe.py:460`, `assert worked`, because `run_worker_once()` returned false.
- Retained failing database: disposable database `swinglens_pytest_1a50af35f30c` (the later failed-set replay used `swinglens_pytest_191bd0a3a84a`). Both were dropped test databases.

The retained JUnit did not emit the fixture-local pipeline or root-job numeric IDs, and those disposable databases no longer exist. Their values therefore cannot be recovered honestly. Identity is nevertheless tested directly: the retry is made ready with `queued["job_id"]`, and the completed row is selected with that same ID. No operator-resume request is issued and no replacement root is created.

## Root cause

The failure was an inconsistent durable state transition across two committed boundaries:

1. The first worker attempt committed Fundamental evidence and the `SCORING_FUNDAMENTALS` step.
2. The deterministic fixture raised `T13E transient acquisition readiness boundary` at the subsequent IB gateway boundary.
3. `execute_full_pipeline()` durably transitioned the pipeline to `FAILED` before propagating the exception.
4. `mark_job_failed_or_retry()` correctly classified the exception as retryable and atomically returned the same root job to `QUEUED`, incrementing its retry count and clearing its lease/token ownership.
5. Queue admission correctly rejects jobs whose pipeline is terminal. Consequently the legitimate same-root retry was invisible to the worker, and `run_worker_once()` returned false.

This was not a lost C1 binding, Fundamental duplication, or stale fixture assertion. It was the missing pipeline half of the automatic root-retry transition introduced/exposed by terminal execution-authority enforcement.

## Correction

The existing reviewed `mark_pipeline_job_failure` transport now also reconciles a retryable `FULL_PIPELINE` root after the job has become `QUEUED`. Under the pipeline row lock it permits `FAILED -> QUEUED` only when all of these are true:

- pipeline execution authority is `ACTIVE`;
- `pipeline.result_json.background_job_id` is exactly the retrying job;
- the same job remains the current durable root;
- no `FULL_PIPELINE` child with `parent_job_id` equal to the root and `trigger_source=PIPELINE_OPERATOR_RESUME` exists.

The state machine recognizes only that exact automatic transition (`FAILED -> QUEUED`, actor `retry`). It does not permit retry reopening from `BLOCKED`, `CANCELLED`, `COMPLETED`, `PARTIAL`, or any other terminal state. Job requeue and pipeline reactivation occur in the same worker control transaction.

Files changed for this task:

- `app/services/background_job_service.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_state_machine.py`
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json`

## Before and after

| Boundary | Before | After |
|---|---|---|
| First exception | root `QUEUED`; pipeline `FAILED` | root and pipeline atomically `QUEUED` |
| Claim authority | root excluded because pipeline was terminal | same root claimable under active authority |
| Superseded root | excluded by queue predicate | still excluded; retry transition also refuses an operator-resume child |
| Next claim | no execution token because no claim | fresh execution token and attempt 2 |
| Configuration | C1 remained stored but could not execute | original C1 anchor executes; invalid current C2 is ignored |
| Fundamental evidence | one committed row | same retained row; no rewrite or duplicate |

## Evidence and configuration proof

The mandated target asserts all of the following in one real-worker sequence:

- first attempt root status is `QUEUED` with the transient boundary error;
- evidence kinds after interruption are exactly `["FUNDAMENTAL"]`;
- every copied current YAML file is overwritten with `invalid-current-C2: true`;
- the completed record has the original root job ID;
- attempt count is exactly 2;
- `effective_configuration_anchor` equals the enqueue-time C1 anchor;
- `completed["evidence"][0] == interrupted["evidence"][0]`.

Because the same job payload is reused, the persisted market-calculation-context ID, PIT cutoff, input-as-of session, calendar version, and readiness version remain the frozen enqueue values. The production worker validates that retained market context before pipeline execution. No current-source or cross-run fallback is added by the correction.

## Certification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/integration/test_phase4_configuration_certification_postgresql.py::test_real_worker_retry_after_native_fundamental_commit_uses_c1' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_6_CERTIFICATION.xml
```

Result: **1 passed, 2 warnings in 51.43 seconds**.

JUnit: `docs/remediation/readiness-phase2/TASK_2_9_6_CERTIFICATION.xml`

No baseline or second certification execution was performed.

## Source pins and authority review

The first validator run correctly reported these reviewed files stale:

- `app/services/background_job_service.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_state_machine.py`

The changes were reviewed against the registered `mark_job_failed_or_retry` and `mark_pipeline_job_failure` native transports and the `WF_PIPELINE_STATE_TRANSITION` operation family. The official generator was used once; immutable historical T14D artifacts were not changed. Final validation:

```json
{"stale": []}
```

## Safety and compatibility

- The target used only its disposable PostgreSQL fixture and subprocesses with finite timeouts.
- Its installed dependency boundary returned an offline IB status and raised before live acquisition; no normal runtime database, live provider, IB Gateway, SEC, or EODHD service was used.
- The fixture-local C2 mutation affected only the copied temporary configuration tree.
- Historical Runs 22–33 were inaccessible and unchanged.
- The operator-resume superseded-root fence remains active in both queue admission and the new locked retry check.
- No Fundamental scoring, evidence eligibility, CERI completion, Winner publication, or provider policy changed.

No Task 2.9.6 blocker remains.
