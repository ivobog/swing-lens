# T16A Original-Context Reconstruction Foundation

## 1. Executive verdict

**PASS.** SwingLens now has a typed, deterministic, fail-closed reconstruction contract and an
exact feasibility inventory for all 12 findings routed by T15E. No legacy authority was fabricated,
no current row can silently substitute for historical authority, and no finding was lost.

This is a foundation result, not a claim that legacy decisions have been rebuilt. The residual
finding-level inventory contains 0 exactly reconstructable, 0 bounded, 2 current-rules-only,
3 evidence-incomplete, 6 permanently unavailable, and 1 external-governance-only target. New/native
subsets are prospectively reconstructable where their retained references validate.

## 2. Baselines

- Repository: `C:/Users/Ivica/Documents/SwingLens`
- Branch before T16A: `codex/t15e-phase6-integration-certification`
- T16A branch: `codex/t16a-original-context-reconstruction-foundation`
- Starting HEAD: `2bafa33e0a2cf869c1ac651ec8e2bf30a581c2ab`
- Original audit: `3a9d47063be996908b7d5d1cc5769e0bbd033546`
- Phase 5: `f587b63e4e35e81486e53ffa0f13b9cd7369f963`
- Phase 6/T15E: `2bafa33e0a2cf869c1ac651ec8e2bf30a581c2ab`
- Migration head: `0083_winner_scope_truth`
- T15E source SHA256: `9b51fd0971ea59fbe95fa8b1b0ba8f2e56a193a0ad961c98f7faebe820b7adf8`
- T15E certificate SHA256: `460456f0195433f8e4a3b9c8bb205e7087c6ed1a069d6673689c2ebcac23dac2`
- T15E finding snapshot SHA256: `415aefe7c1cad358a6d872b424bf63d81c56339e6ccdcce5e3c7c66073b59f9e`
- T15E Phase-7 handoff SHA256: `b9322c53c73add8d670f35dabd2f8332f38840ba12d8000a71b1c3ec43f65791`
- Initial worktree: clean
- Initial local `psql`: unavailable; Docker server 29.6.2 is available, but persistence is not
  introduced by T16A.

## 3. Phase-7 handoff

The authoritative `T15E_phase7_exact_handoff.json` contains exactly 12 findings: `CORE-005`,
`CORE-009`, `CERI-008`, `CERI-010`, `RANK-007`, `SETUP-004`, `SETUP-006`, `SETUP-007`,
`SETUP-010`, `WIN-006`, `XINT-007`, and `XINT-010`.

T16A accounts for 12/12 with 0 lost and 0 unknown reconstructability. Six route to T16B, one to
T16C, and five to T16D. The canonical finding status remains `PARTIAL` for all 12; feasibility
classification does not replace finding status.

## 4. Historical-authority problem

Original-context reconstruction asks what the historical decision actually knew, possessed,
configured, scoped, and calculated. Setting an old timestamp on a current query does not answer that
question. Exact reconstruction therefore requires proof of every material semantic input and fails
closed when proof is incomplete.

## 5. Reconstruction modes

- `ORIGINAL_CONTEXT`: exact retained historical authority only.
- `CURRENT_RULES_RETROSPECTIVE`: current certified rules applied explicitly to historical inputs,
  with new lineage.
- `CURRENT`: current state and current semantic time.

An unavailable original-context request returns `ORIGINAL_CONTEXT_UNAVAILABLE`; the operator must
request retrospective mode separately.

## 6. Reconstruction manifest

`OriginalContextReconstructionManifest` freezes target artifact/decision identity, mode, material
authority resolutions, semantic scope/refresh/plan identities, and code-identity classification. It
derives status, completeness, canonical JSON, and SHA-256 fingerprint. Worker/job attempts are not
semantic identity.

T16A does not add persistence. T16B/T16C should introduce additive immutable storage only if durable
attempt/result APIs require it; no old row may be backfilled.

## 7. Authority dimensions

The typed set covers calculation identity, work scope, refresh cycle, acquisition plan, cutoff,
calendar/session, effective configuration, readiness, source evidence and revision, provider,
currency, possession time, predecessor/prior decision, rules, decision inputs, algorithm/schema, and
code/deployment. Each target uses only dimensions that can affect it.

## 8. Completeness model

Completeness records material required, exact, bounded, unavailable, and permanently unavailable
counts. `exact_for_original_context` is true only when `exact == required`. There is no threshold or
confidence override.

## 9. Exact reconstruction

Exact references must be retained historical, content-addressed historical, an authoritative
archive, or current-looking evidence with independent proof of identical historical identity. The
contract tests the exact case and denies exact status after any material configuration, source,
predecessor, or rule gap.

## 10. Bounded reconstruction

Bounded status requires enumerated alternatives and no unbounded gap. The contract represents the
case where either of two retained revisions could have been selected. It refuses to authorize that
manifest as original-context exact. No T15E residual currently has sufficient evidence to claim a
result bound, so the inventory count is zero.

## 11. Current-rules retrospective

The existing Setup replay is correctly labeled `CURRENT_RULES_RETROSPECTIVE`, records
`original_context_reconstructed: false`, and creates separate evidence when persisted. `SETUP-006`
and `SETUP-010` therefore classify as `CURRENT_RULES_ONLY` until T16B adds a separate native
original-context resolver.

## 12. Configuration availability

Phase-4-native and later paths can retain immutable effective-configuration identity. Pre-Phase-4
legacy rows without it remain `LEGACY_UNKNOWN`. Current `.env`, YAML, defaults, or service config are
not a backfill source. New pinned CERI/Setup alert decisions are prospectively exact; legacy alert
configurations are permanently unavailable.

## 13. Source/revision availability

Supported current structured sources retain exact or content-addressed evidence. Legacy raw
fundamental uploads and earnings schedules lack provider/revision/possession/currency facts.
Pre-certification purged CERI evidence has no sealed archive. Winner rows retain exact PriceBar
revision IDs where present and `REVISION_IDENTITY_UNAVAILABLE` otherwise.

## 14. Temporal authority

Historical eligibility uses the retained business cutoff, session, and SwingLens possession time.
Later facts are excluded even when reconstruction runs today. The attack suite rejects a
current-replacement fact as original temporal authority.

## 15. Scope/refresh authority

Phase 6 exact scope, refresh-cycle, and acquisition-plan identities remain authoritative for
supported current operations. `XINT-007` is evidence-incomplete only for legacy rule/purge/deployment
identity. Legacy cohort membership is never derived from today's eligible population.

## 16. Rule identity

Rules include thresholds, cooldowns, classification/confidence policies, feature interpretation, and
schema/engine version. Current rule identity permits retrospective execution only. Git HEAD is not
deployment or historical rule proof.

## 17. Predecessor state

Setup/Lifecycle/Alert targets need the predecessor snapshot/episode/transition/alert and cooldown
state actually used. Native immutable linkage can be resolved by T16B. A current primary episode or
pointer cannot replace a missing legacy predecessor; the attack suite enforces this boundary.

## 18. Code/deployment identity

The contract distinguishes `EXACT_DEPLOYMENT_IDENTITY`, `BOUNDED_CODE_IDENTITY`, and
`UNKNOWN_CODE_IDENTITY`. T16A found no authoritative deployment ledger that allows timestamp-to-Git
SHA inference for legacy decisions. Retained algorithm/schema identities remain useful but do not
manufacture deployment identity.

## 19. Legacy data

No legacy scope, configuration, revision, provider, rule, or predecessor identity was generated.
Legacy rows retain unavailable classifications. Native-period feasibility does not relabel a legacy
residual as closed.

## 20. Archive limitations

`CORE-005`, `CERI-010`, `RANK-007`, and the pre-capture subset of `WIN-006` are permanently
unavailable absent a future authoritative archive that independently proves the missing identities.
The present repository contains no such archive. T16D owns retention and archive boundary policy.

## 21. External governance

`XINT-010` is `EXTERNAL_GOVERNANCE_ONLY`. Repository app/CLI/scheduler mutation authority is already
certified; privileged DBA SQL and provider-contract enforcement are outside repository code. T16D
must document controls without pretending they are reconstruction logic.

## 22. Phase-7 finding inventory

| Class | Count | Findings |
|---|---:|---|
| EXACTLY_RECONSTRUCTABLE | 0 | — |
| BOUNDED_RECONSTRUCTABLE | 0 | — |
| CURRENT_RULES_ONLY | 2 | SETUP-006, SETUP-010 |
| EVIDENCE_INCOMPLETE | 3 | CORE-009, SETUP-004, XINT-007 |
| PERMANENTLY_UNAVAILABLE | 6 | CORE-005, CERI-008, CERI-010, RANK-007, SETUP-007, WIN-006 |
| EXTERNAL_GOVERNANCE_ONLY | 1 | XINT-010 |

These classifications apply to each finding's residual historical target. Several have exact native
subsets prospectively: new CERI/Setup alerts, Phase-4-native decisions, protected CERI references,
source-backed schedules, and Winner rows with retained revision IDs.

## 23. Setup/Lifecycle/Alert feasibility

An exact Setup reconstruction needs exact technical evidence, combined/ranking metadata where used,
earnings risk, regime, sector, consumed PriceBar revisions, effective configuration, readiness,
business cutoff, scope, and predecessor. The verified dependency graph is preserved: combined
score/decision and Ranking score/decision/profile are metadata; combined earnings risk affects
actionability; Technical, Regime, and Sector feed Setup directly. No CERI-to-Setup dependency is
introduced.

Native retained rows are candidates for T16B resolution. Legacy `SETUP-004` remains incomplete;
existing replay/maintenance remain current-rules/current-state only; legacy alert rules and cooldown
predecessors remain unavailable.

## 24. Other historical domains

Cross-domain scope/refresh/source resolution belongs to T16C for `XINT-007`. Legacy fundamental,
earnings-schedule, purge-archive, and PriceBar revision gaps are archive limitations, not coding
promises. `CORE-009` stays with T16B because projection evidence is part of the retained
Setup/Lifecycle decision boundary.

## 25. Prospective reconstructability

`INV-RECONSTRUCT-001` requires new certified decisions to retain sufficient semantic authority for
later reconstruction or explicitly record a non-retainable external dimension. Supported current
paths assess as yes where all referenced native identities validate. T16B/T16C must turn that
feasibility into resolvers and future integration tests.

`INV-RECONSTRUCT-002` and `INV-RECONSTRUCT-003` are machine-enforced now by
`authorize_reconstruction`.

## 26. Performance

T16A adds no database lookup. Canonical fingerprinting is deterministic and authority batch
validation is set-based. Synthetic 1, 50, and 200 reference fixtures pass. T16B/T16C must resolve
references in bounded batches and may not query once per source.

## 27. PostgreSQL certification

No schema, model, repository query, or persistence is added, so disposable PostgreSQL is not required
for T16A. Local `psql` was unavailable at start and Docker server 29.6.2 was available. The inherited
T15E disposable PostgreSQL 18.6
certificate remains 52 passed. Any future persistence migration must trigger full PostgreSQL
immutability, concurrency, downgrade/re-upgrade, and drift certification.

## 28. Phase-6 regression

The focused Phase-5/6 command produced 120 passes and one known branch-sensitive failure:
`test_t15e_corrected_source_freeze_and_artifact_integrity` recomputes the live checkout and asserts
that HEAD is still T15D, so it necessarily fails after the certified T15E commit and on this Phase-7
branch. The three HEAD-independent T15E assertions pass, and the T15E handoff, finding snapshot, and
certificate SHA-256 values were independently rechecked unchanged. T16A does not change writers,
source selection, scope/refresh, migration state, or negative dependencies. `INV-SCOPE-001`,
`INV-REFRESH-001`, `INV-HANDOFF-001`, and `XINT-012` retain their Phase-6 status.

## 29. T16B handoff

T16B receives `CORE-009`, `CERI-008`, `SETUP-004`, `SETUP-006`, `SETUP-007`, and `SETUP-010` with
exact operation-family IDs and authority matrices. It should implement a separate original-context
resolver/executor for native retained Setup/Lifecycle/Alert/projection evidence, partition legacy
rows, freeze all resolved inputs before calculation, persist only parallel immutable results if
needed, and keep replay/repair semantics unchanged.

## 30. T16C handoff

T16C receives `XINT-007`. It should batch-resolve cross-domain calculation, scope, refresh, plan,
configuration, source revision, rule, and cutoff identities; create a complete manifest or a typed
gap; and measure bounded query counts for 1/50/200 references. It must not derive legacy identities
from current state.

## 31. T16D handoff

T16D receives `CORE-005`, `CERI-010`, `RANK-007`, `WIN-006`, and `XINT-010`. It should formalize
archive/retention classifications, external provider and privileged-SQL governance, prospective
retention guarantees, and explicit permanent-unavailability responses. It must not create synthetic
backfills.

## 32. Residual risks

- No deployment ledger proves legacy timestamp-to-commit identity.
- Exact native-period reconstructability still needs T16B/T16C resolvers and end-to-end execution.
- External archives could alter an unavailable classification only after independent authenticity
  and identity proof.
- A durable reconstruction API may justify additive immutable persistence later.

## 33. Final verdict

T16A passes its foundation scope. The reconstruction modes are unambiguous; exactness is an
all-material proof; current substitution, future knowledge, rule substitution, predecessor
substitution, missing revisions, and silent downgrade fail closed; fingerprints are deterministic;
all 12 Phase-7 findings are conserved; persistence and production runtime remain unchanged.
