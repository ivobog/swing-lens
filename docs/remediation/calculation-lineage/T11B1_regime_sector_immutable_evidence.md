# T11B-1 — Regime and Sector Immutable Contextual Evidence

## 1. Baseline

| Item | Value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase-2 base before T11A | `febe376be67f01589199cd3ba55af98fd54001ed` |
| T11A / T11B-1 starting HEAD | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` |
| Branch | `codex/t11b1-regime-sector-immutable-evidence` |
| Python | `3.12.2` (`.venv`) |
| Migration head before | `0075_core_immutable_evidence` |
| Migration head after | `0076_regime_sector_evidence` |
| Initial tracked/untracked state | clean / none |

The branch was created directly from the required T11A commit. Local `main` and
`origin/main` were both `febe376be67f01589199cd3ba55af98fd54001ed` at the start. The
original lineage registry remains an audited-baseline document and was not rewritten.

## 2. Scope and preserved graph

T11B-1 extends the T11A `core_calculation_evidence`, immutable source-edge, and mutable
current-projection architecture with `REGIME` and `SECTOR` artifact kinds. It does not add
a parallel ledger.

The calculation graph remains:

```text
bounded SPY/QQQ context → Regime → Sector → Setup/Winner
immutable Ranking + Regime + immutable prior Sector → Sector
Sector -X→ same-run Ranking
```

Setup and Winner receive only two additional source identifiers. No Setup/Lifecycle,
Winner scoring, readiness, or publication design was changed.

## 3. Mutability before and after

Before T11B-1, both contextual tables created revisions and marked a current revision, but
the rows remained operational snapshots. Current/latest selectors, deletions, nullable
foreign keys, and later supersession metadata meant a consumed row was not the independent
immutable proof required by `INV-EVIDENCE-001`.

After T11B-1, each identity-aware calculation appends or deterministically reuses a generic
evidence record. The existing snapshot row remains a version/current compatibility view and
holds a nullable `evidence_id`. Moving `is_current_revision`, deleting a compatibility row,
or advancing the generic current projection cannot update/delete the evidence payload or its
source edges. Supported ORM update/delete operations on the ledger remain rejected by the
T11A immutability guards.

| Operation | Classification |
|---|---|
| Regime/Sector service calculation | `VERSION_CREATION` |
| repository snapshot insert/revision/supersession | `VERSION_CREATION` + `CURRENT_PROJECTION_WRITE` |
| `persist_core_evidence` for `REGIME`/`SECTOR` | `IMMUTABLE_EVIDENCE_WRITE` |
| generic projection advance and `is_current_revision` movement | `CURRENT_PROJECTION_WRITE` |
| latest/run/as-of/UI/export queries | `CURRENT_READ` |
| compatible Regime and prior-Sector candidate scans | `CURRENT_READ` feeding a new calculation; evidence is required before consumption |
| `get_evidence_for_identity` | `EVIDENCE_READ`; immutable-only and fail closed |
| rows with no embedded Calculation Identity/evidence pointer | `LEGACY_CURRENT` / `LEGACY_UNKNOWN` |
| Regime `delete_for_run` | compatibility-row maintenance; evidence survives independently |

Pipeline, API recalculation, direct service, and read-only lazy-build entry points converge
on these repositories. Resume uses the same pipeline functions. Test doubles retain their
non-database interface and do not manufacture evidence.

## 4. Regime evidence

`MarketRegimeRepository.upsert_snapshot` now captures `REGIME` evidence for a real,
identity-aware write. Its immutable payload contains the complete write DTO plus run scope
and the repository evidence hash, including:

- Calculation Identity payload/fingerprint and policy;
- as-of session, cutoff, calendar identity, calculation/model version, and config version;
- effective configuration digest already supplied by Phase 1;
- exact fingerprints, row counts, and column identities for the bounded benchmark price and
  trades frames actually consumed;
- input symbols/source sessions, participation and sector-leadership source envelope;
- regime/risk values, confidence, reasons, warnings, gate and policy/profile/setup outputs.

The frame fingerprints strengthen the existing Phase-1 source-envelope digest without a
provider call or fabricated PriceBar revision ID. Full repository-wide configuration
authority and exact immutable raw PriceBar revision lineage remain later work.

Global Regime evidence truthfully uses nullable run/ticker scope. Run-owned Regime evidence
uses its run scope. No run-ID equality was introduced into contextual compatibility.

## 5. Regime current projection

The generic projection table now supports ticker-scoped, run-context-scoped, and global
context-scoped uniqueness through three partial unique indexes. Regime projection movement
therefore has this separation:

```text
R1 evidence (immutable)    R2 evidence (immutable)
                                  ↑
                         current projection
```

An identity read for R1 continues to resolve R1 after the projection moves to R2. Existing
`MarketRegimeSnapshot.is_current_revision` and latest queries remain compatibility/current
views, not historical evidence APIs.

## 6. Sector evidence and exact source graph

`SectorRotationRepository.save_snapshot` freezes the complete snapshot DTO and every sector
row DTO, including aggregates, ranks/deltas, confidence, warnings, gates/permissions,
component scores, configuration/version/mode, debug/source envelope, and evidence hash.

Before persistence, the Sector service obtains the same coherent, identity-compatible set
of Ranking rows used by the universe calculation. It supplies every one to the immutable
graph, plus the selected Regime and selected prior Sector where applicable. The repository
batch-validates that each evidence ID exists and has the required artifact kind:

```text
SectorEvidence S1
  ├── ranking:000001 → RankingEvidence E1
  ├── ranking:000002 → RankingEvidence E2 ...
  ├── regime          → RegimeEvidence R1
  └── prior_sector    → SectorEvidence S0 (when used)
```

Missing source sets, missing evidence IDs, and wrong source kinds raise
`EVIDENCE_UNAVAILABLE`. Mutable Ranking or Regime snapshot IDs alone cannot authorize new
Sector evidence. The source-edge foreign keys remain `ON DELETE RESTRICT`.

## 7. Prior-Sector pinning

The Phase-1 selector still filters by compatible configuration, algorithm/mode, calendar,
and strictly earlier session before recency selection. T11B-1 additionally requires the
candidate to have immutable evidence. When S1 consumes S0, S1 receives a `prior_sector`
edge to S0's evidence ID. Creating S2 or S3 cannot alter that edge or the frozen predecessor
payload.

Legacy identity-only Sector rows remain visible to explicit legacy/current history views,
but cannot act as immutable predecessor evidence for a new identity-aware Sector artifact.

## 8. Cross-run reuse

Compatible global or cross-run Regime reuse remains supported. The selector does not demand
run equality; it applies the existing `SECTOR_REGIME_COMPATIBILITY` policy and then requires
the selected row's immutable evidence pointer. Sector evidence pins that exact Regime
evidence ID. Later Regime recalculation or a different run consuming another projection does
not change the original Sector graph.

## 9. Setup and Winner lineage

New Setup snapshots add `regime_evidence_id` and `sector_evidence_id` to `source_ids_json`
alongside the existing compatibility snapshot IDs. All promoted fields, technical behavior,
actionability, confidence, and lifecycle behavior are unchanged.

New Winner captures add the same two IDs to the already frozen `source_ids_json`. Existing
Winner predictions/vectors are not migrated or rewritten. Later Regime/Sector recalculation
only advances current projections; it cannot mutate a previously frozen Winner lineage map.

## 10. Historical reads and legacy handling

`get_evidence_for_identity` now accepts `REGIME` and `SECTOR`. It queries only the immutable
ledger. It never substitutes a latest/current snapshot or generic current projection. Zero
matches raise `EVIDENCE_UNAVAILABLE`; multiple matches raise `EVIDENCE_AMBIGUOUS` unless the
caller supplies a payload discriminator.

The migration performs no scan, inference, or backfill. Existing snapshot rows retain
`evidence_id = NULL` and are `LEGACY_CURRENT` (deeper provenance may be `LEGACY_UNKNOWN`). A
row without an embedded Phase-1 identity is never promoted by `persist_core_evidence`.
Genuine later recalculation may create proper evidence from the newly calculated DTO and
actual immutable source set.

## 11. Migration and schema

Migration `0076_regime_sector_evidence`:

- expands the two evidence-kind checks with `REGIME` and `SECTOR`;
- makes generic evidence/projection run and ticker scope nullable for contextual/global
  artifacts;
- replaces the old all-non-null projection uniqueness constraint with three partial unique
  indexes covering ticker, run-context, and global-context scopes;
- expands immutable source roles from 32 to 128 characters;
- adds nullable indexed `evidence_id` foreign keys to `market_regime_snapshots` and
  `sector_rotation_snapshots` using `ON DELETE SET NULL` for compatibility rows;
- retains `ON DELETE RESTRICT` inside the immutable evidence graph.

Existing rows are preserved. No values are updated and no evidence is backfilled.

Production data rewrite: **NO**. Legacy evidence backfill: **NO**.

Upgrade and downgrade compile through Alembic's PostgreSQL operations context. The optional
real disposable-PostgreSQL runtime case is skipped on this host because the configured test
administrator cannot authenticate. No production or authoritative database was contacted.

```text
POSTGRESQL MIGRATION RUNTIME CERTIFICATION: DEFERRED
```

## 12. Tests

| Lane | Result |
|---|---|
| T11B-1 focused semantics + offline PostgreSQL DDL | `4 passed, 0 failed, 1 skipped, 0 deselected, 1 warning` |
| Regime/Sector subsystem | `97 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Setup/Winner consumers | `567 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| T11A immutable evidence | `3 passed, 0 failed, 1 skipped, 0 deselected, 1 warning` |
| Phase-1 identity regression | `118 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Expanded Phase-0 regression | `144 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Broader non-production unit/service lane | `2,540 passed, 0 failed, 0 skipped, 34 deselected, 22 warnings` |

Focused coverage proves Regime immutability/projection/history, global scope and cross-run
pinning, same-values/different-identity separation, exact retry reuse, complete Sector row
payloads, all Ranking edges, Regime and prior-Sector edges, source-kind rejection, legacy
rejection, and offline migration upgrade/downgrade. Existing Setup/Winner tests prove the new
IDs while the complete consumer lane verifies unchanged behavior.

The focused/T11A skips are the same unavailable disposable-PostgreSQL runtime facility.
Warnings are the existing Starlette/httpx deprecation and, in the broad lane, 21 Python 3.12
SQLite datetime-adapter deprecations.

## 13. Finding and invariant status

| Finding/invariant | T11B-1 status |
|---|---|
| `CORE-006` | `CLOSED_FOR_NEW_IMMUTABLE_EVIDENCE`: compatible global Regime selection now also pins exact immutable evidence; Regime readiness/confidence semantics remain open |
| `CORE-007` | `CLOSED_FOR_NEW_IMMUTABLE_EVIDENCE`: prior Sector use requires and pins the exact predecessor evidence; legacy/current history views remain non-evidence |
| `XINT-005` | `PARTIALLY_REMEDIATED`: immutable evidence now covers the core four plus Regime/Sector; remaining domains and repository-wide historical-read enforcement remain |
| `INV-EVIDENCE-001` | `PARTIALLY_ENFORCED`: enforced for Fundamental, Technical, Combined, Ranking, Regime, and Sector plus their explicit identity reads |
| `INV-TRUTH-001` | `PARTIALLY_ENFORCED`: later core/contextual current truth cannot change previously captured evidence; remaining decision domains are outside T11B-1 |
| `INV-IDENTITY-001` | `PRESERVED`: all Phase-1 contextual policies remain green; evidence availability is an additional gate, not a replacement identity rule |

## 14. Residual risks

- Full authoritative effective-configuration snapshots/governance remain later
  configuration-authority work; the complete effective digest and available version are
  pinned here.
- Exact immutable raw PriceBar revision IDs are not available in the Regime layer; complete
  bounded-frame fingerprints are pinned without claiming provider/revision provenance.
- Generic UI/export/history endpoints still expose explicitly current/versioned compatibility
  rows. Repository-wide migration of historical callers belongs to T11D.
- CERI, IBMI, Setup/Lifecycle/Alert state, and Winner publication evidence are outside this
  task.
- ORM guards and repository boundaries protect supported application writes; privileged
  ad-hoc SQL remains an operational-governance concern.
- A downgrade after contextual evidence has been written intentionally cannot coerce nullable
  contextual scope back into T11A's non-null-only schema without operator disposition; the
  migration performs no destructive cleanup.

## 15. Verdict and production safety

**T11B-1 PASS.** Newly calculated identity-aware Regime and Sector artifacts have immutable
evidence, current projection movement is separate, Sector pins exact Ranking/Regime/prior-
Sector evidence, compatible cross-run reuse remains supported, legacy rows cannot satisfy
explicit evidence reads, and Setup/Winner retain exact contextual evidence IDs.

No production database, pipeline, provider, brokerage, publication workflow, or external
runtime was contacted or mutated. No migration was applied outside isolated test attempts.
