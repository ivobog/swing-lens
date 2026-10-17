# Executed certification commands

Run from `C:\Users\Ivica\Documents\SwingLens` with
`.venv\Scripts\python.exe`. Commands below use `-m pytest`, `-q --tb=short` and
`--junitxml=docs/remediation/run40-lifecycle/evidence/<named-output>` unless the
isolated browser procedure is stated. JUnit durations include test fixtures.
The machine-readable inventory is `evidence/executed-test-index.json`.

| Actual pytest arguments before `-q` | JUnit output |
|---|---|
| `tests/setup_lifecycle tests/test_pipeline_executor.py tests/test_pipeline_service.py tests/test_background_job_service.py tests/test_background_worker.py tests/test_worker_supervisor_reliability.py` | `final-unit.xml` |
| `tests/setup_lifecycle/test_query_service.py tests/setup_lifecycle/test_export_service.py tests/setup_lifecycle/test_replay.py tests/test_background_worker.py::test_ceri_job_renews_control_heartbeat_without_progress_callbacks` | `final-consumers.xml` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_real_worker_infrastructure_retry_claim_exhaustion_and_cancel tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_healthy_watchdog_delay_cannot_block_heartbeat_and_true_stall_still_fences tests/integration/test_worker_progress_reliability_postgresql.py tests/integration/test_pipeline_execution_authority_postgresql.py` | `worker-postgresql.xml` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_healthy_watchdog_delay_cannot_block_heartbeat_and_true_stall_still_fences tests/test_background_job_service.py tests/setup_lifecycle/test_evaluation_service.py` | `final-boundaries.xml` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_real_worker_infrastructure_retry_claim_exhaustion_and_cancel[terminal] tests/integration/test_lifecycle_immutability_postgresql.py tests/integration/test_setup_lifecycle_reconciliation_postgresql.py tests/integration/test_t14c_decision_writer_postgresql.py tests/integration/test_t14c_winner_writer_postgresql.py` | `authority-downstream.xml` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_legacy_partial_publication_reuses_original_proof_and_processes_alerts` | `legacy-replay.xml`, later `historical-replay.xml` after historical-selection coverage was added |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_failed_sql_session_cannot_publish_staged_business_rows_during_step_reporting` | `failed-sql-rollback.xml` |
| `tests/setup_lifecycle/test_canonicalization.py tests/setup_lifecycle/test_evaluation_service.py` | `canonical-checkpoints-unit.xml` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_native_lifecycle_interruption_replay_and_scale[2] tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_legacy_partial_publication_reuses_original_proof_and_processes_alerts tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_failed_sql_session_cannot_publish_staged_business_rows_during_step_reporting` | `final-checkpoints-postgresql.xml` on isolated snapshot `d538cfe` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_native_winner_continuation_fixture_contract` | `sector-session-after.xml`, two parameterized cases after the detached-sector correction |
| `tests/test_core_mutation_temporal_identity.py tests/test_source_mutation_authority.py tests/test_sector_rotation_service.py tests/test_sector_rotation_repository.py tests/test_sector_rotation_policy.py` | `sector-source-consumers.xml` |
| `tests/test_ranking_profile_service.py tests/test_combined_ranking_identity_adoption.py tests/test_pipeline_executor.py tests/test_technical_work.py` | `upstream-progress-unit.xml` |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_upstream_evidence_checkpoint_interruption_rolls_back_native_writes` | `upstream-progress-postgresql-corrected.xml` |
| `tests/test_pipeline_executor.py::test_ranking_evidence_checkpoint_honors_pipeline_cancellation` | `upstream-progress-cancellation.xml` |
| `tests/setup_lifecycle/test_evaluation_service.py tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_legacy_partial_publication_reuses_original_proof_and_processes_alerts tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_native_lifecycle_interruption_replay_and_scale[2]` | `final-replay-boundaries.xml`: 10 passed, including actual PostgreSQL cancellation at both final replay read boundaries. |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_native_lifecycle_interruption_replay_and_scale[88]` | Started with `RUN40_BENCHMARK_LABEL=final-native` and requested `native-88-final.xml`. Builder handed off its authentic fixture before current capture/timed evaluation; no final measurement/JUnit is claimed for that interrupted process. |
| `tests/integration/test_run40_lifecycle_remediation_postgresql.py::test_retained_native_88_fixture_on_final_runtime` | `native-88-final-runtime.xml`; `RUN40_NATIVE_FIXTURE_DUMP` points to authenticated `native-fixture-prepublication.dump`, `RUN40_BENCHMARK_LABEL=final-runtime`, `RUN40_ARTIFACT_DIR` is the absolute evidence directory. Actual capture and the exact existing interruption/publication/replay/Winner test body execute in a fresh process; final measurement is `final-runtime-88.json`. |

The native tests use the repository's disposable-database factory, migrations and
database-safety guard. There are no production database overrides or permission
bypasses. Temporary historical-fixture pauses are recorded in
`evidence/test-scheduling.json`; they occur before run 702 and its measured evaluation.

## Clean browser certification

The shared checkout is intentionally dirty. After explicit user authorization,
candidate files were copied byte-for-byte into the attached isolated worktree:

`C:\Users\Ivica\.codex\worktrees\run40-certification\SwingLens`

Only that detached worktree received local Git snapshot commits. The final
browser application candidate is `80026eba50c677bc2c08415f686c4b49d6fbda3e`.
The cold native run used `d839b59d4b8624a9e0cc1a999ddbffa72ee2cc11`, whose changes
after the browser run were only test-fixture execution. Final local snapshot
`4396ff7ad143360a044f7967445fd8f326f371d2` adds read-only completed-replay
episode/alert progress/cancellation checks, covered by the final 10-test group.
Its queries, validators, result construction, writers and authority are unchanged.
Source hashes and
version-specific coverage are in `evidence/certification-source-manifest.json`,
`candidate-equivalence.json` and `certification-source-version-matrix.json`.

The browser runner uses the absolute shared-checkout venv Python, with its working
directory set to the clean isolated worktree. It supplies the existing database
and PostgreSQL-admin URLs as environment variables (never printed), and puts
artifacts outside that worktree using `SWINGLENS_CERTIFICATION_ARTIFACT_ROOT`.
The fixture creates its own disposable database. Deterministic provider mode is
the existing default. It does not place broker orders or start a production run.

Final actual arguments:

```text
-m pytest
tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-88]
tests/e2e/single_run_certification/test_single_run_certification.py::test_single_run_comprehensive_e2e_certification[chromium]
-q --tb=short
--junitxml=C:\Users\Ivica\Documents\SwingLens\docs\remediation\run40-lifecycle\evidence\full-e2e-upstream-progress-final.xml
```

Artifact root: `evidence/e2e-upstream-progress-final/`.
Earlier ten-symbol and comprehensive positive verification on `87cdbdc` is in
`full-e2e-sector-session-final.xml`; its strict 88 failure remains in that same file.
All failed browser attempts and corrections are listed in
`CERTIFICATION_ATTEMPTS.md`.

## Other executed checks

```powershell
.venv\Scripts\python.exe -m docs.remediation.run40-lifecycle.verify_preservation
.venv\Scripts\python.exe -m docs.remediation.run40-lifecycle.review_registry
.venv\Scripts\python.exe scripts/check_architecture_registry.py --check
git diff --check
```

Ruff check/format checks were run on the modified production and test files.
The architecture check fails with 19 pre-existing missing registrations; the
baseline comparison reports zero unregistered sites added by this remediation.
Preservation and source-equivalence checks are read-only against production.
No live-provider canary is claimed by this certification.
