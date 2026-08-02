# SwingLens Phase 0 Review Baseline

Review date: 2026-08-02
Review plan source: `C:\Users\Ivica\Downloads\software_review_plan.md`
Workspace: `C:\Users\Ivica\Documents\SwingLens`

## Objective

Phase 0 establishes the exact version, runtime, repository inventory, configuration surface,
schema state, lint/test baseline, and governance observations being reviewed.

## Evidence Log

| Check | Evidence |
|---|---|
| Git root | `C:/Users/Ivica/Documents/SwingLens` |
| Branch | `codex/catalyst-estimate-revision-intelligence` |
| Commit SHA | `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685` |
| Working tree | Clean at baseline capture |
| Remote | `https://github.com/ivobog/swing-lens.git` |
| Python | `Python 3.12.2` |
| uv | `uv 0.11.27` |
| OS | Microsoft Windows 10 Home `10.0.19045`, 64-bit |
| PostgreSQL client | `psql` not found on PATH |
| PostgreSQL server | `PostgreSQL 18.3 on x86_64-windows` via SQLAlchemy |
| Effective DB target | `postgresql+psycopg://<redacted>@127.0.0.1:5432/swinglens` |
| Docker | Docker `29.2.1`, Compose `v5.1.0`; daemon not reachable during review |
| IB Gateway/TWS | Not observed from repository or local process evidence in phase 0 |

## Baseline Commands

| Command | Result | Duration | Notes |
|---|---:|---:|---|
| `uv sync --frozen --extra dev` | Passed | 0.13s | Lockfile resolved without dependency changes |
| `alembic upgrade head` | Passed | 2.51s | Current DB reached head |
| `ruff check app tests` | Passed | 0.10s | No Ruff findings |
| `pytest -q` | Passed | 41.13s | `943 passed in 38.14s` |
| `pytest tests/test_golden_pipeline.py -q` | Passed | 4.34s | `1 passed in 2.00s` |

Current Alembic head in database: `0021_add_ceri_earnings_consensus_reason`.

## Repository Inventory

| Area | Count |
|---|---:|
| Application files under `app` | 253 |
| Routers | 12 |
| Services | 186 |
| Models | 5 |
| Alembic migrations | 21 |
| Templates | 35 |
| Static assets | 8 |
| YAML configuration files | 12 |
| Tests | 177 |
| Documentation files | 17 |
| GitHub workflow files | 0 |

Primary subsystems:

- Web layer: FastAPI app factory, routers, Jinja templates, static JS/CSS.
- Persistence: SQLAlchemy models, Alembic migrations, PostgreSQL-backed repositories.
- CSV and fundamentals: upload, mapping, validation, ranking, exports.
- IB market data: connection, contract resolution, fetch planning, fetch jobs, price-bar cache.
- Technical scoring: Pine-compatible indicators, feature flags, technical score v4.
- Decision/ranking: combined decision, ranking profiles, penalties, gates, exports.
- Context engines: market regime and sector rotation.
- Advanced engines: setup lifecycle, winner probability, CERI.
- Operations: durable pipeline, background jobs, worker leases, health/readiness.

## Route Surface

Registered method counts from `create_app(Settings(_env_file=None, job_worker_enabled=False))`:

| Method | Count |
|---|---:|
| GET | 116 |
| HEAD | 4 |
| POST | 38 |

State-changing route inventory captured 38 POST routes across uploads, IB fetch/test/resolve,
run recalculation, pipeline control, ranking refresh, market/sector recalculation,
setup-lifecycle actions, winner-probability actions, and CERI admin/processing actions.
These should feed phase 15's local-admin and CSRF matrix.

## Feature-Flag Matrix

Runtime flags from `app.settings.Settings`:

| Flag | Default | Effective | Direct test refs |
|---|---:|---:|---:|
| `debug` | `True` | `True` | 180 |
| `use_durable_pipeline` | `True` | `True` | 5 |
| `ib_use_rth` | `True` | `True` | 0 |
| `ib_force_conservative_mode` | `True` | `True` | 0 |
| `ib_fetch_benchmarks` | `True` | `True` | 0 |
| `ib_revision_audit_enabled` | `True` | `True` | 0 |
| `job_worker_enabled` | `True` | `True` | 26 |
| `winner_probability_enabled` | `False` | `False` | 3 |
| `winner_probability_capture_in_pipeline` | `False` | `False` | 3 |
| `winner_probability_admin_enabled` | `False` | `False` | 9 |
| `setup_lifecycle_enabled` | `False` | `False` | 13 |
| `setup_lifecycle_pipeline_step_enabled` | `False` | `False` | 13 |
| `setup_lifecycle_alerts_enabled` | `False` | `False` | 3 |
| `setup_lifecycle_replay_enabled` | `False` | `False` | 3 |
| `setup_lifecycle_reconstruction_enabled` | `False` | `False` | 3 |
| `setup_lifecycle_retain_indefinitely` | `True` | `True` | 4 |
| `setup_lifecycle_purge_enabled` | `False` | `False` | 4 |
| `setup_lifecycle_purge_requires_preview` | `True` | `True` | 3 |
| `setup_lifecycle_replay_promotion_requires_confirmation` | `True` | `True` | 3 |
| `ceri_enabled` | `False` | `False` | 11 |
| `ceri_provider_ingest_enabled` | `False` | `False` | 3 |
| `ceri_run_capture_enabled` | `False` | `False` | 9 |
| `ceri_ui_enabled` | `False` | `False` | 11 |
| `ceri_alerts_enabled` | `False` | `False` | 3 |
| `ceri_admin_enabled` | `False` | `False` | 8 |
| `ceri_backfill_enabled` | `False` | `False` | 3 |

Initial flag observations:

- The checked-in `.env.example` includes the advanced feature flags, but local `.env` only includes
  core/IB/job flags. Missing local values fall back to `Settings` defaults.
- IB behavior flags have no direct test references by flag/env name in the current suite. These are
  untested combinations for phase 3 and phase 6 unless covered indirectly.
- Advanced engines default off at runtime while several nested YAML sections default internal
  component switches on. Phase 3 should verify parent/child flag compatibility and fail-fast rules.
- Potentially invalid combinations to test include capture/admin/backfill/UI flags enabled while the
  parent engine flag is disabled, purge enabled without preview, and pipeline steps enabled while
  the underlying engine is disabled.

## Dependency Baseline

Top-level locked dependencies from `uv tree --frozen --depth 1`:

- `alembic v1.18.5`
- `fastapi v0.139.0`
- `ib-insync v0.9.86`
- `jinja2 v3.1.6`
- `numpy v2.5.1`
- `openpyxl v3.1.5`
- `pandas v3.0.3`
- `psycopg[binary] v3.3.4`
- `pydantic-settings v2.14.2`
- `python-multipart v0.0.32`
- `pyyaml v6.0.3`
- `sqlalchemy v2.0.51`
- `uvicorn[standard] v0.50.2`
- dev extra: `httpx v0.28.1`, `pytest v9.1.1`, `ruff v0.15.20`

## Governance and CI

Local repository evidence:

- `.github/workflows` is absent.
- No CI workflow files were present in the working tree.
- No Dependabot configuration was present in the working tree.
- `gh` CLI was not installed.

GitHub public API evidence:

- Repository `ivobog/swing-lens` is public.
- Default branch is `main`.
- Repository is not archived.
- Actions workflows API returned `workflow_count=0`.
- Branch-protection endpoint returned `401 Unauthorized`; status checks, required reviews,
  secret scanning, and admin-enforced protection were not observable without authenticated access.

## No-Order Boundary Smoke Evidence

Targeted application/test scan for broker-order sentinel APIs:

`placeOrder|submit_order|cancel_order|modify_order|broker_order|reqOpenOrders|openOrder|whatIfOrder|ib.placeOrder|Order(`

Result: no application Python matches; matches were confined to test sentinel strings in
`tests/setup_lifecycle/test_setup_lifecycle_acceptance_fixture.py`.

This is baseline smoke evidence only. The first review sprint should add or consolidate a
repository-wide regression test for the no-order boundary.

## Findings Register

ID: PH0-001
Title: No CI workflows are present for the repository
Severity: S2 Medium
Confidence: Confirmed
Affected components: Engineering system, release quality gates
Evidence: `rg --files` and GitHub workflows API both found zero workflow files/workflows.
Reproduction steps: Check `.github/workflows`; call GitHub Actions workflows API.
Expected behavior: Baseline lint, tests, migration checks, and security/dependency checks run in CI.
Observed behavior: No workflows are configured in the repository evidence available to phase 0.
Impact: Baseline quality gates can pass locally but are not enforced for changes.
Root cause or likely cause: CI has not been added or is managed outside the repository.
Recommended remediation: Add GitHub Actions for `uv sync --frozen --extra dev`, Alembic migration
smoke, Ruff, full pytest, golden pipeline, dependency audit, and secret scanning.
Acceptance criteria: Pull requests cannot merge without required green checks.
Regression tests required: CI workflow validation through a test pull request.
Owner profile: Maintainer / DevOps-capable engineer
Dependencies: Authenticated repository admin access for branch protection.

ID: PH0-002
Title: Branch protection and required status checks could not be verified
Severity: S2 Medium
Confidence: Strong
Affected components: Repository governance
Evidence: Public branch protection API returned `401 Unauthorized`; no `gh` CLI available.
Reproduction steps: Query `https://api.github.com/repos/ivobog/swing-lens/branches/main/protection`
without authenticated admin scope.
Expected behavior: Review baseline can confirm required reviews, required checks, force-push
protection, and admin enforcement.
Observed behavior: Protection details were not observable in phase 0.
Impact: Governance risk remains unknown; a green local baseline may not be enforced on `main`.
Root cause or likely cause: Missing authenticated repository access in the review environment.
Recommended remediation: Re-run governance capture with admin-scoped GitHub access and document
branch protection, required checks, dependency automation, and secret scanning settings.
Acceptance criteria: Governance evidence is captured in the review log and any gaps become issues.
Regression tests required: Not code-level; repository setting verification checklist.
Owner profile: Repository owner / maintainer
Dependencies: GitHub admin permissions.

ID: PH0-003
Title: IB runtime behavior flags lack direct flag-coverage evidence
Severity: S2 Medium
Confidence: Strong
Affected components: IB integration, market-data lineage
Evidence: Direct test-reference scan found zero test references for `ib_use_rth`,
`ib_force_conservative_mode`, `ib_fetch_benchmarks`, and `ib_revision_audit_enabled`.
Reproduction steps: Count flag/env-name occurrences in `tests/**/*.py`.
Expected behavior: High-impact market-data flags have tests for enabled/disabled behavior and
lineage effects.
Observed behavior: No direct flag tests were found by name.
Impact: Changes to RTH handling, benchmark fetch, conservative mode, or revision audit behavior
could silently alter market-data completeness or lineage.
Root cause or likely cause: IB tests focus on services rather than explicit flag matrix coverage.
Recommended remediation: Add risk-based flag tests in phase 3/6, especially for stale-data,
benchmark alignment, RTH semantics, and revision-audit on/off behavior.
Acceptance criteria: Each IB flag has at least one explicit positive/negative test or a documented
reason for indirect coverage.
Regression tests required: Unit/service tests plus integration tests where provider behavior matters.
Owner profile: Backend engineer with market-data domain context
Dependencies: IB fixture strategy or fakes that preserve lineage semantics.

ID: PH0-004
Title: Local database setup documentation and defaults are inconsistent with Docker port mapping
Severity: S2 Medium
Confidence: Confirmed
Affected components: Setup documentation, database baseline
Evidence: `docker-compose.yml` maps host `5433` to container `5432`, while `Settings.database_url`
and local `.env` target host port `5432`.
Reproduction steps: Inspect `docker-compose.yml`, `app/settings.py`, and effective `Settings`.
Expected behavior: Documented Docker setup and default database URL agree, or documentation clearly
explains the two modes.
Observed behavior: Alembic passed because a separate PostgreSQL server exists on `5432`, while the
checked-in Compose service would publish on `5433`.
Impact: A clean developer may start Compose and still fail to connect with the default `.env`.
Root cause or likely cause: Local native Postgres and Docker Postgres defaults diverged.
Recommended remediation: Align `.env.example`/README with Compose or change Compose mapping; add a
readiness note that reports the effective DB host/port.
Acceptance criteria: Following README from a clean machine creates a reachable database without
manual port inference.
Regression tests required: Documentation smoke or setup script check.
Owner profile: Maintainer
Dependencies: Decide whether native Postgres or Compose is the preferred default.

## Action Backlog

Immediate:

- Re-run repository-governance capture with authenticated GitHub access.
- Decide and document the canonical local PostgreSQL path: native `5432` or Compose `5433`.

Near term:

- Add GitHub Actions for dependency sync, migration smoke, Ruff, full pytest, and golden pipeline.
- Add explicit tests for IB runtime flags and parent/child advanced feature-flag combinations.
- Add or centralize a repository-wide no-order-boundary regression test.

Structural:

- Build an automatically generated feature-flag compatibility matrix.
- Add a route authorization/CSRF matrix for all 38 POST routes in phase 15.
- Add a clean-database migration smoke test that can run in CI against PostgreSQL.

## Test Additions Proposal

- Migration smoke: create an empty PostgreSQL database, run `alembic upgrade head`, assert head.
- Flag matrix: pairwise tests for advanced engine parent flags versus capture/admin/UI/backfill
  children.
- IB flag tests: RTH on/off, conservative mode on/off, benchmark fetch on/off, revision audit on/off.
- No-order boundary: scan application Python and templates for order-capable IB APIs and order routes.
- CI smoke: run `pytest tests/test_golden_pipeline.py -q` separately so golden drift is easy to see.

## Phase Scorecard

| Dimension | Rating | Rationale |
|---|---|---|
| Dependency reproducibility | Green | `uv sync --frozen --extra dev` passed |
| Schema creation to head | Green | `alembic upgrade head` passed against configured DB |
| Lint/test baseline | Green | Ruff, full pytest, and golden pipeline passed |
| Runtime inventory | Amber | IB Gateway/TWS version not observed; `psql` unavailable |
| Feature-flag baseline | Amber | Inventory exists, but several high-impact combinations need tests |
| CI/governance | Red | No workflows observed; branch protection/status checks not verified |
| Clean setup documentation | Amber | Docker port and default DB URL diverge |

## Exit Report

Passed checks:

- Locked dependency sync.
- Alembic upgrade to head on the configured local PostgreSQL server.
- Ruff lint.
- Full pytest baseline.
- Golden pipeline baseline.
- Initial repository inventory.
- Initial feature-flag inventory.
- Initial route/state-changing endpoint inventory.

Failed or incomplete checks:

- `psql --version` could not run because `psql` is not on PATH.
- Docker daemon was not reachable, so Docker-based clean setup was not verified.
- IB Gateway/TWS version was not observed.
- Branch protection, required status checks, and secret scanning could not be verified without
  authenticated GitHub access.

Deferred to later phases:

- Deep validation of parent/child feature-flag semantics.
- Clean PostgreSQL migration from a newly created database in CI/Testcontainers.
- Full route authorization and CSRF review.
- Point-in-time leakage test matrix.
- Architecture dependency graph beyond the subsystem inventory above.

Phase 0 status: baseline established with Amber/Red governance and setup follow-ups.
