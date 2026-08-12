# Phase 6 — IB Request Reuse Analysis and Rollout

Validated on the SwingLens laptop PostgreSQL database on 2026-08-12. The latest
completed US equity session at validation time was 2026-08-11.

## Why the representative pipeline issued 352 requests

IB fetch run 140, for upload run 94, contained 177 symbols: 175 requested
companies plus SPY and QQQ dependencies. One requested company had a failed
contract, leaving 176 resolved symbols. Each resolved symbol needed two
independent daily datasets:

- `ADJUSTED_LAST` for adjusted price calculations;
- `TRADES` for unadjusted trades/volume calculations.

That produced `176 × 2 = 352` incremental requests. SPY was a market benchmark.
QQQ was both a configured benchmark and the configured sector proxy. The
requests were session-rollover top-ups, not full backfills. Each series was one
completed market session behind its freshness threshold, so equivalent current
data did not yet exist in PostgreSQL. A repeated plan after those bars were
stored skipped the current series.

## Problem found

The planner previously treated every series with fewer than 252 bars as a full
backfill candidate, even when a successful three-year full backfill had already
proved that the instrument had limited listed history. The current database has
18 such adjusted/trades series across nine recently listed symbols.

Historical evidence showed 112 successful full-backfill requests for those 18
series. Only the first request per series was needed to establish available
history; 94 were repeated full backfills. MIAX alone received 78 successful
three-year requests across its two datasets.

## Implemented decision model

- A missing or insufficient series without matching full-backfill evidence gets
  one configured full backfill.
- Full-backfill evidence is valid only for the same dataset, configured duration,
  and bar interval.
- A limited-history series with matching evidence is never repeatedly backfilled.
  If stale, it gets an incremental top-up; if current, it is skipped while its
  limited-history coverage state remains explicit.
- Daily freshness remains strict: adjusted and trades data are evaluated
  independently against the latest completed US session, using the US exchange
  calendar and the 16:15 America/New_York daily-bar readiness boundary.
- Incremental requests cover every missing session plus five configured revision
  sessions. A one-session rollover therefore uses an eight-calendar-day request
  on the observed dates instead of a fixed ten-day request.
- Force refresh and force full refresh continue to bypass reuse.
- SPY/QQQ dependencies remain independently freshness checked. QQQ is marked as
  both benchmark and sector dependency.

Each fetch run now stores decision counts for requested, reused, incremental,
full-backfill, skipped-fresh, and forced-refresh decisions. Each item stores its
ticker role, dataset, coverage state, stored coverage use, full-backfill evidence,
freshness threshold and lag, missing range, request range, action, duration, and
bar interval. The plan, fetch-progress, direct-fetch, prewarm, and performance
baseline outputs expose this telemetry.

## Before versus after

| Measurement | Before | After |
| --- | ---: | ---: |
| Repeated full backfills found for current limited-history series | 94 | 0 after matching evidence exists |
| One-session incremental request duration | fixed 10 D | observed 8 D: missing session + 5-session revision window |
| Current run-96 plan | not explicitly classified | 2 incremental, 352 reused, 0 full backfills |
| Current run-95 plan | not explicitly classified | 592 incremental, 808 series reused, 0 full backfills |
| Full-backfill evidence lookup | historical-table scan | partial index; 0.609 ms measured execution |

The 592 run-95 requests are currently explainable and unavoidable in the
foreground fetch model: 296 requested symbols are one completed session behind,
and each needs adjusted and trades data. Moving those unavoidable calls out of
the foreground critical path is Phase 7 prewarm work; Phase 6 does not disguise
them as reusable.

## Validation

- PostgreSQL migration head: `0039_ib_fetch_decision_telemetry`.
- The evidence query used an index-only scan on
  `idx_ib_fetch_items_full_backfill_evidence`.
- A real PostgreSQL no-network execution persisted fetch run 142 with four
  current SPY/QQQ series, zero requests, four skips, and complete per-item
  decision metadata.
- The focused IB, calendar, settings, prewarm, fetch-progress, and performance
  suites passed. The final repository regression result was 1,393 passed and
  9 skipped, including the disposable PostgreSQL single-run certification and
  GUI/database parity checks.
- No historical jobs, bars, pipeline outputs, or prior fetch records were
  deleted or rewritten.

## Acceptance criteria

- Every IB request has an explainable reason: **pass**. Request and missing ranges,
  dataset, dependency role, freshness, stored coverage, and action are persisted.
- Unnecessary full backfills are eliminated: **pass** for matching full-backfill
  evidence; 94 historical repeats are prevented by the new rule.
- Repeated pipelines materially reduce requests when data is current: **pass**.
  Current run 96 reuses 352 series and requests only two genuinely stale series.
- No stale data is silently reused: **pass**. The latest completed exchange
  session remains the strict daily threshold for both datasets and dependencies.

## Remaining risks

- The bounded duration calculation is unit- and database-validated, but a live IB
  request was intentionally not forced solely for validation. Its returned bar
  span should be observed in the next natural session rollover.
- CLBK has no bars after 2026-07-17 and remains explicitly stale; Phase 6 does not
  mark it fresh or suppress its request without stronger no-data availability
  evidence.
- Phase 7 must operationalize prewarm only for the unavoidable session-rollover
  requests and retain normal foreground freshness verification.
- The Phase 6 API and external worker processes were reloaded. A Windows control
  signal could not be delivered gracefully to the previous worker; its active
  provider-ingest job was left leased and unchanged for normal stale-lease
  recovery. The replacement worker registered and heartbeated successfully. It
  recovered the expired row to `QUEUED` at 16:20:57 with a `STALE_RECOVERED`
  lease event and no cancellation or destructive DB update.
