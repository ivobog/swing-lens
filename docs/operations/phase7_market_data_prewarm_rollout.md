# Phase 7 — Market-Data Prewarm Operational Rollout

Phase 7 moves unavoidable Interactive Brokers latency outside a user-triggered
pipeline. It does not weaken the Phase 6 freshness model: every foreground
pipeline still builds its normal fetch plan and requests anything missing or
stale.

## CLI and scheduling

Queue the latest five completed upload universes with:

```powershell
python -m app.prewarm --source recent-runs --recent-run-count 5
```

Other bounded sources are supported:

```powershell
python -m app.prewarm --source watchlist
python -m app.prewarm --source explicit --tickers AAPL,MSFT,NVDA
```

The command queues durable work and returns a JSON job identity. It does not
keep a terminal attached while IB requests run. A laptop scheduler should invoke
the recent-runs command after the US daily bar readiness boundary, currently
16:15 America/New_York. SwingLens derives the latest completed exchange session,
so weekends, observed holidays, and pre-close runs use the prior session.

## Idempotency contract

The request key contains:

- an order-independent universe fingerprint;
- the effective completed market session;
- daily bar size and adjusted/trades datasets;
- the explicit prewarm configuration version and a configuration fingerprint.

Only active jobs coalesce. A terminal job is not trusted as cache authority:
re-running the command rebuilds the Phase 6 coverage plan, allowing missing or
invalidated local data to be repaired safely.

## Broker-lane and preemption behavior

`MARKET_DATA_PREWARM` remains in the simple `BROKER` class. Its priority is 200,
below the foreground pipeline priority, and interactive claims remain preferred.

When a new foreground pipeline is created, SwingLens marks every running
prewarm job for cooperative cancellation and records a 45-second deadline. The
active IB request has a configured 30-second request timeout; pacing and retry
backoff poll cancellation every 250 ms. At the next safe item boundary:

1. already committed bars remain in PostgreSQL;
2. the partial fetch run is marked cancelled;
3. the prewarm job clears the foreground cancellation flag;
4. the same durable job is deferred for 30 seconds;
5. it continues yielding while a foreground pipeline is queued or running;
6. after foreground completion it rebuilds coverage, skips completed tickers,
   and fetches only the remainder.

Manual cancellation remains terminal and is not converted into a deferred
resume.

## Visibility

The prewarm result and status API report:

- requested, already-current, fetched, stale/missing, and ready tickers;
- adjusted/trades dataset and bar interval;
- effective session and configuration identity;
- planned and executed requests;
- pacing, network, and cache-write time;
- coverage counts and ratio;
- preemption history and measured stop latency;
- dynamically calculated prewarm age;
- foreground pipeline reuse observations.

Pipeline performance output reports `prewarm_job_id`, `prewarm_age_seconds`,
`prewarm_covered_tickers`, and `prewarm_reused_tickers`. Reuse attribution counts
only tickers actually fetched by the current-session prewarm and subsequently
skipped by the foreground plan; previously current data is not misattributed.

## Operational guardrails

- `MARKET_DATA_PREWARM_ENABLED` disables new prewarm jobs without changing normal
  foreground fetching.
- The universe is capped by `MARKET_DATA_PREWARM_MAX_TICKERS`.
- The configured cancellation bound must cover one IB request timeout plus the
  minimum request spacing.
- A prewarm never marks stale data current and never bypasses a foreground
  coverage check.
- No historical bars, jobs, or pipeline results are deleted or rewritten.

## Laptop rollout validation (2026-08-12)

The rollout was enabled locally with the v2 configuration and validated against
PostgreSQL and the live IB connection using a one-ticker `IMAX` universe:

- first run: 2 incremental requests, 0 full backfills, 1 fetched/current ticker,
  6.020 seconds IB network time, and 210 ms cache-write time;
- identical second run: 0 requests, 2 fresh-series skips, 0 IB network/pacing
  time, and current coverage retained;
- the status API returned the effective session, configuration identity,
  coverage, timing, and dynamically increasing prewarm age;
- the pipeline resolver attributed the stored IMAX coverage to the earlier
  same-session fetch even after the later no-op verification job.

The current recent-run universes were planned before scheduling:

| Recent runs | Tickers | Incremental requests | Full backfills | Reused series | Minimum pacing |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 175 | 2 | 0 | 352 | 6 seconds |
| 5 | 585 | 820 | 0 | 1,172 | 2,460 seconds |

Only the bounded one-run universe was executed during rollout. It finished
`PARTIAL`: 173/175 tickers were ready, CLBK remained stale after IB returned no
security definition, and MOG.A retained its existing failed-contract state. The
five-run job was intentionally not scheduled because its approximately 41
minutes of minimum pacing would create substantial background load without
improving the rollout proof. Re-evaluate that universe after its 410 stale
tickers are understood or intentionally selected for a scheduled maintenance
window.
