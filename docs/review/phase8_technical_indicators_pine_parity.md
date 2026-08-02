# Phase 8 Review - Technical Indicators and Pine Parity

Date: 2026-08-02
Reviewer: Codex
Scope: `app/services/technical_indicators.py`, Pine replica scoring, adaptive technical features,
breakout/contraction/stage/climax helpers, relative strength alignment, HTF resampling, technical
confidence, technical fixtures, and Pine parity documentation.

## Objective

Prove technical features are mathematically correct, temporally valid, and equivalent to intended
TradingView Pine behavior.

## Executive Summary

Phase 8 is not exit-ready.

The technical engine has a useful, modular indicator base and the focused Python test suite is green.
Most rolling-window calculations are trailing and causal. However, the current implementation does
not yet prove Pine parity against frozen TradingView output, and one score-producing pivot path
backdates future-confirmed information onto the pivot bar. Input normalization also does not flag
duplicate/gapped/malformed bars, relative-strength date alignment fails on timezone mismatches or
non-overlap, and partial HTF period handling is not explicitly surfaced to downstream confidence.

## Evidence Log

| Check | Result | Notes |
| --- | --- | --- |
| Phase 8 checklist from `C:/Users/Ivica/Downloads/software_review_plan.md` | Reviewed | Objective, outputs, and exit criteria mapped to code/tests. |
| Static scan for `center=True` / negative shifts | Reviewed | Only score-producing centered windows found are pivots in `technical_indicators.py`. No `shift(-n)` found in `app/services`. |
| Focused technical test suite | Passed | `uv run pytest tests/test_technical_indicators.py tests/test_pine_replica_engine.py tests/test_technical_confidence.py tests/test_technical_score_v4.py tests/test_technical_feature_flags.py tests/test_technical_scoring_config.py tests/test_adaptive_technical_features.py tests/test_box_breakout.py tests/test_climax_risk.py tests/test_stage_analysis.py tests/test_relative_leadership.py tests/test_volatility_contraction.py -q` -> `73 passed in 15.30s`. |
| Market/sector technical suite | Passed | `uv run pytest tests/test_market_regime.py tests/test_market_regime_command_center.py tests/test_sector_etf_rotation_service.py tests/test_chart_data_service.py -q` -> `19 passed in 3.67s`. |
| Duplicate-date probe | Reproduced issue | `prepare_ohlcv_frame` retained both duplicate `2026-01-02` rows. |
| Relative-strength timezone probe | Reproduced issue | Mixed timezone-aware/naive dates raised pandas `ValueError` during merge. |
| Relative-strength non-overlap probe | Reproduced issue | Non-overlapping date ranges raised `IndexError` through `_latest_features` on an empty frame. |
| Pivot confirmation probe | Reproduced issue | With `right=2`, `pivot_high` wrote the pivot value onto the original pivot bar, not the confirmation bar. |
| Runtime versions | Captured | Current environment: NumPy `2.5.1`, pandas `3.0.3`; `pyproject.toml` allows `numpy>=2.0` and `pandas>=2.2`. |

## Indicator Formula Catalogue

| Feature family | Implementation | Pine intent / review note |
| --- | --- | --- |
| SMA | `series.rolling(length, min_periods=length).mean()` (`technical_indicators.py:224-225`) | Matches full-window `ta.sma` behavior for non-null histories. |
| EMA | `series.ewm(span=length, adjust=False, min_periods=length).mean()` (`technical_indicators.py:220-221`) | Plausible Pine-compatible EMA, but no TradingView fixture proves seed equivalence. |
| RMA | `series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()` (`technical_indicators.py:228-229`) | Used for RSI, ATR, and ADX; no frozen Pine fixture verifies Wilder seed behavior. |
| RSI | Wilder gains/losses via `rma`; zero-loss path fills 100 (`technical_indicators.py:232-240`) | Uptrend primitive is tested; flat-series Pine behavior is not independently fixture-verified. |
| ATR | True range from high/low/previous close, smoothed with `rma` (`technical_indicators.py:243-257`) | Formula is directionally correct; seed parity still unproven against Pine. |
| DMI/ADX | Directional movement, ATR denominator, DX, RMA smoothing (`technical_indicators.py:260-281`) | No external Pine comparison for flat, one-way, and zero-ATR cases. |
| OBV | `sign(close.diff()) * volume` cumulative sum (`technical_indicators.py:285-287`) | Causal; zero-volume behavior is natural but not separately fixture-locked. |
| ROC | `(series / series.shift(length) - 1) * 100` (`technical_indicators.py:290-291`) | Causal; zero prior close yields inf/NaN risk without explicit flag. |
| Slopes | Percent distance from shifted moving average (`technical_indicators.py:294-295`) | Causal; zero denominator can produce NaN and is not explicitly flagged. |
| 52-week position | Trailing rolling high/low over configured 252 bars (`technical_indicators.py:364-377`) | Pine `ta.highest/lowest` current-bar inclusion appears consistent. |
| Breakout highs | Previous resistance uses `high.shift(1).rolling(...)` (`technical_indicators.py:430`) | Causal; prior-bar exclusion is explicit. |
| Donchian/box | High/low helpers shift by one before rolling (`box_breakout.py:71-75`) | Causal breakout reference levels. |
| Pivots | Centered rolling high/low (`technical_indicators.py:302-311`) | Finds pivots, but values are backdated to bars where Pine would only confirm after `right` bars. |
| Pullback geometry | Iterative prior window excludes current high and includes current low after prior high (`technical_indicators.py:610-632`) | Causal. |
| Volume mix | Price frame volume can be overwritten from trades frame (`technical_indicators.py:207-215`) | Good separation mechanism, but repository currently chooses TRADES as price source when present. |
| Weekly HTF | Daily OHLCV resampled with `W-FRI`; confirmed mode returns second-last weekly row (`technical_indicators.py:160-196`) | Avoids most incomplete-week use, but partial-period status is not surfaced and single-week histories still return latest. |
| Relative strength | Exact date merge of stock vs benchmark/sector close (`technical_indicators.py:92-157`) | No timezone/session normalization, non-overlap handling, or controlled insufficient-data output. |
| Adaptive percentiles | Rolling percentile of current point in trailing window (`adaptive_technical_features.py:89-108`) | Causal and tested for current-window behavior. |
| VCP/contraction | Bollinger/Keltner/tight-close trailing windows (`volatility_contraction.py`) | Causal by inspection; no Pine fixture. |
| Stage | Row classifier over precomputed causal indicators (`stage_analysis.py`) | Deterministic rule layer; no external reference fixture. |
| Climax risk | Shifted recent moves plus adaptive flags (`climax_risk.py`) | Causal by inspection; divide-by-zero handled through NaN replacement in ATR extension paths. |
| Stop/target/RR | Stop fallback and target fallback use current close/ATR/structure; RR divides by entry risk with zero replacement (`technical_indicators.py:520-559`) | Sensible fallbacks, but no approved Pine stop/target fixture. |

## Findings Register

### PH8-001 - Pivot confirmation is backdated into score-producing features

Severity: Critical

Evidence:
- `pivot_high` and `pivot_low` use centered rolling windows with `center=True`
  (`app/services/technical_indicators.py:302-311`).
- `_calculate_feature_frame` writes these pivot values directly into `pivot_high` / `pivot_low`
  and immediately derives `higher_high` / `higher_low`
  (`app/services/technical_indicators.py:383-386`).
- `_higher_last_pivot` treats a non-null pivot as known at the same row where it appears
  (`app/services/technical_indicators.py:579-592`).
- Probe result: with `right=2`, a high at bar 2 was written onto bar 2 even though bars 3 and 4
  are required to confirm it.

Impact: Any score, label, tag, or lifecycle logic that consumes `higher_high`, `higher_low`,
`pivot_high`, or `pivot_low` can receive future-confirmed information before it would have been
known in real time. This directly violates the Phase 8 exit criterion: no unresolved future-bar
usage in score-producing features.

Recommendation:
- Preserve two separate columns: `pivot_high_at_pivot_bar` for chart annotation and
  `pivot_high_confirmed_value` / `pivot_high_confirmed_at` shifted forward by `right` bars for
  scoring.
- Feed only confirmed-at-current-bar pivot state into score-producing features.
- Add a small Pine-style fixture where `right=3` proves no pivot-dependent signal changes until
  the third bar after the pivot.

### PH8-002 - Pine parity harness and frozen TradingView output fixture are missing

Severity: High

Evidence:
- Design docs require known TradingView output for selected tickers
  (`docs/sdd.md:1543-1570`) and a 90-95% tested-row parity target (`docs/vision.md:629-635`).
- `tests/fixtures` currently contains only `golden_pipeline.json` and
  `ranking_profiles_golden.json`; no multi-ticker Pine output fixture exists.
- `tests/test_technical_indicators.py` covers primitives and synthetic latest values
  (`tests/test_technical_indicators.py:32-37`, `tests/test_technical_indicators.py:80-92`),
  while `tests/test_pine_replica_engine.py` covers classification helpers and score shape, not
  exported Pine row parity.

Impact: EMA/RMA seeding, RSI/ATR/ADX warmup behavior, stop/target calculation, weekly HTF behavior,
and classification priority can drift from Pine while all current tests remain green.

Recommendation:
- Add `tests/fixtures/pine_parity_v3_2_multi_ticker.csv` or `.json` with TradingView-exported
  date-by-date expected outputs.
- Add `tests/test_pine_parity_fixture.py` that compares every approved feature with the numerical
  tolerance policy below and emits a mismatch summary grouped by ticker/date/field.
- Version the fixture with the Pine script hash and data-source metadata.

### PH8-003 - Relative-strength date alignment can crash instead of degrading to insufficient data

Severity: High

Evidence:
- `calculate_relative_strength_features` converts dates with `pd.to_datetime` then performs an
  exact merge on `date` (`app/services/technical_indicators.py:100-106`).
- It immediately calls `_latest_features(aligned)` without checking whether the merge is empty
  (`app/services/technical_indicators.py:117-120`).
- The beta-adjusted RS helper has the same exact-date merge pattern
  (`app/services/relative_leadership.py:101-110`).
- Probe result: mixed UTC-aware and naive dates raised pandas `ValueError`; non-overlapping dates
  raised `IndexError`.

Impact: Timezone/session differences between stock, benchmark, and sector data can fail a technical
run or silently remove RS coverage, exactly where the docs already expect IB/TradingView timezone
and weekly-construction differences (`docs/vision.md:864-871`).

Recommendation:
- Normalize all technical dates to a single market-session date before merges.
- Check empty aligned frames and return explicit missing flags such as
  `missing_benchmark_overlap`, `timezone_mismatch`, or `insufficient_rs_history`.
- Add fixtures for timezone-aware vs naive inputs, shifted benchmark holidays, and no-overlap date
  ranges.

### PH8-004 - Bar-quality edge cases are normalized silently, not flagged

Severity: High

Evidence:
- `_normalize_frame` keeps required columns, coerces numerics, drops missing OHLC rows, fills missing
  volume with zero, and returns the frame (`app/services/technical_indicators.py:668-675`).
- `prepare_ohlcv_frame` only sorts by date and resets the index
  (`app/services/technical_indicators.py:200-217`).
- `_missing_data` only checks row count and missing columns, not duplicates, gaps, invalid OHLC
  relationships, zero/negative prices, stale sessions, or split-like discontinuities
  (`app/services/technical_indicators.py:649-659`).
- Probe result: two duplicate `2026-01-02` bars remained as two rows after preparation.

Impact: Phase 8 explicitly calls for flat series, one-direction series, zero volume, gaps, splits,
missing rows, duplicate dates, and short histories. Today, several of those cases can alter rolling
windows and score-producing values without a clear warning or quarantine status.

Recommendation:
- Introduce a technical bar-quality validator before feature calculation.
- Either reject, deduplicate with a deterministic policy, or mark insufficient for duplicate dates,
  invalid OHLC (`high < low`, close outside range if required), zero/negative prices, and calendar
  gaps beyond allowed sessions.
- Add data-quality flags into `TechnicalFeatureResult.missing_data` and technical confidence.

### PH8-005 - Adjusted-vs-trades price selection remains a split/parity blocker

Severity: High

Evidence:
- `load_preferred_ohlcv_frames` loads adjusted and trades frames, then chooses TRADES as the price
  frame whenever TRADES exists (`app/services/price_bar_repository.py:47-51`).
- The current technical test locks in this behavior for TradingView parity
  (`tests/test_technical_indicators.py:59-75`).
- The design says price calculations should prefer `ADJUSTED_LAST` and use TRADES for volume
  (`docs/sdd.md:800-804`; `docs/vision.md:223-224`).

Impact: Corporate actions and split-adjusted histories can diverge sharply. Phase 8 asks for split
handling and Pine parity, but there is no single approved data-source policy to compare against.

Recommendation:
- Decide and document the parity target: TradingView adjusted daily bars, IB `ADJUSTED_LAST`, raw
  TRADES, or a hybrid adjusted-price/trades-volume frame.
- Add split-adjusted fixtures where adjusted and trades prices intentionally diverge.
- Store selected price/volume source in technical debug output and mismatch reports.

### PH8-006 - Partial HTF period behavior is not explicitly flagged

Severity: Medium

Evidence:
- Weekly bars are built by resampling all available daily rows with `W-FRI`
  (`app/services/technical_indicators.py:180-196`).
- Confirmed HTF mode returns the second-last weekly row whenever more than one weekly row exists
  (`app/services/technical_indicators.py:175-177`).
- If only one weekly row exists, confirmed mode returns that row even if it is incomplete. If the
  latest weekly row is actually complete, the code still omits it.
- Tests assert the second-last weekly row is returned (`tests/test_technical_indicators.py:201-206`)
  but do not distinguish incomplete current week, Friday after close, holiday-shortened week, or
  single-week history.

Impact: The implementation is conservative for normal midweek histories, but the output does not
tell downstream scoring whether the HTF row was confirmed, skipped as partial, or unavailable. The
Phase 8 exit criterion requires insufficient history and partial periods to be explicitly flagged.

Recommendation:
- Return HTF metadata: `htf_source_week`, `htf_confirmed`, `htf_latest_week_excluded`,
  `htf_insufficient_confirmed_history`.
- Add calendar-aware fixtures for Monday/Tuesday incomplete weeks, Friday completed weeks,
  holiday-shortened weeks, and one-week histories.

### PH8-007 - Supported NumPy/pandas range is broader than the deterministic validation

Severity: Medium

Evidence:
- `pyproject.toml` allows `numpy>=2.0` and `pandas>=2.2` with no upper bound beyond Python
  compatibility (`pyproject.toml:19-21`).
- `uv.lock` currently resolves NumPy `2.5.1` and pandas `3.0.3`.
- No test matrix or golden numeric run proves deterministic indicator output across supported
  pandas/NumPy versions.

Impact: Rolling, EWM, dtype, timezone, and nullable-value behavior can shift across pandas versions.
The current local lock is deterministic, but the declared supported range is not proven.

Recommendation:
- Either narrow supported pandas/NumPy bounds to the locked validated range or add a CI matrix that
  runs the Pine parity fixture on the oldest and newest supported versions.
- Record pandas/NumPy versions in parity mismatch reports.

## Pine Parity Harness and Mismatch Report

Current status: Missing.

Required harness shape:

1. Load frozen Pine export rows with columns:
   `ticker`, `date`, `pine_script_hash`, `data_vendor`, `data_adjustment`, `session_timezone`,
   `trend_score`, `momentum_score`, `setup_score`, `risk_score`, `market_score`, `rs_score`,
   `htf_score`, `dual_score`, `classification`, `suggested_stop`, `suggested_target`,
   `reward_risk`, and approved raw indicator columns.
2. Load the matching Python OHLCV fixture using the same date/session metadata.
3. Run `calculate_technical_features`, `calculate_htf_trend_features`,
   `calculate_relative_strength_features`, and `score_from_feature_result`.
4. Compare each field using the tolerance policy below.
5. Emit a mismatch table:
   `ticker`, `date`, `field`, `pine_value`, `python_value`, `abs_diff`, `rel_diff`,
   `tolerance`, `source_note`, `pass`.
6. Fail CI on critical field mismatches unless the row has an approved, documented data-source
   exception.

Current mismatch report:

| Area | Status |
| --- | --- |
| Frozen Pine fixture | Missing |
| Fixture runner | Missing |
| Mismatch report artifact | Missing |
| Classification exact-match report | Missing |
| Stop/target/RR parity report | Missing |
| Data-source exception ledger | Missing |

## Look-Ahead Risk Register

| Feature | Status | Evidence / action |
| --- | --- | --- |
| `pivot_high`, `pivot_low` | Red | Centered windows backdate confirmed pivots (`technical_indicators.py:302-311`). Shift scoring state forward by `right` bars. |
| `higher_high`, `higher_low` | Red | Consumes backdated pivot values (`technical_indicators.py:383-386`, `technical_indicators.py:579-592`). Feed only confirmed pivot state. |
| Weekly HTF | Amber | Latest weekly bar excluded in normal confirmed mode, but confirmation metadata and edge-case fixtures are absent. |
| Relative strength | Amber | Exact date merge is causal but not robust to timezone/session alignment; empty aligned frames crash. |
| Adaptive percentiles | Green | Rolling window uses current value only; test proves no future window (`tests/test_adaptive_technical_features.py:11-17`). |
| Donchian/box highs/lows | Green | Previous levels use `shift(1)` before rolling (`box_breakout.py:71-75`). |
| Pullback breakout resistance | Green | Previous resistance uses prior highs via `high.shift(1).rolling(...)` (`technical_indicators.py:430`). |
| Pullback geometry | Green | Iterative window excludes current high from prior-high selection (`technical_indicators.py:617-627`). |
| Climax recent moves | Green | Uses positive lag shifts only (`climax_risk.py`). |

## Numerical Tolerance Policy

Until the Pine fixture exists, this is the recommended policy rather than an enforced one.

| Field type | Tolerance | Notes |
| --- | --- | --- |
| Raw OHLCV fixture inputs | Exact for dates and OHLC, exact or documented delta for volume | Any adjustment/source mismatch must be tagged before indicator comparison. |
| SMA, ROC, RS line, ratios | `abs <= 1e-8` or `rel <= 1e-8` | Deterministic arithmetic should be effectively exact for identical inputs. |
| EMA, RMA, RSI, ATR, DMI/ADX | `abs <= 1e-6` after warmup | Seed behavior must be documented; compare warmup rows separately. |
| Scores | `abs <= 0.01` | Report rounded and raw values when available. |
| Stop, target, reward/risk | `abs <= 0.01` for prices, `abs <= 0.001` for RR | Tighter tolerance can be used if fixture prices are rounded consistently. |
| Booleans and classifications | Exact | Any exception requires a documented data-source or session difference. |
| Missing/insufficient flags | Exact | Missing history, partial HTF, and unavailable context must match the approved policy. |

## Golden Multi-Market Dataset Proposal

Recommended fixture set:

| Bucket | Proposed contents | Purpose |
| --- | --- | --- |
| Liquid mega-cap uptrend | MSFT or AAPL daily bars plus SPY/QQQ/sector ETF | EMA/SMA/RS/HTF baseline parity. |
| High-beta momentum | NVDA or TSLA | Extension, climax, volatility, and stop/target stress. |
| Breakdown/distribution | A ticker with known failed breakout sequence | Classification priority and risk override parity. |
| Split/corporate action | One adjusted-vs-raw divergent history | Validate chosen ADJUSTED/TRADES policy. |
| Low/zero volume edge | ETF/ADR-like fixture or synthetic approved case | OBV, volume ratio, liquidity warnings. |
| Gapped sessions | Missing weekday and holiday-shortened week | Calendar/session alignment and HTF partial flags. |
| Duplicate date fixture | Two conflicting bars for one date | Deterministic rejection/deduplication policy. |
| Short history | 20, 40, 126, and 251 rows | Insufficient-history flags and warmup behavior. |
| Timezone alignment | UTC-aware stock vs naive benchmark, plus normalized market-session date | Relative-strength merge safety. |

Minimum stored metadata:

- Pine script version and hash.
- TradingView symbol/exchange and timezone.
- Data vendor/source and adjustment mode.
- Export timestamp and date range.
- Python package versions used for the comparison.
- Approved tolerance profile version.

## Phase Scorecard

| Area | Status | Rationale |
| --- | --- | --- |
| Indicator primitives | Amber | Good local primitives and tests; Pine seed parity unproven. |
| Temporal validity | Red | Pivot confirmation is backdated into score-producing features. |
| Weekly confirmed HTF | Amber | Normal latest-week exclusion exists, but partial-period metadata and edge fixtures are missing. |
| Relative strength alignment | Red | Timezone mismatch and non-overlap crash instead of controlled insufficiency. |
| Edge-case data quality | Red | Duplicate/gapped/malformed bars are not explicitly flagged. |
| Stop/target/RR safety | Amber | Zero-risk denominator guarded; parity and abnormal-price fixtures missing. |
| Pine parity evidence | Red | No frozen multi-ticker TradingView output fixture or mismatch report. |
| Determinism across supported versions | Amber/Red | Lock is deterministic locally; declared version range is not matrix-validated. |

## Exit Criteria Assessment

| Exit criterion | Result | Evidence |
| --- | --- | --- |
| No unresolved future-bar usage exists in score-producing features | Fail | Pivot/higher-pivot features backdate future-confirmed values. |
| Critical indicators match approved references within documented tolerances | Fail | No approved Pine/TradingView fixture or harness exists. |
| Insufficient history and partial periods are explicitly flagged | Partial/Fail | Short history is flagged; HTF partial status, duplicate dates, gaps, and RS alignment failures are not. |

## Recommended Next Work

1. Fix pivot confirmation semantics before using pivot-derived features in scoring.
2. Add the Pine parity fixture and mismatch harness as a CI gate.
3. Add technical bar-quality validation and missing-data flags for duplicate dates, gaps, malformed
   OHLC, split discontinuities, zero volume, and short histories.
4. Normalize relative-strength dates to market-session dates and return controlled insufficient-data
   outputs for timezone/no-overlap cases.
5. Make HTF confirmation metadata explicit and cover incomplete/completed week fixtures.
6. Decide the ADJUSTED/TRADES price-source policy and lock it with split fixtures.
7. Either constrain pandas/NumPy versions or validate the parity fixture across the supported range.
