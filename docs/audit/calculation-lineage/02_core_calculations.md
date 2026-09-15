# SwingLens Core Market Calculations — Calculation Lineage Audit

Task: 02 — Core Market Calculations
Verification state: repository/static analysis plus isolated tests; no provider calls or database writes
Evidence vocabulary: `VERIFIED`, `PARTIALLY_VERIFIED`, `INFERRED`, `UNKNOWN`, `CONTRADICTORY`, `LEGACY`, `DEAD_CODE`

## Scope and method

This report covers input validation, fundamental scoring, IB historical market-data acquisition, OHLCV storage and revision handling, important technical indicators and composites, higher-timeframe construction, market-regime snapshots, and sector-rotation snapshots. CERI, ranking, setup lifecycle, and Winner are mentioned only as downstream or upstream dependencies. The audit did not run a pipeline, contact Interactive Brokers or another provider, apply migrations, enqueue work, or mutate application data.

Substantive statements carry an evidence classification. File and line references describe the audited commit and may move in later revisions.

## Repository identity and implementation baseline

| Item | Value | Status |
|---|---|---|
| Repository path | `C:\Users\Ivica\Documents\SwingLens` | VERIFIED |
| Current branch | `codex/winner-evidence-remediation` | VERIFIED |
| Current repository HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Implementation baseline SHA | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Application implementation drifted from baseline? | **No.** `HEAD` equals the baseline; both the commit range and tracked file diff are empty. | VERIFIED |
| Exact implementation-changing commits since baseline | None | VERIFIED |
| Exact implementation-changing files since baseline | None | VERIFIED |
| `BASELINE_DRIFT` | **Not flagged.** No material calculation/orchestration implementation change exists after the baseline. | VERIFIED |
| Worktree at audit start | Untracked `docs/audit/` (Task 01 artifact), `scripts/ops/ib_historical_probe.py`, and `tests/ops/test_ib_historical_probe.py`; no tracked changes were reported. The two `scripts/ops`/`tests/ops` files pre-existed this audit series and were not inspected as authoritative committed implementation. | VERIFIED |
| Change made by Task 02 | This file only: `docs/audit/calculation-lineage/02_core_calculations.md` | VERIFIED |
| Relevant repository migration head | `0074_ceri_evidence_quarantine` | VERIFIED |
| Live database migration revision | Not queried: the configured database safety context was absent, so a live connection was not opened. | UNKNOWN |
| Python runtime | CPython 3.12.2 (`.venv` used for tests) | VERIFIED |
| Verification time | 2026-09-14T01:00:21+02:00 (Europe/Zurich) | VERIFIED |

Documentation-only work under `docs/audit/calculation-lineage/` is excluded from implementation drift by the user-supplied baseline rule. Because the current SHA is identical to the baseline, the conclusions below apply directly to the established Task 01 implementation.

## Executive conclusions

1. **Validation is layered but not a single eligibility contract.** Upload validation proves that a CSV is bounded, parseable, contains at least one non-empty ticker, and has no duplicate normalized tickers. It does not validate ticker syntax, exchange, security type, fundamental freshness, or provider identity. Later IB contract, coverage, and capability checks affect whether market data is fetched, but insufficient history normally degrades a ticker rather than removing it. **VERIFIED**
2. **Fundamentals are run-bound raw snapshots with mutable derived scores.** The uploaded row JSON is retained for a run; the v2 score can later be deleted and recreated from that raw JSON using the then-current aliases and configuration. Provider, observation time, filing period, and currency conversion lineage are not persisted as calculation inputs. **VERIFIED**
3. **Daily price inputs have the strongest point-in-time machinery in this scope.** IB `ADJUSTED_LAST` price and `TRADES` volume are revision-audited. Bounded readers use completed-session and observation-time cutoffs and can reconstruct a pre-revision view when the revision ledger is complete. **VERIFIED**
4. **Technical calculations exclude future daily bars and unconfirmed weekly buckets.** Daily-to-weekly aggregation is explicit, the current incomplete week is removed, and centered pivots are shifted by the right-confirmation count. No monthly technical aggregation is implemented. **VERIFIED**
5. **Market-regime safeguards contain two material correctness defects.** Sparse-but-nonempty inputs can be classified as a bullish tradable regime, and severe staleness changes the displayed risk color to Gray without changing bullish permissions or sizing. **VERIFIED; findings CORE-001 and CORE-002**
6. **Sector rotation is universe leadership by default, not ETF price rotation.** ETF/proxy scoring exists but is disabled. In normal pipeline order the sector snapshot consumes ranking outputs and therefore cannot feed the same run's already-completed ranking stage; it is contextual for later consumers. **VERIFIED**

## Intended contract versus current implementation

| Subsystem | Intended contract | Current implementation | Assessment |
|---|---|---|---|
| Upload validation | Admit a well-formed, identifiable analysis universe. | Bounds/CSV parsing, non-empty ticker presence, and duplicate detection; semantic instrument validity is deferred to IB contract resolution. | PARTIALLY_VERIFIED |
| Fundamentals | Score comparable company fundamentals with known periods, provenance, and freshness. | Header aliases encode TTM/FY/FQ-like semantics, but uploaded values have no provider timestamp, filing identity, effective session, or enforced currency normalization. | CONTRACT GAP — VERIFIED |
| Market data | Use complete, completed-session OHLCV and retain revision lineage. | Completed-session/PIT bounds and revision rows are strong; coverage uses count/min/max and does not establish internal session completeness. | PARTIAL CONTRACT VIOLATION — VERIFIED |
| Technicals | Deterministic score from data available at the calculation cutoff. | Cutoff-aware preferred frames, confirmed weekly buckets, config hashes/debug, and run binding exist. Missing numeric features are frequently converted to zero before scoring; confidence warnings are the main guard. | PARTIALLY_VERIFIED |
| Market regime | Market permissions should become conservative when inputs are missing, insufficient, or severely stale. | Empty SPY fails closed, but sparse SPY can appear bullish; severe stale data changes risk state only, retaining the original regime policy. | CONTRACT VIOLATION — VERIFIED |
| Sector rotation | Compare sector leadership under a common session and preserve calculation identity. | Full config hash, cutoff, run binding, and revisions are persisted. Default mode measures same-run universe composition; standalone fallbacks may select cross-run market/prior snapshots. | PARTIALLY_VERIFIED |

## Calculation topology and input classes

```text
uploaded CSV
  -> mapped/raw run rows
  -> v2 fundamental components and score

IB contract + historical requests
  -> current PriceBar projection + PriceBarRevision ledger
  -> point-in-time preferred daily OHLCV
       -> daily features
       -> confirmed weekly features
       -> v4 active technical score (+ v5 shadow fields by default)
       -> SPY/QQQ market-regime snapshot
       -> optional sector-ETF rotation inputs

run raw/fundamental/technical/combined/ranking rows
  -> sector universe metrics
  -> default universe-only sector rotation snapshot
```

Important input classes:

- **RAW:** uploaded row JSON; IB daily OHLCV; sector text. **VERIFIED**
- **DERIVED:** canonical fundamental fields, component scores, technical features, market index health, sector aggregates. **VERIFIED**
- **CONFIGURATION:** column aliases, fundamentals v2, Pine defaults, technical v4/v5, market-regime command center, sector rotation, runtime flags. **VERIFIED**
- **TEMPORAL:** market calculation cutoff, latest completed NYSE session, per-series latest session, snapshot `as_of_date`, revision observation time. **VERIFIED**
- **EXECUTION_CONTEXT:** `run_id` and `market_calculation_context_id`; IB fetch-run/item identity on price revisions. **VERIFIED**
- **HISTORICAL_STATE:** earlier OHLCV revisions and prior sector snapshot/rows. **VERIFIED**
- **EXTERNAL_DATA:** user-supplied fundamental export and Interactive Brokers historical data. The identity of the fundamental export provider is not persisted. **PARTIALLY_VERIFIED**

## Validation

### What validation means in SwingLens

There is no unified validation result consumed by all calculations. Validation is a sequence of checks at different boundaries:

| Boundary | Check | Failure/degradation behavior | Affects downstream eligibility? | Evidence |
|---|---|---|---|---|
| File upload | `.csv`, byte-size, row-count, and column-count bounds | Upload fails; a failed run may be recorded | Yes, run cannot proceed normally | `app/services/upload_service.py:42-103`; CSV loader tests — VERIFIED |
| Row mapping | Case-insensitive aliases; strip/uppercase ticker; copy non-empty mapped values | Unmapped fields stay absent | Indirectly, through missing inputs | `app/services/column_mapper.py:18-90`; `config/column_aliases.yaml` — VERIFIED |
| Universe validation | At least one row and one non-empty ticker; exact normalized ticker must be unique | Validation error | Yes | `app/services/validation_service.py:7-24` — VERIFIED |
| Ticker eligibility | No syntax, exchange, asset-class, or listing-status check during upload | Arbitrary non-empty ticker can enter raw/fundamental stages | No at upload; later market-data resolution may fail | VERIFIED |
| Pipeline validation step | Confirms run has ticker rows; conditionally performs SEC preflight for CERI provider mode | Step/pipeline may fail | Yes at pipeline level | `app/services/pipeline_executor.py:309-323` — VERIFIED |
| Provider readiness | IB health/readiness when IB is required; historical capability when planned requests exist | Required mode fails; fallback mode may reuse cache and degrade | Pipeline- and configuration-dependent | `app/services/pipeline_executor.py:332-466` — VERIFIED |
| Contract resolution | Persisted IB contract must resolve unambiguously before request | Fetch item fails or requires resolution | Affects fetch, not fundamental eligibility | `app/services/ib_fetch_plan_service.py:349-430` — VERIFIED |
| Coverage sufficiency | At least 252 daily price rows by default, TRADES volume present, latest bar current | Fetch full history/top-up; limited listings can be accepted after completed backfill | Usually degrades technical confidence; ticker is still scored/persisted | `app/services/ohlcv_coverage_service.py:80-202`; `app/services/ib_fetch_plan_service.py:349-430` — VERIFIED |
| Daily frame integrity | Required date/OHLCV schema; numeric coercion; drop missing date/OHLC; volume null -> 0; reject duplicate dates, negative volume, and invalid OHLC ranges | Feature result is error/insufficient | Numeric score can still be produced with low/error confidence in some paths | `app/services/technical_indicators.py:347-366, 830-887` — VERIFIED |
| Session checks | Request end and consumed bars are bounded to latest completed US trading session and calculation observation cutoff | Out-of-scope returned bars fail execution; future consumed bars raise | Yes for bounded core readers | `app/services/ib_historical_request_scope.py`; `app/services/ib_fetch_executor.py:1127-1163`; `app/services/market_regime_command_center.py:284-312` — VERIFIED |
| Internal missing sessions | Coverage checks only count/min/max; feature quality notes calendar gaps greater than five calendar days | No completeness failure | No; a gapped series can be considered ready | VERIFIED; finding CORE-004 |

The effective ticker eligibility rule is therefore: a non-empty unique uploaded ticker may receive a fundamental score immediately; an unresolvable or data-poor ticker can remain in the run and usually receives missing/low-confidence technical outputs rather than being removed. **VERIFIED**

## Fundamentals

### Source, selection, normalization, and temporal behavior

| Concern | Current implementation | Status |
|---|---|---|
| Raw provider | A user-uploaded CSV export. TradingView-style headings are strongly suggested by aliases, but provider identity is neither required nor stored as structured provenance. | PARTIALLY_VERIFIED |
| Source tables | `raw_company_rows.raw_json` is the retained raw input; `fundamental_scores` stores derived results. | VERIFIED |
| Selection | One mapped score result per stored row with a non-empty ticker; upload validation rejects duplicate normalized tickers. | VERIFIED |
| Period semantics | TTM, annual/FY, quarterly/FQ, NTM, and estimate meanings come only from mapped column names. There is no period-end, filing, accession, fiscal-calendar, restatement, or observation-time selection. | VERIFIED |
| Currency | Parser removes `$`, `€`, `£`, `¥` and common three-letter currency codes. No FX conversion, currency compatibility check, or retained per-value currency exists. | VERIFIED |
| Numeric normalization | Percent signs are removed; commas/spaces stripped; parentheses become negative; K/M/B/T suffixes scale values; configured missing tokens become null. | VERIFIED |
| Missing values | Coverage penalties are based on priority. Within a component, present subfeatures are reweighted; a component with no usable subfeature returns neutral `5.0`. | VERIFIED |
| Outliers | Component helpers linearly interpolate configured poor/good/excellent bands and clamp components/overall result to `[0,10]`; there is no statistical winsorization. | VERIFIED |
| Freshness | No provider `as_of`, retrieval timestamp, filing date, or effective market session is attached to input fields. | VERIFIED |
| Run behavior | Raw JSON is frozen to a run unless its row is externally mutated. Derived scores are replaceable: pipeline recalculation deletes the run's prior fundamental scores and recreates them from raw JSON with current code/config/aliases. | VERIFIED |
| Cross-run caching | No shared fundamental calculation cache was found. Each upload/run creates its own scores. | VERIFIED |

### V2 score formula

`fundamental_score = clamp_0_10(sum(component_score × component_weight) - missing_data_penalty)`.

The model is `fundamentals_v2.1`. The ten component weights sum to 1.0:

| Component | Weight | Important formula/inputs | Missing/outlier behavior | Status |
|---|---:|---|---|---|
| Growth quality | 0.13 | Revenue, EPS, EBITDA, FCF, gross-profit and net-income growth; positive revenue scale; penalties for growth unsupported by income/cash flow | Present-feature weighted average; bounded 0–10 | VERIFIED |
| Profitability quality | 0.13 | Gross/EBITDA/operating/net margins, ROE, ROA, ROIC, ROCE, return on total capital; TTM-vs-annual trend bonus | Same | VERIFIED |
| FCF quality | 0.12 | Positive FCF, FCF margin/growth, P/FCF, EV/FCF, OCF/share and capex burden; high-capex/nonpositive-FCF-growth penalty | Same | VERIFIED |
| Earnings quality | 0.12 | Sloan ratio 45%, cash conversion 35%, OCF/share 20% | Same; warnings at configured Sloan bands | VERIFIED |
| Capital efficiency | 0.12 | ROIC, return on total capital, ROA, asset turnover and TTM-vs-annual trends; high asset-growth deterioration penalty | Same | VERIFIED |
| Balance-sheet quality | 0.12 | Liabilities/assets, working capital/share, liquidity, cash, leverage and interest coverage | Same | VERIFIED |
| Valuation quality | 0.10 | P/E, forward P/E, earnings yield, P/S, EV/revenue, EV/EBITDA, P/FCF, EV/FCF and PEG | Same | VERIFIED |
| Forward quality | 0.08 | Positive EPS/revenue/net-income/EBIT/cash/capex estimates and analyst-rating contribution | Same | VERIFIED |
| Shareholder quality | 0.05 | Buyback/dividend yield, payout ratio and share count | Same | VERIFIED |
| Liquidity/risk quality | 0.03 | Dollar volume, float, relative/current volume-price behavior, beta and ATR% | Same; nominal dollar thresholds are currency-blind | VERIFIED |

Missing-field penalties are `0.35/0.20/0.10/0.05` for critical/high/medium/low fields, capped at `2.50`; coverage below `0.65` produces sparse-data warnings. Labels are ordered: balance stress or poor cash conversion -> `Value trap risk`; earnings/forward warning -> quality-risk label; otherwise `Clean compounder` requires total >= 7.6, profitability >= 7, FCF >= 6.5, and earnings quality >= 6.5; `High-quality quant` >= 6.7; `Mixed but interesting` >= 5; otherwise `Low priority`. **VERIFIED**

### Fundamental fingerprint and mutability

The scorer canonicalizes the parsed fundamentals config as sorted, compact JSON and computes SHA-256. The resulting hash, model version, component values, coverage details, parse diagnostics, and present canonical fields are stored inside `fundamental_scores.debug_json`; there is no dedicated immutable config-hash column or input-value hash. The hash binds rules but not the repository commit, uploaded file bytes, provider, filing identity, effective session, or currency. **VERIFIED**

Allowed writer paths are initial upload and `recalculate_run_fundamentals`; the latter deletes and replaces all scores for the run. Consequently, raw inputs are run-scoped, but a historical run's derived score is not frozen against later configuration/code changes. **VERIFIED**

## Market data fetching and OHLCV lineage

### Acquisition contract

| Property | Implementation | Status |
|---|---|---|
| Provider/API | Interactive Brokers through `reqHistoricalData` | VERIFIED |
| Data bases | `ADJUSTED_LAST` and `TRADES` | VERIFIED |
| Interval | `1 day` | VERIFIED |
| Regular-hours flag | `useRTH=true` by default | VERIFIED |
| Full history | `3 Y` default | VERIFIED |
| Incremental top-up | Missing sessions plus a five-session revision window; nominal fallback `10 D` | VERIFIED |
| Forced recent refresh | `60 D` default | VERIFIED |
| Benchmarks | SPY and QQQ are included by default | VERIFIED |
| Request termination | `keepUpToDate=false`; `formatDate=1` | VERIFIED |
| Daily end semantics | Explicit latest-completed-session end for TRADES; blank/current IB end for ADJUSTED_LAST, guarded by reviewed-session expiry | VERIFIED |
| Returned-scope validation | Rejects bars before reviewed start or after reviewed end | VERIFIED |
| Completeness validation | No expected-session-versus-returned-session reconciliation | VERIFIED |

The fetch planner selects full backfill if there are no bars or fewer than the required count and no successful full backfill is recorded. It selects top-up when the latest bar is stale, a configured refresh when forced, otherwise skip. A limited-history listing may be accepted after a successful complete backfill even below 252 rows. **VERIFIED**

### Storage, deduplication, and revisions

`price_bars` is the mutable current projection, unique on `(ticker, bar_date, timeframe, what_to_show)`. An MD5 data hash covers OHLCV plus source/basis/adjustment fields. Equal data updates `last_seen_at`; changed data creates a `price_bar_revisions` row containing the previous and new hashes and values, observation time, and optional IB fetch-run/item IDs, then mutates the current row and increments its revision count. **VERIFIED**

MD5 here is used for change detection, not an adversarial authenticity guarantee. Separate evidence services create SHA-256 canonical hashes for immutable price evidence consumed by later subsystems. **VERIFIED**

The authoritative core historical write route is `ib_fetch_executor -> cache_bars`; `ensure_daily_bars` is an older direct fetch/cache entry point using the same writer. Pipeline fetches, durable IB fetch jobs, and prewarm execution all converge on the planner/executor/cache path. A repository-wide search found no other application construction/update/delete of `PriceBar`; other subsystems can trigger these mechanisms, but direct SQL mutation is not an intended writer. **VERIFIED**

### Adjusted/unadjusted selection

Preferred frames use adjusted prices only when the ADJUSTED_LAST frame covers every date in the TRADES frame; otherwise price falls back to TRADES. Volume always comes from TRADES. This avoids mixing an incomplete adjusted-price date set with a fuller volume calendar. It does not prove that either set contains every expected exchange session. **VERIFIED**

### Point-in-time reconstruction

Bounded reads require `bar_date <= latest completed session` and `first_seen_at/created_at <= calculation cutoff`. For a row revised after the cutoff, the repository walks its revision ledger and reconstructs the state visible at the cutoff; when required revision provenance is incomplete, it excludes the row rather than using an unprovable current value. Revised historical bars can therefore change newly calculated results, while a replay with the original observation cutoff can recover the prior bar state if its ledger is complete. **VERIFIED**

Price-series versions and technical feature-artifact caching exist, but defaults are `technical_series_version_maintenance_enabled=false` and cache mode `OFF`. Thus current default runtime recalculates rather than actively reusing those artifacts. **VERIFIED**

## Technical indicators and composite scores

### Important indicator register

All rows below use point-in-time preferred daily frames unless stated otherwise. Pandas is used for rolling/EWM/resampling; the formulas are application-owned rather than delegated to TA-Lib. **VERIFIED**

| Indicator/group | Timeframe and lookback | Formula/parameters | Warmup/NaN behavior | Score/output role | Status |
|---|---|---|---|---|---|
| EMA/SMA trend stack | Daily; EMA 10/20, SMA 50/150/200 | Pandas EWM `adjust=False`, rolling mean | Minimum periods produce NaN until warm | Trend flags, local trend, extension; `TechnicalScore` | VERIFIED |
| Slopes | Daily; SMA50 over 10, SMA200 over 20 | Percent and ATR-normalized change over lookback | Null until source/lookback exists | Local trend | VERIFIED |
| ADX/DMI | Daily; directional length 14, smoothing 14 | True range, directional movement, Wilder-style EWM/RMA; trend threshold 18 | Early NaN | Local trend | VERIFIED |
| RSI | Daily; 14 | Wilder-smoothed gains/losses | Early NaN | Momentum, setup and risk thresholds (75/80 danger bands) | VERIFIED |
| ATR/volatility | Daily; ATR 14; percentiles 126/252 | True range; Wilder ATR; ATR% and rolling empirical percentile | Long percentiles require complete windows | Stops, risk, contraction/expansion | VERIFIED |
| Volume/OBV/distribution | Daily; volume mean 20, green/red 10, recent red 5, distribution 10, OBV SMA 20/slope 10 | Rolling means/sums; cumulative signed volume; red/down-volume conditions | TRADES volume null is normalized to 0 at frame validation | Momentum, setup, risk | VERIFIED |
| Price structure/pivots | Daily; structure 20; pivot left/right 3/3; 52-week 252 | Rolling highs/lows; centered pivot confirmation shifted right by 3 bars | Unconfirmed pivot unavailable until right bars exist | Support/resistance, trend, stop | VERIFIED |
| Pullback/breakout | Daily; pullback 20, breakout 40; depth 3–18%; MA touch 4%; volume ratio 1.2 | Application boolean/continuous rules | Missing prerequisites become false/zero-derived in score layer | Setup/classification | VERIFIED |
| Relative strength | Daily; benchmark SPY, sector default QQQ; ROC 21/63/126; RS SMA 50 | Stock/benchmark ratio, ROCs and breadth of relative outperformance; broad/sector score mix 70/30 | Missing benchmark/sector lowers readiness/confidence | Momentum, RS score/gate | VERIFIED |
| Donchian/Darvas/VCP | Daily; channels 20/55; box 20/min age 7; BB/KC 20 | Breakout and tight-range/contraction application rules | 126/252 adaptive histories may remain unavailable | v4 setup tags and scores | VERIFIED |
| Stage/climax | Daily + weekly context; distribution >=3; climax >=7 | MA/structure stage rules and vertical move, RSI, volume/range/extension risk | Missing histories lower confidence | v4 classification/caps | VERIFIED |
| Stops/targets | Daily; ATR 14 and structure | Structure + 1.5 ATR stop; 0.5/0.3 ATR buffers; 1.5 ATR target; minimum R/R 2 | Null/invalid geometry guarded in readiness | Suggested stop/target/RR | VERIFIED |
| Higher-timeframe trend | Confirmed weekly; EMA10, SMA30/40, slope 4, ROC13 | See dedicated section | Current incomplete week excluded | HTF score/gate and trend blend | VERIFIED |

The global daily-history requirement is the maximum of SMA200, 52-week high/low 252, and long ROC126: **252 rows**. A short series sets `insufficient_history` and low/error confidence but does not consistently suppress all numeric component outputs. **VERIFIED**

### Active v4 and shadow v5 composites

The Pine-replica v3 base produces additive 0–10 component scores for local trend, momentum, setup, risk (higher is worse), market, relative strength, and HTF. In Balanced mode its legacy dual score is:

`0.25 trend + 0.25 momentum + 0.20 setup + 0.10 (10-risk) + 0.08 market + 0.08 RS + 0.04 HTF`.

V4 builds adaptive, volatility-contraction, channel/box, stage, leadership and climax features, then uses regime-dependent weights. Bull weights are trend/momentum/setup/leadership/risk-control/market/execution = `0.22/0.20/0.20/0.14/0.10/0.08/0.06`; choppy = `0.18/0.10/0.24/0.10/0.22/0.10/0.06`; risk-off = `0.10/0.05/0.10/0.05/0.40/0.25/0.05`. **VERIFIED**

V5 is disabled as the active result by default, but shadow comparison and shadow-field persistence are enabled. Technical strength is `0.52 trend + 0.30 momentum + 0.18 leadership`; trend is 75% local/25% HTF, momentum 85% base/15% acceleration. The final v5 composite uses strength/setup/entry weights `0.45/0.35/0.20` in bull, `0.35/0.35/0.30` in choppy, and `0.25/0.25/0.50` in risk-off, then applies confidence factors high/normal/low/error = `1.00/0.85/0.50/0.00`. **VERIFIED**

`technical_scores` is unique by `(run_id,ticker)`. Recalculation deletes matching run/ticker rows and inserts rebuilt values. It persists calculation context, cutoff timestamp, input session, calendar version, engine version, confidence, missing-data diagnostics, and v4/v5 debug. **VERIFIED**

### Technical fingerprints

- Feature/scoring config hashes use canonical sorted JSON and SHA-256. **VERIFIED**
- The optional local artifact key includes ticker, timeframe, ADJUSTED_LAST and TRADES series versions, feature config hash, cutoff session, engine version, and artifact schema version. It intentionally separates local-feature identity from run-relative scoring identity. **VERIFIED**
- V5 input signature covers base/derived features, contraction/box/stage/climax state, regime, readiness, adaptive/leadership/sector resolution, and v5 config hash. **VERIFIED**
- With series-version maintenance/cache disabled by default, these artifact fingerprints do not normally control active production reads. **VERIFIED**
- A technical row has rich debug/config identity but no dedicated immutable input-manifest row binding every consumed price revision. Exact bar reconstruction depends on the market calculation context and price revision ledger. **PARTIALLY_VERIFIED**

## Higher-timeframe calculations

Daily bars are resampled with pandas `W-FRI`: weekly open=first, high=max, low=min, close=last, volume=sum. Confirmation does not merely require a Friday label. The implementation obtains the final US trading session for the week (handling market holidays) and retains a weekly bucket only if that final session is at or before the calculation's latest completed session and is actually present in the daily input. The in-progress current week is therefore excluded. **VERIFIED**

Weekly features use EMA10, SMA30, SMA40, four-week slopes, ROC13, and a 0–10 additive HTF score; the gate threshold is 5.5. Confirmed weekly context may be blended into daily trend. **VERIFIED**

No daily-to-monthly production aggregation or monthly indicator consumer was found. Monthly HTF semantics are therefore **not implemented**, not unknown. **VERIFIED**

Centered daily pivot detection uses three left and three right bars, then shifts the identified pivot by the three right-confirmation bars. The value becomes available only after confirmation; no unshifted future-bar pivot was found in the scoring path. **VERIFIED**

## Market regime snapshot

### Inputs and formula

The primary benchmark is SPY and the optional risk proxy is QQQ. Both are read from point-in-time bounded daily OHLCV and passed through the same technical feature engine. Each benchmark market score awards 2.5 for above SMA200, 2.0 for above SMA50, 1.5 for SMA50>SMA200, 1.5 for positive SMA50 slope, 1.0 each for positive ROC21/ROC63, and subtracts 2.0 for at least four distribution days. With QQQ, final score is 65% SPY / 35% QQQ. **VERIFIED**

Classification precedence is crash risk, distribution, correction, bear rally, risk-on breakout/bull trend, bull pullback, then choppy. Missing SPY returns Unknown/risk-off/gate-false/low-confidence. Missing QQQ reuses SPY as the risk proxy and marks low confidence. **VERIFIED**

Universe breadth (`MarketParticipation`) and sector leadership are persisted into the snapshot for context, but neither changes the classifier or numeric regime score. Optional IWM, TLT, and VIXY symbols appear in configuration but are not classifier inputs; no macro input is used. **VERIFIED** for behavior; unused optional-symbol configuration is `DEAD_CODE` relative to this calculation.

### Ownership, freshness, revisions, and fingerprint

Pipeline invocation creates a run-owned snapshot with the shared market calculation context. Standalone invocation creates a standalone context. Source frames are bounded by the context cutoff and latest completed session. Freshness is the trading-session distance to that session: stale after more than 3 sessions and severely stale after more than 6 by default. **VERIFIED**

The snapshot `as_of_date` is the maximum of the source symbols' latest dates, not their common/minimum date. This can label a mixed-session SPY/QQQ calculation with the newer date when one source lags within the tolerance. **VERIFIED; finding CORE-003**

`market_regime_snapshots` stores `run_id`, context/cutoff/session/calendar, calculation and config version strings, output/policy payloads, debug source sessions, an evidence hash, revision number, current-revision flag, and supersession link. Rebuilding the same logical snapshot with changed evidence creates a revision rather than updating the historical row in place. It is recalculated, not blindly reused. **VERIFIED**

The evidence hash canonically covers the snapshot write payload, so changed inputs/outputs generally produce changed evidence. However, the full config payload/hash is not a dedicated field; correctness depends on manually updating `config_version`, and identical output under changed config is not distinguishable. **PARTIALLY_VERIFIED; finding CORE-008**

## Sector rotation

### Universe and default mode

Canonical sector mapping and TradingView aliases define the sector universe. In the normal run-bound path, same-run raw rows, fundamental scores, technical scores, combined results, and ranking results are aggregated by sector. The default ranking profile is selected from sector configuration. **VERIFIED**

The default `etf_score.enabled=false`, so the operative mode is `universe_only`. Its five components are average technical score 25%, average profile score 20%, normalized top-25 candidate share 20%, buyable/watch setup density 20%, and risk control 15%. Risk control is `10 - clamp(danger_density×7 + danger_warning_density×3)`. A sector has low confidence below 5 members or below 50% technical availability, and high confidence at least 10 members and at least 80% availability. **VERIFIED**

When ETF mode is explicitly enabled, sector proxies (XLK, XLF, XLE, XLI, XLV, XLY, XLP, XLU, XLRE, XLB, XLC) are compared with SPY using price trend, relative strength, momentum, Donchian breakouts, and risk control. Combined mode uses 55% universe / 45% ETF. Missing ETF policy defaults to universe-only. **VERIFIED**

### Classification and temporal behavior

Rotation state precedence includes risk-off at danger share >=30%, crowded at score >=7.5 and top-25 share >=35%, improving/fading at score change >=+0.75/<=-0.75, leading >=7.5, lagging <=4.5, otherwise neutral. Sector permission and size multiplier also depend on the selected market-regime bucket. **VERIFIED**

Snapshot time is the supplied date or the shared context's latest completed session; a requested future date is rejected. ETF frames are cutoff-aware. `sector_rotation_snapshots` persists the run, linked market snapshot, context/cutoff/session/calendar, version, full config hash, mode, evidence hash, revisions and child rows. **VERIFIED**

The prior-snapshot lookup requires an earlier date, matching mode and config hash; it filters by run only when `run_id` is non-null. Therefore pipeline snapshots compare only inside the same run—usually leaving no historical delta for a newly created run—while a standalone `run_id=None` calculation can pick a run-owned prior snapshot. **VERIFIED; finding CORE-007**

Market context selection prefers a run-owned market snapshot at/before the date, then silently falls back to a global snapshot. This is a useful standalone fallback but a cross-run contamination boundary if the expected run snapshot is absent. The warning is emitted only when no snapshot exists, not when the global fallback is used. **VERIFIED; finding CORE-006**

Pipeline order places ranking before sector rotation. Sector rotation consumes ranking and cannot affect that same run's already-created ranking outputs. It supplies contextual snapshots to later/UI/Winner consumers rather than ranking input in the audited pipeline. **VERIFIED**

## Temporal integrity matrix

| Calculation | Governing as-of/cutoff | Latest permitted bar/evidence | HTF rule | Revision/replay behavior | Lookahead assessment | Status |
|---|---|---|---|---|---|---|
| Upload validation | Upload transaction/time | Submitted file only | N/A | Re-run on a different file is a new upload | No market lookahead; no fundamental observation time | PARTIALLY_VERIFIED |
| Fundamental v2 | Run raw row; no market cutoff | All fields present in uploaded row, regardless of their provider age | N/A | Recomputed in place from frozen raw JSON with current rules | Provider-time lookahead cannot be assessed | CONTRACT GAP |
| IB fetch planning | Latest completed NYSE session at planning | Reviewed request end | N/A | Execution replans/validates scope; five-session revision window | Endpoint-bounded; completeness gap remains | VERIFIED |
| Price-bar PIT read | `calculation_cutoff_at` + `latest_completed_session` | Bar session <= session and first observed <= timestamp | N/A | Revision ledger reconstructs earlier visible state | Strong guard when revision provenance complete | VERIFIED |
| Technical score | Shared market calculation context | PIT preferred daily frame at/before completed session | Last confirmed complete weekly bucket only | Run/ticker row replaced on recompute; price view reproducible through cutoff/revisions | No future/partial-week use found | VERIFIED |
| Market regime | Shared context; source-specific latest sessions retained | Same PIT daily rules | Daily features only | Revisioned snapshots | Mixed-source session and sparse-data risks | PARTIALLY_VERIFIED |
| Sector rotation | Shared context/latest completed session | Run outputs plus bounded ETF frames and market snapshot <= date | ETF daily features | Revisioned snapshot; prior state used for deltas | Pipeline run binding strong; fallback paths are cross-run risks | PARTIALLY_VERIFIED |

Weekend, holiday, premarket, regular-hours, and postmarket decisions are centralized in the US-market calendar and market calculation context. A daily bar becomes eligible only after the configured daily-bar ready delay following the exchange session close. Holidays and early closes come from the NYSE schedule. **VERIFIED**

## Contamination boundaries and invariants

### Fundamental calculation

- **Allowed influences/writers:** the run's retained raw rows, current alias/config/code; upload and explicit run recalculation. **VERIFIED**
- **Dangerous influences:** reinterpreting old raw headings with changed aliases/config; incomparable currencies; unknown provider/fiscal observation times. **VERIFIED**
- **Invariant:** one score per run/ticker is database-enforced. **VERIFIED**
- **Invariant:** a historical run always retains its original derived score is **VIOLATED** by delete/recreate recalculation semantics.

### Price and technical calculation

- **Allowed influences/writers:** resolved IB historical data through the bar cache; observation-time revisions; run calculation context; Pine/v4/v5 configs. **VERIFIED**
- **Dangerous influences:** unrecorded/manual database mutation, incomplete revision provenance, internal missing sessions, current runtime with artifact series-versioning disabled. **PARTIALLY_VERIFIED**
- **Invariant:** no consumed daily bar may exceed the completed-session or observation-time cutoff is enforced in bounded readers and tests. **VERIFIED**
- **Invariant:** only confirmed higher-timeframe buckets may affect a score is enforced in code and tests. **VERIFIED**
- **Invariant:** sufficient count implies continuous trading-session history is **NOT ENFORCED**.

### Market regime and sector rotation

- **Allowed influences/writers:** bounded price series, same-run participation/sector/ranking inputs, versioned config, shared market context, repository revision writers. **VERIFIED**
- **Dangerous influences:** sparse feature dictionaries, source-session mismatch, global snapshot fallback, standalone prior-snapshot cross-run selection, manual config-version drift. **VERIFIED**
- **Invariant:** missing/unsafe market data must fail closed is **VIOLATED** for sparse and severely stale inputs.
- **Invariant:** a pipeline sector snapshot should consume its same-run market snapshot is not strictly enforced because global fallback remains. **NOT ENFORCED**

## Core Calculation Register

| Calculation | Inputs | Formula/algorithm | Parameters | Output | Session-bound | Fingerprint | Historical revision exposure | Downstream consumers | Implementation | Risk | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Upload/universe validation | CSV bytes/rows, mapped ticker | Bounds + nonempty + normalized duplicate check | Upload limits, aliases | Valid/invalid run inputs | No | None | New upload only | All run calculations | `upload_service.py`, `validation_service.py`, `column_mapper.py` | Semantic ticker/provider gaps | VERIFIED |
| Fundamental v2 | Run `raw_json`, canonical fields, config | Weighted 10 components minus missing penalty, clamp 0–10 | `fundamentals_v2.yaml` | `fundamental_scores` | No effective session | SHA-256 config hash in debug | Score replaced using current config | Combined/ranking/sector/later consumers | `fundamental_ranker_v2.py`, components/coverage services | Temporal/currency lineage | PARTIALLY_VERIFIED |
| OHLCV fetch planning | Contracts, cached count/endpoints, completed session, fetch history | Full/top-up/refresh/skip state machine | IB settings, 252 bars, 5-session revision window | Fetch plan/items | Yes | Request-scope/plan fields; no single plan hash in this scope | Replanned; provider revisions expected | Bar cache/technicals/regime | `ib_fetch_plan_service.py` | Interior gaps | PARTIALLY_VERIFIED |
| OHLCV cache/revision | IB bars and basis | Natural-key upsert, hash compare, append revision then update projection | ADJUSTED_LAST/TRADES | `price_bars`, `price_bar_revisions` | Bar date + observation time | MD5 data hash; separate SHA-256 evidence | Explicit revision ledger | All market calculations | `bar_cache_service.py` | Manual/unproven mutations | VERIFIED |
| PIT preferred OHLCV | Current bars + revision ledger + context | Reconstruct cutoff state; adjusted prices only on complete date coverage; TRADES volume | Max session/as-of | pandas price/volume frames | Yes | Context plus source hashes indirectly | Reconstructable if ledger complete | Technical/regime/ETF sector | `price_bar_repository.py` | Incomplete ledger/gaps | VERIFIED |
| Daily technical features | Preferred daily OHLCV | EMA/SMA/RSI/ATR/DMI/OBV/ROC/structure/volume/custom features | Pine + v4 configs | Feature result/debug | Yes | Feature config hash; optional artifact key | Recomputed from PIT view | Technical scoring, regime | `technical_indicators.py` | Missing-to-zero downstream | PARTIALLY_VERIFIED |
| Confirmed weekly features | Daily frame | W-FRI OHLCV aggregation + completed-week fence | Pine HTF config | Weekly latest/features | Yes | Included in technical signatures/debug | Recomputed from PIT daily view | HTF gate/trend | `technical_indicators.py` | No monthly equivalent | VERIFIED |
| Active technical v4 | Daily/weekly, benchmark, regime, leadership | Regime-weighted components/classification | Pine + v4 | `technical_scores` active fields | Yes | Config hashes/debug/context | Row replaced on recalculation | Combined/ranking/sector/lifecycle/Winner | `pine_replica_engine.py`, `technical_score_v4.py`, `technical_score_service.py` | Sparse numeric leakage | PARTIALLY_VERIFIED |
| Shadow technical v5 | Same plus run-relative leadership/sector | Strength/setup/entry composite and confidence factor | v5 config; active=false, shadow=true | v5 fields/debug on `technical_scores` | Yes | V5 input signature/config hash | Rebuilt with run | Forensics/future activation | `technical_score_v5.py`, `technical_strength_v5.py` | Shadow values may be mistaken for active | VERIFIED |
| Market regime | SPY/QQQ features; participation/sector context | 65/35 index score + rule classification + policy | Command-center config | Revisioned market snapshot | Yes | Evidence hash + version strings, no full config hash | New revisions on changed evidence | Technical context, sector, later consumers/UI | `market_regime.py`, command center/policy/repository | Sparse/stale/mixed session | CONTRACT VIOLATION |
| Sector universe rotation | Same-run sector aggregates, ranking and market snapshot | Five weighted components; state/permission/rank | `sector_rotation.yaml` | Revisioned snapshot/rows | Yes | SHA-256 full config + evidence hash | Revisioned; prior snapshot for deltas | Context/UI/later consumers | sector universe/policy/service/repository | New-run history absent; fallbacks | PARTIALLY_VERIFIED |
| Sector ETF rotation (disabled) | Sector proxy + SPY PIT bars | Trend/RS/momentum/breakout/risk score | ETF enable flag and weights | ETF component/combined sector score | Yes | Sector config/evidence hashes | Recomputed | Sector combined mode | `sector_etf_rotation_service.py` | Disabled by default | VERIFIED |

## Important Parameter Register

| Parameter | Calculation | Default | Source | Configurable | Unit | Purpose | Effect if changed | Code reference | Status |
|---|---|---:|---|---|---|---|---|---|---|
| Required daily bars | Coverage/technicals | 252 | settings + Pine maximum | Yes | sessions | Minimum long-history target | Alters backfill/readiness population | `settings.py:197`; `ohlcv_coverage_service.py:232-243` | VERIFIED |
| Revision window | Fetch top-up | 5 | settings | Yes | sessions | Re-request recent finalized bars | Changes revision detection window/request size | `settings.py:199`; `ib_fetch_plan_service.py:233-239` | VERIFIED |
| Full/top-up/refresh | IB fetch | 3Y / 10D / 60D | settings | Yes | IB duration | Historical request extent | Changes coverage, cost, and revision exposure | `settings.py:183-186` | VERIFIED |
| useRTH | IB fetch | true | settings | Yes | boolean | Regular-session daily bars | Changes OHLCV population | `settings.py:181`; `ib_data_fetcher.py:86-100` | VERIFIED |
| Fundamental component weights | Fundamental v2 | .13/.13/.12/.12/.12/.12/.10/.08/.05/.03 | `fundamentals_v2.yaml` | Yes | fraction | Blend quality dimensions | Direct score/order change | `fundamental_ranker_v2.py:166-170` | VERIFIED |
| Missing penalties | Fundamental v2 | .35/.20/.10/.05, cap 2.5 | `fundamentals_v2.yaml` | Yes | score points/field | Penalize incomplete exports | Direct score and labels | `fundamental_coverage_service.py:31-60` | VERIFIED |
| Sparse coverage | Fundamental v2 | 0.65 | config | Yes | fraction | Warning threshold | Changes warnings/quality interpretation | `fundamental_ranker_v2.py:129-135` | VERIFIED |
| Sloan bands | Earnings quality | good <=5; warning >=10; danger >=20 | config | Yes | percent-like input | Accrual-quality scoring/warnings | Component and label risk | `fundamentals_v2.yaml` | VERIFIED |
| ROA/ROIC bands | Profitability/efficiency | ROA 8/15; ROIC 10/20 | config | Yes | percent | Quality interpolation | Component scores | `fundamentals_v2.yaml` | VERIFIED |
| Liquidity nominal bands | Fundamental liquidity | $2m weak/$25m good | config | Yes | nominal uploaded currency | Market liquidity score | Currency-sensitive score change | `fundamentals_v2.yaml` | VERIFIED |
| EMA/SMA lengths | Daily trend | 10/20; 50/150/200 | `pine_defaults.yaml` | Yes | sessions | Trend horizons | Features, gates, 252 requirement | `technical_indicators.py` | VERIFIED |
| RSI/ATR/ADX | Technical | 14/14/14 smoothing 14; ADX min 18 | Pine config | Yes | sessions/index | Momentum, risk and trend strength | Component/classification changes | `pine_defaults.yaml` | VERIFIED |
| Pullback/breakout | Setup | 20/40; 3–18%; volume 1.2 | Pine config | Yes | sessions/percent/ratio | Setup detection | Setup score/classification | `pine_defaults.yaml` | VERIFIED |
| Risk thresholds | Technical | extension 8/15%; ATR 6/10%; heavy volume 1.5 | Pine config | Yes | percent/ratio | Danger scoring/caps | Risk, entry and class | `pine_defaults.yaml` | VERIFIED |
| RS horizons/mix | Technical leadership | ROC 21/63/126; broad/sector 70/30 | Pine/v5 | Yes | sessions/fraction | Relative performance | Momentum/leadership/gates | `pine_defaults.yaml`; `technical_scoring_v5.yaml` | VERIFIED |
| Weekly HTF | Technical HTF | EMA10/SMA30/40, slope4, ROC13, min score5.5 | Pine config | Yes | weeks/score | Confirmed higher-timeframe trend | Trend blend/gate | `pine_defaults.yaml`; `technical_indicators.py:279-345` | VERIFIED |
| V4 regime weights | Technical v4 | bull/choppy/risk-off vectors | v4 config | Yes | fraction | Regime-sensitive composite | Direct active score change | `technical_scoring_v4.yaml` | VERIFIED |
| V5 activation | Technical v5 | active false; shadow compare/persist true | settings | Yes | boolean | Choose active vs shadow model | Changes served technical result | `settings.py:235-237`; `technical_score_service.py` | VERIFIED |
| Market index mix | Market regime | SPY 65% / QQQ 35% | code | No config weight | fraction | Blend primary/risk proxy | Regime score | `market_regime.py:45-47` | VERIFIED |
| Market staleness | Market regime | stale >3; severe >6 | command-center config | Yes | sessions | Freshness policy | Warnings/risk color; currently not sizing permissions | `market_regime_command_center.py:226-238` | VERIFIED |
| Sector universe weights | Sector rotation | technical .25, profile .20, top25 .20, setup .20, risk .15 | sector config | Yes | fraction | Default sector leadership | Sector scores/ranks | `sector_rotation.yaml`; `sector_rotation_policy.py` | VERIFIED |
| ETF mode and blend | Sector rotation | disabled; universe/ETF .55/.45 when on | sector config | Yes | boolean/fraction | Enable price-based rotation | Changes universe-only to combined | `sector_rotation.yaml`; `sector_rotation_service.py:83-108` | VERIFIED |
| Rotation state thresholds | Sector rotation | leading/crowded 7.5, lagging 4.5, delta ±.75, danger .30 | sector config | Yes | score/fraction | State classification | Permissions/ranks/context | `sector_rotation_policy.py` | VERIFIED |

## Core Data Provenance Register

| Data | Provider | Storage | Timestamp semantics | Revision behavior | Consumers | Historical risk |
|---|---|---|---|---|---|---|
| Uploaded fundamental row | User upload; upstream provider unknown | `raw_company_rows.raw_json` plus mapped identity fields | `created_at` is ingestion, not provider observation/effective time | No application revision ledger; row is run-bound | Fundamental scoring, sector universe, later stages | Cannot prove filing/decision-time availability; alias reinterpretation | HIGH — VERIFIED |
| Fundamental score | Internal v2 scorer | `fundamental_scores` | `created_at` is calculation insert time; no input session | Delete/recreate per run | Combined/ranking/sector/later stages | Historical value can change under current config | HIGH — VERIFIED |
| IB contract | Interactive Brokers resolution | `ib_contracts` | `last_resolved_at` | Mutable resolution state | Fetch planner/executor | Instrument remapping if identity changes | MEDIUM — VERIFIED |
| Daily OHLCV current view | Interactive Brokers | `price_bars` | `bar_date` trading date; first/last seen and revised timestamps are observation lineage | Mutated projection with revision_count/hash | All price calculations | Current view includes later provider revisions | MEDIUM — VERIFIED |
| Daily OHLCV revision | Interactive Brokers/cache writer | `price_bar_revisions` | `observed_at` is when change was seen | Append-only intended | PIT repository/evidence | Incomplete ledger prevents full reconstruction | LOW/MEDIUM — VERIFIED |
| Preferred PIT frames | Internal reconstruction | In-memory pandas frames | Bound by session and cutoff timestamp | Reconstructed from current + first post-cutoff revision | Technical/regime/sector ETF | Strong when provenance complete; internal session gaps remain | MEDIUM — VERIFIED |
| Technical score | Internal Pine/v4/v5 | `technical_scores` | calculation cutoff and input session explicitly stored | Delete/reinsert per run/ticker | Combined/ranking/sector/later stages | Recalculation may change with code/config/revisions | MEDIUM — VERIFIED |
| Market regime snapshot | Internal SPY/QQQ classifier | `market_regime_snapshots` | context/cutoff/session plus source latest sessions; `as_of_date` is max source date | Evidence-hashed revisions/supersession | Technical context, sector and later consumers | Sparse/stale/mixed-session defects | HIGH — VERIFIED |
| Sector rotation snapshot/rows | Internal run aggregates; optional IB ETF data | `sector_rotation_snapshots`, `sector_rotation_rows` | explicit context/cutoff/session/date | Evidence-hashed revisions/supersession | Context/UI/later consumers | Global/prior fallback can cross run in standalone/missing-run cases | MEDIUM — VERIFIED |

## Significant findings

### CORE-001 — Sparse market history can produce a bullish, tradable regime

- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Market regime
- **Finding:** A non-empty SPY feature dictionary with a close but missing SMA50/SMA200/ROC/distribution inputs is normalized with missing numerics as zero. A positive close is therefore considered above both moving averages. The isolated audited probe classified `{close: 100}` as `Bull trend`, score `4.5`, `gate_ok=true`, confidence `normal`.
- **Evidence:** `app/services/market_regime.py:25-132`; `MarketRegimeCommandCenterService._load_market_input` records `insufficient_data` only in debug and still sends `feature_result.latest` to the classifier (`app/services/market_regime_command_center.py:144-190`).
- **Why it matters:** The command center is expected to fail closed when required trend history is not available.
- **Potential contamination/correctness effect:** Bullish permissions, starter sizing and downstream context can be emitted from fewer than the required 200/252 observations.
- **Existing guard:** Completely missing SPY returns Unknown/risk-off/gate-false. Feature calculation separately marks insufficient history.
- **Missing guard:** Classifier-level required-feature/readiness validation and fail-closed policy for insufficient benchmark history.
- **Recommended future remediation:** Pass structured readiness into classification; require close/SMA50/SMA200 and configured momentum/distribution warmups before a non-Unknown regime; add sparse-frame service tests.

### CORE-002 — Severe staleness changes risk color but retains bullish policy

- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Market regime policy
- **Finding:** Severe staleness overrides only `risk_state` (normally to Gray). The policy still returns the original regime's position-size multiplier, allowed profiles/setups, blocked lists, minimum-score adjustment, and summary. The audited pure-policy probe retained Bull-trend multiplier `1.0` and aggressive setup permission while reporting Gray/severely stale warnings.
- **Evidence:** `app/services/market_regime_policy.py:65-117`.
- **Why it matters:** Color and warnings do not enforce safe downstream behavior.
- **Potential contamination/correctness effect:** Stale data may authorize full-size entries and bullish setups despite an apparent fail-safe risk state.
- **Existing guard:** Stale and severe-stale warnings are persisted; severe staleness selects the configured risk color.
- **Missing guard:** A stale-data policy override for permissions, multiplier, gate/confidence, and minimum score.
- **Recommended future remediation:** Apply the Unknown/Gray fail-closed policy wholesale on severe staleness, or define an explicit stale policy; assert multiplier/permissions in tests.

### CORE-003 — Market regime can mix benchmark sessions and label the result with the newer date

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Market regime temporal integrity
- **Finding:** SPY and QQQ are loaded independently. The snapshot date is `max(source latest dates)`, while a lag of up to three sessions is treated as fresh.
- **Evidence:** `app/services/market_regime_command_center.py:67-127, 210-242`.
- **Why it matters:** A snapshot dated Tuesday may combine Tuesday SPY with Monday QQQ without an explicit degraded-confidence result.
- **Potential contamination/correctness effect:** The score/classification can blend non-contemporaneous market states and overstate its effective evidence date.
- **Existing guard:** Per-symbol source sessions are persisted in debug; larger lags generate stale warnings.
- **Missing guard:** Common-session alignment or explicit mixed-session confidence degradation/date semantics.
- **Recommended future remediation:** Truncate all benchmarks to the minimum common completed session, or store/evaluate a session vector and mark any mismatch degraded.

### CORE-004 — OHLCV readiness does not prove internal session completeness

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Market data and technical input validation
- **Finding:** Coverage aggregates count/min/max and current endpoint. Fetch-scope validation checks only returned boundaries. Neither compares cached/returned dates with the expected NYSE-session set.
- **Evidence:** `app/services/ohlcv_coverage_service.py:207-230`; `app/services/ib_fetch_plan_service.py:349-430`; `app/services/ib_fetch_executor.py:1127-1163`. Feature validation records only large calendar gaps and does not fail them (`app/services/technical_indicators.py:844-887`).
- **Why it matters:** A 252-row, current series can still omit intervening trading days.
- **Potential contamination/correctness effect:** Rolling windows, weekly OHLCV, pivots, momentum and relative-strength alignment may use distorted horizons and still be marked ready.
- **Existing guard:** Duplicate dates and invalid OHLC/negative volume fail; count and latest-date freshness are checked.
- **Missing guard:** Expected-session completeness, explicit acceptable suspension/listing exceptions, and post-fetch reconciliation.
- **Recommended future remediation:** Compute missing exchange sessions per requested/relevant range; repair or mark degraded before scoring; persist gap diagnostics.

### CORE-005 — Fundamental scores lack decision-time and currency comparability contracts

- **Severity:** P2
- **Status:** VERIFIED — CONTRACT GAP
- **Subsystem:** Fundamentals
- **Finding:** Fundamental inputs have no structured provider, provider-observed/published time, fiscal period end, revision identity, or effective session. Currency markers are removed during parsing and no FX conversion occurs, while fixed nominal dollar-volume thresholds are applied.
- **Evidence:** `app/services/numeric_parser.py:7-106`; `app/services/column_mapper.py`; `app/models/tables.py:99-126, 374-419`; `config/fundamentals_v2.yaml`.
- **Why it matters:** A score cannot prove that its facts were available at decision time or comparable across currencies.
- **Potential contamination/correctness effect:** Historical recalculation may silently reinterpret stale/revised/provider-mixed inputs, and non-USD nominal fields may cross USD-like thresholds incorrectly.
- **Existing guard:** Raw uploaded JSON and parser diagnostics are retained; config hash/model version are stored in debug.
- **Missing guard:** Provider/effective timestamp/fiscal identity/currency schema, FX policy, and immutable input manifest.
- **Recommended future remediation:** Require source metadata and period identities, preserve value currencies/units, define FX normalization, and bind an input hash plus cutoff to every score.

### CORE-006 — Sector rotation silently falls back from run-owned to global market context

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Sector rotation
- **Finding:** When no run-owned market snapshot exists at/before the sector date, the service selects the latest global snapshot. No warning identifies that ownership fallback.
- **Evidence:** `app/services/sector_rotation_service.py:395-408`.
- **Why it matters:** A run-bound sector artifact can consume market interpretation owned by another execution context.
- **Potential contamination/correctness effect:** Sector permissions and size multipliers may reflect a global snapshot not produced with the run's calculation context.
- **Existing guard:** Selection is date-bounded; the selected snapshot ID and summary are persisted.
- **Missing guard:** Same-run requirement or explicit fallback provenance/status.
- **Recommended future remediation:** Fail or degrade the pipeline path when the same-run snapshot is absent; reserve global fallback for explicitly standalone calls and persist the fallback reason.

### CORE-007 — Standalone prior-sector lookup may cross run ownership

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Sector rotation historical state
- **Finding:** `get_previous_snapshot` filters by `run_id` only when it is non-null. A standalone `run_id=None` build can therefore choose a run-owned previous snapshot.
- **Evidence:** `app/services/sector_rotation_repository.py:177-204`.
- **Why it matters:** Rank/score changes are historical comparisons and must have a defined ownership series.
- **Potential contamination/correctness effect:** Standalone sector deltas can be calculated against unrelated run composition.
- **Existing guard:** Earlier date, mode, and config hash must match; normal pipeline calls pass a run ID.
- **Missing guard:** `run_id IS NULL` filtering for standalone history or an explicit global-series identity.
- **Recommended future remediation:** Add the null-run filter and tests; define whether historical sector rotation is global, per run, or a separately published series.

### CORE-008 — Market-regime snapshots do not persist a full configuration hash

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Market regime provenance
- **Finding:** Snapshots store a free-form `config_version`, not a SHA-256 hash of the full loaded configuration. Evidence hashing covers the produced write payload rather than the source config payload itself.
- **Evidence:** `app/models/tables.py:720-881`; `app/services/market_regime_policy.py:26-63`; market regime repository evidence serializer.
- **Why it matters:** A config edit without version bump is not independently identifiable, especially when it does not change the resulting snapshot fields.
- **Potential contamination/correctness effect:** Forensic replay cannot prove the exact thresholds/policies used from the row alone.
- **Existing guard:** Calculation/config version strings, full outputs/debug, evidence hash and revision history.
- **Missing guard:** Canonical full-config hash and ideally repository code identity.
- **Recommended future remediation:** Persist and include a canonical config hash in the logical/evidence identity; validate version/hash consistency.

### CORE-009 — Insufficient technical history can still yield positive numeric score signals

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Technical scoring
- **Finding:** The score-layer numeric adapter maps missing values to zero. With a 20-row monotonic price frame, the audited isolated probe marked `insufficient_history=true` and confidence `low` but still produced trend `2.92`, momentum `1.85`, dual `3.8525`, and `above_slow=true` even though SMA200 was unavailable; classification remained `No trade`.
- **Evidence:** `app/services/pine_replica_engine.py` derived-input numeric normalization and scoring; `app/services/technical_indicators.py:830-887`; `app/services/technical_confidence.py:45-88`.
- **Why it matters:** Downstream readers may consume numeric components/flags without honoring confidence and missing-data fields.
- **Potential contamination/correctness effect:** Sparse tickers can receive misleading positive trend/momentum features and enter aggregates such as sector averages.
- **Existing guard:** `insufficient_data`, missing-data JSON, warning flags, low confidence, and conservative classification.
- **Missing guard:** Null/suppressed components or hard eligibility gates for unavailable long-horizon prerequisites; consumer enforcement.
- **Recommended future remediation:** Preserve tri-state feature semantics, suppress affected component scores, and require downstream aggregators to filter insufficient technical rows.

## Audit Coverage

### Fully audited

- Upload mapping/basic validation and their eligibility effects.
- Fundamental v2 formula structure, weights, thresholds, missing behavior, storage, and recalculation semantics.
- Daily IB historical request planning, preferred OHLCV selection, cache upsert/revision behavior, and point-in-time reconstruction.
- Important daily and weekly technical features, active v4/shadow v5 selection, persistence, and cutoff semantics.
- Market-regime inputs, classification, policy, freshness, ownership, revisions, and identified failure modes.
- Sector universe/ETF modes, policy/ranking/classification, snapshot lineage, and default pipeline dependency direction.

### Partially audited

- Runtime database contents, live provider entitlements/responses, and real historical revision completeness were not inspected.
- Every mathematical subfeature inside the large adaptive v4/v5 implementation was not independently recomputed; important score-affecting groups and composites were traced.
- All possible administrative/manual SQL writers cannot be proven absent from static repository inspection.

### Not audited

- CERI, ranking mathematics, setup lifecycle, and Winner internals, except dependency edges necessary for this report.
- Production configuration/environment overrides and any deployment-specific schedules.
- External accuracy of Interactive Brokers data or the uploaded fundamental export.

## Known Unknowns

- The actual upstream provider, export time, fiscal-calendar identity, and currency for any stored fundamental upload are UNKNOWN without live row/file evidence.
- The live database Alembic revision and whether its historical price revision ledger is complete are UNKNOWN.
- Environment-specific overrides may activate v5, ETF sector scoring, artifact caching, or different IB parameters; only committed defaults are VERIFIED.
- It is UNKNOWN whether downstream consumers outside this task consistently reject `technical_confidence=low`/`insufficient_data=true`; Task-specific consumers must be audited in their own reports.

## Contradictions

- Sector “rotation” suggests time-series relative performance, but the default committed implementation is same-run universe leadership and usually has no previous snapshot for a newly created run. Price-based ETF rotation exists only behind a disabled flag. **CONTRADICTORY naming/operational semantics**
- Market severe-staleness presentation says Gray while behavioral policy remains the underlying bullish policy. **CONTRADICTORY state versus permission semantics**
- Technical readiness reports insufficient history while some derived booleans and positive scores imply trend state. **CONTRADICTORY numeric versus readiness semantics**

## Potential Contract Violations

- CORE-001: sparse benchmark data can authorize bullish market regime.
- CORE-002: severe stale data retains bullish sizing/permissions.
- CORE-003: a single snapshot date can overstate the common evidence session.
- CORE-004: “ready” market data need not be session-complete.
- CORE-005: fundamental decision-time/currency provenance is unprovable.
- CORE-006/007: sector context/history can cross execution ownership in fallback paths.
- CORE-009: insufficient technical inputs can leak usable-looking numeric signals.

## High-Risk Cross-Subsystem Dependencies

- The mutable `price_bars` projection feeds technicals, market regime, sector ETF mode, CERI/lifecycle/Winner paths; point-in-time correctness depends on every reader using the bounded repository and on a complete revision ledger.
- Market-regime policy feeds technical context and sector permission. CORE-001/002 can therefore amplify beyond the market snapshot itself.
- Technical confidence/missingness feeds sector averages indirectly; CORE-009 can bias sector leadership unless consumers enforce readiness.
- Sector rotation consumes ranking and combined results, so later ranking/fundamental/technical recalculation can produce a new sector revision for the same logical date.
- Fundamental raw rows are run-bound but temporally unanchored; later scoring/ranking/sector outputs inherit that uncertainty.

## Files Inspected

- `app/models/tables.py`
- `app/settings.py`
- `app/services/upload_service.py`
- `app/services/validation_service.py`
- `app/services/column_mapper.py`
- `app/services/numeric_parser.py`
- `app/services/fundamental_ranker_v2.py`
- `app/services/fundamental_components_v2.py`
- `app/services/fundamental_coverage_service.py`
- `app/services/fundamental_score_service.py`
- `app/services/pipeline_executor.py`
- `app/services/ib_data_fetcher.py`
- `app/services/ib_fetch_plan_service.py`
- `app/services/ib_fetch_executor.py`
- `app/services/ib_historical_request_scope.py`
- `app/services/ohlcv_coverage_service.py`
- `app/services/bar_cache_service.py`
- `app/services/price_bar_repository.py`
- `app/services/price_bar_evidence.py`
- `app/services/price_series_version_service.py`
- `app/services/technical_artifact_cache.py`
- `app/services/technical_indicators.py`
- `app/services/pine_replica_engine.py`
- `app/services/technical_score_v4.py`
- `app/services/technical_score_v5.py`
- `app/services/technical_strength_v5.py`
- `app/services/technical_score_service.py`
- `app/services/technical_confidence.py`
- `app/services/technical_work.py`
- `app/services/market_clock_service.py`
- `app/services/market_calculation_context_service.py`
- `app/services/us_market_calendar.py`
- `app/services/market_regime.py`
- `app/services/market_regime_command_center.py`
- `app/services/market_regime_policy.py`
- `app/services/market_regime_repository.py`
- `app/services/market_participation_service.py`
- `app/services/sector_leadership_service.py`
- `app/services/sector_rotation_config.py`
- `app/services/sector_universe_service.py`
- `app/services/sector_etf_rotation_service.py`
- `app/services/sector_rotation_policy.py`
- `app/services/sector_rotation_service.py`
- `app/services/sector_rotation_repository.py`
- `config/column_aliases.yaml`
- `config/fundamentals_v2.yaml`
- `config/pine_defaults.yaml`
- `config/technical_scoring_v4.yaml`
- `config/technical_scoring_v5.yaml`
- `config/market_regime_command_center.yaml`
- `config/sector_rotation.yaml`

## Tests Inspected

- `tests/test_csv_upload_services.py`
- `tests/test_upload_service_v2.py`
- `tests/test_fundamental_ranker_v2.py`
- `tests/test_fundamental_coverage_service.py`
- `tests/test_fundamental_components_v2.py`
- `tests/test_fundamentals_v2_acceptance.py`
- `tests/test_ohlcv_coverage_service.py`
- `tests/test_ib_fetch_plan_service.py`
- `tests/test_ib_historical_request_scope.py`
- `tests/test_ib_services.py`
- `tests/test_ib_fetch_executor.py`
- `tests/test_technical_indicators.py`
- `tests/test_technical_score_v4.py`
- `tests/test_technical_scoring_v5.py`
- `tests/test_technical_confidence.py`
- `tests/test_technical_artifact_cache.py`
- `tests/test_market_regime.py`
- `tests/test_market_regime_policy.py`
- `tests/test_market_regime_repository.py`
- `tests/test_market_regime_command_center.py`
- `tests/test_sector_rotation_config.py`
- `tests/test_sector_rotation_service.py`
- `tests/test_sector_rotation_policy.py`
- `tests/test_sector_rotation_repository.py`
- `tests/test_session_temporal_integrity_remediation.py`
- `tests/forensics/test_session_temporal_integrity_forensics.py`

Selective non-destructive verification executed 266 tests across upload, fundamentals, OHLCV planning/scope, technicals, regime, sector rotation, and temporal-integrity suites: **266 passed, 1 Starlette/httpx deprecation warning**. The first attempt used the system Python and failed during test collection because `prometheus_client` was not installed there; rerunning with the repository `.venv` passed. This was an environment-selection failure, not an application-test failure.

Two additional pure in-memory probes exercised the sparse market-regime classifier, severe-stale policy, and short-history technical scorer. They performed no database or provider operations.

## SQL Inspected

No live SQL was executed. ORM schema, queries, constraints, and repository mutation logic were inspected statically. Live database inspection was intentionally omitted because the database safety context was not established.

## Verification Metadata

| Item | Value |
|---|---|
| Last verified commit / current HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Implementation baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Baseline drift | No |
| Branch | `codex/winner-evidence-remediation` |
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Runtime used for tests | `C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe` (Python 3.12.2) |
| Test result | 266 passed, 1 warning |
| Provider calls | None |
| Pipeline/jobs/rebuilds | None |
| Database writes | None |
| Migration changes | None |
| Runtime/application changes | None |
| Audit artifact | `docs/audit/calculation-lineage/02_core_calculations.md` |
