# CERI P3 full writer-manifest serialization and hashing remediation

## EXECUTIVE VERDICT

- P3 FULL-MANIFEST BOTTLENECK CONFIRMED: **PASS**
- MANIFEST STABLE/VARIABLE DECOMPOSITION COMPLETE: **PASS**
- STREAMING/SEGMENT REUSE SAFE: **PASS**
- NATIVE MANIFEST OBJECT EQUIVALENCE: **PASS**
- CANONICAL BYTE EQUIVALENCE: **PASS**
- WRITER DIGEST EQUIVALENCE: **PASS**
- FEATURE EVIDENCE EQUIVALENCE: **PASS**
- P0 INVARIANTS PRESERVED: **PASS**
- P1 CACHE/DECLARATION SEMANTICS PRESERVED: **PASS**
- P2 LOCK/OWNERSHIP SEMANTICS PRESERVED: **PASS**
- CONTROLLED 94-TICKER BENCHMARK: **PASS**
- P2 SERIAL BASELINE FEATURE WALL: `705.609 s`
- P3 CONTROLLED FEATURE WALL: `458.234 s`
- P2 SERIAL WRITER MANIFEST: `561.351 s`
- P3 WRITER MANIFEST: `139.170 s`
- P2 SERIAL FINGERPRINT: `217.844 s`
- P3 FINGERPRINT: `60.086 s`
- REAL PRODUCTION PIPELINE: **PASS — Run 15 / Pipeline 15**
- REAL CERI CERTIFIED COMPLETION: **PASS — 94/94 certified captures**
- RUN 11 MODIFIED: **NO**
- RUN 13 MODIFIED: **NO**
- RUN 14 MODIFIED: **NO**
- SECOND FEATURE WORKER ENABLED: **NO**
- P2 REBENCHMARK WORTH CONSIDERING AFTER P3: **YES**

P3 preserves the native manifest, canonical JSON byte stream, SHA-256 digest, two independent writer declarations, source/PIT authority, feature payload, and durable evidence. It makes the same proof cheaper by streaming the exact canonical bytes into SHA-256 and re-emitting one transaction-scoped exact byte fragment for the stable batch price-bar container.

The final controlled replay is 1.540x faster. The real canary reproduced the feature improvement: 486.270 seconds versus Run 14's 740.441 seconds, a 34.33% reduction. Total CERI was slower because live provider/normalization and post-feature capture increased by 545.639 seconds, more than the 254.171-second feature saving. P3 conclusions therefore rest primarily on the exact frozen replay and production feature/writer spans, not provider wall.

## 1. Baseline state

P3 began on branch `codex/ceri-authority-remediation` at HEAD `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`, schema head `0089_pipeline_execution_authority`. The pre-P3 P0/P1/P2 diff was saved and fingerprinted before production changes:

- P0/P1/P2 diff Git-blob SHA-1: `e23fb5491cefa1c4fdeeed6a03123671b5cabb1c`;
- P0/P1/P2 diff stream SHA-256: `de4c766d9f08699c337b5063577bb0564660861502bc9d449296239b26d3c63b`;
- full status Git-blob SHA-1: `a3905a0ec9aad6164ed8fc8b3ddf60b51979d95a`;
- diff-stat Git-blob SHA-1: `126d0a540fa6177261a55b5eca4c81c129eddf9c`;
- `.env` SHA-256: `a0e453...` (recorded without exposing configuration secrets);
- active topology: one `local-worker-1`; no feature-only second worker.

The complete baseline is `ceri_p3_baseline_state.md`, file SHA-256 `3413cd4613370a994881687e6132e124d4e01f42b6fe9b018abc7e1a18cd1df8`.

P0/P1/P2 layered files and their tests were retained. No reset, clean, checkout-over, rebase, commit, or push occurred. Runs 11, 13, and 14 were never resumed, recovered, or rewritten.

## 2. Bottleneck confirmation

The frozen P2 reference produced 188 unique writer documents and logically serialized 6,997,455,205 bytes. Complete documents did not repeat, so whole-document memoization was not viable. Yet each 34–40 MB document embedded a 34.137–39.240 MB canonical price-bar batch segment repeatedly.

P2 controlled time was dominated by the final writer path:

| Measurement | P2 serial |
|---|---:|
| Feature makespan | 705.609 s |
| Writer manifest | 561.351 s |
| Fingerprint | 217.844 s |
| Logical canonical bytes | 6,997,455,205 |
| Writer calls | 188 |

One-ticker decomposition proved the large-document shape directly. For ticker A, the outer document was 39,534,594 bytes and its `context` value alone was 39,533,442 bytes. The inner document was 39,585,512 bytes and used the exact same context SHA-256, while adding output-dependent derived, price, revision, and state rows. LGND's context digest differed from A, proving that the complete context was not globally reusable; the nested batch price-bar container remained the stable segment.

## 3. Complete manifest decomposition

Every frozen invocation recorded owner, ticker, depth, parent writer, semantic-argument count, exact document length, digest, batch source identity, calculation context, and selected top-level section identities. The two batches used immutable source manifests 25/26 and exact bundle fingerprints from Run 14.

### Owner summary

The stable figure below is conservative: it counts only the exact whole-batch `bars_by_ticker` fragment, not smaller P1 subtree hits. There are two unique large stable fragments—one per transaction/bundle—not one fragment shared across batches.

| Owner | Calls | Avg bytes | Stable bytes weighted/call | Variable bytes weighted/call | Stable % | Unique full manifests |
|---|---:|---:|---:|---:|---:|---:|
| `_rebuild_company` | 94 | 37,194,667 | 36,851,722 | 342,946 | 99.08% | 94 |
| `_persist_company` | 94 | 37,246,345 | 36,851,722 | 394,624 | 98.94% | 94 |

Per batch, the stable exact fragment was 39,240,410 bytes for job 384 and 34,137,303 bytes for job 385. The first call creates it; later calls validate container membership/order/body and emit the cached bytes.

### Section decomposition

| Manifest section | Canonical bytes (A example) | Stable across batch? | Stable across ticker? | Stable across writer? | P3 reuse strategy |
|---|---:|---|---|---|---|
| Outer `context` | 39,533,442 | No as a whole | No | Yes for the same ticker/invocation pair | Stream; reuse only its proven stable nested bars container |
| Nested `context.bars_by_ticker` | 39,240,410 in batch A; 34,137,303 in batch B | Yes within sealed bundle | Yes within batch | Yes | Exact transaction/bundle-scoped byte fragment |
| Outer `company` | 306 | No | No | Outer only | Stream normally |
| Outer `request` | 593 | Mostly batch-stable but includes request identity | No | Outer only | Stream normally; no speculative cache |
| Outer `input_hash` | 66 | No | No | Outer only | Stream normally |
| Outer `started` | 14 | No | No | Outer only | Stream normally |
| Inner `derived_rows` | 5,775 | No | No | Inner only; output-dependent | Stream normally |
| Inner `price_row` | 8,623 | No | No | Inner only; output-dependent | Stream normally |
| Inner `revision_rows` | 36,755 | No | No | Inner only; output-dependent | Stream normally |
| Inner `state_row` | 732 | No | No | Inner only; output-dependent | Stream normally |
| Writer owner/name | small | Yes per owner | Yes per owner | No | Stream normally |

The cache cannot cross Session, transaction, bundle, worker, run, or calculation context. A membership witness checks mapping keys, list identity, exact row identity/order, and each PIT-projected mapped column plus as-of/revision metadata before reuse. Dirty/deleted rows and all P1/P0 invalidation events clear or bypass fragments.

## 4. Duplicate/full-document analysis

| Scope | Calls | Unique documents | Unique digests | Duplicates |
|---|---:|---:|---:|---:|
| Batch A / job 384 | 100 | 100 | 100 | 0 |
| Batch B / job 385 | 88 | 88 | 88 | 0 |
| Combined | 188 | 188 | 188 | 0 |

There were no duplicate complete manifests across tickers, between outer and inner writers, or across the two frozen batches. First/last common canonical spans reinforce the result:

- job 384 inner: 39,260,648-byte prefix / 215-byte suffix;
- job 384 outer: 48-byte prefix / 98-byte suffix;
- job 385 inner: 34,154,623-byte prefix / 216-byte suffix;
- job 385 outer: 50-byte prefix / 99-byte suffix.

The long inner prefix is incidental document layout, not a safe whole-prefix cache contract. P3 therefore caches the structurally identified nested canonical value, never a positional prefix.

## 5. Design alternatives considered

1. **Whole-document memoization:** rejected because all 188 documents and digests are unique.
2. **Outer digest inheritance by the inner writer:** rejected because the writers are independent provenance boundaries and their documents differ.
3. **Digest-of-digests, binary encoding, or schema v2:** rejected because it changes evidence identity.
4. **Row-fragment-only streaming:** safe but insufficient; it still traversed/emitted millions of row fragments and did not materially attack the largest repeated segment.
5. **Global or cross-worker fragment cache:** rejected because it would outlive source authority.
6. **Selected design:** exact canonical iterator + incremental SHA-256 + one sealed-bundle stable-container fragment with fail-closed witness validation.

## 6. Selected P3 design

`CanonicalEvidenceSerializer.iter_bytes()` emits the same UTF-8 JSON stream with the existing ordering, separators, escaping, scalar normalization, unordered-field sorting, and errors. `fingerprint_streaming()` buffers at most 256 KiB before `sha256.update()` and records logical byte counts even though no giant final byte string is retained.

The existing `bytes()` and `fingerprint()` APIs are unchanged. Diagnostic modes can run P2 materialization, P3 streaming, or both against the same native manifest. Compare mode raises `MUTATION_SOURCE_WRITER_MANIFEST_EQUIVALENCE_FAILED` on any manifest, byte, or digest mismatch; it never silently falls back.

The source bundle owns `_writer_canonical_fragments`. Revalidation clears it together with P1 source/canonical caches. The stable container optimization is explicitly limited to `CeriFeatureBatchContext.bars_by_ticker`, whose identity/membership/PIT body is revalidated on every writer call.

## 7. Files changed

P3 production changes:

- `app/services/canonical_evidence.py`: exact streaming serializer/fingerprint and telemetry;
- `app/services/source_mutation_authority.py`: writer compare modes, bundle-scoped fragments, stable-container witness, telemetry;
- `tests/test_canonical_evidence_serializer.py`: serializer torture/equivalence suite;
- `tests/test_source_mutation_authority.py`: membership/order/PIT-body/invalidation tests;
- `tests/integration/test_t14d_source_bundle_postgresql.py`: real transaction invalidation and fragment clearing;
- `scripts/profile_ceri_writer_manifest.py`: decomposition, compare paths, cProfile, isolated fingerprint benchmark;
- `scripts/benchmark_ceri_p2_parallel.py`: reference/compare fingerprint controls and complete invocation diagnostics;
- `scripts/forensics/capture_ceri_p3_canary.py`: concise durable canary extraction;
- `scripts/forensics/capture_ceri_p3_immutability.py`: bounded old-run immutability hashes.

P0/P1/P2 files already dirty at baseline were preserved. P3 did not alter the qualified `FOR SHARE` redesign, claim allowlists, worker ownership, stale fencing, checkpoint barriers, or default worker topology.

## 8. Serializer correctness proof

The torture suite compares reference bytes/digest and exception behavior for:

`None`, booleans, integers, positive/negative floats, negative zero, Decimal, aware datetime, date, time, fixed offsets, zoneinfo/tzinfo, UUID, Enum, bytes, nested and unsorted mappings, lists, tuples, sets, frozensets, `reason_codes`, nested unordered fields, Unicode, escapes, long strings, empty containers, stringified-key collisions, unsupported objects, NaN, Infinity, and naive datetimes.

Results:

- reference bytes equal streaming materialization;
- `sha256(reference bytes)` equals streaming fingerprint;
- exception types and contract-significant messages match;
- fragment hit/miss output remains byte-identical;
- `bytes()` remains supported and unchanged for general callers.

Focused unit/regression result: **139 passed**, two unrelated dependency deprecation warnings. Ruff and `git diff --check` passed.

## 9. Writer byte/digest parity

The full compare-both-path replay executed P2 materialization and P3 streaming for every writer call. It would fail at the writer boundary on one byte of difference. It completed all 188 calls:

- job 384 semantic SHA-256: `e5404a4f1e717ffeea8f4f17a32a1b92c3849486309fa062e830a73a838d0550`;
- job 385 semantic SHA-256: `1d9b54333e33f8208ee3c9ee3a1644cf7681e3e849567053cc38baa7d40bcfff`;
- legacy/reference fingerprints: 188;
- streaming/candidate fingerprints: 188;
- mismatch exceptions: 0.

The comparison artifact is `ceri_p3_frozen_serial_full_parity.json`, SHA-256 `4ecb6fe01e4f05a5386dae24966969fa933a4028ebdcb166bcef75d6fdde0489`.

Representative exact identities follow. Reference and candidate lengths/digests are identical; full 94-ticker coverage is stronger than this required sample.

| Batch/ticker | Outer bytes | Outer SHA-256 | Inner bytes | Inner SHA-256 |
|---|---:|---|---:|---|
| A/A | 39,534,594 | `29bb0475cf43a6a7361bf1a537cba69186af28fb74cff8ca2c92bf80e176f729` | 39,585,512 | `a8d88ca4ae9a430762c1902c131f6b994c32f0ae07859271cb008b06a7438c10` |
| A/AAMI | 39,528,142 | `574a0eb686310c2f7f31ea3fd81aefb5ba908668f185594c29016bb8d6efdc84` | 39,571,413 | `63d06a3cf7921ebdf12b53046ab797471a8cf12e9ee64797b8a9ec31af7f0576` |
| A/AAPL (largest) | 40,021,949 | `f9eb24decfa3a60b05afcff7878a99470870a2ec792a5896086d827e7557415f` | 40,119,405 | `4bce6836dd467e8c6cf2e3f047a38fdaf57cf3b8999f688052e1d2cd2e8b59b2` |
| A/ACMR | 39,535,005 | `9c29aa82ff66788e7626c82c76c93e1f376eb04f287872cffab2a5a298dbdafb` | 39,585,580 | `a6674d23f31473ec1a9f6c013fe945370250a1bc89af5ad927da97e8cc2bdbf5` |
| A/ADI | 39,570,947 | `8ff40f8d2e455845ed5a9768c042caf1262e939eb22807a3d3c1284a63bdc6c8` | 39,626,842 | `5806481e537d7fc3cb22c4b096e8f76495d5c686148facb1c412a8af5ac6e7b3` |
| A/AIT | 39,557,049 | `2b1bff33ef202b8a71d3b21f3d6638584c059689481b60de3d1ebe95b302e35d` | 39,607,852 | `2bc0d1e2160546a373f1c7fc5b914b17bd15f6ca186779bc96456d93359c39bc` |
| A/AME | 39,728,933 | `85b8d20c0759651980a9c8b0d6e04fa82eac2a28d7c84abc65d51408deb78032` | 39,779,864 | `3e847c5ceeb31ad49649a4a64a388f85cc8abfff4f8ab8afa3df5e78e248f715` |
| A/AMG | 39,630,270 | `7089f85c8c13ee3fc68cf9db949bc30be1af92b33e52ae5ce53b5214e6fe58cd` | 39,682,674 | `bae705b1c0ac554f7ab639c2b0da60c26bde402b5f3d50f910bd1fda0096d68d` |
| A/LGND (last) | 39,540,135 | `70069a2f4cc14ce14ddae2a1981c02086429b26967fc75405ceedd6c8c1f0be7` | 39,592,227 | `57b272d4ddfab7016bec5600895041fcf33dec00cbd5fb841e92f8f921031050` |
| A/CRDO (smallest) | 39,508,254 | `6150f628ac2602749ab91ad80afeabb1c1b50f0102ade06299ec1e464b80db35` | 39,558,528 | `11500f327a4c6bf3b7916d80b2e5e019f8a30476f24b23d61fa61295c0dc1821` |
| B/LSCC (first) | 34,441,236 | `3ae25e38f51d861513a12090ee5c57bc79170b3cbc08f47ee46b9da4650ca5c3` | 34,493,681 | `01c606d2863f483fd0c70b5eec09eb748b23b8e3c782fdc2eb0a6e8bfb8e16f4` |
| B/LTC | 34,432,380 | `afa14b5b3e6cebbf36b4c639dd2a9790230f0e304d8224d172c02150f9344837` | 34,483,620 | `c7488a0219a0144e039d59a002623d63eba9e8b8a573cbc1203d2845b18d6dbf` |
| B/MGNI | 34,426,295 | `0dc6233e5878c799143dd91d5953f513ff25ad83d635e8163837b296194863e6` | 34,476,799 | `ffc20338455c04193a9582d75565589c2ff8395aa379dbd7de27527bd731a3b2` |
| B/MITK | 34,421,829 | `1f5a3d60d58743af061d381bec24e24fc40a60e44e099f84cd5d2635765b20d7` | 34,471,292 | `3d75699b504be704f69400529c05183d64089b8fde386a9102ef870d1d2e1586` |
| B/MPWR | 34,461,252 | `561012ee011e02d66dcb8178ca82f2cb2ef41d0943b30caf0c69938228793292` | 34,510,096 | `c5682a11d538ad547b783f18b9bd5c2243f421fad4c8cf07cb51f0ef43391a09` |
| B/MSGE | 34,463,217 | `3a75d26b6e693a084009eda8615609fa696eca56b8c0de98bd431ebf43cbacbb` | 34,515,538 | `be7165c8c55045c4ab3dbbcf13d160d54db3f08fa4e9d34ee28cf4db2ef89baa` |
| B/MTSI | 34,530,664 | `187a3a53c318d76a7ddc73e6f55c9eef6edb7564fe6565f3daa0f0667b029b62` | 34,575,464 | `7e5f166f845bf4caf3282c004e09e9385a0b7d3bd0244fe74709b7feff872961` |
| B/NMM | 34,466,631 | `48ed6fea7dc13a0354d8565a13f0b9ba1fb2903f99b410a55bd34cddd2f46467` | 34,516,172 | `0aebda501dcb86a71c259b012d74e927b0c0559454f68e2197dd2858709ac6e1` |
| B/ZBRA (last) | 34,438,566 | `eec1c26fa5a88087a3f10dc7f4e1b3ea5f28e712ca27bae1d3152680f7d62dfe` | 34,491,128 | `71dff030feb382f529886106994b895563a1227ac0e784c6615c1e4374da7840` |
| B/NPK (smallest) | 34,179,522 | `ffce523f360993e6ad48b6aadd45fa3ef19c04e61f656706dd653a55c4150897` | 34,192,578 | `cc3e309a14419f44ce9f7e9f2540347d49ff3a2a6921f25337c169fc0ff75b39` |
| B/NVDA (largest) | 35,294,402 | `c765804c1587a7782bfbb0b57d84cde926e31b2d0c94d16751e02204ff7dc98c` | 35,448,769 | `c3edcf373dd364d2c93c05235ab60a5cac94a379f03d64495ef7a6dc62b1735e` |

## 10. Frozen 94-ticker semantic parity

The rollback-only serial comparison used the exact retained Run 14 source manifests and database identities. Required parity passed for:

- source count and exact bundle fingerprint;
- ticker order;
- native manifest object;
- canonical bytes and writer SHA-256;
- input/output feature evidence hashes;
- feature payload and feature count;
- both writer boundaries for all 94 tickers.

The final optimized replay retained the same semantic projection hashes shown above. Logical bytes were 6,997,455,206—one byte above the recorded P2 invocation because the diagnostic `started` value was captured at a different representational boundary. Same-invocation compare mode proved exact bytes; evidence semantics and both frozen semantic hashes are identical. P3 does not claim that two executions with different timing metadata must have identical whole documents.

## 11. Controlled before/after benchmark

| Measurement | P2 serial baseline | P3 optimized | Change |
|---|---:|---:|---:|
| Feature makespan | 705.609 s | 458.234 s | −247.375 s / −35.06%; 1.540x |
| Writer manifest | 561.351 s | 139.170 s | −422.181 s / −75.21% |
| Fingerprint | 217.844 s | 60.086 s | −157.758 s / −72.42% |
| Canonical logical bytes | 6,997,455,205 | 6,997,455,206 | same semantic bytes; timing-metadata byte noted above |
| Writer calls | 188 | 188 | unchanged |
| Source fingerprint parity | PASS | PASS | unchanged |
| Feature evidence parity | PASS | PASS | unchanged |
| Peak RSS | 940,802,048 B | 1,030,430,720 B | +89,628,672 B / +9.53% |
| Minimum host available | not recorded | 6,492,602,368 B | safe headroom |
| Executed refreshes | 0 | 0 | unchanged |
| Full audits | 2 | 2 | unchanged |
| PostgreSQL lock-wait samples | 0 | 0 | unchanged |

The final artifact is `ceri_p3_frozen_serial_optimized_final.json`, SHA-256 `c04dfab478939631e5ce765875b8524327da23f8a5b8ff05fa3517736b5caf7b`.

Logical evidence volume did not shrink. P3 avoided repeatedly allocating and walking that volume: 6.879 GB of stable fragment bytes were reused while the exact 6.997 GB logical stream was hashed.

## 12. Profiler before/after

The rollback-only one-ticker profiler exercised both real writer boundaries. cProfile inflates absolute wall, so the evidence is call/cumulative structure rather than a substitute for the unprofiled benchmark.

| Function | P2 reference calls | P2 cumulative/self | P3 calls | P3 cumulative/self | Finding |
|---|---:|---:|---:|---:|---|
| `_source_value` | 155,454/10 | 82.523/3.911 s | 79,495/10 | 63.599/1.972 s | stable container avoids repeat source traversal |
| `_source_value_impl` | 155,454/10 | 79.620/6.887 s | 79,495/10 | 3.910/3.949 s | repeated recursive body conversion removed |
| `canonicalize` | 2,252,140/76,130 | 43.451/21.959 s | 2,022,244/82,964 | 33.169/19.192 s | fewer repeated canonical reconstructions |
| `bytes` | 121 | 8.237/0.043 s | 119 ordinary calls | 2.713 cumulative | giant writer bytes no longer use this path |
| `fingerprint_streaming` | 0 | N/A | 2 | 2.917/0.130 s | exact incremental writer hashing |
| `_iter_bytes` | 0 | N/A | 201,933/29,915 | 2.203/0.292 s | exact streaming emission |
| JSON `iterencode` | 1,100 | 0.103 s self | 3,706 | 0.118 s self | bounded fragment serialization remains cheap |
| UTF-8 `str.encode` | 370 | 0.048 s self | 13,292 | 0.027 s self | small chunk encoding, no giant final copy |
| OpenSSL SHA-256 constructor/update | 183 | 0.197 s self | streaming updates included above | measured separately | hashing remains a real floor |

Reference profile: `ceri_p3_profile_reference_A.json`. Optimized profile: `ceri_p3_profile_optimized_A.json`.

The final isolated benchmark used the exact in-memory outer and inner manifests captured from certified Run 15 batch 416, ticker A, after the production worker became idle. Seven post-warmup repetitions compared the P2 materialized `bytes()+sha256` path with the P3 streaming/fragment path:

| Manifest | Path | Wall min | Wall median | Wall p90/p95 | Wall max | CPU median | Tracemalloc peak | Digest |
|---|---|---:|---:|---:|---:|---:|---:|---|
| outer `_rebuild_company` | P2 reference | 1,656.992 ms | 1,838.486 ms | 1,917.379 ms | 1,917.379 ms | 1,421.875 ms | 79,088,916 B | equal |
| outer `_rebuild_company` | P3 candidate | 202.868 ms | 285.125 ms | 361.127 ms | 361.127 ms | 250.000 ms | 289,111 B | equal |
| inner `_persist_company` | P2 reference | 1,537.622 ms | 2,076.485 ms | 2,181.600 ms | 2,181.600 ms | 1,437.500 ms | 79,194,232 B | equal |
| inner `_persist_company` | P3 candidate | 271.773 ms | 360.864 ms | 400.066 ms | 400.066 ms | 281.250 ms | 289,651 B | equal |

At the median, P3 was 6.45x faster for the outer fingerprint and 5.75x faster for the inner fingerprint. Both exact digests matched on every repetition. Reference runs triggered 6 generation-0 and 1 generation-1 GC collections per large manifest; the candidate triggered none for the outer and 4 generation-0 collections for the inner. The artifact is `ceri_p3_isolated_fingerprint_A.json`, SHA-256 `096aa5ec6342138e0e48d5691e3974593b470838f50d8ed4896bf546bfd2723e`.

Ordinary-document guardrail results were also exact. The streaming path was slower in absolute microbenchmarks—0.143 ms versus 0.030 ms median for a tiny scalar mapping, and 2.980 ms versus 1.138 ms for a nested Unicode document—but the existing general `fingerprint()`/`bytes()` API remains unchanged and still uses the reference materialized path. P3 routing is limited to the writer-manifest call site, so this measured streaming overhead does not regress ordinary production evidence.

## 13. Allocation/memory comparison

P2 materialized one 34–40 MB canonical `bytes` object for each writer fingerprint after constructing/walking the document. P3 keeps the general `bytes()` API but uses a 256 KiB pending buffer for writer hashing and directly feeds large exact cached fragments to SHA-256.

Measured structural allocation indicators:

- largest logical document: 40,119,405 bytes;
- logical bytes: 6.997 GB, unchanged;
- hash updates: 764 across 188 documents in the final replay;
- stable fragment bytes reused: 6,878,658,862;
- streaming serialization wall: 24.266 s;
- hashing wall: 35.810 s;
- peak controlled RSS: 1.030 GB versus 940.8 MB P2 (+9.53%);
- minimum host available: 6.493 GB;
- no memory guard, worker, or database failure.

The isolated identical-manifest benchmark gives the allocation mechanism directly: materializing either ~39.5 MB document peaked at ~79.1 MB of Python-traced allocation, while streaming the same bytes and digest peaked below 290 KB (more than 99.6% lower). This is a per-call temporary-allocation result; process RSS remains the authoritative whole-batch safety measure above.

The RSS increase is real and is retained as a remaining risk, but it is bounded and far smaller than host headroom. The real feature jobs peaked at approximately 940 MB by API sampling. A later capture phase reached ~2.26 GB while persisting comparisons; that phase begins after both P3 feature jobs and is not attributed to the writer serializer.

## 14. Run 14 versus P3 production benchmark

Run 15 was admitted exactly once through `POST /uploads` and exactly once through `POST /runs/15/pipeline`, with `ALLOW_CACHE_FALLBACK`, normal PostgreSQL, normal app, normal durable queue, and one `local-worker-1`. It reached durable `ceri_completion_state=CERTIFIED` with feature jobs 416/417 and 94 certified captures. No retry occurred.

| Measurement | Run 14 production | P3 Run 15 | Change |
|---|---:|---:|---:|
| CERI total | 2,465.386 s | 2,757.560 s | +292.174 s / +11.85% |
| Provider/normalize | 1,544.292 s | 1,894.709 s | +350.417 s / +22.69% live variance |
| Feature wall | 740.441 s | 486.270 s | −254.171 s / −34.33% |
| Batch A | 415.017 s | 261.288 s | −153.729 s / −37.04% |
| Batch B | 325.038 s | 224.199 s | −100.839 s / −31.02% |
| Post-feature | 180.653 s | 375.875 s | +195.222 s / +108.01% live capture variance |
| Writer manifest | 558.058 s | 112.772 s | −445.286 s / −79.79% |
| Fingerprint | ~220.132 s | 49.290 s | −170.842 s / −77.61% |
| Persistence wrapper/phase | 280.978 s | 46.637 s | −234.341 s / −83.40% |
| Named family compute | 4.659 s | 11.308 s | live/input variance; definition not used for P3 verdict |
| Canonical logical bytes | ~6.986 GB | 6.988 GB | same evidence shape; live rows changed |
| Feature workers | 1 | 1 | unchanged |

Run 15 source manifests contain 83,735 and 72,747 rows with bundle fingerprints `0086d40b...9317fb` and `d63542a8...d360`. Both audits covered those exact counts. After CERI certification and alert rebuild completed, the normal continuation later failed at `EVALUATING_SETUP_LIFECYCLES` with `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH` at 16:48:41+02:00. That step is downstream of the durable CERI barrier, was not modified or recovered by P3, and does not alter the certification verdict. The final canary artifact records both facts: `ceri_completion_state=CERTIFIED` with 94 captures and terminal pipeline status `FAILED` at the downstream step. `ceri_p3_production_canary_run15.json` SHA-256: `e7236e2d4f9d0f4a2f614f3c08c579491971acee2cae1124535c779fcb2b4e8b`.

Historical progression:

| Measurement | Run 11 | Run 13 P0 | Run 14 P1 | P2 serial current | P3 |
|---|---:|---:|---:|---:|---:|
| CERI total | 13,065.931 s | 4,968.483 s | 2,465.386 s | N/A diagnostic | 2,757.560 s production |
| Feature wall | 11,404.773 s | 3,091.732 s | 740.441 s | 705.609 s diagnostic | 486.270 s production / 458.234 s controlled |
| Writer manifest | historical inclusive | 2,889.955 s | 558.058 s | 561.351 s | 112.772 s production / 139.170 s controlled |
| Fingerprint | historical | historical | ~220.132 s | 217.844 s | 49.290 s production / 60.086 s controlled |
| Feature SQL | 154.640 s | 14.679 s | 11.451 s | not separately certified | writer-attributed 3.104 s production |

## 15. P0 regression evidence

Real PostgreSQL source-authority suite: **20 passed** in 214.29 seconds. It includes exact retained revision bodies, unflushed/dirty rejection, fail-closed raw SQL invalidation, commit/session/physical-connection invalidation, exact-scope guards, 75,000-identity retry chunking, retained `FOR SHARE` mutation blocking, and missing-identity detection.

Frozen and production telemetry:

- clean assertion rows: 0;
- dirty assertion rows: 0;
- full audits: 2;
- production audit rows: 83,735 + 72,747;
- executed refreshes: 0;
- refreshed rows: 0;
- invalidations: 0 normal-path;
- unknown mutation tests: fail closed.

## 16. P1 regression evidence

P1 source-value and canonical-subtree caches remain bundle/transaction scoped. New tests prove:

- stable membership/order/list replacement is rejected;
- PIT-projected body mutation is rejected;
- revalidation clears source, canonical, fragment, and stable-container caches;
- dirty/deleted retained rows bypass reuse;
- transaction and connection invalidation clear reuse;
- outer and inner declarations remain distinct.

Production emitted 100 + 88 streaming calls, 0 legacy calls, and retained both owner/depth boundaries. Stable-container hits/misses were 99/1 and 87/1, exactly one construction per batch.

## 17. P2 regression evidence

The qualified `FOR SHARE OF <source table>` design is unchanged. Ten targeted PostgreSQL tests passed in 73.72 seconds, covering:

- competing feature workers cannot claim the same attempt or unrelated jobs;
- exact worker registry and queue/job-type allowlists;
- stale owner fencing and late checkpoint rollback;
- detached checkpoint isolation;
- concurrent finalizer exactly-once behavior;
- pipeline completion barrier and parent-failure propagation;
- historical pipeline jobs remain non-authoritative.

The full t14d PostgreSQL suite also passed lock coexistence, UPDATE/DELETE blocking, DML-CTE invalidation, and 75k retry tests. The P3 canary used only `local-worker-1`; the second feature worker remained disabled.

## 18. New performance ceiling

P3 removed 247.375 seconds, or 35.06%, from controlled feature wall. The new measured serial floor is 458.234 seconds.

Remaining measured spans (some overlap and must not be summed as exclusive phases):

| Remaining span | P3 controlled | Share of feature wall |
|---|---:|---:|
| Writer manifest | 139.170 s | 30.37% |
| Native-source conversion inside manifest | 79.065 s | 17.25% |
| Fingerprint total | 60.086 s | 13.11% |
| Hashing portion | 35.810 s | 7.82% |
| Streaming serialization portion | 24.266 s | 5.30% |
| Bounded full audits | 77.706 s | 16.96% |
| Retained loads | 49.806 s | 10.87% |
| Sealing | 32.612 s | 7.12% |
| Writer body exclusive | 24.042 s | 5.25% |
| Writer SQL | 7.712 s | 1.68% |

The highest-value safe future work is no longer giant final JSON materialization. It is audit/witness cost, remaining non-stable native-source conversion, and raw SHA-256 throughput. Any further cache must retain the same transaction/PIT invalidation proof.

## 19. Whether P2 deserves a future rebenchmark

**YES, as analysis only.** P3 reduced writer CPU and feature wall enough to change the contention economics. Production batch walls are now 261.288 and 224.199 seconds; an impossible zero-contention lower bound for two workers would be about 261 seconds rather than 486 seconds. That does not prove a real speedup on this two-core host.

P2's measured range was unstable (0.811x–1.194x), and its exact parallel run was slower. Keep the second worker disabled. A future rebenchmark should use the new P3 code, exact parity checks, at least three serial/parallel repetitions, and a host with demonstrably available physical CPU. Enablement requires stable benefit, not one favorable sample.

## 20. Remaining risks

1. Peak controlled RSS increased 9.53%. It remained safe with 6.49 GB host headroom, but future larger batches need monitoring.
2. The stable witness intentionally scans ordered membership/PIT bodies on each reuse. That is correctness-preserving but now contributes measurable CPU.
3. Fragment caches rely on the existing P1 bundle lifecycle. New mutation entry points must call the same fail-closed invalidation path.
4. SHA-256 throughput is now a larger fraction of fingerprint time; changing the digest or hashing a different representation is forbidden.
5. Production total CERI remains provider- and capture-variable. Feature/writer spans, not total wall, are the P3 acceptance metric.
6. Ten new comparison events referenced prior certified snapshots during Run 15, but directly owned rows for Runs 11/13/14 were unchanged. Eight direct table hashes combined to `317a75da115a56d66425f014770462a17d83abad78e5b00ad2ccc6b8e2499cc2` both before and after.
7. Run 14's later `HISTORICAL_EVIDENCE_UNAVAILABLE` COHR continuation issue remains out of scope and untouched.
8. Run 15's later `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH` lifecycle failure likewise occurred after durable CERI certification. P3 did not retry, recover, or repair it; the application performed its own normal retry accounting.

Evidence discipline:

- pre/post old-run artifacts: `ceri_p3_runs_11_13_14_pre_canary.json` / `ceri_p3_runs_11_13_14_post_canary.json`;
- direct old-run upload, raw rows, pipelines, steps, jobs, manifests, feature states, and snapshots: unchanged;
- no commit or push;
- no P2 concurrency enablement;
- no historical run recovery or repair.
