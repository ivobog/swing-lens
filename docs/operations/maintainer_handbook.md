# Maintainer Handbook

## Daily Setup

```powershell
python -m pip install uv
uv sync --frozen --extra dev
Copy-Item .env.example .env
.\swinglens.ps1 start
.\swinglens.ps1 status
```

The configured locally installed Windows PostgreSQL service is authoritative. Docker PostgreSQL is
never part of routine operation. The web lifespan owns the durable supervisor, which owns the
worker; do not start a second supervisor manually. Use `.\swinglens.ps1 restart` and
`.\swinglens.ps1 stop` for controlled transitions.

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
| App cannot connect to DB | Run `.\swinglens.ps1 status`; verify the redacted local endpoint, exact Windows PostgreSQL service, and `uv run alembic current` |
| `/ready` degraded | Inspect readiness checks, local directories, migrations, worker state, and stale jobs |
| IB fetch stalls | Check `/ib/status`, Gateway/TWS port, client id, and fetch progress page |
| Pipeline stuck | Check pipeline status route and background job stale recovery |
| OWPE output missing | Check feature flags, capture-in-pipeline flag, operations page, and model status |
| CERI output missing | Check feature flags, provider health, credentials, stale/quarantine views |
| Export too large | Narrow filters or lower page/export size; XLSX is deferred |
| Secret appears in output | Rotate secret, inspect logs/DB, and patch shared redaction before release |
