# SwingLens Phase-3 Readiness and Eligibility Certification

## 1. Certification baseline

T12E starts at `3dfac7a62f9701875705f3483afd00cbea5ea075` on
`codex/t12e-phase3-readiness-certification`. The commit containing this document is
the certified tree; its exact final SHA is recorded in the delivery response.
Original audit: `3a9d47063be996908b7d5d1cc5769e0bbd033546`.
Phase-2 certificate: `7b015124807b8f895911d7e790fcbf01949ff85f`.
All four T12A–D commits and Phase-2 remain ancestors. The original audited registry
is unchanged. This implementation certificate supplements
[the common contract](SWINGLENS_READINESS_CONTRACT.md), rather than replacing it.
The detailed evidence, lane accounting and finding dispositions are in
[the T12E report](../remediation/calculation-lineage/T12E_phase3_readiness_certification.md).

## 2. Producer readiness model

The frozen `ProducerReadinessEnvelope` retains producer, typed status and reasons,
Calculation Identity fingerprint, calculation versions, native confidence,
coverage, freshness and signals, existing business anchors and readiness version.
Its canonical fingerprint excludes the database evidence ID to avoid circular
identity; its DTO includes that address. The shared writer owns normalization,
adds the envelope before payload hashing, and preserves Phase-2 evidence keys and
source pins. No new columns or migration are introduced.

Statuses remain READY, DEGRADED, INSUFFICIENT_EVIDENCE, STALE, ERROR, UNKNOWN and
LEGACY_UNKNOWN. They are distinct from trading lifecycle states. Native units and
thresholds remain producer-specific. Numeric presence does not authorize use.

T12E Regime normalization binds the technical feature engine's existing primary
benchmark `insufficient_data` flag and active risk-proxy flag. Explicit missing or
insufficient evidence blocks; absent sufficiency proof cannot certify READY.
Native sparse classification, score, gate and sizing formulas are unchanged.
Regime, Combined, Ranking and Sector create their envelopes under respectively
`regime-readiness-v2`, `combined-readiness-v2`, `ranking-readiness-v2` and
`sector-readiness-v2`. The latter three identify supported producer services that
now enforce the newly audited upstream permissions. Other producers retain
`producer-readiness-v1`.

## 3. Consumer eligibility model

Each exact named policy produces a frozen `ConsumerEligibilityDecision` with
consumer, ELIGIBLE / INELIGIBLE / POLICY_UNDECIDED, typed reasons, policy version,
producer readiness fingerprint and evidence ID. Only ELIGIBLE permits numeric
consumption. Exact-pointer resolution validates identity, producer, owner and
scope before projecting frozen fields. Unresolved evidence fails explicitly;
unpointed legacy sources do not gain permission.

The eleven policies requiring a v2 producer contract return POLICY_UNDECIDED with
`PRODUCER_READINESS_VERSION_UNSUPPORTED` for an old v1 READY envelope. This is a
prospective compatibility rule. Reading historical producer or consumer evidence
does not change its version, status, reasons, inclusion state or identity.

## 4. Certified policy matrix

E = ELIGIBLE; U = POLICY_UNDECIDED; I = INELIGIBLE. Columns are native READY,
DEGRADED, INSUFFICIENT_EVIDENCE, STALE, ERROR, UNKNOWN, LEGACY_UNKNOWN. READY assumes
the required producer version and a valid exact evidence binding. A blocking
reason always takes precedence. No policy currently grants a degraded override.

| Edge | Current policy | READY | DEGRADED | INSUFFICIENT | STALE | ERROR | UNKNOWN | LEGACY |
|---|---|---|---|---|---|---|---|---|
| Technical → Combined | technical-to-combined-v1 | E | U | I | I | I | U | U |
| Technical → Ranking | technical-to-ranking-v1 | E | U | I | I | I | U | U |
| Technical → Setup | technical-to-setup-v1 | E | U | I | I | I | U | U |
| IBMI liquidity → Ranking | ibmi-liquidity-to-ranking-v1 | E | U | I | I | I | U | U |
| IBMI volatility → CERI | ibmi-volatility-to-ceri-v1 | E | U | I | I | I | U | U |
| IBMI short pressure → CERI | ibmi-short-pressure-to-ceri-v1 | E | U | I | I | I | U | U |
| Regime → Setup | regime-to-setup-v2 | E | U | I | I | I | U | U |
| Sector → Setup | sector-to-setup-v2 | E | U | I | I | I | U | U |
| Technical → Winner | technical-to-winner-v1 | E | U | I | I | I | U | U |
| Ranking → Winner | ranking-to-winner-v2 | E | U | I | I | I | U | U |
| Regime → Winner | regime-to-winner-v2 | E | U | I | I | I | U | U |
| Sector → Winner | sector-to-winner-v2 | E | U | I | I | I | U | U |
| Fundamental → Combined | fundamental-to-combined-v1 | E | U | I | I | I | U | U |
| Fundamental → Ranking | fundamental-to-ranking-v1 | E | U | I | I | I | U | U |
| Fundamental → Setup | fundamental-to-setup-v1 | E | U | I | I | I | U | U |
| Combined → Setup | combined-to-setup-v2 | E | U | I | I | I | U | U |
| Fundamental → Winner | fundamental-to-winner-v1 | E | U | I | I | I | U | U |
| Combined → Winner | combined-to-winner-v2 | E | U | I | I | I | U | U |
| Technical → Sector | technical-to-sector-v1 | E | U | I | I | I | U | U |
| Combined → Sector | combined-to-sector-v2 | E | U | I | I | I | U | U |
| Ranking → Sector | ranking-to-sector-v2 | E | U | I | I | I | U | U |
| Regime → Sector | regime-to-sector-v2 | E | U | I | I | I | U | U |
| Prior Sector → Sector | prior-sector-to-sector-v2 | E | U | I | I | I | U | U |

There are 23 distinct current policy versions: 12 v1 and 11 v2. The five original
context/compound v1 policies replaced here remain historical policy identities;
they do not authorize new decisions under this certificate. The all-state matrix
has 161 cases; actual numeric-consumption attacks cover all 23 policies, including
all 69 DEGRADED / UNKNOWN / LEGACY_UNKNOWN POLICY_UNDECIDED cases.

## 5. Certified consumer graph

The original twelve edges plus eleven evidenced material edges above form the
certified graph. Fundamental contributes to Combined components, Ranking
components and Setup liquidity; Combined earnings risk contributes to Setup;
Fundamental and Combined supply Winner features. Technical, Combined and Ranking
contribute to Sector universe scoring and population; Regime posture and prior
Sector score/rank history contribute to Sector decisions. These are existing
dependencies discovered by re-audit, not new product dependencies.

Setup → Lifecycle → Alerts propagates Setup's frozen permission. These two
propagation edges do not add direct upstream selectors or named producer policies.
Ranking/Combined Setup score/rank labels, Sector's Fundamental average, Market
Participation and Sector Leadership remain display metadata. They do not add
material decision edges merely by appearing in a payload or identity.

## 6. Negative dependency graph

The following nine edges remain absent: CERI → Ranking, CERI → Setup, CERI →
Lifecycle, CERI → Winner, Setup → Winner, Lifecycle → Winner, Alert → Winner,
IBMI → Winner direct, and Sector → same-run Ranking. Native IBMI influence remains
through the declared Ranking and CERI edges. Static negative-graph regression and
source-selector tests preserve this boundary.

## 7. Blocking semantics

INSUFFICIENT_EVIDENCE, STALE, ERROR or any typed blocking reason produces
INELIGIBLE. Native Technical history errors, IBMI unavailable/failed constituents,
Regime benchmark insufficiency/staleness, Combined/Ranking incompleteness and
Sector confidence are represented without hiding their surviving diagnostics.
The same positive score/rank under a different frozen readiness state cannot
re-enter material components, population, context, actionability or Winner capture.
STALE is defensively blocked for every policy, but native staleness certification
is NOT_APPLICABLE for Technical, Fundamental, Combined, Ranking and Sector when
their producers have no native age policy. No synthetic age threshold is added.

## 8. DEGRADED semantics

All current policies explicitly abstain: POLICY_UNDECIDED. None converts low
confidence into an implicit weight, penalty or permission. A future permissive
native rule must acquire its own policy version and certification. Fundamental
V2 sparse/missing-data/warning signals therefore cannot authorize its positive
numeric remnants in any newly audited material consumer.

## 9. UNKNOWN / LEGACY semantics

UNKNOWN and LEGACY_UNKNOWN produce POLICY_UNDECIDED. New consumers omit/reject
these sources; historical evidence retains its stored decisions. Legacy current
views remain available without automatic promotion, reinterpretation or backfill.
Old v1 READY evidence from corrected producers is similarly undecided for new v2
consumers, while retaining its original frozen READY classification on reads.

## 10. Optional-source omission

IBMI overlays are omitted when unpermitted. CERI uses its existing missing-module
semantics; Ranking applies no tradeability influence from an excluded feature.
Regime and Sector context, Fundamental and Combined Setup influences and Sector
inputs are omitted before native math. Winner's optional Fundamental, Regime and
Sector features use the existing nullable missing representation. Considered
source provenance and the omitted decision remain frozen even when a feature's
consumed-source ID is null. No current/global replacement is attempted.

## 11. Mandatory-source rejection

Winner requires permitted Technical, Ranking and Combined. Acquisition evaluates
all six source permissions and rejects atomically before prediction, episode,
forward outcome, target/stop outcome or estimate creation. Combined and Ranking
use their existing native missing-source normalization and completeness gates;
Setup cannot regain actionability when its Technical permission is absent.

## 12. No-zero-substitution rule

Omission precedes model math. Nullable features and native unavailable components
remain missing. CERI's native event-risk maximum may legitimately be zero after
missing optional influences; this does not assert a valid zero-valued IBMI
feature. Combined's existing missing-source penalty and Ranking's native
missing/population rules remain unchanged. Equality to explicitly absent-source
business outputs proves exclusion does not add a valid-poor-value penalty.

## 13. Immutable eligibility decisions

Combined and Ranking debug payloads, CERI evidence lineage, Sector snapshot and
universe ledgers, Setup source lineage and Winner's reserved acquisition lineage
freeze exact permissions. Core payload hashing and source evidence keys retain
Phase-2 mutation protection. Lifecycle and Alert evidence pins the exact Setup
and evaluation. Prospective policy changes create new decisions; they do not
edit earlier permissions. ORM hooks reject mutation of reserved Winner lineage.

## 14. Historical semantics

Current pointers may advance from E1/R1/C1 to E2/R2/C2. Historical evidence reads
its own payload. Unsupported/missing evidence fails rather than reacquiring a
different current source. Source selection stays within existing identity,
calendar, cutoff and compatibility rules. READY cross-run Regime reuse remains
valid for Setup and Winner; same run ID is not added as a requirement.

## 15. Winner acquisition semantics

Eligibility is evaluated only at initial source acquisition. Historical read,
rescore and outcome maturation use frozen features and permissions. Native
PostgreSQL tests advance all four original producers, prohibit current policy
evaluation and source acquisition, then run actual maturation with native bars,
resolved contract and satisfied market-data obligations. Operational outcome
lineage may advance; feature JSON and reserved eligibility lineage cannot change.
Bounded cohort preloads include the additional Fundamental/Combined evidence;
repeated per-ticker acquisition makes zero additional evidence SELECTs.

## 16. Producer sufficiency gaps

CORE-001's sparse native-NORMAL loophole is fixed using existing feature-history
flags, with absent proof also failing closed. Old envelopes are not rewritten and
cannot authorize new corrected consumers through their old version. CORE-002's
stale diagnostic gate/sizing remains for forensics while typed STALE blocks use.

Fundamental's existing coverage/warning contract is not replaced with a new
coverage threshold. Its unsupported states are now fenced in all material
consumers; optional Winner fields remain null. Combined's completeness-only
Winner gate was insufficient for ERROR/UNKNOWN/DEGRADED, so exact typed permission
is now mandatory in addition to native completeness.

Two nonblocking diagnostic gaps remain separately inventoried: Regime stale
policy diagnostics and CERI's own disconnected event-risk stale penalty.
CERI's typed producer staleness remains non-ready and CERI has no downstream
decision edge in this graph. CERI-007 remains PARTIAL/OPEN for that producer
calculation issue; IBMI input enforcement does not close the original finding.

## 17. Certified boundary

This certificate covers supported producer services, exact immutable evidence
resolution, named consumer policies, numeric enforcement, frozen historical
semantics, actionability propagation, native PostgreSQL persistence and READY
business parity. All 23 material edges are enforced; within that boundary,
POTENTIAL_BYPASS = 0 and CONFIRMED_BYPASS = 0.

Readiness hashing binds recorded contract fields; it does not prove omitted raw
facts, semantic truth of arbitrary forged producer DTOs, full original-context
replay, global entry-point equivalence, future sufficiency redesign or privileged
direct-SQL governance. Native probability cohort evidence is separate from
producer permission. Production data has not been reclassified or backfilled.

## 18. Deferred Phase-4+ work

Configuration authority; original-context replay/reconstruction; acquisition-plan
semantics; refresh-cycle identity; target-scope freezing; entry-point unification;
privileged direct-SQL governance. Retained diagnostic producer calculations are
tracked separately. No Phase-4 implementation is included in T12E.
