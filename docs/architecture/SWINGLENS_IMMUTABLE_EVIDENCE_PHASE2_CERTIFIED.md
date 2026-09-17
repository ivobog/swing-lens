# SwingLens Immutable Evidence — Phase 2 Certified Architecture

## 1. Certification baseline

Phase 2 is certified from original audited baseline
`3a9d47063be996908b7d5d1cc5769e0bbd033546` through T11A–T11D, with T11E starting at
`5da05510feb0b43dea2d670e56f49dffb2a913cc`. The certified migration head is
`0079_setup_lifecycle_alert_ev`. Certification used PostgreSQL 16.13 in an isolated,
disposable Docker container on 2026-09-15; no authoritative database or production runtime
was touched.

## 2. Immutable evidence model

`CoreCalculationEvidence` is the shared append-only envelope for Fundamental, Technical,
Combined, Ranking, Regime, Sector, CERI, IBMI, and Setup decisions. It freezes calculation
identity, scope, input/output payloads, hashes, session/cutoff/calendar, and version identity.
`CoreCalculationEvidenceSource` pins exact upstream evidence IDs. Dedicated append-only tables
freeze lifecycle evaluations and transitions plus alert rules and decisions.

Exact deterministic retries may reuse the same evidence row; a different identity, source set,
rule, provider fact, price revision, or decision output creates distinct evidence. Application
writers do not update or delete certified evidence.

## 3. Current projection model

`CoreCalculationCurrentProjection` maps a current scope to an immutable evidence ID. Existing
artifact rows, canonical Setup selection, lifecycle episodes, mutable alert rules, and alert
notification status remain serving projections. They may advance independently. Moving a
projection from E1 to E2 never changes E1, its frozen source graph, or a consumer already
pinned to E1.

## 4. Historical read modes

| Mode | Contract |
|---|---|
| `CURRENT` | Reads the explicitly mutable current projection; historical anchors are rejected. |
| `EVIDENCE` | Requires an exact evidence ID or Calculation Identity and fails closed after a miss. |
| `CURRENT_RULES_RETROSPECTIVE` | Re-evaluates selected inputs under current rules and is not represented as stored history. |
| `ORIGINAL_CONTEXT` | Returns an explicit unsupported/unavailable status where complete original-context reconstruction is not implemented. |

No historical read mode silently falls back to current state, recalculates, repairs, or promotes
a legacy row.

## 5. Evidence graph

The positive graph is:

`Fundamental + Technical → Combined → Ranking`, with Regime and exact prior Sector context
feeding Sector and the consumers that declare them; exact evidence from those branches feeds
CERI, IBMI, and Setup only where the subsystem contract declares it. Setup feeds immutable
lifecycle evaluation, which may produce immutable transition evidence. Setup plus lifecycle and
the exact alert-rule snapshot feed immutable alert-decision evidence. Winner keeps its own
frozen vector and pins exact Combined/Ranking evidence. Pipeline handoff/resume pins and verifies
the complete frozen evidence set.

Every historical edge is an ID edge. A run/ticker pair, equal values, or a newer current pointer
is not a substitute for the recorded source.

## 6. Artifact classification

| Subsystem | Immutable evidence | Current projection | Legacy behavior | Historical lookup behavior |
|---|---|---|---|---|
| Fundamental | Core `FUNDAMENTAL` envelope | scoped current pointer/artifact row | current-only, uncertified | exact ID/identity or unavailable |
| Technical | Core `TECHNICAL` envelope | scoped current pointer/artifact row | current-only, uncertified | exact ID/identity or unavailable |
| Combined | Core `COMBINED` envelope | scoped current pointer/artifact row | current-only, uncertified | exact evidence output |
| Ranking | Core `RANKING` envelope including profile | scoped current pointer/artifact row | current-only, uncertified | exact evidence and profile scope |
| Regime | Core `REGIME` envelope | snapshot/current revision | current-only, uncertified | exact ID; compatible cross-run selection only for new calculations |
| Sector | Core `SECTOR` envelope | snapshot/current revision | current-only, uncertified | exact ID and pinned Regime/Ranking/prior Sector sources |
| CERI | Core `CERI` decision envelope | latest score/detail projection | legacy current/history rows excluded | stored history follows exact evidence |
| IBMI | Core `IBMI` envelope with constituent IDs | latest feature view | legacy feature is uncertified | exact ID and exact constituent map |
| Setup | Core `SETUP` envelope | canonical snapshot selection | `LEGACY_CURRENT`/`LEGACY_UNKNOWN` | exact Setup/transition evidence |
| Lifecycle | evaluation and transition evidence | mutable episode/event projection | legacy chain is current-only | exact predecessor-linked evidence |
| Alerts | rule and decision evidence | rule and notification state | legacy events are current-only | exact rule/decision/predecessor evidence |
| Winner | frozen prediction/vector and versioned evidence | serving/publication pointers | explicit unavailable metadata if unpinned | exact frozen Combined/Ranking IDs |
| Pipeline/resume | decision handoff manifest and evidence members | active execution state | incomplete handoff rejected | fingerprint and every exact member verified |

## 7. Evidence identity

Evidence identity includes artifact kind, run/ticker and profile where applicable, calculation
context, cutoff/session/calendar, calculation/config/version identity, and deterministic payload
and source fingerprints. Equal numeric values under different identities remain different
evidence. Same run and ticker cannot satisfy an identity mismatch.

## 8. Evidence source pinning

Downstream envelopes store exact `source_evidence_id` references with restrictive foreign keys.
Sector pins exact Ranking, Regime, and predecessor Sector evidence; CERI and IBMI retain their
certified constituents; Setup retains all declared calculation and price-manifest sources;
lifecycle and alerts use dedicated exact predecessor links. Resume validates the manifest's
exact members rather than reselecting current rows.

## 9. Later truth vs decision evidence

Provider corrections, price-bar revisions, rule changes, lifecycle advancement, repairs, and
current-pointer movement create or select later evidence. They do not reinterpret an earlier
decision. Later truth remains available through separate current projections and versioned
facts, while historical output continues to mean what the pinned evidence recorded.

## 10. Repair/replay semantics

Repair is `CURRENT_STATE_REPAIR`: valid repairs append evidence and may advance a projection;
an older-context repair that would cross a temporal bound fails closed. Replay is
`CURRENT_RULES_RETROSPECTIVE`: it creates separate evidence and does not rewrite the original
chain. Complete `ORIGINAL_CONTEXT` replay is not claimed and returns an explicit unsupported
status where unavailable.

## 11. Negative dependency graph

The certified graph continues to exclude CERI from Ranking, Setup, Lifecycle, and Winner;
Setup, Lifecycle, and Alerts from Winner acquisition; same-run Sector from Ranking; and a direct
IBMI-to-Winner edge. Shared serialization or DTO definitions do not constitute a production
decision edge.

## 12. PostgreSQL schema guarantees

The linear Phase-2 chain is `0075 → 0076 → 0077 → 0078 → 0079`, with one Alembic head.
PostgreSQL runtime certification covers base-to-head and empty-to-head upgrades, schema/model
agreement, nullable compatibility pointers, deterministic uniqueness, identity/scope indexes,
restrictive evidence foreign keys, ORM persistence/reload, populated downgrade to `0074`, and
re-upgrade. Downgrade removes newer evidence kinds and dependent evidence before restoring the
older kind constraints; it is intentionally destructive for data the older schema cannot
represent and was exercised only on disposable databases.

## 13. Application-level immutability boundary

SQLAlchemy mutation hooks reject supported ORM update/delete operations for shared and dedicated
evidence models. Production writers use insert-or-exact-reuse behavior and update only current
projection tables. Restrictive PostgreSQL foreign keys prevent deletion of referenced evidence.
This is an application/database-reference boundary, not a claim that a privileged database
administrator cannot issue direct SQL updates.

## 14. Known limitations

- Full original-context replay/reconstruction is intentionally unsupported.
- Fundamental provider/effective-time/currency provenance remains broader than Phase-2 evidence.
- Mixed price-basis policy and complete configuration authority remain partial.
- Legacy rows are not fabricated into certified history.
- Current-mode and maintenance operations remain time-sensitive by design.
- Privileged manual direct SQL remains an operational governance concern.
- Alembic emits a non-drift warning for the intentional cyclic lifecycle evaluation/transition
  references; the runtime upgrade and schema comparison pass.

## 15. Deferred later-phase work

Deferred work is original-context replay/reconstruction, readiness propagation, configuration
authority, `PIPE-005` acquisition-plan semantics, refresh-cycle identity, background target-scope
freezing, entry-point unification beyond evidence reads, and privileged direct-SQL governance.
None is silently treated as closed by this Phase-2 certification.
