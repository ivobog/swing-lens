# OWPE Ranking Pipeline Investigation — Run 105

## Verdict

**CONFIRMED_DEFECT — Severity: P1.** Run 105 has 186 `CombinedResult` rows and zero `RankingResult` rows because the full pipeline contains no ranking trigger, step, result counter, or failure state. Ranking creation exists only behind explicit refresh routes/services. The pipeline therefore completed all declared steps without attempting ranking, and its PARTIAL status came from other warning/incomplete conditions—not ranking absence.

This defect explains why every Run 105 Winner snapshot lacks `ranking_result_id`/`ranking_profile`. It must not be “repaired” by fabricating historical ranking values, and it does not block L5/global evidence.

## Live database trace

Read-only query:

```sql
SELECT
  (SELECT count(*) FROM raw_company_rows WHERE run_id = 105) AS raw_n,
  (SELECT count(*) FROM fundamental_scores WHERE run_id = 105) AS fundamental_n,
  (SELECT count(*) FROM technical_scores WHERE run_id = 105) AS technical_n,
  (SELECT count(*) FROM combined_results WHERE run_id = 105) AS combined_n,
  (SELECT count(*) FROM ranking_results WHERE run_id = 105) AS ranking_n,
  (SELECT count(*) FROM winner_prediction_snapshots WHERE run_id = 105) AS winner_n;
```

Returned:

```text
raw=186, fundamental=186, technical=186,
combined=186, ranking=0, winner_snapshots=186
```

Pipeline run PK 102 is `PARTIAL`. Its persisted steps are:

```text
1 VALIDATING_RUN                    COMPLETED
2 SCORING_FUNDAMENTALS              COMPLETED
3 FETCHING_MARKET_DATA              COMPLETED
4 SCORING_TECHNICALS                COMPLETED
5 MARKET_REGIME_SNAPSHOT            COMPLETED
6 COMBINING_RESULTS                 COMPLETED
7 SECTOR_ROTATION_SNAPSHOT          COMPLETED
8 CERI_PROVIDER_INGEST              COMPLETED
9 CAPTURING_SETUP_SIGNALS           COMPLETED
10 EVALUATING_SETUP_LIFECYCLES      COMPLETED
11 CAPTURING_WINNER_PREDICTIONS     COMPLETED
```

There is no ranking step, and no step contains a ranking error. Pipeline result JSON reports `combined_results=186` and Winner capture results but has no ranking count or ranking failure field.

## Code trace

### CombinedResult -> ranking trigger/gate

`PIPELINE_STEP_NAMES` in `app/services/pipeline_service.py` declares `COMBINING_RESULTS`, `SECTOR_ROTATION_SNAPSHOT`, and `CAPTURING_WINNER_PREDICTIONS`, but no ranking step. `PipelineStatus` likewise has no ranking state. `pipeline_executor.py` executes combining at line 415 and Winner capture at line 505 with no ranking call between them.

**Finding:** `CombinedResult` completion has no automatic handoff to ranking.

### RankingResult creation

`refresh_all_ranking_profiles` in `app/services/ranking_profile_service.py` line 29 is capable of loading run inputs, evaluating every configured profile, converting decisions into `RankingResult`, adding the rows, and flushing. It is not imported or called by the full-pipeline executor.

The callable service is reached through explicit unsafe POST routes in `app/routers/run_routes.py` beginning at line 397 (`/runs/{run_id}/rankings/refresh`) and line 433 (single-profile refresh). Thus ranking is currently an operator/UI action, not a pipeline product.

### Persistence

The refresh service first deletes existing ranking rows for that run (line 40), then `add_all`/flushes newly computed rows (line 54). No invocation occurred for Run 105, so persistence correctly contains zero rows under the current trigger architecture.

### Status and error handling

Because ranking has no `PipelineStep`, it cannot be pending, running, failed, skipped, retried, or reflected in pipeline result JSON. A pipeline can therefore claim all declared steps completed while ranking is absent. This is a contract/observability gap as well as an orchestration omission.

## OWPE impact

- `WinnerPredictionCaptureService` receives no persisted `RankingResult`, so snapshot ranking fields remain null.
- Pre-1.1 compatibility must retain missing ranking as missing. It may admit those samples to L5 or other levels that do not require ranking; it must exclude them from a ranking-dependent L0 match.
- Current prediction ranking absence does not invalidate historical L5 evidence or serving.
- Ranking scores must not be synthesized or copied from current mutable state into historical snapshots.

## Root cause

**CONFIRMED_DEFECT — P1:** the ranking subsystem was implemented as a manual refresh feature but never integrated into the durable full-pipeline step graph. There is no evidence of a failed ranking algorithm, persistence transaction, or swallowed exception for Run 105 because the call did not occur.

## Minimal remediation proposal (not implemented in this task)

1. Add a durable `RANKING_PROFILES` pipeline step after `COMBINING_RESULTS` and before consumers that need ranking.
2. Add a dependency injection point in `PipelineExecutionDependencies` and an idempotent ranking refresh implementation that avoids an unprotected delete/rebuild window.
3. Add ranking result/profile counts to `PipelineExecutionResult`, status UI, metrics, and persisted result JSON.
4. Define whether zero configured profiles is valid `SKIPPED` or a blocking failure; define whether zero results with non-empty inputs is `FAILED`/`PARTIAL`.
5. Add retry/idempotency tests and a Run 105-shaped fixture proving 186 combined inputs trigger ranking persistence.
6. Do not retroactively write ranking values into immutable historical Winner snapshots. A later current rescore may refer to newly persisted ranking results under explicit rescore semantics.

## Classification summary

| Finding | Classification | Severity |
|---|---|---:|
| No ranking step/trigger in full pipeline | `CONFIRMED_DEFECT` | P1 |
| Zero Run 105 ranking rows | `CONFIRMED_EXPECTED_BEHAVIOR` under current trigger architecture | P1 impact |
| No ranking error/status record | `CONTRACT_AMBIGUITY` | P1 |
| Missing ranking must not block L5 | `CONFIRMED_EXPECTED_BEHAVIOR` | P0 guard |
| Historical ranking reconstruction from current rows | Rejected design | P0 guard |
