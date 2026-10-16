# SwingLens Task 2.9.12p Final Report

## Acceptance status

| Gate | Status | Evidence |
|---|---|---|
| CERI regression | **CLOSED** | No CERI failure existed in accessible evidence; latest CERI chain completed; semantic non-interference and negative tests passed |
| Setup inherited/CPU transaction defect | **CORRECTED IN TARGETED TESTS** | Parent transaction absent at all CPU checkpoints; real Setup-100 max age 35.933041 s |
| Setup transaction acceptance | **NOT CLOSED** | Chromium-100 found a remaining composed handoff/publication transaction at 120.292053 s |
| Cross-system targeted certification | **PASS, FOCUSED** | Native JUnit: 203 passed, 0 failed/errors/skipped |
| Chromium-100 | **FAIL** | Native JUnit: 1 failed in 1058.017 s; not retried |
| Phase 2.9 release | **NOT CERTIFIED** | Official complete release suite was not authorized after Chromium failure |

No production-readiness or full-release claim is made.

## Required answers

### 1. What was the latest CERI failure?

There was no accessible new CERI failure before the task, and the new execution
`20261015T155847Z-ea97e8f1` completed all CERI jobs. Its actual failure was
`POSTGRES_TRANSACTION_AGE_EXCEEDED` / `POSTGRES_IDLE_IN_TRANSACTION` in Setup at
`CAPTURING_SETUP_SIGNALS`, backend 12344, age 120.292053 seconds.

### 2. Was it genuinely a CERI defect or a downstream/shared-service failure?

It was a downstream Setup/shared transaction-and-authority-validation failure. CERI provider,
normalization, features, finalization, certified capture, change, alert, exact barrier, continuation,
and decision handoff all completed.

### 3. What changed after the previous certified baseline?

Task 2.9.12p added a clean parent boundary before Setup CPU construction, production build
checkpoints every 25 members, guarded refusal to discard pending writes, immutable evidence batch
prefetch, real Setup-100 transaction observation, and CERI semantic non-interference coverage. The
official source pins were updated with the current-overlay process.

### 4. Which change caused the failure?

No recent CERI change caused it. The causal code area is the pre-existing composed Setup
handoff/publication path: full authority/configuration/source validation remains inside a
100-member atomic transaction long enough to exceed 120 seconds under actual E2E observability.
The new changes exposed, but did not introduce, that remaining scale condition.

### 5. Why did previous CERI certification miss it?

The previous CERI certification was valid but ended before this downstream stage. Cross-stage tests
and Chromium-10 did not reproduce 100-member load. The new Setup-100 fixture measured the actual
loader/builder/progress/publication path but did not compose a consumed 100-member decision handoff
and full E2E SQL telemetry in the same transaction.

### 6. Which producers and consumers were affected?

CERI producers were not semantically affected. Decision handoff completed. The Setup consumer and
shared mutation/configuration authority validators owned the failing work. Runtime monitoring
correctly stopped it. Winner, terminal roll-up, GUI terminal assertions, and financial cardinality
were not reached.

### 7. Which files were corrected?

Production:

- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/setup_lifecycle/repository.py`

Regression coverage:

- `tests/setup_lifecycle/test_snapshot_builder.py`
- `tests/integration/test_run8_setup_performance_postgresql.py`
- `tests/integration/test_ceri_batched_workflow_v2.py`

Source authority:

- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json`

### 8. Did CERI evidence change for identical deterministic inputs?

No. The new PostgreSQL non-interference test compares stable source identities, contexts,
membership, evidence keys, canonical evidence fingerprints, feature/output parity, and continuation
bindings across identical frozen inputs with and without unrelated Setup/GUI/pipeline/Winner
metadata. It passed. Generated IDs and timestamps were intentionally excluded.

### 9. Was the 100-member Setup transaction defect corrected?

Partly, but not sufficiently for acceptance. The inherited parent transaction and CPU-only hold are
corrected; the real targeted path passed with no CPU transaction and a 35.933041-second maximum.
The single Chromium-100 run found a distinct remaining composed handoff/publication transaction at
120.292053 seconds. Therefore **SETUP TRANSACTION remains NOT CLOSED**.

### 10. Were all cross-subsystem regressions certified?

The focused 203-test selection passed CERI positive/negative/barrier, continuation, decision
handoff, v1/v2 reconstruction, Setup-100, Winner authority, cancellation, execution fencing,
rollback, concurrency, source/PIT/configuration, and duplicate-evidence checks. Full-scale terminal
integration was not certified because Chromium stopped during Setup before Winner.

### 11. Did Chromium-100 pass?

No. The original test was run once and reported 1 failure in 1058.017 seconds. It was not retried.
The native failure is preserved in `TASK_2_9_12P_CHROMIUM_CERTIFICATION.xml` with execution-owned
environment, invariant, and process logs.

### 12. What remains uncertified?

- Exact backend-PID-to-SQLAlchemy-Session ownership across the 100-member verification/publication boundary.
- A sub-120-second composed 100-member handoff plus atomic Setup publication under full telemetry.
- Full-scale Winner publication and authorized consumption.
- Terminal pipeline result and financial-cardinality assertions.
- The official complete Phase 2.9 release suite.

## Native test and measurement summary

| Evidence | Tests | Failures / errors / skips | Time / measurement |
|---|---:|---:|---:|
| Final targeted JUnit | 203 | 0 / 0 / 0 | 708.941 s |
| Setup-100 testcase | 1 | 0 / 0 / 0 | 211.599 s test; 40.608105 s capture |
| Setup-100 max transaction | — | — | 35.933041 s |
| Setup-100 parent CPU states | — | — | six observations, all false |
| Chromium-100 JUnit | 1 | 1 / 0 / 0 | 1058.017 s |
| Chromium violating transaction | — | — | 120.292053 s |

Static and authority results: compileall passed; Ruff lint passed; Ruff format check passed; `git
diff --check` passed with line-ending warnings only; official T14 returned `{"stale":[]}`.

## Latest execution timeline

- CERI feature jobs 26/27 completed 100 checkpoints.
- CERI finalize 28 completed at `16:11:48.115818Z`.
- CERI capture 29 completed at `16:12:29.562188Z`.
- Change 30 and alert 31 completed by `16:12:45.445973Z`.
- Continuation 32 was claimed once.
- Decision handoff completed before Setup.
- Setup stage began at `16:13:50.493403Z`.
- The failing transaction began at approximately `16:13:58.852938Z`.
- Setup publication began at `16:14:23.313679Z`.
- The observer failed at `16:15:59.144991Z` while configuration-anchor validation was active.

## Cleanup and evidence integrity

- Task-owned database check: `TASK_DATABASE_EXISTS=false`.
- Task-owned process check: `NO_TASK_OWNED_PROCESSES`.
- Failure evidence, environment, worker/web/provider logs, runtime invariants, native JUnit, and SQL
  telemetry were retained.
- No live provider was contacted.
- No historical run or immutable financial evidence was modified.
- No source/PIT/configuration assertion was weakened.
- The PostgreSQL threshold remained 120 seconds.
- No Chromium retry, broad release suite, commit, merge, push, or deployment occurred.

## Artifact index

- `TASK_2_9_12P_LATEST_FAILURE_FORENSICS.md`
- `TASK_2_9_12P_REGRESSION_CAUSAL_ANALYSIS.md`
- `TASK_2_9_12P_CERI_CONTRACT_RECONCILIATION.md`
- `TASK_2_9_12P_SETUP_TRANSACTION_REMEDIATION.md`
- `TASK_2_9_12P_CROSS_SYSTEM_REGRESSION_MATRIX.md`
- `TASK_2_9_12P_TARGETED_CERTIFICATION.xml`
- `TASK_2_9_12P_CHROMIUM_CERTIFICATION.xml`
- `TASK_2_9_12P_FINAL_REPORT.md`

