# System Operations

Open `http://127.0.0.1:8000/system/operations` from the local host. The HTML page and its JSON APIs
are local-administrator only.

The page is a bounded current-state view, not a replacement for Prometheus history or SQL forensics:

- Health: web, PostgreSQL, migration head, supervisor, worker registration/heartbeat/capability,
  jobs, disk/storage, DB pool, SEC/CERI, telemetry, and the most recent observed IB state.
- Supervisor/worker: instance and process identity, generation, heartbeat age, CPU, RSS/private
  bytes, memory state, current job, stage, counts, and progress age.
- Queues: runnable, scheduled-future, blocked, running, stalled and recovering by bounded queue
  class, failures in 24 hours, and oldest runnable age. Future `run_after` jobs never inflate
  runnable backlog or age.
- Pipelines: up to five active runs with stage elapsed/progress and ten recent failures.
- Database: pool state, recorder queue/capacity, writer errors/latency, priority drops, slow-query and
  long-transaction counts, and ten in-memory safe fingerprints. Query text and bind values are not
  shown.
- Providers: 24-hour request/failure/retry aggregates, p95 latency, and latest observed time.
- Anomalies: readiness failures plus backlog, stalls, memory, provider degradation, DB/telemetry,
  disk, and exact durable root fanout thresholds.
- Causality: the 20 most recent roots and links to their bounded job/attempt trees.

JSON endpoints:

```text
GET /api/system/operations
GET /ops/system/causality/{root_correlation_id}
GET /api/system/operations/causality/{root_correlation_id}
```

Detailed tree results are capped by `OBSERVABILITY_OPERATIONS_LIMIT` (10–500, default 100). A
`truncated` flag tells the client when the cap was reached. The page uses aggregate PostgreSQL
queries and eager-loads bounded pipeline steps; it never performs a list-all query or scrapes
Prometheus on request.

IB health is `optional_unavailable` while IB is down and no runnable/active work requires it. A
bounded aggregate query promotes IB to required for `FULL_PIPELINE`, `MARKET_DATA_PREWARM`, or
registered `IB_*` work; unavailable IB then makes readiness fail. Operations does not perform an
IB network probe. Resource-sampler and web-owned system-collector health are reported separately,
as are process presence and functional worker/supervisor control-loop progress.

## Troubleshooting order

1. Start with the health grid and current anomalies.
2. Check supervisor and worker identity/heartbeat before changing job state.
3. Identify queue class and the oldest work.
4. Open the root tree for stalled, multiplied, or coalesced work.
5. Use Grafana for historical direction and alert timing.
6. Use the SQL Flight Recorder analyzer only when DB detail is needed.

Recent roots come from the indexed `background_job_fanout_roots` rollup and are capped at 20.
Provider p95 uses a time-leading covering index and only the latest 10,000 rows in the configured
time window. The Operations readiness result has a short TTL and reuses the request's already-live
database session for its liveness probe. Certification at 10, 1,000, 100,000 and 1,000,000 rows
kept full snapshot time below 0.6 seconds and bounded the root/provider reads to 20/10,000 rows.

The page is observational. It does not restart, cancel, retry, or mutate work.
