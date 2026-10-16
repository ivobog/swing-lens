# SwingLens Task 2.9.12r Final Report

## Result

Task 2.9.12r corrected the known Setup evidence/source-edge publication defect, but the
complete 100-ticker pipeline is **not certified**. The one authorized Chromium-100 run
failed at the 120-second PostgreSQL transaction invariant during Setup; it was not retried.

| Gate | Result |
|---|---|
| Focused Setup regressions | PASS: 38/38 |
| Real PostgreSQL Setup-100 | PASS: 1/1; 100 snapshots and 100 evidence rows |
| Financial parity | PASS: prepared and published fingerprints identical |
| Static/Ruff/T14 | PASS; `{"stale":[]}` |
| Original Chromium-100 | **FAIL: 1/1; not retried** |
| Setup in Chromium | **NOT COMPLETED** |
| Winner in Chromium | **NOT REACHED** |

## Exact bug and correction

The Setup batch writer performed evidence, source-edge, and current-projection ORM flushes
for every member while holding the single atomic publication transaction. At 100 members
this produced 403 flushes, and the durable worker crossed the 120-second limit during an
`INSERT` into `core_calculation_evidence_sources`.

The correction keeps the same atomic boundary and authority checks while changing only how
the graph is staged:

- PostgreSQL evidence IDs are reserved before the batch write.
- Setup evidence and source edges are staged without per-member flushes.
- The evidence graph is flushed once, current projections are advanced only after their
  targets exist, and projections are flushed once.
- The deferred path is restricted to Setup; cancellation or any validation failure still
  rolls back the whole batch.

The corrected 100-member path used five flushes. Prepared and published financial output
fingerprints were identical, and the existing rollback, altered-retry, concurrent capture,
source-mutation, and transaction-boundary tests passed.

Changed files:

- `app/services/core_calculation_evidence.py`
- `app/services/setup_lifecycle/decision_evidence.py`
- `app/services/setup_lifecycle/repository.py`
- `tests/integration/test_run8_setup_performance_postgresql.py`
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json`

## Focused verification

`TASK_2_9_12R_SETUP_REGRESSION.xml` records 38 passed, zero failures/errors/skips in
24.391 seconds. Three additional directly affected PostgreSQL rollback/source-authority
tests passed in 56.15 seconds.

`TASK_2_9_12R_FOCUSED_CERTIFICATION.xml` records the production Setup capture service with
100 realistic members and real PostgreSQL: 1 passed, zero failures/errors/skips in 294.106
seconds. It persisted 100 snapshots and 100 immutable Setup evidence rows, used five
flushes, and observed a maximum parent transaction age of 40.412994 seconds. The prepared
and published financial fingerprint was
`83095128355e5957278cc7711a3918f5b74b57f6ac3b7c801803d83a06d27e55`.

One post-failure trace reran only this same focused regression and passed in 314.66 seconds.
It observed a 38.437282-second maximum PostgreSQL transaction age and a 46.70-second traced
parent root lifetime. All bounded child verification transactions were short; the longest
was 6.36 seconds. Its prepared/published fingerprint also matched exactly. This establishes
that the Chromium violation belongs to the atomic parent publication boundary, not a leaked
source-loading or handoff-verification child transaction.

## Chromium-100 outcome

The original test was executed exactly once. Native evidence is
`TASK_2_9_12R_CHROMIUM_CERTIFICATION.xml`: 1 failed, zero errors/skips, 1132.205 seconds.

- Execution: `20261015T202628Z-f641e3a8`
- Upload run: `3`; pipeline: `1`; background job: `32`
- Root correlation: `root-752486fd96fa45d3a356a53d5e8773d7`
- Worker PID: `41500`; violating backend PID: `39664`
- Last committed stage: `FREEZING_DECISION_HANDOFF_MANIFEST`
- Failing stage: `CAPTURING_SETUP_SIGNALS`
- First invariant: `POSTGRES_TRANSACTION_AGE_EXCEEDED`
- Observed age: `120.211225` seconds
- State: `idle in transaction`
- Last SQL: frozen `market_calculation_contexts` lookup
- Setup publication: not committed
- Setup lifecycle: not started
- Winner: not reached

The previous per-member source-edge insertion signature is gone, but under the full durable
worker/browser load the remaining atomic parent publication transaction still exceeded the
limit. The focused trace proves this is not an unclosed preparation/verification child.
Further splitting that transaction would break atomic publication and source-authority
locking; bypassing or moving its remaining financial verification would change financial
transaction semantics. Task 2.9.12r therefore stops at this concrete tradeoff instead of
making a speculative change. A second complete E2E run requires explicit authorization.

## Static checks and cleanup

- `compileall app tests`: PASS
- Ruff lint on the four changed Python files: PASS
- Ruff formatting check: PASS
- `git diff --check`: PASS (line-ending warnings only)
- Official T14 validator: PASS, `{"stale":[]}`
- Disposable database `swinglens_pytest_cert_404f894e4f32` exists after teardown: `false`
- Task-owned PIDs `23312`, `41500`, `9372`, and `10492` remaining: `0`
- Deterministic providers only; no live provider access

The branch and pre-existing dirty worktree were preserved. No reset, stash, discard, commit,
merge, push, deployment, threshold increase, financial assertion weakening, historical
evidence rewrite, or Chromium retry occurred.

