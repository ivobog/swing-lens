# SwingLens Winner maturation state model

This document defines the canonical state predicates for current `NEXT_OPEN` / H5 Winner
forward outcomes. Queue accounting and the maturation selector implement these predicates
through the shared helpers in `outcome_service.py`.

## Predicates

Let `completed_session` be the latest completed US trading session, or the workflow's frozen
earlier due-session bound. Let `operation_time` be the workflow's timezone-aware operation
cutoff.

- **OUTCOME_TEMPORALLY_DUE**: the forward outcome is `PENDING`, is the current revision, has
  `due_session <= completed_session`, and its prediction passes the live temporal-eligibility
  projection. For an already admitted workflow, immutable admitted prediction membership
  replaces the later live projection.
- **ACQUISITION_READY**: at least one market-data obligation for the forward outcome is
  `SATISFIED`. This matches the actual H5 selector. Other outcome families retain their
  pre-obligation behavior.
- **RETRY_READY**: `retry_not_before_at IS NULL` or
  `retry_not_before_at <= operation_time`.
- **MATURATION_READY**: `OUTCOME_TEMPORALLY_DUE AND ACQUISITION_READY AND RETRY_READY`.
  `eligible_remaining` and `maturation_ready` count exactly this set.
- **WAITING_FOR_ACQUISITION**: the outcome is temporally due, has no `SATISFIED` obligation,
  and either has no obligation yet or has at least one `FETCH_REQUIRED` obligation. These rows
  remain visible in `acquisition_pending`; they are not immediately selectable.
- **TERMINALLY_UNPROCESSABLE**: the outcome is temporally due, has installed obligations, has
  no `SATISFIED` obligation, and has no `FETCH_REQUIRED` obligation. Its obligations therefore
  consist only of terminal states such as `IDENTITY_BLOCKED`, `UNAVAILABLE`, or `FAILED`.
- **SUCCESSFULLY_MATURED**: the current forward outcome is `MATURED` and its immutable native
  outcome proof and any target/stop results have been persisted under the normal Winner writer
  authority.

Retry-deferred work is acquisition-ready but not retry-ready. It is reported separately and
uses the existing bounded durable deferral mechanism.

## Durable workflow behavior

An acquisition-only empty scan terminates the claimed job once as `PARTIAL` with
`WAITING_FOR_ACQUISITION`. It does not become `BLOCKED`, does not enqueue an immediate child,
and does not requeue the same job. Market-data prewarm and its bounded IB fetch workflow remain
the sole acquisition owner; Winner maturation does not create a duplicate provider fetch.

The continuously invoked Winner scheduler is the durable rediscovery owner. Active global
work (`QUEUED`, `RUNNING`, `RECOVERING`, or `STALLED`) suppresses concurrent admission. All
terminal history suppresses only its own session lineage. A same-session
`WAITING_FOR_ACQUISITION` lineage is reconsidered after acquisition; a deterministic hash of
the exact maturation-ready outcome IDs is used as the readiness request key. Historical
`PARTIAL`, `COMPLETED`, `BLOCKED`, `FAILED`, `CANCELLED`, and `STALE` jobs never suppress a
different completed-session key.

This design survives process, worker, and scheduler restarts because both obligation state and
job/session identity are durable PostgreSQL state. No in-memory continuation is required.
