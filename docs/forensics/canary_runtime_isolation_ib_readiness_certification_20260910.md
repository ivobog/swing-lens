# Canary Runtime Isolation and IB Readiness Certification — 2026-09-10

## A. Executive verdict

```text
CANARY RUNTIME ISOLATION CERTIFIED: YES
IB GATEWAY READINESS CONTRACT CERTIFIED: YES
PRE-ENQUEUE OPERATIONAL GATE CERTIFIED: YES
OPERATIONAL REMEDIATION CERTIFIED: YES
SAFE FOR ONE FINAL LIVE DUAL-MODE CANARY: YES
```

This certifies only the operational remediation. No live canary, production pipeline, production
business write, historical repair, or merge was performed. Temporal/PIT/lifecycle semantics were
left unchanged.

## B. Baseline

| Control | Observed result |
|---|---|
| Branch / implementation HEAD | `codex/canary-runtime-isolation-ib-readiness` / `3ec207614bfc364a32548fe5471c30968bb222f3` |
| Initial worktree | clean at `c706821afeb4187826f5b93873bd8e96cf82ca55` |
| Required ancestry | `c706821` and `d5accbf` are ancestors of the implementation |
| Code / production Alembic | `0072_ceri_artifact_context_lineage` / `0072_ceri_artifact_context_lineage` |
| Production identity | database `swinglens`, PostgreSQL 18.3, user `postgres`, `127.0.0.1:5432` |
| App / worker / supervisor | stopped / stopped / stopped |
| Port 8000 | no listener |
| Jobs | active 0; queued 0; retrying 0; recovering 0; scheduled/due 0 |
| Latest production IDs | Run 154 / Pipeline 145 / Job 42981 |
| Plan / context | Plan 3 persisted `RESERVED` but expired; Context 7 unowned |
| Winner settings | auto maturation enabled; auto cohort refresh enabled in normal production profile |
| CERI settings | enabled; provider ingest enabled; batched workflow enabled; run capture enabled; legacy path disabled |
| Other runtime settings | durable worker enabled; durable pipeline enabled; market prewarm enabled; pipeline capture enabled |
| Baseline time | `2026-09-10T18:19:52+02:00` Zurich / `16:19:52Z` / `12:19:52-04:00` New York |
| Closing IB probe | `IB_API_READY`; process/API ready; server version 176; current-time smoke returned; 419 ms; checked `2026-09-10T17:07:36.074698Z`; five-second expiry |

The expected production schema matched; the audit continued. The repository Run-154 report and
machine summary at evidence commit `c706821` were read before implementation.

## C. Run-154 reconstruction

Run 154 is the valid import/audit record for ADAM (`SAME_SESSION_REPLACEMENT`) and DRS
(`NEW_SESSION_CANONICAL_INITIALIZATION`). Plan 3 reserved Context 7. The earlier IB observation was
`READY` at `2026-09-10T13:59:32.838500Z`; the pre-enqueue observation at
`2026-09-10T14:00:13.971932Z` was `PROCESS_RUNNING_API_NOT_READY`. The existing hard stop left
Pipeline and Job uncreated. Startup independently created unrelated Winner Job 42981. Runs
148–153, protected price bars, CERI lineage, and all historical immutable evidence remained
unchanged.

## D. Winner Job 42981 root cause

The full creation/execution chain was:

```text
worker supervisor
  -> python -m app.worker
  -> background_worker.run_worker
  -> run_worker_once(schedule_winner_probability=True)
  -> schedule_primary_h5_maturation
  -> latest_completed_session(2026-09-09)
  -> enqueue_outcome_maturation_workflow(trigger=SCHEDULER)
  -> background_job_service.enqueue_job
  -> durable worker claim
  -> execute_outcome_maturation_job
  -> H5NextOpenOrchestrationService.drain_due
  -> Winner processing run 9999
```

Normal production configuration enabled automatic maturation, so every worker poll invoked the
session scheduler before claiming. No active job or session request key existed, allowing Job
42981 (`WINNER_OUTCOME_MATURATION`, request key
`winner:h5-next-open:session:2026-09-09`) to be created. Its due scan found zero eligible work:
due, processed, matured, retry, and material-evidence counts were zero; it terminated
`DRAIN_COMPLETE` / `NO_WORK_REMAINING`. Thus it wrote only enqueue/fanout/Job/processing-run control
records, not Winner evidence rows. Isolation failed because the prior guard observed the queue
before startup but neither constrained scheduler creation nor filtered claims. Finding:
`OPS-ISO-F001`.

## E. Certification runtime profile

`RUNTIME_MODE=CERTIFICATION` is explicit and fail-closed. It requires
`USE_DURABLE_PIPELINE=true` and `JOB_WORKER_ENABLED=true`, and rejects startup if Winner automatic
maturation, Winner automatic cohort refresh, or market-data prewarm is enabled. Effective runtime
configuration exposes the mode, disabled workflow list, allowed control activity, and authorized
root type.

Allowed activity is exactly web runtime bookkeeping; worker registration/heartbeat/control-loop
heartbeat; supervisor heartbeat; preflight control-row read/lock; an explicit `FULL_PIPELINE` root
marked `certification_authorized=true` with a positive transition-plan ID; and descendants whose
root correlation resolves to that authorized root. The central enqueue service rejects all other
job creation, and the durable claim query sees only that authorized lineage.

The invariants are:

- `OPS-INV-ISO-001`: before certification enqueue, no unrelated runnable, scheduled-due, or
  startup-created business job exists.
- `OPS-INV-ISO-002`: from certification startup to explicit enqueue, unrelated business-job count
  delta is zero.
- `OPS-INV-ISO-003`: explicit authorized canary work remains enqueueable and executable.

## F. Automatic-workflow inventory

| Workflow | Trigger | Startup-capable? | Creates jobs? | Business mutation possible? | Needed for canary? | Certification classification |
|---|---|---:|---:|---:|---:|---|
| Winner primary H5 maturation | worker poll scheduler | yes | yes | yes | no | `AUTOMATIC_BUSINESS_WORK`; disabled |
| Winner cohort refresh | maturation result/config or explicit route | indirectly | yes | yes | no | automatic path disabled; unrelated claims blocked |
| Winner latest rescore | explicit route / deferred same-job continuation | no | yes | yes | no | `EXPLICIT_USER_ACTION_ONLY`; unrelated claims blocked |
| Winner prediction capture | pipeline or explicit route | no independent tick | yes | yes | only if pipeline config requires | authorized pipeline lineage only |
| Winner revision/backfill/training/cache jobs | explicit/admin or workflow continuation | no | yes | yes | no | unrelated claims blocked |
| CERI provider/normalize/features/capture/change/alert chain | pipeline/admin roots and continuations | no independent startup tick found | yes | yes | configured pipeline subset | authorized pipeline lineage only |
| CERI backfill/repair/purge | explicit admin route | no | yes | yes | no | `EXPLICIT_USER_ACTION_ONLY`; unrelated claims blocked |
| Market-data prewarm | explicit route/CLI | no scheduler found | yes | cache mutation | no | config rejected; unrelated claims blocked |
| Provider prefetch/cache refresh | pipeline or explicit workflow | no independent startup tick found | possible | cache/business mutation | only inside pipeline | authorized lineage only |
| Durable evidence retention | periodic worker timer | yes | no new business job | deletes operational evidence | no | `CERTIFICATION_DISABLED` |
| Retry scheduler / due-work claim | every worker poll | yes | no | executes existing jobs | only authorized lineage | claim-filtered |
| Continuation jobs | running handler | indirectly | yes | yes | pipeline descendants only | central enqueue lineage check |
| Startup abandoned/stale recovery | worker poll | yes | no | changes job state/executes later | no | `CERTIFICATION_DISABLED` |
| Embedded worker | application lifespan legacy path | startup | no itself | can execute jobs | no | not used; certification requires durable worker |
| Durable worker | supervisor/CLI | startup | no itself | executes jobs | yes | `REQUIRED_CONTROL_PLANE`; one only |
| Worker supervisor | external runtime supervisor | startup | no | spawns worker | yes | `REQUIRED_CONTROL_PLANE`; heartbeat allowed |
| Web/resource/DB samplers and heartbeats | app runtime timers | yes | no business job | control/telemetry only | yes | `SAFE` control plane |

No independent automatic CERI, provider-prefetch, cache-refresh, or prewarm scheduler was found.
They are still denied at the shared enqueue/claim boundary unless they descend from the explicit
authorized pipeline.

## G. Startup isolation certification

Ten accelerated certification worker start/tick/stop cycles captured unrelated-job counts before
startup, after startup, after scheduler ticks, and before explicit action. Every delta was zero.
The PostgreSQL matrix included due Winner maturation, due cohort refresh, due rescore, due CERI
work, due prewarm, a retryable unrelated job, and a stale continuation. They remained queued and
unclaimed; certification did not delete, corrupt, recover, or execute them. The Winner scheduler
was suppressed, evidence cleanup did not run, and stale/abandoned recovery did not run.

The same due-state that created production Job 42981 was reproduced in the production clone before
remediation (new Job 42982), then held inert after remediation with zero new scheduler jobs. An
authorized `FULL_PIPELINE` root and child were claimable, proving the durable worker was not
globally disabled. This satisfies `OPS-INV-ISO-001` through `003`.

## H. IB readiness call graph

```text
run route
  -> pipeline_service.start_pipeline (certification)
  -> validate_pre_enqueue_operational_gate
  -> ib_gateway_health_service.check_status (fresh invocation)
  -> configured IB factory / ib_insync.IB.connect(host, port, clientId, timeout, readonly)
  -> client.isReady()                    # handshake / next-valid-ID-backed readiness
  -> client.serverVersion() > 0
  -> IB.reqCurrentTime()                 # bounded, read-only response smoke
  -> IB.isConnected() at completion
  -> disconnect in finally
```

Process enumeration is only a failure classifier. A process, open port, or successful executable
launch never establishes `IB_API_READY`. Existing Gateway auto-launch remains separate: manual
login/session setup must complete and the same full API probe must pass; no credentials or 2FA are
automated.

The old `READY` then later `PROCESS_RUNNING_API_NOT_READY` sequence is consistent with external IB
session/login state changing between independent probes. The remediation does not mask that flap:
it removes startup-result reuse and bases enqueue on a new full API observation.

## I. Authoritative IB_API_READY contract

The deterministic states are `IB_PROCESS_NOT_RUNNING`,
`IB_PROCESS_RUNNING_API_NOT_READY`, `IB_API_READY`, `IB_SESSION_LOST`, plus fail-closed
`CONFIG_ERROR`. Readiness requires all of: configured endpoint connects read-only; API handshake
reports ready; positive server version is available; `reqCurrentTime` returns; and the connection
survives through completion. Market-data or account permissions unrelated to this smoke are not
required.

Connect/request work uses the configured 3.0-second bounded health timeout. Failures classify API
unreachable/not ready, timeout, client-ID-in-use, session loss, or configuration error. The
connection is disconnected in `finally`, including failures. The result carries `checked_at`,
`expires_at`, process/API state, failure category/details, server version, smoke flag, and latency.
The gate accepts it for at most 5.0 seconds and evaluates freshness at the enqueue decision. A
single immediate full probe was selected; evidence did not justify a second probe or an arbitrary
sleep.

## J. Pre-enqueue operational gate

One service now owns this exact order immediately before creation:

1. Load and validate the Run-owned active `RESERVED` plan and its unowned, unexpired context.
2. Require effective certification runtime mode.
3. Require exactly one live durable worker with fresh worker and control-loop heartbeats and the
   `FULL_PIPELINE` queue.
4. Require zero unrelated `RUNNING`, runnable `QUEUED`, `RECOVERING`, or `RETRYING` jobs.
5. Require exactly one repository Alembic head and equality with the connected database.
6. Run and accept only a fresh `IB_API_READY` full API probe.
7. Lock and reverify the Decision Manifest plus pointer/evidence fingerprints.
8. Atomically consume the plan, bind the context, and create one Pipeline and authorized root Job.

Every rejection has a stable code, message, plan ID, context ID, and relevant worker/queue/IB
details. No Pipeline or root Job exists before all checks pass. The common enqueue service also
rejects unauthorized certification jobs.

## K. Run-without-pipeline semantics

Classification: `VALID_AUDIT_RECORD`. Run 154 records accepted import/fundamental evidence and a
pre-enqueue operational abort; it is not a PARTIAL pipeline and must not be deleted. Structured
gate output explicitly says no Pipeline was created and carries the rejection classification, so
the record cannot be mistaken for normal pipeline execution.

## L. Plan/context abort lifecycle

An operational rejection before enqueue does not consume or cancel a still-active reservation:
the plan is `RESERVED_REUSABLE_UNTIL_EXPIRY`, and the context is
`RESERVED_UNOWNED_REUSABLE_WITH_PLAN`. This permits a corrected retry with the same control
artifacts and avoids duplicate Run/plan/context creation. The normal expiry sweep later transitions
an abandoned plan to `EXPIRED`; expiration makes it unusable even if its persisted row has not yet
been swept.

Production Plan 3 is now time-expired but remains persisted `RESERVED` because the application and
expiry sweep are stopped; Context 7 remains unowned. Neither was manually repaired. A retry of an
already consumed plan returns its existing Pipeline before probing or creating artifacts.

## M. Failure injection

The disposable matrix passed for: startup API-ready then pre-enqueue unavailable; process alive
with incomplete login/API; process start followed by genuine readiness; session loss during smoke;
probe timeout; client-ID conflict; stale ready result; expired worker heartbeat; second worker;
due Winner/cohort/rescore/CERI/prewarm work; scheduler tick; unrelated runnable/retry/stale work;
schema mismatch; stale manifest/evidence; and duplicate operator action. Every failing gate returned
a deterministic classification with zero Pipeline/root-Job delta and zero unauthorized execution.

## N. Production-clone replay

A read-only PostgreSQL dump of the current production database was restored into disposable clone
`swinglens_cert_runtime_clone_20260910`; all mutations below occurred only there.

- Before remediation, one worker scheduler tick reproduced a new
  `WINNER_OUTCOME_MATURATION` Job 42982 (42,537 to 42,538 jobs).
- After remediation, the same due state plus scheduler request produced zero new jobs and did not
  claim Job 42982.
- With one clone worker, extended clone-only Plan 3/Context 7 reservation, and injected
  process-running/API-not-ready, the gate returned `IB_API_NOT_READY`; Pipeline and Job deltas were
  zero; plan stayed reserved and context unowned.
- With a genuine full API-ready result, the gate created exactly clone Pipeline 146 and authorized
  Job 42983, consumed the plan, and bound Context 7. It did not execute the pipeline.
- Repeating the consumed-plan action returned Pipeline 146 with coalescing and no duplicate.

The clone and approximately 792 MB temporary dump were deleted after verification. Production
source data remained intact and unchanged.

## O. Observability/operator behavior

Startup configuration and gate logs expose runtime mode, effective disabled automatic workflows,
allowed control activity, worker count/identity, queue-isolation state and unrelated count, IB
process/API state, check/expiry time, latency and failure category, gate result/reason, plan ID,
context ID, and schema head. No secret is emitted.

Process-present/API-incomplete returns:

```text
IB Gateway is running, but its API is not ready.
Complete login/session setup and retry. No pipeline was created.
```

It is not misreported as a worker failure.

## P. Tests

- Focused runtime/IB/gate/service regression: 123 passed, 1 warning.
- Disposable PostgreSQL certification plus transition preflight: 23 passed, 24 warnings.
- Dedicated certification PostgreSQL integration: 2 passed.
- Recorded-failure recheck: 429 passed, 25 deselected, 1 warning.
- Full non-slow regression: 2,356 passed, 9 skipped, 58 deselected, 23 warnings; exit 0.
- Ruff across `app` and `tests`: PASS. Ruff formatting for all 15 changed Python files: PASS. The
  repository-wide format audit also identified 190 unchanged legacy files that Ruff would
  reformat; no unrelated bulk-formatting change was made.
- Compileall across `app` and `tests`: PASS.
- Alembic heads: exactly `0072_ceri_artifact_context_lineage`.
- JSON parse, required A–S report sections, and `git diff --check`: PASS.

## Q. Production safety

```text
Runs 148–154 changed: NO
plan 3 manually repaired: NO
context 7 manually repaired: NO
production pipeline run: NO
production business writes: NO
historical repair: NO
merge: NO
```

All production database operations in this task were read-only. No live canary was run.
The closing snapshot again showed Run 154 / Pipeline 145 / Job 42981, zero active/queued/retrying/
recovering/due jobs, zero app/worker/supervisor processes, and zero port-8000 listeners.

## R. Residual risks

IB Gateway remains an external interactive dependency and can lose authentication or its API
session after any readiness probe. The narrowest safe mitigation is the fresh probe at the final
enqueue boundary; execution-time market-data guards remain responsible for later failures. The
five-second readiness lifetime limits, but cannot eliminate, that unavoidable interval.

Certification mode intentionally refuses dirty queues and conflicting automation rather than
draining or deleting them. Operators must start the documented isolated topology: one web app, one
durable worker, no embedded worker, and no unrelated runnable/due work. Production Plan 3 is
expired and must be handled by normal lifecycle semantics in a separately authorized canary task,
not manually repaired here.

## S. Final recommendation

The operational blockers have deterministic defenses at configuration, scheduler, enqueue, claim,
and final pre-enqueue boundaries. Use implementation commit `3ec2076`, activate the certification
profile with its required automatic settings disabled, establish exactly one ready durable worker,
and obtain a fresh `IB_API_READY` result through the unified gate. The repository is operationally
safe for one separately authorized final live dual-mode canary. This report does not authorize that
canary or a merge.
