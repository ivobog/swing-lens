# Unified local lifecycle

SwingLens has one NORMAL operator entry point:

```powershell
pwsh .\swinglens.ps1 start
pwsh .\swinglens.ps1 status
pwsh .\swinglens.ps1 status -Json
pwsh .\swinglens.ps1 diagnose
pwsh .\swinglens.ps1 restart
pwsh .\swinglens.ps1 stop
```

The canonical ownership tree is `lifecycle controller -> app.worker_supervisor -> app.serve +
app.worker`. See [the ownership decision](../architecture/runtime_process_ownership.md). Do not
start a second supervisor or use the web process as a supervisor owner.

## Start and runtime reuse

PowerShell obtains Pydantic-parsed configuration and sanitized source provenance from the Python
probe. Canonical durable startup forces `DURABLE_WORKER_PROCESS_ENABLED=true`,
`EMBEDDED_JOB_WORKER_ENABLED=false`, and legacy `JOB_WORKER_ENABLED=false`. Contradictory raw
configuration fails before launch.

With `AUTHORITATIVE_LOCAL`, the selected database must match its loopback endpoint, configured
database name, PostgreSQL major, exact Windows service and executable, SQL data directory, listener
PID/executable, and listener/service ancestry. Only after that proof may Alembic run under the
repository lifecycle lock and PostgreSQL advisory lock.

The desired generation fingerprint covers Git SHA, normalized checkout, Python executable, runtime
mode, topology version, sanitized database endpoint and verified identity, repository Alembic heads,
critical configuration, ports, and worker modes. An active runtime is reused only when its exact
process tree, database/schema checks, and fingerprint match. A same-Git mismatch returns
`RESTART_REQUIRED`; migrations never run underneath an active runtime.

Start waits on `GET /ready/core`. Prometheus and Grafana are optional: their failure leaves a
core-ready runtime running and produces `DEGRADED`.

## Persisted runtime-state classification

The controller classifies physical process and listener evidence before applying generation
fingerprint semantics:

| Classification | Required interpretation | Controller behavior |
|---|---|---|
| `ACTIVE_VALID` | Recorded identities, topology, listeners, ancestry, and generation are valid. | Reuse the active runtime. |
| `ACTIVE_GENERATION_MISMATCH` | The recorded runtime is strongly verified alive, but its generation differs. | Fail closed with `RESTART_REQUIRED`. |
| `DEAD_STALE` | Every recorded identity is absent and no canonical role process or lifecycle-port listener remains. | Report stopped; `start`, `stop`, or `restart` may retire the record under the lifecycle lock. |
| `AMBIGUOUS_CONFLICT` | Identity reuse/mismatch, a partial surviving topology, an unexpected role process/listener, or insufficient evidence prevents safe proof. | Fail closed with `CONFLICT`; never signal or retire automatically. |
| `MISSING` | No canonical runtime-state file exists. | Report the normal stopped state. |

`status` never changes the runtime-state file. Lock-holding mutating commands revalidate a
`DEAD_STALE` record, archive it under `data/cache/lifecycle-archive`, and append a
`stale_runtime_state_retired` / `DEAD_GENERATION_RETIRED` lifecycle event before continuing. Git SHA
remains part of the generation fingerprint; only a physically dead, unambiguous record bypasses
active-generation mismatch handling.

## Readiness contracts

- `/health`: web-process liveness only.
- `/ready/core`: database safety/provenance, Alembic heads, storage, canonical supervisor/web/worker
  identities and parentage, fresh registrations, and mandatory listener identities.
- `/ready`: comprehensive application-operational state, including queues, telemetry, collectors,
  providers, and optional integrations.

A historical recovery, unavailable optional provider, telemetry loss, queue pressure, or Docker
failure does not turn a valid runtime tree into a startup failure. `status` reports CORE,
APPLICATION, and OBSERVABILITY separately; non-core trouble is `DEGRADED`.

## Stop, restart, and active leases

Stop requests durable quiescence and classifies `RUNNING` and `RECOVERING` rows. A fresh execution
lease or fresh recovery owner fails closed with `ACTIVE_LEASE_BLOCKS_STOP` and reports job ID/type,
owner, lease expiry, heartbeat/update age, and blocking reason. Stale rows are diagnosed but never
mutated merely to permit shutdown.

Signals target only revalidated members of the recorded runtime generation and process tree. Stop
then verifies WEB, SUPERVISOR, and DURABLE_WORKER are gone and ports 8000, 9101, and 9102 (when
enabled) are released. It never kills Python or PostgreSQL by process name. `restart` uses one
operation ID with explicit stop and start phases under the same repository lock.

## Failure evidence and diagnosis

Every invocation has a lifecycle operation ID. Supervisor, web, and worker logs also carry the
runtime instance ID, Git SHA, and configuration fingerprint. Redacted lifecycle transitions are
appended to `logs/lifecycle/lifecycle.jsonl`; lifecycle metric handoff is atomically persisted under
`data/cache`.

`diagnose` is read-only with respect to database and business state. It writes a sanitized bundle to
`artifacts/diagnostics/lifecycle-<timestamp>-<operation-id>/` with process/listener evidence,
configuration sources, database/schema provenance, registrations/jobs, readiness, observability
health and actual Prometheus target results, redacted log tails, a summary, manifest, and SHA256
checksums. It never migrates, launches, stops, enqueues, or runs a pipeline.

## Disposable databases

CI and tests must explicitly set `SWINGLENS_DATABASE_SAFETY_CONTEXT=DISPOSABLE_TEST` and use an
allowed disposable database prefix such as `swinglens_ci_` or `swinglens_pytest_`. NORMAL lifecycle
sets `AUTHORITATIVE_LOCAL`. Neither context is inferred from CI/pytest, and disposable checks reject
the active SwingLens database.

The optional `docker-compose.postgres-test.yml` remains quarantined test infrastructure and is not
used by NORMAL lifecycle.
