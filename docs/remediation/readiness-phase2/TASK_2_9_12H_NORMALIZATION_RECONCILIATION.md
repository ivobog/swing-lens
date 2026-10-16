# Task 2.9.12h — CERI Normalization-State Reconciliation

## A. Executive verdict

**PASS.** The Task 2.9.12g failure was caused by a missing fixture identity
prerequisite, not by the per-ticker transaction correction or a production
normalization defect. The focused fixture now persists the same deterministic
`CeriCompany` identity shape used by the production batched workflow before it
invokes production ingestion and normalization services.

The sole focused PostgreSQL invocation passed all 100 ticker boundaries. It
certified 400 exact source memberships, 400 `NORMALIZED` memberships, 400 source
records, 400 estimate snapshots, 100 completed ingestion runs, 100 completed
processing runs, deterministic fingerprints, zero cross-ticker substitution,
and zero checked-out connections after the assertions.

Task 2.9.12g's pre-pipeline transaction certification is therefore closed. The
historical v1/v2 anchor compatibility issue remains a separate release blocker.
Chromium-100 and full Phase 2.9 release certification remain pending.

## B. Timing

| Event | Time |
|---|---|
| Start | `2026-10-14T21:23:56.0033489+02:00` |
| Finish | `2026-10-14T21:29:57.1332593+02:00` |
| Elapsed | `361.1299104 seconds` (6 minutes 1.130 seconds) |
| Budget | `1500 seconds` (25 minutes) |

The task completed within budget and used exactly one final pytest invocation.

## C. Retained failure

Task 2.9.12g's independent observer ran after the first ticker Session had
committed and closed. It proved that the seeder backend had no `xact_start` and
was not `idle in transaction`. Its next query counted memberships for the ticker
with `normalization_state='NORMALIZED'` and returned `0`, while the test expected
`4`.

The retained JUnit did not record all-state membership, source-record,
estimate-snapshot, or processing-run counts. Task 2.9.12h therefore did not infer
those missing actual counts from the failed assertion; it reconstructed the
deterministic first-ticker writer path and corrected the proven fixture gap.

## D. Root cause

The four expected memberships are not four provider/dataset combinations. They
are four EODHD estimate observations for one ticker in the single
`eodhd/estimates` ingestion run:

- `<ticker>-eps-90d`
- `<ticker>-eps-30d`
- `<ticker>-eps-7d`
- `<ticker>-eps-current`

`CeriIngestionRunSourceRecord.normalization_state` is a non-null run-local
lifecycle field. Its supported states are `PENDING`, `NORMALIZED`, `REUSED`,
`QUARANTINED`, and `REJECTED`. Ingestion initially writes `PENDING` for a valid
source fact. `CeriNormalizationService.normalize` resolves the source to a
`CeriCompany`, creates the dataset-specific normalized evidence, and then marks
the membership `NORMALIZED`; an already-existing representation becomes
`REUSED`. Identity rejection, source quarantine, and processing rejection are
terminal states but do not create valid estimate evidence.

For estimates, authoritative normalized evidence is persisted in
`ceri_estimate_snapshots`, with one immutable row linked to each
`ceri_source_records.id`. Membership state and normalized evidence are separate,
and this certification verifies both.

The focused Task 2.9.12g setup created `UploadRun` and `RawCompanyRow` records but
no `CeriCompany` identities. `CeriIdentityResolver.prepare` consequently
materialized an empty company set. Each of the four source facts was unresolved;
the resolver set `identity_unresolved`, the normalizer marked its membership
`REJECTED`, and no `CeriEstimateSnapshot` was created.

The previous helper checked only `normalized.failed == 0`. Unresolved identity is
counted as quarantined rather than failed, so that assertion accepted the
`PARTIAL` normalization result. The fixture then called
`CeriProcessingRunService.finish(..., status='COMPLETED')`, masking the partial
state for the observer's processing query. The observer's ticker predicate was
not the defect: it correctly returned zero `NORMALIZED` memberships.

Classification: **fixture identity prerequisite missing**. Production behaved
according to its existing fail-closed identity and normalization contract. No
production source or lifecycle semantics changed.

The fixture-only correction seeds, before retained history, the exact identity
shape used by `_ensure_ceri_companies` in the production batched workflow:
uppercase ticker, exchange `US`, and EODHD provider identity `<TICKER>.US`. The
helper now also rejects any result that is not exactly `COMPLETED`, four read,
four normalized, zero quarantined, and zero failed before it can commit and mark
processing complete.

## E. Before/after persistence

| Evidence | Expected | Before | After |
|---|---:|---:|---:|
| Source memberships | 4 per ticker / 400 total | All-state retained count not recorded | 4 per ticker / 400 total verified |
| `NORMALIZED` memberships | 4 per ticker / 400 total | 0 at the retained first-ticker query | 4 per ticker / 400 total verified |
| Normalized estimate evidence | 4 per ticker / 400 total | Retained count not recorded | 4 per ticker / 400 total verified in `ceri_estimate_snapshots` |
| Completed ingestion runs | 1 per ticker / 100 total | Retained count not recorded | 1 per ticker / 100 total verified |
| Completed processing runs | 1 per ticker / 100 total | Retained count not recorded | 1 per ticker / 100 total, each with `read_count=4`, `normalized_count=4`, `failed_count=0` |
| Transaction-open boundary | 0 after every ticker | First boundary was clean | 0 at all 100 controlled checkpoints |
| Cross-ticker contamination | 0 | Retained count not recorded | 0 verified |

Additional verified end state:

- 100 unique raw tickers and 100 deterministic CERI company identities;
- 400 immutable source records and 400 exact run-local memberships;
- zero duplicate raw tickers, duplicate memberships, orphan memberships, or
  orphan estimate snapshots;
- zero incomplete processing runs;
- zero pipeline runs, calculation contexts, or configuration anchors;
- raw-row, scope-membership, source-identity, and source-membership fingerprints
  all matched independently constructed deterministic projections.

## F. Transaction integrity

The Task 2.9.12g per-ticker structure is preserved:

1. open one ticker-owned Session/transaction;
2. persist four ingestion memberships;
3. normalize all four into estimate snapshots;
4. finish the processing run;
5. measure PostgreSQL transaction age;
6. commit and close the Session;
7. observe the committed state through an independent autocommit connection;
8. verify that the next ticker has not been admitted.

The focused run completed **100 commits and 100 Session closures**. At each of
100 checkpoints, every seeder backend had `xact_start IS NULL` and none was
`idle in transaction`. The observer saw exactly the current ticker's committed
ingestion, membership, normalized-evidence, and processing state; its total
ingestion-run count equaled the completed ticker number, and the next ticker had
zero memberships.

The test measured PostgreSQL transaction age immediately before every commit and
asserted every observation—and therefore their maximum—was strictly below the
unchanged `120-second` threshold. The exact in-memory maximum was not exported to
JUnit, so this report records the certified bound `<120 seconds` rather than
inventing a numeric value. The test did not wait 120 seconds.

After final assertions, both engine pools reported zero checked-out connections.
The `finally` block disposed both engines, and the disposable PostgreSQL fixture
completed teardown.

## G. Focused certification

Exact node:

```text
tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_retained_provider_history_closes_transactions_between_100_members
```

The invocation was process-bounded to 600 seconds and executed the required
pytest command with:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_retained_provider_history_closes_transactions_between_100_members' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12H_CERTIFICATION.xml
```

Result: **PASS** — `1 passed` in `52.14 seconds`; JUnit suite time
`52.126 seconds`, testcase time `51.751 seconds`. The warnings were existing
Starlette/Alembic deprecation warnings and did not affect certification.

JUnit: `docs/remediation/readiness-phase2/TASK_2_9_12H_CERTIFICATION.xml`.

Although the node resides under `tests/e2e`, it is the requested focused
PostgreSQL-only fixture test. It did not instantiate Playwright, Chromium, an
application server, a worker, or a pipeline.

## H. Changed files

- `tests/e2e/single_run_certification/test_real_writer_cardinality.py` — retained
  Task 2.9.12g's per-ticker Session boundary; added deterministic CERI company
  prerequisites to both the Chromium fixture preparation path and its focused
  proof; added strict normalization closure, exact run/provider/dataset/source
  checks, independent committed visibility, transaction-age measurements,
  deterministic fingerprints, integrity counts, and resource assertions.
- `docs/remediation/readiness-phase2/TASK_2_9_12H_CERTIFICATION.xml` — passing
  result from the sole focused invocation.
- `docs/remediation/readiness-phase2/TASK_2_9_12H_TRANSACTION_DIAGNOSTIC.json` —
  bounded, redacted machine-readable certification summary.
- `docs/remediation/readiness-phase2/TASK_2_9_12H_NORMALIZATION_RECONCILIATION.md`
  — this report.

No production source changed. Task 2.9.12f's v2 anchor contract and all production
normalization behavior remain untouched.

## I. T14

The official validator completed without regeneration:

```json
{"stale": []}
```

No immutable historical T14 artifact was modified.

## J. Safety

- Only a disposable PostgreSQL database was used.
- The deterministic frozen provider registry supplied all financial inputs; no
  live IBKR, SEC, EODHD, or CERI provider was contacted.
- No browser, Chromium-100, server, worker, real pipeline, broad CERI suite, or
  full pytest suite was started.
- The normal SwingLens database and historical Runs 22–33 were not accessed.
- No scoring formula, C1 configuration, PIT identity, calculation authority,
  certification barrier, execution fence, or publication authority changed.
- The production `120-second` transaction threshold remains unchanged.
- Existing working-tree changes were preserved. No reset, stash, discard, clean,
  merge, commit, push, or deployment occurred.
- Resource assertions and disposable-database teardown passed.

## K. Remaining blockers

1. **Task 2.9.12g transaction certification:** **CLOSED** by this 100-member
   focused PASS.
2. **Historical v1/v2 anchor compatibility:** **OPEN**. Persisted v1 anchors are
   version-labelled, but verification rebuilds v2 unconditionally. No historical
   anchor was rewritten or reinterpreted in this task.
3. **Chromium-100 terminal certification:** **PENDING / NOT EXECUTED**. Tasks
   2.9.12a/d/e remain open until the complete Chromium-100 case passes.
4. **Full Phase 2.9 release certification:** **NOT EXECUTED**.

## L. Next action

Address or formally classify the historical v1/v2 anchor compatibility policy in
a separately authorized task. After that release decision, authorize exactly one
final Chromium-100 certification using the v2 anchor correction from Task
2.9.12f and the transaction corrections from Tasks 2.9.12a/d/g/h.

Neither action was started here.

`TASK 2.9.12H NORMALIZATION RECONCILIATION: CLOSED`

`TASK 2.9.12G PRE-PIPELINE TRANSACTION: CLOSED`

