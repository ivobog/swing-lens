# SwingLens

SwingLens is a local-only stock research cockpit. It accepts a daily CSV, preserves the uploaded data, connects to Interactive Brokers Gateway for OHLCV data, and combines fundamental plus Pine-compatible technical scoring into a ranked decision cockpit.

## Current Status

This repository currently contains the MVP application:

- FastAPI application shell
- Jinja2 template setup
- Static asset mounting
- Environment-based settings
- SQLAlchemy database engine/session helper
- Health and readiness endpoints
- Local upload/export/cache directories
- CSV upload with raw-row preservation
- Fundamental scoring
- IB Gateway contract and daily bar cache
- Pine v3.2 replica technical scoring
- Combined decision cockpit
- Market regime command center
- Sector rotation dashboard
- Setup Lifecycle and Signal-Change Engine
- Outcome-Calibrated Winner Probability Engine
- Catalyst and Estimate-Revision Intelligence
- CSV exports and run history

## Runtime Targets

- Python 3.12.x through 3.14.x
- PostgreSQL local database
- Interactive Brokers Gateway
- FastAPI + Jinja2 + HTMX

The project supports the locally installed Python 3.12 line and can move forward to the 3.14 line when that runtime is installed.

## Local Setup

Recommended reproducible setup uses the checked-in `uv.lock` file:

```powershell
python -m pip install uv
uv sync --frozen --extra dev
```

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Create a local `.env` from `.env.example` and adjust PostgreSQL or IB Gateway settings if needed:

```powershell
Copy-Item .env.example .env
```

Start the local PostgreSQL database on the same host port used by `.env.example`:

```powershell
docker compose up -d postgres
```

Apply migrations, then run the lightweight web/API control plane:

```powershell
uv run alembic upgrade head
uv run python -m app.serve --host 127.0.0.1 --port 8000
```

Run the durable worker under its independent watchdog in a second terminal:

```powershell
uv run python -m app.worker_supervisor --worker-id local-worker-1 --queues interactive,broker,background
```

The API process never executes pipeline or broker jobs. `app.worker_supervisor` fences and
restarts a worker that exits, exceeds its memory budget, or remains alive without durable job
progress. For source-editing sessions only, `python -m app.serve --reload` enables reload with
runtime logs, outputs, artifacts, caches, and generated data excluded from file watching. Do not
use reload while executing real pipelines.

Open:

```text
http://127.0.0.1:8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

Readiness check:

```text
http://127.0.0.1:8000/ready
```

## Dependency Management

`pyproject.toml` is the source of direct dependencies. `uv.lock` stores exact resolved
versions for reproducible installs.

Install exact locked dependencies:

```powershell
uv sync --frozen --extra dev
```

Update dependencies intentionally:

```powershell
uv lock --upgrade
ruff check app tests
pytest -q
pytest tests/test_golden_pipeline.py -q
```

Review any golden scoring changes before committing dependency updates. Commit the dependency
definition and `uv.lock` together.

## Database Migrations

SwingLens uses Alembic for PostgreSQL schema migrations. After installing the project dependencies in the virtual environment, apply the schema with:

```powershell
uv run alembic upgrade head
```

To review the SQL without applying it:

```powershell
uv run alembic upgrade head --sql
```

Backup and restore validation are documented in `docs/operations/backup_restore.md`.
Readiness, metrics, and incident response are documented in `docs/operations/observability.md`
and `docs/operations/incidents.md`.
Before destructive migrations or purge/lifecycle work, create a PostgreSQL backup and validate a
restore into a clean database:

```powershell
.\scripts\ops\backup_postgres.ps1 -BackupDir backups
.\scripts\ops\restore_postgres.ps1 `
  -BackupPath backups\swinglens_YYYYMMDD_HHMMSS.dump `
  -ValidationReport backups\restore_validation_YYYYMMDD_HHMMSS.json
```

## Configuration Files

SwingLens keeps MVP scoring and mapping defaults in `config/`:

- `column_aliases.yaml` maps uploaded CSV headers to canonical internal field names.
- `pine_defaults.yaml` stores the Pine v3.2 parameter defaults to port into Python.
- `scoring_weights.yaml` stores initial fundamental and combined-decision weights.
- `market_regime_command_center.yaml` stores market-regime policy, benchmark symbols,
  risk-state mapping, and ranking-profile permissions.
- `sector_rotation.yaml` stores sector taxonomy, universe leadership weights, optional ETF
  confirmation weights, rotation-state thresholds, and advisory permission mappings.
- `setup_lifecycle.yaml` stores setup lifecycle state semantics, setup-family thresholds,
  signal-change definitions, alert rules, replay policy, retention policy, and API targets.
- `winner_probability.yaml` stores OWPE feature, outcome, model, calibration, drift, and retention
  policy.
- `ceri.yaml` and `ceri_catalyst_taxonomy.yaml` store CERI provider, scoring, alert, redaction,
  taxonomy, and retention policy.

Uploaded CSV rows are still preserved exactly as raw JSON in PostgreSQL.

Market Regime Command Center operation is documented in
`docs/market_regime_command_center.md`.

Sector Rotation Dashboard operation is documented in
`docs/sector_rotation_dashboard.md`.

Setup Lifecycle and Signal-Change Engine operation is documented in
`docs/setup_lifecycle_signal_change_engine.md`, with release notes in
`docs/release_notes_setup_lifecycle_signal_change_engine.md`.

Fundamental scoring, technical/Pine parity, combined decisions/ranking profiles, winner
probability, CERI, and route/export contracts are documented in:

- `docs/fundamental_scoring.md`
- `docs/technical_scoring_pine_parity.md`
- `docs/combined_decisions_ranking_profiles.md`
- `docs/winner_probability_engine.md`
- `docs/ceri.md`
- `docs/routes_exports.md`

Maintainer setup, glossary, versioning, governance checklists, and ADRs are documented in:

- `docs/operations/maintainer_handbook.md`
- `docs/glossary.md`
- `docs/versioning.md`
- `docs/governance/`
- `docs/adr/`

## Interactive Brokers

SwingLens talks to a locally running IB Gateway or Trader Workstation through `ib_insync`.

Useful local endpoints:

```text
GET  /ib/status
POST /ib/test
POST /ib/resolve/{ticker}
POST /ib/fetch?tickers=MSFT,NVDA
```

The IB integration only reads market data and contract metadata. There are no order endpoints.

## Cockpit Workflow

1. Upload a TradingView-style CSV from the home page.
2. Use IB Gateway paper trading to fetch cached bars for the uploaded tickers.
3. Open the run detail page and select `Refresh cockpit`.
4. Review ranked combined decisions, technical classifications, and position-size hints.
5. Use `/history` to inspect previous runs.

If a ticker has no cached OHLCV data or cannot be technically scored, SwingLens stores a low-confidence technical row and keeps the refresh moving. The combined result for that ticker is marked as incomplete rather than failing the whole run.

## Exports

Every run exposes CSV exports:

```text
/runs/{run_id}/exports/combined.csv
/runs/{run_id}/exports/fundamentals.csv
/runs/{run_id}/exports/technicals.csv
/runs/{run_id}/exports/raw.csv
/market-regime/export.json
/market-regime/export.csv
/runs/{run_id}/market-regime/export.json
/runs/{run_id}/market-regime/export.csv
/runs/{run_id}/sector-rotation/export.csv
/runs/{run_id}/sector-rotation/export.json
/runs/{run_id}/sector-rotation/brief.md
/setup-lifecycle/export.csv
/setup-lifecycle/export.json
/api/setup-lifecycle/changes/export.csv
/api/setup-lifecycle/changes/export.json
/api/setup-lifecycle/alerts/export.csv
/api/setup-lifecycle/alerts/export.json
/api/setup-lifecycle/episodes/{episode_id}/export.csv
/api/setup-lifecycle/episodes/{episode_id}/export.json
/api/setup-lifecycle/operations/export.json
/api/winner-probability/run/{run_id}/export.csv
/api/winner-probability/run/{run_id}/export.json
/api/winner-probability/outcomes/explorer/export.csv
/api/winner-probability/estimates/{estimate_id}/reproduction/export.json
/ceri/export.csv
/ceri/export.json
```

The generated route/export inventory lives in `docs/routes_exports.md`. XLSX export is deferred
until a separate workbook implementation and test plan exists.

## Technical Indicators

The technical indicator engine lives in `app/services/technical_indicators.py`. It calculates
daily OHLCV features such as EMA/SMA, RSI, ATR, DMI/ADX, OBV, ROC, pullback geometry,
breakout state, volume quality, candle risk signals, stop/target, relative strength inputs,
and weekly higher-timeframe trend features.

## Hardening Checks

Before a trading-research session:

```powershell
uv run alembic upgrade head
uv run ruff check app tests scripts
uv run python scripts/docs/check_route_inventory.py
uv run pytest -q
uv run pytest tests/test_golden_pipeline.py -q
```

Then confirm:

```text
http://127.0.0.1:8000/ready
```

## Input References

The MVP design references these local input files:

```text
C:/Users/Ivica/Downloads/money money_2026-07-02.csv
C:/Users/Ivica/Downloads/dual_trend_momentum_indicator_v3.pine
C:/Users/Ivica/Downloads/dual_trend_momentum_engine.pine
```

They are not copied into the repository by default.

## Safety Boundary

SwingLens is decision support only. It must not place, modify, or cancel broker orders.
Setup lifecycle alerts and state changes are research signals, not trading instructions.

The optional, feature-flagged IBKR Market Intelligence extension adds read-only liquidity, borrow-pressure, volatility, options-activity, scanner-discovery, histogram price-acceptance, and Flex trade-journal evidence. See [IB Market Intelligence](docs/IB_MARKET_INTELLIGENCE.md) for activation, semantics, entitlements, operations, security, and validation.
