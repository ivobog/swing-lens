# SwingLens QA Execution Guide

This directory is the auditable execution record for the SwingLens QA plan. Automated tests use
deterministic local fixtures by default. Live broker credentials, licensed provider credentials,
and the user's active research database are outside the automated suite.

## Safety Rules

- Run destructive database checks only against databases whose names begin with
  `swinglens_pytest_` or `swinglens_qa_`.
- Never point migration, restore, purge, or cleanup tests at the active research database.
- IB doubles must connect with `readonly=True`; the suite fails on order-related method access.
- Do not commit generated reports, browser traces, backups, uploads, exports, caches, or secrets.
- Golden scoring changes require an explicit formula/version review; never re-baseline to hide a
  failure.

## Test Lanes

| Marker | Purpose | Typical command |
| --- | --- | --- |
| `unit` | Isolated parser, scoring, policy, state, and route behavior | `uv run pytest -q -m unit` |
| `integration` | PostgreSQL, service boundary, or multi-component workflow | `uv run pytest -q -m integration` |
| `e2e` | Live local app exercised through a real browser | `uv run pytest tests/e2e -q --browser chromium --browser firefox` |
| `security` | Redaction, authorization, injection, and no-order controls | `uv run pytest -q -m security` |
| `performance` | Repeatable local budgets and scale fixtures | `uv run pytest -q -m performance` |
| `destructive` | Disposable migration, restore, purge, or cleanup checks | `uv run pytest -q -m destructive` |
| `external` | Opt-in live dependency checks; never part of core regression | `uv run pytest -q -m external` |
| `slow` | Browser, scale, or other intentionally slower checks | `uv run pytest -q -m slow` |

Every collected test is assigned a test level by `tests/conftest.py`; specialized risk markers are
added in addition to that level.

## Reproducible Local Gate

```powershell
uv sync --frozen --extra dev
uv run ruff check app tests scripts
uv run python scripts/docs/check_route_inventory.py
uv run python scripts/qa/scan_tracked_secrets.py
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
uv run pytest -q -m "not e2e and not external" `
  --junitxml=test-results/regression.xml `
  --cov=app `
  --cov-report=term-missing `
  --cov-report=xml:test-results/coverage.xml
uv run pytest tests/test_golden_pipeline.py tests/test_ranking_profiles_golden.py -q
uv run playwright install chromium firefox
uv run pytest tests/e2e -q --browser chromium --browser firefox `
  --junitxml=test-results/browser-smoke.xml
uv run pytest -q -m performance --junitxml=test-results/performance.xml
```

Generated JUnit, coverage, browser, backup, and restore artifacts are ignored locally and published
by CI where applicable. Exact results for the current execution are in
`QA_EXECUTION_REPORT.md`; requirement-level traceability is in `QA_EXECUTION_MATRIX.md`.

## Reusable Fixtures

`tests/conftest.py` provides isolated upload/export/cache paths, an environment-independent
`Settings` factory, a FastAPI client factory, deterministic CSV and OHLCV factories, a fixed UTC
clock, a read-only scripted IB Gateway, and a safely named disposable PostgreSQL fixture.

`tests/e2e/conftest.py` migrates a fresh PostgreSQL database, launches SwingLens on an ephemeral
localhost port with advanced modules and the worker disabled, waits for `/health`, and tears down
the process and database after the browser lane.

## Evidence Index

- `QA_EXECUTION_MATRIX.md` — requirement/risk-to-test traceability and status.
- `QA_EXECUTION_REPORT.md` — environment, commands, outcomes, defects, and verdict.
- `MANUAL_TEST_PROCEDURES.md` — human verification not replaced by deterministic automation.
- `LIVE_IB_PAPER_VALIDATION.md` — read-only paper Gateway procedure.
- `PERFORMANCE_BASELINE.md` — machine-specific measurements and limitations.
- `RELEASE_QA_CHECKLIST.md` — release gates and sign-off state.
