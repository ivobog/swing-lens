# Task 2.9.12 E2E Remediation Summary

## A. Executive verdict

**PARTIAL / RELEASE BLOCKED.** Three of the four exact cases passed. The Chromium
100-cardinality case failed its single allowed final execution at a new downstream
transaction checkpoint, so Task 2.9.12a is not closed.

| Case | Root cause | Fix type | Final test | Verdict |
|---|---|---|---|---|
| Cardinality Chromium 100 | Original Technical CPU interval held a read transaction; after that fix, Setup capture retained a frozen configuration-anchor transaction for 120.398 s. | Production Technical transaction boundary; downstream blocker remains | Exact Chromium 100 | **FAIL** |
| Windows worker recovery `[0]` | Readiness and web replacement used hard-coded port 8000 after worker recovery had already succeeded. | Fixture port/ownership isolation | Exact parameter `[0]`, 69.639 s | **PASS** |
| Windows worker recovery `[1]` | Same wrong-port fixture defect; the test otherwise recovered independently. | Fixture port/ownership isolation | Exact parameter `[1]`, 66.712 s | **PASS** |
| Windows controller exit/generation | Fixture used the canonical repository lifecycle-state file instead of task-owned state. | Fixture state-path propagation | Exact controller case, 103.544 s | **PASS** |

## B. Timing

Overall Task 2.9.12 started at `2026-10-14T15:50:25.0701281+02:00` and finished
reporting at approximately `2026-10-14T16:24:00+02:00`, within the hard deadline
`2026-10-14T17:00:25.0701281+02:00`.

| Work unit | Start | Test finish | Test elapsed | Budget |
|---|---|---|---:|---:|
| 2.9.12a | 15:55:56+02:00 | 16:16:10+02:00 | 1157.047 s | 25 min |
| 2.9.12b | 16:17:05+02:00 | 16:19:34+02:00 | 136.587 s | 25 min |
| 2.9.12c | 16:20:15+02:00 | 16:22:10+02:00 | 103.754 s | 20 min |

No fresh baseline was run. Each exact case received one final execution only.

## C. Transaction remediation

The original retained Chromium failure occurred at `120.404372` seconds with the
backend `idle in transaction` after loading the frozen `market_calculation_contexts`
row. The Technical pure-sequential path loaded a complete database-backed work item
then ran CPU calculation before ending its transaction.

The correction closes a pipeline-owned read transaction after each immutable
`TechnicalWorkItem` is materialized and before CPU work. Independent local
artifact-cache writes close before the next ticker calculation. The complete
Technical score/evidence set is still published by the existing fenced, atomic
`finalize_technical_scores` transaction; execution tokens, configuration identity,
PIT identity, source manifests, and rollback behavior remain enforced.

The final run passed the unchanged transaction monitor through Technical scoring,
the asynchronous provider/normalization/feature graph, CERI finalization, CERI
capture, change detection, alert rebuild, and decision-handoff freezing. It then
failed at `2026-10-14T14:15:48.099097+00:00` during
`CAPTURING_SETUP_SIGNALS`. A different backend was `idle in transaction` for
`120.397988` seconds after selecting the exact `execution_configuration_anchors` row.

The observer stopped the pipeline before terminal completion, so the 100-member
financial counts, final frozen membership checks, and authoritative final publication
assertions were not reached. They are not certified. The invariant was not weakened.

## D. Windows worker recovery

Each parameter selected a fresh loopback port, confirmed no listener, propagated the
port through `Settings` and child environment, and used it consistently for readiness,
listener ownership, controlled web termination, replacement, and recovered readiness.

Both cases proved this sequence:

1. supervisor generation 1 and worker generation 1 registered;
2. the test killed only the worker PID owned by that supervisor;
3. the supervisor recorded `DURABLE_WORKER_PROCESS_LOST` and restart count 1;
4. worker generation 2 registered under a new instance;
5. the resumed 50-checkpoint job and a new 3-checkpoint job completed;
6. readiness reported `supervisor`, `web`, `worker`, and `topology` as `ok`;
7. the test killed only the proven child web PID;
8. the supervisor recorded `WEB_CHILD_EXITED`, restart count 1, and recovered
   readiness on the same isolated port.

Both state files ended with `cycle_failed=false`, worker/web state `RUNNING`, and no
crash loop. Parameter `[1]` used independent database, state, port, runtime instance,
and configuration identity rather than state leaked from `[0]`.

## E. Windows controller lifecycle

The retained fixture asserted against, read, and deleted
`data/cache/swinglens-lifecycle.json`. It now creates its lifecycle JSON beneath
pytest `tmp_path`, passes `SWINGLENS_LIFECYCLE_STATE_PATH`, and points the controller
subprocess and imported production-equivalent probe to that same file.

The passing case proved that children survived the bounded 45-second controller-exit
window; a second controller reused the exact runtime instance, supervisor, web
identity, and database registrations; role cardinality stayed one per role; generation
1 was reused without stale-state retirement; and graceful stop emitted all required
shutdown events. All isolated ports, processes, and the task-owned lifecycle file were
gone at teardown. The canonical repository lifecycle path was unchanged.

## F. Source changes

- `app/services/technical_score_service.py` — close pipeline Technical read/cache
  transactions around CPU-only work without changing atomic score publication.
- `tests/e2e/test_windows_durable_worker_recovery.py` — allocate and propagate a
  test-owned web port; parameterize readiness and listener ownership.
- `tests/e2e/test_windows_lifecycle_persistence.py` — use one task-owned lifecycle
  state path across controller and probe processes.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json` —
  regenerated by the official current-overlay generator after transaction review.
- Three JUnit files, three unit reports, this summary, and the redacted 2.9.12a
  failure checkpoint were added as evidence.

All pre-existing dirty-worktree changes from Tasks 2.9.1–2.9.11 were preserved.

## G. Certification

| Exact command target | JUnit | Suite result |
|---|---|---|
| `test_real_writer_cardinality[chromium-100] --browser chromium` | `TASK_2_9_12A_CERTIFICATION.xml` | 1 failed; 1157.047 s |
| `test_windows_worker_kill_self_heals_repeatedly[0]` and `[1]` | `TASK_2_9_12B_CERTIFICATION.xml` | 2 passed; 136.587 s |
| `test_windows_runtime_survives_controller_exit_and_reuses_generation` | `TASK_2_9_12C_CERTIFICATION.xml` | 1 passed; 103.754 s |

The exact commands are reproduced in the corresponding unit reports. No broad E2E
or repository suite was started.

## H. Compatibility

The Windows changes are fixture-only and do not alter production lifecycle defaults.
They certify the existing worker-generation, lease, execution-token, readiness, and
generation-reuse contracts under isolated resources.

The Technical transaction change is a shared production boundary added after prior
Phase 2.9 certifications. It retains source/configuration identity and fenced atomic
publication, and the final browser execution demonstrated progress beyond Technical.
However, the exact 100-cardinality contract is not recertified because the downstream
Setup transaction failed. No prior broad certification is silently claimed for this
new source state; a new focused Chromium case is required after the remaining Setup
fix.

## I. T14

The reviewed operation family is core Technical scoring (`WRITE-023`, `WRITE-024`,
and independent `WRITE-025` cache artifacts). The new checkpoints end immutable-input
reads and independent cache transactions; authoritative Technical score/evidence
publication remains at the existing fenced writer boundary.

The official command was used:

```text
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins --write
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins
```

Final result: `{"stale": []}`.

## J. Safety

- Every execution used a generated disposable PostgreSQL database; the normal
  SwingLens database and historical Runs 22–33 were not accessed or mutated.
- Browser providers were deterministic/frozen. Windows provider ingest, automatic
  Winner work, and market prewarm were disabled. No live SEC, EODHD, IBKR, or CERI
  provider was called.
- Browser/server/worker/controller processes used test identities and bounded waits.
- Windows web termination was restricted to a PID proven to descend from the
  test-owned supervisor; no global or process-name kill was used.
- Post-test inspection found no matching browser certification, worker-recovery, or
  controller-lifecycle process. Test-owned ports had no listeners.
- The failed browser database was removed by teardown. No credentials or execution
  tokens appear in the reports.
- No reset, stash, discard, merge, push, deployment, full regression, or full Phase
  2.9 release certification occurred.

## K. Remaining blockers

1. **Chromium 100 remains FAIL.** The Setup capture path holds a transaction after
   loading `execution_configuration_anchors` while performing more than 120 seconds
   of work. The next separately authorized task must materialize its frozen inputs,
   close the read transaction before long work, and revalidate configuration plus
   execution ownership at publication.
2. After that correction, run the exact Chromium 100 case once and require all
   cardinality, source/PIT, publication, transaction, cleanup, and offline-provider
   assertions to pass.
3. Only after all original cases have focused PASS evidence should a separately
   authorized repository regression, skip audit, source-authority review, and release
   review begin.

`TASK 2.9.12B WINDOWS WORKER RECOVERY: CLOSED`

`TASK 2.9.12C WINDOWS CONTROLLER LIFECYCLE: CLOSED`

Task 2.9.12a has no closure statement.

## L. Release status

Only three of the four cases passed, so the 19-case focused-certification closure is
not claimed.

`FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`

The application is not declared ready for live operation.
