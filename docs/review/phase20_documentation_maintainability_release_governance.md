# Phase 20 - Documentation, Maintainability, and Release Governance Review

Review date: 2026-08-02

## Scope

Phase 20 reviews whether SwingLens is understandable and safely maintainable after the software
review. It checks setup, migrations, configuration, architecture, workflows, troubleshooting,
quantitative-engine documentation, ADRs, stale comments/docstrings, contributor guidance,
versioning, changelog/release-note expectations, ownership, high-risk review rules, CODEOWNERS, and
pull-request templates.

## Executive Summary

Phase 20 is amber/red.

SwingLens has a useful documentation base: the README explains local setup, migrations, core
workflow, hardening checks, and safety boundaries; domain docs exist for market regime, sector
rotation, setup lifecycle, and CERI; and release notes exist for setup lifecycle, winner
probability, and CERI. The code also persists many domain versions and config hashes.

The repository is not yet maintainable by a new maintainer using docs alone. The top-level setup
path has a concrete PostgreSQL port mismatch, newer OWPE/CERI workflows are not documented in the
README at the same level as older modules, governance files are missing, ADRs are missing, semantic
versioning rules are implicit rather than enforced, and no owner/reviewer map exists for high-risk
scoring, migration, background-job, security, provider, purge, or model-release changes.

## Evidence Reviewed

| Area | Evidence |
| --- | --- |
| README setup and workflow | `README.md:31-76`, `README.md:105-116`, `README.md:119-145`, `README.md:156-194`, `README.md:204-234` |
| Runtime/dependency metadata | `pyproject.toml:5-34`, `pyproject.toml:39-50` |
| Environment defaults | `.env.example:1-61` |
| Docker PostgreSQL | `docker-compose.yml:2-16` |
| Actual FastAPI route table | Runtime introspection of `app.main:app` on 2026-08-02 |
| Domain docs | `docs/market_regime_command_center.md`, `docs/sector_rotation_dashboard.md`, `docs/setup_lifecycle_signal_change_engine.md`, `docs/ceri.md` |
| Release notes | `docs/release_notes_setup_lifecycle_signal_change_engine.md`, `docs/release_notes_winner_probability_engine.md`, `docs/release_notes_ceri.md` |
| Version/config persistence | `config/winner_probability.yaml:3-4`, `config/setup_lifecycle.yaml:3-5`, `config/sector_rotation.yaml:1`, `config/technical_scoring_v4.yaml:2-3`, `app/models/tables.py:518-660`, `app/models/tables.py:1404-1768`, `app/models/tables.py:2123-2584`, `app/models/ceri_tables.py:499-645` |
| Governance file absence | `Test-Path .github`, `CODEOWNERS`, `CONTRIBUTING.md`, `CHANGELOG.md` all returned `False` |
| Stale/development marker scan | `rg "TODO|FIXME|HACK|phase_3|pending_phase|stub|legacy|temporary"` |

## Verification Commands

```powershell
rg --files -g "README*" -g "docs/**" -g ".github/**" -g "CODEOWNERS" -g "CONTRIBUTING*" -g "CHANGELOG*" -g "ADRs/**" -g "adr/**" -g "*.md"
uv run python -c "from app.main import app; [print(f'{','.join(sorted(getattr(r, 'methods', []) or []))} {getattr(r, 'path', '')}') for r in app.routes if getattr(r, 'path', '') and not getattr(r, 'path', '').startswith('/static')]"
Test-Path .github
Test-Path CODEOWNERS
Test-Path CONTRIBUTING.md
Test-Path CHANGELOG.md
rg -n "TODO|FIXME|HACK|phase_3|pending_phase|stub|legacy|temporary" app tests docs README.md config
```

No application code was changed for this phase.

## Findings

### PH20-001 - Local setup documentation has a PostgreSQL port mismatch

Severity: P1

Observed behavior: `.env.example` configures `DATABASE_URL` to
`127.0.0.1:5432` (`.env.example:9`), while `docker-compose.yml` maps the local PostgreSQL container
to host port `5433` (`docker-compose.yml:10-13`). The README local setup tells a maintainer to copy
`.env.example`, run the app, and apply Alembic migrations, but it does not mention starting Docker
Postgres or resolving the port mismatch (`README.md:37-76`, `README.md:105-116`).

Expected behavior: A new maintainer should be able to follow one setup path and reach a working DB,
migrations, and app without guessing which port is authoritative.

Impact: New maintainers can fail migrations or runtime DB checks immediately after following the
documented setup.

Recommended remediation:

- Align `.env.example` and `docker-compose.yml` host port, or explicitly document both paths.
- Add a setup smoke test section: `docker compose up -d postgres`, `uv run alembic upgrade head`,
  `uv run uvicorn ...`, `/ready`.
- Prefer `uv run alembic ...` and `uv run uvicorn ...` in docs so commands work before shell
  activation.

Acceptance criteria:

- A clean checkout can follow README setup verbatim and reach `/ready` with database checks passing.

### PH20-002 - Top-level docs lag the actual route/workflow surface

Severity: P1

Observed behavior: README documents core uploads, IB, cockpit, market regime, sector rotation, and
setup lifecycle exports (`README.md:119-194`). Runtime route introspection also exposes extensive
winner-probability routes and CERI routes:

- `/winner-probability/operations`, `/winner-probability/models`,
  `/api/winner-probability/run/{run_id}/export.csv`, `/api/winner-probability/models/{id}/drift`,
  and related outcome/model routes.
- `/ceri`, `/ceri/changes`, `/ceri/operations`, `/ceri/export.csv`,
  `/api/ceri/operations/status`, `/api/ceri/providers/health`, and admin purge/backfill/reprocess
  routes.

Those modules have release notes or domain docs, but they are not integrated into the top-level
maintainer workflow, export catalog, or setup/configuration guide.

Expected behavior: README should point maintainers to every major subsystem and list feature flags,
entry points, exports, admin boundaries, and smoke tests for each.

Impact: New maintainers may miss disabled-by-default subsystems, admin routes, model governance
surfaces, and export/API obligations when changing shared code.

Recommended remediation:

- Add README sections for OWPE and CERI alongside market regime, sector rotation, and setup
  lifecycle.
- Add a route/export inventory generated from `app.main:app` or a checked review script.
- Add a subsystem smoke matrix with flags, pages, APIs, and focused test commands.

Acceptance criteria:

- A maintainer can discover every major subsystem and export path from the README without reading
  route modules directly.

### PH20-003 - Repository governance files are missing

Severity: P1

Observed behavior: There is no `.github` directory, no `CODEOWNERS`, no `CONTRIBUTING.md`, and no
`CHANGELOG.md`. No PR template, migration checklist, model-change checklist, release checklist, or
code-review checklist was found.

Expected behavior: High-risk changes should have explicit review and versioning rules, and routine
contributors should have one documented path for setup, tests, review expectations, and release
notes.

Impact: Changes to scoring formulas, migrations, provider licensing, admin routes, purge behavior,
or model artifacts can be reviewed like ordinary application changes.

Recommended remediation:

- Add `CONTRIBUTING.md`.
- Add `.github/pull_request_template.md`.
- Add `CODEOWNERS`.
- Add `CHANGELOG.md` or `docs/releases/`.
- Add checklists for code review, migrations, model changes, and releases.

Acceptance criteria:

- Every high-risk file family has named reviewers and mandatory checklist items.

### PH20-004 - ADRs are absent for major design choices

Severity: P2

Observed behavior: Design decisions are embedded across `docs/vision.md`, `docs/sdd.md`, execution
plans, release notes, and review reports, but no ADR directory or ADR index exists. Major decisions
without standalone ADRs include local-only/no-orders scope, PostgreSQL as durable evidence store,
IB read-only behavior, point-in-time evidence semantics, background job leasing/fencing, CERI
provider licensing/redaction, audit-only versus destructive purge semantics, and OWPE model
activation/rollback gates.

Expected behavior: Durable architectural decisions should be short, indexed, dated, and linked to
the code and tests they constrain.

Impact: Maintainers have to reconstruct why a design exists from long execution plans and review
reports, which increases the chance of undoing safety decisions accidentally.

Recommended remediation:

- Create `docs/adr/0000-template.md`.
- Create ADRs for the backlog listed later in this report.
- Link ADRs from README and PR template.

Acceptance criteria:

- High-risk design changes require creating or updating an ADR before merge.

### PH20-005 - Quantitative engine documentation is uneven

Severity: P1

Observed behavior: Market regime, sector rotation, setup lifecycle, and CERI have domain docs with
configuration, routes, limitations, and verification commands. Earlier engines are less well served:
fundamental scoring, technical/Pine parity, combined decisions, ranking profiles, and OWPE rely on
older SRS/SDD material, execution plans, review reports, tests, and release notes. The README only
briefly names technical indicators (`README.md:196-202`), and OWPE has release notes but no
compact maintainer reference equivalent to `docs/sector_rotation_dashboard.md`.

Expected behavior: Each quantitative engine should have a current maintainer doc covering inputs,
formulas, versions, config fields, persistence, limitations, validation evidence, and release rules.

Impact: Formula and version changes can drift from docs and tests, especially for engines whose
contracts are distributed across long plans and review artifacts.

Recommended remediation:

- Add maintainer references:
  - `docs/fundamental_scoring.md`
  - `docs/technical_scoring_pine_parity.md`
  - `docs/combined_decisions_ranking_profiles.md`
  - `docs/winner_probability_engine.md`
- Each doc should include formula tables, field contracts, versioning rules, golden fixtures,
  validation commands, and known limitations.
- Archive or relabel older `vision`/`srs`/`sdd` docs as historical unless they are updated to match
  the current app.

Acceptance criteria:

- A maintainer can change any scoring/model engine and know which docs, tests, fixtures, and version
  numbers must change.

### PH20-006 - Semantic versioning is present in data, but policy is implicit

Severity: P1

Observed behavior: The project version is `0.1.0` (`pyproject.toml:5-8`) and FastAPI app version is
also `0.1.0` (`app/main.py:98`). Domain configs and tables persist many versions and hashes:
winner-probability feature/calculation versions (`config/winner_probability.yaml:3-4`), setup
lifecycle engine/schema/config versions (`config/setup_lifecycle.yaml:3-5`), sector rotation
config version (`config/sector_rotation.yaml:1`), technical scoring version
(`config/technical_scoring_v4.yaml:2-3`), model artifact hashes, schema versions, config hashes, and
calculation versions in database models. No repository policy defines when to bump application,
schema, engine, config-schema, export, or model-artifact versions.

Expected behavior: Versioning rules should be explicit and enforced by review checklists/tests.

Impact: A scoring or export contract can change under the same version, weakening reproducibility
and auditability.

Recommended remediation:

- Add `docs/versioning.md` with SemVer-like rules for:
  - application version,
  - Alembic schema revision,
  - scoring engine calculation version,
  - config schema version,
  - config content hash,
  - export schema version,
  - model artifact schema/version/hash,
  - provider terms/version.
- Add tests/checks requiring version bumps when formula/export/config fixtures change.

Acceptance criteria:

- Reviewers can reject a high-risk change for missing version updates using written rules.

### PH20-007 - Comments and development markers include stale phase/stub wording

Severity: P3

Observed behavior: A scan found runtime metadata/debug strings such as
`phase_3_decision_time_contract_stub`, `phase_3`, `phase_3_snapshot_builder`, and
`pending_phase_4_canonicalization` in winner-probability and setup lifecycle services. Some are
historical markers, but without comments or documentation they read like unfinished implementation
state.

Expected behavior: Comments, docstrings, constants, and debug strings should identify stable
contract versions or clearly explain why a historical phase label remains.

Impact: Maintainers can misinterpret stable behavior as temporary scaffolding, or fail to update
legacy/debug identifiers when the contract changes.

Recommended remediation:

- Rename phase/stub identifiers that are not externally required.
- Where identifiers are persisted for compatibility, add comments and versioning notes.
- Add a periodic docs/comments scan to the release checklist.

Acceptance criteria:

- No source-visible "phase/stub/pending" marker remains unexplained in maintained runtime paths.

## Documentation Gap Register

| Gap | Priority | Current evidence | Target artifact |
| --- | --- | --- | --- |
| Clean local setup path mismatch | P1 | `.env.example:9`, `docker-compose.yml:10-13`, `README.md:37-76` | README setup fix |
| Database backup/restore and restore validation | P1 | Phase 19 finding | `docs/operations/backup_restore.md`, restore script/report |
| Full subsystem route/export inventory | P1 | Runtime route table exceeds README exports | Generated route/export inventory |
| Contributor guide | P1 | `CONTRIBUTING.md` absent | `CONTRIBUTING.md` |
| PR template | P1 | `.github` absent | `.github/pull_request_template.md` |
| CODEOWNERS | P1 | `CODEOWNERS` absent | `CODEOWNERS` |
| Changelog/release-note policy | P1 | `CHANGELOG.md` absent; release notes are feature-specific | `CHANGELOG.md`, `docs/releases/` |
| Versioning policy | P1 | Versions/hashes exist but no policy | `docs/versioning.md` |
| Fundamental scoring maintainer doc | P1 | Existing SRS/SDD/review artifacts only | `docs/fundamental_scoring.md` |
| Technical scoring/Pine parity maintainer doc | P1 | README paragraph and historical docs/review only | `docs/technical_scoring_pine_parity.md` |
| Combined/ranking-profile maintainer doc | P1 | Config/tests/review artifacts only | `docs/combined_decisions_ranking_profiles.md` |
| OWPE maintainer doc | P1 | Release notes and execution plan, no compact reference | `docs/winner_probability_engine.md` |
| Operations handbook | P1 | Operations pages exist; no handbook | `docs/operations/maintainer_handbook.md` |
| Incident runbooks | P2 | Phase 19 missing runbooks | `docs/operations/incidents.md` |
| ADR index and template | P2 | ADRs absent | `docs/adr/` |
| Security/local-admin guide | P2 | Phase 15 review only | `docs/security_local_admin.md` |
| Migration checklist | P1 | README has basic Alembic commands | `docs/governance/migration_checklist.md` |
| Model-change checklist | P1 | OWPE/fundamental findings require it | `docs/governance/model_change_checklist.md` |
| Release checklist | P1 | README hardening checks are partial | `docs/governance/release_checklist.md` |
| Ownership map | P1 | No owner map | `docs/governance/ownership.md` |

## Maintainer Handbook

### Daily Setup

1. Install Python 3.12.
2. Install dependencies:

```powershell
python -m pip install uv
uv sync --frozen --extra dev
```

3. Start PostgreSQL using the documented port after `.env.example`/Compose are aligned:

```powershell
docker compose up -d postgres
```

4. Copy environment defaults:

```powershell
Copy-Item .env.example .env
```

5. Apply migrations:

```powershell
uv run alembic upgrade head
```

6. Start the app:

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

7. Confirm:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/ready
```

### Routine Verification

```powershell
uv run ruff check app tests
uv run pytest -q
uv run pytest tests/test_golden_pipeline.py -q
uv run alembic current
```

### Subsystem Smoke Matrix

| Subsystem | Entry points | Focused checks |
| --- | --- | --- |
| Upload/history/core cockpit | `/`, `/runs`, `/history`, `/runs/{run_id}` | Upload, raw preservation, exports, golden pipeline |
| IB market data | `/ib`, `/runs/{run_id}/ib/plan`, fetch progress routes | Read-only test, contract resolution, failed fetch export |
| Technical scoring | run detail, chart routes, technical exports | Pine parity, data readiness, chart payload |
| Combined/ranking | run detail, ranking profile routes | Golden pipeline, ranking profile config/routes/exports |
| Market regime | `/market-regime`, run-scoped market pages/APIs | Policy, command center, export tests |
| Sector rotation | run-scoped sector dashboard/drilldown/APIs | Service/repository/export/route tests |
| Setup lifecycle | `/setup-lifecycle`, alerts, operations, APIs | `tests/setup_lifecycle -q` and pipeline integration |
| Winner probability | `/winner-probability/*`, OWPE APIs | `tests/winner_probability -q` |
| CERI | `/ceri`, ticker/change/operations pages/APIs | `tests/ceri -q` |
| Background jobs | pipeline, IB fetch, OWPE, SLSE, CERI jobs | background worker/job service tests |
| Security/admin | POST/admin/local-only routes | Phase 15 security suite |
| Operations/recovery | `/health`, `/ready`, operations pages | Phase 19 backup/readiness/incident checks |

### Troubleshooting Guide

| Symptom | First checks |
| --- | --- |
| App cannot connect to DB | Verify `.env` `DATABASE_URL`, Docker host port, and `docker compose ps`. |
| Alembic migration fails | Stop app/worker writes, backup DB, inspect `alembic current`, then rerun against disposable DB. |
| `/ready` degraded | Inspect readiness `checks`, DB availability, local directory permissions, and Phase 19 readiness gaps. |
| IB fetch stalls/fails | Check `/ib/status`, IB Gateway paper/live port, client ID, fetch progress, failed CSV. |
| Pipeline stuck | Check `/runs/{run_id}/pipeline/{pipeline_id}/status`, background jobs, stale lease recovery. |
| OWPE output missing | Check feature flags, capture-in-pipeline flag, operations page, processing runs. |
| CERI output missing | Check CERI feature flags, provider health, provider credentials, operations stale/quarantine views. |
| Export too large/slow | Review Phase 17 export memory findings and use scoped exports where possible. |
| Secret appears in error/log | Rotate secret, search logs/DB, apply shared redaction fix from Phase 19. |

## Release and Model-Governance Checklists

### Code Review Checklist

- Tests cover the changed behavior at the appropriate layer.
- User-facing routes, exports, and docs are updated.
- Error paths are redacted.
- Background jobs are idempotent or explicitly guarded.
- Any admin/destructive action has CSRF/local-admin protection and auditability.
- Any score/probability/gate change updates formulas, fixtures, and versions.
- Any DB change has a migration, downgrade/restore decision, and clean-DB verification.

### Migration Checklist

- Migration has correct `down_revision`.
- Clean PostgreSQL `upgrade head` passes.
- Existing populated database upgrade passes.
- Downgrade or restore plan is documented.
- Data backfill is idempotent and bounded.
- New indexes and constraints have query/performance rationale.
- Backup exists before production-like migration.

### Model/Scoring Change Checklist

- Identify affected engine: fundamentals, technical, combined, ranking, market regime, sector
  rotation, setup lifecycle, winner probability, or CERI.
- Bump calculation/model/config/export schema version as required by `docs/versioning.md`.
- Update formula documentation.
- Update golden fixtures and expected outputs.
- Run focused engine tests and full regression.
- Document limitations, validation evidence, and rollback path.
- For OWPE model artifacts, validate artifact hash, feature schema, config hash, calibration, drift,
  lifecycle event, and fallback model.

### Release Checklist

- `uv sync --frozen --extra dev`
- `uv run ruff check app tests`
- `uv run pytest -q`
- Focused subsystem tests for touched areas.
- `uv run alembic upgrade head` on clean PostgreSQL.
- Backup created and restore validation passed.
- `/health` and `/ready` pass in target environment.
- Route/export inventory reviewed.
- Changelog/release notes updated.
- ADRs updated for major design changes.
- Incident/rollback procedure confirmed for high-risk changes.

## Semantic Versioning Rules

| Artifact | Version source | Bump rule |
| --- | --- | --- |
| Application | `pyproject.toml`, `app/main.py` | Bump for every release; keep values aligned. |
| Database schema | Alembic revision | New migration for every persisted schema change. |
| Fundamental model | `scoring_model_version`/config | Bump on formula, field, label, threshold, or weight semantics change. |
| Technical engine | `technical_engine_version`, technical config | Bump on Pine parity, indicator, parameter, readiness, or output schema change. |
| Combined/ranking | ranking/combined config and output docs | Bump config/export schema when labels, gates, weights, or sort behavior change. |
| Market regime | `calculation_version`, `config_version`, `config_hash` | Bump calculation for logic, config version for policy contract, hash for content. |
| Sector rotation | calculation/config/hash | Same as market regime. |
| Setup lifecycle | engine/schema/config versions/hash | Bump schema for snapshot/event payload contract; engine for lifecycle logic. |
| Winner probability | feature schema, calculation version, model artifact schema/hash | Bump any time feature, estimate, outcome, model, or artifact semantics change. |
| CERI | calculation/config/provider terms versions/hash | Bump for scoring logic, provider policy, export policy, retention, or terms changes. |
| Exports | export schema version | Bump when columns, meanings, evidence mode, or redaction semantics change. |

## Ownership Map

| Area | Primary owner recommendation | Required reviewers |
| --- | --- | --- |
| App shell/routes/templates/static | App maintainer | UX/accessibility reviewer for user-facing changes |
| Upload/CSV/raw preservation | Data ingestion owner | Security reviewer for file handling |
| Database models/Alembic | Persistence owner | Domain owner plus migration reviewer |
| Background jobs/pipeline | Operations owner | Persistence owner |
| IB market data | Provider integration owner | Safety/security reviewer |
| Fundamental scoring | Quant scoring owner | Golden-fixture reviewer |
| Technical/Pine scoring | Quant scoring owner | Pine-parity reviewer |
| Combined decisions/ranking profiles | Quant scoring owner | UX/safety reviewer |
| Market regime | Market context owner | Quant scoring reviewer |
| Sector rotation | Market context owner | Export/API reviewer |
| Setup lifecycle | Lifecycle owner | OWPE owner if point-in-time features change |
| Winner probability/OWPE | Model governance owner | Quant validation reviewer |
| CERI/provider/licensing/purge | Provider governance owner | Security/legal-policy reviewer |
| Security/local admin | Security owner | App maintainer |
| Operations/backup/recovery | Operations owner | Persistence owner |
| Docs/release governance | Maintainer lead | Affected domain owner |

## CODEOWNERS Recommendation

```text
# Global fallback
* @maintainer-lead

/app/models/ @persistence-owner
/alembic/ @persistence-owner
/app/services/background_* @operations-owner
/app/services/pipeline* @operations-owner
/app/services/upload* @data-ingestion-owner
/app/services/ib* @provider-integration-owner
/app/services/fundamental* @quant-scoring-owner
/app/services/technical* @quant-scoring-owner
/app/services/combined* @quant-scoring-owner
/app/services/ranking* @quant-scoring-owner
/app/services/market_regime* @market-context-owner
/app/services/sector_rotation* @market-context-owner
/app/services/setup_lifecycle/ @lifecycle-owner
/app/services/winner_probability/ @model-governance-owner
/app/services/ceri/ @provider-governance-owner
/app/routers/* @app-maintainer
/app/templates/ @ux-accessibility-owner
/app/static/ @ux-accessibility-owner
/config/ @maintainer-lead @quant-scoring-owner
/docs/ @docs-owner
/tests/ @maintainer-lead
```

Replace placeholders with real repository usernames before enabling.

## Pull Request Template Recommendation

```markdown
## Summary

## Risk Area

- [ ] Scoring/model behavior
- [ ] Database migration/data retention
- [ ] Background job/concurrency
- [ ] Provider/API integration
- [ ] Admin/destructive operation
- [ ] Security/redaction/local-only boundary
- [ ] UI/export/accessibility
- [ ] Documentation only

## Verification

- [ ] `uv run ruff check app tests`
- [ ] Focused tests:
- [ ] `uv run pytest -q`
- [ ] Alembic check:
- [ ] Manual smoke:

## Versioning and Governance

- [ ] App/version change considered
- [ ] Migration version added or not applicable
- [ ] Engine/model/config/export version bump considered
- [ ] Golden fixtures updated or not applicable
- [ ] ADR updated or not applicable
- [ ] Changelog/release notes updated

## Safety

- [ ] No broker order path added
- [ ] Secrets and local paths redacted
- [ ] Admin/destructive actions protected and audited
- [ ] Rollback/restore path documented for high-risk change
```

## ADR Backlog

| ADR | Priority | Decision |
| --- | --- | --- |
| ADR-001 | P1 | Local-only decision-support boundary and no broker orders. |
| ADR-002 | P1 | PostgreSQL as durable evidence store and immutable raw upload preservation. |
| ADR-003 | P1 | Alembic migration and restore-first schema rollback policy. |
| ADR-004 | P1 | Background job leases, execution-token fencing, and stale recovery. |
| ADR-005 | P1 | Scoring engine versioning and golden-fixture governance. |
| ADR-006 | P1 | OWPE point-in-time evidence, immutable decision-time estimates, and model activation gates. |
| ADR-007 | P1 | CERI provider licensing, export redaction, and purge semantics. |
| ADR-008 | P2 | Local admin enablement, CSRF, and localhost binding policy. |
| ADR-009 | P2 | Evidence provenance vocabulary across HTML, JSON, CSV, and Markdown exports. |
| ADR-010 | P2 | Backup/restore, retention, archive, and cleanup policy. |
| ADR-011 | P2 | Market regime and sector rotation are advisory overlays that do not mutate ranking order. |
| ADR-012 | P2 | Dependency update and lockfile governance. |

## Exit Criteria Assessment

| Exit criterion | Status | Assessment |
| --- | --- | --- |
| New maintainer can set up the app using docs | Not met | README setup has a DB port mismatch and omits a complete Docker/Postgres path. |
| New maintainer can test the app using docs | Partially met | README and domain docs list many commands, but no unified subsystem matrix/release gate exists. |
| New maintainer can operate the app using docs | Not met | Phase 19 found missing backup/restore, incident, alert, and rollback runbooks. |
| New maintainer can trace the app using docs | Partially met | Domain docs and review reports help, but route/export inventory and ADRs are missing. |
| High-risk changes have explicit review/versioning rules | Not met | No CODEOWNERS, PR template, contributor guide, changelog policy, ADR process, or versioning policy. |

## Recommended Remediation Order

1. Fix the README/.env/Docker PostgreSQL setup path.
2. Add `CONTRIBUTING.md`, PR template, CODEOWNERS, and `CHANGELOG.md`.
3. Add `docs/versioning.md` and high-risk review checklists.
4. Add compact maintainer docs for fundamentals, technical/Pine parity, combined/ranking, and OWPE.
5. Add operations docs from Phase 19: backup/restore, incidents, rollback, alerts.
6. Create ADR template/index and write the P1 ADRs.
7. Add generated route/export inventory checks so docs drift becomes visible in CI.
