# CERI P1 writer-manifest / persistence-wrapper forensic certification

## EXECUTIVE VERDICT

- P1 WRITER-MANIFEST BOTTLENECK ROOT CAUSE: CONFIRMED
- EXCLUSIVE WRITER COST MEASURED: PASS
- REDUNDANT CANONICALIZATION PROVED: PASS
- REDUNDANT CANONICALIZATION REMOVED: PASS
- WRITER DIGEST EQUIVALENCE: PASS
- NATIVE-SOURCE MANIFEST EQUIVALENCE: PASS
- MUTATION DECLARATION PRESERVED: PASS
- MUTATION FENCE PRESERVED: PASS
- P0 SOURCE-AUTHORITY INVARIANTS PRESERVED: PASS
- PIT CORRECTNESS: PASS
- FEATURE EVIDENCE EQUIVALENCE: PASS
- RETRY DETERMINISM: PASS
- POSTGRESQL TESTS: PASS
- CONTROLLED P1 BENCHMARK: PASS
- REAL PRODUCTION PIPELINE: PASS (normal production path through the required CERI boundary; a later unrelated parent continuation failed as described below)
- REAL CERI CERTIFIED COMPLETION: PASS
- RUN 11 MODIFIED: NO
- RUN 13 MODIFIED: NO
- SAFE FOR P2 LOCKING/CONCURRENCY DESIGN: YES

P1 succeeds. The dominant cost was not PostgreSQL, feature mathematics, mutation fencing, or the P0 source refresh architecture. It was repeated reconstruction and recursive canonicalization of the same sealed retained-source rows by two nested writer declarations for every ticker. The optimization reuses exact immutable row-state and canonical subtrees only inside the owning sealed source bundle and transaction authority. It leaves the native manifest bytes, SHA-256 evidence digest, independent outer/inner declarations, independent fences, source authority, PIT identity, retry behavior, and feature evidence unchanged.

The frozen 94-ticker Run 13 workload improved from 2,957.819 s to 628.691 s across both rollback-only batches (4.70x). The new production run's feature phase improved from 3,091.732 s to 740.441 s (76.05%), and the complete CERI child workflow improved from 4,968.483 s to 2,465.386 s (50.38%) despite live-provider work remaining outside this P1 optimization.

## 1. Baseline and forensic preservation

P1 began from the current, P0-certified dirty tree. The P0 work was not committed and was deliberately preserved rather than reset or rewritten.

| Baseline property | Recorded value |
|---|---|
| Branch | `codex/ceri-authority-remediation` |
| HEAD | `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae` |
| Working tree | P0 changes present and uncommitted; P1 layered on top |
| Schema head | `0089_pipeline_execution_authority` |
| Python | 3.12.2 |
| PostgreSQL | 18.3, Windows x86-64 |
| Durable worker topology | one logical `local-worker-1`, queues `interactive,broker,background` |
| Provider batch | 25 |
| Normalization batch | 50 |
| Feature batch | 50 |
| Batch checkpoint interval | 5 |

The final read-only audit still showed the original immutable rows:

| Run | Pipeline status | CERI state | Feature manifests | Source rows | Manifest fingerprints |
|---:|---|---|---:|---:|---|
| 11 | FAILED at Setup Lifecycle | CERTIFIED | 2 | 155,244 | `8fc361...e4eba`, `445537...54aa` |
| 13 | FAILED at Setup Lifecycle | CERTIFIED | 2 | 155,720 | `29dab5...2455`, `d4c6cf...8c7` |

No recovery, resume, status write, manifest write, or artifact rewrite was issued against Run 11 or Run 13. Their failure remains `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH`; that lifecycle issue is outside P1.

## 2. Exact root cause

For each feature ticker, `CeriFeatureRebuildService._rebuild_company` is a depth-1 `source_mutation_writer`, and its body calls the separately decorated `_persist_company` depth-2 writer. Both declarations receive large, substantially overlapping semantic inputs rooted in the same batch context. That context contains the sealed source bundle, including roughly 75,000 price bars plus provider-derived rows.

Before P1, each writer did all of the following independently:

1. bind all arguments;
2. recursively traverse the large semantic graph through `_source_value`;
3. inspect retained ORM mapper columns;
4. look up the retained body;
5. fingerprint retained row state repeatedly;
6. reconstruct identical row dictionaries;
7. recursively canonicalize the resulting native-source tree;
8. serialize tens of megabytes of canonical JSON and hash it;
9. create and validate the declaration, fence, and only then run the writer body.

On one representative Run 13 ticker (`A`, 83,406 retained rows), the unprofiled reference path took 36.689 s wall / 34.828 s CPU. Writer manifest work consumed 35.549 s, comprising 24.978 s native-source construction and 10.571 s fingerprint serialization. The actual exclusive writer bodies consumed only 0.185 s. The path made 151,900 retained-row fingerprint calls, 151,899 source-body lookups, and 151,901 refresh guards for two writer calls.

The bounded cProfile run made 110.4 million function calls. Its dominant work was:

| Function | Calls | Self s | Cumulative s | Meaning |
|---|---:|---:|---:|---|
| `CanonicalEvidenceSerializer.canonicalize` | 6,890,641 / 151,951 primitive | 32.116 | 63.992 | recursive canonical tree construction |
| `_source_value_impl` | 155,252 / 10 primitive | 4.899 | 57.820 | semantic graph and ORM conversion |
| `_mapping_path` | 6,402,518 | 4.565 | 6.670 | canonical traversal paths |
| `datetime.isoformat` | 916,123 | 4.225 | 4.225 | repeated datetime normalization |
| SQLAlchemy `inspect` | 310,528 | 1.061 | 2.735 | repeated ORM inspection |
| `PrefetchedSourceBodies.refresh` | 151,905 | 1.258 | 2.445 | constant-time guard repeated per row |
| `Canonical.fingerprint` | 151,963 | 0.373 | 38.469 | hashing after repeated serialization |

This proves a CPU-bound canonicalization problem: the one-ticker SQL time was small, and the body/fence/declaration times were negligible compared with native conversion and hashing.

### Duplicate work proved

For controlled batch 351, the old path visited 7,595,642 retained ORM values across 100 writer calls and did 7,598,482 retained fingerprints. The same immutable rows were converted in the outer writer and then converted again in the nested writer. Batch 352 repeated the same pattern: 5,807,926 retained visits and 5,810,272 retained fingerprints across 88 calls.

Across both frozen batches, 13,248,047 retained-row visits became exact cache hits after P1, while only 155,521 unique/first-use conversions were needed. This is the structural proof: output size and values visited remain essentially unchanged because evidence format is preserved, but duplicate conversion, row fingerprinting, and recursive canonicalization are removed.

## 3. Writer call tree and timing semantics

The actual call tree for every rebuilt ticker is:

```text
_rebuild_company writer (depth 1; no parent)
  manifest/native-source/digest/declaration/fence
  body (inclusive)
    feature calculation and orchestration
    _persist_company writer (depth 2; parent = _rebuild_company)
      manifest/native-source/digest/declaration/fence
      body (exclusive persistence SQL)
```

There are exactly two writer owners. Run 13 and the new production run each invoked the outer and inner writer once per ticker: 94 outer + 94 inner = 188 calls.

The old telemetry interpretation needs a precise correction:

- outer **wrapper inclusive** time contains the inner wrapper;
- outer **body inclusive** time also contains the inner wrapper;
- inner body is therefore double-counted if old body and outer-inclusive numbers are added mechanically;
- each writer's manifest phase occurs before its body, so outer and inner manifest phases are sequential; their sum is not the nested-body overlap;
- P1 now records wrapper/body wall and CPU as both inclusive and exclusive, and records manifest/native/fingerprint phases directly.

Therefore the supplied Run 13 `writer manifest = 2,889.955 s` and `writer body = 751.714 s` must not be added. The body includes nested writer execution. The trustworthy controlled reference measured 2,895.841 s exclusive wrapper time and 2,882.109 s manifest time for the two frozen batches.

### Required writer-owner table (same frozen batch 351 before/after)

| Writer owner | Calls | Parent/child depth | Before exclusive ms | After exclusive ms | Native-source ms | Fingerprint ms | Body ms | SQL ms | Digest parity |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| `_rebuild_company` | 50 | depth 1; parent of `_persist_company` | 1,178,166.385 | 171,893.030 | 106,603.532 | 62,116.646 | 3,159.939 exclusive | 918.663 | PASS |
| `_persist_company` | 50 | depth 2; 50 nested calls | 463,698.617 | 159,155.408 | 95,254.777 | 61,079.442 | 2,809.285 exclusive | 1,740.007 | PASS |

The post-P1 owner numbers retain independent manifests, contexts, declarations, and fences. No nested writer was collapsed.

## 4. Canonicalization measurements

The first table is the bounded, exact one-ticker cProfile comparison. `Canonical fingerprint calls` for the optimized cProfile is marked unavailable because it fell outside the retained top-function export; the exact writer-specific retained fingerprint counter is shown separately rather than inferred.

| Metric | P0 baseline | P1 |
|---|---:|---:|
| Canonical fingerprint calls (whole bounded profile) | 151,963 | NOT AVAILABLE in retained top-function export |
| Canonicalize recursive calls | 6,890,641 | 2,247,365 |
| Retained ORM visits | 151,843 | 151,843 |
| Actual retained ORM conversions | 151,843 | 75,921 |
| Mapping conversions | 126 | 126 |
| Dataclass conversions | 104 | 104 |
| Canonical bytes produced | 78,988,946 | 78,988,946 in exact parity run |
| Duplicate immutable conversions | 75,922 | 0 |
| Reused immutable conversions | 0 | 75,922 |
| Writer retained-row fingerprint calls | 151,900 | 56 |
| Refresh guard calls (one ticker) | 151,901 | 2 |
| Refresh guard wall (one ticker unprofiled) | 969.725 ms | 0.052 ms |
| Refresh guard calls (Run 13 production baseline / P1 workload) | approximately 13.4M | 188 structurally; 188 measured in controlled benchmark |
| Refresh guard wall | 49.121 s in Run 13 | production durable field redacted; controlled exact 0.004072 s |

Across both production-sized controlled batches:

| Metric | Reference | Optimized | Structural result |
|---|---:|---:|---|
| Values visited | 13,702,354 | 13,702,354 | evidence shape retained |
| Canonical bytes produced | 6,986,905,449 | 6,986,905,443 | same structure; six-byte run-context variance across separate executions |
| Retained ORM visits | 13,403,568 | 13,403,568 | same semantic inputs |
| Cache/canonical subtree hits | 0 | 13,248,047 | duplicate work reused |
| Cache/canonical subtree misses | 0/not applicable | 155,521 | first-use exact row states |
| Source-body lookups | 13,408,660 | 160,613 | 98.80% reduction |
| Retained-row fingerprints | 13,408,754 | 5,092 | 99.96% reduction |
| Refresh guards | 13,409,036 | 188 | one per writer invocation |
| Refresh guard wall | 91.212 s | 0.004 s | constant-time guard retained, repetition removed |

The separate six-byte aggregate difference came from independently created diagnostic execution context, not an evidence change. Exact same-input comparison in a single invocation produced byte-for-byte canonical equality and identical digests for both writers.

## 5. Implementation design

The optimization is deliberately scoped to the existing authority object.

### Exact retained-row state cache

`PrefetchedSourceBodies` now owns two private per-bundle caches:

- the exact normal typed row-state dictionary already admitted by the sealed bundle;
- its exact canonical subtree.

Reuse is allowed only when the object is the retained ORM instance in the sealed bundle, its row is neither dirty nor deleted in the owning session, and transaction/connection/SQL-transaction witnesses still match. The bundle retains strong references to its rows, so identity keys cannot be recycled while the cache is live.

### Invalidation and fail-closed rules

Both caches are cleared whenever `_require_revalidation` is entered. That happens for relevant source-table DML, unknown SQL, transaction change, SQL transaction change, or connection change. Dirty/deleted retained rows bypass reuse and remain subject to existing mutation detection. Different PIT projections do not use the retained-ORM cache, so the same physical price-bar row under a different projection cannot inherit the wrong semantic representation.

### Canonical subtree substitution

`CanonicalEvidenceSerializer.bytes(..., precomputed=...)` substitutes only an already canonicalized subtree for the exact in-memory object identity supplied by the sealed bundle. All surrounding mappings, list ordering, reason-code ordering, timestamps, key collision checks, final JSON serialization, and SHA-256 hashing remain unchanged. No digest-only evidence, evidence version, schema, source identity, or lossy form was introduced.

### Bounded authority guard

The writer now performs one bundle `refresh()` guard and one dirty/deleted identity snapshot per invocation. `_source_value` consumes that proven snapshot rather than invoking the same no-op guard millions of times. Actual refresh and fail-closed behavior are unchanged.

### Low-risk immutable metadata cache

SQLAlchemy mapper-column metadata is cached by mapped type. Row values and mutation state are never cached there.

### Precise telemetry

Context-local call frames now record owner, parent, depth, ticker, semantic argument count, wall/CPU inclusive and exclusive times, native conversion, fingerprint, declaration, fence, body, SQL, flush, `_source_value` types, values visited, bytes, cache hits/misses, row fingerprints, lookups, and top invocations. SQL cursor listeners and ORM flush listeners attribute actual writer SQL without high-cardinality Prometheus labels.

Diagnostic contexts provide a reference path and a compare-both-paths mode. The compare mode fails closed with `MUTATION_SOURCE_WRITER_MANIFEST_EQUIVALENCE_FAILED` if manifest, canonical bytes, or digest differ.

## 6. Exact files changed

P1 implementation and evidence files:

| File | P1 purpose |
|---|---|
| `app/services/canonical_evidence.py` | exact precomputed canonical-subtree support |
| `app/services/source_mutation_authority.py` | scoped reuse, invalidation, mapper metadata cache, call-tree/SQL/flush telemetry, reference comparison |
| `app/services/ceri/batched_job_handlers.py` | batch telemetry publication and one bounded audit integration |
| `tests/test_canonical_evidence_serializer.py` | byte-equivalent substitution and mutation-outside-cache tests |
| `tests/test_source_mutation_authority.py` | authority and bounded-work regression coverage |
| `tests/integration/test_t14d_source_bundle_postgresql.py` | real PostgreSQL reuse, byte/digest equality, invalidation, and 75k retry coverage |
| `scripts/profile_ceri_writer_manifest.py` | rollback-only Run 13 replay/profiler/reference comparison |
| `artifacts/forensics/run13_p1_writer_*.json` | bounded profiles, development stages, exact digest parity |
| `artifacts/forensics/run13_p1_controlled_{before,after}_job{351,352}.json` | frozen full-batch benchmarks |
| `artifacts/forensics/ceri_writer_manifest_p1_report.md` | this certification report |

The dirty tree also contains inherited P0 files and artifacts, including `deployment_identity.py`, P0 workflow tests, `profile_ceri_source_bundle.py`, and the P0 report. They were not reverted. The current aggregate diff therefore includes both P0 and P1 and must not be mistaken for a P1-only patch.

## 7. Digest, manifest, output, and evidence proof

The exact compare-path profiler replayed the real Run 13 job 351 source manifest (83,406 rows), real PostgreSQL objects, real writers, and real upserts inside a transaction that always rolled back.

For ticker `A`:

| Writer | Reference digest | Optimized digest | Manifest equal | Canonical bytes equal |
|---|---|---|---|---|
| `_rebuild_company` | `b2d37f1f64a0400818fc9fb56843231ffcfffd56fff52ee000932a7c21c3bcdb` | same | true | true |
| `_persist_company` | `2c17c80183accacea73c0a91b45c817bef7c65322909170dae78c1aaa45f2ed4` | same | true | true |

The full controlled before/after artifacts additionally prove:

- job 351: same bundle fingerprint, same 83,406 rows, same ticker ordering, and exact equality of all 50 `input_evidence_hash`, `output_evidence_hash`, and output feature counts;
- job 352: same bundle fingerprint, same 72,314 rows, same ticker ordering, and exact equality of all 44 evidence records;
- both executions used real PostgreSQL, real source manifests, real declaration/fence code, real writers/upserts, and ended with rollback;
- SQL count was unchanged for each workload (3,040 and 2,519);
- declaration and fence calls remained one per writer invocation.

The benchmark does not prove live provider inputs are stable; that is why exact equality is established on frozen Run 13 inputs, while production correctness is established through the normal certification barrier.

## 8. Controlled production-sized benchmark

### Batch 351: 50 tickers / 83,406 retained rows

| Measurement | Reference path | Optimized path | Change |
|---|---:|---:|---:|
| Wall | 1,676.982 s | 358.004 s | 4.684x faster |
| CPU | 1,632.172 s | 344.781 s | -78.88% |
| Writer calls / max depth | 100 / 2 | 100 / 2 | unchanged |
| Wrapper inclusive | 2,105.564 s | 490.204 s | -76.72% |
| Wrapper exclusive | 1,641.865 s | 331.048 s | -79.84% |
| Manifest | 1,634.006 s | 325.060 s | -80.11% |
| Native-source | 1,120.739 s | 201.858 s | -81.99% |
| Fingerprint | 513.261 s | 123.196 s | -75.99% |
| Body exclusive | 7.839 s | 5.969 s | -23.86% |
| Writer SQL | 2.833 s / 3,040 | 2.659 s / 3,040 | calls identical |
| Cache hits / misses | 0 / 0 | 7,512,340 / 83,302 | structural reuse |
| Retained fingerprints | 7,598,482 | 2,790 | -99.96% |
| Guards | 7,598,632 / 52.956 s | 100 / 0.001972 s | one per writer |
| Assertion rows | 0 | 0 | P0 invariant |
| Bounded audit | 1 / 83,406 | 1 / 83,406 | P0 invariant |
| Executed refresh / rows / invalidations | 0 / 0 / 0 | 0 / 0 / 0 | P0 invariant |

### Batch 352: 44 tickers / 72,314 retained rows

| Measurement | Reference path | Optimized path | Change |
|---|---:|---:|---:|
| Wall | 1,280.837 s | 270.687 s | 4.732x faster |
| CPU | 1,239.875 s | 263.188 s | -78.77% |
| Writer calls / max depth | 88 / 2 | 88 / 2 | unchanged |
| Wrapper inclusive | 1,597.477 s | 365.953 s | -77.09% |
| Wrapper exclusive | 1,253.976 s | 247.955 s | -80.23% |
| Manifest | 1,248.103 s | 242.898 s | -80.54% |
| Native-source | 858.415 s | 147.468 s | -82.82% |
| Fingerprint | 389.682 s | 95.425 s | -75.51% |
| Body exclusive | 5.856 s | 5.040 s | -13.93% |
| Writer SQL | 2.171 s / 2,519 | 2.184 s / 2,519 | calls identical |
| Cache hits / misses | 0 / 0 | 5,735,707 / 72,219 | structural reuse |
| Retained fingerprints | 5,810,272 | 2,302 | -99.96% |
| Guards | 5,810,404 / 38.255 s | 88 / 0.002100 s | one per writer |
| Assertion rows | 0 | 0 | P0 invariant |
| Bounded audit | 1 / 72,314 | 1 / 72,314 | P0 invariant |
| Executed refresh / rows / invalidations | 0 / 0 / 0 | 0 / 0 / 0 | P0 invariant |

Combined controlled wall was 2,957.819 s before and 628.691 s after: 4.705x faster. This was not a one-off production/provider comparison; it used the identical frozen inputs and preserved exact feature evidence.

## 9. New production certification: Run/Pipeline 14

Run 14 was created through the supported upload and pipeline HTTP APIs using the same 94-ticker CSV and `ALLOW_CACHE_FALLBACK` policy. Market data actually used live `IB_GATEWAY`: 192 planned/executed requests, all successful, zero cache. One durable worker ran the normal provider, normalization, source-manifest, feature, finalizer, capture, change, and alert sequence.

The CERI root correlation was `root-cf808d65b88642c4af7b33cab0c62ee8`; feature jobs were 384 and 385. All CERI jobs 360-389 completed. The CERI step completed at `2026-10-03T03:02:50.094568+02:00` with message `Required CERI child workflow completed and certified.` The pipeline's durable `result_json.ceri_completion_state` is `CERTIFIED`.

### Production scale and batch telemetry

| Measurement | Batch 384 | Batch 385 |
|---|---:|---:|
| Tickers | 50 | 44 |
| Retained source rows | 83,553 | 72,449 |
| Price bars | 75,908 | 65,944 |
| Source records | 3,598 | 3,017 |
| Estimate snapshots | 2,847 | 2,284 |
| Earnings actuals | 352 | 306 |
| Catalyst events / revisions | 399 / 399 | 427 / 427 |
| Guidance events | 0 | 0 |
| Job wall | 415.017 s | 325.038 s |
| Instrumented batch wall | 413.895 s | 324.018 s |
| CPU | 402.390 s | 315.937 s |
| CPU/wall | 97.220% | 97.506% |
| SQL wall / calls | 6.265 s / 2,853 | 5.186 s / 2,544 |
| Persistence phase | 158.937 s | 122.041 s |
| Writer wrapper inclusive | 482.951 s | 367.742 s |
| Writer wrapper exclusive | 324.579 s | 246.109 s |
| Writer manifest | 317.503 s | 240.555 s |
| Native-source | 194.007 s | 143.908 s |
| Fingerprint | 123.490 s | 96.642 s |
| Writer body inclusive | 165.419 s | 127.161 s |
| Writer body exclusive | 7.047 s | 5.529 s |
| Fence | 0.012522 s | 0.009608 s |
| Declaration/context | 0.004225 s | 0.003820 s |
| Writer SQL writes / wall | 250 / 1.282 s | 217 / 1.216 s |
| Writer-attributed flush | 0 / 0 s | 0 / 0 s |
| Global ORM flush count | 320 | 282 |
| Cache hits / misses | 7,522,447 / 83,413 | 5,743,546 / 72,318 |
| Retained row fingerprints | 0 | 0 |
| Canonical bytes | 3,955,749,075 | 3,030,371,243 |
| Values visited | 7,774,079 | 5,941,351 |
| Named feature compute | 2.521 s | 2.138 s |

Core upserts explain why writer-attributed ORM flush is zero; the global monitor still recorded all ORM flush events elsewhere in the job.

### Production writer owners

| Batch / owner | Calls | Nested | Depth | Incl ms | Excl ms | Manifest ms | Native ms | Fingerprint ms | Body excl ms | SQL ms | Cache hit/miss |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 384 `_rebuild_company` | 50 | 0 | 1 | 324,579.250 | 166,207.345 | 162,917.214 | 102,416.362 | 60,497.449 | 3,274.413 | 0 | 3,719,542 / 83,413 |
| 384 `_persist_company` | 50 | 50 | 2 | 158,371.905 | 158,371.905 | 154,585.519 | 91,590.291 | 62,992.372 | 3,772.902 | 1,281.669 | 3,802,905 / 0 |
| 385 `_rebuild_company` | 44 | 0 | 1 | 246,109.106 | 124,476.651 | 121,756.890 | 73,976.304 | 47,777.544 | 2,706.871 | 0 | 2,835,636 / 72,318 |
| 385 `_persist_company` | 44 | 44 | 2 | 121,632.454 | 121,632.454 | 118,798.321 | 69,931.561 | 48,864.134 | 2,821.956 | 1,216.198 | 2,907,910 / 0 |

### Production P0 authority telemetry

| Authority metric | Batch 384 | Batch 385 | Required result |
|---|---:|---:|---|
| Clean assertions | 150 | 132 | retained |
| Assertion rows | 0 | 0 | PASS |
| Dirty assertion rows | 0 | 0 | PASS |
| Bounded full audits | 1 | 1 | PASS |
| Audit rows | 83,553 | 72,449 | exact retained membership |
| Audit wall | 20.220 s | 19.998 s | measured |
| Refresh calls / no-ops | 309 / 309 | 273 / 273 | bounded guards/scopes |
| Executed refreshes | 0 | 0 | PASS |
| Refreshed rows | 0 | 0 | PASS |
| Refresh SQL / validation | 0 / 0 | 0 / 0 | PASS |
| Invalidations / causes | 0 / none | 0 / none | PASS |

The durable-result scrubber replaced telemetry fields whose names contained `authority_guard` with restricted markers. Consequently production guard milliseconds are not recoverable from the durable JSON and are reported as unavailable rather than invented. Structurally the code makes one writer guard per call (100 and 88), and the identical controlled workload measured exactly 100/88 calls totaling 4.072 ms.

### CERI correctness boundary

- immutable source manifests 25/26 exist with `ceri-feature-source-manifest-v1`, exact source counts 83,553/72,449, and bundle fingerprints `ef350d...fd1` / `d62243...ca4`;
- all 94 companies completed feature processing, with 2,546 new feature rows reported by the two batch jobs and no feature-job failures;
- finalizer job 386 completed;
- capture job 387 completed with 94 certified score snapshots, four change events, zero failed, zero stale, zero quarantined, and zero conflicted;
- change-detection job 388 completed with zero failures;
- alert-rebuild job 389 completed with `alerts_status=REBUILT` and zero failures;
- checkpointed processing records reported no failed processing rows;
- the normal completion barrier certified the CERI result.

After certification, normal orchestration created FULL_PIPELINE continuation job 390. It failed at the already out-of-scope decision handoff boundary with `HISTORICAL_EVIDENCE_UNAVAILABLE: resume artifact missing COHR:market_regime_snapshot`. Setup Lifecycle and Winner Prediction never started. This occurred after job 389 and after durable CERI certification; P1 did not modify or recover it.

## 10. Run 13 versus P1 production

Provider and normalization are live and not controlled by P1. The feature rows changed slightly (155,720 to 156,002, +0.18%), so per-ticker and feature-phase measurements are the primary comparison.

| Measurement | Run 13 P0 baseline | New P1 production | Change |
|---|---:|---:|---:|
| CERI total | 4,968.483 s | 2,465.386 s | -50.38% |
| provider/normalize | 1,711.907 s | 1,544.292 s | -9.79% live variance |
| feature wall | 3,091.732 s | 740.441 s | -76.05%; 4.176x faster |
| batch A wall | 1,883.576 s | 415.017 s | -77.97% |
| batch B wall | 1,207.906 s | 325.038 s | -73.09% |
| feature seconds/ticker | 32.89 s | 7.877 s | -76.05% |
| feature SQL | 14.679 s | 11.451 s | -21.99% |
| persistence wrapper | 739.534 s | 280.978 s | -62.01% |
| writer calls | 188 | 188 | unchanged |
| writer manifest inclusive | 2,889.955 s | 558.058 s | -80.69%; old semantics caveat above |
| writer manifest exclusive | NOT AVAILABLE | 558.058 s phase time | newly measured |
| writer wrapper exclusive | NOT AVAILABLE | 570.688 s | newly measured |
| writer body inclusive | 751.714 s | 292.581 s | -61.08%; both include inner wrapper |
| writer body exclusive | NOT AVAILABLE | 12.576 s | newly measured |
| writer fence | 0.023223 s | 0.022130 s | preserved, -4.71% noise |
| named feature compute | 5.226 s | 4.659 s | -10.85% |
| refresh guard time | 49.121 s | durable metric restricted; controlled 0.004072 s | structural reduction to one/call |
| clean assertion rows | 0 | 0 | PASS |
| bounded audit rows | 155,720 | 156,002 | exact new retained cohort |
| executed full refreshes | 0 | 0 | PASS |
| source refresh rows | 0 | 0 | PASS |

The new CERI internal phase split was 1,544.292 s provider/normalization, 740.441 s feature wall, and 180.653 s post-feature finalizer/capture/change/alert. Total is measured from first child job 360 start to terminal alert job 389 completion.

## 11. Three-generation history

| Measurement | Run 11 pre-P0 | Run 13 after P0 | New P1 |
|---|---:|---:|---:|
| CERI total | 13,065.931 s | 4,968.483 s | 2,465.386 s |
| feature wall | 11,404.773 s | 3,091.732 s | 740.441 s |
| batch A | 6,219.333 s | 1,883.576 s | 415.017 s |
| batch B | 5,185.440 s | 1,207.906 s | 325.038 s |
| feature SQL | 154.640 s | 14.679 s | 11.451 s |
| persistence wrapper | 1,097.159 s | 739.534 s | 280.978 s |
| named feature compute | 5.780 s | 5.226 s | 4.659 s |

Run 11 is historical context only. The P1 performance conclusion is based on frozen Run 13 replay and Run 13-to-Run 14 production comparison.

## 12. Tests and gates

The requested focused sequence was followed.

| Gate | Result |
|---|---|
| A: canonical/writer unit tests | 24 passed |
| B/C: real PostgreSQL source authority, invalidation, transaction/connection, retry | 18 passed in 214.98 s |
| C: >65,535 PostgreSQL retry identities | PASS with 75,000 price-bar identities |
| D/E: systemic, batched, semantic, nested ownership suites | 48 passed |
| D: legacy-versus-batched feature/evidence parity and cancellation | 40 passed |
| F: exact digest compare and two production-sized controlled batches | PASS |
| G: new real production pipeline through durable CERI certification | PASS |
| Static | Ruff PASS on all modified Python; `py_compile` PASS; `git diff --check` PASS except line-ending warnings |

Coverage explicitly exercises byte/digest equality, reuse, dirty retained rows, source DML invalidation, unknown SQL fail-closed behavior, transaction and connection replacement, PIT projections, same row under distinct semantic context, nested writers, independent fences/declarations, retry, cancellation, multi-ticker, multi-batch, and the parameter-limit retry fix.

A broader existing contract-adoption suite produced 46 passes and one unrelated pre-existing failure: `tests/test_t14b_writer_contract_adoption.py` expects a decorator on `WF_CERI_CONSENSUS_ATTACH`. That writer is outside this P1 path and was not changed to disguise the baseline failure.

## 13. Top 20 expensive invocations before and after

These are from the same controlled Run 13 batch 351, sorted by exclusive wrapper wall. Bytes remain large because the final evidence is intentionally unchanged.

### Before P1

| # | Owner | Ticker | Depth | Wrapper incl ms | Wrapper excl ms | Manifest ms | Native ms | Fingerprint ms | Body excl ms | Bytes |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `_rebuild_company` | FTNT | 1 | 50,666.382 | 37,548.504 | 37,474.296 | 28,292.700 | 9,181.543 | 74.037 | 39,524,426 |
| 2 | `_rebuild_company` | FTK | 1 | 42,877.468 | 30,856.673 | 30,746.159 | 23,113.715 | 7,631.964 | 110.284 | 39,472,790 |
| 3 | `_rebuild_company` | AAMI | 1 | 35,844.054 | 27,806.978 | 27,728.295 | 21,641.075 | 6,087.148 | 78.304 | 39,473,767 |
| 4 | `_rebuild_company` | BHE | 1 | 43,002.160 | 27,676.809 | 27,605.572 | 22,114.998 | 5,490.523 | 71.066 | 39,471,932 |
| 5 | `_rebuild_company` | CAT | 1 | 34,349.892 | 25,818.093 | 25,740.359 | 20,700.732 | 5,039.576 | 77.564 | 39,524,481 |
| 6 | `_rebuild_company` | GCT | 1 | 34,231.895 | 25,181.059 | 25,100.977 | 20,183.494 | 4,917.433 | 79.914 | 39,473,104 |
| 7 | `_rebuild_company` | GLBE | 1 | 33,582.376 | 25,029.267 | 24,899.085 | 19,540.814 | 5,358.216 | 129.962 | 39,477,596 |
| 8 | `_rebuild_company` | CRDO | 1 | 33,882.704 | 25,022.088 | 24,923.940 | 18,735.857 | 6,188.009 | 97.884 | 39,446,298 |
| 9 | `_rebuild_company` | LAUR | 1 | 33,781.067 | 24,932.868 | 24,846.874 | 19,778.685 | 5,068.133 | 85.832 | 39,568,236 |
| 10 | `_rebuild_company` | CHEF | 1 | 33,610.911 | 24,782.915 | 24,710.584 | 19,622.727 | 5,087.788 | 71.975 | 39,504,126 |
| 11 | `_rebuild_company` | A | 1 | 35,643.703 | 24,698.487 | 24,581.701 | 19,851.169 | 4,730.443 | 116.512 | 39,472,749 |
| 12 | `_rebuild_company` | KLAC | 1 | 33,663.050 | 24,553.427 | 24,450.726 | 18,372.829 | 6,077.838 | 102.414 | 39,514,879 |
| 13 | `_rebuild_company` | CON | 1 | 33,770.363 | 24,531.783 | 24,458.128 | 19,544.341 | 4,913.734 | 73.472 | 39,477,298 |
| 14 | `_rebuild_company` | BOW | 1 | 33,128.828 | 24,217.407 | 24,134.223 | 19,484.015 | 4,650.163 | 83.019 | 39,476,694 |
| 15 | `_rebuild_company` | GNK | 1 | 32,509.033 | 24,149.944 | 24,078.943 | 19,199.302 | 4,879.592 | 70.829 | 39,473,454 |
| 16 | `_rebuild_company` | EXEL | 1 | 33,707.080 | 24,135.956 | 24,063.656 | 19,333.526 | 4,730.082 | 72.102 | 39,578,460 |
| 17 | `_rebuild_company` | ETN | 1 | 34,120.579 | 23,773.553 | 23,667.539 | 18,768.843 | 4,898.620 | 105.775 | 39,702,887 |
| 18 | `_rebuild_company` | CGNX | 1 | 31,636.475 | 23,473.371 | 23,384.764 | 18,438.833 | 4,945.879 | 88.359 | 39,470,263 |
| 19 | `_rebuild_company` | GLW | 1 | 32,687.808 | 23,428.727 | 23,347.136 | 18,412.725 | 4,934.354 | 81.385 | 39,706,055 |
| 20 | `_rebuild_company` | DAL | 1 | 33,611.419 | 23,233.047 | 23,110.510 | 17,532.759 | 5,577.702 | 122.338 | 39,743,151 |

### After P1

| # | Owner | Ticker | Depth | Wrapper incl ms | Wrapper excl ms | Manifest ms | Native ms | Fingerprint ms | Body excl ms | Bytes |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `_rebuild_company` | A | 1 | 11,583.771 | 8,399.305 | 8,348.221 | 7,253.649 | 1,094.477 | 50.871 | 39,472,749 |
| 2 | `_persist_company` | AME | 2 | 7,007.442 | 7,007.442 | 6,957.445 | 4,996.926 | 1,960.466 | 49.833 | 39,710,355 |
| 3 | `_rebuild_company` | EXEL | 1 | 9,271.297 | 6,267.110 | 6,208.532 | 5,147.427 | 1,061.053 | 58.384 | 39,578,460 |
| 4 | `_persist_company` | CVX | 2 | 4,965.392 | 4,965.392 | 4,906.653 | 2,947.154 | 1,959.444 | 58.576 | 39,687,609 |
| 5 | `_rebuild_company` | AME | 1 | 11,893.999 | 4,886.557 | 4,769.327 | 2,224.736 | 2,544.535 | 117.022 | 39,666,991 |
| 6 | `_rebuild_company` | FRSH | 1 | 7,523.213 | 4,788.460 | 4,697.743 | 3,305.159 | 1,392.534 | 90.541 | 39,473,799 |
| 7 | `_persist_company` | KLIC | 2 | 4,635.585 | 4,635.585 | 4,564.456 | 3,334.189 | 1,230.212 | 70.924 | 39,540,973 |
| 8 | `_rebuild_company` | BHE | 1 | 8,209.877 | 4,341.817 | 4,244.055 | 2,543.350 | 1,700.636 | 97.561 | 39,471,932 |
| 9 | `_persist_company` | AVT | 2 | 4,017.260 | 4,017.260 | 3,967.499 | 2,891.586 | 1,075.864 | 49.607 | 39,565,930 |
| 10 | `_rebuild_company` | AMN | 1 | 6,802.398 | 3,962.347 | 3,911.419 | 2,624.833 | 1,286.538 | 50.748 | 39,477,123 |
| 11 | `_rebuild_company` | ARM | 1 | 6,705.584 | 3,874.963 | 3,818.865 | 2,007.871 | 1,810.939 | 55.917 | 39,575,978 |
| 12 | `_rebuild_company` | FTNT | 1 | 7,290.297 | 3,873.206 | 3,818.676 | 2,432.249 | 1,386.375 | 54.358 | 39,524,427 |
| 13 | `_persist_company` | BHE | 2 | 3,868.059 | 3,868.059 | 3,810.601 | 2,155.490 | 1,655.027 | 57.252 | 39,521,021 |
| 14 | `_persist_company` | KLAC | 2 | 3,834.484 | 3,834.484 | 3,667.876 | 1,680.586 | 1,987.223 | 166.392 | 39,560,412 |
| 15 | `_rebuild_company` | CHEF | 1 | 6,348.310 | 3,664.107 | 3,593.406 | 2,419.472 | 1,173.869 | 70.486 | 39,504,126 |
| 16 | `_rebuild_company` | DAL | 1 | 6,396.235 | 3,547.902 | 3,486.703 | 1,961.443 | 1,525.205 | 60.962 | 39,743,151 |
| 17 | `_rebuild_company` | CVX | 1 | 8,511.059 | 3,545.668 | 3,472.400 | 2,000.010 | 1,472.321 | 73.030 | 39,633,223 |
| 18 | `_rebuild_company` | APH | 1 | 6,479.617 | 3,515.246 | 3,422.897 | 2,233.324 | 1,189.514 | 92.148 | 39,624,594 |
| 19 | `_rebuild_company` | COHR | 1 | 6,103.846 | 3,504.039 | 3,448.535 | 2,196.988 | 1,251.496 | 55.318 | 39,526,032 |
| 20 | `_rebuild_company` | GCT | 1 | 6,234.264 | 3,499.177 | 3,444.365 | 2,226.528 | 1,217.755 | 54.595 | 39,473,104 |

## 14. Remaining bottlenecks and risks

The remediation removes duplicate tree construction; it cannot remove the final evidence serialization without changing or safely reusing a whole writer digest. Production still emitted approximately 6.986 GB of canonical bytes and spent 558.058 s in manifest construction across 188 independent declarations. Fingerprinting alone remained 220.132 s. This is now the dominant feature cost.

Further reduction would require proving that an entire native request manifest, not merely immutable retained subtrees, is identical under the writer's full semantic arguments. That proof is not part of this P1 patch, and blindly inheriting a parent's digest would violate the independent declaration boundary.

The bounded end-of-batch audits now cost about 40.2 s total. They remain intentionally present because they detect in-place mutable changes that SQLAlchemy may not surface as dirty. Removing them would regress P0.

Risks are bounded as follows:

- reuse is process-local and bundle-local; it is not shared across workers;
- cache lifetime is shorter than transaction authority and is cleared on every fail-closed revalidation cause;
- final canonical JSON is still generated and hashed, so cached subtrees cannot substitute a different final document;
- the production scrubber hides guard telemetry fields by name, an observability issue only; correctness and controlled evidence remain available;
- live provider variation and the later handoff failure are independent of writer performance.

## 15. Recommendation for P2

It is safe to begin a separate P2 locking/concurrency design, using Run 14's single-worker measurements as the new baseline. P2 should not assume the P1 cache can cross sessions, transactions, batches, or workers. Before enabling parallel feature batches, explicitly model:

1. ownership of the long source transaction and `FOR UPDATE` locks;
2. shared benchmark/advisory lock contention;
3. memory multiplication from each worker's 72k-84k sealed bundle and canonical caches;
4. deterministic checkpoint and retry ownership;
5. per-worker source-manifest isolation;
6. certification ordering for finalizer/capture/change/alert;
7. unchanged exact digest/evidence parity under concurrency.

Do not combine P2 with another evidence-format optimization. The remaining full-document hashing cost can be investigated separately only with a same-input, same-bytes, same-digest proof comparable to this P1 certification.
