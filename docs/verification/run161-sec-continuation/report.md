# 1. EXECUTIVE VERDICT

RUN-161 ROOT CAUSE CONFIRMED: YES  
CONFIGURATION LINEAGE FIXED: YES  
DETERMINISTIC RETRY CLASSIFICATION FIXED: YES  
PIPELINE ERROR VISIBILITY FIXED: YES  
DUPLICATE CONTINUATION PROTECTION CERTIFIED: YES  
RUN 161 SAFE TO RECOVER: NO — normal runtime replacement is blocked  
RUN 161 RECOVERED: NO

The five code/certification YES results describe the working-tree fix, which
has not been loaded by the production runtime. Incident data satisfies the
configuration/SEC prerequisites, but the normal lifecycle command refused
deployment because runtime identity metadata is missing. Recovery stopped at
that safety guard; no Resume request or replacement continuation was issued.

# 2. ROOT CAUSE

Upload run 161 owns pipeline execution 151. Its original FULL_PIPELINE job is
43268. The immutable registry contains `pipeline:151` and `job:43268`, both
pointing to anchor
`c3a654bf956deacdda507957a963743c15162eb6b6955004a377ba78c393e1f2`, with business
fingerprint `7bc8ee0c7b8ac8c44a770023afc0cd41155d2ca7f5b4afd555044319be30f77d`.
There is no run-scoped configuration binding in the certified schema: the
execution, rather than the uploaded CSV, owns configuration authority.

SEC_READINESS_REPAIR job 43269 is a child of 43268 and carries pipeline_run_id
151, related_run_id 161, the SEC processor signature, and its validation resume
checkpoint. It has no `job:43269` binding. This absence is intentional under
Phase 4's helper-job exclusion, not a missing original pipeline anchor.

The exact failing path is `execute_sec_readiness_repair` →
`enqueue_pipeline_after_sec_repair` → `enqueue_job` → `anchor_enqueue_payload`
→ `binding_reference(job_id=43269)`. Worker causality makes the repair the
continuation's immediate parent. The old enqueue resolver checks that parent's
binding before reaching the retained pipeline binding and raises
MISSING_CONFIGURATION_ANCHOR_BINDING. A read-only reproduction confirmed this
path. No manifest or latest-record lookup is involved.

The four repair ingestions (53354–53357) completed for DOCN, EBAY, PAY and SEI;
their scopes name pipeline 151/run 161 and their configuration hash matches
the pipeline's retained execution.ceri configuration. All 86 tickers are ready.
The failure is continuation scheduling, rather than SEC acquisition. Repeated
attempts cannot fix the helper's intentionally absent binding. The old worker
eventually exhausted all five retries, leaving the pipeline PREPARING and
validation PENDING despite the terminal job failure.

# 3. ARCHITECTURAL FIX

The continuation should not obtain configuration authority from a repair job:
that job is a correlation/transport hop. `ExecutionConfigurationBinding`
remains the immutable authority. For a
pipeline-linked execution, `pipeline:<id>` owns C1. A shared execution resolver
verifies run ownership, any supplied anchor and fingerprint, mandatory business
job bindings, and declared parent/root execution relationships against C1.
Pipeline-linked SEC helpers resolve this registry authority even when their
pre-contract queue payload has no anchor. Existing helper bindings, if present,
must agree; they are never manufactured or backfilled.

New helpers retain C1's reference in their payload. Both direct SEC repair calls
and worker execution install verified C1 delivery, so native configuration
loaders and continuation enqueue retain it after C2 drift or restart. Helpers
cannot become new configuration roots. Missing authority and mismatches fail
deterministically. The SEC processor signature is also fenced against the
repair request's original signature.

Continuation publication checks and locks the current durable lease row, then
locks the pipeline row. The existing permanent workflow-stage uniqueness index
is used with `pipeline:<id>:sec-continuation`. Existing continuations, including
completed or legacy rows, are verified and reused. Replayed repair completions
return that continuation before resetting pipeline progress or acquiring SEC
data. Stale lease owners cannot publish progress or continuation state.

Terminal repair and FULL_PIPELINE failures publish actual pipeline/step errors
with safe identifiers, including failures before handlers start. The awaiting
pipeline's retained job pointer determines error ownership, so a wrong child
pipeline/run payload cannot conceal the original pipeline's failure. Read-only
status projection also exposes failures left by the old worker. The existing
Resume action reconciles that terminal helper checkpoint
through application state transitions and queues continuation under the
retained pipeline anchor; it does not patch historical evidence or bindings.

# 4. FILES CHANGED

- `app/services/configuration_delivery.py`: shared execution ownership resolver;
  helper delivery and enqueue inheritance with mismatch and cycle protection.
- `app/services/background_worker.py`: authoritative delivery for pipeline
  helpers/business jobs and captured attempt token.
- `app/services/background_job_service.py`: precise deterministic configuration
  failure taxonomy, transactional lease fencing and terminal execution reporting.
- `app/services/ceri/sec/readiness_repair.py`: C1 execution scope, processor fence,
  completed-continuation reuse, publication fencing and visible failure states.
- `app/services/pipeline_service.py`: permanent continuation idempotency,
  validated existing jobs, read-only terminal status projection and normal
  Resume support for failed awaited helpers and pipeline continuations.
- `tests/integration/test_sec_readiness_repair_postgresql.py`: retained-binding
  setup for existing tests and PostgreSQL incident regression scenarios.
- `tests/test_configuration_lineage_retry.py`: deterministic configuration
  failures and preserved transient/unknown-error retries.
- `tests/ops/test_lifecycle_powershell.py`: isolate mocked core-stop cleanup
  to a temporary runtime-state file. The old test used the real state path.
- `tests/ops/test_lifecycle_safety.py`: isolate physical process/listener
  evidence for the mocked dead-runtime case; it must not inspect the live host.
- This verification directory: forensic snapshots, test transcripts,
  credential-safe disposable test runner, integrity comparison script and report.

Verification helpers: `check_incident.py`, `run_tests.py`,
`run_baseline_checks.py`, `snapshot_integrity.py`.
Snapshots: `baseline.json`, `sec-evidence.json`, `integrity-before.json`,
`integrity-predeploy.json`, `integrity-after.json`, `pre-recovery.json`,
`final-incident.json`, `runtime-registrations.json`, `runtime-stop.json`.
Transcripts: `alembic-check.txt`, `focused-initial.txt`, `focused-final.txt`,
`focused-certification.txt`, `focused-certified.txt`, `postgres-initial.txt`,
`postgres-certification.txt`, `postgres-final.txt`, `incident-final.txt`,
`incident-certified.txt`, `restart-certified.txt`, `unit-suite.txt`,
`broad-failure-recheck.txt`, `baseline-winner-checks.txt`,
`preflight-certified.txt`, `process-pool-certified.txt`,
`lifecycle-certified.txt`, `integrity-predeploy.txt`, `integrity-after.txt`.
`report.md` is this report. Earlier transcripts are retained as diagnostics;
only the explicitly described final runs are certification evidence.

# 5. TESTS

Commands below run from `C:\Users\Ivica\Documents\SwingLens`. The test runner
uses the configured local server's admin connection without printing credentials.
Every PostgreSQL fixture verifies a `swinglens_pytest_` database identity before
migration and drops that disposable database afterwards.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_configuration_lineage_retry.py tests/test_pipeline_service.py tests/test_pipeline_executor.py tests/test_background_worker.py tests/test_background_job_service.py tests/ceri/test_sec_identity_repair.py tests/ceri/test_sec_processor_lifecycle.py tests/ceri/test_sec_processor_capability.py tests/ceri/test_sec_client_telemetry.py tests/ceri/test_sec_guidance.py -q --tb=short
```

**139 passed**, latest production changes. `focused-certified.txt`.

```powershell
.venv\Scripts\python.exe -m pytest tests/ceri/test_sec_pipeline_preflight.py -q --tb=short
.venv\Scripts\python.exe docs/verification/run161-sec-continuation/run_tests.py tests/integration/test_sec_readiness_repair_postgresql.py -q --tb=short -rs
```

**9 passed** preflight; **13 passed** PostgreSQL incident cases after the final
authoritative error-owner fix. `preflight-certified.txt`, `incident-certified.txt`.
The incident cases cover real validation/bootstrap scheduling, repair, live C2
drift, native C1 fundamental calculation evidence, missing authority, wrong
anchor/pipeline/run, retained helper binding B, completed replay without downloads,
concurrent completion, stale recovered leases, legacy helper compatibility,
old-worker error visibility and normal Resume, and pre-handler continuation failure.

```powershell
.venv\Scripts\python.exe docs/verification/run161-sec-continuation/run_tests.py tests/integration/test_sec_readiness_repair_postgresql.py::test_worker_repair_continuation_restart_and_c2_drift_keep_c1 -q --tb=short -rs
```

**1 passed**, including a fresh Python worker with C2 settings and no inherited
ORM, delivery scope or settings cache. It rejects current-configuration resolution
and returns the original C1/native configuration identities. `restart-certified.txt`.

```powershell
.venv\Scripts\python.exe docs/verification/run161-sec-continuation/run_tests.py tests/integration/test_sec_readiness_repair_postgresql.py tests/integration/test_decision_configuration_delivery_postgresql.py tests/integration/test_phase4_configuration_certification_postgresql.py tests/integration/test_ceri_batched_workflow_v2.py tests/integration/test_winner_jobs_reliability_postgresql.py -q --tb=short -rs -o faulthandler_timeout=60
```

**60 passed, 3 failed** in the broad PostgreSQL run. All incident, configuration
delivery, native Phase-4 process/retry/resume, migration round-trip and CERI workflow
cases passed. The failures are winner manual-continuation fixtures lacking a job
binding; latest-rescore fixtures lacking a generation binding; and a winner batch
prefetch timing limit (164 seconds versus 10). All three reproduce on the untouched
starting SHA: the same two missing-binding errors and 92.9 seconds versus 10.
No enforcement was weakened
to make old fixtures pass. `postgres-final.txt`.

The full unit lane was executed through a credential-safe stdin launcher using
`pytest.main(["-m", "unit", "-q", "--tb=short"])`: **3,307 passed, 2 failed,
7 skipped, 295 deselected**. One failure was Windows process spawning unable to
reload the stdin launcher; a normal `python -m pytest` rerun passes. The other
lifecycle test assumes physical quiescence while the user's application is live;
its physical evidence is now isolated explicitly. `unit-suite.txt`,
`broad-failure-recheck.txt`. The entire repository suite, including external/live
and other integration lanes, was not run.

```powershell
.venv\Scripts\python.exe -m pytest tests/ops/test_lifecycle_powershell.py tests/ops/test_lifecycle_safety.py tests/ops/test_lifecycle_stopped_generation.py -q --tb=short
```

**78 passed** after isolating the two lifecycle fixtures. This also retains the
tests proving occupied listeners/live processes block stale-state retirement.
`lifecycle-certified.txt`. Both initially failing unit cases now pass their
appropriate reruns; this is not a second complete unit-lane run.

An earlier parallel broad run emitted a Windows access violation during the
winner stress case. The guarded runner completed with the results above; this
diagnostic transcript remains in `postgres-certification.txt` and is not counted
as a successful certification run.

```powershell
git worktree add --detach .qa_work/run161-baseline d3157c8642c119a7e5d7a84c69f4a58c6d3866d8
.venv\Scripts\python.exe docs/verification/run161-sec-continuation/run_baseline_checks.py tests/integration/test_winner_jobs_reliability_postgresql.py::test_manual_race_with_continuation_has_one_active_workflow tests/integration/test_winner_jobs_reliability_postgresql.py::test_bounded_generation_resume_coalescing_and_atomic_publication tests/integration/test_winner_jobs_reliability_postgresql.py::test_h5_batch_context_prefetch_is_constant_query_count_for_thousands -q --tb=short -rs
git worktree remove .qa_work/run161-baseline
.venv\Scripts\python.exe -m pytest tests/test_technical_work.py::test_process_pool_shadow_mode_returns_fresh_score_and_validation_candidate -q --tb=short
```

Baseline: **3 failed**, reproducing the pre-existing failures.
`baseline-winner-checks.txt`. The clean task-owned baseline worktree was removed
normally. Process-pool harness recheck: **1 passed**. `process-pool-certified.txt`.

```powershell
$env:SWINGLENS_DATABASE_SAFETY_CONTEXT = 'AUTHORITATIVE_LOCAL'
.venv\Scripts\python.exe -m alembic check
.venv\Scripts\ruff.exe check app/services/background_job_service.py app/services/background_worker.py app/services/configuration_delivery.py app/services/pipeline_service.py app/services/ceri/sec/readiness_repair.py tests/integration/test_sec_readiness_repair_postgresql.py tests/test_configuration_lineage_retry.py
git diff --check
```

Alembic: **No new upgrade operations detected** at
`0080_effective_configuration`. Ruff and whitespace checks pass. No migration added.

# 6. RUN 161 RECOVERY

Final database state is preserved in `final-incident.json`:

- Upload run 161: COMPLETED, 86 CSV rows. This is upload completion, not pipeline completion.
- Pipeline 151: PREPARING, current stage VALIDATING_RUN; validation remains PENDING
  in the old runtime. No remaining pipeline stage has been reached.
- SEC readiness: 86/86 under processor `sec-guidance:eed017654682a0c9`.
- Original root job 43268: COMPLETED. Repair 43269: FAILED, retry_count 6,
  no execution token/lease. No continuation exists; no new production jobs were created.
- Original bindings: `pipeline:151` and `job:43268`, with the anchor and
  fingerprint shown in section 2. Their identities and payload hash are unchanged.
  No helper binding was backfilled.
- Market context 13 retains the original 2026-09-16 completed session and
  2026-09-16T23:20:46.979906Z cutoff. IB API readiness passed its read-only handshake.
- No SEC downloads or processing were repeated in production.

A read-only invocation of the fixed status service against the real incident
correctly projects BLOCKED with: “Automatic SEC preparation completed, but
pipeline continuation failed. Pipeline 151, job 43269:
MISSING_CONFIGURATION_ANCHOR_BINDING.” The underlying database remains
PREPARING; the live runtime still uses the old implementation.

After certification, the authorized normal command was attempted:

```powershell
pwsh -NoProfile -File .\swinglens.ps1 stop -Json
```

It established the normal worker claim fence, acknowledged quiescence and
confirmed zero active jobs. It then **refused shutdown** with `FOREIGN_LISTENER`:
port 8000 is occupied but strong runtime identity verification cannot proceed
because `data/cache/swinglens-lifecycle.json` is missing. Operation ID:
`35153eb9-43bd-4b69-95c2-a00873f125d4`. `runtime-stop.json` captures the refusal.

Investigation identified a pre-existing test-isolation defect:
`test_core_stop_waits_for_supervisor_exit_before_registered_cleanup` mocks
process exits and listeners but used the module's real RuntimeStatePath. Running
the broad unit lane executed its actual final Remove-Item against the live
metadata file. This session caused that operational metadata loss by running
the existing test. The test now uses a task-owned temporary file, and the other
dead-runtime unit test now mocks physical evidence consistently. Neither change
weakens production lifecycle verification.

The normal stop failure automatically resumed claims; final worker
quiesce_requested_at/quiesced_at are null. The original web, supervisor and worker
are still alive. No forced termination, reconstructed identity state, start,
Resume POST, manual run/pipeline patch or configuration-binding mutation followed
the refusal. This follows the requested STOP condition when normal safe recovery
is unavailable.

Recommendation: resolve the runtime metadata loss through a separately reviewed
lifecycle recovery procedure, load this certified patch, then use the existing
Resume action for pipeline 151 at VALIDATING_RUN. Preserve context 13 and C1;
the already-ready SEC evidence should be reused. A CSV re-upload or full SEC
redownload is unnecessary.

# 7. DATA INTEGRITY

Investigation and certification use read-only production queries and disposable
PostgreSQL fixtures. `baseline.json`, `sec-evidence.json` and
`integrity-before.json` retain the forensic state and historical content hashes.
Integrity comparison uses original ID ceilings to distinguish historical
mutation from authorized new downstream calculation evidence.

`integrity-predeploy.json` matches the original snapshot for every captured class:
49,217 SEC filing documents; 74,418 document extractions; 1,081 sync-state rows;
1 transition handoff manifest; 1,895 winner evidence manifests; 51 frozen
configuration records; original anchor/bindings; all four incident ingestions
and their 439 CERI source records. Historical core evidence had zero rows at
baseline. The original market context 13 is retained with its original cutoff
and completed session. These checks cover the named historical tables and
incident records; they are not an exhaustive hash of every production table.

`integrity-after.json` and `integrity-after.txt` show every captured historical
hash still matches after the refused recovery. This includes SEC/CERI source
evidence, immutable manifests, winner evidence and retained configuration.
No new calculation identities were created in production. There are no jobs
after the original job-ID ceiling, and no pipeline continuation. Production
evidence unrelated to run 161 received no writes from this work; captured
historical SEC/manifest tables are unchanged. The exception is **operational
runtime metadata**, described in section 6: its file was removed by the old unit
test. It is not financial evidence or a configuration binding and remains
unrestored because fabricating runtime identity would bypass the safety guard.

# 8. SIBLING PATH AUDIT

- Initial SEC bootstrap, automatic repair and repair at CERI stage re-entry use
  the same helper scheduler/executor and are covered by the systemic fix.
- Manual retry/resume resolves the existing pipeline registry binding;
  `pipeline_configuration_delivery` continues to fence stage re-entry.
- CERI provider, feature, capture, change and alert workflows use the shared
  enqueue/delivery path; child jobs inherit verified retained authority. A
  standalone explicit rebuild is a new current-rules operation, not a historic
  pipeline continuation.
- Winner maturation continuations declare parent/root lineage. The shared
  resolver verifies that lineage. Cohort generations keep their separately
  retained winner-generation bindings; no generation authority is rewritten.
- Setup lifecycle and IB intelligence worker handlers use retained job delivery.
  Existing direct legacy APIs remain explicitly current-rules entry points;
  this patch does not reinterpret them as certified historic execution.
- Deferred jobs, watchdog recovery and abandoned-worker recovery retain their
  existing queue payload/bindings while replacing lease ownership. No new
  configuration resolution is introduced by deferral/recovery.
- IB fetch, scanner, Flex import, prewarm and recovery probe remain excluded
  transport helpers. Inspection found no equivalent helper-to-business
  continuation requiring a fabricated helper binding in these paths.
- No latest configuration record or current-env fallback was added. Pipeline
  and winner-generation authorities remain separate certified scopes.
- Separately, `market_context_for_pipeline` documents first-execution temporal
  initialization for legacy pipelines without a context row, and
  `market_context_for_upload_run` selects the latest upload-level context for
  standalone consumers. These are existing temporal compatibility policies,
  rather than configuration-anchor continuation resolvers. Run 151 already
  retains context 13; recovery must reuse it. No broader temporal policy was
  changed in this patch.

# 9. REMAINING RISKS

- The live runtime has not loaded the fix. Run 161 remains unrecovered until
  runtime identity metadata is resolved and normal deployment/Resume succeeds.
- The broad unit run exposed an unsafe pre-existing PowerShell test and removed
  the live operational runtime-state file. Test isolation is fixed; the missing
  operational metadata is still a deployment blocker. No financial evidence changed.
- Three pre-existing winner integration failures reproduce on the starting SHA:
  two fixtures lack certified bindings and one misses its performance budget.
  They require separate fixture/performance work; they were not bypassed here.
- Optional Docker observability was already degraded at baseline.
- The full external/live repository suite was not executed. Broader validation
  therefore does not constitute an entirely green full-suite certification.

# 10. GIT STATE

Starting branch: `codex/t13e-phase4-config-certification`.
Starting SHA: `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8`.
The worktree was clean at the forensic baseline. No commits or branch switches
were created for this repair. Final SHA is unchanged:
`d3157c8642c119a7e5d7a84c69f4a58c6d3866d8`. Commits created: **0**.
The worktree contains the nine code/test changes in section 4 and this untracked
verification directory. All starting unrelated changes were preserved (the
starting worktree was clean). The task-owned detached baseline worktree was
removed normally; the main branch was never switched, reset or cleaned.
