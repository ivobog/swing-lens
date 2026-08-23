# Technical Scoring v5 forensic recalibration report

## 1. Executive verdict

**CONTINUE SHADOW**

V5 remains disabled as the production default. The frozen baseline was investigated,
not retuned. The current evidence identifies EQ/Trigger as the most plausible useful
idea, but does not establish later-period superiority or consumer safety.

## 2. Dataset and effective sample size

- Observations: 5,848
- Complete run universes: 17
- Independent decision dates: 10
- Date range: 2026-07-17 through 2026-08-21
- Regimes: Bull trend, Choppy, Distribution
- Missing resolved sector benchmark: 48.75%
- Frozen config hash: `ad813416d238476c98b2f03c94175396247be7a9c1a6bf5a65ae095018672ae4`
- Engine: `5.0.0`; candidate name: `V5_BASELINE`

The 50-date minimum and 80+ preferred targets are not met. The collection/reconstruction
pipeline is verified and all currently defensible rows are used; no synthetic fixture
is treated as trading evidence.

## 3. TS forensic diagnosis

Among the observed cohorts, low-extension returned 1.863%/4.698% at 5d/10d (N=13/11), versus high-extension at 0.276%/0.389% (N=446/312). Detailed component/decile/cohort evidence is in `ts_forensics.csv`.
All major TS inputs have negative within-run rank correlations at 5d and 10d; Momentum
Quality is -0.140/-0.217 and final TS is -0.151/-0.231. The evidence supports the
strong-but-late hypothesis, but the favorable low-extension high-TS cohort has only 22
rows and 13/11 outcomes. It is plausible, not proven across independent dates.

## 4. EQ forensic diagnosis

Among the observed cohorts, EQ < 5 returned 0.264%/0.947% at 5d/10d (N=179/140), versus EQ >= 7 at 0.089%/1.663% (N=344/231). EQ is still the leading v5 research signal, principally as a possible
timing selector rather than a description of chart strength. **EQ does not materially
improve 5-day timing among TS >= 8 rows; it shows tentative 10-day improvement only.**
Trigger Quality has the strongest 10-day rank correlation among EQ internals (0.139),
while Stop Geometry is 0.103; RR Quality is negative (-0.070). G5 remains insufficient.

Confidence adjustment remains unproven: raw/adjusted top-20 returned 0.476%/0.461% at
5d and 2.483%/2.601% at 10d. A minimum-confidence gate was worse (0.106%/1.579%).

## 5. Trigger findings

Among the observed cohorts, at-trigger returned -1.120%/1.076% at 5d/10d (N=72/46), versus freshly-triggered at 2.144%/5.064% (N=503/333). The discrepancy persists, so trigger bands remain frozen.

## 6. Setup findings

Current type-specific, old-max and hybrid scores were compared within each setup family.
No pooled in-sample winner is promoted. Momentum continuation remains a targeted defect
hypothesis, with late-entry/extension/trigger/Stage/volume/regime/EQ slices exported.
Its extended-beyond-trigger rows returned -2.300%/-1.423% at 5d/10d; the P80-P90
extension cohort returned -2.271%/-2.327%. The hybrid worsened its within-family top-20
to -2.449%/-3.281%, so the hybrid is not a rescue candidate.

## 7. Sector RS findings

Broad-only, current broad+sector, sector-only and isolated sector contribution are
reported. Missingness is audited by country, exchange, sector label, setup, liquidity
and regime. Mapping is unresolved for 48.75%; moreover, ETF bar history is
absent for the mapped 51.25%, so sector-only predictive value is not
testable. The named `V5_SECTOR_DATA_FIX` removes the erroneous missing-data penalty and
changes TCS by 0.045 points on average. Sector RS remains unproven.

## 8. Danger label and cap findings

Label validity is separated from cap calibration. Current, half-strength, label-only
and no-cap variants were evaluated with TS/SQ/regime/sector/setup matched controls.
Distribution risk outperformed matched controls (1.702%/3.576% vs 1.049%/2.918%);
failed breakout was mixed (1.363%/1.968% vs 0.885%/2.308%); climax has only 15 rows.
The current cap admitted 7 distribution-risk rows into run-relative top 20%, versus 49
with half caps and 129 label-only/no-cap. Blowoff and late-stage extension have zero
rows. The caps move ranks, but adverse label separation is not validated, so no cap
change is justified.

## 9. Classification/action transitions

Classification changed 61.73%; action wording changed
97.95%; canonical decision buckets changed 25.89%.
This proves the original ~98% figure contains terminology churn but is not merely naming.

## 10. Updated G8 consumer audit

Persistence, distinct v5 exports/UI and opaque-score consumers are safe. Ranking-profile
components, Setup Lifecycle/alerts, Winner Evidence/cohort features and SLSE v4-era
contracts still block activation. No ambiguous consumer was silently upgraded.

## 11. Candidate architecture comparison

Candidate definitions were fixed before evaluation: V4, V5_BASELINE, fixed EQ-heavy
(0.25/0.30/0.45), TS gates 6.0/6.5/7.0 ranked by 0.40 SQ + 0.60 EQ, and a two-stage
TS/confidence/regime filter followed by the same timing rank. `V5_SECTOR_DATA_FIX` is a
named correctness sensitivity, not a tuned model candidate.

Holdout top-20 results (two dates only):

| candidate | N | mean_return_5d | mean_return_10d | spearman_10d |
|---|---|---|---|---|
| V4 | 176 | 2.064 | 2.476 | -0.035 |
| V5_BASELINE | 176 | 1.789 | 2.006 | -0.043 |
| V5_EQ_HEAVY | 176 | 1.802 | 2.074 | -0.047 |
| V5_SECTOR_DATA_FIX | 176 | 1.774 | 2.126 | -0.031 |
| V5_STRENGTH_GATE_6.0 | 139 | 1.436 | 2.042 | 0.039 |
| V5_STRENGTH_GATE_6.5 | 114 | 1.254 | 1.881 | 0.062 |
| V5_STRENGTH_GATE_7.0 | 93 | 1.373 | 1.510 | 0.031 |
| V5_TWO_STAGE | 72 | 0.877 | 2.099 | 0.113 |

## 12. Walk-forward and holdout

The outcome-eligible split is chronological: 3 calibration dates,
1 validation date(s) and 2 final holdout dates. The four
dates without defensible 10-day outcomes are excluded from candidate scoring, not
imputed. No random final split was used. Candidate definitions were not selected
from holdout performance, and no in-sample result is promoted.

## 13. Statistical uncertainty

Component/cohort tables include decision-date-cluster bootstrap intervals. Ten dates are
too few for stable market-cycle inference; intervals and absent cohorts are reported as
limitations rather than filled or pooled away.

## 14. Code defects fixed

The historical reconstruction failed to mark a mapped sector benchmark as data-missing
when the ETF series was wholly absent. This caused missing sector evidence to be scored
as weak and added a phantom 0.7 risk point. The reconstruction now fails over to broad
market RS and has regression coverage. The original scores remain named `V5_BASELINE`;
the corrected sensitivity is `V5_SECTOR_DATA_FIX`. Production defaults/configuration
were not modified.

## 15. Exact test results

| Verification lane | Result |
|---|---|
| V5/technical scoring/calibration/forensics | 123 passed |
| Winner Probability + Setup Lifecycle/alerts | 447 passed |
| Full repository suite | 1,850 passed, 9 skipped, 13 warnings |
| Ruff on changed Python files | PASS |
| `git diff --check` | PASS |
| Alembic heads | one head: `0053_split_artifact_identity` |

The full-suite warnings are dependency/configuration deprecations (11 SQLAlchemy
datetime-adapter warnings and two Alembic `path_separator` warnings). No test was
weakened or skipped by this change.

## 16. Activation gates

| Gate | Status | Evidence |
|---|---|---|
| G1 Correctness | PASS | PIT enrichment; missing-sector reconstruction defect fixed and tested |
| G2 Coverage | FAIL | 10 independent dates; target is 50/80+ |
| G3 Ranking value | FAIL | baseline v5 did not beat v4 in the certified campaign |
| G4 Danger validation | FAIL | labels/caps do not show stable adverse separation |
| G5 EQ validation | INSUFFICIENT | 10d separation is promising but 5d evidence is mixed |
| G6 Robustness | FAIL | short regime window and heterogeneous setup/sector slices |
| G7 Out-of-sample | INSUFFICIENT | holdout contains only two decision dates |
| G8 Consumer safety | FAIL | ranking, lifecycle/alerts, Winner Evidence and SLSE block |

## 17. Final recommendation

**CONTINUE SHADOW**

Continue collecting independent dates with the frozen baseline. Revisit only the small
named architecture set after materially broader later-period coverage; do not enable v5.
