# Pipeline authority architecture and stage contracts

## Before

```text
FULL_PIPELINE handler Session
  -> stage writes PipelineRun directly
  -> SEC helper inserts child inside parent transaction
  -> ambient ContextVar says "root owns domain writes"
  -> detached heartbeat Session inherits that authority
  -> heartbeat commit hook SELECT ... FOR UPDATE root job
  -> waits behind this process's uncommitted child FK transaction
```

Pipeline state was written directly at 21 sites across five service modules. SEC helper completion,
failure, cancellation, pipeline execution, recovery, CERI barriers, and operator actions each had
part of the transition protocol.

## After

```text
Transaction A: pipeline orchestrator
  lock PipelineRun -> create PipelineDependency(PENDING_ENQUEUE or RUNNING)
  -> PipelineRun(WAITING_DEPENDENCY or WAITING_FOR_CERI_COMPLETION) -> COMMIT

Transaction B: dependency enqueue
  lock PipelineDependency + PipelineRun
  -> create SEC child with explicit root/parent/dependency identity
  -> dependency(QUEUED) -> COMMIT

Child worker control transaction
  claim/heartbeat/retry/terminal status -> COMMIT

Pipeline orchestrator reconciliation transaction
  lock dependency + pipeline
  -> child terminal => dependency terminal
  -> exactly-once continuation with immutable identity
  -> PipelineRun(QUEUED) -> COMMIT
```

Only `pipeline_state_machine.transition_pipeline` assigns `PipelineRun.status`. Domain authority is
bound to exactly one SQLAlchemy Session by `fence_domain_commits(db, ...)`. A different Session in
the same call context is rejected unless it enters `control_plane_transaction`, which removes
domain authority and commits or rolls back a bounded control transaction. Every durable handler
enters `deferred_execution_ownership_lock`: it validates the execution token immediately but defers
the job-row `FOR UPDATE` until the authoritative domain commit. Decision mutation authority uses
that same fence instead of taking a second unconditional ownership lock. Per-item work uses an
explicitly bound bounded Session, so its commit cannot borrow ambient authority from the parent
Session.

The same durable handoff rule now covers both asynchronous pipeline boundaries:

- `SEC_READINESS` records the missing subjects before the repair child is enqueued.
- `CERI_WORKFLOW` records the certified-workflow identity before the CERI DAG is scheduled; the
  root may finish only while that exact dependency is active. The barrier atomically completes the
  dependency and binds its one continuation.

## Ownership matrix

| State or datum | Sole writer | Readers | Durable boundary |
|---|---|---|---|
| Pipeline status/current stage | Pipeline state machine, invoked by orchestrator/operator adapter | UI, readiness, invariant inspector | Orchestrator transaction |
| Dependency state and continuation identity | Pipeline dependency service | Worker, readiness, inspector | Dependency transaction |
| Job lease, heartbeat, retry, cancel flag, terminal job state | Background job control services | Worker, orchestrator, supervisor | Short control transaction |
| Financial/domain rows | Explicitly bound job domain Session | Pipeline stages and downstream readers | Domain commit fenced by current job token |
| Operator resume | `resume_pipeline` through state machine | Worker/orchestrator | Enqueue and pipeline transition transaction |
| Recovery | Stale-job recovery plus dependency reconciler | Worker/readiness | Recovery transaction; no historical guesswork |

## State model

The active protocol uses `PENDING`/`QUEUED`, `RUNNING`, `WAITING_DEPENDENCY`, stage-specific active
states, `CANCEL_REQUESTED`, and terminal `COMPLETED`, `PARTIAL`, `FAILED`, `BLOCKED`, `CANCELLED`, or
`FEATURE_CERTIFIED`. `PREPARING` remains readable for historical rows but the SEC path no longer
emits it. Terminal states cannot be revived by delayed worker completion. Only an explicit operator
resume may move `FAILED`, `BLOCKED`, or `PARTIAL` to `QUEUED`. A pipeline cannot enter `COMPLETED`
while an active dependency exists.

## Stage-by-stage contract

| Stage | Input contract | Writer / transaction owner | Checkpoint and retry | Success transition | Failure/cancel visibility |
|---|---|---|---|---|---|
| `VALIDATING_RUN` | Frozen upload scope, market context, execution configuration | Root domain Session | Stage attempt history; safe full-stage replay | Next stage or `WAITING_DEPENDENCY` | Typed preflight failure; cancel checked before work |
| SEC readiness dependency | Immutable signature, tickers, root and continuation identity | Dependency orchestrator; SEC child owns only repair domain writes | Durable `PENDING_ENQUEUE` crash gap; child retry count | Dependency `COMPLETED`, one continuation | Dependency `FAILED`/`CANCELLED`; pipeline terminal or cancel convergence |
| `SCORING_FUNDAMENTALS` | Frozen scope/configuration and calculation context | Root domain Session | Per-stage attempt; evidence-idempotent recalculation | `FETCHING_MARKET_DATA` | Failed stage and root failure reconcile |
| `FETCHING_MARKET_DATA` | Acquisition plan and retained authority | Root/authorized fetch child domain Session | Durable fetch plan/items and progress checkpoints | `SCORING_TECHNICALS` | Circuit/lease/cancel typed diagnostics |
| `SCORING_TECHNICALS` | Frozen bars and calculation context | Root domain Session | Ticker checkpoints and artifact cache identity | `MARKET_REGIME_SNAPSHOT` | Typed technical error or cancellation |
| `MARKET_REGIME_SNAPSHOT` | Point-in-time market evidence | Root domain Session | Immutable snapshot identity | `COMBINING_RESULTS` | Stage failure visible on pipeline/step |
| `COMBINING_RESULTS` | Fundamental and technical results | Root domain Session | Deterministic recomputation | `RANKING_PROFILES` | Stage failure visible on pipeline/step |
| `RANKING_PROFILES` | Combined results and profile configuration | Root domain Session | Deterministic recomputation | `SECTOR_ROTATION_SNAPSHOT` | Stage failure visible on pipeline/step |
| `SECTOR_ROTATION_SNAPSHOT` | Ranked universe and point-in-time sector data | Root domain Session | Immutable snapshot identity | Optional research boundary or winner capture | Stage failure visible on pipeline/step |
| `CERI_PROVIDER_INGEST` | Frozen scope, provider workflow key, CERI configuration | CERI child DAG; orchestrator owns `CERI_WORKFLOW` dependency and pipeline wait | Durable dependency, workflow jobs, and completion barrier | Exactly one `FREEZING_DECISION_HANDOFF_MANIFEST` continuation bound to the dependency | DAG roll-up through state machine; root terminal is valid only while the dependency is active |
| `CERI_FEATURE_CERTIFYING` | Exact certification graph and stop boundary | Feature-certification root and bounded children | Durable graph cardinality and terminal-child check | `FEATURE_CERTIFIED` | Typed graph failure through state machine |
| `CERI_CAPTURE_SNAPSHOT` | Certified provider outputs and calculation context | CERI capture child | Evidence-key idempotency | Setup-signal stage | Child terminal roll-up |
| `FREEZING_DECISION_HANDOFF_MANIFEST` | Certified upstream evidence set | Root continuation domain Session | Immutable manifest identity | Setup/winner stages | Manifest failure blocks downstream work |
| `CAPTURING_SETUP_SIGNALS` | Decision manifest and market context | Setup capture domain Session | Durable handoff/checkpoint | `EVALUATING_SETUP_LIFECYCLES` | Typed child failure/cancel |
| `EVALUATING_SETUP_LIFECYCLES` | Captured setups and lifecycle policy | Lifecycle domain Session | Bounded batch checkpoint | Winner capture | Typed child failure/cancel |
| `CAPTURING_WINNER_PREDICTIONS` | Frozen decision evidence | Root or authorized winner writer | Publication fence and evidence identity | `COMPLETED`/`PARTIAL` | Terminal status and failure classification |

## Cancellation and restart

Cancellation first commits `CANCEL_REQUESTED`. It then requests cancellation on the root, active
dependency child, and continuation with a PostgreSQL `lock_timeout` of 750 ms per target. A lock
timeout returns structured session/blocker diagnostics and never waits indefinitely. Worker/app
restart scans `PENDING_ENQUEUE` dependencies with `FOR UPDATE SKIP LOCKED` and safely completes only
the unambiguous enqueue gap. It never invents missing lineage or rewrites historical ambiguity.

## Runtime invariants

`inspect_pipeline_invariants` is read-only and classifies each finding as:

- `SAFE_AUTO_RECONCILE`: deterministic crash gaps or expired operational leases.
- `REQUIRES_OPERATOR_REVIEW`: historical ambiguity, stale generations, or terminal-root mismatch.
- `FATAL_STARTUP_INVARIANT`: invalid dependency foreign identity, duplicate active roots or
  continuations, or a terminal pipeline with a live continuation.

Startup fails only for fatal findings. Readiness degrades for safe/review findings and fails for
fatal findings. Run 188 is therefore reported for operator review; it is not silently rewritten.
