# Pipeline authority certification report

## Scope and evidence

The forensic baseline and real PostgreSQL reproducer were committed before implementation. The
original state of Runs 188 and 189 remains immutable in `FORENSIC_BASELINE.md`. The reproducer
observes SQL plus `pg_stat_activity`/blocking edges and proved the original
root/child/heartbeat self-block. The bisect result is recorded in `BISECT.md`.

After the fix was deployed, startup reconciliation automatically resumed historical UploadRun 189
(pipeline 178) before the planned canaries began. That state change was captured rather than hidden:
SEC child 43644 and continuation 43645 were created; the pipeline was then deliberately cancelled
and converged to `CANCELLED`. Historical UploadRun 188 / pipeline 177 was not reconciled and remains
`RUNNING` with cancelled root 43640 for operator review.

## Objective complexity metrics

| Metric | Before (`e5217fd`) | After |
|---|---:|---:|
| Direct `PipelineRun.status` assignment sites in the measured authority surface | 20 | 1 (state machine only) |
| Pipeline transition call sites using the authoritative API | 0 | 27 |
| Physical LOC in the same 11 orchestration/authority modules | 11,197 | 12,761 |
| Explicit Session/session-factory creation references | 14 | 13 |
| `FOR UPDATE` sites | 14 | 25 (dependency and state ownership is now explicit) |
| External `detached_control_plane_scope` call sites | 1 | 0 |
| Session commit-hook registrations | 1 | 1 |
| Direct job-status assignment sites | 8 | 8 (worker control plane remains the owner) |
| Explicit dependency records | 0 | 1 per asynchronous prerequisite |
| Unbounded cancellation row-lock waits | possible | 0; 750 ms bound |
| Functional continuation creation paths | 3 | 3 (operator, SEC dependency, CERI barrier); both async paths now have durable identity and idempotency |

The 1,564-line increase is intentional protocol and certification code in the measured production
surface: durable dependency identity, transition validation, restart reconciliation, invariant
classification, and diagnostics replace implicit cross-module behavior. This is not a LOC
reduction. The value is fewer ambiguous authorities and zero external detached-scope workarounds;
the cost is more explicit state/lock sites, primarily the durable dependency protocol. Per-item
Session creation decreased and now routes through one reusable `bounded_domain_session` boundary.

## Mandatory PostgreSQL scenarios

The final combined real-PostgreSQL command completed with **39 passed** in 223.20 seconds. It covers
the exact reproducer, all 20 required concurrency cases, the four additional authority boundaries,
pre-upgrade compatibility cases, and CERI barrier behavior. The required cases are:

1. SEC already ready / bounded repair path reaches readiness.
2. SEC missing -> repair -> same-pipeline continuation.
3. Heartbeat during child enqueue reproducer has no blocked edge.
4. Cancel while waiting for dependency.
5. Cancel while child running.
6. App restart between wait commit and enqueue.
7. Worker restart while waiting.
8. Child completion delivered twice.
9. Continuation submitted twice concurrently.
10. Two pipelines require SEC simultaneously.
11. Root heartbeat during uncommitted child creation.
12. Supervisor/invariant inspection during an active transaction.
13. PostgreSQL lock-timeout cancellation.
14. Child failure.
15. Child retry.
16. Parent cancellation before child completion.
17. Stale worker generation during continuation.
18. Unresolved dependency prevents pipeline completion.
19. Terminal pipeline cannot be resumed by delayed completion.
20. Terminal root cannot leave its pipeline running indefinitely.

Additional structural cases certify explicitly bound per-item domain Sessions, deferred root
ownership locking during long work, terminal-root handoff through a durable `CERI_WORKFLOW`
dependency, and detached heartbeat progress while a generic non-pipeline durable handler keeps its
business transaction open. The dedicated winner-capture recovery file also completed **2 passed**
in 21.76 seconds.

The lane uses migrated disposable PostgreSQL, production services, real constraints, multiple
Sessions/threads, real commits/rollbacks, and PostgreSQL lock timeouts. SQLite is not certification
evidence.

## Focused compatibility lane

The final domain-fence, worker, pipeline service/executor, startup preflight, readiness, and CERI
barrier lane completed with **111 passed and 4 skipped**. Repository-wide Ruff remains red at
baseline because 59 pre-existing errors exist in old migrations and retained verification scripts;
all changed Python files pass Ruff.

## Live canaries

Canaries A-C passed the SEC wait, bounded cancellation, continuation, and two-pipeline concurrency
gates. Larger attempts D1-D6 deliberately exposed and closed the remaining implicit authority gaps:
IB/winner per-item Sessions, early ownership locking, and the CERI asynchronous handoff. D6 first
proved the bounded Session repair through feature/finalize/capture, then exposed an unconditional
decision-authority job-row lock during change detection. The two verified waiting heartbeat queries
were cancelled (not their backends), the worker was quiesced, and the generic durable-handler fence
plus shared decision authority check was deployed as `831ace9`.

The same queued D6 job resumed without row editing on worker generation 128. Change detection,
alerting, the exactly-one continuation, setup capture, lifecycle evaluation, and winner capture all
completed. Pipeline 189 ended validly `PARTIAL` for business completeness (10 combined rows, 5
incomplete, 0 IB failures; winner evidence 5 inserted, 5 excluded, 0 failed). The final scoped
observer reported `continued_pipeline_ids: [189]`, no cancellations, and
`maximum_blocked_sessions: 0`. Exact IDs and both diagnostic query-cancellation actions are retained
in `LIVE_CANARY_EVIDENCE.md`.

## Decision

`SAFE TO RUN NORMAL PIPELINE: YES` for the remediated authority protocol. D6 reached a valid terminal
result after resuming the same durable work, every post-fix stage completed, the continuation was
created exactly once, and the application-database observer recorded zero blockers. Historical
pipeline 177 remains an explicit operator-review item and continues to degrade readiness; it is not
evidence of a live authority failure and was intentionally not rewritten.
