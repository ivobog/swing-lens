# SwingLens Task 2.9.12q Final Report

## Status

| Gate | Result |
|---|---|
| Composed real PostgreSQL Setup-100 | PASS: 1/1 |
| Session/backend ownership | VERIFIED for the guarded equivalent run |
| Financial parity | PASS: prepared and published fingerprints identical |
| Cross-system negative/Winner gate | PASS: 203/203, no skips or xfails |
| Static and T14 | PASS; `{"stale":[]}` |
| Original Chromium-100 | **FAIL: 1/1, not retried** |
| Phase 2.9 release | **NOT CERTIFIED** |

Task start was `2026-10-15T19:26:39.3211294+02:00`; the absolute 150-minute deadline was
`2026-10-15T21:56:39.3211294+02:00`.

## Required answers

1. **Which exact Session held the violating transaction?** The guarded equivalent composed run
   proves parent Setup Session `1430738281648`, SQLAlchemy root transaction `1431343082000`, backend
   `34480`. The historical PID 12344 Session ID is unrecoverable. The new Chromium failure used
   backend `18600`; its Python Session ID was not captured because the mandated original node's
   harness had transaction tracing disabled.
2. **Why did it remain open?** Full per-member native recomputation, configuration/source/evidence
   validation, evidence/source-edge publication, projection advancement, and ORM flush work ran
   after the parent acquired publication authority. Most elapsed time was application processing,
   not SQL execution.
3. **What consumed time?** Baseline: 5,150 queries, 4,488 duplicates, 902 configuration loads,
   207 evidence loads, 403 flushes, and 6.922818 seconds of SQL. Final traced root: 1,441 SQL
   operations, 4.614746 seconds SQL, 51.198767 seconds application work, 403 flushes.
4. **How many repeated queries were eliminated?** 2,556 total and 1,966 classified duplicate
   queries; configuration loads fell by 870.
5. **Before/after durations?** Composed max transaction 68.094043 to 55.611459 seconds; an
   uninstrumented optimized run measured 41.358952 seconds. The final Chromium transaction still
   hit 120.005556 seconds.
6. **How is financial authority verified?** Independent production recomputation occurs in bounded
   Sessions, followed by exact locked revalidation of source rows, immutable evidence, PIT prices,
   configuration, history/current selections, run/context, handoff, and prepared payload. Prepared
   and persisted projection fingerprints both equal
   `7fe60a11e1cf527ce4bcbbff91175be6369689008d2b80906a40216054ea4bd9`.
7. **Is publication atomic?** Yes. Evidence, source edges, Setup snapshots, and current projections
   remain in one parent transaction; negative tests prove whole-batch rollback.
8. **Did the true composed Setup-100 test pass?** Yes: 100 snapshots, 100 immutable evidence rows,
   2,594 queries, 6.276045 seconds total SQL, 55.611459 seconds max transaction.
9. **Did Setup and Winner integration pass?** The focused production-backed gate passed, including
   Winner consumption. Full-scale E2E Winner was not reached.
10. **Did Chromium-100 pass?** No. The single original run failed at Setup transaction age
    120.005556 seconds. It was not retried.
11. **Did the complete Phase 2.9 suite pass?** Not run because Chromium failed; release remains
    `NOT CERTIFIED`.
12. **What remains?** Stabilize final Setup evidence/source-edge publication under complete worker
    load, retain Python Session tracing on the original node, export DB diagnostics on observer
    exceptions before teardown, then obtain a newly authorized Chromium PASS. Full release remains
    separately uncertified.

## Native evidence

- `TASK_2_9_12Q_COMPOSED_SETUP_100_CERTIFICATION.xml`: 1 passed, 0 failed/errors/skipped,
  390.705 seconds.
- `TASK_2_9_12Q_TARGETED_CERTIFICATION.xml`: 203 passed, 0 failed/errors/skipped, 592.502 seconds.
- `TASK_2_9_12Q_CHROMIUM_CERTIFICATION.xml`: 1 failed, 0 errors/skips, 1230.30 seconds.
- Compileall: PASS.
- Ruff lint: PASS.
- Ruff format: PASS (10 files).
- `git diff --check`: PASS with line-ending warnings only.
- Official T14 current-overlay validator: `{"stale":[]}`.

## Chromium failure identity and cleanup

- Execution: `20261015T191419Z-013522b1`
- Upload run: `3`; pipeline: `1`; background job: `32`
- Worker PID: `14300`; backend PID: `18600`
- Root correlation: `root-5ee4aa88ab01475dadfd1030272aae5c`
- Stage: `CAPTURING_SETUP_SIGNALS`
- First invariant: `POSTGRES_TRANSACTION_AGE_EXCEEDED` at 120.005556 seconds
- State/wait: `idle in transaction`, `ClientRead`
- Last SQL family: `INSERT core_calculation_evidence_sources`
- Task database exists after teardown: `false`
- Task-owned processes remaining: `0`

No live provider was contacted. No historical run/evidence was rewritten. No threshold or financial
assertion was weakened. No commit, reset, stash, merge, push, deployment, or Chromium retry occurred.
