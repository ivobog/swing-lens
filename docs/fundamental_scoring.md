# Fundamental Scoring Maintainer Notes

Fundamental scoring converts mapped CSV fundamentals into durable `fundamental_scores` rows. Raw
upload values remain preserved on `raw_company_rows.raw_json`.

## Inputs

- Uploaded CSV rows mapped through `config/column_aliases.yaml`.
- Numeric parsing from `app/services/numeric_parser.py`.
- Fundamental score logic in `app/services/fundamental_ranker_v2.py`.

## Versioning

Bump the fundamental scoring version when formulas, thresholds, labels, component meanings, warning
flags, or parsed field semantics change. Update golden fixtures and release notes in the same PR.

## Validation

```powershell
uv run pytest tests/test_fundamental_ranker.py tests/test_fundamental_ranker_v2.py -q
uv run pytest tests/test_fundamental_components_v2.py tests/test_fundamentals_v2_acceptance.py -q
uv run pytest tests/test_golden_pipeline.py -q
```

## Review Rules

- Keep raw uploaded evidence unchanged.
- Document formula changes in this file and `docs/versioning.md` if policy changes.
- Treat changes that alter score order or labels as model/scoring changes.
- Do not backfill historical scores without an explicit, auditable rebuild path.

## Forward quality: fundamentals_v2.2

Only the 8% forward component changes. All nine other component calculations,
outer weights, missing-data penalties/priorities, warning rules, and classification
thresholds remain unchanged. Retained v2.1 configurations dispatch to the original
formula; they are never reinterpreted as v2.2.

### Previous formula (v2.1)

The base was the available-value weighted average of positivity scores for annual
EPS (1.0), quarterly EPS (0.7), annual estimated revenue (0.8), NTM estimated net
income (1.0), NTM estimated EBIT (1.0), annual estimated cash (0.4), and a CAPEX sign
score (0.3). Positivity scored 10 for positive amounts and 0 otherwise. CAPEX scored
7 for negative amounts and 5 otherwise. The base defaulted to 5 if unavailable.
The existing analyst bonus was then added and the result clamped to 0–10. Positive
forecasts could therefore score highly even when substantially below historical results.

### Current formula (v2.2)

```text
Forward = sum(weight * available_subscore) / sum(available_weights)

weights:
  net income growth:           0.30
  annual revenue comparison:  0.25
  expected profitability:     0.20 (currently unavailable)
  annual cash comparison:     0.10
  CAPEX sustainability:       0.10
  analyst rating:             0.05
```

Every available subscore is in [0, 10]. Unavailable values are excluded from both
sums, not scored as zero. No available subscore means 5.0. Existing four-decimal
normalization/rounding is retained. With all currently supported terms available,
the denominator is 0.80 because compatible forecast profitability is unavailable.

For positive historical earnings, revenue, or cash, comparison percentages are
`100 * (estimate / historical - 1)`. Growth normalization is piecewise:

```text
g >= 0: clamp(5 + 5*g/positive_threshold, 0, 10)
g <  0: clamp(5 + 5*g/negative_threshold, 0, 10)
```

| Comparison | Positive threshold | Negative threshold magnitude |
| --- | ---: | ---: |
| NTM net income versus TTM net income | 30% | 25% |
| Annual revenue estimate versus TTM revenue | 20% | 20% |
| Annual cash estimate versus annual cash balance | 30% | 30% |

For earnings: positive-to-nonpositive scores 0; nonpositive-to-positive scores 7;
both nonpositive scores 0. These cases never divide by a nonpositive denominator.
Revenue and cash require positive historical amounts and nonnegative estimates;
invalid signs and zero denominators make those comparisons unavailable.

CAPEX uses absolute spending magnitudes, accommodating both cash-outflow and
positive-spending conventions observed in uploads:

```text
capex_growth = 100 * (abs(estimate_CAPEX) / abs(TTM_CAPEX) - 1)
pressure = max(0, capex_growth - net_income_growth)
CAPEX_score = clamp(10 - pressure/5, 0, 10)
```

Historical CAPEX must be nonzero. Both historical and expected earnings must be
positive for growth-pressure scoring. Known nonpositive expected earnings score 0
when the CAPEX pair and its denominator are valid, even if historical earnings are
missing; this case needs no earnings-growth division. A loss-to-profit transition
makes CAPEX sustainability unavailable. A reduction in CAPEX is evaluated relative
to earnings growth and does not automatically earn a favorable score.

Analyst labels use exact, case-insensitive, whitespace-normalized matches:
Strong Buy=10, Buy/Outperform=7.5, Hold/Neutral=5, Sell/Underperform=2.5, Strong Sell=0.
The slash denotes alternative labels. Missing, `No rating`, numeric values, and
unrecognized labels are unavailable. No analyst bonus is added in v2.2.

### Financial validity and limitations

Monetary comparisons require finite parsed amounts and matching explicit
field-specific currency metadata recognized by the existing numeric parser's
currency-code set. Missing, unknown, or conflicting currencies make that pair
unavailable. Price currency is not a substitute. No FX conversions are performed.
Currency metadata is a validity guard and adds no scoring/coverage metric.

Forward-only finite guards catch parser overflow and non-finite numeric strings;
the shared parser is unchanged. Decimal arithmetic for growth and CAPEX pressure
prevents overflow and `inf - inf` for finite extreme/subnormal inputs. Existing
score clamping and rounding bound the output without inventing denominator cutoffs.

NTM income and TTM income are twelve-month flows. Annual revenue estimates have an
unverified fiscal target and are explicitly compared with TTM revenue, rather than
described as verified forward YoY growth. Annual cash values are balance-sheet
snapshots; their comparison has no verified one-year interval. The CSV does not
establish estimate accounting-basis alignment, target fiscal years, or exact dates.

Expected profitability is unavailable because NTM income cannot be combined with
annual revenue without matching forecast periods. Historical net margin remains a
declared dependency as approved; its presence does not establish forecast alignment.
No proxy or speculative period-resolution machinery is used. The reserved design,
for future separately approved compatible inputs, is:

```text
expected_margin = 100 * expected_income / expected_revenue
improvement_pp = expected_margin - historical_margin
profitability = 0.5 * clamp(expected_margin/2, 0, 10)
              + 0.5 * clamp(5 + improvement_pp, 0, 10)
```

Nonpositive expected margin would score zero. This calculation is not activated.

### Coverage, admission, and compatibility

v2.1 retains 79 distinct scoring fields and policy `fundamental-admission-v3` /
`fundamental-admission-shadow-v3`. v2.2 has exactly 76 fields and registers policy
v5 / shadow-v5. The certified field count is resolved from the actual model using
an explicit two-entry mapping. Unknown versions and cross-version counts fail closed.
v2.2 also rejects fractional/non-finite count evidence rather than truncating it.
All pre-existing consistency, identity, warning, edge, and threshold checks remain.
The overall minimum remains 65%; the component minimum remains 50%.

Annual EPS, quarterly EPS, and NTM EBIT move to `coverage_only_fields` for v2.2.
Their priority penalties remain identical, and annual EPS/NTM EBIT retain their
existing warning dependencies. Historical income, revenue, margin, cash, and CAPEX
join the forward declaration. Those historical fields already belonged to other
components; retiring three distinct estimates changes the union from 79 to 76.
Coverage-only fields remain in priority penalties, not the component-field union.

Frozen Core model selection determines both formula dispatch and readiness-policy
registration. Retained v2.1 readiness configuration remains exactly v3; v2.2 records
the v5 compatibility registration. Shadow ledgers record the evaluated policy's
actual version. No hashing, retained evidence, or temporal validation is weakened.

Field-presence coverage is different from mathematical availability. Policy v5
requires the producer's frozen `debug_json.forward_financial_evidence_available`
boolean to be literal true for v2.2. It is computed from available financial
subcomponents of the accepted formula, excluding analyst rating. A financial score
of zero is usable evidence; false, missing, or invalid facts remain UNDECIDED. This
additive evidence fact was explicitly approved before implementation. Its content
is sealed by the existing evidence mechanism; historical payloads are not rewritten.
v2.1 ignores the new fact and retains its original serialized admission input shape.

The repeat comparison restores COKE to UNDECIDED without altering its neutral 5.0
score, any penalty, or any of the other 126 admission outcomes. The 65%/50% coverage
thresholds remain unchanged. Existing v2.2 policy-v4 candidate payloads lacking the
new fact fail closed under the revised v5 policy.

The sealed `config/fundamentals_v2.yaml` remains the byte-identical v2.1 profile.
New computations and the GUI load `config/fundamentals_v2_2.yaml`; frozen delivery
continues to use its retained model. This keeps historical protected source hashes
valid while giving v2.2 its own explicit source profile.

For identical inputs, raw total-score change is exactly
`0.08 * (new_forward - old_forward)`, with absolute maximum 0.8. Existing total-score
rounding/clamping and score-derived warnings can change classifications. Historical
production scores are not recalculated. See
`docs/verification/forward-quality-v22/implementation-report.md` and its row-level
comparison artifacts for validation and release blockers.
