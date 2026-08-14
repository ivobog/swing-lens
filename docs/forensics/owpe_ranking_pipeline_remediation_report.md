# OWPE Ranking Pipeline Remediation Report

## Root cause and repair

The Run 105 investigation established that the durable full-pipeline graph went directly from `COMBINING_RESULTS` to downstream stages, so configured ranking profiles were never materialized. This was a `CONFIRMED_DEFECT` (P1): 186 CombinedResult rows and 186 Winner snapshots existed, but RankingResult count was zero.

The durable graph is now:

```text
COMBINING_RESULTS
  -> RANKING_PROFILES
  -> SECTOR_ROTATION / configured research consumers
  -> CAPTURING_WINNER_PREDICTIONS
```

Implementation anchors:

- `app/services/pipeline_service.py:34` — persisted graph ordering;
- `app/services/pipeline_executor.py:437` — Ranking step execution before downstream capture;
- `app/services/ranking_profile_service.py:39` — explicit success/skipped/failure contract;
- `app/services/ranking_profile_service.py:117` — keyed idempotent persistence;
- `app/services/winner_probability/api_service.py:359` — DB-backed rank/profile provenance in run API rows;
- `app/templates/pipeline_progress.html:88` and `app/static/app.js:530` — status UI and live updates.

## Code and contract changes

- Added `RANKING_PROFILES` to the durable pipeline state and persisted step list.
- Added `refresh_rankings` to `PipelineExecutionDependencies`.
- Ranking executes after combined results and before consumers and Winner capture.
- Pipeline result JSON exposes `ranking_status`, `ranking_profiles`, `ranking_results`, and `ranking_reason`.
- Winner run-list serialization hydrates the referenced CombinedResult and RankingResult, exposing final rank, profile rank, and profile score instead of dropping them on list pages.
- The progress UI renders those values and updates them during status polling.
- Metrics record pipeline executions and result counts by terminal ranking status.
- Zero configured profiles is an explicit `SKIPPED` result with reason `no_configured_ranking_profiles`.
- Configured profiles plus nonempty input plus zero results raises an error, making the step/pipeline fail instead of silently succeeding.

## Persistence safety and idempotency

Ranking persistence no longer deletes every result before rebuilding. It updates an existing `(run_id, ranking_profile, ticker)` row in place and inserts only missing keys. Existing primary keys remain stable, so Winner snapshot foreign keys do not encounter a delete/rebuild visibility gap.

Finding: `CONFIRMED_EXPECTED_BEHAVIOR` (P1). Repeated execution converges to the same logical row set and preserves referenced RankingResult IDs.

`CONTRACT_AMBIGUITY` (P3): profile removal does not currently delete stale results because safe historical references take precedence. A future profile-retirement workflow should mark configuration retirement explicitly rather than deleting referenced rows.

## Run-106 production proof

| Item | Result |
|---|---:|
| Upload run | 106 |
| Pipeline run | 103 |
| Durable job | 30316 |
| CombinedResult rows | 186 |
| Configured ranking profiles | 5 |
| RankingResult rows | 930 |
| Ranking step | COMPLETED |
| Winner snapshots | 186 |
| Winner snapshots joined to RankingResult | 186 |
| Wrong-run ranking references | 0 |
| Ticker mismatches | 0 |
| Profile mismatches | 0 |

The Ranking step message is `Persisted 930 ranking results across 5 profiles.` It completed before Winner capture. All 186 Winner snapshots carry a non-null `ranking_result_id` and matching profile/ticker provenance. No ranking value was fabricated for an immutable historical snapshot.

## Tests

Tests cover:

- durable step ordering and dependency invocation;
- success path;
- no-profile `SKIPPED` path;
- configured-profile zero-result failure;
- idempotent retry without delete/rebuild;
- recovery/replay step semantics;
- a Run-105-shaped fixture with 186 combined rows and 930 persisted ranking rows;
- Winner capture receiving ranking provenance;
- progress UI/status JSON compatibility when legacy fixture payloads omit ranking result fields.

The focused Ranking/pipeline/reproduction slice passed 47/47 after the final template compatibility correction. The broader OWPE/pipeline/background slice passed 269/269.

Certification state: `PASS_WITH_NONBLOCKING_FINDINGS`
