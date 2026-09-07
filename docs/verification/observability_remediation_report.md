# Adversarial Review Remediation OBS-001–OBS-013

Certification date: 2026-09-07
Branch: `main`
HEAD: `f6be50aa8b64ddb3fe4a0fa9892066551e9744d9`
Certification database policy: disposable PostgreSQL databases only

## Finding matrix

| Finding | Root cause | Fix | Tests | Runtime certification | Verdict |
| ------- | ---------- | --- | ----- | --------------------- | ------- |
| OBS-001 | Redaction handled structured keys but not credentials embedded in arbitrary strings and retained execution-token suffixes. | One shared, idempotent redactor now covers auth schemes, mixed-case secret assignments, quoted/JSON/query values, URI userinfo and provider credentials; token metadata has no reversible suffix. | Redaction matrix plus logging, `_safe_error`, readiness, Operations, CERI/provider and DB-URL surface tests. | Repository password scan and serialization tests expose no fixture secret or literal Grafana credential. | PASS |
| OBS-002 | Worker/supervisor registries were process-local and Docker reachability/listener behavior was unproven; Windows child interpreter handoff could start the wrong runtime. | Separate web/worker/supervisor targets remain; listeners are loopback-only, Docker Desktop uses `host.docker.internal`, and the supervisor preserves the active virtual-environment interpreter. | Metrics/security/supervisor tests and `scripts/certify_observability_cross_process.py`. | Real web, real supervisor, supervisor-owned real worker and Prometheus were all UP. Worker PIDs 15032 then 18224 completed real probe jobs; the worker-only sample timestamp advanced from 1788778390.733 to 1788778402.159 after restart; supervisor liveness metric was 1. | PASS |
| OBS-003 | Commit-dependent metrics were emitted before transaction outcome was known. | SQLAlchemy transaction hooks buffer commit-dependent metrics, merge nested commits, discard rollback state and publish safely once after the outer commit. | Flush/commit failure, explicit rollback, nested rollback, retry-after-rollback, successful commit and real PostgreSQL rollback tests. | Rolled-back enqueue produced 0 job rows, 0 enqueue-attempt rows and metric delta 0; successful commit produced exactly one metric. | PASS |
| OBS-004 | Aggregate fanout mixed unrelated roots and did not preserve created/coalesced/rejected/depth semantics. | Durable per-root rollups record bounded workflow family, attempted/created/coalesced/rejected counts, descendants, depth and job-family distribution; Prometheus labels use family, never root ID. | 100-small-roots, excessive Winner root, deep root, coalescing and retry tests. | 100 roots each had one created job and no alert; abnormal root had 24 attempts/23 created/1 coalesced/22 descendants; deep root had 7 created/6 descendants/depth 6; retry left totals unchanged. | PASS |
| OBS-005 | Winner eligibility polling entered root/enqueue instrumentation before recognizing an already-completed daily job. | Same-session completion is checked before root scope or enqueue evidence; enqueue-attempt retention is configurable. | Unit and PostgreSQL 5,000-poll tests plus next-day scheduling. | Before/after 5,000 polls: jobs 1/1, attempts 0/0, roots 0/0, fanout metric 0/0. Next day adds exactly one job, attempt and root. | PASS |
| OBS-006 | The CERI compatibility registry retained an unbounded `_samples` list. | The adapter is stateless and writes only to the declared Prometheus registry. | Two 20,000-event threaded batches with retained-memory measurement. | 40,000 events, Prometheus count 40,000, sample count 0, retained growth -640 bytes, peak traced memory 168,273 bytes. | PASS |
| OBS-007 | Metric names/types/labels were inferred dynamically, allowing semantically wrong counters and units. | A closed catalog declares canonical name, type, help, unit, labels, allowed-value policy and buckets; unknown metrics and wrong labels/types fail validation. Provider request count and latency are one counter event plus one observation. | Catalog contract, AST call-site scan, wrong-label/type/unit tests and metric-failure containment. | 20,000 metric emissions averaged 18.156 microseconds; a scrape contained only declared series. | PASS |
| OBS-008 | Queue age included future work and pool capacity used current overflow rather than configured maximum overflow. | Readiness distinguishes runnable, scheduled, blocked, recovering, stalled and running; oldest age uses runnable rows only. Pool pressure uses checked-out/base/configured-overflow plus wait/timeout signals. | Queue and pool state matrices. | Future-only queue reports runnable 0/scheduled 8/oldest 0; capacity cases cover idle, base-full with overflow available, overflow use, exhaustion, timeout and wait pressure. | PASS |
| OBS-009 | Readiness omitted required/optional IB semantics and cross-process recorder/collector/supervisor failures. | Four states (`ok`, `degraded`, `failed`, `optional_unavailable`) and durable worker/supervisor telemetry are included; runnable job capabilities are evaluated per job type. HTTP 503 is returned only for overall `failed`. | IB, worker recorder, collector death, database failure, missing worker/supervisor and HTTP status tests. | Full readiness test matrix passed; stale/missing critical process state is not reported healthy. | PASS |
| OBS-010 | A sampler exception could abort startup or permanently kill collection. | Each category is independently fault-contained; the loop retries on the normal cadence, records health/error metrics, redacts warnings and keeps expensive DB-size work on a slower cadence. | Fault injection for process memory, disk, paths, DB query, pool, DB monitor and loop recovery. | Startup survived injected failures; unaffected categories continued and failed categories recovered on the next interval without a tight loop. | PASS |
| OBS-011 | Recent-root grouping and provider p95 queries could scan unbounded history; Operations repeated an independent readiness DB connection. | Recent roots use the rollup table/index and limit 20; provider aggregation uses a time-leading covering index and bounded latest-10,000 CTE; selected columns and bounded results are used. Operations reuses its already-live request session for the cached readiness DB probe. Schema-capability inspection is cached on the enqueue hot path. | Unit bounds tests and 10/1k/100k/1m PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` gate with snapshot timing. | At 1m rows root query read 20 rows in 0.140 ms via `idx_fanout_roots_recent`; provider read at most 10k rows in 13.589 ms via `ix_ceri_provider_telemetry_observed_provider`; snapshot was 0.149 s. | PASS |
| OBS-012 | Compose published Prometheus/Grafana broadly and included a fixed Grafana administrator password. | Compose binds `127.0.0.1:9090` and `127.0.0.1:3000`; Grafana requires an environment-supplied password and `.env.example` contains a placeholder only. | Compose asset, loopback-binding and repository credential-scan tests. | Local Prometheus access worked during certification; published port was `127.0.0.1:9090`; no non-loopback monitoring publication or static admin password was found. | PASS |
| OBS-013 | The request-key partial unique index fenced QUEUED/RUNNING but not service-active RECOVERING jobs. | Migration 0065 replaces the index with a concurrent partial unique index covering QUEUED/RUNNING/RECOVERING. | Status matrix, independent-transaction RECOVERING race and migration downgrade/re-upgrade test. | QUEUED, RUNNING and RECOVERING duplicates were rejected; COMPLETED allowed a new active row; the race ended with one authoritative active job. | PASS |

## Working database incident

The first disposable certification attempt passed a database URL through Alembic
`config.attributes`, but the prior `alembic/env.py` unconditionally replaced it with
`get_settings().database_url`. Alembic therefore applied 0065 to the configured `swinglens`
working database before fixture code began. URL precedence is now explicit: an injected URL wins.

The working database was audited using read-only transactions. It is at
`0065_observability_remediation`, which is the sole worktree head; no later revision was applied.
Migration 0065 added three nullable telemetry columns to each worker registry table, created the
fanout-root rollup table and two indexes, rebuilt the active request-key unique index to include
RECOVERING, and added the enqueue time/root and provider observed-time covering indexes. The
migration contains no application-table DML. The failed attempt inserted, updated or deleted no
application/business row: certification-marker counts were zero for jobs, attempts, workers and
supervisors, and the fanout table was empty. Only schema objects and `alembic_version` changed.
The observed schema exactly matches 0065 and the current ORM/index definitions, so no downgrade or
other corrective mutation was performed.

## Cross-process Prometheus evidence

The real certification used a newly created `swinglens_obs_cert_*` PostgreSQL database, migrated it
to head, and established the current SEC processor state through the audited register/certify/
promote lifecycle API. It then launched the actual ASGI web process, actual worker supervisor and
supervisor-owned durable worker, followed by Prometheus in Docker Desktop.

```text
web target UP: true
worker target UP: true
supervisor target UP: true
first worker PID: 15032; probe job: COMPLETED
second worker PID: 18224; probe job: COMPLETED
query: swinglens_job_progress_total{job="swinglens-worker",stage="WORKER_RECOVERY_PROBE"}
present before restart: true
present after restart: true
sample time before/after: 1788778390.733 / 1788778402.159
supervisor liveness metric: true
```

The metric was emitted only in the durable worker process. A fresh post-restart sample timestamp,
not a stale series, was required. The script removed the processes, container and disposable DB.

## Transaction, scheduler, fanout and readiness evidence

- PostgreSQL rollback: job rows `0`, enqueue-attempt rows `0`, enqueue metric `0`; subsequent commit
  produced one row/evidence chain and metric value `1`.
- Winner idle polling: 5,000 calls took 11.169085 s (2.234 ms/call), with jobs `1 -> 1`, attempts
  `0 -> 0`, roots `0 -> 0` and fanout metric `0 -> 0`; next-day work added one of each durable row.
- Fanout: 100 unrelated roots did not alert; the 22-descendant Winner root and depth-6 root alerted;
  coalescing stayed distinct; retry changed none of the created/attempt/depth counters.
- Readiness: future-only work is scheduled, never runnable; oldest age ignores it. Pool capacity is
  base plus configured maximum overflow. Required IB absence, recorder/collector failures, dead
  collectors, missing worker/supervisor and database failure are explicit states.

## Operations query plans

All figures are from disposable PostgreSQL and include actual execution and buffer data.

| Rows in each fixture | Root access | Root plan / exec | Root rows / buffers | Provider access | Provider plan / exec | Provider rows / buffers | Full snapshot |
| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: |
| 10 | sequential (small-table choice) | 15.543 / 0.109 ms | 10 / hit 1 | sequential (small-table choice) | 13.176 / 0.243 ms | 10 / hit 9 | 0.554 s |
| 1,000 | `idx_fanout_roots_recent` | 4.969 / 0.070 ms | 20 / hit 5 | `ix_ceri_provider_telemetry_observed_provider` | 4.897 / 2.207 ms | 1,000 / hit 21 | 0.163 s |
| 100,000 | `idx_fanout_roots_recent` | 3.548 / 0.064 ms | 20 / hit 7 | time-leading covering index, bounded 10k | 7.201 / 17.004 ms | 10,000 / hit 10,073 | 0.155 s |
| 1,000,000 | `idx_fanout_roots_recent` | 3.186 / 0.140 ms | 20 / hit 2, read 4 | time-leading covering index, bounded 10k | 4.204 / 13.589 ms | 10,000 / hit 10,002, read 72 | 0.149 s |

## Performance results

| Path | Result | Gate |
| --- | ---: | ---: |
| Metric increment hot path | 18.156 microseconds mean over 20,000 | <100 microseconds |
| `/metrics` registry serialization | 6.261 ms, 34,461 bytes | <250 ms |
| PostgreSQL enqueue, including commit | 34.085 ms mean / 30.612 ms p95 over 25 | p95 <100 ms |
| PostgreSQL completion, including commit | 13.208 ms mean / 19.098 ms p95 over 25 | p95 <100 ms |
| Winner completed-session idle poll | 2.234 ms mean over 5,000 | <3 ms mean |
| CERI metric loop | 40,000 events in 7.008 s under `tracemalloc`; 0 retained samples | bounded |
| Operations snapshot at 1m+row scale | 0.149 s | <3 s |

Repeated schema inspection initially caused a measured enqueue regression. Caching immutable
post-migration schema capabilities reduced enqueue p95 from 899 ms to 30.612 ms. Operations'
redundant fresh readiness connection initially produced a 4.60 s snapshot; reusing the already-live
request session reduced it to 0.554 s cold and 0.149 s at one million rows.

## Test results

- Relevant observability/background/worker/supervisor/readiness/CERI/Winner/pipeline/DB-monitor/
  Operations unit set: **641 passed, 0 failed, 0 skipped, 7 deselected**.
- Full repository unit lane, excluding explicitly marked integration/e2e/external/destructive/
  performance tests: **1,951 passed, 0 failed, 0 skipped, 171 deselected**.
- PostgreSQL remediation set excluding the separate million-row gate: **9 passed, 0 failed,
  0 skipped, 1 deselected**.
- Million-row Operations gate: **1 passed, 0 failed, 0 skipped**.
- PostgreSQL causality and bounded Operations integration: **4 passed, 0 failed, 0 skipped**.
- External worker/process and worker-progress PostgreSQL integration: **5 passed, 0 failed,
  0 skipped**.
- Metrics/CERI performance checks: **2 passed, 0 failed, 0 skipped**.
- Real Windows/Docker cross-process script: **PASS**.

The unrestricted all-lanes suite was not run because several unrelated integration/e2e lanes use
external IB/browser infrastructure or the configured database; doing so would violate the explicit
working-database protection rule. All OBS-001–OBS-013 integration certifications ran against
disposable databases.

## Migration operational notes

0065 is additive except for replacing the active request-key partial unique index. PostgreSQL builds
the replacement concurrently before dropping/renaming the old index. Existing conflicting
RECOVERING rows make the build fail closed, leaving the old fence in place for operator resolution.
The enqueue-attempt and provider indexes are also built concurrently. Downgrade recreates the legacy
QUEUED/RUNNING fence concurrently, then removes 0065-only indexes/table/columns; its disposable
downgrade/re-upgrade round trip passed. On very large tables, concurrent builds avoid long write
locks but require extra temporary disk and take longer than a blocking build.

## Final verdict

OBS-001 through OBS-013 are all **PASS**. The required business-transaction → PostgreSQL commit →
durable evidence → bounded metrics → Prometheus chain is certified. No OBS-014–OBS-018 behavior was
redesigned; those findings remain outside this remediation scope.
