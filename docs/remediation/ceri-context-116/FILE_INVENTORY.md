# Exact implementation inventory

`file-inventory.json` records the baseline, SHA256 and byte length of every
changed or added source/test utility. `implementation.patch` contains all those
files, including additions, and passes `git apply --check` against pristine
`6d6952f`. No changes are committed.

| File | Change and purpose |
|---|---|
| `alembic/versions/20261116_0096_ceri_native_context.py` | Transactional native/legacy partial unique indexes; duplicate preflight; safe downgrade refusal. |
| `app/models/ceri_tables.py` | Match model metadata to native, legacy and independent replay contracts. |
| `app/services/ceri/score_ownership.py` | Resolve one actual main/reverse pipeline context binding. |
| `app/services/ceri/score_population.py` | Shared exact current population, frozen identity, immutable seals and PIT lineage validation. |
| `app/services/ceri/capture_service.py` | Context-qualified retry candidates, strict compatibility, verified owner, writer serialization and configuration source binding. |
| `app/services/ceri/snapshot_service.py` | Validate physical native fields against frozen identity/context before persistence. |
| `app/services/ceri/evidence_eligibility.py` | Use actual bound owner; unresolved legacy evidence fails closed on pipeline uploads. |
| `app/services/ceri/pipeline_containment_service.py` | Contain the failed owner's evidence; explicitly record unresolved snapshot incidents. |
| `app/services/ceri/change_rebuild_service.py` | Restrict current generation to exact score receipt while retaining prior comparison selection. |
| `app/services/ceri/job_handlers.py` | Verify owner/workflow and carry exact certified IDs through capture, change and alert. |
| `app/services/core_mutation_authority.py` | Validate dedicated reverse owner without assigning the unique main-context FK. |
| `app/services/market_calculation_context_service.py` | Share strict frozen context validation with explicit MAIN and CERI roles; dedicated reverse ownership stays unambiguous. |
| `app/services/decision_mutation_authority.py` | Select the CERI role for native score-change decisions while retaining main-context checks for other writers. |
| `app/services/pipeline_service.py` | Certify exact nonempty capture receipt before async completion and dependency continuation. |
| `app/services/pipeline_executor.py` | Apply the same population validation to synchronous capture and resumed barriers. |
| `app/services/transition_preflight_plan_service.py` | Share current handoff selection/validation; preserve historical manifest membership. |
| `app/services/pipeline_state_machine.py` | Permit recovery's idempotent queued-parent reclaim, retaining terminal fences. |
| `tests/ceri/test_native_score_identity.py` | Eight identity/seal rejection controls. |
| `tests/integration/test_ceri_context_uniqueness.py` | Actual PG migration, NULL/replay semantics, concurrent insertion and non-lossy rollback behavior. |
| `tests/integration/test_ceri_batched_workflow_v2.py` | Actual native pipeline capture/tail/barrier/manifest receipt and strict rejection cases. |
| `tests/integration/test_ceri_pipeline_completion_barrier.py` | Unsealed fabricated populations cannot complete the barrier. |
| `tests/integration/test_ceri_run_isolation_remediation.py` | Bound sibling ownership, safe PARTIAL policy and ambiguous legacy containment. |
| `tests/integration/test_ceri_change_rebuild_scoping_postgresql.py` | Real owner/context fixtures for current-generation scoping; preserve all owner-state cases. |
| `tests/integration/test_ceri_systemic_remediation.py` | Use real frozen calendar identity in immutable-evidence tests. |
| `tests/integration/test_ceri_dedicated_change_authority.py` | Actual native score-change event with a sealed historical comparator; reject main-role and ambiguous-owner substitutions. |
| `tests/ceri/test_ceri_schema.py` | Assert new indexes and retained replay uniqueness. |
| `tests/ceri/test_t15d_residual_findings.py` | Schema-head assertions updated to 0096. |
| `tests/ceri/test_ceri_acceptance_fixture.py` | Supply explicit valid cutoff in the purge fixture. |
| `tests/ceri/test_wave1_p0.py` | Fake DB supports retained issuer-successor ownership lookup. |
| `tests/ceri/test_sec_processor_lifecycle.py` | Retain fail-closed same-ticker issuer conflict; separately test distinct issuer signatures. |
| `tests/test_pipeline_recovery_transition.py` | Five public terminal-fence cases around recovery reclaim. |
| `tests/test_contextual_calculation_identity_adoption.py` | Give legacy mocks actual native key fields and positive IBMI fixtures the frozen engine version; retain unknown/calendar rejection. |
| `scripts/qa/ceri_validation_environment.py` | Safe QA credentials, read-only source checks/schema export, preflight and isolated test runner. |
| `scripts/qa/ceri_historical_fixture_closure.py` | Explicitly authorized genuine missing-row prerequisite copy with FK closure and batch hashes. |
| `scripts/qa/ceri_scope_fixture_completion.py` | Exact separately approved 447 scope-member copy, row-fingerprint checks, offline QA counters and real abandoned-claim deferral. |
| `scripts/qa/ceri_handoff_fixture_completion.py` | Complete the approved technical prerequisite closure from 447 exact immutable Combined pins, including blocked-input references; never change existing rows. |
| `scripts/qa/ceri_context_116_certification.py` | Mandatory native historical capture with genuine job-2546 configuration and real claims/services. |
| `scripts/qa/ceri_context_116_continuation.py` | Finish only existing admitted QA parent jobs through actual default handlers and terminal completion gates. |
| `scripts/qa/ceri_certification_postflight.py` | Actual postflight, historical manifest-41 membership and transactional downgrade-refusal evidence after a successful capture. |

The accompanying report, canonical contract, recovery procedure, SQL, test XML,
historical hashes and certification diagnostics are evidence artifacts. They
are not production application changes. Scratch diagnostics, temporary virtual
environments and the unrelated primary checkout are excluded from the patch.
