# SwingLens Calculation Lineage Registry — Task 01

## Pipeline Orchestration and Execution Lineage

This report is a non-destructive forensic audit of how an uploaded SwingLens run becomes calculated artifacts, how execution identity and time context move through the durable job system, and which work can outlive or bypass a pipeline. It records current behavior; it does not approve that behavior as the intended contract and does not remediate findings.

## Repository Identity

| Field | Value | Status |
|---|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` | VERIFIED |
| Branch | `codex/winner-evidence-remediation` | VERIFIED |
| HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Worktree before audit | Untracked `scripts/ops/ib_historical_probe.py`; untracked `tests/ops/test_ib_historical_probe.py` | VERIFIED; pre-existing and untouched |
| Audit change | This file only | VERIFIED |
| Repository migration head | `0074_ceri_evidence_quarantine` | VERIFIED by `alembic heads` |
| Database migration revision | Unknown: `alembic current` was rejected by the database safety-context gate; the gate was not bypassed | UNKNOWN |
| Python | 3.12.2 | VERIFIED |
| Audit timestamp | 2026-09-14T00:28:48.7577804+02:00 | VERIFIED |

## Executive Assessment

The intended production path is a durable `FULL_PIPELINE` job with a persisted `PipelineRun`, ordered `PipelineStep` records, and one frozen `MarketCalculationContext`. Queue claims and job-state updates are fenced by worker instance, lease, and a per-claim execution token. Pipeline stage transactions also invoke the lease guard before commit. These are material correctness controls. **Status: VERIFIED.**

The boundary is not fully closed:

1. `CERI_PROVIDER_INGEST` is an enqueue stage, not a completion barrier. The parent pipeline can run Setup and Winner capture and finalize while its CERI child graph remains queued or running. Child failures do not roll up into the already-finalized pipeline result. **Status: VERIFIED; contract risk P1.**
2. A CERI-resumed pipeline with Setup enabled calls Setup evaluation without the required frozen cutoff and pipeline ID. The call raises `TypeError` before evaluation. **Status: VERIFIED; current implementation defect P1.**
3. The non-durable route remains executable and performs only a subset of the canonical pipeline without a `PipelineRun`, frozen context, or full stage set. Certification forbids it, but normal runtime configuration can enable it. **Status: LEGACY and VERIFIED; contract risk P2.**
4. Durable maintenance/manual jobs intentionally survive pipelines and can mutate shared price evidence, CERI state, Setup/lifecycle state, or Winner historical state. Identity and temporal fences differ by subsystem, so “pipeline completed” is not a global immutability boundary. **Status: PARTIALLY_VERIFIED; risk P2.**

No production pipeline, provider ingest, maturation, cohort refresh, rescore, backfill, job enqueue, migration, or production-data write was performed.

## Intended Contract Versus Current Implementation

### Intended contract

The documented production topology is `supervisor-root-v1`: one supervisor owns a web process and a separate durable worker process. The web process creates durable work; it does not execute broker or pipeline work. A pipeline freezes its market/session context at enqueue, each child retains the owning run/context, and calculation outputs remain attributable to a run, pipeline, stage, configuration, and cutoff. Certification adds a single-worker requirement, queue isolation, schema and IB readiness, transition preflight, evidence fingerprints, and disables unrelated automatic work. **Status: VERIFIED from architecture documents, settings validation, pre-enqueue gate, and tests.**

The isolation objective inferred from those controls is:

> A job may survive a process restart, but its calculation identity and temporal inputs must not drift. Work belonging to an older run, pipeline, generation, or cutoff must not be mistaken for, overwrite, or silently influence the artifacts of a newer execution.

**Status: INFERRED from the temporal-invariant and runtime-ownership documents.**

### Current implementation

- The canonical route creates a `PipelineRun`, a single persisted market calculation context, ordered steps, and one `FULL_PIPELINE` job. Job payload fields repeat the context ID, cutoff, completed session, calendar version, and bar-readiness version; the worker validates them against the persisted context before execution. **Status: VERIFIED.**
- A pipeline stage commits its `RUNNING` state before service execution and commits completion/failure afterward. On replay, a stale `RUNNING` attempt is archived as interrupted. **Status: VERIFIED.**
- The executor runs core calculation stages serially inside the parent job. Market-data fetch is synchronous in that job, not an `IB_FETCH` child. **Status: VERIFIED.**
- When provider CERI is enabled, the CERI stage enqueues a child DAG and returns immediately. The remaining parent stages do not wait on that DAG. **Status: VERIFIED and inconsistent with treating pipeline completion as completion of all planned calculation work.**
- Some standalone services obtain the newest market context for an upload run rather than an explicitly named pipeline context. If a run has multiple pipeline contexts, selection is “highest context ID,” not “context belonging to the caller.” **Status: VERIFIED.**
- Startup recovery can requeue stale or abandoned durable work; worker-loop scheduling can create due Winner maturation work. Thus a restart is deliberately not a calculation boundary. **Status: VERIFIED.**

## Actual Entry Path and Execution Lineage

### Before pipeline

`POST /uploads` calls `create_upload_run`. The upload service validates and stores the CSV, creates `UploadRun` and `RawCompanyRow` records, maps input rows, and also calculates and persists fundamental scores before any pipeline exists. The later pipeline recalculates fundamentals. Upload therefore contains a material pre-pipeline calculation/write path rather than being input persistence only. **Status: VERIFIED** (`app/routers/upload_routes.py:54-81`, `app/services/upload_service.py:42-103`).

Important identities at this point:

- `UploadRun.id` is the durable run identity and becomes `BackgroundJob.related_run_id`. **VERIFIED.**
- No `PipelineRun`, pipeline step, market calculation context, or pipeline job exists yet. **VERIFIED.**
- Upload-time fundamental output is run-bound but not pipeline-bound or market-session-bound. **VERIFIED.**

### Pipeline creation and plan

`POST /runs/{run_id}/pipeline` performs route-level IB and live-worker checks, calls `start_pipeline`, and commits. `start_pipeline` validates the upload run and execution policy, optionally requires a successful transition preflight, derives the step list, coalesces an existing authoritative pipeline or active request-key job, creates `PipelineRun`, creates one `MarketCalculationContext`, persists the planned `PipelineStep` rows, enqueues `FULL_PIPELINE`, and requests preemption of active prewarm jobs. **Status: VERIFIED** (`app/routers/run_routes.py:627-705`, `app/services/pipeline_service.py:118-365`).

The durable job carries:

- `related_run_id = UploadRun.id`;
- `pipeline_run_id`;
- `market_calculation_context_id`;
- `market_cutoff_at`;
- `input_as_of_session`;
- market calendar and bar-readiness versions;
- transition-preflight identity/fingerprint when applicable;
- a request key derived from run, policy, and planned steps.

**Status: VERIFIED.** The enqueue service itself does not generically prove that `related_run_id`, payload run IDs, pipeline ownership, and context ownership agree; pipeline execution performs the context checks for `FULL_PIPELINE`, while other job families validate different subsets. **Status: PARTIALLY_VERIFIED.**

### Actual execution DAG

```text
Upload request
  └─ UploadRun + RawCompanyRows
      └─ upload-time fundamental calculation/write                     [before pipeline]

POST /runs/{run_id}/pipeline
  ├─ optional transition preflight                                     [before enqueue]
  ├─ PipelineRun
  ├─ MarketCalculationContext (one per pipeline; frozen at enqueue)
  ├─ ordered PipelineStep rows
  └─ FULL_PIPELINE BackgroundJob                                       [durable parent]
      ├─ worker claim → worker/instance/lease/execution_token
      ├─ validate persisted context against job payload
      ├─ VALIDATING_RUN
      ├─ SCORING_FUNDAMENTALS
      ├─ FETCHING_MARKET_DATA
      │   └─ FetchPlan → synchronous IB/cache execution → IBFetchRun + PriceBars
      ├─ SCORING_TECHNICALS → TechnicalScore
      ├─ MARKET_REGIME_SNAPSHOT → MarketRegimeSnapshot
      ├─ COMBINING_RESULTS → CombinedResult
      ├─ RANKING_PROFILES → RankingSnapshot/profile rows
      ├─ SECTOR_ROTATION_SNAPSHOT → SectorRotationSnapshot
      ├─ one of:
      │   ├─ CERI_CAPTURE_SNAPSHOT → run-scoped CERI snapshots          [synchronous]
      │   └─ CERI_PROVIDER_INGEST → enqueue CERI workflow jobs          [asynchronous]
      │       ├─ provider ingest batches
      │       ├─ normalization batches          (barrier on provider)
      │       ├─ feature batches                (barrier on normalization)
      │       ├─ finalizer                      (barrier on features)
      │       └─ CERI_CAPTURE_RUN
      │           └─ change detection → alert work
      ├─ FREEZING_DECISION_HANDOFF_MANIFEST                             [policy-dependent]
      ├─ CAPTURING_SETUP_SIGNALS                                        [feature-dependent]
      ├─ EVALUATING_SETUP_LIFECYCLES                                    [feature-dependent]
      ├─ CAPTURING_WINNER_PREDICTIONS
      └─ finalize PipelineRun and FULL_PIPELINE job

Independent or post-pipeline durable work
  ├─ IB_FETCH and MARKET_DATA_PREWARM → shared PriceBars/IB fetch records
  ├─ SEC_READINESS_REPAIR → may enqueue a resume of the same pipeline
  ├─ Setup evaluate/replay/repair/daily-maintenance/alert rebuild
  ├─ Winner maturation → revision checks and optional cohort refresh
  ├─ Winner latest rescore → bounded slices against a frozen target set/generation
  └─ Winner historical backfill → explicitly selected historical runs
```

**Graph status: VERIFIED for nodes and ordering; artifact names are summarized at table/family level rather than an exhaustive schema catalog.**

### Non-linear dependencies

- Market regime, sector rotation, technical scoring, Setup capture, and normal Setup evaluation receive the frozen market cutoff explicitly. **VERIFIED.**
- Combined scoring and rankings depend on previously persisted run artifacts, not direct function-return handoff alone. **VERIFIED.**
- CERI provider batches are a barriered DAG among themselves, but the parent `FULL_PIPELINE` has no reverse barrier on their terminal state. **VERIFIED.**
- Winner prediction capture is invoked synchronously by the parent pipeline, not as `WINNER_PREDICTION_CAPTURE`; it relies on persisted decision evidence/manifest logic rather than receiving market cutoff and pipeline ID in the executor call. **PARTIALLY_VERIFIED; detailed evidence selection is Task 06 scope.**
- A CERI failure can lead to `SEC_READINESS_REPAIR`, which queues a resume of the same pipeline and reuses the original persisted market context. **VERIFIED.**

## Pipeline Step Register

Rows are in actual execution order. Optional steps are marked explicitly.

| Step | Purpose | Trigger | Important inputs | Important outputs | Run-bound | Session-bound | Async work | Child jobs | Can mutate historical state | Failure semantics | Implementation | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `VALIDATING_RUN` | Establish usable ticker universe and provider preconditions | `FULL_PIPELINE` start | Raw rows, tickers, settings/policy, optional CERI preflight | Validation counts/diagnostics | Yes | No direct market session | No | Possible SEC repair is scheduled from blocked handling, not this service body | No material calculation output | Empty/invalid input fails; provider readiness can block the pipeline | `pipeline_executor.py:309-323` | VERIFIED |
| `SCORING_FUNDAMENTALS` | Recalculate run fundamentals | Prior step success | Raw upload fields, scoring configuration | Fundamental-score rows | Yes | No | No | None | Yes, when rerunning an existing run | Exception fails pipeline; prior upload-time values are replaced/recomputed | `pipeline_executor.py:325-329`; `fundamental_score_service.py` | VERIFIED |
| `FETCHING_MARKET_DATA` | Plan/reuse/fetch bars for run universe and benchmarks | Fundamentals success | Tickers, settings, IB health, cache coverage, prewarm evidence | `IBFetchRun`, shared `PriceBar` rows, fetch metrics | Run on fetch record; bars are shared | Plan is evaluated under pipeline execution; downstream projection uses frozen cutoff | Synchronous inside parent | None | Yes, shared market data may cover historical dates | Can use cache fallback; broker/fetch failure fails or degrades according to executor result | `pipeline_executor.py:332-466` | VERIFIED |
| `SCORING_TECHNICALS` | Produce technical features/scores | Market data success | Price bars projected as of frozen cutoff, tickers, technical configs | `TechnicalScore` rows | Yes | Yes | Internal overlap/process parallelism, not durable child jobs | None | Yes, rerun overwrites/rebuilds run rows | Errors can produce degraded/partial outcome; cancellation/lease checked during work | `pipeline_executor.py:468-556` | VERIFIED |
| `MARKET_REGIME_SNAPSHOT` | Capture run market-regime interpretation | Technical completion | Frozen cutoff, benchmark bars/config | Market-regime snapshot | Yes | Yes | No | None | Yes | Failure contributes to partial/failure result | `pipeline_executor.py:558-574` | VERIFIED |
| `COMBINING_RESULTS` | Combine persisted score families | Regime completion | Fundamental, technical, regime and other run artifacts/config | Combined results | Yes | Indirect through inputs | No | None | Yes | Failure aborts; zero combined rows makes final pipeline failed | `pipeline_executor.py:576-583`, `pipeline_executor.py:1604-1619` | VERIFIED |
| `RANKING_PROFILES` | Generate configured ranking outputs | Combined completion | Combined results, ranking profiles/config | Ranking rows/snapshots | Yes | Indirect through inputs | No | None | Yes | Missing/failed rankings contribute to incomplete/partial result | `pipeline_executor.py:585-619` | VERIFIED |
| `SECTOR_ROTATION_SNAPSHOT` | Capture sector-rotation state | Ranking completion | Frozen cutoff, price/run evidence, configuration | Sector rotation snapshot | Yes | Yes | No | None | Yes | Failure contributes to partial result | `pipeline_executor.py:620-637` | VERIFIED |
| `CERI_CAPTURE_SNAPSHOT` | Capture run-scoped CERI evidence from available PIT state | Sector completion when provider-ingest mode is off | Run tickers, shared CERI evidence, frozen cutoff | CERI run snapshots | Yes | Yes | No | None | Yes, rerun can change run snapshot | Failure contributes to partial result | `pipeline_executor.py:654-665` | VERIFIED |
| `CERI_PROVIDER_INGEST` | Schedule provider ingestion/normalization/features/capture | Sector completion when provider-ingest mode is on; also sole supported resume point | Run, CERI config/workflow key, frozen context | Count of jobs enqueued; eventual shared/run CERI artifacts | Yes via `related_run_id`/workflow key | Yes, context fields propagated to child payloads | Yes; stage does not wait | Provider, normalize, feature, finalizer, capture, change/alert graph | Yes | Enqueue success marks step complete; later child failure is not rolled up | `pipeline_executor.py:639-653`, `823-881`; `ceri/batched_workflow.py:91-320` | VERIFIED; P1 finding |
| `FREEZING_DECISION_HANDOFF_MANIFEST` | Freeze cross-subsystem decision evidence for certification/policy path | After CERI stage, before Setup/Winner | Pipeline/run/context, preflight/evidence fingerprints | Decision handoff manifest | Yes | Yes | No | None | Yes, creates a pipeline-specific manifest | Missing/incompatible evidence fails the guarded path | `pipeline_executor.py:667-693`; `models/tables.py:1844-1877` | VERIFIED |
| `CAPTURING_SETUP_SIGNALS` | Capture Setup input signals | Feature/policy enabled | Run evidence and frozen market cutoff | Setup signal capture | Yes | Yes | No | None | Yes | Failure contributes to partial result | `pipeline_executor.py:695-713` | VERIFIED |
| `EVALUATING_SETUP_LIFECYCLES` | Evaluate lifecycle state | Setup capture success | Capture handoff, frozen cutoff, pipeline ID, lifecycle config/history | Lifecycle state/evaluation/alerts | Yes | Yes on normal path | No | None | Yes, including state transitions | Normal path is guarded; resumed CERI path raises before evaluation because required args are omitted | `pipeline_executor.py:714-731`, `911-919`, `2061-2086` | VERIFIED; P1 defect on resume |
| `CAPTURING_WINNER_PREDICTIONS` | Freeze prediction records/evidence for the run | Last enabled upstream step | Run decision artifacts, Winner configuration/manifest selection | Winner predictions/evidence | Yes | Decision-session semantics are internal to Winner service | Synchronous in parent | None at capture | Yes, capture route/rerun may write run predictions subject to service guards | Failure contributes to partial result; executor call does not explicitly carry cutoff/pipeline ID | `pipeline_executor.py:733-763` | PARTIALLY_VERIFIED |
| Pipeline finalization | Derive terminal status and persist result/performance | All parent stages return | Stage counts, warnings, degradation/failure fields | Terminal `PipelineRun`, terminal parent job | Yes | Reuses original context | Does not await CERI child graph | None | No additional domain artifact beyond status/result | Failed if combined count is zero; partial on configured warning/incomplete fields | `pipeline_executor.py:764-820`, `1604-1619` | VERIFIED |

## Job Architecture

### Generic durable-job contract

- Enqueue coalesces active work by workflow-stage or request key, stores causal correlation, and inherits current job causality for child enqueues. `parent_job_id`/`triggered_by_job_id` can be inferred from worker scope even when the caller omits them. **VERIFIED** (`background_job_service.py:80-248`, `observability/correlation.py:107-163`).
- Queue selection uses row locking with `FOR UPDATE SKIP LOCKED`, assigns worker ID/instance, lease timestamps, and a new random execution token. **VERIFIED** (`background_job_service.py:702-803`).
- Progress, completion, partial, cancellation, and retry transitions require the current `RUNNING` row and matching execution token. A superseded worker cannot update the job row. **VERIFIED** (`background_job_service.py:806-899`, `1656-1686`; `tests/test_background_job_service.py:522-560`).
- Normal failures retry with delays 60, 180, and 600 seconds up to each job's `max_retries`; designated deterministic provenance failures do not retry. Barrier deferral does not consume retry budget. **VERIFIED** (`background_job_service.py:1283-1369`, `1553-1555`; `tests/test_background_worker.py:182-227`).
- Stale/abandoned recovery clears the old execution token and worker identity, then requeues while `retry_count < max_retries`; it does not increment `retry_count`. Therefore repeated lease loss can produce unbounded recoveries of the same job. **VERIFIED** (`background_job_service.py:1454-1550`).
- Calculation-table writes are not generically stamped with the job execution token. The parent pipeline mitigates this by common transaction boundaries and `lease_guard` before commits, but an exhaustive proof for services that commit internally is outside Task 01. **PARTIALLY_VERIFIED.**

### Important Job Type Register

| Job type | Creator | Parent | Children | Run binding | Pipeline binding | Session binding | Retry | Continuation | Mutation surface | Historical reach | Risk | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `FULL_PIPELINE` | Pipeline API/service | HTTP causal root | CERI DAG; SEC repair/requeue paths | `related_run_id` + payload | `pipeline_run_id` + validated context | Persisted cutoff/session/calendar/readiness | Generic max-retry policy; resume normally creates a new keyed job | Only CERI resume supported; reuses same pipeline/context | All core run calculations and some shared market/CERI data | Can rerun an older pipeline after restart | High | VERIFIED |
| `WORKER_RECOVERY_PROBE` | Operational probe | None | None | No | No | No | Generic | None | Operational proof row/result only | None | Low | VERIFIED |
| `IB_FETCH` | Run/IB routes and retry/resume service | Usually HTTP; causality if created by job | None | Payload and `related_run_id`; `IBFetchRun.run_id` | No | No frozen plan/session; plan rebuilt at execution | Max 3 | Resume/retry creates a new fetch run/job | Shared `PriceBar`, IB fetch run/items | Yes; delayed job can fetch/write later | High | VERIFIED |
| `MARKET_DATA_PREWARM` | Scheduler/admin prewarm path | None or causal caller | None | No single run; frozen ticker universe | No; foreground pipeline requests preemption | Payload records effective/freshness session, but execute rebuilds plan without enforcing it | Generic; cooperative preemption/deferral | Same job may defer/resume | Shared `PriceBar`, fetch records/cache coverage | Yes | High | VERIFIED |
| `SEC_READINESS_REPAIR` | Blocked pipeline handling | `FULL_PIPELINE` causality | New `FULL_PIPELINE` resume job | Run in payload/related identity | Same pipeline ID | Original context copied to resumed job | Generic | Enqueues pipeline at CERI checkpoint after repair | SEC readiness/evidence plus orchestration state | Yes | High | VERIFIED |
| CERI provider batch jobs | `CERI_PROVIDER_INGEST` scheduler | Parent pipeline causality; batch jobs are sibling stages linked by workflow/barriers | No direct children until finalizer | Run ID + workflow key `ceri:pipeline:{run}:{config_hash}` | Indirect through `calculation_context_id`; pipeline ID not the primary workflow key | Cutoff/session/calendar/context propagated | Generic; barriers defer without retry | Batch partition semantics | Shared provider evidence/ingestion state | Yes | High | VERIFIED |
| CERI normalize batch jobs | Batched workflow | Same causal root | None | Run/workflow bound | Indirect context | Propagated temporal context | Generic/barrier deferral | Waits for provider terminal state | Normalized evidence | Yes | High | VERIFIED |
| CERI feature batch jobs | Batched workflow | Same causal root | None | Run/workflow bound | Indirect context | Propagated temporal context | Generic/barrier deferral | Waits for normalization terminal state | Derived CERI features | Yes | High | VERIFIED |
| CERI finalizer / `CERI_CAPTURE_RUN` / change-alert chain | Batched workflow/finalizer/capture handler | Causal chain retained | Capture then change/alert jobs | Run-bound | Indirect context; no parent-pipeline completion barrier | Temporal fields forwarded | Generic | Child creation after upstream barriers | Run CERI snapshots, changes, alerts | Yes, after parent pipeline completion | High | VERIFIED |
| Setup run evaluation | Setup API | HTTP | None | Payload run ID + `related_run_id` | No for manual job | No frozen cutoff in manual payload; service falls back to latest run context/current standalone semantics | Generic | None | Setup/lifecycle rows and alerts | Yes | High | VERIFIED |
| Setup replay/repair/daily maintenance/alert rebuild | Setup APIs/schedulers | HTTP or scheduler | Varies by handler | Run/ticker/date scope varies | No | Explicit dates for replay/maintenance vary; not a pipeline context contract | Generic | Handler-specific | Historical lifecycle/evaluations/alerts | Explicitly historical | High | PARTIALLY_VERIFIED |
| `WINNER_PREDICTION_CAPTURE` | Winner API/job path | HTTP/causal caller | None | Run-bound | Not inherently | Winner decision evidence/session rules | Generic | None | Prediction/evidence rows | Can capture an older run | High | VERIFIED; pipeline uses direct service call instead |
| `WINNER_OUTCOME_MATURATION` | Worker-loop scheduler or API | Scheduler/HTTP | Revision checks; optional cohort refresh | Global (`run_id=None`) over due predictions | No | Scheduler key uses latest completed session | Generic; deterministic zero-progress/limit handling | Bounded child continuation with root/parent/depth | Historical prediction outcomes | Explicitly cross-run | High | VERIFIED |
| `WINNER_OUTCOME_REVISION_CHECK` | Maturation | Maturation causal parent | Optional follow-on work | Prediction/run through targets | No | Outcome/revision session logic | Generic | Bounded workflow | Historical outcome revisions | Explicitly historical | High | PARTIALLY_VERIFIED |
| `WINNER_COHORT_REFRESH` | API or maturation | Maturation when automatic refresh enabled | Latest rescore may follow by workflow | Cohort definition/watermark, not one run | No | Desired watermark/session in plan | Generic | Same job defers across slices; generation/manifest guards | Cohort members/aggregates/generation publication | Cross-run by design | High | VERIFIED |
| `WINNER_LATEST_RESCORE` | Winner workflow/API | Cohort/publication workflow | None | Frozen target IDs span runs | No | Payload/generation target set | Generic | Same job slices/defers | Winner estimates for selected predictions | Explicitly historical | High | VERIFIED |
| `WINNER_HISTORICAL_BACKFILL` | Winner admin/API | HTTP | None | Explicit/trusted run IDs | No | Backfill scope supplies historical identity | Generic | Handler-specific bounded batches | Historical prediction/evidence rows | Explicitly historical | High | VERIFIED |
| `WINNER_MODEL_TRAINING`, `WINNER_SIMILARITY_CACHE` | Constants declared | None established | None established | Unknown | No | Unknown | Unknown | Unknown | No registered default handler found | Unknown | Medium | DEAD_CODE in current default worker registry |

The default worker handler registry is authoritative for currently executable job types in this audit (`background_worker.py:617-641`). Declared Winner constants without registered handlers are classified `DEAD_CODE` only with respect to the default worker path; an unsearched plugin or external worker could change that classification. **Status: PARTIALLY_VERIFIED caveat.**

## Worker, Startup, and Shutdown

### Worker and supervisor

The durable worker registers an exact worker identity including instance and process metadata, starts heartbeats, logs migration/Git/configuration metadata, recovers abandoned work for its worker identity, and then loops. Each loop can recover stale work (outside certification), schedule due Winner maturation, claim one queue-eligible job, heartbeat its lease, execute the registered handler, and write a token-fenced terminal transition. **Status: VERIFIED** (`background_worker.py:84-320`, `390-574`).

Queue ownership is split into interactive (`FULL_PIPELINE`), broker (`IB_*`, `MARKET_DATA_PREWARM`), and background queues. Priority and optional fairness affect ordering. An older queued job can therefore execute during the lifetime of a newer upload or pipeline; queue age alone is not a rejection criterion. **Status: VERIFIED** (`background_queue.py`).

The canonical supervisor owns web and worker child processes, observes exact worker registration/PID/start time, fences stalled worker jobs, restarts within configured budgets/backoff, and retires stale registrations. A parent watchdog stops children if their exact parent process is lost. Worker identity materially protects claim ownership and recovery correctness, but it does not become business provenance on every calculation row. **Status: VERIFIED** (`worker_supervisor.py:64-385`, `worker_registry.py`, `parent_watchdog.py`, `worker.py`).

FastAPI lifespan does not start an embedded worker. Settings forbid an embedded durable worker and require a worker process for durable mode; certification further forbids non-durable pipeline execution and unrelated automatic work. **Status: VERIFIED** (`main.py`, `settings.py:480-523`, `tests/test_app_lifespan_worker.py`).

### Startup effects

| Startup/loop behavior | Can create/requeue/mutate work? | Assessment |
|---|---|---|
| FastAPI lifespan | Starts monitoring only; no job creation or calculation execution identified | VERIFIED |
| Worker registration/start | Can recover abandoned jobs for the same worker identity | VERIFIED |
| Each normal worker loop | Can recover expired/stalled jobs and can schedule due Winner maturation | VERIFIED |
| Supervisor recovery | Can fence/requeue jobs owned by a dead or frozen worker instance | VERIFIED |
| Startup preflight | Checks DB/migration/storage; no business calculation job creation found | VERIFIED |
| Legacy queued pipeline without context | Creates/fixes its context on first execution, freezing time at execution rather than original enqueue | LEGACY and VERIFIED |

Jobs created before restart can execute afterward by design. Correctness depends on immutable payload/context validation and handler idempotency, not on process lifetime. **Status: VERIFIED.**

### Shutdown and partial-write behavior

The canonical lifecycle stop requests a database-backed claim fence, verifies exact worker identity, waits for acknowledgement, and treats `RUNNING`/`RECOVERING` jobs as shutdown blockers. It does not intentionally hand an in-flight job to another worker. The supervisor stops web before worker. **Status: VERIFIED** (`lifecycle_quiesce.py`, `scripts/ops/lifecycle_probe.py:670-765`, `scripts/ops/SwingLensLifecycle.psm1:422-496`, `tests/ops/test_worker_quiesce.py`).

Unexpected worker loss is different: an expired/stalled job is fenced, its token is cleared, and it can be requeued. Pipeline steps persist boundaries around each stage; a crash can leave a `RUNNING` step, whose next attempt is archived as interrupted before replay. The generic job token prevents the old worker from later completing the job record. **Status: VERIFIED.**

Whether every calculation service is free of internal commits after lease loss is not proven by Task 01. Calculation tables generally lack an execution-token column, so the final domain-write fence is transactional discipline and idempotent/upsert constraints in each writer. **Status: PARTIALLY_VERIFIED.**

## Historical Contamination Analysis

### Cross-Run Risk Register

| Component | Can survive run | Can write after run | Identity fence | Temporal fence | Risk | Evidence |
|---|---|---|---|---|---|---|
| `FULL_PIPELINE` queued/retried job | Yes | Yes | Run, pipeline, context ID; active request-key coalescing; execution token | Persisted context validated against payload | Medium | `pipeline_service.py:118-365`; `background_worker.py:688-704` — VERIFIED |
| CERI provider child graph | Yes | Yes | Run/workflow/config hash, causal parent, context ID | Cutoff/session/calendar fields copied to every stage | High: not awaited by parent; failures do not roll up | `ceri/batched_workflow.py:91-320`; `ceri/batched_job_handlers.py:436-517`; `pipeline_executor.py:639-653` — VERIFIED |
| Resumed pipeline | Yes | Yes | Same pipeline ID; new request key; original context payload | Original persisted cutoff/session reused | High when Setup enabled because required context args are omitted | `pipeline_service.py:473-598`; `pipeline_executor.py:823-987` — VERIFIED |
| Stale/abandoned job | Yes | Yes after reclaim | Old token invalidated; new claim gets new token | Payload retained unchanged, but handler decides what it honors | High for handlers that re-plan; recovery count is not a retry bound | `background_job_service.py:1454-1550`; tests at 426-560 — VERIFIED |
| Manual `IB_FETCH` | Yes | Yes | Fetch run ID, run ID, request key, execution token | No frozen fetch plan/session; plan rebuilt on execution | High | `ib_fetch_job_service.py:65-88`, `209-222` — VERIFIED |
| `MARKET_DATA_PREWARM` | Yes | Yes | Universe/config fingerprints and request key; preempted by foreground pipeline | Effective session recorded but not supplied to execution-time plan builder | High | `market_data_prewarm_service.py:186-255` — VERIFIED |
| Shared `PriceBar` store | Yes | Yes | Symbol/bar identity and repository constraints; no pipeline owner | Readers can project as of cutoff, but writers are not pipeline-scoped | High shared influence | Pipeline/IB/prewarm services — PARTIALLY_VERIFIED |
| Standalone technical scoring | N/A; invoked by route/service | Yes, for an old run | Run ID only | Falls back to newest context for run, else standalone current context | High if multiple pipeline contexts exist | `technical_score_service.py:111-118`, `329-336`, `464-470`; context service 136-150 — VERIFIED |
| Setup source loader/manual evaluation | Job can survive | Yes | Run ID; manual job not pipeline-bound | Newest run context or standalone cutoff; pipeline enforcement only when pipeline ID is supplied | High | `setup_lifecycle/source_loader.py:106-115`; `setup_lifecycle_routes.py:657-697`; evaluation service — VERIFIED |
| Winner maturation/revision | Yes | Yes | Prediction IDs, causal root/parent, bounded continuation depth | Latest completed-session scheduler and outcome eligibility rules | High by design; historical artifacts intentionally mutable | `winner_probability/scheduler.py:18-77`; `winner_probability/job_handlers.py:394-640` — VERIFIED |
| Winner cohort/rescore generation | Yes | Yes | Definition, desired watermark, generation/manifest, frozen target IDs | Watermark/session/generation controls | High but guarded publication path | Winner job handlers/services — PARTIALLY_VERIFIED |
| Winner historical backfill | Yes | Yes | Explicit/trusted run IDs and job token | Backfill parameters; detailed cutoff proof is Task 06 | High by design | `winner_probability/job_handlers.py` — PARTIALLY_VERIFIED |
| Non-durable pipeline route | No durable job | Writes immediately | Run ID only | No persisted pipeline context | High; bypasses canonical stage set | `run_routes.py:706-772`; `settings.py:480-523` — LEGACY/VERIFIED |
| Upload-time fundamentals | Before pipeline exists | Yes | Run ID | Not market-session dependent | Medium: duplicates a later pipeline writer | `upload_service.py:42-103` — VERIFIED |

### Fences that prevent or limit contamination

1. **Run and pipeline identity:** core run artifacts carry `run_id`; `PipelineStep` carries `pipeline_run_id`; the parent job carries both run and pipeline identities. **VERIFIED.**
2. **Frozen temporal context:** one `MarketCalculationContext` per pipeline, with cutoff, completed session, daily-bar readiness, calendar version, and readiness version. The worker fails closed on payload/context mismatch. **VERIFIED** (`models/tables.py:1752-1784`, `market_calculation_context_service.py:153-304`).
3. **Job single-flight:** partial unique indexes and enqueue lookups prevent duplicate active workflow stages/request keys, including a special active Winner-maturation workflow. **VERIFIED** (`models/tables.py:1419-1625`, `background_job_service.py:80-248`).
4. **Lease ownership:** row-lock claim, worker generation/instance, heartbeats, expiration, and an execution token fence terminal/progress job mutations. **VERIFIED.**
5. **Pipeline transaction boundaries:** stage status and attempt history are persisted; lease guard runs before pipeline commits. **VERIFIED** (`pipeline_executor.py:1079-1254`, `1437-1443`).
6. **CERI internal barriers:** normalize waits for provider terminal state, feature waits for normalization, finalizer waits for feature completion; temporal payload is propagated through capture/change/alert descendants. **VERIFIED.**
7. **Generation/manifest controls:** Winner cohort/rescore paths retain target/generation identity and avoid unbounded continuation. **PARTIALLY_VERIFIED in Task 01.**
8. **Certification isolation:** requires durable mode, live worker/queue isolation/schema/IB/evidence preconditions and disables unrelated automatic work. **VERIFIED** (`pre_enqueue_operational_gate.py:102-238`, `settings.py:502-523`).

### Boundary weaknesses

- `BackgroundJob.related_run_id` is not a database foreign key to `UploadRun`; payload-to-column identity equality is not a generic enqueue invariant. **VERIFIED** (`models/tables.py:1419-1625`).
- Parent/child lineage uses both generic causal columns (`parent_job_id`, `triggered_by_job_id`, `root_correlation_id`) and Winner-specific `root_job_id`/continuation fields. A job can retain causal lineage while `root_job_id` remains null. **VERIFIED.**
- `root_job_id` and `parent_job_id` use `ON DELETE SET NULL`, so database deletion can remove direct lineage while correlation metadata remains. **VERIFIED.**
- Multiple terminal pipelines may exist for one upload run. `market_context_for_upload_run` selects the newest context by ID, so a standalone recalculation can silently bind to a different pipeline than the artifacts it is replacing. **VERIFIED.**
- Pipeline resume checkpoint validation counts distinct tickers and required snapshot presence but does not hash/fingerprint the content of completed stages. Mutated-but-count-equivalent upstream rows pass the checkpoint. **VERIFIED** (`pipeline_executor.py:989-1069`).

## Pipeline Isolation Contract

### Allowed influences

- The owning `UploadRun` and its raw rows.
- The owning `PipelineRun` plan and persisted steps.
- The single persisted `MarketCalculationContext` for that pipeline.
- Configuration explicitly loaded for the execution and, where implemented, its version/hash.
- Shared external/PIT evidence only when projected to the frozen cutoff/session and compatible provenance.
- Historical state explicitly required by a subsystem and selected under that subsystem's version, generation, and cutoff rules.
- Child and continuation jobs that retain the original run, workflow, context, and causal identity.

**Status: INFERRED intended contract, with enforcement varying by subsystem.**

### What must never cross between pipelines/runs

- Another run's raw rows, score rows, ranking rows, Setup state, Winner predictions, or run-scoped snapshots.
- Another pipeline's calculation context, transition preflight, decision handoff, step state, or completion result.
- Price/evidence newer than the owning pipeline cutoff when reconstructing decision-time output.
- A later provider revision unless the calculation explicitly models revisions and records the revision identity.
- A stale worker's writes after its execution token/lease has been superseded.
- Children from an obsolete generation/workflow publishing into the current generation.
- A maintenance/backfill job rewriting frozen decision-time evidence without explicit target identity and audit trail.
- A retry or restart rebuilding “now” semantics when the original operation promised frozen enqueue-time semantics.

**Status: INFERRED intended invariant. Current implementation enforces the first two strongly on the canonical parent path, temporal crossing partially across independent job families, and stale-domain-write prevention only partially.**

### Invariant register

| Invariant | Enforcement | Assessment |
|---|---|---|
| Every canonical pipeline job resolves the context belonging to its payload pipeline/run | Code validation plus service tests | ENFORCED IN CODE / TESTS — VERIFIED |
| A superseded lease holder cannot complete or update the durable job row | Conditional execution-token updates plus tests | ENFORCED IN CODE / TESTS — VERIFIED |
| A pipeline cannot be terminal while planned child calculations remain nonterminal | No parent barrier for provider CERI | VIOLATED — VERIFIED |
| Resume preserves the original market cutoff and pipeline identity | Resume payload/context does; resumed Setup evaluation call does not | VIOLATED for CERI+Setup resume — VERIFIED |
| Completed upstream stages accepted by resume are the exact original artifacts | Count/presence checks only | NOT ENFORCED — VERIFIED |
| Standalone recalculation uses the intended pipeline context | Newest context for run is selected implicitly | NOT ENFORCED where multiple contexts exist — VERIFIED |
| Recovery of one job is bounded | Retry count bounds exceptions, not lease recoveries | NOT ENFORCED — VERIFIED |
| Canonical production execution always uses durable mode | Certification enforces; normal config still exposes synchronous route | DOCUMENTED/CONFIG-GUARDED ONLY — LEGACY/VERIFIED |
| Historical/background writers cannot mutate current artifacts accidentally | Per-subsystem IDs/keys/generations exist; no uniform global boundary | PARTIALLY ENFORCED — PARTIALLY_VERIFIED |

## Findings

### PIPE-001

- **Severity:** P1
- **Status:** VERIFIED — potential contract violation
- **Subsystem:** Pipeline / CERI orchestration
- **Finding:** `CERI_PROVIDER_INGEST` completes after scheduling the CERI DAG; the parent immediately proceeds to decision handoff, Setup, Winner, and finalization without waiting for the CERI descendants.
- **Evidence:** `app/services/pipeline_executor.py:639-763`; the batched child barriers are only inside `app/services/ceri/batched_job_handlers.py:436-517`. `tests/test_pipeline_executor.py:246-288` verifies schedule-then-Winner order, not child completion.
- **Why it matters:** A terminal pipeline does not mean all planned calculation work is terminal.
- **Potential contamination/correctness effect:** Winner/Setup can consume pre-existing CERI state while newly scheduled CERI evidence arrives later; child failure is absent from the parent status.
- **Existing guard:** Child jobs retain run/workflow/context, and their internal stage barriers preserve CERI ordering.
- **Missing guard:** Parent completion barrier or an explicit split contract that excludes provider ingest from pipeline completeness and prevents downstream consumers from assuming it completed.
- **Recommended future remediation:** Make provider ingest a prerequisite barrier before run-scoped capture/downstream consumers, or move it outside the pipeline plan and record a frozen input manifest; roll terminal child state into pipeline status.

### PIPE-002

- **Severity:** P1
- **Status:** VERIFIED — current implementation defect
- **Subsystem:** Pipeline resume / Setup lifecycle
- **Finding:** `_execute_resumed_pipeline` calls `_invoke_setup_evaluation` without required keyword-only `market_cutoff` and `pipeline_run_id`.
- **Evidence:** Call at `app/services/pipeline_executor.py:911-919`; required signature at `2061-2086`. The normal path supplies both. The existing CERI-resume test has Setup disabled.
- **Why it matters:** The only supported durable resume checkpoint fails whenever Setup lifecycle is enabled.
- **Potential contamination/correctness effect:** Pipeline remains failed/partial after repaired CERI readiness; Setup and Winner continuation cannot complete. Passing fallback/current time instead would also be unsafe, so fail-closed is preferable to implicit drift.
- **Existing guard:** Function signature requires both frozen-context values and rejects evaluators that cannot accept them.
- **Missing guard:** Correct arguments in resumed path and a test covering CERI resume with Setup enabled.
- **Recommended future remediation:** Pass `dependencies.market_cutoff` and `pipeline.id`, assert context ownership, and add an isolated regression test.

### PIPE-003

- **Severity:** P2
- **Status:** LEGACY and VERIFIED — potential contract violation outside certification
- **Subsystem:** Pipeline route / runtime configuration
- **Finding:** With `USE_DURABLE_PIPELINE=false`, the route synchronously recalculates only fundamentals, market data, technicals, and combined results; it creates no pipeline/context/steps and omits ranking, regime, sector, CERI, Setup, and Winner stages.
- **Evidence:** `app/routers/run_routes.py:706-772`; settings restrictions at `app/settings.py:480-523`; intended topology in `docs/architecture/runtime_process_ownership.md:5-26`.
- **Why it matters:** The same pipeline endpoint has materially different correctness, completeness, identity, and time semantics.
- **Potential contamination/correctness effect:** A run can appear refreshed while retaining stale omitted artifacts and lacks an auditable pipeline boundary.
- **Existing guard:** Certification requires durable mode; architecture labels direct/non-durable execution a development/test exception.
- **Missing guard:** A hard non-development runtime prohibition or an explicit endpoint/result that cannot be mistaken for canonical pipeline completion.
- **Recommended future remediation:** Remove the branch from production builds or enforce environment-scoped activation and invalidate all omitted artifacts.

### PIPE-004

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Durable recovery
- **Finding:** Stale/abandoned recovery requeues while `retry_count < max_retries` but does not increment retry count or enforce a maximum `recovery_count`.
- **Evidence:** `app/services/background_job_service.py:1500-1549`; tests prove one recovery/new token but do not impose a cumulative recovery ceiling.
- **Why it matters:** Repeated worker loss can replay the same handler indefinitely despite a nominal retry limit.
- **Potential contamination/correctness effect:** Non-idempotent or partially committed services may repeat historical writes; even idempotent work can create unbounded operational load.
- **Existing guard:** New execution token, old-token fencing, handler checkpoints/idempotency keys, supervisor restart budgets.
- **Missing guard:** Per-job recovery ceiling and terminal policy independent of exception retry count.
- **Recommended future remediation:** Bound recoveries, record attempt generations, and require resumable/idempotent certification for mutation-capable handlers.

### PIPE-005

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Market-data background work
- **Finding:** `IB_FETCH` and `MARKET_DATA_PREWARM` persist request metadata but rebuild a fetch plan at execution. Prewarm records an effective session/config fingerprint yet does not pass the stored session into `build_fetch_plan` or fail on drift.
- **Evidence:** `app/services/ib_fetch_job_service.py:65-88`, `209-222`; `app/services/market_data_prewarm_service.py:186-255`.
- **Why it matters:** A durable delay or restart changes cache state, current session, settings, and therefore the actual requests relative to enqueue time.
- **Potential contamination/correctness effect:** Shared bars can be filled under a later context than the job identity implies. Pipeline readers still project by cutoff, limiting but not eliminating provenance ambiguity.
- **Existing guard:** Run/ticker/options, universe fingerprint, request key, config metadata, foreground preemption, downstream cutoff projection.
- **Missing guard:** Frozen serialized plan or execute-time equality checks for session/config/plan fingerprint.
- **Recommended future remediation:** Persist canonical plan inputs and hash, validate them at claim, and re-enqueue under a new identity when intentional replanning is required.

### PIPE-006

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Resume integrity
- **Finding:** Resume checkpoint validation proves row counts/presence, not content identity, configuration compatibility, or fingerprints of completed stages.
- **Evidence:** `app/services/pipeline_executor.py:989-1069`.
- **Why it matters:** Manual recalculation or background mutation can change upstream artifacts while preserving counts; resume then treats those artifacts as the original checkpoint.
- **Potential contamination/correctness effect:** One pipeline result can combine upstream artifacts produced by a different execution/configuration with downstream artifacts produced under the resumed context.
- **Existing guard:** Expected ticker counts, required ranking/regime/sector presence, original market context validation, stronger transition/certification manifests on guarded paths.
- **Missing guard:** Per-stage immutable artifact manifest/fingerprint checked at resume.
- **Recommended future remediation:** Store canonical output fingerprints and configuration/code identities at stage completion and require exact compatibility before resume.

### PIPE-007

- **Severity:** P2
- **Status:** VERIFIED
- **Subsystem:** Temporal context selection
- **Finding:** Standalone technical and Setup readers can resolve `market_context_for_upload_run`, which selects the newest context ID for the run rather than requiring the context of the pipeline whose artifacts are being recomputed.
- **Evidence:** `app/services/market_calculation_context_service.py:136-150`; callers in `technical_score_service.py:111-118`, `329-336`, `464-470` and `setup_lifecycle/source_loader.py:106-115`.
- **Why it matters:** The schema permits multiple pipeline contexts for a run, and standalone recalculation is not pipeline-specific.
- **Potential contamination/correctness effect:** Historical artifacts can be silently recomputed under a later cutoff/session and coexist with or overwrite earlier pipeline-derived rows.
- **Existing guard:** Canonical pipeline passes an explicit cutoff; certification decision handoff provides stronger evidence binding.
- **Missing guard:** Explicit pipeline/context parameter for every historical mutation, or immutable versioned outputs rather than run-level replacement.
- **Recommended future remediation:** Eliminate implicit “latest context” for writers; require caller-selected context and persist it on the produced artifact.

### PIPE-008

- **Severity:** P2
- **Status:** PARTIALLY_VERIFIED
- **Subsystem:** Cross-cutting background writers
- **Finding:** Independent CERI, Setup, Winner, IB, replay, repair, and backfill paths can mutate pipeline-influencing or historical state after a pipeline completes, but there is no uniform pipeline/generation/temporal contract across all job types.
- **Evidence:** Registered handlers in `background_worker.py:617-641`; Setup routes `setup_lifecycle_routes.py:657-799`; Winner scheduler/handlers; IB/prewarm services; CERI routes/handlers.
- **Why it matters:** Pipeline terminal status is not a universal immutability or publication boundary.
- **Potential contamination/correctness effect:** Later UI/API reads may combine frozen pipeline artifacts with mutable shared or historical evidence unless each reader selects a compatible snapshot.
- **Existing guard:** Run/request/workflow keys, CERI context propagation, Winner generation/manifests, Setup optional pipeline checks, job leases.
- **Missing guard:** System-wide writer registry, required provenance envelope, allowed mutation window, and compatibility validation at every reader.
- **Recommended future remediation:** Complete Tasks 02–07 writer-level audits, then enforce a common provenance contract and immutable publication boundary.

### PIPE-009

- **Severity:** P2
- **Status:** PARTIALLY_VERIFIED
- **Subsystem:** Lease fencing / domain writes
- **Finding:** Execution-token fencing protects the job control row and pipeline commit boundaries, but domain artifacts are not uniformly execution-token-bound; an exhaustive internal-commit audit was not performed in Task 01.
- **Evidence:** Conditional job updates in `background_job_service.py:1656-1686`; pipeline lease-guard commits in `pipeline_executor.py:1079-1254`, `1437-1443`; calculation models do not share a generic execution-token provenance column.
- **Why it matters:** If a handler commits domain rows after its lease is superseded, the job-row fence alone cannot retract those writes.
- **Potential contamination/correctness effect:** Duplicate or stale-attempt artifacts despite exactly-one terminal job transition.
- **Existing guard:** Cooperative heartbeat/cancellation, transaction rollback on handler failure, uniqueness/upsert logic, pipeline lease guard.
- **Missing guard:** Uniform commit-time ownership check or attempt/generation stamp on mutation-capable artifacts.
- **Recommended future remediation:** Audit every writer transaction in Task 07 and require a lease/attempt guard at each commit boundary.

## Known-Safe and Known-Dangerous Execution Paths

| Path | Classification | Reason |
|---|---|---|
| Canonical durable parent pipeline with no provider-async branch | VERIFIED, strongest available path | Frozen context is persisted/validated; stages are ordered and token/lease guarded |
| Certification pipeline | VERIFIED, strongest isolation policy | Adds preflight, evidence fingerprints, queue/single-worker checks, and disables unrelated automation |
| Provider CERI inside parent pipeline | VERIFIED but unsafe completeness assumption | Child DAG is temporally bound but not awaited |
| CERI resume with Setup disabled | VERIFIED | Existing implementation/test reuses context and skips expensive stages |
| CERI resume with Setup enabled | VERIFIED defective | Missing required arguments causes failure |
| Non-durable pipeline branch | LEGACY | No durable identity/context and incomplete stage set |
| Manual recomputation using implicit latest run context | VERIFIED dangerous for multi-pipeline runs | Context is reconstructed by newest ID rather than explicit ownership |
| Historical maintenance/backfill | PARTIALLY_VERIFIED, intentionally dangerous | Necessary mutation capability; correctness depends on subsystem-specific scope/fences |

## Audit Coverage

### Fully audited

- Upload-to-pipeline route and durable pipeline creation.
- Actual parent pipeline step ordering and optional step insertion.
- `FULL_PIPELINE` context creation, payload propagation, claim, execution, stage transaction, finalization, cancellation/retry/recovery mechanics.
- CERI child scheduling boundary and internal high-level barrier DAG.
- Worker/supervisor ownership, heartbeats, claim/lease/token, startup recovery, and canonical shutdown/quiesce behavior.
- Presence and orchestration role of important registered job families.

### Partially audited

- Domain-write idempotency and internal transaction boundaries for every CERI, Setup, Winner, IB, repair, replay, rescore, and backfill handler.
- Exact Winner evidence, cohort generation, and publication compatibility rules (Task 06 scope).
- Exact CERI evidence writer and revision semantics (Task 03 scope).
- Exact Setup/lifecycle historical transition and alert writer semantics (Task 05 scope).
- Database constraints were inspected from ORM/migrations only; no live read-only catalog query was possible under the active safety context.

### Not audited

- Individual scoring mathematics, weights, thresholds, and indicator formulas except where needed for orchestration.
- Exhaustive fingerprint input/canonicalization registry.
- Every cache, hidden writer, database conflict, and snapshot compatibility path (Task 07 scope).
- Live production queue contents, real worker registrations, current database revision, or historical row samples.
- External provider behavior or paid calls.

## Known Unknowns

- Current database revision and whether the live database exactly matches repository head; the safety gate prevented `alembic current` and was not bypassed.
- Whether an external/non-default worker registers handlers for declared `WINNER_MODEL_TRAINING` or `WINNER_SIMILARITY_CACHE` job types.
- Whether every mutation-capable service checks lease ownership immediately before each internal commit.
- Whether operational deployment outside certification can set `USE_DURABLE_PIPELINE=false`; code permits the configuration, but deployed environment values were not inspected.
- Exact live retention/deletion policy for job rows and how often `ON DELETE SET NULL` removes parent/root lineage in practice.

## Contradictions

1. The pipeline plan can include `CERI_PROVIDER_INGEST`, while architecture guidance describes provider ingestion as outside the ordinary full pipeline by default and run-scoped capture as the consumer of shared PIT state. Current code allows enqueue-only provider ingest inside the parent plan. **Status: CONTRADICTORY.**
2. The durable pipeline promises a frozen context through resume, but the CERI-resume Setup evaluation call omits that context and pipeline identity. **Status: CONTRADICTORY.**
3. Runtime architecture names durable supervised execution as canonical and settings prohibit non-durable mode in certification, while the normal route still implements a materially incomplete synchronous “pipeline.” **Status: LEGACY/CONTRADICTORY.**
4. `effective_session` and prewarm configuration fingerprints are recorded as job provenance, but execution replans without enforcing those recorded values. **Status: CONTRADICTORY.**

## Potential Contract Violations

- **PIPE-001:** parent pipeline completion before planned CERI descendants are terminal.
- **PIPE-002:** CERI-resume + Setup cannot preserve/execute the required frozen evaluation contract.
- **PIPE-003:** normal-runtime configuration can expose a non-canonical incomplete pipeline path.
- **PIPE-006:** resume can combine count-compatible but provenance-incompatible upstream artifacts.
- **PIPE-007:** implicit newest-context selection can recompute a run under a different pipeline's cutoff.
- **PIPE-009:** stale-attempt domain writes are not proven globally fenced at commit.

## High-Risk Cross-Subsystem Dependencies

1. Shared `PriceBar` writes from pipeline fetch, manual `IB_FETCH`, and prewarm feed technical, regime, sector, Setup, Winner, and CERI price-response consumers. Reader cutoff projection is essential.
2. CERI provider evidence is shared and mutable; run snapshots/Setup/Winner may read it before or after the asynchronous child graph completes.
3. Manual technical/Setup recalculation can resolve the latest context for a run, linking run-level mutable outputs to whichever pipeline context was created last.
4. Winner maturation, revision, cohort refresh, rescore, and historical backfill intentionally span pipelines and runs; generation/manifest correctness is therefore a publication boundary, not merely a job concern.
5. Worker recovery and supervisor restart make process lifetime irrelevant; correctness depends on payload immutability, token/lease fences, and idempotent writer transactions.
6. Resume trusts previously persisted core artifacts by count/presence, so all manual and background writers to those artifacts become dependencies of resume correctness.

## Files Inspected

- `app/main.py`
- `app/worker.py`
- `app/worker_supervisor.py`
- `app/settings.py`
- `app/models/tables.py`
- `app/observability/correlation.py`
- `app/routers/upload_routes.py`
- `app/routers/run_routes.py`
- `app/routers/ib_routes.py`
- `app/routers/market_regime_routes.py`
- `app/routers/sector_rotation_routes.py`
- `app/routers/ceri_routes.py`
- `app/routers/setup_lifecycle_routes.py`
- `app/routers/winner_probability_routes.py`
- `app/services/upload_service.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `app/services/market_calculation_context_service.py`
- `app/services/pre_enqueue_operational_gate.py`
- `app/services/background_job_service.py`
- `app/services/background_queue.py`
- `app/services/background_worker.py`
- `app/services/worker_registry.py`
- `app/services/parent_watchdog.py`
- `app/services/lifecycle_quiesce.py`
- `app/services/startup_preflight.py`
- `app/services/ib_fetch_job_service.py`
- `app/services/market_data_prewarm_service.py`
- `app/services/technical_score_service.py`
- `app/services/setup_lifecycle/source_loader.py`
- `app/services/setup_lifecycle/evaluation_service.py`
- `app/services/setup_lifecycle/job_handlers.py`
- `app/services/ceri/constants.py`
- `app/services/ceri/batched_workflow.py`
- `app/services/ceri/batched_job_handlers.py`
- `app/services/ceri/job_handlers.py`
- `app/services/winner_probability/job_handlers.py`
- `app/services/winner_probability/scheduler.py`
- `scripts/ops/lifecycle_probe.py`
- `scripts/ops/SwingLensLifecycle.psm1`
- `docs/architecture/runtime_process_ownership.md`
- `docs/architecture/temporal_preflight_invariants.md`
- `docs/execution_plan_catalyst_estimate_revision_intelligence.md`
- `docs/forensics/session_temporal_integrity_controlled_canary_3_20260908.md`
- `docs/forensics/session_temporal_integrity_canary_failure_remediation_20260908.md`
- `docs/forensics/run118_ceri_sec_recovery_20260819.md`

## Tests Inspected

- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`
- `tests/test_background_job_service.py`
- `tests/test_background_worker.py`
- `tests/test_worker_supervisor_reliability.py`
- `tests/ops/test_worker_quiesce.py`
- `tests/test_app_lifespan_worker.py`
- `tests/e2e/single_run_certification/certification_server.py`

Selective non-destructive execution:

```text
python -m pytest -q \
  tests/test_pipeline_service.py \
  tests/test_pipeline_executor.py \
  tests/test_background_job_service.py \
  tests/test_background_worker.py \
  tests/test_worker_supervisor_reliability.py \
  tests/ops/test_worker_quiesce.py \
  tests/test_app_lifespan_worker.py

117 passed, 1 warning in 4.43s
```

The warning was a Starlette `httpx` deprecation warning and did not affect test results. The first attempted test command named a nonexistent `tests/test_market_calculation_context_service.py`, so pytest stopped before collection; the corrected command above is the verification result.

## SQL Inspected

- No live SQL was executed.
- ORM table definitions, indexes, unique constraints, foreign keys, and migration head were inspected statically.
- `alembic current` was attempted read-only but rejected because `SWINGLENS_DATABASE_SAFETY_CONTEXT` was unset. The safety control was preserved, so live schema/current-row assertions remain `UNKNOWN`.

## Verification Metadata

| Item | Value |
|---|---|
| Last verified commit | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Branch | `codex/winner-evidence-remediation` |
| Repository migration head | `0074_ceri_evidence_quarantine` |
| Verification date | 2026-09-14 (Europe/Zurich) |
| Verification method | Static code/model/test/architecture inspection plus isolated unit tests |
| Runtime mutation | None |
| External/provider calls | None |
| Audit output | `docs/audit/calculation-lineage/01_pipeline_execution.md` only |
