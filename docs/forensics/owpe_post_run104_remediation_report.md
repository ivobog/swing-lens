# SwingLens OWPE Post-Run-104 Remediation Report

- Date: 2026-08-14 (Europe/Zurich)
- Repository: `C:\Users\Ivica\Documents\SwingLens`
- Baseline commit: `3e45cf4b898548e7b8869e1413103c20501fdf15`
- Branch: `codex/ceri-run101-remediation`
- Database: PostgreSQL 18.3, database `swinglens`, server timezone `Europe/Berlin`
- Schema: Alembic `0045_ceri_changes_alerts_semantics` (head)
- New OWPE calculation version: `owpe-calc-1.1.0`
New config hash: `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527`

## 1. Executive verdict

The code remediation establishes the missing production contracts: a durable H5-first drain,
structured eligibility decisions, an explicit evidence funnel, L5-first cohort materialization,
exact evidence-member reproduction, and one versioned minimum-display authority. The focused
test suite passes.

Production is **not certifiable**. The fresh-run evidence exposes four independent blockers:

1. Existing historical snapshots have no `capture_training_candidate` policy record and are
   deliberately rejected as `LEGACY_ELIGIBILITY_UNCLASSIFIED`; no revisioned reclassification
   was authorized.
2. While testing worker recovery, the initial automatic-scheduler rollout guard inherited the
   existing `WINNER_PROBABILITY_ENABLED=true` environment value and processed production H5 rows.
   This violated the no-backfill constraint. The worker was stopped, no rollback was attempted,
   and automatic maturation now has a separate default-off gate.
3. The fresh calculation/config family has no compatible mature target/stop labels, so its
   evidence funnel reaches zero at `compatible_target_stop_label`.
4. The full fresh pipeline produced no ranking results, leaving ranking context absent for all
   186 snapshots.

Run 104 prediction snapshots and estimates were not rewritten. The unauthorized maturation did
change historical pending outcome rows and is documented precisely in section 3.

## 2. Finding register

| Finding | Classification | Severity | Evidence |
|---|---|---:|---|
| Single-pass, untargeted outcome processing could not drain primary H5 | `CONFIRMED_DEFECT` | P0 | Former `OutcomeMaturationService.process_due_outcomes`; Run 104 job 15900 processed only 500 |
| Capture default did not implement a production-training policy | `CONFIRMED_DEFECT` | P0 | Former `capture_run(... production_training_allowed=False)` |
| Evidence query omitted required compatibility, policy, quality, rolling, and revision-cutoff gates | `CONFIRMED_DEFECT` | P0 | Former 107-line `evidence_service.py` |
| Minimum display was 5 while SRS/SDD require 15 | `CONFIRMED_DEFECT` | P1 | Old `config/winner_probability.yaml`; new version is 15 |
| L5 was attempted last but not materialized first | `CONFIRMED_DEFECT` | P1 | Former `_select_cohort` returned on first eligible level |
| Exact reproduction checked only probability, n, and manifest hash | `CONFIRMED_DEFECT` | P1 | Former `reproduction_service._mismatches` |
| API default resolved the oldest primary definition after a version bump | `CONFIRMED_DEFECT` | P0 | `api_service._resolve_outcome_definition`; Run 105 initially selected inactive definition 1 |
| Legacy rows lack the new eligibility-policy decision | `DATA_QUALITY_ISSUE` | P0 | 2,398 current matured primary-H5 rows: 0 have `capture_training_candidate` |
| Automatic scheduler processed the legacy backlog without authorization | `CONFIRMED_DEFECT` | P0 | Background job 30260 and processing runs 109/110 |
| Run 104 immutable estimates remained unchanged | `CONFIRMED_EXPECTED_BEHAVIOR` | — | 184 estimates; latest creation remains 2026-08-14 04:34:33+02 |

## 3. Production mutation incident and containment

This section is intentionally explicit. The task prohibited production backfill without approval.

The first scheduler wiring used `settings.winner_probability_enabled`. The local environment had:

```text
WINNER_PROBABILITY_ENABLED=true
WINNER_PROBABILITY_CAPTURE_IN_PIPELINE=true
```

Hot-reloaded worker code therefore enqueued background job 30260 at
`2026-08-14 21:44:25+02`, request key
`winner:h5-next-open:session:2026-08-13`. The first worker died with processing run 109 still
`RUNNING`; lease-safe recovery created processing run 110 and completed the same job as `PARTIAL`.

Persisted job 30260 result:

```json
{
  "due_h5_next_open": 3439,
  "processed_h5": 3439,
  "matured_h5": 2328,
  "target_stop_matured": 2328,
  "pending_h5_after_cycle": 1111,
  "excluded_h5": 0,
  "failed_h5": 0,
  "oldest_due_h5_session": "2026-08-07",
  "oldest_due_h5_age": 6,
  "unvisited_h5_after_cycle": 0,
  "last_successful_full_drain_at": null
}
```

That incident payload was emitted before the final age-unit correction; its value `6` is elapsed
calendar days. Current code defines `oldest_due_h5_age` as completed U.S. trading sessions, for
which 2026-08-07 through 2026-08-13 is 4.

Across both attempts, database evidence shows 2,398 H5 `NEXT_OPEN` forward rows and 2,398
matching target/stop rows evaluated between `21:44:26` and `22:07:20+02`. Forward IDs range
from 23 to 35083; target/stop IDs range from 5 to 7017. There were zero new probability
estimates in that interval. There remain 1,111 due primary-H5 rows, oldest due session
2026-08-07.

Containment:

- the worker was stopped;
- no update, delete, rollback, or historical estimate replay was attempted;
- `Settings.winner_probability_auto_maturation_enabled` was added with default `False`;
- the background worker now schedules daily maturation only under that separate gate;
- `.env.example` documents `WINNER_PROBABILITY_AUTO_MATURATION_ENABLED=false`;
- background job 30262 (cohort refresh) was left preserved, not deleted or edited.

This event alone prevents a `PASS` certification.

## 4. H5 NEXT_OPEN orchestration design

Implementation:

- `app/services/winner_probability/outcome_orchestration_service.py`
- `app/services/winner_probability/scheduler.py`
- `app/services/winner_probability/job_handlers.py`
- `app/services/background_worker.py`

The scheduler creates one deterministic request key per completed U.S. session. A job claims
the normal background-job lease and heartbeat contract. `H5NextOpenOrchestrationService` selects
only current `PENDING`, due, H5 `NEXT_OPEN` rows ordered by due session and primary key. It processes
bounded batches and excludes already-attempted IDs during a cycle so a missing-bar row cannot
starve valid later rows. An unvisited queue causes a durable continuation job. Material target/stop
maturations enqueue a cohort refresh with a persisted refresh cutoff.

Observability is persisted in `WinnerProcessingRun.counts_json` and shown by the existing operations
page/API:

```text
due_h5_next_open
oldest_due_h5_session
oldest_due_h5_age              # U.S. trading sessions
processed_h5
matured_h5
pending_h5_after_cycle
excluded_h5
failed_h5
target_stop_matured
unvisited_h5_after_cycle
last_successful_full_drain_at
```

Late maturations never update an immutable `DECISION_TIME` estimate. Cohort refresh now calls
`create_latest_rescore` at the persisted refresh cutoff.

## 5. Eligibility policy and reason codes

`TrainingEligibilityPolicy` is the single capture-policy authority. It persists:

```text
training_eligibility_policy_version
capture_training_candidate
evidence_training_eligible
production_training_allowed       # compatibility projection
training_rejection_reasons[]
```

Capture candidacy covers native lineage, point-in-time validation, prediction eligibility, and
blocking source quality. Evidence eligibility additionally requires episode independence.
Evidence-time compatibility and rolling gates remain explicit in `EvidenceService` because they
depend on the estimate cutoff and selected outcome definition.

Reason codes:

```text
RECONSTRUCTED_HISTORY
POINT_IN_TIME_NOT_VALIDATED
PREDICTION_NOT_ELIGIBLE
DEPENDENT_EPISODE
SOURCE_QUALITY_BLOCKED
LEGACY_ELIGIBILITY_UNCLASSIFIED
FEATURE_SCHEMA_MISMATCH
CALCULATION_VERSION_MISMATCH
CONFIG_MISMATCH
OUTCOME_DEFINITION_MISMATCH
OUTCOME_NOT_CURRENT_AT_CUTOFF
OUTCOME_REVISED_AFTER_CUTOFF
QUALITY_GATE_BLOCKED
OUTSIDE_ROLLING_WINDOW
```

Legacy rows are not inferred from the former boolean. Absence of a policy record is a closed-state
rejection. This is deliberate and is why the newly matured legacy outcomes do not become production
evidence automatically.

## 6. Evidence-gate matrix

`EvidenceService.diagnostic_funnel` applies and returns before/after counts for:

| Order | Gate |
|---:|---|
| 1 | historical prediction and snapshot visible before cutoff |
| 2 | full horizon and target/stop evaluation mature before cutoff |
| 3 | forward revision visible at cutoff |
| 4 | exact entry model, horizon, definition, target, stop, and non-null label |
| 5 | target/stop revision visible at cutoff |
| 6 | prediction eligibility |
| 7 | point-in-time validation |
| 8 | native capture policy |
| 9 | persisted capture-training candidacy |
| 10 | feature-schema compatibility |
| 11 | calculation-version compatibility |
| 12 | config-hash compatibility |
| 13 | active primary outcome-definition compatibility |
| 14 | blocking quality gates |
| 15 | configured five-year rolling window |
| 16 | source revision cutoff not after training cutoff |
| 17 | cohort match |
| 18 | independent episode |
| 19 | one deterministic representative per episode |

Tests include a positive 15-member independent control and isolated rejection fixtures for every
policy/compatibility/quality/rolling/leakage gate.

## 7. Configuration authority

SRS/SDD governance value 15 is authoritative. Because the config hash is immutable evidence, the
old file was not edited under the old calculation identity. The versioned path is:

```text
owpe-calc-1.1.0
config hash 218a8976...57b527
cold_start.minimum_display_n = 15
L5 min_effective_n = 15
Low grade min_effective_n = 15
rolling_window_years = 5
```

Cross-section validation refuses a config when the three minimum values differ. Boundary tests
lock 14/15/40/100 to Insufficient/Low/Medium/High for balanced evidence with acceptable interval
width.

## 8. Cohort and L5 contract

For each estimate cutoff, cohort calculation now starts at L5 and proceeds L4 through L0. Each
level gets a versioned definition, statistic, and manifest hash before selection evaluates the
most specific eligible level. This preserves backoff semantics while guaranteeing a persisted
global baseline.

Estimate metadata separates:

```text
selected_cohort_level
selected_cohort_key
attempted_cohort_level
attempted_cohort_key
```

When no cohort is selected, selected fields and `cohort_definition_id` remain null. Attempted L5
identity is diagnostic only.

## 9. Probability and membership reproduction

For a non-null cohort estimate, persisted data now includes or references prediction, outcome
definition, cutoff, cohort, schema, calculation version, config hash, sample n, effective n, wins,
prior alpha/beta, posterior alpha/beta, interval, grade, manifest, and exact member rows with both
forward and target/stop revisions.

`ReproductionService` recomputes and compares:

```text
manifest hash
sample_n
effective_n
wins
point_probability
lower_bound
upper_bound
interval_width
config_hash
```

It fails if either the forward revision or target/stop revision cannot be reproduced. Withheld
probabilities persist no interval.

The run-list API now resolves only an active outcome-definition version and orders duplicate
version identities newest-first (`api_service.py:310-333`). This was discovered during Run 105
reconciliation: before the fix, the default endpoint selected inactive calculation 1.0 definition
1 and returned all estimates as missing; after the fix it selects active calculation 1.1
definition 3 and reports 184 estimates. A regression test asserts the generated active-only,
descending-version query.

## 10. Tests and commands

Passing focused suite:

```text
python -m pytest tests/test_settings.py tests/test_us_market_calendar.py \
  tests/winner_probability tests/test_background_worker.py \
  tests/test_background_job_service.py -q
217 passed in 13.60s
```

Static checks:

```text
python -m ruff check app tests
All checks passed

git diff --check
No whitespace errors (Windows line-ending notices only)
```

Run 105 also revealed that evaluating the same immutable global evidence funnel separately for
every capture caused a 1,581,271 ms capture phase. A cutoff-and-contract keyed cache now reuses
only the L5 global funnel when every prediction shares the exact decision-time cutoff. The key
includes prediction identity whenever the prediction cutoff differs, preserving latest-rescore
self-exclusion. All 217 focused tests pass after this change.

## 11. Safe legacy reclassification proposal — not executed

Any legacy use requires a separately approved, revisioned procedure:

1. Freeze a manifest of candidate prediction IDs and all source IDs/hashes.
2. Re-run point-in-time, native/reconstruction, eligibility, episode, quality, schema, calculation,
   config, and outcome-definition checks without altering source snapshots.
3. Write a new append-only eligibility-decision table or revision-2 classification artifact with
   policy version, actor, timestamp, reason codes, source manifest hash, and old/new state.
4. Require independent review of all allow decisions and a dry-run count by reason.
5. Use a new calculation/config compatibility family; never mark old decision-time estimates as
   having had the new evidence.
6. Make job keys and manifests idempotent and publish before/after SQL checksums.

The 2,398 inadvertently matured rows must remain excluded until this procedure is authorized and
completed. No mass boolean update is acceptable.

## 12. Remaining risks

- P0: unauthorized maturation incident requires governance review.
- P0: no approved production-training history exists under `owpe-calc-1.1.0` and the new config hash.
- P0: fresh-run certification cannot claim reproducible non-null production probability.
- P0: Run 105 has zero ranking results and therefore no ranking profile/rank provenance.
- P0: active calculation 1.1 has no compatible mature target/stop labels; its funnel reaches zero
  at that gate.
- P1: background job 30262 is preserved in a retryable state and must be dispositioned through the
  normal audited job-control path after approval.
- P1: the fresh pipeline completed `PARTIAL` because two rows were excluded for insufficient bars
  and one IB failure was recorded; worker recovery is covered in the certification report.
- P2: existing model/source enum vocabulary remains broader/different from the proposed SDD names;
  no enum migration was made in this remediation.
