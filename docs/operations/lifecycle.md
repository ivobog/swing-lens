# Unified Local Lifecycle

SwingLens has one daily operator interface:

```powershell
pwsh .\swinglens.ps1 start
pwsh .\swinglens.ps1 status
pwsh .\swinglens.ps1 restart
pwsh .\swinglens.ps1 stop
```

## Ownership model

PowerShell 7.4 or newer is required. Python/Pydantic is the only `.env` parser. The database selected
by the effective `DATABASE_URL` must also match the configured service name, major version, service
executable, SQL-reported data directory, database name, listener PID/creation time/executable, and
listener-to-service process ancestry. If any signal is missing or ambiguous, the lifecycle reports
`CONFLICT` and does not run Alembic or control PostgreSQL.

The normal process tree is:

```text
app.serve (JOB_WORKER_ENABLED=true)
  -> SupervisorProcessManager
     -> app.worker_supervisor
        -> app.worker (JOB_WORKER_ENABLED=false)
```

Do not manually start a second supervisor.

Docker is an optional observability dependency only. Normal commands use exactly
`docker-compose.observability.yml` for Prometheus and Grafana. Docker Desktop itself is never
stopped. If Docker is unavailable, healthy core services remain running and overall status is
`DEGRADED`.

## Start

`start` obtains sanitized configuration from the Python lifecycle probe. It reuses a strongly
identified runtime at the same Git commit without migrating under it. Otherwise it proves database
provenance, then runs bounded Alembic under both the full lifecycle mutex and a PostgreSQL advisory
migration lock. `app.serve` independently verifies the database, the complete Alembic head set, and
local storage before Uvicorn binds.

Port 8000 is handled idempotently. A verified healthy SwingLens process is reused. A verified but
unhealthy SwingLens process is reported without being killed by `start`. A foreign listener causes
`CONFLICT` with its PID and process name.

After core readiness, the lifecycle verifies that `GRAFANA_ADMIN_PASSWORD` exists without printing
it, starts only the observability Compose file, checks Prometheus and Grafana, and expects the
`swinglens-web`, `swinglens-worker`, and `swinglens-supervisor` targets to be 3/3 UP.

## Stop and restart

`stop` first writes a durable quiesce request. Every claim transaction locks and checks that same
worker registration row, so acknowledgement means no later job can become `RUNNING`. It then
re-checks `RUNNING` and `RECOVERING` leases. `QUEUED`, future-scheduled, `BLOCKED`, and `STALLED` jobs
may remain durable. An active lease aborts stop and clears quiesce. Process signaling requires PID,
creation time, role/module, checkout path, runtime/registry instance identity, generation, and web
listener ownership; the tuple is inspected again immediately before signaling. Metrics listeners
are supplementary only and may be disabled or moved.

One repository-scoped Windows named mutex covers the entire `start`, `stop`, or `restart`
transition, including stop-plus-start for restart. Acquisition is bounded by
`SWINGLENS_LIFECYCLE_LOCK_TIMEOUT_SECONDS`.

Grafana and Prometheus are stopped individually through the observability Compose file. Their stop
or start failures and bounded CLI timeouts are warnings and never prevent core restart recovery. The
conservative default is `SWINGLENS_MANAGE_POSTGRES=false`, because a system-wide Windows service
may be shared or require an elevated service-control token. With that setting, the exact service is
reported but deliberately left running. Set `SWINGLENS_MANAGE_POSTGRES=true` only when full-stack
service shutdown is wanted and the shell is authorized; the lifecycle then stops only the verified
service through Windows service control. No database files or Docker volumes are deleted.

`restart` is the same `stop` implementation followed by the same `start` implementation.

## Status

`status` is read-only and reports core service/endpoint/schema/process health, Docker Engine,
Prometheus, Grafana, and one of `HEALTHY`, `DEGRADED`, `FAILED`, `STOPPED`, or `CONFLICT`.
Application readiness JSON, not HTTP status alone, determines core health. Exit codes are 0 for
healthy/stopped-as-requested, 2 for degraded, and 1 for failed, conflict, or incomplete operations.
Database credentials and the Grafana password are never emitted.

## Disposable PostgreSQL Compose file

`docker-compose.postgres-test.yml` is quarantined test infrastructure. It uses the separate Compose
project `swinglens-postgres-test`, container `swinglens-postgres-test`, host port 5433, and database
name `swinglens_disposable`. It is not used by CI, routine lifecycle, certification, backup, or
restore. Use it only when explicitly creating isolated disposable test infrastructure, and never
point the real SwingLens `DATABASE_URL` at it.
