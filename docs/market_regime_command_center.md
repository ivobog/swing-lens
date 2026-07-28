# Market Regime Command Center

The Market Regime Command Center is an additive market-permission layer for SwingLens. It calculates a deterministic market regime from cached benchmark bars, persists snapshots, and displays trading posture beside run results and ranking profiles.

It does not place broker orders, mutate ranking scores, or change ranking order in v1.

## Main Entry Points

- HTML dashboard: `/market-regime`
- Run-scoped dashboard: `/runs/{run_id}/market-regime`
- Latest JSON API: `/api/market-regime/latest`
- History JSON API: `/api/market-regime/history?limit=30`
- Run JSON API: `/api/market-regime/run/{run_id}`
- Run recalculation: `POST /api/market-regime/run/{run_id}/recalculate`

## Snapshot Creation

Snapshots are persisted in `market_regime_snapshots` with optional `run_id`.

There are three creation paths:

1. Full pipeline execution creates a run-scoped snapshot automatically in the durable `MARKET_REGIME_SNAPSHOT` step after technical scoring and before combined results.
2. `POST /api/market-regime/run/{run_id}/recalculate` explicitly rebuilds and stores a run-scoped snapshot.
3. `/market-regime` can calculate a latest index-level snapshot on demand when no persisted latest snapshot exists and market data is available.

Run pages and ranking-profile result routes prefer the latest run-scoped snapshot. If none exists, they still render without market context.

## Configuration

The main config file is `config/market_regime_command_center.yaml`.

Top-level fields:

- `calculation_version`: version tag stored with each snapshot.
- `config_version`: policy/config version stored with each snapshot.
- `symbols.primary_market`: broad-market benchmark, currently `SPY`.
- `symbols.risk_proxy`: growth/risk proxy, currently `QQQ`.
- `symbols.use_risk_proxy`: enables QQQ as an additional confidence/risk input.
- `freshness.max_stale_trading_days`: stale-data threshold used for warnings and confidence.
- `risk_state_map`: maps each regime to a display risk state.
- `market_regime_v4`: classifier thresholds passed to the existing market-regime classifier.
- `policy_by_regime`: permission matrix for each regime.

Each `policy_by_regime` entry defines:

- `position_size_multiplier`: suggested starter size multiplier, such as `1.0`, `0.5`, or `0.0`.
- `preferred_profiles`: ranking profiles favored by this market state.
- `allowed_profiles`: ranking profiles allowed at normal research visibility.
- `reduced_profiles`: profiles that should be treated with reduced aggression.
- `blocked_profiles`: profiles that should not be used for new long entries.
- `allowed_setups`: setup labels allowed by this policy.
- `blocked_setups`: setup labels blocked by this policy. `*` means all setups.
- `minimum_score_adjustment`: reserved for future threshold adjustments; v1 displays policy only and leaves scoring unchanged.
- `summary`: human-readable action summary shown in the UI.

Default policy intent:

- Bull trend and risk-on breakout permit normal long swing research.
- Bull pullback favors quality pullbacks and reduces breakout aggression.
- Choppy favors defensive quality and compounder pullbacks, reduces momentum swing, and blocks early rocket.
- Bear rally, distribution, correction, and crash risk progressively restrict or block new long exposure.
- Unknown uses a defensive, low-confidence policy.

## Data Inputs

Index health uses cached OHLCV data from `price_bars` for SPY and, when enabled, QQQ. The service reuses the existing technical feature calculation path.

Run-scoped snapshots also include:

- universe participation from current run technical, combined, ranking, and raw rows,
- sector leadership summaries,
- warning and reason lists,
- debug JSON for reproducibility.

Universe participation is based on the uploaded run universe only. It is not a full-market breadth model.

## Pipeline Behavior

The full pipeline step order is:

1. `VALIDATING_RUN`
2. `SCORING_FUNDAMENTALS`
3. `FETCHING_MARKET_DATA`
4. `SCORING_TECHNICALS`
5. `MARKET_REGIME_SNAPSHOT`
6. `COMBINING_RESULTS`

Missing SPY or stale benchmark data should produce an Unknown or low-confidence snapshot when possible. Low-confidence snapshots are nonfatal, and the pipeline can finish as partial. Systemic service errors still fail the snapshot step and pipeline.

Pipeline `result_json` includes market-regime summary fields:

- `market_regime_snapshots`
- `market_regime`
- `market_risk_state`
- `market_regime_confidence`
- `market_regime_warning_count`

## Ranking Profile Integration

Run detail pages display market context beside the Ranking Profiles section. Ranking-profile JSON responses include a `market_context` object with:

- regime,
- risk state,
- confidence,
- position-size multiplier,
- preferred, allowed, reduced, and blocked profile lists,
- current profile alignment,
- reduced or blocked profile warning,
- `score_threshold_adjustments_enabled: false`.

V1 does not change `RankingResult.profile_score`, ranking profile thresholds, or ranking order.

## Exports

Latest snapshot exports:

- `/market-regime/export.json`
- `/market-regime/export.csv`

Run-scoped exports:

- `/runs/{run_id}/market-regime/export.json`
- `/runs/{run_id}/market-regime/export.csv`

JSON exports include the full snapshot payload. CSV exports flatten the primary regime fields, policy lists, warnings, reasons, and debug fields for spreadsheet review.

## V1 Limitations

- Universe participation is run-universe based, not full-market breadth.
- Ranking scores and ranking order are not mutated by market regime policy.
- `minimum_score_adjustment` is documented and carried in config, but disabled in v1 behavior.
- There are no trading or broker-order actions.
- SPY and QQQ are the v1 benchmark set; IWM, VIX, TLT, and other macro inputs are deferred.

## Verification

Recommended regression before release:

```powershell
ruff check app tests
pytest tests/test_market_regime.py tests/test_market_regime_policy.py -q
pytest tests/test_market_regime_command_center.py tests/test_market_participation_service.py tests/test_sector_leadership_service.py -q
pytest tests/test_market_regime_routes.py tests/test_market_regime_export_service.py -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest tests/test_ranking_profile_routes.py -q
pytest -q
alembic upgrade head
alembic current
```

Manual smoke check:

1. Start the app.
2. Ensure SPY and QQQ have cached bars, or run the full pipeline with benchmark fetching.
3. Open `/market-regime`.
4. Open `/runs/{run_id}/market-regime`.
5. Confirm SPY/QQQ health, action summary, permission matrix, universe panels, history, and exports.
6. Open a run detail page and confirm ranking profiles show market context without score changes.
