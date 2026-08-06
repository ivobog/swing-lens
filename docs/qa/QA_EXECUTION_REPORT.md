# SwingLens QA Execution Report

Execution date: 2026-08-06 (Europe/Zurich)  
QA verdict: **CONDITIONAL PASS**  
Recommended release decision: **GO only after the remaining manual P0/P1 checks are signed off**

## 1. Executive Verdict

All feasible deterministic automation is green after one release-blocking backup/restore defect was
fixed. No open S0 or S1 defect remains. Golden/scoring, evidence immutability, future-data leakage,
feature isolation, advisory non-mutation, destructive confirmations, secret redaction, migration,
restore validation, and the no-order safety boundary passed their implemented automated controls.

The verdict is conditional because no live IB paper session, Microsoft Edge/screen-reader review,
licensed CERI provider certification, populated multi-module restore, or long-running 250/1,000-
ticker resilience soak was available in this execution. These are reported as blocked/manual, not
as passes.

## 2. Repository and Environment

| Item | Value |
| --- | --- |
| Repository | `ivobog/swing-lens` |
| Branch | `codex/qa-implementation` |
| Baseline commit | `de5c78cdb91f4fca98f3c3eaf0cd303583d7dac6` |
| Code candidate tested | `b459c40` plus documentation-only working changes |
| Application version | `0.1.0` |
| OS | Windows 10 Home 2009, build 19045 |
| Python | CPython 3.12.2 in `.venv` |
| PostgreSQL | 18.3 local Windows server |
| Migration head | `0026_technical_artifact_cache` |
| Dependency state | `uv.lock` synchronized frozen; QA additions locked |
| Browser engines | Playwright Chromium 151.0.7922.34 and Firefox 153.0 |

The QA plan names Windows 11 and PostgreSQL 16 as the primary baseline. This machine used Windows
10 and PostgreSQL 18.3. CI is configured with PostgreSQL 16 and Python 3.12, but that remote workflow
was not dispatched from this local execution.

Default flags observed: durable pipeline and worker enabled; technical rollout, prewarm, OWPE, SLSE,
and CERI feature groups disabled. Automated settings/config suites exercised enabled and disabled
states without modifying `.env` or using secrets.

## 3. Commands and Exact Outcomes

| Command / operation | Outcome |
| --- | --- |
| `git switch -c codex/qa-implementation origin/main` | PASS; clean branch created from `main` |
| `uv sync --frozen --extra dev` | PASS; exact lock installed; later QA tooling re-locked and frozen sync passed |
| `uv run ruff check app tests scripts` | PASS; `All checks passed!` |
| `uv run python scripts/docs/check_route_inventory.py` | PASS; exit 0, no drift |
| Untouched `uv run pytest -q` | PASS; 1,086 passed, 4 warnings in 204.20 s |
| Phase 1 `uv run pytest -q` | PASS; 1,096 passed, 4 warnings in 126.14 s |
| Final complete `uv run pytest -q` | PASS; 1,104 passed, 4 warnings in 102.34 s |
| `uv run pytest tests/qa/test_qa_infrastructure.py -q` | PASS; 7 passed in 0.78 s |
| Focused upload/recalculation tests | PASS; 28 passed in 1.00 s |
| Chromium + Firefox E2E | PASS; 6 passed in 39.21 s |
| Secret scanner tests and scanner CLI | PASS; 2 passed; zero tracked credential-shaped findings |
| Backup/restore regression and validator tests | PASS; 6 passed in 1.29 s |
| `uv run pytest -q -m performance` | PASS; 21 passed, 1,083 deselected in 5.33 s |
| Coverage regression excluding E2E/external | PASS; 1,098 passed, 6 deselected, 4 warnings in 188.63 s |
| Coverage report | PASS; 82.9% branch-aware total; XML written |
| Focused golden pipeline and ranking profiles | PASS; 3 passed in 0.34 s |
| Clean disposable Alembic upgrade | PASS; all 26 revisions applied to `0026` |
| Disposable downgrade/re-upgrade | PASS; `0026 -> 0025 -> 0026` |
| Custom backup→clean restore→validator | PASS after DEF-001; `passed: true`, no missing tables/FK violations/blank hashes |
| `uv run playwright install chromium firefox` | PASS; both pinned engines installed |

Four non-failing warnings remain: one Starlette TestClient/httpx deprecation, one Python 3.12 SQLite
datetime adapter deprecation, and two Alembic `path_separator` deprecations.

## 4. Coverage by Product Area

Overall measured application coverage is 82.9% with branch coverage enabled. The area figures below
are line-oriented groupings derived from `coverage.xml`; they are diagnostic, not release thresholds.

| Area | Covered / measured lines | Line coverage |
| --- | ---: | ---: |
| Upload and CSV | 375 / 421 | 89.1% |
| Fundamental scoring | 726 / 794 | 91.4% |
| IB market data | 985 / 1,277 | 77.1% |
| Technical scoring | 2,099 / 2,292 | 91.6% |
| Combined decisions and ranking | 893 / 981 | 91.0% |
| Jobs and pipeline | 931 / 1,048 | 88.8% |
| Market regime | 726 / 808 | 89.9% |
| Sector rotation | 1,483 / 1,624 | 91.3% |
| Setup Lifecycle | 3,133 / 3,868 | 81.0% |
| Winner Probability | 2,879 / 3,502 | 82.2% |
| CERI | 3,916 / 4,584 | 85.4% |
| Core and other | 3,261 / 3,526 | 92.5% |

Primary residual code-coverage risk is in DB-backed query/repository and advanced API route branches,
especially Setup Lifecycle query/repository and Winner Probability API/repository paths. Scoring,
state engines, safety boundaries, and deterministic model contracts have stronger coverage.

## 5. Tests and Infrastructure Added or Changed

- Added centralized deterministic QA fixtures and automatic `unit`, `integration`, `e2e`, `slow`,
  `destructive`, `external`, `security`, and `performance` classification.
- Added safely scoped disposable PostgreSQL support and temporary upload/export/cache settings.
- Added deterministic CSV, OHLCV, fixed-clock, HTTP client, and read-only scripted IB factories.
- Added real Chromium/Firefox tests for responsive keyboard navigation, upload/run creation, repeated
  upload behavior, Unicode input, and research-only settings.
- Added Unicode and unsupported-encoding upload regressions.
- Strengthened fundamental recalculation coverage to assert raw JSON immutability.
- Added PostgreSQL client URL conversion regressions for backup/restore scripts.
- Added a tracked-file credential-shape scanner and tests.
- Added coverage configuration, JUnit/coverage artifacts, CI browser lane, golden gate, PostgreSQL
  migration gate, nightly performance lane, and artifact upload.

## 6. Defects Found and Fixed

### DEF-001 — S1 — Backup/restore runbooks mis-handle documented database URL

- Affected: QO-08, F-15, E2E-019, R-09; migration/restore release gate.
- Environment: Windows 10, PostgreSQL/client 18.3, Python 3.12.2, commit `5ae9ce6`.
- Reproduction: migrate a disposable source DB, then call `backup_postgres.ps1 -DatabaseUrl` with
  the documented `postgresql+psycopg://...` URL.
- Expected: `pg_dump` connects to the named source DB and creates a custom-format backup.
- Actual: libpq misparsed the SQLAlchemy driver-qualified URL and attempted localhost as user
  `Ivica`; authentication failed before backup creation.
- Evidence: `pg_dump: ... password authentication failed for user "Ivica"`.
- Root cause: PostgreSQL client tools require `postgresql://`; SQLAlchemy accepts
  `postgresql+psycopg://`.
- Fix: shared `PostgresUrl.psm1` removes only the SQLAlchemy driver qualifier before invoking
  `pg_dump`, `pg_restore`, or `psql`; the original URL remains available to SQLAlchemy validation
  and redacted metadata.
- Regression: `tests/ops/test_postgres_url.py` covers psycopg, psycopg2, and native URLs and verifies
  both runbooks use the normalized value.
- Retest: real migrate→backup→restore→validator passed; validator found all critical tables, zero FK
  violations, zero blank/null hashes, and the expected Alembic head.
- Remaining risk: the executed restored database had a clean migrated schema with zero domain rows;
  M-06 remains for a populated multi-module evidence set.

No other product defect was confirmed. A first Firefox smoke failure was traced to a new test's
incorrect empty-database assumption after Chromium created a run; the test was corrected to assert
the stable UI contract and is not counted as a product defect.

## 7. Blocked and Manual Verification

| Item | State | Exact reason |
| --- | --- | --- |
| Live IB paper validation | BLOCKED | No approved paper Gateway session/credentials or entitlement set was supplied |
| Licensed CERI provider | BLOCKED | No licensed adapter or approved test credential exists in scope |
| Microsoft Edge smoke | MANUAL | Playwright Chromium/Firefox ran; Edge-specific binary/visual review not executed |
| Screen-reader/contrast review | MANUAL | Requires Narrator/NVDA and human judgment |
| Python 3.13/3.14 compatibility | BLOCKED | Only project Python 3.12.2 was installed/executed |
| PostgreSQL 16 local execution | PARTIAL | Local server is 18.3; CI service is configured for 16 but not remotely run here |
| Populated multi-module restore | MANUAL | Clean schema restore passed; representative live evidence set not seeded |
| 50/250/1,000 full pipeline + eight-hour soak | MANUAL | No long-running monitored disposable environment was executed |
| Real process/worker/PostgreSQL restart drill | MANUAL | Deterministic fault/lease tests passed; external process restart procedure remains |

## 8. Performance Observations

The performance lane passed 21 repeatable checks. It includes structured export 413 behavior,
cleanup safety, deterministic p50/p95 instrumentation, a 1,000-ticker SLSE identity workload under
its 1.0 s local budget, and a 500-row CERI export under its 2.0 s budget. The full non-browser suite
with coverage took 188.63 s; the final uninstrumented full suite took 102.34 s. These measurements
are specific to the recorded machine and are not universal guarantees.

## 9. Security and Safety Verdict

**PASS for implemented automated controls.** Localhost defaults, trusted hosts, unsafe-route
classification, CSRF/local-admin checks, disabled feature gates, filename/path hardening, template
escaping, CSV formula mitigation, provider/export redaction, error redaction, purge confirmations,
tracked-secret scanning, and audit contracts passed.

The no-order boundary passed static source scanning, unsafe route inventory, read-only IB connection
assertions, and an IB spy that fails on order-method access. No route, UI action, or service path was
found that places, modifies, routes, or cancels an order.

## 10. Migration and Restore Verdict

**PASS with one residual manual depth check.** Clean upgrade, current revision, one-step downgrade,
re-upgrade, custom backup, clean restore, table inventory, FK checks, and evidence/hash validation
passed on disposable databases. DEF-001 was fixed. A populated multi-module restore remains M-06.

## 11. Feature-Flag Verdict

**PASS for automated configuration and route/service isolation.** Defaults are off for technical
rollouts, prewarm, OWPE, SLSE, and CERI. Existing config, route, job, pipeline, acceptance, and admin
suites exercise staged on/off combinations and assert no disabled writes/exposure. Live licensed
CERI behavior remains blocked by provider availability.

## 12. Files Changed

- `.github/workflows/ci.yml`
- `.gitignore`
- `pyproject.toml`, `uv.lock`
- `tests/conftest.py`
- `tests/e2e/conftest.py`, `tests/e2e/test_browser_smoke.py`
- `tests/qa/test_qa_infrastructure.py`, `tests/qa/test_secret_scan.py`
- `tests/test_csv_upload_services.py`, `tests/test_run_actions_phase3.py`
- `tests/ops/test_postgres_url.py`
- `scripts/qa/scan_tracked_secrets.py`
- `scripts/ops/PostgresUrl.psm1`, `backup_postgres.ps1`, `restore_postgres.ps1`
- `docs/qa/README.md`, `QA_EXECUTION_MATRIX.md`, `QA_EXECUTION_REPORT.md`,
  `MANUAL_TEST_PROCEDURES.md`, `LIVE_IB_PAPER_VALIDATION.md`,
  `PERFORMANCE_BASELINE.md`, `RELEASE_QA_CHECKLIST.md`

## 13. Commit List

- `ec98f17 test(qa): establish deterministic integration fixtures`
- `b7d0c96 test(upload): cover raw evidence and encoding behavior`
- `5ae9ce6 ci(qa): enforce coverage browser and secret gates`
- `b459c40 fix(ops): normalize database URLs for backup restore tools`
- Documentation evidence commit: recorded in repository history after this report is committed.

## 14. Residual Risks and Release Decision

Residual risk is environmental rather than an observed deterministic product failure: live IB
entitlements/pacing, Edge/assistive visual behavior, licensed provider policy, populated restore
fidelity, long-running resource behavior, Python 3.13/3.14, and local PostgreSQL 16 execution.

Recommendation: **CONDITIONAL GO** for continued local research validation; **do not issue an
unconditional release sign-off** until the required manual checks in `RELEASE_QA_CHECKLIST.md` are
completed and accepted. Any future golden drift, leakage, raw-evidence mutation, restore failure,
advisory score mutation, destructive guard bypass, duplicate durable evidence, or broker-order path
is an immediate NO-GO.
