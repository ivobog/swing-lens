# Task 2.9.5b — CERI Resume Barrier Final Certification

## A. Verdict

**FAIL.** The exact test ran once and failed. No retry or source/test/probe modification was made.

The continuation job completed with result `WAITING_FOR_CERI_COMPLETION`, but the persisted pipeline was reconciled to `FAILED` with `PIPELINE_ROOT_TERMINAL_WITH_ACTIVE_PIPELINE`. The duplicate-resume checkpoint was therefore not executed and Task 2.9.5 cannot be closed.

## B. Duration

- Start: `2026-10-14T12:04:02.6609156+02:00`
- Evidence collection finish: `2026-10-14T12:07:04.2372037+02:00`
- Elapsed: `00:03:01.5762881`
- Budget: 15 minutes; pytest outer deadline: 600 seconds

## C. Test result

Command:

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_phase4_configuration_certification_postgresql.py::test_public_resume_retains_c1_and_duplicate_completed_work_stays_behind_ceri_barrier' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_5B_CERTIFICATION.xml
```

- Result: `1 failed`, 2 warnings
- JUnit runtime: `66.268 s` (console: `66.31 s`)
- Failing checkpoint: test line 402
- Expected: pipeline `WAITING_FOR_CERI_COMPLETION`
- Observed: pipeline `FAILED`, message `Pipeline root completed without a terminal pipeline transition.`, error `PIPELINE_ROOT_TERMINAL_WITH_ACTIVE_PIPELINE`
- JUnit: `docs/remediation/readiness-phase2/TASK_2_9_5B_CERTIFICATION.xml`

## D. Verified state transitions

| Checkpoint | Root job | Continuation | Pipeline | CERI barrier |
|---|---|---|---|---|
| Initial enqueue | ID `1`, `QUEUED` | None | ID `1`, `PENDING` | Not entered |
| After interruption | `QUEUED`, attempt `1`; scheduling-boundary error retained | None | `FAILED` at `CERI_PROVIDER_INGEST` | Provider scheduling interrupted |
| Resume accepted | Root remains `QUEUED` | ID `6`, `QUEUED`, attempt `0`, resumes at `CERI_PROVIDER_INGEST` | `QUEUED` | Resume admitted |
| Continuation executed | Root `QUEUED`, attempt `1` | ID `6`, `COMPLETED`, attempt `1`, result `WAITING_FOR_CERI_COMPLETION` | **`FAILED`** at `CERI_PROVIDER_INGEST` | Waiting result was not backed by an active durable dependency handoff |
| Duplicate resume rejected | Not reached | Not reached | Not reached | Not certified |

Immediate evidence identifies a fixture/probe mismatch. The offline `scheduling_boundary` returns `0` instead of invoking a scheduler that persists a CERI workflow dependency. Root reconciliation permits a completed root in `WAITING_FOR_CERI_COMPLETION` only when exactly one active `CERI_WORKFLOW` dependency is bound to that root; without it, the fail-closed transition to `FAILED` is expected. No production correction was attempted in this certification-only task.

## E. Integrity certification

- Frozen C1 anchor preserved through continuation: anchor ID `6685a9ddd6b9cd3c0a45c56f5363f560096c63b42fa012b8d09f675feb35be78`; fingerprint `94c475062178ceeecd40b225c03f58ed4b324fc32268af905c23ec66a84a01f5`.
- Calculation context `1`, scope identity, and PIT cutoff `2026-10-14T10:05:02.5392850Z` were exact between root and continuation.
- Evidence before and after continuation was byte-for-byte equal: 12 rows, IDs `1–12`; no duplicate financial evidence was observed.
- Root attempt count remained `1`; continuation attempt count was `1`.
- Setup Lifecycle evaluations `0`, transitions `0`, alerts `0`, Winner snapshots `0`.
- Job-ID stability and HTTP 409 duplicate-resume rejection were not executed because the preceding pipeline-state assertion failed.
- The run used disposable PostgreSQL, synthetic IB readiness, patched offline provider scheduling, bounded task-owned subprocesses, and no live SEC/EODHD/IBKR access.

## F. Source pins

Verification-only result before pytest:

```json
{"stale": []}
```

No regeneration occurred.

## G. Working-tree safety

- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- The pre-existing dirty worktree was preserved. No production, test, probe, schema, or source-review logic was changed.
- Task-created files are this report, the JUnit artifact, and retained pytest stdout/stderr logs.
- No task Python process remained after completion. Retained output contains only redacted database user information and no password, API key, or secret sentinel.

## H. Remaining blockers

`TASK 2.9.5 CERI RESUME BARRIER: OPEN`

The corrected duplicate-resume assertions are still uncertified because the offline fixture does not create the durable active CERI dependency required for a valid waiting handoff.

## I. Next recommended single task

In one bounded remediation, change only the offline CERI scheduling fixture so it persists a task-owned active `CERI_WORKFLOW` dependency for continuation job `6`, without completing the dependency or contacting a provider. Then rerun only this exact node once. Do not begin another Phase 2.9 regression.
