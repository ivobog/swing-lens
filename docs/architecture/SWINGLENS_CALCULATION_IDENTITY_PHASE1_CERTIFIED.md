# SwingLens Calculation Identity — Phase-1 Certified Snapshot

## 1. Authority

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase-1 certification baseline | `3ddad46624e31075245c20186d5926a13d7b512a` |
| T09A | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| T09C | `bbb345b3257d0df572db425242c62ea1447e5d24` |
| T10A | `e08bf42a527c1345883e5587af794a7396443a7e` |
| T10B | `7edef90175de190f4fbfc5ea3fd283ed8c9aa2a9` |
| T10C | `fc380503508e16a4d58c3ef2a446040e64194d75` |
| T10D | `3ddad46624e31075245c20186d5926a13d7b512a` |
| T10E final HEAD | The focused certification commit containing this snapshot; its SHA is recorded in the T10E final response because a commit cannot contain its own SHA. |
| Certification date | 2026-09-14 |

This document is the authoritative post-remediation snapshot for **Phase-1 Calculation
Identity logic**. It does not replace the original lineage registry, which remains an audit
record of baseline `3a9d47063be996908b7d5d1cc5769e0bbd033546`. PostgreSQL integration,
external-provider, and browser/E2E certification are explicitly outside this snapshot.

## 2. Identity model

T10A defines one typed `CalculationIdentity` envelope with these semantic groups:

| Group | Dimensions |
|---|---|
| Ownership | run ID, pipeline ID |
| Subject | ticker, company ID |
| Calculation context | `MarketCalculationContext` ID and complete context fingerprint |
| Temporal | decision/as-of session, calculation cutoff, calendar/timezone/readiness identity |
| Configuration | complete effective-configuration digest and resolution contract |
| Algorithm | calculation, model, schema, engine, and component versions |
| Source lineage | typed artifact references, semantic fingerprints, aggregate lineage digest, declared proof boundary |
| Generation | generation ID/key/watermark/cutoff where applicable |

Each dimension is explicitly `KNOWN`, `UNKNOWN`, `LEGACY_UNKNOWN`, or `NOT_APPLICABLE`.
Canonical serialization is deterministic and SHA-256 fingerprinted. Operational worker,
lease, retry, and job-attempt fields are forbidden from semantic identity. `UNKNOWN` and
`LEGACY_UNKNOWN` never prove a required compatibility dimension; `NOT_APPLICABLE` is valid
only where the named policy explicitly permits it.

## 3. Compatibility policy registry

### Shared T10A profiles

| Policy | Contract | Enforcement |
|---|---|---|
| `STRICT_DECISION_COMPATIBILITY` | complete decision identity including source/generation where applicable | `VALIDATED`; shared strict profile |
| `PIPELINE_CONTEXT_COMPATIBILITY` | run, pipeline, context, session, cutoff, calendar | `ENFORCED` at persisted context and explicit Combined/Ranking pipeline boundaries |
| `TEMPORAL_COMPATIBILITY` | context fingerprint, session, cutoff, calendar | `VALIDATED`; reusable infrastructure profile |
| `FEATURE_REUSE_COMPATIBILITY` | subject, temporal, config, algorithm, source lineage | `VALIDATED`; reusable infrastructure profile |
| `GENERATION_COMPATIBILITY` | config, algorithm, source lineage, complete generation identity | `VALIDATED`; reusable infrastructure profile |

### Implemented producer/consumer policies

| Policy | Producer → consumer | Required proof / reuse | Failure behavior | Level |
|---|---|---|---|---|
| `COMBINED_INPUT_COMPATIBILITY` | Fundamental + Technical → Combined | exact run/pipeline/ticker/context/session/cutoff/calendar; both inputs require known config/version/source lineage | reject calculation | `ENFORCED` |
| `RANKING_INPUT_COMPATIBILITY` | Fundamental + Technical → Ranking | same decision spine and intrinsic proof as Combined | reject calculation | `ENFORCED` |
| `RANKING_IBMI_COMPATIBILITY` | IBMI → Ranking | ticker/session/calendar/config/versions/source plus bounded cutoff; global ownership/context N/A allowed | omit optional feature | `ENFORCED` |
| `REGIME_CONTEXT_COMPATIBILITY` | Regime → contextual consumer | session/cutoff/calendar/config/calculation/engine/source; run/pipeline/ticker N/A | reject candidate | `ENFORCED` |
| `SECTOR_RANKING_COMPATIBILITY` | Ranking → Sector | exact run/pipeline/ticker/context/session/cutoff/calendar and known intrinsic proof | omit incompatible row | `ENFORCED` |
| `SECTOR_REGIME_COMPATIBILITY` | Regime → Sector | global Regime contract | skip candidate | `ENFORCED` |
| `SECTOR_PRIOR_COMPATIBILITY` | prior Sector → Sector | earlier session, calendar, Sector config, calculation/engine/mode/source | skip candidate | `ENFORCED` |
| `SETUP_TECHNICAL_COMPATIBILITY` | Technical → Setup | exact decision spine and known intrinsic proof | reject/omit source; cannot drive Setup | `ENFORCED` |
| `SETUP_COMBINED_COMPATIBILITY` | Combined → Setup | exact decision spine and known intrinsic proof | omit earnings-risk and metadata | `ENFORCED` |
| `SETUP_RANKING_METADATA_COMPATIBILITY` | Ranking → Setup | exact decision spine and known intrinsic proof | omit metadata | `ENFORCED` |
| `SETUP_REGIME_COMPATIBILITY` | Regime → Setup | global Regime contract | skip/omit candidate | `ENFORCED` |
| `SETUP_SECTOR_COMPATIBILITY` | Sector → Setup | exact run/pipeline/context/session/cutoff/calendar/config/versions/source | skip/omit candidate | `ENFORCED` |
| `CERI_IBMI_COMPATIBILITY` | IBMI → CERI | reusable IBMI context contract with bounded cutoff | omit optional feature | `ENFORCED` |
| `IBMI_CONTEXT_COMPATIBILITY` | IBMI → generic contextual consumer | alias of the reusable IBMI proof contract | reject/omit per consumer | `VALIDATED`; reusable alias |
| `CERI_CONTEXT_COMPATIBILITY` | existing CERI → CERI reuse | exact decision spine plus known config/calculation/source | reject legacy/incompatible reuse | `ENFORCED` |
| `WINNER_HANDOFF_COMPATIBILITY` | Handoff → Winner | run/pipeline/context/session/cutoff/calendar, contract version, complete manifest fingerprint | fail capture | `ENFORCED` |
| `WINNER_RAW_COMPATIBILITY` | Raw → Winner | run/ticker plus exact Handoff artifact ID/hash | fail ticker acquisition | `ENFORCED` |
| `WINNER_FUNDAMENTAL_COMPATIBILITY` | Fundamental → Winner | exact decision spine plus exact Handoff artifact | omit optional source | `ENFORCED` |
| `WINNER_TECHNICAL_COMPATIBILITY` | Technical → Winner | exact decision spine plus exact Handoff artifact | fail ticker acquisition | `ENFORCED` |
| `WINNER_COMBINED_COMPATIBILITY` | Combined → Winner | exact decision spine plus exact Handoff artifact | fail ticker acquisition | `ENFORCED` |
| `WINNER_RANKING_COMPATIBILITY` | Ranking → Winner | exact decision spine plus exact Handoff artifact; filter before priority | omit incompatible candidates | `ENFORCED` |
| `WINNER_REGIME_COMPATIBILITY` | Regime → Winner | global Regime contract plus exact Handoff artifact | skip/omit candidate | `ENFORCED` |
| `WINNER_SECTOR_COMPATIBILITY` | Sector → Winner | exact run/pipeline/context/session/cutoff/calendar/config/model/mode plus Handoff artifact | omit incompatible evidence | `ENFORCED` |

## 4. Producer/consumer identity matrix

| Producer | Consumer | Artifact | Policy | Required dimensions | Cross-run? | Mismatch / unknown | Enforcement location | Status |
|---|---|---|---|---|---|---|---|---|
| Fundamental | Combined | `FundamentalScore` | `COMBINED_INPUT_COMPATIBILITY` | decision spine, config/version/source, exact Raw lineage | No | reject | `combined_decision.refresh_combined_results` | `CERTIFIED` |
| Technical | Combined | `TechnicalScore` | `COMBINED_INPUT_COMPATIBILITY` | decision spine, config/version/source | No | reject | same | `CERTIFIED` |
| Fundamental | Ranking | `FundamentalScore` | `RANKING_INPUT_COMPATIBILITY` | decision spine, config/version/source, exact Raw lineage | No | reject | `ranking_profile_service._validated_ranking_sources` | `CERTIFIED` |
| Technical | Ranking | `TechnicalScore` | `RANKING_INPUT_COMPATIBILITY` | decision spine, config/version/source | No | reject | same | `CERTIFIED` |
| IBMI | Ranking | `IBIntelligenceFeature` | `RANKING_IBMI_COMPATIBILITY` | ticker/session/bounded cutoff/calendar/config/versions/source | Yes | omit | `ranking_profile_service._identity_safe_liquidity` | `CERTIFIED` |
| Ranking | Sector | `RankingResult` | `SECTOR_RANKING_COMPATIBILITY` | decision spine, profile config/version/source | No | omit | `sector_universe_service`, `sector_rotation_service` | `CERTIFIED` |
| Regime | Sector | `MarketRegimeSnapshot` | `SECTOR_REGIME_COMPATIBILITY` | global temporal/config/version/source | Yes | skip | `sector_rotation_service._latest_market_snapshot` | `CERTIFIED` |
| prior Sector | Sector | `SectorRotationSnapshot` | `SECTOR_PRIOR_COMPATIBILITY` | earlier session, calendar/config/version/mode/source | Yes | skip | `sector_rotation_service._select_compatible_previous_snapshot` | `CERTIFIED` |
| Technical | Setup | `TechnicalScore` | `SETUP_TECHNICAL_COMPATIBILITY` | exact decision spine and intrinsic proof | No | reject/omit | `setup_lifecycle.source_loader` | `CERTIFIED` |
| Combined | Setup | `CombinedResult` | `SETUP_COMBINED_COMPATIBILITY` | exact decision spine and intrinsic proof | No | omit | same | `CERTIFIED` |
| Ranking | Setup | `RankingResult` | `SETUP_RANKING_METADATA_COMPATIBILITY` | exact decision spine and intrinsic proof | No | omit | same | `CERTIFIED` |
| Regime | Setup | `MarketRegimeSnapshot` | `SETUP_REGIME_COMPATIBILITY` | global temporal/config/version/source | Yes | skip/omit | same | `CERTIFIED` |
| Sector | Setup | `SectorRotationSnapshot` | `SETUP_SECTOR_COMPATIBILITY` | run/pipeline/context/temporal/config/version/source | No | skip/omit | same | `CERTIFIED` |
| IBMI | CERI | `IBIntelligenceFeature` | `CERI_IBMI_COMPATIBILITY` | ticker/session/bounded cutoff/calendar/config/versions/source | Yes | omit | `ceri.capture_service` PIT selectors | `CERTIFIED` |
| Raw | Winner | `RawCompanyRow` | `WINNER_RAW_COMPATIBILITY` | run/ticker/exact ID/hash | No | reject | `winner_probability.acquire_winner_sources` | `CERTIFIED` |
| Fundamental | Winner | `FundamentalScore` | `WINNER_FUNDAMENTAL_COMPATIBILITY` | decision spine/exact Handoff artifact | No | omit | same | `CERTIFIED` |
| Technical | Winner | `TechnicalScore` | `WINNER_TECHNICAL_COMPATIBILITY` | decision spine/exact Handoff artifact | No | reject | same | `CERTIFIED` |
| Combined | Winner | `CombinedResult` | `WINNER_COMBINED_COMPATIBILITY` | decision spine/exact Handoff artifact | No | reject | same | `CERTIFIED` |
| Ranking | Winner | `RankingResult` | `WINNER_RANKING_COMPATIBILITY` | decision spine/exact Handoff artifact | No | filter/omit | same | `CERTIFIED` |
| Regime | Winner | `MarketRegimeSnapshot` | `WINNER_REGIME_COMPATIBILITY` | global temporal/config/version/source and Handoff artifact | Yes | skip/omit | same | `CERTIFIED` |
| Sector | Winner | `SectorRotationSnapshot/Row` | `WINNER_SECTOR_COMPATIBILITY` | run/pipeline/context/temporal/config/version/mode/Handoff artifact | No | omit | same | `CERTIFIED` |
| Handoff | Winner | `TransitionDecisionHandoffManifest` | `WINNER_HANDOFF_COMPATIBILITY` | ownership/context/session/cutoff/calendar/anchor/fingerprint/artifact set | No | fail capture | `validate_winner_handoff` before acquisition | `CERTIFIED` |

## 5. Global reuse rules

- **Regime:** run, pipeline, context, and ticker are intentionally N/A. Reuse requires the
  exact session, cutoff, calendar/readiness, full Regime configuration, calculation/engine
  versions, and known source lineage. Consumers filter all eligible candidates before
  recency selection.
- **IBMI:** reusable features may have N/A run/pipeline/context ownership. Ticker, session,
  calendar, full IBMI configuration, calculation/source versions, source lineage, and an
  at-or-before decision cutoff are mandatory. Incompatible evidence is optional omission.
- **Prior Sector:** cross-run history is permitted only for a strictly earlier session with
  matching calendar, Sector configuration, calculation/engine version, mode component, and
  known lineage. Newer incompatible predecessors are skipped.

Cross-run reuse is therefore semantic compatibility, never run equality or unqualified
`latest` selection.

## 6. Winner acquisition contract

The frozen pipeline `MarketCalculationContext` supplies Winner's decision identity. The
exact Decision Handoff supplies pipeline/run ownership, transition anchor, context, and the
expected artifact set. Winner validates Handoff and every independently consumed source
before feature extraction, hashing, and persistence. Historical/backfill capture requires
the original explicit identity and fails closed without it. Frozen `feature_json`, feature
hash, prediction identity, and rescore-from-frozen-vector semantics remain unchanged.

Handoff enforcement level is **RECORDED + VALIDATED + ENFORCED**. It is authoritative for
the artifact inventory it fingerprints, but is **not globally authoritative** or a sole
proof of producer compatibility; individual embedded source identities remain mandatory.

## 7. Legacy / UNKNOWN behavior

- `UNKNOWN` and `LEGACY_UNKNOWN` fail every required compatibility dimension.
- Legacy Fundamental/Technical input cannot prove Combined or Ranking compatibility.
- Legacy Combined/Ranking rows may be replaced only by genuine recalculation from actual,
  identity-aware inputs; persisted identity is never fabricated.
- Legacy CERI results cannot be silently reused or upgraded.
- Legacy Winner predictions remain historical records and cannot be reinterpreted as an
  identity-aware duplicate.
- Missing/legacy Handoff cannot authorize Winner capture.
- No historical identity backfill is performed.

## 8. Negative dependency graph

The following direct edges are absent and certified by source trace plus regression tests:

```text
CERI -X-> Ranking
CERI -X-> Setup
CERI -X-> Winner
SetupSignal -X-> Winner
Setup Lifecycle -X-> Winner
Sector -X-> same-run Ranking
IBMI -X-> Winner
```

Stage order does not create a data dependency. Winner continues to consume Raw,
Fundamental, Technical, Combined, Ranking, Regime, Sector, and Handoff independently.

## 9. Enforcement levels

| Artifact/contract | Level | Boundary |
|---|---|---|
| CalculationIdentity schema/canonical fingerprint | `AUTHORITATIVE` | identity semantic representation |
| Named compatibility policies | `ENFORCED` | the 22 major producer/consumer edges in §4 |
| MarketCalculationContext fingerprint | `ENFORCED` | pipeline context propagation and comparison |
| Producer identity metadata | `RECORDED + VALIDATED` | parsed fingerprint and policy-required fields |
| Decision Handoff | `RECORDED + VALIDATED + ENFORCED` | Winner acquisition; not globally authoritative |
| Legacy/unknown identity | `NOT_ENFORCED` as evidence | isolated/rejected/recalculated, never accepted |
| Operational ownership/lease identity | `NOT_APPLICABLE` to calculation compatibility | T09B write fencing only |

## 10. Finding reconciliation

| Finding | Phase-1 identity result | Remaining non-identity portion | Final status |
|---|---|---|---|
| `RANK-004` | run-only compatibility replaced by exact source/context policy | none in identity scope | `CLOSED` |
| `CORE-006` | Regime→Sector global reuse is compatibility-filtered | broader Regime semantics/readiness | `PARTIALLY_CLOSED` |
| `CORE-007` | prior Sector reuse requires compatible predecessor identity | immutable historical state/evidence | `PARTIALLY_CLOSED` |
| `SETUP-005` | all major Setup source edges are identity-filtered; resume propagation fixed by T10E | general entry-point/replay unification | `PARTIALLY_CLOSED` |
| `SETUP-008` | Combined earnings-risk and Ranking metadata require compatibility | readiness and exact-same-identity mutability | `PARTIALLY_CLOSED` |
| `WIN-001` | current/historical decision identity is explicit; no historical wall clock | none in identity scope | `CLOSED` |
| `WIN-002` | Handoff is validated/enforced before acquisition | global authority not claimed | `CLOSED` |
| `WIN-003` | Ranking identity/profile selection is enforced | readiness policy | `PARTIALLY_CLOSED` |
| `WIN-005` | Regime/Sector acquisition is identity-filtered | confidence/readiness semantics | `PARTIALLY_CLOSED` |
| `XINT-001` | no major Phase-1 edge substitutes execution ownership for calculation identity | later-phase non-identity concerns | `CLOSED` for Phase-1 scope |
| `INV-IDENTITY-001` | enforced on all scoped major paths | absolute all-code/legacy scope remains partial | `ENFORCED` for Phase-1 major paths |

## 11. Residual non-identity risks

| Later concern | Explicitly not certified here |
|---|---|
| Immutable evidence | exact immutable PriceBar/source revision lineage; exact-same-identity row mutability |
| Readiness | Technical/Fundamental/Ranking confidence and ineligibility propagation |
| Configuration authority | repository-wide authoritative effective-config snapshots and governance |
| Entry-point unification | uniform command APIs beyond the identity checks already enforced |
| Background scope | complete durable target-set freezing for every job family |
| Replay/reconstruction | full original-context replay taxonomy and reconstruction engine |
| Integration environments | PostgreSQL, live provider, and browser/E2E campaigns |

## 12. Certification verdict

```text
CALCULATION IDENTITY INFRASTRUCTURE: PASS
COMBINED/RANKING IDENTITY: PASS
CONTEXTUAL CONSUMER IDENTITY: PASS
WINNER ACQUISITION IDENTITY: PASS
LEGACY/UNKNOWN ISOLATION: PASS
CROSS-RUN GLOBAL REUSE SAFETY: PASS
NEGATIVE DEPENDENCY GRAPH: PASS
XINT-001 PHASE-1 CLOSURE: PASS
INV-IDENTITY-001 PHASE-1 ENFORCEMENT: PASS

PHASE-1 CALCULATION IDENTITY LOGIC: PASS
FULL POSTGRESQL INTEGRATION CERTIFICATION: DEFERRED BY PROJECT POLICY
```
