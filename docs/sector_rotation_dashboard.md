# Sector Rotation Dashboard

The Sector Rotation Dashboard is an additive run-scoped leadership layer for SwingLens. It groups a run universe by normalized sector, scores sector leadership explainably, persists snapshots, and exposes dashboard, drilldown, API, and export views.

It does not place broker orders, mutate ticker ranking scores, or change ranking order in v1.

## Main Entry Points

- HTML dashboard: `/runs/{run_id}/sector-rotation`
- HTML sector drilldown: `/runs/{run_id}/sector-rotation/{sector_slug}`
- Run JSON API: `/api/runs/{run_id}/sector-rotation`
- Run sector JSON API: `/api/runs/{run_id}/sector-rotation/{sector_slug}`
- Snapshot history API: `/api/sector-rotation/snapshots?limit=30`
- Run-filtered snapshot history API: `/api/sector-rotation/snapshots?run_id={run_id}&limit=30`
- Snapshot JSON API: `/api/sector-rotation/snapshots/{snapshot_id}`
- Run recalculation: `POST /api/runs/{run_id}/sector-rotation/recalculate`

## Snapshot Creation

Snapshots are persisted in `sector_rotation_snapshots` and `sector_rotation_rows` with a required `run_id`.

There are three creation paths:

1. Full pipeline execution creates a run-scoped snapshot automatically in the durable `SECTOR_ROTATION_SNAPSHOT` step after combined results.
2. `POST /api/runs/{run_id}/sector-rotation/recalculate` explicitly rebuilds and stores a run-scoped snapshot.
3. `/runs/{run_id}/sector-rotation` and `/api/runs/{run_id}/sector-rotation` lazily calculate and persist a snapshot when the selected run has no persisted sector rotation snapshot yet.

Run detail pages show the latest run-scoped snapshot summary when one exists. If no snapshot exists, the run detail page still renders with an entry point to the dashboard.

## Configuration

The main config file is `config/sector_rotation.yaml`.

Top-level fields:

- `version`: config version stored with each snapshot hash.
- `defaults.default_ranking_profile`: ranking profile used when sector rows need a default profile label.
- `defaults.top_candidate_cutoffs`: candidate thresholds used for top-candidate share metrics.
- `defaults.min_tickers_for_normal_confidence`: ticker count needed for normal confidence.
- `defaults.min_tickers_for_high_confidence`: ticker count needed for high confidence.
- `defaults.unknown_sector_label`: fallback label for missing or unmapped sectors.
- `sector_taxonomy.canonical`: canonical sector list, including `Unknown`.
- `sector_taxonomy.aliases`: source-sector aliases normalized into canonical sectors.
- `sector_etf_proxies`: sector-to-ETF proxy map used when ETF mode is enabled.
- `universe_score`: run-universe score components, setup labels, and warning buckets.
- `etf_score`: optional sector ETF confirmation settings.
- `combined_score`: universe/ETF blend settings.
- `rotation_states`: score and risk thresholds used to assign sector states.
- `permissions`: advisory permission matrix by market bucket and rotation state.

Default universe score weights:

- `average_technical_score`: `0.25`
- `average_profile_score`: `0.20`
- `top_candidate_share`: `0.20`
- `setup_density`: `0.20`
- `risk_control`: `0.15`

Default ETF score settings:

- `etf_score.enabled`: `false`
- `etf_score.benchmark_ticker`: `SPY`
- `etf_score.weights.trend`: `0.25`
- `etf_score.weights.relative_strength`: `0.30`
- `etf_score.weights.momentum`: `0.20`
- `etf_score.weights.breakout`: `0.10`
- `etf_score.weights.risk_control`: `0.15`

Default combined score weights:

- `combined_score.weights.universe`: `0.55`
- `combined_score.weights.etf`: `0.45`
- `combined_score.missing_etf_policy`: `use_universe_only`

ETF mode remains disabled unless `etf_score.enabled` is set to `true` and the configured benchmark/proxy tickers have cached OHLCV data.

## Sector Metrics

Universe-mode metrics are calculated from the selected run only. Inputs include raw company rows, technical scores, combined results, and ranking profile results when available.

The dashboard summarizes:

- ticker count and participation by sector,
- average technical, profile, and combined scores,
- top-candidate share across configured cutoffs,
- setup distribution,
- warning distribution,
- risk flag and danger-share metrics,
- current and previous sector rank,
- score change from the prior comparable snapshot.

Missing sector labels are normalized to `Unknown` so uploaded rows remain visible instead of being dropped.

## Rotation States And Permissions

Each sector receives a rotation state:

- `Leading`
- `Improving`
- `Neutral`
- `Fading`
- `Lagging`
- `Crowded risk`
- `Risk-off`
- `Insufficient data`

Permissions are advisory labels derived from the configured market bucket and sector state:

- `full_allowed`
- `reduced_size`
- `watch_only`
- `avoid_new_longs`

The permission matrix is displayed as research guidance only. It does not place trades or alter ticker scores.

## ETF Confirmation Mode

When ETF mode is enabled, sector ETF proxies reuse the existing OHLCV feature pipeline and relative-strength calculation against the configured benchmark. ETF metrics include trend, relative strength, momentum, breakout, and risk-control components.

If both universe and ETF scores are available, the sector rotation score uses the configured combined weights. If ETF data is missing and `combined_score.missing_etf_policy` is `use_universe_only`, the sector falls back to the universe score and carries ETF warnings such as missing proxy data or missing benchmark data.

The dashboard and JSON payload include `etf_metrics` only when ETF mode produces data for that sector. Universe-only mode remains the default.

## Exports

Run-scoped exports:

- `/runs/{run_id}/sector-rotation/export.csv`
- `/runs/{run_id}/sector-rotation/export.json`
- `/runs/{run_id}/sector-rotation/brief.md`

JSON exports include the full snapshot payload, row metrics, warnings, reasons, and optional ETF metrics. CSV exports use stable flattened headers for spreadsheet review, including an `etf_score` column that remains blank in universe-only mode. Markdown exports provide a compact sector rotation brief.

## Pipeline Behavior

The full pipeline step order is:

1. `VALIDATING_RUN`
2. `SCORING_FUNDAMENTALS`
3. `FETCHING_MARKET_DATA`
4. `SCORING_TECHNICALS`
5. `MARKET_REGIME_SNAPSHOT`
6. `COMBINING_RESULTS`
7. `SECTOR_ROTATION_SNAPSHOT`

Pipeline `result_json` includes sector rotation summary fields:

- `sector_rotation_snapshots`
- `sector_rotation_sector_count`
- `sector_rotation_leading_sector`
- `sector_rotation_weakest_sector`
- `sector_rotation_warning_count`

Sector snapshot failures fail the sector rotation pipeline step. Low-confidence or incomplete sectors are represented as warnings and row-level confidence states when possible.

## V1 Limitations

- Universe metrics are based on the uploaded run universe, not full-market breadth.
- ETF mode is disabled by default and needs configured/cached ETF and benchmark data.
- Permissions are advisory and do not place, modify, or cancel trades.
- Ranking profile scores, combined scores, technical scores, and row order are not mutated by sector rotation.
- Sector normalization depends on configured aliases plus uploaded source data quality.

## Verification

Recommended regression before release:

```powershell
ruff check app tests
pytest tests/test_sector_taxonomy.py tests/test_sector_universe_service.py -q
pytest tests/test_sector_rotation_policy.py tests/test_sector_rotation_service.py -q
pytest tests/test_sector_rotation_repository.py tests/test_sector_rotation_exports.py -q
pytest tests/test_sector_rotation_routes.py -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest -q
alembic upgrade head
alembic current
```

Manual smoke check:

1. Start the app.
2. Open a run with raw rows, technical scores, combined results, and ranking profile results.
3. Open `/runs/{run_id}/sector-rotation`.
4. Confirm the mode is labeled `universe_only` unless ETF mode was intentionally enabled.
5. Confirm missing sectors appear as `Unknown`.
6. Confirm summary, sector table, profile distribution, setup distribution, and warning distribution render.
7. Open a sector drilldown route.
8. Export CSV, JSON, and Markdown.
9. Recalculate the snapshot and confirm rank/score change behavior when data changes.
10. Confirm market regime and ranking profile pages still render.
