# Phase 8 — Fetch / Technical Overlap Rollout

Phase 8 allows database-free technical calculation to run while the foreground
pipeline is waiting for serialized Interactive Brokers requests. IB access stays
single-threaded and final technical ranking and persistence stay in the parent
database session.

## Verified dependency order

The fetch executor processes configured benchmark and sector dependencies before
requested securities. A ticker-ready event is emitted only after every planned
data stream for that ticker has reached a terminal state and the bar transaction
has committed.

The technical coordinator holds submissions behind the benchmark barrier. Before
final persistence it reloads SPY, QQQ, and the configured sector frame, compares
their content signature with the signature used for each submitted ticker, and
rescores only affected tickers.

## Bounded execution

The laptop rollout uses:

```text
TECHNICAL_WORKER_PROCESSES=2
TECHNICAL_MAX_IN_FLIGHT=4
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

The application bootstrap sets the numerical thread limits before pandas/NumPy
load, so spawned workers inherit them. The ticker-ready callback never waits for
a CPU result merely because the in-flight queue is full. It leaves the ticker
pending and lets the serialized IB loop continue; finalization drains the bounded
remainder.

Worker inputs are immutable data records containing frames/configuration, not
SQLAlchemy sessions or ORM objects. Completed artifacts remain in memory until
fetching has ended and dependency validation has passed. The parent session then
publishes artifacts and replaces technical rows in deterministic upload order.

## Failure and cancellation contract

Process creation, callback preparation, executor submission, or a broken process
pool activates one safe pure-sequential retry using the final committed bars.
The fallback is recorded in pipeline performance and operational counters.

Cancellation or job lease loss stops new submissions, cancels pending futures
where possible, and prevents technical-score persistence. A fatal fallback is
attempted only while the lease remains valid and cancellation has not been
requested.

## Baseline and measurement

The closest stored recent-style sequential baseline is pipeline 91 / upload run
94 (175 tickers):

| Component | Sequential duration |
| --- | ---: |
| IB fetch, 352 requests | 1,055.110 s |
| Technical scoring | 93.796 s |
| Sequential fetch + technical | 1,148.906 s |

That workload has since become current through Phase 6/7 reuse, so it was not
force-refetched merely to recreate 352 broker calls. Doing so would violate the
request-reuse priority and add about 17.6 minutes of unnecessary IB traffic.

The overlap path has exact synthetic technical-row parity with pure sequential
execution and real PostgreSQL parent-session publication. Per-run metrics now
report `technical_tickers_completed_during_fetch`; the next naturally stale
recent-style pipeline will provide the real critical-path measurement without a
forced refresh.

## Rollback

Set:

```text
FETCH_TECHNICAL_OVERLAP_ENABLED=false
```

Normal sequential technical scoring remains available. Disabling overlap does
not change market-data freshness, artifact invalidation, technical formulas,
ranking, or persisted result contracts.
