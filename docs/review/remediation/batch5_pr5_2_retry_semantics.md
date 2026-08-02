# Batch 5 PR 5.2 Retry and Lease-Fencing Semantics

Status: implemented for PR 5.2 scope.

Source findings: PH14-002, PH14-003, PH14-005, PH4-006, PH13-004, PH19-005.

## Full-Pipeline Replay Policy

`FULL_PIPELINE` retries replay the pipeline from the first step. This remains acceptable only because
the pipeline executor records replay attempts and requires every progress/final-status commit to pass
a lease guard. A step whose previous state was `RUNNING`, `COMPLETED`, `FAILED`, or `CANCELLED` is
marked as replayed by incrementing `pipeline_steps.retry_count` and writing a replay message before
the step body runs again.

Future pipeline steps must either:

- be idempotent when replayed from the beginning; or
- add persisted checkpoint/resume behavior before introducing non-idempotent external side effects.

## Lease-Fencing Policy

The background worker passes a lease-only guard into the full-pipeline executor. The executor calls
that guard before every pipeline progress, step, failure, cancellation, and completion flush/commit.
If the guard raises `JobLeaseLost`, the executor re-raises without marking the pipeline failed or
committing stale step progress.

Cancellation remains separate from lease fencing. A cancellation check may mark a pipeline cancelled
only while the lease guard still succeeds.

## Redaction Policy

Lease events and winner processing metadata store execution-token hashes and short suffixes, never
raw execution tokens. Job status and persisted job result/error surfaces are redacted through the
shared operational redaction helper before exposure or storage.
