# SwingLens Phase 7 Fundamental Scoring Correctness

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 1 traceability: `docs/review/phase1_requirements_traceability.md`
Phase 5 CSV safety: `docs/review/phase5_csv_ingestion_normalization_export_safety.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 7 validates fundamental parsing, transformations, component formulas, penalties, labels,
warnings, duplicate-ticker behavior, explanation lineage, boundary tests, and model-change
governance.

Overall status: not exit-ready. Fundamentals v2 has useful component, coverage, warning, debug, and
export plumbing, and focused tests pass. The blocking correctness gaps are label-contract drift,
duplicate ticker handling, insufficient formula/version governance, and incomplete boundary/golden
coverage.

## Evidence Log

Inspected surfaces:

- Scoring engines: `app/services/fundamental_ranker_v2.py`,
  `app/services/fundamental_components_v2.py`, `app/services/fundamental_ranker.py`.
- Coverage and warnings: `app/services/fundamental_coverage_service.py`,
  `app/services/fundamental_warning_service.py`, `app/services/warning_flag_service.py`.
- Upload persistence bridge: `app/services/upload_service.py`, `app/models/tables.py`.
- Downstream combined/ranking behavior: `app/services/combined_decision.py`,
  `app/services/ranking_profile_penalties.py`, `app/services/ranking_profile_engine.py`.
- Parsing and mapping: `app/services/numeric_parser.py`, `config/column_aliases.yaml`.
- Model config: `config/fundamentals_v2.yaml`, `config/scoring_weights.yaml`.
- Requirements/docs: `docs/srs.md`, `docs/sdd.md`, `docs/vision.md`.
- Golden and focused tests: fundamental ranker, components, coverage, acceptance, upload bridge,
  warning flags, combined decision, config, run detail, score card, ranking profile, and golden
  pipeline tests.

Command evidence:

| Command | Result | Notes |
|---|---:|---|
| `uv run pytest tests/test_fundamental_ranker_v2.py tests/test_fundamental_components_v2.py tests/test_fundamental_coverage_service.py tests/test_fundamentals_v2_acceptance.py tests/test_upload_service_v2.py tests/test_numeric_parser.py tests/test_warning_flag_service.py tests/test_combined_decision.py tests/test_fundamental_ranker.py -q` | Passed | `39 passed in 4.82s`. |
| `uv run pytest tests/test_golden_pipeline.py tests/test_config_files.py tests/test_run_detail_view_models.py tests/test_score_card_view_service.py tests/test_ranking_profile_components.py tests/test_ranking_profile_engine.py -q` | Passed | `54 passed in 4.69s`. |
| Quality-risk label probe | Reproduced | v2 emitted `Quality risk`, which is not in the SRS allowed label list. |
| Combined-decision label probe | Reproduced | `Quality risk` scored like `High-quality quant`; `Growth trap risk` and `Value trap risk` receive penalties. |
| Quarterly-growth probe | Reproduced | Changing only `revenue_growth_quarterly_yoy` or `eps_growth_quarterly_yoy` from `-100` to `100` did not change growth or final score. |
| Duplicate ticker scoring probe | Reproduced | Two mapped rows with ticker `DUP` produced two v2 score results, while persistence has a unique run/ticker constraint. |
| Sparse-row probes | Reviewed | Sparse rows were flagged and penalized in tested cases; no sparse row exceeded `Low priority` in the probes. |

## Fundamental Formula Specification

Current active upload path:

- `create_upload_run` maps CSV rows, stores raw rows, scores with `score_rows_v2`, and persists
  `FundamentalScore` rows through `_fundamental_score_from_v2`.
- `score_row_v2` calculates ten v2 components:
  `growth_quality_score`, `profitability_quality_score`, `fcf_quality_score`,
  `earnings_quality_score`, `capital_efficiency_score`, `balance_sheet_quality_score`,
  `valuation_quality_score`, `forward_quality_score`, `shareholder_quality_score`, and
  `liquidity_risk_score`.
- `config/fundamentals_v2.yaml` weights sum to `1.0`:
  growth `0.13`, profitability `0.13`, FCF `0.12`, earnings quality `0.12`, capital efficiency
  `0.12`, balance sheet `0.12`, valuation `0.10`, forward `0.08`, shareholder `0.05`, liquidity
  risk `0.03`.
- Each component uses present-field weighted averages; if no subfields are present, the component
  defaults to `5.0`.
- Scores are clamped to `[0, 10]`.
- Coverage is calculated from configured component fields and priority lists, with a capped
  missing-data penalty of `2.50`.
- Final fundamental score is:

```text
clamp(weighted_component_score - missing_data_penalty, 0, 10)
```

Current label logic:

```text
Value trap risk     if balance_sheet_stress or poor_cash_conversion
Quality risk        if earnings_quality_risk or forward_quality_weak
Clean compounder    if score >= 7.6 and profitability >= 7 and FCF >= 6.5 and earnings >= 6.5
High-quality quant  if score >= 6.7
Mixed but interesting if score >= 5.0
Low priority        otherwise
```

Current persisted/debug lineage:

- Persisted fields include legacy-compatible component columns plus v2 columns, final score, label,
  explanation, data coverage, missing-data penalty, warning flags, model version, and debug JSON.
- Debug JSON includes model version, component scores, component coverage, coverage ratios,
  missing-field priority lists, parse diagnostics, warnings, and canonical fields present.

## Findings Register

### PH7-001

Title: Fundamentals v2 emits an undocumented label and cannot emit a documented label

Severity: S1 High

Confidence: Confirmed

Evidence:

- `docs/srs.md:273-280`, `docs/sdd.md:875-879`, and `docs/vision.md:527-531` list allowed labels
  including `Growth trap risk`; none list `Quality risk`.
- `app/services/fundamental_ranker_v2.py:130-144` can return `Value trap risk`, `Quality risk`,
  `Clean compounder`, `High-quality quant`, `Mixed but interesting`, and `Low priority`.
- The legacy ranker can return `Growth trap risk` at `app/services/fundamental_ranker.py:240-242`,
  but the active upload path uses v2.
- Probe produced `Quality risk 6.5766 ['high_accrual_risk', 'earnings_quality_risk',
  'sparse_fundamental_data']`.

Impact: Stored labels, UI, exports, warnings, ranking profiles, and user expectations are not using a
single label contract. A downstream user can see a label the SRS does not authorize, while a
documented trap label is unreachable in the active model.

Recommended remediation:

- Decide whether `Quality risk` replaces `Growth trap risk` or is a new approved label.
- Update SRS/SDD/vision, combined decision, ranking profile penalties, warning flag maps, exports,
  and tests together.
- Add boundary tests for every allowed label, including exact thresholds around `7.6`, `6.7`, and
  `5.0`.

### PH7-002

Title: `Quality risk` is not penalized like other fundamental risk labels

Severity: S1 High

Confidence: Confirmed

Evidence:

- `app/services/combined_decision.py:147-150` penalizes only `Value trap risk` and
  `Growth trap risk`.
- `app/services/ranking_profile_penalties.py:174-184` uses the same label set for profile penalties.
- Probe with identical scores/technicals produced:
  - `Quality risk`: final score `7.925`, decision `Candidate`.
  - `High-quality quant`: final score `7.925`, decision `Candidate`.
  - `Growth trap risk`: final score `6.425`, decision `Watchlist`.
  - `Value trap risk`: final score `5.925`, decision `Avoid`.
- `tests/test_warning_flag_service.py:30-45` verifies warning flags for `Quality risk`, but no
  combined/ranking penalty test covers it.

Impact: A v2 earnings/forward quality risk can carry warnings but still receive no score penalty or
decision downgrade beyond generic sorting effects. This can make a risk-labeled company rank like a
normal high-quality candidate.

Recommended remediation:

- Add explicit combined and ranking profile policy for `Quality risk`.
- If quality risk should only warn, document that as a decision record and add tests asserting no
  penalty is intentional.
- If it should behave like a trap, add penalties and acceptance tests.

### PH7-003

Title: Duplicate ticker rows are scored twice before hitting unique persistence

Severity: S1 High

Confidence: Confirmed

Evidence:

- `app/services/fundamental_ranker_v2.py:42-44` scores every mapped row with a ticker.
- `app/services/upload_service.py:65` persists every v2 score returned by `score_rows_v2`.
- `app/models/tables.py:245` enforces unique `(run_id, ticker)` for `fundamental_scores`.
- `app/services/combined_decision.py:310-317` later deduplicates raw rows by first ticker occurrence,
  but fundamentals are not deduped before insert.
- Probe with two `DUP` rows produced two score results for the same ticker.

Impact: A CSV containing duplicate ticker rows can either fail at database commit or produce
inconsistent row-selection behavior across raw rows, fundamentals, combined decisions, ranking
profiles, and exports. Phase 7 requires an explicit duplicate ticker behavior and row-selection
policy.

Recommended remediation:

- Choose first-row-wins, last-row-wins, best-coverage-wins, or hard-fail-on-duplicate as the
  canonical policy.
- Enforce it before scoring and before persistence.
- Add tests for duplicate tickers with conflicting fundamentals, sector/company values, and row
  numbers.

### PH7-004

Title: Fundamentals v2 formula/config contract has field drift

Severity: S2 Medium

Confidence: Confirmed for quarterly growth; likely for broader config drift.

Evidence:

- `config/fundamentals_v2.yaml:109-115` lists `revenue_growth_quarterly_yoy` and
  `eps_growth_quarterly_yoy` under `growth_quality_score`.
- `app/services/fundamental_components_v2.py:34-54` does not read either quarterly growth field.
- Probe changing only those fields from `-100` to `100` left growth and final scores unchanged.
- `config/fundamentals_v2.yaml` also contains priority-only fields such as `market_cap`,
  `number_of_shareholders_annual`, and `total_assets_estimate_annual` that are validated/missing
  but not part of the component-field union.

Impact: Column-mapping summaries and coverage debug can tell users a field is a scoring field even
when the active formula ignores it. Conversely, priority-only fields can affect missing penalties
without appearing in component coverage. This weakens reproducibility and explanation trust.

Recommended remediation:

- Generate the formula catalogue from code or move formulas fully into validated config.
- Add a contract test that every configured component field either changes at least one component
  score in a directional fixture or is explicitly marked `coverage_only`.
- Add a coverage-only field section for fields that intentionally affect penalties but not component
  scores.

### PH7-005

Title: Fundamentals v2 configuration is loaded without schema or threshold validation

Severity: S2 Medium

Confidence: Confirmed

Evidence:

- `app/services/fundamental_ranker_v2.py:105-109` loads `config/fundamentals_v2.yaml` with
  `yaml.safe_load` and returns it directly.
- Current tests check model version, weight sum, and alias coverage in `tests/test_config_files.py`,
  but there is no loader-level validation for required sections, unknown weights, missing component
  functions, threshold ordering, numeric bounds, duplicate fields, or a forced model-version bump.
- Other subsystems such as ranking profiles and sector rotation have stronger config rejection tests.

Impact: A malformed fundamentals config can silently change scoring behavior, fail at runtime, or
produce unreviewed model drift while retaining `fundamentals_v2.0`.

Recommended remediation:

- Add a `FundamentalsConfigError` and validate the config at load time.
- Reject unknown/missing weights, non-normalized weights, invalid threshold order, duplicate fields,
  unknown fields, missing label thresholds, and incompatible model version changes.
- Add tests for invalid configs and require golden fixture updates when model version or formula
  weights change.

### PH7-006

Title: Golden fixture coverage is too narrow for model correctness

Severity: S2 Medium

Confidence: Confirmed

Evidence:

- `tests/test_golden_pipeline.py:12-29` validates one pipeline fixture with one expected
  fundamental score/label.
- Existing v2 tests cover deterministic scoring, one earnings-quality trap, one asset-growth flag,
  sparse old CSV behavior, two component examples, coverage diagnostics, and upload/export plumbing.
- There are no boundary tests for all v2 labels, all warning flags, monotonicity properties for each
  metric family, outlier/currency/unit cases, or duplicate ticker row-selection policy.

Impact: The model can drift materially while the current golden and unit tests remain green. This is
especially risky because the app’s output directly guides research prioritization.

Recommended remediation:

- Add a multi-row golden fundamentals fixture covering clean compounder, high-quality quant, mixed,
  low priority, value trap, quality/growth trap policy, sparse data, bad numeric parses, and
  duplicate ticker behavior.
- Add property tests for monotonic directions where intended: higher growth/margins/ROIC/liquidity
  should not lower component score, and higher leverage/valuation/beta/ATR should not raise it.
- Add threshold-boundary tests for every label and warning flag.

## Independent Calculation Workbook Or Script

Recommended artifact: `tests/fixtures/fundamentals_v2_independent_cases.json` plus
`scripts/audit_fundamentals_v2.py`.

Script requirements:

- Read fixture rows and expected component/final values.
- Reimplement only the formula specification, not by importing `score_row_v2`.
- Emit per-field contribution, component score, coverage penalty, final score, label, and warnings.
- Fail if active `score_row_v2` differs outside a documented rounding tolerance.
- Include at least:
  clean full-coverage row, sparse row, value trap, quality/growth trap decision case, high leverage,
  poor cash conversion, high accruals, asset growth without returns, weak forward estimates, dividend
  payout risk, bad numeric parse, and duplicate ticker fixture.

## Boundary And Property-Test Backlog

- Label thresholds: exact `7.6`, just below `7.6`, exact `6.7`, just below `6.7`, exact `5.0`, just
  below `5.0`, with component-gate boundaries for profitability, FCF, and earnings quality.
- Warning thresholds: Sloan warning/danger, FCF-to-net-income, asset-growth-without-return,
  liabilities-to-assets, quick/current ratio, debt-to-EBITDA, dividend payout, beta, ATR percent, and
  dollar-volume thresholds.
- Parser/units: `%`, parentheses negatives, suffix multipliers, currency symbols/codes, booleans,
  infinities, comma decimals if ever expected, negative ratios, zero denominator cases, and conflicting
  currency columns.
- Coverage/missingness: all fields missing, only critical fields, only non-critical fields, invalid
  numeric values, text analyst rating, coverage just below/at/above sparse threshold.
- Monotonicity: each scalar scorer should be non-decreasing for higher-better metrics and
  non-increasing for lower-better metrics across representative ranges.
- Duplicate policy: duplicate ticker with identical rows, conflicting rows, different sectors, and
  different row numbers.
- Persistence/export: debug payload includes model version, fields present, component scores,
  missing fields, warnings, and parse diagnostics for every boundary fixture.

## Versioning And Model-Change Governance

Proposed rule:

- Any change to component formulas, weights, thresholds, labels, missing-data penalty, field
  priorities, parser semantics, or duplicate policy must bump `model_version`.
- A model-version bump must include updated formula documentation, independent fixture expectations,
  golden pipeline expectations, release notes, and a decision record describing intentional score
  movement.
- Cosmetic explanation changes may keep the same model version only if component/final scores,
  labels, warnings, and debug keys are unchanged.
- CI should run fundamentals config validation, unit/property tests, and the independent calculation
  script.

## Phase Scorecard

| Dimension | Rating | Notes |
|---|---|---|
| Parser/unit handling | Amber | Useful numeric parser and diagnostics; broader currency/unit edge cases need fixtures. |
| Component formulas | Amber | Deterministic and bounded; config/code field drift and missing formula catalogue remain. |
| Missing-data policy | Amber | Sparse rows are flagged/penalized; coverage-only fields and sparse reward boundaries need tests. |
| Label correctness | Red | Active v2 emits undocumented `Quality risk` and cannot emit documented `Growth trap risk`. |
| Duplicate ticker policy | Red | Scored twice before unique persistence; no explicit row-selection policy. |
| Explanation lineage | Amber | Debug payload is strong; explanation is generic and formula/version docs lag implementation. |
| Golden/regression coverage | Amber/Red | Existing fixture is valuable but too narrow for model-change control. |
| Model governance | Red | No fundamentals config schema, threshold validation, or required version-bump enforcement. |

## Exit Criteria Status

| Criterion | Status | Notes |
|---|---|---|
| Representative scores can be reproduced independently | Partial | One golden fixture exists; no independent workbook/script yet. |
| All thresholds and missing-data policies have boundary tests | Fail | Only a handful of component/warning cases are covered. |
| Model changes require explicit version and reviewed regression update | Fail | `fundamentals_v2.yaml` carries a model version but no enforcement. |
| Duplicate ticker behavior is defined and tested | Fail | Duplicate rows are scored twice and conflict with unique persistence. |
| Labels are documented and reachable | Fail | `Quality risk` is undocumented; `Growth trap risk` is unreachable in v2. |

Phase 7 should remain open until PH7-001, PH7-002, and PH7-003 are fixed or intentionally accepted
with a documented product decision, and until PH7-005 has at least loader-level validation.
