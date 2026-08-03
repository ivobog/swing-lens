# Observability Notes

SwingLens exposes lightweight local operational signals without requiring a hosted monitoring stack.

## Liveness And Readiness

- `GET /health` is process liveness. It should stay shallow and answer whether the web process is up.
- `GET /ready` is operational readiness. It degrades when database, migration, storage, worker, or
  stale-job checks fail.
- Readiness error messages are passed through shared redaction before returning to the caller.

Readiness checks:

| Check | Degrades When |
| --- | --- |
| `database` | The app cannot run `select 1`. |
| `migrations` | `alembic_version` does not match repository head. |
| `storage` | Upload, export, or cache directories cannot be created or probed. |
| `worker` | Durable pipeline is enabled while the local embedded worker is disabled. |
| `jobs` | Running background jobs have expired leases. |

## Metrics

`GET /metrics` returns Prometheus text format from an in-process counter registry.

Initial counters:

| Metric | Labels | Meaning |
| --- | --- | --- |
| `swinglens_jobs_enqueued_total` | `job_type` | Background jobs accepted for execution. |
| `swinglens_jobs_coalesced_total` | `job_type` | Duplicate active job requests coalesced. |
| `swinglens_jobs_finished_total` | `job_type`, `status` | Jobs reaching a terminal or partial status. |
| `swinglens_jobs_retry_total` | `job_type`, `status` | Jobs requeued for retry. |
| `swinglens_jobs_failed_total` | `job_type`, `status` | Jobs exhausted retries and failed. |
| `swinglens_pipelines_started_total` | `step_count` | Full pipelines queued. |
| `swinglens_pipelines_coalesced_total` | `status` | Duplicate pipeline requests attached to active work. |
| `swinglens_pipelines_cancel_requested_total` | `status` | Pipeline cancellation requests. |
| `swinglens_exports_generated_total` | `schema_id` | CSV exports generated. |
| `swinglens_export_rows_total` | `schema_id` | CSV rows written. |

The registry is intentionally process-local. Counters reset on process restart.

## Logging Schema

Operational logs should use stable event names and redacted structured fields:

```json
{
  "event": "job.worker.started",
  "worker_id": "local-worker-1",
  "job_type": "FULL_PIPELINE",
  "status": "RUNNING",
  "run_id": 42,
  "correlation_id": "job-123"
}
```

Do not log raw tokens, provider payloads, SQL statements, local file paths, or credentials. Use
`app.services.redaction.redact_sensitive` for dictionaries and `redact_text` for exception strings.
