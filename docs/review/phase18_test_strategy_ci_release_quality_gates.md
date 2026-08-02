# Phase 18 Review: Test Strategy, CI, and Release Quality Gates

Date: 2026-08-02

## Objective

Turn the review's key invariants into automated protection.

## Verification Performed

- Test inventory:
  - 175 test files.
  - 943 collected tests.
  - 80 root test files.
  - 30 setup lifecycle test files.
  - 27 winner probability test files.
  - 37 CERI test files.
  - 1 integration test file.
- Tooling inventory:
  - Present: `pytest`, `ruff`, `httpx`, locked dependencies through `uv.lock`.
  - Not present: `coverage`, `pytest-cov`, `hypothesis`, `mutmut`, `cosmic-ray`, `playwright`, `pytest-playwright`.
- Commands run:
  - `uv run ruff check app tests`: passed.
  - `uv run pytest -q`: `943 passed, 1 warning` in 48.12s.
  - `uv run pytest --collect-only -q`: `943 tests collected`.
  - `uv run alembic heads`: single head, `0021_add_ceri_earnings_consensus_reason`.
  - `uv run alembic upgrade head --sql`: failed at migration `0014_sector_metadata`.

The test warning is the existing Starlette/httpx TestClient deprecation warning from `fastapi.testclient`.

## Test Pyramid And Gap Analysis

| Layer | Current Coverage | Evidence | Gaps |
| --- | --- | --- | --- |
| Unit/service tests | Strong | Broad root suite plus feature suites for scoring, IB, ranking, regime, sector, setup lifecycle, winner probability, and CERI. | Add mutation/fault checks around gates and scoring priorities. |
| Fake-database tests | Very heavy | `FakeDb` appears in 54 test files; `SimpleNamespace` in 26; `monkeypatch` in 34. | Too much concurrency, constraint, JSONB, lock, and migration behavior is simulated. |
| Integration tests | Thin | `tests/integration/test_webapp_fix_flows.py` covers upload, fetch plan/execution, cockpit, exports, technical refresh, and failed-contract resume. | Still uses fake DB/IB collaborators; no real PostgreSQL or browser-backed workflow. |
| Route/API tests | Good breadth | `TestClient` appears in 13 files and feature route tests cover dashboard, run detail, winner, CERI, setup lifecycle, market/sector. | Mostly semantic route assertions; no browser-level focus/navigation/JS/polling smoke tests. |
| Migration/schema tests | Partial | Schema tests inspect model metadata, JSONB types, indexes, and migration text/down revisions. | No clean PostgreSQL `alembic upgrade head` test. Offline SQL generation currently fails. |
| Contract tests | Strong for local contracts | Config validation, provider protocol, export columns, feature schemas, route error shapes, source redaction, provider health. | External IB/provider contracts are mocked; no network contract sandbox. |
| Quantitative tests | Strong seeds | Golden pipeline, ranking profile golden fixtures, Pine parity, winner probability validation, calibration/drift. | No mutation threshold for score/gate logic; golden updates need governance. |
| Performance tests | Early but useful | Setup lifecycle performance/index tests; CERI 500-row export under 2 seconds; pagination/rate limiter/worker tests. | No query-count, memory, real PostgreSQL large-fixture, or concurrent-browser tests. |
| End-to-end browser tests | Missing | No Playwright tooling installed. | Need smoke coverage for upload, run detail, progress polling, chart render, filters, exports, and admin confirmations. |

## S0/S1 Invariants To Gate

S0 invariants:

- SwingLens must not expose broker order placement, modification, or cancellation endpoints.
- Point-in-time evidence must not leak future observations or later corrections into historical views.
- Decision-time winner probability estimates must remain immutable.
- Background job leases must prevent stale workers from committing after lease loss.
- Purge/export policies must not expose restricted provider payloads or local paths.
- Migrations must create the expected PostgreSQL schema from a clean database.
- Uploaded raw rows and source lineage must remain preserved.

S1 invariants:

- Technical/fundamental scoring must degrade to incomplete/low-confidence rows instead of failing whole runs.
- Market, sector, earnings, lifecycle, CERI, and winner gates must remain deterministic.
- CSV/JSON/Markdown exports must preserve required audit fields.
- Pipeline/fetch retry, resume, cancel, and partial-completion behavior must remain durable.
- Pagination and page-size limits must be enforced.
- Golden fixture outputs must not change without review.

Current protection:

- Many S0/S1 invariants have direct unit or fake-DB tests.
- The weakest S0 area is real PostgreSQL behavior: migration execution, partial indexes, JSONB operators, `FOR UPDATE SKIP LOCKED`, concurrent claims, and transactional uniqueness.

## CI Workflow Design

Recommended checked-in workflows:

| Job | Trigger | Required | Command / Behavior |
| --- | --- | --- | --- |
| dependency-lock | PR, push | Yes | `uv sync --frozen --extra dev`; fail if lock is stale. |
| lint | PR, push | Yes | `uv run ruff check app tests`. |
| unit-fast | PR, push | Yes | `uv run pytest -q` on Python 3.12. |
| python-matrix | PR, nightly | Yes for release | Run test suite on supported Python versions declared by `pyproject.toml` (`>=3.12,<3.15`); start with 3.12 and 3.13, add 3.14 when generally available. |
| migration-postgres | PR, push | Yes | Start PostgreSQL 16, create clean DB, run `uv run alembic upgrade head`, verify `alembic current`, then run schema smoke queries. |
| migration-upgrade-fixture | PR for migrations | Yes | Restore a representative older schema/data fixture and upgrade to head. |
| migration-sql | PR for migrations | Yes after fix | Run `uv run alembic upgrade head --sql`; currently blocked by migration `0014_sector_metadata`. |
| postgres-integration | PR, push | Yes before release | Real DB tests for locks, uniqueness, JSONB, partial indexes, transaction rollback, and concurrent job claims. |
| coverage | PR, push | Report now, gate later | Add `pytest-cov`; capture line and branch coverage. Ratchet by module after baseline instead of choosing arbitrary global percentage. |
| security-static | PR, push | Yes | CodeQL or Semgrep plus secret scanning. |
| dependency-review | PR | Yes | Dependency review / vulnerability audit against `uv.lock`. |
| browser-smoke | PR for UI, nightly | Yes for release | Playwright smoke for upload/dashboard, run detail, progress polling, chart load, filters, export download, admin confirmation disabled/enabled paths. |
| performance-smoke | Nightly, release | Advisory then required | Query-count and large-fixture budgets from Phase 17. |

## Migration Gate Finding

`uv run alembic heads` reports one head, so the revision graph is linear at the head level.

However, `uv run alembic upgrade head --sql` fails while running `0014_sector_metadata`. The migration calls `_backfill_sector_metadata()` during upgrade (`alembic/versions/20260729_0014_add_raw_company_sector_metadata.py:91`), then calls `op.get_bind()` and executes a query (`alembic/versions/20260729_0014_add_raw_company_sector_metadata.py:128-132`). In offline SQL mode, the bind is not an executable connection, so the command fails with:

`AttributeError: 'NoneType' object has no attribute 'mappings'`

Required remediation:

- Either make data-backfill migrations offline-safe or remove offline SQL generation as a required gate.
- Add real PostgreSQL clean-upgrade CI so migration correctness is not inferred from metadata tests.
- Add upgrade-fixture tests for migrations with data movement/backfills.

## Required Quality Gates

Minimum PR gates:

1. `uv sync --frozen --extra dev`
2. `uv run ruff check app tests`
3. `uv run pytest -q`
4. `uv run alembic heads` with exactly one head
5. PostgreSQL clean migration: `alembic upgrade head`
6. Secret scan over repository contents
7. Dependency review for `uv.lock`

Required before release:

1. All PR gates.
2. Real PostgreSQL integration suite.
3. Browser smoke suite.
4. Coverage report with no drop from accepted baseline.
5. Performance smoke suite for Phase 17 budgets.
6. Golden fixture diff review.
7. Migration upgrade from at least one prior representative database.

Branch protection:

- Require all PR gates before merge.
- Require linear history or squash merges.
- Require review for changes under `config/`, `alembic/versions/`, `tests/fixtures/`, scoring engines, gate logic, and export schemas.
- Require signed or verified commits if this repository is shared beyond local-only use.

## Flaky-Test And Slow-Test Backlog

| Item | Evidence | Recommendation |
| --- | --- | --- |
| TestClient deprecation warning | Full suite emits Starlette/httpx warning. | Pin/upgrade compatible FastAPI/Starlette/httpx stack or track `httpx2` migration. Make warnings fail after this is resolved. |
| No slow markers | Only parametrization marks were found; no `slow`, `postgres`, `browser`, or `performance` markers. | Add pytest markers and split fast PR tests from nightly/release tests. |
| Time-sensitive tests | Calendar/session and lifecycle tests use dates/time semantics. | Centralize frozen clock helpers and require explicit timezone/date fixtures. |
| Thread/worker timing | App lifespan tests use short waits (`timeout=1/2`). | Mark as timing-sensitive and isolate from overloaded CI workers. |
| Fake DB overuse | 54 files use `FakeDb`. | Keep fakes for unit tests, but mirror critical fakes with real PostgreSQL contract tests. |
| Performance tests as unit tests | CERI/setup lifecycle performance tests use synthetic/fake paths. | Move large real DB performance tests behind `performance` marker. |

## Property, Mutation, And Fault-Injection Backlog

Property-based tests to add:

- CSV parsing and column alias normalization: random header casing, whitespace, missing values, duplicate columns.
- Numeric/date parsers: locale-like formatting, sentinel values, invalid dates, future dates.
- Technical state/gate monotonicity: worsening risk cannot improve final actionability.
- Lifecycle state machine: terminal states never reopen; repeated same evidence is idempotent.
- Background jobs: enqueue/claim/retry/cancel sequences preserve terminal-state rules.
- Winner probability: future evidence is excluded across generated cutoff/order combinations.
- CERI dedup/correction: later correction never changes `AS_KNOWN` historical output.

Mutation/fault injection candidates:

- Flip earnings gate comparisons and verify combined-decision tests fail.
- Swap market risk-on/risk-off mapping and verify regime/technical tests fail.
- Remove evidence cutoff filters and verify winner/CERI leakage tests fail.
- Disable lease-token checks and verify concurrency tests fail.
- Remove redaction/masking and verify export/provider tests fail.
- Change golden fixture expected values and require explicit review label.

## Golden-Data Governance Policy

Golden fixtures currently exist for pipeline and ranking behavior (`tests/fixtures/golden_pipeline.json`, `tests/fixtures/ranking_profiles_golden.json`) and README already says golden scoring changes must be reviewed.

Policy:

- Golden fixture updates require a PR label such as `golden-update`.
- PR description must include before/after output, reason for intentional change, and affected config/model versions.
- A reviewer must inspect both fixture data and code/config changes.
- CI should fail if fixture files change without the required label.
- Golden tests should never be updated in the same commit as broad refactors unless the behavioral reason is isolated and documented.
- Store generated comparison artifacts for release candidates so future reviewers can see what changed.

## Exit Criteria Assessment

Phase 18 review is complete, but exit criteria are not fully met.

Met:

- S0/S1 invariants are identified.
- Existing local gates are healthy: ruff passes and all 943 tests pass.
- The suite has strong unit, fake-DB, contract, schema, quantitative, and route coverage.

Not yet met:

- No checked-in CI workflow currently enforces the gates.
- Coverage and branch coverage were not measured because coverage tooling is not installed.
- Migration behavior is not tested against clean PostgreSQL in CI.
- Offline migration SQL generation currently fails.
- Real PostgreSQL concurrency, constraint, JSONB, and partial-index behavior is not covered.
- Browser-level end-to-end smoke tests are absent.
