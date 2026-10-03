# CERI source-authority P0 remediation report

## EXECUTIVE VERDICT

- P0 SOURCE-AUTHORITY HOT PATH: PASS
- FULL CLEAN-BUNDLE PER-TICKER SCANS REMOVED: PASS
- DIRTY-ROW VALIDATION: PASS
- SOURCE-TABLE INVALIDATION PRECISION: PASS
- UNKNOWN MUTATION FAIL-CLOSED: PASS
- RETRY PARAMETER LIMIT FIXED: PASS
- >65,535 POSTGRESQL RETRY CERTIFIED: PASS
- SOURCE MANIFEST EQUIVALENCE: PASS
- FEATURE OUTPUT EQUIVALENCE: PASS
- PIT CORRECTNESS: PASS
- MUTATION AUTHORITY PRESERVED: PASS
- TARGETED TESTS: PASS
- POSTGRESQL INTEGRATION: PASS
- CONTROLLED BENCHMARK: PASS
- REAL PRODUCTION PIPELINE: PASS
- REAL CERI CERTIFIED COMPLETION: PASS
- RUN 11 MODIFIED: MUST BE NO

## Baseline

- Branch: `codex/ceri-authority-remediation`
- Starting/current Git SHA: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`
- Schema head: `0089_pipeline_execution_authority`
- Python: `3.12.2`
- PostgreSQL: `18.3`, Windows x86-64
- Worker topology: one durable synchronous worker, `local-worker-1`
- Effective CERI configuration: batched workflow enabled; provider batch 25; normalization batch 50; feature batch 50; checkpoint interval 5.
- The starting tree already contained the Run 11 forensic telemetry/report work. It was retained and extended.
- Run 11 immutable manifests remain job 287 = 83,167 rows / `8fc36165889c1a663712192565fef5594b5b50814db99f4ef472e447fbee4eba`; job 288 = 72,077 rows / `445537fff9ae40f73ca283276668b3acbdc57d307c7e9e2efbc6cd8c51c754aa`.
- Historical Run 11: CERI 13,065.931 s; feature wall 11,404.773 s; batch A 6,219.333 s; batch B 5,185.440 s; 21,989,214 projected in-memory row checks; 94 executed full refreshes; only 5.780 s of named feature computation.

## Root cause confirmation

The current tree still had three full retained-bundle assertions per ticker: the outer source scope exit, inner source scope exit, and explicit rebuild assertion. Each reconstructed protected-column dictionaries and canonical fingerprints for every row. For 94 tickers this was 21,989,214 redundant retained-row checks.

The exact refresh invalidator was also proved, rather than inferred. Every one of the 94 ticker paths called `CeriProcessingRunService.create_or_get`, which called `session_database_schema_revision()`. That helper executed `text("select version_num from alembic_version")`. The engine listener correctly treated the unstructured `TextClause` as unknown/fail-closed and set the bundle to revalidate. Historical SQL recording showed exactly 94 such groups, matching 50 + 44 full refreshes. The statement is a read-only schema-control query, but its raw expression type made that fact structurally unknowable to the listener.

The separate retry defect was present: the expected-manifest load path emitted all retained primary keys in one `IN` expression. Run 11 job 287 has 75,806 `price_bars`, beyond the driver's 65,535-parameter limit.

## Implementation

- `app/services/source_mutation_authority.py`
  - Retains exact ORM-instance-to-source-address membership.
  - Normal assertions intersect `Session.dirty` / `Session.deleted` with retained instances and fingerprint only relevant candidates. A clean assertion inspects zero rows.
  - A single full audit remains immediately before the batch handler's durable publication boundary. It detects in-place mutable-body changes that need not appear in `Session.dirty`.
  - SQL invalidation classifies structural SELECTs as safe, unrelated-table DML as safe, retained-table DML as invalidating, existing permitted supporting-column updates as safe, and raw/ambiguous SQL as fail-closed.
  - Transaction, SQL transaction, and connection replacement remain fail-closed.
  - Expected-manifest loads use the existing 50,000-parameter budget, deterministic address ordering, the same Session/transaction and `FOR UPDATE`, exact membership checks before and after loading, and duplicate/missing/unexpected detection.
  - Trusted internal comparisons now read private retained bodies directly; the public body view still returns defensive deep copies.
  - Adds assertion, bounded-audit, invalidation-cause, refresh, and writer telemetry.
- `app/services/ceri/deployment_identity.py`
  - Replaces the raw Alembic query with a structurally classifiable SQLAlchemy `Select`. It is now provably read-only without weakening unknown-SQL behavior.
- `app/services/ceri/batched_job_handlers.py`
  - Performs exactly one full in-memory audit and one refresh at the durable end-of-batch boundary.
  - Persists batch wall/CPU, ticker phase, SQL, source-integrity, and writer counters.
- Tests add clean/dirty/unrelated/in-place mutation coverage, structural SQL classification, exact retry membership, a 75,000-row PostgreSQL retry, batch-boundary audit counts, and current-context parity fixtures.
- `scripts/profile_ceri_source_bundle.py` executes the real `prepare_batch`/expected-manifest production path rollback-only against Run 11 evidence.

The invariant is unchanged: calculations consume only exact admitted source identities and bodies, source-table/unknown mutation forces exact revalidation, retained ORM mutation is detected, retry identity is exact, and publication crosses a bounded full audit.

## Before/after source-authority benchmark

Rollback-only production-path measurements against the exact Run 11 manifests:

| Measurement | Before | After | Improvement |
|---|---:|---:|---:|
| clean assert wall, job 287 | 16.781831 s | 0.0000419 s | 400,521x |
| clean assert wall, job 288 | 13.375999 s | 0.0000391 s | 342,097x |
| clean assert rows | 83,167 / 72,077 | 0 / 0 | O(bundle) -> O(changed rows) |
| no-op refresh wall | historically executed | 0.0000102 / 0.0000105 s | constant-time |
| forced refresh wall | 21.619 / 19.053 s | 16.484 / 13.900 s | 23.8% / 27.0% faster when genuinely required |
| forced refresh SQL | 2.603 / 2.506 s | 2.900 / 2.251 s | comparable exact SQL work |
| forced refresh validation | 18.426 / 15.984 s | 13.030 / 11.152 s | 29.3% / 30.2% faster |
| executed refreshes/ticker, clean production | 1 | 0 | eliminated |
| assertion rows/ticker | 249,501 / 216,231 | 0; one audit per batch | storm eliminated |

The actual dirty-row probes inspected one row in 0.302/0.298 ms and detected both mutations. Full bounded audits took 14.338 s for 83,167 rows and 19.880 s for 72,077 rows, once per batch rather than three times per ticker. Durable manifest generation took 6.168/6.368 s. Exact retry reconstruction took 19.526/23.283 s.

## Retry certification

- Production benchmark: 83,167 and 72,077 retained rows reconstructed with fingerprints exactly equal to the immutable Run 11 manifests.
- Largest table: 75,806 `price_bars` in job 287.
- Budget: 50,000 bind parameters, using `source_refresh_identity_chunk_size` and `SOURCE_REFRESH_QUERY_PARAMETER_BUDGET`.
- With one ambient selector bind, the single-key identity chunk is 49,999; job 287 therefore uses two deterministic price-bar chunks.
- PostgreSQL regression: 75,000 identities loaded through the production expected-manifest branch in two chunks; source count, addresses, bodies, and bundle fingerprint were identical. Observed parameter counts were 50,000 and 25,002 (ambient predicate included), never above budget.
- The same transaction and `FOR UPDATE` semantics are retained. Composite-key chunk sizing remains covered.

## Real production pipeline benchmark

Run 13 was created from the same user upload/cohort as Run 11 through the normal upload and pipeline APIs. It used real PostgreSQL, the normal queue, worker, provider, normalization, manifest, feature, finalizer, capture, change, alert, and certification paths. No database row or evidence was repaired manually.

- Run/pipeline: 13/13; root job: 326; correlation: `root-0f58a5cd724a48e6af09c42b506afd08`
- Git SHA/schema: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae` / `0089_pipeline_execution_authority`
- Runtime instance/config fingerprints: `775d157dd87449ec8fb5dd0956e97023` / `8ba2f7a572d30937dd99bbf64ea9d88c9d171a38972d97cda404ac3670b979e7`
- Worker: PID 18268, instance `512814a47d624a208d2fd647c22e3fb9`, generation 26
- Pipeline start: `2026-10-02T22:05:07.105475+02:00`
- CERI start/end: `2026-10-02T22:14:25.561616+02:00` / `2026-10-02T23:37:14.044720+02:00`
- Durable result: step COMPLETED with “Required CERI child workflow completed and certified”; pipeline result `ceri_completion_state=CERTIFIED`.

| Measurement | Run 11 | Run 13 production | Change |
|---|---:|---:|---:|
| CERI total wall | 13,065.931 s | 4,968.483 s | -61.97%; 2.630x |
| provider/normalize | 1,461.305 s | 1,711.907 s | +17.15% live-provider variance |
| feature wall | 11,404.773 s | 3,091.732 s | -72.89%; 3.689x |
| feature batch A | 6,219.333 s | 1,883.576 s | -69.71%; 3.302x |
| feature batch B | 5,185.440 s | 1,207.906 s | -76.71%; 4.293x |
| feature SQL | 154.640 s | 14.679 s | -90.51% |
| assertion rows | ~21.99M projected | 0 clean; 155,720 in 2 bounded audits | storm removed |
| executed full refreshes | 94 | 0 | eliminated |
| source refresh rows | 7,329,738 projected | 0 | eliminated |
| named feature compute | 5.780 s | 5.226 s | comparable; live inputs differ |

Post-feature finalizer/capture/change/alert wall was 164.843 s. The two feature batches retained 83,406 and 72,314 rows, only 0.31% more total than Run 11. Both retained the same 75,806/65,854 price-bar counts; provider-derived rows account for the small difference. Run 13 therefore has directly comparable scale. Its feature cost fell from 121.33 to 32.89 seconds/ticker. Bounded audit work amortizes to 1,656.6 rows/ticker versus 233,927.8 historical assertion rows/ticker.

Run 13 manifests are immutable: batch 351 = 83,406 rows / `29dab588a5970a9b933365fb5f6c88a7a0e0de7a824bf15d05311ccc80982455`; batch 352 = 72,314 rows / `d4c6cf6824be933fc18063001f568bdf53131c859e894335558869cca07a38c7`. They are not expected to equal Run 11 fingerprints because live provider evidence changed; exact equality is proved separately by rollback retry reconstruction using Run 11's frozen inputs.

## New source-authority telemetry

| Counter | Batch 351 | Batch 352 |
|---|---:|---:|
| ticker/source rows | 50 / 83,406 | 44 / 72,314 |
| wall / CPU | 1,883.576 / 1,732.890 s | 1,207.906 / 1,175.265 s |
| SQL time / count | 8.583 s / 2,853 | 6.096 s / 2,544 |
| persistence wrapper | 456.000 s | 283.534 s |
| named feature compute | 3.119 s | 2.107 s |
| assert calls / rows / ms | 150 / 0 / 2.080 | 132 / 0 / 1.964 |
| clean no-op / dirty rows | 150 / 0 | 132 / 0 |
| full audits / rows / ms | 1 / 83,406 / 18,509.967 | 1 / 72,314 / 14,733.901 |
| refresh calls / no-op | 7,595,951 / 7,595,951 | 5,808,199 / 5,808,199 |
| executed refresh / rows | 0 / 0 | 0 / 0 |
| refresh SQL / validation ms | 0 / 0 | 0 / 0 |
| invalidations / causes | 0 / `{}` | 0 / `{}` |
| writer calls | 100 | 88 |
| writer manifest ms | 1,764,865.206 | 1,125,089.847 |
| writer fence ms | 12.539 | 10.684 |
| writer body ms | 463,223.320 | 288,491.056 |

Refresh-call counts are intentionally transparent: `_source_value` performs millions of constant-time authority checks. They consumed 49.121 s in total but executed no SQL. Writer timers are inclusive/nested and not additive to batch wall. They clearly expose repeated writer-manifest canonicalization as the next dominant area; source validation is no longer dominant.

## Test evidence

- Authority + focused CERI unit gate: `72 passed`.
- Earlier direct authority gate during development: `35 passed`.
- PostgreSQL source-authority suite: `15 passed in 185.60s`.
- Standalone >65,535-bind PostgreSQL regression: `1 passed in 74.08s`.
- Systemic source-purity/retry/determinism regressions: `3 passed`.
- PostgreSQL legacy-vs-batched semantic/evidence parity: `1 passed in 30.83s`.
- Focused CERI workflow unit group during development: `58 passed`.
- Ruff over all modified Python files: PASS.
- `git diff --check`: PASS (only Git CRLF conversion notices).

The parity test compares normalized source records, revisions, feature payloads and feature evidence hashes, score payloads and evidence hashes, change events, and alerts. It passed after its stale fixture was updated to model the current post-acquisition context/workflow identity on both sides. No feature formula changed.

## Run 11 preservation

A final read-only query reconfirmed every supplied Run 11 step status and timestamp, the lifecycle error, both CERI manifest counts, and both fingerprints. Run 11 remains FAILED only at the known downstream `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH`; its CERI state remains CERTIFIED. No Run 11 recovery, resume, status update, manifest update, or data mutation occurred.

## Remaining risks

- A pre-existing standalone feature-certification workflow test still assumes the run-start context while current feature authority requires the post-acquisition CERI context. The normal production CERI path is proven by Run 13, and changing that separate admission/business workflow is expressly outside this task.
- Run 13 used the supported `ALLOW_CACHE_FALLBACK` market-data policy because IB Gateway was running but its API was not ready. CERI providers, normalization, manifests, and all CERI processing were live/normal. The initial fail-closed `REQUIRE_IB` attempt did not create or repair CERI evidence.
- Run 13 continued beyond certified CERI into Setup processing. Any later known lifecycle failure is a separate downstream result and not a CERI failure.
- Millions of constant-time refresh guards and writer-manifest canonicalization remain measurable. They do not weaken correctness and were not expanded into a broader redesign here.

## Next recommendation

The system is ready for **P1 persistence-wrapper investigation**. Concentrate on repeated/inclusive writer-manifest canonicalization and the still-large persistence wrapper time while preserving its declaration/fence semantics. After P1 is independently certified, plan **P2 locking/concurrency redesign** around the shared benchmark rows and long `FOR UPDATE` transaction lifetime. Do not add workers or parallel batches before that locking design is proven safe.
