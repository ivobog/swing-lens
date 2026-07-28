# SwingLens Execution Plan: Sector Rotation Dashboard

Source documents:

- `C:\Users\Ivica\Downloads\swinglens_sector_rotation_dashboard_srs.md`
- `C:\Users\Ivica\Downloads\swinglens_sector_rotation_dashboard_sdd.md`

This plan implements the Sector Rotation Dashboard as an additive sector-intelligence layer. V1 should use current SwingLens run data first, persist explainable sector snapshots, expose HTML/API/export routes, and leave existing stock rankings unchanged unless a later phase explicitly enables sector-aware ranking policy.

## Current Repo Baseline

- `app/services/sector_leadership_service.py` already calculates a small universe-based sector leadership panel for the Market Regime Command Center.
- `app/services/market_regime_command_center.py` already persists run-scoped market snapshots and includes universe participation plus sector leadership JSON.
- `app/models/tables.py` uses SQLAlchemy 2 typed models, PostgreSQL `JSONB`, `BigInteger` primary keys, explicit indexes, and Alembic migrations.
- `RankingResult` already exists and stores profile score, profile rank, sector, warning flags, component scores, gates, and decision labels.
- `TechnicalScore` already stores classification, dual score, feature flags, warning flags, stage, VCP/breakout/box/climax metrics, and debug JSON.
- `CombinedResult` already stores final rank/score, sector, fundamental score, dual score, classification, warning flags, and decision fields.
- `MarketRegimeSnapshot` already stores regime, risk state, position-size multiplier, preferred/reduced/blocked ranking profiles, and sector leadership JSON.
- `PriceBar` plus existing technical-indicator services can support ETF mode later, but v1 should not depend on ETF data availability.
- `app/routers/market_regime_routes.py` is the closest route/API/export pattern to copy for snapshot-driven pages.
- `app/main.py` registers routers explicitly, so a new sector rotation router must be added there.
- Prior execution plans live in `docs/` and use phased implementation with exit criteria and verification commands.

## V1 Product Decisions

Use these decisions to keep the first build useful and contained:

1. Build v1 as universe mode plus ranking profile distribution, sector state/permission, persistence, drilldown, and exports.
2. Defer ETF rotation and combined score to v2 unless ETF proxy bars are already proven available and clean.
3. Treat sector confidence separately from sector strength. Low ticker count is not the same as weak leadership.
4. Label all run-derived sector metrics as universe-based, not full-market breadth.
5. Reuse the existing `sector_leadership_service.py` concepts, but replace or wrap them with richer sector rotation DTOs rather than duplicating logic in routes.
6. Keep ranking profiles independent in v1. Sector permissions are advisory display/export fields, not ranking score mutations.
7. Persist one snapshot plus one row per sector so history and rank changes can be calculated later.
8. Use YAML config as the authority for taxonomy, aliases, scoring weights, thresholds, setup buckets, warning buckets, and permission mapping.
9. Prefer lazy calculation on page/API request first, then add pipeline integration after the core service and persistence are stable.
10. Keep Market Regime integration optional. When a run has a market-regime snapshot, use it for permissions; otherwise fall back to an unknown/choppy policy.

## Open Decisions Before Implementation

Resolve these early because they affect config defaults and persistence behavior:

1. Whether `SectorRotationSnapshot.run_id` should use `CASCADE` or `SET NULL`. Recommendation: `CASCADE`, because these snapshots are run-derived in v1.
2. Whether the existing Market Regime sector leadership panel should continue using `SectorLeadershipService` directly or read from the new latest sector rotation snapshot. Recommendation: keep existing panel unchanged in v1, then optionally converge later.
3. Which ranking profile is the default profile for sector scoring. Recommendation: `momentum_swing`.
4. Which concentration warning basis is primary. Recommendation: top 25 share, with top 10 included in debug/summary.
5. Whether permissions should use `full_allowed`, `reduced_size`, `watch_only`, `avoid_new_longs` internally and pretty labels only in templates. Recommendation: yes.
6. Whether ETF mode is included in the first PR. Recommendation: no, unless a Phase 0 data check confirms XLK/XLF/XLV/XLE/XLI/XLY/XLP/XLU/XLB/XLRE/XLC and SPY bars are present.

## Phase 0: Preparation and Baseline

Goal: establish baseline behavior and identify reusable local patterns before schema changes.

Tasks:

1. Create a branch, for example `codex/sector-rotation-dashboard`.
2. Capture current checks:
   ```powershell
   ruff check app tests
   pytest -q
   alembic heads
   ```
3. Inspect current sample data availability:
   - latest upload run with raw rows,
   - ranking result count by profile,
   - market regime snapshots by run,
   - optional ETF proxy price bars.
4. Confirm current Alembic head. The next migration should revise `0012_add_market_regime_snapshots` if that remains current.
5. Leave unrelated workspace changes alone.

Exit criteria:

- Baseline test status is known.
- Migration head is known.
- V1/v2 ETF decision is confirmed.
- At least one run exists for manual dashboard verification, or a fixture plan is documented.

Phase 0 baseline captured on `2026-07-28`:

- Branch: `codex/sector-rotation-dashboard`
- Working tree before implementation: untracked `docs/execution_plan_sector_rotation_dashboard.md`
- Alembic head: `0012_add_market_regime_snapshots`
- `ruff check app tests`: passed
- `pytest -q`: `443 passed`
- Latest upload runs:
  - Run `60`: `skimmer 2_2026-07-28 (1).csv`, `COMPLETED`, `158` raw rows
  - Run `59`: `skimmer 2_2026-07-28.csv`, `COMPLETED`, `89` raw rows
  - Run `58`: `money money_2026-07-28.csv`, `COMPLETED`, `118` raw rows
- Latest run `60` score availability:
  - `technical_scores`: `158`
  - `fundamental_scores`: `158`
  - `combined_results`: `158`
  - `ranking_results`: five profiles with `158` rows each, ranks `1-158`
- Market regime snapshots:
  - Latest run `60`: none
  - Global latest: snapshot `1`, `2026-07-27`, `Distribution`, risk state `Orange`, confidence `normal`
- ETF proxy price bars:
  - Available: `SPY`, `1536` daily bars, `2023-07-05` through `2026-07-27`
  - Missing: `XLK`, `XLC`, `XLY`, `XLP`, `XLF`, `XLV`, `XLI`, `XLE`, `XLB`, `XLRE`, `XLU`
- V1 decision confirmed: keep ETF rotation deferred; build universe mode first.

## Phase 1: Configuration and Taxonomy

Goal: make sector normalization, scoring weights, thresholds, and permissions configurable before touching persistence.

Primary files:

- `config/sector_rotation.yaml`
- `app/services/sector_rotation_config.py`
- `app/services/sector_taxonomy.py`
- `tests/test_sector_taxonomy.py`
- `tests/test_config_files.py`

Tasks:

1. Add `config/sector_rotation.yaml` with:
   - config version,
   - canonical sector taxonomy,
   - aliases,
   - default ranking profile,
   - top candidate cutoffs,
   - min ticker thresholds,
   - setup label buckets,
   - warning flag buckets,
   - universe score weights,
   - ETF proxy mapping with ETF scoring disabled,
   - combined score settings,
   - rotation state thresholds,
   - permission matrix and position-size multipliers.
2. Implement `load_sector_rotation_config(path=Path("config/sector_rotation.yaml"))`.
3. Implement `sector_rotation_config_hash(config)`.
4. Validate:
   - required sections exist,
   - universe weights sum to 1.0,
   - combined weights sum to 1.0,
   - thresholds are numeric and ordered where applicable,
   - canonical taxonomy includes `Unknown`,
   - all aliases resolve to canonical sectors,
   - all permission multipliers are between 0 and 1.
5. Implement:
   - `normalize_sector(raw_sector, config)`,
   - `sector_slug(sector)`,
   - `sector_from_slug(slug, known_sectors)`.

Tests:

- Loads default config.
- Invalid score weights fail clearly.
- Aliases normalize case-insensitively.
- Missing or blank sector becomes `Unknown`.
- Slugs handle spaces and punctuation.
- Unknown slug lookup returns `None`.

Exit criteria:

- Sector config and taxonomy are deterministic and independently tested.

Phase 1 implementation captured on `2026-07-28`:

- Added `config/sector_rotation.yaml`
- Added `app/services/sector_rotation_config.py`
- Added `app/services/sector_taxonomy.py`
- Added `tests/test_sector_rotation_config.py`
- Added `tests/test_sector_taxonomy.py`
- Extended `tests/test_config_files.py`
- Default v1 config covers:
  - canonical sector taxonomy and aliases,
  - ETF proxy mapping with ETF mode disabled,
  - universe score weights,
  - setup and warning buckets,
  - combined score fallback policy,
  - rotation state thresholds,
  - sector permission matrix and multipliers.
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_sector_rotation_config.py tests/test_sector_taxonomy.py tests/test_config_files.py -q`: `20 passed`
  - `pytest -q`: `455 passed`

## Phase 2: DTOs and Universe Metrics

Goal: aggregate rich sector metrics from one run without database writes.

Primary files:

- `app/services/sector_rotation_dtos.py`
- `app/services/sector_universe_service.py`
- `tests/test_sector_universe_service.py`

Tasks:

1. Add frozen DTOs:
   - `SectorUniverseMetrics`,
   - `SectorRotationDecision`,
   - `SectorRotationSnapshotDto`,
   - optional `SectorTickerDrilldownRow`.
2. Load `RawCompanyRow`, `FundamentalScore`, `TechnicalScore`, `CombinedResult`, and `RankingResult` for a run.
3. De-duplicate by uppercase ticker, matching existing run rules from combined/ranking services.
4. Normalize sector from raw rows first, then ranking/combined fallback, then `Unknown`.
5. Calculate per-sector:
   - ticker count,
   - universe share,
   - average fundamental score,
   - average technical score,
   - average final score,
   - average profile score for the default profile,
   - top 10/top 25/top 50 counts,
   - buyable/watch/danger counts and shares,
   - setup distribution,
   - warning distribution,
   - specific counts for clean pullback, breakout, VCP, tight base breakout, extended/overheated,
   - missing fundamental and technical counts,
   - profile distribution JSON.
6. Preserve missing values as unavailable in debug output rather than converting them to false zeros.
7. Generate low-level warnings such as `missing_sector`, `missing_technical_scores`, `missing_ranking_profile_results`.

Tests:

- Groups missing sectors as `Unknown`.
- Calculates ticker counts and shares correctly.
- Calculates top 25 counts from `RankingResult.profile_rank`.
- Handles no ranking results gracefully.
- Computes buyable/watch/danger buckets from configured labels.
- Counts danger warning flags from `TechnicalScore.warning_flags_json`.
- Missing scores produce missing counts and debug notes.
- Output sorting is deterministic.

Exit criteria:

- A run can produce complete in-memory sector universe metrics with no route or schema dependency.

## Phase 3: Universe Leadership Score and Confidence

Goal: calculate explainable 0-10 universe scores and confidence levels.

Primary files:

- `app/services/sector_universe_service.py`
- `tests/test_sector_universe_service.py`

Tasks:

1. Implement score components:
   - average technical score,
   - average profile score with fallback policy,
   - top candidate share overrepresentation,
   - setup density,
   - risk control.
2. Apply YAML weights and clamp all components/final universe score to 0-10.
3. Calculate confidence:
   - `insufficient` for zero usable tickers,
   - `low` below normal ticker threshold or with poor technical availability,
   - `high` above high ticker threshold with strong technical availability,
   - `normal` otherwise.
4. Generate reason codes:
   - `strong_average_technical_score`,
   - `top_candidate_overrepresentation`,
   - `high_setup_density`,
   - `low_danger_density`,
   - `low_confidence_sector`,
   - `high_danger_density`.
5. Preserve component scores and component missing reasons in `component_scores` and `debug`.

Tests:

- Universe score is bounded 0-10.
- A large sector does not win solely from ticker count.
- Top candidate share rewards overrepresentation against expected top share.
- High danger density reduces risk control.
- Low ticker count lowers confidence but does not force a weak score.
- Missing profile scores follow configured fallback behavior.

Exit criteria:

- Sector strength can be ranked explainably from current run data.

## Phase 4: Rotation Policy

Goal: convert scores and risk metrics into sector states, permissions, multipliers, reasons, and warnings.

Primary files:

- `app/services/sector_rotation_policy.py`
- `tests/test_sector_rotation_policy.py`

Tasks:

1. Implement `decide_sector_rotation(universe, etf, market_regime, previous, config)`.
2. Implement final score selection:
   - universe score only in v1,
   - optional ETF/combined code path behind disabled config.
3. Implement rotation states:
   - `Insufficient data`,
   - `Risk-off`,
   - `Crowded risk`,
   - `Improving`,
   - `Fading`,
   - `Leading`,
   - `Lagging`,
   - `Neutral`.
4. Implement market regime buckets:
   - supportive,
   - choppy,
   - risk_off,
   - unknown.
5. Implement permission mapping:
   - `full_allowed`,
   - `reduced_size`,
   - `watch_only`,
   - `avoid_new_longs`.
6. Include position-size multiplier and advisory reason codes.
7. Ensure high danger density prevents a clean `Leading` label without a warning or state override.

Tests:

- Assigns Leading, Improving, Neutral, Fading, Lagging, Crowded risk, Risk-off, and Insufficient data.
- Applies supportive/choppy/risk-off market policy.
- Produces reduced size in choppy conditions.
- Produces avoid permission for dangerous sectors.
- Calculates rank and score change from previous row payload.
- Keeps insufficient data separate from lagging.

Exit criteria:

- Policy decisions are pure, deterministic, and database-free.

## Phase 5: Persistence Schema and Repository

Goal: persist snapshots and per-sector rows for history, exports, and drilldowns.

Primary files:

- `app/models/tables.py`
- `alembic/versions/*_add_sector_rotation_tables.py`
- `app/services/sector_rotation_repository.py`
- `tests/test_sector_rotation_repository.py`
- `tests/test_schema_phase2.py`

Tasks:

1. Add `UploadRun.sector_rotation_snapshots` relationship.
2. Add `SectorRotationSnapshot` model:
   - `run_id`,
   - optional `market_regime_snapshot_id`,
   - `as_of_date`,
   - calculation/config version and hash,
   - mode,
   - default ranking profile,
   - benchmark ticker,
   - sector/ticker summary fields,
   - summary/warnings/debug JSON,
   - timestamps.
3. Add `SectorRotationRow` model:
   - snapshot relationship,
   - sector and slug,
   - ticker counts/shares,
   - average scores,
   - top counts,
   - setup/warning distributions,
   - universe/ETF/final scores,
   - state, permission, multiplier, confidence,
   - previous/current rank and score change,
   - JSON payloads for profile distribution, components, reasons, warnings, debug.
4. Add uniqueness:
   - snapshot identity, likely `run_id`, `as_of_date`, `calculation_version`, `config_hash`, `mode`,
   - `snapshot_id`, `sector_slug`.
5. Add indexes:
   - snapshot run/date,
   - snapshot date,
   - row snapshot/rank,
   - row sector slug.
6. Implement repository:
   - `save_snapshot(db, dto)`,
   - `latest_for_run(db, run_id)`,
   - `previous_comparable(db, as_of_date, mode, config_hash=None)`,
   - `get_snapshot_rows(db, snapshot_id)`,
   - `get_sector_row(db, snapshot_id, sector_slug)`,
   - `history(db, limit=30, run_id=None)`.
7. Services should flush; routes should commit or roll back.

Tests:

- Metadata includes both tables and constraints.
- Snapshot save writes all rows.
- Duplicate sector slug in a snapshot is rejected or replaced deterministically.
- Latest-for-run returns most recent snapshot.
- Previous comparable excludes current and mismatched modes.
- Rows are returned rank-sorted.
- Run deletion behavior matches the Phase 0 decision.

Exit criteria:

- Sector rotation snapshots and rows can be persisted/read without page integration.

## Phase 6: Orchestration Service

Goal: produce one complete snapshot DTO and optionally persist it.

Primary files:

- `app/services/sector_rotation_service.py`
- `tests/test_sector_rotation_service.py`

Tasks:

1. Implement `build_sector_rotation_snapshot(db, run_id, as_of_date=None, persist=True, config=None)`.
2. Load config and config hash.
3. Resolve `as_of_date` from latest available run/market context or current date fallback.
4. Load latest run-scoped `MarketRegimeSnapshot` if present.
5. Build universe metrics.
6. Load previous comparable snapshot rows.
7. Apply policy decisions.
8. Sort by:
   - final score descending,
   - confidence priority,
   - danger share ascending,
   - ticker count descending,
   - sector name ascending.
9. Assign current ranks and calculate rank/score changes.
10. Build summary:
   - leading sector,
   - weakest sector,
   - riskiest sector,
   - most represented top-candidate sector,
   - fastest improving sector when available,
   - concentration warnings.
11. Persist snapshot and rows when requested.
12. Return DTO that can be serialized without ORM access.

Tests:

- Empty run returns empty dashboard with warning.
- First snapshot works with no previous data.
- Second snapshot calculates rank and score changes.
- Summary fields are populated deterministically.
- Persist false returns DTO without writes.
- Missing market regime snapshot uses unknown policy.

Exit criteria:

- One service call can calculate the complete dashboard payload.

## Phase 7: Exports

Goal: export dashboard and drilldown data in stable CSV/JSON/Markdown forms.

Primary files:

- `app/services/sector_rotation_export_service.py`
- `tests/test_sector_rotation_exports.py`

Tasks:

1. Implement:
   - `export_sector_rotation_csv(snapshot, rows)`,
   - `export_sector_rotation_json(snapshot, rows)`,
   - `export_sector_rotation_markdown(snapshot, rows)`,
   - optional `export_sector_drilldown_csv(snapshot, row, tickers)`.
2. CSV should include:
   - rank,
   - sector,
   - state,
   - permission,
   - final/universe/ETF scores,
   - ticker count,
   - top 25 count/share,
   - buyable/danger share,
   - average technical/fundamental/profile scores,
   - position-size multiplier,
   - confidence,
   - warnings,
   - reasons.
3. JSON should include full component scores, debug-friendly reason codes, summary, and score source.
4. Markdown should produce a concise journaling brief.

Tests:

- CSV headers are stable.
- CSV rows are rank-sorted.
- JSON includes component scores and warnings.
- Markdown does not invent ETF details when ETF is unavailable.

Exit criteria:

- Persisted snapshots can be exported without recalculation.

## Phase 8: Routes and API

Goal: expose dashboard, drilldown, history, recalculation, and exports.

Primary files:

- `app/routers/sector_rotation_routes.py`
- `app/main.py`
- `tests/test_sector_rotation_routes.py`

Tasks:

1. Add HTML routes:
   - `GET /runs/{run_id}/sector-rotation`,
   - `GET /runs/{run_id}/sector-rotation/{sector_slug}`.
2. Add API routes:
   - `GET /api/runs/{run_id}/sector-rotation`,
   - `GET /api/runs/{run_id}/sector-rotation/{sector_slug}`,
   - `GET /api/sector-rotation/snapshots`,
   - `GET /api/sector-rotation/snapshots/{snapshot_id}`,
   - optional `POST /api/runs/{run_id}/sector-rotation/recalculate`.
3. Add export routes:
   - `GET /runs/{run_id}/sector-rotation/export.csv`,
   - `GET /runs/{run_id}/sector-rotation/export.json`,
   - `GET /runs/{run_id}/sector-rotation/brief.md`.
4. Register router in `app/main.py`.
5. Require run existence before calculating/exporting.
6. Prefer latest persisted snapshot; calculate on demand if missing.
7. Commit on successful recalculation and roll back on errors.
8. Return structured 404s for missing snapshot or unknown sector slug.

Tests:

- Dashboard route returns 200 for a run with raw rows.
- API returns structured JSON with snapshot and rows.
- Drilldown handles sector slugs with spaces and `Unknown`.
- Recalculate route persists a snapshot.
- Export content types and filenames are correct.
- Missing run returns 404.
- Existing market regime and ranking routes still pass.

Exit criteria:

- Backend and route surface are complete before template polish.

## Phase 9: Templates, Navigation, and Drilldown UI

Goal: make the dashboard usable as a daily sector cockpit.

Primary files:

- `app/templates/sector_rotation_dashboard.html`
- `app/templates/sector_rotation_drilldown.html`
- `app/templates/partials/_nav.html`
- `app/static/app.css`
- optional `app/static/sector_rotation_dashboard.js`
- `tests/test_sector_rotation_routes.py`

Tasks:

1. Add navigation from run detail and optionally primary nav.
2. Build dashboard page sections:
   - header with run/date/mode/regime/default profile,
   - summary cards,
   - main sector leadership table,
   - ranking profile distribution panel,
   - setup distribution panel,
   - risk/warning distribution panel,
   - export links,
   - collapsed debug section.
3. Build drilldown page sections:
   - sector summary,
   - score component breakdown,
   - top tickers by default profile,
   - top tickers by technical score,
   - top tickers by fundamental score,
   - buyable/watch/danger setup lists,
   - warning flags,
   - historical rank/score change,
   - ETF placeholder only when enabled or explicitly unavailable.
4. Keep UI dense and operational, matching existing SwingLens pages.
5. Do not rely on color alone for states or permissions.
6. Keep debug JSON collapsed by default.

Tests:

- Templates render with full data.
- Templates render with missing profile data.
- Templates render with empty sector data.
- Sector links point to slug routes.
- Warning/reason text appears from actual reason codes.

Exit criteria:

- A user can identify leading, risky, crowded, and improving sectors from the page.

## Phase 10: Pipeline and Run Detail Integration

Goal: make sector snapshots naturally available after ranking refreshes.

Primary files:

- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `app/routers/run_routes.py`
- `app/templates/run_detail.html`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`
- `tests/test_run_detail_view_models.py`

Tasks:

1. Add optional pipeline step `SECTOR_ROTATION_SNAPSHOT` after ranking profile generation, or after combined results if ranking profiles are absent.
2. Add dependency injection hook in `PipelineExecutionDependencies`.
3. In executor, call `SectorRotationService.build_sector_rotation_snapshot(db, run_id=...)`.
4. Add sector snapshot summary to pipeline result JSON:
   - sector count,
   - leading sector,
   - weakest sector,
   - warning count.
5. Treat missing optional ranking profile data as nonfatal.
6. Add run detail link/status:
   - latest sector snapshot date,
   - leading sector,
   - export/dashboard links.
7. Keep ranking scores unchanged.

Tests:

- Pipeline creates the step in the expected order.
- Executor calls sector rotation after required data is available.
- Missing ranking results do not fail the pipeline.
- Pipeline result JSON includes sector summary.
- Run detail renders with and without a sector snapshot.

Exit criteria:

- Full pipeline creates a run-scoped sector rotation snapshot automatically.

## Phase 11: ETF Rotation Mode

Goal: add optional ETF confirmation and combined scoring after universe mode is stable.

Primary files:

- `app/services/sector_etf_rotation_service.py`
- `app/services/sector_rotation_policy.py`
- `tests/test_sector_etf_rotation_service.py`
- `tests/test_sector_rotation_service.py`

Tasks:

1. Confirm ETF proxy bars and SPY benchmark bars are available through `PriceBar`.
2. Load ETF bars for configured proxies and benchmark.
3. Reuse existing technical indicator conventions where possible.
4. Calculate ETF metrics:
   - close,
   - SMA50/SMA200 state,
   - SMA50 slope,
   - ROC21/ROC63/ROC126,
   - ATR percent if available,
   - relative strength vs benchmark,
   - RS ROC21/ROC63,
   - Donchian 20/55 breakout,
   - distribution count,
   - risk-off flag.
5. Calculate ETF score components:
   - trend,
   - relative strength,
   - momentum,
   - breakout,
   - risk control.
6. Add combined score:
   - use configured universe/ETF weights,
   - bound final score to 0-10,
   - use universe-only policy when ETF score is unavailable.
7. Surface ETF confirmation/contradiction in reasons and warnings.

Tests:

- Missing ETF data yields null ETF score, not zero.
- Missing benchmark yields visible warning.
- ETF score is bounded 0-10.
- Combined mode uses both scores when available.
- Missing ETF does not punish universe-only sectors in v1 policy.

Exit criteria:

- ETF mode can be enabled by config without breaking universe mode.

## Phase 12: Documentation and Verification

Goal: make the feature maintainable and safe to operate.

Primary files:

- `README.md`
- optional `docs/sector_rotation_dashboard.md`
- test suite

Tasks:

1. Document config fields and default weights.
2. Document snapshot creation paths:
   - lazy page/API calculation,
   - recalculate route,
   - pipeline integration.
3. Document export endpoints.
4. Document v1 limitations:
   - universe-based metrics are not full-market breadth,
   - ETF mode is disabled unless data is configured,
   - permissions are advisory and do not place trades,
   - ranking scores are not mutated.
5. Run focused and full verification.

Recommended verification commands:

```powershell
ruff check app tests
pytest tests/test_sector_taxonomy.py tests/test_sector_universe_service.py -q
pytest tests/test_sector_rotation_policy.py tests/test_sector_rotation_service.py -q
pytest tests/test_sector_rotation_repository.py tests/test_sector_rotation_exports.py -q
pytest tests/test_sector_rotation_routes.py -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest -q
alembic upgrade head
```

Manual verification:

1. Start the app.
2. Open a run with raw rows and ranking results.
3. Open `/runs/{run_id}/sector-rotation`.
4. Confirm mode is labeled `universe_only`.
5. Confirm missing sectors appear as `Unknown`.
6. Confirm summary, sector table, profile distribution, setup distribution, and warning distribution render.
7. Open a sector drilldown route.
8. Export CSV, JSON, and Markdown.
9. Recalculate a second snapshot and confirm rank/score change behavior when data changes.
10. Confirm market regime/ranking pages still render.

Exit criteria:

- Automated checks pass.
- One run can produce, display, drill into, export, and reuse a persisted sector rotation snapshot.

## Recommended Implementation Order

1. Config and taxonomy.
2. Universe metrics DTOs and aggregation.
3. Universe score, reason codes, and confidence.
4. Rotation policy and permission matrix.
5. Snapshot/row schema, migration, and repository.
6. Orchestration service.
7. Export service.
8. API and HTML routes.
9. Templates and navigation.
10. Pipeline/run detail integration.
11. ETF mode.
12. Documentation and full regression.

## Rollback Strategy

- All schema, services, routes, and templates are additive.
- Existing combined results, ranking profile scores, technical scores, and market regime snapshots remain unchanged.
- If the dashboard page has a defect, hide the route/nav link while leaving persisted snapshots unused.
- If automatic pipeline calculation has a defect, disable or remove only the sector snapshot pipeline step while preserving manual recalculation.
- Migration downgrade removes only `sector_rotation_rows` and `sector_rotation_snapshots`.
- ETF mode is config-gated and can remain disabled independently from universe mode.

## Definition of Done

This work is done when SwingLens can calculate universe-based sector rotation from a selected run, normalize sectors, score sector leadership explainably, assign rotation states and advisory permissions, persist snapshot history, calculate rank/score changes, expose dashboard/drilldown/API/export routes, render usable templates, integrate with the pipeline or run detail, pass focused and full regression tests, and clearly label v1 results as universe-based rather than full-market breadth.
