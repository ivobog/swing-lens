# Run 38 EODHD recovery and fresh pipeline validation

Validated on 17 October 2026, Europe/Zurich. Fresh **Run 39 / Pipeline 39 completed at 02:01:13**, with **13/13 stages and 15/15 jobs COMPLETED**. The rendered UI, status endpoint, PostgreSQL rows, and worker logs agree.

## Root cause and evidence

Run 38's database and worker log confirm 34 failed catalyst acquisitions out of 88. Each failure exhausted four HTTP attempts: recorded latency 128,356–128,538 ms, averaging 128,398 ms. Provider jobs 1320 and 1322 ended PARTIAL with 15 and 19 failures respectively; both had `retry_count=0`, despite `max_retries=3`. Normalization, features, capture, and alerts completed, but the strict certification barrier correctly rejected the incomplete provider workflow.

The client replaced the transport exception with `EODHD network or response failure`. Ingestion then converted it to terminal PARTIAL, and the provider batch added the failed ticker to its completion checkpoint. Consequently, durable job retries could never reacquire the missing data. Completed/PARTIAL ingestion coalescing reinforced that behavior.

A fresh live probe reproduced `TimeoutError: The read operation timed out`. Another request returned 50 news records in 27.3 seconds. The existing symbol/date query matches the [official EODHD example](https://github.com/EodHistoricalData/EODHD-openapi/blob/main/EXAMPLES.md). Successful urllib responses were already buffered and closed correctly. There is no evidence that an invalid query or a leaked successful connection caused Run 38. Its original underlying exception was discarded, so that exact historical transport cause cannot be recovered.

Additional reproduced client defects: JSON decoding preceded HTTP status handling; HTTPError response handles were not closed; the urllib error path ignored Retry-After; the alternate path allowed unbounded Retry-After sleeps; retries were excluded from application quota accounting.

Evidence: `forensic-telemetry.json`, `runtime-log-evidence.json`.

## Minimal implementation

| File | Change |
| --- | --- |
| `app/services/ceri/providers/eodhd_client.py` | Retain redacted exception type/message, endpoint, timeout, and attempt count; classify exhausted temporary failures for existing worker retries; close HTTP errors; check status before decoding; respect bounded Retry-After; account for every attempt; honor the configured User-Agent. |
| `app/services/ceri/orchestration.py` | Record temporary acquisition failures as FAILED and propagate the retryable exception. A later attempt uses the same workflow/refresh identity. Preserve cancellation, including empty responses. |
| `app/services/ceri/batched_job_handlers.py` | Commit each acquired ticker under existing execution-token and parent-pipeline fences before saving its detached checkpoint. Leave the failed ticker uncheckpointed and stop the attempt for the existing worker retry. Include retained data-quality failures in the final batch outcome. |
| `app/services/ceri/job_handlers.py` | Propagate the same temporary error from the existing single-ticker handler. |
| `config/architecture_registry.json` and transaction-boundary audit | Register the fenced per-ticker provider transaction boundary and link its PostgreSQL recovery test. |

The configured 30-second timeout and four HTTP attempts remain unchanged. Existing job retries remain bounded by their configured budget (normally three retries, delayed 60/180/600 seconds). Retry-After sleeps are capped at 30 seconds. Persistent temporary failures exhaust that budget and fail the parent with diagnostic details. Already acquired tickers coalesce without contacting the provider again, including a crash between data commit and checkpoint publication.

No scheduler, schema, scoring algorithm, certification rule, or provider validation was changed. Pre-existing workspace edits were preserved.

## Regression validation

Six new assertions failed against the original implementation before the fix. Final passing coverage totals **691 tests**:

- CERI, pipeline executor/service, background job service, and worker suite: 662 passed; four subsequently added HTTP/cancellation/checkpoint cases also passed (666 unique tests).
- Existing PostgreSQL batched workflow and completion-barrier suites: 20 passed.
- New real-worker/PostgreSQL timeout recovery and persistent-failure tests: 2 passed. These verify durable data survives rollback, successful tickers are requested once, source memberships do not duplicate, the barrier stays closed during retry, and budget exhaustion fails the parent.
- PostgreSQL evidence quarantine and scope/refresh acquisition suites: 3 passed.
- Ruff checks for all changed application/test files: passed.

Test additions are in `tests/ceri/test_eodhd_recovery.py`, `tests/integration/test_eodhd_job_recovery_postgresql.py`, and the existing ingestion/batched-workflow test modules. Disposable PostgreSQL databases were used; the application database was not rebuilt.

## Fresh live run

Run 39 uses one exact AAPL input row from Run 38 and the normal **REQUIRE_IB** policy. It ran through validation, fundamental scoring, market acquisition, technicals, regime, combination, ranking, sector rotation, provider ingestion, decision handoff, setup capture, setup evaluation, and Winner capture.

- EODHD live acquisitions: 48 estimate records, 7 earnings records, 50 news/catalyst records; no failed acquisitions. News took 26.228 seconds.
- SEC native discovery succeeded; five already completed document extractions were correctly reused. A separate fresh native SEC probe confirmed two HTTP 2xx responses (ticker map and submissions), without mocks.
- The pipeline verified live IB readiness and correctly reused six current market-data series under existing freshness rules. It required zero new historical fetches. A supplemental read-only IB request returned three AAPL daily bars through 16 October; it did not change cached data.
- CERI snapshot 3897 has evidence 37746, run 39 ownership, and calculation context 71. All four ingestion rows retain Run 39's exact scope/refresh/acquisition identities.
- CERI dependency 54 is COMPLETED; continuation 1352 was automatically scheduled and completed. CERI completion is CERTIFIED.
- Final outputs: one Combined result, five Ranking results, one decision handoff manifest, one Setup snapshot, and one Winner prediction/decision-time estimate. All eight applicable core evidence kinds are present.
- No pending, running, failed, or PARTIAL jobs remain for this validation run. Logs contain all 13 stage completions and all 15 job completions.

Evidence: `live-run-latest.json`, `live-final-database.json`, `live-ui-status.json`, `live-provider-acquisition.json`, `live-ib-readiness.json`, `live-ib-bars-probe.json`, `live-sec-probe.json`, and `runtime-log-evidence.json`. UI screenshot: `output/playwright/run39-completed.png`.

Run 38's pipeline, 13 steps, 33 jobs, and 360 ingestion rows have identical before/after hashes: `243c3ff03c0967041b8961486855a04cea76c7e1a76b620396348c3de511178f`. See `run38-historical-verification.json`.

## Verdict

- ROOT CAUSE: **PARTIALLY CONFIRMED** — application retry/diagnostic defects confirmed; the discarded original transport exception is unrecoverable.
- FIX: **PASS**
- REGRESSION TESTS: **PASS**
- LIVE END-TO-END PIPELINE: **PASS**
- FINAL PIPELINE STATUS: **COMPLETED**

No remaining pipeline blockers. Optional Prometheus/Grafana services were unavailable during the lifecycle restart; core application, worker, database, and IB readiness passed. Historical non-authoritative jobs belonging to unrelated runs were not modified.

## Main-branch integration

The fix was applied independently to the current GitHub `main` base (`0f4f797`), preserving main's existing worker result-status handling and excluding unrelated local changes and divergent branch history. The PostgreSQL recovery test explicitly enables ingestion so it also runs without a local `.env` file. The new fenced commit site is documented under TX-08 and included in the architecture registry; the checker itself is unchanged. On this main base, revalidation passed 667 CERI/pipeline/worker tests and 21 PostgreSQL tests (19 existing workflow/barrier tests plus both recovery tests), together with full repository lint, architecture closure, secret scanning, and route inventory checks. The live Run 39 evidence above describes the original local application validation, before this integration onto main.
