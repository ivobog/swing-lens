# SwingLens full-week fingerprint ranking appendix

Window: `2026-08-26T11:25:41.966762Z` through `2026-09-04T09:14:30Z`. Worker figures are observed lower bounds because the worker writer dropped records.

## Highest cumulative SQL time

| Rank | Fingerprint | Shape | Calls | Total s | Mean ms | p95 ms | p99 ms | Max ms | Rows | Primary caller |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `50e38550046d` | SELECT background_workers | 910,754 | 925.021 | 1.016 | 1.997 | 4.024 | 6,574.664 | 910,751 | `app/services/worker_registry.py:92 heartbeat_worker` |
| 2 | `d0ca5d0f21db` | SELECT ceri_score_snapshots | 54 | 680.024 | 12,593.032 | 41,818.809 | 46,579.086 | 46,579.085 | 492,507 | `app/services/ceri/change_rebuild_service.py:263 _load` |
| 3 | `0aabd17ec382` | SELECT winner_forward_outcomes | 7,494 | 607.109 | 81.013 | 177.391 | 424.711 | 5,965.593 | 7,494 | `app/services/winner_probability/cohort_generation_service.py:201 EvidenceWatermarkService.current_material_watermark` |
| 4 | `327dc44bd5f2` | UPDATE background_workers | 768,292 | 595.322 | 0.775 | 1.667 | 2.967 | 1,008.544 | 768,292 | `app/services/worker_registry.py:111 heartbeat_worker` |
| 5 | `6695c6fd1b39` | SELECT background_jobs | 301,631 | 560.852 | 1.859 | 3.450 | 4.739 | 518.522 | 2 | `app/services/background_job_service.py:842 recover_abandoned_jobs_for_worker` |
| 6 | `ad20d22c226d` | SELECT processor_lifecycle.py:45 lifecycle_state | 301,777 | 542.774 | 1.799 | 3.075 | 5.388 | 255.719 | 603,554 | `app/services/ceri/sec/processor_lifecycle.py:45 lifecycle_state` |
| 7 | `c13437921eeb` | SELECT background_jobs | 301,629 | 418.679 | 1.388 | 2.256 | 4.065 | 769.588 | 301,622 | `app/services/winner_probability/scheduler.py:24 schedule_primary_h5_maturation` |
| 8 | `29b86bb023f7` | SELECT background_jobs | 301,596 | 397.943 | 1.319 | 2.344 | 3.392 | 758.506 | 0 | `app/services/background_job_service.py:816 recover_stale_jobs` |
| 9 | `d6898f0d2f93` | UPDATE background_jobs | 349,684 | 386.127 | 1.104 | 2.796 | 7.770 | 1,395.794 | 349,684 | `app/services/background_job_service.py:976 _apply_running_job_update` |
| 10 | `b8557bbba800` | SELECT background_jobs | 301,579 | 337.883 | 1.120 | 1.593 | 3.137 | 613.799 | 6,561 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 11 | `27aad416c525` | SELECT background_jobs | 380,257 | 330.497 | 0.869 | 1.719 | 5.165 | 445.072 | 380,257 | `app/services/ceri/batched_job_handlers.py:563 _heartbeat_and_cancel` |
| 12 | `8c8d01add0ec` | SELECT background_jobs | 301,614 | 326.471 | 1.082 | 1.583 | 3.105 | 441.202 | 31 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 13 | `378099918942` | SELECT background_supervisors | 248,124 | 289.493 | 1.167 | 2.082 | 4.595 | 804.340 | 248,106 | `app/services/supervisor_registry.py:65 heartbeat_supervisor` |
| 14 | `5fda8f92f221` | SELECT background_jobs | 295,017 | 283.074 | 0.960 | 1.347 | 2.836 | 797.304 | 0 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 15 | `ea44d10bd11e` | SELECT background_jobs | 295,018 | 282.612 | 0.958 | 1.330 | 2.825 | 631.433 | 7,604 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 16 | `b4554f5978c1` | SELECT background_jobs | 128,103 | 192.361 | 1.502 | 2.525 | 8.121 | 514.283 | 8,015 | `app/services/background_job_service.py:410 fence_stalled_jobs` |
| 17 | `92633bbbc299` | SELECT ceri_source_records | 220,933 | 185.741 | 0.841 | 1.619 | 4.961 | 813.791 | 187,335 | `app/services/ceri/source_record_service.py:422 _maybe_scalar` |
| 18 | `3377c3c259ae` | SELECT background_jobs | 380,882 | 167.785 | 0.441 | 0.876 | 2.364 | 404.907 | 380,882 | `app/services/background_job_service.py:791 is_cancel_requested` |
| 19 | `678746779802` | UPDATE background_supervisors | 128,152 | 131.912 | 1.029 | 1.808 | 3.700 | 2,322.692 | 128,152 | `app/services/supervisor_registry.py:70 heartbeat_supervisor` |
| 20 | `7fb3dc372193` | SELECT ceri_guidance_events | 59 | 118.185 | 2,003.128 | 3,719.314 | 11,904.597 | 11,904.597 | 3,343,417 | `app/services/ceri/query_service.py:3095 _load` |
| 21 | `9fa2476031bc` | SELECT orchestration.py:147 CeriIngestionService.ingest | 207,173 | 102.715 | 0.496 | 1.015 | 3.034 | 416.015 | 207,173 | `app/services/ceri/orchestration.py:147 CeriIngestionService.ingest` |
| 22 | `0bb68ba6178a` | SELECT background_jobs | 6,065 | 95.058 | 15.673 | 31.620 | 54.323 | 1,325.632 | 312,881 | `app/services/ceri/batched_job_handlers.py:451 _require_terminal_stage` |
| 23 | `e200131a88ad` | SELECT price_bars | 6,218 | 91.612 | 14.733 | 21.857 | 125.431 | 1,139.650 | 9,705,104 | `app/services/ceri/price_response_service.py:446 _scalars` |
| 24 | `aa2ada521993` | SELECT ceri_score_snapshots | 3,110 | 91.201 | 29.325 | 98.074 | 288.524 | 3,103.527 | 44,265 | `app/services/ceri/capture_service.py:870 _scalars` |
| 25 | `bde9aef2c98e` | SELECT winner_forward_outcomes | 14,954 | 82.294 | 5.503 | 8.081 | 17.134 | 478.287 | 14,954 | `app/services/winner_probability/outcome_orchestration_service.py:152 H5NextOpenOrchestrationService._backlog` |
| 26 | `9f2bb537843f` | SELECT winner_prediction_snapshots | 16 | 77.791 | 4,861.921 | 18,120.893 | 18,120.893 | 18,120.893 | 479,446 | `app/services/winner_probability/evidence_service.py:408 EvidenceService.diagnostic_funnel` |
| 27 | `7213f5be6937` | UPDATE background_jobs | 38,175 | 75.201 | 1.970 | 4.978 | 13.391 | 584.472 | 38,175 | `app/services/ceri/source_record_service.py:68 CeriSourceRecordService.create_ingestion_run` |
| 28 | `05910ec4da7f` | INSERT ceri_revision_features | 3,316 | 73.710 | 22.229 | 32.707 | 101.914 | 861.079 | 79,584 | `app/services/ceri/feature_rebuild_service.py:797 _execute_upsert` |
| 29 | `e2ef0f9194c8` | SELECT price_bars | 65 | 67.287 | 1,035.189 | 1,812.232 | 8,780.937 | 8,780.937 | 5,092,886 | `app/services/ceri/feature_rebuild_service.py:818 _scalars` |
| 30 | `9ad420eb1589` | SELECT background_job_service.py:170 _database_has_job_progress_columns | 16,153 | 62.700 | 3.882 | 6.314 | 14.203 | 134.299 | 565,355 | `app/services/background_job_service.py:170 _database_has_job_progress_columns` |

## Highest call count

| Rank | Fingerprint | Shape | Calls | Total s | Mean ms | p95 ms | p99 ms | Max ms | Rows | Primary caller |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `50e38550046d` | SELECT background_workers | 910,754 | 925.021 | 1.016 | 1.997 | 4.024 | 6,574.664 | 910,751 | `app/services/worker_registry.py:92 heartbeat_worker` |
| 2 | `327dc44bd5f2` | UPDATE background_workers | 768,292 | 595.322 | 0.775 | 1.667 | 2.967 | 1,008.544 | 768,292 | `app/services/worker_registry.py:111 heartbeat_worker` |
| 3 | `3377c3c259ae` | SELECT background_jobs | 380,882 | 167.785 | 0.441 | 0.876 | 2.364 | 404.907 | 380,882 | `app/services/background_job_service.py:791 is_cancel_requested` |
| 4 | `27aad416c525` | SELECT background_jobs | 380,257 | 330.497 | 0.869 | 1.719 | 5.165 | 445.072 | 380,257 | `app/services/ceri/batched_job_handlers.py:563 _heartbeat_and_cancel` |
| 5 | `d6898f0d2f93` | UPDATE background_jobs | 349,684 | 386.127 | 1.104 | 2.796 | 7.770 | 1,395.794 | 349,684 | `app/services/background_job_service.py:976 _apply_running_job_update` |
| 6 | `ad20d22c226d` | SELECT processor_lifecycle.py:45 lifecycle_state | 301,777 | 542.774 | 1.799 | 3.075 | 5.388 | 255.719 | 603,554 | `app/services/ceri/sec/processor_lifecycle.py:45 lifecycle_state` |
| 7 | `6695c6fd1b39` | SELECT background_jobs | 301,631 | 560.852 | 1.859 | 3.450 | 4.739 | 518.522 | 2 | `app/services/background_job_service.py:842 recover_abandoned_jobs_for_worker` |
| 8 | `c13437921eeb` | SELECT background_jobs | 301,629 | 418.679 | 1.388 | 2.256 | 4.065 | 769.588 | 301,622 | `app/services/winner_probability/scheduler.py:24 schedule_primary_h5_maturation` |
| 9 | `8c8d01add0ec` | SELECT background_jobs | 301,614 | 326.471 | 1.082 | 1.583 | 3.105 | 441.202 | 31 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 10 | `29b86bb023f7` | SELECT background_jobs | 301,596 | 397.943 | 1.319 | 2.344 | 3.392 | 758.506 | 0 | `app/services/background_job_service.py:816 recover_stale_jobs` |
| 11 | `b8557bbba800` | SELECT background_jobs | 301,579 | 337.883 | 1.120 | 1.593 | 3.137 | 613.799 | 6,561 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 12 | `ea44d10bd11e` | SELECT background_jobs | 295,018 | 282.612 | 0.958 | 1.330 | 2.825 | 631.433 | 7,604 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 13 | `5fda8f92f221` | SELECT background_jobs | 295,017 | 283.074 | 0.960 | 1.347 | 2.836 | 797.304 | 0 | `app/services/background_job_service.py:604 _claim_ready_job_id` |
| 14 | `378099918942` | SELECT background_supervisors | 248,124 | 289.493 | 1.167 | 2.082 | 4.595 | 804.340 | 248,106 | `app/services/supervisor_registry.py:65 heartbeat_supervisor` |
| 15 | `92633bbbc299` | SELECT ceri_source_records | 220,933 | 185.741 | 0.841 | 1.619 | 4.961 | 813.791 | 187,335 | `app/services/ceri/source_record_service.py:422 _maybe_scalar` |
| 16 | `9fa2476031bc` | SELECT orchestration.py:147 CeriIngestionService.ingest | 207,173 | 102.715 | 0.496 | 1.015 | 3.034 | 416.015 | 207,173 | `app/services/ceri/orchestration.py:147 CeriIngestionService.ingest` |
| 17 | `678746779802` | UPDATE background_supervisors | 128,152 | 131.912 | 1.029 | 1.808 | 3.700 | 2,322.692 | 128,152 | `app/services/supervisor_registry.py:70 heartbeat_supervisor` |
| 18 | `b4554f5978c1` | SELECT background_jobs | 128,103 | 192.361 | 1.502 | 2.525 | 8.121 | 514.283 | 8,015 | `app/services/background_job_service.py:410 fence_stalled_jobs` |
| 19 | `facf4e7603c5` | SELECT price_bars | 80,597 | 40.164 | 0.498 | 0.853 | 2.549 | 483.344 | 80,597 | `app/services/winner_probability/outcome_service.py:801 _bars_in_range` |
| 20 | `881a75680dfc` | SELECT change_rebuild_service.py:269 _get | 74,334 | 33.179 | 0.446 | 1.161 | 2.460 | 55.180 | 74,334 | `app/services/ceri/change_rebuild_service.py:269 _get` |
| 21 | `acce219c9b5e` | SELECT repository.py:681 SetupLifecycleRepository.get_signal_change_event | 61,610 | 40.869 | 0.663 | 1.060 | 4.081 | 156.173 | 10,212 | `app/services/setup_lifecycle/repository.py:681 SetupLifecycleRepository.get_signal_change_event` |
| 22 | `7213f5be6937` | UPDATE background_jobs | 38,175 | 75.201 | 1.970 | 4.978 | 13.391 | 584.472 | 38,175 | `app/services/ceri/source_record_service.py:68 CeriSourceRecordService.create_ingestion_run` |
| 23 | `e3231fca7ef9` | INSERT ceri_source_records | 33,599 | 59.415 | 1.768 | 3.304 | 14.019 | 286.325 | 33,599 | `app/services/ceri/source_record_service.py:212 CeriSourceRecordService.store_source_record` |
| 24 | `533a11c32aeb` | SELECT ceri_source_records | 33,598 | 33.905 | 1.009 | 2.369 | 7.935 | 247.723 | 10,845 | `app/services/ceri/source_record_service.py:422 _maybe_scalar` |
| 25 | `50635f7b42b6` | UPDATE background_jobs | 29,194 | 34.190 | 1.171 | 2.528 | 6.069 | 299.759 | 29,194 | `app/services/background_job_service.py:365 record_job_progress` |
| 26 | `2405987992fe` | SELECT winner_forward_outcomes | 27,669 | 13.214 | 0.478 | 0.828 | 2.866 | 47.948 | 5,619 | `app/services/winner_probability/repository.py:195 WinnerProbabilityRepository.get_forward_outcome` |
| 27 | `98113dde1bbf` | INSERT repository.py:883 SetupLifecycleRepository.add | 25,921 | 21.808 | 0.841 | 1.493 | 4.170 | 48.588 | 25,921 | `app/services/setup_lifecycle/repository.py:883 SetupLifecycleRepository.add` |
| 28 | `98fe477debd1` | SELECT source_record_service.py:422 _maybe_scalar | 25,607 | 17.377 | 0.679 | 1.387 | 3.890 | 52.653 | 10,815 | `app/services/ceri/source_record_service.py:422 _maybe_scalar` |
| 29 | `bfebe850a34a` | INSERT winner_forward_outcomes | 22,350 | 15.473 | 0.692 | 1.259 | 4.242 | 247.929 | 22,350 | `app/services/winner_probability/repository.py:238 WinnerProbabilityRepository.add` |
| 30 | `b025e605ca5a` | SELECT winner_prediction_snapshots | 22,131 | 13.992 | 0.632 | 1.123 | 3.517 | 119.626 | 22,131 | `app/services/winner_probability/outcome_service.py:251 OutcomeMaturationService._calculate_forward` |

## Highest mean latency (minimum 5 samples)

| Rank | Fingerprint | Shape | Calls | Total s | Mean ms | p95 ms | p99 ms | Max ms | Rows | Primary caller |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `d0ca5d0f21db` | SELECT ceri_score_snapshots | 54 | 680.024 | 12,593.032 | 41,818.809 | 46,579.086 | 46,579.085 | 492,507 | `app/services/ceri/change_rebuild_service.py:263 _load` |
| 2 | `9f2bb537843f` | SELECT winner_prediction_snapshots | 16 | 77.791 | 4,861.921 | 18,120.893 | 18,120.893 | 18,120.893 | 479,446 | `app/services/winner_probability/evidence_service.py:408 EvidenceService.diagnostic_funnel` |
| 3 | `4be370963ef3` | DELETE fundamental_score_service.py:24 recalculate_run_fundamentals | 17 | 60.462 | 3,556.580 | 16,025.429 | 16,025.429 | 16,025.429 | 3,809 | `app/services/fundamental_score_service.py:24 recalculate_run_fundamentals` |
| 4 | `a07b79b5533a` | SELECT ceri_score_snapshots | 8 | 19.399 | 2,424.830 | 5,479.427 | 5,479.427 | 5,479.427 | 28,640 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 5 | `7fb3dc372193` | SELECT ceri_guidance_events | 59 | 118.185 | 2,003.128 | 3,719.314 | 11,904.597 | 11,904.597 | 3,343,417 | `app/services/ceri/query_service.py:3095 _load` |
| 6 | `99938cc3f619` | SELECT winner_prediction_snapshots | 34 | 53.633 | 1,577.451 | 4,772.782 | 5,676.013 | 5,676.013 | 301,206 | `app/services/winner_probability/evidence_service.py:612 _load_compatibility_replays` |
| 7 | `7e5046155b38` | SELECT price_bars | 18 | 24.961 | 1,386.724 | 2,500.149 | 2,500.149 | 2,500.149 | 14,700 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 8 | `999a31e8857c` | SELECT price_bars | 9 | 12.400 | 1,377.814 | 9,338.077 | 9,338.077 | 9,338.077 | 2,534 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 9 | `7dc94b20c443` | SELECT price_bars | 10 | 13.607 | 1,360.659 | 3,195.524 | 3,195.524 | 3,195.524 | 7,500 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 10 | `7a0bfc678ae1` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 6 | 7.967 | 1,327.801 | 1,905.192 | 1,905.192 | 1,905.192 | 17,706 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |
| 11 | `f8b74e6aa486` | DELETE combined_decision.py:117 refresh_combined_results | 17 | 22.129 | 1,301.697 | 21,981.689 | 21,981.689 | 21,981.690 | 136 | `app/services/combined_decision.py:117 refresh_combined_results` |
| 12 | `9e52dd2e329c` | SELECT price_bars | 8 | 10.153 | 1,269.079 | 4,255.838 | 4,255.838 | 4,255.838 | 4,982 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 13 | `92380d68c9ae` | SELECT ceri_revision_features | 43 | 49.895 | 1,160.353 | 2,888.375 | 3,796.876 | 3,796.876 | 6,308,586 | `app/services/ceri/query_service.py:3095 _load` |
| 14 | `4db24219b7c5` | SELECT price_bars | 7 | 7.626 | 1,089.454 | 2,401.122 | 2,401.122 | 2,401.122 | 5,572 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 15 | `e2ef0f9194c8` | SELECT price_bars | 65 | 67.287 | 1,035.189 | 1,812.232 | 8,780.937 | 8,780.937 | 5,092,886 | `app/services/ceri/feature_rebuild_service.py:818 _scalars` |
| 16 | `29a0260bcdbb` | SELECT price_bars | 22 | 22.149 | 1,006.752 | 2,113.450 | 2,565.774 | 2,565.774 | 15,028 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 17 | `8c50bd41a614` | SELECT winner_prediction_snapshots | 18 | 17.047 | 947.065 | 6,564.858 | 6,564.858 | 6,564.858 | 87,270 | `app/services/winner_probability/evidence_service.py:166 EvidenceService.load_generation_evidence` |
| 18 | `9ca2e06d5bee` | SELECT price_bars | 8 | 7.302 | 912.739 | 3,056.108 | 3,056.108 | 3,056.108 | 4,860 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 19 | `fae991a8bf5a` | SELECT price_bars | 7 | 5.655 | 807.894 | 1,779.913 | 1,779.913 | 1,779.913 | 4,848 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 20 | `b04abbf7be78` | SELECT price_bars | 10 | 6.702 | 670.192 | 1,340.546 | 1,340.546 | 1,340.546 | 5,472 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 21 | `bae6dc694e58` | INSERT ranking_profile_service.py:141 _persist_rankings | 25 | 14.651 | 586.058 | 1,042.310 | 1,123.413 | 1,123.413 | 18,365 | `app/services/ranking_profile_service.py:141 _persist_rankings` |
| 22 | `fc71043ce7db` | SELECT query_service.py:977 CeriQueryService._database_operations_status | 9 | 5.265 | 584.996 | 833.362 | 833.362 | 833.362 | 9 | `app/services/ceri/query_service.py:977 CeriQueryService._database_operations_status` |
| 23 | `4450b2a68f1d` | INSERT technical_score_service.py:272 finalize_technical_scores | 17 | 9.656 | 567.996 | 1,364.918 | 1,364.918 | 1,364.918 | 3,809 | `app/services/technical_score_service.py:272 finalize_technical_scores` |
| 24 | `60094dedced3` | INSERT fundamental_score_service.py:26 recalculate_run_fundamentals | 33 | 18.142 | 549.749 | 2,173.969 | 9,270.773 | 9,270.773 | 7,482 | `app/services/fundamental_score_service.py:26 recalculate_run_fundamentals` |
| 25 | `4fdff7866991` | SELECT price_bars | 20 | 10.395 | 519.751 | 640.446 | 4,598.445 | 4,598.445 | 5,512 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 26 | `748e99db1903` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 6 | 2.895 | 482.424 | 779.272 | 779.272 | 779.272 | 7,266 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |
| 27 | `4a1d20b0eb64` | SELECT price_bars | 11 | 5.066 | 460.565 | 1,128.328 | 1,128.328 | 1,128.328 | 2,794 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 28 | `de11be2e836a` | SELECT price_bars | 11 | 4.621 | 420.086 | 838.403 | 838.403 | 838.403 | 3,072 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 29 | `95ebd5bf8521` | SELECT ceri_source_records | 36 | 14.174 | 393.716 | 1,146.596 | 1,228.607 | 1,228.607 | 36 | `app/services/ceri/query_service.py:2865 <genexpr>` |
| 30 | `d3939a39143d` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 6 | 2.233 | 372.227 | 648.524 | 648.524 | 648.524 | 6,948 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |

## Highest p95 latency

| Rank | Fingerprint | Shape | Calls | Total s | Mean ms | p95 ms | p99 ms | Max ms | Rows | Primary caller |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `d0ca5d0f21db` | SELECT ceri_score_snapshots | 54 | 680.024 | 12,593.032 | 41,818.809 | 46,579.086 | 46,579.085 | 492,507 | `app/services/ceri/change_rebuild_service.py:263 _load` |
| 2 | `d7ba56d7ef55` | SELECT price_bars | 2 | 26.910 | 13,455.220 | 23,821.734 | 23,821.734 | 23,821.734 | 1,496 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 3 | `f8b74e6aa486` | DELETE combined_decision.py:117 refresh_combined_results | 17 | 22.129 | 1,301.697 | 21,981.689 | 21,981.689 | 21,981.690 | 136 | `app/services/combined_decision.py:117 refresh_combined_results` |
| 4 | `9f2bb537843f` | SELECT winner_prediction_snapshots | 16 | 77.791 | 4,861.921 | 18,120.893 | 18,120.893 | 18,120.893 | 479,446 | `app/services/winner_probability/evidence_service.py:408 EvidenceService.diagnostic_funnel` |
| 5 | `4be370963ef3` | DELETE fundamental_score_service.py:24 recalculate_run_fundamentals | 17 | 60.462 | 3,556.580 | 16,025.429 | 16,025.429 | 16,025.429 | 3,809 | `app/services/fundamental_score_service.py:24 recalculate_run_fundamentals` |
| 6 | `c77ac05dbb86` | SELECT price_bars | 4 | 22.874 | 5,718.435 | 14,225.464 | 14,225.464 | 14,225.464 | 2,720 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 7 | `999a31e8857c` | SELECT price_bars | 9 | 12.400 | 1,377.814 | 9,338.077 | 9,338.077 | 9,338.077 | 2,534 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 8 | `b772e72a0993` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 3 | 9.985 | 3,328.434 | 8,794.953 | 8,794.953 | 8,794.953 | 3,783 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |
| 9 | `72f720564eb3` | SELECT price_bars | 2 | 11.021 | 5,510.281 | 8,561.900 | 8,561.900 | 8,561.900 | 1,628 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 10 | `8c50bd41a614` | SELECT winner_prediction_snapshots | 18 | 17.047 | 947.065 | 6,564.858 | 6,564.858 | 6,564.858 | 87,270 | `app/services/winner_probability/evidence_service.py:166 EvidenceService.load_generation_evidence` |
| 11 | `45b542bbfb7e` | SELECT ceri_score_snapshots | 1 | 5.805 | 5,804.796 | 5,804.796 | 5,804.796 | 5,804.796 | 3,691 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 12 | `a07b79b5533a` | SELECT ceri_score_snapshots | 8 | 19.399 | 2,424.830 | 5,479.427 | 5,479.427 | 5,479.427 | 28,640 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 13 | `99938cc3f619` | SELECT winner_prediction_snapshots | 34 | 53.633 | 1,577.451 | 4,772.782 | 5,676.013 | 5,676.013 | 301,206 | `app/services/winner_probability/evidence_service.py:612 _load_compatibility_replays` |
| 14 | `9e52dd2e329c` | SELECT price_bars | 8 | 10.153 | 1,269.079 | 4,255.838 | 4,255.838 | 4,255.838 | 4,982 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 15 | `67b9c6678fed` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 3 | 5.964 | 1,987.896 | 4,093.666 | 4,093.666 | 4,093.666 | 9,492 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |
| 16 | `e8137b990cfd` | SELECT ceri_score_snapshots | 2 | 7.480 | 3,740.064 | 3,974.711 | 3,974.711 | 3,974.711 | 7,726 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 17 | `e058bde532c3` | SELECT ceri_score_snapshots | 1 | 3.849 | 3,849.020 | 3,849.020 | 3,849.020 | 3,849.020 | 3,796 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 18 | `dd4fa0eec8ec` | UPDATE background_workers | 3 | 6.374 | 2,124.741 | 3,824.489 | 3,824.489 | 3,824.489 | 3 | `app/services/worker_registry.py:76 register_worker` |
| 19 | `7fb3dc372193` | SELECT ceri_guidance_events | 59 | 118.185 | 2,003.128 | 3,719.314 | 11,904.597 | 11,904.597 | 3,343,417 | `app/services/ceri/query_service.py:3095 _load` |
| 20 | `287d33bcf39e` | SELECT price_bars | 2 | 6.328 | 3,164.036 | 3,660.902 | 3,660.902 | 3,660.902 | 1,240 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 21 | `900b4dd8de34` | SELECT query_service.py:1946 _combined_change_page_rows | 11 | 3.764 | 342.201 | 3,599.836 | 3,599.836 | 3,599.836 | 502 | `app/services/setup_lifecycle/query_service.py:1946 _combined_change_page_rows` |
| 22 | `c9986441af52` | SELECT price_bars | 2 | 6.788 | 3,393.885 | 3,524.103 | 3,524.103 | 3,524.103 | 1,384 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 23 | `7de52cd9afbc` | SELECT ceri_score_snapshots | 1 | 3.417 | 3,416.796 | 3,416.796 | 3,416.796 | 3,416.796 | 3,698 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 24 | `09f74e9c7580` | SELECT ceri_score_snapshots | 1 | 3.412 | 3,412.474 | 3,412.474 | 3,412.474 | 3,412.474 | 3,862 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 25 | `f71b8d00ac0c` | SELECT ceri_score_snapshots | 1 | 3.343 | 3,343.262 | 3,343.262 | 3,343.262 | 3,343.262 | 3,694 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 26 | `171cf3b94d1e` | SELECT ceri_score_snapshots | 1 | 3.335 | 3,334.963 | 3,334.963 | 3,334.963 | 3,334.963 | 3,889 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 27 | `06c277e25ba1` | SELECT ceri_source_records | 4 | 3.741 | 935.221 | 3,334.821 | 3,334.821 | 3,334.821 | 2,320 | `app/services/ceri/query_service.py:1882 _snapshot_freshness` |
| 28 | `7dc94b20c443` | SELECT price_bars | 10 | 13.607 | 1,360.659 | 3,195.524 | 3,195.524 | 3,195.524 | 7,500 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 29 | `9ca2e06d5bee` | SELECT price_bars | 8 | 7.302 | 912.739 | 3,056.108 | 3,056.108 | 3,056.108 | 4,860 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 30 | `df97b6fdfbc0` | SELECT ceri_score_snapshots | 1 | 2.966 | 2,965.596 | 2,965.596 | 2,965.596 | 2,965.596 | 3,575 | `app/services/ceri/query_service.py:1265 CeriQueryService._snapshots_for_ids` |

## Highest p99 latency

| Rank | Fingerprint | Shape | Calls | Total s | Mean ms | p95 ms | p99 ms | Max ms | Rows | Primary caller |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `d0ca5d0f21db` | SELECT ceri_score_snapshots | 54 | 680.024 | 12,593.032 | 41,818.809 | 46,579.086 | 46,579.085 | 492,507 | `app/services/ceri/change_rebuild_service.py:263 _load` |
| 2 | `d7ba56d7ef55` | SELECT price_bars | 2 | 26.910 | 13,455.220 | 23,821.734 | 23,821.734 | 23,821.734 | 1,496 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 3 | `f8b74e6aa486` | DELETE combined_decision.py:117 refresh_combined_results | 17 | 22.129 | 1,301.697 | 21,981.689 | 21,981.689 | 21,981.690 | 136 | `app/services/combined_decision.py:117 refresh_combined_results` |
| 4 | `9f2bb537843f` | SELECT winner_prediction_snapshots | 16 | 77.791 | 4,861.921 | 18,120.893 | 18,120.893 | 18,120.893 | 479,446 | `app/services/winner_probability/evidence_service.py:408 EvidenceService.diagnostic_funnel` |
| 5 | `4be370963ef3` | DELETE fundamental_score_service.py:24 recalculate_run_fundamentals | 17 | 60.462 | 3,556.580 | 16,025.429 | 16,025.429 | 16,025.429 | 3,809 | `app/services/fundamental_score_service.py:24 recalculate_run_fundamentals` |
| 6 | `c77ac05dbb86` | SELECT price_bars | 4 | 22.874 | 5,718.435 | 14,225.464 | 14,225.464 | 14,225.464 | 2,720 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 7 | `7fb3dc372193` | SELECT ceri_guidance_events | 59 | 118.185 | 2,003.128 | 3,719.314 | 11,904.597 | 11,904.597 | 3,343,417 | `app/services/ceri/query_service.py:3095 _load` |
| 8 | `999a31e8857c` | SELECT price_bars | 9 | 12.400 | 1,377.814 | 9,338.077 | 9,338.077 | 9,338.077 | 2,534 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 9 | `60094dedced3` | INSERT fundamental_score_service.py:26 recalculate_run_fundamentals | 33 | 18.142 | 549.749 | 2,173.969 | 9,270.773 | 9,270.773 | 7,482 | `app/services/fundamental_score_service.py:26 recalculate_run_fundamentals` |
| 10 | `b772e72a0993` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 3 | 9.985 | 3,328.434 | 8,794.953 | 8,794.953 | 8,794.953 | 3,783 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |
| 11 | `e2ef0f9194c8` | SELECT price_bars | 65 | 67.287 | 1,035.189 | 1,812.232 | 8,780.937 | 8,780.937 | 5,092,886 | `app/services/ceri/feature_rebuild_service.py:818 _scalars` |
| 12 | `72f720564eb3` | SELECT price_bars | 2 | 11.021 | 5,510.281 | 8,561.900 | 8,561.900 | 8,561.900 | 1,628 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 13 | `8c50bd41a614` | SELECT winner_prediction_snapshots | 18 | 17.047 | 947.065 | 6,564.858 | 6,564.858 | 6,564.858 | 87,270 | `app/services/winner_probability/evidence_service.py:166 EvidenceService.load_generation_evidence` |
| 14 | `78154f1174a8` | SELECT query_service.py:3095 _load | 59 | 11.625 | 197.030 | 432.357 | 6,101.769 | 6,101.769 | 917,946 | `app/services/ceri/query_service.py:3095 _load` |
| 15 | `45b542bbfb7e` | SELECT ceri_score_snapshots | 1 | 5.805 | 5,804.796 | 5,804.796 | 5,804.796 | 5,804.796 | 3,691 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 16 | `99938cc3f619` | SELECT winner_prediction_snapshots | 34 | 53.633 | 1,577.451 | 4,772.782 | 5,676.013 | 5,676.013 | 301,206 | `app/services/winner_probability/evidence_service.py:612 _load_compatibility_replays` |
| 17 | `a07b79b5533a` | SELECT ceri_score_snapshots | 8 | 19.399 | 2,424.830 | 5,479.427 | 5,479.427 | 5,479.427 | 28,640 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 18 | `4fdff7866991` | SELECT price_bars | 20 | 10.395 | 519.751 | 640.446 | 4,598.445 | 4,598.445 | 5,512 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 19 | `9e52dd2e329c` | SELECT price_bars | 8 | 10.153 | 1,269.079 | 4,255.838 | 4,255.838 | 4,255.838 | 4,982 | `app/services/ohlcv_coverage_service.py:226 _bar_stats` |
| 20 | `67b9c6678fed` | SELECT repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before | 3 | 5.964 | 1,987.896 | 4,093.666 | 4,093.666 | 4,093.666 | 9,492 | `app/services/setup_lifecycle/repository.py:403 SetupLifecycleRepository.canonical_snapshot_histories_before` |
| 21 | `e8137b990cfd` | SELECT ceri_score_snapshots | 2 | 7.480 | 3,740.064 | 3,974.711 | 3,974.711 | 3,974.711 | 7,726 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 22 | `e058bde532c3` | SELECT ceri_score_snapshots | 1 | 3.849 | 3,849.020 | 3,849.020 | 3,849.020 | 3,849.020 | 3,796 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 23 | `dd4fa0eec8ec` | UPDATE background_workers | 3 | 6.374 | 2,124.741 | 3,824.489 | 3,824.489 | 3,824.489 | 3 | `app/services/worker_registry.py:76 register_worker` |
| 24 | `14d20db30cd6` | SELECT feature_rebuild_service.py:818 _scalars | 65 | 23.196 | 356.863 | 646.191 | 3,820.342 | 3,820.342 | 545,278 | `app/services/ceri/feature_rebuild_service.py:818 _scalars` |
| 25 | `92380d68c9ae` | SELECT ceri_revision_features | 43 | 49.895 | 1,160.353 | 2,888.375 | 3,796.876 | 3,796.876 | 6,308,586 | `app/services/ceri/query_service.py:3095 _load` |
| 26 | `287d33bcf39e` | SELECT price_bars | 2 | 6.328 | 3,164.036 | 3,660.902 | 3,660.902 | 3,660.902 | 1,240 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 27 | `900b4dd8de34` | SELECT query_service.py:1946 _combined_change_page_rows | 11 | 3.764 | 342.201 | 3,599.836 | 3,599.836 | 3,599.836 | 502 | `app/services/setup_lifecycle/query_service.py:1946 _combined_change_page_rows` |
| 28 | `c9986441af52` | SELECT price_bars | 2 | 6.788 | 3,393.885 | 3,524.103 | 3,524.103 | 3,524.103 | 1,384 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` |
| 29 | `7de52cd9afbc` | SELECT ceri_score_snapshots | 1 | 3.417 | 3,416.796 | 3,416.796 | 3,416.796 | 3,416.796 | 3,698 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |
| 30 | `09f74e9c7580` | SELECT ceri_score_snapshots | 1 | 3.412 | 3,412.474 | 3,412.474 | 3,412.474 | 3,412.474 | 3,862 | `app/services/ceri/query_service.py:1303 CeriQueryService._snapshots_for_ids` |

## Slowest individual executions

| Rank | Duration ms | Fingerprint | Route/job | Rows | Caller | Shape |
| ---: | ---: | --- | --- | ---: | --- | --- |
| 1 | 46,579.085 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,065 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 2 | 45,655.255 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 8,915 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 3 | 41,818.810 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 9,314 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 4 | 37,275.582 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,718 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 5 | 32,429.313 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,310 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 6 | 28,252.528 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 7,606 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 7 | 23,821.734 | `d7ba56d7ef55` | `FULL_PIPELINE` | 748 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` | SELECT price_bars.id, price_bars.ticker, price_bars.bar_date, price_bars.timefra |
| 8 | 22,608.861 | `d0ca5d0f21db` | `GET /ceri/changes` | 9,955 | `app/services/ceri/query_service.py:3095 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 9 | 21,981.690 | `f8b74e6aa486` | `FULL_PIPELINE` | 136 | `app/services/combined_decision.py:117 refresh_combined_results` | DELETE |
| 10 | 21,686.968 | `6fcebb31579a` | `FULL_PIPELINE` | 752 | `app/services/price_series_version_service.py:34 maintain_price_series_versions` | INSERT INTO price_bars (ticker, bar_date, timeframe, open, high, low, close, vol |
| 11 | 20,931.495 | `d0ca5d0f21db` | `GET /ceri/changes` | 8,008 | `app/services/ceri/query_service.py:3057 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 12 | 18,120.893 | `9f2bb537843f` | `FULL_PIPELINE` | 32,849 | `app/services/winner_probability/evidence_service.py:408 EvidenceService.diagnostic_funnel` | SELECT winner_prediction_snapshots.id, winner_prediction_snapshots.run_id, winne |
| 13 | 18,024.435 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 9,314 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 14 | 16,083.699 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 8,630 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 15 | 16,025.429 | `4be370963ef3` | `FULL_PIPELINE` | 346 | `app/services/fundamental_score_service.py:24 recalculate_run_fundamentals` | DELETE |
| 16 | 15,514.749 | `d0ca5d0f21db` | `GET /ceri/changes` | 10,310 | `app/services/ceri/query_service.py:3095 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 17 | 14,749.508 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 8,915 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 18 | 14,225.464 | `c77ac05dbb86` | `FULL_PIPELINE` | 680 | `app/services/setup_lifecycle/source_loader.py:211 SetupLifecycleSourceLoader._load_price_bars` | SELECT price_bars.id, price_bars.ticker, price_bars.bar_date, price_bars.timefra |
| 19 | 13,292.813 | `d0ca5d0f21db` | `GET /ceri/changes` | 8,630 | `app/services/ceri/query_service.py:3095 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 20 | 13,225.939 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,065 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 21 | 12,494.423 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,310 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 22 | 12,460.800 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 9,314 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 23 | 11,937.544 | `d0ca5d0f21db` | `GET /ceri` | 10,310 | `app/services/ceri/query_service.py:3095 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 24 | 11,904.597 | `7fb3dc372193` | `UNKNOWN` | 56,355 | `app/services/ceri/query_service.py:3090 _load` | SELECT ceri_guidance_events.id, ceri_guidance_events.source_record_id, ceri_guid |
| 25 | 11,490.433 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,718 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 26 | 11,301.394 | `d0ca5d0f21db` | `GET /ceri/changes` | 8,008 | `app/services/ceri/query_service.py:3057 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 27 | 11,145.410 | `d0ca5d0f21db` | `CERI_ALERT_REBUILD` | 7,606 | `app/services/ceri/job_handlers.py:680 _load_rows` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 28 | 10,941.197 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 10,444 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 29 | 10,678.524 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 8,978 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
| 30 | 10,098.812 | `d0ca5d0f21db` | `CERI_CHANGE_DETECTION` | 9,314 | `app/services/ceri/change_rebuild_service.py:263 _load` | SELECT ceri_score_snapshots.id, ceri_score_snapshots.controlled_replay_id, ceri_ |
