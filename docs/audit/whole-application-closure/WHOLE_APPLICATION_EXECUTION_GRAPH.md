# Whole-application execution graph

Audit baseline: working tree at `b80dfccce72d8a00ab55f53a299d8c60da53ea58`, including the pre-existing uncommitted Run-166 remediation. This document is descriptive; it does not certify that the working tree is deployable.

## Mechanical boundary

The graph was generated from all 1,746 repository files, then reconciled against 191 FastAPI route registrations, 95 Python `__main__` entrypoints (5 application, 90 scripts), all calls to `enqueue_job`, all 34 registered durable handlers, all status assignments, SQLAlchemy persistence primitives, startup/lifespan hooks, and supervisor/worker call sites. Fifty-two route registrations use a mutating HTTP method. Forty-nine script entrypoints contain a persistence or transaction primitive and were manually classified as production administration, certification/QA, disposable-only, or false-positive read-only SQL execution.

## Top-level graph

```text
EXEC-001 upload POST
  -> EXEC-002 upload validation/raw-row persistence
  -> EXEC-003 full-pipeline enqueue
  -> EXEC-004 durable claim/lease
  -> EXEC-005 validation + frozen scope/config/acquisition authority
  -> EXEC-006 fundamentals
  -> EXEC-007 market-data acquisition / optional SEC readiness branch
  -> EXEC-008 technical scoring + source manifest
  -> EXEC-009 market-regime snapshot
  -> EXEC-010 combined results
  -> EXEC-011 rankings
  -> EXEC-012 sector rotation
  -> EXEC-013 CERI dispatch
       batched: ingest-batch -> normalize-batch -> feature-batch -> run-finalize
       legacy:  ingest -> normalize -> rebuild-features
       both:    capture -> change detection -> alert rebuild
  -> EXEC-014 CERI completion continuation
  -> EXEC-015 decision-handoff freeze
  -> EXEC-016 setup-signal capture
  -> EXEC-017 lifecycle evaluation + lifecycle alerts
  -> EXEC-018 Winner prediction capture
  -> EXEC-019 pipeline terminalization
       -> EXEC-020 Winner maturation/revision continuation
       -> EXEC-021 cohort refresh/materialization/publication
       -> EXEC-022 alert/publication/read-model consumers

Independent authorities:
EXEC-023 mutating/admin HTTP routes
EXEC-024 CLI/maintenance/backfill/certification scripts
EXEC-025 worker startup/periodic loop
EXEC-026 supervisor watchdog/death/shutdown loop
EXEC-027 retry/run_after/recovery dispatcher
EXEC-028 application lifespan and startup preflight (read-only business state)
EXEC-029 migration/bootstrap execution (external operator authority)
```

## Node registry

`Tx` is the material transaction boundary ID in `TRANSACTION_BOUNDARY_REGISTRY.md`. Authority abbreviations: `U` upload/run, `P` pipeline/root job, `C` calculation/configuration, `E` evidence/source bundle, `S` certification session, `W` worker lease/token, `G` generation/publication.

| ID | Exact production implementation | Caller / kind | Tx | Authority | Retry, continuation, cancellation, terminal behavior |
| --- | --- | --- | --- | --- | --- |
| EXEC-001 | `upload_routes.upload_file` -> `upload_service.create_upload_run` | HTTP, synchronous | TX-01 | upload content/hash | Request failure rolls back; creates `UploadRun` and raw rows. |
| EXEC-002 | `create_upload_run`, pipeline preflight validation | EXEC-001/003, synchronous | TX-01/05 | U | Upload becomes COMPLETED/FAILED; pipeline validation is separately terminalized. |
| EXEC-003 | `run_routes.run_full_pipeline_action` -> `pipeline_service.start_pipeline` -> `enqueue_job` | HTTP, asynchronous handoff | TX-02 | U,P,C; S in certification | Coalescing/idempotency key; certification root needs plan and session. |
| EXEC-004 | `background_job_service.claim_next_job` | worker | TX-04 | W; S predicate in certification | `QUEUED/RECOVERING -> RUNNING`; claim commit publishes lease. |
| EXEC-005 | `pipeline_executor.execute_full_pipeline` validation/preflight/scope setup | durable FULL_PIPELINE | TX-05 | P,C,U and frozen scope IDs | SEC deficiency branches to EXEC-007 repair; cancellation checks between stages. |
| EXEC-006 | pipeline executor -> fundamental scoring writer | synchronous stage | TX-05 | C,E,P | Re-execution guarded by canonical identities and source pins. |
| EXEC-007 | IB fetch plan/executor, price bar repository, `schedule_sec_readiness_repair` | sync plus async repair branch | TX-06/07 | acquisition plan, scope, refresh cycle, P | IB retry/resume/cancel; SEC repair enqueues continuation. |
| EXEC-008 | technical score service and artifact cache | synchronous stage | TX-05 | C, technical source manifest, price series | Pipeline retry may re-enter; immutable manifest must be revalidated after commit. |
| EXEC-009 | market regime service | synchronous stage or direct retired endpoint | TX-05 | C,E,P | Standalone mutation route rejects unbound writes. |
| EXEC-010 | combined score service | synchronous stage | TX-05 | C,E,P | Fail blocks/terminates stage. |
| EXEC-011 | ranking/profile service | synchronous stage | TX-05 | C,E,P | Persists profile-specific ranks; pipeline cancellation applies. |
| EXEC-012 | `SectorRotationRepository.save_snapshot` | synchronous stage | TX-05 | C,E,P | Snapshot plus rows in same caller transaction. |
| EXEC-013 | `_schedule_ceri_provider_ingest`, `schedule_ceri_batched_workflow`, CERI handlers | asynchronous DAG | TX-08..12 | P,C,E,S inherited from parent | Partial status, child coalescing, cancellation between batches, finalizer roll-up; CERI change domain work is atomic while progress/heartbeat/cancel use a detached control transaction. |
| EXEC-014 | `enqueue_pipeline_after_ceri_completion` / `_release_pipeline_after_ceri` | finalizer/callback | TX-13 | P, root correlation, S inherited | One continuation; CERI failure can roll up PARTIAL/FAILED. |
| EXEC-015 | transition preflight and handoff manifest service | synchronous stage | TX-14 | P,C,E, frozen cutoff/manifest | Manifest is terminal input to downstream calculation. |
| EXEC-016 | setup signal capture/repository | synchronous stage | TX-05 | C,E,P,handoff | Current selection and immutable selection event are written together. |
| EXEC-017 | lifecycle evaluator/repository/alert service | synchronous or durable maintenance job | TX-15 | C,E,P or explicit maintenance mode | Replay/repair APIs are independent entry chains; jobs may be PARTIAL. |
| EXEC-018 | Winner capture handler/service | pipeline stage or manual enqueue | TX-16 | P,C,E,model/generation | Partial allowed; certification descendants need explicit inherited session marker. |
| EXEC-019 | `_mark_pipeline_finished/failed/blocked/cancelled`, worker terminalizers | pipeline/worker | TX-17 | P,W | Cancels unfinished steps; background job terminal state committed afterward. |
| EXEC-020 | Winner maturation/revision/backfill/rescore handlers | scheduler, API, continuation | TX-18 | prediction/outcome authority, G | Can create bounded continuation chain; coalesced job keys. |
| EXEC-021 | cohort planner/generation/materialization/publication | API or maturation continuation | TX-19 | evidence watermark, G/publication generation | BUILDING -> READY -> PUBLISHED; supersedes prior generation. |
| EXEC-022 | CERI/setup alerts, Winner publication/read models | pipeline jobs or admin APIs | TX-20 | exact change/run/company/ticker/time authority; source evidence IDs; G | CERI alerts keyset-batch changes and scope cooldown history; ack/dismiss remain direct synchronous mutations. |
| EXEC-023 | 52 mutating route registrations | external local-admin/browser | route-specific | central runtime mutation capability plus domain authority | R1 classifies all writes and fails closed in certification. |
| EXEC-024 | 49 mutation-indicator CLI scripts | operator/process | script-specific | manifest/flags/database safety varies | Production ops scripts and disposable-only historical QA are separate chains. |
| EXEC-025 | `background_worker.run_worker/run_worker_once` | process startup/loop | TX-03/04/21 | W, process role; S claim in certification | Registers worker, registers SEC processor, NORMAL-only retention/recovery/scheduler. |
| EXEC-026 | `worker_supervisor.main/_supervise_once/_shutdown_owned_worker` | independent process loop | TX-22 | supervisor lease, worker identity; S passed in certification | Watchdog/death/memory/shutdown fence and requeue. |
| EXEC-027 | `mark_job_failed_or_retry`, `recover_*`, `requeue_stalled_jobs` | worker/supervisor/time | TX-23 | W/status; caller-composed S on some paths | Delay/backoff, RECOVERING, cancellation finalization. |
| EXEC-028 | `main.lifespan`, `serve.run_startup_preflight` | web process | none for business state | process role | Health/resource sampling is read-only with respect to pipeline data. |
| EXEC-029 | Alembic and external restore/bootstrap | explicit operator | external transaction | migration revision/operator | Not runtime reachable; may create/backfill schema/state under operator control. |

## Edge/reachability registry

| Edge | From -> to | Normal | Retry | recovery/startup/supervisor | scheduler/manual/finalizer |
| --- | --- | --- | --- | --- | --- |
| EDGE-001 | EXEC-001 -> 003 | yes | no | no | manual HTTP |
| EDGE-002 | EXEC-003 -> 004 | async | delayed job | claim after restart | manual pipeline POST |
| EDGE-003 | EXEC-004 -> 005 | yes | yes | stale/abandoned/supervisor requeue | no |
| EDGE-004 | EXEC-005 -> 006 -> 012 | yes | root retry | interrupted steps reset by resume/repair | reduced direct writers are retired |
| EDGE-005 | EXEC-007 -> SEC repair -> 005/008 | conditional | repair retry | continuation survives restart | finalizer callback |
| EDGE-006 | EXEC-012 -> 013 | conditional CERI | child retry | recovery claims same children | pipeline-created jobs |
| EDGE-007 | CERI ingest -> normalize -> feature -> finalize/capture | async | per child | durable queue recovery | handler-created continuation |
| EDGE-008 | capture -> change -> alert | async | per child | durable queue recovery | handler-created continuation |
| EDGE-009 | EXEC-013 -> 014 -> 015 | yes | continuation job retry | finalizer/restart | finalizer callback |
| EDGE-010 | EXEC-015 -> 016 -> 018 -> 019 | yes | root retry | manual resume | no |
| EDGE-011 | EXEC-018/019 -> 020 | conditional | delayed continuation | queue recovery | scheduler/manual API |
| EDGE-012 | EXEC-020 -> 021/022 | conditional | child retry | queue recovery | maturation continuation/cohort planner |
| EDGE-013 | EXEC-023 -> any enqueueable handler | manual | normal retry | queue recovery | API |
| EDGE-014 | EXEC-024 -> services/writers | manual | script-specific | replay/backfill/repair | CLI |
| EDGE-015 | EXEC-026 -> EXEC-027 -> EXEC-004 | no | no | watchdog/death/shutdown | supervisor |

## Background-job cross-check

Every registered type maps to creator/handler/recovery/cancellation/authority. All use the common claim and terminalization machinery (`EXEC-004`, `EXEC-019`, `EXEC-027`); cancellation is cooperative unless the job is still queued/recovering.

| Job type(s) | Creator(s) | Handler | Specific authority/continuation |
| --- | --- | --- | --- |
| `FULL_PIPELINE` | `start_pipeline`, `resume_pipeline`, SEC/CERI continuation | `_execute_full_pipeline_job` | pipeline/root correlation; certification root plan/session |
| `SEC_READINESS_REPAIR` | readiness repair scheduler | `_execute_sec_readiness_repair_job` | pipeline + scope/acquisition IDs; resumes pipeline |
| `IB_FETCH` | run API/pipeline fetch service | durable IB fetch handler | acquisition plan/scope/refresh; summary terminalization |
| `MARKET_DATA_PREWARM` | market-data API/prewarm service | prewarm handler | job/run scope; preemption/cancel supported |
| `CERI_PROVIDER_INGEST`, `CERI_PROVIDER_INGEST_BATCH` | CERI API/pipeline DAG | provider ingest handlers | provider/dataset/run/company scope; enqueue normalize |
| `CERI_NORMALIZE`, `CERI_NORMALIZE_BATCH` | ingest handlers | normalize handlers | source/ingestion scope; enqueue feature |
| `CERI_REBUILD_FEATURES`, `CERI_FEATURE_BATCH` | normalize/API | feature handlers | run/company/cutoff; enqueue finalizer/capture |
| `CERI_RUN_FINALIZE` | batched feature workflow | run finalizer | root/run completeness; enqueue capture |
| `CERI_CAPTURE_RUN` | pipeline/finalizer/API | capture handler | run/calculation/evidence identity; enqueue change |
| `CERI_CHANGE_DETECTION` | capture/API | change handler | run/company/session/cutoff; detached token-fenced progress/cancel; atomic domain commit; enqueue alert |
| `CERI_ALERT_REBUILD` | change handler/API | alert handler | exact change IDs or run/company/ticker/time SQL scope; 250-row keyset batches; releases pipeline |
| `CERI_BACKFILL` | CERI backfill API | backfill handler | explicit request scope |
| `CERI_PURGE_LICENSED_DATA` | purge preview/execute API | purge handler | provider/license/confirmation canonical ID manifest; exact 200-row locks in one atomic transaction |
| `IB_SCANNER_RUN` | IBMI API | scanner handler | request/run identity |
| `IB_FLEX_IMPORT` | IBMI API | flex handler | source report identity |
| `IB_HISTOGRAM_FETCH` | IBMI API | histogram handler | contract/request identity |
| `IB_INTELLIGENCE_LIVE_SNAPSHOT` | IBMI API | live snapshot handler | request/run identity |
| `IB_INTELLIGENCE_HISTORICAL_REFRESH` | IBMI API | historical refresh handler | contract/date scope |
| `IB_INTELLIGENCE_REBUILD_FEATURES` | IBMI API | rebuild handler | run/ticker scope |
| `SETUP_LIFECYCLE_EVALUATE_RUN` | pipeline/setup API | evaluate handler | run/handoff/evidence identity |
| `SETUP_LIFECYCLE_DAILY_MAINTENANCE` | setup API | maintenance handler | explicit maintenance scope |
| `SETUP_LIFECYCLE_REPAIR_TICKER` | setup repair API | repair handler | ticker + retained original context |
| `SETUP_LIFECYCLE_REPLAY` | setup replay API | replay handler | manifest/run/original-context decision |
| `SETUP_ALERT_REBUILD` | setup API/finalizer | alert rebuild handler | signal/lifecycle evidence scope |
| `WINNER_PREDICTION_CAPTURE` | pipeline/Winner API | capture handler | run/model/evidence/generation |
| `WINNER_OUTCOME_MATURATION` | scheduler/API/capture | maturation handler | prediction/outcome definition; may continue |
| `WINNER_OUTCOME_REVISION_CHECK` | maturation workflow | revision handler | prediction/outcome revision identity |
| `WINNER_COHORT_REFRESH` | API/planner | cohort handler | evidence watermark/generation |
| `WINNER_HISTORICAL_BACKFILL` | Winner API/ops | backfill handler | explicit range/scope |
| `WINNER_LATEST_RESCORE` | Winner API/ops | rescore handler | latest retained evidence/model |
| `WORKER_RECOVERY_PROBE` | operational tests/ops | probe handler | operational-only worker token |

`WINNER_MODEL_TRAINING` and `WINNER_SIMILARITY_CACHE` are constants for deliberately disabled handlers, not registered job types; direct tests assert their absence. They are therefore not unmatched queue types.

## Reconciliation result

Top-down nodes reach every one of the 119 mutable SQLAlchemy table families. `engine_parameters` is the single intentional read-only/legacy mapping. The apparent static orphan `sector_rotation_rows` resolves interprocedurally through `SectorRotationRepository.save_snapshot -> _to_row_model -> add_all`. The five tables added after the T14A census are reached through `work_scope_identity` (four) and `technical_score_service` (one). No unknown production writer caller remains. R1 authority and R2 bounded-access inventories reconcile; R3 generated drift enforcement and R4 aggregate budgets remain, so this is not a live-canary approval.
