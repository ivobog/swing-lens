# Task 2.9.4 — Durable Decision-Graph Recovery

## A. Verdict

**PARTIAL.** The original worker-generation ownership defect was reproduced and narrowly corrected. The single permitted post-fix certification advanced past that defect and proved restart recovery of the root graph, but then failed on a separate stale probe expectation. Per the task directive, no second repair/retest cycle was started.

## B. Duration

- Start: `2026-10-14T10:53:23.8585943+02:00`
- Finish: `2026-10-14T10:59:05.4806342+02:00`
- Elapsed: `00:05:41.6220399`

## C. Root cause

The baseline failed before the simulated root-worker restart at the prerequisite assertion (`test_phase4_configuration_certification_postgresql.py:117`). CERI feature rebuild job 2 completed and created capture job 4, but job 4 was returned to `QUEUED` with `worker registration 't13e-native-<pid>' is owned by another process`.

`run_worker_once()` allowed its caller to omit `worker_instance_id`. `register_worker()` then generated/retained the durable instance identity, but `run_worker_once()` discarded it and passed `str(None)` to detached CERI control-plane heartbeat/progress. The registry correctly fenced that value as a competing generation. This is **incorrect process ownership**, not lost configuration, source, or decision authority.

The narrow correction retains the `BackgroundWorker` returned by registration and adopts its `instance_id` for the claim and all detached control-plane operations. The post-fix run passed the former assertion, recovered the abandoned root job with a new token, completed it, and preserved the frozen anchor.

The post-fix run later failed at line 158. The old `queue-downstream` probe enqueued a second `WINNER_PREDICTION_CAPTURE` after its owning pipeline was already terminal. The production execution-authority predicate correctly left that pipeline-owned job (id 8) `QUEUED`; jobs 1–7 were `COMPLETED`, and one authoritative Winner snapshot already existed from the real pipeline. This is a second **outdated fixture expectation**. The retained trace is the stopping point required by the task.

## D. Restart/recovery comparison

| Invariant | Before | After |
|---|---|---|
| Frozen decision-graph identity | Root restart was not reached; prerequisites stopped on the fenced CERI capture | Root recovered and completed; queued/executed anchor equality assertions passed (`9967113278629e3e9709698bb6b761aebf27ad2f201a9e163a828bcf37e22851` in retained post-fix state) |
| Configuration identity | Frozen anchor was present on queued work | Same frozen anchor remained on recovered root and downstream jobs |
| Calculation/source identity | CERI feature work completed, but capture continuation was fenced | Evidence contained `FUNDAMENTAL`, `TECHNICAL`, `COMBINED`, `RANKING`, `REGIME`, `SECTOR`, `CERI`, `IBMI`, and `SETUP`; cutoff/source assertions before line 158 passed |
| Worker ownership | Generated registration identity was discarded; detached heartbeat used `"None"` and was fenced | Registration identity became canonical; abandoned root used a different recovery token and completed |
| Continuation outcome | CERI capture remained queued after an ownership error | Root and jobs 2–7 completed; only the fixture's post-terminal duplicate Winner request remained queued by design |
| Duplicate authoritative work | No duplicate was published | One Winner snapshot existed; the second manual request was not authorized or executed |
| Live provider attempts | None | None; synthetic IB status and offline dependency replacement remained in force |

## E. Files changed

- `app/services/background_worker.py` — adopt the durable instance identity returned by `register_worker()` inside `run_worker_once()`.
- `docs/remediation/readiness-phase2/TASK_2_9_4_BASELINE.xml` — retained baseline JUnit.
- `docs/remediation/readiness-phase2/TASK_2_9_4_CERTIFICATION.xml` — retained post-fix JUnit.
- `docs/remediation/readiness-phase2/TASK_2_9_4_DECISION_GRAPH_RECOVERY.md` — this bounded report.

No historical fixture, scoring policy, database migration, provider, supervisor, or unrelated test was changed.

## F. Focused tests

| Run | Command | Runtime | Result | JUnit |
|---|---|---:|---|---|
| Baseline | `.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_phase4_configuration_certification_postgresql.py::test_public_upload_enqueue_process_restart_native_worker_and_frozen_history[decision-graph]' -q --junitxml=docs/remediation/readiness-phase2/TASK_2_9_4_BASELINE.xml` | 39.706 s suite / 38.918 s case | FAIL at line 117; prerequisite ownership fence | `TASK_2_9_4_BASELINE.xml` |
| Certification | same exact node, writing `TASK_2_9_4_CERTIFICATION.xml` | 76.832 s suite / 76.214 s case | FAIL at line 158; post-terminal duplicate Winner fixture job remained unauthorized | `TASK_2_9_4_CERTIFICATION.xml` |

No additional test process was started.

## G. Safety verification

- PostgreSQL: both runs used generated disposable databases (`swinglens_pytest_2e1f9d300413` and `swinglens_pytest_eb571cbe2ff6`) through the repository fixture.
- Providers: the probe supplied synthetic IB health, patched `PipelineExecutionDependencies` before worker execution, seeded recorded rows, and exposed no SEC/EODHD/IB socket path. No provider connection attempt appeared in retained output.
- Processes: subprocess calls had 180/360-second limits; no test-owned Python/probe process remained after pytest teardown.
- Frozen history: configuration lived under pytest temp roots; invalid C2 files tested retained delivery. Runs 22–33 and the normal application database were untouched.
- PIT/immutability: the post-fix run reached root completion with the original anchor and frozen evidence kinds. It failed before final historical equality/replay assertions, so those final assertions are not certified by this run.
- Source pins: `.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins` completed but reported `app/services/background_worker.py` as stale. No official pin regeneration was attempted after the required stop, so source-pin certification is not green.

## H. Remaining blockers

Current task:

1. Update the stale decision-graph probe so it does not enqueue a new pipeline-owned Winner capture after terminalization (the real pipeline already published one), while retaining a negative assertion that terminal pipeline authority rejects new work.
2. Run the official reviewed source-pin regeneration/verification for the worker boundary.
3. Re-run this exact node once in a new bounded task to certify final history/replay assertions.

Unrelated Phase 2.9 resume, Fundamental retry, heartbeat, Winner publication, and deterministic-recovery failures remain unresolved and were not investigated.

## I. Recommended next single task

Reconcile the decision-graph test fixture with terminal pipeline execution authority, retain the explicit rejection proof for post-terminal Winner work, refresh the reviewed worker source pin, and certify only this exact node. Do not start any wider recovery suite.
