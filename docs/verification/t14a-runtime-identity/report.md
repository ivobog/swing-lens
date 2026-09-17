# 1. EXECUTIVE VERDICT

- RUN-161 FIX COMMITTED: YES
- LIVE RUNTIME IDENTITY PROVEN: YES
- LIFECYCLE METADATA RECOVERY SAFE: YES
- LIFECYCLE METADATA RECOVERED: YES
- NORMAL SHUTDOWN CERTIFIED: YES
- OLD RUNTIME FULLY STOPPED: YES
- PORT 8000 RELEASED: YES
- RUN 161 MUTATED: NO
- SAFE FOR T14B DEPLOYMENT: YES

SwingLens is intentionally STOPPED. No new runtime was started, Run 161 was not resumed, and no continuation was created. Optional Docker observability remains unavailable; it did not block canonical core shutdown. T14B is a separate deployment/recovery task.

# 2. STARTING STATE

Branch: `codex/t13e-phase4-config-certification`. Starting HEAD: `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8`. Nine tracked remediation/test files were modified and the prior verification directory was untracked. The complete status, diff/stat, migration head, process tree, listeners, safe identity environments, registrations, health responses, jobs and supervisor recorder were captured before edits/commits in `baseline.json` and `starting.diff`.

Web PID 8408 owned 127.0.0.1:8000. Supervisor PID 15440 and worker PID 4000 were alive, with venv launchers 6972/2880/13124. `data/cache/swinglens-lifecycle.json` was absent (`metadata_exists=false`). Migration head was `0080_effective_configuration`, matching the repository. Run 161/Pipeline 151 and all original authority/context invariants passed read-only baseline checks.

# 3. RUN-161 FIX COMMIT

Remediation commit: `25cc0a3bc546ad51728d6a224fcac2141b20a0bc`; exact tree: `7e475521c55e7e40fe2321ba7b27c172468a0d61`. The nine files and reasons are listed in section 7. Existing remediation was reviewed and preserved without altering the financial recovery behavior during T14A.

Prior certification artifacts were committed separately as `7c4955dae3adbf152491bfc489f0d77e2e068406`; tree `4c31df1ae0b53edc37a527d3885188b83c1d36db` (33 files). They remain unchanged point-in-time records of the earlier blocked stop, not descriptions of the final T14A state. After those commits only T14A files were outstanding. T14A code is separately committed as `4683a6cee21c3282e96a743c0fdb0df56afabb0f`, tree `7c560576a14e1cdd20811158fbb61dae996ffccb`. Its eight files are listed below. The final verification commit and clean Git status are recorded in the final delivery response after committing this report.

# 4. RUNTIME IDENTITY EVIDENCE

| Evidence | Observed value | Expected value | Result |
|---|---|---|---|
| Listener | 127.0.0.1:8000, PID 8408 | Configured address/port; sole verified web owner | PASS |
| Executable | C:\Users\Ivica\AppData\Local\Programs\Python\Python312\python.exe (inner roles); repo .venv\\Scripts\\python.exe (launchers) | Expected base interpreter or repository venv executable | PASS |
| Web command | -m app.serve --host 127.0.0.1 --port 8000 --runtime-instance-id 330f441756da4de7a1b44d2263936dfe --repo-root C:\Users\Ivica\Documents\SwingLens | Exact module and single-valued matching bind/repository/UUID arguments | PASS |
| Supervisor command | -m app.worker_supervisor --worker-id local-worker-1 --queues interactive,broker,background; same host/port/UUID/repository arguments | Exact module, worker scope and generation | PASS |
| Worker command | -m app.worker --worker-id local-worker-1 --queues interactive,broker,background | Exact module/scope; generation proved by actual environment, not invented CLI arguments | PASS |
| OS creation timestamps | Web 2026-09-16T23:18:49.491671+00:00; supervisor 2026-09-16T23:18:46.987648+00:00; worker 2026-09-16T23:18:50.094193+00:00 | DB/watchdog timestamps agree within 0.01 seconds; children do not predate ancestors | PASS |
| Repository | C:\Users\Ivica\Documents\SwingLens; all inner working directories and verified CLI repository arguments agree | This repository | PASS |
| Supervisor | 15440; registry instance a39f9b30b9bf410cad7728939a611dce, generation 65 | OS PID/start time agrees with fresh DB row and supervisor recorder | PASS |
| Worker | 4000; registry instance 42ef9f672dcf462cbc0ae2bea1f6fb1b, generation 76 | OS PID/start time and launcher agree with fresh nonstopping DB row | PASS |
| Ancestry | 6972 → 15440 → 2880 → 8408; 15440 → 13124 → 4000 | Same verified supervisor tree and launchers | PASS |
| Parent watchdog | Web/worker environment parent PID 15440 and its exact creation timestamp | Same proven supervisor | PASS |
| Heartbeat | Fresh worker/supervisor registrations and supervisor flight recorder before recovery | Within configured timeout, capped at 60 seconds | PASS |
| Generation | 330f441756da4de7a1b44d2263936dfe; Git d3157c8642c119a7e5d7a84c69f4a58c6d3866d8; fingerprint 0494fb0b2d5610866450948e7ed055622c0563a9317a60563844669aea17da27 | All three live environments and supervisor recorder agree on the old generation | PASS |
| Topology | supervisor-root-v1 | supervisor-root-v1 | PASS |
| Health/readiness | /health 200 and /ready 200; worker ready | Supporting evidence only; not used as adoption authority | PASS |
| Log evidence | Structured process_boot records match the live UUID, Git/fingerprint and current role PIDs | Independent startup ownership corroboration | PASS |
| Durable safety | 0 active jobs; 0 running pipelines; no jobs after 43269 | No active financial processing | PASS |


No strong evidence contradicted the live identity. The recorder documents one earlier worker restart during original startup; the current worker is uniquely verified by its live PID, creation time, fresh registration, launcher and environment. The worker CLI has no runtime UUID argument: its actual environment supplies that independently cross-checked identity. No CLI field was fabricated.

Every recovered field has a source: PID/creation time/executable from OS observations; repository/role/module/UUID from verified CLI/cwd/environment; Git/fingerprint from agreeing live environments and recorder; supervisor/worker registry instance/generation from fresh DB registrations; topology from the verified recorder; process group from the verified outer supervisor launcher; web legacy launcher fields from its verified supervisor owner; port from verified configuration/CLI/socket; schema version from the existing v5 contract; recordedAtUtc from the recovery observation clock. The actual published state is retained in `recovered-state.json`.

# 5. ROOT CAUSE OF METADATA LOSS

The pre-existing core-stop test reached production's default RuntimeStatePath cleanup. The already-certified fixture repair places that state in `tmp_path` and mocks operational probes. T14A's focused audit additionally found that the shared PowerShell helper allowed other module defaults to resolve under the real repository. Its autouse fixture now requires a test-owned root and overrides RepoRoot and RuntimeStatePath before lifecycle functions run, isolating default files, locks, journals and fallback probes.

The regression writes through those defaults and checks the exact temporary paths. Python cleanup/publication/signal tests likewise use temporary files and mocked OS/DB operations. The direct Windows PowerShell 5.1 launcher test exits at the version gate before import. The explicitly live `tests/e2e/test_windows_lifecycle_persistence.py` was identified and not executed. No destructive live tests ran. Audit findings/search results are preserved in `test-path-isolation.md` and `test-path-audit.txt`. Metadata was still missing after the unit lane and before explicit recovery.

# 6. RECOVERY ARCHITECTURE

Existing lifecycle ownership uses recorded PID/creation time, repository/module/runtime UUID, listener ownership, supervisor ancestry and v5 Git/fingerprint/topology; DB registrations support quiescence and registered remainder cleanup. Existing write-state is a trusted-launch path, and stale-state retirement handles dead generations. Inspection found no supported strong adoption mechanism for a live generation with missing metadata.

Added explicit `pwsh -NoProfile -File .\swinglens.ps1 recover-identity -Json` under the existing lifecycle lock. It collects read-only OS/DB/recorder observations twice, proves each, requires stable generation/process/registration identities, validates the unchanged v5 schema, and atomically publishes through a create-only hard link. Existing valid, corrupt, empty or raced-in files are never overwritten. No application process is launched or signaled, and recovery performs no DB writes.

Recovery refuses unknown/ambiguous listeners or roles, wrong executable/module/repository/scope/bind, unavailable evidence, stale or stopping registrations, creation-time mismatches/PID reuse, inconsistent UUID/Git/fingerprint/topology, broken ancestry/watchdogs/launchers, changed observations, active jobs or running pipelines. No current PID is special-cased and no generation field is inferred from the new HEAD.

After full existing OS ownership checks, normal shutdown can accept a different desired configuration fingerprint via an explicit shutdown-only option. Startup/reuse and normal status still report ACTIVE_GENERATION_MISMATCH/RESTART_REQUIRED. FOREIGN_LISTENER and PID/ancestry checks remain intact. Shutdown revalidates ownership and submits the existing instance/PID/start-time-scoped request to the proven supervisor.

Recovery exit: 0; operation `a2567c8f-516d-44e3-9073-24333d6ade86`. It reconstructed the OLD `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8` generation with fingerprint `0494fb0b2d5610866450948e7ed055622c0563a9317a60563844669aea17da27`, never the newly committed code generation.

# 7. FILES CHANGED

Previously certified Run-161 code/tests preserved in the remediation commit:

| File | Why |
|---|---|
| [app/services/configuration_delivery.py](<C:/Users/Ivica/Documents/SwingLens/app/services/configuration_delivery.py>) | Carry immutable authority through descendant jobs; reject missing authority deterministically. |
| [app/services/background_worker.py](<C:/Users/Ivica/Documents/SwingLens/app/services/background_worker.py>) | Load the original delivery before dispatch and preserve it across continuation execution. |
| [app/services/background_job_service.py](<C:/Users/Ivica/Documents/SwingLens/app/services/background_job_service.py>) | Classify configuration lineage failures as terminal and retain duplicate-continuation protection. |
| [app/services/ceri/sec/readiness_repair.py](<C:/Users/Ivica/Documents/SwingLens/app/services/ceri/sec/readiness_repair.py>) | Create continuation under the original pipeline configuration scope. |
| [app/services/pipeline_service.py](<C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_service.py>) | Expose failed repair as a blocked run with its error through a read-only projection. |
| [tests/integration/test_sec_readiness_repair_postgresql.py](<C:/Users/Ivica/Documents/SwingLens/tests/integration/test_sec_readiness_repair_postgresql.py>) | Disposable PostgreSQL lineage and duplicate-continuation certification. |
| [tests/test_configuration_lineage_retry.py](<C:/Users/Ivica/Documents/SwingLens/tests/test_configuration_lineage_retry.py>) | Configuration-scope and retry-classification regressions. |
| [tests/ops/test_lifecycle_powershell.py](<C:/Users/Ivica/Documents/SwingLens/tests/ops/test_lifecycle_powershell.py>) | Isolate the previously unsafe core-stop cleanup fixture. |
| [tests/ops/test_lifecycle_safety.py](<C:/Users/Ivica/Documents/SwingLens/tests/ops/test_lifecycle_safety.py>) | Mock physical evidence for the synthetic gone-process fixture. |


New T14A lifecycle code/tests:

| File | Why |
|---|---|
| [app/services/runtime_identity_recovery.py](<C:/Users/Ivica/Documents/SwingLens/app/services/runtime_identity_recovery.py>) | Pure, reusable strong-identity proof and atomic create-only metadata publication. |
| [scripts/ops/lifecycle_probe.py](<C:/Users/Ivica/Documents/SwingLens/scripts/ops/lifecycle_probe.py>) | Read-only evidence collection, two-observation recovery, and shutdown-only generation compatibility. |
| [scripts/ops/SwingLensLifecycle.psm1](<C:/Users/Ivica/Documents/SwingLens/scripts/ops/SwingLensLifecycle.psm1>) | Locked recover-identity route; request shutdown-only compatibility explicitly from normal stop. |
| [swinglens.ps1](<C:/Users/Ivica/Documents/SwingLens/swinglens.ps1>) | Expose the explicit recover-identity action through the canonical launcher. |
| [tests/ops/test_runtime_identity_recovery.py](<C:/Users/Ivica/Documents/SwingLens/tests/ops/test_runtime_identity_recovery.py>) | 35 valid/negative, PID-reuse, race, no-overwrite, and shutdown-guard cases. |
| [tests/ops/test_lifecycle_powershell.py](<C:/Users/Ivica/Documents/SwingLens/tests/ops/test_lifecycle_powershell.py>) | Require temporary helper roots; certify path isolation and explicit shutdown flag. |
| [tests/ops/test_lifecycle_contracts.py](<C:/Users/Ivica/Documents/SwingLens/tests/ops/test_lifecycle_contracts.py>) | Update canonical-action assertion for the supported recovery command. |
| [tests/ops/test_lifecycle_safety.py](<C:/Users/Ivica/Documents/SwingLens/tests/ops/test_lifecycle_safety.py>) | Adapt mocked validation to the explicit shutdown-only keyword. |


Previously certified verification artifacts, separately preserved without editing their contents (each is prior-task certification evidence):

- [docs/verification/run161-sec-continuation/alembic-check.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/alembic-check.txt>)
- [docs/verification/run161-sec-continuation/baseline-winner-checks.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/baseline-winner-checks.txt>)
- [docs/verification/run161-sec-continuation/baseline.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/baseline.json>)
- [docs/verification/run161-sec-continuation/broad-failure-recheck.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/broad-failure-recheck.txt>)
- [docs/verification/run161-sec-continuation/check_incident.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/check_incident.py>)
- [docs/verification/run161-sec-continuation/final-incident.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/final-incident.json>)
- [docs/verification/run161-sec-continuation/focused-certification.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/focused-certification.txt>)
- [docs/verification/run161-sec-continuation/focused-certified.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/focused-certified.txt>)
- [docs/verification/run161-sec-continuation/focused-final.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/focused-final.txt>)
- [docs/verification/run161-sec-continuation/focused-initial.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/focused-initial.txt>)
- [docs/verification/run161-sec-continuation/incident-certified.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/incident-certified.txt>)
- [docs/verification/run161-sec-continuation/incident-final.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/incident-final.txt>)
- [docs/verification/run161-sec-continuation/integrity-after.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/integrity-after.json>)
- [docs/verification/run161-sec-continuation/integrity-after.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/integrity-after.txt>)
- [docs/verification/run161-sec-continuation/integrity-before.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/integrity-before.json>)
- [docs/verification/run161-sec-continuation/integrity-predeploy.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/integrity-predeploy.json>)
- [docs/verification/run161-sec-continuation/integrity-predeploy.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/integrity-predeploy.txt>)
- [docs/verification/run161-sec-continuation/lifecycle-certified.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/lifecycle-certified.txt>)
- [docs/verification/run161-sec-continuation/postgres-certification.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/postgres-certification.txt>)
- [docs/verification/run161-sec-continuation/postgres-final.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/postgres-final.txt>)
- [docs/verification/run161-sec-continuation/postgres-initial.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/postgres-initial.txt>)
- [docs/verification/run161-sec-continuation/pre-recovery.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/pre-recovery.json>)
- [docs/verification/run161-sec-continuation/preflight-certified.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/preflight-certified.txt>)
- [docs/verification/run161-sec-continuation/process-pool-certified.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/process-pool-certified.txt>)
- [docs/verification/run161-sec-continuation/report.md](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/report.md>)
- [docs/verification/run161-sec-continuation/restart-certified.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/restart-certified.txt>)
- [docs/verification/run161-sec-continuation/run_baseline_checks.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/run_baseline_checks.py>)
- [docs/verification/run161-sec-continuation/run_tests.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/run_tests.py>)
- [docs/verification/run161-sec-continuation/runtime-registrations.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/runtime-registrations.json>)
- [docs/verification/run161-sec-continuation/runtime-stop.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/runtime-stop.json>)
- [docs/verification/run161-sec-continuation/sec-evidence.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/sec-evidence.json>)
- [docs/verification/run161-sec-continuation/snapshot_integrity.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/snapshot_integrity.py>)
- [docs/verification/run161-sec-continuation/unit-suite.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/run161-sec-continuation/unit-suite.txt>)

New T14A verification files:

| T14A verification file | Purpose |
|---|---|
| [docs/verification/t14a-runtime-identity/staged-diff-check.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/staged-diff-check.txt>) | Staged whitespace check; excludes only the immutable original unified-diff capture. |
| [docs/verification/t14a-runtime-identity/affected-tests.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/affected-tests.txt>) | Corrected PowerShell/contract lane: 53 passed. |
| [docs/verification/t14a-runtime-identity/baseline.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/baseline.json>) | Forensic pre-edit Git/OS/listener/health/database baseline and missing metadata proof. |
| [docs/verification/t14a-runtime-identity/before-recovery-runtime.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/before-recovery-runtime.json>) | Repeated independent live identity/DB/log observation before publication. |
| [docs/verification/t14a-runtime-identity/diff-check.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/diff-check.txt>) | Precommit whitespace check output; no errors. |
| [docs/verification/t14a-runtime-identity/final-python-tests.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/final-python-tests.txt>) | Final complementary Python lane: 149 passed. |
| [docs/verification/t14a-runtime-identity/final_database_check.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/final_database_check.py>) | Read-only global heartbeat/lease and new-row checks. |
| [docs/verification/t14a-runtime-identity/focused-tests.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/focused-tests.txt>) | Initial 198-pass/one-outdated-assertion result retained transparently. |
| [docs/verification/t14a-runtime-identity/incident-after.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/incident-after.json>) | Read-only post-stop incident verification. |
| [docs/verification/t14a-runtime-identity/incident-before.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/incident-before.json>) | Read-only incident starting state. |
| [docs/verification/t14a-runtime-identity/incident-pre-shutdown.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/incident-pre-shutdown.json>) | Read-only incident check before recovery/shutdown. |
| [docs/verification/t14a-runtime-identity/integrity-after.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/integrity-after.json>) | Post-stop historical hashes. |
| [docs/verification/t14a-runtime-identity/integrity-before.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/integrity-before.json>) | Starting historical evidence hashes/ID ceilings. |
| [docs/verification/t14a-runtime-identity/integrity-pre-shutdown.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/integrity-pre-shutdown.json>) | Historical hash check before shutdown. |
| [docs/verification/t14a-runtime-identity/invariants-after.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/invariants-after.json>) | Every incident/historical comparison passed. |
| [docs/verification/t14a-runtime-identity/invariants-pre-shutdown.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/invariants-pre-shutdown.json>) | All pre-shutdown comparisons passed. |
| [docs/verification/t14a-runtime-identity/lifecycle-operation-events.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/lifecycle-operation-events.json>) | Recovery/stop journal evidence and matching supervisor shutdown request. |
| [docs/verification/t14a-runtime-identity/live-proof.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/live-proof.json>) | Initial pure identity proof and proposed state; metadata still missing. |
| [docs/verification/t14a-runtime-identity/observe_runtime.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/observe_runtime.py>) | Reusable read-only runtime/DB/log observation helper. |
| [docs/verification/t14a-runtime-identity/post-stop-database-check.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/post-stop-database-check.json>) | No fresh registrations/leases; stable stopped heartbeats and all ID ceilings unchanged. |
| [docs/verification/t14a-runtime-identity/powershell-syntax.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/powershell-syntax.txt>) | PowerShell AST parser pass. |
| [docs/verification/t14a-runtime-identity/pre-shutdown-runtime.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/pre-shutdown-runtime.json>) | Verified recovered ownership and zero work immediately before canonical stop. |
| [docs/verification/t14a-runtime-identity/recovered-state.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/recovered-state.json>) | Copy of the state actually published by reviewed recovery. |
| [docs/verification/t14a-runtime-identity/recovery-exit.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/recovery-exit.txt>) | Recovery exit 0. |
| [docs/verification/t14a-runtime-identity/recovery-operation.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/recovery-operation.txt>) | Canonical recovery success output. |
| [docs/verification/t14a-runtime-identity/recovery-tests.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/recovery-tests.txt>) | Standalone 35-case recovery validation. |
| [docs/verification/t14a-runtime-identity/report.md](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/report.md>) | This T14A report. |
| [docs/verification/t14a-runtime-identity/ruff.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/ruff.txt>) | Ruff check and format results. |
| [docs/verification/t14a-runtime-identity/runtime-after.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/runtime-after.json>) | Independent six-PID absence, empty roles/ports, metadata cleanup and retired registrations. |
| [docs/verification/t14a-runtime-identity/starting.diff](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/starting.diff>) | Full pre-edit tracked diff of the previously certified changes. |
| [docs/verification/t14a-runtime-identity/status-after.json](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/status-after.json>) | Independent canonical status: STOPPED, runtimeActive false, metadata MISSING. |
| [docs/verification/t14a-runtime-identity/status-exit.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/status-exit.txt>) | Status exit 0. |
| [docs/verification/t14a-runtime-identity/stop-exit.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/stop-exit.txt>) | Stop exit 0. |
| [docs/verification/t14a-runtime-identity/stop-operation.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/stop-operation.txt>) | Canonical stop transcript, claim fence/acknowledgement and STOPPED result. |
| [docs/verification/t14a-runtime-identity/test-path-audit.txt](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/test-path-audit.txt>) | Focused destructive/default path search results. |
| [docs/verification/t14a-runtime-identity/test-path-isolation.md](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/test-path-isolation.md>) | Audit scope and isolation findings. |
| [docs/verification/t14a-runtime-identity/verify_invariants.py](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/verify_invariants.py>) | Compare starting/final read-only incident and historical hashes. |
| [docs/verification/t14a-runtime-identity/verify_syntax.ps1](<C:/Users/Ivica/Documents/SwingLens/docs/verification/t14a-runtime-identity/verify_syntax.ps1>) | Reproducible source-only PowerShell syntax validation. |


Operational changes were limited to reviewed lifecycle-state reconstruction followed by canonical cleanup, normal claim fencing/quiescence, worker/supervisor registration retirement, shutdown request/recorder and logs. Financial/configuration evidence was not changed.

# 8. TESTS

Commands ran from `C:\Users\Ivica\Documents\SwingLens` against isolated fixtures:

```powershell
.venv\Scripts\python.exe -m pytest tests/ops/test_runtime_identity_recovery.py -q --tb=short
```

35 passed. Covers valid ownership restoration, foreign listener/command/executable/repository, PID reuse, supervisor/worker/launcher/watchdog/registry/generation mismatch, missing/stale evidence, nonzero work, existing valid/corrupt/empty files, observation change, publication race and shutdown-only compatibility after strong identity checks.

```powershell
.venv\Scripts\python.exe -m pytest tests/ops/test_lifecycle_powershell.py tests/ops/test_lifecycle_contracts.py -q --tb=short
```

53 passed (219.40 seconds), including temporary default-path and shutdown flag regressions. The initial combined lane had 198 passes and one obsolete five-action assertion. That assertion was updated for recover-identity; the corrected final lane passed. An initial command using nonexistent test filenames collected no tests; no production actions occurred.

```powershell
.venv\Scripts\python.exe -m pytest tests/ops/test_runtime_identity_recovery.py tests/ops/test_lifecycle_safety.py tests/ops/test_lifecycle_stopped_generation.py tests/ops/test_lifecycle_convergence.py tests/ops/test_lifecycle_config.py tests/ops/test_lifecycle_observability.py tests/test_worker_supervisor_reliability.py tests/test_worker_cli.py tests/test_process_role_topology.py tests/ops/test_worker_quiesce.py tests/test_background_worker.py tests/test_app_lifespan_worker.py -q --tb=short
```

149 passed (7.32 seconds). Together with the 53 PowerShell/contract cases, all 202 final focused cases pass. The sole emitted warning is the existing Starlette TestClient/httpx deprecation.

```powershell
.venv\Scripts\ruff.exe check app/services/runtime_identity_recovery.py scripts/ops/lifecycle_probe.py tests/ops/test_runtime_identity_recovery.py tests/ops/test_lifecycle_powershell.py tests/ops/test_lifecycle_safety.py tests/ops/test_lifecycle_contracts.py
.venv\Scripts\ruff.exe format --check app/services/runtime_identity_recovery.py scripts/ops/lifecycle_probe.py tests/ops/test_runtime_identity_recovery.py tests/ops/test_lifecycle_powershell.py tests/ops/test_lifecycle_safety.py tests/ops/test_lifecycle_contracts.py
git diff --check
git diff --cached --check -- . ':(exclude)docs/verification/t14a-runtime-identity/starting.diff'
pwsh -NoProfile -File docs/verification/t14a-runtime-identity/verify_syntax.ps1
```

Ruff: all checks passed; six Python files formatted. Source diff check: pass (only Git line-ending conversion advisories). The staged verification check excludes only the immutable starting.diff: unified-diff blank context lines necessarily contain their leading context space, which a check of the patch as a new text file flags as trailing whitespace. The original forensic patch is preserved byte-for-byte; all other staged files pass. PowerShell AST parser: pass for the launcher and lifecycle module. No live lifecycle E2E was executed.

Read-only operational verification commands:

```powershell
.venv\Scripts\python.exe docs/verification/t14a-runtime-identity/observe_runtime.py before before-recovery-runtime.json
.venv\Scripts\python.exe docs/verification/t14a-runtime-identity/observe_runtime.py before pre-shutdown-runtime.json
.venv\Scripts\python.exe docs/verification/t14a-runtime-identity/observe_runtime.py after runtime-after.json
.venv\Scripts\python.exe docs/verification/t14a-runtime-identity/verify_invariants.py pre-shutdown
.venv\Scripts\python.exe docs/verification/t14a-runtime-identity/verify_invariants.py after
.venv\Scripts\python.exe docs/verification/t14a-runtime-identity/final_database_check.py
pwsh -NoProfile -File .\swinglens.ps1 status -Json
```

All passed. Original read-only check_incident.py and snapshot_integrity.py produced uniquely named T14A outputs subsequently moved into this directory; prior certification files were not overwritten.

# 9. NORMAL SHUTDOWN

Only shutdown command executed:

```powershell
pwsh -NoProfile -File .\swinglens.ps1 stop -Json
```

Operation `3143abea-e265-4804-b1b0-8af47c065810`; exit 0; successful operation-complete journal event; duration 104,452 ms. Before stop: three roles strongly verified, no active jobs/running pipelines/new jobs, no continuation, original incident unchanged.

Normal worker claim fencing established and acknowledged: request 11:55:38.401010+02:00; acknowledgement 11:55:40.108649+02:00; acknowledgement latency 1.707639 seconds; safeToStop=true, workerIdentityValid=true, activeCount=0. Supervisor PID 15440 accepted the instance-scoped request for this operation at 11:55:56+02:00 and logged successful shutdown at 11:56:12+02:00.

Independent verification: web 8408, supervisor 15440, worker 4000 and launchers 2880/6972/13124 all absent; no canonical roles remain; no listeners on 8000/9101/9102. Lifecycle file removed by canonical cleanup. Worker/supervisor stopping timestamps are 11:56:12.255465+02:00 and 11:56:12.274465+02:00; final heartbeats equal their retirement timestamps and did not advance at the later check. Globally: zero fresh nonstopping worker/supervisor registrations and zero fresh job leases. No half-stopped generation remains.

A separate status -Json command returned exit 0, overall STOPPED, runtimeActive=false, runtimeClassification=MISSING. Existing stop emits textual status despite -Json; its transcript is preserved and the separate status command supplies JSON. PostgreSQL remains up under managementEnabled=false. Optional observability-stop returned Docker exit 1; Prometheus/Grafana unavailable. No forced or arbitrary process termination was attempted outside canonical shutdown.

# 10. RUN 161 INTEGRITY

Upload 161: COMPLETED, 86 rows. Raw Pipeline 151: PREPARING / VALIDATING_RUN; validation step PENDING. SEC readiness 86/86. Original FULL_PIPELINE 43268: COMPLETED. SEC_READINESS_REPAIR 43269: FAILED, no execution token. Continuation count: 0. No Resume/continuation/pipeline start or SEC rerun occurred.

C1 anchor: `c3a654bf956deacdda507957a963743c15162eb6b6955004a377ba78c393e1f2`. Fingerprint: `7bc8ee0c7b8ac8c44a770023afc0cd41155d2ca7f5b4afd555044319be30f77d`. Original pipeline/job binding authority remains unchanged; the failed helper remains unbound as originally recorded. Market context 13 unchanged, session 2026-09-16, original cutoff 2026-09-16T23:20:46.979906Z. Raw incident fields, steps, job payloads/configuration references and full context compare equal to the starting observation. The fixed application's read-only BLOCKED projection does not alter raw PREPARING.

All historical hash comparisons pass: 49,217 SEC documents; 74,418 extractions; 1,081 SEC sync states; 439 repaired source records/four repaired ingestions; one handoff manifest; 1,895 winner manifests; 51 frozen configuration records; three original bindings; original anchor and incident bindings; zero historical core-calculation rows. Maximum IDs across evidence/jobs/pipelines also match baseline, proving no additional rows beyond the historical ceilings. Jobs after 43269: zero. Only operational lifecycle state changed.

# 11. REMAINING RISKS

Optional Docker observability stop returned exit 1; Prometheus/Grafana are unavailable. This task did not broaden into Docker remediation; core shutdown and T14B deployment prerequisites are proven. PostgreSQL intentionally remains running.

Run 161 is still unrecovered by design. The newly committed application has not been started or live-smoke-tested; deployment and controlled continuation remain T14B work. Recovery requires readable OS identity environments, the proven supervisor topology, fresh registries/recorder, and supported atomic create-only publication; unavailable evidence fails closed. Existing TestClient/httpx deprecation and stopped status's raw core/application failed sentinels are unrelated to process shutdown (overall STOPPED).

# 12. GIT STATE

Branch: `codex/t13e-phase4-config-certification`. Starting SHA: `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8`. Run-161 remediation: `25cc0a3bc546ad51728d6a224fcac2141b20a0bc`. Prior evidence: `7c4955dae3adbf152491bfc489f0d77e2e068406`. T14A lifecycle code: `4683a6cee21c3282e96a743c0fdb0df56afabb0f` (tree `7c560576a14e1cdd20811158fbb61dae996ffccb`). At report assembly, tracked code is clean and only this new verification directory is untracked. This directory is subsequently committed separately; final HEAD/tree and clean worktree status are recorded in the final delivery response, avoiding an impossible self-referential commit hash inside its own committed report. No branch switch/reset or unrelated worktree change occurred. SwingLens remains STOPPED.
