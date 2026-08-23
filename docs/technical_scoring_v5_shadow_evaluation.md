# Technical Scoring v5.0 shadow calibration and evaluation

## 1. Executive verdict

**CONTINUE SHADOW**

Technical Scoring v5.0 is point-in-time reproducible for 5,848 observations from 17
complete historical run universes, but it does not demonstrate ranking superiority to
v4 in the available window. Raw v5 TCS underperformed v4 in the run-normalized top 10%
and top 20% selections. Technical Strength was materially inverted, danger states did
not show worse matched outcomes, and the calibration period covers only 10 decision
dates and 35 calendar days. Entry Quality and Trigger Quality show some promising
10-day separation, but not enough stable evidence to recalibrate or activate v5.

V5 remains disabled as the production default. Synthetic fixtures were used only for
deterministic tests, never as empirical trading evidence.

## 2. Frozen baseline and Phase 0 audit

| Item | Frozen value |
|---|---|
| Git commit | `eb6798ee990e268b2ef808bb0747465220b9b0e7` |
| V5 engine | `5.0.0` |
| Persisted v4 engine | `4.0.0` |
| V5 config hash | `ad813416d238476c98b2f03c94175396247be7a9c1a6bf5a65ae095018672ae4` |
| Source migration head after fixes | `0053_split_artifact_identity` |
| Local live database | Left unchanged at its pre-task revision (`0051`) |
| Production setting | `technical_v5_enabled = false` |
| Shadow settings | compare and persistence remain enabled by default |

The certified baseline was still HEAD before edits and the tracked worktree was clean.
Unrelated user-owned untracked SEC/recovery artifacts were preserved.

The implementation map covers `config/technical_scoring_v5.yaml`,
`technical_score_v5.py`, `leadership_v5.py`, `setup_quality_v5.py`,
`entry_quality_v5.py`, `sector_benchmark_service.py`, persistence in
`technical_score_service.py`/`tables.py`, calibration helpers, Winner Evidence,
historical `technical_scores`/`price_bars`/`price_bar_revisions`, the technical artifact
cache, ranking profiles, Setup Lifecycle, alerts, exports, UI, and combined-score
consumers. The original implementation certificate remains a historical
**READY FOR SHADOW** certificate and has not been rewritten as an activation claim.

## 3. Dataset and provenance

| Measure | Value |
|---|---:|
| Source technical rows | 19,129 |
| Admitted observations | 5,848 |
| Source/admitted runs | 105 / 17 |
| Distinct decision dates | 10 |
| Date range | 2026-07-17 to 2026-08-21 |
| 5d outcome N / missing | 3,954 / 32.39% |
| 10d outcome N / missing | 2,587 / 55.76% |
| Current-series reconciled | 4,232 |
| Revision-history reconciled | 1,616 |
| Stop/target outcome available | 4,497 (76.90%) |
| Price-basis-changed stop/target unavailable | 1,351 (23.10%) |

Rejections were explicit: 4,966 rows failed exact feature reconciliation, 8,170
otherwise reconstructed rows were removed because their run universe was incomplete,
and 145 lacked persisted decision features. A run was admitted only when every member
reconciled; Leadership therefore never used a partial or future universe.

Regime coverage is Distribution 4,453, Bull trend 912, and Choppy 483. Only
Distribution has 10-day outcomes; Choppy has no 5-day outcomes. All seven setup types
appear: pullback 3,588, VCP 1,215, none 421, extended momentum 211, trend repair 176,
momentum continuation 121, and breakout 116. Twelve sector labels appear, including
Unknown. Sector benchmark evidence is missing for 48.75% of rows; Leadership and HTF
are complete in the admitted data; low confidence is 33.29%; error confidence is 0%.

## 4. Point-in-time certification

The calibration pipeline is read-only against PostgreSQL. It reconstructs ticker, SPY,
and sector ETF histories using `first_seen_at`, revision observation time, and prior bar
values at each score's `created_at`. Persisted ROC21/63/126, ATR14, and RSI14 must exactly
reconcile (tight numeric tolerance, at least three checks). V5-only scoring evidence is
then derived from that as-of history.

Forward outcomes begin strictly after the decision date. Returns and MFE/MAE use a
consistent final-adjusted decision/future price basis. Stored stop/target sequences are
used only when their decision-time price basis still matches; otherwise they are marked
unavailable. Same-bar stop/target touches are `AMBIGUOUS`.

Confirmed weekly HTF uses the last completed weekly bar. Future-pivot invariance is
tested by comparing a decision-date prefix with the same row in the full series. Forward
outcomes, Leadership run membership, split ordering, and as-of revision/backfill behavior
have dedicated tests. No future outcomes enter scoring or candidate selection.

## 5. V4 versus v5

Selections and deciles are normalized within run; bootstrap intervals resample complete
runs. Returns are percentage points.

| Score | Selection | N | Mean 5d | Mean 10d | 5d hit | MFE 5d | MAE 5d |
|---|---|---:|---:|---:|---:|---:|---:|
| v4 | Top 10% | 593 | 0.413 | 3.025 | 50.54% | 3.558 | -2.926 |
| raw TCS | Top 10% | 593 | 0.050 | 1.677 | 48.29% | 3.023 | -2.762 |
| adjusted TCS | Top 10% | 593 | 0.090 | 1.849 | 48.14% | 3.036 | -2.813 |
| v4 | Top 20% | 1,180 | 0.676 | 2.594 | 55.35% | 3.588 | -2.801 |
| raw TCS | Top 20% | 1,178 | 0.476 | 2.483 | 52.41% | 3.383 | -2.796 |
| adjusted TCS | Top 20% | 1,178 | 0.461 | 2.601 | 51.86% | 3.392 | -2.818 |

For raw TCS versus v4, the run-cluster bootstrap interval for the top-20 5-day return
delta is -0.354 to +0.166 percentage points. This does not establish superiority. The
within-run Spearman correlations for v4 are -0.093/-0.057 at 5d/10d; raw TCS is
-0.090/-0.025; confidence-adjusted TCS is -0.089/-0.021. None is a strong monotonic
ranker in this window.

The score delta `v5_TCS - v4` averages -1.085 (median -0.834). Classification differs
on 61.73% of rows and action wording differs on 97.95%, reinforcing the downstream
consumer/versioning risk. Large disagreements are exported with subscores, setup,
regime, Stage, danger, missing evidence, outcomes, and exact selection reasons.

## 6. TS, SQ, and EQ findings

### Technical Strength

TS is the weakest result. Run-normalized top-20 TS returns are 0.370%/1.113% versus
v4's 0.676%/2.594%, and the clustered 5-day delta interval is entirely negative
(-0.541 to -0.091). The lowest TS decile outperforms the highest at both horizons.
Current TS composition should not be promoted and needs later-period diagnosis before
any weight tuning.

### Setup Quality

SQ top-20 returns are 0.475%/1.612%; its 5-day interval versus v4 spans
-0.433 to +0.144. Type-level outcomes are heterogeneous:

| Setup | Outcome N 5d/10d | Mean 5d | Mean 10d | Evidence |
|---|---:|---:|---:|---|
| Pullback | 2,445 / 1,630 | 1.336 | 3.200 | stronger aggregate |
| VCP | 779 / 500 | 1.096 | 2.316 | stronger aggregate |
| Trend repair | 121 / 95 | 2.323 | 2.098 | useful/noisy |
| Breakout | 95 / 75 | 1.392 | 1.606 | weak |
| Extended momentum | 148 / 72 | 0.097 | -0.260 | useful/noisy |
| Momentum continuation | 81 / 39 | -1.451 | -0.007 | weak |
| None | 285 / 176 | 1.144 | 1.257 | useful/noisy |

The old-max setup ablation improves top-selection point estimates by 0.109/0.116
points at 5d/10d. The type-specific SQ architecture is therefore not empirically proven
better in this sample.

### Entry Quality

EQ is the only promising subscore: its run-normalized top-20 returns are 1.225%/3.193%,
with a 5-day delta interval versus v4 of -0.006 to +0.985. The point estimate is better,
but the interval touches zero and only 17 runs are available.

Among TS >= 8 observations, EQ < 5 produces 0.264%/0.947% (N 179/140) while EQ >= 7
produces 0.089%/1.663% (N 344/231). High EQ improves the 10-day result but not 5-day
timing. Median-split TS/EQ quadrants are confounded by regime/date composition and do not
show the intended clean separation. G5 remains unpassed.

## 7. Trigger, execution, and danger findings

Freshly triggered observations are strongest at 2.144%/5.064% (N 503/333), while
extended-beyond-trigger is 0.614%/1.427%. However, at-trigger observations are
-1.120%/1.076% (N 72/46), so the current ATR state curve is not monotonically validated.
Removing Trigger Quality reduces top-selection point estimates by 0.208/0.411, but its
observation-level 5-day interval crosses zero. Preserve Trigger Quality in shadow; do
not retune bands from this period.

All admitted targets use `ATR_TARGET`, so the fallback discount and alternate target
sources cannot be calibrated. Low-liquidity rows underperform normal liquidity at 5d
(0.441% versus 1.237%), but N is limited. Stop-distance and ATR-percentile slices are in
`execution_slices.csv`.

Danger caps are not validated:

| Danger | Outcome N 5d/10d | Danger mean 5d/10d | Matched non-danger 5d/10d |
|---|---:|---:|---:|
| Distribution risk | 910 / 602 | 1.702 / 3.576 | 1.051 / 2.918 |
| Failed breakout | 383 / 304 | 1.363 / 1.968 | 0.885 / 2.308 |
| Climax reversal | 3 / 3 | 2.711 / -1.449 | -5.120 / -0.770 |

Distribution risk is better than its TS/SQ/regime/sector-matched controls; Failed
breakout is mixed; Climax has only three outcomes. Blowoff top and late-stage extension
have no admitted observations. Removing danger caps changes ranks materially but changes
top-selection returns only +0.037/-0.042. Current cap values cannot be justified from
this evidence.

## 8. Regime, Stage, sector, and missing evidence

Risk-off weighting raises TCS in 47.79% of Distribution rows; EQ is the strongest
component in 35.57%. This is the expected weighted-average behavior, not a defect.
Replacing regime weights with bull weights reduces top-selection point estimates by
0.210/1.098 at 5d/10d. That favors the current regime policy in-sample, but no independent
regime-cycle holdout exists.

Stage 4 has only 11 rows, two 5-day outcomes and one 10-day outcome. There are no Stage
4 observations with TCS >= 8; actions are gated (`No qualified setup`, `Wait for trend
repair`, or `Avoid`). The single 10-day result is -5.791%, but the sample is purely
descriptive. No stronger Stage penalty is justified.

Sector results range widely (for example Health Care 2.185%/4.430%, Technology
2.022%/3.005%, Consumer Staples 0.147%/1.283%), but sectors are not balanced across
dates/regimes. Sector RS removal improves point estimates by 0.104/0.256, with an
observation-level interval spanning zero. Sector RS needs later-period replication.

Missing HTF, missing Leadership, unknown regime, and error-confidence behavior cannot be
studied because their admitted N is zero. Missing sector is 48.75%: raw TCS averages
6.479, adjusted TCS 6.223, and mean rank changes only -0.008. Low-confidence rows have
better realized outcomes than normal-confidence rows. Confidence adjustment therefore
does not demonstrate improved ranking and does not fix a measurable missing-HTF issue in
this dataset.

## 9. Ablations

The complete A0-A14 campaign ran. Point estimates below are top-20 selection deltas
versus baseline v5; intervals in the CSV were observation bootstraps generated by the
completed campaign and are exploratory because only 17 runs survive. The pipeline source
now uses run-cluster bootstraps for future campaigns.

| Ablation | 5d delta | 10d delta | Interpretation |
|---|---:|---:|---|
| No Leadership | +0.011 | +0.131 | no demonstrated incremental value |
| No residual momentum | +0.011 | -0.026 | no demonstrated incremental value |
| No ROC126 | +0.051 | +0.029 | negligible |
| No benchmark RS | -0.028 | +0.029 | negligible |
| No sector RS | +0.104 | +0.256 | potentially harmful; replicate |
| No HTF | -0.007 | +0.001 | no demonstrated incremental value |
| No momentum acceleration | +0.032 | -0.061 | no demonstrated incremental value |
| No Stage modifier | +0.001 | -0.008 | no demonstrated incremental value |
| No Trigger Quality | -0.208 | -0.411 | promising component, uncertain |
| No Climax Risk | -0.026 | -0.392 | small sample/confounded |
| Old max setup | +0.109 | +0.116 | v5 SQ not proven better |
| No confidence adjustment | +0.014 | -0.118 | no demonstrated improvement |
| Fixed bull regime weights | -0.210 | -1.098 | current regime policy promising |
| No danger caps | +0.037 | -0.042 | caps not validated |

No component qualifies for removal or weight change from this short window alone, but TS,
sector RS, type-specific SQ, danger caps, and confidence adjustment are priority concerns.

## 10. Calibration and walk-forward

Small grids covered HTF 0/10/20/25/30%, acceleration 0/10/15/20/25%, residual momentum
0/7.5/15/22.5/30%, Stage none/half/current/stronger Stage 3-4, plus baseline.

Acceleration 10% was selected on calibration (top-20 10d 3.399% versus baseline 3.297%).
Validation was 1.246% versus 1.188%. On the untouched holdout, both selected and baseline
rankings produced exactly 2.018% 10-day and 1.782% 5-day, so the candidate adds no
holdout ranking value. No configuration change is justified. Danger-cap grids were not
run because the underlying danger signals failed the prerequisite outcome validation.

The split is time ordered, but it spans only 10 decision dates and does not constitute an
independent market-cycle holdout. Walk-forward evidence is insufficient for activation.

## 11. Leadership universe stability

Repeated 90% subsets have mean absolute Leadership deltas of roughly 0.023-0.033 and TCS
deltas of 0.0010-0.0015. Repeated 75% subsets have Leadership deltas 0.046-0.079 and TCS
deltas 0.0021-0.0036. Mean rank deltas remain below 0.0007; no classification or action
changes occur. Leadership is stable to these random universe reductions. A separate
reference universe is not required by this sample, although Leadership's predictive
increment is unproven.

## 12. Correctness and implementation fixes

- V5 input signatures now cover every deterministic material base, derived,
  contraction/VCP, box/breakout, Stage, Climax, regime, readiness/adaptive, Leadership,
  sector-resolution, and v5-config input while excluding unrelated generated timestamps.
- Local artifact identity now separates `feature_config_hash` from final
  `scoring_config_hash`; pure v5 weights do not invalidate OHLCV/indicator artifacts,
  while Pine/v4 feature-generation changes do.
- Migration `0053_split_artifact_identity` adds/backfills the feature hash and leaves one
  source migration head.
- Historical reconstruction is revision- and first-seen-aware for ticker, SPY, and sector
  histories; residual momentum cannot reuse a revised internal beta path merely because
  endpoint ROC values happen to match.
- Outcome construction uses a consistent price basis and marks indefensible stop/target
  sequences unavailable.
- Calibration checkpoints are written atomically; bootstrap duplicate-index and
  within-run ranking defects found during execution have regression coverage.
- The consumer map identifies ranking profiles, Setup Lifecycle/alerts, Winner Evidence,
  and v4-era golden contracts as default-activation blockers.

## 13. Test and tool evidence

| Lane | Passed | Skipped | Failed |
|---|---:|---:|---:|
| V5/signature/cache/calibration | 66 | 0 | 0 |
| Affected technical/Winner/lifecycle/alerts | 517 | 0 | 0 |
| Broad repository suite | 1,844 | 9 | 0 |

The broad suite emitted 14 deprecation warnings. Ruff passes for all changed/affected
code. `ruff check .` also ran and reported 29 pre-existing/unrelated findings in early
Alembic migrations and user-owned untracked SEC recovery scripts; those files were not
modified. `git diff --check` passes. Alembic reports exactly one source head. Spreadsheet
artifact inspection verified the primary CSV used ranges and found zero formula/error
tokens.

## 14. Downstream consumer safety

Persistence, UI, export, combined-score, market-participation, and sector aggregation
are v5-safe. Ranking profiles mix active `dual_score` with legacy component penalties;
Setup Lifecycle snapshots/alerts mix v5 total with v4 component semantics; Winner
Evidence/cohort features are trained on v4 meanings; SLSE golden thresholds are v4-era.
These block default activation. See `technical_scoring_v5_consumer_audit.md`.

## 15. Activation gates

| Gate | Status | Evidence |
|---|---|---|
| G1 correctness | PASS | PIT reconstruction, signatures, cache split, tests, one migration head |
| G2 data coverage | FAIL | only 10 dates/35 days; 10d outcomes only in Distribution; sector missing 48.75% |
| G3 ranking value versus v4 | FAIL | raw TCS top selections do not beat v4; TS materially worse |
| G4 danger-state validation | FAIL | matched danger outcomes are not consistently worse; several states absent |
| G5 Entry Quality validation | INSUFFICIENT | promising top-selection/10d signal, mixed 5d behavior and wide uncertainty |
| G6 robustness across slices | FAIL | results depend on short regime/date window; sector/setup heterogeneity |
| G7 out-of-sample validation | INSUFFICIENT | frozen candidate ties baseline on a very short holdout |
| G8 downstream consumer safety | FAIL | ranking, lifecycle/alerts, Winner Evidence, SLSE contracts block default |

## 16. Follow-up actions

1. Continue shadow collection across multiple independent regime cycles, retaining the
   corrected signatures, as-of provenance, complete run universes, and current baseline.
2. Prioritize diagnosis of TS inversion and replicate EQ/Trigger performance; do not tune
   weights until later-period data is available.
3. Collect enough Stage 4, Climax, blowoff, late-stage, missing-HTF, and alternate target
   observations to evaluate presently untestable behavior.
4. Version or isolate ranking profiles, Setup Lifecycle/alerts, Winner Evidence, and SLSE
   contracts before any limited active feature flag.
5. Re-run the frozen calibration/validation/holdout campaign; promote no candidate unless
   it improves later-period ranking and all activation gates pass.
