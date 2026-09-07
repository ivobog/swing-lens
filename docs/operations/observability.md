# SwingLens observability architecture

SwingLens uses three complementary stores. Prometheus holds restart-safe time series; PostgreSQL
holds durable job causality and provider evidence; the SQL Flight Recorder keeps detailed JSONL
forensics. None of these paths changes queue ordering, scoring, market-data, CERI, or Winner
semantics.

## Process topology

The web process starts an out-of-process supervisor, which starts and replaces the durable worker.
Each process owns a normal Prometheus registry and a distinct scrape target:

| Process | Endpoint | Content |
| --- | --- | --- |
| Web | `http://127.0.0.1:8000/metrics` | Queue/system state, web resources, pipeline and request metrics |
| Worker | `http://127.0.0.1:9101/metrics` | Worker resources and job/provider/pipeline activity |
| Supervisor | `http://127.0.0.1:9102/metrics` | Supervisor health and cumulative worker restarts |

This separately-scraped design is intentional. It was selected over Prometheus multiprocess mode
because SwingLens uses independently restarted Windows subprocesses; shared multiprocess files
would require ambiguous cleanup ownership during supervisor replacement. Prometheus combines the
three targets and retains 30 days locally, so history survives application restarts.

The web process samples PostgreSQL-backed system state every
`OBSERVABILITY_COLLECTION_INTERVAL_SECONDS`. Database size uses the slower
`OBSERVABILITY_DB_SIZE_INTERVAL_SECONDS`. `/metrics` only serializes the registry; it does not run
queue, disk, or database-size queries.

## Causality

HTTP middleware accepts safe `X-Request-ID` and `X-Root-Correlation-ID` values or creates them, and
returns both headers. Scheduler and administrative entry points create roots. A task-safe
`ContextVar` carries root and immediate causation through the worker and is always reset by its
scope manager.

New jobs persist `root_correlation_id`, `causation_id`, `parent_job_id`,
`triggered_by_job_id`, `trigger_kind`, `trigger_name`, `triggered_by_request_id`, and
`fanout_group_id`. Retries and recovery update the existing row and therefore preserve the root.
Legacy rows remain null; no history is fabricated.

Every meaningful new or coalesced enqueue decision also creates an append-only
`background_job_enqueue_attempts` row. This is the authoritative normalized equivalent of placing
`coalesced_into_job_id` on a job row that was deliberately not created. CERI processing runs and
provider-request telemetry copy the active root, cause, request, and job IDs.

Winner's periodic eligibility check returns before creating a root or enqueue attempt when the
completed job for the current US session already proves that no work is needed. Attempt evidence is
pruned according to `OBSERVABILITY_ENQUEUE_ATTEMPT_RETENTION_DAYS`.

`background_job_fanout_roots` is the bounded per-root rollup used by Operations and alerts. It
tracks attempted, created, coalesced and rejected enqueues, descendant count, maximum depth and a
job-family distribution. Prometheus uses only the bounded workflow family; a root correlation ID is
never a metric label.

Use the local-admin endpoint:

```text
GET /ops/system/causality/{root_correlation_id}
```

Results are bounded by `OBSERVABILITY_OPERATIONS_LIMIT` and include jobs, enqueue attempts,
child/coalescing edges, and attempted/created/coalesced fanout totals.

## Metrics and labels

`app/observability/metrics.py` defines real Prometheus counters, gauges, and histograms. The former
`operational_metrics` import is a compatibility facade over that registry. Every metric is declared
in a closed catalog with type, description, unit, labels, allowed-values policy and histogram
buckets where applicable; runtime calls cannot create undeclared metrics. CERI's local registry is
a stateless adapter with no retained sample list; durable CERI provider telemetry remains in
PostgreSQL.

Commit-dependent metrics are queued on the SQLAlchemy session. Nested commits merge into their
outer transaction, rollback discards pending publications, and only the successful outer commit
publishes them. Metric-client failure after commit is contained and cannot invalidate business
state or replace the original application exception.

Allowed labels are bounded operational dimensions: job type, status, stage, queue class, provider,
dataset, result, reason code, process role, telemetry priority, and the configured bounded worker
ID. The facade rejects job/run/request/correlation IDs, workflow/request keys, tickers, companies,
execution tokens, SQL fingerprints, errors, and filesystem paths as labels.

The mandatory inventory is grouped below. Names ending in `_seconds` that represent distributions
are Prometheus histograms.

- Jobs/queues: `swinglens_jobs_enqueued_total`, `swinglens_jobs_coalesced_total`,
  `swinglens_jobs_finished_total`, `swinglens_jobs_retry_total`,
  `swinglens_jobs_failed_total`, `swinglens_job_progress_total`, `swinglens_queue_depth`,
  `swinglens_queue_oldest_age_seconds`, `swinglens_jobs_running`,
  `swinglens_jobs_stalled`, `swinglens_jobs_recovering`, `swinglens_job_wait_seconds`,
  `swinglens_job_duration_seconds`, `swinglens_job_progress_age_seconds`,
  `swinglens_job_fanout_total`, `swinglens_job_fanout_size`.
- Processes: `swinglens_worker_up`, `swinglens_worker_heartbeat_age_seconds`,
  `swinglens_worker_rss_bytes`, `swinglens_worker_private_bytes`,
  `swinglens_worker_cpu_percent`, `swinglens_worker_restarts_total`,
  `swinglens_worker_memory_status`, `swinglens_supervisor_up`,
  `swinglens_supervisor_heartbeat_age_seconds`, `swinglens_process_cpu_percent`, and
  `swinglens_process_rss_bytes`.
- Pipeline: `swinglens_pipelines_started_total`, `swinglens_pipelines_finished_total`,
  `swinglens_pipeline_duration_seconds`, `swinglens_pipeline_stage_duration_seconds`,
  `swinglens_pipeline_failures_total`, `swinglens_pipeline_current_stage`, and
  `swinglens_pipeline_active`.
- Providers: `swinglens_provider_requests_total`,
  `swinglens_provider_request_duration_seconds`, `swinglens_provider_response_bytes_total`,
  `swinglens_provider_stored_bytes_total`, `swinglens_provider_retries_total`,
  `swinglens_ceri_ingestion_total`, `swinglens_ceri_normalization_total`,
  `swinglens_ceri_feature_rebuild_total`, `swinglens_ceri_scoring_total`,
  `swinglens_ib_connected`, `swinglens_ib_fetch_requests_total`, and
  `swinglens_ib_fetch_duration_seconds`. Existing IB Market Intelligence metrics remain available.
- Database/storage: `swinglens_db_pool_size`, `swinglens_db_pool_checked_out`,
  `swinglens_db_pool_overflow`, `swinglens_db_pool_wait_seconds`,
  `swinglens_db_slow_queries_total`, `swinglens_db_long_transactions_total`,
  `swinglens_db_monitor_records_total`, `swinglens_db_monitor_written_total`,
  `swinglens_db_monitor_dropped_total`, `swinglens_db_monitor_queue_depth`,
  `swinglens_db_monitor_writer_errors_total`, `swinglens_db_monitor_writer_latency_seconds`,
  `swinglens_disk_free_bytes`, `swinglens_disk_total_bytes`, `swinglens_db_size_bytes`, and
  `swinglens_log_storage_bytes`.

## Structured logs

`app/serve.py`, `app/worker.py`, and `app/worker_supervisor.py` configure the shared JSON formatter.
The stable envelope includes timestamp, severity, event, process role/ID, request/root/cause/job
lineage, run/workflow/request identity, worker, stage/status/reason, provider/dataset, versions, and
duration. Non-applicable fields are null. Existing logger calls are serialized by the same formatter;
critical paths also use `log_event`.

Shared redaction removes credentials and bearer values, sensitive field values, SQL detail, local
user paths, payloads, and execution tokens. Detailed SQL parameters are never logged.

## Readiness

`/health` remains shallow liveness. `/ready` exposes `ok`, `degraded`, `failed`, and
`optional_unavailable` check states. Overall `failed` returns HTTP 503; `ok` or `degraded` returns
HTTP 200. Database/migration/storage failures, required IB absence, missing worker/supervisor,
worker SQL-recorder failure and dead collectors are explicit rather than web-local assumptions.

Queue readiness reports runnable, scheduled-future, blocked, recovering, stalled and running work.
Only `QUEUED` rows with `run_after <= now()` contribute to runnable backlog and oldest runnable age.
DB-pool pressure uses checked-out connections divided by base size plus configured maximum overflow;
current overflow usage is reported separately. Recent waits and timeouts can degrade readiness even
before the pool is fully exhausted.

See [alerts.md](alerts.md), [system_operations.md](system_operations.md),
[prometheus_grafana.md](prometheus_grafana.md), and [database_monitor.md](database_monitor.md).
