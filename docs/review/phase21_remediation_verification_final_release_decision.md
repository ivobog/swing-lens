# Phase 21 - Remediation Verification and Final Release Decision

Review date: 2026-08-02

## Scope

Phase 21 is the final review gate. It verifies fixes where available, reruns broad and specialized
checks, recalculates phase scorecards, documents residual risk, and makes separate release
recommendations for:

- core upload and fundamental scoring,
- IB market-data and technical scoring,
- combined ranking and contextual overlays,
- setup lifecycle,
- winner probability,
- CERI,
- administrative and destructive operations.

No remediation pull request or fix set was provided between the prior phase reviews and this final
gate. Therefore, this report treats previously documented high-severity findings as open unless the
Phase 21 verification directly disproved or closed them.

## Final Decision

Overall release recommendation: **No-Go**.

The automated Python regression suite is strong and currently green, but the release gate fails on
clean database migration, security/admin controls, point-in-time/evidence integrity risks, missing
backup/restore validation, and missing release governance. Continued use should remain limited to a
local research environment with known data, disabled high-risk optional subsystems where applicable,
and explicit operator awareness of the open findings.

## Verification Performed

| Check | Result | Evidence |
| --- | --- | --- |
| Lint | Passed | `uv run ruff check app tests` -> `All checks passed!` |
| Full automated suite | Passed | `uv run pytest -q` -> `943 passed, 1 warning in 43.91s` |
| Specialized regression slice | Passed | `uv run pytest tests/test_golden_pipeline.py tests/test_technical_score_v4.py tests/test_pipeline_service.py tests/test_pipeline_executor.py tests/test_background_job_service.py tests/test_background_worker.py tests/test_hardening.py tests/setup_lifecycle tests/winner_probability tests/ceri -q` -> `459 passed, 1 warning in 30.78s` |
| Current configured DB revision | Passed | `uv run alembic current` -> `0021_add_ceri_earnings_consensus_reason (head)` |
| Alembic heads | Passed | `uv run alembic heads` -> single head `0021_add_ceri_earnings_consensus_reason (head)` |
| Clean PostgreSQL migration | Failed | Disposable `swinglens_phase21_clean` database failed at migration `0019_add_ceri_ingestion_audit_fields` with duplicate `ceri_ingestion_runs.retry_count` |
| Backup/restore verification | Not run | No backup/restore scripts or restore validation runbook exist; Phase 19 found this missing |
| Browser/end-to-end smoke | Not run | No Playwright/browser smoke suite exists; Phase 18 found this missing |
| Security/static/dependency CI gates | Not run | No committed CI/security gates exist; Phase 15/18/20 found this missing |
| Remediation PR review | Not applicable | No remediation changes were present to inspect |

The recurring warning is the existing Starlette/httpx `TestClient` deprecation warning.

## Clean Migration Reproduction

Commands executed:

```powershell
uv run python -c "import psycopg; conn=psycopg.connect('postgresql://postgres:postgres@127.0.0.1:5432/postgres', autocommit=True); conn.execute('DROP DATABASE IF EXISTS swinglens_phase21_clean'); conn.execute('CREATE DATABASE swinglens_phase21_clean'); conn.close(); print('created swinglens_phase21_clean')"

$env:DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/swinglens_phase21_clean'
uv run alembic upgrade head

uv run python -c "import psycopg; conn=psycopg.connect('postgresql://postgres:postgres@127.0.0.1:5432/postgres', autocommit=True); conn.execute('DROP DATABASE IF EXISTS swinglens_phase21_clean WITH (FORCE)'); conn.close(); print('dropped swinglens_phase21_clean')"
```

Observed failure:

```text
psycopg.errors.DuplicateColumn: column "retry_count" of relation "ceri_ingestion_runs" already exists
[SQL: ALTER TABLE ceri_ingestion_runs ADD COLUMN retry_count INTEGER DEFAULT '0' NOT NULL]
```

This confirms the Phase 4 clean-migration blocker remains open. The current configured database is
already at head, but a fresh install, disaster recovery into a clean database, or CI migration smoke
would fail.

## Verified Remediation Register

| Item | Status | Notes |
| --- | --- | --- |
| Remediation changes reviewed | None | No fix branch/PR/change set was provided after the phase reviews. |
| S0/S1 finding reproduction | Partial | Clean migration blocker was reproduced. Other S0/S1 items remain supported by earlier phase evidence but were not all re-executed individually in Phase 21. |
| Automated regression health | Verified green | Full suite and specialized slice passed. This is a baseline health signal, not remediation closure. |
| Migration remediation | Not closed | Clean PostgreSQL upgrade still fails. |
| Security remediation | Not closed | CSRF/local-admin/host policy findings remain open. |
| Backup/restore remediation | Not closed | No runbook or restore validation exists. |
| Governance remediation | Not closed | No CONTRIBUTING, CODEOWNERS, PR template, changelog, ADRs, or versioning policy exists. |

## Phase Scorecard

| Phase | Status | Release-gate assessment |
| --- | --- | --- |
| Phase 0 - Baseline | Amber/Red | Baseline established; Docker/psql/GitHub governance checks incomplete. |
| Phase 1 - Requirements traceability | Amber/Red | Requirements broadly trace, but admin controls and safety regression are incomplete. |
| Phase 2 - Architecture/modularity | Not verified | No Phase 2 artifact was found in `docs/review`; final gate is incomplete without it. |
| Phase 3 - Configuration/feature flags | Amber/Red | Advanced configs are strong; runtime settings and core config lineage are weak. |
| Phase 4 - Database/migrations/transactions | Red | Clean migration fails; live ORM imports in migrations remain a release blocker. |
| Phase 5 - CSV ingestion/export safety | Amber/Red | Useful protections exist, but raw evidence exactness, formula injection, duplicate tickers, and upload cleanup need work. |
| Phase 6 - IB market data | Amber/Red | Read-only posture is good; stale-threshold behavior, split/parity semantics, and provider edge cases remain open. |
| Phase 7 - Fundamental scoring | Amber/Red | v2 scoring is covered by tests, but label contracts, duplicate semantics, and model-change governance are incomplete. |
| Phase 8 - Technical/Pine parity | Amber/Red | Technical tests are broad; Pine frozen parity, backdated pivot semantics, split handling, and edge-case flags remain blockers for high reliance. |
| Phase 9 - Combined/ranking | Amber/Red | Deterministic layer exists; missing-data penalty, growth-trap sizing, reconstructability, and label semantics remain open. |
| Phase 10 - Market/sector overlays | Amber | Useful advisory overlays; future-context leakage and immutability issues limit release confidence. |
| Phase 11 - Setup lifecycle | Red | Snapshot mutability, future context, replay confirmation, and purge semantics block authoritative use. |
| Phase 12 - Winner probability | Amber/Red | Strong fixture/test base; model promotion and quantitative approval gates are too weak. |
| Phase 13 - CERI | Red | Correction lineage, score-history mode, purge semantics, and raw job payload redaction remain open. |
| Phase 14 - Background jobs | Amber/Red | Leases/fencing are strong in unit tests; duplicate enqueue and real PostgreSQL concurrency tests remain gaps. |
| Phase 15 - Security/local admin | Red | State-changing POST CSRF/local-admin boundary, static CERI CSRF token, host/debug policy, and public error redaction remain open. |
| Phase 16 - UX/accessibility | Amber | Advanced evidence views are good; chart accessibility, table semantics, async feedback, and evidence provenance need consistency. |
| Phase 17 - Performance/capacity | Amber/Red | Small/medium use likely works; large-run SQL/memory/export/query-budget risks remain. |
| Phase 18 - Test strategy/CI gates | Amber/Red | 943 tests pass; real PostgreSQL, browser, coverage, security, and migration gates are missing. |
| Phase 19 - Operations/backup/recovery | Red | Backup/restore validation, alerting, readiness depth, shared redaction, runbooks, and rollback procedures are missing. |
| Phase 20 - Docs/governance | Red | Setup docs drift, missing governance files, missing ADRs, and implicit versioning policy block maintainable release. |

## Residual-Risk Register

| Risk | Severity | Affected areas | Status | Proposed owner | Target | Monitoring/compensating control |
| --- | --- | --- | --- | --- | --- | --- |
| Clean install and disaster recovery fail because Alembic migration chain is broken | S0/S1 | Database, all subsystems | Not accepted | Backend/database owner | Before any release | Block release; add clean PostgreSQL migration CI |
| Historical migrations import live ORM metadata | S1 | Database, OWPE, SLSE, CERI | Not accepted | Backend/database owner | Before migration release | Static migration lint rejecting `from app.models` in revisions |
| State-changing POST routes lack central local-admin and CSRF protection | S0/S1 | Upload, pipeline, IB, admin routes | Not accepted | Security/app owner | Before shared/browser use | Block public/non-loopback binding; add route-map guard tests |
| Static/query-string CERI CSRF token | S0/S1 | CERI admin | Not accepted | Security/CERI owner | Before CERI admin use | Keep `CERI_ADMIN_ENABLED=false`; reject query-string token |
| Host/debug/public binding policy is not enforced | S0/S1 | Whole app | Not accepted | Security/app owner | Before any network exposure | Bind only to `127.0.0.1`; add TrustedHost/fail-fast validation |
| Backup/restore and restore integrity validation are missing | S0/S1 | Operations, database | Not accepted | Operations/database owner | Before release | Manual backup before risky changes; implement restore test |
| Point-in-time leakage/future context risks remain | S0 | Technical pivots, market/sector, SLSE, OWPE, CERI | Not accepted | Quant/evidence owner | Before research reliance on historical claims | Disable authoritative historical/model use; add cutoff tests |
| CERI purge execution is audit-only despite purge naming | S0/S1 | CERI provider/licensing | Not accepted | Product/CERI owner | Before provider data use | Keep live provider disabled; rename or implement purge semantics |
| Shared error redaction is incomplete | S0/S1 | Background jobs, readiness, provider errors | Not accepted | Security/operations owner | Before release | Avoid exposing raw support bundles; add shared redaction |
| Model/scoring version policy is implicit | S1 | Fundamental, technical, combined, OWPE, CERI | Not accepted | Quant governance owner | Before model changes | Require manual review of config/golden diffs |
| Real PostgreSQL concurrency/constraint coverage is thin | S1 | Jobs, pipelines, unique evidence | Not accepted | Backend/testing owner | Next remediation milestone | Add PostgreSQL integration marker/job |
| Browser/e2e smoke and accessibility automation missing | S2 | UI workflows | Not accepted | UX/testing owner | Before broad user use | Manual smoke per subsystem |
| Performance budgets are not enforced | S2 | Large runs, exports, OWPE, CERI | Not accepted | Performance/backend owner | Before large workloads | Keep runs small; avoid large exports |
| Repository governance files missing | S1/S2 | Maintainability/release process | Not accepted | Maintainer lead | Before shared contribution | Add CODEOWNERS, PR template, changelog, ADRs |

No residual risk has documented accountable-owner acceptance, expiration date, and compensating
control sufficient to satisfy the Phase 21 exit criteria.

## Subsystem Release Recommendations

| Subsystem | Decision | Rationale | Conditions to change decision |
| --- | --- | --- | --- |
| Core upload and fundamental scoring | Conditional Go | Full tests pass and core workflow is usable for local research, but upload safety, duplicate ticker semantics, formula/export injection, model label drift, migration, and CSRF risks remain. | Fix clean migrations and CSRF guard; document duplicate/upload retention policy; add formula/export hardening and model-change governance. |
| IB market-data and technical scoring | Conditional Go | IB paths are read-only and tests cover many data states, but Pine frozen parity, adjusted-vs-trades semantics, stale threshold behavior, and bar-quality flags are incomplete. | Add Pine parity fixture, split/corporate-action policy, stale threshold fix, and provider edge-case tests. |
| Combined ranking and contextual overlays | Conditional Go | Ranking, market regime, and sector rotation are useful advisory layers with good tests, but missing-data penalty, growth-trap sizing, future-context leakage, snapshot immutability, and label taxonomy remain unresolved. | Fix ranking/gate semantics, immutable snapshot revisions, run-scoped context cutoff rules, and shared advisory wording. |
| Setup lifecycle | No-Go for authoritative use | Open findings include mutable snapshots, future global context attachment, replay confirmation bypass, purge execution despite disabled config, and missing property coverage. | Close PH11 findings, enforce point-in-time/canonical semantics, add confirmation gates and lifecycle property tests. |
| Winner probability | No-Go for production/model reliance; Conditional Go for disabled shadow review | Tests and evidence structures are strong, and flags default off, but promotion gates, model approval policy, calibration visibility, and upstream point-in-time risks are not release-ready. | Keep disabled until model governance, promotion gates, per-feature cutoff audit, and rollback policy are implemented and reviewed. |
| CERI | No-Go for provider/licensed-data use; Conditional Go for manual fixture exploration with admin disabled | CERI has strong docs/redaction foundations, but correction lineage, score-history mode, purge semantics, and raw job payload redaction remain blockers. | Resolve purge lifecycle, correction supersession chain, redacted job status, provider terms enforcement, and point-in-time mode application. |
| Administrative and destructive operations | No-Go | Phase 15 blockers remain: missing shared CSRF/local-admin boundary, static CERI token, public binding/debug policy, raw error details, and incomplete no-order regression. | Add centralized POST guard, real CSRF, host allowlist/fail-fast settings, public error redaction, and route-map/security tests. |

## Final Release Gate Status

| Gate | Status | Notes |
| --- | --- | --- |
| All S0 findings closed | Failed | Multiple S0-class risks remain: point-in-time leakage, unauthorized state-changing actions, secret/error exposure, purge semantics, and migration/data recovery. |
| All S1 findings closed or accepted | Failed | No accountable-owner acceptance, compensating controls, or target dates are documented. |
| Complete automated suite | Passed | 943 tests pass. |
| Golden/specialized suites | Passed for local covered suites | 459-test specialized slice passes. Pine external frozen parity remains missing from Phase 8. |
| Clean migration | Failed | Clean PostgreSQL upgrade fails at CERI migration `0019`. |
| Concurrency tests | Partial | Unit/fake DB worker tests pass; real PostgreSQL concurrency remains missing. |
| Security tests/gates | Failed/Partial | Some tests pass, but central CSRF/local-admin/host guards are missing. |
| Performance tests | Partial | Lightweight tests exist; large-fixture/query/memory gates missing. |
| Backup/restore | Failed | No restore test/runbook. |
| Governance/docs | Failed | Phase 20 exit criteria not met. |

## Required Remediation Before Go

Immediate blockers:

1. Repair Alembic migrations so a clean PostgreSQL database can upgrade to head.
2. Add a central state-changing route guard with local-admin, CSRF, Host/debug/public-bind policy,
   and route-map tests.
3. Implement backup/restore scripts and a restore validation report.
4. Resolve point-in-time leakage issues in technical pivots, market/sector context, setup lifecycle,
   winner-probability inputs, and CERI history modes.
5. Resolve CERI purge semantics and redacted job status exposure.
6. Add governance files: `CONTRIBUTING.md`, `CODEOWNERS`, PR template, `CHANGELOG.md`,
   `docs/versioning.md`, and P1 ADRs.

Next remediation milestone:

1. Add real PostgreSQL integration tests for locks, constraints, JSONB, partial indexes, and
   concurrent job claims.
2. Add browser smoke tests for upload, run detail, progress polling, charts, filters, exports, and
   admin confirmation states.
3. Add route/export inventory generation and documentation drift checks.
4. Add scoring/model golden-governance enforcement.
5. Add performance/query/memory budgets for large runs and exports.

## Follow-Up Review Schedule

| Review | Timing | Scope |
| --- | --- | --- |
| Remediation Review A | Immediately after migration/security fixes | Re-run clean migration, security route-map tests, full suite, and affected subsystem tests. |
| Remediation Review B | After point-in-time and CERI purge fixes | Re-run Pine parity, cutoff/leakage tests, CERI history/purge tests, winner feature audit tests. |
| Operations Review | After backup/restore implementation | Execute backup, restore to clean DB, validation report, readiness, alert, and incident runbook drill. |
| Governance Review | Before shared contribution or release branch | Verify CODEOWNERS, PR template, changelog, ADRs, versioning policy, release checklist. |
| Final Release Recut | After all S0/S1 findings closed or formally accepted | Recalculate subsystem decisions and produce Go/Conditional Go/No-Go update. |

## Exit Criteria Assessment

| Exit criterion | Status | Assessment |
| --- | --- | --- |
| All S0 findings are closed | Not met | Several S0-class risks remain open and unaccepted. |
| All S1 findings are closed or accepted | Not met | No owner acceptance records with target dates and compensating controls exist. |
| Final report links conclusions to evidence | Met | This report links decisions to Phase 4, 8, 10, 11, 12, 13, 15, 18, 19, 20, and Phase 21 verification evidence. |

Phase 21 status: **No-Go until S0/S1 remediation is completed and reverified**.
