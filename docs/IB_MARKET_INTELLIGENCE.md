# IBKR Market Intelligence Extension

SwingLens adds seven feature-flagged, read-only IBKR intelligence modules. The extension reuses the existing IB Gateway connection, contract resolution, durable worker, and conservative historical pacing without changing the OHLCV `PriceBar` pipeline. Flex reporting uses a separate HTTPS client and does not require TWS or IB Gateway.

## Architecture and safety boundary

TWS-backed modules connect with `readonly=True` and use only historical data, market-data snapshots, scanner subscriptions, and histogram data. The package contains no order placement, modification, cancellation, open-order, or execution-request calls. Flex is delayed reporting/journaling only. It cannot transmit orders.

Non-price historical values are stored in `ib_historical_metric_bars`, never in `price_bars`. A value such as `0.42` implied volatility therefore cannot be interpreted as a 42-cent security price. Raw evidence, corrections, availability states, request provenance, calculation versions, configuration hashes, and feature inputs remain auditable.

Every external operation belongs to an `ib_intelligence_run`; individual broker requests are recorded in `ib_intelligence_request_items`. Durable background jobs use deterministic request keys, priorities, checkpoints, coalescing, cancellation, retry, and lease fencing from SwingLens's existing worker.

## Modules and source semantics

### IB-1 Liquidity and Spread

Daily `BID_ASK` bars are parsed as IBKR time-average bid (open) and time-average ask (close). SwingLens calculates midpoint, absolute/percentage spread, robust 5- and 20-session medians, spread stability, percentile context, and an explainable liquidity grade. Invalid or inverted bid/ask observations are retained with warnings but excluded from calculations. Liquidity is an execution-quality overlay; it does not alter technical scoring by default.

### IB-2 Short Pressure

Historical `FEE_RATE` values retain starting/highest/lowest/last borrow-fee semantics. Bounded generic tick 236 snapshots collect shortability and available shares when returned. The 0-10 Short Pressure Score explicitly combines fee level, fee acceleration, and availability, reweighting available components instead of zero-filling missing data.

Borrow fee and shortable shares describe IBKR securities-lending conditions. They are not official exchange short interest and must never be labeled as short-interest percentage or squeeze probability.

### IB-3 Volatility

`HISTORICAL_VOLATILITY` and `OPTION_IMPLIED_VOLATILITY` are typed volatility series. Features include current HV/IV, IV/HV, IV premium, IV change, percentile/rank, and expansion/contraction state. Zero or missing HV leaves IV/HV null. A point-in-time, bounded options-event-premium component (maximum 1.5) may enrich CERI Event Risk when both feature flags are enabled. CERI never reads future intelligence evidence.

### IB-4 Options Activity

Bounded generic ticks 100, 101, and 105 collect call/put volume, call/put open interest, and average option volume. Ratios require a positive observed denominator. Unavailable fields are distinct from observed zero. Labels are `CALL_HEAVY`, `PUT_HEAVY`, `BALANCED`, `ABNORMAL_OPTION_ACTIVITY`, or `INSUFFICIENT`; they are confirmation context, not directional certainty.

### IB-5 Market Discovery

Scanner parameter XML is cached and hashed. Versioned YAML presets are validated against the current IBKR parameter response before execution. Each scan is capped at 50 results and scanner subscriptions are cancelled. Candidate merging is deterministic by conId/ticker while preserving every scanner and rank reason. Candidate CSV exports use universe source `IBKR_SCANNER` so they can enter normal research without overwriting uploaded evidence. Scanner rank is discovery evidence, never an automatic recommendation.

### IB-6 Price Acceptance

Histogram requests store every price/activity-count bin, the requested period, RTH setting, observation time, and reference price. Derived features include a POC-like maximum-activity level, a configurable dominant zone, low-activity levels, concentration, nearest activity support/resistance, and relative-price context.

These are IBKR histogram-derived price-level activity measures. They are not claimed to be an exchange-standard volume profile or official exchange volume POC.

### IB-7 Flex Trade Journal

The Flex client implements version-3 `SendRequest` followed by bounded `GetStatement` polling. Send requests are limited to one per second and ten per minute. TEXT/CSV-like and XML executions are normalized, account identifiers are locally hashed/masked, tokens are redacted, identical reports are no-ops, and corrected execution IDs supersede prior evidence.

FIFO-position episodes support partial fills, scale-ins, partial exits, complete exits, crossing through zero, reopened/reversed positions, commissions, and fees. Research matching selects only the latest completed same-ticker SwingLens evidence at or before entry. Ties are `AMBIGUOUS`; absent evidence is `UNMATCHED`. Derived P&L is reported separately from broker-reported realized P&L.

## Availability states and entitlements

No module converts missing data to zero. Evidence uses `AVAILABLE`, `UNAVAILABLE`, `SUBSCRIPTION_REQUIRED`, `NOT_SUPPORTED`, `STALE`, `FAILED`, or `UNKNOWN`.

| Module | Typical live requirement |
| --- | --- |
| Liquidity | TWS/Gateway API access and applicable bid/ask market-data entitlement |
| Short Pressure | Instrument/account support for `FEE_RATE`; generic tick 236/shortable fields where available |
| Volatility | TWS/Gateway API access; options market-data subscriptions for implied volatility |
| Options Activity | Applicable underlying/options market-data subscriptions and permissions |
| Discovery | TWS/Gateway scanner API; scan/filter availability varies by account and current parameter metadata |
| Price Acceptance | TWS/Gateway histogram support for the resolved instrument |
| Flex Journal | Flex Web Service token and correctly configured Trade Confirmation/Activity query |

Paid package names and exchange requirements vary by instrument, exchange, account, and IBKR policy. The Operations page shows the observed capability result rather than assuming entitlement.

## Configuration and activation

All modules default off. Enable the parent flag and the selected environment flag, then set `engine.enabled` and the module's `enabled` value in `config/ib_market_intelligence.yaml`. Restart the application because SwingLens caches settings.

Environment flags and operational limits are documented in `.env.example`. Flex secrets are:

- `IB_FLEX_TOKEN`
- `IB_FLEX_TRADE_QUERY_ID`
- `IB_FLEX_ACTIVITY_QUERY_ID`

Never place real values in YAML, code, job payloads, logs, exports, or source control.

## Operations and API

Read surfaces:

- `/ib-intelligence`
- `/ib-intelligence/scanner`
- `/ib-intelligence/trade-journal`
- `/ib-intelligence/operations`
- `/api/ib-intelligence/ticker/{ticker}`
- `/api/ib-intelligence/histogram/{ticker}`
- `/api/ib-intelligence/scanner/runs`
- `/api/ib-intelligence/trade-journal`

Local-admin actions queue durable jobs and require the local CSRF header:

- `POST /api/ib-intelligence/refresh`
- `POST /api/ib-intelligence/live-snapshot`
- `POST /api/ib-intelligence/scanner/run`
- `POST /api/ib-intelligence/histogram/fetch`
- `POST /api/ib-intelligence/flex/import`
- `POST /api/ib-intelligence/rebuild-features`

Priorities preserve existing OHLCV work: shortlist live snapshots and liquidity precede normal intelligence, histogram, Flex, scanners, and optional rebuild/backfill work.

## Validation and troubleshooting

Run fixture/golden tests:

```powershell
.\.venv\Scripts\pytest.exe -q tests\ib_market_intelligence
.\.venv\Scripts\pytest.exe -q tests\integration\test_ib_market_intelligence_persistence.py
```

Run existing IBKR/OHLCV regressions:

```powershell
.\.venv\Scripts\pytest.exe -q tests\test_ib_services.py tests\test_ib_rate_limiter.py tests\test_ib_fetch_plan_service.py tests\test_ib_fetch_executor.py tests\test_ib_fetch_job_service.py tests\test_ib_fetch_summary_service.py
```

Live validation is intentionally separate and never required by normal CI. Use a tiny ticker set, enable only the module being tested, and review Operations capability statuses. Flex validation additionally requires the operator's secret token/query configuration. A subscription result of `SUBSCRIPTION_REQUIRED` is a valid capability diagnostic, not a fixture-test failure.

Common failures:

- Gateway unavailable: TWS jobs retry/fail independently; Flex remains usable.
- Missing subscription: evidence is `SUBSCRIPTION_REQUIRED`; no score penalty is applied.
- Scanner code missing: refresh scanner parameters or correct the preset.
- Histogram empty: raw request evidence remains, derived state is `INSUFFICIENT`.
- Flex pending: bounded polling continues; persistent delay becomes a retryable job failure.
- Flex schema changed: import fails with the missing/invalid row diagnostic and persists no partial report.
- Ambiguous research match: the trade remains imported and is marked for review; SwingLens does not guess.
