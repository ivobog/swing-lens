# MSI operational compatibility release candidate

This report certifies release code and migration compatibility only after the gates below pass. Operational deployment and live-data readiness remain unauthorized and uncertified. No operational migration, startup, provider acquisition, backlog recovery or MSI history capture was performed.

## Identity and evidence

- Parent: `dc4224856a160bf7e0fe3866a356cad3bafa31fc`.
- Branch: `feature/msi-operational-compatibility`.
- Worktree: `C:/Users/Ivica/.codex/worktrees/msi-operational-compatibility/SwingLens`.
- Fresh external evidence: `C:/Users/Ivica/Documents/SwingLens-Audits/20261023_135210_CEST_MSI_Operational_Compatibility`.
- Readiness source: `20261023_130717_CEST_MSI_Operational_Readiness`; retained certification source: `20261023_123103_CEST_MSI_Baseline_Final_Certification`. Both remain immutable.

The operational PostgreSQL instance is local PostgreSQL 18.3, database `swinglens`, at installed revision `0092_phase2_eligibility_ledgers`. No application web/supervisor/worker runtime was active at preflight. The original dirty checkout is not assumed to equal selected main or the certified MSI integration.

## Migration arrangement

Before: certified integration `0091_readiness_constraints -> 0093_market_sector_rotation`; operational database `0091_readiness_constraints -> 0092_phase2_eligibility_ledgers`. The integration could not resolve installed 0092.

After: `0091_readiness_constraints -> 0092_phase2_eligibility_ledgers -> 0093_market_sector_rotation`, with exactly one head, `0093_market_sector_rotation`.

The original installed 0092 migration is restored with its revision identity and executable content unchanged. Only 0093's parent changes. All 0093 function ASTs, MSI tables, immutability triggers and uniqueness constraints remain unchanged. A linear ancestry is preferable to a merge here: 0092 is installed and has 1,876 consumer decisions and 874 Winner acquisition decisions; 0093 has never been operationally applied. An additional merge would add a second branch and revision without resolving a deployed two-head history. Existing immutable certified commits remain unchanged.

0092 also exists in committed history at `fbe0a55952fcb382eaf71a560820fcf3507bc7c2` (Phase 2.9 remediation). Local original/D1/D2/D3/D4/review-remediation/vision-completion histories contain that commit; the retained D2 report explicitly records 0092 as its parent. Its identity therefore cannot be replaced with an invented revision or stamped away. No historical branch is rewritten here.

Disposable verification reproduced the certified graph's exact `Can't locate revision identified by '0092_phase2_eligibility_ledgers'` failure. It restored the authorized operational schema export plus 33 actual records (4,255,689 serialized bytes, 15 tables, including two consumer decisions and two Winner decisions). All 36 copied physical foreign-key relationships were validated. The graph upgraded successfully; all existing 129 tables, 2,459 columns, 2,051 constraints, 588 indexes, 22 noninternal triggers and all 33 copied record values survived unchanged. Only the two MSI rotation tables were added. An injected mid-migration failure rolled back both schema and version, and retry and repeated head upgrade passed. Application schema-head enforcement agrees with Alembic. Database and ORM immutability and MSI idempotent capture are verified separately.

This is a bounded representative physical-FK closure, not a full operational restore or a complete closure of JSON historical evidence. A validated full backup and restore rehearsal remain activation prerequisites. Operational ledgers and version table were never stamped, downgraded or rewritten.

## Policy reconciliation

| Contract | Original / integrated difference | Candidate decision |
| --- | --- | --- |
| Fundamental admission | Original defaults ACTIVE with v3 bounded score admission; integration omitted it | Restore ACTIVE, LEGACY and certification-only SHADOW. Keep `fundamentals_v2.1`, 79-field universe, 65% total / 50% component bounds, exact warning classification, finite values and frozen source/cutoff validation. |
| Consumer decisions | Installed 0092 append-only records lacked target runtime writers | Restore typed append-only DTOs/models and fingerprint idempotency. Publish Combined/Ranking decisions inside main's existing bounded evidence transaction, never through the old unbounded refresh implementation. |
| Configuration authority | Main freezes readiness families and exact source authority | Bind non-LEGACY mode and admission policy versions into the same readiness family, include mode in cache identity, and reject mode drift before persistence. LEGACY preserves main's exact serialized readiness family. |
| Warning taxonomy | Original supported classified advisory warnings | Adapt only for Fundamental, Combined and Ranking producers. Technical and other main producer readiness remain unchanged; historical readiness remains frozen. |
| Winner | Original recorded acquisition outcomes; main has newer bounded read release and calendar authority | Add INSERTED/DUPLICATE/EXCLUDED/FAILED ledger outcomes at existing native transaction boundaries. Preserve main's read-transaction release, sealed prediction evidence, source authority and calendar checks. |
| CERI | Original opportunity actionability checks; main explicitly permits accepted risk alerts without rated opportunity | Restore opportunity confidence/provenance checks and freeze their policy version in alert configuration. Keep main's independent accepted-risk/comparable-transition rule, native source sealing and notification authority. Low-confidence research visibility remains available. No CERI capture or UI implementation is copied. |
| Worker memory | Original 2048 MiB post-job recycle setting absent | Restore a soft idle-boundary recycle. Sample private bytes (RSS fallback), check exact worker instance has no RUNNING/RECOVERING job, exit normally, and let supervisor replace the matching stopped child without recording a crash. Retain main's 4096/6144 MiB warning/critical policies. |
| Backlog isolation | Main CERTIFICATION blocks unrelated claims/recovery, but preclaim reconciliation was global | Scope dependency/continuation/cancellation reconciliation to explicitly authorized roots of the current certification epoch. An empty root set performs no pipeline reconciliation. NORMAL behavior remains unchanged. |

Operational v3 decisions contain 883 INCLUDED and 55 UNDECIDED records for each Fundamental consumer edge. Existing 0092 rows are read unchanged. Fresh ACTIVE publication, repeat, rollback, SHADOW, LEGACY, unloaded ORM evidence, wrong-kind/cross-run and exact-source rejection are verified in disposable PostgreSQL. No old v1 context is relabeled or granted current execution authority.

The CERI alert snapshot resolver and executable-policy guard share the versioned opportunity policy. Older alert snapshots still decode for history; they fail execution under the changed code policy. This preserves main's distinction between historical inspection and current execution authority.

Main's Technical V4/V5 engines, Technical publication, pipeline executor, core evidence writer, Setup lifecycle and MSI source/configuration/presentation remain unchanged. Selected dedicated original modules and historical test fixtures are copied only after contract review; unused `eligibility_shadow.py` and old pipeline implementations are excluded. Source hashes and AST comparisons are retained externally.

## Backlog and future containment procedure

`BACKLOG_DISPOSITION_MATRIX.csv` in the external evidence directory contains all 1,629 jobs and the requested job, run, status, lease, authority, provider-risk and disposition columns. SHA-256: `3d99c18307896151c5126adf6eceb7e9ccbc643f464640ae912c7145b29e10b3`.

There are 49 QUEUED and one stale RUNNING job. Job 182 is a provider-ingest job for run 9 with an expired October 1 lease; expiration grants no replay permission. Queued FULL_PIPELINE job 1384 belongs to failed run 40 and remains quarantined. The other 49 active records require a newly authorized current calendar/context before replacement. All 81 stored calculation contexts remain calendar v1. Terminal/failed/blocked histories remain historical. No existing queued job is asserted to be valid future work merely because it is queued.

The following procedure is for a separately approved future activation; none of these commands were run in this task:

1. Keep the application stopped; verify no external worker or scheduler uses the database. Approve and verify a full database backup (including roles/configuration) and recovery point. Retain the current schema/code/configuration manifests and the backlog matrix.
2. Review the sanitized `config/msi_operational_compatibility.env.example` against secure local settings. Supply secrets outside Git. Keep CERTIFICATION, automatic Winner maturation/cohort refresh/prewarm and IB auto-launch disabled. Do not reuse an earlier runtime instance or create certification root jobs.
3. After explicit migration approval and approved dependency/runtime preparation, run `uv run python scripts/ops/lifecycle_probe.py migrate` from the candidate checkout. This supported lifecycle subcommand verifies PostgreSQL provenance before guarded `alembic upgrade head`; it does not authorize provider work. Never stamp or run MSI DDL outside Alembic. Confirm the installed single head and strict application guard before any web or worker activation.
4. After explicit runtime approval, from the candidate checkout invoke the supported entry point: `pwsh -NoProfile -File .\swinglens.ps1 start -RuntimeMode CERTIFICATION`. The canonical launcher creates a fresh runtime epoch and owns the supervisor/web/durable-worker topology. This command can migrate and write control-plane state; it is not a read-only readiness command. Do not run it before steps 1Ã¢â‚¬â€œ3 are approved. There is no invented launcher `--job-types` flag.
5. Verify the fresh epoch has no authorized root, no legacy job claim/recovery, no new provider work, and no old pipeline reconciliation. A disposable two-poll native-worker test retains 49 queued jobs, one stale running job, v1 context and cancellation-requested historical pipeline values exactly; a separate test proves another epoch cannot confer reconciliation scope.
6. Keep containment until every active backlog row has an approved disposition. Any cancel/replan/replacement, new v2 calculation context, provider access, scheduling or MSI capture requires separate authorization. Do not switch to NORMAL with the old backlog untreated.

The original configuration's ACTIVE admission, 2 Technical worker processes / 4 in-flight tasks, ACTIVE artifact cache, Setup/Winner/CERI capabilities and 2048/4096/6144 MiB memory settings are preserved in the template. Its CERTIFICATION/automatic-off settings deliberately differ for containment. Original IB configuration refers to 1048; observed running installation is 1051. Both path existence and version intent must be reviewed before any future auto-launch. No dependencies were installed; retained preflight evidence found the 15 direct dependencies matched the installed lock. Database pool defaults remain 5 + 10 overflow per process, not a global connection cap.

## Verification and resource limits

Final gate results, distinct test identities, resolved failures and changed-file manifest are filled below after certification completes. Fresh JUnit, command manifests, complete diffs and comparison JSON are retained in the external directory. Earlier browser evidence is retained, not counted as a fresh run: MSI UI and its API contracts are unchanged.

The global architecture scan remains FAIL with the same baseline three findings: unregistered writer candidates `ib_fetch_executor._admit_returned_bars` and `pipeline_outcome._integrity_hazards`, plus recovery census drift. The exact unregistered recovery functions remain `_memory_recovery_identity` and `_record_progress_recovery` in `background_job_service`. New ledger writers, tables and transaction sites are registered explicitly; no baseline finding is suppressed or repaired.

Tests use one pytest worker, bounded small native fixtures and disposable databases. Provider connections are fenced. Threshold tests simulate memory samples rather than allocating 2 GiB. Recycling is effective at a completed, committed idle job boundary; it is not a hard memory cap, a mid-job kill, or proof that a full-size workload fits. PostgreSQL service/shared memory, browser and production concurrency require separate capacity assessment. A full production-sized pipeline/restore was not run.

## Activation and rollback limits

Live-data readiness remains blocked: the eleven sector ETFs have no stored price bars; SPY/QQQ last bars were October 19 and stale at the October 22 cutoff; independent MSI rotation history is absent. Synthetic tests do not repair this. Provider permissions, genuine current daily/weekly coverage, point-in-time anchors, current calendar contexts and separately authorized historical capture/scheduling remain prerequisites.

Before activation, approve a maintenance window, full backup/restore rehearsal, reviewed secure configuration, backlog disposition, resource headroom and provider/capture permissions. Stop any future runtime through its canonical instance-scoped lifecycle before rollback. Do not roll back to certified dc422 against installed 0092: its graph lacks 0092. Do not casually downgrade 0093 after MSI captures; downgrade drops the new immutable history tables. Prefer a reviewed forward fix or restore the coordinated pre-activation database/code/configuration backup with a known 0092-compatible release. Reconcile any post-backup business writes before restore. No rollback action was performed here.

## Final certification record

**MSI OPERATIONAL COMPATIBILITY: PASS** — release code and migration compatibility certified. Operational deployment and live-data readiness are not authorized or certified.

There are **2,077 distinct fresh test identities**, all PASS on their latest run, zero skips and zero unresolved failures. Repeats, parameterized case identities and ACTIVE/LEGACY profiles are reconciled by `classname::name`; multiple profiles do not inflate the distinct count. The representative schema/data verification is a separate PASS, not an extra pytest identity.

| Fresh run | Passed | Failed in that run | Disposition |
| --- | ---: | ---: | --- |
| `candidate-main-msi-regressions` | 1644 | 2 | Resolved by the final focused runs |
| `candidate-authority-postgresql` | 111 | 10 | Resolved by the final focused runs |
| `compatibility-final-postgresql` | 28 | 0 | PASS |
| `final-policy-technical-security` | 429 | 0 | PASS |
| `staged-ceri-predecessor-final` | 37 | 0 | PASS |

The audit retains all test attempts and the **24 identities that failed before correction** in `all-test-attempts.json` and `resolved-and-unresolved-test-failures.json`. Earlier failures included an incomplete new fixture, an import alias, ORM assertion/error-message assumptions from the older source, datetime canonicalization in new permission payloads, Technical advisory scope, the accepted-risk CERI regression, the expected 0093 parent and the CERI resolver/executable-policy mismatch. Corrections preserve main's exact-source rejection and authority checks; assertions were not removed to admit invalid evidence. The final PostgreSQL group repeats the affected configuration-delivery modules, the CERI feature boundary, the new operational tests and the existing MSI migration/concurrency test. The final unit group covers the restored policies, worker shutdown markers, Technical V4/V5/calendars/cache, Market Regime and security boundaries. Staged review additionally preserved main's predecessor-snapshot requirement for both opportunity transition types; the final 37-case CERI group verifies that guard, existing alert behavior and two native configuration/publication boundaries.

Ruff PASS on all **29** changed/new Python files; syntax PASS without bytecode writes; `git diff --check` PASS; Alembic PASS with exactly one expected head. The migration functions and installed 0092 identity/content comparison pass. Secret-value checks on the 35 reviewed files pass; fixture connection userinfo in failure evidence is redacted while preserving XML identities, results and timestamps.

Architecture **GLOBAL FAIL: 3 unchanged baseline findings**, zero additions. The complete finding text and the exact recovery caller/candidate census differences are identical to the certified baseline. This is an unchanged-baseline comparison gate, not a clean global pass. No suppression or unrelated remediation was introduced.

Peak sampled test-process-tree RSS across fresh attempts: **466,284,544 bytes (444.68 MiB)**. One pytest worker; native race tests include bounded thread concurrency. Final 429-case unit group sampled **131.25 process-tree CPU seconds** over **141.04 wall seconds**, with minimum observed host available memory **3,902,705,664 bytes (3.63 GiB)**. Host evidence: about 16 GiB RAM, 2 physical / 4 logical CPUs. Maximum sampled simultaneous disposable-database connections: **2**, plus fixture-admin and read-only monitor connections. One-second connection sampling can miss short-lived connections and is not a server-wide connection cap. RSS excludes PostgreSQL service/shared memory and browsers; these bounded measurements do not certify production throughput or memory demand.

The 2048 MiB recycle path is verified using simulated samples without allocating 2 GiB: no second claim, no active exact-instance job, normal loop unwind, matching stopping registration and supervisor recognition. It measures the worker process, not all child-process memory; a long job or Technical children can exceed available host headroom before an idle boundary. Recheck capacity before activation.

Final read-only comparison: all inventoried fields of **1,629 jobs, 45 pipelines, 63 dependencies and 81 contexts**, ledger summaries and operational schema head are unchanged. Operational MSI tables remain absent. This is not a full physical database checksum. All current-task disposable databases were removed through their guarded test fixtures; the 42 remaining disposable-named databases have directory creation times predating this task and were left untouched.

Protection before local checkpoint: **23 other worktrees** retain identical HEAD/branch/status/index and recorded dirty-file hashes; all refs are unchanged, and **199 protected historical evidence files** retain identical hashes. The source comparison verifies 63 MSI/Setup/core contract files unchanged and all 0093 function ASTs identical. A post-commit receipt separately verifies that only the candidate branch ref moves, the commit parent is dc422, and the candidate index/worktree are clean.

The local checkpoint identity is recorded externally in `GIT_CHECKPOINT.json` after exactly one commit. No push, PR, merge, deployment, operational migration, NORMAL startup, provider request, operational history capture or backlog mutation is authorized by this PASS.

## Exact changed-file manifest

All paths are relative to `C:/Users/Ivica/.codex/worktrees/msi-operational-compatibility/SwingLens`. Exactly 35 intended files; audit logs, restored operational records, local databases and credentials remain outside Git. The two run JSON files are reviewed frozen regression fixtures already present in the original source, not a newly captured operational database export.

```text
alembic/versions/20261013_0092_phase2_eligibility_ledgers.py
alembic/versions/20261022_0093_market_sector_rotation.py
app/models/tables.py
app/services/background_worker.py
app/services/ceri/actionability.py
app/services/ceri/alert_service.py
app/services/combined_decision.py
app/services/consumer_eligibility_ledger.py
app/services/contextual_consumer_eligibility.py
app/services/decision_effective_configuration.py
app/services/eligibility_policy.py
app/services/fundamental_admission_policy.py
app/services/pipeline_dependency_service.py
app/services/producer_readiness.py
app/services/ranking_profile_service.py
app/services/warning_taxonomy.py
app/services/winner_probability/capture_service.py
app/settings.py
app/worker_supervisor.py
config/architecture_registry.json
config/msi_operational_compatibility.env.example
docs/audit/whole-application-closure/GENERATED_ARCHITECTURE_REGISTRY.md
docs/market_sector_intelligence/MSI_operational_compatibility_report.md
tests/fixtures/readiness_phase2/run_32.json
tests/fixtures/readiness_phase2/run_33.json
tests/integration/test_market_sector_intelligence_rotation_postgresql.py
tests/integration/test_msi_operational_compatibility_postgresql.py
tests/integration/test_operational_admission_postgresql.py
tests/operational_readiness_helpers.py
tests/test_ceri_actionability.py
tests/test_fundamental_admission_policy.py
tests/test_market_sector_intelligence_rotation_storage.py
tests/test_msi_operational_compatibility.py
tests/test_operational_fundamental_admission.py
tests/test_warning_taxonomy.py
```
