# Lifecycle convergence baseline — 2026-09-11

This report records the required non-destructive baseline before lifecycle convergence edits. No process, service, listener, schema, job, or business evidence was changed while collecting it. Secret values are omitted or reduced to presence only.

## Repository

- Repository root: `C:\Users\Ivica\Documents\SwingLens`
- Branch: `codex/final-certification-runtime-topology-remediation`
- HEAD: `e61bdbd76551a78d5c83cf077f7093f94c66e86b`
- Upstream default: `origin/main`
- Merge base with `origin/main`: `1912eb4e5a1fb371e3898cb8971afd4e41cce048`
- `git status --short`: clean (no modified, staged, deleted, or untracked files)
- Existing dirty/untracked user files: none

## Toolchain

- Python: `3.12.2`
- Python executable: `C:\Users\Ivica\AppData\Local\Programs\Python\Python312\python.exe`
- Repository virtual environment: `.venv`
- PowerShell: `7.6.5` Core

## Sanitized effective runtime configuration

The ignored local `.env` was inspected read-only. The application settings object confirms the following effective values and sources. No secret value was printed.

| Setting | Effective value | Source |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://127.0.0.1:5432/swinglens` | `.env` |
| `USE_DURABLE_PIPELINE` | `true` | `.env` |
| `DURABLE_WORKER_PROCESS_ENABLED` | `true` | compatibility mapping from `.env` `JOB_WORKER_ENABLED=true` |
| `EMBEDDED_JOB_WORKER_ENABLED` | `false` | application default |
| `JOB_WORKER_ENABLED` | `true` | `.env` (legacy input) |
| `RUNTIME_MODE` | `NORMAL` | application default |
| `APP_HOST` | `127.0.0.1` | `.env` |
| `APP_PORT` | `8000` | `.env` |
| `OBSERVABILITY_METRICS_HOST` | `127.0.0.1` | `.env` |
| `OBSERVABILITY_WORKER_METRICS_PORT` | `9101` | `.env` |
| `OBSERVABILITY_SUPERVISOR_METRICS_PORT` | `9102` | `.env` |
| `SWINGLENS_POSTGRES_SERVICE` | `postgresql-x64-18` | `.env` |
| `SWINGLENS_POSTGRES_EXPECTED_MAJOR` | `18` | `.env` |
| `SWINGLENS_POSTGRES_DATA_DIR` | `C:\Program Files\PostgreSQL\18\data` | `.env` |
| `SWINGLENS_POSTGRES_EXECUTABLE` | `C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe` | `.env` |
| `SWINGLENS_MANAGE_POSTGRES` | `false` | `.env` |
| `GRAFANA_ADMIN_PASSWORD` | `PRESENT` | `.env` |

No lifecycle-critical process-environment override was present before invoking the controller. The controller itself currently sets `PROCESS_ROLE=CLI_OR_MAINTENANCE`; certification mode also applies explicit process-level overrides.

## Runtime and listeners

`pwsh -File .\swinglens.ps1 status` reported:

```text
Database           READY
Schema             HEAD
Web/API            STOPPED
Application        STOPPED
Worker             STOPPED
Disk               UNAVAILABLE
IB API             STOPPED
Prometheus         UNAVAILABLE
Grafana            UNAVAILABLE
OVERALL            STOPPED
```

There were no actual SwingLens supervisor, web, or worker processes. The only process matched by the broad collection expression was the temporary read-only PowerShell probe itself, so it is excluded from the runtime tree.

| Port | Listener |
|---|---|
| 8000 | none |
| 9101 | none |
| 9102 | none |
| 9090 | none |
| 3000 | none |
| 5432 | PID 6976, `postgres.exe`, listening on `0.0.0.0` and `::` |

HTTP probes to web `/health` and `/ready`, Prometheus `/-/ready` and `/api/v1/targets`, and Grafana `/api/health` were refused because those components were stopped.

## Authoritative PostgreSQL

- Sanitized endpoint: `postgresql+psycopg://127.0.0.1:5432/swinglens`
- Server version: `18.3`
- SQL-reported data directory: `C:/Program Files/PostgreSQL/18/data`
- SQL server address/port: `127.0.0.1/32:5432`
- Windows service: `postgresql-x64-18`
- Service state/start mode: `Running` / `Auto`
- Service identity: `NT AUTHORITY\NetworkService`
- Service PID: `5040`
- Service command: `C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe runservice -N postgresql-x64-18 -D C:\Program Files\PostgreSQL\18\data -w`
- Listener PID: `6976`
- An unrelated `pgagent-pg17` service exists but is stopped; it is not the configured database service.

## Schema and durable registrations

- Database Alembic revision: `0072_ceri_artifact_context_lineage`
- Repository head set: `{0072_ceri_artifact_context_lineage}`
- Schema state: at head
- Supervisor registration rows: one historical/stopped `local-worker-1` row, generation 45, PID 13820, last heartbeat/stopping timestamp `2026-09-11T14:50:35+02:00`
- Worker registration rows: historical rows only. The most recent canonical `local-worker-1` row is generation 49, PID 12036, last heartbeat/stopping timestamp `2026-09-11T14:50:35+02:00`, quiesced at `2026-09-11T14:50:29+02:00`.
- Active `RUNNING` jobs: none
- Active `RECOVERING` jobs: none
- Fresh active lease blockers: none

The database contains older historical worker registration rows from prior local/certification work. They are stale and no corresponding process is alive; no row was modified during baseline collection.

## Docker and observability

- Docker CLI: available
- Docker Engine: reachable, Docker Desktop `4.85.0`, Engine `29.6.2`, Linux containers
- Observability Compose services: no running services reported
- Prometheus: stopped/unreachable
- Grafana: stopped/unreachable
- Expected Prometheus targets `swinglens-web`, `swinglens-worker`, and `swinglens-supervisor`: unavailable because Prometheus is stopped; no `3/3 UP` claim is made

## CI for baseline HEAD

Latest canonical CI for this exact HEAD is [run 34601813963](https://github.com/ivobog/swing-lens/actions/runs/34601813963), triggered by push at `2026-09-11T12:59:32Z`. It completed **failure**:

- `Lint, Test, and Migration Gates`: failed at `Upgrade CI database`. The PostgreSQL 16 service was treated as authoritative-local and rejected because configured expected major is 18: `LifecycleConflict: PostgreSQL server major version does not match configuration`.
- `Chromium and Firefox Smoke`: 21 setup errors. Its disposable PostgreSQL 16 migration followed the same authoritative-local path and failed with the same major-version conflict.
- `Windows Durable Worker Recovery Gate`: both parameterized cases failed before exercising recovery. The purposely created temporary PostgreSQL 18 database was treated as authoritative-local and rejected because its temporary data directory did not match `C:\Program Files\PostgreSQL\18\data`.
- Lint, tracked-secret scan, route inventory, and Alembic head-graph steps were green before the Linux migration failure.

The relevant pull request (#71) is already merged, but the current branch/HEAD remains red in canonical CI. Therefore the baseline verdict is **NOT CERTIFIED**.

## Baseline constraints for remediation

- No live business job or fresh lease blocks static work or disposable tests.
- The real runtime is stopped, so no running generation may be silently migrated underneath.
- The authoritative database already satisfies the strict PostgreSQL 18 service, path, endpoint, and schema expectations. Those production checks must remain strict.
- Real-machine lifecycle certification is deferred until safe/disposable tests and canonical CI are green.
