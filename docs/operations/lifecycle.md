# Unified Local Lifecycle

SwingLens has one daily operator interface:

```powershell
.\swinglens.ps1 start
.\swinglens.ps1 status
.\swinglens.ps1 restart
.\swinglens.ps1 stop
```

## Ownership model

The database selected by the effective `DATABASE_URL` is the authoritative locally installed
Windows PostgreSQL database. The lifecycle correlates the endpoint, listener process tree, service
executable, PostgreSQL data directory, and `postgresql.conf` port before controlling a service. It
does not guess a service name. If the evidence is missing or ambiguous, it fails without starting
or stopping any PostgreSQL service.

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

`start` loads `.env` without rewriting it, probes the configured database, and reuses it when it is
reachable. Otherwise it starts only the uniquely verified local PostgreSQL Windows service and
waits for a SQL connection. It then runs `uv run alembic upgrade head`; a failure prevents a new web
listener. `app.serve` independently verifies the database, Alembic head, and local storage before
Uvicorn binds.

Port 8000 is handled idempotently. A verified healthy SwingLens process is reused. A verified but
unhealthy SwingLens process is reported without being killed by `start`. A foreign listener causes
`CONFLICT` with its PID and process name.

After core readiness, the lifecycle verifies that `GRAFANA_ADMIN_PASSWORD` exists without printing
it, starts only the observability Compose file, checks Prometheus and Grafana, and expects the
`swinglens-web`, `swinglens-worker`, and `swinglens-supervisor` targets to be 3/3 UP.

## Stop and restart

`stop` first verifies the web, supervisor, and worker identities and refuses to interrupt active
`RUNNING` or `RECOVERING` durable jobs. It requests a controlled web shutdown so the lifespan stops
the supervisor and worker through their ownership chain, then verifies ports 8000, 9101, and 9102
are released. Any fallback termination is limited to a revalidated PID tree; broad Python or
PostgreSQL process-name termination is forbidden.

Grafana and Prometheus are stopped individually through the observability Compose file. The
conservative default is `SWINGLENS_MANAGE_POSTGRES=false`, because a system-wide Windows service
may be shared or require an elevated service-control token. With that setting, the exact service is
reported but deliberately left running. Set `SWINGLENS_MANAGE_POSTGRES=true` only when full-stack
service shutdown is wanted and the shell is authorized; the lifecycle then stops only the verified
service through Windows service control. No database files or Docker volumes are deleted.

`restart` is the same `stop` implementation followed by the same `start` implementation.

## Status

`status` is read-only and reports core service/endpoint/schema/process health, Docker Engine,
Prometheus, Grafana, the three expected targets, and one of `HEALTHY`, `DEGRADED`, `FAILED`,
`STOPPED`, or `CONFLICT`. Database credentials and the Grafana password are never emitted.

## Disposable PostgreSQL Compose file

`docker-compose.postgres-test.yml` is quarantined test infrastructure. It uses the separate Compose
project `swinglens-postgres-test`, container `swinglens-postgres-test`, host port 5433, and database
name `swinglens_disposable`. It is not used by CI, routine lifecycle, certification, backup, or
restore. Use it only when explicitly creating isolated disposable test infrastructure, and never
point the real SwingLens `DATABASE_URL` at it.
