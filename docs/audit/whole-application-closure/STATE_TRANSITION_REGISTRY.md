# State-transition registry

Mechanical search found 229 status-like field assignments in production Python. The registry below normalizes them into legal transition families and retains every independent mutating function for the pipeline-critical entities. Computed status functions are listed by function name because their value depends on persisted outcomes.

## BackgroundJob

| ID | Transition | Every production mutator | Authority / notes |
| --- | --- | --- | --- |
| STATE-001 | absent -> `QUEUED` | `background_job_service.enqueue_job`, `_enqueue_pre_migration_job` | creator causality, idempotency key; certification enqueue gate when active |
| STATE-002 | `QUEUED`/`RECOVERING` -> `RUNNING` | `claim_next_job` / `_apply_running_job_update` | row lock, queue, `run_after`, worker ID, new execution token; certification claim predicate |
| STATE-003 | `RUNNING` -> `RUNNING` (heartbeat/progress) | `heartbeat_job`, `record_job_progress`, handler `_save_checkpoint`/`_checkpoint` paths | execution token and lease owner |
| STATE-004 | `RUNNING` -> `COMPLETED` | `mark_job_completed` | execution token; worker after handler success |
| STATE-005 | `RUNNING` -> `PARTIAL` | `mark_job_partial`; CERI batched/legacy handlers, setup handlers, Winner handlers may first set `job.status` | execution token at terminalizer; handler-set PARTIAL is interpreted by worker |
| STATE-006 | `RUNNING` -> `FAILED` | `mark_job_failed_or_retry` when deterministic/exhausted; handler-specific failure roll-up | execution token; failure classification |
| STATE-007 | `RUNNING` -> `BLOCKED` | `mark_job_blocked`; `scope_refresh_adoption.record_zero_progress` | execution token or scoped job object |
| STATE-008 | `RUNNING` -> `QUEUED` (retry) | `mark_job_failed_or_retry` | retryable classification, retry count/max, delayed `run_after`, ownership cleared |
| STATE-009 | `RUNNING` -> `QUEUED` (defer) | `mark_job_deferred` | execution token, explicit delay/reason |
| STATE-010 | `RUNNING` -> `STALLED` | `fence_stalled_jobs`; `reconcile_jobs_for_worker_loss` (`fence_jobs_for_worker` compatibility wrapper) | supervisor watchdog or proven worker loss; certification scope is caller-supplied |
| STATE-011 | `STALLED` -> `RECOVERING` | `requeue_stalled_jobs` | ID/status only; recovery count increment; GAP-003 |
| STATE-012 | `RUNNING` -> `QUEUED` | `recover_stale_jobs`, `recover_abandoned_jobs_for_worker` -> `_recover_jobs` | NORMAL worker only; lease/heartbeat or worker identity |
| STATE-013 | `RUNNING` -> `STALE` | `_recover_jobs` after max recovery count | lease/worker identity; terminal timestamp set |
| STATE-014 | `QUEUED` -> `CANCELLED` | `request_job_cancel` | job ID/local admin or parent pipeline cancel |
| STATE-015 | `RUNNING` -> requested cancellation | `request_job_cancel` sets `requested_cancel`; `market_data_prewarm_service.request_active_prewarm_preemption` | cooperative until handler/worker observes it |
| STATE-016 | `RUNNING` -> `CANCELLED` | `mark_job_cancelled`; `_finalize_recovery_cancellation` delegates for running job | execution token normally required |
| STATE-017 | `STALLED`/`RECOVERING` -> `CANCELLED` | `request_job_cancel`, `_finalize_recovery_cancellation`, recovery/fence loops when request flag exists | prevents cancelled work from resurrection |
| STATE-018 | prewarm `PARTIAL` -> resumable job state | `_prepare_preempted_job_for_resume` | clears requested-cancel under explicit prewarm resume semantics |
| STATE-019 | legacy active -> `CANCELLED` | `apply_legacy_ceri_backlog_cleanup` | explicit maintenance operation |

There is no production transition from a terminal `COMPLETED`, `FAILED`, `BLOCKED`, or `CANCELLED` job back to runnable state in the common service. Manual reruns create new jobs. `STALE` is a code-defined terminal-like state even though it is absent from the requested example list.

## PipelineRun and PipelineStep

| ID | Entity transition | Every mutator |
| --- | --- | --- |
| STATE-020 | absent -> `PENDING` | `pipeline_service.start_pipeline` |
| STATE-021 | `PENDING` -> `PREPARING` | `sec.readiness_repair.schedule_sec_readiness_repair`, `_update_progress` |
| STATE-022 | `PENDING/PREPARING` -> stage-specific running status / `RUNNING` | `pipeline_executor._mark_pipeline_running`, `_pipeline_step` |
| STATE-023 | running -> `WAITING_FOR_CERI_COMPLETION` | `_mark_pipeline_waiting_for_ceri` |
| STATE-024 | waiting/failed continuation state -> `PENDING` | `resume_pipeline`, `enqueue_pipeline_after_sec_repair`, `enqueue_pipeline_after_ceri_completion` |
| STATE-025 | running/waiting -> `COMPLETED`/`PARTIAL` | `_mark_pipeline_finished` with computed status |
| STATE-026 | any active -> `FAILED` | `_mark_pipeline_failed`, `mark_pipeline_job_failure`, `_roll_up_ceri_pipeline_failure` |
| STATE-027 | active -> `BLOCKED` | `_mark_pipeline_blocked`, SEC `_mark_pipeline_unresolved`, repair failure roll-up |
| STATE-028 | active -> `CANCELLED` | `pipeline_service.cancel_pipeline`, `_mark_pipeline_cancelled`, SEC `_guard_cancel`, startup cancellation in `start_pipeline` |
| STATE-029 | step absent/`PENDING` -> `RUNNING` | `_pipeline_step` |
| STATE-030 | step `RUNNING` -> `COMPLETED`/`FAILED`/`BLOCKED`/`CANCELLED` | `_pipeline_step`; `_apply_winner_step_outcome` |
| STATE-031 | step active -> `INTERRUPTED` | `_interrupt_superseded_running_steps`; background-job `_interrupt_fenced_pipeline_steps` |
| STATE-032 | step interrupted/terminal -> `PENDING` | `_pipeline_step` retry preparation; SEC `_reset_pipeline_step`; CERI/SEC continuation functions |
| STATE-033 | unstarted step -> `SKIPPED` | `execute_full_pipeline` conditional ranking path |
| STATE-034 | unfinished steps -> `CANCELLED` | `_cancel_unfinished_steps`; pipeline cancellation/terminal roll-up |

## CERI workflow state

| ID | Transition family | Mutators / values |
| --- | --- | --- |
| STATE-035 | ingestion absent -> running -> success/partial/failed | `source_record_service.start_ingestion_run`, `finish_ingestion_run(status=...)`; provider handlers |
| STATE-036 | processing absent -> running -> completed/partial/failed | `processing_run_service.start`, `finish`; all CERI handlers |
| STATE-037 | feature-build state create/update | feature rebuild services and batched handlers | run/company/version/source authority |
| STATE-038 | source/normalized immutable revision create | source record and normalization services | duplicates coalesce; catalyst current revision is superseded by `normalization_service` |
| STATE-039 | catalyst revision current -> non-current; new revision -> current | `normalization_service.normalize` | current code computes next revision after a global read (READ-013) |
| STATE-040 | CERI job running -> PARTIAL | provider/feature/capture/change/backfill/batch handlers | failed item counts; worker later terminalizes PARTIAL |
| STATE-041 | alert active -> `ACKNOWLEDGED`/`DISMISSED` | `CeriAlertService.acknowledge`, `dismiss` | direct local-admin mutation |
| STATE-042 | CERI artifacts/alerts -> invalidated/quarantined/deleted lifecycle | `CeriPurgeService._apply_purge_lifecycle` | preview hash + confirmation token + immutable-evidence block |
| STATE-043 | controlled replay absent -> running/result status | `CeriControlledReplayService.replay` | manifest status and completed timestamp |
| STATE-044 | SEC processor absent -> `DEPLOYED` | `register_deployed_processor`, `certify_processor` if missing | startup/direct management |
| STATE-045 | `DEPLOYED` -> `CERTIFIED` | `certify_processor` | actor/evidence |
| STATE-046 | `CERTIFIED` -> `ACTIVE`; prior `ACTIVE` -> `RETIRED` | `promote_processor` | locks all releases; explicit actor |

## Winner state

| ID | Transition | Every mutator |
| --- | --- | --- |
| STATE-047 | processing run absent -> running -> result status | `_start_processing_run`, `_finish_processing_run` in Winner job handlers |
| STATE-048 | prediction absent -> captured/current episode | prediction capture and episode services |
| STATE-049 | outcome absent -> `PENDING`/`EXCLUDED` or calculated result | `outcome_service._mark_pending`, `_mark_excluded`, calculation methods |
| STATE-050 | market obligation absent/current -> `IDENTITY_BLOCKED`/`FETCH_REQUIRED`/`SATISFIED` | `market_data_obligation_service.evaluate` |
| STATE-051 | obligation fetch -> `UNAVAILABLE`/`FAILED`/satisfied result | `record_fetch_results` |
| STATE-052 | cohort generation absent -> `BUILDING` | `cohort_generation_service.capture_or_resume` |
| STATE-053 | `BUILDING` -> `READY` | `cohort_materialization_service.materialize_slice` |
| STATE-054 | `READY` -> `PUBLISHED`; prior `PUBLISHED` -> `SUPERSEDED` | `cohort_generation_service._activate_locked` |
| STATE-055 | active generation -> `CANCELLED` | `cohort_materialization_service._cancel` |
| STATE-056 | estimate candidate -> `PUBLISHED`; prior published -> `SUPERSEDED` | `estimate_publication_service.publish` |
| STATE-057 | model candidate/certified -> `ACTIVE` | `model_registry.promote_model` |
| STATE-058 | active model -> `RETIRED` | `retire_model`, `_retire_active_replaced_models` |

## Setup/lifecycle/alerts

| ID | Transition | Every mutator |
| --- | --- | --- |
| STATE-059 | evaluation run absent -> running -> result status | setup repository create/`complete_evaluation_run` and evaluate handler |
| STATE-060 | lifecycle episode absent/active -> state revisions | evaluator, `episode_service`, repository canonical revision writers |
| STATE-061 | active episode -> `CLOSED` | `episode_service._close_episode` |
| STATE-062 | legacy active episode -> `LEGACY_RETIRED` | `_retire_legacy_active_episodes` |
| STATE-063 | setup/lifecycle job -> PARTIAL | `execute_evaluate_run_job`, `execute_daily_maintenance_job` |
| STATE-064 | signal alert active -> `ACKNOWLEDGED`/`DISMISSED` | repository `acknowledge_alert_event`, `dismiss_alert_event` |

## Certification/runtime/worker state

| ID | Transition | Every mutator |
| --- | --- | --- |
| STATE-065 | preflight plan created -> `CONSUMED`/`CANCELLED`/`EXPIRED` | transition preflight plan service |
| STATE-066 | supervisor absent/stale -> acquired -> heartbeat -> released | supervisor registry acquire/heartbeat/release |
| STATE-067 | worker absent/stale -> registered -> heartbeat | worker registry register/heartbeat/control-loop heartbeat |
| STATE-068 | worker active -> stopping/quiesced -> retired/resumed | worker registry + lifecycle quiesce + supervisor retirement |
| STATE-069 | certification job unauthorized -> rejected (no transition) | `require_enqueue_authorized`, `claim_next_job` certification predicate |
| STATE-070 | certification root/descendant -> normal job terminal states | common worker terminalizers | session marker is payload authority; supervisor recovery now scopes when supplied |

## Transition ownership finding

Background-job recovery has more than one owner by design: worker stale recovery, worker abandoned recovery, supervisor watchdog, supervisor proven-loss reconciliation, shutdown reconciliation, and manual cancellation all overlap the same rows. Row locks and execution-token invalidation prevent simultaneous commits, but session authority is not intrinsic to all primitives. `requeue_stalled_jobs` and the compatibility `fence_jobs_for_worker` accept status/IDs without a required certification authority object. This is GAP-003, not an unknown transition.
