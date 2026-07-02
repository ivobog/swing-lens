# SwingLens — Software Requirements Specification

**Project:** SwingLens  
**Document type:** Software Requirements Specification (SRS)  
**Version:** 0.1  
**Owner:** Ivica Bogoevski  
**Status:** Draft for MVP implementation  
**Mode:** Local-only decision-support web app  
**Auto-trading:** Never

---

## 1. Introduction

### 1.1 Purpose

This document defines the functional and non-functional requirements for **SwingLens**, a local web application for daily stock research.

SwingLens will accept a daily CSV file containing US stock/company fundamentals, fetch historical OHLCV data from Interactive Brokers, calculate fundamental rankings, calculate technical trend/momentum classifications using a Python replica of the Pine v3.2 dual trend + momentum engine, and store all results in PostgreSQL.

The document is intended for implementation planning, development, testing, and future maintenance.

### 1.2 Product scope

SwingLens is a **local research cockpit**, not an automated trading system.

It will help the user answer:

```text
Which companies are strongest by fundamentals?
Which companies have clean technical setups?
Which companies are traps, overheated, broken, or low priority?
Which candidates should be opened in TradingView for final visual review?
How did today’s results compare with previous runs?
```

### 1.3 Fixed MVP decisions

| Area | Requirement |
|---|---|
| Application name | SwingLens |
| Framework | FastAPI + Jinja2 + HTMX |
| Database | PostgreSQL |
| IB connector | ib_insync |
| Market universe | US stocks only |
| Historical data type | Both `ADJUSTED_LAST` and `TRADES` |
| Fundamental data source | Uploaded CSV only |
| Auto-trading | Never |
| Deployment | Local-only |

### 1.4 Definitions

| Term | Meaning |
|---|---|
| CSV | Daily file uploaded by the user containing company fundamentals and tickers. |
| OHLCV | Open, high, low, close, volume historical price data. |
| IB | Interactive Brokers. |
| TWS | Trader Workstation. |
| IB Gateway | Lightweight IB application used for API connectivity. |
| `ib_insync` | Python wrapper around the Interactive Brokers API. |
| Pine engine | The TradingView Pine Script dual trend + momentum logic being replicated in Python. |
| Fundamental score | Numeric ranking derived from company financial metrics. |
| Technical score | Numeric trend/momentum/risk classification derived from OHLCV and benchmark data. |
| Combined decision | Final merged label from fundamental and technical engines. |
| Run | One complete CSV upload and processing cycle. |

---

## 2. Overall Description

### 2.1 Product perspective

SwingLens is a standalone local application with these major components:

```text
Browser UI
FastAPI backend
CSV upload service
Fundamental ranking engine
IB historical data fetcher
Technical indicator engine
Pine replica classification engine
Combined decision engine
PostgreSQL database
XLSX/CSV export module
```

SwingLens will not depend on TradingView for technical calculations. TradingView may still be used by the user for manual chart review after SwingLens produces its decision table.

### 2.2 User class

The MVP has one intended user:

```text
A local trader/researcher who uploads a CSV, reviews ranked candidates, and manually decides what to trade.
```

No multi-user access control is required for the MVP.

### 2.3 Operating environment

SwingLens must run locally on the user’s machine.

Recommended runtime environment:

```text
Python 3.11+
FastAPI
Jinja2
HTMX
PostgreSQL
Docker Compose for PostgreSQL
IB Gateway or Trader Workstation running locally
```

### 2.4 Constraints

- The app must not place orders.
- The app must not modify the user’s IB account.
- The app must not send data to external APIs for fundamentals.
- The app must use uploaded CSV as the only fundamental data source in MVP.
- The app must support US stocks only in MVP.
- The app must store results in PostgreSQL.
- The app must use `ib_insync` for IB connectivity.
- The app must fetch and store both `ADJUSTED_LAST` and `TRADES` bars when available.

### 2.5 Assumptions

- The user has an Interactive Brokers account with market data access for the relevant US stocks.
- IB Gateway or TWS is running before starting a full processing run.
- The daily CSV contains a ticker column and enough fundamental metrics for ranking.
- Some tickers may fail IB resolution and must be reported clearly.
- IB data and TradingView data may differ slightly.

---

## 3. System Features and Functional Requirements

Requirement priority levels:

```text
MUST   = required for MVP
SHOULD = important but can be implemented after the first working slice
COULD  = optional enhancement
WON'T  = explicitly out of scope
```

---

## 4. CSV Upload and Run Management

### FR-001: Upload CSV

**Priority:** MUST

The system must provide a web page where the user can drag and drop or select a CSV file.

Acceptance criteria:

- User can upload a `.csv` file from the browser.
- The app rejects non-CSV files.
- The app stores the original file under a local upload directory.
- The app creates an `upload_runs` database record.
- The app records upload timestamp, filename, status, and row count.

### FR-002: Validate CSV structure

**Priority:** MUST

The system must validate that the CSV contains required columns.

Minimum required columns:

```text
Ticker
```

Recommended columns:

```text
Company
Market Cap
Revenue Growth
EPS Growth
FCF Growth
Margins
Debt metrics
Valuation metrics
Dilution metrics
Liquidity metrics
Risk metrics
```

Acceptance criteria:

- Missing ticker column prevents processing.
- Missing optional columns do not prevent processing but reduce confidence.
- Validation errors are shown in the UI.
- Validation errors are stored in the database.

### FR-003: Store raw CSV rows

**Priority:** MUST

The system must store every CSV row as raw JSON.

Acceptance criteria:

- Each row is associated with an upload run.
- Original values are preserved.
- Original values remain available for later review and export.

### FR-004: Column alias mapping

**Priority:** SHOULD

The system should support a configurable column alias map.

Example:

```yaml
ticker:
  - Ticker
  - Symbol
  - Ticker Symbol
market_cap:
  - Market Cap
  - Market Capitalization
```

Acceptance criteria:

- Column aliases are loaded from `config/column_aliases.yaml`.
- The system maps known aliases to canonical internal names.
- Unknown columns are preserved in raw JSON.

---

## 5. Fundamental Ranking Engine

### FR-005: Calculate fundamental scores

**Priority:** MUST

The system must calculate a fundamental score for each company based on uploaded CSV values.

Score components:

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

Acceptance criteria:

- Each component receives a numeric score.
- Missing values are penalized or marked as missing.
- Final fundamental score is calculated for every row with a ticker.
- Score calculation is deterministic for the same input.

### FR-006: Assign fundamental labels

**Priority:** MUST

The system must assign one primary fundamental label.

Allowed labels:

```text
Clean compounder
High-quality quant
Mixed but interesting
Value trap risk
Growth trap risk
Low priority
```

Acceptance criteria:

- Every processed ticker receives exactly one primary fundamental label.
- Labels are stored in `fundamental_scores`.
- Labels are visible in the results table.

### FR-007: Detect trap flags

**Priority:** MUST

The system must detect possible trap conditions.

Trap flag examples:

```text
Negative free cash flow
High leverage
Weak liquidity
Extreme valuation
Share dilution
Growth without profitability
Cheap but deteriorating fundamentals
Missing critical data
```

Acceptance criteria:

- A ticker can have zero, one, or multiple trap flags.
- Trap flags are stored as JSON.
- Trap flags are visible on the ticker detail page.

### FR-008: Fundamental explanation

**Priority:** SHOULD

The system should generate a short explanation for each fundamental label.

Acceptance criteria:

- Explanation references the strongest positive and negative score components.
- Explanation is stored with the fundamental score.
- Explanation is shown in the ticker detail page.

---

## 6. Interactive Brokers Integration

### FR-009: Connect to IB

**Priority:** MUST

The system must connect to IB Gateway or TWS using `ib_insync`.

Acceptance criteria:

- IB host, port, and client ID are configurable.
- The system can test the IB connection from the UI or backend.
- Connection errors are displayed clearly.
- Failed IB connection prevents technical processing but does not delete uploaded data.

Default local connection examples:

```text
TWS paper:       127.0.0.1:7497
TWS live:        127.0.0.1:7496
IB Gateway paper: configurable
IB Gateway live: configurable
```

### FR-010: Resolve US stock contracts

**Priority:** MUST

The system must resolve each ticker to an IB contract.

Default contract assumptions:

```text
secType: STK
currency: USD
exchange: SMART
market: US
```

Acceptance criteria:

- Contract metadata is stored in `ib_contracts`.
- Contract resolution status is stored.
- Failed tickers are shown in the UI.
- The user can still view fundamental results for unresolved tickers.

### FR-011: Fetch historical daily bars

**Priority:** MUST

The system must fetch historical daily bars for every resolved ticker.

Required data types:

```text
ADJUSTED_LAST
TRADES
```

Recommended request parameters:

```text
barSizeSetting: 1 day
durationString: 2 Y
useRTH: 1
keepUpToDate: false
```

Acceptance criteria:

- Bars are stored in `price_bars`.
- `what_to_show` identifies `ADJUSTED_LAST` or `TRADES`.
- Duplicate bars are not inserted.
- The system records fetch failures.
- The system can continue processing other tickers if one ticker fails.

### FR-012: Cache historical bars

**Priority:** MUST

The system must cache bars in PostgreSQL.

Acceptance criteria:

- Already stored bars are reused.
- New runs fetch only missing or stale bars where possible.
- The user can reprocess a run without refetching all data.

### FR-013: Fetch benchmark data

**Priority:** MUST

The system must fetch benchmark symbols required by the Pine replica logic.

Default benchmark symbols:

```text
SPY
QQQ
```

Acceptance criteria:

- SPY is available for market regime and default relative strength.
- QQQ is available for default sector benchmark logic if needed.
- Missing benchmark data is treated as a technical processing error.

### FR-014: Respect IB pacing

**Priority:** MUST

The system must avoid aggressive IB requests.

Acceptance criteria:

- Historical fetches are throttled.
- Failed pacing requests are logged.
- The system retries only when safe.
- The user can see fetch progress.

---

## 7. Technical Indicator Engine

### FR-015: Calculate moving averages

**Priority:** MUST

The system must calculate:

```text
EMA10
EMA20
SMA50
SMA150
SMA200
```

Acceptance criteria:

- Calculations use the selected adjusted price series.
- Results are available for the latest completed daily bar.

### FR-016: Calculate momentum and volatility indicators

**Priority:** MUST

The system must calculate:

```text
RSI14
ATR14
ATR%
ROC21
ROC63
ROC126
Volume average
Volume ratio
OBV
OBV SMA
OBV rising
DMI / ADX
```

Acceptance criteria:

- Indicator values are stored in technical debug JSON.
- Indicator values are visible on ticker detail page.
- Missing history produces a clear insufficient-data status.

### FR-017: Calculate pullback geometry

**Priority:** MUST

The system must calculate:

```text
prior high
recent low after prior high
pullback depth %
had pullback
not too deep
near EMA20 / SMA50 / SMA200 support
held near support
```

Acceptance criteria:

- Logic follows the Pine v3.2 approach.
- Pullback depth uses the low after the prior high.
- Pullback status is visible in debug details.

### FR-018: Calculate breakout geometry

**Priority:** MUST

The system must calculate:

```text
previous resistance
near resistance
fresh breakout
failed breakout
active breakout level
bars since breakout
```

Acceptance criteria:

- Fresh breakout requires price above previous resistance and volume confirmation.
- Failed breakout uses the stored breakout level, not a newly recalculated level.
- Failed breakout status is stored and visible.

### FR-019: Calculate volume quality

**Priority:** MUST

The system must calculate:

```text
green volume average
red volume average
green beats red
recent red volume
prior red volume
red volume declining
volume dry-up
liquidity warning
notional volume
```

Acceptance criteria:

- Volume calculations use `TRADES` data where available.
- Notional volume is calculated as volume times close.
- Liquidity warnings are stored.

### FR-020: Calculate candle and risk signals

**Priority:** MUST

The system must calculate:

```text
strong close
upper wick %
heavy red candle
gap up %
gap exhaustion
distribution count
extension above SMA50 %
blowoff top
distribution risk
```

Acceptance criteria:

- Risk signals are included in technical debug JSON.
- Danger states influence technical classification.

---

## 8. Pine Replica Engine

### FR-021: Port Pine defaults

**Priority:** MUST

The system must store Pine v3.2 defaults in a config file.

File:

```text
config/pine_defaults.yaml
```

Acceptance criteria:

- Defaults match the Pine v3.2 indicator defaults.
- The exact parameters used in each run are saved in `engine_parameters`.

### FR-022: Calculate market regime

**Priority:** MUST

The system must calculate:

```text
market score
market regime
market risk-off
market distribution count
market gate OK
```

Acceptance criteria:

- SPY is the default market symbol.
- Market score follows Pine v3.2 weighting.
- Market risk-off blocks buyable classifications when required.

### FR-023: Calculate relative strength

**Priority:** MUST

The system must calculate:

```text
benchmark relative strength score
sector relative strength score
combined relative strength score
relative strength status
relative strength gate OK
```

Acceptance criteria:

- SPY is default benchmark.
- QQQ is default sector symbol when sector benchmark logic is used.
- Combined RS follows Pine v3.2 behavior.

### FR-024: Calculate higher-timeframe trend

**Priority:** MUST

The system must calculate weekly higher-timeframe trend features.

Required outputs:

```text
HTF score
HTF status
HTF gate OK
```

Acceptance criteria:

- Weekly bars are derived consistently from daily bars or fetched directly.
- Confirmed HTF mode is supported.
- HTF score follows Pine v3.2 logic.

### FR-025: Calculate trend, momentum, setup, risk, and dual score

**Priority:** MUST

The system must calculate:

```text
local trend score
blended trend score
momentum score
setup score
risk score
dual score
```

Acceptance criteria:

- Scoring functions are ported from Pine v3.2.
- Scores are clamped between 0 and 10.
- Intermediate values are stored in debug JSON.

### FR-026: Classify setup

**Priority:** MUST

The system must assign one technical classification.

Allowed classifications:

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

Acceptance criteria:

- Classification follows the Pine v3.2 priority order.
- Danger classifications override buyable classifications.
- Every ticker with sufficient data receives exactly one classification.
- Tickers with insufficient data receive a clear insufficient-data status.

### FR-027: Calculate action bias

**Priority:** MUST

The system must generate a human-readable action bias.

Examples:

```text
Best buyable, R/R ok
Buyable, R/R ok
Breakout buy, R/R ok
Do not chase, wait mini-pullback
Overheated, avoid chasing
Avoid / exit risk
No clear trade
```

Acceptance criteria:

- Action bias follows classification and filter status.
- Action bias is shown in results table and ticker detail page.

### FR-028: Calculate stop, target, and reward/risk

**Priority:** MUST

The system must calculate:

```text
suggested stop
suggested target
entry risk %
reward/risk to target
```

Supported stop modes:

```text
ATR
SMA50
Structure
Structure + ATR
```

Supported target modes:

```text
R Multiple
Prior High
Measured Move
ATR Target
```

Acceptance criteria:

- Default stop mode is `Structure + ATR`.
- Default target mode is `R Multiple`.
- Invalid stop/target values fall back according to Pine v3.2 logic.

---

## 9. Combined Decision Engine

### FR-029: Merge fundamental and technical results

**Priority:** MUST

The system must merge outputs by ticker.

Acceptance criteria:

- Every uploaded ticker appears in final results.
- Missing technical data does not erase fundamental data.
- Missing fundamental data does not erase technical data.

### FR-030: Calculate final score

**Priority:** MUST

The system must calculate a final score for ranking.

Initial recommendation:

```text
Final score = weighted combination of fundamental score and technical dual score, with penalties for trap flags and danger technical classifications.
```

Acceptance criteria:

- Final score is deterministic.
- Weighting is stored in engine parameters.
- Final rank is based on final score.

### FR-031: Assign final decision label

**Priority:** MUST

Allowed final decision labels:

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

Acceptance criteria:

- Each ticker receives one final decision label.
- Danger technical classes result in `Avoid` or equivalent danger label.
- Overheated momentum results in `Wait for pullback` or equivalent caution label.

---

## 10. Web UI Requirements

### FR-032: Upload page

**Priority:** MUST

The app must have a simple upload page.

Required elements:

```text
CSV drag/drop area
Upload button
Current IB connection status
Recent runs link
Validation feedback area
```

### FR-033: Processing status page

**Priority:** SHOULD

The app should show progress while processing.

Progress stages:

```text
CSV validation
Saving raw rows
Resolving IB contracts
Fetching ADJUSTED_LAST bars
Fetching TRADES bars
Calculating fundamentals
Calculating technicals
Merging results
Saving results
```

### FR-034: Results table

**Priority:** MUST

The app must show a sortable results table.

Required columns:

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

### FR-035: Result filters

**Priority:** SHOULD

The results page should support filters.

Useful filters:

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

### FR-036: Ticker detail page

**Priority:** SHOULD

Each ticker should have a detail page.

Required sections:

```text
Raw CSV row
Fundamental score breakdown
Trap flags
Technical score breakdown
Indicator values
Classification reasoning
Stop/target calculation
IB data status
Historical run comparison
```

### FR-037: Run history page

**Priority:** MUST

The app must show previous processing runs.

Acceptance criteria:

- Runs are listed by upload time and filename.
- User can open results from previous runs.
- Failed runs are visible with error status.

---

## 11. Export Requirements

### FR-038: Export results to XLSX

**Priority:** MUST

The app must export a processed run to XLSX.

Workbook sheets:

```text
Final Results
Fundamental Scores
Technical Scores
Raw Uploaded CSV
Engine Parameters
Errors / Missing Data
```

### FR-039: Export results to CSV

**Priority:** SHOULD

The app should export final results to CSV.

Acceptance criteria:

- Export preserves final rank, scores, labels, and classifications.
- Export file is stored under local exports directory.

---

## 12. Database Requirements

### DR-001: PostgreSQL persistence

**Priority:** MUST

All processed data must be persisted in PostgreSQL.

Required tables:

```text
upload_runs
raw_company_rows
ib_contracts
price_bars
fundamental_scores
technical_scores
combined_results
engine_parameters
```

### DR-002: Raw data preservation

**Priority:** MUST

The original CSV values must be preserved as raw JSON.

### DR-003: Debug data preservation

**Priority:** MUST

Technical calculations must store enough debug data to explain classification decisions.

### DR-004: Idempotent bar cache

**Priority:** MUST

The `price_bars` table must prevent duplicate bars for the same ticker, date, timeframe, and data type.

Recommended unique key:

```text
ticker + bar_date + timeframe + what_to_show
```

---

## 13. Non-Functional Requirements

### NFR-001: Local-only operation

**Priority:** MUST

The app must run locally and must not require cloud deployment.

### NFR-002: No automatic trading

**Priority:** MUST

The system must not place, modify, cancel, or recommend automatic broker orders.

### NFR-003: Performance

**Priority:** SHOULD

For a CSV of around 70 tickers, the system should process a run in a reasonable time, limited mainly by IB historical data pacing.

Target after bars are cached:

```text
Under 60 seconds for ranking and technical calculation, excluding fresh IB fetch time.
```

### NFR-004: Reliability

**Priority:** MUST

One ticker failure must not fail the entire run.

### NFR-005: Explainability

**Priority:** MUST

Every final result must be explainable from stored score components, technical classifications, and flags.

### NFR-006: Maintainability

**Priority:** MUST

The Pine replica engine must be modular and testable.

### NFR-007: Testability

**Priority:** MUST

Core score functions must have unit tests.

Required test modules:

```text
test_fundamental_ranker.py
test_technical_indicators.py
test_pine_replica_engine.py
test_combined_decision_engine.py
```

### NFR-008: Security

**Priority:** SHOULD

Because the app is local-only, no full authentication is required for MVP. However:

- Database credentials must not be committed.
- `.env` must be used for local secrets.
- Uploaded files must be stored locally.
- The app should bind to localhost by default.

### NFR-009: Auditability

**Priority:** MUST

Each run must store:

```text
uploaded file name
upload timestamp
engine version
parameters used
row count
errors
warnings
final results
```

---

## 14. Error Handling Requirements

### ER-001: CSV errors

The app must clearly report:

```text
invalid file type
missing ticker column
empty file
unreadable CSV
encoding problem
```

### ER-002: IB connection errors

The app must clearly report:

```text
IB not running
wrong host or port
client ID conflict
market data permission problem
request timeout
pacing violation
```

### ER-003: Contract resolution errors

The app must show unresolved tickers and continue processing other tickers.

### ER-004: Insufficient data errors

If a ticker lacks enough bars for SMA200, 52-week position, or long ROC, technical classification must be marked as insufficient data.

### ER-005: Calculation errors

A calculation error for one ticker must be stored and must not crash the full run.

---

## 15. Acceptance Criteria for MVP

The MVP is accepted when:

1. User can run SwingLens locally.
2. User can upload a CSV with US stock tickers.
3. Raw CSV rows are stored in PostgreSQL.
4. Fundamental ranking runs for all rows with tickers.
5. IB connection through `ib_insync` works.
6. US stock contracts are resolved and stored.
7. Daily `ADJUSTED_LAST` and `TRADES` bars are fetched and cached.
8. Technical indicators are calculated for tickers with sufficient data.
9. Python Pine replica produces dual score and classification.
10. Fundamental and technical results are merged into final results.
11. Results are shown in a sortable table.
12. Results are saved in PostgreSQL.
13. Results can be exported to XLSX.
14. App never places orders.
15. Failed tickers/errors are visible and do not break the full run.

---

## 16. Explicitly Out of Scope

The following are not part of the MVP:

```text
Auto trading
Order placement
Portfolio optimization
Options strategies
Intraday live trading dashboard
Cloud deployment
User accounts
React frontend
News aggregation
External fundamental data APIs
Non-US stocks
Crypto
Forex
```

---

## 17. Implementation Notes

### Recommended first implementation slice

Build the smallest vertical slice first:

```text
Upload CSV
Store run
Display raw rows
Connect PostgreSQL
```

Then add:

```text
Fundamental ranker
IB fetcher
Technical engine
Combined result table
XLSX export
```

### Recommended development style

- Keep services small and testable.
- Port Pine functions one by one.
- Store debug values generously.
- Validate Python output against TradingView Pine before trusting signals.
- Prefer correctness over visual polish in MVP.

---

## 18. Final Requirement Statement

SwingLens must become a local decision cockpit where the daily CSV gives the candidate universe, IB provides market data, Python calculates fundamental and technical intelligence, PostgreSQL stores the memory, and the browser shows a clean ranked decision table.

The app must support research.  
The app must preserve evidence.  
The app must never trade automatically.
