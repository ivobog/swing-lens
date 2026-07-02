# SwingLens

SwingLens is a local-only stock research cockpit. It accepts a daily CSV, preserves the uploaded data, connects to Interactive Brokers Gateway for OHLCV data, and will later combine fundamental and Pine-compatible technical scoring into a ranked decision table.

## Current Status

This repository currently contains the project skeleton:

- FastAPI application shell
- Jinja2 template setup
- Static asset mounting
- Environment-based settings
- SQLAlchemy database engine/session helper
- Health endpoint
- Local upload/results/cache directories

## Runtime Targets

- Python 3.12.x through 3.14.x
- PostgreSQL local database
- Interactive Brokers Gateway
- FastAPI + Jinja2 + HTMX

The project supports the locally installed Python 3.12 line and can move forward to the 3.14 line when that runtime is installed.

## Local Setup

Create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Create a local `.env` from `.env.example` and adjust PostgreSQL or IB Gateway settings if needed:

```powershell
Copy-Item .env.example .env
```

Run the app:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

## Database Migrations

SwingLens uses Alembic for PostgreSQL schema migrations. After installing the project dependencies in the virtual environment, apply the schema with:

```powershell
alembic upgrade head
```

To review the SQL without applying it:

```powershell
alembic upgrade head --sql
```

## Configuration Files

SwingLens keeps MVP scoring and mapping defaults in `config/`:

- `column_aliases.yaml` maps uploaded CSV headers to canonical internal field names.
- `pine_defaults.yaml` stores the Pine v3.2 parameter defaults to port into Python.
- `scoring_weights.yaml` stores initial fundamental and combined-decision weights.

Uploaded CSV rows are still preserved exactly as raw JSON in PostgreSQL.

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

## Technical Indicators

The technical indicator engine lives in `app/services/technical_indicators.py`. It calculates
daily OHLCV features such as EMA/SMA, RSI, ATR, DMI/ADX, OBV, ROC, pullback geometry,
breakout state, volume quality, candle risk signals, stop/target, relative strength inputs,
and weekly higher-timeframe trend features.

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
