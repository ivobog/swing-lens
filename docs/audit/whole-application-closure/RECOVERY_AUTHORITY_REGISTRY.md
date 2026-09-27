# Recovery authority registry

Twenty-two independently reachable retry, recovery, reconciliation, replay, cancellation, and restart paths were found. “Cross session” describes the current working tree, not HEAD alone.

| ID | Initiator and exact function | Mutated state / replay scope | Authority and concurrency guard | Cancel/session/history/duplicate analysis |
| --- | --- | --- | --- | --- |
| REC-001 | worker `mark_job_failed_or_retry` | RUNNING job -> delayed QUEUED or FAILED | execution token, locked job, retry count/classifier | request cancellation is handled separately; may retry historical NORMAL jobs; idempotency belongs to handler |
| REC-002 | worker `mark_job_deferred` | RUNNING -> delayed QUEUED | execution token and explicit delay | no upstream replay until next claim; no duplicate row |
| REC-003 | worker `recover_stale_jobs` | expired RUNNING -> QUEUED/STALE | lease expiry, row lock; NORMAL-only caller | skips certification; requested-cancel now terminalizes; historical NORMAL rows are eligible |
| REC-004 | worker `recover_abandoned_jobs_for_worker` | same worker ID with dead heartbeat -> QUEUED/STALE | worker ID/heartbeat, row lock; NORMAL-only caller | can affect old jobs for reused worker ID; cancellation terminalized; recovery count limits |
| REC-005 | supervisor `_fence_no_progress` -> `fence_stalled_jobs` | RUNNING -> STALLED, step INTERRUPTED, pipeline PENDING | worker ID/instance/heartbeat/progress timeout; certification predicate when session supplied | does not replay until REC-006; cancellation terminalized; current cert scope prevents cross-session mutation |
| REC-006 | supervisor `_requeue` -> `requeue_stalled_jobs` | STALLED -> RECOVERING | explicit IDs from REC-005/007/008; row lock/status | primitive itself has no session parameter (GAP-003); no new job, same row |
| REC-007 | supervisor dead/stale worker `_fence_worker` -> `reconcile_jobs_for_worker_loss` | owned RUNNING -> STALLED, then REC-006 | worker ID/instance and certification claim when supplied | requested-cancel terminalized; unauthorized certification rows reported untouched; may replay current stage |
| REC-008 | supervisor critical-memory branch | same as REC-007 | memory status + worker identity + session | terminates process after fence; same duplicate protection |
| REC-009 | supervisor shutdown `_shutdown_owned_worker` | current worker jobs -> STALLED/RECOVERING | worker instance + session in current tree | can cause planned replay; requested cancel preserved; no cross-session in current path |
| REC-010 | worker/supervisor process restart | registration/claim/recovery resumes queue | process/worker/supervisor leases | NORMAL worker can recover historical eligible jobs; certification worker claims only exact active lineage |
| REC-011 | manual pipeline `resume_pipeline` | pipeline/steps reset, new FULL_PIPELINE job | pipeline ID, resumable state, frozen authority | creates a new job but coalescing/active-job checks limit duplicates; cannot resume CANCELLED without allowed state |
| REC-012 | SEC readiness `schedule_sec_readiness_repair` / `execute_sec_readiness_repair` | pipeline PREPARING, repair child, pipeline continuation | pipeline/scope/acquisition/processor identity | can repeat fetch/repair work idempotently; cancellation guard; certification descendant inheritance |
| REC-013 | `enqueue_pipeline_after_sec_repair` | repaired pipeline -> PENDING + FULL_PIPELINE | pipeline/root causality, active child checks | continuation crosses transaction; duplicate enqueue coalesced |
| REC-014 | CERI finalizer/capture/alert `_release_pipeline_after_ceri` -> `enqueue_pipeline_after_ceri_completion` | WAITING pipeline -> PENDING + continuation | pipeline/run/root correlation and active children | durable completion only; CERI failure rolls up rather than silently continuing; coalesced |
| REC-015 | `_roll_up_ceri_pipeline_failure` / `mark_pipeline_job_failure` | pipeline/step -> PARTIAL/FAILED | child/root relationship and job terminal result | no resurrection; cancels pending steps as applicable |
| REC-016 | Winner `enqueue_outcome_maturation_workflow` and handler continuation | more maturation/revision jobs | prediction scope, causality, continuation depth/coalescing | bounded depth (1000); can process old predictions by explicit policy; certification enqueue gate applies |
| REC-017 | Winner cohort refresh planner/scheduler | cohort refresh job | evidence watermark/current refresh state/idempotency | NORMAL scheduler only; manual API also available; generation locks prevent double publish |
| REC-018 | IB fetch retry/resume APIs and `ib_fetch_recovery_service` | failed/interrupted items and fetch run | run/plan/scope/refresh, item status | explicit cancel; item identity/idempotency; historical run explicitly selected |
| REC-019 | CERI backfill/reprocess/recalculate APIs and controlled replay service | provider/normalize/feature/capture/replay state | explicit request/manifest plus source/cutoff authority | queue gate blocks unbound certification jobs; NORMAL may intentionally affect historical scope |
| REC-020 | setup lifecycle replay/repair/daily maintenance | lifecycle episodes/events/alerts | retained original-context decision or maintenance authority | direct historical operation by design; job coalescing varies by operation |
| REC-021 | market prewarm preemption/resume | requested_cancel/PARTIAL -> resumed prewarm | active prewarm job/run and preemption policy | deliberately clears cancellation on explicit resume; no pipeline financial writer authority by itself |
| REC-022 | cancellation paths `request_job_cancel`, `cancel_pipeline`, worker `CancelRequested` | job/pipeline/children terminalization | local-admin or parent pipeline, job ID/execution token | queued/stalled/recovering cancel immediately; running is cooperative; new recovery code avoids resurrection |

## Duplicate-authority analysis

The recovery authorities are serialized by `SELECT ... FOR UPDATE`/`SKIP LOCKED`, status predicates, worker instance identity, and execution-token invalidation. Those mechanisms stop two actors from successfully owning the same execution attempt. They do not, by themselves, prove semantic authority to touch the row. Certification correctness therefore depends on the claim predicate being threaded into every supervisor path. The current uncommitted supervisor code does this for watchdog, worker loss, memory critical, and shutdown, but the lower-level requeue primitive remains ambient-ID based.

## Upstream replay and historical reach

- REC-001..010 may re-execute the current handler or pipeline stage; they do not automatically create a new upload or calculation identity.
- REC-011..015 can re-enter pipeline stages after a durable checkpoint and must reacquire source/configuration authority after every commit.
- REC-016..020 intentionally operate on prior predictions/runs when explicit scope permits it.
- NORMAL recovery can see historical rows matching status/lease/worker predicates. CERTIFICATION worker recovery is disabled and supervisor recovery is session-scoped in the current working tree.
- No path should resurrect `CANCELLED`; the current pre-existing changes add cancellation terminalization to all common recovery loops. Prewarm REC-021 is the one explicit domain exception and requires its resume operation.

## Idempotency classification

| Class | Paths | Mechanism |
| --- | --- | --- |
| Same-row recovery | REC-001..010, REC-022 | locks, status compare, execution token, recovery count |
| Continuation job | REC-011..017 | idempotency/coalescing keys, active-child queries, root causality |
| Domain replay/backfill | REC-018..021 | domain natural keys/manifests/generations; not universally no-op on repeated invocation |

## Verdict

The set of recovery paths is finite and mapped. The registry is inventory-complete, but recovery authority is not safety-closed until GAP-003 makes the certification/session capability intrinsic at the shared primitive boundary and the mapped paths receive direct PostgreSQL tests.
