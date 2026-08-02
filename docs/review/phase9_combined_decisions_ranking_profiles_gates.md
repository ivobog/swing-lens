# Phase 9 Review - Combined Decisions, Ranking Profiles, Gates, and User Guidance

Date: 2026-08-02
Reviewer: Codex
Scope: combined decision scoring, ranking profiles, warning flags, earnings gates, position-size
hints, exports, run-detail/ticker/history UI surfaces, and persisted decision evidence.

## Objective

Validate how fundamental and technical evidence becomes ranking, decisions, warnings, and
position-size hints.

## Executive Summary

Phase 9 is partially exit-ready.

The decision layer is small, deterministic, and has useful tests around danger overrides, earnings
gates, warning flags, ranking profile directionality, exports, and view models. Complete combined
rows sort ahead of incomplete rows, and ranking profiles persist much richer debug evidence than the
base combined result.

The main gaps are policy and auditability gaps rather than broad implementation instability:
`missing_data_policy.penalty` is parsed but not used by ranking profiles, a `Growth trap risk` row
can still surface as `Strong candidate` with `Full starter`, combined results do not persist enough
calculation evidence to reconstruct the decision after config changes, and labels/wording are not
fully harmonized between combined decisions, ranking profiles, UI, and exports.

## Evidence Log

| Check | Result | Notes |
| --- | --- | --- |
| Phase 9 checklist from `C:/Users/Ivica/Downloads/software_review_plan.md` | Reviewed | Objective, activities, outputs, and exit criteria mapped. |
| Focused Phase 9 test suite | Passed | `uv run pytest tests/test_combined_decision.py tests/test_confidence_service.py tests/test_warning_flag_service.py tests/test_earnings_risk_service.py tests/test_earnings_date_parser.py tests/test_ranking_profile_config.py tests/test_ranking_profile_components.py tests/test_ranking_profile_engine.py tests/test_ranking_profile_service.py tests/test_ranking_result_export.py tests/test_ranking_profile_routes.py tests/test_ranking_profiles_golden.py tests/test_score_card_view_service.py tests/test_run_detail_view_models.py tests/test_exports_history.py tests/test_golden_pipeline.py -q` -> `142 passed in 8.20s`. |
| Boundary probe | Reviewed | Combined/profile thresholds are inclusive at threshold; `risk_score <= 3.5` gives `Full starter`, `3.5001` gives `Half starter`. |
| Missing-data probe | Reproduced issue | `momentum_swing` has `missing_data_policy.penalty = 1.0`; `calculate_profile_score(None, 10.0)` returns `10.0` before separate penalties/gates. |
| Contradiction probe | Reproduced issue | `Growth trap risk` with 10/10 scores and a low-risk buyable technical classification returned `8.5`, `Strong candidate`, `Full starter`, and `growth_trap_risk` warning. |
| UI/export wording scan | Reviewed | Main run detail says size is advisory; ticker chart, history, and CSV exports expose size hints without local caveat. |

## Decision-Table Specification

### Combined Results

Formula:

1. Use available-data weighted score from fundamental score and technical dual score
   (`combined_decision.py:125-130`, `combined_decision.py:215-229`).
2. Subtract penalties for missing fundamental, missing technical, danger classification,
   overheated momentum, value trap, growth trap, liquidity warning, and earnings risk
   (`combined_decision.py:133-160`).
3. Clamp and round final score to `[0, 10]` (`combined_decision.py:163`, `combined_decision.py:390-391`).
4. Assign label:
   - missing fundamental or missing technical -> `Incomplete data`
   - danger classification -> `Avoid`
   - `Value trap risk` -> `Avoid`
   - score >= strong threshold -> `Strong candidate`
   - score >= candidate threshold -> `Candidate`
   - score >= watch threshold -> `Watchlist`
   - otherwise -> `Avoid`
   (`combined_decision.py:232-252`).
5. Earnings block can override label to `Blocked by earnings gate`
   (`combined_decision.py:172-176`).
6. Position-size hint:
   - `Blocked by earnings gate` -> `No new entry`
   - `Incomplete data` -> `Wait`
   - `Avoid` -> `Avoid`
   - strong + buyable technical + risk <= 3.5 -> `Full starter`
   - strong/candidate -> `Half starter`
   - otherwise -> `Small probe`
   (`combined_decision.py:255-275`).

Sort order:

- `cockpit_sort_key` sorts by `sort_bucket`, descending final score, then ticker
  (`cockpit_sorting.py:8-13`).
- Combined sort buckets put complete strong/candidate/watch rows ahead of blocked/avoid, then
  incomplete rows (`confidence_service.py:76-86`).

### Ranking Profiles

Formula:

1. Extract technical component scores from persisted technical fields and debug/explainability
   (`ranking_profile_components.py`).
2. Calculate weighted technical profile score from profile component weights
   (`ranking_profile_components.py:104-113`).
3. Combine technical profile score and fundamental score with profile weights, optionally
   rescaling available data (`ranking_profile_engine.py:180-198`).
4. Subtract configured penalties for missing data, technical danger, overheated momentum, liquidity,
   trap flags, dilution/FCF warnings, and earnings risk (`ranking_profile_penalties.py`).
5. Apply gates for earnings block, danger, fundamental floor, liquidity cap, and data quality
   (`ranking_profile_gates.py:33-106`).
6. Sort by bucket, profile score, fundamental score, technical profile score, then ticker
   (`ranking_profile_engine.py:214-223`).
7. Persist profile-specific penalties, gates, component scores, and debug payload
   (`ranking_profile_service.py:185-188`; `models/tables.py:417-440`).

## Findings Register

### PH9-001 - Ranking profile `missing_data_policy.penalty` is inert

Severity: High

Evidence:
- `MissingDataPolicy` defines `penalty` and the config parser reads/validates it
  (`app/services/ranking_profile_config.py:35-38`, `app/services/ranking_profile_config.py:121-126`,
  `app/services/ranking_profile_config.py:184-187`).
- The only engine read of `profile.missing_data_policy` is `rescale_available`
  (`app/services/ranking_profile_engine.py:196-198`).
- Static search found no read of `missing_data_policy.penalty` outside parser/tests.
- Probe: `momentum_swing` has `missing_data_policy.penalty = 1.0`, but
  `calculate_profile_score(technical_profile_score=None, fundamental_score=10.0)` returned `10.0`
  before separate penalties/gates.

Impact: Each profile appears to expose a configurable missing-data penalty, but changing it has no
effect. The active missing-data penalty comes from `profile.penalties["missing_data"]` instead. This
can make profile configuration misleading and violates the Phase 9 request to test each profile's
missing-data policy.

Recommendation:
- Either remove `missing_data_policy.penalty` from config/DTOs or wire it into
  `calculate_profile_penalties` as the authoritative missing-data penalty.
- Add tests that changing `missing_data_policy.penalty` changes a missing-data profile score, or
  tests that the field is rejected as unsupported.

### PH9-002 - Growth-trap contradiction can still produce `Full starter`

Severity: High

Evidence:
- Combined decisions subtract only a `growth_trap_risk` penalty and do not cap the decision
  (`app/services/combined_decision.py:150-152`, `app/services/combined_decision.py:232-252`).
- Position-size hints ignore fundamental trap labels and look only at final decision, buyable
  technical class, and technical risk score (`app/services/combined_decision.py:255-275`).
- Probe: fundamental label `Growth trap risk`, fundamental score `10`, technical score `10`,
  classification `Prime clean pullback`, risk `1.0` returned `8.5`, `Strong candidate`,
  `Full starter`, and only a `growth_trap_risk` warning.
- Design language says growth trap risk should map to high risk / reduced size unless technical
  classification is very strong (`docs/sdd.md:1290-1294`).

Impact: A severe fundamental contradiction can appear as the strongest position-size hint. The
warning flag is present, but the top-line hint and label are not conservative enough for a trap
classification unless this behavior is explicitly approved.

Recommendation:
- Add a growth-trap gate or size cap, for example max decision `Candidate` or max size
  `Half starter`, unless a documented override condition is met.
- Include trap label in `_position_size_hint` inputs.
- Add contradiction tests for high score plus growth trap, value trap, high earnings risk, low
  technical confidence, and liquidity warning.

### PH9-003 - Combined results are not fully reconstructable from persisted evidence

Severity: High

Evidence:
- `CombinedResult` persists final score, source scores, labels, earnings fields, notes, warning
  flags, completeness flags, and sort bucket (`app/models/tables.py:316-372`).
- It does not have a `debug_json`, calculation payload, config hash/version, penalty breakdown,
  weighted pre-penalty score, or label-threshold snapshot.
- `_to_model` writes only the summary fields (`app/services/combined_decision.py:281-306`).
- `combine_row_decision` loads mutable `config/scoring_weights.yaml` at calculation time
  (`app/services/combined_decision.py:341-343`).

Impact: After weights, penalties, labels, or earnings-gate settings change, an old combined result
cannot be independently reconstructed from the combined row alone. Ranking results are better here
because they persist penalties, gates, component scores, and debug JSON
(`app/models/tables.py:417-440`).

Recommendation:
- Add combined-result debug evidence with engine version, config hash, weights, thresholds, raw
  component scores, weighted score before penalties, penalty breakdown, earnings calculation, final
  score, label decision path, and sort key.
- Export the debug evidence or a compact calculation summary for audit.
- Add a regression test that recomputes a persisted combined result from the stored debug payload.

### PH9-004 - Combined and ranking profile decision labels are semantically inconsistent

Severity: Medium

Evidence:
- Combined score labels include `Watchlist` (`app/services/combined_decision.py:250-251`).
- Ranking profile labels use `Watch`, plus gate-only `Low confidence` and `Speculative watch`
  (`app/services/ranking_profile_engine.py:201-209`; `app/services/ranking_profile_gates.py:10-16`).
- Help text documents combined labels only and includes `Watchlist`, not ranking-profile-only
  labels (`app/templates/help.html:40-51`).
- The plan asks to compare combined decisions with ranking-profile decisions for semantic conflicts.

Impact: A ticker can show `Watchlist` in combined output and `Watch` or `Speculative watch` in a
profile, while help text does not define all exposed labels. This is understandable internally, but
the user-facing semantics are underdocumented.

Recommendation:
- Define a canonical label taxonomy for combined decisions, ranking profiles, market profiles, and
  setup lifecycle.
- Add a mapping matrix: label, source, meaning, sort bucket, allowed position hints, warnings, and
  UI tone.
- Add a test that every emitted label is documented in the help/glossary payload.

### PH9-005 - Position-size hints need stronger research-only context on all surfaces

Severity: Medium

Evidence:
- Main run detail panel states: "Position size is an advisory research hint"
  (`app/templates/run_detail.html:671-676`).
- Ticker chart panel displays `Position` / `Position Size` directly without nearby caveat
  (`app/templates/ticker_chart_panel.html:27-37`, `app/templates/ticker_chart_panel.html:63-74`).
- History displays a `Size` column directly (`app/templates/history.html:143-168`).
- Combined and ranking CSV exports include `position_size_hint` without an adjacent
  research-only/disclaimer field (`app/services/export_service.py:58-75`,
  `app/services/export_service.py:354-372`, `app/services/ranking_result_export.py:14-31`,
  `app/services/ranking_result_export.py:53-74`).
- Help page does state the app is research-only and places no broker orders
  (`app/templates/help.html:81-85`).

Impact: The product-level safety boundary exists, but position-size hints are shown in some
contexts as terse action-like labels: `Full starter`, `Half starter`, `Small probe`, `No new entry`.
Phase 9 explicitly requires these to be clearly research labels, not executable instructions.

Recommendation:
- Rename display copy to `Research size hint` everywhere.
- Add export metadata columns such as `guidance_type=research_hint` and
  `execution_instruction=false`.
- Prefer softer labels such as `Review full-size candidate`, `Review half-size candidate`, and
  `Watch-only probe candidate`, or add a permanent tooltip/copy near all size fields.

### PH9-006 - Boundary tests are useful but not exhaustive

Severity: Medium

Evidence:
- Existing tests cover danger overrides, missing technical sorting, earnings windows, warning flags,
  ranking profile directionality, and exports (`tests/test_combined_decision.py`,
  `tests/test_earnings_risk_service.py`, `tests/test_ranking_profiles_golden.py`).
- Boundary probe confirmed threshold behavior:
  - combined `8.0` -> `Strong candidate`; `7.9999` -> `Candidate`
  - combined `6.8` -> `Candidate`; `6.7999` -> `Watchlist`
  - combined `5.5` -> `Watchlist`; `5.4999` -> `Avoid`
  - ranking profiles use the same numeric inclusivity but label `Watch`
  - `risk_score=3.5` -> `Full starter`; `3.5001` -> `Half starter`
- These exact below/at/above threshold assertions are not all locked in named tests.

Impact: Boundary behavior can drift while broad path tests remain green. This is especially relevant
because position-size hints and decision labels are user-facing.

Recommendation:
- Add parameterized tests for every combined and ranking profile threshold, every earnings threshold,
  the `3.5` risk-score size boundary, clamping at `0` and `10`, and rounding before label
  assignment.
- Include contradictory-evidence cases: high score plus danger, high score plus growth trap, strong
  fundamentals plus insufficient technical history, and high technical score plus missing benchmark.

## Ranking Consistency Report

| Area | Combined decision | Ranking profiles | Status |
| --- | --- | --- | --- |
| Score scale | 0-10 final score | 0-10 profile score | Consistent |
| Missing scores | Available-score rescale, then missing penalty, label `Incomplete data` | Available-score rescale, missing penalty, data-quality gate to `Low confidence` | Semantically different |
| Watch label | `Watchlist` | `Watch` | Inconsistent wording |
| Danger technicals | Label cap to `Avoid` | Gate cap to `Avoid` | Consistent |
| Value trap | Label cap to `Avoid` | Penalty only unless profile gates indirectly cap | Different |
| Growth trap | Penalty only | Penalty only | Potentially too permissive |
| Earnings blocked | Label override to `Blocked by earnings gate` | Gate override to `Blocked by earnings gate` | Consistent |
| Earnings high/medium | Score penalty and warning | Profile penalty and warning | Consistent in spirit |
| Liquidity warning | Score penalty only; no label/size cap | Some profiles can cap to `Watch` | Different by profile |
| Sort tie-break | bucket, score desc, ticker | bucket, profile score desc, fundamental desc, technical desc, ticker | Deterministic |
| Debug payload | Summary fields only | Penalties, gates, components, debug JSON | Ranking profiles stronger |

## Boundary and Contradiction Test Suite Proposal

Recommended additions:

| Test area | Cases |
| --- | --- |
| Combined thresholds | `strong`, `candidate`, and `watch` values immediately below, at, and above configured thresholds. |
| Ranking thresholds | Same below/at/above checks for all five enabled profiles. |
| Position-size hints | Blocked, incomplete, avoid, watch/watchlist, candidate, strong non-buyable, strong buyable risk `3.5`, strong buyable risk `3.5001`, missing risk. |
| Clamping/rounding | Scores below `0`, above `10`, and values that round to threshold boundaries. |
| Missing data | Missing fundamental only, missing technical only, missing both, missing benchmark/market, low technical confidence, insufficient history. |
| Contradictions | Growth trap plus excellent technicals, value trap plus excellent technicals, danger technical plus excellent fundamentals, liquidity warning plus strong score, medium/high earnings risk plus strong score. |
| Ranking profile policies | Every profile's weights, component weights, configured penalties, gates, missing-data policy, and emitted labels. |
| Sorting ties | Equal bucket/score/fundamental/technical values with ticker tie-break; incomplete high score vs complete lower score. |
| Earnings dates | Missing, unparseable, stale/past, today, tomorrow, boundary days, and conflicting raw/parsed values if supported later. |

## UI Wording and Warning Improvements

- Use `Research size hint` instead of `Position Size` in ticker, history, profile, sector, and export surfaces.
- Add `guidance_type` / `execution_instruction=false` to combined and ranking CSV exports.
- Document `Watch`, `Watchlist`, `Low confidence`, `Speculative watch`, and `Blocked by earnings gate`
  in one shared glossary.
- Show warning severity and size-hint caps together, so a top-line hint cannot visually outrank a
  severe warning.
- Add a compact "why this label" link or row expander using persisted penalty/gate/debug evidence.

## Exit Criteria Assessment

| Exit criterion | Result | Evidence |
| --- | --- | --- |
| Every decision label and position-size hint has explicit, tested conditions | Partial | Core conditions exist in code; exact boundary and full label taxonomy tests are incomplete. |
| Missing and conflicting data produce conservative, visible outcomes | Partial/Fail | Missing technicals sort conservatively; growth trap can still produce `Full starter`; ranking `missing_data_policy.penalty` is inert. |
| Results are deterministically sortable and explainable | Partial | Sort keys are deterministic; ranking profiles are explainable; combined results lack reconstructable debug payload. |

## Recommended Next Work

1. Wire or remove ranking `missing_data_policy.penalty`.
2. Add a growth-trap size/label cap or explicitly document and test the override condition.
3. Add persisted combined decision debug evidence and config hash/version.
4. Unify the decision-label taxonomy across combined results and ranking profiles.
5. Rename size fields to research guidance and add export safety metadata.
6. Add parameterized boundary and contradiction tests for all labels, gates, penalties, and position
   hints.
