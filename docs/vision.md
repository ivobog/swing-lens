# SwingLens — Project Vision

**Project name:** SwingLens  
**Owner:** Ivica Bogoevski  
**Status:** Vision / MVP blueprint  
**Mode:** Local-only decision-support web app  
**Auto-trading:** Never

---

## 1. One-sentence vision

**SwingLens** is a simple local web app where I drag and drop my daily company CSV, the app fetches historical OHLCV data from Interactive Brokers, ranks companies fundamentally, calculates a Python replica of my Pine dual trend + momentum classification, saves the full run in PostgreSQL, and shows one clean decision table for every company.

---

## 2. Why SwingLens exists

The current workflow is powerful but manual:

1. Export a CSV with company financial numbers.
2. Rank the companies by growth, profitability, free cash flow, balance sheet, valuation, dilution, and risk.
3. Open interesting tickers in TradingView.
4. Apply the dual trend + momentum Pine indicator/strategy.
5. Manually interpret classification, trend, momentum, risk, stop, target, and setup quality.

SwingLens turns this into a repeatable local research process:

```text
Daily CSV
→ fundamental ranking
→ IB OHLCV fetch
→ Python dual trend + momentum engine
→ combined decision table
→ PostgreSQL history
→ optional XLSX/CSV export
```

The goal is **not** automatic trading.  
The goal is **better decision making, repeatability, auditability, and faster screening**.

---

## 3. Fixed MVP decisions

| Area | Decision |
|---|---|
| App name | **SwingLens** |
| Framework | **FastAPI + Jinja2 + HTMX** |
| Database | **PostgreSQL** |
| IB connector | **ib_insync wrapper** |
| Market universe | **US stocks only** |
| Historical data type | **Both: ADJUSTED_LAST and TRADES** |
| Fundamental data source | **Uploaded CSV only** |
| Auto-trading | **Never** |

---

## 4. Core architecture decision

SwingLens will use **Path C: fetch OHLCV data from Interactive Brokers**.

This is better than relying on TradingView CSV technical fields because:

- TradingView standard screener exports may not include all custom Pine values.
- Pine Screener export capability may be limited or inconsistent.
- Python can reproduce the Pine logic more faithfully from OHLCV data.
- The app becomes independent from TradingView technical export limitations.
- Technical values can be recalculated consistently for every uploaded universe.
- Every daily run can be stored and compared historically.

The uploaded CSV remains the source for **fundamental/company data**.  
Interactive Brokers provides **daily/weekly OHLCV market data** for technical calculations.

---

## 5. MVP scope

### In scope

- Local-only web app.
- Drag/drop CSV upload.
- Validate CSV columns.
- Save original CSV rows.
- Run fundamental ranking script.
- Fetch OHLCV from IB using `ib_insync`.
- Store both `ADJUSTED_LAST` and `TRADES` historical data where available.
- Calculate technical indicators in Python.
- Replicate Pine v3.2 dual trend + momentum scoring/classification.
- Show combined result table.
- Save all results in PostgreSQL.
- Export results to XLSX/CSV.
- Store run history.

### Out of scope

- Automatic trading.
- Broker order placement.
- Live intraday dashboard.
- Full portfolio management.
- Options logic.
- Cloud deployment.
- Multi-user authentication.
- React frontend.
- Financial/news AI module.

---

## 6. Recommended technology stack

```text
Backend:      Python + FastAPI
Frontend:     Jinja2 templates + HTMX + Bootstrap
Database:     PostgreSQL
Data engine:  Pandas + NumPy
IB access:    ib_insync
Exports:      openpyxl
Local run:    Docker Compose recommended
```

### Why this stack

- **FastAPI** is simple, fast, and clean for a local web app.
- **Jinja2 + HTMX** gives enough interactivity without frontend complexity.
- **PostgreSQL** gives durable historical storage and future extensibility.
- **Pandas/NumPy** are ideal for CSV processing, ranking, and indicator calculations.
- **ib_insync** is a practical wrapper around the IB API and faster for MVP implementation.
- **openpyxl** enables XLSX exports for review and archiving.

---

## 7. Main daily workflow

1. Start IB Gateway or Trader Workstation locally.
2. Start SwingLens locally.
3. Open browser at `http://localhost:8000`.
4. Drag and drop the daily CSV.
5. Click **Process**.
6. SwingLens validates the CSV.
7. SwingLens stores the raw uploaded rows.
8. SwingLens fetches/updates IB historical bars for all US stock tickers.
9. SwingLens runs the fundamental ranking engine.
10. SwingLens runs the Python dual trend + momentum engine.
11. SwingLens merges the fundamental and technical outputs.
12. SwingLens shows a sortable result table.
13. SwingLens stores the full run in PostgreSQL.
14. User reviews the best candidates and can open selected tickers in TradingView if needed.

---

## 8. Data sources

### 8.1 Uploaded CSV

The uploaded CSV is the only source for company fundamental numbers in the MVP.

Expected fields may include roughly 70 company metrics, such as:

```text
Ticker
Company
Market Cap
Revenue Growth
EPS Growth
FCF Growth
Gross Margin
Operating Margin
Net Margin
ROE
ROIC
Debt/Equity
Debt/Assets
Current Ratio
P/E
Forward P/E
P/S
P/FCF
EV/EBITDA
Buyback Yield
Shares Dilution
Beta
Dollar Volume
ATR %
```

The exact field names should be handled through a flexible column mapper, because CSV exports may change names slightly.

### 8.2 Interactive Brokers historical data

Initial market scope:

```text
US stocks only
```

Recommended historical range:

```text
At least 2 years of daily bars
Preferably 600–800 daily bars when available
```

Why:

- SMA200 needs at least 200 bars.
- 52-week position needs about 252 trading days.
- Long ROC uses 126 bars.
- Weekly HTF logic needs enough weekly history.
- More history improves indicator stability.

### 8.3 Historical data type

SwingLens should store both:

```text
ADJUSTED_LAST
TRADES
```

Recommended usage:

```text
ADJUSTED_LAST → primary price calculations when available
TRADES        → volume and fallback calculations
```

If both are available, store both and record which one was used for each technical output.

---

## 9. IB integration

### Required local setup

The user must run one of:

```text
IB Gateway
Trader Workstation
```

API access must be enabled.

### Recommended request style

For MVP:

```text
bar size:       1 day
duration:       2 Y
whatToShow:     ADJUSTED_LAST and TRADES
useRTH:         1
keepUpToDate:   false
```

### Important caveats

IB integration can be tricky because:

- Symbol resolution may require exchange, primary exchange, security type, and currency.
- US stocks should start with currency `USD` and security type `STK`.
- Historical data permissions depend on the IB account.
- IB has pacing limits.
- Different data vendors may produce slightly different bars.
- TradingView and IB may differ because of adjustments and session logic.

SwingLens should cache IB bars and fetch incrementally to avoid unnecessary requests.

---

## 10. System architecture

```text
Browser
  |
  v
FastAPI Web App
  |
  +--> CSV Upload / Validation
  |
  +--> Fundamental Ranking Engine
  |
  +--> IB Contract Resolver
  |
  +--> IB Historical Data Fetcher
  |
  +--> Bar Cache Service
  |
  +--> Technical Indicator Engine
  |
  +--> Pine Replica Classification Engine
  |
  +--> Combined Decision Engine
  |
  +--> PostgreSQL Database
  |
  +--> Results Table / XLSX Export
```

---

## 11. Proposed folder structure

```text
swing-lens/
  README.md
  pyproject.toml
  .env.example
  docker-compose.yml

  docs/
    vision.md

  app/
    main.py
    settings.py
    db.py

    models/
      database.py
      schemas.py

    routers/
      upload_routes.py
      result_routes.py
      export_routes.py
      settings_routes.py

    services/
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

    templates/
      base.html
      upload.html
      runs.html
      results.html
      ticker_detail.html
      settings.html

    static/
      app.css

  config/
    column_aliases.yaml
    pine_defaults.yaml

  data/
    uploads/
    exports/
    cache/

  tests/
    test_fundamental_ranker.py
    test_technical_indicators.py
    test_pine_replica_engine.py
    test_combined_decision_engine.py
```

---

## 12. Database design

PostgreSQL is the MVP database.

### `upload_runs`

```sql
id
filename
uploaded_at
processed_at
row_count
status
pine_engine_version
python_engine_version
notes
```

### `raw_company_rows`

```sql
id
run_id
ticker
company_name
raw_json
```

### `ib_contracts`

```sql
id
ticker
ib_conid
symbol
exchange
primary_exchange
currency
sec_type
last_resolved_at
resolution_status
```

### `price_bars`

```sql
id
ticker
bar_date
timeframe
open
high
low
close
volume
source
what_to_show
adjustment_type
created_at
```

### `fundamental_scores`

```sql
id
run_id
ticker
growth_score
profitability_score
fcf_score
balance_sheet_score
valuation_score
momentum_score
dilution_score
risk_score
fundamental_score
fundamental_label
trap_flags_json
explanation
```

### `technical_scores`

```sql
id
run_id
ticker
trend_score
momentum_score
setup_score
risk_score
market_score
relative_strength_score
sector_relative_strength_score
combined_relative_strength_score
htf_score
dual_score
classification
pullback_health
action_bias
suggested_stop
suggested_target
reward_risk
entry_risk_pct
technical_confidence
missing_data_json
debug_json
```

### `combined_results`

```sql
id
run_id
ticker
company_name
final_rank
final_score
fundamental_label
technical_classification
dual_score
combined_decision
position_size_hint
notes
```

### `engine_parameters`

```sql
id
run_id
parameters_json
created_at
```

---

## 13. Fundamental ranking engine

The fundamental ranking script should reproduce the existing scoring framework:

```text
Growth
Profitability
Free cash flow
Balance sheet strength
Valuation
Momentum
Dilution
Risk
```

### Output labels

```text
Clean compounder
High-quality quant
Mixed but interesting
Value trap risk
Growth trap risk
Low priority
```

### Purpose

The fundamental engine answers:

```text
Is this a good company by the numbers?
Is it profitable?
Is growth supported by cash flow?
Is the balance sheet safe?
Is valuation extreme?
Is dilution hurting shareholders?
Is it a value trap or growth trap?
```

---

## 14. Technical engine: Python replica of Pine v3.2

The technical engine should port the Pine v3.2 logic into Python.

### Main indicator calculations

Calculate from OHLCV:

```text
EMA10
EMA20
SMA50
SMA150
SMA200
RSI14
ATR14
ATR%
Average volume
Volume ratio
DMI / ADX
OBV
OBV SMA
OBV rising
ROC 21 / 63 / 126
52-week high/low position
Pivot higher high / higher low
Pullback depth
Near MA support
Extension above SMA50
Previous resistance
Fresh breakout
Green vs red volume quality
Volume dry-up
Heavy red candle
Gap exhaustion
Distribution count
Failed breakout
Suggested stop
Suggested target
Reward/risk
```

### Market and RS calculations

For every stock, also calculate:

```text
Market score, default SPY
Market regime
Market risk-off
Stock vs benchmark relative strength
Stock vs sector relative strength
Combined relative strength score
HTF weekly trend score
```

Even though the MVP market universe is US stocks only, market and benchmark symbols such as SPY and QQQ should still be fetched from IB because they are required for market regime and relative strength.

### Pine classification target

The Python engine should reproduce the Pine classification classes:

```text
Prime clean pullback
Clean bull pullback
Fresh breakout
Momentum continuation
Extended momentum
Overheated momentum
Filtered pullback
Filtered momentum
Trend repair
Distribution risk
Blowoff top
Failed breakout
No trade
```

### Exact parity target

```text
Python output should match TradingView Pine output for at least 90–95% of tested rows.
```

Some differences are expected because IB and TradingView may use different market data, adjustment logic, and time zones.

---

## 15. Pine defaults to port

Initial default values should match the Pine v3.2 indicator.

```yaml
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
rsiLen: 14
atrLen: 14
volLen: 20
greenRedVolLookback: 10
recentRedVolLookback: 5
distributionLookback: 10
obvSmaLen: 20
obvSlopeLookback: 10
pullbackLookback: 20
breakoutLookback: 40
minPullbackPct: 3.0
maxPullbackPct: 18.0
maTouchPct: 4.0
breakoutVolRatio: 1.20
failureVolRatio: 1.10
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
stopMode: Structure + ATR
atrStopMultiple: 1.50
smaStopAtrBuffer: 0.50
structureAtrBuffer: 0.30
targetMode: R Multiple
targetRewardMultiple: 2.0
atrTargetMultiple: 3.0
minRewardRisk: 2.0
useModeRewardRisk: true
useRewardRiskFilter: true
useMarketFilter: true
useRelativeStrengthFilter: true
useSectorBenchmark: false
marketSymbol: SPY
benchmarkSymbol: SPY
sectorSymbol: QQQ
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
useHtfTrendFilter: true
blendHtfIntoTrendScore: true
htfTimeframe: W
useConfirmedHtf: true
htfFastLen: 10
htfMidLen: 30
htfSlowLen: 40
htfSlopeLookback: 4
htfRocLookback: 13
htfMinScore: 5.5
scoringMode: Balanced
```

---

## 16. Combined decision engine

SwingLens should not simply show two separate scores. It should combine them into a final decision.

Example decision logic:

```text
If fundamental label is Clean compounder
and technical classification is Prime clean pullback
and dual score >= 8
then Final Decision = Strong candidate
```

```text
If technical classification is Overheated momentum
then Final Decision = Wait for pullback
```

```text
If technical classification is Distribution risk / Failed breakout / Blowoff top
then Final Decision = Avoid / danger
```

### Suggested final decision labels

```text
Strong candidate
Candidate
Watch
Wait for pullback
Breakout candidate
High risk / reduced size
Avoid
Low priority
```

---

## 17. Result screen

### Main table columns

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

### Useful filters

```text
Only strong candidates
Only buyable technical setups
Only clean compounders
Only high-quality quant
Hide avoid / low priority
Show overheated names
Show danger names
Show missing IB data
```

### Ticker detail page

```text
Raw CSV numbers
Fundamental score breakdown
Technical score breakdown
OHLCV fetch status
Indicator values
Classification reasoning
Stop/target calculation
Historical run comparison
```

---

## 18. XLSX export

The app should export a workbook with:

```text
Sheet 1: Final Results
Sheet 2: Fundamental Scores
Sheet 3: Technical Scores
Sheet 4: Raw Uploaded CSV
Sheet 5: Engine Parameters
Sheet 6: Errors / Missing Data
```

---

## 19. Validation plan

Use 10–20 liquid US tickers:

```text
MSFT
GOOGL
META
LLY
NVDA
AVGO
COLB
BUSE
SPY
QQQ
```

For each ticker, compare:

```text
TradingView Pine classification
Python classification
TradingView dual score
Python dual score
TradingView trend score
Python trend score
TradingView momentum score
Python momentum score
TradingView stop/target
Python stop/target
```

Expected differences may come from:

```text
IB adjusted vs TradingView adjusted data
TradingView session handling
volume differences
timezone alignment
weekly bar construction
indicator rounding
IB pacing/missing bars
```

Success criteria:

```text
Core classification matches for most liquid US stocks.
Score differences are explainable.
Stop/target differences are small or caused by data source differences.
```

---

## 20. Development roadmap

### MVP 0.1 — App shell and CSV upload

- FastAPI local app.
- Jinja2 + HTMX upload screen.
- Drag/drop CSV.
- Store upload run.
- Display raw rows.
- Basic validation.
- PostgreSQL connection.

### MVP 0.2 — Fundamental ranking engine

- Port current company ranking logic.
- Produce fundamental score and label.
- Display fundamental results.
- Save results.

### MVP 0.3 — IB data fetcher

- Connect to local IB Gateway/TWS with `ib_insync`.
- Resolve US stock contracts.
- Fetch daily bars for `ADJUSTED_LAST`.
- Fetch daily bars for `TRADES`.
- Cache bars in PostgreSQL.
- Show missing/error status.

### MVP 0.4 — Technical indicator engine

- Implement EMA/SMA/RSI/ATR/ADX/OBV/ROC.
- Implement market regime, RS, HTF.
- Implement pullback, breakout, risk/reward.
- Implement Pine v3.2 classification.

### MVP 0.5 — Combined result cockpit

- Merge fundamental + technical outputs.
- Show final decision.
- Add filters.
- Add ticker detail page.

### MVP 0.6 — Export and history

- XLSX export.
- Historical runs list.
- Compare ticker across runs.

### Version 1.0 — Stable local research cockpit

- Tested with daily workflow.
- Pine parity validation.
- Good error handling.
- Documentation.

---

## 21. Major risks

### Risk 1: IB data differs from TradingView

Mitigation:

```text
Store source type.
Validate against Pine.
Use adjusted data where possible.
Accept small score differences.
Focus on classification consistency.
```

### Risk 2: IB contract resolution fails

Mitigation:

```text
Create ib_contracts table.
Allow manual correction.
Store primary exchange and currency.
Start with US stocks only.
```

### Risk 3: IB pacing limits

Mitigation:

```text
Cache bars.
Fetch incrementally.
Throttle requests.
Process tickers sequentially or in small batches.
```

### Risk 4: Python Pine replica drifts from Pine script

Mitigation:

```text
Store engine version.
Add unit tests.
Port engine functions explicitly.
Keep pine_defaults.yaml synced.
Validate after every Pine change.
```

### Risk 5: App becomes too big too early

Mitigation:

```text
No React in MVP.
No auto trading.
No cloud deployment.
No news module in MVP.
No portfolio module in MVP.
```

---

## 22. Final product definition

The finished MVP should let me say:

```text
I uploaded today’s CSV.
SwingLens ranked all companies fundamentally.
SwingLens fetched OHLCV from IB.
SwingLens calculated the same type of trend/momentum classification as my Pine script.
SwingLens showed which companies are clean compounders, traps, buyable technical setups, overheated setups, and danger names.
SwingLens saved everything so I can compare tomorrow.
```

That is the product.

It is not an autopilot.  
It is a local research cockpit.

The CSV is the candidate list.  
IB is the market-data engine.  
Python is the scoring brain.  
PostgreSQL is the memory.  
The browser is the cockpit.
