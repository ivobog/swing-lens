# Task 2.9.12k — Pre-Pipeline Transaction Forensics

Task start: `2026-10-14T23:27:06.5007811+02:00`  
Task finish: `2026-10-14T23:38:38.4862047+02:00`  
Absolute deadline: `2026-10-15T01:02:06.5007811+02:00`  
Elapsed: `691.9854236s`

## A. Historical incident

Task 2.9.12j observed PostgreSQL backend PID `40828` idle in transaction for
`127.729317` seconds, above the unchanged `120`-second limit. Its last retained
query referenced `setup_signal_snapshots`, no pipeline row existed, and no lock
was waiting. That database and process no longer exist, so this task did not
claim to resample or directly correlate the historical PID.

## B. Exact owner

The owner was the fixture Session formerly created inline by
`test_frozen_real_writer_cardinality` in
`tests/e2e/single_run_certification/test_real_writer_cardinality.py`.

The causal chain was:

```text
100-member real-writer fixture preparation
  -> test_frozen_real_writer_cardinality
  -> one SQLAlchemy Session
  -> _seed_retained_ceri_companies SELECT starts the transaction
  -> IB contracts and CERI identities are flushed
  -> _seed_setup_lifecycle_baseline flushes 100 Setup snapshots/selections
  -> _seed_price_bars performs CPU-heavy trading-date and ORM construction
     for 100 tickers x 2 price bases x 320 bars
  -> the same Session and PostgreSQL transaction remain open
  -> final selection and price-bar writes remain pending until one late commit
```

This is failure class **B — baseline writer transaction**. It is fixture-owned,
not a production helper commit defect. The production/fixture helpers correctly
use their caller's Session and do not silently commit unrelated work.

The transaction begins at the first database operation in
`_seed_retained_ceri_companies`. The last completed SQL before the long Python
interval is emitted by `_seed_setup_lifecycle_baseline`. The caller then retains
the Session while `_seed_price_bars` computes dates and constructs approximately
64,000 `PriceBar` ORM objects. Cross-domain atomicity between the completed Setup
baseline and independent price history is not required.

## C. New equivalent-path correlation

A disposable, test-scoped pre-fix probe exercised the exact old 100-member path.
It correlated:

- Python Session identity: `1870808732528`
- PostgreSQL backend PID: `34796`
- backend state: `idle in transaction`
- wait: `ClientRead`
- longest sampled transaction age: `19.406738s`
- Python price-history interval: `18.882826s`
- pending writes during the interval: yes
- last sampled relation family: `setup_signal_snapshot_current_selections`

The historical sample ended on `setup_signal_snapshots`; the equivalent probe
ended on the adjacent current-selection table because ORM flush statement order
can differ. Both are emitted by the same `_seed_setup_lifecycle_baseline` call in
the proven Session. The probe did not reproduce the historical wall time, but it
did reproduce and correlate the unsafe ownership pattern without a sleep or live
provider.

The temporary probe was removed before certification. Its disposable database
was dropped by the guarded fixture.

## D. Correction

The inline block is now `_prepare_real_writer_pre_pipeline_baseline` with two
explicit caller-owned units:

1. IB contracts, CERI identities, Setup snapshots, and Setup current selections
   commit and the Session closes.
2. Price history is built in a fresh Session. Its CPU-heavy object construction
   occurs before the first database flush; its own writer transaction covers only
   publication of that atomic price-history unit.

The earliest safe boundary is therefore immediately after the complete
Setup/identity unit. No callee commits a caller's unrelated work, and no atomic
financial publication was split.

No production source, scoring logic, frozen configuration, PIT selection,
source identity, decision manifest, worker fencing, cancellation behavior, or
transaction threshold changed.

## E. Focused execution result

The one authorized focused invocation was:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_pre_pipeline_preparation_closes_transactions_between_fixture_phases' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12K_FOCUSED_CERTIFICATION.xml
```

Result: **FAIL**, native pytest exit code `1`; suite time `77.322s`, testcase time
`76.376s`.

Before the failure, the test passed:

- guarded disposable PostgreSQL creation and migration;
- baseline fixture completion with no open transaction at return;
- committed visibility at the `SETUP_IDENTITY` boundary;
- committed visibility at the `PRICE_HISTORY` boundary;
- closed backend transaction at both corrected boundaries;
- `transaction_age_seconds < 120` at both corrected boundaries;
- all 100 retained-provider commits, Session closures, committed four-member
  evidence units, and per-boundary transaction-age assertions;
- exact counts: 101 total raw rows, 100 current raw rows, 100 Setup snapshots,
  100 Setup selections, 20 Winner baselines, 64,000 exact price bars, 100 exact
  IB contracts, 100 exact CERI companies, 400 exact sources, 400 memberships,
  and 400 estimates;
- zero duplicate raw rows, duplicate Setup identities, duplicate memberships,
  orphan Setup selections, orphan memberships, cross-ticker memberships,
  cross-run Setup substitution, or pipeline rows;
- raw population identity and raw evidence fingerprints.

The first failed assertion was the Setup evidence fingerprint at line 1052:

```text
actual:   de20197073f4c90acb08fb541d604479e22cd558b15eb914fbfedf00cdc5e8c2
expected: 426cd833c04059f3aaf614ab0a9417ca42ffdd000a9188c1b2c0917efc1d3c7e
```

The persisted query orders Setup rows by ticker, while the expected list retains
the original non-lexical member order. `CanonicalEvidenceSerializer` preserves
list order, and a read-only check confirmed the serializer is order-sensitive.
The task's stop-on-focused-failure rule prohibits correcting or rerunning this
assertion here.

Because failure occurred before final property emission, the continuous
observer's numeric maximum was not retained in JUnit. All 102 controlled boundary
measurements individually passed `<120s`, but a complete focused PASS and an
exported whole-preparation maximum are not claimed.

## F. Safety and cleanup

- Native failing JUnit was preserved without modification.
- Chromium, web server, worker, and pipeline were not started.
- The exact focused database `swinglens_pytest_ca69ba78b43f` was removed.
- Disposable database inventory returned to count `36` and digest
  `905b872fc23587ba64b810b16948a092af69de64ed583308e8027b3e5b137b51`.
- The 36 unrelated disposable databases were untouched.
- The temporary forensic probe file was removed.
- No normal database, historical Run 22–33, or live provider was accessed.
- No commit, push, merge, reset, stash, deployment, or retry occurred.

`TASK 2.9.12K PRE-PIPELINE TRANSACTION: OPEN`

`CHROMIUM-100: NOT STARTED`

