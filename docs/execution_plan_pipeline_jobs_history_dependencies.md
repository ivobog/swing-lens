# SwingLens Execution Plan: Pipeline, Durable Jobs, History, and Dependencies

Source documents:

- `C:\Users\Ivica\Downloads\SwingLens_SRS_pipeline_jobs_history_dependencies.md`
- `C:\Users\Ivica\Downloads\SwingLens_SDD_pipeline_jobs_history_dependencies.md`

This plan is ordered by implementation dependency rather than by product surface. The durable job foundation comes first because the true full pipeline depends on recoverable work state.

## Current Repo Baseline

- `POST /runs/{run_id}/pipeline` in `app/routers/run_routes.py` currently recalculates fundamentals, builds an IB plan, scores technicals immediately, refreshes combined results, and only then queues an IB fetch if needed.
- IB fetch work currently uses `app/services/ib_fetch_job_service.py`, which stores fetch runs/items in PostgreSQL but tracks active execution through an in-memory `ThreadPoolExecutor`, futures, and cancel events.
- `/runs` loads all `UploadRun` rows.
- `/history` eager-loads all runs and combined results, then summarizes in Python.
- `pyproject.toml` has loose dependency ranges and there is no `uv.lock` or `requirements.lock`.

## Phase 0: Preparation and Guard Rails

Goal: make the change easy to stage, verify, and roll back.

Tasks:

1. Create a working branch, for example `codex/durable-pipeline`.
2. Run the current test suite to capture the baseline:
   ```powershell
   pytest -q
   ruff check app tests
   ```
3. Add a feature flag setting for the new durable pipeline path:
   - `USE_DURABLE_PIPELINE=false`
4. Add worker settings to `app/settings.py` and `.env.example`:
   - `JOB_WORKER_ENABLED=false`
   - `JOB_POLL_INTERVAL_SECONDS=2`
   - `JOB_STALE_AFTER_SECONDS=900`
   - `JOB_WORKER_ID=local-worker-1`
   - `RUNS_DEFAULT_PAGE_SIZE=25`
   - `HISTORY_DEFAULT_PAGE_SIZE=50`
   - `HISTORY_MAX_PAGE_SIZE=200`

Exit criteria:

- Existing app behavior still works.
- Manual routes remain unchanged.
- New settings are covered by simple config tests.

## Phase 1: Durable Job Foundation

Goal: replace in-memory job tracking as the primary orchestration mechanism.

Primary files:

- `app/models/tables.py`
- `alembic/versions/*_add_background_jobs.py`
- `app/services/background_job_service.py`
- `app/services/background_worker.py`
- `app/worker.py`
- `tests/test_background_job_service.py`
- `tests/test_background_worker.py`

Tasks:

1. Add `BackgroundJob` ORM model with:
   - job type, related run ID, status, priority,
   - payload/result JSON,
   - error message,
   - retry count/max retries,
   - requested cancel,
   - worker ID,
   - locked/run-after/created/started/completed timestamps.
2. Add Alembic migration for `background_jobs` and indexes:
   - `(status, priority, run_after, created_at)`
   - `related_run_id`
   - `locked_at`
3. Implement `background_job_service.py`:
   - `enqueue_job`
   - `claim_next_job`
   - `mark_job_completed`
   - `mark_job_failed_or_retry`
   - `request_job_cancel`
   - `recover_stale_jobs`
   - `is_cancel_requested`
4. Use PostgreSQL `FOR UPDATE SKIP LOCKED` for job claiming.
5. Implement `background_worker.py` with a single-job loop and dispatch table.
6. Add `app/worker.py` entrypoint:
   ```powershell
   python -m app.worker
   ```
7. Keep the existing IB fetch job service operational during this phase.

Tests:

- Queued job can be claimed once.
- Two claim attempts do not claim the same job.
- Failed jobs retry with backoff until `max_retries`.
- `RUNNING` jobs older than stale timeout are recovered or marked `STALE`.
- Cancel request flips persistent state.

Exit criteria:

- A durable no-op or test job can be enqueued, claimed, completed, failed, retried, cancelled, and recovered after simulated restart.

## Phase 2: Pipeline Persistence

Goal: add durable pipeline state before changing the full pipeline route.

Primary files:

- `app/models/tables.py`
- `alembic/versions/*_add_pipeline_tables.py`
- `app/services/pipeline_service.py`
- `tests/test_pipeline_service.py`

Tasks:

1. Add `PipelineRun` ORM model.
2. Add `PipelineStep` ORM model.
3. Add relationships from `UploadRun` where useful.
4. Add Alembic migration for:
   - `pipeline_runs`
   - `pipeline_steps`
   - indexes on upload run ID, status, created timestamp, pipeline run ID,
   - unique `(pipeline_run_id, step_name)`.
5. Implement `pipeline_service.py`:
   - `start_pipeline(db, upload_run_id, requested_by=None)`
   - `get_pipeline_status(db, pipeline_run_id)`
   - `cancel_pipeline(db, pipeline_run_id)`
6. `start_pipeline` should create:
   - one `pipeline_runs` row,
   - expected step rows,
   - one durable `FULL_PIPELINE` background job.

Recommended step names:

- `VALIDATING_RUN`
- `SCORING_FUNDAMENTALS`
- `FETCHING_MARKET_DATA`
- `SCORING_TECHNICALS`
- `COMBINING_RESULTS`

Tests:

- Starting a pipeline for a missing upload run fails cleanly.
- Starting a pipeline creates pipeline run, steps, and `FULL_PIPELINE` job atomically.
- Cancelling a pipeline requests cancellation on the related background job.
- Pipeline status DTO returns current step and step list.

Exit criteria:

- The app can persist a pipeline and expose status without executing real pipeline work yet.

## Phase 3: Full Pipeline Executor

Goal: make one durable `FULL_PIPELINE` job execute the whole workflow in order.

Primary files:

- `app/services/pipeline_executor.py`
- `app/services/background_worker.py`
- `app/services/ib_fetch_executor.py`
- `tests/test_pipeline_executor.py`
- `tests/test_full_pipeline_integration.py`

Tasks:

1. Implement `execute_full_pipeline(db, pipeline_run_id, should_cancel=None)`.
2. Load and validate `UploadRun`.
3. Mark pipeline and each step as started/completed/failed.
4. Recalculate fundamentals via `recalculate_run_fundamentals`.
5. Build IB fetch plan via `build_fetch_plan`.
6. If fetch requests are needed, execute IB fetch inside the same `FULL_PIPELINE` job using `execute_fetch_plan`.
7. Score technicals only after fetch completion or after confirming no fetch is needed.
8. Refresh combined results.
9. Determine final pipeline status:
   - `COMPLETED` when all required outputs are usable,
   - `PARTIAL` when ticker-level failures or incomplete market data remain,
   - `FAILED` only for systemic failures.
10. Check `should_cancel` between major steps and before each fetch request where the existing executor supports it.
11. Update background job result JSON with counts:
   - uploaded rows,
   - fundamental scores,
   - IB planned/executed/succeeded/failed/skipped,
   - technical scores,
   - combined rows,
   - incomplete rows,
   - warning rows.

Important implementation decision:

- Do not create nested durable jobs for the MVP. The first version should use one `FULL_PIPELINE` job that calls the existing services sequentially.

Tests:

- Missing OHLCV triggers fetch before technical scoring.
- No missing OHLCV skips fetch and still scores technicals/combined.
- Failed IB contract marks ticker incomplete and pipeline `PARTIAL`.
- Cancel request stops before the next major operation.
- Executor is idempotent for repeated runs: no duplicate score/result rows.

Exit criteria:

- Acceptance criteria `AC-PIP-001`, `AC-PIP-002`, `AC-JOB-001`, and `AC-JOB-002` are covered by automated tests.

## Phase 4: Route and UI Integration

Goal: replace the current incomplete full-pipeline action with durable orchestration and visible progress.

Primary files:

- `app/routers/run_routes.py`
- `app/templates/run_detail.html`
- `app/templates/fetch_progress.html` or new `pipeline_progress.html`
- `app/static/app.js`
- `tests/test_run_actions_phase3.py`
- new route tests for pipeline status/cancel

Tasks:

1. Change `POST /runs/{run_id}/pipeline` to call `start_pipeline`.
2. Redirect back to run detail or a pipeline progress page with `pipeline_id`.
3. Add routes:
   - `GET /runs/{run_id}/pipeline/{pipeline_id}`
   - `GET /runs/{run_id}/pipeline/{pipeline_id}/status`
   - `POST /runs/{run_id}/pipeline/{pipeline_id}/cancel`
4. Add progress UI showing:
   - current status,
   - current step,
   - step timeline,
   - counts,
   - last error,
   - cancel/retry/resume affordances where supported.
5. Keep manual actions:
   - fundamentals recalculate,
   - IB fetch,
   - technical refresh,
   - combined refresh,
   - exports.
6. Honor `USE_DURABLE_PIPELINE=false` as a rollback path during rollout.

Tests:

- Pipeline route enqueues work and returns quickly.
- Status endpoint returns progress from persisted rows.
- Cancel endpoint sets persistent cancel request.
- Existing manual buttons still work.

Exit criteria:

- User can click `Run full pipeline` once and observe durable progress.

## Phase 5: Runs and History Pagination

Goal: stop loading all runs/results into memory.

Primary files:

- `app/services/pagination.py`
- `app/services/history_query_service.py`
- `app/routers/run_routes.py`
- `app/templates/runs.html`
- `app/templates/history.html`
- `alembic/versions/*_add_history_indexes.py`
- `tests/test_pagination.py`
- `tests/test_history_query_service.py`
- `tests/test_runs_pagination_routes.py`
- `tests/test_history_pagination_routes.py`

Tasks:

1. Add `Page` DTO and `paginate_query`.
2. Add filter DTOs:
   - `RunFilters`
   - `DecisionFilters`
3. Implement `paged_runs` using SQL filters:
   - `page`
   - `page_size`
   - `status`
   - `from_date`
   - `to_date`
   - `search`
4. Implement `paged_decisions` using SQL joins from `CombinedResult` to `UploadRun`:
   - `page`
   - `page_size`
   - `from_date`
   - `to_date`
   - `decision`
   - `ticker`
   - `sector`
   - `min_score`
   - `has_warning`
   - `incomplete_only`
5. Add run-level summary query for `/runs`.
6. Update templates with filters, page size selector, total count, and previous/next links.
7. Add indexes:
   - `upload_runs(uploaded_at DESC)`
   - `upload_runs(status)`
   - `combined_results(ticker)`
   - `combined_results(combined_decision)`
   - `combined_results(final_score)`
   - `combined_results(has_warning)`
   - `combined_results(is_complete)`
   - `combined_results(run_id, final_rank)`

Tests:

- `/runs` does not eager-load all relationships.
- `/history` filters in SQL.
- Invalid page/page size values are clamped.
- Query results are stable and sorted correctly.

Exit criteria:

- Acceptance criteria `AC-HIS-001` and `AC-HIS-002` are covered by route or integration tests.

## Phase 6: Dependency Pinning and Golden Regression

Goal: make installs reproducible and scoring changes detectable.

Primary files:

- `pyproject.toml`
- `uv.lock`
- `README.md`
- `tests/test_golden_pipeline.py`
- `tests/fixtures/*`

Tasks:

1. Generate lock file:
   ```powershell
   uv lock
   ```
2. Document exact install:
   ```powershell
   uv sync --frozen
   ```
3. Document dependency update process:
   ```powershell
   uv lock --upgrade
   ruff check app tests
   pytest -q
   pytest tests/test_golden_pipeline.py -q
   ```
4. Add a golden pipeline test using fixed CSV and OHLCV fixtures.
5. Assert:
   - top ranked ticker,
   - final score tolerance,
   - decision labels,
   - warning flags,
   - incomplete rows.

Exit criteria:

- Acceptance criteria `AC-DEP-001` and `AC-DEP-002` are covered.

## Phase 7: End-to-End Verification

Goal: prove the complete product workflow behaves as required.

Manual verification script:

1. Start PostgreSQL.
2. Apply migrations:
   ```powershell
   alembic upgrade head
   ```
3. Start web app.
4. Start worker:
   ```powershell
   python -m app.worker
   ```
5. Upload a sample CSV.
6. Click `Run full pipeline`.
7. Confirm pipeline progresses through:
   - fundamentals,
   - market data,
   - technicals,
   - combined results.
8. Restart the app while a job is queued or running.
9. Confirm job remains visible and is recovered according to policy.
10. Open `/runs` and `/history` with filters.
11. Export combined CSV.

Automated verification:

```powershell
ruff check app tests
pytest -q
```

Exit criteria:

- One-click pipeline finishes `COMPLETED` or `PARTIAL`.
- No manual technical/combined refresh is required after IB fetch.
- Restart does not silently lose queued/running job state.
- `/runs` and `/history` are paginated.
- Locked install works from a fresh checkout.

## Recommended Implementation Order

1. Durable background job table and service.
2. Pipeline tables and pipeline service.
3. One sequential `FULL_PIPELINE` executor.
4. Route/UI progress integration.
5. Paginated `/runs` and `/history`.
6. `uv.lock`, README updates, and golden regression test.

## Rollback Strategy

- Keep existing manual routes operational throughout.
- Keep existing IB fetch page and fetch run audit tables.
- Use `USE_DURABLE_PIPELINE=false` to fall back during rollout.
- New tables are additive and can remain unused if rollback is needed.

## Definition of Done

This work is done when a user can upload a CSV, click `Run full pipeline` once, watch durable progress, restart the app without losing job visibility, open fast paginated history pages, and reinstall exact dependency versions from the lock file.
