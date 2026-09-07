# SwingLens alert rules

Prometheus loads `monitoring/prometheus/alerts.yml`. Rules use aggregate, bounded dimensions and do
not require Alertmanager. Grafana and Prometheus display firing alerts locally; notification routing
can be added later without application changes.

| Alert | Default condition | Hold | Operator response |
| --- | --- | ---: | --- |
| `SwingLensWorkerMissing` | Worker scrape target absent/down or all `worker_up` zero | 1m | Inspect supervisor and worker JSON logs, then causality for interrupted work. |
| `SwingLensSupervisorMissing` | Supervisor target absent/down or supervisor gauge zero | 1m | Check web child-process manager and supervisor registration. |
| `SwingLensJobStalled` | Any stalled-job gauge is nonzero | 1m | Open Operations, inspect progress age and root tree. |
| `SwingLensQueueBacklog` | Oldest item over 1800s or queued depth over 1000 | 5m | Identify queue class, worker health, and provider/DB pressure. |
| `SwingLensJobFanoutAnomaly` | A per-root rollup crosses its workflow-family descendant or depth threshold | 2m | Inspect that exact root's created, coalesced, rejected, descendant and depth evidence in PostgreSQL. No work is auto-cancelled. |
| `SwingLensRepeatedJobFailures` | More than 3 failures for a job type in 15m | 2m | Follow the root and inspect redacted failure events. |
| `SwingLensWorkerMemoryCritical` | Worker memory status is `CRITICAL` | 1m | Let supervisor fencing/recovery complete; inspect workload and memory checkpoints. |
| `SwingLensDatabasePressure` | Checked-out connections exceed 95% of pool plus overflow | 2m | Inspect pool wait, slow queries, long transactions, then the Flight Recorder. |
| `SwingLensCriticalTelemetryLoss` | Any P0 drop or writer error in 5m | immediate | Protect disk capacity and inspect writer state; P0 evidence loss is readiness-impacting. |
| `SwingLensDiskPressure` | Any monitored path has under 5% free | 5m | Free/extend storage without deleting current forensic evidence blindly. |

The alert thresholds are deliberately conservative. Fanout thresholds are configured per bounded
workflow family; unrelated roots are never summed into a false fanout incident, and root IDs never
become Prometheus labels. Application readiness uses configurable environment values for queue,
disk, DB-pool, and fanout policy. When changing a Prometheus rule,
change the corresponding setting and this table together, validate the rule, and observe at least
one normal workload window before tightening further.

Useful checks:

```powershell
docker compose -f docker-compose.observability.yml config
docker run --rm -v "${PWD}/monitoring/prometheus:/etc/prometheus:ro" `
  prom/prometheus:v3.5.0 promtool check config /etc/prometheus/prometheus.yml
```

The second command requires a running Docker engine. Unit tests always verify the exact ten rule
names and reject forbidden high-cardinality matchers.
