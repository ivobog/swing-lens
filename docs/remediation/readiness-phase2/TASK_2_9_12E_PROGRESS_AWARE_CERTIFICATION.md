# Task 2.9.12e Progress-Aware Chromium-100 Certification

## A. Executive verdict

**FAIL / NOT CERTIFIED.** The one authorized Chromium-100 test reached a real
pytest terminal result after 980.84 seconds. It did not time out. Steps 1–10 and
the complete CERI child workflow succeeded, but the continuation failed closed in
`CAPTURING_SETUP_SIGNALS` with:

```text
DECISION_MANIFEST_MISMATCH: run-start anchor no longer matches at decision handoff
```

This is an authority-integrity/dependency-continuation failure, not a recurrence of
the Technical or Setup long-lived pipeline transaction defects and not a stalled
job. No production correction or second browser run was attempted.

The strict certification also remains blocked by a separately observed 131.807-second
pre-pipeline fixture/seeding transaction. It was closed before pipeline admission and
does not prove that either repaired production boundary recurred, but it exceeds the
task's all-phases 120-second criterion and is not concealed.

## B. Timing

- Task start: `2026-10-14T19:36:19.6100072+02:00`.
- Task deadline: `2026-10-14T20:26:19.6100072+02:00`.
- Certification start: `2026-10-14T19:38:20.033580+02:00`.
- Certification end: `2026-10-14T19:54:40.776580+02:00`.
- Artifact-validation checkpoint: `2026-10-14T19:58:26.7598260+02:00`.
- Total task elapsed to that checkpoint: approximately `22m 07s` of the 50-minute
  budget; approximately `27m 53s` remained.
- Pytest runtime: `980.743` seconds; pytest summary: `980.84s (0:16:20)`.
- Outer deadline: 2,400 seconds, ending at
  `2026-10-14T20:18:20.033580+02:00`; it was not reached.
- Result: one test, one failure, exit code `1`.

Exact invocation:

```text
.venv\Scripts\python.exe -m pytest 'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-100]' --browser chromium -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12E_CERTIFICATION.xml
```

The JUnit is pytest's original output. It was not replaced with a synthetic result.

## C. Previous timeout analysis

The retained Task 2.9.12d runtime evidence contained 1,519 observations, zero reported
transaction-invariant failures, and one active job at its last observation. It did
not capture the active job identity, pipeline stage, last completed stage, progress
sequence, processed count, current item, dependency, heartbeat/lease state, CPU, or
lock state.

| Question | Retained 2.9.12d answer |
|---|---|
| Last pipeline stage / last completed stage | `UNKNOWN` |
| Last persisted progress sequence | `UNKNOWN` |
| Heartbeat advancing / lease valid | `UNKNOWN` |
| Processed count or current item advancing | `UNKNOWN` |
| Child jobs admitted/completed | `UNKNOWN` |
| Waiting on an authorized dependency | `UNKNOWN` |
| CPU work progressing | `UNKNOWN` |
| Blocked by database locks | `UNKNOWN` |
| Sufficient to distinguish progress from liveness | No |

Initial classification: **`INSUFFICIENT_PROGRESS_EVIDENCE`**. The number of monitor
samples was not treated as evidence of business progress. No definitive deadlock,
stalled checkpoint, or lost authority was demonstrated, so the one final run was
permitted.

## D. Progress timeline

| Stage | Start | End | Elapsed | Checkpoint progress | Result |
|---|---|---|---:|---|---|
| Validating Run | 19:42:24.835 | 19:42:24.991 | 0.156s | Durable step completion | COMPLETED |
| Scoring Fundamentals | 19:42:25.093 | 19:42:41.753 | 16.660s | Durable step completion | COMPLETED |
| Fetching Market Data | 19:42:41.883 | 19:42:45.273 | 3.390s | Deterministic IB boundary | COMPLETED |
| Scoring Technicals | 19:42:45.479 | 19:44:46.604 | 121.124s | Sequence 9/0 processed to sequence 51/100 processed | COMPLETED |
| Market Regime Snapshot | 19:44:46.738 | 19:44:48.543 | 1.806s | Durable step completion | COMPLETED |
| Combining Results | 19:44:48.656 | 19:45:08.342 | 19.686s | Durable step completion | COMPLETED |
| Ranking Profiles | 19:45:08.462 | 19:46:26.000 | 77.538s | 500 results across 5 profiles | COMPLETED |
| Sector Rotation Snapshot | 19:46:26.352 | 19:47:24.195 | 57.843s | Sequence 69, 100/900 observed | COMPLETED |
| CERI Provider Ingest | 19:47:24.491 | 19:52:52.900 | 328.409s | Authorized dependency; completed child count 6→14→19→21→24→26→28→31 | COMPLETED |
| Freezing Decision Handoff Manifest | 19:52:53.478 | 19:53:31.017 | 37.540s | Continuation sequence advanced 3→41 | COMPLETED |
| Capturing Setup Signals | 19:54:07.342 | 19:54:15.250 | 7.907s | Sequence 45→46; 0/100 processed | **FAILED** |

The progress-aware evidence distinguishes real work from heartbeat-only liveness:

- Technical's durable sequence and processed count advanced, and the stage completed.
- Sector rotation exposed a durable 100/900 checkpoint and completed.
- CERI was an explicit `CERI_WORKFLOW` dependency. Its completed count rose while its
  queue fell; the active provider, normalization, feature, and capture jobs each
  advanced durable item counters.
- The continuation watchdog repeatedly reported
  `HEALTHY_PROGRESS_SEQUENCE_ADVANCED`; the sequence reached 46 before the authority
  rejection.
- Test-owned worker CPU rose from 158.141 seconds at the first pipeline sample to
  804.156 seconds at the last database sample.
- PostgreSQL reported zero blocked sessions and zero lock waits in every bounded
  external observation.

The terminal browser screenshot records `FAILED`, `10 / 13 steps`, the completed CERI
dependency, failed continuation, and the exact decision-manifest error.

## E. Transaction invariants

- Existing production threshold: `120` seconds; unchanged.
- Built-in runtime observer: 1,290 observations from
  `2026-10-14T19:42:24.447780+02:00` through
  `2026-10-14T19:54:18.065860+02:00`.
- Built-in observer failures: `0`; maximum active job count: `28`; final active job
  count: `0`.
- Bounded external observations: `20`; short autocommit queries, 2-second statement
  timeout, no business writes, no locks retained, no lease or heartbeat mutation.
- Maximum externally observed transaction age while a pipeline row existed:
  `48.207s`.
- Maximum externally observed pre-pipeline fixture/seeding transaction age:
  `131.807s`; two idle-in-transaction connections were visible at that sample, with
  no lock wait or blocked session. The transaction closed before pipeline admission.
- Technical completed after 121.124 seconds without retaining one transaction across
  that stage and without a runtime invariant failure.
- Setup ran for only 7.907 seconds and failed on the decision-manifest check before
  evidence publication. No Setup transaction-age recurrence was observed.

Thus, the repaired Technical and Setup pipeline boundaries did not recur. The
pre-pipeline 131.807-second test-preparation transaction is a separate strict-criteria
failure requiring later, separately authorized fixture/boundary forensics.

## F. Population and evidence

The 100-ticker browser case was launched and progressed through CERI, but the test's
final population/evidence assertions were not reached.

Observed durable results:

- Requested ticker population: `100`.
- Ranking: `500` results across `5` profiles.
- CERI provider acquisition: `16/16` jobs and `400/400` known-total items.
- CERI normalization: `8/8` jobs and `400/400` items.
- CERI feature computation: `2/2` jobs and `100/100` items.
- CERI capture: `1/1` job and `100/100` items; capture job reported `100` score
  snapshots.
- CERI certification barrier: `3/3` jobs.
- CERI completion state: `CERTIFIED`.
- CERI continuation: `0/1` successful; failed in Setup entry.

Not reached and therefore not certified:

- Final fundamental, technical, combined, CERI, Setup, and Winner persisted-count
  assertions.
- Eligibility/exclusion assertions.
- Setup publication and Winner publication assertions.
- Retry-duplicate, cross-run contamination, and final orphan-job assertions.

The pipeline's failure diagnostic reports `ceri_score_snapshots=0` because the failed
continuation did not assimilate the certified CERI capture into the pipeline result;
the completed CERI capture job independently reports `100`. Both observations are
retained rather than conflated.

## G. Failure classification

The proven terminal failure is a **dependency-continuation authority rejection**:

1. CERI completed and certified.
2. The CERI continuation job retained active authority, a valid lease, stable worker
   instance/generation, zero retries, zero recoveries, and no stall marker.
3. The decision-handoff manifest step completed.
4. Setup capture started, then the run-start anchor comparison failed closed.

This is not a transaction timeout, deadlock, lost worker lease, provider failure, or
cardinality assertion failure. The evidence does not establish which run-start anchor
input changed, so no root cause beyond the exact invariant rejection is claimed. The
smallest next action is a separate, read-only forensic comparison of the run-start
anchor inputs at admission, post-CERI continuation, and Setup entry. It must precede
any new browser run; it was not started here.

## H. Source pins

Repository state recorded:

- Branch: `codex/ceri-authority-remediation`.
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`.
- Existing dirty working tree preserved.
- No production source changed.
- The only helper was a task-owned, behavior-neutral, read-only diagnostic.

Official validator command:

```text
.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins
```

Final result:

```json
{"stale": []}
```

No source hashes were regenerated.

## I. Runtime safety and cleanup

- Runtime ID: `20261014T173821Z-65dd66de`.
- Disposable database: `swinglens_pytest_cert_ef48c5f55c83`.
- Provider mode: deterministic/frozen; no live provider was contacted.
- The test verified its disposable database identity before Alembic.
- The fixture's shutdown completed normally after pytest failure.
- Post-test database existence check: `false`.
- All six known test-owned process PIDs: absent.
- Listeners owned by those PIDs: zero.
- Pre-existing desktop Chrome and Codex processes were recorded before execution and
  were not targeted.
- Historical Runs 22–33 were inaccessible in the fresh disposable database and were
  not modified.
- Credentials, execution tokens, complete configuration payloads, and anchor payloads
  are omitted from diagnostics.
- No reset, stash, discard, commit, push, deployment, live-provider run, baseline,
  Windows test, broad E2E run, or repository suite was performed.

## J. Artifacts

- Pytest JUnit: `TASK_2_9_12E_CERTIFICATION.xml`.
- Bounded progress diagnostic: `TASK_2_9_12E_PROGRESS_DIAGNOSTIC.json`.
- Redacted failure summary: `TASK_2_9_12E_FAILURE_DIAGNOSTIC.log`.
- Raw runtime evidence:
  `test-results/single-run-certification/20261014T173821Z-65dd66de`.
- Terminal browser screenshot:
  `screenshots/gui/020-pipeline-progress.png` within that runtime directory.
- Built-in invariant stream: `runtime-invariants.jsonl` within that runtime directory.

## K. Remaining blockers

1. The exact Chromium-100 node failed on the run-start anchor comparison during Setup
   entry; the original pytest traceback is preserved in JUnit.
2. Final 100-ticker population, evidence, duplicate, contamination, and orphan
   assertions were not reached.
3. Setup and Winner evidence publication was not reached.
4. A pre-pipeline fixture/seeding transaction was externally observed at 131.807
   seconds, exceeding the strict 120-second task criterion even though the production
   runtime observer later recorded zero failures.
5. The reason the run-start anchor no longer matched was not proven in this
   verification-only task. No speculative correction is justified from the retained
   evidence alone.

## L. Release eligibility

`TASK 2.9.12A CARDINALITY: OPEN`

`TASK 2.9.12D SETUP TRANSACTION REMEDIATION: OPEN`

`TASK 2.9.12E CHROMIUM-100 CERTIFICATION: OPEN`

`ALL 19 ORIGINAL PHASE 2.9 FAILING CASES HAVE FOCUSED PASS CERTIFICATIONS: NO`

`FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`

Production readiness and normal-runtime/live-provider readiness are not declared.
