# Contributing

SwingLens is local-only decision-support software. Contributions must preserve the no-orders
boundary, reproducibility of historical evidence, and local-admin protections.

## Setup

```powershell
python -m pip install uv
uv sync --frozen --extra dev
Copy-Item .env.example .env
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Check `http://127.0.0.1:8000/ready` before starting manual validation.

## Before Opening a PR

Run the focused tests for the subsystem you changed, then run the standard gates:

```powershell
uv run ruff check app tests scripts
uv run python scripts/docs/check_route_inventory.py
uv run pytest -q
```

For database changes, also run Alembic against a clean PostgreSQL database and a populated local
database. For high-risk scoring/model changes, update version fields, golden fixtures, docs, and the
changelog in the same PR.

## High-Risk Areas

Use the checklists in `docs/governance/` when touching:

- scoring formulas, model features, ranking gates, or export schemas;
- database migrations, retention, purge, or backup/restore behavior;
- background job leasing, cancellation, retry, or idempotency;
- provider integrations, CERI licensed data, or redaction policy;
- local-admin routes, CSRF, host binding, or unsafe actions.

## Documentation Expectations

Update docs in the same PR as behavior changes. Route changes must update
`docs/routes_exports.md` with `python scripts/docs/check_route_inventory.py --write`.
Architectural decisions that constrain future work need an ADR in `docs/adr/`.
