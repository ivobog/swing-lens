# SwingLens — Software Design Document

**Project:** SwingLens  
**Document type:** Software Design Document (SDD)  
**Version:** 0.1  
**Owner:** Ivica Bogoevski  
**Status:** Draft for MVP implementation  
**Target stack:** FastAPI + Jinja2 + HTMX + PostgreSQL + ib_insync  
**Market scope:** US stocks only  
**Auto-trading:** Never

---

## 1. Purpose

This document describes the technical design for **SwingLens**, a local web app for daily stock research.

SwingLens accepts a daily CSV file, stores its rows, fetches US stock OHLCV data from Interactive Brokers, calculates financial rankings, calculates a Python replica of the Pine v3.2 dual trend + momentum engine, merges the outputs, and displays a local decision cockpit.

This SDD is based on the current uploaded reference files:

```text
money money_2026-07-02.csv
dual_trend_momentum_indicator_v3.pine
dual_trend_momentum_engine.pine
```

The uploaded CSV sample contains:

```text
78 rows
70 columns
```

The uploaded Pine references contain:

```text
dual_trend_momentum_indicator_v3.pine: 1302 lines
dual_trend_momentum_engine.pine:      325 lines
```

---

## 2. Design Goals

### 2.1 Primary goals

- Keep the app local and simple.
- Use uploaded CSV as the only fundamental data source.
- Use Interactive Brokers as the OHLCV data source.
- Store all results in PostgreSQL.
- Replicate the Pine v3.2 engine in testable Python modules.
- Produce explainable rankings and classifications.
- Make daily repeated use fast through historical bar caching.

### 2.2 Non-goals

SwingLens will not implement:

```text
Auto trading
Broker order placement
Portfolio optimization
Options logic
Cloud deployment
User account system
React frontend
External fundamental data APIs
Non-US stocks
Crypto
Forex
News aggregation
```

---

## 3. High-Level Architecture

```text
+-----------------------------+
| Browser                     |
| Jinja2 + HTMX + Bootstrap   |
+--------------+--------------+
               |
               v
+-----------------------------+
| FastAPI Application         |
| Routes + Services           |
+--------------+--------------+
               |
     +---------+----------+----------------+----------------+
     |                    |                |                |
     v                    v                v                v
+-----------+      +---------------+  +-------------+  +--------------+
| CSV       |      | Fundamental   |  | IB Data     |  | Technical    |
| Loader    |      | Ranker        |  | Fetcher     |  | Engine       |
+-----------+      +---------------+  +-------------+  +--------------+
     |                    |                |                |
     +--------------------+----------------+----------------+
                          |
                          v
                 +----------------+
                 | Combined       |
                 | Decision       |
                 | Engine         |
                 +-------+--------+
                         |
                         v
                 +----------------+
                 | PostgreSQL     |
                 +----------------+
```

---

## 4. Runtime Topology

Recommended local runtime:

```text
Host machine
  ├── IB Gateway or TWS
  ├── SwingLens FastAPI process
  └── PostgreSQL database, preferably via Docker Compose
```

Suggested local ports:

```text
SwingLens web app:     localhost:8000
PostgreSQL:            localhost:5432
TWS paper trading:     localhost:7497
TWS live trading:      localhost:7496
IB Gateway:            configurable
```

SwingLens must bind to `127.0.0.1` by default.

---

## 5. Proposed Repository Structure

```text
swing-lens/
  README.md
  pyproject.toml
  .env.example
  docker-compose.yml

  docs/
    vision.md
    srs.md
    sdd.md

  app/
    __init__.py
    main.py
    settings.py
    db.py

    models/
      __init__.py
      tables.py
      schemas.py
      enums.py

    routers/
      __init__.py
      upload_routes.py
      run_routes.py
      result_routes.py
      ticker_routes.py
      export_routes.py
      settings_routes.py
      ib_routes.py

    services/
      __init__.py
      csv_loader.py
      column_mapper.py
      validation_service.py
      fundamental_ranker.py
      ib_contract_resolver.py
      ib_data_fetcher.py
      bar_cache_service.py
      technical_indicators.py
      pine_defaults.py
      pine_replica_engine.py
      combined_decision_engine.py
      xlsx_exporter.py
      run_processor.py

    templates/
      base.html
      upload.html
      runs.html
      results.html
      ticker_detail.html
      settings.html
      partials/
        processing_status.html
        result_table.html
        error_panel.html

    static/
      app.css
      app.js

  config/
    column_aliases.yaml
    pine_defaults.yaml
    scoring_weights.yaml

  data/
    uploads/
    exports/
    cache/

  tests/
    test_csv_loader.py
    test_column_mapper.py
    test_fundamental_ranker.py
    test_technical_indicators.py
    test_pine_replica_engine.py
    test_combined_decision_engine.py
    test_ib_contract_resolver.py
```

---

## 6. Main Application Components

### 6.1 FastAPI app

`app/main.py` creates the application and registers routers.

Responsibilities:

- Configure Jinja2 templates.
- Configure static files.
- Register routers.
- Initialize database connection.
- Provide health endpoint.

### 6.2 Settings module

`app/settings.py` loads configuration from environment variables and `.env`.

Settings:

```text
DATABASE_URL
APP_HOST
APP_PORT
UPLOAD_DIR
EXPORT_DIR
IB_HOST
IB_PORT
IB_CLIENT_ID
IB_TIMEOUT_SECONDS
IB_USE_RTH
IB_DEFAULT_DURATION
IB_DEFAULT_BAR_SIZE
```

### 6.3 Database module

`app/db.py` creates database engine/session management.

Recommended libraries:

```text
SQLAlchemy 2.x
Alembic for migrations
psycopg or asyncpg
```

For MVP, synchronous SQLAlchemy is acceptable and simpler.

---

## 7. Data Model Design

### 7.1 Entity overview

```text
UploadRun
  ├── RawCompanyRow
  ├── FundamentalScore
  ├── TechnicalScore
  ├── CombinedResult
  └── EngineParameters

IBContract
  └── PriceBar
```

### 7.2 PostgreSQL tables

#### `upload_runs`

Stores one processing run.

```sql
CREATE TABLE upload_runs (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    file_path TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    row_count INTEGER,
    status TEXT NOT NULL,
    pine_engine_version TEXT,
    python_engine_version TEXT,
    error_message TEXT,
    notes TEXT
);
```

Allowed status values:

```text
UPLOADED
VALIDATING
FETCHING_IB_DATA
PROCESSING
COMPLETED
COMPLETED_WITH_WARNINGS
FAILED
```

#### `raw_company_rows`

Stores original CSV rows without losing data.

```sql
CREATE TABLE raw_company_rows (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES upload_runs(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    raw_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX idx_raw_company_rows_run_id ON raw_company_rows(run_id);
CREATE INDEX idx_raw_company_rows_ticker ON raw_company_rows(ticker);
```

#### `ib_contracts`

Caches resolved IB contracts.

```sql
CREATE TABLE ib_contracts (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    ib_conid BIGINT,
    symbol TEXT,
    exchange TEXT,
    primary_exchange TEXT,
    currency TEXT,
    sec_type TEXT,
    local_symbol TEXT,
    trading_class TEXT,
    resolution_status TEXT NOT NULL,
    error_message TEXT,
    last_resolved_at TIMESTAMPTZ
);
```

#### `price_bars`

Stores historical bars.

```sql
CREATE TABLE price_bars (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    bar_date DATE NOT NULL,
    timeframe TEXT NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    source TEXT NOT NULL,
    what_to_show TEXT NOT NULL,
    adjustment_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, bar_date, timeframe, what_to_show)
);
```

Expected `what_to_show` values:

```text
ADJUSTED_LAST
TRADES
```

#### `fundamental_scores`

```sql
CREATE TABLE fundamental_scores (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES upload_runs(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    growth_score NUMERIC,
    profitability_score NUMERIC,
    fcf_score NUMERIC,
    balance_sheet_score NUMERIC,
    valuation_score NUMERIC,
    momentum_score NUMERIC,
    dilution_score NUMERIC,
    risk_score NUMERIC,
    missing_data_penalty NUMERIC,
    fundamental_score NUMERIC,
    fundamental_label TEXT,
    trap_flags_json JSONB,
    explanation TEXT,
    debug_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, ticker)
);
```

#### `technical_scores`

```sql
CREATE TABLE technical_scores (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES upload_runs(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    trend_score NUMERIC,
    local_trend_score NUMERIC,
    momentum_score NUMERIC,
    setup_score NUMERIC,
    risk_score NUMERIC,
    market_score NUMERIC,
    relative_strength_score NUMERIC,
    sector_relative_strength_score NUMERIC,
    combined_relative_strength_score NUMERIC,
    htf_score NUMERIC,
    dual_score NUMERIC,
    classification TEXT,
    pullback_health TEXT,
    action_bias TEXT,
    suggested_stop NUMERIC,
    suggested_target NUMERIC,
    reward_risk NUMERIC,
    entry_risk_pct NUMERIC,
    technical_confidence TEXT,
    insufficient_data BOOLEAN NOT NULL DEFAULT false,
    missing_data_json JSONB,
    debug_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, ticker)
);
```

#### `combined_results`

```sql
CREATE TABLE combined_results (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES upload_runs(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    final_rank INTEGER,
    final_score NUMERIC,
    fundamental_score NUMERIC,
    fundamental_label TEXT,
    technical_classification TEXT,
    dual_score NUMERIC,
    combined_decision TEXT,
    position_size_hint TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, ticker)
);
```

#### `engine_parameters`

```sql
CREATE TABLE engine_parameters (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES upload_runs(id) ON DELETE CASCADE,
    parameters_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 8. Uploaded CSV Design

### 8.1 Sample CSV profile

The uploaded reference CSV has:

```text
Rows:    78
Columns: 70
```

### 8.2 Exact sample columns

SwingLens must be able to ingest and preserve these columns:

```text
Symbol
Description
Price
Price - Currency
Price change %, 1 day
Volume change %, 1 day
Relative volume, 1 day
Market capitalization
Market capitalization - Currency
Price to earnings ratio
Earnings per share diluted growth %, Quarterly YoY
Earnings per share diluted growth %, TTM YoY
Earnings per share estimate, Annual
Earnings per share estimate, Annual - Currency
Sector
Performance %, 1 week
Performance %, 1 month
Performance %, 3 months
Performance %, 1 year
Revenue growth %, Quarterly YoY
Revenue growth %, TTM YoY
Revenue growth %, 5 year CAGR
Revenue estimate, Annual
Revenue estimate, Annual - Currency
Gross margin %, Trailing 12 months
Gross profit growth %, TTM YoY
EBITDA growth %, TTM YoY
EBITDA margin %, Trailing 12 months
Operating margin %, Trailing 12 months
Net margin %, Trailing 12 months
Return on equity %, Trailing 12 months
Return on total capital %, Trailing 12 months
Free cash flow, Trailing 12 months
Free cash flow, Trailing 12 months - Currency
Free cash flow growth %, TTM YoY
Free cash flow margin %, Trailing 12 months
Price to free cash flow ratio
Enterprise value to free cash flow, Trailing 12 months
Operating cash flow per share, Trailing 12 months
Operating cash flow per share, Trailing 12 months - Currency
Net debt to EBITDA ratio, Trailing 12 months
Debt to equity ratio, Quarterly
Debt to EBITDA ratio, Annual
Debt to assets ratio, Annual
Current ratio, Quarterly
Enterprise value to revenue ratio, Trailing 12 months
Price to sales ratio
Return on invested capital %, Trailing 12 months
Return on capital employed %, Trailing 12 months
EBITDA interest coverage, Trailing 12 months
Total debt per share, Annual
Total debt per share, Annual - Currency
Total debt to capital, Annual
Total common shares outstanding
Buyback yield %
Forward non-GAAP price to earnings, Annual
Price to earning to growth, Trailing 12 months
Enterprise value to revenue ratio, Trailing 12 months.1
Enterprise value to EBITDA ratio, Trailing 12 months
Price × average volume, 10 days
Price × average volume, 10 days - Currency
Price × average volume, 30 days
Price × average volume, 30 days - Currency
Price × average volume, 60 days
Price × average volume, 60 days - Currency
Free float
Momentum, 10, 1 day
Average true range %, 14, 1 day
Beta, 1 year
Beta, 3 years
```

### 8.3 Canonical column mapping

The app should map CSV columns into internal canonical fields.

Examples:

```yaml
ticker: Symbol
company_name: Description
price: Price
sector: Sector
market_cap: Market capitalization
pe_ratio: Price to earnings ratio
eps_growth_qoq_yoy: Earnings per share diluted growth %, Quarterly YoY
eps_growth_ttm_yoy: Earnings per share diluted growth %, TTM YoY
revenue_growth_qoq_yoy: Revenue growth %, Quarterly YoY
revenue_growth_ttm_yoy: Revenue growth %, TTM YoY
revenue_growth_5y_cagr: Revenue growth %, 5 year CAGR
gross_margin_ttm: Gross margin %, Trailing 12 months
ebitda_margin_ttm: EBITDA margin %, Trailing 12 months
operating_margin_ttm: Operating margin %, Trailing 12 months
net_margin_ttm: Net margin %, Trailing 12 months
roe_ttm: Return on equity %, Trailing 12 months
roic_ttm: Return on invested capital %, Trailing 12 months
fcf_ttm: Free cash flow, Trailing 12 months
fcf_growth_ttm_yoy: Free cash flow growth %, TTM YoY
fcf_margin_ttm: Free cash flow margin %, Trailing 12 months
pfcf: Price to free cash flow ratio
ev_fcf: Enterprise value to free cash flow, Trailing 12 months
net_debt_to_ebitda: Net debt to EBITDA ratio, Trailing 12 months
debt_to_equity: Debt to equity ratio, Quarterly
debt_to_assets: Debt to assets ratio, Annual
current_ratio: Current ratio, Quarterly
ps_ratio: Price to sales ratio
ev_ebitda: Enterprise value to EBITDA ratio, Trailing 12 months
buyback_yield: Buyback yield %
forward_pe: Forward non-GAAP price to earnings, Annual
peg_ratio: Price to earning to growth, Trailing 12 months
dollar_volume_10d: Price × average volume, 10 days
dollar_volume_30d: Price × average volume, 30 days
dollar_volume_60d: Price × average volume, 60 days
free_float: Free float
tradingview_momentum_10d: Momentum, 10, 1 day
tradingview_atr_pct_14d: Average true range %, 14, 1 day
beta_1y: Beta, 1 year
beta_3y: Beta, 3 years
```

### 8.4 Handling duplicate-like columns

The CSV includes:

```text
Enterprise value to revenue ratio, Trailing 12 months
Enterprise value to revenue ratio, Trailing 12 months.1
```

Design decision:

- Preserve both in `raw_json`.
- Map the first one to canonical `ev_revenue`.
- Ignore the `.1` variant unless a future validation shows it contains distinct data.

---

## 9. Processing Flow

### 9.1 Full run sequence

```text
POST /uploads
  → save file
  → create upload_run
  → parse CSV
  → validate columns
  → store raw rows
  → resolve IB contracts
  → fetch missing ADJUSTED_LAST bars
  → fetch missing TRADES bars
  → calculate fundamentals
  → calculate technicals
  → merge combined results
  → mark run completed
```

### 9.2 Run processor pseudo-code

```python
def process_run(run_id: int) -> None:
    run = upload_run_repository.get(run_id)
    update_status(run, "VALIDATING")

    rows = csv_loader.load(run.file_path)
    mapped_rows = column_mapper.map_rows(rows)
    validation_service.validate(mapped_rows)
    raw_company_repository.save_all(run_id, mapped_rows)

    update_status(run, "FETCHING_IB_DATA")
    tickers = extract_tickers(mapped_rows)
    benchmarks = ["SPY", "QQQ"]
    all_symbols = sorted(set(tickers + benchmarks))

    contracts = ib_contract_resolver.resolve_many(all_symbols)
    bar_cache_service.ensure_daily_bars(contracts, what_to_show="ADJUSTED_LAST")
    bar_cache_service.ensure_daily_bars(contracts, what_to_show="TRADES")

    update_status(run, "PROCESSING")
    fundamental_scores = fundamental_ranker.score_rows(mapped_rows)
    technical_scores = technical_engine.score_tickers(tickers)
    combined_results = combined_decision_engine.combine(
        mapped_rows,
        fundamental_scores,
        technical_scores,
    )

    repositories.save_scores(run_id, fundamental_scores, technical_scores, combined_results)
    update_status(run, "COMPLETED")
```

---

## 10. IB Data Design

### 10.1 Contract resolver

File:

```text
app/services/ib_contract_resolver.py
```

Responsibilities:

- Convert ticker symbol into IB stock contract.
- Use US stock defaults.
- Cache successful contract metadata.
- Return detailed errors for unresolved symbols.

Default contract:

```python
Stock(symbol=ticker, exchange="SMART", currency="USD")
```

Resolution steps:

```text
1. Check ib_contracts cache.
2. If resolved and recent, reuse.
3. If missing/stale, call IB qualifyContracts.
4. Store conId, primaryExchange, localSymbol, tradingClass.
5. Return resolved contract or error.
```

### 10.2 Historical data fetcher

File:

```text
app/services/ib_data_fetcher.py
```

Responsibilities:

- Fetch historical daily bars from IB.
- Fetch both `ADJUSTED_LAST` and `TRADES`.
- Respect pacing.
- Convert IB bars to internal bar DTOs.

Default request:

```python
bars = ib.reqHistoricalData(
    contract,
    endDateTime="",
    durationStr="2 Y",
    barSizeSetting="1 day",
    whatToShow="ADJUSTED_LAST",
    useRTH=True,
    formatDate=1,
    keepUpToDate=False,
)
```

Repeat with:

```python
whatToShow="TRADES"
```

### 10.3 Bar cache service

File:

```text
app/services/bar_cache_service.py
```

Responsibilities:

- Check if sufficient bars exist for a ticker.
- Fetch missing bars.
- Upsert bars into `price_bars`.
- Provide price DataFrames to engines.

Bar selection for calculations:

```text
Price calculations:  ADJUSTED_LAST preferred, TRADES fallback
Volume calculations: TRADES preferred
```

If `ADJUSTED_LAST` lacks volume or has unusable volume, the technical engine must merge adjusted close/high/low/open with TRADES volume where appropriate.

---

## 11. Fundamental Ranking Design

File:

```text
app/services/fundamental_ranker.py
```

### 11.1 Input

List of mapped CSV rows.

### 11.2 Output DTO

```python
@dataclass
class FundamentalScore:
    ticker: str
    growth_score: float
    profitability_score: float
    fcf_score: float
    balance_sheet_score: float
    valuation_score: float
    momentum_score: float
    dilution_score: float
    risk_score: float
    missing_data_penalty: float
    fundamental_score: float
    fundamental_label: str
    trap_flags: list[str]
    explanation: str
    debug: dict
```

### 11.3 Score modules

```text
Growth score
  Uses revenue growth, EPS growth, EBITDA growth, FCF growth, 5Y revenue CAGR.

Profitability score
  Uses gross margin, EBITDA margin, operating margin, net margin, ROE, ROIC, ROCE.

Free cash flow score
  Uses FCF TTM, FCF margin, FCF growth, P/FCF, EV/FCF, operating cash flow/share.

Balance sheet score
  Uses net debt/EBITDA, debt/equity, debt/assets, current ratio, debt/capital, interest coverage.

Valuation score
  Uses P/E, forward P/E, P/S, EV/revenue, EV/EBITDA, PEG, P/FCF.

Momentum score
  Uses 1W, 1M, 3M, 1Y performance and optional TradingView momentum.

Dilution score
  Uses buyback yield and total shares where history is available later.

Risk score
  Uses beta, leverage, liquidity, negative FCF, missing data, extreme valuation.
```

### 11.4 Labels

Allowed fundamental labels:

```text
Clean compounder
High-quality quant
Mixed but interesting
Value trap risk
Growth trap risk
Low priority
```

### 11.5 Missing data

Missing critical data must reduce confidence and apply a penalty. Missing values should not crash scoring.

Design rule:

```text
A missing value is neutral only when the metric is optional.
A missing value is penalized when the metric is essential to the label.
```

---

## 12. Technical Indicator Design

File:

```text
app/services/technical_indicators.py
```

### 12.1 Input

A price DataFrame with at least:

```text
date
open
high
low
close
volume
```

### 12.2 Output

A technical feature dictionary for the latest completed bar.

### 12.3 Indicators to implement

```text
EMA
SMA
RSI
ATR
ATR%
DMI / ADX
OBV
ROC %
Slope %
Slope ATR
Rolling sum
Pivot high / pivot low
52-week position
Pullback depth
Resistance
Fresh breakout
Failed breakout
Distribution count
Gap exhaustion
Stop / target / reward-risk
```

### 12.4 Pine compatibility details

The Python technical indicator functions should mimic Pine behavior closely.

Important Pine semantics to match:

```text
ta.sma
 ta.ema
 ta.rsi
 ta.atr
 ta.dmi
 ta.pivothigh
 ta.pivotlow
 ta.highest
 ta.lowest
 ta.highestbars
 math.sum
 historical indexing with source[n]
```

Implementation note:

- Use Pandas rolling windows for simple rolling operations.
- Implement Wilder-style smoothing for RSI, ATR, and ADX.
- Unit test against known Pine/TradingView output for selected tickers.

---

## 13. Pine Replica Engine Design

File:

```text
app/services/pine_replica_engine.py
```

### 13.1 Reference Pine files

The Python port must replicate the logic from:

```text
dual_trend_momentum_indicator_v3.pine
dual_trend_momentum_engine.pine
```

The engine library exports these functions:

```text
engineVersion
classPrimePullback
classCleanPullback
classFreshBreakout
classMomentumContinuation
classExtendedMomentum
classOverheatedMomentum
classFilteredPullback
classFilteredMomentum
classTrendRepair
classDistributionRisk
classBlowoffTop
classFailedBreakout
classNoTrade
pullbackHealthy
pullbackMixed
pullbackDangerous
marketBullish
marketMixed
marketBearish
marketRiskOff
rsStrong
rsNeutral
rsWeak
htfStrong
htfNeutral
htfWeak
clampScore
rocPct
slopePct
slopeAtr
pctDistance
rollingSum
distributionBar
relativeStrengthScore
combinedRelativeStrengthScore
relativeStrengthStatus
htfScore
htfStatus
localTrendScore
blendedTrendScore
momentumScore
setupScore
riskScore
dualScore
blowoffTop
distributionRisk
classifySetup
pullbackHealthStatus
filterProblemText
actionBiasText
```

### 13.2 Python module layout

```text
pine_replica_engine.py
  ├── constants and class labels
  ├── helper functions
  ├── relative strength functions
  ├── HTF functions
  ├── score functions
  ├── danger functions
  ├── classification function
  └── action text function
```

### 13.3 Main DTOs

```python
@dataclass
class PineInputs:
    ticker: str
    price_df: pd.DataFrame
    trades_df: pd.DataFrame
    market_df: pd.DataFrame
    benchmark_df: pd.DataFrame
    sector_df: pd.DataFrame | None
    params: PineParams

@dataclass
class TechnicalScore:
    ticker: str
    local_trend_score: float
    trend_score: float
    momentum_score: float
    setup_score: float
    risk_score: float
    market_score: float
    relative_strength_score: float
    sector_relative_strength_score: float
    combined_relative_strength_score: float
    htf_score: float
    dual_score: float
    classification: str
    action_bias: str
    pullback_health: str
    suggested_stop: float | None
    suggested_target: float | None
    reward_risk: float | None
    entry_risk_pct: float | None
    insufficient_data: bool
    debug: dict
```

### 13.4 Classification priority

The Python function must preserve Pine priority:

```text
Blowoff top
Failed breakout
Distribution risk
Overheated momentum
Prime clean pullback
Clean bull pullback
Fresh breakout
Momentum continuation
Extended momentum
Filtered pullback
Filtered momentum
Trend repair
No trade
```

### 13.5 Parameters

Defaults live in:

```text
config/pine_defaults.yaml
```

The app must save exact parameters used per run in `engine_parameters`.

---

## 14. Pine Defaults Config Design

File:

```text
config/pine_defaults.yaml
```

This file should contain all ported Pine defaults.

Example structure:

```yaml
engine:
  pine_version: "3.2.0"
  python_port_version: "3.2.0-port.1"

trend:
  emaFastLen: 10
  emaPullbackLen: 20
  smaMidLen: 50
  smaTrendLen: 150
  smaSlowLen: 200
  midSlopeLookback: 10
  slowSlopeLookback: 20
  adxLen: 14
  adxSmoothing: 14
  minAdxTrend: 18.0
  structureLookback: 20
  pivotLeftBars: 3
  pivotRightBars: 3
  highLow52Len: 252

momentum:
  rsiLen: 14
  atrLen: 14
  volLen: 20
  greenRedVolLookback: 10
  recentRedVolLookback: 5
  distributionLookback: 10
  obvSmaLen: 20
  obvSlopeLookback: 10

pullback_breakout:
  pullbackLookback: 20
  breakoutLookback: 40
  minPullbackPct: 3.0
  maxPullbackPct: 18.0
  maTouchPct: 4.0
  breakoutVolRatio: 1.20
  failureVolRatio: 1.10

risk:
  extensionWarnPct: 8.0
  extensionDangerPct: 15.0
  heavyRedVolRatio: 1.50
  nearResistancePct: 3.0
  failedBreakoutBars: 8
  atrWarnPct: 6.0
  atrDangerPct: 10.0
  gapExhaustionPct: 4.0
  gapExhaustionVolRatio: 1.30
  minAvgVolume: 300000
  useLiquidityWarning: true
  useNotionalLiquidityFilter: true
  minNotionalVolume: 10000000
  notionalVolumeLookback: 20

stop_target:
  stopMode: "Structure + ATR"
  atrStopMultiple: 1.50
  smaStopAtrBuffer: 0.50
  structureAtrBuffer: 0.30
  targetMode: "R Multiple"
  targetRewardMultiple: 2.0
  atrTargetMultiple: 3.0
  minRewardRisk: 2.0
  useModeRewardRisk: true
  useRewardRiskFilter: true

market_rs:
  useMarketFilter: true
  useRelativeStrengthFilter: true
  useSectorBenchmark: false
  marketSymbol: "SPY"
  benchmarkSymbol: "SPY"
  sectorSymbol: "QQQ"
  marketMinScore: 5.5
  rsMinScore: 5.5
  sectorMinScore: 5.0
  marketDistributionMax: 4
  marketRiskOffPenalty: 2.0
  rsSmaLen: 50
  rocShortLen: 21
  rocMediumLen: 63
  rocLongLen: 126
  rsNewHighLookback: 21

htf:
  useHtfTrendFilter: true
  blendHtfIntoTrendScore: true
  htfTimeframe: "W"
  useConfirmedHtf: true
  htfFastLen: 10
  htfMidLen: 30
  htfSlowLen: 40
  htfSlopeLookback: 4
  htfRocLookback: 13
  htfMinScore: 5.5

scoring:
  scoringMode: "Balanced"
```

---

## 15. Combined Decision Engine Design

File:

```text
app/services/combined_decision_engine.py
```

### 15.1 Inputs

```text
Raw company row
FundamentalScore
TechnicalScore
```

### 15.2 Output

```python
@dataclass
class CombinedResult:
    ticker: str
    company_name: str
    sector: str | None
    final_score: float
    final_rank: int | None
    fundamental_label: str
    technical_classification: str
    combined_decision: str
    position_size_hint: str | None
    notes: str
```

### 15.3 Decision rules

Initial rule examples:

```text
Danger technical classes → Avoid
Overheated momentum → Wait for pullback
Prime clean pullback + strong fundamentals → Strong candidate
Clean bull pullback + high-quality fundamentals → Candidate
Fresh breakout + good fundamentals → Breakout candidate
Low priority fundamentals + weak/no trade technical → Low priority
Value trap risk → Avoid unless technical classification is very strong
Growth trap risk → High risk / reduced size unless technical classification is very strong
```

### 15.4 Final score design

Initial formula should be simple and explainable:

```text
base_score = 0.55 * fundamental_score + 0.45 * dual_score
```

Then apply penalties:

```text
Danger classification penalty
Overheated classification penalty
Value trap risk penalty
Growth trap risk penalty
Missing data penalty
Liquidity warning penalty
```

The exact weights should be stored in:

```text
config/scoring_weights.yaml
```

---

## 16. API Route Design

### 16.1 Upload routes

```text
GET  /                  → upload page
POST /uploads           → upload CSV and start/process run
GET  /uploads/{run_id}  → upload/run status
```

### 16.2 Run routes

```text
GET /runs               → list previous runs
GET /runs/{run_id}      → run summary
POST /runs/{run_id}/reprocess → reprocess using cached data
```

### 16.3 Result routes

```text
GET /runs/{run_id}/results             → final result table
GET /runs/{run_id}/results/table       → HTMX partial table
GET /runs/{run_id}/results?filter=...  → filtered results
```

### 16.4 Ticker detail routes

```text
GET /runs/{run_id}/tickers/{ticker}    → ticker detail page
```

### 16.5 Export routes

```text
GET /runs/{run_id}/export/xlsx         → XLSX export
GET /runs/{run_id}/export/csv          → CSV export
```

### 16.6 IB routes

```text
GET  /ib/status                       → IB connection status
POST /ib/test                         → test IB connection
POST /ib/resolve/{ticker}             → resolve one ticker
```

---

## 17. UI Design

### 17.1 Pages

```text
Upload page
Runs page
Results page
Ticker detail page
Settings page
```

### 17.2 Upload page

Elements:

```text
App title
CSV drag/drop box
Upload button
IB connection status badge
Recent runs link
Validation/error panel
```

### 17.3 Results page

Elements:

```text
Run metadata
Summary cards
Filter buttons
Sortable result table
Export buttons
```

Summary cards:

```text
Total tickers
Strong candidates
Candidates
Wait for pullback
Avoid/danger
IB failures
```

### 17.4 Result table columns

```text
Rank
Ticker
Company
Final decision
Fundamental label
Fundamental score
Technical classification
Dual score
Trend score
Momentum score
Setup score
Risk score
Reward/risk
Suggested stop
Suggested target
Notes
```

### 17.5 Ticker detail page

Sections:

```text
Header: ticker, company, sector, final decision
Fundamental score breakdown
Trap flags
Technical score breakdown
Indicator debug values
Stop/target block
Raw CSV JSON
IB bar status
Historical run comparison
```

---

## 18. Export Design

File:

```text
app/services/xlsx_exporter.py
```

Workbook sheets:

```text
Final Results
Fundamental Scores
Technical Scores
Raw Uploaded CSV
Engine Parameters
Errors / Missing Data
```

Design rules:

- Freeze header row.
- Auto-size columns where practical.
- Apply simple conditional coloring for final decision.
- Preserve raw uploaded values.
- Include engine version and parameter snapshot.

---

## 19. Error Handling Design

### 19.1 Error model

Use structured errors:

```python
@dataclass
class ProcessingError:
    ticker: str | None
    stage: str
    code: str
    message: str
    details: dict
```

### 19.2 Error stages

```text
CSV_LOAD
CSV_VALIDATE
IB_CONNECT
IB_CONTRACT_RESOLVE
IB_FETCH_BARS
FUNDAMENTAL_SCORE
TECHNICAL_SCORE
COMBINED_SCORE
EXPORT
```

### 19.3 Error behavior

- A CSV-level fatal error fails the run.
- A ticker-level error does not fail the whole run.
- Failed tickers remain visible in the final table.
- Error details are stored and exported.

---

## 20. Testing Design

### 20.1 Unit tests

Required tests:

```text
test_csv_loader.py
test_column_mapper.py
test_fundamental_ranker.py
test_technical_indicators.py
test_pine_replica_engine.py
test_combined_decision_engine.py
test_xlsx_exporter.py
```

### 20.2 Pine parity tests

Create a fixture with known TradingView outputs for selected tickers.

Compare:

```text
trend score
momentum score
setup score
risk score
market score
relative strength score
HTF score
dual score
classification
suggested stop
suggested target
reward/risk
```

Expected tolerance:

```text
Scores: allow small decimal differences
Classification: should match unless data source difference explains mismatch
```

### 20.3 IB integration tests

IB integration tests should be optional because they require TWS/Gateway.

Mark them separately:

```text
pytest -m ib
```

---

## 21. Logging and Observability

Use structured logs.

Log events:

```text
run_created
csv_loaded
csv_validation_failed
ib_connected
ib_connection_failed
contract_resolved
contract_resolution_failed
bars_fetched
bars_fetch_failed
fundamental_scored
technical_scored
combined_result_saved
run_completed
run_failed
```

Each log should include:

```text
run_id
ticker when applicable
stage
duration_ms
error code when applicable
```

---

## 22. Security Design

Because SwingLens is local-only:

- No user login is required for MVP.
- App binds to localhost by default.
- `.env` stores database and IB settings.
- `.env` must not be committed.
- Uploaded files remain local.
- No broker order endpoints exist.
- No external fundamental API calls are made.

---

## 23. Implementation Sequence

### Slice 1: Project skeleton

```text
FastAPI app
Jinja2 base template
PostgreSQL connection
Alembic setup
Health endpoint
```

### Slice 2: CSV upload

```text
Upload page
CSV loader
Column mapper
Raw row persistence
Run list
```

### Slice 3: Fundamental scoring

```text
Fundamental ranker
Labels
Trap flags
Fundamental result view
```

### Slice 4: IB integration

```text
IB status
Contract resolver
Historical data fetcher
Bar cache
Benchmark fetching
```

### Slice 5: Technical engine

```text
Technical indicators
Pine helpers
Pine score functions
Classification
Debug JSON
```

### Slice 6: Combined cockpit

```text
Combined decision engine
Results table
Filters
Ticker detail
```

### Slice 7: Export and polish

```text
XLSX export
CSV export
Error sheet
Documentation
Tests
```

---

## 24. Critical Design Decisions

### 24.1 Preserve raw data

Every uploaded CSV row is preserved as JSON. This prevents data loss and makes future scoring changes possible.

### 24.2 Separate fundamental and technical engines

The two engines must remain independent:

```text
fundamental_ranker.py does not need OHLCV
the technical engine does not need financial metrics
combined_decision_engine.py merges both
```

### 24.3 Store debug values generously

The app must be able to explain why a ticker received a classification.

Debug JSON should include:

```text
raw indicator values
boolean gates
risk flags
filter results
classification intermediate booleans
stop/target inputs
```

### 24.4 No hidden trading behavior

SwingLens may say:

```text
Strong candidate
Candidate
Wait for pullback
Avoid
```

It must never say or do:

```text
Place order
Buy automatically
Sell automatically
Modify stop
```

---

## 25. Future Extension Points

Not part of MVP, but the architecture should not block:

```text
Additional markets
Additional data vendors
More chart screenshots
Manual notes per ticker
Portfolio review
Backtest result imports
```

These should remain optional modules, not MVP dependencies.

---

## 26. Final Design Summary

SwingLens is designed as a local, modular, auditable stock research cockpit.

```text
CSV Loader                 → gets the daily universe
Fundamental Ranker          → judges company numbers
IB Fetcher                  → gets market data
Technical Indicator Engine  → builds Pine-compatible inputs
Pine Replica Engine         → classifies trend/momentum setup
Combined Decision Engine    → creates final ranked decision
PostgreSQL                  → stores memory
Jinja2 + HTMX UI            → displays the cockpit
```

The design keeps the app small enough to build, but structured enough to grow. It avoids spreadsheet sorcery, TradingView export dependency, and automatic trading traps. The engine room stays Python, the memory stays PostgreSQL, and the browser becomes the daily command deck.
