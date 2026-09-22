# T16B Setup / Lifecycle / Alert Original-Context Reconstruction

## 1. Executive verdict

PASS. Native Setup, Lifecycle evaluation/transition, and Alert decision evidence can now be resolved through the T16A manifest contract as `ORIGINAL_CONTEXT / EXACT` when every material authority is retained. Missing or inconsistent authority returns typed unavailability without querying current substitutes. Legacy gaps are deliberately not repaired.

## 2. Baselines

- Original audited baseline: `3a9d47063be996908b7d5d1cc5769e0bbd033546`
- Phase-6 certified baseline: `2bafa33e0a2cf869c1ac651ec8e2bf30a581c2ab`
- T16A starting point: `e1e1d089efed7b611bc1e05869e0da2fee736fef`
- Branch: `codex/t16b-setup-lifecycle-alert-reconstruction`
- Migration head: `0083_winner_scope_truth`; T16B adds no migration.

Pre-change T16A SHA256 values were: report `79cfa0057f5c627615f1df6cd058304c07abe8013074b0e715da4c5deeb7de12`, inventory `31c1ea270cb3efb602140d7ff6fff7b276f07108e4a7836b131e7f2acbc9a44a`, authority matrix `c33bbd862f9a268ee19585a68e1ca91d1ec53659306331fedfbd7d016a013ff4`, and handoff `9f3d291b3e6a1f38bc85fd86f344a9f6e866ba360ad85765e01dfa6dd8f4edd7`. The recorded T16A source/test freezes were `9d9a61b0426be6a2ab317835c0e013e7c0a7d22095b0604ed8c444cf5b0992bb` and `83527bf0cbc6a4ac65135703a27920c1d8501e486195af093fc3595e9893eeac`.

## 3. T16A handoff

Exactly six findings were assigned and reconciled: `CORE-009`, `CERI-008`, `SETUP-004`, `SETUP-006`, `SETUP-007`, and `SETUP-010`. Assigned = 6, reconciled = 6, lost = 0. `INV-HANDOFF-001` passes.

## 4. Finding reconciliation

The pre-implementation matrix is `T16B_finding_reconciliation.csv`. It records the original audit defect, T16A class, native target, legacy residual, implementation, tests, status, operation families, and current safe behavior. Statuses are: CORE-009 PARTIAL, CERI-008 PARTIAL, SETUP-004 PARTIAL, SETUP-006 CLOSED, SETUP-007 PARTIAL, SETUP-010 CLOSED.

## 5. Setup reconstruction graph

The native graph is Setup evidence → exact Technical, Combined, Ranking metadata, Regime, and Sector evidence. Combined supplies score/decision metadata and earnings-risk actionability. The resolver requires all five roles, verifies the immutable source-edge graph against `source_evidence_ids_json`, verifies every evidence payload/configuration/identity fingerprint, and rejects missing or mismatched source kinds. CERI and Winner are not Setup inputs.

## 6. Setup manifest

`resolve_setup_original_context` is a domain adapter over the shared batch builder `resolve_original_contexts`. The manifest uses T16A authority dimensions for calculation identity, scope, business cutoff, calendar, effective configuration, readiness, source evidence, decision inputs, and algorithm/schema identity. Stateless Setup calculation marks lifecycle predecessor and alert rule dimensions not applicable rather than material.

## 7. Setup exactness gate

The T16A authorizer is the sole gate. Any material `LEGACY_UNKNOWN`, missing, ambiguous, current-only, corrupt, or non-historical reference prevents a result fingerprint and produces `ORIGINAL_CONTEXT_UNAVAILABLE`. No fallback path exists in the resolver.

## 8. Setup predecessor chains

Setup calculation itself is stateless. Its historical stateful continuation is represented by immutable Lifecycle evaluation and transition evidence. Native chain reconstruction follows explicit predecessor IDs; it never uses `SetupSignalSnapshotCurrentSelection`, a current episode pointer, timestamps, or nearest-row inference.

## 9. Replay semantics

Original-context reconstruction is read-only and returns a separate immutable `ReconstructionResult` envelope. Existing replay remains an explicitly current-rules calculation and is not called by an original-context failure path. A failed exact request stays failed.

## 10. Current-rules retrospective

`CURRENT_RULES_RETROSPECTIVE` remains supported by the certified existing replay writer with current certified configuration and a new calculation identity/lineage. `classify_operation_mode` maps `REPLAY` only to that mode. It can compare a new result with history but cannot claim historical rule identity or `MATCHED_ORIGINAL` under the T16A contract.

## 11. Maintenance/repair distinction

`MAINTENANCE` maps to `CURRENT_STATE_REPAIR`, while reconstruction maps to `ORIGINAL_CONTEXT_RECONSTRUCTION`. Repair may restore a mutable current projection from certified evidence under its existing mutation contract; it neither creates nor implies a historical reconstruction. These names are distinct semantic identities.

## 12. Lifecycle reconstruction

The Lifecycle adapters resolve immutable evaluation or transition evidence, the exact Setup evidence, frozen configuration/readiness, cutoff/calendar, decision inputs, and explicit predecessor state. The target payload is returned as immutable semantic output and compared to its own retained original body.

## 13. Lifecycle predecessor semantics

Evaluation and transition predecessor chains are followed to an explicit retained `null` root. Every row is fingerprint/key checked. Missing rows, cycles, future predecessors, and ticker/timeframe/setup-family mismatches fail closed. Reconstructing T1 reads its retained E1; later E2/T2/current episode state is irrelevant.

## 14. Alert reconstruction

The Setup/Lifecycle Alert adapter resolves the decision, exact rule evidence, Setup/Lifecycle sources, frozen configuration, cutoff/calendar, calculation identity, and cooldown/dedup predecessor chains. It imports no mutable alert rule or notification-state model. A separate `resolve_ceri_alert_rule_original_context` adapter reconstructs CERI’s retained rule/config context without creating a CERI → Setup edge.

## 15. Rule identity

`SignalAlertRuleEvidence` is verified with `signal-alert-rule-evidence-v1`, including payload fingerprint, evidence key, rule ID, version, severity, scope, cooldown, confidence threshold, conditions, restrictions, and metadata. A current `SignalAlertRule` cannot replace missing historical evidence. CERI alerts independently use their sealed `native_alert_proof.rule`, body fingerprint, frozen `decision.alerts.ceri` configuration, and exact source change proof; the adapter never reads the current `CeriAlertRule` row.

## 16. Cooldown lineage

Both cooldown and dedup predecessor members must be explicitly present in the immutable decision payload. A null value is authoritative only as a content-addressed root marker. Non-null chains are followed, verified, checked for cycles, and prohibited from pointing into the future.

## 17. CERI-008

Native retained CERI alerts prove R1/C1/K1 through `native_alert_proof`, the immutable alert body fingerprint, exact source change proof, and frozen effective configuration even after R2/C2/K2 becomes current. The dedicated CERI adapter is separate from Setup reconstruction. Legacy CERI alerts without that proof remain permanently unavailable. Therefore the finding remains PARTIAL overall while the native rule-context path is exact.

## 18. CORE-009

The exact retained Technical evidence and its readiness envelope are required Setup sources. An historical insufficient-readiness state remains in that evidence. The resolver does not recompute Technical, so later N+200 bars cannot turn the historical state into READY. Legacy Setup rows without the Technical evidence ID remain unavailable; status is PARTIAL.

## 19. PriceBar/Technical authority

The proof boundary intentionally prefers exact calculated Technical evidence. Setup decision evidence also seals its PIT bar manifest through the native writer contract. Reconstruction validates the retained Technical artifact and does not query `price_bars`; it therefore cannot consume later bar additions or current revisions.

## 20. Native exact period

No date is guessed. Native exact support begins semantically when all required contracts are present: content-addressed `CoreCalculationEvidence` for Setup and sources, `setup-lifecycle-evaluation-evidence-v1`, `setup-lifecycle-transition-evidence-v1`, `signal-alert-rule-evidence-v1`, and `signal-alert-decision-evidence-v1`, including the required explicit members. Migration 0079 introduced the lifecycle/alert tables, but row-level contract completeness—not migration date alone—is the gate.

## 21. Legacy unavailable boundary

`T16B_legacy_reconstruction_boundaries.csv` records each permanent boundary. Missing configuration, Technical evidence, predecessor IDs, rule identity, or cooldown/dedup lineage is never inferred from current state. Such rows return `PERMANENTLY_UNAVAILABLE` with material dimensions listed.

## 22. Prospective reconstructability

Native Setup/Lifecycle/Alert writers already retain immutable configuration, readiness, sources, and predecessor/rule decisions. T16B adds the resolver that consumes and verifies those facts, moving `INV-RECONSTRUCT-001` to enforced for these native contracts. Corrupt or incomplete new rows fail certification rather than becoming heuristically reconstructable.

## 23. Persistence

No persistence was added. T16A already defines an immutable semantic result envelope; T16B produces it in memory without DML. A generic durable reconstruction ledger remains a cross-domain Phase-7 choice and should not be introduced as a Setup-specific table.

## 24. Performance

The shared loader batches target evidence, rules, source edges, and source evidence with `IN` queries. Recursive predecessor loads are bounded by chain depth, not target count. The 1/50/200 Setup benchmark observes the same query count at every size and enforces a maximum of four SELECTs. Repeated source and Setup validations are cached within a resolution batch.

## 25. PostgreSQL certification

The focused integration suite migrated a disposable PostgreSQL 18 database to `0083_winner_scope_truth`, created a complete native Setup/Lifecycle/Alert chain, resolved all four adapters as exact, and verified the SQLAlchemy session had no new, dirty, or deleted objects (`1 passed`). A separate upgrade plus `alembic check` reported `No new upgrade operations detected.` No persistent reconstruction schema was needed.

## 26. Phase-6 regression

Affected Setup/Lifecycle/Alert writer authority, T15E scope/refresh, T15C Winner isolation, T15B Pipeline/CERI, and Phase-5 mutation authority suites passed (`79 passed`, `2 skipped`, `1 intentionally deselected`). The focused T16A/T16B non-PostgreSQL suite passed (`27 passed`, `1 PostgreSQL test deselected`). Ruff, changed-file formatting, compileall, diff checking, the tracked-secret scan, single-head check, T16A byte-integrity checks, handoff conservation, and certificate completeness passed. T16B changes only new read-only services, tests, and certification artifacts; existing mutation writers are unchanged.

## 27. Negative dependencies

The resolver contains no CERI or Winner imports. The certified graph preserves CERI → Setup absent, CERI → Winner absent, Setup → Winner absent, and Lifecycle → Winner absent. Set-based loading follows only persisted source IDs.

## 28. Finding statuses

- CORE-009: PARTIAL—native historical insufficiency is preserved; legacy missing Technical authority remains unavailable.
- CERI-008: PARTIAL—native exact rule/config is reconstructable; legacy rule identity is unavailable.
- SETUP-004: PARTIAL—native coherent chains are exact; legacy predecessor gaps remain.
- SETUP-006: CLOSED—exact reconstruction and current-rules retrospective are unambiguous and never auto-converted.
- SETUP-007: PARTIAL—native cooldown/rule chains are exact; legacy chains remain unavailable.
- SETUP-010: CLOSED—repair, retrospective, and original-context reconstruction are distinct.

## 29. T16C handoff impact

No T16B finding is transferred. T16C can rely on the unchanged T16A contract and T16B’s domain adapters when resolving cross-domain scope, refresh, acquisition-plan, and revision authority for `XINT-007`.

## 30. T16D handoff impact

No T16B finding is transferred. T16D may use `T16B_legacy_reconstruction_boundaries.csv` as evidence that absent legacy rule, predecessor, provider, revision, or archive facts remain unavailable rather than being synthesized.

## 31. Residual risks

Legacy evidence remains incomplete by design. Exact deployment commit SHA is not retained; code identity is classified BOUNDED and non-material because the resolver reproduces the content-addressed semantic output rather than executing unproven historical binaries. Durable reconstruction-result persistence remains deferred. Chain loading grows with predecessor depth, though not with authority dimensions per target.

## 32. Final verdict

PASS. All six findings are conserved. Native exact evidence resolves under T16A, missing authority fails closed, modes remain distinct, resolution performs no writes, current state is never substituted, performance is set-based, PostgreSQL native-chain coverage exists, and no production rewrite or backfill is performed.
