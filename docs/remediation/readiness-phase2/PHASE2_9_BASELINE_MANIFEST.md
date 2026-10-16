# Phase 2.9 baseline manifest

Captured: 2026-10-13 (Europe/Zurich)

## Source state

- Branch: `codex/ceri-authority-remediation`
- Baseline commit: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Alembic head: `0092_phase2_eligibility_ledgers` (single head)
- Execution source: the existing working tree, including its uncommitted files.
- Isolation decision: a new worktree cannot reproduce the required source because the
  Phase 2 implementation depends on uncommitted authority-remediation and GUI work.
  No changes were reset, stashed, or discarded; Phase 2.9 is being applied in place
  with this manifest and its implementation ledger as the ownership boundary.

## Working-tree ownership groups

| Group | Representative scope | Ownership/treatment |
|---|---|---|
| Pre-existing authority and runtime remediation | T14 inventory/registry work, lifecycle operations, transition preflight, CERI writer boundaries, route/export inventory | Required dependency; preserve and repair only where a reproduced release blocker requires it |
| Pre-existing market-data and GUI remediation | migrations 0090/0091, market-data readiness services/tests, GUI reports, route/template/static changes | User work; preserve and avoid unrelated rewrites |
| Phase 2 readiness/eligibility | migration 0092, readiness/eligibility ledgers, policy/taxonomy, pipeline outcome, CERI containment/actionability, Winner evidence, ADR-0013, Phase 2 tests/docs | Required implementation under certification |
| Phase 2.9 | PostgreSQL test-environment resolution, remaining failure repairs, bounded Fundamental activation, consolidated certification evidence | Track in `PHASE2_9_IMPLEMENTATION_LEDGER.md` |

The authoritative path-by-path starting inventory is the `git status --short`
capture in the Phase 2.9 test artifacts. Phase 2.9 changes are deliberately kept
minimal because several files already contain mixed pre-existing work.

## Database and migration boundary

- Normal application database: configured local `swinglens` database; never a
  destructive-test target.
- Test administrator resolution: explicit
  `SWINGLENS_TEST_POSTGRES_ADMIN_URL` for CI/dedicated environments, or credentials
  derived from the configured database only when its host is loopback. The selected
  database is forcibly changed to `postgres`; passwords are never printed.
- Destructive targets: UUID-suffixed databases whose names begin with an approved
  `swinglens_pytest_` prefix.
- Guard: every created database must pass live connected-identity verification in
  `assert_disposable_database` before migration or application use.
- Safety context: `SWINGLENS_DATABASE_SAFETY_CONTEXT=DISPOSABLE_TEST`.
- Cleanup: `DROP DATABASE ... WITH (FORCE)` is scoped to the generated, verified
  disposable name in fixture teardown.
- Optional reproducible service: `docker-compose.postgres-test.yml` binds a dedicated
  PostgreSQL 16 service to loopback port 5433. Docker Desktop was unavailable on this
  host, so local loopback credential reuse is the active implementation path.

## Test/runtime configuration

- Python: repository `.venv` interpreter.
- Full-suite command: `.venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider`.
- Browser tests: Playwright-launched headless browser and a disposable migrated
  PostgreSQL database per browser session.
- E2E application runtime: local loopback Uvicorn, durable/background worker features
  disabled unless the certification scenario explicitly exercises them.
- Live SEC, EODHD, IBKR, and other provider access is prohibited for this phase.
- Runs 22–33 are immutable and may only be read for verification.

## Reproducible starting failures

The previous Phase 2 report recorded `4018 passed, 443 skipped, 20 failed, 24
errors` before three targeted Phase 2 repairs. That total is historical evidence,
not a current result. The remaining reported set to reproduce consists of 17
failures (purge emulation, lifecycle inventory, registry, transition preflight,
T14A, T14B, and T14D) and 24 browser setup errors caused by the obsolete hardcoded
`postgres:postgres` default. A complete repository run had not been executed after
the three repairs; Phase 2.9 will create the new authoritative result.

## Gate A decision

PASS. The exact revision, mixed-worktree dependency, schema head, runtime test
configuration, database targets, safety boundary, and historical failure set are
recorded. Existing work is protected by in-place, additive remediation and a
durable per-stage ledger.
