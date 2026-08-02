# Phase 17 Review: Performance, Capacity, and Resource Safety

Date: 2026-08-02

## Objective

Establish practical limits and prevent resource exhaustion or performance collapse.

## Verification Performed

- Static review of upload, CSV loading, exports, pagination, background worker, IB fetch, pipeline execution, technical scoring, setup lifecycle, winner probability, and CERI paths.
- Local footprint check:
  - `data/uploads`: 71 files, 10.609 MB.
  - `data/exports`: 1 file, approximately 0 MB.
  - `data/cache`: 1 file, approximately 0 MB.
  - `logs` and `artifacts`: absent/empty.
- Focused performance/resource regression suite:
  `uv run pytest tests/test_pagination.py tests/setup_lifecycle/test_performance.py tests/ceri/test_ceri_performance.py tests/test_background_job_service.py tests/test_background_worker.py tests/test_ib_rate_limiter.py tests/winner_probability/test_routes_admin.py tests/winner_probability/test_job_handlers.py -q`
- Result: `48 passed, 1 warning` in 7.55s. The warning is the existing Starlette/httpx TestClient deprecation warning.

## Existing Capacity Controls

| Control | Current Evidence | Assessment |
| --- | --- | --- |
| Upload byte limit | `app/settings.py:26`, `app/services/upload_service.py:35`, `app/services/upload_service.py:126-138` | Good first guard: default max upload is 20 MB and validation happens before saving/processing. |
| Run/history page limit | `app/settings.py:81-83`, `app/services/pagination.py:28-50`, `tests/test_pagination.py:8-14` | Page size is clamped; default run page is 25, history default is 50, max is 200. |
| Setup lifecycle page limit | `config/setup_lifecycle.yaml:383-384`, `tests/setup_lifecycle/test_performance.py:86-94` | Default 50, max 500, with route tests checking cursor/limit contracts. |
| Winner probability page limit | `config/winner_probability.yaml:158`, `app/services/winner_probability/api_service.py:475-478` | Max 500 enforced by API query validation. |
| CERI page limit | `app/services/ceri/dtos.py:246-247` | Page size must be 1..500. |
| IB request pacing | `app/settings.py:39-42`, `app/services/ib_fetch_executor.py:181-207`, `tests/test_ib_rate_limiter.py:24` | Conservative by default: 20/min, 3-second minimum gap, 90-second backoff, 3 retries. |
| Background worker leases/retries | `app/settings.py:51-52`, `app/services/background_worker.py:45-61`, `app/services/background_job_service.py:23-25`, `app/services/background_job_service.py:297-301` | One local worker loop, 2-second idle poll, 900-second lease, retry delay capped at 600 seconds. |
| Lease metadata growth | `app/services/background_job_service.py:26`, `app/services/background_job_service.py:324-345` | Lease event history is capped at 50 events per job. |
| CERI backfill batch controls | `config/ceri.yaml:138-141` | Backfill uses 100-company / 30-session batches and max one concurrent CERI backfill job. |
| Retention declarations | `config/winner_probability.yaml:136-152`, `config/setup_lifecycle.yaml:408-413`, `config/ceri.yaml:197-201` | Policies exist, but cleanup execution is not consistently automated. |

## Representative Workloads

| Size | Tickers | Bars Per Ticker | Runs / Snapshots | Alerts / Provider Records | Expected Use |
| --- | ---: | ---: | ---: | ---: | --- |
| Small | 25 | 252 daily bars x 2 data types | 1-5 runs, under 5k derived rows | under 1k | Fast local smoke, manual upload, one-off scoring. |
| Medium | 250 | 756 daily bars x 2 data types | 25-100 runs, 25k-100k snapshots | 10k-50k | Practical local research workload. Current design should target this after query-count verification. |
| Large | 1,000 | 756-1,000 daily bars x 2 data types | 250+ runs, 100k+ snapshots | 100k+ | Needs batch loading, streaming exports, query budgets, and retention jobs before being declared supported. |

Recommended supported envelope today:

- Upload: up to 20 MB by configuration, but operationally prefer <= 5,000 CSV rows until row-count and memory refusal limits are added.
- Decision cockpit/run detail: keep runs around <= 1,000 tickers. `_load_run` eagerly loads raw, fundamental, technical, combined, and ranking relationships (`app/routers/run_routes.py:1813-1820`), so very large runs can balloon response memory.
- Technical scoring: medium-size batches only. `score_run_technicals` loops per ticker (`app/services/technical_score_service.py:31-68`) and each ticker loads adjusted/trades frames separately (`app/services/price_bar_repository.py:42-49`).
- Exports: suitable for current page/run scale, not guaranteed for large artifacts. Most exporters build complete `StringIO`/JSON strings in memory (`app/services/export_service.py:319-325`, `app/services/winner_probability/exports.py:112-117`, `app/services/ceri/export_service.py:32-38`).
- Background jobs: safe for one local worker and serialized heavy work; not yet sized for multiple concurrent workers or sustained queue pressure.

## Performance Baseline

Measured in current test environment:

- Pagination clamp test passes and verifies max-page enforcement.
- Setup lifecycle performance contracts verify:
  - API p95 target setting is 500 ms.
  - capture/evaluation target setting is 60 seconds.
  - performance fixture target is 100,000 snapshots.
  - critical index contracts exist.
  - 1,000 ticker event-key generation completes under 1 second.
- CERI current-view export for 500 ticker snapshots completes under 2 seconds.
- Background job retry, stale lease recovery, heartbeat, and retry delay behavior pass.

Budgets to formalize:

| Target | Budget |
| --- | --- |
| Dashboard and run/history list | p95 <= 500 ms for <= 100 runs and <= 200-row page. |
| Run detail | p95 <= 1.5 s for <= 1,000 tickers after eager-load optimization check. |
| Technical scoring | <= 60 s for 250 tickers x 3 years daily bars on local machine; fail/queue for larger batches. |
| IB fetch plan | p95 <= 1 s for 1,000 tickers with cached coverage summaries. |
| CSV export | <= 2 s and <= 100 MB response for 10k rows, or switch to streaming. |
| Setup lifecycle list APIs | p95 <= 500 ms at 100k snapshots and <= 500-row page. |
| Winner probability run API | p95 <= 1 s for 500 predictions; database-backed paging required beyond that. |
| CERI current export | <= 2 s for 500 rows, already covered by test. |
| Worker polling | Idle poll <= 2 s, no more than one heavy local job running unless explicitly configured. |

## Query Optimization Backlog

| Priority | Area | Evidence | Recommendation |
| --- | --- | --- | --- |
| P1 | Technical scoring OHLCV loading | `app/services/technical_score_service.py:31-68`, `app/services/price_bar_repository.py:10-35`, `app/services/price_bar_repository.py:42-49` | Add batch OHLCV loader by ticker/data type/timeframe. Avoid two SQL queries plus DataFrame construction per ticker. |
| P1 | Winner probability run evidence | `app/services/winner_probability/api_service.py:77-96`, `app/services/winner_probability/api_service.py:389-407` | Push filters/sort/page into SQL and batch-load estimates. Current path loads all predictions then performs per-prediction estimate lookups before paging. |
| P1 | Run detail eager load | `app/routers/run_routes.py:1813-1820` | Add a lightweight run-detail summary path, server-side cockpit pagination, and lazy detail fetch for large runs. |
| P2 | CERI exports | `app/services/ceri/export_service.py:48-72`, `app/services/ceri/export_service.py:187-194` | Filter in SQL instead of loading all score snapshots when no test fixture list is supplied. Stream large CSV/JSON exports. |
| P2 | Generic exports | `app/services/export_service.py:319-325` | Convert high-volume exports to generators/`StreamingResponse`; include row/byte refusal thresholds before full materialization. |
| P2 | History run summaries | `app/services/history_query_service.py:104-150` | Review correlated subqueries with real PostgreSQL plans; add composite indexes or summary table if history grows. |
| P2 | Offset pagination | `app/services/pagination.py:39-50`, `app/services/setup_lifecycle/query_service.py:82-94` | Keep offset for small/admin pages; add keyset pagination for high-cardinality lists. |
| P3 | CERI/admin operations status | `app/routers/ceri_routes.py:1024-1033` | Avoid unbounded list-all helpers in operations status; cap or aggregate processing/job history. |

## Resource-Limit Recommendations

1. Add CSV row-count and column-count limits in `load_csv_rows`, not only byte-size limits.
2. Reject or queue technical refreshes above a configured ticker threshold; show estimated bar queries and memory before executing.
3. Add export row/byte limits and switch high-volume exports to streaming.
4. Add max chart bars returned by chart-data API; default to recent 1,000 bars with explicit full-history export if needed.
5. Add max background queue depth per job type and per run; return existing active job instead of enqueueing duplicates.
6. Add query-count instrumentation in route tests for run detail, winner evidence, lifecycle pages, and CERI operations.
7. Add per-job heartbeat frequency budgets so long IB backoff sleeps do not look stale.
8. Add database statement timeout for local web requests; allow longer timeout only inside background workers.
9. Add memory budget tests for CSV upload, technical scoring, winner training, and exports using representative fixtures.
10. Add a user-facing capacity table in help/settings so local limits are visible before users queue expensive work.

## Retention And Cleanup Proposal

| Resource | Current State | Proposal |
| --- | --- | --- |
| Uploaded CSV files | Stored under `data/uploads`; current local footprint 10.609 MB. | Add cleanup job: keep source uploads for 90 days or while their run is retained; allow explicit "archive/delete upload file but keep DB evidence." |
| Export files | Config treats some exports as rebuildable, but most routes generate in memory. | If exports are persisted, delete after 7-30 days by default; keep manifest/hash if needed. |
| Cache files | `data/cache` exists but has no documented cleanup cadence. | Add max disk size and oldest-first eviction for rebuildable cache. |
| Price bars | Retained in DB; can grow quickly with 2 data types x 1,000 tickers x years. | Retain daily bars indefinitely only for active universe; archive or summarize delisted/unused symbols after N days. |
| Background jobs | Lease events capped, but completed job rows can accumulate. | Keep completed/failed operational job rows 90 days; retain aggregate counts and failed-job error fingerprints longer. |
| Setup lifecycle evidence | Immutable evidence retained indefinitely and purge disabled. | Keep immutable business evidence, but cleanup rebuildable indexes/caches and expired alert delivery attempts. |
| Winner probability | Permanent vs rebuildable classes are declared; operational logs 90 days. | Implement cleanup for rebuildable `neighbor_caches`, `temporary_aggregates`, `export_files`, and `operational_logs`. |
| CERI source evidence | Retain indefinitely, provider purge disabled. | Add dry-run disk/row-count report and provider-license purge executor gated by preview, confirmation, and audit. |

## Performance Regression Test Candidates

- Upload a generated 5,000-row CSV and assert parse/map/fundamental scoring time and peak memory.
- Count SQL statements for `/runs/{id}` with 1,000 tickers; fail if it exceeds a fixed budget.
- Technical scoring fixture with 250 tickers x 756 bars and a query-count budget after batch loader exists.
- Winner probability run API with 2,000 predictions; assert page response does not load all estimates.
- CERI current export with 5,000 snapshots using real DB filters; assert SQL filter and streaming behavior.
- Setup lifecycle list query at 100,000 snapshots against PostgreSQL, not fake collections.
- Concurrent browser/API smoke: open run detail and poll progress while a background pipeline job is running.
- Export byte-limit/refusal tests for CSV and JSON.
- Worker retry storm test with 100 failing queued jobs; verify delayed retries and bounded queue churn.
- Cleanup dry-run test covering uploads, exports, cache, completed jobs, and rebuildable winner/CERI artifacts.

## Exit Criteria Assessment

Phase 17 review is complete, but exit criteria are only partially met.

Met:

- Practical workload bands and current supported envelope are documented.
- Several key controls exist: upload byte cap, page-size caps, conservative IB pacing, worker leases/retries, and some performance/index tests.
- Critical UI/API targets now have proposed measurable budgets.

Not yet met:

- Common operations can still materialize large data in memory: upload CSV rows, exports, CERI exports, and winner run evidence.
- Technical scoring and winner probability have N+1 or all-before-page patterns that need remediation before large workloads.
- Disk retention/cleanup policies are declared in places but not consistently implemented as scheduled cleanup jobs.
- Query-count and peak-memory tests are not yet automated against real PostgreSQL.
