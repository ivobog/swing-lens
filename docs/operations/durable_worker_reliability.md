# Durable worker reliability invariants

SwingLens treats the worker process heartbeat, a job lease heartbeat, and useful durable job
progress as three independent signals.

1. `JOB_WORKER_ENABLED=true` means the web application continuously maintains an
   out-of-process supervisor. The supervisor continuously maintains a usable durable worker.
2. A worker heartbeat proves only that the worker process is alive.
3. A job lease heartbeat proves only that the current execution token still owns the lease.
4. A useful checkpoint updates `last_progress_at`, `progress_sequence`, the explicit stage,
   processed/total counts, and checkpoint identity in the same transaction.
5. A worker execution is identified by `worker_id` plus a unique `worker_instance_id`.
   Process liveness additionally validates PID plus OS process creation time.
6. A supervisor cycle failure is logged and retried; it does not exit the supervisor loop.
   The web-side guardian also restarts a supervisor process that unexpectedly exits.
7. Worker replacement fences only jobs owned by the observed worker instance. Queued jobs are
   never tied to a worker and remain durable.
8. Execution tokens fence late writes, so a recovering job cannot have two valid owners.
9. Full Pipeline remains fail-closed: without a fresh capable worker registration, the API
   returns `503 DURABLE_WORKER_UNAVAILABLE` and creates no job.
10. After supervisor recovery and a fresh worker heartbeat, Full Pipeline becomes usable
    without application restart, configuration edits, job repair, or operator action.

The progress watchdog persists its last observed `progress_sequence` and the time at which
that sequence stopped changing. Fresh worker and lease heartbeats do not reset that timer.
An advancing sequence resets it even when a job has run longer than the configured timeout.

On Windows, SwingLens does not use `os.kill(pid, 0)` for liveness. It opens the process with
query-only rights, verifies it is active, reads its creation time, and compares that time with
the registered process instance. Inspection errors are contained and treated as a failed
liveness probe so recovery can continue safely.
