# Task 2.9.12k — Final Certification

Execution window: `2026-10-14T23:27:06.5007811+02:00` to
`2026-10-14T23:38:38.4862047+02:00` (`691.9854236s`), within the 95-minute
deadline ending `2026-10-15T01:02:06.5007811+02:00`.

## A. Executive verdict

**FAIL — focused PostgreSQL certification failed. Chromium admission was
BLOCKED.**

The exact transaction owner was proven and the minimal fixture boundary was
implemented, but the sole focused invocation failed its Setup evidence
fingerprint assertion. Per the mandatory gate, Chromium-100 was not started and
no retry or correction followed.

```text
TASK 2.9.12K PRE-PIPELINE TRANSACTION:
OPEN

CHROMIUM-100:
NOT STARTED
```

## B. Historical failure

Task 2.9.12j retained a pre-pipeline backend at `127.729317s`, PID `40828`,
`idle in transaction`, waiting on `ClientRead`, with last SQL involving
`setup_signal_snapshots`. No pipeline row existed and no lock wait was present.

## C. Exact ownership

- Fixture phase: 100-member real-writer baseline preparation.
- Session creator: the inline Session formerly in
  `test_frozen_real_writer_cardinality`.
- First SQL: `_seed_retained_ceri_companies` identity SELECT.
- Transaction begin: SQLAlchemy implicit begin on that SELECT.
- Relevant Setup SQL: 100 Setup snapshots/current selections flushed by
  `_seed_setup_lifecycle_baseline`.
- Retaining work: `_seed_price_bars` built approximately 64,000 price-bar ORM
  objects and trading dates before the caller's late commit.
- Pending writes: yes.
- Classification: fixture-owned baseline writer transaction.
- Earliest safe boundary: after complete Setup/identity publication and before
  independent price-history construction.

A new equivalent-path probe correlated Session `1870808732528` with backend PID
`34796`, `idle in transaction` / `ClientRead`, while price history was built. Its
maximum sampled age was `19.406738s`; the historical PID was not claimed as
directly retraced.

## D. Correction

Task-local source change:

- `tests/e2e/single_run_certification/test_real_writer_cardinality.py` now routes
  the browser fixture through `_prepare_real_writer_pre_pipeline_baseline`.

Before: one Session covered IB/CERI identities, Setup baseline publication, and
CPU-heavy price-history construction.

After: Setup/identity evidence commits and closes as one complete unit; price
history is materialized and committed in a fresh Session. Production code and
financial semantics are unchanged.

## E. Focused PostgreSQL certification

Exact node:

```text
tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_pre_pipeline_preparation_closes_transactions_between_fixture_phases
```

Result: **FAIL** — native exit code `1`; `1 failed` in `77.34s`; JUnit suite time
`77.322s`.

The two corrected transaction boundaries and all 100 retained-provider
boundaries closed successfully and individually measured below `120s`. Exact
cardinalities and duplicate/orphan/cross-run integrity assertions passed before
the first failure.

First failed assertion:

```text
Setup baseline fingerprint equality
actual:   de20197073f4c90acb08fb541d604479e22cd558b15eb914fbfedf00cdc5e8c2
expected: 426cd833c04059f3aaf614ab0a9417ca42ffdd000a9188c1b2c0917efc1d3c7e
```

The expected list retained original ticker order while the persisted query used
lexical ticker order; the canonical fingerprint is list-order-sensitive. The
failure is preserved unchanged. Because it occurred before final property
emission, no numeric whole-preparation maximum was exported, so the focused
transaction gate is not declared PASS.

## F. Chromium admission

| Prerequisite | Result |
|---|---|
| Exact root cause identified | PASS |
| Minimal fix implemented | PASS |
| Focused PostgreSQL regression passed | **FAIL** |
| All fixture-integrity assertions passed | **FAIL — Setup fingerprint** |
| Corrected transaction boundary directly verified | PASS at 102 controlled boundaries |
| No known pre-pipeline violation remains | NOT CERTIFIED |
| Static checks | PASS |
| `.venv` dependency preflight | PASS |
| Official T14 validator | PASS — `{"stale": []}` |
| Database/process isolation | PASS |
| No unresolved focused failure | **FAIL** |
| Sufficient time | available, but irrelevant after failed gate |

`CHROMIUM_100_ADMISSION: BLOCKED`

## G. Chromium execution

Not started. There is no Chromium JUnit, progress JSON, stage timeline, browser
assertion, screenshot, or terminal financial-cardinality claim for Task 2.9.12k.

## H. Source pins

The final official validator returned:

```json
{"stale": []}
```

No source pin was regenerated.

## I. Safety

- The focused database was guarded, disposable, and removed after failure.
- Disposable inventory returned to the prior count `36` and digest
  `905b872fc23587ba64b810b16948a092af69de64ed583308e8027b3e5b137b51`.
- No Chromium, web server, worker, pipeline, live provider, normal database, or
  historical Run 22–33 was touched.
- Existing working-tree changes were preserved.
- No commit, push, merge, reset, stash, deployment, retry, assertion weakening,
  skip, xfail, population reduction, or threshold change occurred.

## J. Final release status

`TASK 2.9.12K PRE-PIPELINE TRANSACTION: OPEN`

`TASK 2.9.12K CHROMIUM-100: NOT STARTED / NOT CERTIFIED`

`TASK 2.9.12J CHROMIUM-100 CERTIFICATION BLOCKER: NOT CLOSED`

`ALL 19 ORIGINAL PHASE 2.9 FAILING CASES: NOT YET FOCUSED PASS CERTIFIED`

`FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`

Production readiness is not declared.

