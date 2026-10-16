# SwingLens Task 2.9.12p Cross-System Regression Matrix

## Final targeted certification

Native JUnit: `TASK_2_9_12P_TARGETED_CERTIFICATION.xml`

Result: **203 passed, 0 failed, 0 errors, 0 skipped in 708.941 seconds**.

| Contract | Positive coverage | Negative/fence coverage | Targeted result | Full E2E result |
|---|---|---|---|---|
| CERI production and capture | Batched deterministic workflow, exact barrier | Missing/incomplete capture | Pass | Pass before failure |
| CERI non-interference | Stable semantic authority under unrelated metadata | Any semantic difference fails | Pass | No CERI difference observed |
| CERI continuation | Exact-once/coalesced and post-CERI continuation | Partial child, parent failure, stale generation | Pass | Continuation 32 acquired once |
| Decision handoff | Two-phase committed handoff, v1 compatibility | Later source/bar, mismatch, rollback | Pass | Handoff completed |
| Setup source/CPU ownership | Detached source context, independent progress, 100-member build | Pending parent writes rejected | Pass | CPU fix exercised; later transaction failed |
| Setup publication | 100 rows, evidence, concurrency, rollback | Altered retry and direct attacks | Pass | Did not finish before observer failure |
| Setup/Winner authority | Ready Winner consumer and permissions | Incomplete/degraded/optional variants | Pass | Winner not reached |
| Cancellation/execution fencing | Steps 17/19/23/26/29 and readiness authority | Stale/superseded/delayed execution | Pass | No fence violation observed |
| Source/PIT/config identity | PostgreSQL transition suite and T14c | Wrong context/run, post-cutoff changes | Pass | No identity mismatch observed |
| Transaction age | Setup-100 external `pg_stat_activity` observer | Parent transaction asserted absent during CPU | 35.933041 s max | **Fail: 120.292053 s** |
| Leaked Sessions/processes | Fixture cleanup and bounded Session tests | Cleanup assertions | Pass | DB dropped; no task-owned processes |
| Unauthorized duplicate evidence | Exact-once/concurrent capture tests | Duplicate and altered retry attacks | Pass | No duplicate reported before failure |

## Maintainable cross-subsystem gate

The following existing production-backed nodes form the recommended pre-merge gate. They use real
PostgreSQL where authority/persistence matters and do not create a parallel workflow model.

```text
tests/integration/test_ceri_pipeline_completion_barrier.py
tests/integration/test_ceri_batched_workflow_v2.py::test_ceri_authority_is_unchanged_by_downstream_operational_metadata
tests/test_pipeline_executor.py::test_post_ceri_continuation_runs_handoff_setup_and_winner_without_upstream_replay
tests/integration/test_transition_preflight_plan_postgresql.py::test_two_phase_manifest_accepts_committed_run_local_artifacts_and_is_idempotent
tests/integration/test_transition_preflight_plan_postgresql.py::test_historical_v1_anchor_is_verified_without_rewrite_and_fails_closed
tests/integration/test_transition_preflight_plan_postgresql.py::test_handoff_rejects_later_market_bar
tests/integration/test_transition_preflight_plan_postgresql.py::test_handoff_rejects_later_ceri_source_document
tests/integration/test_setup_source_session_contract_postgresql.py
tests/integration/test_run8_setup_performance_postgresql.py::test_setup_capture_run8_scale_is_bounded[100]
tests/integration/test_winner_consumer_eligibility_postgresql.py::test_native_winner_permissions_atomic_retry_and_frozen_history[ready]
tests/integration/test_pipeline_authority_certification_postgresql.py::test_17_stale_worker_generation_during_continuation_is_reported
tests/integration/test_pipeline_authority_certification_postgresql.py::test_19_terminal_pipeline_is_not_resumed_by_delayed_child_completion
tests/integration/test_pipeline_authority_certification_postgresql.py::test_23_completed_root_can_handoff_to_durable_ceri_dependency
tests/integration/test_pipeline_authority_certification_postgresql.py::test_26_cancel_observes_terminal_winner_idempotently
tests/integration/test_pipeline_authority_certification_postgresql.py::test_29_continuation_claim_and_running_transition_hold_one_authority_lock
tests/integration/test_t14c_decision_writer_postgresql.py::test_native_setup_output_and_altered_retry_roll_back[True]
tests/integration/test_t14c_decision_writer_postgresql.py::test_native_first_capture_single_and_batch_converge_concurrently
tests/integration/test_t14c_decision_writer_postgresql.py::test_setup_direct_attacks_roll_back_all_staged_rows
```

This gate should be extended, before it is relied upon as the full transaction gate, with one
targeted real-PostgreSQL node that composes the consumed 100-member handoff and Setup publication
under production SQL telemetry and records backend PID/Session ownership. The current Setup-100 node
does not reproduce that final Chromium condition.

## Development and final test record

| Selection | Result |
|---|---|
| Setup builder unit suite | 37 passed |
| Setup source Session PostgreSQL | 2 passed |
| First Setup-100 performance run | 1 failed: 2,603 evidence loads (preserved developmental finding) |
| Setup-100 after prefetch | 1 passed |
| CERI non-interference | 1 passed |
| Setup builder + pipeline executor | 79 passed |
| T14c Setup authority/rollback/concurrency | 3 passed |
| Final clean integrated selection | 203 passed, no skips/xfails |
| Original Chromium-100 | 1 failed, no retry |

## Static/source authority

- `python -m compileall -q app tests`: pass
- Ruff lint on changed files: pass
- Ruff format check: 5 files already formatted
- `git diff --check`: pass; only pre-existing LF/CRLF warnings
- Official T14 validator: `{"stale":[]}`
- Official current-overlay source-pin update process was used; historical immutable evidence was not rewritten.

