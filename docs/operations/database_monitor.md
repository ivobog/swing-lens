# SQL Flight Recorder Operations

SwingLens instruments the shared SQLAlchemy engine in `app/db.py`. The recorder is
observability-only: it does not change statements, parameters, transactions, ORM flush
behavior, queue ordering, or domain calculations.

## Configuration

| Setting | Default | Purpose |
| --- | ---: | --- |
| `DB_MONITOR_ENABLED` | `true` | Installs the engine hooks and correlation scopes. |
| `DB_MONITOR_SLOW_QUERY_MS` | `100` | Marks an execution as slow. |
| `DB_MONITOR_FULL_TRACE_MS` | `250` | Captures a compact application stack. |
| `DB_MONITOR_FULL_STACK_FOR_ALL_SQL` | `false` | Short-session debug override only. |
| `DB_MONITOR_RETENTION_DAYS` | `8` | Deletes older JSONL segments. |
| `DB_MONITOR_LOG_DIR` | `logs/db-monitor` | Independent trace sink. |
| `DB_MONITOR_QUEUE_SIZE` | `10000` | Bounded nonblocking telemetry queue. |
| `DB_MONITOR_MAX_FILE_MB` | `100` | Size segment bound in addition to daily rotation. |
| `DB_MONITOR_MAX_STACK_FRAMES` | `20` | Maximum useful application frames per full trace. |
| `DB_MONITOR_N_PLUS_ONE_THRESHOLD` | `10` | Repeated calls in one scope considered an N+1 candidate. |
| `DB_MONITOR_ACTIVITY_SAMPLER_ENABLED` | `false` | Enables the optional `pg_stat_activity` sampler. |
| `DB_MONITOR_ACTIVITY_THRESHOLD_MS` | `1500` | Active-query sample threshold. |
| `DB_MONITOR_ACTIVITY_SAMPLE_INTERVAL_SECONDS` | `10` | Sampler cadence. |

Files use `sql-YYYY-MM-DD-pPID.jsonl` with numbered size segments. Process-specific
filenames prevent the API and worker from interleaving writes. The writer reports
`records_written`, `db_monitor_dropped_records`, queue depth, and write failures in
`monitor_status` records. Queue saturation drops telemetry instead of blocking SQL.

SQL records contain normalized SQL and a SHA-256 fingerprint, but never bind values.
The safe parameter description contains only container shape, parameter count, batch
size, and Python type names. String and numeric literals and trace-only SQL comments are
removed during normalization. Error summaries are length-bounded and literal-redacted.

The engine hooks are `before_cursor_execute`, `after_cursor_execute`, and `handle_error`.
An `after_flush` session hook records only new/dirty/deleted object counts. A statement
may opt out with the SQLAlchemy execution option `db_monitor_excluded=true`; the dedicated
health sampler uses a separate engine and this exclusion as defense in depth.

## Correlation and summaries

FastAPI middleware propagates or creates `X-Request-ID` and uses a task-safe `ContextVar`.
SQL inherits the method, actual path, resolved route name/template, and request ID. A
`finally` block emits `request_summary` and resets both context variables.

The worker sets job context immediately around the registered handler and resets it in a
`finally` block. SQL inherits real values only: job ID/type, related run, worker,
workflow key, and singular ticker/company fields when present. Each handler execution
emits `job_summary`, including failed, deferred, and cancelled handlers.

Both summaries include operation counts, total/max SQL time, unique/duplicate
fingerprints, the most expensive and most repeated shape, top expensive shapes, and ORM
flush counts. `CERI_FEATURE_BATCH` also places the current SQL snapshot alongside its
feature-specific telemetry.

## Analysis CLI

Seven-day text report:

```powershell
uv run python scripts/analyze_db_monitor.py --hours 168
```

Machine-readable report and fingerprint detail:

```powershell
uv run python scripts/analyze_db_monitor.py `
  --hours 168 `
  --format json `
  --output output/db-monitor-week.json

uv run python scripts/analyze_db_monitor.py `
  --hours 168 `
  --fingerprint 28fa...
```

The analyzer streams files and skips an incomplete final line. It reports slowest
executions, cumulative/call/average fingerprint rankings, route and job aggregates,
N+1 candidates, and write-heavy origins. Tune N+1 sensitivity with
`--n-plus-one-threshold`; slow-average rankings use `--min-average-calls`.

## PostgreSQL complement status (2026-08-15)

A read-only inspection found one active SwingLens background job. No process was stopped
or restarted and no PostgreSQL setting or extension was changed.

- `pg_stat_statements` is available but not installed.
- `shared_preload_libraries` is empty, so enabling `pg_stat_statements` requires a
  PostgreSQL configuration change and restart.
- `pg_stat_statements.track` is not currently set. At a safe maintenance boundary,
  configure the preload, restart, create the extension, and set tracking to `all` after
  validating version-specific syntax.
- `auto_explain` is not preloaded and is not exposed in `pg_available_extensions` on this
  installation. It remains disabled. Do not set `auto_explain.log_min_duration=0`; if the
  module is installed later, begin at `500ms` without global `log_analyze`.
- The application `pg_stat_activity` sampler is implemented but defaults off. It can be
  enabled without making statement capture depend on PostgreSQL extensions.

Perform the `pg_stat_statements` restart only after the authoritative worker cutover
check shows no active feature job. Application instrumentation and its tests do not wait
for that maintenance window.

## Sanitized record examples

HTTP query:

```json
{"record_type":"sql","operation":"SELECT","duration_ms":12.4,"normalized_sql":"SELECT score FROM ceri_snapshots WHERE run_id = ?","query_fingerprint":"28fa...","origin_type":"HTTP","request_id":"req-example","http_method":"GET","http_path":"/ceri","route_path":"/ceri","python_caller":{"source_file":"app/services/ceri/query_service.py","line_number":122,"function":"CeriQueryService.current_scores"},"parameter_shape":{"shape":"mapping","parameter_count":1,"parameter_types":["int"],"batch_size":1},"success":true}
```

Background query:

```json
{"record_type":"sql","operation":"UPDATE","duration_ms":18.1,"normalized_sql":"UPDATE ceri_derived_features SET value = ? WHERE id = ?","query_fingerprint":"61be...","origin_type":"BACKGROUND_JOB","job_id":91,"job_type":"CERI_FEATURE_BATCH","run_id":108,"worker_id":"worker-example","success":true}
```

Slow query with compact stack:

```json
{"record_type":"sql","operation":"SELECT","duration_ms":820.0,"slow_query":true,"query_fingerprint":"7cca...","application_stack":[{"source_file":"app/routers/ceri_routes.py","line_number":401,"function":"dashboard"},{"source_file":"app/services/ceri/query_service.py","line_number":122,"function":"CeriQueryService.current_scores"},{"source_file":"app/services/ceri/repository.py","line_number":88,"function":"CeriRepository.load_current"}]}
```

Request summary:

```json
{"record_type":"request_summary","request_id":"req-example","http_method":"GET","route_path":"/ceri","total_duration_ms":1100.0,"sql_query_count":18,"sql_select_count":18,"total_sql_ms":910.0,"sql_time_pct":82.727,"maximum_sql_ms":820.0,"unique_query_fingerprints":9,"duplicate_query_count":9,"most_repeated_query_calls":6}
```

Job summary:

```json
{"record_type":"job_summary","job_id":91,"job_type":"CERI_FEATURE_BATCH","run_id":108,"total_duration_ms":4200.0,"sql_query_count":31,"sql_select_count":11,"sql_insert_count":10,"sql_update_count":10,"total_sql_ms":740.0,"maximum_sql_ms":95.0,"unique_query_fingerprints":14,"duplicate_query_count":17}
```
