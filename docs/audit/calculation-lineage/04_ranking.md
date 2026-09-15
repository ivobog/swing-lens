# SwingLens Combined Scoring and Ranking Profiles — Calculation Lineage Audit

Task: 04 — Combined Scoring and Ranking Profiles
Verification state: repository/static analysis, focused tests, and isolated in-memory calculation probes; no provider calls, production jobs, migrations, or database writes
Evidence vocabulary: `VERIFIED`, `PARTIALLY_VERIFIED`, `INFERRED`, `UNKNOWN`, `CONTRADICTORY`, `LEGACY`, `DEAD_CODE`

## Repository identity and implementation baseline

| Item | Value | Status |
|---|---|---|
| Repository path | `C:\Users\Ivica\Documents\SwingLens` | VERIFIED |
| Current branch | `codex/winner-evidence-remediation` | VERIFIED |
| Current repository HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Implementation baseline SHA | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Application implementation drifted from baseline? | **No.** `HEAD` equals the baseline; the commit range and tracked implementation diff are empty. | VERIFIED |
| Exact implementation-changing commits since baseline | None | VERIFIED |
| Exact implementation-changing files since baseline | None | VERIFIED |
| `BASELINE_DRIFT` | **Not flagged.** No calculation or orchestration conclusion relies on superseded implementation. | VERIFIED |
| Worktree at audit start | Untracked Task 01–03 reports, `scripts/ops/ib_historical_probe.py`, and `tests/ops/test_ib_historical_probe.py`; no tracked modifications. The probe files pre-existed this audit and are not authoritative committed implementation. | VERIFIED |
| Change made by Task 04 | This file only: `docs/audit/calculation-lineage/04_ranking.md` | VERIFIED |
| Relevant repository migration head | `0074_ceri_evidence_quarantine` | VERIFIED |
| Live database migration revision | Not queried because a safe live-database context was not established. | UNKNOWN |
| Python runtime | CPython 3.12.2 from `.venv` | VERIFIED |
| Verification date | 2026-09-14 (Europe/Zurich) | VERIFIED |

Documentation-only work under `docs/audit/calculation-lineage/` is excluded from implementation drift by the supplied baseline rule. **VERIFIED**

## Scope and method

This report audits the legacy combined decision, five configured ranking profiles, their actual input readers, formulas, readiness behavior, market/regime dependencies, optional IB liquidity overlay, persistence, publication, recalculation, and downstream dependency direction. It explicitly tests all cross-task requirements supplied after Tasks 01–03. **VERIFIED**

The audit does not assume that `CombinedResult` feeds `RankingResult`: code establishes that both independently read raw, fundamental, and technical rows. It also does not assume that CERI, market-regime snapshots, or sector-rotation snapshots are ranking inputs merely because they appear in broader architecture diagrams. **VERIFIED**

## Executive conclusions

1. Canonical execution is technical -> market-regime snapshot -> combined results -> ranking profiles -> sector rotation -> CERI -> setup/lifecycle -> Winner. This ordering is implemented directly in `pipeline_executor.py:558-665`. **VERIFIED**
2. Neither combined scoring nor profile ranking queries any CERI artifact. `opportunity_score`, `event_risk_score`, CERI confidence, posture, and alerts are each `NOT_USED`; no same-run, prior-run, latest-ticker, or global CERI fallback exists. Current-run CERI is produced later, so no backwards dependency needs explaining. **VERIFIED**
3. Sector rotation does not feed same-run combined scoring or ranking. The direction is the reverse: sector rotation runs after ranking and consumes same-run ranking/combined results. Technical sector-relative-strength inputs are benchmark-price features, not `SectorRotationSnapshot` values. **VERIFIED**
4. Combined scoring treats every non-null `TechnicalScore.dual_score` as numerically valid regardless of `insufficient_data` or low/error confidence. An isolated low-confidence/insufficient probe produced combined score 7.7 and decision `Candidate`. CORE-009 therefore propagates into the combined final rank. **VERIFIED — CONTRACT VIOLATION; RANK-001**
5. Ranking profiles inspect readiness only after calculating a positive profile score. The gate caps the label to `Low confidence`, but does not null the score or exclude the row. An insufficient row scored 7.747, was marked complete, and remained in warning sort bucket 1. A technical error row with null `dual_score` still produced a technical profile score and `has_technical=true`. **VERIFIED — CONTRACT VIOLATION; RANK-002**
6. The persisted command-center `MarketRegimeSnapshot` is not read. Its risk state, gate, position-size multiplier, allowed profiles/setups, confidence, warnings, and source sessions do not control ranking. Sparse-regime defects can still propagate through the earlier technical engine's internal regime classification and `dual_score`; the command-center Gray/staleness policy cannot restrain ranking. **VERIFIED — CONTRACT GAP; RANK-008**
7. Same `run_id` is the principal compatibility rule for raw/fundamental/technical inputs. Ranking does not validate or persist pipeline ID, market calculation context, session, cutoff, calendar, input config/model versions, snapshot revision, or generation. **VERIFIED — CONTRACT VIOLATION; RANK-004**
8. Three profiles optionally consume the latest global per-ticker IB liquidity feature bounded by upload `processed_at`, not the frozen market calculation cutoff. Selection ignores run/context/version compatibility, and ranking provenance omits the feature ID and temporal/version fields. **VERIFIED — CONTRACT VIOLATION; RANK-003**
9. Combined results retain a full combined-scoring config snapshot/hash and input row IDs, but are delete-and-replaced. Ranking rows retain only a partial profile description and engine string, no profile config hash/version, and no fundamental/technical/liquidity input IDs. Ranking refresh updates the existing row in place. **VERIFIED — CONTRACT VIOLATION; RANK-005 and RANK-006**
10. Both calculators derive earnings risk from the run's temporally unanchored uploaded earnings date and, unless a test/explicit caller supplies `today`, the current Zurich wall-clock date. Historical recalculation can therefore change score, gate, and label solely because time passed. **VERIFIED — CONTRACT VIOLATION; RANK-007**

## Actual dependency direction

```text
RawCompanyRow (same upload run)
  ├─> FundamentalScore (same run; no market-session provenance)
  └─> TechnicalScore (same run; market-context fields exist)

RawCompanyRow + FundamentalScore + TechnicalScore
  ├─> CombinedResult
  └─> RankingResult × five profiles
         └─ optional latest global IB LIQUIDITY feature by ticker

CombinedResult + RankingResult + FundamentalScore + TechnicalScore
  └─> same-run SectorRotationSnapshot (later stage)

later: CERI -> setup/lifecycle -> Winner
```

`RankingResult` does not consume `CombinedResult`; it independently recomputes a profile-specific blend. Consequently, combined and profile scores are sibling outputs with different weights, technical constructions, penalties, and sort semantics. **VERIFIED**

## Cross-task dependency and compatibility checks

### 1. Ranking versus CERI execution order

| Question | Combined result | Ranking profiles | Status |
|---|---|---|---|
| Uses CERI `opportunity_score` | No | No | VERIFIED |
| Uses CERI `event_risk_score` | No | No | VERIFIED |
| Uses CERI confidence | No | No | VERIFIED |
| Uses CERI posture | No | No | VERIFIED |
| Uses CERI alerts/changes/features | No | No | VERIFIED |
| Selects same-run CERI | No selection | No selection | VERIFIED |
| Selects prior/current/global CERI | No selection/fallback | No selection/fallback | VERIFIED |
| CERI compatibility validated | NOT_APPLICABLE | NOT_APPLICABLE | VERIFIED |

A repository-wide search of combined/ranking code and configuration found no CERI model, service, field, query, or profile component. A search of CERI services likewise found no `CombinedResult` or `RankingResult` dependency. The architecture is not circular. **VERIFIED**

When provider ingestion is enabled, the parent pipeline schedules CERI work after sector rotation and the eventual capture completes asynchronously. When direct run capture is enabled without provider ingest, capture still occurs after sector rotation. Neither path can supply current-run CERI to the already-persisted ranking, and the ranking implementation makes no attempt to do so. **VERIFIED**

### 2. Technical readiness consumer contract

| Behavior | Combined result | Every ranking profile | Status |
|---|---|---|---|
| Checks `insufficient_data` before numeric score | No | No; checked only by post-score label gate | VERIFIED |
| Checks `technical_confidence` before numeric score | No | No; low/error checked after score/penalties | VERIFIED |
| Rejects/suppresses insufficient row | No | No | VERIFIED |
| Treats non-null `dual_score` as valid | Yes | `base_technical_score` retained; profile score instead uses extracted components | VERIFIED |
| Treats missing technical features as zero | Combined does not inspect features | Yes, most numeric features default to zero | VERIFIED |
| Special missing defaults | Not applicable | Risk defaults to 5 before inversion; missing/unknown pullback health defaults to 5 | VERIFIED |
| Includes low-confidence row in ranking order | Yes, with ordinary decision bucket | Yes, with warning bucket when numerically complete | VERIFIED |
| Includes row in cross-sectional normalization | No normalization is performed | No normalization; all rows participate in ordering/tie-breaking | VERIFIED |

Combined warnings correctly include `insufficient_history` and `low_technical_confidence`, but `is_complete` is based only on the presence of fundamental and `dual_score`, and the warnings do not change the decision or sort bucket. **VERIFIED**

Profile extraction returns a nonempty component dictionary for any `TechnicalScore` object. Missing values become zeros/defaults, so `technical_profile_score` is non-null even for the standard technical error row. Profile `is_complete` and `has_technical` use that synthetic score rather than `dual_score` or readiness. The low-data-quality gate changes only the label. **VERIFIED**

Isolated probe results:

| Input | Combined/profile result | Status |
|---|---|---|
| Fundamental 8; technical components/dual 8; `technical_confidence=low`; `insufficient_data=true` | Combined 7.7, `Candidate`, bucket 20; warnings retained | VERIFIED |
| Same input, `momentum_swing` | Profile 7.747, `Low confidence`, `is_complete=true`, bucket 1 | VERIFIED |
| Fundamental 8; technical error object; null `dual_score` | All profiles produced technical scores 0.5–2.95 and persisted `has_technical=true`, `is_complete=true`; decisions were Avoid | VERIFIED |

CORE-009 can therefore affect combined final ranks, every profile's numeric score/rank, and downstream sector averages. The profile gate reduces actionability but does not remove numerical contamination. **VERIFIED — RANK-001/RANK-002**

### 3. Market-regime consumer contract

| Field | Combined consumes? | Ranking consumes? | Exact source/effect | Status |
|---|---|---|---|---|
| Command-center regime label | No | No | No `MarketRegimeSnapshot` read | VERIFIED |
| Command-center numeric score | No direct read | No direct read | No snapshot read | VERIFIED |
| Risk state / Gray | No | No | Display/policy state is ignored | VERIFIED |
| `gate_ok` | No snapshot gate | No snapshot gate | Earlier technical engine has a separate internal market gate baked into score/classification | VERIFIED |
| Position-size multiplier | No | No | Combined/profile position hints use decision and technical risk/classification | VERIFIED |
| Allowed profiles | No | No | All five enabled YAML profiles always execute | VERIFIED |
| Allowed setups | No | No | No ranking filter | VERIFIED |
| Minimum score adjustment | No | No | No command-center policy adjustment | VERIFIED |
| Snapshot confidence | No | No | No ranking effect | VERIFIED |
| Snapshot warnings/staleness | No | No | Gray/severe-stale policy cannot cap ranking | VERIFIED |
| Snapshot/source sessions | No | No | Not validated or persisted | VERIFIED |
| `TechnicalScore.dual_score` with embedded regime | Yes, 45% before penalties | No direct profile blend; classification/gates can reflect it | V4 active default incorporates its internal regime score and weight family | VERIFIED |
| `TechnicalScore.market_score` | Through active technical score construction, not direct | Defensive Quality uses it directly at 15% | This is legacy/base technical market score, not command-center snapshot score | VERIFIED |

Technical scoring and the later command-center snapshot both use bounded SPY/QQQ feature inputs and call related/same regime logic, but they are separately materialized and not identity-linked by ranking. Sparse benchmark input can therefore create a bullish internal technical regime and lift/categorize technical outputs before ranking. The command-center's later freshness policy and Gray risk state are never consumed. **VERIFIED**

Benchmark source sessions may differ. `TechnicalScore` carries a declared `input_as_of_session`, context ID, cutoff, and calendar, while detailed source-latest sessions remain in debug lineage; combined/ranking do not read or copy those fields. **VERIFIED**

### 4. Sector-rotation dependency direction

No combined or ranking module imports or queries `SectorRotationSnapshot` or its rows. No profile YAML references sector-rotation state, rank, score, permission, or multiplier. **VERIFIED**

The sector-rotation stage runs after rankings and `SectorUniverseService` reads same-run `RankingResult`, `CombinedResult`, `FundamentalScore`, and `TechnicalScore`. If the configured default profile is absent, sector top-count logic can fall back to combined rank. This is a ranking-to-sector dependency, not a sector-to-ranking dependency. **VERIFIED**

Technical scoring may consume a configured sector benchmark price to build relative-strength features; that market series is not a prior/global sector-rotation snapshot. No earlier/global `SectorRotationSnapshot` is used by ranking. **VERIFIED**

Sector rotation can influence later setup/lifecycle and Winner consumers, but cannot alter the same run's already-created ranking absent an explicit later ranking refresh—and ranking refresh still does not read sector rotation. **VERIFIED**

### 5. Fundamental temporal weakness

Combined debug records the selected `FundamentalScore.id`, copied score/label, and the combined calculation's own config hash/snapshot. It does not copy the fundamental scoring model version or fundamental config hash. The ID is JSON, not a foreign key. **VERIFIED**

Ranking records only copied fundamental score/label and has no `fundamental_score_id`, scoring-model version, fundamental config hash, source row IDs beyond `raw_row_id`, or field-level evidence identity. **VERIFIED**

Fundamentals are selected by same `run_id` and ticker. They have no market calculation context, as-of session, cutoff, calendar, or source effective-time contract. Combined/ranking therefore can mix a session-bound historical technical row with a temporally unanchored fundamental score. No report field invents a fundamental session identity. **VERIFIED**

Fundamental recalculation deletes and recreates same-run `FundamentalScore` rows from raw JSON using current code/config, then refreshes combined results. It does not refresh ranking profiles. Existing rankings can silently become inconsistent with the now-current same-run fundamentals; a later ranking refresh overwrites them under the same ranking identity. **VERIFIED — RANK-006**

### 6. Input compatibility envelope

The required classification vocabulary below is literal: `DATABASE_ENFORCED`, `CODE_ENFORCED`, `VALIDATED_AT_RUNTIME`, `ASSUMED`, `NOT_ENFORCED`, `NOT_APPLICABLE`, `UNKNOWN`.

| Dimension | CombinedResult enforcement | RankingResult enforcement | Evidence and consequence | Status |
|---|---|---|---|---|
| `run_id` | CODE_ENFORCED; output FK DATABASE_ENFORCED | CODE_ENFORCED; output FK DATABASE_ENFORCED | Readers filter raw/fundamental/technical by run; output belongs to run | VERIFIED |
| `pipeline_id` | NOT_ENFORCED | NOT_ENFORCED | Neither output stores pipeline; multiple pipeline attempts for one upload collapse together | VERIFIED |
| `market_calculation_context_id` | NOT_ENFORCED | NOT_ENFORCED | Technical has it, consumers do not validate/copy it | VERIFIED |
| `as_of_session` | NOT_ENFORCED | NOT_ENFORCED | Technical has it; fundamental does not; outputs omit it | VERIFIED |
| `calculation_cutoff` | NOT_ENFORCED | NOT_ENFORCED | Technical has it; ranking liquidity uses upload `processed_at` instead | VERIFIED |
| `calendar_version` | NOT_ENFORCED | NOT_ENFORCED | Technical has it; outputs omit it | VERIFIED |
| Combined/profile configuration hash/version | Combined config hash/snapshot CODE_ENFORCED for its own formula | NOT_ENFORCED | Ranking has no config hash/version and partial debug snapshot | VERIFIED |
| Fundamental configuration/model version | NOT_ENFORCED | NOT_ENFORCED | Not checked/copied | VERIFIED |
| Technical configuration/model version | NOT_ENFORCED | NOT_ENFORCED | Engine/version fields exist upstream but are not compatibility gates | VERIFIED |
| Liquidity configuration/calculation/source version | NOT_APPLICABLE | NOT_ENFORCED | Global feature reader ignores version fields | VERIFIED |
| Snapshot revision | NOT_APPLICABLE | NOT_APPLICABLE | Inputs are replaceable score rows, not immutable snapshots | VERIFIED |
| Generation/pipeline attempt | NOT_ENFORCED | NOT_ENFORCED | No generation field; same output key is replaced/updated | VERIFIED |
| Ticker identity | CODE_ENFORCED; output uniqueness DATABASE_ENFORCED | CODE_ENFORCED; output uniqueness DATABASE_ENFORCED | Uppercase dictionary lookup; `(run,ticker)` or `(run,profile,ticker)` unique | VERIFIED |

Shared `run_id` does not prove temporal or version compatibility. The code assumes that pipeline order created one coherent current set for the run, but standalone refreshes and repeat pipeline attempts invalidate that assumption. **VERIFIED — RANK-004**

### 7. Implicit fallback search

| Fallback | Artifact selected | Cross-run/session/version possibility | Persisted provenance | Distinguishable? | Status |
|---|---|---|---|---|---|
| Latest liquidity by ticker | First `IBIntelligenceFeature` ordered session/calculated/id descending, module LIQUIDITY, bounded by upload `processed_at`/date | Yes; global table, any intelligence run/config/calculation/context | Only coverage/classification/derived grade copied; no ID/session/version | No | VERIFIED — contamination risk |
| Missing `UploadRun.processed_at` | Current UTC timestamp | Can admit evidence created long after upload/run intent | Not identified as fallback | No | VERIFIED — contamination risk |
| Technical explainability | Prefer `v4_debug_json`, fall back to nested `debug_json.explainability` | Same row, but may represent legacy schema/version | Result components only; fallback choice not explicit | Partially | VERIFIED |
| Missing technical component | Numeric zero; risk defaults 5; pullback defaults 5 | Not cross-run | Derived component values persisted, missing decision not itemized | Partially | VERIFIED |
| Missing entire component family | Available fundamental/technical weight is rescaled when enabled | Not cross-run | Output and applied missing penalty persist | Yes for whole-family absence | VERIFIED |
| Specific technical penalty absent | Falls back to broader danger/distribution penalty key | Current profile config | Applied penalty persists, resolution path not fully | Partially | VERIFIED |
| Earnings calculation date | Current Zurich local date unless caller supplies `today` | Wall-clock may be later than historical run/session | Resulting date delta/risk persists; calculation date itself absent | No | VERIFIED — contamination risk |
| Fundamental/technical row missing | No prior/latest fallback; component absent | No | Missing flags/penalty persist | Yes | VERIFIED |
| CERI/sector/regime snapshot missing | No fallback because they are never queried | NOT_APPLICABLE | NOT_APPLICABLE | Yes from code, not row | VERIFIED |

The global liquidity fallback applies only to `momentum_swing`, `early_rocket`, and `defensive_quality`, whose tradeability overlays are enabled. It applies a penalty only when feature coverage is `AVAILABLE` and the grade is poor/very poor or dollar volume falls below the profile floor. **VERIFIED**

### 8. Persisted ranking provenance

| Recoverability requirement | CombinedResult | RankingResult | Status |
|---|---|---|---|
| Exact component score rows | Fundamental/technical/raw IDs in debug, but no FKs and rows are replaceable | Raw row FK only; no fundamental/technical/liquidity IDs | PARTIALLY_VERIFIED / NOT_ENFORCED |
| Input sessions | Technical source could be followed while row survives; not copied | Not stored | NOT_ENFORCED |
| Input cutoffs | Same limitation | Not stored | NOT_ENFORCED |
| Profile/config version | Full `scoring_weights.yaml` snapshot + SHA-256 | No version/hash; partial profile debug only | VERIFIED / NOT_ENFORCED |
| Profile weights | Stored | Technical/fundamental and technical-component weights stored | VERIFIED |
| Thresholds | Stored in combined config snapshot | Omitted from ranking debug | NOT_ENFORCED for ranking |
| Penalty/gate definitions | Stored in combined config snapshot | Only applied penalty/gate results stored; full definitions omitted | PARTIALLY_VERIFIED |
| Calculation version | `combined-decision-1.0.0` column/debug | `ranking_engine_version=1.1.0` in debug only | VERIFIED / PARTIALLY_VERIFIED |
| Upstream model versions | Omitted | Omitted | NOT_ENFORCED |
| Source snapshot revisions | Inputs are mutable rows; no revision/generation | Same | NOT_APPLICABLE / NOT_ENFORCED |
| Code/deployment identity | Omitted | Omitted | NOT_ENFORCED |
| Eligibility decision | Complete/warnings/decision and earnings gate output | Complete/warnings/decision/applied gates | PARTIALLY_VERIFIED |
| Normalization population | No cross-sectional normalization; input universe not hashed | No normalization; rank population membership not frozen/hashed | NOT_ENFORCED |
| Missing-data decisions | Whole-family missing and warnings retained | Whole-family penalty and outputs retained; per-component zero-fill causes are not | PARTIALLY_VERIFIED |
| Publication state | No explicit state | No explicit state; query/export exposes persisted row immediately | NOT_ENFORCED |

A ranking score alone is not treated as provenance in this report. The ranking row is insufficient to reproduce the historical calculation from immutable inputs and frozen rules. **VERIFIED — RANK-005**

### 9. CERI terminology

No generic numeric “CERI score” is used. The implementation-defined CERI outputs are evaluated independently:

- `opportunity_score`: NOT_USED by combined/ranking. **VERIFIED**
- `event_risk_score`: NOT_USED by combined/ranking. **VERIFIED**
- CERI confidence score/label: NOT_USED by combined/ranking. **VERIFIED**
- CERI posture: NOT_USED by combined/ranking. **VERIFIED**
- CERI alerts/change state: NOT_USED by combined/ranking. **VERIFIED**

The combined/ranking earnings-risk gate is separate from CERI `event_risk_score`; it uses uploaded `RawCompanyRow.upcoming_earnings_date` and calendar-day thresholds. **VERIFIED**

### 10. Cross-task invariants

| Invariant | Result | Evidence | Status |
|---|---|---|---|
| Same-run ranking cannot silently consume decision evidence produced after ranking | Pass for CERI/sector/setup/lifecycle; no such reads | Static imports/queries plus pipeline order | VERIFIED |
| Inputs must be temporally/version compatible, not merely share ticker/run | Fail | Only run/ticker enforced for core rows; liquidity is global | VERIFIED — RANK-003/RANK-004 |
| Low-confidence/insufficient upstream calculations need explicit consumer policy | Partial fail | Profile label cap exists, but numeric score/rank persists; combined only warns | VERIFIED — RANK-001/RANK-002 |
| Other-run/global fallback must not masquerade as same-run provenance | Fail | Liquidity latest-by-ticker fallback has no persisted artifact identity | VERIFIED — RANK-003 |
| Exact profile config must be recoverable | Fail | No hash/version; incomplete debug snapshot | VERIFIED — RANK-005 |
| Upstream recalculation must not silently change historical ranking meaning | Fail | Upstream rows replace; ranking stays stale, then refresh overwrites in place | VERIFIED — RANK-006 |

## Combined scoring

### Inputs and formula

`refresh_combined_results(run_id)` loads unique raw rows in source-row order and creates ticker dictionaries from same-run `FundamentalScore` and `TechnicalScore`. It does not read ranking, CERI, command-center regime, sector rotation, setup, lifecycle, or IB intelligence. **VERIFIED**

The pre-penalty score is the available-weight average of fundamental score (0.55) and technical `dual_score` (0.45). If one is absent, its weight is removed and the present score is rescaled to 100%; a 1.0 missing-data penalty is then applied. With neither, base score is zero. **VERIFIED**

Penalties are danger classification 3.0, overheated momentum 1.5, value trap 2.0, growth trap 1.5, quality risk 1.5, missing data 1.0, and liquidity warning 1.0. Uploaded earnings proximity adds 3/2/1 at <=2/<=5/<=10 calendar days and 0.3 for a missing/unparseable date; score clamps to 0–10. **VERIFIED**

Decision thresholds are Strong candidate >=8.0, Candidate >=6.8, Watchlist >=5.5, else Avoid. Both component families must be non-null or the label becomes `Incomplete data`; danger and value-trap labels force Avoid; growth/quality risk caps Candidate; a blocked earnings window overrides with `Blocked by earnings gate`. **VERIFIED**

Combined final ordering is sort bucket, descending final score, ticker. Buckets are Strong 10, Candidate 20, Watchlist 30, Avoid/earnings-blocked 40, incomplete 50, fallback 60. Technical readiness warnings do not affect these buckets. **VERIFIED**

### Persistence and recalculation

`CombinedResult` is unique on `(run_id,ticker)`. Refresh first nulls Winner snapshot foreign keys, deletes every combined row for the run, and inserts new rows/ranks. There is no revision/generation or publication state. **VERIFIED**

The row stores calculation version, combined config hash, full config snapshot, applied inputs/weights/thresholds/penalties, earnings result, final result, and raw/fundamental/technical source IDs. It does not store pipeline/context/session/cutoff/calendar or input model/config versions. **VERIFIED**

## Ranking profile calculation

### Shared algorithm

For every enabled profile and every unique raw ticker, the engine extracts technical components, computes `technical_profile_score = clamp(sum(component × configured_weight))`, blends it with the fundamental score using profile technical/fundamental weights, subtracts profile/earnings/tradeability penalties, clamps 0–10, maps thresholds, applies gates, and sorts. No ticker is excluded from output. **VERIFIED**

If a whole technical or fundamental family is absent and `rescale_available=true`, the available family is rescaled to full weight before one missing-data penalty is subtracted. If both are absent, score starts at zero. Multiple missing families share one maximum missing penalty rather than accumulating two. **VERIFIED**

Technical component extraction uses these material formulas:

- Momentum strength = 55% momentum + 30% combined relative strength + 15% RS-new-high Boolean. **VERIFIED**
- Momentum health = 30% VCP + 15% box tightness + 20% pullback health + 15% volume dry-up + 20% absence of distribution. **VERIFIED**
- Setup quality is the maximum of setup, VCP, and breakout quality; breakout-or-VCP is their relevant maximum. **VERIFIED**
- Risk control = `10 - max(technical risk, climax risk)`; missing technical risk defaults to 5. **VERIFIED**
- Trend repair = 35% classification Boolean + 35% trend + 20% momentum strength + 10% relative strength. **VERIFIED**
- RS acceleration and volume expansion are weighted Boolean summaries; missing flags become zero. **VERIFIED**
- Pullback health maps Healthy 10, Mixed 5.5, Dangerous 0, and missing/unknown 5. **VERIFIED**
- Market-regime alignment is `TechnicalScore.market_score`, not `MarketRegimeSnapshot.score`. **VERIFIED**

Shared gates block earnings, cap danger classifications, apply optional fundamental floors, cap fundamental risk labels, optionally cap liquidity warnings, and cap missing/low/error/insufficient technical data to `Low confidence`. Gates do not change `profile_score`. **VERIFIED**

Shared sort order is bucket, descending profile score, descending fundamental score, descending technical profile score, then ticker. Clean rows are bucket 0, warning rows 1, incomplete rows 2, and Avoid/blocked rows 3. Because low-confidence numeric rows are considered complete, they can sort ahead of genuinely incomplete and Avoid rows. **VERIFIED**

### Profile register

| Profile | Purpose/target | Fundamental / technical | Technical components | Thresholds strong/candidate/watch | Special gates/overlays | Status |
|---|---|---:|---|---|---|---|
| Momentum Swing | Technical-led swing with fundamental confirmation | 0.45 / 0.55 | momentum strength .30; health .25; trend .15; setup .10; breakout/VCP .10; risk .10 | 8.0 / 6.8 / 5.5 | Fundamental floor 3 -> Speculative watch; liquidity floor $5m, penalties .5/1 | VERIFIED |
| Quality Momentum | Balanced strong chart/business | 0.55 / 0.45 | momentum strength .25; health .25; trend .20; setup .15; risk .15 | 8.0 / 6.8 / 5.5 | No tradeability overlay; no numeric fundamental floor | VERIFIED |
| Early Rocket | Aggressive emerging technical leader | 0.30 / 0.70 | momentum strength .35; repair .20; breakout .15; RS acceleration .10; volume expansion .10; risk .10 | 8.2 / 7.0 / 5.8 | Fundamental floor 2.5 -> Speculative watch; liquidity floor $3m, penalties .4/.8 | VERIFIED |
| Clean Compounder Pullback | Fundamental-led quality pullback | 0.65 / 0.35 | pullback health .30; trend .25; risk .20; momentum health .15; RS .10 | 8.0 / 6.9 / 5.7 | Fundamental floor 5.5 -> Watchlist; no tradeability overlay | VERIFIED |
| Defensive Quality | Risk-first trap avoidance | 0.60 / 0.40 | risk .35; trend .20; momentum health .15; setup .15; base technical market score .15 | 8.2 / 7.0 / 5.8 | Liquidity cap and $10m floor; penalties .75/1.5 | VERIFIED |

All profiles enable earnings block and danger-blocks-candidate. Profile-specific penalty tables additionally cover configured danger, overheated/climax/distribution, liquidity, missing data, earnings risk, and fundamental trap/dilution conditions where defined. **VERIFIED**

## Ranking Dependency Matrix

`GATE` means the input can determine completeness/decision actionability even though a numeric output may still exist. `NOT_USED*` notes an indirect technical-derived concept but no direct artifact read.

| Ranking output/profile | fundamentals | technical | CERI | regime | sector | setup | lifecycle | other |
|---|---|---|---|---|---|---|---|---|
| CombinedResult | GATE | GATE | NOT_USED | NOT_USED* | NOT_USED | NOT_USED* | NOT_USED | GATE: raw earnings date |
| Momentum Swing | GATE | GATE | NOT_USED | NOT_USED* | NOT_USED | NOT_USED* | NOT_USED | OPTIONAL: global liquidity; GATE: earnings |
| Quality Momentum | GATE | GATE | NOT_USED | NOT_USED* | NOT_USED | NOT_USED* | NOT_USED | GATE: earnings |
| Early Rocket | GATE | GATE | NOT_USED | NOT_USED* | NOT_USED | NOT_USED* | NOT_USED | OPTIONAL: global liquidity; GATE: earnings |
| Clean Compounder Pullback | GATE | GATE | NOT_USED | NOT_USED* | NOT_USED | NOT_USED* | NOT_USED | GATE: earnings |
| Defensive Quality | GATE | GATE | NOT_USED | NOT_USED* | NOT_USED | NOT_USED* | NOT_USED | OPTIONAL: global liquidity; GATE: earnings |

`NOT_USED*` for regime means no `MarketRegimeSnapshot` is read; market values are already embedded in `TechnicalScore`. `NOT_USED*` for setup means no setup/lifecycle artifact is read; some profiles use technical `setup_score`/setup-derived features. **VERIFIED**

## Ranking Parameter Register

| Parameter | Profile | Purpose | Value/default | Source | Versioned | Effect | Implementation | Status |
|---|---|---|---|---|---|---|---|---|
| Engine version | All profiles | Algorithm identity | 1.1.0 | Code constant | Partial: debug only | Identifies declared engine | `ranking_profile_engine.py:19,287` | VERIFIED |
| Profile weights | Momentum Swing | Family blend | T .55 / F .45 | YAML | No config version/hash; values copied | Weighted/rescaled blend | `ranking_profiles.yaml:6-8` | VERIFIED |
| Profile weights | Quality Momentum | Family blend | T .45 / F .55 | YAML | Same | Same | `ranking_profiles.yaml:52-54` | VERIFIED |
| Profile weights | Early Rocket | Family blend | T .70 / F .30 | YAML | Same | Same | `ranking_profiles.yaml:88-90` | VERIFIED |
| Profile weights | Clean Compounder | Family blend | T .35 / F .65 | YAML | Same | Same | `ranking_profiles.yaml:130-132` | VERIFIED |
| Profile weights | Defensive Quality | Family blend | T .40 / F .60 | YAML | Same | Same | `ranking_profiles.yaml:169-171` | VERIFIED |
| Technical components | Each profile | Profile-specific technical meaning | See profile register; each sums to 1 | YAML | Values copied, no hash | Produces technical profile score | `ranking_profile_components.py:13-114` | VERIFIED |
| Missing rescale | All | Use available family | true | YAML | Omitted from persisted debug | Removes absent family weight | `ranking_profile_engine.py:201-219` | VERIFIED |
| Missing penalty | MS/QM/ER/CC/DQ | Penalize absent family | 1/1/.8/1.2/1.5 | YAML | Applied result stored; definition omitted | Subtract once/max | `ranking_profile_penalties.py:274-287` | VERIFIED |
| Thresholds | MS/QM | Decision labels | 8 / 6.8 / 5.5 | YAML | Omitted from persisted debug | Strong/Candidate/Watch | `ranking_profiles.yaml:25-28,69-72` | VERIFIED |
| Thresholds | ER | Decision labels | 8.2 / 7 / 5.8 | YAML | Omitted | Same | `ranking_profiles.yaml:107-110` | VERIFIED |
| Thresholds | CC | Decision labels | 8 / 6.9 / 5.7 | YAML | Omitted | Same | `ranking_profiles.yaml:147-150` | VERIFIED |
| Thresholds | DQ | Decision labels | 8.2 / 7 / 5.8 | YAML | Omitted | Same | `ranking_profiles.yaml:187-190` | VERIFIED |
| Earnings windows | All + combined | Near-event gate | 2/5/10 calendar days | `scoring_weights.yaml` | Combined snapshots config; ranking omits | Block/penalize | `earnings_risk_service.py:35-103` | VERIFIED |
| Earnings unknown | All + combined | Missing schedule | warn; penalty .3 | scoring YAML | Same | Warning/penalty | `scoring_weights.yaml:29-42` | VERIFIED |
| Fundamental floor | MS/ER/CC | Prevent weak fundamentals | 3/2.5/5.5 | ranking YAML | Applied gate stored; definition partly | Caps decision | `ranking_profile_gates.py:81-97` | VERIFIED |
| Data quality gate | All | Low readiness handling | low/error/insufficient | Code | Engine version only | Caps label to Low confidence | `ranking_profile_gates.py:118-167` | VERIFIED |
| Liquidity floor | MS/ER/DQ | Tradeability | $5m/$3m/$10m | ranking YAML | Values copied | Penalty/cap | `ranking_profile_engine.py:327-354` | VERIFIED |
| Sort order | All profiles | Deterministic rank | bucket, score, F, T, ticker | Code | Engine version only | Assigns profile rank | `ranking_profile_engine.py:233-244` | VERIFIED |
| Combined weights | Combined | Generic blend | F .55 / T .45 | scoring YAML | Full snapshot/hash | Base combined score | `combined_decision.py:146-151` | VERIFIED |
| Combined calculation version | Combined | Formula identity | combined-decision-1.0.0 | Code | Column/debug | Partitions meaning, not generations | `combined_decision.py:63,384` | VERIFIED |

## Ranking Compatibility Matrix

| Input | Must match run | Must match pipeline | Must match session | Must match version | Enforced where | Risk |
|---|---|---|---|---|---|---|
| RawCompanyRow | Yes | No | No session exists | No | Query + FK/raw-row FK | Medium: earnings date temporally unanchored |
| FundamentalScore | Yes | No | No session exists | No | Query by run; ticker map | High: current recalculation can replace meaning |
| TechnicalScore | Yes | No | Not checked | Not checked | Query by run; ticker map | High: context/session/version may mismatch |
| Combined scoring config | Current file | No | No | Own hash/version stored | Runtime load + debug snapshot | Medium: row replaced on refresh |
| Ranking profile config | Current file | No | No | No hash/version | Runtime parser only | High: historical rules unrecoverable |
| Earnings gate config/date | Current file/current date | No | Not tied to market session | No ranking config hash | Runtime only; output effects copied | High: time-dependent recalculation |
| IB liquidity feature | No | No | Only `<= upload processed_at.date()` | No | Latest per ticker query | High: silent global fallback |
| CERI artifacts | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | Not consumed | None |
| MarketRegimeSnapshot | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | Not consumed | High policy-bypass implication |
| SectorRotationSnapshot | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | NOT_APPLICABLE | Not consumed | None; direction is downstream |

## Snapshot lineage, publication, and recalculation

There is no immutable ranking snapshot object. `RankingResult` is the persisted result and is unique on run/profile/ticker. It stores rank, scores, labels, warnings, applied penalties/gates, component scores, partial debug, earnings outputs, completeness, and timestamps. **VERIFIED**

It does not store pipeline ID, market context, session, cutoff, calendar, profile config version/hash, full profile definition, input score IDs, liquidity feature ID, source signatures, or generation. `raw_row_id` is the only component FK. **VERIFIED**

Ranking rows become immediately available through run/profile query, UI, API, and CSV export. There is no draft/published state or publication timestamp. CSV export omits debug, applied penalties/gates, config/engine identity, input IDs, and temporal lineage. **VERIFIED**

Refresh of one/all profiles loads current YAML and current same-run component rows, then copies every mutable calculated field into the existing ranking row. ID and created/updated fields are excluded from explicit copy; database `onupdate` may advance `updated_at`. No prior generation is retained. **VERIFIED**

Combined refresh is more destructive: it deletes and recreates run rows. Fundamental and technical refresh routes rebuild combined results but do not refresh rankings, enabling same-run disagreement until a separate ranking refresh. **VERIFIED**

## Significant findings

### RANK-001 — Combined score promotes insufficient technical values

- **ID:** RANK-001
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Combined scoring / technical readiness
- **Finding:** Combined scoring uses non-null `dual_score` without gating on `insufficient_data`, missing-history state, or low/error technical confidence.
- **Evidence:** `combined_decision.py:139-151,209-234`; `confidence_service.py:57-73`; isolated probe produced 7.7 and `Candidate` from an insufficient low-confidence technical row.
- **Why it matters:** Warning generation is not an eligibility policy.
- **Potential contamination/correctness effect:** CORE-009 positive sparse-history values can enter final score and receive Candidate/Strong rank buckets.
- **Existing guard:** Warning flags identify insufficient history and low confidence.
- **Missing guard:** Score suppression, explicit confidence-adjustment, or an actionability cap before decision/rank.
- **Recommended future remediation:** Define and persist a combined technical-readiness policy and exclude or cap invalid numeric values before blending.

### RANK-002 — Profile readiness gate preserves synthetic scores and false completeness

- **ID:** RANK-002
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Ranking profiles / technical components
- **Finding:** Component extraction zero/default-fills missing fields before readiness gating; low/error/insufficient rows retain scores/ranks, and even technical error rows can be marked complete/technical-present.
- **Evidence:** `ranking_profile_components.py:13-114,129-157`; `ranking_profile_engine.py:92-120,151-197`; `ranking_profile_gates.py:118-167`; isolated probes.
- **Why it matters:** `Low confidence` is only a label cap, not a numeric exclusion.
- **Potential contamination/correctness effect:** Sparse/error rows affect profile ranks and downstream sector averages; consumers may use profile score independently of label.
- **Existing guard:** Warning/low-data gate and sort buckets reduce actionability.
- **Missing guard:** Readiness-aware nullable technical profile score and truthful completeness semantics.
- **Recommended future remediation:** Reject component synthesis for unready rows or mark scores explicitly non-actionable and exclude them from ranking populations.

### RANK-003 — Global latest liquidity fallback masquerades as ranking input

- **ID:** RANK-003
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Ranking tradeability overlay
- **Finding:** Three profiles select the latest qualifying global LIQUIDITY feature by ticker using upload `processed_at`, with no run/context/version match and no persisted artifact identity.
- **Evidence:** `ranking_profile_service.py:199-227`; `ranking_profile_engine.py:304-314,327-354`.
- **Why it matters:** A shared/current artifact can silently influence an execution-owned ranking.
- **Potential contamination/correctness effect:** Penalty and rank may depend on another run's session, config, calculation, or input signature.
- **Existing guard:** `calculated_at` and `as_of_session` are upper-bounded; coverage must be AVAILABLE.
- **Missing guard:** Frozen market cutoff/context/version match and exact feature ID/session/version provenance.
- **Recommended future remediation:** Resolve liquidity through the run's calculation context and persist/validate the selected feature identity.

### RANK-004 — Run equality is treated as temporal compatibility

- **ID:** RANK-004
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Combined/ranking input envelope
- **Finding:** Consumers enforce run/ticker but ignore pipeline, context, session, cutoff, calendar, upstream version, and generation.
- **Evidence:** `combined_decision.py:92-124,401-418`; `ranking_profile_service.py:230-267`; output schemas.
- **Why it matters:** One upload can have multiple refreshes/pipeline attempts with independently replaced upstream rows.
- **Potential contamination/correctness effect:** A ranking can mix temporally/version-incompatible evidence while appearing same-run canonical.
- **Existing guard:** Database run foreign keys and per-run/ticker uniqueness.
- **Missing guard:** Compatibility validation against a frozen calculation-context/manifest and persisted input identities.
- **Recommended future remediation:** Require one calculation manifest/generation and validate every input against it before persistence.

### RANK-005 — Exact ranking profile configuration is unrecoverable

- **ID:** RANK-005
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Ranking provenance
- **Finding:** Ranking YAML has no version/hash, and persisted debug omits thresholds, missing-data policy, penalty definitions, gate definitions, and earnings config.
- **Evidence:** `ranking_profile_config.py:75-200`; `ranking_profile_engine.py:269-324`; `RankingResult` schema.
- **Why it matters:** Applied outputs cannot prove the complete rules that could have applied.
- **Potential contamination/correctness effect:** Editing YAML silently changes historical recalculation meaning under the same profile name/row identity.
- **Existing guard:** Engine string, family/component weights, tradeability settings, applied penalties/gates, and component outputs persist.
- **Missing guard:** Canonical full-config snapshot/hash/version bound to every row.
- **Recommended future remediation:** Version/hash the complete resolved profile plus shared earnings/scoring config and persist it immutably.

### RANK-006 — Recalculation overwrites ranking history and can leave stale same-run rows

- **ID:** RANK-006
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Recalculation / persistence
- **Finding:** Ranking refresh updates existing rows in place; upstream fundamental/technical refresh replaces inputs and combined rows without refreshing ranking.
- **Evidence:** `ranking_profile_service.py:117-162`; `run_routes.py:571-623`; fundamental/technical delete-recreate writers.
- **Why it matters:** Same run can expose mutually inconsistent current artifacts, and refresh destroys prior ranking meaning.
- **Potential contamination/correctness effect:** Historical audit cannot distinguish original decision-time rank, stale rank, or later recalculated rank.
- **Existing guard:** `updated_at` may change and result identity is deterministic.
- **Missing guard:** Immutable generations, dependency invalidation state, and explicit recalculation lineage.
- **Recommended future remediation:** Append ranking generations linked to immutable upstream IDs/config and mark supersession/staleness explicitly.

### RANK-007 — Earnings risk is wall-clock-relative and temporally unanchored

- **ID:** RANK-007
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Combined/profile earnings gate
- **Finding:** Default calculation uses current Europe/Zurich date and an uploaded earnings date without per-value publication/retrieval/effective provenance.
- **Evidence:** `combined_decision.py:482-496`; `earnings_risk_service.py:35-107`; raw-row lineage from Task 02.
- **Why it matters:** Historical recomputation is not anchored to the decision session.
- **Potential contamination/correctness effect:** The same run/profile/input rows can move from blocked to clear as wall-clock time passes, changing score and rank.
- **Existing guard:** Result stores earnings date, days, risk, warning, and applied penalty.
- **Missing guard:** Frozen calculation date/session and source-backed schedule revision.
- **Recommended future remediation:** Use the market calculation context's session and revisioned earnings schedule evidence; persist both identities.

### RANK-008 — Command-center regime policy does not govern ranking

- **ID:** RANK-008
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT GAP
- **Subsystem:** Market regime / ranking policy
- **Finding:** Ranking ignores the persisted regime's risk state, gate, permissions, size multiplier, confidence, warnings, and source-session lineage; all profiles execute regardless of `allowed_profiles`.
- **Evidence:** No market-regime imports/queries in combined/ranking; pipeline order; direct use only of technical embedded market fields.
- **Why it matters:** Displayed Gray/risk-off policy may not be the effective ranking policy.
- **Potential contamination/correctness effect:** Severe staleness cannot restrain scores/profiles, while sparse bullish state may already be embedded upstream.
- **Existing guard:** Technical score/classification includes its own market regime logic and warnings.
- **Missing guard:** Explicit choice and persisted lineage: either consume the command-center snapshot or declare it non-authoritative for ranking.
- **Recommended future remediation:** Define one market-policy consumer contract and persist the exact regime artifact/fields applied.

## Audit Coverage

### Fully audited

- Combined input loading, formula, penalties, warnings, labels, sorting, persistence, and refresh routes. **VERIFIED**
- Ranking profile configuration/parser, all five profile formulas/components/gates/penalties, optional liquidity overlay, sorting, persistence, query/export, and refresh routes. **VERIFIED**
- CERI, market-regime, sector-rotation, setup/lifecycle, and Winner dependency direction at the ranking boundary. **VERIFIED**
- Required input compatibility dimensions, fallbacks, provenance, recalculation, and cross-task invariants. **VERIFIED**

### Partially audited

- Downstream sector/setup/Winner consumers were traced only far enough to prove dependency direction and propagation; their full calculations belong to later tasks. **PARTIALLY_VERIFIED**
- Database constraints and migrations were inspected statically; deployed rows, triggers, and schema revision were not queried. **PARTIALLY_VERIFIED**
- UI/API/CSV publication paths were inspected but not browser-tested in this task. **PARTIALLY_VERIFIED**

### Not audited

- Live database contents, current ranking rows, active deployment configuration, provider state, and operator refresh history. **UNKNOWN**
- Predictive/investment validity or calibration of profile weights and thresholds. **UNKNOWN**
- Task 05 and later prompt-pack scopes. **VERIFIED — intentionally excluded**

## Known Unknowns

- Whether production currently contains rankings generated under older profile files, stale same-run rankings, or cross-context liquidity features is unknown because the live database was not queried. **UNKNOWN**
- Whether deployment overrides alter technical V5 activation, ranking files, or market policy is unknown. **UNKNOWN**
- The intended product contract for command-center `allowed_profiles` versus the current non-consumption is not declared in ranking code. **UNKNOWN**
- Whether `UploadRun.processed_at` is always populated in production before ranking is expected but not database-enforced as non-null. **PARTIALLY_VERIFIED**

## Contradictions

- A market-regime snapshot is built immediately before combined/ranking, yet neither stage consumes it; profile permissions and Gray risk state are presentation/downstream policy rather than effective ranking policy. **CONTRADICTORY**
- Ranking's `data_quality` gate reports failure while `is_complete=true` and `has_technical=true` can still persist for an error technical row. **CONTRADICTORY**
- Profile configuration is data-driven but has no data-version identity; only the code engine string is persisted. **CONTRADICTORY**
- Same-run uniqueness suggests one canonical ranking, while refresh mutates that row across different upstream generations/configurations. **CONTRADICTORY**
- Combined output records exact source IDs/config snapshot, while ranking—the more profile-specific decision artifact—does not. **CONTRADICTORY**

## Potential Contract Violations

- RANK-001: combined scoring promotes insufficient low-confidence technical values. **VERIFIED**
- RANK-002: ranking profiles preserve synthetic scores/rank and false completeness for unready technical rows. **VERIFIED**
- RANK-003: global latest liquidity fallback is not context-compatible or provenance-complete. **VERIFIED**
- RANK-004: run/ticker equality substitutes for temporal/version compatibility. **VERIFIED**
- RANK-005: exact profile configuration is not recoverable. **VERIFIED**
- RANK-006: recalculation overwrites ranking history and permits stale same-run coexistence. **VERIFIED**
- RANK-007: earnings risk uses current wall-clock date and unanchored schedule evidence. **VERIFIED**
- RANK-008: command-center market policy does not govern ranking. **VERIFIED**

## High-Risk Cross-Subsystem Dependencies

| Producer | Consumer | Dependency | Risk | Status |
|---|---|---|---|---|
| Fundamental scoring | Combined/ranking | Run/ticker score and label | Temporally unanchored, replaceable, versions not copied | VERIFIED |
| Technical scoring | Combined/ranking | Dual score, components, class, warnings | CORE-009 numeric leakage; context/version ignored | VERIFIED |
| SPY/QQQ technical features | Technical then combined/ranking | Embedded market score/regime | Sparse bullish defect can propagate | VERIFIED |
| Command-center regime | Combined/ranking | No direct dependency | Gray/gates/permissions do not protect ranking | VERIFIED |
| IB intelligence | Three ranking profiles | Latest global liquidity feature | Silent cross-run/context penalty | VERIFIED |
| Uploaded earnings date | Combined/ranking | Calendar-day penalty/gate | Current-date recomputation and no PIT schedule lineage | VERIFIED |
| Ranking/combined | Sector rotation | Same-run universe/profile aggregates | Low-confidence numeric leakage propagates downstream | VERIFIED |
| Ranking | Setup/lifecycle and Winner | Rank/profile context | Downstream may inherit mutable/incomplete provenance | PARTIALLY_VERIFIED |
| CERI | Combined/ranking | None | No backwards/current-global reuse | VERIFIED |

## Files Inspected

- `config/ranking_profiles.yaml`, `config/scoring_weights.yaml`, relevant technical and market-regime configuration. **VERIFIED**
- `app/services/combined_decision.py`, `confidence_service.py`, `warning_flag_service.py`, `earnings_risk_service.py`, and `cockpit_sorting.py`. **VERIFIED**
- All `ranking_profile_*` services, `ranking_result_export.py`, run routes, pipeline service/executor, and upload/fundamental/technical refresh writers. **VERIFIED**
- Technical component/regime construction, preferred bounded market reader integration, command-center regime service/policy/repository, sector universe/rotation services, and selected setup/Winner source loaders. **VERIFIED**
- `UploadRun`, `FundamentalScore`, `TechnicalScore`, `CombinedResult`, `RankingResult`, `MarketCalculationContext`, market-regime/sector models, and `IBIntelligenceFeature` schemas. **VERIFIED**
- Ranking migration `0011_create_ranking_results` and relevant later context/technical migrations through repository head 0074. **VERIFIED**

## Tests Inspected

- Combined decision, ranking config/components/engine/service/routes/golden/export, pipeline executor, schema/run actions, technical confidence, market-regime command center/policy, sector universe/rotation, and fundamental V2 tests. **VERIFIED**
- Focused execution: 117 tests passed in the primary combined/ranking/pipeline suite; 92 additional schema/refresh/regime/sector/readiness tests passed. One Starlette deprecation warning appeared in each invocation; no test failed. **VERIFIED**
- Two isolated in-memory probes verified insufficient/error technical propagation without writing repository or database state. **VERIFIED**
- No live-provider, live-database, migration, full end-to-end pipeline, or browser test was run. **VERIFIED**

## SQL Inspected

- SQLAlchemy queries for same-run raw/fundamental/technical inputs, global liquidity selection, ranking upsert, combined delete/recreate, and result query/export. **VERIFIED**
- Model/migration foreign keys, unique constraints, indexes, timestamps, and JSON provenance columns for combined/ranking inputs and outputs. **VERIFIED**
- No ad hoc SQL was executed against a live database and no migration was applied. **VERIFIED**

## Verification Metadata

| Item | Value | Status |
|---|---|---|
| HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Baseline drift | No | VERIFIED |
| Branch | `codex/winner-evidence-remediation` | VERIFIED |
| Runtime | CPython 3.12.2 | VERIFIED |
| Tests | 209 passed in two focused invocations; two isolated probes completed | VERIFIED |
| Live external systems | Not accessed | VERIFIED |
| Deliverable | `docs/audit/calculation-lineage/04_ranking.md` only | VERIFIED |
