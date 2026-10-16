# SwingLens Task 2.9.12l Final Report

## 1. Executive verdict

The Setup fingerprint blocker is reconciled and the focused PostgreSQL gate passed.
The single authorized Chromium-100 run then triggered the mandatory 120-second
PostgreSQL safety gate before terminal certification.

`TASK 2.9.12L FOCUSED CERTIFICATION: PASS`

`CHROMIUM-100: FAILED / NOT CERTIFIED`

`TASK 2.9.12L: OPEN — CHROMIUM SAFETY ABORT`

No retry, assertion change, production change, full-suite run, live-provider call,
commit, push, merge, or deployment was performed.

## 2. Fingerprint mismatch cause and proof

The retained Task 2.9.12k hashes were reproduced exactly without rerunning its
baseline test:

- fixture-order hash:
  `426cd833c04059f3aaf614ab0a9417ca42ffdd000a9188c1b2c0917efc1d3c7e`
- lexical persisted-order hash:
  `de20197073f4c90acb08fb541d604479e22cd558b15eb914fbfedf00cdc5e8c2`
- baseline session: `2026-10-13`

The reconstructed inputs each contained 100 rows and 100 unique identities.
Duplicate, missing, unexpected, and field-different row counts were all zero. The
fixture began `BHE, BLLN, KLIC, LSCC, PDFS, ACMR...`; the persisted query began
`ACMR, AVT, BHE, BLLN, DVN, F0000...`. The mismatch was therefore exclusively
array ordering.

Setup evidence is a uniquely identified record set; member position has no financial
meaning. The schema's complete snapshot identity is:

`(run_id, ticker, timeframe, data_as_of_date, engine_version, config_hash,
source_data_hash)`

The current-selection semantic key is `(ticker, timeframe, data_as_of_date)`.
The test now orders both full projections by the complete stable snapshot identity,
compares full rows and joined current-selection semantics, and only then fingerprints
the original three fields. The production serializer, production Setup publication,
fixture data, cardinality, and duplicate behavior were not changed.

## 3. Changed and generated files

- `tests/e2e/single_run_certification/test_real_writer_cardinality.py`
  - preserves separate Setup/identity and price-history Sessions;
  - adds deterministic full-identity Setup and current-selection comparison;
  - retains exception-safe transaction diagnostics.
- `TASK_2_9_12L_FINGERPRINT_FORENSICS.md`
- `TASK_2_9_12L_FOCUSED_CERTIFICATION.xml`
- `TASK_2_9_12L_TRANSACTION_DIAGNOSTIC.json`
- `TASK_2_9_12L_CHROMIUM_PROGRESS.json`
- `TASK_2_9_12L_FINAL_REPORT.md`

Chromium was forcibly interrupted for the mandatory safety abort before pytest
session finalization. Consequently, no native
`TASK_2_9_12L_CHROMIUM_CERTIFICATION.xml` exists; no synthetic XML was created.

## 4. Focused PostgreSQL certification

Exact invocation:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_pre_pipeline_preparation_closes_transactions_between_fixture_phases' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12L_FOCUSED_CERTIFICATION.xml
```

Native result: **1 passed in 83.30 seconds**, exit code `0`, JUnit tests `1`,
failures `0`, errors `0`.

Transaction result:

- controlled boundaries: 102 total;
- Setup/identity boundary: 1;
- price-history boundary: 1;
- retained-provider per-ticker boundaries: 100;
- maximum measured age: **10.960206 seconds**;
- maximum phase: `PRICE_HISTORY`;
- owner: `price-history-writer`;
- application: `task-2.9.12l-pre-pipeline-seeder`;
- monitor errors: 0;
- checked-out engine connections at completion: 0;
- checked-out observer connections at completion: 0;
- final open transactions: 0;
- final idle-in-transaction sessions: 0.

One transient idle-in-transaction writer was sampled during the bounded price-bar
write, with a maximum sampled idle age of 10.887378 seconds. It committed and closed
well below the unchanged 120-second threshold.

Evidence result:

- raw rows: 101 total, 100 in the intended run;
- Setup snapshots/current selections: 100/100;
- Winner baseline snapshots: 20;
- price bars: 64,000;
- IB contracts: 100;
- CERI companies/source records/memberships/estimate snapshots:
  100/400/400/400;
- Setup expected/persisted full rows: 100/100;
- unique complete Setup identities: 100/100;
- Setup missing/unexpected/field differences: 0/0/0;
- current-selection missing/unexpected/field differences: 0/0/0;
- duplicate, orphan, cross-ticker, cross-run, and pre-pipeline pipeline-row
  failures: all zero;
- canonical ordered Setup fingerprint:
  `de20197073f4c90acb08fb541d604479e22cd558b15eb914fbfedf00cdc5e8c2`;
- raw fingerprint:
  `6d94b2a027817bed0b0148e70fc64154bd28d71248631cca9cdf7c9f52555148`;
- CERI source fingerprint:
  `3b3b7c86a8f6e8bf4d7c5c75ed65222d523651c95b86bbaf9caffa675cfcfd52`.

The focused disposable database was removed automatically. The post-test inventory
returned to 36 databases with the unchanged digest
`905b872fc23587ba64b810b16948a092af69de64ed583308e8027b3e5b137b51`.

## 5. Chromium admission and execution

Admission checks passed before launch:

- exact ordering-only root cause proven;
- focused test and all 102 controlled boundaries passed;
- Python compilation passed;
- focused-file Ruff lint and Ruff format checks passed;
- `git diff --check` passed;
- repository `.venv` dependency preflight passed;
- T14 returned `{"stale":[]}`;
- database/process isolation was clean;
- sufficient wall-clock budget remained.

The single Chromium invocation was:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-100]' `
  --browser chromium `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12L_CHROMIUM_CERTIFICATION.xml
```

Execution ID: `20261014T231952Z-578a22d6`  
Database: `swinglens_pytest_cert_32102043ffa4`  
Provider mode: deterministic  
Fixture version: `single-run-certification-1.0.0`  
Dynamic loopback port: `7639`

An external autocommit observer was started before the test database and before
pipeline admission. Its first process found the database but exited before its first
sample because dictionary row conversion was not enabled. The corrected observer
began sampling at `2026-10-14T23:20:39.231121Z`. A later Windows file-replacement
lock caused one bounded pre-admission persistence interruption; sampling resumed at
`23:22:31.889343Z`, still before preflight consumption at `23:23:38.718958Z`.
These observer initialization events are retained in the progress artifact rather
than concealed. The Chromium pytest itself was never restarted.

## 6. Chromium safety failure

At `2026-10-14T23:23:33.249203Z`, before the pipeline row was visible to the
observer, PostgreSQL reported:

- failure code: `POSTGRES_TRANSACTION_AGE_EXCEEDED`;
- age: **120.035264 seconds**;
- phase: `PRE_PIPELINE_OR_ADMISSION`;
- backend PID: `15932`;
- application: `swinglens-test`;
- state: `idle in transaction`;
- wait: `ClientRead`;
- open transaction count in that sample: 3;
- idle-in-transaction count in that sample: 2;
- last query: a `SELECT` from `setup_signal_snapshots`.

This is a real browser-path pre-admission transaction-lifetime violation. It is not
the focused Setup fingerprint comparison, a lock wait, a deadlock, a retry, or an
observer transaction. The mandatory safety abort interrupted the existing pytest
process with exit code `1`; no retry was made.

The current evidence localizes the remaining owner to the browser run's
`swinglens-test` Setup-snapshot read path between run upload/rendering and pipeline
admission. It does not yet prove the exact Python Session/request source line, so no
speculative correction was made.

## 7. Terminal pipeline and financial snapshot

The violation occurred before the observer saw pipeline creation. In the few seconds
required to deliver the abort, pipeline 1 was admitted and reached this bounded final
snapshot:

| Stage | Terminal snapshot |
|---|---|
| `VALIDATING_RUN` | COMPLETED |
| `SCORING_FUNDAMENTALS` | RUNNING |
| Remaining 11 stages | PENDING |

Root `FULL_PIPELINE` job 1 was `RUNNING`, progress sequence 4, retry count 0,
recovery count 0, with no stall marker. Worker instance
`f77dc34a8b5545bdbe5389dd5da637a7` held the generation-bound lease. The consumed
preflight plan used `transition-run-start-anchor-v2`; no decision-handoff manifest
had been created.

Counts at abort were pre-terminal and are not certification claims:

| Evidence | Count |
|---|---:|
| Raw company rows | 101 |
| Price bars | 69,660 |
| Setup snapshots/current selections | 101/101 |
| Winner snapshots | 20 |
| CERI companies | 108 |
| CERI source records/memberships | 437/437 |
| CERI estimate snapshots | 418 |

CERI acquisition/finalization, decision handoff, production Setup capture, Winner
capture, terminal cardinality, and terminal GUI assertions were not reached. No
terminal screenshot or trace is claimed.

## 8. T14, safety, and cleanup

Preflight and final T14 both returned:

```json
{"stale":[]}
```

Before the Chromium run there were 36 unrelated disposable-named databases with
digest `905b872fc23587ba64b810b16948a092af69de64ed583308e8027b3e5b137b51`.
After the safety abort, terminal diagnostics were captured and only the exact
guard-verified database `swinglens_pytest_cert_32102043ffa4` was dropped. Final
inventory again contained 36 databases with the identical digest. Remaining
execution-owned processes: 0. Remaining listener on port 7639: 0. Ungranted locks
at the terminal snapshot: 0.

The normal SwingLens database, historical Runs 22–33, and all 36 unrelated
disposable databases were left untouched.

## 9. Remaining release blocker

Setup fingerprint reconciliation is complete, and Task 2.9.12k's focused
pre-pipeline transaction split remains certified. Chromium-100 is not certified
because a separate browser-path Setup snapshot read transaction remained idle across
pre-admission work for 120.035264 seconds.

`TASK 2.9.12L FOCUSED CERTIFICATION: PASS`

`CHROMIUM-100: FAILED / NOT CERTIFIED`

`TASK 2.9.12J CHROMIUM-100 BLOCKER: NOT RESOLVED`

`FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`

Production readiness is not declared.
