# Live IB Paper Validation

This procedure is manual and optional for normal automated regression. It verifies the installed IB
Gateway/TWS paper environment without placing, modifying, routing, or cancelling any order.

## Preconditions

- Use an IB paper account and paper port (commonly `4002`), never live trading credentials.
- Confirm API access is enabled and the session is configured read-only.
- Use a disposable SwingLens run with non-sensitive tickers such as `MSFT`, `SPY`, and one invalid
  symbol.
- Keep `IB_FORCE_CONSERVATIVE_MODE=true`; do not change pacing limits to bypass Gateway controls.
- Record Gateway/TWS version, market-data entitlements, timezone, test time, app commit, and
  redacted configuration.

## Procedure

1. Start PostgreSQL, migrate to head, and launch SwingLens on `127.0.0.1`.
2. Open `/ib` and `GET /ib/status`; record disconnected state before connecting.
3. Invoke `POST /ib/test`. Expect a successful read-only connection, client ID, server version,
   and no credential, token, SQL, or local-path disclosure.
4. Resolve `MSFT` and `SPY`. Expect one unambiguous US stock/ETF contract each.
5. Resolve an intentionally invalid symbol. Expect a stable, redacted failure without a persisted
   successful contract.
6. Upload a disposable CSV containing two valid tickers and request daily bars with benchmarks.
7. Verify cache coverage, chronological dates, unique ticker/date/what-to-show keys, and explicit
   stale/insufficient warnings where entitlements are missing.
8. Repeat the request. Expect cache reuse or incremental top-up, not duplicate bars.
9. Disconnect Gateway during a multi-ticker request. Expect successful ticker results to remain,
   failed items to be explicit/exportable, and retry-failed to target only failures.
10. Cancel a running request at a safe boundary, reconnect, and resume. Expect consistent terminal
    status and no duplicate evidence.
11. Review app logs and exported failures for secret, authorization-header, SQL, provider-payload,
    and local-path leakage.
12. Inspect OpenAPI, rendered controls, `app/`, and captured IB method calls. Confirm zero order
    routes, buttons, forms, `placeOrder`, `cancelOrder`, `whatIfOrder`, or global-cancel calls.

## Pass Criteria

- All connections are read-only and use the paper endpoint.
- Contract, cache, pacing, retry, partial-failure, cancel, and resume behavior matches the
  documented contracts.
- No successful data is lost when another ticker fails.
- No secret is exposed and no broker-order API is invoked.

If any order-related behavior is observed, stop immediately, preserve redacted evidence, and record
an S0 defect. Do not retry against a live account.

## Execution Record — 2026-08-06

Status: **PARTIAL** on commit `298cd47`.

- Environment: IB Gateway `10.48`, API server version `176`, paper port `127.0.0.1:4002`, Europe/Zurich,
  conservative pacing enabled, read-only client ID `21`. Port `4001` was closed.
- Direct guarded API smoke: connected with `readonly=True`; resolved `MSFT` (`conId 272093`) and
  `SPY` (`conId 756733`); rejected `SWINGLENSINVALIDXYZ` with IB error 200; returned nine unique,
  chronological completed daily bars for each valid symbol from 2026-07-24 through 2026-08-05.
- The live client was instrumented so `placeOrder`, `cancelOrder`, `whatIfOrder`,
  `reqOpenOrders`, `reqAllOpenOrders`, and `reqGlobalCancel` failed on access. Invocation count: zero.
- Route smoke on a migrated disposable database: `/health`, `/ready`, `/ib/status`, `/ib/test`,
  contract resolution, historical fetch, repeat fetch, mixed failure, and OpenAPI inspection passed.
  The first `MSFT`/`SPY` request inserted 1,502 unique daily bars; the repeat planned and executed
  zero historical requests and inserted zero duplicates.
- Forced mixed retest after DEF-003: `MSFT` plus the invalid ticker returned `PARTIAL`, planned two
  potential historical calls, executed one, preserved and inserted 752 `MSFT` bars, and reported
  the invalid contract separately. The disposable database was dropped after verification.
- Captured app logs had zero matches for database credentials, `DATABASE_URL`, authorization or
  bearer headers, and local user paths. OpenAPI exposed no broker-write path.

Not yet executed: step 6 through an uploaded run with benchmarks, step 9's intentional live Gateway
disconnect/retry-failed drill, and step 10's live cancellation/reconnect/resume drill. Deterministic
fake and durable-job automation covers these behaviors, but it is not a substitute for the remaining
environmental procedure.
