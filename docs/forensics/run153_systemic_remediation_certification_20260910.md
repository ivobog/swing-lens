# Run 153 systemic remediation certification

Certification date: 2026-09-10

## A. Executive verdict

All three Run 153 defects are systemically remediated. The selected architecture is Model B: preflight persists a canonical semantic decision manifest and production reconstructs and exactly compares that manifest before any lifecycle mutation. A restored-production clone proved 138/138 real candidate manifests equal and proved a material bar change is rejected before snapshot, pointer, or ledger writes. Pipeline-owned CERI temporal feature artifacts now have service and PostgreSQL enforcement of context lineage. Price-bar integrity now distinguishes immutable market/PIT evidence from operational re-observation metadata.

```text
DECISION-MANIFEST EQUIVALENCE CERTIFIED: YES
CERI ARTIFACT LINEAGE CERTIFIED: YES
IMMUTABLE EVIDENCE CLASSIFICATION CERTIFIED: YES
RUN-153 SYSTEMIC REMEDIATION CERTIFIED: YES
SAFE FOR ONE FINAL LIVE DUAL-MODE CANARY: YES
```

## B. Baseline

| Item | Observed |
|---|---|
| Required branch | `codex/run153-decision-manifest-lineage-remediation` |
| Starting HEAD/worktree | `0363a1483b747ee8f551766421e4fc04704d912f`; clean |
| Certified ancestry | `0363a148`, `a4e80fef`, and `b278601` are all ancestors: YES |
| Code/production Alembic at start | `0071_transition_preflight_plan` / `0071_transition_preflight_plan` |
| Production DB | PostgreSQL 18.3, database `swinglens`, local endpoint 127.0.0.1:5432 |
| Runtime | app STOPPED; worker STOPPED; supervisor STOPPED; port 8000 listeners 0 |
| Jobs | active 0; queued 0; retrying 0 |
| Latest run/pipeline/job | 153 / 145 / 42977 |
| Baseline clock | approximately 2026-09-10 12:33 Europe/Zurich, 10:33 UTC, 06:33 America/New_York |

The actual repository sources were read: `docs/forensics/final_live_dual_mode_canary_20260910.md` and `artifacts/forensics/final_live_dual_mode_canary_summary.json`. Production remained on 0071 throughout this task.

## C. TBLA divergence reconstruction

Plan 2 reserved Context 6 with cutoff `2026-09-10T08:13:04.357207Z`, latest completed session 2026-09-09. TBLA was HIGH, expected pointer 5815 → snapshot 18885 revision 1, and planned `TBLA/1d/2026-08-03`. Its evidence/technical fingerprints were `96a5ad0c…` / `388af2ab…`.

Execution persisted technical row 33636 and snapshot 36950. The technical source lineage ended at 2026-07-31 for both adjusted and trades inputs, so snapshot 36950 used `TBLA/1d/2026-07-31`; pointer 5815 did not change.

| Decision input | Preflight | Historical execution | Equal? | Root reason if different |
|---|---|---|---:|---|
| Context/cutoff/session | 6 / 08:13:04Z / Sep 9 | same | YES | — |
| Candidate type | SAME_SESSION_REPLACEMENT | non-competing new key | NO | downstream of bar projection |
| Latest eligible ticker bars | 1786648/1786650, Aug 3 | 1786647/1786649, Jul 31 | NO | old loader discarded post-cutoff-revised current rows |
| Aug 3 values as known at cutoff | volume 3,067,219 | absent | NO | revision history was not projected backward |
| Technical input end session | Aug 3 | Jul 31 | NO | same loader defect |
| Technical score/classification | preview semantic fingerprint `388af2ab…` | persisted historical fingerprint differs; dual 6.7869 / No trade | NO | historical row was computed from the truncated frame |
| Selection/data-as-of key | Aug 3 | Jul 31 | NO | latest eligible bar drives the key |
| Expected/actual pointer | 5815 / 18885 / rev 1 | 5815 / 18885 / rev 1 | YES | mismatch prevented competition |
| Lifecycle snapshot | prospective | 36950, non-canonical | NO | no old pre-mutation equality gate |

Revision rows 34268/34269 prove both Aug 3 bars existed before cutoff with volume 3,067,219, then were observed after cutoff with volume 3,069,033. Their identity and previous values were not uncertain. The fixed loader reconstructs Aug 3 exactly. The read-only Run 153 reproducer now shows the fixed preflight and fixed lifecycle loader both select Aug 3 while correctly identifying that the already-persisted historical technical artifact differs. The restored clone proves newly produced preflight and execution manifests are equal.

## D. Preflight vs production call graphs

Preflight:

```text
Context reservation
→ SetupLifecycleSourceLoader
→ shared project_price_bar_rows_as_of
→ preview_run_technicals (production scorer/repositories)
→ shared build_run_context_snapshots (history + velocity)
→ TransitionCandidateDiscoveryService.assess
→ canonical TransitionDecisionManifest
→ immutable plan JSON/fingerprint
```

Production:

```text
same Context claim/reload
→ score_run_technicals (same PIT projection/scorer, persisted result)
→ SetupLifecycleSourceLoader
→ shared project_price_bar_rows_as_of
→ shared build_run_context_snapshots (same history + velocity)
→ same assess + manifest builder
→ exact canonical equality under locked consumed plan
→ only then evaluation/snapshot/canonical-selection/pointer/ledger writes
```

The same repository, query filters, revision projection, deterministic ordering, session resolver, serializer, rounding semantics, engine/config versions and fallback context now feed both paths. Preview lacks only a database technical-score ID; the manifest intentionally excludes that administrative ID and binds the full semantic score fingerprint instead. Technical debug binds the input signature, source latest sessions, benchmark/sector status and engine/config semantics, so those technically relevant inputs are transitively covered.

## E. Decision Input Manifest design

`transition-decision-manifest-v1` contains only selection-relevant facts:

- frozen context ID, cutoff, latest session, calendar and readiness versions;
- ticker, timeframe, data-as-of/selection key, candidate type/reason/confidence and predicted advances;
- expected latest/exact pointer snapshot IDs and revisions;
- technical semantic fingerprint, input session, cutoff/context and engine version;
- every eligible ticker bar ID, session, basis, revision/data identity and immutable-evidence hash;
- stable source IDs (excluding only the non-semantic preview/persisted technical DB ID);
- lifecycle engine/config/schema versions, promoted score/classification fields, signals, flags, missing-data, coverage, freshness and quality.

CanonicalEvidenceSerializer provides UTC normalization, Decimal stability and sorted maps. Bar sets are explicitly sorted. Equality is between canonical semantic objects plus their SHA-256 fingerprints, never presentation JSON.

## F. Manifest equality/mutation gate

The plan stores each manifest and fingerprint. Enqueue repeats preflight discovery and rejects changed manifests. Immediately before capture, production reconstructs all expected manifests under a row lock on the consumed plan. Any missing, extra or unequal ticker raises `DECISION_MANIFEST_MISMATCH` before lifecycle evaluation-run creation and therefore before snapshot, pointer, selection-ledger or decision-linked writes.

Failure injection changed one eligible bar’s volume/data hash after preflight. The gate rejected all 138 expected tickers; lifecycle snapshot, current-selection and selection-event counts were identical before/after. Query-order and timezone variants remain equal; pointer changes and actual OHLCV/revision changes do not. A `last_seen_at`-only re-observation remains equal by contract.

## G. Broad production-path replay

The guarded script used the restored production clone, real `create_transition_preflight_plan`, real `start_pipeline`, real `score_run_technicals`, the production loader/builder and the pre-mutation gate.

| Result | Count |
|---|---:|
| Cases | 138 |
| Equivalent | 138 |
| Divergent | 0 |
| HIGH / LOW | 94 / 44 |
| New-session initialization | 93 |
| Same-session replacement | 1 |
| Non-transition (`NOT_BETTER`/incomplete) | 44 |

The real candidate set includes missing-input and non-transition controls. Separate temporal/preflight suites cover late revision, holiday/weekend, readiness boundary, warm/cold cache, retry and pointer-race behavior. No unexplained divergence remains.

## H. CERI artifact-lineage inventory

| Artifact | Table/model | Pipeline-owned? | Context field exists? | Required? | All write paths set it? |
|---|---|---:|---:|---:|---:|
| Parent/child jobs | `background_jobs` | YES | payload JSON | YES | YES; handlers reject missing context |
| Processing run | `ceri_processing_runs` | YES for workflow runs | `scope_json` + `cutoff_at` | YES | YES for pipeline feature work |
| Estimate/earnings/guidance/catalyst/source records | source/normalization tables | ingestion facts, context-independent | source known-at provenance | NO | N/A; reused across contexts by PIT queries |
| Revision feature | `ceri_revision_features` | YES or standalone | 0072 columns | conditional | YES |
| Derived feature | `ceri_derived_features` | YES or standalone | 0072 columns | conditional | YES |
| Price response | `ceri_price_response_features` | YES or standalone | existing context columns + 0072 ownership | conditional | YES |
| Feature build state | `ceri_feature_build_states` | YES or standalone | 0072 columns | conditional | YES |
| Score snapshot | `ceri_score_snapshots` | YES | existing FK/columns | YES | YES |
| Change event | `ceri_change_events` | derived from score snapshots | snapshot FKs | transitive explicit | YES |
| Alert event | `ceri_alert_events` | derived from change/snapshot | change/snapshot FKs | transitive explicit | YES |
| Controlled replay | `ceri_controlled_replays` | standalone certification | explicit original cutoff | context optional by design | YES |

## I. Rows 8684/8685 root cause

Root pipeline job 42962 resumed at 42964. Feature child 42973 carried Context 6, cutoff `08:13:04.357207Z`, session Sep 9 and the calendar. It processed DRS (company 1011) and TBLA (1012). Capture child 42975 carried the same payload context; snapshot 10930 persisted Context 6.

Row 8684 is DRS catalyst 18933, reaction Sep 8, using bars 2263479/2263504; row 8685 is TBLA `NONE` with no event/bars. Both stored the correct cutoff/calendar but NULL context. The batch handler constructed `CeriFeatureRebuildRequest` without context/calendar, `_price_response_row` built the feature from that truncated request, and `persist` could update an existing context-free event row without copying lineage. Context was dropped at request construction and then preserved as NULL by the retry/update path.

## J. CERI lineage remediation

Pipeline handlers now require all four frozen fields and mark ownership `PIPELINE`; standalone callers explicitly use `STANDALONE`. Request, batch context, feature builders, hashes, preload keys and PostgreSQL upserts all bind ownership/context. Pipeline price event keys include context. Retry copies all non-administrative fields.

Migration 0072 leaves historic data nullable and labelled `LEGACY_UNKNOWN`; it adds conditional checks requiring context, cutoff and calendar for `PIPELINE`, plus an enum check. A two-session real DRS rebuild on the clean clone created 24 revision, 3 derived, 1 price-response and 1 build-state row under Context 6, then skipped unchanged work on retry while preserving all IDs. Every pipeline-null count was zero; null-PIPELINE and unknown-ownership inserts were rejected by PostgreSQL.

## K. Price-bar field classification

| Field | Classification | Reason |
|---|---|---|
| `ticker`, `bar_date`, `timeframe` | IMMUTABLE_MARKET_EVIDENCE | natural series/session identity |
| `open`, `high`, `low`, `close`, `volume` | IMMUTABLE_MARKET_EVIDENCE | market values |
| `source`, `what_to_show`, `adjustment_type` | IMMUTABLE_MARKET_EVIDENCE | provider/basis identity |
| `created_at`, `first_seen_at` | IMMUTABLE_PIT_PROVENANCE | first-known boundary |
| `revised_at`, `revision_count`, `data_hash` | IMMUTABLE_PIT_PROVENANCE | current-version/revision identity |
| `last_seen_at` | MUTABLE_OPERATIONAL_METADATA | identical provider re-observation timestamp |
| `id` | ADMINISTRATIVE_METADATA | database surrogate; manifests bind it separately where lineage requires it |

`PriceBar` has no `ingested_at`, `updated_at`, source-batch ID or source-version column. No schema field is unresolved and no derived-cache metadata exists on this model.

## L. last_seen_at semantics

The IB persistence path advances `last_seen_at` when an identical natural-key/data-hash bar is observed again. It does not change OHLCV, source/basis, revision count, data hash or first-known time; it is not a revision selector and is not used for PIT eligibility. It is freshness/provider bookkeeping. Therefore it is operational, but remains present in the full-row diagnostic and in mutation reporting.

## M. Run 153 protected-bar reclassification

For both bars all fields except `last_seen_at` were byte-equivalent between the verified backup clone and production.

| Bar/field | Before UTC | After UTC | Classification | Authorized mutation? |
|---|---|---|---|---:|
| 2263479 `last_seen_at` | 2026-09-09 09:31:04.697382Z | 2026-09-10 09:31:54.083450Z | MUTABLE_OPERATIONAL_METADATA | YES, normal identical re-observation |
| 2263504 `last_seen_at` | 2026-09-09 09:31:07.168191Z | 2026-09-10 09:31:56.949094Z | MUTABLE_OPERATIONAL_METADATA | YES, normal identical re-observation |

Immutable hashes remained `c9049dcc…` and `36127195…`; full-row diagnostic hashes changed. Both conclusions are `EXPECTED_OPERATIONAL_METADATA_CHANGE`. Production was not repaired.

## N. Historical hash contract

`price-bar-immutable-evidence-v1` hashes market values, natural identity, provider/basis, first-known/created timestamps and all revision/version markers. It excludes only `id` and `last_seen_at`. Actual value, source, first-known or revision changes alter it. `price-bar-full-row-diagnostic-v1` retains every column and is never the historical PASS/FAIL gate. Operational changes are surfaced through that diagnostic.

## O. Adjacent lineage/hash audit

| Area | Classification | Result |
|---|---|---|
| Technical scores/artifacts | LINEAGE_COMPLETE | explicit context/cutoff/session/calendar; technical input signature and source-session lineage |
| Lifecycle snapshot/pointer/ledger/events | LINEAGE_COMPLETE | snapshot context; immutable selection keys; append-only selection ledger |
| Signal alerts/change rows | LINEAGE_COMPLETE | snapshot/event lineage |
| Winner decision-time/outcome artifacts | LINEAGE_COMPLETE | decision cutoff and source-bar lineage hashes |
| IBMI features | CONTEXT_OPTIONAL_BY_DESIGN | independent source-known/session provenance; no Run 153 live rows |
| Market/sector derived rows | LINEAGE_COMPLETE | explicit calculation context in pipeline repositories |
| Setup/CERI price loaders | formerly MISSING_CONTEXT_BLOCKING | fixed through shared revision projection |

The two adjacent full-row price gates in Winner maturation and candidate-estimate scripts were changed to immutable PASS/FAIL plus full-row diagnostic reporting. Exact restore/backup table hashes remain full-row by design because their purpose is physical restoration equality, not semantic historical integrity.

## P. Migration

Migration required: YES. New head: `0072_ceri_artifact_context_lineage`, directly after 0071; one Alembic head. It is additive first, creates no fabricated historical context, and was upgraded, downgraded and upgraded again on the disposable clone. It was not applied to production.

## Q. Test matrix

| Layer | Result |
|---|---|
| Exact Run 153 TBLA read-only reconstruction | PASS; historical technical-manifest divergence reproduced, fixed loader key Aug 3 |
| Manifest unit contract | PASS: canonical order/timezone, re-observation stability, material revision/pointer mismatch |
| 138-candidate production-path clone replay | PASS: 138 equal, 0 divergent; injected mismatch/no mutation PASS |
| CERI real-service clone replay/retry | PASS: all four feature artifact families context-bound; DB rejection PASS |
| Migration 0072 clone down/up | PASS |
| Focused fixture regressions | 50 passed |
| Broad subsystem regression | 732 passed after four expected fixture-contract updates; those four reran 41 passed |
| Full non-slow | PASS: 2,208 passed, 130 skipped, 58 deselected, 0 failed |
| Post-collection focused rerun | PASS: 71 passed, 0 failed (final added hash-gate test rerun separately) |
| Final projection/ownership hardening rerun | PASS: 32 passed, 0 failed |
| Ruff / format / compileall / Alembic one-head | PASS after final formatting; code head 0072 |

The disposable PostgreSQL pytest fixture skipped three tests because its configured admin login was unavailable. Equivalent migration, constraint, hash-roundtrip and retry assertions were executed against the guarded restored clone instead; this is an environment skip, not a test failure.

## R. Production safety

```text
Runs 148–153 changed: NO
CERI snapshots 10929/10930 changed: NO
CERI rows 8684/8685 changed: NO
bars 2263479/2263504 repaired: NO
production business writes: NO
production pipeline: NO
live canary: NO
historical repair: NO
merge: NO
```

All production SQL used read-only transactions. Migration/replay writes targeted only the exact guarded disposable database `swinglens_pytest_run153_remediation_clone`.

## S. Residual risks

- Historical CERI feature rows retain `LEGACY_UNKNOWN`; this is deliberate non-fabrication, not certified reconstructed lineage.
- The next live canary must first deploy code and migration 0072 through the normal reviewed process. It must use a fresh preflight/context; Plan 2 and Context 6 are historical evidence and must not be reused.
- Full-row diagnostics will still report benign operational changes; reviewers must use the explicit immutable result for PASS/FAIL and inspect diagnostics separately.
- No production deployment or canary behavior is inferred from clone success beyond readiness for the separately authorized final canary.

## T. Final recommendation

After review and normal deployment of this branch plus migration 0072, authorize exactly one final live dual-mode canary using a fresh immutable plan. Require one same-session replacement and one new-session initialization under one context, verify the decision-manifest gate, check CERI child ownership/context, and report both immutable and full-row price hashes. Do not repair Run 153 historical rows and do not reuse its plan/context.
