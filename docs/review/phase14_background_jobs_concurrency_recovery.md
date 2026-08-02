# SwingLens Phase 14 Background Jobs, Durable Pipeline, Concurrency, and Recovery

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 3 configuration: `docs/review/phase3_configuration_feature_flags.md`
Phase 4 database: `docs/review/phase4_database_migrations_transactions.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 14 reviews whether asynchronous work is safe under retries, crashes, multiple workers,
cancellation, stale recovery, and partial failure. The review separates two layers:

- row-level background-job leasing and fencing;
- end-to-end job handler and durable-pipeline idempotency.

The row-level lease primitives are comparatively strong. The higher-level pipeline recovery contract
still needs hardening before two-worker operation should be treated as proven.

## Evidence Log

Inspected files and surfaces:

- `app/services/background_job_service.py`
- `app/services/background_worker.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `app/services/ib_fetch_executor.py`
- job handlers under `app/services/ceri`, `app/services/setup_lifecycle`, and
  `app/services/winner_probability`
- worker lifecycle in `app/main.py`
- migrations `0007_add_background_jobs`, `0008_add_pipeline_tables`,
  `0015_harden_background_job_leases`
- focused tests for background jobs, worker dispatch, app lifespan, pipeline service/executor, IB
  fetch jobs, setup lifecycle jobs, winner jobs, and CERI orchestration

Command evidence:

| Command | Result | Notes |
|---|---:|---|
| `uv run pytest tests/test_background_job_service.py tests/test_background_worker.py tests/test_app_lifespan_worker.py tests/test_pipeline_service.py tests/test_pipeline_executor.py tests/test_ib_fetch_job_service.py tests/setup_lifecycle/test_setup_lifecycle_job_handlers.py tests/winner_probability/test_job_handlers.py tests/ceri/test_ceri_orchestration.py -q` | Passed | `72 passed, 1 warning in 5.86s` |
| Real PostgreSQL probe: one queued job, two uncommitted sessions claiming with `SKIP LOCKED` | Passed | First worker claimed job `1`; second got `None` |
| Real PostgreSQL probe: two queued jobs, two sessions | Passed | Workers claimed distinct jobs |
| Real PostgreSQL probe: stale recovery plus late old-worker commit | Passed | Recovered job got a new token/owner; old token commit raised `JobLeaseLost` |

Probe limitation:

- The PostgreSQL probe used a temporary database and created only the `background_jobs` table from
  current metadata. This directly exercises row locking and token fencing but is not a committed
  integration test and does not cover full pipeline side effects.

## Current Concurrency Model

Background-job model:

- `enqueue_job` creates queued jobs with priority, `run_after`, retry counts, payload, and optional
  related run.
- `claim_next_job` selects the next queued job with `FOR UPDATE SKIP LOCKED`, marks it `RUNNING`,
  assigns `lease_owner`, `worker_id`, `execution_token`, heartbeat, and lease expiry.
- `heartbeat_job`, `mark_job_completed`, `mark_job_partial`, `mark_job_cancelled`, and
  `mark_job_failed_or_retry` update by `id`, current status, and execution token.
- `recover_stale_jobs` requeues expired running jobs or marks retry-exhausted jobs `STALE`.
- `run_worker_once` recovers stale jobs, claims one job, executes the handler, and commits terminal
  or retry state.

Pipeline model:

- `start_pipeline` creates one `PipelineRun`, step rows, and a `FULL_PIPELINE` background job.
- `execute_full_pipeline` runs steps sequentially.
- `_pipeline_step` commits progress at step start and completion/failure.
- Cancellation is cooperative through `should_cancel`, which calls job heartbeat in the full
  pipeline worker wrapper.
- Full pipeline steps are not resumed from persisted checkpoint state; retries start the handler
  again.

## Failure-Injection Matrix

| Failure point | Current behavior | Risk |
|---|---|---|
| Worker dies before claim commit | Job remains queued or transaction rolls back | Low |
| Worker dies after claim commit, before handler side effects | Stale recovery requeues after lease expiry | Low/Medium |
| Worker loses lease while still running | Final job update is fenced by execution token | Low at job row, higher for side effects |
| Worker runs a long step without heartbeat | Lease can expire and another worker can reclaim | High for pipeline side effects |
| Failure after one pipeline step commits | Retry starts full handler again | Medium/High unless every prior step is idempotent |
| Failure after IB fetch item commits | Retry can create additional fetch-run evidence and reprocess market data | Medium |
| Duplicate full-pipeline start for same upload run | Creates another pipeline/job | High |
| Cancellation while queued | Job and pending steps cancel immediately | Low |
| Cancellation while running | Cooperative; checked between steps and by handlers that call heartbeat | Medium |
| Worker shutdown | Lifespan sets stop event and joins for 5 seconds | Medium for long-running handler shutdown |

## Idempotency Requirements by Job Type

| Job type/family | Evidence | Gap |
|---|---|---|
| `FULL_PIPELINE` | Some steps use delete/insert or natural keys; progress is persisted per step | No run-level dedupe; no resume from completed steps; no handler-wide idempotency contract |
| CERI ingestion/normalization/backfill | Deterministic request keys and source-record idempotency keys exist | Route/job coalescing is not DB-unique and can race |
| CERI capture/rebuild/purge | Processing-run request keys and duplicate-aware services exist | Purge lifecycle is audit-only per Phase 4; rebuild/purge retries need real DB tests |
| Setup lifecycle jobs | Handlers call heartbeat/cancel hooks and repositories use natural keys/current revisions | No real concurrent worker tests for duplicate lifecycle events under retry |
| Winner probability jobs | Handlers call heartbeat/cancel hooks; capture reports duplicates | No real concurrent worker tests for duplicate estimates/outcomes/training artifacts |
| IB fetch execution | Per-item commits and price-bar cache upserts exist | Retried full pipeline can create additional fetch-run rows and repeat external calls |

## Findings Register

ID: PH14-001
Title: Full pipeline starts are not idempotent per upload run
Severity: S1 High
Confidence: Confirmed
Affected components: `app/services/pipeline_service.py`, `app/routers/run_routes.py`,
`pipeline_runs`, `background_jobs`
Evidence: `start_pipeline` always creates a new `PipelineRun`, new `PipelineStep` rows, and a new
`FULL_PIPELINE` job after confirming the upload run exists. The durable route calls it directly when
`use_durable_pipeline` is enabled. There is no active-pipeline lookup, request key, uniqueness
constraint, or coalescing behavior for the same upload run.
Reproduction steps: Call `start_pipeline(db, upload_run_id)` twice for the same completed upload
run, or submit `/runs/{run_id}/pipeline` twice.
Expected behavior: Duplicate starts for the same run either return the active pipeline or require an
explicit "start another run" action.
Observed behavior: Duplicate pipelines and background jobs are created.
Impact: Two workers can process the same upload run concurrently, repeating IB fetches and racing
delete/insert refreshes for derived scores and snapshots.
Root cause or likely cause: Full-pipeline enqueue lacks the request-key/coalescing pattern used in
some CERI routes.
Recommended remediation: Add an active-pipeline idempotency key, DB uniqueness/partial index where
possible, and route-level coalesced response. Provide an override only for intentional reruns.
Acceptance criteria: Two duplicate start requests produce at most one active `FULL_PIPELINE` job for
the same upload run unless an explicit rerun key is provided.
Regression tests required: Unit and real PostgreSQL race tests for duplicate pipeline starts.
Owner profile: Backend engineer
Dependencies: Product decision on whether simultaneous reruns should ever be allowed.

ID: PH14-002
Title: Pipeline retries restart from the beginning after committed step side effects
Severity: S1 High
Confidence: Confirmed
Affected components: `app/services/pipeline_executor.py`, `app/services/background_worker.py`,
scoring refreshes, IB fetch, optional CERI/SLSE/OWPE captures
Evidence: `_pipeline_step` calls `_save_progress`, which flushes and commits at step start and
completion. `run_worker_once` requeues failed jobs through `mark_job_failed_or_retry`; retrying
`FULL_PIPELINE` calls `execute_full_pipeline` from the beginning. There is no persisted checkpoint
that skips already-completed steps.
Reproduction steps: Inject an exception after a later step has committed, then allow the background
job to retry.
Expected behavior: Retry either resumes from the next safe step or explicitly re-executes only
idempotent steps with a recorded attempt/replay policy.
Observed behavior: Handler starts the full step sequence again.
Impact: External fetches and derived-result refreshes can be repeated. Correctness depends on every
step being idempotent under repetition, but that invariant is not tested end to end.
Root cause or likely cause: Durable progress tracking was added before resume semantics and
per-step idempotency contracts were fully specified.
Recommended remediation: Add per-step checkpoint/resume semantics or make each step explicitly
idempotent with tests. Store attempt numbers and distinguish replayed work from first execution.
Acceptance criteria: Retrying after failure at each step has documented behavior and does not
duplicate external side effects, alerts, or persisted derived rows.
Regression tests required: Failure-injection tests for each full-pipeline step using PostgreSQL.
Owner profile: Backend/data engineer
Dependencies: Define replay policy for IB fetch runs and optional research engines.

ID: PH14-003
Title: Long-running pipeline steps can outlive their lease and still commit side effects
Severity: S1 High
Confidence: Strong
Affected components: `background_worker`, `pipeline_executor`, full-pipeline dependencies
Evidence: The full-pipeline wrapper heartbeats through `should_cancel`, and `execute_full_pipeline`
calls `should_cancel` between steps. Many step bodies call dependency functions that may run for a
long time without internal heartbeat or token checks. `_save_progress` commits pipeline state
independently of the job token. If a long step exceeds `job_stale_after_seconds`, another worker can
recover/reclaim the job; the old worker's final job update is fenced, but side effects already
committed by the old pipeline session are not fenced.
Reproduction steps: Configure a short lease, inject a long-running dependency that sleeps past lease
expiry, run stale recovery in another worker, and let both continue.
Expected behavior: A worker that loses its lease cannot commit pipeline side effects after lease
loss.
Observed behavior: Job-row completion is fenced, but pipeline/scoring side effects do not check the
execution token.
Impact: Two workers can interleave full-pipeline side effects for the same pipeline/run after stale
recovery.
Root cause or likely cause: Lease ownership is enforced only at the background job row, not at the
pipeline/run side-effect boundary.
Recommended remediation: Pass a lease guard into long-running steps and call it before every commit
or destructive refresh. Heartbeat during long operations and abort if token fencing fails.
Acceptance criteria: A stale old worker cannot commit pipeline progress or derived rows after a new
worker takes the lease.
Regression tests required: Two-session PostgreSQL failure-injection test with short lease and a
blocked/slow pipeline dependency.
Owner profile: Backend engineer
Dependencies: Background-job fencing API must be exposed to job handlers and pipeline dependencies.

ID: PH14-004
Title: Real PostgreSQL concurrency probes are not part of the automated test suite
Severity: S2 Medium
Confidence: Confirmed
Affected components: test suite, CI, background job service
Evidence: Existing background-job tests use fake sessions. The review's temporary PostgreSQL probes
passed for `SKIP LOCKED`, distinct two-worker claims, stale recovery, new execution tokens, and old
token rejection, but these probes are not committed tests.
Reproduction steps: Inspect `tests/test_background_job_service.py`; it uses `FakeDb` rather than a
PostgreSQL session.
Expected behavior: At-most-one active lease owner per job is protected by a real PostgreSQL
integration test.
Observed behavior: The invariant is manually probed in review but not automated.
Impact: Future changes can break the exact PostgreSQL locking behavior without failing CI.
Root cause or likely cause: Test suite favors fast fake-session tests; no PostgreSQL integration
harness exists yet.
Recommended remediation: Add Testcontainers/local PostgreSQL tests for claim contention, stale
recovery, old-token rejection, cancellation races, and duplicate enqueue races.
Acceptance criteria: CI runs at least one real PostgreSQL two-worker lease suite.
Regression tests required: New integration suite under a PostgreSQL marker.
Owner profile: Backend/test engineer
Dependencies: CI PostgreSQL service or testcontainer support.

ID: PH14-005
Title: Execution tokens are persisted in operational metadata and logs
Severity: S2 Medium
Confidence: Confirmed
Affected components: `background_job_service`, `background_jobs.operational_metadata_json`,
`app/services/ceri/observability.py`, winner processing metadata
Evidence: `_with_lease_event` stores `execution_token` in every lease event retained in
`operational_metadata_json`. `ceri/observability.py` includes `execution_token` in structured event
fields, and winner job handlers store execution token in processing metadata.
Reproduction steps: Claim/recover a job and inspect `background_jobs.operational_metadata_json`.
Expected behavior: Operational metadata identifies lease events without persisting raw active or
historical fence tokens.
Observed behavior: Raw execution tokens are stored.
Impact: Tokens are not credentials, but they are operational fencing material. Persisting and
logging them increases the blast radius of log/metadata exposure and complicates redaction policy.
Root cause or likely cause: Lease diagnostics were optimized for debugging.
Recommended remediation: Store token hashes or short suffixes instead of raw tokens, and redact
tokens from structured logs/metadata shown in UI.
Acceptance criteria: No raw execution token appears in persisted operational metadata or exported
diagnostics.
Regression tests required: Redaction tests for lease events and CERI/winner operational payloads.
Owner profile: Backend/security engineer
Dependencies: Decide diagnostic format for lease correlation.

ID: PH14-006
Title: Duplicate job coalescing is inconsistent and race-prone outside CERI processing runs
Severity: S2 Medium
Confidence: Strong
Affected components: CERI routes, winner admin routes, setup lifecycle routes, generic
`enqueue_job`, background jobs table
Evidence: CERI routes use `_enqueue_job_once`, but it scans nonterminal jobs in application code and
has no database uniqueness guarantee on job type/request key. Winner and setup lifecycle admin
routes call `enqueue_job` directly. Generic `background_jobs` has no request-key column or partial
unique index for active jobs.
Reproduction steps: Submit the same admin job concurrently from two sessions.
Expected behavior: Duplicate administrative jobs are coalesced or rejected atomically.
Observed behavior: Some CERI jobs may coalesce in a single request path; other job families enqueue
duplicates, and CERI route coalescing can race.
Impact: Duplicate rebuild/backfill/training jobs can waste resources or create duplicate artifacts
unless every handler is independently idempotent.
Root cause or likely cause: Idempotency was implemented per subsystem instead of in the job queue.
Recommended remediation: Add queue-level `request_key` and a partial unique index for active
nonterminal jobs. Make route helpers use a shared atomic enqueue/coalesce API.
Acceptance criteria: Concurrent duplicate enqueue attempts produce one active job or a deterministic
coalesced response across all job families.
Regression tests required: Two-session PostgreSQL duplicate enqueue tests.
Owner profile: Backend engineer
Dependencies: Define request-key semantics for every job type.

## Action Backlog

Immediate:

- Add full-pipeline active-run idempotency so duplicate starts cannot create concurrent work for the
  same upload run by accident.
- Add a lease guard to full-pipeline side-effect commits so old workers cannot commit after lease
  loss.
- Create real PostgreSQL concurrency tests for the background-job lease primitives that passed in
  the manual probe.

Near term:

- Define retry/resume semantics for every full-pipeline step.
- Add queue-level request keys and atomic duplicate-job coalescing.
- Redact or hash execution tokens in operational metadata and logs.
- Add failure-injection tests for retry after each pipeline step.

Structural:

- Separate web and worker topology as a first-class operating mode, with explicit guidance for
  one-worker local mode versus multi-worker test/deployment mode.
- Build a stuck-job runbook covering queued, running, stale, cancelled, partial, and failed jobs.
- Create per-job idempotency contracts and require them in code review for new handlers.

## Test Additions Proposal

- PostgreSQL test: one queued job, two concurrent claimers, assert one owner.
- PostgreSQL test: two queued jobs, two concurrent claimers, assert distinct jobs.
- PostgreSQL test: stale recovery replaces token and old worker cannot heartbeat, complete, or fail
  the job.
- PostgreSQL test: old worker cannot commit pipeline side effects after lease loss.
- PostgreSQL test: duplicate full-pipeline starts race and coalesce.
- Failure-injection matrix for `FULL_PIPELINE`: crash after claim, after step-start commit, after
  IB fetch item commit, after combined refresh, after optional capture, after final pipeline commit.
- Cancellation race tests: cancel before claim, during IB fetch, during CPU-bound scoring, and after
  terminal completion.
- Redaction tests for `operational_metadata_json`, logs, and job result payloads.

## Decision Records Needed

- DR-PH14-001: Full-pipeline rerun semantics for the same upload run.
- DR-PH14-002: Queue-level request-key and duplicate-job policy.
- DR-PH14-003: Pipeline retry policy: replay from start versus resume from checkpoints.
- DR-PH14-004: Worker topology: embedded local worker only, separate process, or multi-worker
  supported mode.
- DR-PH14-005: Lease event metadata retention and token redaction policy.

## Worker Topology Recommendation

Short term:

- Treat embedded single-worker mode as the only supported operational mode for ordinary local use.
- Keep `job_worker_enabled` on for local convenience, but document that multi-worker operation is
  under review until PH14 findings are closed.

Medium term:

- Support a separate worker process using the same queue only after real PostgreSQL lease,
  duplicate-enqueue, and stale-recovery tests are in CI.
- Keep one active full pipeline per upload run unless an explicit rerun request key is supplied.

## Phase Scorecard

| Dimension | Rating | Rationale |
|---|---|---|
| Row-level lease claiming | Green | Real PostgreSQL probe passed `SKIP LOCKED` contention |
| Execution-token fencing | Green/Amber | Job-row fencing works; side-effect fencing is incomplete |
| Stale recovery | Green/Amber | Stale job token replacement works; old pipeline side effects need guarding |
| Pipeline retry idempotency | Red | Retries restart from the beginning after committed steps |
| Duplicate enqueue protection | Amber/Red | Full pipeline lacks coalescing; CERI coalescing is not DB-atomic |
| Cancellation | Amber | Cooperative cancellation exists but depends on handler checkpoints |
| Worker shutdown | Amber | Stop event and 5-second join exist; long handlers may continue past shutdown window |
| Operational metadata redaction | Amber/Red | Raw execution tokens are persisted/logged |
| Automated concurrency tests | Amber/Red | Manual PostgreSQL probe passed; CI coverage is missing |

## Exit Report

Passed checks:

- Focused Phase 14 tests passed: `72 passed`.
- Temporary PostgreSQL probes confirmed row-level `SKIP LOCKED` behavior, distinct two-worker
  claims, stale recovery, new token assignment, and old-token rejection.
- Worker lifespan starts/stops the embedded worker when configured.
- Background-job retry, cancellation, and lease-event retention behavior are covered by unit tests.

Failed checks:

- Duplicate full-pipeline starts are possible for the same upload run.
- Full-pipeline retry/resume semantics are not safe enough for multi-worker confidence.
- Pipeline side effects are not fenced against old workers after lease loss.
- Real PostgreSQL concurrency probes are not automated.
- Raw execution tokens are retained in operational metadata/log paths.

Deferred items:

- Two-worker full-pipeline failure-injection test with real PostgreSQL.
- End-to-end retry idempotency tests for CERI, SLSE, OWPE, IB fetch, and full pipeline.
- Performance/fairness proof that one long job cannot monopolize a worker indefinitely.
- A complete stuck-job diagnostic and operator runbook.

Phase 14 status: row-level lease primitives are promising and passed direct PostgreSQL probes, but
the durable pipeline is not yet proven safe for retries, duplicate starts, or multi-worker recovery.
Keep production posture to local single-worker mode until full-pipeline idempotency, lease-guarded
side effects, and automated PostgreSQL concurrency tests are in place.
