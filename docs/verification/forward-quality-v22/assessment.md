# Forward quality v2.2: input assessment and contract blocker

Assessment date: 2026-11-19 (Europe/Zurich). **Verdict: BLOCKED.**

## Executive summary

Phase 1 found sufficient local inputs for several proposed comparisons, but the
required formula dependency changes conflict with the protected coverage and
admission contracts. Implementation stopped under section 7 of the request:

> If required dependency declarations cannot be aligned without changing a protected
> contract, stop and report the issue rather than bypassing validation.

No scoring formula, configuration, version registration, admission rule, or historical
evidence was changed. This commit contains only this assessment and its input audit.
It does **not** deliver or certify an implemented v2.2 model.

## Baseline and isolation

- Fetched `origin/main` successfully; baseline is
  `c015e1d26e67f31bc63acbfe1049660ec44e342d` (PR #85).
- Dedicated branch: `codex/forward-quality-v22`.
- Dedicated checkout: `.worktrees/forward-quality-v22`.
- The original `release/swinglens-msi-live` checkout contains extensive unrelated
  modifications; none were included or edited.
- Reviewed the nine requested source/config/documentation files, the numeric parser,
  column mapper, admission policy, effective configuration authority, and governance
  checklists.

## Verified inputs

The latest inspected upload is
`20261118_222945_77d1ca43_money money_2026-11-18 _1_.csv`: 111 companies across
15 sectors. SHA-256:
`2247b49dc3f4e8fd88a1f96d50d095b1c17b8ba30a06ae6d2822bf199d532331`.
The three preceding inspected uploads contain 109, 136, and 447 rows, respectively.
These uploads overlap and are not independent company populations.

[input-assessment.json](input-assessment.json) records source hashes, mapped headers,
numeric availability, rating frequencies, currency counts, and date-related headers
for all four files. It contains aggregate observations, not newly calculated scores.

| Canonical field | Available / 111 | Units and period | Compatibility assessment |
| --- | ---: | --- | --- |
| `net_income_ttm` | 111 | USD; trailing 12 months | Historical earnings baseline |
| `net_income_estimate_ntm` | 108 | USD; next 12 months | Comparable twelve-month amount to TTM earnings; estimate accounting basis is not supplied |
| `total_revenue_ttm` | 111 | USD; trailing 12 months | Strictly positive denominator must still be checked per row |
| `revenue_estimate_annual` | 110 | USD; annual fiscal estimate | Annual-estimate-versus-TTM comparison only; exact forward YoY timing is unverified |
| `net_margin_ttm` | 111 | Percent; trailing 12 months | Already-mapped historical margin; no extra scaling by 100 |
| `eps_estimate_annual` | 110 | USD per share; annual | No direct contribution in the proposed numeric formula; existing warning dependency remains |
| `eps_estimate_quarterly` | 110 | USD per share; quarterly | No direct contribution in the proposed formula; cannot be combined with annual EPS as an equivalent period |
| `ebit_estimate_ntm` | 108 | USD; next 12 months | No direct contribution in the proposed numeric formula; existing warning dependency remains |
| `capex_ttm` | 111 | USD; trailing 12 months | 105 positive, one negative, five zero amounts |
| `capex_estimate_ntm` | 79 | USD; next 12 months | All 79 observed estimates positive; absolute magnitudes are appropriate for the approved comparison |
| `cash_short_term_investments_annual` | 104 | USD; annual balance-sheet snapshot | Historical stock, not a twelve-month flow |
| `cash_short_term_investments_estimate_annual` | 104 | USD; estimated annual balance-sheet snapshot | Annual estimate versus historical annual balance; exact target year and interval are not supplied |
| `analyst_rating` | 110 recognized | Text | 36 Strong buy, 66 Buy, eight Neutral; one No rating is unavailable |

All present currency metadata for these monetary fields in the four inspected files
is USD. The alias map already includes corresponding field-specific currency columns,
as well as price and market-cap currencies. Price currency alone cannot establish
the reporting currency of financial amounts. Missing currency metadata must not be
replaced by an assumed currency or an external conversion.

Only `upcoming_earnings_date` is mapped as a date. The uploads contain no fiscal
period start/end, target estimate year, or metadata proving alignment of annual
revenue estimates and NTM net income. Therefore the requested expected profitability
subcomponent is **unavailable** for these rows; its 20% would be redistributed.
Annual revenue and cash comparisons must explicitly retain their snapshot/annual
interpretation. Exact forecast horizons, accounting-basis reconciliation, and
annual cash target chronology cannot be verified from the supplied headers.

Ratings are textual in all four inspected uploads. The existing bonus assumes lower
numeric ratings are better, but does not validate or document a 1-to-5 scale. That
numeric scale was **not confirmed** by local input evidence. Text categories can be
mapped exactly; `No rating` must not become Hold, zero, or a bonus.

Existing numeric parsing supports units/suffixes and rejects many invalid values.
Its string-number path can still produce non-finite floats from sufficiently large
numeric strings; a future forward-only implementation needs an explicit finite-value
guard, without altering the other nine components' shared parser behavior.

Stored raw upload rows could not be verified: access to the configured `.env` was
denied, and a connection using the application's default local database configuration
failed with `OperationalError`. The connection attempt specified
`default_transaction_read_only=on`, a five-second statement timeout, and a five-second
connect timeout. No database writes or provider calls were made.

## Exact dependency conflict

`COMPONENT_FORMULA_FIELDS` and `components.forward_quality_score.fields` currently
declare eight forward fields. The approved replacement removes direct numeric use of:

- `eps_estimate_annual`;
- `eps_estimate_quarterly`;
- `ebit_estimate_ntm`.

The replacement additionally depends on historical `net_income_ttm`,
`total_revenue_ttm`, `net_margin_ttm`, `cash_short_term_investments_annual`, and
`capex_ttm`. All five already occur in other components' field sets.

Consequently, aligning the forward dependencies and moving the three retired inputs
to `coverage_only_fields` reduces the distinct component-field union from **79 to 76**.
Field priorities and the missing-data penalty parameters can remain identical, but
this does not preserve the coverage/admission contract:

1. `fundamental_ranker_v2._validate_components` rejects non-formula component fields.
   A candidate configuration containing the five new historical dependencies is
   rejected by the current declaration, as expected before implementing it.
2. `_validate_coverage_only` rejects overlap with component formula fields. Keeping
   retired inputs simultaneously in the forward list and in `coverage_only_fields`
   cannot legitimately preserve the old count.
3. `fundamental_coverage_service.calculate_coverage_v2` derives the overall field
   denominator exclusively from the component-field union. Coverage-only inputs
   retain their priority penalties but are excluded from that denominator.
4. `fundamental_admission_policy.FUNDAMENTAL_SCORING_FIELD_COUNT` is fixed at **79**.
   `evaluate_fundamental_admission` rejects any other total, even with 100% coverage.
5. Moving fields also changes per-component coverage and can affect the existing
   50% admission gate. Registering v2.2 alone does not resolve this issue.

Read-only, synthetic contract probes used a finite score, valid label and identity,
all component coverages of 1.0, no missing fields, and internally consistent aggregate
coverage. These are validation probes, **not company results**:

| Model | Total / available fields | Admission | Reason |
| --- | ---: | --- | --- |
| `fundamentals_v2.1` | 79 / 79 | INCLUDED | `complete_score_evidence` |
| `fundamentals_v2.1` | 76 / 76 | UNDECIDED | `field_coverage_contract_invalid` |
| `fundamentals_v2.2` | 79 / 79 | UNDECIDED | `scoring_model_version_unsupported` |
| `fundamentals_v2.2` | 76 / 76 | UNDECIDED | `field_coverage_contract_invalid`, `scoring_model_version_unsupported` |

The version failure could be addressed by the requested minimal version registration.
The separate field-count and component-coverage conflict requires an explicit scope
decision. Neither retaining inaccurate formula declarations nor relaxing validation
is an acceptable workaround.

## Formula status and company comparison

**Implemented mathematical change: none.** Current v2.1 remains active. Its forward
base is the available-value weighted average of positivity scores for annual EPS,
quarterly EPS, annual revenue, NTM net income, NTM EBIT, and annual estimated cash,
with weights `1.0, 0.7, 0.8, 1.0, 1.0, 0.4`, plus CAPEX sign scoring with weight `0.3`.
Positive amounts score 10, nonpositive amounts 0; CAPEX scores 7 if negative and 5
otherwise. A missing base is 5. The existing analyst bonus is added and clamped.

The approved, **unimplemented** replacement is:

```text
F = available_weighted_mean(
    earnings_growth_score * 0.30,
    annual_revenue_vs_TTM_score * 0.25,
    profitability_score * 0.20,
    annual_cash_comparison_score * 0.10,
    capex_sustainability_score * 0.10,
    analyst_rating_score * 0.05
)
```

Unavailable terms are excluded; no terms available means 5. Growth normalization is
`clamp(5 + 5*g/t, 0, 10)`, with positive/negative threshold magnitudes of 30/25 for
earnings, 20/20 for revenue, and 30/30 for cash. CAPEX is
`clamp(10 - max(0, capex_growth - earnings_growth)/5, 0, 10)` under the requested
profit/currency/denominator guards. Compatible profitability would equally combine
`clamp(expected_margin/2, 0, 10)` and `clamp(5 + margin_improvement_pp, 0, 10)`;
nonpositive expected margin scores zero. All special-case rules remain as requested;
none has been activated or substituted with a proxy.

The outer forward weight remains 0.08. The planned raw total-score difference is
`0.08 * (new_forward - old_forward)`, bounded by 0.8 before existing rounding/clamping.
This is a design invariant, not a verified implemented-model result.

Real input data is available, but the 30-company old/new score comparison was not
performed after the explicit stop condition was established. There is no validated
new model to compare. New averages, medians, subcomponent scoring availability rates,
classification changes, largest changes, and nine-component equivalence claims are
therefore **not available**, rather than fabricated.

The latest upload has 111 profitable historical earnings values and no unprofitable
companies. It alone cannot satisfy the requested loss/turnaround sample coverage;
older uploaded populations would need inspection after scope resolution.

## Validation, compatibility, and release status

Focused baseline command (using the existing repository virtual environment):

```powershell
python -m pytest tests/test_fundamental_components_v2.py tests/test_fundamental_ranker_v2.py tests/test_fundamental_coverage_service.py tests/test_fundamental_admission_policy.py -q
```

Result: **38 passed, one existing dependency deprecation warning, in 30.34 seconds**.
These validate the unchanged baseline; they do not establish acceptance of v2.2.
The in-memory configuration check and four admission probes reproduced the conflict.
No integration tests, browser tests, full regression suite, or pipelines were run.

`docs/governance/model_change_checklist.md` and `CONTRIBUTING.md` require full
regression/release gates for scoring changes. Those checks exceed the requested
focused execution scope and remain release blockers for any later implementation;
no checks, branch protections, or certification requirements were bypassed.

The model version remains `fundamentals_v2.1`, because no formula changed. Historical
versions and all historical scores/evidence remain untouched. No schema/API/GUI,
provider, policy, dependency, pipeline, or runtime changes were made. Performance
impact on scoring is zero. The original checkout was not modified.

Git diff: two new documentation/evidence files under
`docs/verification/forward-quality-v22/`; no production or test code changes.

## Required scope decision

Resumption requires authorization to resolve the coverage/admission contract, for
example by explicitly versioning the 76-field v2.2 coverage and its admission
certification, or by separately defining preserved upload coverage and actual formula
dependencies. Either option exceeds the current protected-contract constraints.
No such change is included in this commit. Final verdict: **BLOCKED**.
