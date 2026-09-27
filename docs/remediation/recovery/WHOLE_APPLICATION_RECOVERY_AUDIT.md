# SwingLens Whole-Application Recovery Audit

Audit date: 2026-09-24  
Scope: read-only diagnosis and isolated testing; no remediation was implemented  
Authoritative live incident: upload/run 163, pipeline 154, 100 requested tickers

## 1. Executive verdict

| Question | Verdict |
|---|---|
| ROOT CAUSE OF TECHNICAL 81-MINUTE RUN IDENTIFIED | **YES** |
| TECHNICAL KERNEL HEALTHY | **YES** — a 100-ticker requested workload (95 calculable at the frozen cutoff) took 72.883 s in the pure sequential kernel |
| TECHNICAL PRODUCTION PATH HEALTHY | **NO** |
| JOB LEASE MODEL HEALTHY | **NO** |
| CANCELLATION MODEL HEALTHY | **NO** |
| DOWNSTREAM STAGES EXERCISED | **YES** — isolated tests plus retained production runs; not by another live canary |
| ADDITIONAL HIDDEN BLOCKERS FOUND | **1** — five newly fetched tickers were invisible at the pipeline cutoff |
| READY FOR REMEDIATION IMPLEMENTATION | **YES** |
| SAFE FOR ANOTHER LIVE CANARY | **NO** |

Technical did not spend 81 minutes calculating indicators. Its numerical work is approximately linear and completes in low minutes. The production path amplifies a universe-wide price-bar lineage manifest into every ticker's score, then reconstructs and validates that same cohort manifest once per score. For this canary, 95 calculable scores each carried 481 source manifests containing about 380,000 bar-state entries: approximately 36.24 million repeated state visits and 3.60 GB of uncompressed duplicate debug JSON. The score INSERTs alone consumed about 342 seconds of PostgreSQL execution and wrote about 6.54 GiB of temporary blocks. The subsequent evidence-validation loop repeatedly selected, locked, materialized, and rehashed price bars until the backend was terminated.

The same long transaction acquired the `background_jobs` row with `FOR UPDATE`. Technical contained no item-level heartbeat or cancellation callback. Consequently the job lease expired while the worker process heartbeat remained fresh, the watchdog skipped the locked job, and the cancellation update blocked behind that lock. This is a coupled correctness/liveness defect, not a telemetry issue.

## 2. Baseline

### Source and schema

| Item | Recorded value |
|---|---|
| Branch | `main` |
| HEAD | `595102d2d6a70a14dd35eff75601141119515c07` |
| Worktree at audit start | clean |
| Upstream divergence | `main...origin/main [ahead 48]`; 48 ahead, 0 behind |
| Repository migration head | `0083_winner_scope_truth` |
| Database migration current | `0083_winner_scope_truth` |
| PostgreSQL | PostgreSQL 18.3, x86-64 Windows build |

The existing artifacts under `docs/remediation/release/` were inspected but not modified. Existing user work was not reset, cleaned, or overwritten.

### Runtime and retained state

- PostgreSQL was running. No SwingLens web or worker Python process was active during the audit.
- Database endpoint: local `127.0.0.1:5432/swinglens`.
- No active background jobs existed at audit time.
- Pipeline 154 remained the failed retained canary; pipeline 152 was a historical `BLOCKED` record, not active work.
- Database inspection was performed in explicit read-only transactions. Targeted tests used their normal isolated fixtures; the PostgreSQL fencing integration test created and dropped its disposable database.

### Effective configuration relevant to the incident

| Setting | Effective value |
|---|---:|
| DB pool size / overflow / timeout | 5 / 10 / 30 s |
| Technical pure boundary | enabled |
| Pure boundary shadow compare flag | enabled |
| Technical process pool | enabled |
| Worker processes / max in flight | 2 / 4 |
| Technical series-version maintenance | enabled |
| Technical artifact cache | `ACTIVE` |
| Technical V5 production | disabled |
| Technical V5 shadow compare | enabled |
| Persist V5 shadow results | enabled |
| Fetch/Technical overlap | disabled |
| Job stale-after / lease period | 900 s |
| Worker heartbeat interval / timeout | 5 s / 30 s |
| Normal / market / long progress timeout | 300 s / 360 s / 1,800 s |
| Watchdog interval | 5 s |
| Winner capture in pipeline | enabled |
| Setup lifecycle pipeline step | enabled |

Environment keys were checked without recording secrets or credential values. No effective statement timeout rescued this transaction. Although the pure-boundary shadow flag is enabled, the process-pool branch does not run the legacy pure comparison; V5 shadow calculation and persistence remain active.

## 3. Technical timeline

All times are retained database timestamps from pipeline 154.

| Event | Time / duration | Durable result |
|---|---:|---|
| Pipeline started | 13:20:03 | admitted once |
| Resume after SEC preparation | 13:37:53 | readiness satisfied |
| `VALIDATING_RUN` | 0.094 s | complete |
| `SCORING_FUNDAMENTALS` | 20.502 s | 100 rows committed |
| `FETCHING_MARKET_DATA` | 438.334 s (7m18s) | 204 planned; 142 requests; 62 justified skips; 0 failures |
| `SCORING_TECHNICALS` began | 13:45:33.044 | job progress remained 0 / unknown total |
| Last job progress/lease renewal | 13:45:33.058 | sequence 416, stage Technical |
| Expected lease expiry | about 14:00:33 | worker process still alive |
| Technical ended | 15:07:07.193 | failed after backend termination/rollback |
| Technical elapsed | **4,894.149 s (81m34s)** | 0 Technical/evidence rows committed |
| Pipeline result | 15:07:07 | `FAILED` at Technical; downstream pending |

Background job 43303 (`FULL_PIPELINE`) ran for about 5,354.5 seconds in total, had `recovery_count=0`, and was eventually `CANCELLED` with `requested_cancel=true`. Its only lease event was `CLAIMED`. The watchdog recorded an unchanged Technical stage, but did not reclaim it.

The final run-163 row counts were: 100 raw, 100 Fundamental, 0 Technical, 0 Combined, 0 rankings, and 0 Technical evidence.

## 4. Technical performance decomposition

### Benchmark method

The deterministic benchmark used run 163's exact ordered 100-ticker source and pipeline 154's frozen market cutoff. It used a read-only SQL transaction and the production loaders, work-item builder, pure scorer, process-pool shape, V4/V5 construction, and identity-building code. Persistence was diagnosed from retained PostgreSQL statistics and the live transaction because reproducing the destructive 3.6 GB write amplification was neither necessary nor safe.

Five requested tickers (`BHE`, `BLLN`, `KLIC`, `LSCC`, `PDFS`) had no bars visible at the frozen cutoff, so 95 entered the mathematical kernel. They are part of the 100-ticker workload and are reported as failed prerequisites, not silently dropped.

### Requested scales

| Requested tickers | Calculable | Input loading | Preparation | Sequential pure kernel | 2-process pool | Pool delta vs sequential | Input SQL / rows | Peak combined RSS |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0.496 s | 0.232 s | 1.590 s | 9.850 s | **+8.260 s slower** | 4 / 1,530 | 307 MB |
| 10 | 10 | 4.380 s | 1.626 s | 14.703 s | 17.527 s | **+2.825 s slower** | 25 / 15,640 | 481 MB |
| 25 | 23 | 9.084 s | 4.097 s | 36.670 s | 19.463 s | **17.206 s faster** | 59 / 35,990 | 545 MB |
| 100 | 95 | 23.737 s | 10.622 s | **72.883 s** | 53.749 s | **19.134 s faster** | 250 / 146,916 | 805 MB |

The core numerical path is approximately linear. The production failure is super-linear: each score contains the entire cohort's lineage and synchronously revalidates it.

### Production-path decomposition

The table separates direct benchmark observations from a reconstruction of the retained incident. CPU is process CPU where it was measurable; process-pool worker CPU is not fully represented by parent CPU. Rows written are zero for read-only benchmarks and zero durably for the rolled-back incident.

| Component | Wall time | CPU time | SQL query count | Rows read | Rows written | Peak memory / materialization | Evidence basis |
|---|---:|---:|---:|---:|---:|---:|---|
| Shared benchmark/sector/market prerequisites | about 28.4 s | about 27 s | bounded | 1,618+ bars | 0 | included below | read-only production loaders |
| 100-request ticker history loading | 19.114–23.737 s | not isolated | 250 | 146,916 visible bars | 0 | 301 MB after load | exact retained input |
| Dataframe/work-item preparation | 7.482–10.622 s | not isolated | 0 | in-memory | 0 | 379 MB | exact retained input |
| Pure Technical mathematical kernel | **72.883 s** | 73.047 s | 0 | already loaded | 0 | 389 MB | sequential, no DB/evidence/shadow/orchestration |
| Current 2-process execution | **44.968–53.749 s** | parent CPU incomplete | 0 in workers | serialized frames | 0 | **805 MB combined** | exact retained input; startup/serialization/IPC are included |
| Process-pool overhead | crossover-dependent: +8.260 s at 1, +2.825 s at 10; hidden by parallel benefit at 25/100 | not separately attributable across spawned workers | 0 | serialized frames | 0 | included in pool peak | measured against the sequential kernel at each scale |
| V5/shadow path | 0.015 s context load; V5 score construction is included in the 246.260 s finalization group | negligible context CPU; construction interleaved | 3 | small config/context | 0 | included below | no second pool/kernel pass; pure legacy shadow is bypassed by process-pool branch |
| Evidence construction/hashing + V4/V5 finalization + ORM object/identity construction, no persistence | **246.260 s** | 242.266 s | 0 | in-memory | 0 | 3.60 GB uncompressed JSON generated across scores | exact retained input; these operations are interleaved in one function and are reported as one measured group |
| Technical-score `INSERT`/flush | about **341.7 s SQL execution** | server-side | normalized variants totaling 100 rows | n/a | 100 attempted, 0 committed | about **6.54 GiB temp blocks** | `pg_stat_statements`, retained failed run |
| Repeated evidence validation/persistence before termination | approximately **4,207 s residual** | dominated by Python materialization/hash work | 4,718 representative price-bar lock queries | 3,750,518 price-bar rows returned | 0 committed | repeated ~38 MB payloads | incident total less measured pre-persist and score INSERT; approximate |
| Commit/finalization | **NOT REACHED** | n/a | n/a | n/a | 0 | n/a | backend terminated; transaction rolled back |

The residual is a reconstruction, not a separately timed successful phase. It is nevertheless bounded by hard measurements: total Technical time was 4,894.149 seconds; pre-persistence production-shaped work is a few minutes; the score INSERTs consumed about 342 seconds; commit never occurred. PostgreSQL spent only about 23.9 seconds executing the 4,718 price-bar queries themselves. The large remaining time was Python ORM materialization, historical projection, canonicalization, hashing, and repeated validation of the same cohort source states.

### Amplification measurements

- 95 calculable scores.
- 481 source manifests embedded in **each** score.
- Roughly 380,580–381,526 price-bar state entries per score.
- 36,238,176 total state-entry visits across the 95 scores.
- About 37.85–37.94 MB of debug JSON per score.
- 3,603,955,598 bytes (3.60 GB decimal; 3.36 GiB) of uncompressed duplicate debug JSON across 95 scores.
- The design scales approximately as `O(tickers² × bars_per_ticker)` for evidence validation, not as the `O(tickers × bars)` mathematical kernel.

## 5. Pure mathematical kernel benchmark

> **100-ticker requested-set pure Technical kernel time = 72.883 seconds.**

This bypassed database writes, immutable evidence, shadow comparison, pipeline orchestration, background jobs, and leasing. It operated only on already-loaded prepared work items. At the frozen canary cutoff, 95 of the requested 100 had eligible input and were scored; five failed before the kernel because their newly fetched bars were not cutoff-visible.

The result is healthy against the audit's “seconds to low minutes” target and far below the 81m34s production stage. It does not excuse the roughly 1.5 seconds/ticker small-scale cost or the five missing inputs; those are separately tracked.

## 6. SQL and transaction analysis

### Exact amplification path

1. `app/services/price_bar_repository.py:11-67` materializes full ORM bar rows, creates a pandas frame, and creates a PIT manifest state for every bar. `load_preferred_ohlcv_frames` at `:173-195` performs both `ADJUSTED_LAST` and `TRADES` selection per ticker.
2. `app/services/technical_score_service.py:425-437` combines every member's manifests into `cohort_manifests`.
3. `technical_score_service.py:507-517` copies that entire cohort manifest into every score's debug lineage; `:524-544` builds an identity per score.
4. `technical_score_service.py:545-554` deletes/re-adds scores and flushes all score objects. The repeated giant JSON is sent to PostgreSQL before evidence persistence.
5. `technical_score_service.py:555-572` loops score by score through mutation declaration and evidence persistence.
6. `app/services/core_mutation_authority.py:778-806` extracts and validates the score's source manifests. `validate_price_source_manifest` at `:1166-1203` runs a price-bar `SELECT ... WHERE id IN (...) FOR UPDATE`, materializes a historical projection, and rehashes bars.
7. `app/services/core_calculation_evidence.py:198-300` canonicalizes and hashes the large payload and identity, performs evidence lookup/insert/projection work, and flushes.

Representative repeated SQL shape:

```sql
SELECT ...
FROM price_bars
WHERE price_bars.id IN (...)
FOR UPDATE;
```

The statement ran 4,718 times and returned 3,750,518 rows before termination. Its PostgreSQL execution time was about 23.943 seconds total; repeated ORM object creation and Python projection/hash work caused most wall time. This is both an N+1 pattern and a repeated-validation pattern. It is not primarily a missing-index problem.

The normalized Technical score INSERT variants totaled 100 attempted rows and about 341.7 seconds of PostgreSQL execution, with approximately 857,216 temporary 8-KiB blocks written (about 6.54 GiB). There were no relevant table triggers on Technical scores or core evidence. Relevant uniqueness and lookup indexes exist, including run/ticker, evidence-key/projection scope, and price ticker/date paths. No absent index explains the incident.

### Transaction boundary

The actual transaction is:

```text
COMMIT prior stage progress / lease renewal
BEGIN implicitly on Technical reads
  load benchmark, QQQ, sector and per-ticker historical bars
  prepare all work items
  start process pool; serialize tasks; calculate; collect all results
  construct all V4/V5 score objects and enormous repeated lineage/identity payloads
  DELETE existing Technical rows
  INSERT/FLUSH 100 Technical rows                 (~342 s SQL)
  for each score:
    SAVEPOINT (not an outer commit)
    lock/fence background_jobs row FOR UPDATE
    for each of 481 manifests:
      SELECT price bars FOR UPDATE
      materialize, project and rehash
    canonicalize/hash evidence; lookup/insert/flush evidence and projection
  expected stage-exit progress update and COMMIT
COMMIT NEVER REACHED; backend termination caused ROLLBACK
```

`core_writer_transaction` (`app/services/core_mutation_authority.py:630-685`) uses a savepoint but deliberately does not commit the outer transaction. `_pipeline_step` and `_save_progress` (`app/services/pipeline_executor.py:1557-1682`, `:1939-1950`) commit at stage boundaries. Therefore calculation, giant score flush, every evidence validation, and evidence persistence shared one outer transaction for the full 81 minutes.

The job row became part of this business transaction because `validate_core_mutation_authority` (`core_mutation_authority.py:161-212`) performs durable ownership fencing by selecting the `BackgroundJob` row `FOR UPDATE`. That lock was retained until the never-reached outer commit.

### Fresh-fetch cutoff defect

Five canary symbols had successfully fetched bars in the current database but zero bars visible to pipeline 154:

| Symbol | Current bars | First creation relative to frozen cutoff |
|---|---:|---|
| BHE | 1,504 | after cutoff |
| BLLN | 440 | after cutoff |
| KLIC | 1,504 | after cutoff |
| LSCC | 1,504 | after cutoff |
| PDFS | 1,504 | after cutoff |

The pipeline cutoff was 13:19:59, while these first-time bars were created around 13:39–13:43 during the pipeline's own successful Market Data stage. The Technical loader applies `created_at <= cutoff`, so the pipeline cannot consume the first fetch that it just commissioned. This violates the fresh-source handoff contract and would leave five Technical errors even after the performance defect is fixed.

## 7. Process-pool analysis

| Property | Current behavior |
|---|---|
| Start method | Windows `spawn` |
| Pool size | 2 workers |
| Max in flight | 4 |
| Task granularity | one ticker/work item |
| Submitted tasks | one per calculable ticker (95 in retained cutoff workload) |
| Serialized data | prepared dataclass plus ticker/benchmark/sector dataframes and configuration |
| ORM objects cross boundary | no |
| Dataframes copied/serialized | yes |
| Worker database access | no |
| Pool lifecycle | created once per Technical invocation |
| Result collection | futures accumulated in parent; persistence begins after calculation |
| V5 work | V5 score computed during finalization because shadow is enabled; not a second process-pool kernel pass |

The process pool is slower at 1 and 10 tickers because spawn, import, serialization, and IPC dominate. It becomes beneficial at 25 and 100 requested tickers. For the retained 100 request, it reduced scoring from 72.883 to 53.749 seconds, a 19.134-second benefit, but raised peak combined RSS to about 805 MB. Multiprocessing is therefore not the 81-minute root cause. A future implementation should reuse one pool across bounded calculation batches and avoid it below a measured threshold; this is secondary to fixing evidence amplification.

## 8. Lease and heartbeat analysis

### Ownership state machine

```text
QUEUED
  | claim_next_job: assign worker_id, execution_token, RUNNING, lease_expires_at
  v
RUNNING ----------------------------------------------+
  | handler-provided heartbeat / stage boundary       |
  | renew job lease + commit                          |
  |                                                   |
  +--> long Technical call                            |
       no job heartbeat callback                      |
       no cancel callback                             |
       later locks BackgroundJob FOR UPDATE           |
       separate thread renews BackgroundWorker only   |
       |                                               |
       +--> job lease expires                          |
            worker process still fresh                |
            watchdog SELECT ... FOR UPDATE SKIP LOCKED
            skips the locked job; no recovery_count   |
            no safe recovery transition               |
```

`claim_next_job` is called by `run_worker_once` in `app/workers/background_worker.py:478-488`, using the 900-second stale/lease interval. `run_worker_once` provides a heartbeat function (`:506-538`) that renews the job lease and worker record and commits, but it runs only at handler-defined checkpoints. The separate `_worker_heartbeat_loop` (`:325-389`) updates the `BackgroundWorker` process row every five seconds; it does **not** renew the `BackgroundJob` lease.

Pipeline `lease_guard` (`app/services/pipeline_executor.py:759-766`) renews job ownership when called. `_save_progress` calls it at stage boundaries. The non-overlap Technical branch (`pipeline_executor.py:643-649`) calls `score_run_technicals` without a heartbeat, cancellation, or progress callback and does not check cancellation again until the scorer returns (`:711`). Process-pool workers do not directly prevent heartbeat renewal; the parent simply has no periodic job-renewal path for this stage.

The watchdog's stalled-job path (`app/workers/worker_supervisor.py:568-609`; `app/services/background_job_service.py:932-1053`) selects candidates with `FOR UPDATE SKIP LOCKED`. Once evidence validation locks the job row, the watchdog silently skips it. The main worker cannot concurrently perform recovery while it is inside the handler, and the alternative stale-recovery path would block on the same lock.

An expired lease can therefore coexist with an active writer. Duplicate execution did not occur because reclaim could not update the locked row. If another owner could supersede the token, the late-commit fence should reject the stale owner; the isolated PostgreSQL fencing test confirms that ordinary supersession case. But the current lock coupling prevents liveness/reclaim in the incident case. Fencing protects some stale commits, not the ability to monitor, cancel, or safely recover a long transaction.

Root cause: job lease renewal is cooperative and stage-boundary based, while Technical is an 81-minute uncheckpointed call whose business transaction locks the same control-plane row used for ownership, cancellation, and recovery.

## 9. Cancellation analysis

The normal path is `cancel_pipeline` (`app/services/pipeline_service.py:657-681`) to `request_job_cancel` (`app/services/background_job_service.py:1538-1557`), which loads the job, sets `requested_cancel`, flushes the update, and commits through its service boundary.

Technical's evidence authority had already selected that same `background_jobs` row `FOR UPDATE` in its outer transaction. PostgreSQL therefore made the cancellation update wait for the Technical transaction. Even if the flag could have been written, Technical did not poll a cancellation callback inside history loading, pool result collection, score flushing, or the per-score evidence loop. Its next normal observation point was after the entire scorer returned.

The transaction stayed open because score persistence and every evidence item were wrapped in savepoints inside one stage-level outer transaction. Inserts were flushed, but nothing was committed incrementally. The retained run proved that PostgreSQL backend termination was the only timely escape: termination rolled back all uncommitted Technical/evidence work and released the lock, after which cancellation could complete. That emergency mechanism is not an acceptable cancellation model.

## 10. Downstream stage matrix

No new live pipeline was run. Current-head isolated tests were combined with retained production data from pipeline 150/run 160 (185 symbols) and, where useful, pipeline 148/run 158. Retained runs are supporting operational evidence, not substitutes for a current-head deterministic 100-symbol gate.

| Stage | Functional status | Measured runtime | Data prerequisites | Writes required? | Main concern |
|---|---|---:|---|---|---|
| SEC readiness | **PASS** | run 163 preparation completed before resume; provider-heavy | source tickers, SEC submissions/company facts | yes in live prep; mocked/isolated in tests | provider latency, but 100/100 readiness achieved |
| Fundamental | **PASS** | **20.502 s / 100** live | prepared SEC facts | yes | no blocker found; 62 current-head tests in Fundamental/Combined/Ranking group passed |
| Market Data | **PASS** (known live success) | **438.334 s / 100** | IB availability, eligibility/cutoff | yes | provider-bound; its own first-fetch handoff is broken at Technical cutoff |
| Technical | **PERF_FAIL** (and input-handoff defect) | **4,894.149 s / 100 requested** live; 72.883 s pure kernel | OHLCV, benchmarks, frozen context | yes | quadratic evidence amplification, huge transaction, five cutoff-invisible symbols |
| Combined | **PASS** | **1.38 s / 185** retained | Fundamental + Technical results | yes | cannot run in pipeline 154 until Technical commits |
| Ranking | **PASS** | **1.72 s / 185** retained | Combined scores and peer universe | yes | current-head full-scale orchestration still needs deterministic gate |
| CERI | **PASS** | provider-ingest step **1.32 s / 185** retained | ranked/eligible universe, SEC/provider context | yes | pipeline 154 never reached it; 50 current-head tests passed |
| Setup | **PASS** | **9.39 s / 185** retained | handoff/frozen candidates and price context | yes | prior run 159 failure was followed by successful run 160 and current tests |
| Lifecycle | **PASS** | **73.42 s / 185** retained | setups, current price/events | yes | long enough to require progress; 91 Setup/Lifecycle/Alert tests passed |
| Alerts | **PASS** | no standalone production-stage time; targeted tests completed within suite | lifecycle/CERI state and policy | yes when emitted | no separate durable pipeline progress surface |
| Winner | **PASS** | **269.52 s / 185** retained | eligible candidates, evidence/config | yes | near warning budget; 54 current-head tests passed; no automatic mutation was enabled |

Other retained evidence: run 160 held 185 Fundamental, 185 Technical, 185 Combined, 925 ranking, 182 CERI, 185 Setup, and 185 Winner records. Pipeline 150 completed every stage (final state `PARTIAL` for domain outcomes). Its Technical stage took about 181 seconds, before the current evidence path represented by HEAD; that contrast supports a regression in the newer persistence/evidence path.

### Targeted test evidence

- Fundamental + Combined + Ranking: 62 passed in 3.42 s.
- CERI: 50 passed in 5.27 s.
- Setup + Lifecycle + Alerts: 91 passed in 22.89 s.
- Winner: 54 passed in 7.85 s.
- Pipeline + lease + cancellation units: 80 passed in 3.31 s.
- Real PostgreSQL disposable integration: stale-owner fencing and late-checkpoint rollback passed in 18.53 s.
- Total: **338 passed, 0 failed** across the selected checks, including the one separately run PostgreSQL integration test.

### Provisional recovery thresholds

These are audit thresholds, not final SLAs.

| Stage class | Warning | Failure | Basis |
|---|---:|---:|---|
| Technical, 100 tickers | >5 min | >15 min | task-specified; current live is 81m34s |
| Fundamental | >1 min | >3 min | current 100 live is 20.5 s |
| Combined, Ranking, Regime, Sector, Handoff | >1 min each | >3 min each | retained values are seconds |
| CERI compute/capture excluding unavoidable provider wait | >2 min | >10 min | current tests and retained ingest are fast |
| Setup | >1 min | >3 min | retained 185 is 9.4 s |
| Lifecycle | >2 min | >5 min | retained 185 is 73.4 s |
| Winner | >5 min | >15 min | retained 185 is 4m30s |

Market Data is provider-bound and should be governed by its per-item deadline/retry/failure budget rather than a CPU-stage limit. No stage may remain indefinitely “healthy” merely because its process is alive.

### Durable progress semantics

| Stage | Current durable progress | Operational classification |
|---|---|---|
| SEC/Market Data | item-oriented counters/events | useful progress |
| Fundamental | stage boundaries, not a durable per-ticker checkpoint in pipeline execution | `0% → opaque → 100%` risk, currently short |
| Technical | `processed=0`, `total=NULL`, no current/last item for full 81 minutes | **unacceptable opaque long stage** |
| Combined/Ranking/Regime/Sector/Handoff | primarily stage boundaries | opaque but currently short |
| CERI/Setup/Lifecycle/Winner | job handlers contain batching/progress mechanisms, but the synchronous pipeline path does not consistently expose them at pipeline-stage granularity | mixed; long paths need verified checkpoints |
| Alerts | event-level outcomes, no distinct pipeline-stage progress | acceptable only while bounded |

### Batching/checkpoint conclusion

A calculation batch of **10 tickers** is suitable: it bounds work to tens of seconds with a reused two-worker pool and provides frequent cancellation, heartbeat, and progress points. However, naively committing ten production score rows at a time would expose a partially replaced run and would change current atomicity/evidence semantics.

Minimum-change design evidence supports:

1. Calculate and prepare in batches of 10, with heartbeat/cancel/progress after each completed future or batch.
2. Build one canonical, deduplicated cohort manifest and validate each unique source once, not once per score.
3. Keep heavy read-only validation and payload preparation outside the final write transaction.
4. Use one short fenced publish transaction if consumers require all-or-nothing visibility. If partial commits are introduced, gate consumers on a run-generation/completion marker and make retry an exact idempotent upsert; do not expose partial current rows.
5. Preserve stable evidence identity by referencing the canonical manifest digest, not copying hundreds of thousands of states into each score.

The existing run/ticker and evidence-key uniqueness constraints provide useful idempotency primitives. The implementation must checkpoint exact completed tickers/batches and resume without changing identity. Schema redesign is not necessary to prove or fix the primary amplification, and should not be the default.

## 11. Runtime/certification classification

| Classification | Operations |
|---|---|
| **REQUIRED_RUNTIME** | PIT-bounded price loading; benchmark/sector context; V4 production Technical calculation; production score construction; bounded persistence; active configuration binding; result handoff to Combined |
| **REQUIRED_SAFETY** | temporal cutoff validation; uniqueness/idempotency; short ownership fence at publish; stable evidence identity/reference; validation of each unique source manifest; commit-time cancellation/ownership check |
| **ASYNC_VALIDATION_CANDIDATE** | V5 calculation/comparison while V5 production is disabled; persistence of V5 shadow results; exhaustive per-bar replay/lineage validation; deep forensic evidence verification; artifact-cache shadow validation; full projection consistency sweeps |
| **CERTIFICATION_ONLY** | legacy-vs-pure shadow parity; full-cohort replay verification; invariant sweeps over every bar; single-process/process-pool parity and performance comparison; exhaustive evidence reconstruction |
| **LEGACY** | `_score_tickers_legacy` compatibility scorer and obsolete fallback/compatibility paths after parity/rollout evidence permits retirement |

Immutable evidence itself is a safety/forensic requirement in the current architecture. The classification does **not** recommend removing it. It recommends keeping the stable production identity and minimal synchronous validation while moving exhaustive duplicate reconstruction off the critical path. The artifact cache's active read is a runtime optimization; shadow verification of the cache is validation, not result production.

## 12. Certification gaps

Previous certification emphasized mathematical correctness and narrow invariants, not production-shaped scale plus persistence, ownership, and transaction duration.

| Failure observed live | Existing coverage that appeared relevant | Why it did not catch the failure |
|---|---|---|
| Technical >80 minutes | Technical work/scoring unit and parity tests | usually one ticker with synthetic ~300-row frames; `ImmediateExecutor`/one worker; no 100-ticker cohort manifest, evidence loop, giant JSON, or real flush |
| 3.60 GB repeated lineage | evidence/identity unit and PostgreSQL tests | validate one or a few artifacts; do not put a universe-wide bar manifest into every score and measure payload size |
| 342-second score flush / 6.54 GiB temp | ORM correctness tests | assert rows and identities, not PostgreSQL bytes, temp spill, query time, or commit budget |
| 4,718 repeated locked bar queries | mutation-authority tests | prove validation correctness for small manifests; no query-count/scaling assertion across 100 scores |
| Process-pool scale behavior | process-pool order/parity tests | assert output ordering and equality, not spawn/IPC wall time, memory, or crossover point |
| Lease expires while worker lives | worker lease unit tests | call heartbeat/recovery functions directly; no 80-minute uncheckpointed handler with a separate fresh worker heartbeat |
| Watchdog cannot reclaim | stale-owner PostgreSQL integration | proves superseded-token fencing when the job row can be updated; does not hold that row locked inside the business transaction while watchdog uses `SKIP LOCKED` |
| Cancellation blocks | cancellation/batch tests | cancellation is checked between bounded steps; no test issues cancel while Technical owns the job row in a long outer transaction |
| Zero durable Technical progress | pipeline executor tests | dependency-injected/mocked scorer returns promptly; tests do not require item total, checkpoint, heartbeat cadence, or maximum opaque interval |
| Five first-fetch symbols invisible | cutoff/PIT tests | test historical cutoff correctness separately from a pipeline's own post-admission Market Data writes; no fresh-source first-fetch handoff test |
| Downstream hidden risk | prior live canaries and per-stage units | earlier live blockers masked later stages; units bypass full current-head orchestration/persistence; run 160 predates the current evidence regression |

The pipeline golden path uses fake database/state and prebuilt score objects. It validates orchestration decisions, not real SQL/ORM/evidence behavior. There was no current-HEAD, frozen, production-shaped 10→25→100 deterministic gate with wall-time, query-count, payload-size, heartbeat, cancellation, and rollback assertions. That is why successive live canaries became subsystem-discovery tools.

## 13. Consolidated defect list

### P0 — blocks end-to-end recovery

| ID | Subsystem | Evidence | Root cause status | References | Minimal correction |
|---|---|---|---|---|---|
| `REC-P0-01` | Technical evidence/persistence | 81m34s; 481 manifests/score; 36.24M state visits; 3.60 GB JSON; 4,718 repeated lock queries | identified | `technical_score_service.py:425-572`; `core_mutation_authority.py:778-806,1166-1203`; `core_calculation_evidence.py:198-300` | canonicalize/deduplicate cohort manifest, validate unique sources once, reference stable digest per score |
| `REC-P0-02` | Job lease/ownership | lease expired 15 minutes after last progress while worker process heartbeat stayed fresh; no recovery | identified | `background_worker.py:325-389,478-538`; `pipeline_executor.py:643-766`; `background_job_service.py:932-1053` | renew job independently at bounded Technical checkpoints and keep ownership control-plane writes out of long business transactions |
| `REC-P0-03` | Cancellation/transaction | cancel update blocked; no in-stage poll; backend termination required | identified | `pipeline_service.py:657-681`; `background_job_service.py:1538-1557`; `core_mutation_authority.py:161-212,630-685` | add bounded cancellation checks; shorten/fence only publish transaction; prevent business work from retaining job-row lock |
| `REC-P0-04` | Market-to-Technical handoff | 5/100 newly fetched tickers had current bars but zero cutoff-visible bars | identified | `price_bar_repository.py:11-67,173-195`; pipeline market context/cutoff path | define pipeline-owned fetch visibility without weakening PIT semantics; add first-fetch handoff regression test |

### P1 — serious reliability/performance issues

| ID | Subsystem | Evidence | Root cause status | References | Minimal correction |
|---|---|---|---|---|---|
| `REC-P1-01` | Progress semantics | Technical stayed processed 0 / total null for 81 minutes | identified | `pipeline_executor.py:622-715,1939-1950` | report total, completed, current batch, latest checkpoint and bounded progress timestamp |
| `REC-P1-02` | Transaction scope | calculation, 100 giant score INSERTs, and all evidence lived in one transaction; commit never reached | identified | `technical_score_service.py:545-572`; `core_mutation_authority.py:630-685` | precompute/prevalidate outside write transaction; keep final publish short and atomic |
| `REC-P1-03` | Watchdog recovery | `FOR UPDATE SKIP LOCKED` omitted the very job requiring recovery | identified | `worker_supervisor.py:568-609`; `background_job_service.py:932-1053` | separate observable control state from locked business state; surface locked-stalled jobs instead of silently treating them as absent |
| `REC-P1-04` | Evidence payload representation | ~38 MB duplicated per score; 6.54 GiB temp spill on score INSERT | identified | `technical_score_service.py:507-544`; `core_calculation_evidence.py:198-300` | store/reference canonical lineage digest and keep per-score payload bounded while preserving semantics |
| `REC-P1-05` | Certification | 338 selected tests pass despite reproduced live failure modes | identified | matrix in §12 | add one production-shaped frozen recovery test at 10/25/100 with budgets, real DB, leases, cancel, and downstream completion |

### P2 — bounded improvements

| ID | Subsystem | Evidence | Root cause status | References | Minimal correction |
|---|---|---|---|---|---|
| `REC-P2-01` | Process pool | slower by 8.26 s at 1 and 2.83 s at 10; ~805 MB at 100 | identified | `technical_score_service.py:1204-1361`; `technical_work.py:24-195` | use sequential path below measured crossover; reuse one pool across calculation batches |
| `REC-P2-02` | Operational storage | rolled-back attempt left dead tuples and Technical table bloat (~220 MB observed) | identified | PostgreSQL table statistics | after remediation, use normal maintenance/monitoring and verify bloat; do not make cleanup part of correctness fix |

Count: **P0 = 4, P1 = 5, P2 = 2**.

## 14. Recovery implementation plan

This is one bounded remediation, ordered by dependency. It deliberately avoids scoring-formula changes, new frameworks, and broad redesign.

1. **Repair the fresh-fetch visibility contract.** Make bars successfully obtained by the pipeline's own Market Data stage visible to its downstream Technical stage while retaining the frozen business/PIT boundary. Prove all five incident tickers become eligible with an isolated run-163 regression fixture.
2. **Remove evidence amplification without weakening identity.** Build one canonical cohort/series manifest, validate each unique series once, and bind scores to its stable digest/reference. Stop copying and hashing hundreds of thousands of bar states into every ticker payload.
3. **Separate heavy preparation from short publication.** Load, calculate, and perform read-only validation outside the write transaction. Calculate in ten-ticker batches with one reused pool. Keep the final score/evidence publish fenced and atomic unless a completion-gated generation makes partial commits explicitly safe.
4. **Repair the control plane.** Renew the job lease and publish progress at bounded batch/future intervals using a control transaction that cannot be held by Technical evidence work. Poll cancellation in loading, pool collection, and evidence preparation. Restrict job-row fencing to short checkpoint/publish operations. Make watchdog diagnostics distinguish “locked but stalled” from “absent.”
5. **Add targeted regression coverage.** Add the small number of tests enumerated in §15: fresh-fetch visibility, 100-scale query/payload/runtime budget, heartbeat under real work, nonblocking cancellation/rollback, and all-stage deterministic completion.
6. **Run the full gate sequence once.** Do not start a small live slice until frozen 10/25/100 gates pass. Do not start the final 100 live canary until the small live slice reaches Winner and all progress/lease/cancel invariants are recorded.

## 15. Proposed certification gates

Commands below distinguish tests that already exist from focused recovery tests that must be added during remediation. The proposed recovery test file is not a framework; it is one targeted integration suite using the existing pytest/database fixtures.

### Gate 1 — kernel

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_technical_work.py `
  tests/test_technical_score_v4.py `
  tests/test_technical_scoring_v5.py
```

Add and require:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/recovery/test_whole_application_recovery.py::test_pure_technical_100_known_output_and_budget
```

Acceptance: known outputs unchanged; 100 calculable frozen tickers; less than 5 minutes, with the measured target near 73 seconds; zero SQL during the timed kernel.

### Gate 2 — individual stages

Run the current stage suites. The paths below exist at this audit's HEAD and cover the same stage surfaces exercised by the selected 338-test audit sweep:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_fundamental_components_v2.py tests/test_fundamental_ranker.py `
  tests/test_fundamental_ranker_v2.py tests/test_fundamentals_v2_acceptance.py `
  tests/test_combined_decision.py tests/test_combined_ranking_identity_adoption.py `
  tests/test_ranking_profile_engine.py tests/test_ranking_profiles_golden.py

.\.venv\Scripts\python.exe -m pytest -q `
  tests/ceri/test_ceri_acceptance_fixture.py tests/ceri/test_ceri_orchestration.py `
  tests/ceri/test_ceri_performance.py tests/ceri/test_ceri_changes_alerts_forensic.py `
  tests/ceri/test_scoring.py tests/ceri/test_sec_pipeline_preflight.py `
  tests/ceri/test_run102_golden_certification.py

.\.venv\Scripts\python.exe -m pytest -q `
  tests/setup_lifecycle/test_setup_lifecycle_acceptance_fixture.py `
  tests/setup_lifecycle/test_performance.py `
  tests/setup_lifecycle/test_setup_lifecycle_job_handlers.py `
  tests/setup_lifecycle/test_snapshot_builder.py `
  tests/setup_lifecycle/test_evaluation_service.py `
  tests/setup_lifecycle/test_alert_service.py `
  tests/setup_lifecycle/test_lifecycle_engine.py

.\.venv\Scripts\python.exe -m pytest -q `
  tests/winner_probability/test_acceptance_fixture.py `
  tests/winner_probability/test_capture_service.py `
  tests/winner_probability/test_probability_estimator.py `
  tests/winner_probability/test_estimate_publication_policy.py `
  tests/winner_probability/test_job_handlers.py `
  tests/winner_probability/test_winner_job_reliability_semantics.py `
  tests/winner_probability/test_winner_refresh_performance.py
```

Add and require focused database cases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/recovery/test_whole_application_recovery.py::test_pipeline_owned_first_fetch_is_visible `
  tests/recovery/test_whole_application_recovery.py::test_technical_evidence_is_linear_and_bounded `
  tests/recovery/test_whole_application_recovery.py::test_technical_heartbeat_survives_long_work `
  tests/recovery/test_whole_application_recovery.py::test_technical_cancel_is_nonblocking_and_rolls_back
```

Acceptance: formulas and evidence identity remain correct; query count/payload scale approximately linearly; lease never expires during legitimate progress; cancel becomes observable within one batch and does not require backend termination.

### Gates 3–5 — deterministic full pipeline

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/recovery/test_whole_application_recovery.py::test_frozen_pipeline_10

.\.venv\Scripts\python.exe -m pytest -q `
  tests/recovery/test_whole_application_recovery.py::test_frozen_pipeline_25

.\.venv\Scripts\python.exe -m pytest -q `
  tests/recovery/test_whole_application_recovery.py::test_frozen_pipeline_100
```

Use retained/frozen provider responses and a disposable PostgreSQL database. Each gate must reach Winner, leave one coherent evidence generation, show monotonic durable progress, remain within the provisional budgets, and prove a retry is idempotent. The 100 gate must include the five first-fetch incident symbols and assert zero cutoff-invisible inputs.

### Gate 6 — small live vertical slice

Only after Gates 1–5 pass, use the normal supported upload/admission path once with a small representative fresh source. Require SEC → Fundamental → Market → Technical → Combined → Ranking → CERI → Setup → Lifecycle/Alerts → Winner completion. Record per-stage budgets, job lease continuity, progress, and a tested normal cancellation on a separate disposable job—not the release pipeline. Do not enable automatic Winner mutation.

### Gate 7 — final 100-symbol live canary

Only after Gate 6 reaches the final downstream stage. Use exactly one upload and one admission. Run the existing read-only release preflight before admission and the existing release artifact workflow afterward. Acceptance requires all 100 inputs accounted for, Technical below 15 minutes (target low minutes), no expired lease, no opaque interval beyond the checkpoint budget, all downstream stages reached, and no manual database/backend intervention.

## 16. STOP / GO

`READY TO IMPLEMENT CONSOLIDATED REMEDIATION: YES`

The causes are specific, measured, and bounded. The recovery can preserve scoring formulas and evidence semantics while removing duplicated lineage work, shortening transaction/lock scope, and restoring independent job control.

`SAFE TO RUN ANOTHER LIVE CANARY NOW: NO`

Another canary would predictably reproduce the Technical evidence/transaction problem or encounter the five-symbol cutoff defect, and its cancellation/lease behavior remains unsafe. Complete the consolidated remediation and deterministic gates first.
