# Forward Quality v2.2 implementation report

Date: 2026-11-19 (Europe/Zurich). Branch: `codex/forward-quality-v22`.
Baseline: `c015e1d26e67f31bc63acbfe1049660ec44e342d`; accepted assessment: `c7be7e9`.
The implementation commit hash is supplied in the delivery message and branch log.

## Verdicts

| Area | Verdict | Evidence |
| --- | --- | --- |
| Mathematical correctness | PASS | Approved literal thresholds/special cases; focused tests; 127 offline rows; bounded and repeatable |
| Model/contract compatibility | PASS | Explicit v2.1=79 / v2.2=76 checks, exact historical replay, version-specific frozen registration |
| Integration / release readiness | BLOCKED | Protected golden provenance rejects the changed config; COKE's newly permitted admission needs review; mandatory release certification is incomplete |

The original count conflict is resolved under the explicit authorization. The
candidate is implemented and isolated; it has not been merged, deployed, or released.

## Implemented mathematics

```text
Forward = sum(w_i * score_i for available i) / sum(w_i for available i)

earnings growth       0.30
annual revenue / TTM  0.25
profitability        0.20  [unavailable for the existing CSV]
annual cash change   0.10
CAPEX sustainability 0.10
text analyst rating  0.05
```

No available term gives 5.0. Scores are clamped to [0,10] with existing four-decimal
rounding. The outer forward weight stays 0.08; all other outer weights are unchanged.

Growth is `100 * (estimate / historical - 1)`. Its score is
`clamp(5 + 5*growth/threshold, 0, 10)`, selecting positive/negative threshold magnitudes
30/25 for earnings, 20/20 for revenue, and 30/30 for cash. Positive earnings becoming
nonpositive score 0; nonpositive earnings becoming positive score 7; both nonpositive
score 0. Revenue/cash require positive historical amounts and nonnegative estimates.

CAPEX growth uses absolute amounts. Its score is
`clamp(10 - max(0, CAPEX_growth - earnings_growth)/5, 0, 10)` when both earnings are
positive and growth comparisons are valid. Known nonpositive expected earnings score
0 with a valid nonzero-denominator CAPEX pair. Loss-to-profit earnings or a zero
historical CAPEX denominator make CAPEX sustainability unavailable.

Exact normalized analyst labels map Strong Buy=10, Buy/Outperform=7.5,
Hold/Neutral=5, Sell/Underperform=2.5, Strong Sell=0. Numeric/unrecognized/missing
ratings are unavailable. There is no additional v2.2 analyst bonus.

The original positivity/sign/analyst-bonus formula remains intact for v2.1.
[Fundamental scoring documentation](../../fundamental_scoring.md) records both
formulas, all thresholds, the reserved profitability specification, and limitations.

## Inputs and limitations

The accepted [input assessment](input-assessment.json) is retained unchanged. The
latest source is the accepted 111-company CSV, SHA-256
`2247b49dc3f4e8fd88a1f96d50d095b1c17b8ba30a06ae6d2822bf199d532331`.
The comparison checks that exact hash and row count before execution.

Field-specific matching recognized currencies are required for monetary pairs;
missing/mixed/unknown metadata makes the comparison unavailable. No FX conversions
or price-currency substitutions are used. The shared parser is unchanged. Local
finite guards reject overflow/non-finite amounts, and Decimal growth arithmetic
handles extreme finite ratios without `inf - inf` or invented denominator cutoffs.

Annual revenue versus TTM is an annual-estimate comparison, not verified forward YoY
growth. Annual cash is a snapshot comparison with an unverified target interval.
Target fiscal dates and estimate accounting-basis reconciliation are unavailable.
NTM income and annual revenue cannot establish matching forecast periods, so
profitability is always unavailable here; its weight is redistributed, not zeroed.
With all five supported subcomponents available, their denominator is 0.80.

Stored production raw rows/evidence IDs were unavailable in the accepted assessment.
The comparison uses local CSVs and does not claim production point-in-time evidence.
No external providers or production database writes were used.

## Version-specific compatibility

| Model | Formula / forward fields | Distinct fields | Admission version |
| --- | --- | ---: | --- |
| `fundamentals_v2.1` | Original formula / original eight fields | 79 | v3 / shadow-v3 |
| `fundamentals_v2.2` | New formula / ten declared fields | 76 | v4 / shadow-v4 |

The certified counts come from a fixed two-entry map, never from score evidence.
Wrong counts and unknown models fail closed. v2.2 rejects fractional/non-finite
counts; historical v2.1 count parsing remains unchanged. Overall/component minimums
stay 65%/50%, with all existing consistency, identity, warning, edge, and priority
checks retained. Exactly 65% cannot be represented by an integer numerator over
either 79 or 76: tests check the nearest valid counts on both sides and reject
inconsistent evidence claiming exactly 65%. Component 49.99%, 50%, and 50.01% are tested.

Annual EPS, quarterly EPS, and NTM EBIT become coverage-only for v2.2. Their priorities,
penalties, and applicable warnings remain. Historical income/revenue/margin/cash/CAPEX
join forward dependencies; they already occur elsewhere, leaving 76 unique fields.

Frozen Core model selection dispatches the formula and matching readiness registration.
The new readiness registration uses list records for model labels, because dots in
mapping keys are path separators in the existing snapshot format. The shared hashing
implementation is unchanged. Old registrations/hashes are reproduced exactly. Shadow
records now use the evaluated decision's policy version, preserving v3 for old inputs
and recording shadow-v4 for v2.2.

Configuration hashes:

- v2.1: `b919aaea0ed470f293fce0f9abe82173a934b77736722349eb0a8563863b69e1`
- v2.2: `eecc43d64ca72568fe7fe7d0f16b49d31b9170ac87e4b91e335f29bbb623e025`

## Full company comparison

The [complete CSV table](results/company-comparison.csv) and
[structured row results](results/comparison.json) contain **all 111 latest companies**
plus **eight loss-making and eight turnaround companies** from older local uploads.
Eight older files were inspected to select those 16 unique additional tickers across
seven sectors. Every row includes source file/hash/row, requested growth/comparison
metrics, six subcomponent scores/availability, both forward/total scores, all nine
other component scores on both sides, penalties, coverage, classifications, warnings,
and admission reason codes. Blank growth for negative historical earnings means no
valid percentage denominator; the transition score remains explicitly available.

**Admission results are conditional policy simulations.** Both models receive the
same valid-shaped SHA-256 identity, positive placeholder evidence ID, and aware audit
cutoff. The placeholder is not a retained production evidence ID. These comparisons
isolate coverage/score-warning effects; they do not assert production admission,
authenticated provenance, or temporal certification. No validation rule is disabled.

### Mathematical changes

| Latest 111 companies | v2.1 | v2.2 |
| --- | ---: | ---: |
| Mean forward score | 9.9126 | 6.8065 |
| Median forward score | 10.0000 | 7.1880 |
| Mean Fundamental Score | 6.8875 | 6.6390 |
| Median Fundamental Score | 6.8599 | 6.6591 |

Mean total change: -0.2485; median: -0.2221. There are no increases in the latest
sample; COKE is unchanged. Largest decreases are CINF -0.6107, SPG -0.5551,
AMZN -0.5540, FRPT -0.5359, and WTRG -0.5193. The old model's near-ceiling positivity
scores explain the broad decline; no thresholds were retuned to force a distribution.

For the 16 older examples, mean forward scores are 6.7059→3.9900 and mean total
scores 4.3314→4.1141. BBIO increases +0.1384 and ROIV +0.0738; their loss scores remain
zero, while other available subcomponents provide support. This is the approved
weighted model, not evidence of profitability or a new admission exception.

| Company | Old forward | New forward | Old total | New total | Difference |
| --- | ---: | ---: | ---: | ---: | ---: |
| CINF | 8.2592 | 0.6250 | 6.6228 | 6.0121 | -0.6107 |
| SPG | 10.0000 | 3.0612 | 6.3830 | 5.8279 | -0.5551 |
| AMZN | 10.0000 | 3.0744 | 6.1082 | 5.5542 | -0.5540 |
| WTRG | 10.0000 | 3.5079 | 4.4009 | 3.8816 | -0.5193 |
| NDAQ | 10.0000 | 4.9702 | 7.5733 | 7.1709 | -0.4024 |
| COKE | 5.0000 | 5.0000 | 5.9975 | 5.9975 | 0.0000 |
| BBIO (older loss) | 2.8962 | 4.6266 | 3.7725 | 3.9109 | +0.1384 |
| ROIV (older loss) | 2.8962 | 3.8182 | 3.3449 | 3.4187 | +0.0738 |

### Actual mathematical availability

| Subcomponent | Latest available | Rate |
| --- | ---: | ---: |
| Earnings growth / transitions | 108/111 | 97.30% |
| Annual revenue comparison | 110/111 | 99.10% |
| Profitability | 0/111 | 0.00% |
| Annual cash comparison | 101/111 | 90.99% |
| CAPEX sustainability | 78/111 | 70.27% |
| Analyst category | 110/111 | 99.10% |

These rates are not field-presence coverage. For example, cash denominators and CAPEX
validity remove some rows whose individual fields are present. Older-example rates
and all descriptive statistics are in [comparison-summary.json](results/comparison-summary.json).

### Classification changes

Eleven latest companies change classification: ZGN becomes Quality risk through the
existing forward-weak warning; TXRH, ZETA, TMO, and GEO move from High-quality quant
to Mixed but interesting; ULTA, SLDE, MANH, CTAS, V, and FAST move from Clean compounder
to High-quality quant. No older-example classification changes. Classification and
warning source rules are unchanged; the new scores trigger these existing rules.

### Coverage changes

Latest mean overall field coverage is 95.164%→95.032%; mean forward field coverage
is 94.595%→95.495%. The lowest new overall coverage is 77.63%, above the unchanged
65% gate. New forward coverage has a minimum of 60%, reflecting historical dependencies.
Older examples average 91.062%→90.791% overall coverage. No threshold was relaxed.

### Admission impact and investigated anomalies

Conditional inclusion is 108→109 of 111; the older examples remain 11/16. There are
**no newly excluded companies**. HNGE and CHYM remain excluded for inadequate component
coverage. The single change is **COKE: UNDECIDED→INCLUDED**.

COKE's old forward field coverage was 1/8 (12.5%); its new coverage is 6/10 (60%).
Historical income/revenue/margin/cash/CAPEX plus the present `No rating` text support
field-presence coverage. Nevertheless, every actual forward subcomponent is unavailable.
Both model scores remain 5.0 forward and 5.9975 total; its Value trap risk classification
and balance-sheet warning are unchanged. The original reason
`component_coverage_below_minimum` is replaced by the existing bounded-missing and
classified-warning reasons. The difference is entirely explained by approved field
dependencies, not a new source, weaker threshold, or forced parity adjustment.

This newly permitted zero-forecast admission is economically questionable and is
treated as a **release-review blocker** under the request's admission-impact stop
condition. No new forecast-availability gate has been invented to fix it. There is
no integration, promotion, or production rollout in this task.

Other limitations are visible rather than hidden: NDAQ's annual revenue comparison
is -35.21% despite +22.02% NTM earnings growth; accounting/fiscal-basis differences
cannot be verified from the CSV. AMZN's -19.16% earnings comparison, -41.70% annual
cash comparison, and +25.36% CAPEX growth explain its lower result. CINF's large
declines saturate the approved negative thresholds. No external assertions about
these companies or independent accounting reconciliation are implied.

## Historical and scope-isolation evidence

[historical-compatibility.json](results/historical-compatibility.json) records:

- Nine unchanged component function AST hashes, compared directly with baseline main.
- An unchanged AST for the complete original v2.1 forward function.
- Unchanged classification, total weighting, and clamp function ASTs.
- No diff to the coverage service, warning service, shared numeric parser, or aliases.
- **752 identical complete admission decisions** across frozen runs 32/33: 376 rows
  evaluated in both ACTIVE and SHADOW modes against the original baseline function.
- Unchanged frozen fixture hashes, plus exact old readiness snapshots/hashes in
  ACTIVE, SHADOW, and LEGACY modes.

Four frozen v2.1 scorer cases compare the entire result/debug payload with the original
main implementation, including the old config hash. New fixtures are historical
replay regression records, not independent financial oracles or approved release goldens.
Existing historical golden artifacts were not regenerated or edited.

All **1,143 other-component comparisons** (127×9) match exactly, and all 127 penalties
match. Per row the raw delta is `0.08 * (new_forward - old_forward)`; the maximum observed
absolute raw delta is 0.610736, within 0.8. A literal boundary test also reaches exactly
-0.8 without changing another component or the penalty. Final scores retain existing
rounding and clamping. No historical production scores/evidence were modified.

## Validation

The main focused command covered:

```powershell
python -m pytest tests/test_forward_quality_v22.py tests/test_fundamental_components_v2.py tests/test_fundamental_ranker_v2.py tests/test_fundamental_coverage_service.py tests/test_fundamental_admission_policy.py tests/test_fundamentals_v2_acceptance.py tests/test_fundamental_ranker.py tests/test_config_files.py tests/test_operational_fundamental_admission.py -q
```

Result: **275 passed** in 60.02s. Two subsequently added boundary/warning cases passed
separately (0.83s). Nine selected existing frozen-configuration cases passed (4.13s).
There was one existing Starlette/httpx deprecation warning per run. The initial
frozen-registration failure was fixed before this passing run; no assertion was
weakened. This is focused subsystem validation, not full regression or certification.

The required repository-wide `python -m ruff check app tests scripts` passed.
`python -m scripts.docs.check_route_inventory` also passed against the isolated
checkout. The initial direct-path script invocation imported the original checkout
through the reused virtual environment's editable installation; an import-path
probe confirmed this and the invocation was corrected. No route document was
rewritten. The receipt records the corrected result and both import paths. The
diff whitespace check passed while recognizing the existing CRLF line endings
(`git -c core.whitespace=cr-at-eol diff --check`).

The six-case model/count matrix, historical dispatch, mathematical edge cases,
currency/period handling, available-weight redistribution, malformed evidence,
unchanged thresholds, frozen snapshot rehydration, and correct shadow ledger versions
are covered. New models cannot execute under the wrong version/dependency declaration.

The mandatory certification prerequisite was attempted with the existing workflow:

```powershell
python -m scripts.qa.check_certification_registry --check --diff-base c7be7e9
```

It **failed closed**, exit 1:
`golden provenance prerequisite: PROVENANCE_ARTIFACT_HASH_MISMATCH: config/fundamentals_v2.yaml`.
[release-prerequisite.json](results/release-prerequisite.json) retains the receipt;
pipeline result is NOT_EVALUATED. This is a rejected provenance prerequisite, not a
test failure repaired by updating a hash. No verification guard was bypassed.

The source-bound release plan and supported A04/A16 certification, protected golden
revision with independent financial derivation/authenticated owner acknowledgement,
full regression, and required CI remain incomplete. No broad integration tests,
browser runs, full pipelines, live-provider validation, merge, or deployment were run.
Existing GUI/API fresh-version expectations were updated but their broader test lanes
remain for required CI. The protection workflow is documented in
[golden_update_policy.md](../../testing/golden_update_policy.md).

## Modified files and reasons

| File | Reason |
| --- | --- |
| `app/services/fundamental_components_v2.py` | New formula, local validity guards, subscores, preserved legacy dispatch/dependencies |
| `app/services/fundamental_ranker_v2.py` | Validate/select the declared model, including frozen maps; correct version-specific fields |
| `app/services/fundamental_admission_policy.py` | Certified 79/76 counts, new v4 registration, unchanged v2.1 behavior |
| `app/services/decision_effective_configuration.py` | Freeze the appropriate policy registration from retained Core model authority |
| `app/services/contextual_consumer_eligibility.py` | Record the evaluated shadow policy version; no eligibility rule change |
| `config/fundamentals_v2.yaml` | Select v2.2 and accurately declare formula/coverage-only fields |
| `tests/test_forward_quality_v22.py` | Focused new mathematical, configuration, replay, integrity, and admission tests |
| `tests/fixtures/fundamentals_v21.yaml` | Byte-preserved baseline profile for historical replay |
| `tests/fixtures/forward_quality_v21.json` | Frozen full old scorer results for exact compatibility checks |
| `tests/test_operational_fundamental_admission.py` | Exercise v2.1/v2.2 ACTIVE/SHADOW metadata at the existing boundary |
| `tests/test_fundamental_ranker_v2.py` | Current default model expected version |
| `tests/test_fundamentals_v2_acceptance.py` | Current score/export model expected version |
| `tests/test_config_files.py` | Current profile expected version |
| `tests/test_gui_phase1.py` | Current displayed model expected version only |
| `tests/test_run_actions_phase3.py` | Current new-score expected version only |
| `tests/integration/test_operational_admission_postgresql.py` | Current new-upload expected version only; test not executed here |
| `scripts/qa/compare_forward_quality_v22.py` | Reproducible offline comparison and conditional admission analysis |
| `docs/fundamental_scoring.md` | Old/new formulas, thresholds, inputs, limitations, and coverage interpretation |
| `docs/versioning.md` | Dual model/policy compatibility and historical preservation |
| `CHANGELOG.md` | User-visible model change |
| `docs/verification/forward-quality-v22/implementation-report.md` | This report |
| `docs/verification/forward-quality-v22/results/company-comparison.csv` | Full table for all 127 companies |
| `docs/verification/forward-quality-v22/results/comparison.json` | Detailed row-level evidence |
| `docs/verification/forward-quality-v22/results/comparison-summary.json` | Separate score/classification/coverage/admission summaries |
| `docs/verification/forward-quality-v22/results/historical-compatibility.json` | Exact replay/source isolation evidence |
| `docs/verification/forward-quality-v22/results/performance.json` | Local advisory arithmetic timing |
| `docs/verification/forward-quality-v22/results/release-prerequisite.json` | Retained fail-closed release prerequisite receipt |

No schema, REST/GUI payload, provider, dependency, job, pipeline stage, feature flag,
Combined/Ranking formula, Technical, CERI, MSI, lifecycle, or probability change is
included. The dirty `release/swinglens-msi-live` checkout remains untouched.

Git diff scope: **27 files**, comprising five production service files, one existing
model profile, and focused tests, documentation, and offline evidence. Most added
lines are the complete company comparison and historical replay fixtures.

## Performance and remaining release work

The arithmetic remains constant time on one mapped row, with a fixed number of
numeric parses and no network/database operations. The local five-batch median for
the forward component was approximately **246µs (v2.1) versus 611µs (v2.2)** per row,
about +365µs/row or 40ms for 111 rows. This is an advisory local measurement, not a
pipeline benchmark or release certificate. Decimal/validity guards incur a small
absolute CPU cost; there are no new dependencies or background jobs.

Before integration/release, review the COKE coverage-versus-forecast admission
expansion without weakening validation or silently introducing a new gate. Then
complete the protected golden proposal/independent derivation/authenticated owner
workflow and mandatory source-bound CI/certification. The current implementation
does not alter protected oracle hashes, claim their approval, or certify production.
