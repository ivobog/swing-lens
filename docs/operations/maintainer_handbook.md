# Maintainer Handbook

## Daily Setup

```powershell
python -m pip install uv
uv sync --frozen --extra dev
Copy-Item .env.example .env
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m app.serve --host 127.0.0.1 --port 8000
uv run python -m app.worker_supervisor --worker-id local-worker-1 --queues interactive,broker,background
```

Confirm `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/ready`.

## Subsystem Smoke Matrix

| Subsystem | Entry Points | Focused Checks |
| --- | --- | --- |
| Upload/history/cockpit | `/`, `/runs`, `/history`, `/runs/{run_id}` | upload, raw preservation, exports, golden pipeline |
| IB market data | `/ib`, `/runs/{run_id}/ib/plan`, fetch progress | read-only status, contract resolution, failed fetch export |
| Technical scoring | chart and technical-score routes | Pine parity, readiness, chart payload |
| Combined/ranking | run detail, ranking profile APIs | ranking profile config/routes/exports |
| Market regime | `/market-regime`, run-scoped market APIs | command center, policy, exports |
| Sector rotation | run-scoped sector pages/APIs | service, repository, export, route tests |
| Setup lifecycle | `/setup-lifecycle`, alerts, operations | `tests/setup_lifecycle -q` |
| Winner probability | `/winner-probability/*`, OWPE APIs | `tests/winner_probability -q` |
| CERI | `/ceri`, changes, operations, provider APIs | `tests/ceri -q` |
| Background jobs | pipeline, IB, OWPE, SLSE, CERI jobs | background worker/job tests |
| Security/admin | unsafe POST/admin routes | route security tests |
| Operations/recovery | `/health`, `/ready`, backup/restore scripts | ops tests and runbooks |

## Troubleshooting

| Symptom | First Checks |
| --- | --- |
| App cannot connect to DB | Verify `.env`, Docker port, `docker compose ps`, and `uv run alembic current` |
| `/ready` degraded | Inspect readiness checks, local directories, migrations, worker state, and stale jobs |
| IB fetch stalls | Check `/ib/status`, Gateway/TWS port, client id, and fetch progress page |
| Pipeline stuck | Check pipeline status route and background job stale recovery |
| OWPE output missing | Check feature flags, capture-in-pipeline flag, operations page, and model status |
| CERI output missing | Check feature flags, provider health, credentials, stale/quarantine views |
| Export too large | Narrow filters or lower page/export size; XLSX is deferred |
| Secret appears in output | Rotate secret, inspect logs/DB, and patch shared redaction before release |
