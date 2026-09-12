# IB historical-data resilience

SwingLens treats IB Gateway API readiness and historical-data readiness as separate
capabilities. The ordinary readiness endpoint continues to use only the bounded API/current-time
check; it does not issue historical requests.

Immediately before a full pipeline would execute a non-empty IB historical fetch plan, it runs an
explicit, non-persisting capability preflight. The probe qualifies SPY on a short-lived read-only
client and sends exactly one `2 D`, `1 day` request for each required feed: `TRADES` and
`ADJUSTED_LAST`. Both requests use the configured `useRTH` value, `formatDate=1`,
`keepUpToDate=false`, and a current-compatible empty `endDateTime`. The per-request timeout is the
existing `ib_health_timeout_seconds`; no retry or backoff loop is used. The probe imports no
database, model, cache, pipeline, or job modules and cannot mutate `PriceBar` or fetch-run state.

The error classifier combines IB's numeric code with normalized message text. A 162 containing the
narrow phrase `No data of type EODChart is available` is
`HISTORICAL_DATA_UNAVAILABLE` and non-retryable. Recognized pacing and transient service messages
remain retryable. Unrecognized 162 messages are `HISTORICAL_UNKNOWN_162` and retain bounded retry.
Code 321 remains `PROVIDER_REJECTED` and non-retryable.

The fetch executor orders configured benchmarks first and evaluates a narrow circuit rule after
each completed request. The circuit opens only after every planned provider request for both SPY
and QQQ has completed, no such request succeeded, both symbols supplied independent failures, and
every failure has the same systemic `HISTORICAL_DATA_UNAVAILABLE` category. It does not open for
contract failures, zero-data results, arbitrary ticker counts, unknown 162 variants, or mixed
benchmark outcomes. Unstarted items are recorded as `SKIPPED` with zero attempts and reason
`IB_HISTORICAL_CIRCUIT_OPEN`; completed bars remain committed.
