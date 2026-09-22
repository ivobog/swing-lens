# T16D Legacy, Retention, Archive, and Governance Boundaries

## 1. Executive verdict

**PASS.** All five T16D findings are reconciled and all twelve Phase-7 findings are conserved. Current supported paths have no active defect. Historical facts that were never retained remain explicitly unavailable; they are not recreated from current values, configuration, providers, schedules, security metadata, or PriceBars.

## 2. Baselines

Original audit `3a9d47063be996908b7d5d1cc5769e0bbd033546`; Phase 6 `2bafa33e0a2cf869c1ac651ec8e2bf30a581c2ab`; T16A `e1e1d089efed7b611bc1e05869e0da2fee736fef`; T16B `daf2cc05df0887a6271ab9757ac30203d8f2cd3f`; T16C and T16D start `e3dd98b531600f41df6b377fce443ec816ad8a34`. Migration head remains `0083_winner_scope_truth`. The starting canonical source freeze was 672 source files at `cd3acd4c6c3045139507ab4909d707960d29813464238978b5e1b152075667b3` and 457 test files at `181c3808a7be005388f1031fd8cc21b5cbae2a922f5248ae20e562d07486b91c`.

## 3. T16C handoff

T16C assigns exactly `CORE-005`, `CERI-010`, `RANK-007`, `WIN-006`, and `XINT-010`. Assigned: 5; reconciled: 5; lost: 0. T16D transfers no implementation finding and produces the twelve-finding T16E handoff.

## 4. Finding reconciliation

`CORE-005`, `RANK-007`, and `WIN-006` remain `PARTIAL` solely for exact legacy evidence that does not exist. `CERI-010` closes because current purge cannot silently invalidate certified proof. `XINT-010` closes as a repository configuration-system defect because Phase-4 effective configuration is unified; external DBA/provider enforcement remains an explicit governance boundary.

## 5. Historical vs prospective correctness

Historical recoverability asks whether the old authority exists. Prospective safety asks whether new decisions retain the necessary authority. The answers are independent: an unrecoverable legacy row does not imply an active defect when the native path is exact and fail-closed.

## 6. CORE-005

Original defect: Fundamental inputs lacked structured provider, observation/publication time, fiscal period, revision/effective session, and currency/FX comparability. Legacy raw uploads remain permanently unavailable for exact reconstruction. Supported structured-source paths retain provider/source content, temporal semantics, cutoff, currency, configuration, and Calculation Identity; no current value may backfill a legacy fact.

## 7. CERI-010

Original defect: licensed purge could mutate historical snapshot state while retaining the old evidence hash. Current purge is now governed by the shared typed assessment: pinned material source authority is blocked; unpinned licensed material is purged only with explicit invalidation and `PERMANENTLY_UNAVAILABLE`; operational-only state may be removed without changing exactness. Pre-contract purges remain unrecoverable.

## 8. RANK-007

Original defect: earnings risk used wall clock and an unproven uploaded date. Current certified risk uses explicit business time and source-backed schedule evidence with provider and possession/revision authority. Legacy schedule provenance remains externally or permanently unavailable; today's schedule is never substituted.

## 9. WIN-006

Original defect: native outcome revisions omitted exact constituent bar lineage. Current Winner authority retains exact `PriceBarRevision` identity and body when it exists, otherwise records `REVISION_IDENTITY_UNAVAILABLE`. A legacy symbol/date/OHLCV tuple is not promoted into a synthetic revision identity.

## 10. XINT-010

Original defect: settings, YAML, DB rows, API parameters, and constants combined without one persisted effective configuration. Phase 4 supplies an immutable resolved configuration identity for supported paths, and reconstruction consumes that retained identity rather than `.env`, live defaults, or current settings. Missing legacy K1 remains unavailable even when current K2 produces the same output.

## 11. Archive contract

The archive envelope is content-addressed and preserves semantic IDs, authority content hashes, source manifest fingerprint, payload, and archive fingerprint. A restore revalidates the fingerprint. Human-readable values without those identities are not an authority archive.

## 12. Retention contract

The retention horizon is structural: retain internally controlled material authority while dependent immutable decision evidence remains certified/reconstructable. No arbitrary year count is invented. External exceptions must be explicit when provider terms prevent retention.

## 13. Purge semantics

`OPERATIONAL_ONLY` is purgeable. Material unpinned authority requires an explicit availability downgrade. Material authority pinned by immutable evidence is protected. Semantic mutation plus a continuing exact claim is prohibited.

## 14. Configuration retention

Immutable effective configuration records and embedded snapshots are reconstruction-critical and may not be cleaned up while referenced. Current configuration is never a historical fallback.

## 15. Source-evidence retention

Source records become retention-critical when pinned by certified evidence. CERI enforces pin promotion at purge time; Core evidence and other native domains use immutable references and restrictive relationships.

## 16. PriceBar revision retention

Exact revision IDs, exact values/body hashes, basis, and observation cutoffs are retained where consumed. Missing legacy revision identity is explicit and cannot be inferred from matching current bars.

## 17. Provider governance

Provider revision history, licensing, and contractual archive availability are external. SwingLens retains consumed source evidence where permitted and records an exception where not. It does not claim access to evidence only the provider once held.

## 18. Currency authority

Currency and FX identity are semantic authority where nominal values are compared or converted. Legacy unknown currency cannot be inferred from today's security metadata. Native structured sources retain currency; conversion, when present, must retain FX authority.

## 19. Permanent unavailability

Typed outcomes identify the missing dimension, why it cannot be recovered, why current state cannot substitute it, whether retrospective calculation remains possible, and whether native evidence is safe. “Legacy data problem” is not a sufficient classification.

## 20. Current-rules retrospective boundary

`CURRENT_RULES_RETROSPECTIVE` remains explicit, produces new lineage, and never claims `ORIGINAL_CONTEXT`. The retention classifier does not read current sources to authorize an original-context result.

## 21. Prospective reconstructability

All internally controllable authority required by supported current decisions is retained or referenced immutably. Explicit external-provider exceptions are the only prospective boundary. `INV-RECONSTRUCT-001` is enforced repository-wide for supported native decisions.

## 22. Phase-7 invariants

`INV-ARCHIVE-001`, `INV-RETENTION-001`, and `INV-PURGE-001` are newly formalized and machine-enforced. `INV-RECONSTRUCT-001` through `004`, `INV-PROOF-001`, and `INV-HANDOFF-001` remain enforced.

## 23. Performance

Retention assessment consumes preloaded collections and performs no database query. Tests cover 1, 50, and 200 authorities. Existing reconstruction batching remains capped at four SELECTs for 1/50/200 Setup targets.

## 24. PostgreSQL

Disposable PostgreSQL 16 certifies the existing relational `RESTRICT` and domain purge boundary: certified CERI source authority cannot be removed, legacy nullable states remain classifiable, and current provenance/configuration paths remain intact. Seven selected PostgreSQL tests passed. A fresh database upgraded through `0083_winner_scope_truth`, and `alembic check` reported no new upgrade operations. No authoritative database was used.

## 25. T16B/T16C regression

Native Setup/Lifecycle/Alert reconstruction, CERI historical rule authority, cross-domain composition, compatibility attacks, artifact hashes, and handoff conservation remain green. The focused T16D suite passed 19 tests.

## 26. Phase-6 regression

T15E, scope/refresh/handoff invariants, `XINT-012`, Winner scope truth, and Phase-5 mutation authority remain green. The broad non-E2E run passed 3,632 tests before four fail-closed Phase-5 derivative certificates detected the reviewed CERI source change; after deterministic source-pin and operation-family refresh, the complete 40-test Phase-5 inventory/family suite passed. The historical T15E assertion that repository HEAD must still equal T15D was intentionally deselected. T16D changes no financial calculator or numeric output.

## 27. Finding statuses

- `CORE-005`: `PARTIAL`, current safe, legacy permanently unavailable.
- `CERI-010`: `CLOSED`, current purge proof-safe, old purges unavailable.
- `RANK-007`: `PARTIAL`, current safe, legacy schedule authority unavailable.
- `WIN-006`: `PARTIAL`, current exact-or-explicit-unavailable, legacy revision gap retained.
- `XINT-010`: `CLOSED`, current unified, legacy config and external governance bounded.

## 28. T16E handoff

`T16D_T16E_exact_handoff.json` contains all twelve Phase-7 findings with assigned 12, accounted 12, lost 0, unaccounted empty, no open supported-current defect, exact residuals, evidence, and required integration tests.

## 29. Residual governance risks

External provider retention/revision guarantees, license-based payload deletion, privileged SQL controls, and external archive custody cannot be enforced solely by repository code. These boundaries are explicit and never treated as retained historical proof.

## 30. Final verdict

T16D is `PASS`. Repository-wide Ruff, compileall, diff, secret, Alembic-head, and schema-drift checks passed; all changed Python files pass the formatter. The repository-wide formatter continues to identify 215 pre-existing out-of-scope files. No migration, fabricated backfill, production rewrite, or financial output change is required. Phase 7 is ready for T16E integration certification with three legitimate legacy/external `PARTIAL` findings and two T16D closures.
