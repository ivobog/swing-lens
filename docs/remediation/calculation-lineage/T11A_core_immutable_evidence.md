# T11A — Core Immutable Decision Evidence

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase-2 remediation base | `febe376be67f01589199cd3ba55af98fd54001ed` |
| T11A starting HEAD | `febe376be67f01589199cd3ba55af98fd54001ed` |
| Starting branch | `main` (clean; equal to `origin/main`) |
| T11A branch | `codex/t11a-core-immutable-evidence` |
| Python | `3.12.2` (`.venv`) |
| Migration head before | `0074_ceri_evidence_quarantine` |
| Migration head after | `0075_core_immutable_evidence` |

The Phase-2 base is the current certified `main`, includes the Phase 0/1 integration
campaign, and descends from the original audited baseline. No prior remediation history was
rewritten.

## 2. Scope

This change establishes immutable evidence for identity-aware `FundamentalScore`,
`TechnicalScore`, `CombinedResult`, and `RankingResult` calculations. It adds only the
minimum Setup and Winner source references needed to identify that evidence. Regime,
Sector, CERI, IBMI, Setup/Lifecycle state, Alerts, readiness policy, replay, and global
historical-read enforcement remain outside T11A.

## 3. Previous mutability model

| Artifact | Create/retry/recalculation behavior before T11A | Classification |
|---|---|---|
| Fundamental | upload inserted; admin/pipeline recalculation deleted every run row and reinserted | `DELETE_RECREATE`; upload-only rows are `LEGACY_CURRENT` |
| Technical final score | scorer deleted selected run/ticker rows and reinserted; feature cache separately upserts content-addressed artifacts | result `DELETE_RECREATE`; cache `APPEND_ONLY`/content addressed |
| Combined | recompute cleared Winner's current-row FK, deleted run rows, and reinserted | `DELETE_RECREATE` |
| Ranking | inserted missing rows, deleted/reinserted legacy rows, and copied new values into identity-aware rows | `MUTABLE_CURRENT` |

API/admin/pipeline entry points converge on these services. Resume/retry re-entered the
same replacement/upsert writers. A run/ticker row was therefore a current result, not
durable historical evidence.

## 4. New evidence architecture

`core_calculation_evidence` is an append-only ledger differentiated by
`FUNDAMENTAL`, `TECHNICAL`, `COMBINED`, and `RANKING`. Each record stores:

- the complete canonical Phase-1 Calculation Identity payload and fingerprint;
- a canonical payload of every mapped result attribute except the mutable current-row
  physical ID, its evidence pointer, and projection timestamps;
- output/payload fingerprint and deterministic evidence key;
- run, ticker, optional Ranking profile, source-evidence IDs, and calculation time.

`core_calculation_evidence_sources` is an append-only graph. Combined and Ranking each
receive `fundamental` and `technical` edges to exact immutable upstream evidence IDs.
Foreign keys use `RESTRICT`; supported ORM update/delete operations on evidence and source
edges raise `IMMUTABLE_EVIDENCE_MUTATION_REJECTED`.

The ledger is not a broad data copy or a fabricated provenance backfill. It freezes the
exact producer output and only the identity/provenance Phase 1 can actually prove.

## 5. Current projection architecture

`core_calculation_current_projections` is the mutable serving pointer, uniquely scoped by
artifact kind, run, ticker, and Ranking profile. It points at one immutable evidence row.
The existing four result tables remain compatibility/current projections and retain their
existing UI/export query behavior. Their new nullable `evidence_id` identifies the evidence
represented by a newly calculated row.

Advancing either compatibility projection or the dedicated pointer does not update or
delete old evidence. Historical callers use the evidence API, never the compatibility
table as fallback.

## 6. Fundamental evidence

Identity-aware recalculation now freezes score, all component/quality/risk values, coverage,
warnings, labels, explanation/debug output, model/config identity, raw-row lineage available
in Calculation Identity, and calculation time. The old current row may still be replaced;
its prior evidence cannot be. Initial upload scoring has no frozen market/pipeline identity
and intentionally remains `LEGACY_CURRENT` until a genuine identity-aware recalculation.

Provider effective time, fiscal revision, currency/FX lineage, and other unproven
`CORE-005` provenance are not invented.

## 7. Technical evidence

The final result payload freezes score/components, confidence, `insufficient_data`, missing
data, warnings, engine/version/debug output, cutoff/session/calendar, and the Phase-1 source
lineage. Existing technical feature-cache and price-series identity designs are retained.
No readiness interpretation or gate changed.

## 8. Combined evidence

Combined evidence freezes final score/rank/decision, earnings risk, completeness/warnings,
config/version/debug output, and exact Fundamental/Technical evidence edges. Creation fails
closed with `EVIDENCE_UNAVAILABLE` in a real evidence-aware writer when either upstream row
lacks immutable evidence. Moving upstream projections cannot change an existing Combined
evidence graph.

## 9. Ranking evidence

Ranking evidence freezes profile/rank/score/decision, component scores, penalties, gates,
warnings/completeness, profile/config/cohort identity in Calculation Identity, and exact
Fundamental/Technical evidence edges. Any validated IBMI identity remains inside the
Phase-1 identity/source lineage; IBMI itself is not incorrectly promoted to T11A immutable
evidence. That boundary remains T11B.

## 10. Evidence identity and idempotency

The evidence key is SHA-256 over artifact kind, Calculation Identity fingerprint, canonical
payload fingerprint, and exact upstream evidence IDs. The database unique constraint blocks
duplicate keys. A normal exact retry looks up and reuses the same record. A different
identity creates a different record even when all numerical outputs are equal. A changed
payload/source set is never written over an existing record.

## 11. Legacy handling

Rows without an embedded Phase-1 Calculation Identity retain `evidence_id = NULL` and are
classified `LEGACY_CURRENT` (their deeper provenance may be `LEGACY_UNKNOWN`). The migration
does not scan, rewrite, infer, or backfill existing data. `persist_core_evidence` returns no
evidence for such a row. `get_evidence_for_identity` queries only the immutable ledger and
raises `EVIDENCE_UNAVAILABLE`; it never falls back to a current/legacy table.

## 12. Consumer migration

Setup source IDs now include available Fundamental, Technical, Combined, and Ranking
evidence IDs. This is lineage metadata only: Technical remains behavioral, Combined
`earnings_risk` remains behavioral, and Combined/Ranking scores/decisions remain metadata.

Winner capture now freezes the four core evidence IDs in `source_ids_json` alongside legacy
current-row IDs. Newly recalculated sources can therefore be identified after later current
rows are replaced. Existing Winner rows and frozen vectors are not migrated or mutated.

Sector received no runtime behavior change. Its existing Ranking consumption remains a
current-mode integration and was regression-tested.

## 13. Migration/schema impact

Migration `0075_core_immutable_evidence` adds three tables, indexes/constraints, and one
nullable `evidence_id` foreign key/index to each existing core result table. Existing tables
and rows are preserved. Upgrade and downgrade compile as PostgreSQL DDL. A real disposable
PostgreSQL round-trip remains skipped on this host because the configured admin credentials
are rejected; no authoritative/production database was contacted.

Production data rewrite: **NO**. Legacy evidence backfill: **NO**.

## 14. Tests

| Lane | Result |
|---|---|
| T11A focused semantics + offline PostgreSQL DDL | `3 passed, 0 failed, 1 skipped, 0 deselected, 1 warning` |
| Core Fundamental/Technical/Combined/Ranking | `106 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Setup/Winner/Sector consumers | `96 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Phase-1 identity regression | `134 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Phase-0 safety regression | `43 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Broader non-production unit/service lane | `2,548 passed, 0 failed, 7 skipped, 33 deselected, 22 warnings` |

Focused coverage proves all twelve requested families: four-kind immutability/versioning,
same-values/different-identity, retry reuse, projection movement, legacy rejection,
Combined/Ranking source pinning, Winner preservation, explicit mutation rejection,
unchanged Technical readiness representation, and Phase-1 compatibility regression.

Warnings are the existing Starlette/httpx deprecation and 21 Python 3.12 SQLite datetime
adapter deprecations. The one focused skip is the disposable-PostgreSQL schema round-trip;
the migration upgrade/downgrade is still compiled through Alembic's PostgreSQL offline
operations context. External providers, live brokerage, production databases, and browser
E2E were not used.

## 15. Static writer/read classification

| Location/operation | Classification | Rationale |
|---|---|---|
| `upload_service.create_upload_run` Fundamental insert | `LEGACY` | no Phase-1 market/pipeline identity; current-only |
| `fundamental_score_service` delete/reinsert | `CURRENT_PROJECTION_WRITE` | compatibility row replacement followed by evidence capture |
| `technical_score_service` final delete/reinsert | `CURRENT_PROJECTION_WRITE` | final current result only |
| `technical_artifact_cache.upsert_local_artifact` | `OUT_OF_SCOPE` | retained stronger feature cache, not final-score history |
| `combined_decision` Winner FK clear + delete/reinsert | `CURRENT_PROJECTION_WRITE` | old Winner vector/source JSON remains frozen; evidence retained |
| `ranking_profile_service._copy_ranking_values` | `CURRENT_PROJECTION_WRITE` | compatibility projection may advance |
| `ranking_profile_service._replace_legacy_ranking` | `LEGACY` | replaces current legacy row; never converts it to evidence |
| `persist_core_evidence` new ledger/source rows | `IMMUTABLE_EVIDENCE_WRITE` | append/reuse only; unique deterministic key |
| `_advance_current_projection` | `CURRENT_PROJECTION_WRITE` | only dedicated pointer mutates |
| `get_current_evidence` | `CURRENT_READ` | explicitly named projection resolution |
| `get_evidence_for_identity` | `EVIDENCE_READ` | immutable-only, fails unavailable/ambiguous |
| run/UI/export/history queries of four old tables | `CURRENT_READ` / `LEGACY` | no explicit historical Calculation Identity contract |
| Setup/Winner/Sector run-context loaders | `CURRENT_READ` | capture current compatible rows; new lineage freezes evidence IDs |

Searches covered `delete`, `update`, `upsert`, `merge`, `bulk_update`, `session.delete`,
`commit`, `on_conflict`, `latest`, `first`, `order_by`, and `current` in all core writers and
affected readers. No unexplained in-scope `REMAINING_PHASE2_DEFECT` remains. Generic legacy
run-history views are explicitly not certified as immutable historical evidence; they do
not accept a Calculation Identity and remain for T11D migration.

## 16. Residual Phase-2 risks

- Fundamental effective-time/provider/fiscal/currency provenance remains incomplete.
- IBMI, CERI, Regime/Sector, Setup/Lifecycle/Alerts, and publication evidence are not made
  immutable by T11A.
- Existing core rows and existing Winner/Setup records are not backfilled.
- Existing UI/export/run-history APIs still expose compatibility current tables; callers
  requiring historical identity must use the new evidence API. Repository-wide enforcement
  belongs to T11D.
- ORM guards and repository boundaries reject supported mutations; privileged ad-hoc SQL is
  outside the application writer contract and should be governed operationally.
- A concurrent first insert is bounded by the unique evidence key; the losing transaction
  may retry and reuse. T11A does not introduce a new global concurrency framework.

## 17. Finding/invariant status

| Finding/invariant | T11A status |
|---|---|
| `CORE-005` | `PARTIALLY_REMEDIATED`: new Fundamental output is immutable; missing provider/effective-time/currency provenance remains open |
| `RANK-006` | `CLOSED_FOR_NEW_IDENTITY_AWARE_EVIDENCE`: recalculation advances current while immutable Ranking/upstream evidence remains; legacy rows remain current-only |
| `SETUP-009` | `PARTIALLY_REMEDIATED`: immutable core source IDs are available to new Setup snapshots; mutable canonical/episode semantics remain T11C |
| `XINT-005` | `PARTIALLY_REMEDIATED`: core four artifacts only; repository-wide current/history ambiguity remains |
| `INV-EVIDENCE-001` | `PARTIALLY_ENFORCED`: Fundamental/Technical/Combined/Ranking evidence and explicit identity reads only |
| `INV-TRUTH-001` | `PARTIALLY_ENFORCED`: expanded beyond Winner to the core four; contextual evidence remains |

## 18. Final verdict

**T11A PASS.** New identity-aware core calculations append or reuse immutable evidence,
current projections advance independently, historical identity reads fail closed, and
Combined/Ranking pin exact immutable upstream evidence. Legacy data remains honest and
unchanged. T11B contextual evidence, T11C Setup/Lifecycle/Alert evidence, T11D global
historical-read enforcement, and T11E Phase-2 certification remain deferred.

No production/runtime mutation, provider call, brokerage action, Winner publication, or
production migration was performed.
