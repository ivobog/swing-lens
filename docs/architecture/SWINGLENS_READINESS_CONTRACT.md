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
