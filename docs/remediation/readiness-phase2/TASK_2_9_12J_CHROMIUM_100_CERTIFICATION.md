# Task 2.9.12j — Final Chromium-100 End-to-End Certification

## A. Executive verdict

**FAIL / NOT CERTIFIED.** The single authorized Chromium-100 process encountered a proven
pre-pipeline PostgreSQL transaction-age violation. A test-database backend remained
`idle in transaction` for **127.729317 seconds** on a `setup_signal_snapshots` read before a
`pipeline_runs` row existed. This exceeds the unchanged 120-second invariant.

The process was stopped after a bounded final diagnostic. It was not remediated or rerun.

`TASK 2.9.12J FINAL CHROMIUM-100 CERTIFICATION: OPEN`

No prior focused closure is reversed, but Tasks 2.9.12a/d/e cannot receive the requested final
Chromium closure statements from this run. Full Phase 2.9 release certification was not executed.

## B. Execution provenance

- Task start: `2026-10-14T23:03:16.7307380+02:00`
- Task deadline: `2026-10-14T23:58:16.7307380+02:00`
- Browser process start: `2026-10-14T23:04:48.0789376+02:00`
- Browser process finish: `2026-10-14T23:10:08.5588191+02:00`
- Browser outer deadline: `2026-10-14T23:44:48.0789376+02:00`
- Browser process wall time: `320.480s`
- Process result: forced safety abort, exit code `-1`
- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Interpreter: `C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe`
- Execution ID: `20261014T210501Z-1236be96`
- Isolated database: `swinglens_pytest_cert_ec6754de37bf`
- Pipeline/run/root job: pipeline `1`, upload run `3`, root `FULL_PIPELINE` job `1`

Exact single invocation:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-100]' `
  --browser chromium `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12J_CERTIFICATION.xml
```

The process ran under a 2,400-second owner harness. No baseline browser run, second Chromium
invocation, full suite, source edit, assertion edit, package installation, or retry occurred.

Pytest did not reach session finalization, so its JUnit plugin emitted no native XML. The required
XML artifact is therefore an explicitly labelled safety-abort record, not a fabricated native
pytest result or PASS.

## C. Preflight

- Repository `.venv` resolved correctly.
- Imports for `pytest`, `prometheus_client`, `sqlalchemy`, `alembic`, and `psycopg` passed.
- Initial and final T14 checks both returned `{"stale": []}`.
- The fixture created a unique `swinglens_pytest_cert_*` database and verified it was not the
  normal SwingLens database.
- The certification selected deterministic provider mode, deterministic read-only IB, frozen CERI
  providers, loopback-only dynamic port `1768`, isolated runtime directories, and the guarded
  0092 schema.
- Supervisor, web, worker, Chromium, and Playwright processes were descendants of the exact pytest
  process and carried execution ID `20261014T210501Z-1236be96`.
- Worker generation, leases, execution tokens, and the 120-second transaction monitor remained
  enabled. No execution token was exported.
- Before execution, there were 36 unrelated disposable-named databases with digest
  `905b872fc23587ba64b810b16948a092af69de64ed583308e8027b3e5b137b51` and zero
  repository-owned certification processes.

## D. Pipeline timeline

The first failed invariant occurred in pre-pipeline/admission work, before pipeline creation.
The later pipeline was allowed to reach a bounded diagnostic solely long enough to preserve state;
its health does not cure the earlier failure.

| Stage | Start | End | Duration | Progress | Result |
|---|---|---|---:|---|---|
| Deterministic environment / fixture | `23:05:23+02:00` supervisor boot | before `23:08:52+02:00` | not represented by one stage row | 100 intended upload members prepared | **FAIL:** transaction age reached 127.729317s during pre-pipeline/admission |
| `VALIDATING_RUN` | `23:08:52.744460+02:00` | `23:08:52.923487+02:00` | 0.179s | completed | COMPLETED |
| `SCORING_FUNDAMENTALS` | `23:08:53.207055+02:00` | `23:09:08.592949+02:00` | 15.386s | 100 rows persisted | COMPLETED |
| `FETCHING_MARKET_DATA` | `23:09:08.720948+02:00` | `23:09:11.971362+02:00` | 3.250s | deterministic IB path | COMPLETED |
| `SCORING_TECHNICALS` | `23:09:12.109364+02:00` | not completed | 53.749s through last durable progress | sequence 31; 100/100; last completed `RDVT` | RUNNING when stopped |
| `MARKET_REGIME_SNAPSHOT` | not started | — | — | — | PENDING |
| `COMBINING_RESULTS` | not started | — | — | — | PENDING |
| `RANKING_PROFILES` | not started | — | — | — | PENDING |
| `SECTOR_ROTATION_SNAPSHOT` | not started | — | — | — | PENDING |
| `CERI_PROVIDER_INGEST` | not started | — | — | — | PENDING |
| `FREEZING_DECISION_HANDOFF_MANIFEST` | not started | — | — | — | PENDING |
| `CAPTURING_SETUP_SIGNALS` | not started | — | — | — | PENDING |
| `EVALUATING_SETUP_LIFECYCLES` | not started | — | — | — | PENDING |
| `CAPTURING_WINNER_PREDICTIONS` | not started | — | — | — | PENDING |

The last durable checkpoint was root job `1`, `SCORING_TECHNICALS`, sequence `31`, processed
`100/100`, current item `VALIDATING_EVIDENCE:<safe-digest>`, last completed item `RDVT`, last
progress `2026-10-14T23:10:05.858318+02:00`. Generation-1 worker heartbeat was current and its
lease extended to `23:12:05.872317+02:00`; retry and recovery counts were zero.

## E. CERI evidence

The in-pipeline CERI stage was not reached. At abort, the database contained fixture/baseline
totals of 108 CERI companies, 437 source records, 437 ingestion-run memberships, 418 estimate
snapshots, and one baseline score snapshot. These are pre-terminal totals and do **not** certify
the required 600 exact provider-source rows, pipeline capture, finalization, change detection,
alerts, or downstream assimilation. No CERI child jobs existed at the last snapshot.

## F. Decision handoff

- Preflight plan `1` was consumed by pipeline `1`.
- Its anchor contract was `transition-run-start-anchor-v2`.
- No `DECISION_MANIFEST_MISMATCH` occurred before the stop.
- `FREEZING_DECISION_HANDOFF_MANIFEST` was still pending.
- No transition decision-handoff manifest existed.
- Production Setup admission and same-manifest validation were not reached.
- No anchor or manifest was rewritten.

Therefore Task 2.9.12i remains focused-certified, but this run supplies no terminal browser proof
of the complete handoff.

## G. PostgreSQL transaction observations

Bounded diagnostic reads used independent autocommit connections and retained no transaction.

| Phase | Maximum measured age | Violations |
|---|---:|---:|
| Pre-pipeline fixture/admission | **127.729317s** | **1** |
| Production pipeline | no numeric maximum exported; final bounded sample had no open transaction and 133 continuous observer records had no failures | 0 observed after pipeline creation |
| Final publication | not reached | not evaluated |

The pre-pipeline samples progressed from `0.126958s` to `38.628828s`, `97.641897s`, and finally
`127.729317s`. The violating backend was PostgreSQL PID `40828`, state
`idle in transaction`, wait `ClientRead`, and its last query selected from
`setup_signal_snapshots`. At the violating sample:

- no `pipeline_runs` row existed;
- no active background job existed;
- ungranted lock count was zero.

This is a transaction-lifetime failure at the pre-pipeline/admission boundary. It is not a lock
wait, deadlock, observer transaction, heartbeat-only stall, worker-generation rejection, or the
prior manifest mismatch.

## H. Financial evidence and cardinality

The exact browser cardinality assertions were not reached. The last preserved totals were:

| Evidence | Count at abort | Certification interpretation |
|---|---:|---|
| Raw company rows | 101 | 100 intended run members plus one decoy/baseline row |
| Fundamental scores | 100 | stage completed |
| Technical scores | 0 | technical stage had not committed final rows |
| Combined results | 0 | not reached |
| Ranking results | 0 | not reached |
| Market regime snapshots | 0 | not reached |
| Sector rotation snapshots | 0 | not reached |
| CERI score snapshots | 1 | baseline only; pipeline capture not reached |
| Setup snapshots | 101 | seeded baseline population; pipeline Setup publication not reached |
| Winner prediction snapshots | 20 | baseline evidence; pipeline Winner publication not reached |
| Decision handoff manifests | 0 | not reached |

No duplicate, cross-run, orphan, terminal publication, or final cardinality claim is made from
these incomplete totals.

## I. Browser verdict

The Chromium process was real and owned by the certification process tree, but the expected
terminal pipeline state and original GUI/database assertions were not reached. The terminal
pipeline screenshot checkpoint occurs after terminal status and was therefore not produced.
No screenshot or native JUnit is claimed where none exists.

The certification result is based on the independently observed mandatory invariant failure,
not on a page showing progress. Pytest was stopped with exit code `-1` after evidence capture.

## J. Safety and cleanup

- Only the exact pytest-owned process tree was terminated: pytest, Playwright driver, Chromium,
  supervisor, web, worker launcher, and worker.
- Remaining execution-owned processes: `0`.
- Remaining listener on test port `1768`: `0`.
- The exact guarded database `swinglens_pytest_cert_ec6754de37bf` was dropped with force after all
  diagnostics were captured.
- The post-cleanup disposable inventory returned to count `36` and the exact pre-run digest
  `905b872fc23587ba64b810b16948a092af69de64ed583308e8027b3e5b137b51`.
- The 36 unrelated databases were not deleted or reused.
- No normal application database, historical Run 22–33, or unrelated local process was changed.
- No live IBKR, SEC, EODHD, or CERI provider was contacted.
- Existing working-tree changes were preserved; no reset, stash, discard, merge, commit, push, or
  deployment occurred.
- One cleanup guard attempt failed safely before deletion because its SQLAlchemy driver suffix was
  incomplete; the corrected fixture-equivalent `postgresql+psycopg` guard then verified and
  removed only the exact task database.

Retained runtime evidence is under
`test-results/single-run-certification/20261014T210501Z-1236be96/`. It contains environment,
migration, deterministic-provider, lifecycle, supervisor, worker, and 133 pipeline invariant
observations. The terminal screenshot/trace was not produced before the safety abort.

## K. Source pins

Preflight and final official validation both returned:

```json
{"stale": []}
```

No source pin was regenerated and no production or test source was changed.

## L. Remaining blocker and next action

Chromium-100 remains open. The smallest justified follow-up is a focused forensic/remediation task
for the pre-pipeline/admission transaction that retained a Setup snapshot read transaction beyond
120 seconds. It must identify the exact Session owner before any code change. Do not rerun
Chromium-100 until that boundary is focused-certified.

## M. Release statement

`TASK 2.9.12J FINAL CHROMIUM-100 CERTIFICATION: OPEN`

`ALL 19 ORIGINAL PHASE 2.9 FAILING CASES HAVE FOCUSED PASS CERTIFICATIONS: FALSE`

`FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`

Production readiness is not declared.

