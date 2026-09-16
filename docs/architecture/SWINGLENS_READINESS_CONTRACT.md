# SwingLens Producer Readiness and Consumer Eligibility Contract

## Authority and scope

T12A begins Phase 3 from Phase-2 baseline
`7b015124807b8f895911d7e790fcbf01949ff85f`. This is the authoritative common contract
for readiness-at-creation. The original Calculation Lineage Registry remains the
original audit record. Phase-1 identity and Phase-2 immutable evidence remain prerequisites.

**Producer readiness does not by itself define every consumer's eligibility policy.**
**Numeric value presence is never proof of readiness.**

Implementation: `app/services/producer_readiness.py`; shared persistence and projection
resolution: `app/services/core_calculation_evidence.py`.

## Producer envelope

`ProducerReadinessEnvelope` is a frozen dataclass containing producer, typed status,
sorted/deduplicated typed blocking and warning reasons, Calculation Identity fingerprint,
calculation/model/engine version dimensions, readiness-policy version, optional evaluated
business cutoff and session, native confidence/coverage/freshness/signals, and evidence address.

`NativeReadinessMetrics` freezes canonical JSON. Decoded views are defensive copies, including
nested ledgers and missing-data flags. Native field names, labels, decimal precision and units
are preserved. No cross-domain confidence conversion is performed. Empty metric objects mean
no recorded native metrics; absent anchors are null. No freshness is inferred from today's
clock, and no missing metric is fabricated.

The readiness identity is the canonical envelope fingerprint. It contains Calculation Identity
and policy/version identity. The assigned database `evidence_id` is included in `to_dto()` but
excluded from canonical identity to avoid circular hashing. Evaluation time is the existing
business cutoff when known, never an additional wall-clock-only identity axis.

## Readiness statuses

| Status | Producer meaning |
|---|---|
| READY | Existing producer signals support its current intrinsic output contract. No implied universal consumer permission. |
| DEGRADED | Supported output has native confidence or warning caveats; consumer treatment is not globally prescribed. |
| INSUFFICIENT_EVIDENCE | Existing explicit insufficiency, completeness, confidence or availability state prevents intrinsic readiness. Numeric output may remain. |
| STALE | Producer already identifies stale evidence under its native policy. No new age threshold. |
| ERROR | Existing Technical error or IBMI failed availability state. |
| UNKNOWN | New normalization lacks enough recognized producer signals to certify readiness. |
| LEGACY_UNKNOWN | No frozen readiness-at-creation envelope exists, including pre-T12A Phase-2 evidence. |

`ReadinessStatus` uses `Enum`, deliberately not `StrEnum`. `LifecycleState.READY` is a trading
state and cannot compare equal to `ReadinessStatus.READY` or be supplied as an envelope status.
DTOs use the distinct `producer_readiness.status` field, never reinterpret lifecycle state.

## Typed reasons

Blocking codes cover existing Technical history/error, Combined/Ranking incompleteness,
native insufficient confidence, Regime staleness, CERI available-weight/staleness, IBMI
availability/failure/staleness and Setup staleness. Warning codes cover existing native low
confidence, Fundamental missing-data penalties/sparse-data flags, Sector incomplete context,
Setup near-stale status, native producer warnings and unknown/legacy certification.

`PRODUCER_WARNING` identifies the presence of native warnings; their exact original codes/text
remain in `native_signals` and the original evidence output. It does not turn free-text business
warnings into invented hard evidence conditions. Codes are sorted by enum value and deduplicated.
Blocking reasons cannot coexist with READY or DEGRADED. ERROR and INSUFFICIENT_EVIDENCE take
precedence over STALE where both are observed; all relevant blocking reasons survive.

## Native producer mappings

| Producer | Readiness-at-creation mapping |
|---|---|
| Fundamental | Existing coverage contract can support READY; sparse-data flags, missing penalties and warnings degrade it. No minimum coverage gate is introduced. Without recognized quality signals: UNKNOWN. |
| Technical | Explicit `insufficient_data`, missing-history flag or required-history false blocks; native `error` maps ERROR; low confidence without insufficiency is DEGRADED; explicit sufficiency with normal/high confidence supports READY. Missing sufficiency flag never certifies READY. |
| Combined | Existing `is_complete=false` blocks; true supports READY subject to its warnings. Does not propagate upstream Technical blocking in T12A. |
| Ranking | Existing completeness and warnings; profile, scores, ranks, normalization and population unchanged. Missing completeness stays UNKNOWN. |
| Regime | Native confidence and warnings; already-emitted stale-market warnings map STALE. Benchmark source/session data remain native. No gate/profile/Gray policy change. |
| Sector | Snapshot retains each sector's confidence and warnings; all recognized normal/high rows support READY; low/insufficient rows degrade the snapshot, rather than globally invalidating other sectors. Empty/unrecognized rows stay UNKNOWN. |
| CERI | Native data confidence and frozen ledgers; already-unrated opportunity or insufficient component coverage blocks. Existing provider-feed STALE maps STALE, retaining its semantic, age and native limit. |
| IBMI | Native confidence, coverage availability and freshness. FAILED maps ERROR; UNAVAILABLE/NOT_SUPPORTED/SUBSCRIPTION_REQUIRED block; STALE blocks. Unknown coverage/freshness never certifies READY. |
| Setup | Its own confidence label takes precedence over inherited Technical confidence. Native insufficient/low data-quality labels block/warn. Existing STALE blocks and NEAR_STALE warns. Native coverage, warnings and context remain recorded. Trading actionability unchanged. |
| Lifecycle | Evaluation confidence and confidence components; exact Setup Calculation Identity, cutoff and engine identity. Observation-gap evaluations retain native counters/threshold and UNKNOWN own quality when no confidence evaluation exists. Output trading state is not a readiness input. |
| Winner | Frozen upstream quality metadata is recorded, but no independent probability-evidence readiness policy exists here: UNKNOWN. Winner-specific eligibility/exclusions remain separate, unchanged consumer decisions for T12D. |

## Immutable persistence and legacy

The reserved top-level `producer_readiness` member is added by the shared writer before hashing
new core payloads. Callers cannot inject an independently drifting readiness envelope. It is part
of `payload_fingerprint` and `evidence_key`. A changed readiness state or policy produces distinct
evidence even when all business numerics match. Deterministic retries reuse exact new evidence.

Both ordinary and observation-gap lifecycle evaluation payloads freeze the contract before hashing. Transitions continue to
pin exact evaluations. Winner freezes the envelope in prediction lineage at initial creation;
selective ORM hooks reject replacement/removal of that subdocument and deletion of a prediction
carrying it, while permitting existing operational lineage changes. Legacy Winner rows cannot
gain readiness through an ORM update. Core/lifecycle evidence retains Phase-2 mutation guards.
Privileged direct SQL and bulk operations outside the supported ORM boundary remain an operational
governance limitation, as in Phase 2.

No schema columns, migration, production rewrite or backfill is required. Existing evidence
without the reserved member stays unchanged. Readers return LEGACY_UNKNOWN, never retrospectively
normalize its numerics or warnings under today's policy. Newly calculated evidence may advance a
current pointer without changing any earlier envelope.

`get_current_readiness` follows the scoped core projection to its exact evidence.
`get_readiness_for_row` follows a verified compatibility pointer or returns legacy unknown if
unpointed. `get_lifecycle_readiness_for_episode` follows the exact evaluation pointer.
`readiness_from_winner_prediction` reads frozen native lineage only. Missing referenced evidence
or a readiness/identity mismatch fails explicitly; it does not certify mutable row numerics.

## Consumer eligibility contract

`ConsumerEligibilityPolicy.evaluate(readiness, consumer=..., config=...)` produces an immutable
`ConsumerEligibilityDecision`, containing typed ELIGIBLE / INELIGIBLE / POLICY_UNDECIDED status,
typed reasons, consumer policy version, exact producer readiness fingerprint and evidence address.
It is not a flag on the producer envelope.

The T12A placeholder `UndecidedConsumerEligibilityPolicy` returns POLICY_UNDECIDED for every
state, explicitly identifying unknown and legacy readiness. It grants no implicit permission,
including for READY. Later consumer policies own reject/omit/penalize/suppress treatment and
must add their domain-supported typed reasons and versioned rules. No business consumer uses
the placeholder in T12A.

## Compatibility and deferred enforcement

Existing confidence, warnings and business fields remain present. Readiness is initially the
canonical internal contract, serializable as an additive DTO. Debug logging contains only producer,
status, reason codes and policy identity; no source payloads or credentials.

T12B owns Technical → Combined, Ranking and direct Setup/Lifecycle readiness enforcement.
T12C owns IBMI liquidity → Ranking; IBMI volatility/short pressure → CERI; Regime/Sector → Setup.
T12D owns Ranking/Regime/Sector → Winner and its remaining Technical quality policy.
T12E owns integrated Phase-3 certification. T12A does not close those edges.

## T12B core Technical consumer policies

T12B starts at `4c8eb3402b951482d590890e726195a3c445d786`. This section extends the
T12A producer contract; it does not change its statuses, native confidence semantics or
producer normalization. The implementation is `app/services/technical_consumer_eligibility.py`.

| Edge / named policy | Policy version | READY | DEGRADED | Blocking readiness | UNKNOWN | LEGACY_UNKNOWN |
|---|---|---|---|---|---|---|
| TECHNICAL_TO_COMBINED | `technical-to-combined-v1` | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |
| TECHNICAL_TO_RANKING | `technical-to-ranking-v1` | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |
| TECHNICAL_TO_SETUP | `technical-to-setup-v1` | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |

Every blocking reason, ERROR and INSUFFICIENT_EVIDENCE prevents permission. The policies
defensively reject a STALE envelope. Technical's T12A producer does not emit a meaningful
STALE classification or an age threshold: native Technical staleness certification is
NOT_APPLICABLE here, and no new threshold is introduced. Only ELIGIBLE crosses a decision
boundary. POLICY_UNDECIDED has the same omission effect as INELIGIBLE, while retaining its
distinct typed meaning and reasons.

DEGRADED has no explicit numeric-use permission on these three edges. Combined's warning
and completeness conventions, Ranking's post-score low-quality label cap, and Setup's
coverage/confidence fallback do not establish such permission. In particular the Ranking
cap is part of the audited failure, not authorization to retain a valid rank. Each v1
policy therefore leaves DEGRADED undecided. No confidence threshold or configurable
override is added. Future changes require a separately reviewed consumer policy version.

`technical_decision_input` follows an exact, scope-verified Technical evidence pointer,
reads its frozen producer readiness, and evaluates the named policy. An eligible input is
a projection of the evidence's frozen Technical values; current-row values cannot replace
them. Decimal/date types are restored for existing formulas and signal serialization.
Equal Decimal values may retain native display precision without changing the frozen value.
Missing envelopes remain LEGACY_UNKNOWN, even if an earlier evidence payload contains
positive values. Unresolved pointers, malformed contracts and identity mismatches fail
explicitly. No current/latest fallback or retrospective readiness normalization is allowed.

Combined omits the entire ineligible Technical behavior input, including classification,
risk and liquidity penalties. It reuses available-weight normalization, the configured
missing-input penalty and incomplete/Watchlist conventions. Technical diagnostics remain
on the producer and in exact lineage. Exclusion never substitutes a valid numeric zero.

Ranking omits Technical before component extraction, profile weighting, penalties and gates.
Rows without usable Technical remain diagnostic, incomplete and unranked (`profile_rank=0`),
with `No new entry`. They never enter the rankable population. Profile normalization is
within each ticker's component/available-weight formula; this implementation has no
peer-derived numeric statistics. Excluded rows cannot affect those formulas or ordinal
positions of the remaining population. Removing formerly ranked invalid peers may change
valid peers' ordinal ranks; fully READY populations retain exact outputs.

Setup evaluates its direct Technical policy before promoting scores, signals, feature flags,
trigger geometry or confidence. Ineligible inputs produce INSUFFICIENT diagnostic snapshots.
Combined Technical-score/classification fallbacks cannot reintroduce an omitted input.
Combined final score/decision and Ranking profile/score/decision remain metadata; Combined
earnings risk remains behavioral. CERI remains absent from Setup's dependency graph.

The reserved lineage/debug member `technical_consumer_eligibility` freezes the exact
producer readiness DTO, identity/fingerprint, evidence ID, consumer decision, policy version
and typed reasons with Combined, Ranking and Setup evidence before hashing. Source edges
pin the same Technical evidence ID. Policy changes and current recalculations cannot
reinterpret stored decisions. Exact evidence/policy/context retries are deterministic.

Lifecycle consumes Setup, never TechnicalScore directly. Episode evaluation reads permission
from the exact frozen Setup evidence rather than mutable Setup lineage. The engine, family
gateway and actionability policy prevent recovery from residual numeric signals. Ineligible
history is omitted from family calculations and Setup score velocities. A blocked new
evaluation uses existing no-family-evidence conventions: it cannot open an actionable episode
or advance READY/TRIGGERED/CONFIRMED. An existing episode keeps its state/phase and receives
blocked current actionability; no historical state/evidence is rewritten. Existing terminal
and independent observation-gap policies remain in place. Alert generation consequently
has no invalid actionable transition to announce; diagnostic gate-blocked alerts remain
permitted by existing alert rules.

Generic state-machine DTOs with no Technical binding are mathematical test/domain inputs,
not certified Technical consumers. Identity-bound or Technical-linked Setup input without
the frozen permission fails closed. Unpersisted Technical previews do not obtain implicit
permission: prospective discovery through the live builder becomes incomplete/low confidence
until there is an explicit certified preview contract. No preview is promoted or backfilled.

Combined, Ranking and Setup cohort readers select-in load the existing evidence relationship.
Policy evaluation then needs no per-ticker/per-profile readiness query. The shared writer
also advances a loaded Technical evidence reference after same-session recalculation.
There are no new columns or migrations. Winner policy and T12C contextual consumers are
unchanged; repository-wide INV-READINESS-001 remains partial pending T12C/D/E.

## T12C contextual consumer enforcement

T12C starts from T12B `b5af43ca8690cb65980be79c4a453ee1d4f7739e`. The T12A
producer normalizers and the preceding T12B certification remain authoritative.
Implementation: `app/services/contextual_consumer_eligibility.py`. Five independent
consumer policies reuse `ProducerReadinessEnvelope` and `ConsumerEligibilityDecision`:

| Policy | Version | READY | DEGRADED | INSUFFICIENT_EVIDENCE / ERROR / STALE or blocking reasons | UNKNOWN / LEGACY_UNKNOWN |
|---|---|---|---|---|---|
| IBMI_LIQUIDITY_TO_RANKING | ibmi-liquidity-to-ranking-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED |
| IBMI_VOLATILITY_TO_CERI | ibmi-volatility-to-ceri-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED |
| IBMI_SHORT_PRESSURE_TO_CERI | ibmi-short-pressure-to-ceri-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED |
| REGIME_TO_SETUP | regime-to-setup-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED |
| SECTOR_TO_SETUP | sector-to-setup-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED |

Only ELIGIBLE grants behavioral use. Blocking reasons override even a READY status.
Undecided and ineligible inputs are omitted, with different typed reasons retained.
Each DEGRADED edge was reviewed independently: Ranking's bounded optional liquidity
overlay (`ranking_profile_engine._tradeability_penalty`, `config/ranking_profiles.yaml`)
does not grant degraded permission; CERI's options event-premium penalty and short-pressure
reason (`ceri/event_risk_service.py`, `config/ceri.yaml`) do not grant permission for
low-quality provider context; Regime's `low_market_confidence` warning
(`market_regime_policy.py`) is a producer warning rather than a Setup use grant; and
Setup's optional Sector confidence/context conventions (`config/setup_lifecycle.yaml`)
do not authorize DEGRADED snapshot numerics. No degraded override or new threshold exists.

The shared selector verifies the exact evidence pointer, artifact kind, owner/ticker/module
scope, identity fingerprint and T12A envelope binding. Missing envelopes remain legacy;
native current values cannot retrospectively establish readiness. Eligible ORM inputs are
projections of frozen evidence values, including the exact Sector row within its snapshot.
Unresolved pointers and mismatched contracts fail explicitly. A failed eligibility decision
never selects an older READY row, another run, latest/current or a global replacement.
Identity compatibility selection still precedes eligibility and retains its existing rules.

Ranking removes ineligible liquidity before its optional tradeability penalty. The existing
missing-overlay behavior leaves the otherwise qualified Ranking row valid; no extra penalty,
zero-valued substitute, profile weight change or whole-consumer invalidation is introduced.

CERI selects volatility and short pressure independently before Confidence and Opportunity
calculation. Eligible volatility supplies the existing options event-premium risk penalty;
eligible short pressure supplies the existing explanatory risk reason. In the actual engine,
neither input belongs to Opportunity's available-weight formula or Confidence's coverage
formula. This graph is preserved: omission cannot rescue insufficient revisions or bypass
the existing minimum evidence. Missing volatility is `None`, distinct from an eligible native
zero. Removing one source does not remove the other, fabricate coverage, or add a new penalty.

Setup evaluates Regime and Sector independently before source values, promoted signals and
context completeness. Omitted Regime contributes neither market label nor gate; omitted Sector
contributes neither rank nor confidence. Optional omission follows existing missing-context
quality, warning and confidence conventions. `required_context=[technical]` remains unchanged;
optional omission alone is not a universal entry block. An omitted bullish/stale gate cannot
grant permission, but independently qualified Technical behavior can still apply. Native
Regime sizing remains a producer diagnostic and is not a direct Setup field.

Lifecycle, family adapters, confidence and actionability consume frozen Setup permission;
they introduce no direct Regime/Sector producer query. Omitted context also removes its
lineage-date confidence vote and residual signal values, including historical family inputs.
The alert market-context fallback resolves exact frozen Setup evidence and respects recorded
omission. Historical event evidence retains its stored meaning. Existing READY episodes and
their old evaluations are not rewritten; new evaluation follows optional missing-context
semantics and cannot restore excluded context. No CERI -> Setup or IBMI -> Winner edge exists.

The reserved member `contextual_consumer_eligibility` freezes each decision, policy version,
typed reasons, producer readiness DTO/fingerprint, evidence ID, inclusion and selected feature
reference inside Ranking debug, CERI evidence lineage and Setup lineage before hashing.
Certified omitted sources remain pinned for audit. Unpointed legacy references are diagnostic
only and never masquerade as certified source edges. Exact retries are deterministic, and
policy changes do not reevaluate stored decisions or reinterpret historical payloads.

Ranking, CERI and Setup bulk readers select-in load existing evidence relationships. CERI
preloads both optional modules once for the cohort. PostgreSQL verifies zero evidence SELECTs
during repeated permission/profile evaluation after preload. The writer advances loaded
relationships when a current pointer advances. Relationships add no database column or schema.

Regime cross-run reuse remains permitted when the existing temporal/calendar/configuration/
algorithm compatibility policy accepts the exact artifact and REGIME_TO_SETUP is ELIGIBLE.
Cross-run reuse does not make a stale or unknown artifact usable. T12C does not add a Regime
history-length threshold: sparse SPY evidence with native LOW is DEGRADED and omitted, while
sparse evidence still labeled NORMAL by the producer remains native READY. CORE-001 therefore
retains a producer-side residual. CORE-002's consumer gate bypass is enforced without changing
the producer's native stale gate/sizing policy.

T12C enforces its five edges; INV-READINESS-001 and XINT-004 remain partial across the repository.
T12D retains Winner Technical quality and Ranking/Regime/Sector permission certification;
T12E retains Phase-3 integration. No migration, production rewrite or legacy backfill is needed.
Full results and scope limits: `docs/remediation/calculation-lineage/T12C_contextual_consumer_readiness_enforcement.md`.
