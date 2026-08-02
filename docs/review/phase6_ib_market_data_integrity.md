# SwingLens Phase 6 Interactive Brokers Integration and Market-Data Integrity

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 1 traceability: `docs/review/phase1_requirements_traceability.md`
Phase 3 configuration: `docs/review/phase3_configuration_feature_flags.md`
Phase 4 database: `docs/review/phase4_database_migrations_transactions.md`
Phase 14 jobs: `docs/review/phase14_background_jobs_concurrency_recovery.md`
Phase 15 security: `docs/review/phase15_web_security_local_admin.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 6 reviews Interactive Brokers read-only safety, contract resolution, historical-bar fetching,
market-data completeness, corporate-action semantics, stale-data detection, benchmark alignment,
revision lineage, and retry/idempotency behavior.

Overall status: not exit-ready. Read-only connection posture is strong, and fetch progress/retry
coverage is useful. The main remaining risks are wrong-instrument resolution, inconsistent
adjusted-versus-trades price policy, stale configuration that is displayed but unused, and
non-append-only bar revisions.

## Evidence Log

Inspected surfaces:

- IB connection and routes: `app/services/ib_connection.py`, `app/routers/ib_routes.py`.
- Contract resolution: `app/services/ib_contract_resolver.py`, `app/models/tables.py`.
- Historical fetching and caching: `app/services/ib_data_fetcher.py`,
  `app/services/bar_cache_service.py`, `app/services/ib_fetch_executor.py`,
  `app/services/ib_fetch_job_service.py`, `app/services/ib_fetch_plan_service.py`.
- Rate limiting and retry: `app/services/ib_rate_limiter.py`.
- Coverage and market calendar: `app/services/ohlcv_coverage_service.py`,
  `app/services/us_market_calendar.py`.
- Price selection and downstream consumers: `app/services/price_bar_repository.py`,
  `app/services/technical_score_service.py`, `app/services/market_regime_command_center.py`,
  `app/services/sector_etf_rotation_service.py`,
  `app/services/winner_probability/outcome_service.py`,
  `app/services/setup_lifecycle/source_loader.py`.
- Requirements and architecture docs: `README.md`, `docs/vision.md`, `docs/srs.md`, `docs/sdd.md`.

Command evidence:

| Command | Result | Notes |
|---|---:|---|
| `rg -n "placeOrder|cancelOrder|reqOpenOrders|reqAllOpenOrders|Order\\(|whatIf|transmit|ib\\.place|ib\\.cancel|readonly\\s*=|\\.connect\\(" app tests -g "*.py"` | Reviewed | App IB connects use `readonly=True`; only order strings found in a setup-lifecycle acceptance fixture. |
| Preferred OHLCV probe with divergent adjusted/trades closes | Reproduced | `load_preferred_ohlcv_frames` returned `TRADES` closes `[10.0, 11.0]` as price when both series existed. |
| Ambiguous contract probe with two qualified contracts | Reproduced | Resolver returned `RESOLVED`, `conid=1`, `primary_exchange=NYSE` without surfacing the second candidate. |
| Stale-days probe with `stale_after_days=3` and `30` | Reproduced | Both returned `stale`; the parameter is ignored by coverage/fetch-plan helpers. |
| `uv run pytest tests/test_ib_services.py tests/test_ib_rate_limiter.py tests/test_ib_fetch_plan_service.py tests/test_ib_fetch_executor.py tests/test_ib_fetch_job_service.py tests/test_ohlcv_coverage_service.py tests/test_us_market_calendar.py tests/test_technical_confidence.py tests/test_technical_indicators.py -q` | Passed | `53 passed, 1 warning in 11.02s`. |
| `uv run pytest tests/test_market_regime_command_center.py tests/test_market_participation_service.py tests/test_sector_etf_rotation_service.py tests/setup_lifecycle/test_source_loader.py tests/winner_probability/test_outcome_service.py -q` | Passed | `21 passed in 5.53s`. |

## Current IB Model

- IB status, resolve, fetch-plan, and executor paths connect to IB Gateway/TWS with `readonly=True`.
- `/ib/status` reports `order_endpoints: false`; there are no app-code references to common IB order
  methods.
- Contract resolution requests `Stock(symbol, exchange="SMART", currency="USD")`, calls
  `qualifyContracts`, and caches one row per ticker in `ib_contracts`.
- Fetch plans include uploaded tickers plus configured benchmarks, request both `ADJUSTED_LAST` and
  `TRADES` by default, and classify actions as backfill, top-up, refresh, skip, failed, or unresolved.
- Fetch execution is serialized by a one-worker thread pool for the async job path and commits after
  each plan item.
- `price_bars` are unique by `(ticker, bar_date, timeframe, what_to_show)` and store
  `first_seen_at`, `last_seen_at`, `revised_at`, `revision_count`, and `data_hash`.
- Coverage requires some adjusted-or-trades price history, TRADES volume, sufficient count, and a
  latest bar at least as current as the latest completed US trading day.

Positive coverage:

- IB connect calls reviewed use read-only mode.
- Fetch executor retries failed historical requests with configurable backoff.
- Pacing controls enforce a minimum request gap and a rolling requests-per-minute window.
- Fetch progress exposes item/run counts, failures, cancellation state, and resume flow for failed
  items.
- Duplicate price bars are upserted rather than inserted again.
- Missing benchmark/market context lowers technical confidence instead of silently producing a high
  confidence row.
- Calendar tests cover normal close, before-close behavior, weekends, and observed holidays.

## Findings Register

### PH6-001

Title: Ambiguous IB contract resolution silently picks the first qualified contract

Severity: S1 High

Confidence: Confirmed

Evidence:

- `app/services/ib_contract_resolver.py:62-68` qualifies a SMART USD stock contract and stores
  `qualified[0]`.
- `app/models/tables.py:136` makes `ib_contracts.ticker` unique, so one ticker can only cache one
  resolved identity.
- Probe with two qualified contracts returned `RESOLVED` for the first candidate and did not expose
  the second.

Impact: A ticker with multiple listings, share classes, ADR/native variants, primary exchanges, or
ETF/common-stock ambiguity can be mapped to the wrong instrument. Downstream bars, rankings,
technical classifications, outcomes, and setup lifecycle events would then be confidently computed
against the wrong security.

Recommendation:

- Treat more than one qualified contract as `AMBIGUOUS` unless a deterministic policy selects a
  candidate by `secType`, `currency`, `primaryExchange`, `localSymbol`, `tradingClass`, and allowed
  exchange list.
- Store all candidates or a compact candidate JSON for user review.
- Add resolver tests for no match, one exact match, multiple matches, non-USD, ADR/native, ETF,
  share-class tickers, and stale cached contract refresh.

### PH6-002

Title: Price-series selection contradicts adjusted-data requirements

Severity: S1 High

Confidence: Confirmed

Evidence:

- `docs/sdd.md:800` says price calculations should prefer `ADJUSTED_LAST` and fall back to `TRADES`.
- `docs/srs.md:469` says calculations use the selected adjusted price series.
- `docs/vision.md:223-227` says `ADJUSTED_LAST` is primary when available and the output should
  record which series was used.
- `app/services/price_bar_repository.py:47-50` loads both series but sets `price = trades if not
  trades.empty else adjusted`.
- `tests/test_technical_indicators.py:59` locks in TRADES prices for TradingView parity.

Impact: Split and dividend adjustments can be bypassed whenever raw TRADES bars exist. This can
distort moving averages, returns, breakout/risk levels, market regime inputs, sector ETF rotation,
winner outcomes, and chart displays. Because the behavior is test-backed, future changes may keep
the inconsistency alive.

Recommendation:

- Decide and document the canonical policy. If the requirements stand, update the repository to use
  adjusted OHLC for price and TRADES for volume when both exist.
- Persist the selected `price_what_to_show` and `volume_what_to_show` in technical/output debug
  payloads.
- Add a split fixture where adjusted and trades prices diverge and assert technicals use the chosen
  canonical series.

### PH6-003

Title: Bar revision audit is in-place, not append-only

Severity: S1 High

Confidence: Confirmed

Evidence:

- `app/settings.py:48` exposes `ib_revision_audit_enabled`.
- `app/models/tables.py:181-186` stores only `revision_count` and current `data_hash` on
  `price_bars`.
- `app/services/bar_cache_service.py:150-160` overwrites OHLCV fields in place and increments
  `revision_count` when hashes differ.
- No `PriceBarRevision`, `price_bar_revisions`, or equivalent append-only table was found.

Impact: SwingLens can see that a bar changed, but it cannot reconstruct the previous bar value from
the database after the overwrite. That weakens auditability for historical classifications,
winner-probability outcomes, and setup lifecycle snapshots if market data is revised.

Recommendation:

- Add an append-only revision table keyed by price-bar natural key plus revision number, with old and
  new hash/value payloads, fetch-run/item IDs, and observed timestamps.
- Make `ib_revision_audit_enabled` enforceable: either write revision rows when true or remove the
  setting until implemented.
- Add tests proving a revised bar preserves the prior OHLCV values and that repeated identical
  fetches only update `last_seen_at`.

### PH6-004

Title: Displayed stale-after-days setting is ignored by freshness decisions

Severity: S2 Medium

Confidence: Confirmed

Evidence:

- `app/routers/ib_routes.py:69` and the UI expose `daily_bar_stale_after_days`.
- `app/services/ohlcv_coverage_service.py:143-158`,
  `app/services/ib_fetch_plan_service.py:315-317`, and
  `app/services/ib_fetch_executor.py:257-259` accept `stale_after_days` but discard it with
  `_ = stale_after_days`.
- Probe with `stale_after_days=3` and `30` returned the same stale result for the same latest bar.

Impact: Operators can tune a stale threshold that has no effect. The app currently uses a stricter
latest-completed-session rule, which may be desirable for daily trading, but the advertised
configuration creates false control.

Recommendation:

- Rename the setting to match the implemented latest-session policy, or implement a true grace
  threshold with explicit behavior for weekends, holidays, half-days, and current-day incomplete
  bars.
- Add tests that prove changing the setting changes the plan, or remove the setting from status/UI.

### PH6-005

Title: Historical bars are accepted without session/order/shape validation

Severity: S2 Medium

Confidence: Confirmed

Evidence:

- `app/services/ib_data_fetcher.py:34-42` requests historical bars with `keepUpToDate=False`,
  `useRTH=settings.ib_use_rth`, and the requested `whatToShow`.
- `app/services/ib_data_fetcher.py:45-57` converts each IB bar into a cache DTO in returned order.
- `app/services/ib_data_fetcher.py:73-81` converts invalid or negative values to `None`; no check
  rejects missing OHLC, `high < low`, duplicate dates, gaps, stale sessions, partial current-day
  rows, or zero-volume anomalies.
- Coverage counts rows by series in `app/services/ohlcv_coverage_service.py:214-232`, not valid
  trading sessions with complete OHLC fields.

Impact: Partial or malformed IB responses can enter the cache and count toward readiness. Technical
indicator code may later mark some cases as insufficient or error, but the fetch/coverage layer does
not make data-quality defects visible at the ingestion boundary.

Recommendation:

- Add bar-normalization validation that sorts by date, deduplicates deterministically, rejects or
  quarantines incomplete OHLC rows, and records gap/zero-volume/stale warnings by ticker and series.
- Compare fetched dates against a market calendar for expected sessions, with explicit treatment of
  holidays, early closes, DST boundary weeks, and current-day bars.
- Include validity counts in coverage and fetch summaries.

### PH6-006

Title: IB pacing errors are retried generically without error-class policy

Severity: S2 Medium

Confidence: Likely

Evidence:

- `app/services/ib_fetch_executor.py:204-229` catches all exceptions, backs off, and retries until
  `ib_max_retries`.
- `app/services/ib_rate_limiter.py:29-33` applies linear backoff based only on attempt count; the
  `error` argument and `conservative_mode` flag are not inspected.
- Existing tests cover retry/backoff mechanics, but not specific IB pacing violation codes, partial
  historical-data responses, entitlement failures, or disconnect/reconnect classes.

Impact: Entitlement/contract errors may be retried unnecessarily, while pacing violations may need
longer cool-down behavior and clearer operator messaging. The current implementation is safe in the
sense that it backs off, but it is not IB-aware.

Recommendation:

- Classify known IB historical-data errors into retryable pacing/timeouts, non-retryable entitlement
  or invalid-contract errors, and reconnect-required transport failures.
- Make `conservative_mode` materially change limits/backoff.
- Add tests using representative IB exception messages/codes.

## Failure-Mode Matrix

| Failure mode | Current behavior | Visibility | Risk | Required hardening |
|---|---|---|---|---|
| IB Gateway unavailable | Connection/fetch run fails and stores message | UI/status/fetch run | Medium | Redact local details consistently and add reconnect runbook steps |
| Order-capable path introduced | No current app path found | Static scan only | High | Add CI/static test forbidding order APIs outside fixture allowlist |
| Duplicate client ID/session conflict | Connect exception bubbles into status/run failure | Error message | Medium | Add explicit client-ID collision diagnosis |
| Contract no match | Contract row marked `FAILED` | Coverage and plan report failure | Medium | Keep and test |
| Contract multiple matches | First candidate stored as `RESOLVED` | Not visible | High | Mark `AMBIGUOUS` and require selection policy |
| Pacing violation | Generic retry/backoff | Fetch item failure after max retries | Medium | IB-aware classification and longer pacing cool-down |
| Partial/duplicate/gapped bars | Cached as returned/upserted by natural key | Weak | High | Validate sessions and emit data-quality warnings |
| Current-day incomplete bar | Latest completed-session rule usually avoids requiring it | Partial | Medium | Explicit current-day exclusion/assertions |
| Holiday/weekend | Basic observed-holiday calendar | Tested | Low | Add market-calendar package or half-day/DST coverage |
| Half-day/early close | Fixed 16:15 readiness | Not modeled | Low-Medium | Add early-close calendar if same-day half-day fetch matters |
| Revised historical bar | Current row overwritten, count/hash updated | Partial | High | Append-only revision table |
| Repeated identical fetch | Updates `last_seen_at`, no duplicate row | Tested | Low | Add end-to-end idempotent fetch-run test |
| Missing benchmark data | Low/error confidence paths exist | Tested | Medium | Keep enforcing in combined/regime gates |
| Adjusted/trades divergence | TRADES selected for price | Not surfaced | High | Align policy and record selected series |

## Contract-Resolution Ambiguity Backlog

| Case | Example risk | Expected behavior |
|---|---|---|
| Multiple primary exchanges | Same symbol across NYSE/ARCA/NASDAQ venues | Quarantine as ambiguous or choose allowed primary exchange |
| Share classes | `BRK.B`, `GOOG/GOOGL`, class-specific local symbols | Normalize supported input and verify returned local symbol |
| ADR versus native | Same issuer ticker-like symbols across currencies/exchanges | Require USD US listing policy and record sec/currency/exchange |
| ETF versus common stock | Similar symbols or trading classes | Validate `secType`, trading class, and instrument category |
| Delisted/changed symbols | Cached conId no longer valid | Refresh policy marks stale/failed rather than using old identity |
| Non-USD or non-US result | IB returns another currency/exchange | Reject for MVP US-stock universe |
| Entitlement-limited metadata | Partial/no qualification | Store entitlement-specific failure reason |

## Market-Calendar And Stale-Data Test Additions

- Latest completed bar at normal close, before close, weekend, observed holiday: already covered.
- Add half-day sessions, especially the day after Thanksgiving and July 3 early close cases.
- Add DST transition weeks in March and November with timezone-aware `now`.
- Add current-day incomplete bars from IB and assert they do not mark a ticker ready before the
  configured readiness time.
- Add `ib_daily_bar_stale_after_days` tests after deciding whether it is a grace threshold or should
  be removed.
- Add gapped-session fixtures: one missing weekday, one duplicate date, one zero-volume ETF/index day,
  one stale benchmark, and one stale stock with current benchmarks.
- Add split-adjusted fixture where `ADJUSTED_LAST` and `TRADES` diverge sharply.

## Revision Lineage Report

Current lineage:

- `price_bars` records the current value and metadata: `first_seen_at`, `last_seen_at`,
  `revised_at`, `revision_count`, and `data_hash`.
- `cache_bars` computes a hash from OHLCV/source/series metadata and updates `last_seen_at` for
  unchanged rows.
- When a bar changes, `cache_bars` overwrites the current row and increments `revision_count`.
- Winner-probability outcome rows and setup lifecycle snapshots carry source hash fields, which helps
  detect that upstream data changed after derived artifacts were created.

Gap:

- There is no append-only history of prior bar values. A later audit can detect a mismatch but cannot
  fully reconstruct the earlier IB value from the database alone.

Target lineage:

- Each fetch item should identify the exact rows inserted, unchanged, revised, rejected, or
  quarantined.
- Each revised bar should have an immutable revision record with prior values, new values, prior/new
  hashes, fetch run/item ID, and first/last seen timestamps.
- Derived technical, market-regime, setup-lifecycle, and winner-probability artifacts should record
  the price-series policy and source-bar hash/lineage used at calculation time.

## IB Outage And Pacing Runbook

1. Check `/ib/status`; confirm host, port, client ID, `order_endpoints=false`, `readonly=true` in UI
   context, and Gateway/TWS paper/live environment.
2. If disconnected, confirm IB Gateway/TWS is running, API socket access is enabled, the configured
   port matches paper/live, and no other client is using the same client ID.
3. If a fetch run failed, open the fetch progress page or export fetch history; inspect failed items,
   error messages, attempt counts, and whether failures are concentrated in one ticker or every
   ticker.
4. For pacing errors, stop new fetches, wait at least the configured backoff window, resume only the
   failed items, and lower `ib_requests_per_minute` / raise `ib_min_seconds_between_requests` before
   retrying a large universe.
5. For contract failures, resolve the ticker manually, check ambiguity/permissions, and do not treat
   technical results as complete until coverage status is ready.
6. For stale or partial bars, run a top-up/refresh, then refresh technicals and combined results.
7. If revised-bar counts are non-zero, rerun affected technicals/outcomes and retain the fetch run
   export with the source-data hashes until append-only revision history exists.

## Exit Criteria Status

| Criterion | Status | Notes |
|---|---|---|
| Read-only enforcement tested | Partial | Implementation uses `readonly=True`; add explicit fake-IB assertions and static order API denylist test. |
| No order-capable path exists | Pass with caveat | No app order calls found; acceptance fixture contains allowed strings only. |
| Event loop/thread/client cleanup reviewed | Partial | Event loop helper and clean disconnect exist; no client-ID collision/reconnect tests. |
| Rate limiting/retry/backoff tested | Partial | Mechanics tested; IB-specific pacing/error classes missing. |
| Contract ambiguity visible | Fail | Multiple qualified contracts are silently resolved to first candidate. |
| Adjusted/trades semantics verified | Fail | Code/test prefer TRADES price, while docs require adjusted-price preference. |
| Market-data gaps cannot silently produce confidence | Partial | Technical confidence handles missing context; fetch/coverage can still count malformed rows. |
| Revisions auditable | Partial/Fail | Hash and counters exist; prior bar values are overwritten. |
| Repeated fetches idempotent | Partial | Cache unit tests cover unchanged rows; add end-to-end interrupted/repeated fetch tests. |

Phase 6 should remain open until PH6-001, PH6-002, PH6-003, and PH6-005 are remediated or accepted
with an explicit product-risk waiver.
