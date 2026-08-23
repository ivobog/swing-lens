# SwingLens Technical Scoring v5.0 implementation and certification

## A. Configuration shipped

The authoritative configuration is `config/technical_scoring_v5.yaml`. It contains:

- Engine version `5.0.0`, based on the existing `3.2.0` local feature engine.
- Technical Strength weights: Trend `0.52`, Momentum `0.30`, Leadership `0.18`.
- Trend blend: local `0.75`, confirmed weekly HTF `0.25`.
- Momentum blend: existing momentum `0.85`, acceleration quality `0.15`.
- Leadership percentile weights: ROC21 `0.30`, ROC63 `0.25`, ROC126 `0.15`, benchmark RS `0.15`, residual momentum `0.15`.
- Broad/sector benchmark RS mix: broad market `0.70`, sector `0.30` when sector data exists.
- Canonical sector ETF map: XLK, XLF, XLE, XLI, XLV, XLY, XLP, XLU, XLRE, XLB, and XLC.
- Setup-specific component weights for all seven setup types.
- ATR-relative trigger thresholds and qualities.
- Stock-specific risk thresholds and evidence points, secondary risk weight `0.20`.
- Execution weights and stop/target geometry thresholds.
- Stage modifiers, danger detection thresholds, Entry Quality caps, regime-specific composite weights, and confidence factors.

`technical_scoring_v5_config.py` validates the engine version and every configured weight group at load time. The shipped v5 configuration hash is `ad813416d238476c98b2f03c94175396247be7a9c1a6bf5a65ae095018672ae4`; it is persisted in every v5 explainability record.

## B. Code and schema changes

New scoring modules:

- `technical_scoring_v5_config.py`: load, copy, and validate configuration.
- `sector_benchmark_service.py`: canonical sector resolution with explicit missing/unsupported/data-missing states and no QQQ fallback.
- `leadership_v5.py`: vectorized cross-sectional percentile ranking.
- `technical_strength_v5.py`: Trend, Momentum, Leadership composition with missing-evidence renormalization.
- `trigger_quality.py`: ATR-relative trigger-state model.
- `setup_quality_v5.py`: setup selection followed by type-specific scoring and one stage modifier.
- `entry_quality_v5.py`: stock-specific risk, execution quality, trigger quality, danger priority, and EQ-only caps.
- `technical_score_v5.py`: independent v5 composition, classification, confidence, and full explainability.
- `technical_v5_calibration.py`: stable historical comparison schema and required component-ablation record schema.

Integration changes:

- `technical_score_service.py` computes v4 first, computes v5 independently when shadow or active mode is enabled, and persists both without changing v4 output.
- `technical_indicators.py` emits ROC10 and truthful stop/target source diagnostics, including prior resistance and fallback sources.
- `pine_replica_engine.py` exposes the local inputs required by v5, including beta/residual diagnostics.
- `TechnicalScore` has nullable v5 columns and `v5_debug_json`.
- Alembic revision `0052_technical_scoring_v5` adds those nullable columns and is the single migration head.
- Settings default to v5 active `false`, shadow comparison `true`, and shadow persistence `true`.
- Run detail and CSV exports expose v5 TS/SQ/EQ/TCS, confidence-adjusted score, setup, sector benchmark, residual momentum, trigger/stop geometry, and danger state. Historical v4 rows retain their existing fallback display/export behavior.

## C. Exact scoring definitions

All component scores are clamped to `[0, 10]` and rounded deterministically.

### Technical Strength

When confirmed weekly HTF exists:

`TrendQuality = 0.75 * LocalTrend + 0.25 * HTFTrend`

When HTF is missing, `TrendQuality = LocalTrend`; HTF is not replaced by zero, and a warning is recorded.

`MomentumAcceleration10_63 = ROC10 - (10 / 63) * ROC63`

`AccelerationQuality = clamp(5 + 0.25 * MomentumAcceleration10_63)`

`MomentumQuality = 0.85 * ExistingMomentum + 0.15 * AccelerationQuality`

If either ROC input is missing, Momentum Quality uses Existing Momentum and records missing acceleration evidence.

For each Leadership component, the run-universe percentile is converted to `[0, 10]`. Leadership is the configured weighted sum of ROC21, ROC63, ROC126, benchmark RS, and beta-adjusted residual momentum percentiles. Missing components are excluded and remaining weights are renormalized. Neither v4 dual score nor setup score is an input.

`TechnicalStrength = 0.52 * TrendQuality + 0.30 * MomentumQuality + 0.18 * LeadershipQuality`

If Leadership is unavailable, its top-level weight is excluded and the Trend/Momentum weights are renormalized.

Residual returns use `stock_return - rolling_beta_63 * benchmark_return`; 21- and 63-session residual returns are compounded. Residual Momentum is `clamp(5 + 0.20 * residual_return_21 + 0.08 * residual_return_63)` before cross-sectional ranking.

### Setup Quality

The selector evaluates qualified evidence in this order: breakout, VCP, pullback, momentum continuation, extended momentum, trend repair, none. It selects one setup before scoring, so an inapplicable high raw subscore cannot win.

- Pullback: `0.70 * primary + 0.15 * volume_confirmation + 0.15 * trigger_readiness`.
- VCP: `0.75 * primary + 0.15 * trend_confirmation + 0.10 * trigger_readiness`.
- Breakout: `0.75 * primary + 0.15 * volume_confirmation + 0.10 * base_tightness`.
- Momentum continuation: `0.55 * primary + 0.25 * trend_confirmation + 0.20 * execution_readiness`.
- Extended momentum: `0.70 * primary + 0.30 * execution_readiness`.
- Trend repair: `0.70 * primary + 0.30 * trend_confirmation`.
- None: zero.

Stage is applied exactly once to the selected Setup Quality: Stage 2 `+0.25`, Stage 1-to-2 `+0.10`, Stage 1 `0`, Unknown `-0.10`, Stage 3 `-0.50`, Stage 4 `-1.00`. Stage 4 buyable-looking setups are classified as filtered and cannot produce a buy action.

Trigger distance is `(trigger_price - close) / ATR14`. The configuration distinguishes invalidated, too far below, approaching, near, at trigger, freshly triggered, beyond trigger, extended beyond trigger, and not applicable states.

### Entry Quality

Base risk is rebuilt from stock-specific evidence only: extension, RSI, resistance, heavy red volume, distribution, failed breakout, gap exhaustion, liquidity, ATR%, relative strength, optional sector weakness, HTF weakness, and major moving-average breaks. Market regime/risk-off evidence is explicitly excluded.

`CombinedRisk = clamp(max(BaseRisk, ClimaxRisk) + 0.20 * min(BaseRisk, ClimaxRisk))`

`RiskControl = clamp(10 - CombinedRisk)`

Stop distance is `(close - stop) / ATR14`. Stop geometry scores 10 from 1.0 to 2.5 ATR, declines toward the configured 0.5/4.0 ATR outer bounds, and rejects invalid geometry. Reward/risk quality is `clamp(RR / 3 * 10)`. An R-multiple fallback target receives a `0.70` quality discount. Execution is:

`ExecutionQuality = 0.35 * RRQuality + 0.30 * StopGeometry + 0.20 * Liquidity + 0.15 * StopValidity`

`EntryQualityBeforeCap = 0.50 * RiskControl + 0.30 * ExecutionQuality + 0.20 * TriggerQuality`

Danger priority is Failed breakout, Climax reversal risk, Blowoff top, Distribution risk, Late-stage extension. The corresponding EQ caps are 3.5, 4.0, 3.0, 4.5, and 5.0. Caps affect Entry Quality only; they do not alter Technical Strength.

### Composite and confidence

- Bull trend: `TCS = 0.45 * TS + 0.35 * SQ + 0.20 * EQ`.
- Choppy: `TCS = 0.35 * TS + 0.35 * SQ + 0.30 * EQ`.
- Risk-off: `TCS = 0.25 * TS + 0.25 * SQ + 0.50 * EQ`.

Regime contributes only this weight policy. It is not added as a score component and is not included in stock-specific risk.

`ConfidenceAdjusted = clamp(5 + (TCS - 5) * confidence_factor)`, where high/normal/low/error factors are `1.00/0.85/0.50/0.00`. Missing sector data lowers confidence only when `required_for_confidence` is enabled.

## D. Persistence, shadow comparison, and explainability

Shadow mode persists v5 columns and a v4/v5 comparison, while `dual_score`, classification, action, version, and confidence remain the existing v4 values. Active mode explicitly mirrors v5 into those compatibility fields. The original v4 payload remains in `v4_debug_json` in either case. Historical v4-only rows require no backfill.

Every v5 debug payload includes engine version, configuration hash, input signature, TS decomposition, HTF availability, momentum acceleration, Leadership percentiles and universe size, beta/residual diagnostics, sector resolution/fallback, selected setup and reasons, trigger state/distance, stage modifier, risk channels, execution geometry and sources, danger cap, composite weights, confidence, classification/action, flags, warnings, and applied modifiers.

## E. Mandatory golden scenarios

These deterministic synthetic fixtures passed. Scores shown are the persisted v5 values.

| Scenario | TS | SQ | EQ | TCS | Classification | Cap |
|---|---:|---:|---:|---:|---|---:|
| Strong Stage 2 pullback | 8.4554 | 8.3000 | 8.5500 | 8.4199 | Clean bull pullback | — |
| High-quality VCP near trigger | 8.4554 | 9.1750 | 8.9500 | 8.8062 | Volatility contraction setup | — |
| Fresh breakout, strong volume | 8.4554 | 9.1250 | 8.5500 | 8.7087 | Tight base breakout | — |
| Momentum continuation | 8.4554 | 8.5417 | 9.1500 | 8.6245 | Momentum continuation | — |
| Extended momentum | 8.4554 | 8.5000 | 8.4500 | 8.4699 | Extended momentum | — |
| Failed breakout, strong trend | 8.4554 | 7.2500 | 3.5000 | 7.0424 | Failed breakout | 3.5 |
| Distribution risk, high TS | 8.4554 | 8.3000 | 4.5000 | 7.6099 | Distribution risk | 4.5 |
| Climax reversal | 8.4554 | 8.3000 | 4.0000 | 7.5099 | Climax reversal risk | 4.0 |
| Stage 4 false VCP | 8.4554 | 7.7250 | 8.5500 | 8.2187 | Filtered pullback | — |
| Low liquidity, strong chart | 8.4554 | 8.3000 | 7.9900 | 8.3079 | Clean bull pullback | — |
| Missing sector benchmark | 8.4554 | 8.3000 | 8.5500 | 8.4199 | Clean bull pullback | — |
| Missing HTF | 8.5204 | 8.3000 | 8.5500 | 8.4492 | Clean bull pullback | — |
| High-beta, weak residual | 8.3204 | 8.3000 | 8.5500 | 8.3592 | Clean bull pullback | — |
| Moderate ROC, strong residual | 8.4104 | 8.3000 | 8.5500 | 8.3997 | Clean bull pullback | — |
| Same stock, bull regime | 8.4554 | 8.3000 | 8.5500 | 8.4199 | Bull weights | — |
| Same stock, risk-off regime | 8.4554 | 8.3000 | 8.5500 | 8.4639 | Risk-off weights | — |

The last pair intentionally proves that TS, SQ, EQ, and stock risk remain unchanged while only composite weights change. A weight-policy change is not guaranteed to lower TCS; here EQ is the strongest component, so upweighting EQ raises TCS slightly.

Representative synthetic shadow comparisons:

| Scenario | v4 | v5 | Delta | v4/v5 classification |
|---|---:|---:|---:|---|
| Strong pullback | 8.0740 | 8.4199 | +0.3459 | Clean bull pullback / Clean bull pullback |
| Failed breakout | 8.0740 | 7.0424 | -1.0316 | Failed breakout / Failed breakout |
| Climax reversal | 7.3240 | 7.5099 | +0.1859 | Climax reversal risk / Climax reversal risk |

## F. Tests and performance

Certification lanes:

- V5-specific suite: 31 passed, including an explicit single-query/one-load-per-ETF assertion.
- Affected technical, hardening, confidence, worker-boundary, and UI lane: 104 passed, one unrelated Alembic deprecation warning.
- Broad technical/regime selection: 243 passed.
- Final full current-tree suite: 1,822 passed, 9 skipped, 13 dependency deprecation warnings.
- Ruff passes on all changed Python files; `git diff --check` is clean; Alembic has one head.

Microbenchmark (1,000 deterministic score compositions, best of five): v4 scoring was 0.1355 ms/ticker; v4 plus v5 was 0.7254 ms/ticker; incremental v5 composition was 0.5898 ms/ticker. The scoring-only percentage looks large because v4 composition is extremely small; the incremental absolute cost is below 1 ms and is negligible beside the roughly 0.9–1.0 second synthetic feature-engine path. A paired end-to-end synthetic benchmark showed no measurable regression above noise.

Sector data is loaded once per unique ETF per run after one sector metadata query. Cross-sectional Leadership ranking is vectorized. There are no per-ticker sector metadata queries, and the existing process-pool, pure worker boundary, stale-result fencing, overlap coordinator, and local-artifact cache design remain intact. Cross-sectional Leadership is not stored in the ticker-local artifact cache.

## G. Calibration and rollout decision

`technical_v5_calibration.py` defines the requested historical comparison columns: ticker, decision date, v4, v5 TS/SQ/EQ/TCS, classification, setup type, market regime, sector, forward 5/10-day returns, and 5/10-day MFE/MAE. It also validates records for Leadership, residual momentum, stage, HTF, trigger, climax, and old-max-setup ablations. No empirical forward-return dataset was supplied or discovered in this implementation task, so no statistical calibration claim is made.

Original certification decision: **READY FOR SHADOW**, not ready for default activation.
The later empirical shadow decision is maintained separately in
`docs/technical_scoring_v5_shadow_evaluation.md`; this implementation certificate is not
retroactively rewritten into an activation claim.
