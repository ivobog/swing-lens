# T11B-2 — CERI Immutable Decision Evidence

## 1. Baseline

| Item | Value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T11A commit | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` |
| T11B-1 commit / starting HEAD | `bef1abe285779c3aa278e3691a9438126f5ccedc` |
| Branch | `codex/t11b2-ceri-immutable-evidence` |
| Python | `3.12.2` (`.venv`) |
| Migration head before | `0076_regime_sector_evidence` |
| Migration head after | `0077_ceri_decision_evidence` |
| Initial tracked/untracked state | clean / none |

The branch was created directly from the committed T11B-1 PASS result. No merge, rebase,
push, provider call, live pipeline, or production database operation was performed.

## 2. Scope and preserved graph

T11B-2 extends the T11A shared immutable ledger and independent current projection with
artifact kind `CERI`. It does not redesign the CERI formulas or create a parallel
provenance framework.

```text
provider / SEC / event evidence
+ estimates / earnings / guidance / catalysts
+ bounded price response
+ existing Phase-1 IBMI volatility / short-pressure identities
    -> CERI opportunity / event risk / confidence / posture
    -> immutable CoreCalculationEvidence(kind=CERI)
    -> mutable CeriScoreSnapshot compatibility row and current projection

CERI -X-> Ranking
CERI -X-> Setup/Lifecycle
CERI -X-> Winner
```

The post-score change and alert paths remain downstream operational state. They are not
made inputs to Ranking, Setup/Lifecycle, or Winner.

## 3. Evidence inventory and classification

| Source family | Creation/current behavior | Revision/correction behavior | Purge behavior before T11B-2 | T11B-2 decision treatment |
|---|---|---|---|---|
| Provider-normalized source record | Content-keyed append/idempotent row; raw/restricted fields remain operational | Changed provider content creates a new row and `supersedes_id` chain | Licensed purge redacts/tombstones fields in place | Exact selected IDs, receipt/effective metadata, hashes, correction identity, and restricted normalized payload are frozen; raw vendor payload and URL are not copied |
| Estimate snapshots | One normalized row per source record | Later provider observation/correction creates another source/normalized row | Quality/original fields can be invalidated | Exact current/baseline/consensus estimate rows and conversion source references are frozen |
| Earnings actuals | Source-backed normalized row; surprise attachment updates selection/result columns during calculation | New source evidence remains a distinct row | Warning state can be changed | Exact earnings rows after consensus selection plus selected consensus estimates are frozen |
| Guidance | Source-backed rows with supersession; acceptance/review fields are mutable operational state | New event/supersession can become latest corrected truth | Warning state can be changed | All bounded rows considered by the score, selection/rejection ledger, source records, filing accession, and processor identity are frozen |
| Catalyst | Mutable event cluster plus versioned event revisions/current flag and source links | Revision chain retains event changes; current projection moves | Revision/review/source-link state can be invalidated | Exact event, revision, source-link, selected/rejected IDs, and normalized source rows are frozen |
| SEC material | Filing-document cache metadata and extraction execution rows are operational; normalized guidance/catalyst outputs are source records | Processor signature separates extraction semantics | Source-record policy applies downstream | Filing documents/extractions addressed by the selected guidance accessions and the normalized SEC source evidence are frozen |
| Revision/derived CERI features | Version-keyed rows; some builders reuse/update an exact key | New cutoff/context/config/version permits parallel artifacts | Feature ledgers may be invalidated or cleared | Exact selected revision feature rows and their native source references/hashes are frozen; final ledgers freeze other derived component meaning |
| Price response | Feature key is cutoff/context/version aware, but the feature row is an operational upsert | `PriceBar` is mutable current state reconstructed by `PriceBarRevision` for historical cutoffs | Feature metrics/warnings may be invalidated | Exact feature, projected bar values/data hashes, revisions known at cutoff, and first post-cutoff projection boundary where applicable are frozen |
| IBMI volatility / short pressure | Versioned feature row with Phase-1 Calculation Identity derived from persisted fields | New feature/config/source identity produces another row | Outside CERI provider purge | Exact feature row ID/value plus existing Phase-1 identity payload/fingerprint are frozen; constituent Phase-2 certification is explicitly deferred to T11B-3 |
| CERI rule/config inputs | Parsed immutable runtime config object and hash; posture thresholds are code rules | Deployment/config changes alter later calculations | Not a provider-purge target | Complete parsed effective config payload, digest/version, calculation version, and the applied posture thresholds are frozen |
| Persisted alert rules | Mutable DB rule selected by rule ID downstream of the score | DB row can outlive YAML config | Alert may be invalidated | Not represented as a CERI score input; CERI-008 remains open |
| CERI score snapshot | Append-like version row, but exposed as operational/current history | New run/replay can append another snapshot | Confidence/posture/warnings could be mutated without rehashing | New identity-aware rows point to independently immutable evidence; the compatibility row and evidence seal are written atomically |

## 4. Immutable CERI decision envelope

Each new identity-aware `CeriScoreSnapshot` is sealed through
`persist_core_evidence(... kind=CERI ...)`. Its immutable payload contains:

- the complete CERI snapshot output and component/confidence/risk/opportunity ledgers;
- the exact Calculation Identity payload and fingerprint in the generic envelope;
- explicit `AS_KNOWN` or `LATEST_CORRECTED` view semantics;
- complete normalized estimate, earnings, guidance, catalyst, SEC, feature, and source-row
  snapshots addressed by the calculation lineage;
- the bounded price-response feature and the exact PIT bar representation described in §6;
- exact IBMI feature IDs, values, and existing Phase-1 identities;
- the complete parsed effective CERI config payload, declared hash/version, calculation
  version, and explicit applied posture thresholds;
- one deterministic payload fingerprint and evidence key.

`CeriScoreSnapshot.evidence_id` is a nullable compatibility pointer. The generic evidence
and its native source snapshots are the historical proof. The generic current projection
may move independently and cannot update/delete prior evidence through supported ORM writes.
The snapshot insert and evidence seal occur in one nested transaction, preventing a failed
seal from leaving an identity-aware compatibility row for a later outer commit.

Source raw JSON and vendor URLs are intentionally not duplicated in the immutable envelope.
CERI calculates from normalized rows, and T11B-2 prevents the licensed source record from
being purged while certified decision evidence references it.

## 5. AS_KNOWN versus LATEST_CORRECTED

The native point-in-time selection contract remains unchanged:

```text
E1 known at cutoff -> C1(source=E1, view=AS_KNOWN)
later correction E2 -> C2(source=E2, view=LATEST_CORRECTED or later AS_KNOWN cutoff)
```

C1 and C2 have different source manifests and therefore different immutable evidence keys,
even when their numeric scores happen to be identical. C1 retains E1's frozen normalized
payload, receipt/correction metadata, and view mode. A later correction or current selector
cannot rewrite C1. Exact retries with the same identity, source manifest, rules, and output
reuse the same evidence deterministically.

## 6. Price-response evidence

T09A historical selection remains authoritative for CERI: bars are bounded by session and
cutoff and projected through `PriceBarRevision`. T11B-2 does not redesign global PriceBar
storage. The CERI envelope freezes:

- the selected `CeriPriceResponseFeature`, reaction policy and evidence hash;
- every referenced PriceBar ID and its projected-as-known OHLCV, basis, revision count,
  timestamps, and data hash;
- revisions known by the calculation cutoff; and
- the first later revision used as a reconstruction boundary when an old cutoff is replayed
  after a correction.

Consequently, later mutation of the current `PriceBar` row or insertion of another revision
cannot change the already sealed CERI meaning. Existing CERI price-basis limitations from
CERI-002 are not redesigned or claimed closed.

## 7. Rule evidence

The evidence payload freezes the exact parsed configuration that the score services used,
not only its digest. It additionally freezes the hard-coded posture thresholds applied by
`derive_posture`. Snapshot config hash/calculation version must match the service config, and
the resolved payload must reproduce its declared config hash or sealing fails closed.

This is a preservation boundary, not Phase-4 configuration authority. Values present in raw
YAML but ignored by the current parser are not falsely described as applied. Persisted alert
rules operate after score/change creation and are not score inputs; their stale-row issue is
still CERI-008.

## 8. Purge safety

For newly certified evidence, the smallest safe retention policy is a purge prohibition.
The provider-license preview manifest now identifies immutable CERI evidence referencing its
source set. Execution rechecks that exact manifest and fails before any tombstone, redaction,
snapshot invalidation, change mutation, alert mutation, or rebuild enqueue when references
exist. The preview/audit therefore exposes why execution is blocked.

Legacy and non-certified CERI rows retain the pre-existing audited purge behavior. T11B-2
does not rewrite or certify them, so CERI-010 is only partially remediated rather than closed.

## 9. Change, rebuild, replay, and current projections

| Operation | Classification |
|---|---|
| New capture under the same or later corrected truth | `NEW_EVIDENCE_CALCULATION` |
| Controlled replay/current-rules analysis | `RETROSPECTIVE_CALCULATION`; writes a new CERI evidence artifact |
| Exact retry | deterministic evidence reuse |
| Generic preferred pointer movement | `CURRENT_PROJECTION_WRITE`; not historical evidence |
| Change detection and alert rebuild | downstream operational/current state; not CERI score evidence mutation |
| Provider purge with certified references | rejected before mutation |

Controlled replay snapshots now use the same snapshot persistence/sealing boundary as normal
capture. Replay derives a fresh CERI Calculation Identity from the original temporal context
and its replay source/config payload; an identity/config mismatch fails sealing. New
source/config/output semantics therefore create new evidence rather than altering the
original. Existing replay interpretation and repository-wide historical-reader authority
remain separate future work.

## 10. Legacy handling

Migration `0077` performs no data scan or backfill. Existing rows receive
`evidence_id = NULL`. A CERI row without a valid embedded Phase-1 Calculation Identity is
`LEGACY_CURRENT`; deeper provenance is `LEGACY_UNKNOWN`. It may remain visible through
explicit compatibility/current APIs but cannot satisfy `get_evidence_for_identity` and is
never promoted from its old hash alone.

## 11. Migration and schema

Migration `0077_ceri_decision_evidence`:

- adds `CERI` to the shared evidence/current-projection kind constraints;
- adds nullable indexed `ceri_score_snapshots.evidence_id` with `ON DELETE SET NULL` for the
  compatibility row;
- adds the missing partial unique current-projection index for global ticker scope
  (`run_id IS NULL AND ticker IS NOT NULL`), used by standalone/replay-style evidence;
- preserves all existing rows and performs no data rewrite or inference.

The immutable generic ledger remains protected by T11A ORM update/delete guards. Internal
evidence graph deletes remain restricted. Upgrade/downgrade compile through Alembic's
PostgreSQL operations context. The optional disposable PostgreSQL runtime case is skipped on
this host because the configured test administrator cannot authenticate.

```text
Production data rewrite: NO
Legacy evidence backfill: NO
POSTGRESQL MIGRATION RUNTIME CERTIFICATION: DEFERRED
```

## 12. Tests

| Lane | Result |
|---|---|
| T11B-2 focused semantics + offline PostgreSQL DDL | `13 passed, 0 failed, 1 skipped, 0 deselected, 1 warning` |
| CERI subsystem | `456 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Affected IBMI/contextual integration | `122 passed, 0 failed, 9 skipped, 0 deselected, 1 warning` |
| T11A/T11B-1/T11B-2 evidence regressions | `20 passed, 0 failed, 3 skipped, 0 deselected, 1 warning` |
| Phase-1 identity regressions | `100 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Expanded Phase-0 safety regressions | `146 passed, 0 failed, 0 skipped, 0 deselected, 1 warning` |
| Broader non-production unit/service lane | `2,540 passed, 0 failed, 0 skipped, 34 deselected, 22 warnings` |

The focused suite covers migration DDL/runtime deferral, evidence immutability, provider
correction views, price correction, rule changes, purge blocking, rebuild isolation, same
output/different evidence, retry idempotency, legacy rejection, negative dependency edges,
Phase-1 CERI identity retention, and exact Phase-1 IBMI identity retention.

The nine affected-IBMI skips are existing opt-in/external Windows/IB runtime cases. The
focused migration skip is the unavailable disposable PostgreSQL facility. Warnings are the
existing Starlette/httpx deprecation (plus existing SQLite datetime adapter warnings in the
broad lane).

## 13. Finding and invariant status

| Finding/invariant | T11B-2 status |
|---|---|
| `CERI-008` | `OPEN / EVIDENCE BOUNDARY CLARIFIED`: final score evidence freezes its actual applied config/rules, but persisted alert-rule versioning remains downstream and unsolved |
| `CERI-010` | `PARTIALLY_REMEDIATED`: provider purge is forbidden when a new immutable CERI decision references the source set; legacy/non-certified snapshots retain old tombstone semantics |
| `XINT-005` | `PARTIALLY_REMEDIATED`: immutable evidence now covers core, Regime/Sector, and new CERI decisions; Setup canonical/rules, IBMI constituents, publication, and repository-wide historical-reader enforcement remain |
| `INV-EVIDENCE-001` | `PARTIALLY_ENFORCED`: new identity-aware CERI decisions join Fundamental, Technical, Combined, Ranking, Regime, and Sector; explicit immutable history fails closed for legacy CERI |
| `INV-TRUTH-001` | `PARTIALLY_ENFORCED`: provider, price, rule, rebuild, purge, and current-state changes cannot silently reinterpret new sealed CERI decisions; remaining domains and legacy CERI remain outside certification |

## 14. Residual risks and deferrals

- Full Phase-2 constituent evidence for IBMI volatility and short-pressure is T11B-3; this
  task freezes only their exact existing Phase-1 identity/reference and consumed value.
- CERI-008 alert-rule versioning remains open. Alert policy is not promoted into the CERI
  score envelope because it is not a score input.
- Legacy CERI snapshot/source/feature rows remain mutable under the existing audited purge
  flow and are not evidence-certified.
- Full configuration authority, ignored raw-YAML declarations, source-code/dependency
  attestation, and cross-deployment rule governance remain later work.
- CERI's bespoke price-basis selection and public CURRENT-mode limitations remain outside
  T11B-2; only the actual bounded decision input is frozen.
- Current/UI/export APIs may still expose compatibility rows. Repository-wide immutable
  historical-read adoption remains T11D.
- ORM guards and service purge checks protect supported application writes. Privileged ad
  hoc SQL and operational database permissions remain governance concerns.

## 15. Verdict and production safety

**T11B-2 PASS.** New identity-aware CERI decisions are sealed in the shared immutable
evidence architecture with exact normalized inputs, PIT price meaning, existing IBMI
identity, applied rule payload, and output. Corrections/rebuilds create new evidence, purge
cannot mutate referenced certified decisions, legacy rows remain non-certified, and the
negative dependency graph is preserved.

No production database, provider, SEC endpoint, brokerage, live pipeline, publication
workflow, or other external runtime was contacted or mutated. No migration was applied
outside isolated test attempts.
