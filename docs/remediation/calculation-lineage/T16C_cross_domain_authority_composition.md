# T16C Cross-Domain Historical Authority Composition

## 1. Executive verdict

**PASS.** T16C closes `XINT-007` for supported native evidence. `ORIGINAL_CONTEXT` now requires both exact domain-local authority and compatible cross-domain authority. Thirty-one real dependency edges have machine-readable contracts; seven prohibited edges are certified absent. Legacy rows without material producer pins remain unavailable.

## 2. Baselines

- Original audited baseline: `3a9d47063be996908b7d5d1cc5769e0bbd033546`.
- Phase-6 certification: `2bafa33e0a2cf869c1ac651ec8e2bf30a581c2ab`.
- T16A: `e1e1d089efed7b611bc1e05869e0da2fee736fef`.
- T16B and T16C starting HEAD: `daf2cc05df0887a6271ab9757ac30203d8f2cd3f`.
- Migration head remains `0083_winner_scope_truth`; no migration was required.

## 3. XINT-007 original finding

The audit title is **“Recorded fingerprints and manifests have incomplete proof boundaries.”** Its exact finding is: “Several persisted hashes/manifests are not validated by consumers, omit necessary inputs, or can become stale after mutation.” The missing guard was declared schema/scope plus required consumer comparison for every fingerprint. The defect could make hash presence look like proof of completeness, compatibility, or temporal validity.

## 4. T16A/T16B handoff

T16A assigned exactly `XINT-007` to T16C. T16B transferred no additional finding and authorized reuse of its native resolvers. Assigned: 1; reconciled: 1; lost: 0. `INV-HANDOFF-001` passes.

## 5. Historical dependency graph

The verified graph is preserved: Raw→Fundamental; Price PIT→Technical; Fundamental/Technical→Combined; Fundamental/Technical/IBMI liquidity→Ranking; Ranking→Sector; Technical/Combined/Ranking/Regime/Sector→Setup; Setup and explicit predecessor/rule evidence→Lifecycle/Alert; CERI Change/Rule→CERI Alert; and Raw/Fundamental/Technical/Combined/Ranking/Regime/Sector→Winner independently. The matrix contains no unknown edge.

## 6. Proof-boundary model

`HistoricalAuthorityArtifact`, `HistoricalAuthorityCompatibilityContract`, `HistoricalAuthorityDependency`, `HistoricalAuthorityEdgeResult`, and `HistoricalAuthorityCompositionResult` form one typed, read-only layer over already-certified artifacts. A contract declares exact dimensions, compatible dimensions, and intentionally allowed differences. It never infers compatibility from operational ownership.

## 7. Manifest composition

`OriginalContextReconstructionManifest` embeds declared dependencies, expected and resolved producer authority, edge results, and a proof-boundary fingerprint. A material producer substitution changes both composition and manifest fingerprints. `composition_required=True` prevents an individually exact authority set from authorizing without a compatible composition.

## 8. Compatibility contracts

The canonical registry contains 31 contracts and is deterministically checked against `T16C_cross_domain_compatibility_matrix.csv`. Required dimensions are edge-specific. Literal Calculation Identity equality is not imposed across unrelated calculation families; instead each edge validates its declared calculation context, temporal context, producer address, configuration, readiness, lineage, schema, scope, rule, or predecessor relation.

## 9. Temporal compatibility

Temporal contracts use exact temporal identity or explicit session/calendar and producer-not-after-consumer rules. Technical/Combined/Ranking/Setup decisions requiring one context reject otherwise valid evidence from another session or cutoff. The S1 Setup/R2 Ranking attack returns `INCOMPATIBLE_TEMPORAL_CONTEXT`.

## 10. Configuration compatibility

Configuration identity is retained and integrity-checked on Core evidence. Behavioral edges bind the exact producer-family configuration expected by the consumer; independently configured contextual producers are permitted only where the edge says so. Equal output under C2 cannot satisfy a frozen C1 producer expectation.

## 11. Scope compatibility

Ticker-scoped producers require subject compatibility. Population/global producers use declared population membership proof instead of blind scalar-scope equality. Work-scope and refresh-cycle dimensions are available for contracts where materially frozen; absent legacy scope authority is never inferred.

## 12. Source-lineage compatibility

Producer evidence ID/fingerprint and source lineage/revision are independent proof dimensions. `E1(value=75)` and `E2(value=75)` remain different authorities. Substitution changes the proof-boundary fingerprint and returns `INCOMPATIBLE_SOURCE_LINEAGE` where the edge requires the original source.

## 13. Readiness compatibility

Behavioral producer edges bind the readiness envelope from the same evidence record. A score cannot borrow readiness from another row. Metadata-only Setup edges intentionally omit readiness when it cannot influence Setup behavior.

## 14. Predecessor compatibility

Lifecycle and Alert contracts bind exact evaluation, transition, cooldown, and dedup predecessor identities. Setup chain A cannot be composed with predecessor chain B merely because ticker and time overlap. Cross-chain substitution returns `INCOMPATIBLE_PREDECESSOR`.

## 15. Schema/algorithm compatibility

Material producer edges bind algorithm/evidence schema identity. A legacy body cannot be decoded under current semantics unless an explicit compatibility contract permits it. No contract currently treats schema substitution as harmless.

## 16. Fundamental→Combined

Combined pins exact Fundamental `CoreCalculationEvidence`, configuration, readiness, lineage, and algorithm/schema and requires calculation, temporal, and subject compatibility. A newer compatible-looking Fundamental row is rejected.

## 17. Technical→Combined

Combined pins exact Technical evidence under the same behavioral contract. Current/later Technical evidence cannot replace the retained source, including when its numeric score is equal.

## 18. Ranking inputs

Fundamental and Technical are independent behavioral Ranking inputs. Only IBMI liquidity is an IBMI→Ranking dependency; volatility and short-pressure edges were not introduced. Each input retains its own producer address and required policy envelope.

## 19. Ranking→Sector

Sector binds exact contributing Ranking evidence and its population authority. The reverse same-run Sector→Ranking edge is certified absent, preventing a dependency cycle.

## 20. Setup composition

The T16B Setup resolver now batch-loads its immutable Core evidence closure and embeds a cross-domain composition in the reconstruction manifest. Technical and Combined earnings-risk edges are behavioral; Combined and Ranking score/decision/profile edges are metadata-specific; Regime and Sector use contextual contracts. Valid T16B native Setup remains exact.

## 21. Winner composition

Winner has exactly seven independent contract sources: Raw, Fundamental, Technical, Combined, Ranking, Regime, and Sector. Existing capture authority freezes their source IDs and validates native evidence; T16C’s registry validates their historical composition without adding CERI, Setup, Lifecycle, or direct IBMI. A same-value newer Ranking producer is rejected.

## 22. Negative dependencies

The matrix and code certify these edges absent: CERI→Ranking, CERI→Setup, CERI→Winner, Setup→Winner, Lifecycle→Winner, direct IBMI→Winner, and same-run Sector→Ranking. `reject_undeclared_dependency` produces an explicit `UNDECLARED_DEPENDENCY` rejection.

## 23. Legacy behavior

Legacy evidence lacking a material producer pin is `INSUFFICIENT_EVIDENCE` or `PERMANENTLY_UNAVAILABLE` according to the T16A boundary. Same ticker, nearest timestamp, equal value, same run/pipeline, and current pointers cannot upgrade it. There is no best-effort `ORIGINAL_CONTEXT` fallback.

## 24. Prospective reconstructability

Supported native Core, Setup/Lifecycle/Alert, CERI Alert, and Winner creation paths already retain content-addressed producer/configuration/readiness/rule/predecessor identities. The composition contract makes those identities consumable as future cross-domain proof. `INV-RECONSTRUCT-001` is therefore extended prospectively, without relabeling legacy rows.

## 25. Performance

Resolution uses set-based Core evidence and source-edge maps. The native Setup test proves invariant query count for 1, 50, and 200 targets with a maximum of four SELECTs; validation itself is in-memory and read-only.

## 26. PostgreSQL certification

A disposable PostgreSQL database was migrated to `0083_winner_scope_truth`. Native Setup, Lifecycle, Transition, and Alert original-context resolutions stayed exact and issued no writes, while Setup carried an exact cross-domain composition. A second PostgreSQL certification case exercised exact composition plus incompatible producer, equal-value substitution, temporal/config/scope/lineage mismatch, missing legacy authority, and current-only substitution; focused unit coverage also exercised readiness, predecessor, and schema mismatches.

## 27. Phase-6 regression

The focused Phase-5/Phase-6 regression remains green (`114 passed`, with the expected T15E source-freeze assertion deselected because T16C intentionally changes source files), including `INV-SCOPE-001`, `INV-REFRESH-001`, `INV-HANDOFF-001`, `XINT-012`, and mutation-authority tests. T16C changes no supported current calculation or writer.

## 28. XINT-007 reconciliation

The original defect is closed for supported native paths: consumers validate declared proof boundaries, material dimensions are bound in content-addressed composition, and substitutions fail closed. The truthful residual is legacy unavailability, not an open implementation gap.

## 29. T16D handoff impact

T16D retains exactly `CORE-005`, `CERI-010`, `RANK-007`, `WIN-006`, and `XINT-010`. T16C transfers no finding. It supplies the boundaries T16D must preserve: unavailable legacy provider/revision/config/deployment authority, pre-certification purge loss, Winner birth PriceBar revision gaps, and external governance dimensions.

## 30. Residual risks

Historical rows created before native source/config/rule/predecessor capture can remain permanently unavailable. Repository code cannot prove external provider retention, privileged SQL controls, or an unrecorded deployment SHA. Exact code identity is not over-required when immutable producer evidence fully freezes consumed semantics; it remains material where that freeze is incomplete.

## 31. Final verdict

T16C is certified **PASS** with no migration, backfill, production rewrite, or runtime mutation. `INV-RECONSTRUCT-004` and `INV-PROOF-001` are documented and machine-enforced. `XINT-007` is `CLOSED`; T16D’s five-findings handoff is conserved.
