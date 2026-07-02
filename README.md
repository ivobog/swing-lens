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

- Python 3.14.x
- PostgreSQL local database
- Interactive Brokers Gateway
- FastAPI + Jinja2 + HTMX

Python.org currently lists Python 3.14.6 as the latest stable release. The project is therefore configured for the 3.14 line.

## Local Setup

Create a virtual environment with Python 3.14:

```powershell
py -3.14 -m venv .venv
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
