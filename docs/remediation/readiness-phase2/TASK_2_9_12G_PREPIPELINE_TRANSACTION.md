# Task 2.9.12g — Pre-Pipeline Transaction Forensics and Anchor Compatibility

## A. Executive verdict

**PARTIAL.** The precise code-owned transaction was identified as the fixture
Session in `_seed_retained_provider_history`. Its old scope held one caller-owned
transaction across all 100 retained-history tickers. The smallest correction now
commits and closes each independently authoritative ticker before moving to the
next member.

The only permitted focused PostgreSQL invocation did not fully certify that
correction. It directly observed a clean transaction boundary after the first
ticker (`xact_start IS NULL` and not `idle in transaction`), then failed because
the independent publication query found zero memberships in the specifically
asserted `NORMALIZED` state rather than four. It therefore never reached all 100
members or the final fingerprint and cleanup assertions. The failure is retained
in the required JUnit, and no correction/retest loop was attempted.

The pre-pipeline transaction issue remains **OPEN pending one newly authorized
focused certification**. A separate historical v1 anchor compatibility blocker
also remains open. Chromium-100 and the full Phase 2.9 release certification were
not executed.

## B. Timing

| Event | Time |
|---|---|
| Start | `2026-10-14T20:57:30.6265596+02:00` |
| Stop decision after the sole focused result | `2026-10-14T21:03:05.8655907+02:00` |
| Elapsed | `335.2390311 seconds` (5 minutes 35.239 seconds) |
| Budget | `1800 seconds` (30 minutes) |

The task stopped within budget. The one-invocation rule, rather than time, is the
reason certification did not continue.

## C. Retained incident

Task 2.9.12e retained a maximum pre-pipeline transaction age of
`131.806556 seconds`, exceeding the unchanged `120-second` contract. The sample
was taken during deterministic 100-ticker retained-history preparation, before a
pipeline row existed. Two connections were reported idle in transaction at the
relevant observation, with no blocked sessions and no PostgreSQL lock waits. The
transaction closed before normal pipeline execution, whose observer later found
no transaction violation.

The deleted disposable database was not reconnected. The referenced raw runtime
directory `test-results/single-run-certification/20261014T173821Z-65dd66de` was
no longer present, so a historical backend PID and raw query text could not be
recovered. The surviving redacted diagnostic provides the exact age and phase but
does not retain a PID-to-Session mapping. Ownership below is therefore a causal
code-path reconstruction, not a claim that a deleted backend was resampled.

## D. Exact root cause

The offending Session is created by
`tests/e2e/single_run_certification/test_real_writer_cardinality.py::_seed_retained_provider_history`.
Before this task, one `with Session(engine) as db` surrounded the provider,
dataset, and 100-ticker loops, with one `db.commit()` after all loops.

- **Transaction start:** SQLAlchemy implicitly begins on the first database
  operation in the first `CeriIngestionService.ingest` call. The ingestion path
  establishes/looks up semantic scope and ingestion authority before persisting
  source evidence.
- **Last required operation:** `CeriProcessingRunService.finish` flushes the
  final ticker's terminal processing record; only after that did the fixture issue
  its single commit.
- **Open interval:** deterministic provider-result handling, record
  normalization, processing-run completion, and the transitions between 100
  ticker calls all ran inside that outer transaction. Between SQL operations the
  backend can therefore appear idle in transaction while Python continues work.
- **Pending work:** the caller-owned Session accumulates ingestion runs,
  immutable source memberships, normalized estimate evidence, processing-run
  state, and associated semantic-scope writes until the final commit.
- **Ownership:** this is class **A, fixture-owned long transaction**. The
  production ingestion, normalization, and processing services accept the
  caller's Session and flush but do not own its commit. The fixture created an
  unnecessary cross-ticker atomic unit; this is not a production helper retaining
  a hidden transaction.
- **Atomicity:** ingestion plus normalization plus terminal processing state for
  one ticker is the required complete unit. Atomicity across unrelated tickers is
  not required because pipeline admission occurs only after the helper returns.
- **Earliest safe end:** commit and close immediately after that complete
  per-ticker unit, before preparing the next ticker or waiting on any external
  readiness boundary.

This transaction is distinct from the repaired Technical and Setup boundaries:
it exists entirely in pre-pipeline fixture seeding, before pipeline admission.
The retained production runtime maximum was `48.206691 seconds`, and the
Technical/Setup violations did not recur in Task 2.9.12e.

## E. Before/after contract

| Property | Before | After |
|---|---|---|
| Session owner | Fixture helper `_seed_retained_provider_history` | Same fixture helper |
| Transaction scope | One Session/transaction for all 100 tickers | One Session/transaction per independently authoritative ticker |
| Transaction open across CPU/wait | Yes, across provider/normalization work and ticker transitions | Session closes before the next ticker and before the controlled CPU/wait boundary callback |
| Committed fixture completeness | All tickers became visible only at the final commit | Each ticker commits only after ingestion, normalization, and terminal processing finish |
| Frozen source identities | Deterministic frozen registry | Registry and request identities unchanged |
| Required atomicity | Per-ticker work was unnecessarily coupled across 100 members | Required per-ticker evidence unit remains atomic; cross-ticker coupling removed |
| 120-second invariant | Unchanged | Unchanged |

Only the fixture helper changed. No production transaction, financial scoring,
source/PIT rule, configuration authority, or pipeline authority was changed.

## F. Focused certification

Exactly one post-fix invocation was executed:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_retained_provider_history_closes_transactions_between_100_members' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12G_CERTIFICATION.xml
```

Result: **FAIL** (`1 failed`, pytest reported `7.87 seconds`; JUnit suite time
`7.778 seconds`).

The test used disposable PostgreSQL and no browser, application server, or
pipeline. After the first ticker's Session closed, an independent autocommit
connection successfully established that every seeder backend had
`xact_start IS NULL` and none was `idle in transaction`. The next assertion
queried committed run-local memberships for that ticker and found `0` rows with
`normalization_state='NORMALIZED'`, while the test expected `4`:

```text
assert 0 == 4
```

Because this occurred at member 1, the required 100-member completion, stable
raw/membership fingerprints, final evidence counts, and end-of-test pool checks
were not reached. Exception unwinding still executed the test's `finally` block,
disposing both engines, and the disposable database fixture ran its teardown.
No second invocation was made.

JUnit: `docs/remediation/readiness-phase2/TASK_2_9_12G_CERTIFICATION.xml`.

## G. V1/V2 compatibility

The current contract is explicitly persisted in anchor JSON as
`transition-run-start-anchor-v2`. The verifier, however, does not dispatch by the
stored `contract` value. Each verification path calls the current
`build_run_start_anchor_manifest`, which always constructs v2, and then performs
strict payload and fingerprint equality against the persisted anchor.

Current observed policy:

- persisted anchors are version-labelled;
- a historical v1 anchor is not migrated or rewritten;
- v1 is not silently treated as v2 by dropping `run.status` and
  `run.processed_at`;
- instead, v1 compares unequal to reconstructed v2 and fails closed with
  `DECISION_MANIFEST_MISMATCH`;
- a resumed historical v1 pipeline can consequently be rejected after a
  legitimate lifecycle change, and also because the contract/payload shape is
  v1 rather than v2;
- missing anchor data fails closed;
- an unknown contract fails strict structural/fingerprint comparison, although
  there is no explicit version-aware rejection branch.

This preserves original evidence but leaves a separate release blocker: the
repository has neither a version-aware v1 verifier nor a separately formalized
policy declaring all historical v1 resumes unsupported. Resolving that policy or
adding a migration is outside this task; no historical anchor was rewritten.

## H. Changed files

- `tests/e2e/single_run_certification/test_real_writer_cardinality.py` — moved
  retained-history transaction ownership to one complete per-ticker unit and
  added the focused disposable-PostgreSQL boundary proof. The proof currently
  retains its failing first-ticker publication-state assertion unchanged, per the
  stop-on-failure rule.
- `docs/remediation/readiness-phase2/TASK_2_9_12G_CERTIFICATION.xml` — sole
  focused invocation's unmodified failure result.
- `docs/remediation/readiness-phase2/TASK_2_9_12G_TRANSACTION_DIAGNOSTIC.json` —
  redacted ownership, certification, compatibility, and safety facts.
- `docs/remediation/readiness-phase2/TASK_2_9_12G_PREPIPELINE_TRANSACTION.md` —
  this report.

No production source changed in Task 2.9.12g.

## I. T14 source pins

The official validator ran without regeneration:

```json
{"stale": []}
```

The semantic review found no production-source change requiring a current pin
update. Immutable historical T14 artifacts were untouched.

## J. Safety

- Only disposable PostgreSQL was used by the focused proof.
- Chromium-100, browser/E2E execution, the full repository suite, and the full
  Phase 2.9 release certification were not started.
- No SwingLens pipeline was admitted.
- No live IBKR, SEC, EODHD, or CERI provider was contacted; the helper uses the
  deterministic frozen registry.
- The normal application database and historical Runs 22–33 were not accessed.
- The `120-second` invariant remains unchanged and monitoring was not weakened.
- No failure was suppressed or marked xfail.
- Existing working-tree changes were preserved; no reset, stash, clean, merge,
  commit, push, or deployment occurred.
- The focused test's `finally` path disposed both created engines, and the
  disposable-database fixture retained teardown responsibility after failure.

## K. Remaining blockers

1. **Pre-pipeline transaction issue:** the corrected first boundary was directly
   observed transaction-clean, but the sole proof failed before all 100 members.
   Full certification therefore remains open.
2. **V1/v2 anchor compatibility:** stored-version-aware verification/policy is
   unresolved. Historical v1 anchors fail strict comparison against rebuilt v2.
3. **Chromium-100 terminal certification:** not executed. Tasks 2.9.12a/d/e
   remain open until that complete case passes.
4. **Full Phase 2.9 release certification:** **NOT EXECUTED**.

## L. Next recommended action

Authorize a separate, newly budgeted Task 2.9.12g certification pass. Before its
single invocation, reconcile the focused proof's expected publication-state
assertion with the actual frozen estimate normalization contract using static
service/fixture evidence. Then run the same PostgreSQL-only node once and require
all 100 boundaries, deterministic fingerprints, authority non-mutation, no
incomplete evidence, and resource cleanup to pass. Only after that focused pass
should one new Chromium-100 certification be considered with the v2 anchor fix
from Task 2.9.12f and the transaction corrections from Tasks 2.9.12a/d/g.

Do not start Chromium-100 until the focused blocker is closed.

`TASK 2.9.12G PRE-PIPELINE TRANSACTION: OPEN — FOCUSED CERTIFICATION FAILED AT MEMBER 1`

