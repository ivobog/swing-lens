# T14C — Decision and Winner writer contract adoption

T14C VERDICT: **PASS**. Previous T14C failure resolved: **YES**.

All 41 assigned writer families and 84 assigned initiators are reconciled. Classification: 27 PHASE5_CONTRACT_ENFORCED, 12 SUPPORTED_DISTINCT_SAFE, 2 CURRENT_RULES_EXPLICIT; zero legacy-noncertified, caller-pending writer, defect, unknown or potential-bypass families. Supporting/current-rule classification describes native business meaning; these boundaries are enforced and certified. It does not confer historical financial authority.

Branch: `codex/t14c-decision-winner-writer-adoption`. Starting HEAD: `d56fe385e29b7b6233d2df5031a40a576db0ca7f`. Final HEAD/commit: resolve `git rev-parse HEAD` after the single explicit-path PASS-only commit containing this report. No push or merge.

## Baselines

| Baseline | Commit |
|---|---|
| original_audited | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| phase2 | `7b015124807b8f895911d7e790fcbf01949ff85f` |
| phase3 | `5422bcdf7703db891810d9e9c20a8f1770241fc9` |
| phase4 | `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8` |
| t14a | `5eef0d08d783de132fecb14c5dedd66821f0949f` |
| t14b | `d56fe385e29b7b6233d2df5031a40a576db0ca7f` |

## Exact writer reconciliation

| Family | Final classification | Canonical owner |
|---|---|---|
| WF_CERI_ALERT_CREATION | PHASE5_CONTRACT_ENFORCED | `app/services/ceri/alert_service.py:CeriAlertService.persist_alert_for_change` |
| WF_CERI_ALERT_STATUS | SUPPORTED_DISTINCT_SAFE | `app/services/ceri/alert_service.py:CeriAlertService.acknowledge` |
| WF_CERI_CHANGE | SUPPORTED_DISTINCT_SAFE | `app/services/ceri/change_detection_service.py:CeriChangeDetectionService._persist_change` |
| WF_LIFECYCLE_EPISODE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/episode_service.py:SetupLifecycleEpisodeService._update_episode` |
| WF_LIFECYCLE_EVALUATION_EVIDENCE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/decision_evidence.py:persist_lifecycle_evaluation_evidence` |
| WF_LIFECYCLE_OBSERVATION_GAP | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/decision_evidence.py:persist_observation_gap_evaluation_evidence` |
| WF_LIFECYCLE_TRANSITION_EVIDENCE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/decision_evidence.py:persist_lifecycle_transition_evidence` |
| WF_SETUP_ALERT_EVENT_STATE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_alert_event` |
| WF_SETUP_ALERT_RULE_BOOTSTRAP | SUPPORTED_DISTINCT_SAFE | `app/services/setup_lifecycle/alert_service.py:SetupLifecycleAlertService.seed_builtin_rules` |
| WF_SETUP_ALERT_RULE_EVIDENCE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/decision_evidence.py:persist_alert_rule_evidence` |
| WF_SETUP_ALERT_RULE_STATE | SUPPORTED_DISTINCT_SAFE | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.upsert_alert_rule` |
| WF_SETUP_CHANGE_EVENT_STATE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_signal_change_event` |
| WF_SETUP_CURRENT_SELECTION | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.advance_canonical_selection` |
| WF_SETUP_DECISION_EVIDENCE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/decision_evidence.py:persist_alert_decision_evidence` |
| WF_SETUP_DERIVED_PURGE | SUPPORTED_DISTINCT_SAFE | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository._delete_selected` |
| WF_SETUP_EVALUATION_RUN_STATE | SUPPORTED_DISTINCT_SAFE | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.create_evaluation_run` |
| WF_SETUP_PERSISTENCE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.upsert_snapshot` |
| WF_SETUP_TRANSITION_EVENT_STATE | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_lifecycle_event` |
| WF_SETUP_TYPED_REPOSITORY | PHASE5_CONTRACT_ENFORCED | `app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add` |
| WF_WINNER_CALIBRATION | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/calibration_service.py:CalibrationService.persist_bins` |
| WF_WINNER_CAPTURE_TRAINING_METADATA | SUPPORTED_DISTINCT_SAFE | `app/services/winner_probability/training_eligibility.py:TrainingEligibilityPolicy.persist_capture_decision` |
| WF_WINNER_COHORT_DEFINITION | SUPPORTED_DISTINCT_SAFE | `app/services/winner_probability/cohort_definition.py:CohortDefinitionService.ensure_definition` |
| WF_WINNER_COHORT_MATERIALIZATION | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/cohort_materialization_service.py:CohortMaterializationService.materialize_slice` |
| WF_WINNER_DRIFT | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/drift_service.py:DriftService.persist_metrics` |
| WF_WINNER_EPISODE | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/episode_service.py:WinnerEpisodeService.assign_episode` |
| WF_WINNER_ESTIMATOR | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/probability_estimator.py:ProbabilityEstimator._create_estimate` |
| WF_WINNER_EVIDENCE_MANIFEST | SUPPORTED_DISTINCT_SAFE | `app/services/winner_probability/evidence_manifest_service.py:EvidenceManifestService.create_or_get_manifest` |
| WF_WINNER_FORWARD_MATURATION | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/outcome_service.py:OutcomeMaturationService.process_forward_outcome` |
| WF_WINNER_GENERATION | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/cohort_generation_service.py:CohortGenerationService.capture_or_resume` |
| WF_WINNER_INLINE_COHORT | CURRENT_RULES_EXPLICIT | `app/services/winner_probability/probability_estimator.py:ProbabilityEstimator._materialize_cohort_statistic` |
| WF_WINNER_LATEST_RESCORE | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/probability_estimator.py:ProbabilityEstimator.create_latest_rescore_from_generation` |
| WF_WINNER_MARKET_DATA_OBLIGATION | SUPPORTED_DISTINCT_SAFE | `app/services/winner_probability/market_data_obligation_service.py:MarketDataObligationService.ensure_for_outcomes` |
| WF_WINNER_MODEL_GOVERNANCE | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/model_registry.py:ModelRegistry.register_model` |
| WF_WINNER_MODEL_TRAINING | CURRENT_RULES_EXPLICIT | `app/services/winner_probability/model_training.py:ShadowModelTrainingService.persist_training_run` |
| WF_WINNER_OUTCOME_REVISION | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/outcome_revision_service.py:OutcomeRevisionService.upsert_forward_revision` |
| WF_WINNER_PENDING_OUTCOME | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/pending_outcome_service.py:PendingOutcomeService._ensure_forward_outcome` |
| WF_WINNER_PREDICTION_CAPTURE | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/capture_service.py:WinnerPredictionCaptureService._capture_ticker` |
| WF_WINNER_PUBLICATION | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/estimate_publication_service.py:WinnerEstimatePublicationService.publish` |
| WF_WINNER_SIMILARITY | SUPPORTED_DISTINCT_SAFE | `app/services/winner_probability/similarity_service.py:SimilarityService.persist_neighbors` |
| WF_WINNER_TYPED_REPOSITORY | PHASE5_CONTRACT_ENFORCED | `app/services/winner_probability/repository.py:WinnerProbabilityRepository.add` |
| WF_WINNER_WATERMARK | SUPPORTED_DISTINCT_SAFE | `app/services/winner_probability/cohort_generation_service.py:EvidenceWatermarkService._locked_state` |

Each certificate record pins the canonical source SHA-256, native certification lane, immutable/current projection policy, owned member sinks, parallel owners and exact initiator IDs. Source discovery and historical T14A/T14B dispositions remain distinct from current T14C certification.

## Writer boundaries and parity

Native replay is a retrospective operation under its own full configuration and execution binding. The retained original Setup pipeline remains a source pin; it does not own the replay configuration. LIVE pipeline ownership is unchanged.

Inherited eligibility fixtures retain their hand-built facts as algorithm/read-history coverage and explicitly reject current financial writes without native proof. Test-only T14B Setup/alert serializers create retained historical fixture ledgers; they grant no native creation proof and are excluded from native certification. Canonical selection and positive market-data obligations now use actual native financial producers and native Winner captures. No production validator is replaced in the native certification lanes.

Setup single/batch writers validate exact retained input bodies, full frozen configuration, calculation identity, native eligibility, decision/session time and execution ownership before deduplication or persistence. Lifecycle evaluation, transition, episode and gap writers validate native evaluation math and exact predecessor/target. A duplicate retains the original evidence and projection reasons. Maintenance is an explicit current operation with its own aware clock and supporting evidence.

Alerts validate native source events, frozen rules, actual conditions and bounded cooldown/predecessor history. Native PostgreSQL locking proves concurrent duplicate/cooldown behavior. Rule bootstrap/state and human acknowledgement remain separate supporting operations. Transport cannot supply financial eligibility or original identity.

CERI change validates actual SQL normalized/raw/score/company source bodies, full applicable configuration and native delta/guidance math. Rebuild is an explicit operation; its time does not replace original calculation time. Downstream alerts consume the retained native change proof.

Winner prediction derives and seals the original native feature vector. Original estimates pin prediction birth configuration and native model/outcome authority. Latest rescoring creates separate evidence under explicit operation rules and cannot rewrite the original vector. Immutable financial bodies are checked against retained SQL and creation proofs, including nested JSON.

Model registration pins exact native training artifacts. Promotion consumes exact applicable calibration and complete four-metric drift/configuration scope, native sufficiency and explicit active predecessor. Retirement retains distinct governance meaning without invented diagnostic dependencies. Unsupported trained serving fails closed.

Outcome maturation/revision validates exact native prediction, target/policy, calendar/horizon, explicit operation cutoff, actual price content and retained revision IDs/new values. Fresh native forward and target/stop recomputation prevents injected result authority. Existing revision truth is retained; corrections create linked revisions. WIN-006 remains partial for birth bars without revision IDs; WIN-008 target-set freezing remains open.

Inline cohort is explicit current-rules computation. Generation materialization uses the exact frozen native population, hierarchy and both cohort/generation configuration snapshots, with native statistic recomputation. Manifests and watermarks are supporting ledgers. Generation birth and completion are sealed; canonical keys, monotonic watermarks, incomplete rejection and publication ordering remain unchanged.

Publication validates exact generation/completion/manifests, every native candidate/original body, explicit published predecessor, reviewed approval and serving hashes before request deduplication. Reviewed replacement preserves F0 request/stage fencing and atomic serving transitions. Failure after a statistic flush or generation switch leaves no partial new state.

All lower helpers require their exact semantic owner and the same Session. Caller autoflush occurs before owner permission is granted. Mapper rejection unwinds flush and the outer wrapper rolls back the whole semantic Session. SQLite explicitly begins the outer transaction before savepoints. Native PostgreSQL actual enqueue/claim/expiry/recovery/reclaim proves old token T1 cannot commit and current token T2 can commit with identical semantic rules.

No certified writer repairs missing authority using a current/latest lookup. An exact already-validated primary key may be refreshed after its logical lock, followed by retained-proof validation. Mutable selection, serving, status and lifecycle pointers remain distinct from immutable historical financial meaning. No formulas or thresholds changed.

## Four former potential bypasses

| Family | Status and actual consumption |
|---|---|
| WF_WINNER_CALIBRATION | PHASE5_CONTRACT_ENFORCED: native held-out calculation/report; exact model-promotion pin; no direct estimate/publication input |
| WF_WINNER_DRIFT | PHASE5_CONTRACT_ENFORCED: all four native metrics/populations and configuration/sufficiency/freshness; exact promotion-governance pin |
| WF_WINNER_MODEL_TRAINING | CURRENT_RULES_EXPLICIT: native walk-forward artifact from explicitly classified historical facts; exact registration pin; no invented original identity |
| WF_WINNER_SIMILARITY | SUPPORTED_DISTINCT_SAFE: native exact manifest/ranking/configuration/cutoff; diagnostic display links cannot mutate feature/cohort/serving authority |

All four have native positive, altered-source/configuration and retained-proof checks plus actual consumer-role review. Missing-context rejection alone was not used to clear them.

## Final validation

| Lane | Passed | Skipped | Failures/errors | Seconds |
|---|---:|---:|---:|---:|
| t14c-final-broader | 3450 | 7 | 0/0 | 833.00 |
| t14c-final-browser-runtime-fixed | 25 | 0 | 0/0 | 622.47 |
| t14c-final-fetch-delivery | 27 | 0 | 0/0 | 1.08 |
| t14c-final-phase0-4 | 325 | 0 | 0/0 | 1575.09 |
| t14c-final-t14b-native | 14 | 0 | 0/0 | 126.52 |
| t14c-wave7-decision-full | 28 | 0 | 0/0 | 821.17 |
| t14c-wave7-ownership | 53 | 0 | 0/0 | 67.71 |
| t14c-wave7-unit | 799 | 0 | 0/0 | 273.38 |
| t14c-wave7-winner-complete-matrix | 2 | 0 | 0/0 | 77.62 |

Inherited Phase-0–4: 325 passed across the same 30 baseline modules, zero skipped/failed/error cases. The later numeric-retention repair is covered by the fresh native Winner matrix, actual H5 SQL-reload regression, broader lane and complete browser run.

| Inherited module | Passed |
|---|---:|
| `tests.integration.test_phase4_configuration_certification_postgresql` | 7 |
| `tests.test_phase4_configuration_certification` | 36 |
| `tests.integration.test_ceri_immutable_decision_evidence` | 14 |
| `tests.integration.test_contextual_configuration_adoption_postgresql` | 4 |
| `tests.integration.test_contextual_consumer_eligibility_postgresql` | 1 |
| `tests.integration.test_core_calculation_evidence_postgresql` | 4 |
| `tests.integration.test_core_configuration_adoption_postgresql` | 5 |
| `tests.integration.test_decision_configuration_delivery_postgresql` | 20 |
| `tests.integration.test_effective_configuration_postgresql` | 2 |
| `tests.integration.test_ibmi_immutable_constituent_evidence` | 8 |
| `tests.integration.test_phase2_immutable_evidence_certification` | 3 |
| `tests.integration.test_readiness_evidence_postgresql` | 1 |
| `tests.integration.test_regime_sector_immutable_evidence_postgresql` | 5 |
| `tests.integration.test_repository_historical_read_enforcement` | 20 |
| `tests.integration.test_session_temporal_integrity_postgresql` | 2 |
| `tests.integration.test_setup_lifecycle_alert_immutable_evidence` | 6 |
| `tests.integration.test_technical_consumer_eligibility_postgresql` | 1 |
| `tests.integration.test_transition_preflight_plan_postgresql` | 24 |
| `tests.integration.test_winner_consumer_eligibility_postgresql` | 8 |
| `tests.integration.test_winner_temporal_integrity_postgresql` | 10 |
| `tests.test_contextual_effective_configuration` | 30 |
| `tests.test_core_effective_configuration` | 24 |
| `tests.test_decision_effective_configuration` | 31 |
| `tests.test_effective_configuration` | 41 |
| `tests.integration.test_domain_mutation_postgresql` | 4 |
| `tests.integration.test_t14b_writer_authority_postgresql` | 9 |
| `tests.integration.test_t14b_cache_authority_postgresql` | 1 |
| `tests.integration.test_t14b_source_boundaries_postgresql` | 1 |
| `tests.integration.test_t14b_financial_source_values_postgresql` | 2 |
| `tests.integration.test_t14b_regime_legacy_maintenance_postgresql` | 1 |

| Lane | Warnings | Deselected |
|---|---:|---:|
| t14c-final-broader | 23 | 347 |
| t14c-final-browser-runtime-fixed | 2 | 0 |
| t14c-final-fetch-delivery | 1 | 0 |
| t14c-final-phase0-4 | 226 | 0 |
| t14c-final-t14b-native | 43 | 0 |
| t14c-wave7-decision-full | 85 | 0 |
| t14c-wave7-ownership | 1 | 0 |
| t14c-wave7-unit | 1 | 0 |
| t14c-wave7-winner-complete-matrix | 7 | 0 |

The complete inherited Phase-0–4 lane preserves the original Phase-4 decision worker, producer/configuration/readiness/history regressions and T14B native scope. Broader selection preserves the established non-integration/non-e2e and non-live-IB policy. Earlier failed/interrupted attempts remain historical evidence in the machine summary and are not PASS evidence.

The seven skips are explicit external IBMI smoke gates requiring `SWINGLENS_RUN_EXTERNAL_IBMI=true`; no live provider was enabled. Deselection retains the established integration/e2e/live-IB selection policy. Final lanes have zero unexplained failures or errors. Earlier browser timing/artifact and exact-row races were diagnosed and repaired; no timing budget was relaxed.

Finite regression repairs: lifecycle date-gap callers deliver explicit aware time; UI fixtures deliver complete native source/configuration facts; registered alert-rebuild bootstrap ownership is recognized without weakening run/token checks; normal IB fetch delivers its retained completion timestamp to obligation reevaluation; comprehensive browser shares one Playwright runtime with an isolated context. Concurrent pytest artifact roots are separate. The positive browser profile uses an actual enabled disposable YAML through the native parser and full delivery; future price observation, maturation and cohort operation clocks are coherent explicit inputs. Maturation GUI counts use the exact owned processing run. Native outcome seals match PostgreSQL NUMERIC tie rounding; the unpatched H5 SQL-reload regression proves exit price 74.293433 remains financially sealed after retention. Temporary observation hooks were removed.

Static gates: Ruff, compilation, diff whitespace, source ownership/freshness and deterministic inventory. Disposable PostgreSQL uses actual Alembic upgrade and schema-drift checks. Migration required: NO; head `0080_effective_configuration`; production rewrite: NO; legacy backfill: NO. No schema/migration files changed.

## Finding reconciliation

| Finding | Final scoped status |
|---|---|
| INV-ENTRY-001 | 41/41 T14C WRITERS AND 84/84 INITIATORS RECONCILED; ORIGINAL_T14D_SCOPE_PRESERVED |
| PIPE-008 | CLOSED_FOR_CERTIFIED_T14B_T14C_WRITERS; T14D_CALLER_SCOPE_REMAINS |
| SETUP-005 | CLOSED_FOR_CERTIFIED_WRITER_ENFORCEMENT; T14D_CALLER_DELIVERY_REMAINS |
| SETUP-007 | CERTIFIED_RULE_HISTORY_CONFIGURATION_AND_WRITER_ENFORCEMENT_CLOSED; UNANCHORED_ORIGINAL_RULE_RECONSTRUCTION_REMAINS_PARTIAL |
| SETUP-009 | CLOSED_FOR_CERTIFIED_WRITER_PROJECTIONS; ORIGINAL_CONTEXT_CALLER_SCOPE_REMAINS |
| SETUP-010 | CONFIGURATION_AND_WRITER_AUTHORITY_PORTIONS_CLOSED; CURRENT_CONTEXT_MAINTENANCE_AND_OTHER_RECONSTRUCTION_ALGORITHM_SCOPE_REMAINS_PARTIAL |
| WIN-001 | CLOSED_FOR_CERTIFIED_CAPTURE_BUSINESS_TIME; STANDALONE_CALLERS_T14D |
| WIN-003 | CLOSED, RE-CERTIFIED |
| WIN-004 | CLOSED, RE-CERTIFIED |
| WIN-005 | CONSUMER_WRITER_AUTHORITY_ENFORCED; PRODUCER_ALGORITHM_DEFECTS_NOT_CLOSED |
| WIN-006 | PARTIAL: EXACT RETAINED REVISED PRICE IDS/PAYLOADS PROVEN; BIRTH ROWS MAY LACK REVISION IDS |
| WIN-007 | CLOSED, RE-CERTIFIED |
| WIN-008 | OPEN: TARGET-SET STABILITY REQUIRES TARGET-SCOPE FREEZING |
| WIN-009 | CLOSED, RE-CERTIFIED |
| WIN-010 | CLOSED_FOR_CERTIFIED_FINANCIAL_MUTATION_PERMISSION; SUPPORTING_DIAGNOSTICS_AND_RETIREMENT_KEEP_DISTINCT_NATIVE_SEMANTICS |
| XINT-006 | CERTIFIED_T14B_T14C_WRITER_ENFORCEMENT_COMPLETE; T14D_T14E_SCOPE_REMAINS |

Prior WIN-003/004/007/009 closure is re-certified. Producer algorithms, original-context reconstruction, unanchored original-rule rebuild, target-set freezing and background scope semantics are not mutation-authority defects and are not silently closed by this task.

## Exact T14D handoff and remaining scope

`T14C_T14D_exact_handoff.json` lists every caller by exact initiator ID, entrypoint, target families, native authority requirement and required delivery remediation. All 168 original T14D initiators and 25 original writer IDs are preserved; all 84 T14C initiators are reconciled (252 total records). All T14C writer prerequisites are complete. Conditional missing-delivery requirements do not assert that already-certified native pipeline callers lack authority. No unsafe writer work is transferred to T14D.

T14D retains standalone/repair/admin/CLI/legacy/bootstrap/scheduler caller unification and its original trade-episode writer scope. T14E retains Phase-5 integration certification. Background refresh/scope semantics, original-context reconstruction, target-scope freezing and privileged external SQL governance remain deferred. Legacy history remains readable without certified-authority upgrade or backfill. No forbidden CERI/Setup/Lifecycle/IBMI-to-Winner dependencies were introduced.

Uncertified family IDs: `[]`. Confirmed defect family IDs: `[]`. Potential bypass family IDs: `[]`.

Production/runtime mutations performed: NONE. Test runtime used only the owned disposable PostgreSQL container/databases and deterministic test providers. No live provider/trading action, production rewrite, push or merge.

## Changed files

- `app/models/tables.py`
- `app/services/background_worker.py`
- `app/services/canonical_evidence.py`
- `app/services/ceri/alert_authority.py`
- `app/services/ceri/alert_service.py`
- `app/services/ceri/change_authority.py`
- `app/services/ceri/change_detection_service.py`
- `app/services/ceri/normalization_service.py`
- `app/services/configuration_delivery.py`
- `app/services/core_calculation_evidence.py`
- `app/services/core_mutation_authority.py`
- `app/services/decision_effective_configuration.py`
- `app/services/decision_mutation_authority.py`
- `app/services/domain_mutation.py`
- `app/services/domain_write_fence.py`
- `app/services/ib_fetch_executor.py`
- `app/services/setup_lifecycle/alert_authority.py`
- `app/services/setup_lifecycle/alert_service.py`
- `app/services/setup_lifecycle/canonicalization.py`
- `app/services/setup_lifecycle/change_authority.py`
- `app/services/setup_lifecycle/change_detector.py`
- `app/services/setup_lifecycle/decision_evidence.py`
- `app/services/setup_lifecycle/episode_service.py`
- `app/services/setup_lifecycle/evaluation_service.py`
- `app/services/setup_lifecycle/maintenance_service.py`
- `app/services/setup_lifecycle/purge_service.py`
- `app/services/setup_lifecycle/replay_service.py`
- `app/services/setup_lifecycle/repository.py`
- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/transition_preflight_plan_service.py`
- `app/services/winner_probability/calibration_service.py`
- `app/services/winner_probability/capture_service.py`
- `app/services/winner_probability/cohort_authority.py`
- `app/services/winner_probability/cohort_definition.py`
- `app/services/winner_probability/cohort_generation_service.py`
- `app/services/winner_probability/cohort_materialization_service.py`
- `app/services/winner_probability/decision_time_estimate_service.py`
- `app/services/winner_probability/drift_service.py`
- `app/services/winner_probability/episode_service.py`
- `app/services/winner_probability/estimate_authority.py`
- `app/services/winner_probability/estimate_publication_service.py`
- `app/services/winner_probability/evidence_manifest_service.py`
- `app/services/winner_probability/feature_extractor.py`
- `app/services/winner_probability/job_handlers.py`
- `app/services/winner_probability/market_data_obligation_service.py`
- `app/services/winner_probability/model_authority.py`
- `app/services/winner_probability/model_registry.py`
- `app/services/winner_probability/model_training.py`
- `app/services/winner_probability/mutation_authority.py`
- `app/services/winner_probability/outcome_authority.py`
- `app/services/winner_probability/outcome_revision_service.py`
- `app/services/winner_probability/outcome_service.py`
- `app/services/winner_probability/pending_outcome_service.py`
- `app/services/winner_probability/prediction_authority.py`
- `app/services/winner_probability/probability_estimator.py`
- `app/services/winner_probability/repository.py`
- `app/services/winner_probability/similarity_service.py`
- `app/services/winner_probability/training_eligibility.py`
- `docs/architecture/SWINGLENS_MUTATION_ENTRYPOINT_CONTRACT.md`
- `docs/remediation/calculation-lineage/T14C_T14D_exact_handoff.json`
- `docs/remediation/calculation-lineage/T14C_checked_handoff.json`
- `docs/remediation/calculation-lineage/T14C_decision_winner_writer_adoption.md`
- `docs/remediation/calculation-lineage/T14C_decision_winner_writer_certification.json`
- `docs/remediation/calculation-lineage/T14C_semantic_family_review.json`
- `docs/remediation/calculation-lineage/T14C_semantic_review_source_pins.json`
- `docs/remediation/calculation-lineage/T14C_starting_state.json`
- `docs/remediation/calculation-lineage/T14C_validation_summary.json`
- `scripts/qa/t13e_deployed_probe.py`
- `scripts/qa/t14a_semantic_completeness.py`
- `scripts/qa/t14a_semantic_families.py`
- `tests/conftest.py`
- `tests/e2e/single_run_certification/certification_server.py`
- `tests/e2e/single_run_certification/financial_inputs.json`
- `tests/e2e/single_run_certification/fixtures.py`
- `tests/e2e/single_run_certification/test_single_run_certification.py`
- `tests/e2e/test_slse_populated_browser.py`
- `tests/historical_setup_alert_fixture.py`
- `tests/historical_setup_evidence_fixture.py`
- `tests/integration/test_contextual_consumer_eligibility_postgresql.py`
- `tests/integration/test_decision_configuration_delivery_postgresql.py`
- `tests/integration/test_phase2_immutable_evidence_certification.py`
- `tests/integration/test_phase4_configuration_certification_postgresql.py`
- `tests/integration/test_setup_lifecycle_alert_immutable_evidence.py`
- `tests/integration/test_t14c_decision_writer_postgresql.py`
- `tests/integration/test_t14c_winner_writer_postgresql.py`
- `tests/integration/test_technical_consumer_eligibility_postgresql.py`
- `tests/integration/test_transition_preflight_plan_postgresql.py`
- `tests/integration/test_webapp_fix_flows.py`
- `tests/integration/test_winner_consumer_eligibility_postgresql.py`
- `tests/integration/test_winner_temporal_integrity_postgresql.py`
- `tests/native_mutation_support.py`
- `tests/native_winner_support.py`
- `tests/test_canonical_evidence_serializer.py`
- `tests/test_decision_effective_configuration.py`
- `tests/test_producer_readiness.py`
- `tests/test_t14a_mutation_inventory.py`
- `tests/winner_probability/test_consumer_readiness.py`
- `tests/winner_probability/test_generation_publication_fence.py`
- `tests/winner_probability/test_readiness_frozen_operations.py`
- `tests/winner_probability/test_t12d_ready_scenarios.py`
- `tests/winner_probability/test_t12e_readiness_certification.py`
- `tests/winner_probability/test_winner_calculation_identity_adoption.py`
