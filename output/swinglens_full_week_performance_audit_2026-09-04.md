# SwingLens Full-Week SQL and Application Performance Audit

Audit date: **2026-09-04**

Frozen analysis window: **2026-08-26T11:25:41.966762Z–2026-09-04T09:14:30Z**

Elapsed window: **8 days 21 hours 48 minutes 48 seconds**

This was a read-only investigation. No application code, index, PostgreSQL setting, queue/job state, production data, or monitoring data was changed. The scripts and derived JSON/Markdown files under `output/` are offline audit artifacts.

## 1. Executive Summary

The week identifies the application’s important performance problems, but it is **not a complete continuous seven-day capture**. The correct integrity classification is **PARTIAL**. The old global 32-file retention defect is fixed, yet a different monitor failure occurred: worker telemetry saturated its 10,000-record queue and reported **547,009 dropped records** across worker process incarnations (about **6.0% of attempted writes**) plus seven writer errors. Seven system-wide monitor-silent intervals total **32.3 hours**, and the longest continuous observed span is only about **66.1 hours**. Web and supervisor writers reported no drops, so GUI measurements are much more trustworthy than worker SQL totals; job-summary and worker-SQL figures are lower bounds.

The deployed CERI score scoping correction worked exactly where intended. Across nine `GET /runs/{run_id}/ceri` requests there were **zero unfiltered score-snapshot reads** and **18 scoped reads**: nine by `run_id`, plus nine different ID-set reads needed for the change panel. The old same-run score dataset was not loaded twice.

That did not make the run page acceptable. It still issued a median **705 SQL statements**, took **79.26 s median / 241.46 s max**, and spent only **15.2%** of wall time executing SQL. The remaining cost is primarily Python/ORM evidence construction: per-company freshness, diagnostic, guidance, revision, catalyst, earnings and source lookups, plus the page’s whole-table change-panel loads. The strongest next CERI action is to batch the DTO evidence for the visible company page, not add indexes to each tiny lookup.

`GET /ceri/changes` is the slowest typical page and the largest cumulative user wait: **80.68 s median, 195.83 s p95/max, 1,287 s total across 14 requests**. It still loads full tables and filters, sorts, counts and paginates in Python. Because the HTML page also loads alerts, the same guidance/revision/catalyst/change datasets are read twice in one request. SQL is only 21.3% of wall time; object materialization and Python processing dominate.

The other major systemic cost is the worker/supervisor control plane. The identifiable families account for at least **4.58 million calls, 5,284 seconds (1.47 h) of SQL, and roughly 896,000 heartbeat writes**—about **60% of observed SQL calls and 46% of observed SQL time**. These are lower bounds because all telemetry drops occurred in workers. A bounded adaptive idle cadence is strongly justified.

### Required executive answers

| # | Question | Answer |
| ---: | --- | --- |
| 1 | Did the monitor retain a trustworthy full week? | **No. PARTIAL.** Role retention works, but 547,009 worker events were dropped and seven silent intervals remove 32.3 h. |
| 2 | Did the CERI run-page scoping fix work? | **Yes.** 0 unfiltered and 18 scoped score reads across nine requests; one `run_id` load per request. |
| 3 | Is the CERI run page still slow? | **Yes.** 79.26 s median, 241.46 s p95/max. |
| 4 | What causes its remaining latency? | Per-company evidence/freshness/diagnostic N+1 work and whole-table change-panel materialization; about 85% of wall is outside measured SQL. |
| 5 | What is the slowest user-facing page? | `GET /ceri/changes` by typical latency; `GET /runs/{run_id}/ceri` has the worst single sample. |
| 6 | What page causes the most cumulative user waiting? | `GET /ceri/changes`: 1,287 s observed, versus 952 s for the run page. |
| 7 | What is the most expensive SQL fingerprint? | Overall: worker-row read `50e38550046d` (925 s). Most expensive business/data query: unfiltered wide `ceri_score_snapshots` read `d0ca5d0f21db` (680 s). |
| 8 | What is the worst N+1 pattern? | CERI run/dashboard DTO assembly by removable user wait: about 700 queries for 50 companies. Winner Probability also reaches 1,023 queries/request but has much lower observed cumulative wait. |
| 9 | What is the most expensive background job? | `FULL_PIPELINE` has the largest reported total (5.428 h), narrowly ahead of provider ingest (5.339 h), but it is an orchestrator and overlaps child work. `CERI_PROVIDER_INGEST_BATCH` is the largest standalone workload. |
| 10 | What causes the longest job outliers? | Mostly orchestration/wait, provider/network/persistence, and application loops—not SQL or pool waits. The Winner maturation outlier is a price-bar ORM N+1. |
| 11 | Is CERI feature rebuild still a meaningful bottleneck? | **Meaningful but secondary.** 81 jobs took 47.0 min; SQL was 5.16 min/11%. The prior per-feature/per-ticker anti-patterns remain resolved. |
| 12 | How expensive is worker/supervisor polling? | At least 4.58 M calls, 1.47 h SQL and ~896 K writes; actual cost is higher because worker records dropped. |
| 13 | Are there lock/contention problems? | **No material page/job contention.** 21 blocking samples, 11 sessions, max observed age 4.145 s; all were heartbeat/lease updates. |
| 14 | Are there pool-wait problems? | **No systemic problem.** 109 waits totaling 11.07 s; no timeout or overflow. One 1.927 s outlier. |
| 15 | Are long transactions a problem? | **Yes, as an operational risk.** 353 events, 10.19 s median, 165.63 s p95, 460.90 s max; CERI requests and pipelines hold transactions through long application work. |
| 16 | Is PostgreSQL underpowered? | **Not demonstrated.** Median SQL is 0.799 ms; neither pool nor locks saturate. Pathological query shapes and Python materialization dominate. |
| 17 | Are important indexes missing? | Not broadly. Existing indexes support the scoped queries. Two conditional follow-ups are a price-coverage covering index and target-only winner watermark indexes, after call/query-shape fixes. |
| 18 | Should price coverage use a maintained summary? | **Yes.** 150 calls consumed 128.46 s; plans scan/sort large `price_bars` populations. |
| 19 | Should the SQL monitor remain enabled? | **Yes, but not at the current all-statement volume.** Keep summaries/slow traces/pgss; sample or short-retain fast SQL, slow the sampler, compress, and repair writer backpressure. |
| 20 | What five changes should be implemented next? | Push down CERI Changes; batch CERI page evidence; adaptive control-plane backoff; batch Winner Probability lookups/watermark reuse; maintain price-coverage summaries. |

## 2. Audit Integrity

### Window and versions

The authoritative `FULL_WEEK_SQL_AUDIT_START` marker is at `2026-08-26T11:25:41.966762Z`. The last record included is `2026-09-04T09:14:29.639267Z`; the audit end was frozen at `09:14:30Z`.

| Git commit | Observed interval | Records | SQL calls | SQL time | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| `6896e6ba17e` | Aug 26 11:25:41–11:33:14Z | 2,812 | 2,777 | 7.59 s | Dirty startup/marker interval only |
| `f4d31a88c45` | Aug 26 11:33:19–Aug 27 17:04:50Z | 1,063,809 | 907,382 | 1,441.69 s | Retention, telemetry and CERI scoping deployment |
| `baca21413a2` | Aug 27 02:54:08–Sep 4 09:14:29Z | 7,666,404 | 6,678,448 | 10,009.59 s | Current clean code; overlaps old long-lived processes until Aug 27 17:04 |

All records report deployment ID `full-week-sql-audit-20260826`, application version `0.1.0`, and feature implementation `batch-prefetch-v1`. Material performance conclusions are unchanged in the stable `baca214` segment, but the overlap is not treated as a single clean deployment average.

PostgreSQL is **18.3, 64-bit Windows**. Database size at audit time was **6,986 MB**. `shared_preload_libraries` contains `pg_stat_statements, auto_explain`; pgss tracks `all`; auto_explain threshold is 750 ms with nested statements on, `ANALYZE` and buffer logging off.

### Monitor configuration

| Setting | Value |
| --- | --- |
| SQL monitor | enabled |
| Slow query / full trace | 100 ms / 250 ms |
| Full stack for every SQL | false |
| Retention / role max files | 14 days / 512 |
| Max file / role byte cap | 100 MB / 16,384 MB |
| Queue | 10,000 |
| Parameter digest | enabled |
| Long transaction / pool event | 5,000 ms / 5 ms |
| Activity sampler | enabled, 1 s cadence, 1,500 ms active / 5,000 ms idle-tx thresholds |
| Test log path | separate `logs/db-monitor-test` |

### Retention and continuity

Oldest trustworthy production SQL is approximately `2026-08-26T11:25:42Z`; newest is `2026-09-04T09:14:29.525167Z`. Files span the full 8.91-day envelope, but telemetry is not continuous.

| Missing interval (UTC) | Duration |
| --- | ---: |
| Aug 26 13:11–21:27 | 8 h 16 m 42 s |
| Aug 27 22:02–22:31 | 29 m 11 s |
| Aug 27 22:47–Aug 28 01:20 | 2 h 32 m 49 s |
| Aug 30 19:28–22:12 | 2 h 44 m 03 s |
| Aug 31 05:56–06:41 | 45 m 08 s |
| Sep 2 12:29–21:02 | 8 h 33 m 14 s |
| Sep 3 01:32–10:28 | 8 h 56 m 34 s |

There are **seven** system-wide gaps over three minutes, totaling **32.3 h**. They look like host/database/process downtime rather than retention eviction. The longest continuous monitor-health span is about **66.1 h**, not seven days.

### Record inventory and health

| Evidence | Count |
| --- | ---: |
| SQL records | 7,588,607 |
| HTTP request summaries | 6,588 |
| Background-job summaries | 14,167 |
| Job phase records | 46,495 |
| Slow-query traces | about 4,021 in the frozen retained role files |
| Full stack traces | 1,413 |
| Lock/activity records | 11,524 |
| Pool-wait records (>=5 ms) | 109 |
| Long-transaction records | 353 |
| Monitor-health records | 42,490 |
| Activity-sampler errors | 2,072, primarily connection timeouts during unavailable/restart periods |
| ORM flush records | 905,406 |
| JSON parse errors | 3 across 8,951,381 scanned lines |
| SQL failures | 0 in retained role files |

The retained tree was **16,943,439,994 bytes in 322 files** at inventory time. Production role directories contain 290 files; 32 legacy root files are older carryover. Role bytes were worker 15.219 GB/217 files, supervisor 1.221 GB/31, web 214.7 MB/16, CLI 2.71 MB/17, diagnostic 29 KB/9.

The old global `max_files=32` defect is **resolved**: limits are role-specific and 512 files. Test telemetry is isolated and did not consume production retention. However, the worker’s 16 GB role cap supports only about 9–10 days at the observed rate, not the configured 14 days.

Worker health counters sum to **7,875,527 written, 547,009 dropped and 7 writer errors** across process incarnations. The queue reached 10,000, oldest queued age reached 434.7 s and writer latency reached 459.4 s. Web, supervisor, CLI and diagnostic reported zero drops. This is why the overall classification is **PARTIAL**, while the web-route subset is **mostly complete when the system was running**.

## 3. Week Overview

Observed SQL totals are lower bounds for workers:

- 7,588,607 statements; 11,458.87 s (**3.183 h**) execution time.
- SELECT 5,823,006; INSERT 237,303; UPDATE 1,511,720; DELETE 51; other 16,527.
- Mean 1.510 ms; median 0.799; p90 1.943; p95 2.656; p99 7.780; maximum 46,579.086 ms.
- 3,170 canonical fingerprints.

### Daily totals

| Date (UTC) | SQL calls | SQL time s | SELECT | INSERT | UPDATE | DELETE | p95 ms | Max ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Aug 26* | 521,270 | 759.1 | 353,209 | 27,988 | 138,391 | 6 | 2.536 | 28,252.5 |
| Aug 27 | 814,580 | 1,621.2 | 662,737 | 17,713 | 133,175 | 3 | 3.764 | 21,981.7 |
| Aug 28 | 1,468,361 | 2,601.5 | 1,073,181 | 64,892 | 325,949 | 12 | 3.128 | 45,655.3 |
| Aug 29 | 847,488 | 1,221.8 | 668,876 | 17,488 | 159,124 | 6 | 2.532 | 41,818.8 |
| Aug 30 | 480,663 | 580.9 | 412,789 | 0 | 67,874 | 0 | 2.465 | 2,565.8 |
| Aug 31 | 662,763 | 833.3 | 537,041 | 9,958 | 115,234 | 4 | 2.330 | 12,460.8 |
| Sep 1 | 1,036,791 | 1,213.0 | 787,317 | 35,206 | 211,842 | 8 | 2.366 | 6,670.2 |
| Sep 2 | 769,228 | 1,076.2 | 570,627 | 31,584 | 164,592 | 6 | 2.285 | 46,579.1 |
| Sep 3 | 778,851 | 1,289.3 | 578,082 | 32,474 | 166,074 | 6 | 2.841 | 37,275.6 |
| Sep 4* | 208,612 | 262.7 | 179,147 | 0 | 29,465 | 0 | 2.514 | 797.3 |

Aug 26 and Sep 4 are partial dates. Aug 28 is the heaviest complete date. The busiest SQL hour was **Aug 27 20:00Z: 329,053 calls and 729.0 s SQL**, followed by Aug 28 20:00Z at 701.1 s. These are pipeline/job periods. The slowest GUI hour was Sep 3 11:00Z (18 requests, p95 225.1 s), caused by CERI pages rather than pool or lock saturation.

### Process roles

| Role | SQL calls | SQL time s | Share of observed SQL time | Integrity |
| --- | ---: | ---: | ---: | --- |
| Web | 46,428 | 852.69 | 7.44% | No recorded drops; seven host gaps |
| Worker | 6,901,491 | 9,777.95 | 85.33% | Lower bound; 547,009 dropped records |
| Supervisor | 640,607 | 795.08 | 6.94% | No recorded drops |
| CLI/diagnostic | 81 | 33.15 | 0.29% | Read-only investigations; not production workload |
| Other | 0 | 0 | 0% | None observed |

## 4. Comparison with August 26 Audit

| Previous problem | Previous evidence | Current status | Current evidence |
| --- | --- | --- | --- |
| Unfiltered `ceri_score_snapshots` load | Wide full-table load in run-page path | **IMPROVED** | Run page has 0 unfiltered calls; globally the pattern persists: 54 calls/680.0 s in Changes, dashboard, change detection and alert rebuild. |
| Duplicate score-snapshot load | Same run dataset loaded twice | **RESOLVED** | Exactly one `WHERE run_id=?` score read/request; second score read uses a distinct referenced-ID set. |
| `/runs/{run_id}/ceri` N+1 | Up to 655 SQL, 213.6 s wall | **UNCHANGED** | 703–709 SQL/request; median 79.3 s, max 241.5 s. The score fix reduced its SQL tail but DTO loops remain. |
| Full `ceri_guidance_events` loads | Whole-table ORM materialization | **UNCHANGED** | 59 calls, 118.2 s and 3.34 M returned rows; 509,980 rows on nine run pages. |
| Full `ceri_revision_features` loads | Whole-table ORM materialization | **UNCHANGED** | 43 calls, 49.9 s and 6.31 M rows; up to 186,174 rows in one execution. |
| `/ceri/changes` whole-table processing | Max 69.5 s, SQL 14.3 s | **WORSE** | Median 80.7 s; max 195.8 s; SQL median 16.6 s/max 30.2 s; Python/ORM median about 59.5 s. |
| Operations duplicate aggregates | Same aggregate called repeatedly | **RESOLVED** | Parameter digests show no exact duplicate aggregate; `known_total` reuse is present. Route still issues 42 distinct/parameterized calls. |
| Worker idle polling | Poll amplification | **UNCHANGED** | At least 4.08 M worker control-plane calls and 1.30 h SQL; several jobless poll families each execute ~301 K times. |
| Supervisor polling | Repeated fencing/registration/heartbeat | **UNCHANGED** | About 504 K calls/614 s SQL in the three main families. |
| Homepage price coverage | ~1–2 s aggregate over hundreds of thousands of bars | **UNCHANGED** | 60 homepage calls/72.6 s SQL; route SQL share 77.5%; plans use parallel seq/bitmap scans and sort/group. |
| CERI feature revision UPSERT | Remaining feature cost | **IMPROVED** | 73.8 s over the full feature workload, but only 2.6% of feature wall; still the top feature SQL family. |
| CERI feature price-bar preload | Remaining feature cost | **IMPROVED** | 65 retained calls/67.3 s, 5.09 M retained rows; batched rather than per ticker. |
| CERI feature source-record preload | Remaining feature cost | **IMPROVED** | Batched context load; source/estimate preloads are no longer per ticker; full context load is 9.57 min total. |

## 5. SQL Fingerprint Analysis

### Cumulative leaders

| Rank | Fingerprint | Calls | Total s | Mean ms | p95 ms | Rows | Primary meaning |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `50e38550046d` | 910,754 | 925.0 | 1.016 | 1.997 | 910,751 | Worker row read after registration/heartbeat |
| 2 | `d0ca5d0f21db` | 54 | 680.0 | 12,593 | 41,819 | 492,507 | Unfiltered wide CERI score table |
| 3 | `0aabd17ec382` | 7,494 | 607.1 | 81.0 | 177.4 | 7,494 | Winner evidence watermark |
| 4 | `327dc44bd5f2` | 768,292 | 595.3 | 0.775 | 1.667 | 768,292 | Worker heartbeat update |
| 5 | `6695c6fd1b39` | 301,631 | 560.9 | 1.859 | 3.450 | 2 | Abandoned-job recovery check |
| 6 | `ad20d22c226d` | 301,777 | 542.8 | 1.799 | 3.075 | 603,554 | SEC lifecycle state check |
| 7 | `c13437921eeb` | 301,629 | 418.7 | 1.388 | 2.256 | 301,622 | Winner scheduling check |
| 8 | `29b86bb023f7` | 301,596 | 397.9 | 1.319 | 2.344 | 0 | Stale-job recovery check |
| 9 | `d6898f0d2f93` | 349,684 | 386.1 | 1.104 | 2.796 | 349,684 | Job heartbeat/lease update |
| 10 | `b8557bbba800` | 301,579 | 337.9 | 1.120 | 1.593 | 6,561 | Ready-job claim group |
| 11 | `27aad416c525` | 380,257 | 330.5 | 0.869 | 1.719 | 380,257 | Job entity heartbeat/cancel load |
| 12 | `8c8d01add0ec` | 301,614 | 326.5 | 1.082 | 1.583 | 31 | Ready-job claim group |
| 13 | `378099918942` | 248,124 | 289.5 | 1.167 | 2.082 | 248,106 | Supervisor row read |
| 14 | `5fda8f92f221` | 295,017 | 283.1 | 0.960 | 1.347 | 0 | Ready-job claim group |
| 15 | `ea44d10bd11e` | 295,018 | 282.6 | 0.958 | 1.330 | 7,604 | Ready-job claim group |
| 16 | `b4554f5978c1` | 128,103 | 192.4 | 1.502 | 2.525 | 8,015 | Supervisor stale-job fencing |
| 17 | `92633bbbc299` | 220,933 | 185.7 | 0.841 | 1.619 | 187,335 | Provider source-record lookup |
| 18 | `3377c3c259ae` | 380,882 | 167.8 | 0.441 | 0.876 | 380,882 | Cancellation check |
| 19 | `678746779802` | 128,152 | 131.9 | 1.029 | 1.808 | 128,152 | Supervisor heartbeat write |
| 20 | `7fb3dc372193` | 59 | 118.2 | 2,003 | 3,719 | 3,343,417 | Full guidance table |
| 21 | `9fa2476031bc` | 207,173 | 102.7 | 0.496 | 1.015 | 207,173 | Ingestion-run entity lookup |
| 22 | `0bb68ba6178a` | 6,065 | 95.1 | 15.7 | 31.6 | 312,881 | Terminal-stage lookup |
| 23 | `e200131a88ad` | 6,218 | 91.6 | 14.7 | 21.9 | 9,705,104 | Capture price bars per ticker |
| 24 | `aa2ada521993` | 3,110 | 91.2 | 29.3 | 98.1 | 44,265 | Capture score snapshots per company |
| 25 | `bde9aef2c98e` | 14,954 | 82.3 | 5.503 | 8.081 | 14,954 | Winner maturation backlog aggregate |
| 26 | `9f2bb537843f` | 16 | 77.8 | 4,862 | 18,121 | 479,446 | Winner diagnostic funnel full history |
| 27 | `7213f5be6937` | 38,175 | 75.2 | 1.970 | 4.978 | 38,175 | Job metadata update |
| 28 | `05910ec4da7f` | 3,316 | 73.7 | 22.2 | 32.7 | 79,584 | Revision-feature UPSERT |
| 29 | `e2ef0f9194c8` | 65 | 67.3 | 1,035 | 1,812 | 5,092,886 | Feature price-bar preload |
| 30 | `9ad420eb1589` | 16,153 | 62.7 | 3.882 | 6.314 | 565,355 | Repeated schema/catalog inspection |

The highest mean/p95 group is dominated by unfiltered CERI tables, Winner history/funnel reads, price-bar coverage/source loads, and two run-scoped DELETE/INSERT phases. Existing `run_id` indexes already serve the slow deletes, so those are not missing-index findings.

The complete top-30 tables for cumulative time, call count, mean (minimum five samples), p95, p99 and individual executions are in `swinglens_full_week_rankings_appendix.md`.

### Pareto concentration

| Fingerprints | Share of observed SQL time |
| ---: | ---: |
| Top 1 | 8.07% |
| Top 5 | 29.40% |
| Top 10 | 47.58% |
| Top 20 | 67.72% |
| Top 50 | 82.37% |

The week is moderately concentrated: twenty shapes explain about two-thirds of SQL cost. The top twenty split into two qualitatively different groups: fast but incessant control-plane polling, and rare but extremely large application reads.

## 6. GUI Performance

Only routes with observed summaries are reported as measured. With small samples, p95/p99 often equal the maximum.

| Route | Requests | Median ms | p95 ms | p99 ms | Max ms | SQL p95 ms | Queries p95 | SQL share | Classification |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `GET /ceri/changes` | 14 | 80,679 | 195,832 | 195,832 | 195,832 | 30,229 | 16 | 21.3% | MOSTLY_APPLICATION_BOUND |
| `GET /runs/{run_id}/ceri` | 9 | 79,260 | 241,460 | 241,460 | 241,460 | 32,163 | 709 | 15.2% | MOSTLY_APPLICATION_BOUND |
| `GET /ceri` | 4 | 74,211 | 215,278 | 215,278 | 215,278 | 32,422 | 735 | 18.9% | MOSTLY_APPLICATION_BOUND |
| `GET /runs/{run_id}` | 91 | 1,911 | 6,870 | 7,805 | 7,805 | 3,234 | 23 | 47.6% | MIXED |
| `GET /api/ib-gateway/status` | 150 | 449 | 3,765 | 5,025 | 5,063 | 0 | 0 | 0% | APPLICATION_BOUND/network |
| Pipeline status | 5,991 | 18 | 70 | 279 | 6,267 | 19 | 4 | 24.6% | MOSTLY_APPLICATION_BOUND, normally fast |
| `GET /` | 61 | 833 | 5,477 | 12,455 | 12,455 | 3,081 | 6 | 77.5% | MOSTLY_DB_BOUND |
| `POST /uploads` | 16 | 1,629 | 29,171 | 29,171 | 29,171 | 10,439 | 5 | 27.7% | MIXED/application materialization |
| Winner Probability run | 11 | 2,889 | 35,812 | 35,812 | 35,812 | 18,908 | 1,023 | 47.6% | MIXED N+1 |
| Setup Lifecycle run | 23 | 820 | 13,226 | 16,952 | 16,952 | 3,740 | 20 | 36.2% | MIXED |
| Setup Lifecycle alerts | 33 | 334 | 2,028 | 7,150 | 7,150 | 919 | 8 | 34.5% | MIXED |
| `GET /ceri/operations` | 5 | 4,686 | 5,394 | 5,394 | 5,394 | 4,545 | 42 | 85.8% | MOSTLY_DB_BOUND |
| Pipeline detail | 18 | 115 | 7,369 | 7,369 | 7,369 | 2,509 | 10 | 30.9% | MIXED |
| Start pipeline | 17 | 610 | 1,843 | 1,843 | 1,843 | 888 | 24 | 31.1% | MIXED |
| Chart data API | 5 | 830 | 2,498 | 2,498 | 2,498 | 403 | 9 | 23.0% | MOSTLY_APPLICATION_BOUND |
| `GET /market-regime` | 17 | 87 | 2,412 | 2,412 | 2,412 | 507 | 2 | 16.3% | MOSTLY_APPLICATION_BOUND |
| `GET /runs` | 4 | 165 | 331 | 331 | 331 | 144 | 2 | 39.5% | INSUFFICIENT EVIDENCE |

Screen coverage:

- Run detail: measured above. Rankings/technicals/fundamentals were embedded in it, but no independent page route had meaningful samples. Ranking export had only two requests (median 91 ms): **INSUFFICIENT EVIDENCE**.
- CERI dashboard, run, changes and operations: measured. CERI alert and ticker-detail routes: **INSUFFICIENT EVIDENCE**.
- Setup Lifecycle run and alerts: measured. Standalone lifecycle dashboard had three samples; lifecycle changes/operations had zero/one: **INSUFFICIENT EVIDENCE**.
- Winner Probability run: measured. Detail and export each had one sample and 1,023–1,034 queries: evidence of N+1, not a stable latency distribution.
- Market regime: measured. `/ib` had one 47 ms shell request; market-intelligence and other IB pages: **INSUFFICIENT EVIDENCE**. The IB gateway status API is application/network-bound.
- Ops/status: pipeline status has ample samples; readiness, CERI Operations and supervisor-related status are reported. Other ops pages lack evidence.

## 7. CERI Pages

### Run-page fix verification

For `GET /runs/{run_id}/ceri`:

| Measure | Current |
| --- | ---: |
| Requests | 9 |
| Unfiltered score reads | **0** |
| Scoped score reads | 18 |
| Scoped score SQL time | 31.06 s |
| Scoped rows returned | 35,236 |
| Wall median / p95 / max | 79.26 s / 241.46 s / 241.46 s |
| SQL median / p95 / max | 10.98 s / 32.16 s / 32.16 s |
| SQL count median / p95 / max | 705 / 709 / 709 |
| Approx. non-SQL median / max | 69.88 s / 209.30 s |

The earlier maxima were 213.6 s wall, 48.2 s SQL and 655 statements. Current SQL maximum is lower, while wall maximum and query count are higher. Samples/runs are not paired, so no exact speedup claim is justified.

The code now scopes `_filtered_snapshots()` by `run_id`/ticker and keeps an instance snapshot cache. The run route uses one service instance for the run and change panel. There is one `run_id` load and a separate referenced-ID load; salted digests confirm these are not the same logical parameter set.

### Remaining N+1

The page displays 50 companies and executes roughly 14 queries/company plus a few page-level loads:

- `_snapshot_freshness`: source IDs and completed ingestion runs; the ingestion-run shape executes 50 times/request and can consume 12.2 s/request.
- `_evidence_diagnostics`: repeats the same source-ID read, then company estimates, earnings, guidance, catalysts, catalyst revisions, revision features, derived/price-response evidence.
- `_rows_for_company`: common shapes execute 50 or 100 times/request; revision/guidance families alone account for hundreds of calls.
- Whole-table change-panel loads: the nine requests loaded 509,980 guidance rows and 1,321,950 revision-feature rows.

Exact-digest evidence separates different-company N+1 calls from redundant same-parameter calls. The source-ID query is often executed twice for the same snapshot—once by freshness and once by diagnostics. This is a safe request-scoped reuse opportunity, but the larger win is one batched preload for all visible company IDs and source IDs.

This should be the next CERI run-page optimization. Expected outcome is a fall from ~705 queries to a low double-digit count and removal of most of the 70–209 s Python/ORM portion. The change-panel pushdown should happen with it or immediately before it.

### CERI Changes

The whole-table pattern remains. Across 14 requests, the page built both `changes()` and `alerts()` independently, producing two reads of most datasets. For scores, the pair consists of one unfiltered load plus one referenced-ID load rather than two identical unfiltered statements:

| Table | Calls | SQL time | Returned rows |
| --- | ---: | ---: | ---: |
| `ceri_change_events` | 28 | 1.49 s | 271,388 |
| `ceri_score_snapshots` | 28 | 182.28 s | 179,766 |
| `ceri_catalyst_event_revisions` | 28 | 9.06 s | 436,294 |
| `ceri_revision_features` | 28 | 32.41 s | 4,162,248 |
| `ceri_catalyst_events` | 28 | 0.91 s | 426,802 |
| `ceri_guidance_events` | 28 | 47.08 s | 1,586,634 |

The route then filters, creates payloads, sorts, counts and slices in Python. Median SQL is 16.6 s but median non-SQL work is about 59.5 s. Moving predicates, count, sort and `LIMIT/OFFSET` to SQL—and loading only related rows for the 50 visible changes—should plausibly remove **70–90%** of wall time. This is an engineering estimate, not a measured speedup.

### CERI Operations

Aggregate reuse worked: there are no exact same-fingerprint/same-digest duplicate aggregates. Nevertheless every sampled request has 42 queries, versus the earlier maximum of 40, and takes 4.69 s median with 4.34 s median SQL.

- Conflict query: one/request, 1.33 s total across five.
- Dataset freshness: nine parameterized calls/request (45 total); these are same shapes with different dataset/provider parameters, not exact duplicates.
- Stale source counts: four/request and 7.58 s total across the route.
- Pool wait is negligible; transaction stays open for nearly the whole request.

The next step is a single grouped freshness/staleness aggregate (or small cached operations snapshot), not more request-local reuse.

## 8. Background Jobs

Worker counts and SQL are lower bounds. Wall distributions come from retained summaries and may also miss jobs whose summaries were dropped.

| Job type | Executions | Median wall | p95 | Max | Median SQL | SQL share | Median calls/job |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `FULL_PIPELINE` | 28 | 2.68 s | 38.25 m | 47.30 m | 0.72 s | 6.7% | 49 |
| `CERI_PROVIDER_INGEST_BATCH` | 612 | 32.91 s | 61.73 s | 217.48 s | 0.87 s | 6.5% | 1,301 |
| `WINNER_OUTCOME_MATURATION` | 7,476 | 0.175 s | 0.364 s | 234.18 s | 0.103 s | 38.2% | 27 |
| `CERI_FEATURE_BATCH` | 73 summaries / 81 DB jobs | 28.54 s | 72.24 s | 73.71 s | 3.16 s | 10.9% | 817 |
| `CERI_CHANGE_DETECTION` | 16 | 100.29 s | 168.25 s | 168.25 s | 24.17 s | 34.7% | 4,856 |
| `CERI_CAPTURE_RUN` | 16 | 69.72 s | 167.53 s | 167.53 s | 16.87 s | 23.8% | 2,540 |
| `CERI_NORMALIZE_BATCH` | 5,888 | 27.7 ms | 137.7 ms | 29.75 s | 13.2 ms | 19.4% | 1 |
| `SEC_READINESS_REPAIR` | 14 | 42.18 s | 144.45 s | 144.45 s | 3.17 s | 6.6% | 4,250 |
| `WINNER_COHORT_REFRESH` | 15 | 9.90 s | 37.38 s | 37.38 s | 3.27 s | 30.8% | 1,516 |
| `CERI_ALERT_REBUILD` | 16 | 0.477 s | 71.94 s | 71.94 s | 0.086 s | 18.1% | 6 |
| `CERI_RUN_FINALIZE` | 13 | 42.8 ms | 144 ms | 144 ms | 22.6 ms | 59.1% | 9 |

No summaries were found for `WINNER_LATEST_RESCORE`, lifecycle-specific jobs or market-data jobs. Their status is **INSUFFICIENT EVIDENCE**, not fast.

### Long-outlier explanation

- `FULL_PIPELINE`: the prior 181.6-minute maximum did not recur; current max is 47.3 minutes. Its max summary contained 101,866 SQL calls and 189.0 s SQL, only 6.7% overall. `pipeline_execution` accounts for essentially all wall time, so orchestration/child waiting and application work dominate. Do not add child-job wall to pipeline wall when measuring system demand.
- `CERI_PROVIDER_INGEST_BATCH`: the 191.8-minute outlier did not recur; max is 217.5 s. `provider_network_and_persistence` phases total 18,971 s against 19,220 s handler wall. SQL is only 6.5% and pool waits are tiny; provider latency, rate pacing, parsing and persistence around network responses dominate. Current phase instrumentation combines network and persistence, so it cannot split those two exactly.
- `CERI_NORMALIZE_BATCH`: the prior 43.3 cumulative hours did not recur. Retained summaries total 20.0 minutes; median 27.7 ms and max 29.7 s. The earlier number was not representative of the current implementation.
- `WINNER_OUTCOME_MATURATION`: the prior 496 s outlier did not recur; max is 234.2 s. Normal p95 is 364 ms. The outlier made 57,664 calls; repeated `price_bars WHERE id=?` ORM loads dominate, so it is an application N+1 rather than one slow SQL query.
- `CERI_CHANGE_DETECTION` and `CERI_CAPTURE_RUN` are more consistently expensive than normalize. Change detection still loads all scores and executes thousands of per-event reads. Capture performs per-company score reads and per-ticker price-bar reads (up to 804 repeated calls in one job).

## 9. CERI Feature Rebuild

The authoritative database job results contain 81 completed `CERI_FEATURE_BATCH` jobs; only 73 corresponding summaries survived, further demonstrating worker telemetry loss.

| Measure | Result |
| --- | ---: |
| Implementation | `batch-prefetch-v1` for all 81 |
| Tickers / rebuilt / skipped unchanged | 3,673 / 3,380 / 293 |
| Failed | 0 |
| Features computed | 79,656 |
| Inserted / updated / deduplicated | 83,927 / 917 / 8,126 |
| Wall total / median / p95 / max | 46.97 m / 29.12 s / 69.46 s / 73.38 s |
| SQL total / median / p95 / max | 5.16 m / 3.18 s / 8.09 s / 11.35 s |
| SQL share | 10.97% |
| SQL calls total / median / p95 | 61,581 / 823 / 866 |
| SELECT / INSERT / UPDATE / DELETE / other | 15,826 / 17,132 / 21,863 / 0 / 6,760 |
| ORM flushes | 21,448; median 288/job |
| Pool wait | 39.6 ms total; 0.818 ms p95/job |

Phase totals: context preload 9.57 m (median 5.12 s, p95 22.44 s); persistence 4.63 m (median 2.82 s); revision compute 83.45 s; price-response compute 43.18 s; catalyst 9.24 s; all other computes under one second total each.

Rows preloaded over the week include 5.77 M price bars, 939 K source records, 624 K estimates, 184 K guidance events, 88 K catalyst revisions, 86 K catalysts and 44 K earnings actuals. These are large, but they are batch loads rather than repeated per-ticker loads.

### Regression check

| Historical anti-pattern | Status | Evidence |
| --- | --- | --- |
| Full revision table per feature | **RESOLVED** | One batched preload; not repeated per feature. |
| Full derived table per feature | **RESOLVED** | Derived context loaded once/batch. |
| Estimate query per revision window | **RESOLVED** | Estimate preload once/batch; no per-window query family. |
| Evidence tables reloaded per ticker | **RESOLVED** | Job results report 13 application preload SELECTs/batch. |
| Benchmark bars queried per ticker | **RESOLVED** | Batched price preload; no benchmark-per-ticker family. |

Remaining measured costs are revision UPSERT 73.8 s, price preload 67.3 s, estimate preload 23.2 s, source/other context preload about 11.6 s, metadata updates 10.5 s and cancellation checks 5.5 s. The high flush count remains worth reducing. Feature rebuild is a **medium** priority behind CERI GUI/change/capture, control-plane traffic, Winner N+1 and price coverage.

## 10. Worker/Supervisor Control Plane

| Family | Calls | SQL time s | Writes | Notes |
| --- | ---: | ---: | ---: | --- |
| Worker registration/heartbeat row read | 910,754 | 925.0 | 0 | Includes 301,630 registrations and 475,087 heartbeat reads |
| Worker register/heartbeat update | 768,292 | 595.3 | 768,292 | Re-register/update occurs on the two-second loop |
| Stale + abandoned recovery | 603,227 | 958.8 | 0 | Both executed every loop even when nothing recovered |
| Winner scheduling check | 301,629 | 418.7 | 0 | Jobless control-loop calls |
| SEC release check | 301,777 | 542.8 | 0 | Usually unchanged state |
| Four claim groups | 1,193,228 | 1,230.0 | 0 | Mostly empty claims |
| Supervisor registration/heartbeat/fencing | about 504,379 | 613.8 | 128,152 | Unchanged-state work |

Identifiable total: **at least 4.58 M calls / 5,284 s SQL / ~896 K writes**. This excludes cancellation/lease traffic that is legitimate while jobs run and excludes dropped worker records.

The two-second cadence produces a regular baseline even in hours without batch inserts. Job attribution reinforces the distinction: the recovery, scheduling and claim families have no job context and are control-loop work; active jobs add heartbeats and cancellation checks on top.

### Adaptive idle-backoff recommendation

Implement a bounded state machine, not an unbounded sleep:

1. Remain at 1–2 s immediately after startup, a successful claim, or recent queue activity.
2. After consecutive empty claims, back off through 2, 4 and 8 s, capped at 8–10 s.
3. Keep worker/supervisor heartbeat on its own correctness interval (for example 10 s) and never let backoff approach the stale/fencing timeout.
4. Reset immediately on a successful claim; optionally add PostgreSQL `LISTEN/NOTIFY` later as a wake hint, while retaining polling for durability.
5. Run recovery, Winner scheduling and SEC release checks on separate slower cadences (15–60 s) rather than every claim loop.

Expected idle call reduction is **70–90%**. A 10 s maximum adds at most roughly 8 s of idle-start latency without notification; an 8 s cap or wake hint reduces that risk. Heartbeat correctness is preserved by independent timing, and polling remains the durable fallback.

## 11. PostgreSQL pg_stat_statements

`pg_stat_statements` is enabled but reset at **2026-08-30T22:07:42.544274+02:00**, so it covers only the final ~4.5 days and cannot be called a full-week aggregate.

| Query | Calls | Total s | Mean ms | Rows | Shared hit/read | Temp read/write blocks | WAL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Unfiltered CERI scores | 26 | 334.5 | 12,866 | 259,483 | 2.71 M / 304,632 | 0 / 0 | 691 KB |
| SEC release state | 147,044 | 112.6 | 0.766 | 294,088 | 441 K / 125 | 0 / 0 | 0 |
| Job lease heartbeat update | 169,354 | 88.5 | 0.522 | 169,354 | 34.8 M / 23,546 | 0 / 0 | 483.5 MB |
| Activity sampler itself | 315,713 | 73.0 | 0.231 | 4,997 | 4,634 / 0 | 0 / 0 | 0 |
| Full guidance table | 28 | 47.5 | 1,698 | 1.59 M | 312 K / 329,934 | 0 / 0 | 0 |
| Winner diagnostic/history join | 8 | 40.7 | 5,085 | 254,060 | 1.26 M / 95,535 | 68,490 / 68,616 | 13.6 MB |
| Worker heartbeat update | 376,576 | 39.7 | 0.105 | 376,576 | 4.08 M / 98 | 0 / 0 | 89.0 MB |
| Wide price-bar preload | 38 | 36.1 | 950 | 2.65 M | 9,891 / 106,068 | 47,323 / 47,415 | 0 |
| Price bars by ticker/window | 2,808 | 36.0 | 12.8 | 4.39 M | 188 K / 77,733 | 0 / 0 | 0 |
| Scoped CERI score reads | 1,404 | 35.5 | 25.3 | 22,070 | 248 K / 53,179 | 0 / 0 | 1.9 KB |
| `DELETE fundamental_scores WHERE run_id` | 8 | 34.4 | 4,302 | 1,687 | 9.51 M / 1.98 M | 0 / 0 | 1.84 MB |
| Winner snapshot/history materialization | 20 | 32.5 | 1,627 | 177,180 | 1.42 M / 92,637 | 76,973 / 77,107 | 2.14 MB |

The application and pgss rankings agree. Substantial un-attributed work consists of the monitor’s own activity sampler (315 K calls/73 s) and PostgreSQL internal FK checks (for example 1.19 M key-share checks/12.7 s). No unexplained external workload outranks the application’s main costs.

Database counters have no `stats_reset` timestamp and therefore are server-lifetime values: 211 temp files/1.93 GB, zero deadlocks, zero conflicts. They must not be attributed to the audit week. `track_io_timing` is off, so zero block timing is not evidence of zero I/O.

## 12. Execution Plans

There are 497 retained auto_explain plans for 77 query IDs. Because `auto_explain.log_analyze=off`, they contain estimated rather than actual rows and no buffer detail. Across plan trees: 401 sequential scans, 262 sorts, 201 gathers/339 parallel nodes, 6,669 index scans, 188 bitmap heap scans, 136 hash joins and 39 nested loops.

Important plan findings:

- Unfiltered CERI scores: direct sequential scan of 8–10 K very wide rows (estimated row width ~1.8 KB). This is the correct plan for an incorrect query shape; an index would not help.
- Full guidance/revision loads: sequential scans returning tens/hundreds of thousands of ORM rows. Again, predicates and column narrowing come first.
- Price coverage: `Finalize GroupAggregate` above parallel seq or bitmap heap scan plus sort; estimated population around 522 K bars for large ticker lists. Existing `(ticker, bar_date)` and unique `(ticker, bar_date, timeframe, what_to_show)` do not align ideally with timeframe-first grouping.
- Winner history/funnel: parallel scans/hash joins plus sort; pgss confirms temp-block use. It should be scoped before considering more indexes.
- Setup Lifecycle source loading: bitmap scan followed by large ticker/date sort/window; individual executions reached 23.8 s.

No retained plan proves a nested-loop explosion. Cardinality accuracy cannot be judged authoritatively without actual rows; no `EXPLAIN ANALYZE` was run.

## 13. Locks / Transactions / Pool

### Locks and deadlocks

The sampler saw 21 blocking samples involving 11 blocked PIDs. Maximum observed query age was 4.145 s. All blocked statements were one of:

- job heartbeat/lease update (`d6898f...`),
- worker heartbeat/update (`327dc4...` / `38a55a...`), or
- supervisor heartbeat (`678746...`).

No user route query was blocked, and no slow page is lock-bound. The sampler’s non-client waits also include 23 `Lock:transactionid` samples, brief WAL/I/O events, autovacuum delay and parallel-worker IPC. PostgreSQL reports zero deadlocks, but its database-stat reset is unknown; logs contain no deadlock event in the marked interval.

### Pool

There were 109 checkout-wait events above 5 ms, totaling 11.07 s; median 49.8 ms, p95 223.9 ms, max 1.927 s. There were **zero timeouts and zero overflow events**. The max belonged to a Setup Lifecycle request. Homepage had five events totaling 878 ms; CERI run-page pool wait was effectively zero. Pool wait does not explain the application-time gaps.

### Transactions

There were 353 long-transaction events totaling 3.74 h: median 10.19 s, p95 165.63 s, max 460.90 s. Top origins include 37 `FULL_PIPELINE` events (max 460.9 s), all 14 CERI Changes requests, all nine CERI run requests and all four CERI dashboard requests.

The activity sampler repeatedly observed two long idle-in-transaction episodes around technical-feature artifact reads/updates, reaching 459 s and 352 s. Repeated one-second samples are not separate incidents. Provider ingest transaction p95 (56.2 s) approaches its wall p95 (61.7 s), suggesting network/pacing and persistence occur within one session transaction. Feature jobs similarly hold transactions through most batch work. Narrowing transaction scope around external calls is justified after code-level confirmation.

Long transactions are not the primary cause of the measured latency, but they retain snapshots/locks longer, increase vacuum pressure and amplify heartbeat collisions. Treat this as a high operational-risk correction.

## 14. Background-vs-GUI Contention

No consistent worker-caused GUI slowdown is demonstrated:

| Route | Heavy-job overlap median/p95 | No heavy-job overlap median/p95 | Interpretation |
| --- | ---: | ---: | --- |
| CERI run | 84.5 / 241.5 s (n=4) | 56.6 / 225.1 s (n=5) | Both are unacceptable; intrinsic query shape dominates |
| CERI Changes | 94.7 / 168.7 s (n=3) | 80.7 / 195.8 s (n=11) | Slow while idle too |
| Homepage | 0.535 / 6.14 s (n=21) | 0.894 / 3.02 s (n=40) | Tail is noisier during work, but samples/outliers are insufficient for causality |
| Run detail | 1.63 / 6.96 s (n=41) | 2.04 / 6.42 s (n=50) | No degradation |
| Setup Lifecycle run | 0.568 / 16.95 s (n=10) | 0.820 / 13.23 s (n=13) | Some tail variance, not supported by pool/lock evidence |

The slowest CERI samples overlap both idle and heavy periods. No pool saturation or route blocking accompanies them. Background I/O may affect some tails, but the current evidence does not support scheduling jobs away from GUI use as a primary fix.

## 15. Table and Index Hotspots

### Table-level activity

Durations are attributed to every table named in multi-table SQL, so table totals are not additive.

| Table | Calls | SQL s | Reads | Writes | Primary source |
| --- | ---: | ---: | ---: | ---: | --- |
| `background_jobs` | 3,494,628 | 3,994 | 3,017,512 | 477,116 | worker control, provider, pipeline |
| `background_workers` | 1,686,474 | 1,566 | 910,851 | 775,623 | registration/heartbeat |
| `winner_forward_outcomes` | 132,944 | 979 | 86,987 | 45,957 | Winner maturation/pipeline |
| `ceri_score_snapshots` | 9,512 | 897 | 3,364 | 6,148 | capture/change/UI |
| `winner_target_stop_outcomes` | 19,195 | 711 | 13,554 | 5,641 | Winner maturation |
| `price_bars` | 121,056 | 623 | 106,132 | 14,924 | Winner, pipeline, coverage |
| `ceri_sec_processor_releases` | 301,811 | 543 | 301,811 | 0 | idle SEC checks |
| `background_supervisors` | 376,304 | 422 | 248,144 | 128,160 | supervisor control |
| `ceri_source_records` | 312,050 | 383 | 278,451 | 33,599 | provider/CERI DTOs |
| `ceri_guidance_events` | 12,124 | 156 | 11,435 | 689 | capture/normalize/UI |
| `ceri_revision_features` | 4,793 | 142 | 1,477 | 3,316 | feature upsert/UI |
| `setup_signal_snapshots` | 32,435 | 135 | 15,262 | 17,173 | full pipeline |
| `ceri_estimate_snapshots` | 17,511 | 80 | 15,047 | 2,464 | normalize/capture/UI |

Large physical tables include `ceri_source_records` 2.45 GB, `winner_estimate_evidence_members` 1.59 GB, `price_bars` 740 MB, estimates 239 MB, rankings 216 MB, setup signals 185 MB, revision features 180 MB and score snapshots 145 MB.

### Large-result/application-volume findings

One `ceri_revision_features` read returns up to 186,174 wide entities in 0.5–3.8 s SQL, yet the containing page takes 55–196 s. That gap is driver transfer, ORM construction, dict building, filtering and sorting—not PostgreSQL CPU. The same applies to guidance (roughly 56 K rows/load) and wide score JSON rows (~1.8 KB estimated width). These are application data-volume defects.

Telemetry-directed source review confirms:

- `changes()` calls unpredicated `_load()` and applies filters/paging in Python.
- `_score_snapshot_payload()` calls helpers within the visible-snapshot loop.
- `_snapshot_freshness()` and `_evidence_diagnostics()` independently load the same source IDs.
- `_rows_for_company()` executes one ORM query per model/company.
- `_bar_stats()` aggregates live `price_bars` for every ticker on each homepage/run-detail request.
- feature batch persistence still flushes hundreds of times/job.

### Index recommendations

No index should be added for the unfiltered CERI reads, DTO N+1 or Python pagination. Fix those shapes first.

| Table | Candidate definition | Fingerprint/use | Expected improvement | Write cost / condition |
| --- | --- | --- | --- | --- |
| `price_bars` | `CREATE INDEX CONCURRENTLY ... ON price_bars (timeframe, ticker, what_to_show, bar_date)` | `_bar_stats` coverage families | Better index-only grouping and fewer heap reads; likely 2–5x if a maintained summary is deferred | Large additional index on a write-heavy 740 MB table; validate size/plan first. Summary table remains preferred. |
| `winner_training_eligibility_decisions` | `(target_outcome_definition_id, id DESC)` | watermark `0aabd17...` subquery | Avoid target-only scan for `max(id)` | Moderate write/storage cost; only after reducing 7,494 repeated calls. |
| `winner_training_outcome_replays` | `(target_outcome_definition_id, id DESC)` | same watermark | Same | Same. |

Existing indexes already cover CERI `(run_id,ticker)`, CERI company/session, job queue claims, Winner estimate identities and the slow run-scoped deletes. There is no evidence for a broad index campaign.

## 16. Monitor Health

Observed volume is about **1.90 decimal GB/day (1.77 GiB/day)** and **11.6 records/s** over the elapsed envelope; raw SQL averages 9.86 records/s. Worker logs are 90% of retained bytes.

| Metric | Result |
| --- | ---: |
| Retained bytes/files | 16.94 GB / 322 |
| Worker retained bytes/files | 15.22 GB / 217 |
| Dropped events | 547,009, worker only |
| Queue high-water | 10,000/10,000 |
| Queue oldest age max | 434.7 s |
| Writer latency max | 459.4 s worker; ~10 s web/supervisor |
| Writer errors | 7 worker |
| Parse/corrupt lines | 3 |
| Activity-sampler DB work after pgss reset | 315,713 calls / 73.0 s |
| CPU telemetry | Not available |

Classification: **SIGNIFICANT**. Database execution overhead is small relative to application SQL, but disk volume, queue saturation, dropped evidence and writer stalls are operationally significant.

### Permanent configuration

- Keep request and job summaries, job phases, deployment/version metadata, parameter digests, pool timing, long-transaction timing and monitor-health records permanently.
- Keep every query >=100 ms and full stack at >=500 ms; retain 250–500 ms stack threshold temporarily while CERI/Winner work is underway.
- Do not retain every fast SQL event for 14 days. Sample fast SQL at 1–5% or aggregate it per fingerprint/minute; keep raw fast events 48–72 h.
- Keep `pg_stat_statements` permanently and record its reset timestamp/snapshot daily. Keep slow-only auto_explain at 750–1,000 ms, `ANALYZE` off, optionally sampled.
- Change activity sampling from 1 s continuous to 5–10 s steady-state with a 1 s incident burst after a wait/long transaction is detected.
- Compress closed files (zstd/gzip). Retain summaries/slow events 14–30 days, full stacks 7–14 days and raw fast events 2–3 days.
- Allocate at least 30 GB for the compressed production monitor, with a **global** safety cap plus role reservations. At current uncompressed rate, the worker’s 16 GB cap cannot meet 14 days.
- Fix writer batching/backpressure and prioritize summaries/slow events over fast-event samples before the next certification window.

## 17. Top 20 Problems

| Rank | Severity | Problem | Evidence | Impact | Root cause | Recommended fix | Expected impact | Risk |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | CRITICAL | Monitor drops invalidate full-week completeness | 547 K drops, queue full, 7 errors | Biased worker/job evidence | Writer throughput < event rate | Priority queues, batching, fast-event sampling | Trustworthy audit and lower disk | Low–medium |
| 2 | CRITICAL | CERI Changes full-table Python pagination | 80.7 s median; 195.8 s max | Largest cumulative wait | Missing SQL predicates/paging | SQL count/filter/order/limit; related-row fetch | 70–90% route reduction estimate | Medium |
| 3 | HIGH | CERI run/dashboard evidence N+1 | 703–735 calls; 74–79 s medians | Worst interactive experience | Per-company DTO helpers | Batch preload/map visible companies | Low double-digit calls; large wall reduction | Medium |
| 4 | HIGH | Duplicate large CERI loads within Changes page | Same full tables loaded by changes + alerts | Doubles transfer/materialization | Independent service calls | Shared request context or composed SQL read model | Roughly halves affected load before pushdown | Low–medium |
| 5 | HIGH | Worker control-plane polling amplification | >=4.08 M calls, ~1.30 h SQL | DB/WAL/noise/disk | All checks on 2 s loop | Adaptive idle cadence; split schedules | 70–90% idle reduction | Low |
| 6 | HIGH | Unfiltered wide CERI scores outside run page | 54 calls/680 s; max 46.6 s | Slow Changes/jobs | Missing predicate/read model | Scope by run/change IDs; narrow columns | Removes largest business SQL | Medium |
| 7 | HIGH | Winner Probability DTO N+1 | up to 1,023 calls/request | 35.8 s page tail | Per-prediction estimates/results | Batch fetch by prediction IDs | Large page/export reduction | Medium |
| 8 | HIGH | Live price coverage aggregation | 150 calls/128.5 s; homepage 77.5% DB | 5.48 s homepage p95 | Recompute over bars each page | Maintained coverage summary | Sub-second page potential | Medium |
| 9 | HIGH | Long transaction scope | 353; p95 165.6 s; max 460.9 s | Vacuum/lock/recovery risk | Session spans Python/network | Commit/read-copy before external work; short writes | Lower risk/contention | Medium–high |
| 10 | HIGH | CERI Change Detection full loads + per-event work | 100.3 s median; 4,856 calls | Pipeline latency/resource use | Full scores and N+1 comparisons | Incremental run-pair/change-set processing | Major batch reduction | Medium–high |
| 11 | HIGH | CERI Capture per-company/ticker N+1 | 69.7 s median; 2,540 calls | Pipeline latency | Score and price loads in loops | Batch scores/bars for run population | Large batch reduction | Medium |
| 12 | MEDIUM | Provider ingest wall dominated outside SQL | 5.34 h, SQL 6.5% | Largest standalone workload | Network/pacing/parse+persistence | Split phase telemetry; bounded concurrency/batched persistence | Better throughput without DB tuning | Medium |
| 13 | MEDIUM | Winner evidence watermark repeated | 7,494 calls/607 s | Job SQL load | Recomputed per maturation | Cache once per batch/cycle; then index | Near-eliminate 10 min SQL | Low |
| 14 | MEDIUM | Supervisor unchanged-state polling | ~504 K calls/614 s | Avoidable DB/write noise | Fixed cadence | Adaptive/split cadence | 70–90% idle reduction | Low |
| 15 | MEDIUM | Winner diagnostic/history whole loads | 16 calls/77.8 s, temp blocks | Slow cohort/pipeline diagnostics | Unscoped history joins | Predicate/page/project columns | Lower memory/temp/I/O | Medium |
| 16 | MEDIUM | CERI Operations grouped status not consolidated | 42 calls; 4.69 s median, 86% SQL | Slow ops screen | Many per-dataset aggregates | One grouped query or cached snapshot | 2–4 s improvement | Low–medium |
| 17 | MEDIUM | Setup Lifecycle price/history tails | price loads to 23.8 s; route max 17.0 s | Intermittent UI/pipeline delays | Large window/sort loads | Batch/project/date-bound reads | Tail reduction | Medium |
| 18 | MEDIUM | Feature persistence flush density | 21,448 flushes; 288 median/job | Batch overhead | Tight-loop ORM flushes | Chunked Core upsert/flush | Moderate feature reduction | Medium |
| 19 | LOW | Repeated schema/catalog introspection | 16,153 calls/62.7 s | Noise and job overhead | Capability checks repeated | Cache per process/schema version | Remove ~1 min SQL | Low |
| 20 | LOW | IB status API network tail | p95 3.76 s, no SQL | UI polling delays | External handshake | Short TTL async health cache | Better responsiveness | Low–medium |

## 18. Top 5 Recommended Changes

The monitor writer correction is **Phase 0 prerequisite work**, but the five largest product-performance changes are:

| Rank | Change | Evidence | Expected impact | Complexity | Risk |
| ---: | --- | --- | --- | --- | --- |
| 1 | Push CERI Changes filtering/count/sort/pagination and related-row selection into SQL; share change/alert data | 1,287 s cumulative wait; 80.7 s median; millions of rows materialized | 70–90% route reduction estimate; removes duplicate loads | Medium | Medium |
| 2 | Batch CERI visible-company evidence/freshness/diagnostic reads | 703–735 calls and 74–79 s median; 85% outside SQL | Reduce to low double-digit queries and remove most Python/ORM wait | Medium | Medium |
| 3 | Add adaptive worker/supervisor idle backoff and separate check cadences | >=4.58 M calls/1.47 h SQL/~896 K writes | 70–90% idle control reduction, lower WAL/log volume | Low–medium | Low |
| 4 | Batch Winner Probability run/detail/export lookups and reuse watermark per cycle | up to 1,034 calls/request; watermark 607 s/week | Large Winner UI and maturation reduction | Medium | Medium |
| 5 | Maintain a price-coverage summary/read model | 150 calls/128.5 s; homepage p95 5.48 s; large scan/sort plans | Fast homepage/run-detail coverage independent of bar-table growth | Medium | Medium |

### Quick wins

- Repair monitor writer priority/backpressure and sample fast SQL.
- Reuse the source-ID result between CERI freshness and diagnostics.
- Cache Winner watermark once per scheduler/maturation batch.
- Cache schema/capability checks per process.
- Separate SEC, recovery and Winner scheduling checks from the two-second claim loop.

### Medium changes

- CERI visible-company batched context.
- CERI Changes SQL pushdown and shared changes/alerts context.
- Winner Probability batched DTO assembly.
- Batch CERI Capture score/price access.
- Consolidate CERI Operations grouped aggregates.

### Architectural changes

- Maintained price-coverage summary/read model.
- Incremental CERI change read model keyed by run pair/company.
- Event-assisted worker wakeup with polling fallback.
- Materialized/cached operations health snapshots.

## 19. Implementation Roadmap

### Phase 0 — restore observability

| Item | Benefit | Complexity | Risk | Dependencies |
| --- | --- | --- | --- | --- |
| Prioritize summaries/slow traces; batch writer; sample fast SQL | Stops evidence loss | Medium | Low | None |
| Compress rotation and enforce sustainable global/role quotas | Achieves real retention | Low–medium | Low | Writer format/tooling |
| Persist pgss reset metadata and daily snapshots | Comparable DB intervals | Low | Low | None |

### Phase 1 — largest user-visible wins

| Item | Benefit | Complexity | Risk | Dependencies |
| --- | --- | --- | --- | --- |
| CERI Changes SQL pushdown/shared context | Removes largest cumulative wait | Medium | Medium | Query parity tests |
| CERI batched page evidence | Removes worst N+1 and app wait | Medium | Medium | DTO contract tests |
| Winner Probability batching | Removes 1,000-query pages | Medium | Medium | Payload parity tests |
| Price coverage summary | Homepage/run-detail tail | Medium | Medium | Refresh correctness contract |

### Phase 2 — background/resource wins

| Item | Benefit | Complexity | Risk | Dependencies |
| --- | --- | --- | --- | --- |
| Adaptive worker/supervisor cadence | Millions fewer calls/writes | Low–medium | Low | Heartbeat/stale timeout tests |
| Batch CERI Capture reads | Shorter pipelines | Medium | Medium | Run consistency tests |
| Cache/index Winner watermark | ~10 min SQL/week observed | Low | Low | Cache invalidation scope |
| Reduce feature flushes | Moderate rebuild improvement | Medium | Medium | Upsert correctness |
| Split provider network/parse/persist telemetry, then tune | Targets actual provider bottleneck | Low then medium | Low–medium | Better phases first |

### Phase 3 — read models and incremental work

| Item | Benefit | Complexity | Risk | Dependencies |
| --- | --- | --- | --- | --- |
| Incremental CERI change detection/read model | Removes full historic scans | High | Medium–high | Phase 1 query semantics |
| CERI operations snapshot | Predictable ops-page cost | Medium | Medium | Refresh/staleness rules |
| Event-assisted queue wakeup | Low idle DB cost with fast claims | High | Medium | Phase 2 bounded polling |

### Phase 4 — PostgreSQL/system tuning only after remeasurement

- Validate the conditional `price_bars` and Winner watermark indexes with safe plans on production-like parameters.
- Turn on `track_io_timing` in a planned change if restart/config policy allows, then measure—not guess—I/O.
- Reassess `shared_buffers` (currently 128 MB), `work_mem` (4 MB) and storage only after full scans/N+1s are removed.
- Repeat a clean seven-day audit with zero drops and daily pgss snapshots.

### Hardware/cloud conclusion

The host is an i7-7500U-class 2-core/4-thread laptop with 16 GB RAM. No historical CPU, memory-pressure or disk-latency telemetry exists, so saturation cannot be proven. PostgreSQL’s 0.799 ms median, absence of pool exhaustion, brief locks, and application-heavy slow pages argue against hardware as the primary limit. More CPU/faster storage would shorten parallel full scans and Python materialization, but it would also make inefficient work merely less slow.

**Would faster hardware materially improve SwingLens after the identified software fixes?** Probably modestly for heavy pipelines and residual scans, but current evidence does not yet justify a cloud/CPU upgrade. Implement the query-shape, batching, read-model and control-plane fixes first; then measure CPU, disk latency/cache hit, memory and p95 under a complete monitor window.

## 20. Appendix

### Evidence artifacts

- `output/swinglens_full_week_audit_raw.json`: parsed aggregate, routes, jobs, tables, deployments, N+1 and exact-digest evidence.
- `output/swinglens_full_week_rankings_appendix.md`: all requested top-30 fingerprint and individual-execution rankings.
- `output/swinglens_web_sql_details.json`: exact web-route CERI/coverage SQL.
- `output/swinglens_feature_job_audit.json`: read-only feature-job database/result reconciliation.
- `output/swinglens_postgres_audit_snapshot.json`: pgss, indexes, relation sizes and server settings.
- `output/swinglens_auto_explain_audit.json`: retained plan extraction.
- `output/swinglens_full_week_audit_digest.json`: route/job contention and drill-down calculations.

### Method and interpretation rules

Canonical fingerprints use normalized SQL. N+1 candidates are repeated fingerprints within one request. Exact duplicates additionally require the same salted parameter digest; digests were compared, never reversed. SQL share is `summed statement duration / request or job wall`; it does not include driver transfer after execute, ORM construction, Python processing, network, sleep or orchestration wait. Transaction totals can overlap nested phases and are not additive to wall time.

Rows are returned rowcounts, not rows scanned. Scan estimates come from auto_explain; actual rows/buffers are unavailable because `ANALYZE`/buffers were off. No `EXPLAIN ANALYZE`, statistics refresh, index creation or configuration change was performed.

### Final diagnosis

The database is spending most of its call volume on worker/supervisor control-plane churn, while users spend most of their time waiting for unbounded CERI data materialization and per-entity application loops. The August run-score scoping and duplicate-load fixes succeeded, and `batch-prefetch-v1` has not regressed. The remaining wins come from changing read shapes and batching, not from indiscriminate indexing or stronger hardware.
