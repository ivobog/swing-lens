# Local Prometheus and Grafana

The optional stack stores metrics history locally and needs no SaaS account or cloud credential.
SwingLens itself continues to run normally when this stack is absent.

## Start

Use the unified lifecycle; it starts the core against authoritative local Windows PostgreSQL first,
then attempts this optional Docker-only observability branch:

```powershell
pwsh .\swinglens.ps1 start
pwsh .\swinglens.ps1 status
```

If Docker Engine is unavailable, SwingLens core remains running and status reports `DEGRADED`.
`stop` stops the two observability services but never stops Docker Desktop. The lifecycle never
uses the disposable PostgreSQL Compose file.

Local URLs:

- SwingLens: `http://127.0.0.1:8000`
- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`

Before starting the monitoring stack, set `GRAFANA_ADMIN_PASSWORD` in the ignored local `.env`
file. Log in to Grafana with username `admin` and that password. No administrator password is
committed or supplied by default.

Prometheus scrapes:

```text
swinglens-web        host.docker.internal:8000/metrics
swinglens-worker     host.docker.internal:9101/metrics
swinglens-supervisor host.docker.internal:9102/metrics
```

The process metrics listeners bind to `OBSERVABILITY_METRICS_HOST`, which defaults to loopback.
On Windows with Docker Desktop, Prometheus reaches that host loopback through
`host.docker.internal`; SwingLens accepts that internal host name only for the metrics route. The
compose publications remain explicitly bound to `127.0.0.1`, so Prometheus and Grafana are not
published on a non-loopback interface by default. On Linux, add an explicit private host-gateway or
container-network mapping. Do not expose worker/supervisor endpoints publicly.

The real certification starts the actual web process and supervisor-owned durable worker, verifies
all three `up` targets, executes a worker-only probe, restarts the worker through the supervisor,
and requires a newer worker sample afterward. Run it only when Docker Desktop and disposable local
PostgreSQL are available:

```powershell
uv run python scripts/certify_observability_cross_process.py
```

The script refuses a database name without the `swinglens_obs_cert_` prefix and cleans up the
processes, container and database. Before Alembic is imported at the command boundary, it connects
directly to the candidate URL, reads the database/server identity, independently reads the active
SwingLens identity, requires an explicit disposable prefix, and rejects equality. Alembic then
rechecks its actual connection against that verified identity before running migrations.

When `OBSERVABILITY_METRICS_ENABLED=false`, none of the three Prometheus listeners or
Prometheus-only resource collectors starts. The web `/metrics` endpoint remains stable at HTTP 200
with an empty body. Durable PostgreSQL evidence/retention and the SQL Flight Recorder are separate
facilities and continue according to their own settings.

The Prometheus named volume has a 30-day TSDB retention, exceeding the seven-day requirement.
Deleting the volume deletes historical metrics; application PostgreSQL causality and SQL JSONL are
separate and unaffected.

## Provisioned dashboards

Grafana automatically provisions the default `Prometheus` datasource with the stable UID
`swinglens-prometheus` and container-network URL `http://prometheus:9090`. Do not replace this with
`localhost`: inside the Grafana container, `localhost` is Grafana itself.

Grafana also automatically loads this source-controlled dashboard set into the `SwingLens` folder:

1. SwingLens Overview
2. Jobs & Queues
3. Pipeline Performance
4. CERI & Providers
5. Database / SQL Flight Recorder
6. Worker Resources

The dashboards cover queue depth/age, job wait/duration/failure/retry/coalescing/fanout, pipeline and
stage duration, CERI/provider result/latency/bytes/retries, DB pool/wait/slow/transaction/recorder
health, and worker CPU/memory/heartbeat/restarts. No data source or dashboard setup through the
Grafana UI is required after a container start or restart.

## Validate and troubleshoot

The following raw Compose commands are for observability troubleshooting only:

```powershell
docker compose -f docker-compose.observability.yml config
docker compose -f docker-compose.observability.yml ps
docker compose -f docker-compose.observability.yml logs grafana
docker compose -f docker-compose.observability.yml exec grafana ls -R /etc/grafana/provisioning
docker compose -f docker-compose.observability.yml exec grafana ls -R /var/lib/grafana/dashboards
Invoke-WebRequest http://127.0.0.1:8000/metrics
Invoke-WebRequest http://127.0.0.1:9101/metrics
Invoke-WebRequest http://127.0.0.1:9102/metrics
```

The Compose mounts are read-only and source controlled:

```text
monitoring/grafana/provisioning/datasources -> /etc/grafana/provisioning/datasources
monitoring/grafana/provisioning/dashboards  -> /etc/grafana/provisioning/dashboards
monitoring/grafana/dashboards               -> /var/lib/grafana/dashboards
```

Grafana polls the dashboard directory every 30 seconds, which is reliable for Docker Desktop bind
mounts. Restarting only Grafana also reapplies datasource and dashboard provisioning:

```powershell
docker compose -f docker-compose.observability.yml restart grafana
```

If a target is down, first confirm that its process is running and that ports 8000/9101/9102 are
not already occupied. A missing worker endpoint must not be interpreted as a zero-work web metric;
the target's `up` series and missing-worker alert make the absence explicit.

Configuration lives under `monitoring/prometheus` and `monitoring/grafana/provisioning`; dashboards
live under `monitoring/grafana/dashboards`.
