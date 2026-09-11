# Canary Runtime Isolation and IB Readiness Findings — 2026-09-10

## Scope and evidence

This operational audit follows the failed Run-154 canary. It changes no temporal, point-in-time,
lifecycle selection-key, Decision Manifest, pointer/ledger, CERI PIT, or immutable price-bar
semantics. Production was queried read-only; reproduction and mutation tests used disposable
PostgreSQL databases, including a temporary clone of production at Alembic head
`0072_ceri_artifact_context_lineage`.

Implementation commit: `3ec207614bfc364a32548fe5471c30968bb222f3`.

## OPS-ISO-F001 — automatic Winner work breached certification isolation

- **Severity:** Critical / certification blocker.
- **Status:** FIXED and regression-certified.
- **Reproducer:** Starting the normal durable worker against the Run-154 production state caused
  `run_worker_once` to call `schedule_primary_h5_maturation` on its first poll. The scheduler
  created `WINNER_OUTCOME_MATURATION` Job 42981 with request key
  `winner:h5-next-open:session:2026-09-09`, trigger `SCHEDULER`, and Winner processing run 9999.
  It completed `DRAIN_COMPLETE` / `NO_WORK_REMAINING`: due, processed, matured, retry, and material
  evidence counts were all zero. A pre-remediation production-clone replay created equivalent Job
  42982 (job count 42,537 to 42,538).
- **Root cause:** `run_worker` forwarded the production
  `winner_probability_auto_maturation_enabled` setting into every worker poll. `run_worker_once`
  scheduled maturation before claiming work, with no certification runtime profile, no startup
  business-work allowlist, and no claim filter. The session-scoped request key was absent before
  startup, so enqueue idempotence correctly allowed the scheduler request even though the executor
  later found no eligible rows. The old isolation check only inspected the queue before startup; it
  did not constrain what startup itself could create.
- **Affected code:** `app/services/background_worker.py`,
  `app/services/winner_probability/scheduler.py`,
  `app/services/winner_probability/job_handlers.py`, and the shared enqueue/claim path in
  `app/services/background_job_service.py`.
- **Remediation:** Added explicit `RUNTIME_MODE=CERTIFICATION`; fail-closed startup validation for
  durable-worker availability and conflicting Winner/cohort/prewarm automation; suppressed
  scheduling, evidence retention, abandoned/stale recovery, and unrelated claims; and enforced a
  central enqueue/claim allowlist. Only an explicitly authorized `FULL_PIPELINE` root carrying a
  positive transition-plan ID, its correlation-lineage descendants, and runtime control-plane
  bookkeeping are allowed.
- **Tests:** Configuration conflict tests; central enqueue-boundary tests; ten accelerated worker
  start/tick/stop cycles; due Winner/cohort/rescore/CERI/prewarm/retry/stale-continuation tests in
  disposable PostgreSQL; and production-clone before/after replay. The ten-cycle result was zero
  unrelated jobs created or executed. In the clone, remediation left the reproduced Winner job
  queued and created zero new scheduler jobs, while an authorized pipeline root remained
  enqueueable.

## OPS-IB-F001 — process/connect checks were not authoritative API readiness

- **Severity:** High / certification blocker.
- **Status:** FIXED and regression-certified.
- **Reproducer:** Run 154 recorded `READY` at `2026-09-10T13:59:32.838500Z`, then
  `PROCESS_RUNNING_API_NOT_READY` at `2026-09-10T14:00:13.971932Z`. The Gateway process remained
  present. Failure injection reproduced process-present/login-incomplete, connection timeout,
  client-ID conflict, and session loss during the smoke request.
- **Root cause:** The former health check treated a successful `connect`/`isConnected` observation
  as ready. It did not require the ib-insync client-ready handshake, positive server version, a
  bounded response, or connection survival through probe completion. Its result also had no
  expiry contract. Process detection could classify a failed connection, but it could not prove an
  authenticated and usable API session. Two independent observations could therefore legitimately
  diverge as the external IB session/login state changed.
- **Affected code:** `app/services/ib_gateway_health_service.py`,
  `app/services/readiness_service.py`, and `app/services/pipeline_executor.py`.
- **Remediation:** Defined `IB_PROCESS_NOT_RUNNING`,
  `IB_PROCESS_RUNNING_API_NOT_READY`, `IB_API_READY`, and `IB_SESSION_LOST` states. `IB_API_READY`
  now requires configured-host connection, `client.isReady()`, positive `serverVersion()`, a
  successful read-only `reqCurrentTime()` response, and a still-connected session at completion.
  Every probe records `checked_at`, `expires_at`, API/process flags, failure category, server
  version, smoke result, and latency; it always disconnects. Connect/request work is bounded by the
  configured 3-second health timeout and enqueue accepts evidence for at most 5 seconds.
- **Tests:** Process absent; process present/API incomplete; full handshake ready; session lost;
  timeout; client-ID conflict; expiry; fresh pre-enqueue recheck; and production-clone transition
  from process-only failure to genuine API-ready success. A live read-only closing probe returned
  `IB_API_READY`, server version 176, and a current-time response in 332 ms.

## OPS-GATE-F001 — operational checks were scattered before the enqueue boundary

- **Severity:** Critical / certification blocker.
- **Status:** FIXED and regression-certified.
- **Reproducer:** The Run-154 route performed worker/IB checks before `start_pipeline`; the service
  separately verified the preflight and could create Pipeline/Job state without one decision that
  also covered runtime mode, exact worker count, queue isolation, schema, a fresh IB probe, and the
  final manifest/pointer/evidence lock. Failure-injection tests demonstrate each former gap.
- **Root cause:** There was no authoritative pre-enqueue operation spanning all control-plane and
  evidence prerequisites. Readiness data could age between startup/route observation and enqueue,
  and the job service did not independently enforce certification lineage.
- **Affected code:** `app/routers/run_routes.py`, `app/services/pipeline_service.py`, new
  `app/services/pre_enqueue_operational_gate.py`, and `app/services/background_job_service.py`.
- **Remediation:** Certification `start_pipeline` now invokes one ordered gate immediately before
  atomic Pipeline/root-Job creation: reserved plan/context; certification mode; exactly one live
  control-loop-capable durable worker; zero unrelated runnable or due jobs; a single repository
  Alembic head equal to the database; a new full IB API probe that is still fresh; and final locked
  Decision Manifest/pointer/evidence verification. Any failure raises a structured code and leaves
  Pipeline/Job counts unchanged. The central enqueue boundary provides defense in depth.
- **Tests:** All-green enqueue; stale/failed/flapping IB; expired/missing worker; duplicate worker;
  dirty queue; schema mismatch; stale manifest; no-business-state-on-rejection; duplicate-click
  coalescing; and production-clone rejection/success. The clone process-only case produced zero
  Pipeline and Job rows; genuine API readiness produced exactly Pipeline 146 and Job 42983; a
  repeat action returned Pipeline 146 without another artifact.

## OPS-GATE-F002 — rejection lifecycle semantics were implicit

- **Severity:** Medium.
- **Status:** FIXED and documented.
- **Reproducer:** Run 154 existed without Pipeline/Job, while Plan 3 and Context 7 remained
  reserved. Without a single gate result this could be mistaken for an incomplete pipeline.
- **Root cause:** Repository behavior was safe but its classification and retry lifecycle were
  distributed across the route and transition-plan service.
- **Affected code:** `app/services/pre_enqueue_operational_gate.py`,
  `app/services/pipeline_service.py`, and `app/routers/run_routes.py`.
- **Remediation:** The gate emits `VALID_AUDIT_RECORD` for a Run without a Pipeline and structured
  rejection data. A rejected active plan stays `RESERVED_REUSABLE_UNTIL_EXPIRY`; its context stays
  `RESERVED_UNOWNED_REUSABLE_WITH_PLAN`. The existing expiry sweep later marks an abandoned plan
  `EXPIRED`. A consumed-plan retry coalesces to the existing Pipeline before probing or creating
  artifacts.
- **Tests:** Operational rejection state, expired plan, unowned context, retry-before-expiry, and
  consumed-plan duplicate-action tests. Production Plan 3 and Context 7 were not manually changed.

## Blocking status

Blocking findings found: 3. Blocking findings fixed: 3. Blocking findings remaining: 0.
