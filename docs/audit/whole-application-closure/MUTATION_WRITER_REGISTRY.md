# Mutation writer registry

## Scope and reconciliation method

This registry joins the current 120 SQLAlchemy table mappings to exact persistence functions and recursively traced external/system callers. It revalidated, rather than merely trusting, the 115-table machine-readable reverse index in `docs/remediation/calculation-lineage/T14A_table_writer_reverse_index.csv`. The current schema has five additional tables: `acquisition_plan_records`, `work_scope_records`, `work_scope_members`, `refresh_cycle_records`, and `technical_source_manifests`. Those deltas are explicitly registered below.

The existing CSV remains the exhaustive per-physical-site annex for the original 115 tables. The IDs below are the normalized current writer families. When a table has multiple writers it appears in multiple rows and in the physical index with multiple IDs.

## Exact writer families

| ID | Table(s) | Exact writer function | Production entry chain(s) | Mode / authority / Tx |
| --- | --- | --- | --- | --- |
| WRITE-001 | `upload_runs`, `raw_company_rows` | `upload_service.create_upload_run` | upload POST | NORMAL/CERT web; upload content/hash; TX-01 |
| WRITE-002 | `pipeline_runs`, `pipeline_steps` | `pipeline_service.start_pipeline` | full-pipeline POST | pipeline/run/config/session; TX-02 |
| WRITE-003 | `pipeline_runs`, `pipeline_steps` | `pipeline_executor._pipeline_step`, `_mark_pipeline_*`, `_cancel_unfinished_steps` | FULL_PIPELINE handler | root job/pipeline/execution token; TX-05/17 |
| WRITE-004 | `pipeline_runs`, `pipeline_steps` | `pipeline_service.resume_pipeline`, `enqueue_pipeline_after_sec_repair`, `enqueue_pipeline_after_ceri_completion`, `_roll_up_ceri_pipeline_failure` | manual resume, SEC/CERI finalizers | pipeline/root correlation; TX-13 |
| WRITE-005 | `background_jobs` | `background_job_service.enqueue_job`, `_record_enqueue_attempt`, `record_coalesced_enqueue_attempt` | all APIs, pipeline, handlers, schedulers | causality/idempotency/session; TX-02/13/18 |
| WRITE-006 | `background_jobs` | `claim_next_job`, `heartbeat_job`, `record_job_progress`, `mark_job_*`, `mark_job_deferred`, `request_job_cancel` | durable worker and cancel APIs | worker/execution token; TX-04/17/21 |
| WRITE-007 | `background_jobs`, `pipeline_runs`, `pipeline_steps` | `fence_stalled_jobs`, `_interrupt_fenced_pipeline_steps`, `requeue_stalled_jobs` | supervisor watchdog | worker/status plus caller-composed session; TX-22/23 |
| WRITE-008 | `background_jobs`, `pipeline_runs`, `pipeline_steps` | `reconcile_jobs_for_worker_loss`, `_interrupt_fenced_pipeline_steps` | supervisor death/memory/shutdown | worker instance + certification claim when supplied; TX-22 |
| WRITE-009 | `background_jobs` | `recover_stale_jobs`, `recover_abandoned_jobs_for_worker`, `_recover_jobs` | NORMAL worker startup/loop | lease/worker/status; TX-23 |
| WRITE-010 | `background_jobs` | `ceri.backlog_cleanup_service.apply_legacy_ceri_backlog_cleanup` | maintenance CLI/service | explicit cleanup scope; caller commit |
| WRITE-011 | `background_job_enqueue_attempts`, `background_job_fanout_roots` | `_record_enqueue_attempt`, `_update_fanout_summary`, `prune_enqueue_attempt_evidence` | enqueue and retention loop/admin cleanup | operational causality/retention; TX-02/24 |
| WRITE-012 | `background_workers` | `worker_registry.register_worker`, `heartbeat_worker`, `heartbeat_worker_control_loop`, `mark_worker_stopping`, `retire_worker_registration`, `associate_worker_launcher` | worker/supervisor/lifecycle control | worker/process instance; TX-03/21/22 |
| WRITE-013 | `background_workers` | `lifecycle_quiesce.request_worker_quiesce`, `resume_worker_claims` | lifecycle ops scripts | worker identity/operator; caller commit |
| WRITE-014 | `background_supervisors` | `supervisor_registry.acquire_supervisor`, `heartbeat_supervisor`, `release_supervisor` | supervisor main loop | supervisor instance lease; TX-22 |
| WRITE-015 | `transition_preflight_plans` | `transition_preflight_plan_service.create/consume/cancel_transition_preflight`, `expire_abandoned_preflights` | run start, certification/admin | plan/session/frozen checks; TX-14 |
| WRITE-016 | `transition_decision_handoff_manifests` | decision handoff manifest persistence in pipeline executor/service | pipeline stage | pipeline/calculation/evidence/cutoff; TX-14 |
| WRITE-017 | `effective_configuration_records`, `execution_configuration_anchors`, `execution_configuration_bindings` | `configuration_delivery` persistence functions | pipeline/preflight | effective config digest and consumer binding; TX-05 |
| WRITE-018 | `acquisition_plan_records` | `work_scope_identity.persist_*` (`insert(AcquisitionPlanRecord)`) | pipeline and IB/Winner acquisition planning | immutable plan ID/fingerprint; TX-05/06 |
| WRITE-019 | `work_scope_records` | `work_scope_identity.persist_*` (`insert(WorkScopeRecord)`) | pipeline/refresh planning | scope ID/parent/plan; TX-05/06 |
| WRITE-020 | `work_scope_members` | `work_scope_identity.persist_*` (`insert(WorkScopeMember)`) | pipeline/refresh planning | exact ordinal membership; TX-05/06 |
| WRITE-021 | `refresh_cycle_records` | `work_scope_identity.persist_*` (`insert(RefreshCycleRecord)`) | IB fetch/refresh/pipeline | scope + prior refresh; TX-06 |
| WRITE-022 | `fundamental_scores` | canonical fundamental scoring persistence | FULL_PIPELINE | calculation/config/source evidence; TX-05 |
| WRITE-023 | `technical_scores` | `TechnicalScoreService` persistence path | FULL_PIPELINE | calculation/config/source manifest; TX-05 |
| WRITE-024 | `technical_source_manifests` | `TechnicalScoreService` source-manifest creation near `technical_score_service.py:600` | FULL_PIPELINE | immutable manifest digest; TX-05 |
| WRITE-025 | `technical_feature_artifacts` | `technical_artifact_cache.upsert_local_artifact`, `record_local_artifact_shadow_validation` | technical stage/cache maintenance | artifact kind/digest/status; caller transaction |
| WRITE-026 | `combined_results` | combined-score persistence service | FULL_PIPELINE | calculation and input evidence IDs; TX-05 |
| WRITE-027 | `ranking_results` | ranking/profile persistence service | FULL_PIPELINE / profile recalculation | calculation, profile config, upstream evidence; TX-05 |
| WRITE-028 | `market_calculation_contexts`, `market_regime_snapshots` | market context/regime services | FULL_PIPELINE | calculation/session/cutoff/source evidence; TX-05 |
| WRITE-029 | `sector_rotation_snapshots`, `sector_rotation_rows` | `SectorRotationRepository.save_snapshot` and `_to_row_model` | FULL_PIPELINE | calculation/ranking evidence; TX-05 |
| WRITE-030 | `price_bars`, `price_bar_revisions`, `price_series_versions` | `price_bar_repository`, `bar_cache_service.cache_bars` | pipeline, IB fetch, prewarm, explicit IB API | contract/plan/scope/refresh/source; TX-06 |
| WRITE-031 | `ib_contracts` | IB contract resolver/repository | pipeline, IB API, Winner market obligation | ticker/contract resolution authority; TX-06 |
| WRITE-032 | `ib_fetch_runs`, `ib_fetch_items` | `ib_fetch_plan_service`, `ib_fetch_executor`, `ib_fetch_summary_service`, `ib_fetch_recovery_service` | pipeline/run APIs/durable IB handler | plan/scope/refresh/job token; TX-06/07 |
| WRITE-033 | `core_calculation_evidence`, `core_calculation_evidence_sources`, `core_calculation_current_projections` | `core_mutation_authority`/immutable evidence services | core pipeline writers | calculation/config/exact source pins; TX-05 |
| WRITE-034 | `ceri_companies`, `ceri_company_aliases` | CERI identity resolver/company ensure/SEC identity repair | CERI ingest, batch setup, repair scripts | ticker/provider identity; TX-08 |
| WRITE-035 | `ceri_ingestion_runs`, `ceri_source_records`, `ceri_provider_request_telemetry` | `source_record_service.start/finish_ingestion_run`, source upsert, provider telemetry writer | CERI provider handlers/API | provider/dataset/request/run; TX-08 |
| WRITE-036 | `ceri_estimate_snapshots`, `ceri_earnings_actuals`, `ceri_guidance_events` | `normalization_service.normalize` and dataset-specific normalization | normalize handlers/backfill | source record/company/PIT cutoff; TX-09 |
| WRITE-037 | `ceri_catalyst_events`, `ceri_catalyst_event_revisions`, `ceri_catalyst_sources` | `normalization_service` catalyst persistence | normalize handlers/backfill | source/company/event identity; TX-09 |
| WRITE-038 | `ceri_processing_runs` | `processing_run_service.start/finish`; handler `_finish_processing_run` equivalents | all CERI jobs | job/type/run scope; TX-08..12 |
| WRITE-039 | `ceri_feature_build_states`, `ceri_revision_features`, `ceri_derived_features`, `ceri_price_response_features` | CERI feature rebuild and price-response services | feature handlers/recalculate/backfill | run/company/cutoff/source pins; TX-10 |
| WRITE-040 | `ceri_score_snapshots` | `capture_service.CeriRunCaptureService` | capture handler/pipeline | run/calculation/evidence/source bundle; TX-11 |
| WRITE-041 | `ceri_change_events` | `change_detection_service.CeriChangeDetectionService` via `CeriChangeRebuildService.rebuild` | change job/direct service | run/company/session/cutoff/exact bundle; TX-12 |
| WRITE-042 | `ceri_alert_rules` | CERI alert rule seed/config writer | alert initialization/admin | current-rules authority; TX-20 |
| WRITE-043 | `ceri_alert_events` | `CeriAlertService.persist_alert_for_change` | alert rebuild handler | change/revision evidence; TX-20 |
| WRITE-044 | `ceri_alert_events` | `CeriAlertService.acknowledge`, `dismiss` | direct local-admin API | row ID + local-admin; no certification-session gate; TX-20 |
| WRITE-045 | `ceri_alert_events` plus CERI source/derived rows | `CeriPurgeService._apply_purge_lifecycle` | licensed-data purge API/job | provider/license/preview hash/confirmation; TX-25 |
| WRITE-046 | `ceri_manual_reviews` and catalyst revision review field | `ceri_routes.review_ceri_event` | direct local-admin API | review target/human review context; no certification-session gate |
| WRITE-047 | `ceri_controlled_replays` | `controlled_replay_service.replay` | replay CLI/service | replay manifest/cutoff/source set; caller transaction |
| WRITE-048 | `ceri_purge_audits` | `CeriPurgeService.preview/execute` audit persistence | purge API/job | preview/confirmation/actor; TX-25 |
| WRITE-049 | `ceri_evidence_dispositions` | CERI evidence eligibility/disposition service | capture/purge/review flows | evidence ID/reason |
| WRITE-050 | `ceri_sec_filing_documents`, `ceri_sec_document_extractions`, `ceri_sec_sync_states` | SEC incremental ingestion/extraction/readiness repair | SEC repair handler/admin scripts | filing/source/processor signature; TX-07 |
| WRITE-051 | `ceri_sec_processor_releases` | `processor_lifecycle.register_deployed_processor` | every worker startup | deployed signature; global startup write; TX-03 |
| WRITE-052 | `ceri_sec_processor_releases` | `certify_processor`, `promote_processor` | explicit processor management CLI | actor/evidence/signature; caller transaction |
| WRITE-053 | `setup_signal_snapshots`, `setup_signal_snapshot_current_selections`, `setup_signal_snapshot_selection_events`, `signal_change_events` | setup signal capture/canonicalization/repository | pipeline and replay/repair | handoff/calculation/evidence; TX-15 |
| WRITE-054 | `setup_lifecycle_evaluation_runs`, `setup_lifecycle_episodes`, `setup_lifecycle_events` | setup lifecycle evaluator/episode service/repository | pipeline, evaluate, maintenance, replay/repair | run/signal evidence/original-context decision; TX-15 |
| WRITE-055 | `setup_lifecycle_evaluation_evidence`, `setup_lifecycle_transition_evidence` | lifecycle evidence repository | same as WRITE-054 | immutable evidence pins; TX-15 |
| WRITE-056 | `signal_alert_rules`, `signal_alert_rule_evidence` | `setup_lifecycle.alert_service.seed_builtin_rules` and rules writer | startup-on-use/alert service | current-rules config; TX-20 |
| WRITE-057 | `signal_alert_events`, `signal_alert_decision_evidence` | setup alert decision/persistence service | pipeline/alert rebuild | lifecycle/signal/rule evidence; TX-20 |
| WRITE-058 | `signal_alert_events` | repository `acknowledge_alert_event`, `dismiss_alert_event` | direct local-admin API | row ID/local-admin; no certification-session gate |
| WRITE-059 | `setup_lifecycle_administrative_audit_events` | lifecycle admin/replay/repair writers | admin APIs/jobs | actor/operation/target |
| WRITE-060 | `winner_prediction_snapshots`, `winner_prediction_episodes`, `winner_probability_estimates` | Winner capture/probability estimator | pipeline and Winner capture job | run/model/evidence/generation; TX-16 |
| WRITE-061 | `winner_estimate_evidence_members`, `winner_evidence_manifests`, `winner_evidence_manifest_members` | Winner evidence services | capture/publication | exact evidence membership/manifest digest; TX-16/19 |
| WRITE-062 | `winner_forward_outcomes`, `winner_target_stop_outcomes`, `winner_temporal_validity_decisions` | outcome/maturation services | maturation/revision/backfill jobs | prediction/outcome definition/session; TX-18 |
| WRITE-063 | `winner_outcome_definitions` | pending outcome/definition service | maturation setup/admin | versioned outcome policy |
| WRITE-064 | `winner_market_data_obligations` | market-data obligation service | maturation/IB fetch continuation | prediction/contract/required session; TX-18 |
| WRITE-065 | `winner_processing_runs` | Winner job handler `_start_processing_run`, `_finish_processing_run` | all Winner durable jobs | job/generation/run; TX-18/19 |
| WRITE-066 | `winner_cohort_definitions`, `winner_cohort_generations`, `winner_cohort_statistics`, `winner_calibration_bins`, `winner_drift_metrics`, `winner_cohort_refresh_state` | cohort generation/materialization/refresh services | cohort job/API/maturation planner | evidence watermark/generation/publication; TX-19 |
| WRITE-067 | `winner_estimate_publication_requests`, `winner_probability_estimates` | `estimate_publication_service.publish` | publication API/service | publication generation/request/model/evidence; TX-19 |
| WRITE-068 | `winner_model_versions`, `winner_model_training_runs`, `winner_model_lifecycle_events` | `model_registry` training/register/promote/retire | explicit model ops API/scripts | training sources/config/model generation |
| WRITE-069 | `winner_similarity_links` | Winner similarity service | supported direct service/diagnostics | prediction/evidence/model identity |
| WRITE-070 | `winner_training_eligibility_decisions`, `winner_training_outcome_replays` | pre-11 compatibility/training eligibility services | explicit compatibility CLI/service | retained prediction/outcome authority |
| WRITE-071 | all 15 `ib_*` intelligence tables | IBMI orchestration/repository/flex/journal services | six IBMI queue APIs and direct exclude-fill API | request/run/contract/fill identities; handler transactions |
| WRITE-072 | `ib_execution_fills`, `ib_trade_episodes`, `ib_trade_research_links` | `journal.exclude_execution_fill`, `rebuild_trade_episodes`, `match_episode_to_research` | direct API/services | fill/episode/research IDs; global rebuild reads |
| WRITE-073 | `engine_parameters` | none | none | intentionally read-only legacy table |

## Physical table index (all 120)

The comma-separated IDs mean the table has multiple independent writers and therefore appears more than once above.

| Domain | Tables -> writer IDs |
| --- | --- |
| Runtime/pipeline (15) | `upload_runs` -> 001; `raw_company_rows` -> 001; `pipeline_runs` -> 002,003,004,007,008; `pipeline_steps` -> 002,003,004,007,008; `background_jobs` -> 005,006,007,008,009,010; `background_job_enqueue_attempts` -> 011; `background_job_fanout_roots` -> 011; `background_workers` -> 012,013; `background_supervisors` -> 014; `transition_preflight_plans` -> 015; `transition_decision_handoff_manifests` -> 016; `effective_configuration_records` -> 017; `execution_configuration_anchors` -> 017; `execution_configuration_bindings` -> 017; `engine_parameters` -> 073. |
| Scope/core/market (21) | `acquisition_plan_records` -> 018; `work_scope_records` -> 019; `work_scope_members` -> 020; `refresh_cycle_records` -> 021; `fundamental_scores` -> 022; `technical_scores` -> 023; `technical_source_manifests` -> 024; `technical_feature_artifacts` -> 025; `combined_results` -> 026; `ranking_results` -> 027; `market_calculation_contexts` -> 028; `market_regime_snapshots` -> 028; `sector_rotation_snapshots` -> 029; `sector_rotation_rows` -> 029; `price_bars` -> 030; `price_bar_revisions` -> 030; `price_series_versions` -> 030; `ib_contracts` -> 031; `ib_fetch_runs` -> 032; `ib_fetch_items` -> 032; `core_calculation_evidence`, `core_calculation_evidence_sources`, `core_calculation_current_projections` -> 033. |
| CERI (28) | `ceri_companies`, `ceri_company_aliases` -> 034; `ceri_ingestion_runs`, `ceri_source_records`, `ceri_provider_request_telemetry` -> 035; `ceri_estimate_snapshots`, `ceri_earnings_actuals`, `ceri_guidance_events` -> 036; `ceri_catalyst_events`, `ceri_catalyst_event_revisions`, `ceri_catalyst_sources` -> 037; `ceri_processing_runs` -> 038,065-equivalent CERI handler lifecycle; `ceri_feature_build_states`, `ceri_revision_features`, `ceri_derived_features`, `ceri_price_response_features` -> 039; `ceri_score_snapshots` -> 040; `ceri_change_events` -> 041; `ceri_alert_rules` -> 042; `ceri_alert_events` -> 043,044,045; `ceri_manual_reviews` -> 046; `ceri_controlled_replays` -> 047; `ceri_purge_audits` -> 048; `ceri_evidence_dispositions` -> 049; `ceri_sec_filing_documents`, `ceri_sec_document_extractions`, `ceri_sec_sync_states` -> 050; `ceri_sec_processor_releases` -> 051,052. |
| Setup/lifecycle/alerts (17) | `setup_signal_snapshots`, `setup_signal_snapshot_current_selections`, `setup_signal_snapshot_selection_events`, `signal_change_events` -> 053; `setup_lifecycle_evaluation_runs`, `setup_lifecycle_episodes`, `setup_lifecycle_events` -> 054; `setup_lifecycle_evaluation_evidence`, `setup_lifecycle_transition_evidence` -> 055; `signal_alert_rules`, `signal_alert_rule_evidence` -> 056; `signal_alert_events`, `signal_alert_decision_evidence` -> 057,058; `setup_lifecycle_administrative_audit_events` -> 059. |
| Winner (27) | `winner_prediction_snapshots`, `winner_prediction_episodes`, `winner_probability_estimates` -> 060,067; `winner_estimate_evidence_members`, `winner_evidence_manifests`, `winner_evidence_manifest_members` -> 061; `winner_forward_outcomes`, `winner_target_stop_outcomes`, `winner_temporal_validity_decisions` -> 062; `winner_outcome_definitions` -> 063; `winner_market_data_obligations` -> 064; `winner_processing_runs` -> 065; `winner_cohort_definitions`, `winner_cohort_generations`, `winner_cohort_statistics`, `winner_calibration_bins`, `winner_drift_metrics`, `winner_cohort_refresh_state` -> 066; `winner_estimate_publication_requests` -> 067; `winner_model_versions`, `winner_model_training_runs`, `winner_model_lifecycle_events` -> 068; `winner_similarity_links` -> 069; `winner_training_eligibility_decisions`, `winner_training_outcome_replays` -> 070. |
| IB market intelligence (15) | `ib_execution_fills` -> 071,072; `ib_flex_import_runs`, `ib_histogram_bins`, `ib_histogram_snapshots`, `ib_historical_metric_bars`, `ib_historical_metric_revisions`, `ib_intelligence_features`, `ib_intelligence_request_items`, `ib_intelligence_runs`, `ib_market_intelligence_snapshots`, `ib_scanner_candidates`, `ib_scanner_parameter_cache`, `ib_scanner_runs` -> 071; `ib_trade_episodes`, `ib_trade_research_links` -> 071,072. |

## Bottom-up verdict

All 120 mappings are accounted for: 119 mutable table families have at least one production writer and mapped entry chain; one (`engine_parameters`) is intentionally read-only. There are no unknown callers. This is inventory closure, not safety closure: WRITE-007/009/044/046/051/058/072 retain authority or scalability findings in `LINEAGE_CLOSURE_GAP_REPORT.md`.
