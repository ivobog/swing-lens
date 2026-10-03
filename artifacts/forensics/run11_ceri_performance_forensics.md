### EXECUTIVE VERDICT

- RUN 11 CERI WALL TIME: `13,065.931034 s / 03:37:45.931`
- HISTORICAL TIME ACCOUNTED FOR: `100.000% at top-level interval resolution; 10,258.877 s of old feature telemetry lacked an internal phase label`
- DIAGNOSTIC TIME ACCOUNTED FOR: `99.964% (188.136/188.204 s in the exact-manifest profiled window; bounded cached-input diagnostic, not an end-to-end CERI rerun)`
- UNEXPLAINED TIME: `0.000 s at top level; 0.068 s in the profiled diagnostic window`
- PRIMARY BOTTLENECK: `CPU-heavy full-batch source-integrity work repeated per ticker: three 72k-83k-row in-memory scans plus one exact SQL revalidation per ticker`
- SECONDARY BOTTLENECK: `1,097.159 s in persistence wrappers, although only 154.640 s of both feature batches was SQL execution`
- CERI CPU-BOUND: `PARTIAL (the two feature batches are; provider acquisition is mixed network/local work)`
- CERI DB-BOUND: `NO (feature-batch SQL was 1.356% of feature wall time; pool wait was 3.069 ms total)`
- CERI QUEUE-BOUND: `PARTIAL (one synchronous worker serialized all jobs, but it was continuously running useful CERI work)`
- CERI I/O-BOUND: `NO for the measured hot path; host disk telemetry was absent`
- CERI NETWORK/PROVIDER-BOUND: `PARTIAL (506.567 s measured EODHD request time, 3.877% of stage wall time; none in feature batches)`
- SINGLE-WORKER SERIALIZATION COST: `5,185.440 s / 39.687% theoretical overlap opportunity; not safely recoverable with current shared row locks`
- TWO LARGE BATCHES EXPLAIN: `11,404.772638 s / 87.286%`
- REMAINING STAGE OVERHEAD: `1,661.158396 s / 12.714%`
- SAFE PARALLELISM AVAILABLE: `PARTIAL (disjoint company outputs, but current batches share and FOR UPDATE-lock 1,506 benchmark price rows for hour-long transactions)`
- OBSERVABILITY GAPS FOUND: `12: no host/per-core/I/O series, no cumulative process CPU, no historical PG waits/locks, pg_stat_statements absent, track_io_timing off, no old source-authority spans, no old handler phase spans, no ticker row-count spans, no worker capacity/busy metric, SEC-network duration absent, duplicated worker gauges across scrape targets, and no end-to-end trace identity in Prometheus`
- NEW TELEMETRY ADDED: `YES`
- REALISTIC OPTIMIZED CERI DURATION: `about 6,864 s / 01:54:24 after the proven source-authority hot-path repair; modeled range 6,300-7,500 s`
- REALISTIC SPEEDUP: `about 1.90x for the hot-path repair alone; about 3.05x (4,282 s) only after both that repair and a proven-safe two-worker locking redesign`
- CORRECTNESS INVARIANTS PRESERVED: `YES (only timing/logging and rollback-only diagnostics were added; Run 11 was not recovered or modified)`
- READY FOR PERFORMANCE REMEDIATION IMPLEMENTATION: `YES, beginning with source-authority scan/invalidation redesign and the retry parameter-limit defect—not speculative worker scaling`

## 1. Exact timeline

All timestamps below are Europe/Zurich (`UTC+02:00`). PostgreSQL/application timestamps were normalized from UTC before subtraction. Grafana uses `timezone: browser`; Prometheus samples are UTC.

Run identity: pipeline/run `11`, root job `262`, root correlation `root-dd4a6f1bb8774767825fa002a59be2fd`, CERI workflow `ceri:pipeline:11:28adaf159aa4a4af9a62827496436c81be7da239b58bade6b7272897e49e0d9a`, worker process `11440`, worker instance `caf62451ad874e9aa0b2aeae3afad3cd`, commit `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`.

```text
12:44:26.946527  CERI_PROVIDER_INGEST stage starts
12:44:27.041447  jobs 263-289 created in one fan-out
12:44:29.128065  pipeline dependency wait begins (dispatch = 1.744085 s)
12:44:29.531677  job 263 first child starts

12:44:29.532 ─ 12:49:29.472  estimates provider jobs 263-266 (25/25/25/19)
12:49:29.593 ─ 13:05:49.455  estimates normalize jobs 267-268; 24 deferrals
12:49:30.120 ─ 12:52:07.728  earnings provider jobs 269-272
12:52:08.482 ─ 13:07:19.516  earnings normalize jobs 273-274; 16 deferrals
12:52:09.141 ─ 12:54:11.513  catalyst provider jobs 275-278
12:54:12.757 ─ 13:08:20.048  catalyst normalize jobs 279-280; 8 deferrals
12:54:13.338 ─ 12:55:22.724  SEC guidance provider jobs 281-284
13:08:20.151 ─ 13:08:48.145  guidance normalize jobs 285-286

13:08:48.251888 ├─ feature batch 287, 50 tickers
14:52:27.584392 ┘  6,219.332504 s
14:52:28.299814 ├─ feature batch 288, 44 tickers (gap 0.715422 s)
16:18:53.739948 ┘  5,185.440134 s

16:18:55.434692 ├─ finalizer 289 (preceding claim gap 1.694744 s)
16:18:56.339092 ┘  0.904400 s
16:18:56.611466 ├─ capture 290 (gap 0.272374 s)
16:22:09.925407 ┘  193.313941 s
16:22:10.071518 ├─ change detection 291 (gap 0.146111 s)
16:22:10.924368 ┘  0.852850 s
16:22:11.030159 ├─ alert rebuild 292 (gap 0.105791 s)
16:22:12.877561  │  stage certified after 1.847402 s of alert work
16:22:13.050296 ┘  alert job DB completion follows stage by 0.172735 s
16:22:13.158340  continuation job 293 starts; outside this CERI-stage ledger
```

The stage result independently records dispatch `1.744085 s`, dependency wait `13,063.749496 s`, child execution `13,063.345884 s`, and total logical duration `13,065.931034 s`. Every child was executed by PID 11440. There was no worker process startup inside the stage.

## 2. Wall-clock ledger

Top-level intervals are additive. CPU is a sampled estimate and must not be added to wall time. DB and network are nested inside active phases.

| Phase | Start | End | Wall s | % total | Process CPU s (estimated) | DB s | Network s | Queue/gap s | Evidence |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Pre-feature provider/normalization | 12:44:26.946527 | 13:08:48.251888 | 1,461.305361 | 11.184% | 983.717 | not historically complete | 506.567 | 9.545 non-attempt/poll/claim gaps | stage row, job logs, Prometheus |
| Feature batch 287 | 13:08:48.251888 | 14:52:27.584392 | 6,219.332504 | 47.610% | 5,950.461 | 87.205547 | 0 | 0 | job row, job telemetry, SQL recorder, Prometheus |
| Inter-batch claim gap | 14:52:27.584392 | 14:52:28.299814 | 0.715422 | 0.005% | no sample | 0 | 0 | 0.715 | job rows |
| Feature batch 288 | 14:52:28.299814 | 16:18:53.739948 | 5,185.440134 | 39.687% | 4,870.447 | 67.434097 | 0 | 0 | job row, job telemetry, SQL recorder, Prometheus |
| Finalizer/capture/change/alert | 16:18:53.739948 | 16:22:12.877561 | 199.137613 | 1.524% | 197.754 | not historically complete | 0 | 2.219 inter-job gaps | job and stage rows |
| **Total** | 12:44:26.946527 | 16:22:12.877561 | **13,065.931034** | **100.000%** | **11,998.024 direct stage estimate** | **154.639644 known feature SQL** | **506.566636 known** | included above | normalized sources |

Feature-batch additive hierarchy from the old telemetry:

```text
11,404.772638 feature-batch wall
  41.393      initial context/input load
   5.780      six named feature-family calculations
1,097.159     persistence wrappers
4,372.626     unlabelled inside CeriFeatureRebuildService
5,886.251     unlabelled in the batch handler outside service timers
   1.563      job-wrapper/timer boundary residual
```

Those six terms sum exactly to the two DB-row batch durations. The old telemetry therefore assigned the wall clock but did not name 10,258.877 s internally. The new profile and code correlation identify the dominant work in those old unlabelled buckets without inventing a retroactive per-call split.

## 3. Prometheus findings

- Prometheus scraped web `:8000`, worker `:9101`, and supervisor `:9102` every 10 seconds and retained 30 days. It started before Run 11 and contains the complete requested range.
- Worker PID 11440 averaged `91.827%` CPU over the stage (1,307 samples), corresponding to approximately `11,998.024` sampled process CPU-seconds. `psutil.Process.cpu_percent(None)` defines 100% as approximately one logical core; it is not a cumulative OS CPU counter.
- Batch 287: `95.677%` average, `60.1-113.6%`, approximately `5,950.461` CPU-s for `6,219.333` wall-s.
- Batch 288: `93.925%` average, `62.7-103.3%`, approximately `4,870.447` CPU-s for `5,185.440` wall-s.
- RSS was `573-858 MiB` in batch 287 and `706-808 MiB` in batch 288; stage maximum was `1,043 MiB`, reached during post-feature capture.
- `sum(swinglens_jobs_running)` was exactly 1 in every stage sample. Queue depth averaged `3.930`, peaked at 27, and ended at 1; runnable depth averaged `2.905`, peaked at 26, and ended at 0.
- EODHD recorded 282 successful requests, zero retries, `506.566636 s` request duration, 3,659,038 response bytes, and 386,691 stored bytes. No provider requests occurred in the feature batches.
- CERI ingestion-duration counters ended at estimates `273.089 s`, earnings `136.523 s`, catalysts `97.444 s`, and SEC guidance `42.449 s`.
- Worker pool gauges read zero because sampling uses a separate control session; the job-scoped SQL recorder is the authoritative pool-wait source for these jobs.
- The same worker registry gauges are exported by both web and worker targets. Queries must include `job="swinglens-worker"` or they double count.

## 4. Grafana findings

All seven provisioned dashboards use browser timezone. The CERI dashboard exposes provider rate/retries/latency/bytes and aggregate CERI result counters, but no feature phase, source-authority, ticker, or CPU-vs-wall panels. The worker/overview dashboards expose CPU and RSS. The database dashboard exposes pool pressure, slow-query/long-transaction rates, recorder queues, and storage. The jobs dashboard exposes queue depth and job duration.

Grafana did not hide an existing root-cause panel: the necessary feature-internal measurements did not exist. The raw Prometheus series and SQL flight-recorder records were therefore queried directly.

## 5. Host-resource findings

The monitored process was CPU-active for nearly the full feature wall time, and the rollback diagnostic reproduced `94-98%` process-CPU/wall ratios in the suspect functions. That proves a predominantly single-core CPU bottleneck in feature processing.

There is no node/Windows exporter. Historical total CPU, per-core utilization, run queue, context switches, user/system/iowait, committed memory, pagefile, paging, page faults, disk throughput/latency/queue, filesystem utilization, network throughput, thread count, and handle count do not exist. Consequently, host saturation and disk latency cannot be reconstructed. They are UNKNOWN, not silently classified. The low SQL fraction and high process CPU rule them out as the primary feature bottleneck.

## 6. Worker and queue findings

The supervisor owns one `LaunchedWorker` and calls `_supervise_once` for one configured worker ID. `_start_worker` launches one synchronous `python -m app.worker`. `run_worker` enters one claim loop; `run_worker_once` claims and executes one job before returning. `.env` sets `JOB_POLL_INTERVAL_SECONDS=2`.

Observed topology agrees: launcher PID 23412, worker PID 11440, worker ID `local-worker-1`, and exactly one running job throughout. No job-type concurrency cap or CERI-specific advisory lock caused the sequential schedule; the global single process did.

Job 287 waited about 24m20s after fan-out before it could run. Job 288 waited about 2h08m after fan-out and then began only `0.715 s` after job 287's DB completion. The worker was not idling during those waits—it processed the prerequisite provider/normalization work and then batch 287.

## 7. PostgreSQL findings

| Metric | Job 287 | Job 288 |
|---|---:|---:|
| SQL calls | 3,266 | 2,909 |
| SQL calls/ticker | 65.320 | 66.114 |
| SELECT / INSERT / UPDATE / OTHER | 2,412 / 252 / 502 / 100 | 2,157 / 218 / 446 / 88 |
| SQL seconds | 87.205547 | 67.434097 |
| SQL % of job | 1.402% | 1.301% |
| DB seconds/ticker | 1.744 | 1.533 |
| max statement | 2.461 s | 1.606 s |
| ORM flushes | 321 | 283 |
| pool wait | 1.642 ms | 1.427 ms |
| pool wait max | 0.062 ms | 0.043 ms |
| transaction events / time | 167 / 5.230 s | 149 / 6.879 s |
| duplicate statements | 3,098 | 2,752 |
| transaction lifetime | 6,219.589 s | 5,185.761 s |

The dominant SQL fingerprint is `PrefetchedSourceBodies.refresh` rereading exact `price_bars` IDs: 50 calls/46.514 s in batch 287 and 44 calls/39.317 s in batch 288. The next full-source rereads cost 21.809 s and 12.608 s. This is an N-per-ticker full-batch revalidation pattern. Despite being the top SQL, all feature SQL together is only `154.640 s`, or `1.356%` of feature wall.

PostgreSQL is 18.3. Only `plpgsql` is installed. `shared_preload_libraries` names `pg_stat_statements, auto_explain`, but `pg_stat_statements` is not created, so no historical server-side statement table exists. `track_io_timing=off`; read/write timing is zero. Current cumulative database statistics (74 temp files/1.061 GB, zero deadlocks) span activity after Run 11 and cannot be attributed to it. `pg_stat_activity` and `pg_locks` are current-state views, not historical archives. No historical wait-event or lock snapshot exists.

No unbounded `EXPLAIN ANALYZE` was run. The recorder already contains actual statement times and exact call counts, which is safer and more relevant than replaying 75k-ID lock queries against the live database.

## 8. Code-path findings

```text
pipeline CERI stage
  -> fan-out jobs 263-289
  -> worker claim loop
  -> execute_feature_batch_job
     -> prepare_batch (entire 50/44-company source bundle)
        -> load companies, estimates, earnings, catalysts, source records
        -> add shared SPY benchmark
        -> load + FOR UPDATE price/source rows
        -> seal/fingerprint retained bundle
     -> for each ticker
        -> CeriProcessingRun create/get
        -> service.rebuild
           -> outer prefetched_source_scope
           -> rebuild_from_context
              -> inner prefetched_source_scope
              -> feature-family calculations
              -> explicit full-bundle assert_unchanged_in_memory
              -> decorated _persist_company
           -> both scopes assert and refresh on exit
        -> checkpoint + heartbeat
  -> finalizer -> capture -> change detection -> alert rebuild
  -> exact-membership certification -> continuation
```

The principal complexity defect is `O(tickers × all batch source rows)`, not feature mathematics:

- batch 287 retains 83,167 rows; batch 288 retains 72,077;
- the explicit assert plus inner and outer scope-exit asserts perform three complete scans per ticker;
- that is `83,167 × 3 × 50 = 12,475,050` row checks and `72,077 × 3 × 44 = 9,514,164`, or **21,989,214 row integrity checks**;
- raw/operational SQL conservatively marks the bundle for revalidation, causing one exact full-bundle reread per ticker (proved by 50 and 44 calls for each top refresh fingerprint);
- each row check reconstructs dictionaries and canonical JSON, then hashes them;
- ordinary feature-family calculations total only `5.780 s` across all 94 tickers.

Compact, sorted JSON serialization of the two retained manifests is 10.578 MB and 9.196 MB. Repeated fingerprinting and deep copies create heavy Python allocation pressure.

An additional production defect was proved by the first rollback diagnostic: retry reconstruction passes all 75,806 price-bar IDs through one `IN` expression in the expected-manifest branch, exceeding psycopg/PostgreSQL's 65,535 bind-parameter ceiling. `refresh` is chunked; `load(... expected_manifest=...)` is not. A production-sized feature retry would fail before calculation.

## 9. Profiler findings

The diagnostic reconstructed job 287's exact retained manifest by primary key in protocol-safe chunks, verified source count `83,167` and the retained bundle fingerprint, ran no feature writer, and rolled back.

Profiled window:

| Operation | Wall s | CPU s | CPU/wall | Assigned detail |
|---|---:|---:|---:|---|
| exact load + seal | 50.895 | 48.797 | 95.9% | load 30.524 s; seal 19.985 s |
| one in-memory assert | 47.257 | 45.656 | 96.6% | 83,167 rows |
| one forced refresh | 69.952 | 67.703 | 96.8% | SQL 5.048 s; validation 63.923 s |
| one durable manifest | 20.033 | 19.563 | 97.7% | 83,167 entries |
| **total** | **188.136** | **181.719** | 96.6% | profiler total 188.204 s; 0.068 s residual |

`cProfile` recorded 201,621,678 calls. `Canonical.fingerprint` ran 415,836 times and accumulated 163.451 profiler-seconds. Recursive canonicalization made 12,282,905/415,836 calls and accumulated 81.216 seconds; `deepcopy` made 9,306,638/166,334 calls and accumulated 42.853 seconds. Profiling overhead inflates wall time, so clean unprofiled measurements are used for projections:

| Exact bundle | Rows | assert wall/CPU | forced refresh wall/CPU | refresh SQL | refresh validation |
|---|---:|---:|---:|---:|---:|
| job 287 | 83,167 | 16.782 / 15.797 s | 21.619 / 20.625 s | 2.603 s | 18.426 s |
| job 288 | 72,077 | 13.376 / 13.094 s | 19.053 / 18.094 s | 2.506 s | 15.984 s |

The experiment directly proves that validation is CPU-heavy and SQL is the minority even inside forced refresh.

## 10. Per-ticker distribution

These are the historical service `batch_total_ms` values, not checkpoint-to-checkpoint handler time.

| Batch | min | p50 | p75 | p90 | p95 | p99 | max | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 287 (50) | 51.053 | 58.437 | 62.671 | 65.879 | 67.732 | 69.162 | 69.962 | 59.262 s |
| 288 (44) | 44.359 | 53.661 | 62.829 | 73.363 | 80.187 | 91.596 | 93.892 | 57.101 s |

Slowest batch-287 tickers: AME 69.962, GEV 68.330, GNK 67.880, GLBE 67.551, BHE 66.861 s. Slowest batch-288 tickers: SNX 93.892, NVEC 88.553, SN 80.986, PLUS 75.656, SHOO 74.586 s.

Exact-manifest row counts do **not** explain the outliers: duration correlation with per-company price/estimate/earnings/catalyst rows is `r=0.038` and `r=0.032`. NVEC, for example, was the second-slowest in batch 288 with only 1,511 attributable rows, while NVDA had 1,885 rows (including 150 events and 150 revisions) and was faster at 66.498 s. This is consistent with a nearly fixed full-batch scan cost per ticker plus machine/runtime variation, not pathological ticker data.

Feature throughput was `0.495 tickers/minute`, `121.327 feature-wall seconds/ticker`, `65.691 SQL calls/ticker`, `1.645 DB seconds/ticker`, and `0.223 inserted features/second`.

## 11. Batch comparison

| Measurement | Batch 287 | Batch 288 |
|---|---:|---:|
| tickers | 50 | 44 |
| wall | 6,219.333 s | 5,185.440 s |
| context load | 26.169 s | 15.224 s |
| summed per-ticker service | 2,963.112 s | 2,512.453 s |
| persistence wrapper | 604.216 s | 492.943 s |
| named feature compute | 2.783 s | 2.997 s |
| service-internal unlabeled | 2,356.113 s | 2,016.513 s |
| handler-outside-service unlabeled | 3,229.351 s | 2,656.900 s |
| source rows | 83,167 | 72,077 |
| sampled CPU/wall | 95.7% | 93.9% |
| SQL/wall | 1.402% | 1.301% |

Runtime scales much more closely with `tickers × batch source rows` than with ticker-local evidence or named feature work. Batch 288's long tail is not explained by row volume.

## 12. The residual 1,661.158 seconds

The prompt's 1,661.931 s used rounded batch durations. Exact subtraction produces `1,661.158396 s`:

```text
1,461.305361  pre-feature
    0.715422  inter-feature claim gap
  199.137613  post-feature
-------------
1,661.158396
```

Pre-feature decomposes exactly:

| Work | Seconds |
|---|---:|
| provider job attempts (16 jobs) | 636.218313 |
| normalization attempts (8 jobs, including deferral checks) | 815.542502 |
| stage entry/enqueue/poll/claim/inter-job gaps | 9.544546 |
| **pre-feature total** | **1,461.305361** |

Normalization made 56 claims: 8 terminal attempts and 48 dependency deferrals (24 estimates, 16 earnings, 8 catalysts). Provider active time was estimates 299.385, earnings 155.624, catalysts 118.084, guidance 63.124 s. EODHD network requests account for 506.567 s inside those provider jobs; SEC guidance used the cached/readiness path and has no network-duration metric.

Post-feature decomposes exactly: batch-to-finalizer gap 1.694744; finalizer 0.904400; gap 0.272374; capture 193.313941; gap 0.146111; change detection 0.852850; gap 0.105791; alert work before stage certification 1.847402 s.

## 13. Concurrency analysis

The company output sets are disjoint, and final certification already waits for exact membership of both batches. That makes batch-level concurrency architecturally plausible, but it is not safe/effective in the current implementation:

- both manifests share exactly 1,506 `price_bars` identities (the benchmark series);
- `prepare_batch` explicitly adds the benchmark ticker;
- source loads use `SELECT ... FOR UPDATE`;
- each feature job holds a transaction for its complete 86-104 minute life;
- a second worker would block on the shared benchmark rows. The other source-table identities are disjoint.

Safe concurrency requires separating immutable shared benchmark reads from exclusive source locks (or proving an appropriate shared/no-lock immutable snapshot mechanism), shortening the transaction scope, retaining exact manifests, and equivalence tests for output hashes, checkpoints, cancellation, retry, and certification. Until that is done, “add workers” is not a remediation.

The maximum *mathematical* serialization cost is the shorter batch, `5,185.440 s` (39.687% of stage), assuming perfect overlap. It is an upper bound, not a currently realizable saving.

## 14. Experimental results

1. Historical replay setup using the production expected-manifest loader failed safely with `psycopg OperationalError: number of parameters must be between 0 and 65535`. The transaction rolled back; no artifact or Run 11 state was written. This proves the retry-size defect.
2. The diagnostic harness was changed—not production loading behavior—to fetch the exact retained identities in chunks. It verified count and fingerprint before measuring.
3. cProfile exact-bundle run: 188.204 s window, 99.964% phase-accounted, source count 83,167, zero writers, final rollback.
4. Clean exact-bundle timings were run for both historical manifests and produced the assert/refresh table in section 9.
5. Result equivalence was enforced by retained bundle fingerprint equality. No feature outputs were recalculated, so no claim is made about a full end-to-end post-optimization time.

## 15. Amdahl/optimization ceiling

Clean diagnostic timings projected over the **observed** three asserts and one executed refresh per ticker give:

- batch 287: `50 × (3 × 16.782 + 21.619) = 3,598.225 s`;
- batch 288: `44 × (3 × 13.376 + 19.053) = 2,603.967 s`;
- total repeated source-authority opportunity: `6,202.192 s`, 54.382% of feature wall and 47.468% of stage wall.

This is a measured projection, not a retroactively observed historical subspan. A correct dirty-row assertion plus source-table-aware invalidation should make normal no-change checks near constant time while retaining fail-closed checks for actual dirty/unknown writes.

| Scenario | Duration | Speedup | Limiter | Confidence |
|---|---:|---:|---|---|
| Current | 13,065.931 s | 1.00x | repeated canonicalization, one worker | high |
| Zero all feature SQL (impossible upper bound) | 12,911.291 s | 1.012x | Python source validation | high bound |
| Source-authority hot-path repair, one worker | ~6,863.739 s (range 6,300-7,500) | ~1.90x | persistence/handler CPU and serial feature work | medium |
| Two current workers, no lock redesign | no reliable gain; may block | not forecast | 1,506 shared FOR UPDATE rows | high |
| Two workers after hot-path + lock redesign | ~4,282.266 s / 01:11:22 | ~3.05x | longer optimized batch + 1,661 s serial overhead | medium-low |
| Four workers | not forecast | not forecast | only two current batches; 4-core CPU/memory/DB contention untested | high that evidence is insufficient |

For the two-worker combined model, optimized feature remnants are 2,621.107 and 2,581.473 s; perfect overlap leaves the larger plus the measured 1,661.158 s non-feature fraction. This is the traceable basis of 4,282.266 s. It still needs an equivalence/performance experiment before operational adoption.

## 16. Prioritized remediation recommendations

| Priority | Change | Current cost | Expected cost after | Estimated saving | Evidence | Risk |
|---|---|---:|---:|---:|---|---|
| P0 | In `PrefetchedSourceBodies.assert_unchanged_in_memory`, inspect only retained ORM rows present in `Session.dirty`/attribute history; fingerprint those rows against retained bodies. Keep fail-closed handling for unknown mutation paths. | projected 4,282.907 s for three scans/ticker | near-constant on clean source rows; retain one bounded audit at batch boundary if required | about 4,200 s | exact scan counts; clean assert CPU timings; cProfile | medium correctness; requires mutation tests |
| P0 | Make bundle invalidation source-table and statement-semantic aware. Do not invalidate on proven read-only/control SQL such as `set_config`; keep invalidation for source-table DML and unknown write text. | projected 1,919.285 s for one full refresh/ticker | no-op refresh in normal case; bounded exact reread only after relevant mutation | about 1,850-1,900 s | 50/44 refresh calls; clean forced-refresh timings; SQL recorder | medium correctness |
| P1 | Chunk the expected-manifest identity restriction in `PrefetchedSourceBodies.load`, as `refresh` already does. | retries fail above 65,535 binds | deterministic chunked retry load | availability fix, not normal-run time saving | reproduced psycopg failure with 75,806 IDs | low-medium |
| P1 | Preserve the new source/handler CPU and wall telemetry; add regression budgets based on source rows checked/refreshed per ticker. | 10,258.877 s historically unlabeled | <1% unexplained on future batches | measurement, not direct saving | this investigation | low |
| P2 | After P0, separate shared benchmark evidence from exclusive company locks and run the two batches concurrently; verify exact output hashes and barriers. | 5,185.440 s theoretical serial overlap | overlap optimized batches | about 2,581 s after P0 model | exact 1,506-row overlap; long transactions | high until proven |
| P2 | Narrow transaction/lock lifetime; keep immutable manifest identity durable while releasing unnecessary row locks between preparation and compute. | transactions 6,219.589/5,185.761 s | bounded lock intervals | contention-risk reduction; time saving not yet measured | SQL recorder | high correctness |
| P3 | Investigate persistence-wrapper CPU with new writer/fence timers before changing upserts. | 1,097.159 s wrapper; only 154.640 s all SQL | unknown | do not claim yet | old nested timer + SQL recorder | medium |
| Do not prioritize | Generic SQL/index tuning | 154.640 s total feature SQL | zero is impossible | absolute ceiling 154.640 s | SQL recorder | low payoff |

Correctness conditions for P0/P1: exact source identity and body fingerprints, PIT cutoff, source purity, durable manifests, source-table DML detection, retry equivalence, transaction fencing, and deterministic hashes must remain unchanged.

## 17. Observability changes made

- `PrefetchedSourceBodies`: monotonic load, seal, durable-manifest, in-memory assert, refresh SQL, refresh validation, total refresh, scope entry/body/exit, and writer manifest/fence/body totals; row/call counters; low-cardinality snapshot.
- CERI feature handler: per-ticker structured timing for admission, processing-run acquisition, rebuild, output validation, processing finish, checkpoint, heartbeat, total, process CPU, and residual. Ticker remains in structured logs, not Prometheus labels.
- Batch result telemetry: batch process CPU, CPU utilization, summed ticker handler phases, and source-authority snapshot.
- Rollback-only exact-manifest diagnostic profiler with retained fingerprint verification and protocol-safe diagnostic chunking.

The instrumentation uses monotonic clocks for durations and existing structured logs/job result JSON for correlation. It introduces no new high-cardinality Prometheus labels.

## 18. Files changed

- `app/services/source_mutation_authority.py` — source-authority spans/counters only.
- `app/services/ceri/batched_job_handlers.py` — ticker/batch wall and CPU telemetry only.
- `scripts/profile_ceri_source_bundle.py` — rollback-only diagnostic profiler.
- `tests/test_source_mutation_authority.py` — telemetry counter assertions.
- `tests/ceri/test_batched_workflow_v2.py` — batch/ticker telemetry contract assertions.
- `artifacts/forensics/run11_ceri_source_profile_job287.json` — cProfile result.
- `artifacts/forensics/run11_ceri_source_timing_job287.json` and `...job288.json` — clean exact-bundle timings.
- this report.

No speculative performance optimization was implemented. No Run 11 row or pipeline state was changed.

## 19. Commands and queries used

Principal evidence collection included:

```text
git rev-parse HEAD; git status --short; process inventory
SQLAlchemy reads of pipeline_runs, pipeline_steps, background_jobs,
  ceri_feature_source_manifests and job JSON telemetry
stream parse of logs/lifecycle-worker.log for jobs 263-292
stream parse of logs/db-monitor/worker/sql-2026-10-02-p11440*.jsonl
Prometheus /api/v1/query_range over 2026-10-02 10:40-14:30 UTC
  swinglens_worker_cpu_percent, swinglens_worker_rss_bytes,
  swinglens_jobs_running, swinglens_queue_depth, provider/CERI counters
PostgreSQL: version(), pg_extension, settings, pg_stat_database,
  pg_stat_checkpointer, pg_stat_activity, pg_locks
manifest-set overlap and per-ticker retained-row aggregation
scripts/profile_ceri_source_bundle.py --job-id 287 (profiled and clean)
scripts/profile_ceri_source_bundle.py --job-id 288 --skip-cprofile
ruff check; focused pytest suite
```

The raw SQL recorder's job-summary records are the source for final SQL counts; the slightly earlier embedded job snapshot undercounts by 13 statements in each batch.

## 20. Remaining uncertainty

1. Historical feature-internal spans did not exist, so the exact number of old seconds in each source scan cannot be reconstructed. The root cause is proven by call structure, 50/44 refresh repetitions, near-one-core historical CPU, exact-source diagnostics, and function profiling; the 6,202.192-s saving is a projection.
2. No host exporter means total-host saturation, per-core scheduling, disk, paging, and network behavior are historically unknown.
3. No historical PostgreSQL lock/wait snapshots exist; current views cannot answer past waits. Feature SQL's 1.356% share bounds the importance of DB execution but does not reconstruct every OS-level I/O stall.
4. SEC guidance has service duration but no separate network metric. Logs/configuration indicate the cached readiness path; an exact SEC network/local split is unavailable.
5. The controlled diagnostic covered the exact retained hot data and source-authority operations, not a new 94-ticker end-to-end pipeline. This avoided mutating/recovering Run 11 and avoided a redundant multi-hour run. A post-remediation end-to-end benchmark remains required before accepting the 1.90x/3.05x models.
6. Four-worker duration is intentionally not estimated: the current fan-out has only two batches, the host has four logical CPUs, and multi-process memory/DB behavior has not been tested.
