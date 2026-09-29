# Pipeline authority operations runbook

## Inspect safely

1. Use a read-only Session and run `inspect_pipeline_invariants`.
2. For a waiting pipeline, verify exactly one active `pipeline_dependencies` row and inspect its
   root, child, and continuation IDs.
3. For a suspected lock, query `pg_stat_activity`, `pg_locks`, and `pg_blocking_pids(pid)` with a
   statement timeout. Never infer a blocker from application status alone.

## Cancel safely

Use the pipeline cancel endpoint or `cancel_pipeline`. A normal response ends in `CANCELLED` when no
live job remains, otherwise `CANCEL_REQUESTED`. HTTP 409 with
`PIPELINE_CANCELLATION_LOCK_CONTENDED` means the intent was committed but a target control row could
not be locked within 750 ms. Preserve the returned diagnostics, remove the verified blocker through
the normal lifecycle command, and retry cancellation. Do not terminate arbitrary backends.

## Restart safely

Use `swinglens.ps1 stop`, verify role processes/listeners are gone, apply migration 0087, then use
`swinglens.ps1 start`. The worker reconciles only `PENDING_ENQUEUE` dependency rows. A
`REQUIRES_OPERATOR_REVIEW` finding must be investigated rather than auto-repaired.

## Roll back

Stop all roles before a code rollback. Migration 0087 can be downgraded only after proving there are
no dependency-backed jobs or pipeline dependencies that must survive; otherwise retain the schema
and roll back code only. Never delete Run 188/189 evidence to make rollback easier.

## Watch after deployment

Watch `swinglens_pipeline_transitions_total`, `swinglens_pipeline_dependency_wait_seconds`,
`swinglens_pipeline_cancellation_wait_seconds`,
`swinglens_pipeline_cancellation_lock_contention_total`, and
`swinglens_pipeline_continuations_created_total`. Alert on fatal invariant findings, duplicate active
roots/continuations, cancellation contention, or a growing `PENDING_ENQUEUE` age.

