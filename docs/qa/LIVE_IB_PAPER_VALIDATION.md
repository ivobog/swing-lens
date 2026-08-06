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
