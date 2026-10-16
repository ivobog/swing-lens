# SwingLens Task 2.9.12s Final Report

## Result

**PASS.** The original Chromium-100 certification completed on the second evidence-driven attempt. The full pipeline reached `COMPLETED`; CERI, decision handoff, 100-member Setup capture/evaluation, Winner capture, financial cardinality, source authority, and GUI-to-database assertions all passed.

This is a focused Task 2.9.12s certification. It does not replace the separately required complete Phase 2.9 release suite.

## Actual root cause

The reported Setup failure was not a PostgreSQL-enforced limit and was not evidence of an abandoned or blocked Setup transaction. It was a certification-observer policy defect:

- `observe_certification_runtime` classified every transaction older than 120 seconds as `POSTGRES_TRANSACTION_AGE_EXCEEDED`.
- It separately classified an old transaction as `POSTGRES_IDLE_IN_TRANSACTION`, even when the owning process was performing CPU-side deterministic evidence validation between SQL statements.
- The 120-second value existed only in the Chromium certification caller. It was not a PostgreSQL timeout, a frozen financial rule, or a production configuration requirement.
- The failed transaction had a live worker, valid lease, current control-loop and worker heartbeats, advancing durable job progress, no cancellation request, and no lock wait.

The first Task 2.9.12s E2E attempt supplied a decisive second data point. Setup capture completed in 192.144 seconds and Setup evaluation completed in 143.175 seconds, but a provisional 300-second absolute ceiling then stopped healthy Winner publication at transaction age 300.632905 seconds. The last SQL was a `core_calculation_evidence` read, the backend was idle between statements, durable progress remained fresh, and no lock wait existed. Moving the fixed ceiling from 120 to 300 seconds therefore reproduced the same category error rather than defining a safety policy.

## Minimal correction

Transaction age remains a **120-second review boundary**, but it is no longer sufficient by itself to fail healthy atomic work. For a transaction beyond that boundary, the observer now requires all of the following before treating it as healthy:

- The exact pipeline has a `RUNNING` job.
- No cancellation has been requested.
- The job lease is current.
- Job heartbeat and durable `last_progress_at` are both fresh within 30 seconds.
- The job is owned by the matching live worker generation.
- Worker heartbeat and control-loop heartbeat are current.
- The worker is not stopping.

If those conditions are not satisfied, the observer emits `POSTGRES_TRANSACTION_STALLED` and, when applicable, `POSTGRES_IDLE_IN_TRANSACTION`. Existing independent protections remain active:

- Lock waits over 5 seconds: `POSTGRES_LOCK_WAIT_EXCEEDED`.
- Frozen durable progress: `PROGRESS_FROZEN`.
- Stale control loop, expired lease, owner/generation mismatch, cancellation, duplicate jobs/continuations, dependency violations, and the finite workflow deadline.

This is not monitoring removal and not a silent timeout increase. It replaces elapsed time as a proxy with direct progress, ownership, liveness, cancellation, and lock evidence. Publication atomicity, PIT/source authority, calculations, and financial assertions were not changed.

Task-specific files corrected:

- `app/services/runtime_certification_observer.py`
- `tests/e2e/single_run_certification/test_single_run_certification.py`
- `tests/e2e/single_run_certification/test_real_writer_cardinality.py`
- `tests/integration/test_pipeline_authority_certification_postgresql.py`

No general SQL optimization or pipeline redesign was performed.

## Focused regression

Native JUnit: `TASK_2_9_12S_TRANSACTION_POLICY.xml`

- Node: `tests/integration/test_pipeline_authority_certification_postgresql.py::test_12b_transaction_policy_allows_progress_but_rejects_stall_and_cancellation`
- Result: **1 passed** in 11.24 seconds.
- Database: real disposable PostgreSQL.
- Proved: a transaction beyond the review boundary is accepted only with fresh durable progress and live authority; stale progress produces `POSTGRES_TRANSACTION_STALLED` plus `POSTGRES_IDLE_IN_TRANSACTION`; requested cancellation produces the same terminal diagnostics even when heartbeats are refreshed.

The adjacent existing PostgreSQL authority suite retains dedicated lock-wait/cancellation, lease, owner, control-loop, duplicate, lineage, and dependency checks.

## Chromium-100 attempts

Two complete E2E attempts were used; the authorized maximum was three.

### Attempt 1 — expected diagnostic failure after the first concrete policy correction

- Execution: `20261015T211757Z-95f25e44`
- Native JUnit: `TASK_2_9_12S_CHROMIUM_ATTEMPT_1.xml`
- Result: **1 failed** in 1620.37 seconds.
- First failed invariant: `POSTGRES_TRANSACTION_AGE_EXCEEDED` at 300.632905 seconds.
- Backend PID: `29756`.
- Stage: `CAPTURING_WINNER_PREDICTIONS`.
- `healthy_pipeline_progress`: `true`; no lock-wait failure.
- Setup capture had already completed in 192.144 seconds and Setup evaluation in 143.175 seconds.
- Complete preserved diagnostics: `TASK_2_9_12S_CHROMIUM_ATTEMPT_1_DIAGNOSTICS/`.

This failure proved that an absolute age ceiling still falsely terminated valid atomic work and directly motivated the final progress-bounded policy.

### Attempt 2 — final pass

- Execution: `20261015T214653Z-2f7d94b5`
- Upload run: `2` (the fixture's decoy run is `1`).
- Pipeline run: `1`.
- Root pipeline job: `1`; durable continuation job: `32`.
- Worker: `certification-20261015T214653Z-2f7d94b5`.
- Worker instance: `26862616043b4661a0e44802ae5b6f04`; supervisor generation: `1`.
- Native JUnit: `TASK_2_9_12S_CHROMIUM_ATTEMPT_2.xml`.
- Result: **1 passed** in 1683.66 seconds (native testcase time 1683.073 seconds).
- Pipeline status: **COMPLETED**.
- Pipeline service duration: 1393.086306 seconds.
- Runtime observer: 2511 observations, 0 failed observations, maximum 27 active jobs.

Key production-path stage durations:

| Stage | Duration |
|---|---:|
| Technical scoring | 126.283 s |
| Decision handoff manifest | 37.117 s |
| Setup capture/publication | 196.344 s |
| Setup lifecycle evaluation | 141.668 s |
| Winner prediction capture | 302.555 s |

The Winner stage completed beyond both the original 120-second boundary and the disproven provisional 300-second ceiling while all direct safety signals remained healthy.

CERI evidence:

- Workflow duration: 381.091650 seconds.
- Maximum CERI job duration: 55.602167 seconds.
- Maximum CERI checkpoint gap: 19.119376 seconds.
- Provider source records: 600 for 100 members.
- Recovery count: 0; retry count: 0; stalled jobs: 0 (enforced by native assertions).
- Durable workflow counts: 16 provider-ingest batches, 8 normalization batches, 2 feature batches, and one each of finalization, capture, change detection, and alert rebuild.

Financial and authority status:

- Fundamental, technical, and combined result cardinalities were exactly 100.
- Certified CERI, Setup, and Winner evidence was present and consumed through the authorized production workflow.
- No manual decision anchor or alternate authority path was used.
- All original source-integrity, membership, financial-cardinality, evidence, continuation, and GUI-to-database assertions passed.
- No duplicate/partial publication or runtime invariant failure was reported.

Complete successful diagnostics, metrics, logs, invariant observations, and GUI screenshot are preserved in `TASK_2_9_12S_CHROMIUM_ATTEMPT_2_DIAGNOSTICS/`.

## Final gates and cleanup

- `python -m compileall -q app tests`: **PASS**.
- Ruff lint on all Task 2.9.12s changed files: **PASS**.
- Ruff formatting check on all Task 2.9.12s changed files: **PASS**.
- `git diff --check`: **PASS** (only existing Windows line-ending notices).
- Official T14 validator: **PASS**, `{"stale":[]}`.
- Attempt 1 cleanup: 0 owned processes; disposable database absent.
- Attempt 2 cleanup: 0 owned processes; disposable database `swinglens_pytest_cert_e6bb8243b67b` absent.
- Live providers were not contacted; deterministic providers and disposable PostgreSQL were used.

## Remaining blocker

There is no remaining blocker within Task 2.9.12s. The complete Phase 2.9 release suite remains separately required before declaring the overall release certified.
