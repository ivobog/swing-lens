# CERI Freshness State-Machine Audit

## Trace

`CeriScoreSnapshot.warnings_json` -> `CeriChangeDetectionService._score_changes` -> `CeriChangeEvent(DATA_STALE/DATA_REFRESHED)` -> configured `CeriAlertRule(DATA_STALE)` -> `CeriAlertEvent`.

Pre-fix and post-fix transition logic is edge-triggered:

- Fresh -> stale: one `DATA_STALE` change.
- Stale -> stale: no change.
- Stale -> fresh: one `DATA_REFRESHED` change.

The durable database contained 1,824 consecutive stale snapshot pairs and zero erroneous repeated stale changes. `DATA_STALE` had 303 unique dedup keys and 302 alerts; no ticker had multiple stale alerts. The one missing alert was suppressed by alert eligibility/cooldown, not duplicated. Alert `event_key` and change `dedup_key` uniqueness constraints also enforce idempotence.

`DATA_REFRESHED` is symmetric at the change layer (421 durable changes) but intentionally has no configured alert rule, so it produces no alert. The new explicit state-machine test covers FRESH -> STALE -> STALE -> FRESH.

The alert business-identity label for data quality was incorrectly falling through to `OPPORTUNITY_TRANSITION`; it is now `DATA_QUALITY_TRANSITION`. Identity inputs already included company, from/to snapshot, and change type, so this naming defect did not create duplicates.

## Scoped rebuild comparison-context defect

The standalone change rebuild filtered snapshots to the requested run/date scope **before** selecting a prior. A run-scoped rebuild could therefore reset an existing historical snapshot to `NO_PRIOR_COMPARABLE_SNAPSHOT` even though its durable change retained `comparison_state=COMPARABLE` and valid from/to IDs. Baseline mismatch: 939 change rows, 685 destination snapshots, 433 tickers, 15 runs (109-130); all 303 `DATA_STALE` changes were in this metadata mismatch. Their prior snapshots do not contain the stale warning, so the transition events themselves are real transitions under the legacy semantic; only the snapshot comparison metadata was corrupted by incomplete rebuild context.

Classification: `SNAPSHOT_CAPTURE_BUG`, Medium. `CeriChangeRebuildService` now scopes the outputs to rebuild but selects each prior from full company history. A regression test proves a run-scoped rebuild uses the prior snapshot outside the request scope. No historical row was repaired during this investigation; a read-only audit query is included, and an optional controlled metadata repair must be separately authorized.

## Historical validity

Using provider-feed freshness as the product semantic, all 303 historical `DATA_STALE` changes had a successful estimate check age of 0 days at their snapshot cutoff. Therefore the historical stale transitions/302 alerts are semantically false even though their fresh-to-stale edges were correctly detected under v1.2. They remain immutable for audit. A separate administrative classification/backfill may mark them legacy-semantic-invalid, but this investigation performs no mutation.

The active alert query now performs that classification without a database rewrite: a `DATA_STALE`/`DATA_REFRESHED` change lacking `freshness.semantic=PROVIDER_FEED_FRESHNESS` is projected as `INVALID_LEGACY`, status `INVALIDATED`, and non-actionable. Default alert views exclude it; the Invalidated filter preserves forensic access. The persisted alert status, change, snapshot, and source evidence remain untouched.

Event Risk was not affected by the confidence-generated `estimate_data_stale`: capture passed `stale` to Event Risk only from revision-feature warnings, and revision features do not generate that warning. A/ADSK `RISK_ESCALATED` rows were earnings-proximity transitions, not stale penalties.
