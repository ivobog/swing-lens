# A05B completion of a05-v3

The original input manifest and all its artifacts remain byte-identical. These
expectation-only supplements use the existing normalized input identity
`6217a219eea1d954bad0f5e6503f71188e5d82a7e411919aa703935c36118591`.
They do not define v4 inputs. Their own manifest is separately content-sealed.

The four `derive_*.py` records in `a05b-evidence` are one-specimen authoring
worksheets. They load only frozen raw inputs/configuration and independently
reviewed arithmetic. An import guard forbids application imports. No production
output, scoring function, selector or database supplied an expected value.
Production files were read only to identify the active business contract.

## Core arithmetic

Each price sequence is affine with positive slope, close-open=.30,
high-close=1.10, close-low=1.00, increasing volume and 320 completed sessions.
Consequently RSI=ADX=100, there are no internal 3/3 pivots or distribution bars,
confirmed weekly trend scores10, and prior high exceeds current close so no
breakout occurs. Relative strength against SPY/QQQ is determined by the sign of
the derivative of their affine price ratio, equivalently slope/start-price.

`core-derivations.json` records each trend/momentum/setup contribution, peer
percentile, regime weight and execution contribution. Analytical Decimal reductions establish components. Native binary64 accumulation and round4 establish profile/mean boundary cases; the ledger records term order and exact binary representations. Technical persistence applies round4 to ratios/percentiles as well as scores; absolute comparison tolerance remains1e-8, rejecting a changed fourth digit. V5 shadow results are outside this contract.

Price/ATR computations use binary64 in the active port. The independent raw-bar
Wilder recurrence records its latest and lag5 values: LSCC/RDVT satisfy the strict
absolute-ATR contraction comparison by a few ulps, giving VCP6 instead of5.
This policy sensitivity is explicit; it was derived before reading actual
outputs, and no undocumented epsilon was inserted to force mathematical ties.

Stop=close-1-.3*2.1=close-1.63; target=close+1.5*2.1=close+3.15.
Reward/risk=3.15/1.63<2. This is an entry filter, not a rule that suppresses
persisted Technical/Combined/Ranking/Winner rows. The active v4 projection keeps
the base classification/action retained in explainability, while persisting
the regime-weighted composite. Expected Overheated/No-trade cases follow those
existing priorities; no new trading policy was introduced.

Combined=.55*9.1909+.45*Technical, less1.5 for Overheated momentum, rounded four
places. Frozen earnings are Sept15, forty-one days before the business session:
clear, no earnings penalty. All required sources exist; no warning or missing
source is invented. Ranks follow independent score/bucket ordering and symbol
ties. Ranking uses its separately enumerated normalized components, configured
profile weights/penalties/gates, available peers and exact fifty memberships.

## Market and Sector

Both frozen indices satisfy all positive trend/return predicates and no
distribution/breakout predicate. Composite=(2.5+2+1.5+1.5+1+1)*(.65+.35)=9.5,
Bull trend/Green. Index identities are SPY/QQQ, same frozen session/cutoff.

All ten CSV sectors map Technology Services to Technology, including DVN/JNJ:
the fixture CSV, not real-world issuer sectors, defines this test universe.
ETF scoring is disabled. Sector score uses the independently derived means,
top25 component5, setup density0 and risk control10-7*(6/10)=5.8. Six No-trade
classifications are sector danger members, triggering Risk-off before score
thresholds; the supportive market policy maps this to avoid_new_longs. The
single sector has rank1, high confidence and no comparable prior sector row.
The exact numerical ledger and source relationships are in context derivations.

## CERI

There are forty normalized USD EPS observations, ten guidance observations and
ten catalyst/revision identities. Refresh deduplicates the retained estimates;
there is no correction or supersession. All provider receipt timestamps precede
cutoff. For each window, exclude current and select newest baseline at/before
cutoff-window; actual elapsed days determine acceleration. EPS7/EPS30 use the
Sept17 observation, EPS90 June26. Revisions are persisted to six decimal places
before capture reads them. Their Decimal comparisons are exact.

Only EPS/current-fiscal-year exists, giving three usable slots of twenty-four
(12.5% core coverage). Net breadth=7/9; acceleration=pct7/elapsed7-pct90/elapsed90.
SEC guidance has no prior comparable observation, so the reviewed normalization
contract yields UNKNOWN/INSUFFICIENT and rejects it. Catalyst issuer_relevance
is absent, so the catalyst remains unselected; its subtype produces subject
launch and its missing confidence uses Normal. No accepted causal event exists
for price response. Thus available opportunity weight=.25+.15+.10=.50<.60:
opportunity score null, Unrated, never a zero-filled score or positive signal.
Confidence=8*.25+0+1.25*.20+10*.15+9*.10+10*.10=5.65/Low. Provider feed checks
finish on real operational time, later than the frozen cutoff; freshness is
unavailable rather than fabricated fresh. No accepted event risks, previous
certified target capture, guidance change or eligible catalyst change warrants
a change or alert. Those expected populations are explicitly empty.

## Setup and Winner

All four supported Setup adapters require setup>=5.5; the specimen's5.4 falls
short. Generic fallback is trackable for all ten and ready for six. The retained
Oct23 baseline has no certified evidence_id and is excluded by the documented
canonical-history query; it supplies no prior episode or velocity. Agreement
(.25*1+.25*.5+.25*1+.25*1)=.875, context completeness.5 and zero persistence yield confidence72/Normal.
Exactly ten new generic episodes open, six READY and four DEVELOPING. No trigger
is applicable. Canonical selections point to the current source for each
(ticker,1d,Oct26); each owes a canonical revision and episode opening. Openings
are not state transitions, so NEW_READY does not fire. No certified predecessor
means no signal changes or signal-change alerts. Both handoff paths resolve the
same semantic facts, with creator/evaluation/audit references checked by role.

Winner mandatory Technical/Ranking/Combined sources are available and complete;
optional Fundamental/Regime sources are included. Sector risk-off warning makes readiness DEGRADED; the native contextual policy yields POLICY_UNDECIDED and omits optional Sector. Setup data quality is Normal and Winner Sector feature fields are null with missing_sector_rotation_context warning. Feature eligibility does
not require a desirable trade classification. No Setup/Lifecycle feature enters
Winner. Ranking selection is independently resolved by minimum(profile rank,
profile name), not an assumed default profile. Ten predictions are ELIGIBLE;
there are no acquisition/feature exclusions for these inputs.

The frozen calendar establishes next entry Oct27, diagnostic entry Oct26 and
entry-session-is-session1 due dates for horizons1/3/5/10/20. There are100 pending
forwards,20 pending target/stops and10 primary DECISION_TIME estimates. Legacy
decoy history lacks certified training eligibility and target outcomes remain
pending: sample/effective N0, source/grade INSUFFICIENT, probabilities/bounds
null, reason no_eligible_cohort. Future maturation/publication is outside this
oracle. Primary keys and implementation metadata are verified through semantic
foreign-key/context/config relationships rather than invented numeric IDs.

Per-field literals, source lines, intermediate terms, missing/null predicates,
storage boundaries and tolerances accompany each domain's JSON derivation.

## Reviewed corrections to the initial sealed candidate

The initial supplement and actual evidence are preserved under a05b-evidence. No
original v3 input changed. Source inspection, not actual-row copying, established:

- technical_score_service.py:2695-2698 rounds every persisted numeric field4.
- ranking_profile_components.py and ranking_profile_engine.py accumulate binary64
  in original YAML order and round4 at component/profile boundaries. The Decimal
  mathematical tie is insufficient to predict a binary64 boundary; the revised
  worksheet records terms, hex sums and exact Decimal.from_float values.
- Regime is stage5 and Ranking stage7: Regime profile averages are empty. Its
  symbols payload is a role map (SPY primary, QQQ risk proxy enabled). Means use
  binary64 round4, giving7.0971 for the Technical mean.
- provider_registry.py:138-176 permits point_value, not the replay key point; SEC
  point is legitimately null after storage projection. Normalization initializes
  catalyst revision binary_eligible to null; derived binary eligibility is false.
  opportunity_score_service.py:299 emits guidance_rows_rejected.
- Sector risk-off warning -> producer_readiness.py DEGRADED -> contextual consumer
  POLICY_UNDECIDED/includedFalse. This fixed omission rule explains Setup context
  .5, confidence72 and Normal quality; Winner optional Sector nulls/warning. It is
  not permission to weaken mandatory acquisition or the independent Sector score.
- upload_service.py:140-143 sets the processing provenance note; Winner reads it.
  EvidenceGrade.INSUFFICIENT has public value Insufficient.
- Capture and lifecycle each have LIVE evaluation rows; publication audit
  identifies the lifecycle publisher. Counting all LIVE rows as one was invalid.

No production scoring or selector was invoked to regenerate expectations.

## Exact CERI and Setup ownership relationships

market_calculation_context_service.py creates the separate post-acquisition CERI
context with pipeline_run_id=None and retains its exact ID in the parent
pipeline result. contextual_calculation_identity.pipeline_id_for_cutoff reads
that nullable FK, so the CERI immutable identity pipeline dimension is UNKNOWN.
The expected state is explicit. The oracle requires the same retained context,
upload owner, cutoff, completed native CERI_CAPTURE_RUN payload pipeline/context
IDs, and the A03 dependency/root/continuation lineage. It does not fabricate a
known identity dimension or drop the pipeline ownership requirement.

Setup's stored source lineage preserves candidate context dates.
setup_with_contextual_permission copies the scoring snapshot and clears excluded
Sector context before confidence calculation. Context completeness .5 is checked
against the stored eligibility decisions plus source dates, not raw dates alone.

True handoff has one capture evaluation and one lifecycle publisher. False has
two capture evaluations (stage capture then internal recapture) and one lifecycle
publisher; recapture retains the original creator. Current selections refer to
the publisher evaluation, while snapshots refer to their retained creator.
All memberships and references are checked by role; generated IDs are not inputs.
