# A05 v3 input and arithmetic provenance

This artifact was authored before the A05 pipeline executions. No output database,
production calculator, output selector, exporter or stored score supplied an
expected financial number. `author_v1.py` in the A05 audit evidence records input
authoring and the one-specimen Decimal worksheet. It is not a golden update tool.
The immutable manifest hashes the contents of this version. Changes require a
new version and review; never regenerate it from actual results.

## Input provenance

`universe.csv` preserves the existing cardinality test's first canonical ALFA
financial exemplar, fills only absent fields from `financial_inputs.json`, and
changes only Symbol/Description to the established exact ten. Header order and
raw row order are fixed. `financial-source.json` gives the canonical input values.
All ten rows intentionally share these financial values; this is not ten diverse
fundamental scenarios. The raw semantic identity is input CSV SHA-256 + row number
+ symbol, not a database primary key. The legacy CSV supplies no provider fiscal
revision, receipt provenance or native currency proof. Their absence is recorded,
not filled with invented historical facts.

`market-data.json` preserves the two existing arithmetic bar generators as literal
Decimal strings. Cached prices: base `35 + sum(ord(symbol)) mod 90`, slope
`0.06 + (seed mod 7)/100`, except canonical CHAR/RISK slope -0.04; open=close-0.30,
high=close+1.10, low=close-1.00, volume=800000+100*seed+500*i. IB replay uses the
existing separate base/slope/offset/volume contract. The 320 sessions are fixed.
Short canonical prerequisite histories start at progression index zero with the
last N session dates, preserving the previous 90/180-bar fixture convention.
Contracts/feeds/currency/timeframe are explicit. Only target ten prediction inputs
are claimed; canonical/benchmark/ETF data remain declared prerequisites/replay.

`calendar.json` independently enumerates weekdays minus the listed 2025/2026 US
equity closures, including the 2025-01-09 exceptional closure. It records the v2
calendar contract, early closes, session Oct 26, prior session Oct 23 and daily
bar availability 20:15 UTC. Session identities do not depend on today's date.
Source contract: `app/services/us_market_calendar.py`, `market_clock_service.py`.
They were read to identify policy; their functions did not generate expected dates.

`provider-replay.json` preserves the existing EODHD estimates/catalysts, explicit
empty earnings and SEC guidance payloads. Its explicit receipt/retrieved timestamps
equal the replay publication timestamps. This is a deterministic fixture receipt
fact, not a claim that the vendor actually supplied these records historically.
Known-at may not exceed its relevant cutoff. Historical estimates are retained
before admission; the real provider DAG still dispatches and deduplicates them.
`history.json` and the content-pinned `prerequisites-source.py.txt` declare prior
Setup selection, canonical CERI prerequisites and decoy Winner history. The copied
fixture is retained input provenance and is never executed as a second pipeline.

`configuration.json` contains the frozen YAML data (ISO date strings where YAML
has dates); the manifest also hashes original YAML bytes. `runtime-settings.json`
pins the native delivered execution switches and the existing certification
overrides, including v5 disabled with its existing shadow settings. Only the
handoff flag differs between variants. The normalized envelope is UTF-8 JSON,
sorted keys, compact separators, ensure_ascii=True, preserving array order, plus
the selected flags. Its hash is a configuration identity, not financial truth.

## Fundamental specimen, full ten-component worksheet

Source policy: `fundamental_components_v2.py:145-396` and
`fundamental_ranker_v2.py:125-165,212-240`, frozen configuration
`fundamentals_v2.yaml`. `fundamental-derivation.json` lists every term's source
field/value, normalization equation, four-place normalized literal and weight,
the weighted numerator/denominator, trend bonus, and rounded result. It is a
reviewable worksheet for this one input specimen, not a reusable scoring engine.

Higher-is-better terms use clamp((x-poor)/(excellent-poor)*10,0,10); lower-is-better
uses clamp((poor-x)/(poor-excellent)*10,0,10). Positive amounts score 10. Special
ratios/ideal bands are individually written in the worksheet. For example Sloan
2 gives 9.0000; FCF/net-income=900m/800m=1.125 clamps to 10; negative capex burden
is 10-(100m/900m)*3=9.6667; current ratio 3.2 scores
(6-3.2)/(6-3)*10=9.3333. The growth weighted sum is 87.51428 / 9.3 = 9.4101 after
rounding. Complete source fields imply coverage 10 and missing-data penalty 0.
No stress/weakness predicate in the reviewed warning policy is satisfied.

| Component | Expected |
|---|---:|
| Growth | 9.4101 |
| Profitability | 10.0000 |
| FCF | 8.9363 |
| Earnings | 9.5500 |
| Capital efficiency | 10.0000 |
| Balance sheet | 9.2826 |
| Valuation | 6.5515 |
| Forward | 10.0000 |
| Shareholder | 7.9111 |
| Liquidity | 9.4865 |

Weighted sum under the frozen .13/.13/.12/.12/.12/.12/.10/.08/.05/.03 weights,
minus zero missing penalty, rounds to **9.1909**, label **Clean compounder**,
warning set empty. Decimal ROUND_HALF_EVEN at four places is used for normalized
terms, means, bonuses/clamps and aggregate. Stored Fundamental fields are compared
with zero numerical tolerance; a wrong rounding digit or null must fail. The
worksheet audit recomputes arithmetic from these literals, without importing
production scoring functions. It does not independently re-prove every policy
normalization; the explicit equations/inputs are the review evidence.

## Independent Technical indicator subset

For each exact-ten ticker, latest close and SMA50 follow directly from its frozen
arithmetic progression. ATR14 is 2.1 because every true range is 2.1. All price
deltas are positive, so RSI14 is 100. ROC(n)=100*(C319/C(319-n)-1) for 10/21/63/126.
EMA span n starts at C0 and applies E_i=(2/(n+1))*C_i+(1-2/(n+1))*E_(i-1), for
spans 10 and 20. `expected-results.json` retains unrounded Decimal derivations;
comparison absolute tolerance is 1e-8 to accommodate binary indicator arithmetic.
Readiness: 320 bars, final session Oct 26, sufficient completed history. These
claims do not validate composite Technical scores or classifications.

## Identities, exclusions and completeness boundary

Declared exact sets include raw/Fundamental/Technical-input/Combined membership,
five profile/ticker memberships, 13 ordered stages, 60 provider record identities
and receipt facts, ten Setup capture keys and ten Winner acquisition members.
Workflow/batch request-key templates use semantic workflow references, sorted
tickers, fixed provider/dataset and batch 0001; runtime run IDs are resolved later.
One native CERI dependency and one handoff continuation are required. Explicit
empty earnings is valid no-data, not a failure. An independently established
Winner acquisition exclusion has no prediction children; eligible predictions
conditionally owe 2 entry models x 5 horizons, two target-stop definitions and a
decision-time estimate. H5 price obligations are not generalized to every horizon.

The tied-profile negative specimen has equal inputs/scores and equal buckets, so
alphabetical ticker order is the independent tie breaker. It is a comparison
control, not an asserted rank of the real ten. Likewise the missing-Technical
exclusion control does not classify a real-ten ticker by looking at actual rows.

Seven unresolved domain obligations are machine-readable in `expected-results`.
Exact Technical composite, Combined decisions, actual profile ranks, contextual
composites, complete CERI vectors, Setup selections/transitions and Winner
eligibility/feature/child populations have not been independently established.
The loader's `require_complete` refuses this manifest. A05 must remain PARTIAL
until these obligations are supplied; successful subset comparisons cannot be
presented as an independent whole-pipeline oracle or an A04 CERTIFIED verdict.

## Clock separation

Market cutoff 2026-10-26T20:30Z; post-acquisition CERI cutoff 20:32Z; decision
default 20:33Z. Source receipts, replay broker time and fixture histories are
business inputs. The scoped adapter supplies those clock defaults only. It does
not patch time.time, perf_counter, monotonic, sleep, worker/control/queue datetime,
heartbeat, lease, watchdog or execution-token logic. The original FULL_PIPELINE
handler and all A03 callback/fencing checks remain active. Future service-level
maturation retains its separate existing explicit clock after the parent fixture
clock context has closed; it is outside this oracle's financial claim.

## v2 availability-clock correction

v1 was retained after the native Winner PIT validator rejected a real server-default upload time later than the frozen decision. v2 changes no financial input value or expected numerical/domain result. It declares synthetic replay upload=20:28Z, processing/source receipt=20:29Z, and core projection availability=20:30Z. The fixture supplies these business-clock defaults for UploadRun, RawCompanyRow, FundamentalScore, TechnicalScore, CombinedResult, RankingResult, MarketRegimeSnapshot and SectorRotationSnapshot before native ORM writes. Existing historical times are retained. Job, worker, supervisor, queue and control timestamps remain real. This is a clock-source correction, not a retrospective database repair or a bypass of the PIT validator. The original failed v1 scenarios remain evidence.

## v3 UTF-8 header correction

The independent comparison retained expected Fundamental 9.1909 and rejected v2 actual 8.9850. Investigation identified an input-authoring error: reading column_aliases.yaml with the Windows default encoding changed U+00D7 into U+00C3 U+2014 in only the 10-day and 60-day dollar-volume CSV headers. The native UTF-8 mapper consequently missed their unchanged 40000000/50000000 values, explaining two missing medium fields and altered Liquidity/coverage/penalty. v3 corrects those two header strings to the existing UTF-8 source aliases. It changes no financial value, scoring policy, model, expected component, aggregate or domain identity. v2 expected/actual data and failed tests remain preserved. A new regression requires the declared Unicode headers and source values.


V4 candidate: see docs/testing/golden-provenance/a05b-r4/REVIEW.md and independent-review.json. Retained independent literals are proposed unchanged after the documented incremental branch/formula review; this is not owner authorization.
