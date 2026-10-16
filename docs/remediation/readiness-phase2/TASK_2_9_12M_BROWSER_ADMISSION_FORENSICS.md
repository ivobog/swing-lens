# Task 2.9.12m Browser Admission Forensics

## Verdict

`FAIL — TARGETED CERTIFICATION FAILED`

The browser-admission request is strongly localized as the owner class, and the
attempted route-owned transaction split produced two bounded PostgreSQL
transactions. The mandatory SQLAlchemy Session/backend trace was not activated,
however, and the focused browser node failed because its test-only pre-enqueue
stop was not installed. Exact Session ownership and the remediation therefore
are not certified.

## Time and restrictions

- Actual start: `2026-10-15T01:49:32.0075232+02:00`.
- Absolute deadline: `2026-10-15T02:59:32.0075232+02:00`.
- Failure/stop observed: approximately `2026-10-15T02:00:45+02:00`.
- No Chromium-100 run or full Phase 2.9 suite was started.
- Deterministic providers and a uniquely named disposable database were used.

## Retained incident

Execution `20261014T231952Z-578a22d6` failed at exactly `120.035264`
seconds on PostgreSQL backend PID `15932`, application `swinglens-test`, state
`idle in transaction`, wait `ClientRead`, after a SELECT from
`setup_signal_snapshots`. The old database/process cannot be inspected.

The retained web log provides a tighter interval:

1. A web response completed at `2026-10-14T23:21:33.038835Z`.
2. The offending transaction began at approximately `23:21:33.214Z`.
3. `pre_enqueue_operational_gate_passed` occurred at `23:23:38.695954Z` for
   `POST /runs/3/pipeline`.
4. The request completed at `23:23:40.019076Z`.

This places the historical backend inside the browser admission request, not
inside `_prepare_real_writer_pre_pipeline_baseline`.

## Path comparison

Path A, the passing fixture test, ends each owned setup, price-history, and
retained-provider Session before moving to the next unit. It never renders a run,
creates a transition preflight plan, re-verifies that plan, or enqueues a root
pipeline job.

Path B adds:

```text
Playwright upload and run-detail render
  -> POST /runs/{run_id}/pipeline
  -> run_full_pipeline_action dependency Session
  -> create_transition_preflight_plan
  -> TransitionCandidateDiscoveryService.discover_for_run
  -> Setup history/current-pointer reads and technical reconstruction
  -> start_pipeline
  -> validate_pre_enqueue_operational_gate
  -> verify_transition_preflight_for_enqueue
  -> second candidate discovery
  -> pipeline/root-job persistence
```

The corrected preparation helper is used before this extra path and was not
changed again.

## Attempted owner instrumentation

Test-scoped SQLAlchemy listeners were added for Session transaction creation,
begin, commit, rollback and end; pool checkout/checkin; relevant Setup SQL;
backend PID; process/thread/task; request scope; and bounded stacks. Output was
designed as per-process append-only fsynced JSONL.

The browser test defined local opt-in fixtures, but `certification_environment`
is an imported fixture whose own fixture dependencies resolved in its defining
module. Consequently neither `CERTIFICATION_TRANSACTION_TRACE_DIR` nor
`CERTIFICATION_STOP_BEFORE_PIPELINE_ENQUEUE` reached the subprocess. No trace
JSONL was created and the required Python Session identity/creation stack is
missing. This is an explicit evidence gap.

## Focused PostgreSQL observation

Execution `20261014T235619Z-3230ee43` used database
`swinglens_pytest_cert_a1081da9319d` and persisted 1,164 observer samples.

- Backend `25424`: preflight creation, `xact_start`
  `2026-10-15T01:57:57.253972+02:00`, 59 Setup relation samples, maximum
  observed age `64.497914` seconds.
- Backend `21900`: pre-enqueue verification/admission, `xact_start`
  `2026-10-15T01:59:01.865888+02:00`, 57 Setup relation samples, maximum
  observed age `65.490093` seconds, followed by root-job insertion.
- Maximum concurrently open transactions: 4.

This is consistent with the added route-owned commit separating persisted plan
creation from the built-in re-verification phase. It is not sufficient for PASS
because the exact Session event trace did not exist and the test did not stop
before enqueue.

## First failure and stop

The first selected node expected the test adapter to return HTTP 409 at the
pre-enqueue boundary. The adapter was not enabled, so the production endpoint
continued normally, admitted a pipeline, and returned its redirect. The first
failing assertion was the expected-status assertion in
`test_browser_admission_closes_preflight_creation_transaction`.

Pytest printed `F`; execution was interrupted immediately under the task's stop
rule. Because the interruption occurred before pytest session finalization, the
requested native JUnit file was not written. No synthetic JUnit was created.

## Safety and cleanup

The supervisor, web process, worker launcher and listener on dynamic port 9217
were gone after teardown. Database `swinglens_pytest_cert_a1081da9319d` was
dropped and verified absent. The unintended root pipeline was confined to that
database and terminated during fixture teardown. No live provider was used.

The earlier execution directory `20261014T210501Z-1236be96` was not present;
only its retained documentation was available.
