# Run 120 Self-Healing Root Cause

Observed on 2026-08-20 before the self-healing implementation.

## User-visible incident

- Run 120 created pipeline 112 and background job 31115.
- The job remained `QUEUED` while `USE_DURABLE_PIPELINE=true` and
  `JOB_WORKER_ENABLED=false` because the web process permitted durable enqueue
  without proving execution capacity.
- After the worker was enabled, the job was claimed successfully and strict SEC
  preflight rejected the universe before expensive pipeline stages.
- Exact readiness was 91/375: 205 `SYNC_STATE_MISSING`, 27
  `SIGNATURE_MISMATCH`, 31 `CIK_MISSING`, and 21 `UNRESOLVED_MAPPING`.
- The recoverable prerequisite was persisted as terminal `BLOCKED`, and the UI
  required an operator to run maintenance scripts and manually retry preflight.

## Architectural causes

1. Worker health was diagnostic-only. Pipeline enqueue did not require a live
   durable worker, and durable mode did not automatically start the embedded
   worker.
2. SEC readiness validation classified conditions correctly but had no
   production repair orchestrator. The only end-to-end bootstrap and CIK repair
   entry points were operator scripts.
3. `FULL_PIPELINE` converted all `PipelineBlockedError` instances into terminal
   job, pipeline, and step states even when the reason was automatically
   recoverable.
4. Pipeline coalescing followed an active `FULL_PIPELINE` job. Once that parent
   job became terminal, a repair/waiting pipeline was no longer authoritative
   for duplicate clicks.
5. SEC document identity and extraction state were durable and
   signature-aware, but downloaded filing bodies were not cached. A processor
   signature change could therefore force a network fetch before re-extraction
   even when the document identity and prior extraction were already known.
6. Repair progress had no structured pipeline-level representation, and no
   automatic transition existed from repaired readiness back to the original
   pipeline.

## Correctness constraints retained

The SEC gate itself is not the defect and must remain fail-closed. Exact active
processor signature, deterministic issuer identity, durable document/extraction
leases, bounded SEC client retry policy, source-record idempotency, and CERI
downstream barriers remain authoritative.
