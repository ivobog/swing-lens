# CERI Run 102 Targeted Remediation Report

## Outcome

The known-good evidence path is now proven in deterministic vertical tests, and
the live NVDA Run 102 estimate source records replay successfully in memory:

```text
EODHD raw → licensed source projection → normalized EPS → PIT eligibility
→ revision magnitude/breadth/acceleration → component ledger
```

The current deployed Run 102 database was deliberately not altered. The Golden
10 code/fixture gate passes, but broad 200-ticker recertification is **not
authorized** until migration `0043_ceri_run102_relative_evidence` is applied and
a small live post-deploy capture verifies reported earnings plus an accepted
event/price window.

## Exact root causes

### EPS revision N/A

Run 102 EODHD source records contained numeric current EPS, 7/30/60/90-day
baselines, counts, analyst sample, observation reference, and fingerprints.
Missing currency sent all values through the absolute currency-conversion path,
which nulled consensus and canonical scale before same-provider relative
eligibility. Breadth was also calculated only after magnitude baseline success.
Finally, the period-slot selector rejected a provider-defined current quarter
when its fiscal end preceded the cutoff, even though the result had not yet been
reported.

The fixes preserve provider-scale EPS only for response-bound
`SAME_PROVIDER_RELATIVE` comparison, compute dimensionless breadth independently,
honor provider slots across the fiscal-close/report lag, and emit exact rejection
codes. Currency remains required for absolute or cross-provider comparison.

Live NVDA replay now produces 7d `0%`, 30d `0.1442307692%`, 90d
`6.7220002049%`, breadth `1.0`, and numeric acceleration. Details are in
`CERI_RUN102_EPS_DIAGNOSTIC_TRACE.md`.

### Surprise N/A

The reported EODHD schema uses snake-case report/result fields. The adapter read
only alternate camel-case fields and used fiscal `date` as report date, so actual,
estimate, and surprise became null. The licensed projection then discarded event
kind, acquisition policy, and report-time consensus semantics. Existing Run 102
rows cannot be reconstructed because those values were never retained.

The adapter now maps both schemas without losing numeric zero, reported and
upcoming requests remain distinct, lineage survives storage, future null-actual
events are excluded from Surprise, and report-time consensus is PIT-safe.

### Catalyst positive control

Pass. A scheduled material regulatory event whose structured related symbols
contain the target ticker survives source projection and normalization, is
issuer-relevant and selected, and produces nonzero pending Event Risk. Structured
cross-issuer exclusion, query-ticker-only input, completed events, and rejected
reviews remain ineligible. The Run 102 defect was loss of structured relevance,
reason, and expected date at the licensed projection boundary.

### Price Response root cause

Both rebuild and capture could select upcoming/null-actual earnings, unaccepted
guidance, or issuer-unverified catalysts. A company with no usable parent emitted
no diagnostic feature, while old attempts collapsed missing price windows into a
generic warning. Parent selection is now fail-closed and no-parent state persists
`NO_ACCEPTED_EVENT`; timestamp, price-data, and elapsed-window failures are
separate. A deterministic accepted earnings event with IB stock/benchmark bars
produces Price Response quality `6.0`.

## Implementation summary

- Estimate normalization preserves unknown-currency EPS only inside a
  provider-response-relative identity boundary; normalization version is 1.2.0.
- Migration 0043 rehydrates only qualifying existing EPS derivatives and leaves
  canonical currency null. It does not touch guidance acceptance or thresholds.
- Baseline eligibility exposes exact period, scale, provider, currency, origin,
  and observation-reference first causes.
- Revision counts produce breadth without currency; missing/sparse analyst sample
  remains an explicit confidence warning rather than a magnitude veto.
- EODHD reported earnings mapping accepts official snake-case fields and retains
  upcoming/reported lineage across licensed storage.
- Catalyst structured relevance and expected dates survive storage.
- Price Response accepts only reported earnings, literal-true guidance, or
  issuer-relevant non-rejected catalysts; exact unavailability persists.
- Opportunity ledgers expose dominant component blockers rather than generic
  unavailable labels.
- API/UI distinguish source freshness, normalized evidence, eligible evidence,
  selected evidence, and dominant blocker. Full warning lists remain available.

No weights changed. The 60% Opportunity threshold is unchanged. The Confidence
hard gate is unchanged. Missing evidence is never converted to zero. SEC
`accepted_for_scoring` remains literal-true allow-list logic.

## Verification

- Baseline: `280 passed, 1 warning in 19.93s`.
- Final focused CERI suite: `299 passed in 35.62s`.
- Explicit SEC/API/UI/Golden subset: `25 passed in 4.67s`.
- Ruff: all checks passed.
- Alembic: single head `0043_ceri_run102_relative_evidence`.
- Golden 10: PASS, 10/10 traces, 12 stages each.

Twenty new test functions cover provider projection, normalization,
comparability, period slots, breadth, earnings, catalyst controls, Price Response,
component reasons, API/UI diagnostics, migration safety, and Golden 10 rules.

## Availability classification

Now proven available:

- same-provider EPS revision magnitude with missing currency;
- numeric zero and negative revision values;
- revision breadth and EPS acceleration;
- historical reported-earnings Surprise fixture;
- structured pending catalyst positive control and negative control;
- accepted-event Price Response fixture;
- literal-true SEC guidance (unchanged);
- staged API/UI evidence diagnostics.

Still provider/data limited:

- existing Run 102 earnings rows lack retained historical actual/consensus values
  and require a fresh provider capture;
- catalyst availability depends on genuine structured issuer-relevant events;
- Price Response depends on an accepted event plus adequate elapsed IB daily bars;
- absolute/cross-source monetary comparison remains unavailable without verified
  currency, by design;
- Revenue acceleration remains dependent on accumulated comparable history.

Still deployment/application limited:

- migration 0043 has not been applied to the target live database;
- the corrected feature rebuild/capture has not yet been run against a targeted
  deployed cohort;
- old malformed earnings projections are intentionally not promoted.

## Certification decision

The deterministic Golden 10 passes, including nonzero Risk with Unrated
Opportunity, valid/rejected SEC guidance, reported/upcoming earnings, and catalyst
positive/negative controls. Broad 200-ticker recertification remains **not
authorized**. The next safe gate is: deploy, apply 0043, run the focused suite,
capture the Golden 10 tickers live, and confirm at least one real reported
earnings Surprise and one accepted-event price window before widening the cohort.
