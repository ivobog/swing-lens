# SwingLens Execution Plan: Ranking Profiles

Source documents:

- `C:\Users\Ivica\Downloads\swinglens_ranking_profiles_srs.md`
- `C:\Users\Ivica\Downloads\swinglens_ranking_profiles_sdd.md`

This plan implements multiple configurable ranking profiles as an additive backend layer. The existing `combined_results` cockpit remains unchanged, and profile-specific rows are persisted in a new `ranking_results` table.

## Current Repo Baseline

- `app/services/combined_decision.py` already loads raw rows, fundamentals, and technicals once per run, computes decisions in pure-ish service functions, deletes old rows for the run, and persists refreshed `CombinedResult` rows.
- `app/models/tables.py` uses SQLAlchemy 2 typed models, PostgreSQL `JSONB`, `BigInteger` primary keys, explicit indexes, and relationship back-population from `UploadRun`.
- Alembic revisions use stable revision IDs such as `0010_add_earnings_risk_gate`; the next migration should revise `0010_add_earnings_risk_gate`.
- `app/services/export_service.py` centralizes CSV headers and row shaping.
- `app/routers/run_routes.py` owns run detail actions and CSV export routes.
- Existing tests are service-heavy pytest tests with small model factories, especially `tests/test_combined_decision.py` and `tests/test_exports_history.py`.

## V1 Product Decisions

Use these decisions to keep scope tight:

1. Generate only enabled profiles from `config/ranking_profiles.yaml`.
2. Trigger ranking profile generation manually through backend routes/service methods first.
3. Use newly derived technical profile component scores as the technical input, not raw `dual_score`.
4. Keep `combined_results` as the default cockpit view.
5. Do not add profile management UI in v1.
6. Add optional profile result routes and CSV exports; defer run-detail UI tabs unless the backend is complete and stable.

## Phase 0: Preparation and Baseline

Goal: make the ranking-profile work easy to verify and roll back.

Tasks:

1. Create a branch, for example `codex/ranking-profiles`.
2. Capture baseline checks:
   ```powershell
   pytest -q
   ruff check app tests
   ```
3. Confirm the current Alembic head is `0010_add_earnings_risk_gate`.
4. Leave unrelated workspace changes alone, including the existing untracked `=` file.

Exit criteria:

- Baseline test status is known.
- No existing combined-result behavior has been modified.

## Phase 1: Configuration

Goal: load and validate ranking profiles before touching database state.

Primary files:

- `config/ranking_profiles.yaml`
- `app/services/ranking_profile_config.py`
- `tests/test_ranking_profile_config.py`
- `tests/test_config_files.py`

Tasks:

1. Add `config/ranking_profiles.yaml` with the five enabled profiles:
   - `momentum_swing`
   - `quality_momentum`
   - `early_rocket`
   - `clean_compounder_pullback`
   - `defensive_quality`
2. Implement frozen dataclasses:
   - `MissingDataPolicy`
   - `RankingThresholds`
   - `RankingProfileConfig`
3. Implement `load_ranking_profiles(path=Path("config/ranking_profiles.yaml"))`.
4. Validate:
   - profile labels and descriptions exist,
   - technical plus fundamental weights sum to 1.0,
   - technical component weights sum to 1.0,
   - thresholds are ordered high to low,
   - penalties are non-negative,
   - component names are from the supported component registry,
   - no enabled profile set is empty.
5. Raise a custom `RankingProfileConfigError` with profile name and bad field.

Tests:

- Loads all five starter profiles.
- Ignores disabled profiles.
- Fails on invalid profile weights.
- Fails on invalid component weights.
- Fails on unknown technical component.
- Fails on unordered thresholds.
- Includes profile name in validation errors.

Exit criteria:

- Ranking profile YAML is validated by automated tests.
- No DB or route changes are needed yet.

## Phase 2: Pure Scoring Components

Goal: convert existing technical/fundamental model data into profile inputs without SQLAlchemy writes.

Primary files:

- `app/services/ranking_profile_components.py`
- `app/services/ranking_profile_penalties.py`
- `app/services/ranking_profile_gates.py`
- `app/services/ranking_profile_engine.py`
- `tests/test_ranking_profile_components.py`
- `tests/test_ranking_profile_engine.py`

Tasks:

1. Implement numeric helpers:
   - `_float_or_none`
   - `clamp_score`
   - safe nested debug lookup for `debug_json` and `v4_debug_json`
   - boolean-to-score helpers that return 0.0 or 10.0
2. Implement the component registry required by the SRS:
   - `momentum_strength`
   - `momentum_health`
   - `momentum_danger`
   - `trend_quality`
   - `setup_quality`
   - `breakout_quality`
   - `vcp_quality`
   - `box_tightness`
   - `breakout_or_vcp_quality`
   - `pullback_health`
   - `relative_strength`
   - `relative_strength_acceleration`
   - `volume_expansion`
   - `trend_repair`
   - `risk_control`
   - `market_regime_alignment`
3. Prefer persisted v4 columns on `TechnicalScore` when available, then fall back to `debug_json["explainability"]`.
4. Add dataclasses:
   - `ProfilePenaltyResult`
   - `GateResult`
   - `RankingProfileDecision`
5. Reuse existing constants and helpers where practical:
   - danger classifications from `combined_decision.py`
   - earnings risk via `calculate_earnings_risk`
   - raw earnings date detection behavior from the combined service
6. Implement `calculate_technical_profile_score`.
7. Implement `calculate_profile_score` with missing-data rescaling and penalty behavior.
8. Implement decision labels:
   - `Strong candidate`
   - `Candidate`
   - `Watch`
   - `Avoid`
   - `Blocked by earnings gate`
   - `Speculative watch`
   - `Low confidence`
9. Implement gates:
   - earnings block,
   - danger cap,
   - liquidity cap,
   - fundamental floor,
   - data quality cap.
10. Implement deterministic sort key:
    - sort bucket,
    - profile score descending,
    - fundamental score descending,
    - technical profile score descending,
    - ticker ascending.

Tests:

- Component extraction handles missing technicals and missing debug JSON.
- `risk_control` reverses risk consistently and clamps to 0-10.
- Momentum Swing ranks a technical leader above a weaker technical peer.
- Clean Compounder Pullback ranks a fundamental leader above a weak-fundamental breakout.
- Early Rocket favors breakout/trend repair candidates.
- Defensive Quality penalizes danger/liquidity more strongly.
- Earnings block overrides a high score.
- Fundamental floor caps decisions as configured.
- Missing data policy is deterministic and produces warning flags.

Exit criteria:

- A list of `RawCompanyRow` plus score dictionaries can produce fully ranked `RankingProfileDecision` objects without database writes.

## Phase 3: Persistence Model and Migration

Goal: store profile rankings independently from combined results.

Primary files:

- `app/models/tables.py`
- `alembic/versions/20260709_0011_create_ranking_results.py`
- `tests/test_schema_phase2.py` or new `tests/test_ranking_results_schema.py`

Tasks:

1. Add `RankingResult` SQLAlchemy model.
2. Add `UploadRun.ranking_results` relationship with cascade delete.
3. Use `BigInteger` primary key to match existing table conventions.
4. Use `JSONB` for JSON columns:
   - `warning_flags_json`
   - `penalties_json`
   - `gates_json`
   - `component_scores_json`
   - `debug_json`
5. Use `Text` for labels/notes unless a bounded `String` is already important for indexing.
6. Add unique constraint:
   - `run_id`, `ranking_profile`, `ticker`
7. Add indexes:
   - `run_id`
   - `ticker`
   - `ranking_profile`
   - `run_id`, `ranking_profile`, `profile_rank`
   - `run_id`, `ranking_profile`, `profile_score`
   - `earnings_risk_level`
8. Create matching Alembic upgrade and downgrade.

Tests:

- SQLAlchemy metadata includes the table and constraints.
- Alembic migration can create and drop the table in the test database setup pattern already used by the repo.
- Cascade relationship is configured from `UploadRun`.

Exit criteria:

- The additive schema exists and does not alter `combined_results`.

## Phase 4: Refresh Service

Goal: calculate and persist profile rankings idempotently.

Primary files:

- `app/services/ranking_profile_service.py`
- `tests/test_ranking_profile_service.py`

Tasks:

1. Implement:
   - `refresh_all_ranking_profiles(db, run_id, today=None)`
   - `refresh_ranking_profile(db, run_id, profile_name, today=None)`
   - `get_ranking_profiles()`
   - `get_ranking_results(db, run_id, profile_name)`
   - `get_all_ranking_results(db, run_id)`
2. Load rows, fundamentals, and technicals once per refresh.
3. De-duplicate raw rows by uppercase ticker, following `combined_decision._unique_rows`.
4. For refresh-all, delete only `RankingResult.run_id == run_id`.
5. For refresh-one, delete only matching `run_id` and `ranking_profile`.
6. Convert decisions to `RankingResult` models, rounding numeric scores consistently with existing `_to_decimal` style.
7. Store `notes` as a readable comma-separated string and warning/penalty/gate/component/debug details in JSONB.
8. Flush but let routes decide commit/rollback, matching current service style.

Tests:

- Refresh all profiles creates `ticker_count * enabled_profile_count` rows.
- Ranks start at 1 for each profile.
- Refresh all twice does not duplicate rows.
- Refresh one profile replaces only that profile.
- Existing `combined_results` rows survive profile refresh.
- Missing run fails cleanly.
- Unknown profile fails cleanly.

Exit criteria:

- Profile results are persisted idempotently and independently of the cockpit.

## Phase 5: CSV Export

Goal: export one profile or all profiles in stable rank order.

Primary files:

- `app/services/ranking_result_export.py`
- optionally `app/services/export_service.py`
- `tests/test_ranking_result_export.py`

Tasks:

1. Implement `RANKING_RESULT_HEADERS`.
2. Implement:
   - `export_ranking_profile_csv(db, run_id, profile_name)`
   - `export_all_ranking_profiles_csv(db, run_id)`
3. Include required fields:
   - rank,
   - ticker,
   - company name,
   - sector,
   - profile name and label,
   - profile score,
   - technical profile score,
   - fundamental score,
   - base technical score,
   - classifications and labels,
   - decision,
   - position size hint,
   - notes,
   - warning flags,
   - earnings date/risk.
4. Export missing fields as blanks.
5. For all-profiles export, order by profile name then rank.

Tests:

- Profile CSV order matches `profile_rank`.
- All-profiles CSV includes all profiles.
- JSON warning flags are rendered as semicolon-separated text.
- Missing optional fields export as blanks.

Exit criteria:

- Persisted profile rankings can be exported without loading unrelated run relationships.

## Phase 6: Routes

Goal: expose backend access without building profile-management UI.

Primary files:

- `app/routers/run_routes.py` or new `app/routers/ranking_routes.py`
- `app/main.py` if adding a new router
- `tests/test_ranking_profile_routes.py`

Tasks:

1. Add JSON/service-style routes:
   - `GET /runs/{run_id}/rankings/profiles`
   - `POST /runs/{run_id}/rankings/refresh`
   - `POST /runs/{run_id}/rankings/{profile_name}/refresh`
   - `GET /runs/{run_id}/rankings/{profile_name}`
2. Add CSV routes:
   - `GET /runs/{run_id}/rankings/{profile_name}/export.csv`
   - `GET /runs/{run_id}/rankings/export.csv`
3. Validate run existence before refresh/export.
4. Commit on successful refresh and roll back on service errors.
5. Return clear `404` for missing runs and unknown profiles.
6. Keep existing `/runs/{run_id}/exports/combined.csv` behavior unchanged.

Tests:

- List profiles returns five enabled profile definitions.
- Refresh-all route creates rows and commits.
- Refresh-one route updates only one profile.
- Profile results route returns rank-sorted results.
- CSV routes return `text/csv` with safe filenames.
- Existing export route tests still pass.

Exit criteria:

- Backend consumers can refresh, view, and export profile rankings.

## Phase 7: Optional Run Detail Integration

Goal: make the feature discoverable without building profile management.

Primary files:

- `app/templates/run_detail.html`
- `app/routers/run_routes.py`
- `app/static/app.css`
- route/template tests as needed

Tasks:

1. Add a "Refresh ranking profiles" action near the existing combined refresh action.
2. Add links to profile CSV exports after profiles exist.
3. Optionally show a compact profile summary:
   - profile label,
   - row count,
   - top ticker,
   - top decision,
   - warning count.
4. Defer full tabbed ranking tables unless explicitly needed.

Tests:

- Run detail renders when ranking results are absent.
- Run detail renders profile summary when results exist.
- Existing cockpit table remains the primary view.

Exit criteria:

- The user can discover and refresh ranking profiles from the run detail page.

## Phase 8: Golden Regression

Goal: prove the five ranking profiles produce meaningfully different outputs.

Primary files:

- `tests/test_ranking_profiles_golden.py`
- `tests/fixtures/ranking_profiles_golden.json`

Tasks:

1. Create fixture tickers:
   - `MOMO`
   - `QUAL`
   - `ROKT`
   - `COMP`
   - `RISK`
   - `MISS`
2. Build model factories that exercise:
   - high technical momentum,
   - strong fundamentals,
   - early breakout,
   - clean pullback,
   - danger classification,
   - missing data.
3. Assert directional behavior rather than fragile exact global ranks where possible.
4. Assert exact decisions for gates:
   - danger becomes `Avoid` or capped,
   - earnings block becomes `Blocked by earnings gate`,
   - missing data gets warning flags.

Exit criteria:

- The profile engine has a stable regression suite that catches accidental profile drift.

## Recommended Implementation Order

1. `ranking_profiles.yaml` plus config loader.
2. Component extractor and pure ranking engine.
3. Penalties and gates.
4. `RankingResult` model and Alembic migration.
5. Idempotent refresh service.
6. CSV export service.
7. Backend routes.
8. Optional run-detail summary.
9. Golden regression tests and full test pass.

## Verification Commands

Run these after each phase where applicable:

```powershell
ruff check app tests
pytest tests/test_ranking_profile_config.py -q
pytest tests/test_ranking_profile_components.py tests/test_ranking_profile_engine.py -q
pytest tests/test_ranking_profile_service.py tests/test_ranking_result_export.py -q
pytest tests/test_ranking_profile_routes.py -q
pytest -q
```

For schema work:

```powershell
alembic upgrade head
alembic downgrade 0010_add_earnings_risk_gate
alembic upgrade head
```

## Rollback Strategy

- New config, services, routes, and table are additive.
- Do not modify `combined_results` or `refresh_combined_results`.
- If a problem appears, stop calling profile refresh routes; existing cockpit exports continue to work.
- The migration can be downgraded to remove only `ranking_results`.

## Definition of Done

This work is done when one upload run can generate five enabled profile-specific rankings, persist them in `ranking_results`, refresh all or one profile idempotently, export one or all profiles to CSV, expose backend routes for listing/viewing/refreshing/exporting, pass unit and integration tests, and leave existing combined-result behavior unchanged.
