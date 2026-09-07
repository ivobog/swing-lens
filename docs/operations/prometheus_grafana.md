# Local Prometheus and Grafana

The optional stack stores metrics history locally and needs no SaaS account or cloud credential.
SwingLens itself continues to run normally when this stack is absent.

## Start

Start PostgreSQL and SwingLens first, including the supervisor/worker, then:

```powershell
docker compose -f docker-compose.observability.yml up -d
```

Open Prometheus at `http://127.0.0.1:9090` and Grafana at
`http://127.0.0.1:3000`. Set `GRAFANA_ADMIN_PASSWORD` in the ignored local `.env` file before
starting the stack. No administrator password is committed or supplied by default.

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
processes, container and database.

The Prometheus named volume has a 30-day TSDB retention, exceeding the seven-day requirement.
Deleting the volume deletes historical metrics; application PostgreSQL causality and SQL JSONL are
separate and unaffected.

## Provisioned dashboards

Grafana automatically loads:

1. SwingLens Overview
2. Jobs & Queues
3. Pipeline Performance
4. CERI & Providers
5. Database / SQL Flight Recorder
6. Worker Resources

The dashboards cover queue depth/age, job wait/duration/failure/retry/coalescing/fanout, pipeline and
stage duration, CERI/provider result/latency/bytes/retries, DB pool/wait/slow/transaction/recorder
health, and worker CPU/memory/heartbeat/restarts.

## Validate and troubleshoot

```powershell
docker compose -f docker-compose.observability.yml config
Invoke-WebRequest http://127.0.0.1:8000/metrics
Invoke-WebRequest http://127.0.0.1:9101/metrics
Invoke-WebRequest http://127.0.0.1:9102/metrics
```

If a target is down, first confirm that its process is running and that ports 8000/9101/9102 are
not already occupied. A missing worker endpoint must not be interpreted as a zero-work web metric;
the target's `up` series and missing-worker alert make the absence explicit.

Configuration lives under `monitoring/prometheus` and `monitoring/grafana/provisioning`; dashboards
live under `monitoring/grafana/dashboards`.
