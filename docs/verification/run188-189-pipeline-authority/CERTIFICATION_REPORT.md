# Pipeline authority certification report

## Scope and evidence

The forensic baseline and real PostgreSQL reproducer were committed before implementation. Runs
188 and 189 remain unchanged. The reproducer observes SQL plus `pg_stat_activity`/blocking edges and
proved the original root/child/heartbeat self-block. The bisect result is recorded in `BISECT.md`.

## Objective complexity metrics

| Metric | Before (`e5217fd`) | After |
|---|---:|---:|
| Direct `PipelineRun.status` assignment sites in services | 21 | 1 (state machine only) |
| Pipeline transition call sites using the authoritative API | 0 | 27 |
| Named pipeline/status constants in `pipeline_service.py` | 36 | 39 (adds queued, waiting dependency, cancel requested) |
| Commit sites in the four original orchestration modules | 22 | 24 |
| Commit sites inside the new dependency orchestrator | n/a | 0 (caller owns the boundary) |
| Explicit dependency records | 0 | 1 per asynchronous prerequisite |
| Unbounded cancellation row-lock waits | possible | 0; 750 ms bound |
| New authority/invariant modules | 0 lines | 942 lines |

The line-count increase is intentional protocol code: durable dependency identity, transition
validation, restart reconciliation, invariant classification, and diagnostics replace implicit
cross-module behavior. The number of commits did not simply fall; implicit commits were converted
to named transaction boundaries.

## Mandatory PostgreSQL scenarios

The certification command completed with **20 passed** in 80.68 seconds:

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

The lane uses migrated disposable PostgreSQL, production services, real constraints, multiple
Sessions/threads, real commits/rollbacks, and PostgreSQL lock timeouts. SQLite is not certification
evidence.

## Focused compatibility lane

The domain-fence, worker, pipeline service/executor, startup preflight, readiness, and CERI barrier
lane completed with 93 passed and 4 skipped after contract updates. Repository-wide Ruff remains
red at baseline because 59 pre-existing errors exist in old migrations and retained verification
scripts; all changed Python files pass Ruff.

## Live canaries

Pending. No live canary will start until the implementation commit, focused checks, migration
preflight, and lifecycle restart are complete. Record A-D here without overwriting this section.

## Decision

`SAFE TO RUN NORMAL PIPELINE: NO` until live canaries A-D complete. This is an operational gate, not
a failure of the disposable PostgreSQL certification lane.
