# SwingLens Execution Plan: Market Regime Command Center

Source documents:

- `C:\Users\Ivica\Downloads\swinglens_market_regime_command_center_srs.md`
- `C:\Users\Ivica\Downloads\swinglens_market_regime_command_center_sdd.md`

This plan implements the Market Regime Command Center as an additive market-permission layer. The existing technical scoring and ranking logic stay intact in v1; the new work makes the current market regime visible, persisted, reusable, and exportable.

## Current Repo Baseline

- `app/services/market_regime.py` already exposes `classify_market_regime(spy_features, qqq_features, params)` and returns `MarketRegimeResult`.
- `config/technical_scoring_v4.yaml` already has `market_regime_v4` settings with SPY/QQQ support.
- `app/services/technical_score_service.py` already loads SPY and QQQ price bars from `price_bars` and calculates market features through `calculate_technical_features`.
- `app/services/pipeline_executor.py` currently runs: validate run, score fundamentals, fetch market data, score technicals, combine results.
- `app/services/pipeline_service.py` defines pipeline steps as constants, so adding a durable `MARKET_REGIME_SNAPSHOT` step requires updating both step creation and execution.
- `app/models/tables.py` uses SQLAlchemy 2 typed models, PostgreSQL `JSONB`, explicit indexes, and Alembic migrations.
- `app/routers/run_routes.py` owns run detail, history, ranking-profile routes, exports, and manual refresh actions.
- `app/main.py` includes routers explicitly; a new `market_regime_routes` router must be registered there.
- `app/templates/partials/_nav.html` is the primary navigation location for adding a Command Center link.
- Ranking profile work already exists through `RankingResult`, `ranking_profile_service`, and `ranking_profile_config`.

## V1 Product Decisions

Use these decisions to keep the first implementation useful and contained:

1. Reuse `classify_market_regime`; do not fork or rewrite regime rules.
2. Persist snapshots with optional `run_id`; use run-scoped snapshots when a run is provided.
3. Keep ranking profile scores unchanged in v1.
4. Expose market permission labels and position-size guidance beside rankings.
5. Use YAML config for policy and risk-state mapping.
6. Implement SPY and QQQ index health in v1.
7. Implement universe participation and sector leadership from current run data, clearly labeled as universe-based.
8. Support route-driven recalculation, but prefer pipeline-created snapshots when a run is available.
9. Keep all outputs deterministic from market data, config, and calculation version.

## Open Decisions Before Implementation

These should be decided early, because they affect config defaults and persistence constraints:

1. Unknown regime default: use Gray with 0.25 position-size multiplier and defensive-only guidance.
2. Bear rally default: use Orange in v1.
3. Score mutation: disabled in v1; display-only policy labels.
4. IWM/VIX/TLT: defer to v1.5 unless already available at almost no extra cost.
5. Snapshot identity: use `UNIQUE(run_id, as_of_date, calculation_version, config_version)` with nullable `run_id`.

## Phase 0: Preparation and Baseline

Goal: establish a clean baseline before adding schema and service layers.

Tasks:

1. Create a branch, for example `codex/market-regime-command-center`.
2. Capture current checks:
   ```powershell
   ruff check app tests
   pytest -q
   ```
3. Confirm current Alembic head. The next migration should revise the ranking-results migration head if that remains current.
4. Confirm whether the local database already has SPY and QQQ `price_bars`.
5. Leave unrelated workspace changes alone.

Exit criteria:

- Baseline test status is known.
- Current migration head is known.
- SPY/QQQ data availability is known.

Phase 0 baseline captured on `2026-07-28`:

- Branch: `codex/market-regime-command-center`
- Alembic head: `0011_create_ranking_results`
- `ruff check app tests`: passed
- `pytest -q`: `390 passed`
- SPY bars: `1536`, first `2023-07-05`, latest `2026-07-27`
- QQQ bars: `1536`, first `2023-07-05`, latest `2026-07-27`

## Phase 1: Policy Configuration

Goal: define the market permission matrix independently from routes and database writes.

Primary files:

- `config/market_regime_command_center.yaml`
- `app/services/market_regime_policy.py`
- `tests/test_market_regime_policy.py`
- `tests/test_config_files.py`

Tasks:

1. Add `config/market_regime_command_center.yaml` with:
   - engine enabled/version/config version,
   - symbols: SPY primary and QQQ risk proxy,
   - freshness thresholds,
   - risk-state mapping,
   - policy matrix for every supported regime,
   - setup permissions,
   - history/export settings.
2. Implement frozen DTO/dataclass objects:
   - `MarketRegimePolicyDto`
   - `MarketRegimeCommandCenterConfig`
3. Implement config loader and validator:
   - all known regimes have policies,
   - multipliers are between 0 and 1,
   - required lists default to empty lists,
   - `Unknown` policy exists as fallback,
   - risk-state values are one of Green, Yellow, Orange, Red, Gray.
4. Implement `MarketRegimePolicyService.policy_for(regime_result, config, freshness=None)`.
5. Add confidence and stale-data downgrades:
   - low confidence adds warning and can downgrade to Gray,
   - `risk_off` adds `market_risk_off`,
   - severely stale data forces configured stale state.

Tests:

- Bull trend maps to Green and 1.00 size.
- Choppy maps to Yellow and blocks/reduces Early Rocket.
- Distribution adds `market_risk_off`.
- Crash risk blocks all new long entries.
- Unknown falls back to Gray.
- Unknown regime strings use the Unknown policy.
- Bad config fails with clear errors.

Exit criteria:

- Market permissions can be produced from a `MarketRegimeResult` without database access.

Phase 1 implementation captured on `2026-07-28`:

- Added `config/market_regime_command_center.yaml`
- Added `app/services/market_regime_policy.py`
- Added `tests/test_market_regime_policy.py`
- Extended `tests/test_config_files.py`
- Focused verification:
  - `pytest tests/test_market_regime_policy.py tests/test_config_files.py -q`: `17 passed`
  - `ruff check app tests`: passed
- Full verification:
  - `pytest -q`: `400 passed`

## Phase 2: Snapshot Schema and Repository

Goal: persist deterministic regime snapshots and make them queryable.

Primary files:

- `app/models/tables.py`
- `alembic/versions/*_add_market_regime_snapshots.py`
- `app/services/market_regime_repository.py`
- `tests/test_market_regime_repository.py` or schema test coverage

Tasks:

1. Add `MarketRegimeSnapshot` model with:
   - optional `run_id`,
   - `as_of_date`,
   - `calculation_version`,
   - `config_version`,
   - regime/risk fields,
   - policy JSON fields,
   - index health JSON,
   - universe participation JSON,
   - sector leadership JSON,
   - reasons/warnings/debug JSON.
2. Add `UploadRun.market_regime_snapshots` relationship.
3. Use `ondelete="SET NULL"` for `run_id` if snapshots should survive run deletion, or `CASCADE` if run snapshots are disposable. Recommendation: `SET NULL`.
4. Add indexes:
   - `as_of_date`,
   - `run_id`,
   - `regime`,
   - `risk_state`.
5. Add uniqueness:
   - `run_id`, `as_of_date`, `calculation_version`, `config_version`.
6. Implement repository methods:
   - `upsert_snapshot(db, dto, run_id=None)`,
   - `latest(db)`,
   - `latest_for_run(db, run_id)`,
   - `history(db, limit=30)`,
   - `delete_for_run(db, run_id)`.
7. Keep upsert deterministic and route-safe; services flush, routes commit.

Tests:

- Metadata includes the table, indexes, and uniqueness.
- Upsert updates duplicate run/date/version snapshots.
- Latest returns most recent snapshot.
- History returns descending date order.
- Latest-for-run filters by run ID.

Exit criteria:

- Snapshots can be stored and read without page or pipeline integration.

Phase 2 implementation captured on `2026-07-28`:

- Added `MarketRegimeSnapshot` ORM model and `UploadRun.market_regime_snapshots`
- Added Alembic migration `20260728_0012_add_market_regime_snapshots.py`
- Added `app/services/market_regime_repository.py`
- Added `tests/test_market_regime_repository.py`
- Extended `tests/test_schema_phase2.py`
- Verification:
  - `alembic heads`: `0012_add_market_regime_snapshots (head)`
  - `ruff check app tests`: passed
  - `pytest tests/test_schema_phase2.py tests/test_market_regime_repository.py -q`: `28 passed`
  - `pytest -q`: `410 passed`
  - `alembic upgrade head`: upgraded `0011_create_ranking_results -> 0012_add_market_regime_snapshots`
  - `alembic current`: `0012_add_market_regime_snapshots (head)`

## Phase 3: Market Input and Index Health Services

Goal: reuse existing price-bar and feature-calculation paths for SPY/QQQ.

Primary files:

- `app/services/market_regime_command_center.py`
- `app/services/market_participation_service.py`
- `app/services/sector_leadership_service.py`
- `app/services/price_bar_repository.py` only if a helper needs to be shared
- `tests/test_market_regime_command_center.py`

Tasks:

1. Implement DTOs:
   - `MarketRegimeCommandCenterDto`
   - `IndexHealthDto`
   - `MarketParticipationDto`
   - `SectorLeadershipRow`
2. Extract or reuse the feature-loading pattern from `technical_score_service._market_features`.
3. Load market inputs from `price_bars` for configured symbols.
4. Compute `as_of_date` from latest available market bar.
5. Build SPY/QQQ `IndexHealthDto` values:
   - latest close,
   - SMA50/SMA200 booleans,
   - SMA50 slope,
   - ROC21/ROC63,
   - distribution count,
   - Donchian 20 breakout,
   - stale flag and warnings.
6. Call `classify_market_regime` with config-provided market-regime params.
7. Apply policy mapping.
8. Build deterministic action summary from regime, policy, and confidence.
9. Persist through `MarketRegimeRepository`.
10. Handle missing SPY as Unknown/Gray with visible warning, not an exception.

Tests:

- Missing SPY creates Unknown/low-confidence snapshot.
- Missing QQQ lowers confidence when QQQ is enabled.
- SPY/QQQ feature payload reaches `classify_market_regime`.
- Action summary includes size multiplier and confidence caveat.
- Snapshot stores input symbols and warnings.

Exit criteria:

- `build_snapshot(db, run_id=None)` creates a persisted, complete index-only snapshot.

Phase 3 implementation captured on `2026-07-28`:

- Added `app/services/market_regime_command_center.py`
- Added DTOs for command-center snapshot, index health, participation, and sector rows
- Implemented SPY/QQQ market input loading through existing price-bar and feature paths
- Implemented index health, freshness/staleness handling, classification, policy application, action summary, and repository persistence
- Added `tests/test_market_regime_command_center.py`
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_market_regime_command_center.py tests/test_market_regime_policy.py tests/test_market_regime_repository.py -q`: `20 passed`
  - `pytest -q`: `416 passed`

## Phase 4: Universe Participation and Sector Leadership

Goal: enrich run-scoped snapshots with current-universe breadth proxies.

Primary files:

- `app/services/market_participation_service.py`
- `app/services/sector_leadership_service.py`
- `tests/test_market_participation_service.py`
- `tests/test_sector_leadership_service.py`

Tasks:

1. Load `TechnicalScore`, `CombinedResult`, `RankingResult`, and `RawCompanyRow` for a run.
2. Calculate participation metrics:
   - ticker count,
   - technical count,
   - average technical score,
   - clean pullback count,
   - fresh breakout count,
   - VCP count,
   - danger count,
   - market-risk-warning count,
   - above SMA50/SMA200 percentages when debug values exist,
   - average final ranking score by profile when available.
3. Calculate sector leadership rows:
   - sector with `Unknown` fallback,
   - ticker count,
   - average technical score,
   - average fundamental score,
   - top-25 counts by ranking profile,
   - setup counts,
   - danger counts,
   - bounded leadership score.
4. Store these objects into snapshot JSON so page load does not rescan large runs.
5. Mark outputs as universe-based, not full-market breadth.

Tests:

- Missing debug keys produce unavailable metrics, not zero.
- Missing sector groups under Unknown.
- Empty run returns empty panels with notes.
- Sector score is bounded 0-10.
- Top-25 count works when ranking results exist and gracefully skips when absent.

Exit criteria:

- Run-scoped snapshot contains participation and sector panels.

Phase 4 implementation captured on `2026-07-28`:

- Added shared DTO module `app/services/market_regime_dtos.py`
- Added `app/services/market_participation_service.py`
- Added `app/services/sector_leadership_service.py`
- Wired run-scoped participation and sector leadership into `MarketRegimeCommandCenterService.build_snapshot`
- Added `tests/test_market_participation_service.py`
- Added `tests/test_sector_leadership_service.py`
- Extended `tests/test_market_regime_command_center.py`
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_market_participation_service.py tests/test_sector_leadership_service.py tests/test_market_regime_command_center.py -q`: `13 passed`
  - `pytest -q`: `423 passed`

## Phase 5: Routes, API, and Exports

Goal: expose snapshots through HTML, JSON, CSV, and explicit recalculation.

Primary files:

- `app/routers/market_regime_routes.py`
- `app/main.py`
- `app/services/market_regime_export_service.py`
- `tests/test_market_regime_routes.py`
- `tests/test_market_regime_export_service.py`

Tasks:

1. Add HTML routes:
   - `GET /market-regime`,
   - `GET /runs/{run_id}/market-regime`.
2. Add API routes:
   - `GET /api/market-regime/latest`,
   - `GET /api/market-regime/history?limit=30`,
   - `GET /api/market-regime/run/{run_id}`,
   - `POST /api/market-regime/run/{run_id}/recalculate`.
3. Add export routes:
   - `GET /market-regime/export.json`,
   - `GET /market-regime/export.csv`,
   - `GET /runs/{run_id}/market-regime/export.json`,
   - `GET /runs/{run_id}/market-regime/export.csv`.
4. Register router in `app/main.py`.
5. On HTML/API latest, prefer persisted latest snapshot; calculate on demand only when safe and data exists.
6. Recalculation route should commit on success and roll back on failure, matching existing route style.
7. JSON export includes full DTO/snapshot payload.
8. CSV export flattens primary fields:
   - date,
   - regime,
   - risk state,
   - score,
   - confidence,
   - risk flags,
   - position multiplier,
   - preferred profiles,
   - warnings.

Tests:

- `/market-regime` returns 200 when snapshot exists.
- Latest API returns stable JSON.
- Missing snapshot returns structured 404 or computes when data is available.
- History API is ordered and limited.
- Recalculate commits and persists snapshot.
- Export JSON and CSV content types are correct.

Exit criteria:

- The Command Center is accessible as a backend/API feature.

Phase 5 implementation captured on `2026-07-28`:

- Added `app/services/market_regime_export_service.py`
- Added `app/routers/market_regime_routes.py`
- Registered market-regime routes in `app/main.py`
- Added simple Phase 5 HTML responses for `/market-regime` and `/runs/{run_id}/market-regime`
- Added latest/history/run API routes, run recalculation route, and JSON/CSV export routes
- Added `tests/test_market_regime_export_service.py`
- Added `tests/test_market_regime_routes.py`
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_market_regime_export_service.py tests/test_market_regime_routes.py -q`: `14 passed`
  - `pytest -q`: `437 passed`

## Phase 6: HTML Template and Navigation

Goal: make the Command Center usable as a daily cockpit page.

Primary files:

- `app/templates/market_regime.html`
- `app/templates/partials/_nav.html`
- `app/static/app.css`
- optional `app/static/market_regime.js`

Tasks:

1. Add a nav link labeled `Market Regime`.
2. Build `market_regime.html` with:
   - header summary,
   - action summary,
   - risk state label with text,
   - permission matrix,
   - SPY/QQQ health cards,
   - warning explanations,
   - ranking profile alignment,
   - setup permission matrix,
   - universe participation panel,
   - sector leadership table,
   - regime history table,
   - collapsible debug JSON.
3. Add risk-state classes with text labels; do not rely on color alone.
4. Keep the page readable when universe or sector panels are unavailable.
5. Keep debug JSON collapsed by default.

Tests:

- Template renders with full snapshot.
- Template renders with index-only snapshot.
- Warning explanations appear for known warning codes.
- Missing optional panels do not crash rendering.

Exit criteria:

- A user can open `/market-regime` and understand trade stance, allowed profiles, allowed setups, and warnings.

Phase 6 implementation captured on `2026-07-28`:

- Added `app/templates/market_regime.html`
- Added Market Regime navigation link in `app/templates/partials/_nav.html`
- Added MRCC-specific responsive styling in `app/static/app.css`
- Updated `app/routers/market_regime_routes.py` to render the template with snapshot, history, warning explanations, and profile alignment context
- Extended `tests/test_market_regime_routes.py` with template context, navigation, and real TestClient render coverage
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_market_regime_routes.py -q`: `13 passed`
  - `pytest -q`: `439 passed`

## Phase 7: Pipeline Integration

Goal: create run-scoped snapshots automatically during full pipeline execution.

Primary files:

- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`

Tasks:

1. Add pipeline step name:
   - `MARKET_REGIME_SNAPSHOT`.
2. Insert it after `SCORING_TECHNICALS` and before `COMBINING_RESULTS`.
3. Add optional dependency to `PipelineExecutionDependencies`:
   - `build_market_regime_snapshot`.
4. In executor, call snapshot service with `run_id`.
5. Add snapshot summary counts to pipeline `result_json`.
6. If market snapshot fails because of missing SPY, mark step completed with low-confidence snapshot if possible; only fail the pipeline for systemic errors.
7. Ensure cancellation checks still happen before and after this step.

Tests:

- Pipeline creates the new step.
- Executor calls snapshot service after technical scoring.
- Missing SPY produces low-confidence snapshot and pipeline can finish partial rather than crashing.
- Pipeline result JSON includes regime and risk state.

Exit criteria:

- Full pipeline creates a run-scoped market regime snapshot automatically.

Phase 7 implementation captured on `2026-07-28`:

- Added durable `MARKET_REGIME_SNAPSHOT` pipeline step after technical scoring
- Added `PipelineStatus.MARKET_REGIME_SNAPSHOT`
- Wired `PipelineExecutionDependencies.build_market_regime_snapshot` to `MarketRegimeCommandCenterService.build_snapshot(db, run_id=...)`
- Added snapshot count, regime, risk-state, confidence, and warning-count fields to pipeline execution result and public `result_json`
- Extended pipeline service and executor tests for step creation, execution order, low-confidence nonfatal partial snapshots, and result JSON fields
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q`: `12 passed`
  - `pytest -q`: `440 passed`

## Phase 8: Ranking Profile Integration

Goal: show market policy beside rankings without mutating v1 scores.

Primary files:

- `app/routers/run_routes.py`
- `app/templates/run_detail.html`
- ranking profile result template/routes if separated later
- `app/services/ranking_profile_service.py` only if a reusable helper is useful
- `tests/test_ranking_profile_routes.py`
- route/template tests as needed

Tasks:

1. Load latest run snapshot for run detail and ranking-profile result routes.
2. Build a profile alignment payload:
   - Preferred,
   - Allowed,
   - Reduced,
   - Blocked.
3. Display current market context:
   - regime,
   - risk state,
   - position-size multiplier,
   - preferred profiles,
   - blocked/reduced profiles.
4. Add warning text when a viewed profile is reduced or blocked.
5. Do not change `RankingResult.profile_score` or ranking order in v1.
6. Prepare a future config flag for score threshold adjustment, but leave it disabled.

Tests:

- Ranking page shows market context when snapshot exists.
- Ranking page still renders when no snapshot exists.
- Choppy marks Early Rocket as blocked/reduced according to config.
- Existing ranking score tests remain unchanged.

Exit criteria:

- Ranking profile pages can read and display current market policy without score mutation.

Phase 8 implementation captured on `2026-07-28`:

- Loaded latest run-scoped market-regime snapshots into run detail and ranking-profile result routes
- Added shared route view-model helpers for market context, profile alignment, and profile-specific reduced/blocked warnings
- Displayed regime, risk state, position-size guidance, preferred profiles, and reduced/blocked profile labels beside run-detail ranking profiles
- Included market context in ranking-profile JSON payloads while leaving `RankingResult.profile_score` and ranking order unchanged
- Added disabled `score_threshold_adjustments_enabled` metadata for future threshold adjustments
- Extended ranking route and run-detail view-model tests for snapshot/no-snapshot behavior and Choppy `early_rocket` blocking
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_ranking_profile_routes.py tests/test_run_detail_view_models.py -q`: `34 passed`
  - `pytest tests/test_market_regime_routes.py -q`: `13 passed`
  - `pytest -q`: `443 passed`

## Phase 9: Documentation and Verification

Goal: make the feature maintainable and safe to operate.

Primary files:

- `README.md` or `docs/market_regime_command_center.md`
- test suite

Tasks:

1. Document config fields and default policy meaning.
2. Document how snapshots are created:
   - pipeline,
   - explicit recalc route,
   - optional on-demand latest page calculation.
3. Document export endpoints.
4. Document v1 limitations:
   - universe participation is not full market breadth,
   - no score mutation,
   - no trading/broker actions.
5. Run focused and full verification.

Recommended verification commands:

```powershell
ruff check app tests
pytest tests/test_market_regime.py tests/test_market_regime_policy.py -q
pytest tests/test_market_regime_command_center.py tests/test_market_participation_service.py tests/test_sector_leadership_service.py -q
pytest tests/test_market_regime_routes.py tests/test_market_regime_export_service.py -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest tests/test_ranking_profile_routes.py -q
pytest -q
alembic upgrade head
```

Manual verification:

1. Start the app.
2. Ensure SPY and QQQ bars exist or fetch benchmarks through the existing flow.
3. Run a full pipeline for a sample upload.
4. Open `/market-regime`.
5. Open `/runs/{run_id}/market-regime`.
6. Confirm SPY/QQQ panels, action summary, permissions, history, and exports.
7. Open ranking profile pages and confirm market context appears without score changes.

Exit criteria:

- Automated checks pass.
- One run can produce, display, export, and reuse a market regime snapshot.

Phase 9 implementation captured on `2026-07-28`:

- Added `docs/market_regime_command_center.md`
- Updated `README.md` with config and export references
- Documented config fields, default policy meaning, snapshot creation paths, export endpoints, pipeline behavior, ranking-profile integration, and v1 limitations
- Verification:
  - `ruff check app tests`: passed
  - `pytest tests/test_market_regime.py tests/test_market_regime_policy.py -q`: `14 passed`
  - `pytest tests/test_market_regime_command_center.py tests/test_market_participation_service.py tests/test_sector_leadership_service.py -q`: `13 passed`
  - `pytest tests/test_market_regime_routes.py tests/test_market_regime_export_service.py -q`: `16 passed`
  - `pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q`: `12 passed`
  - `pytest tests/test_ranking_profile_routes.py -q`: `11 passed`
  - `pytest -q`: `443 passed`
  - `alembic heads`: `0012_add_market_regime_snapshots (head)`
  - `alembic upgrade head`: passed
  - `alembic current`: `0012_add_market_regime_snapshots (head)`

## Recommended Implementation Order

1. Policy YAML and `MarketRegimePolicyService`.
2. Snapshot model, Alembic migration, and repository.
3. Command Center service with SPY/QQQ index health.
4. JSON/CSV export service.
5. API and HTML routes.
6. Template and navigation.
7. Universe participation and sector leadership.
8. Pipeline step integration.
9. Ranking-profile display integration.
10. Documentation and full regression.

## Rollback Strategy

- All schema and services are additive.
- Existing technical scoring, combined results, and ranking score calculations remain unchanged.
- If the page has a defect, remove or hide the nav link and keep snapshots unused.
- If the pipeline step has a defect, disable snapshot creation in config or route around the step while preserving manual recalculation.
- Migration downgrade removes only `market_regime_snapshots`.

## Definition of Done

This work is done when SwingLens can calculate and persist deterministic market regime snapshots, show `/market-regime` and run-specific Command Center pages, expose latest/history/run APIs, export JSON and CSV, display SPY/QQQ health, explain warnings, show universe and sector panels for run snapshots, display market policy beside ranking profiles without mutating scores, and pass focused plus full regression tests.
