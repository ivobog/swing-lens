# SwingLens Phase 1 Requirements and Safety Traceability

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 1 verifies that implementation behavior can be traced to explicit product and safety
requirements, with special attention to the decision-support-only boundary, incomplete-data
visibility, and administrative/state-changing operations.

## Evidence Log

Inspected requirement and design sources:

- `README.md`
- `docs/srs.md`
- `docs/sdd.md`
- `docs/vision.md`
- `docs/market_regime_command_center.md`
- `docs/sector_rotation_dashboard.md`
- `docs/setup_lifecycle_signal_change_engine.md`
- `docs/ceri.md`
- execution plans and release notes by targeted grep
- `app/settings.py`
- router modules under `app/routers`
- service modules under `app/services`
- SQLAlchemy metadata from `app.models.tables` and `app.models.ceri_tables`
- tests under `tests`

Targeted command evidence:

| Command | Result | Notes |
|---|---:|---|
| `rg --glob '*.py' "placeOrder|submit_order|cancel_order|modify_order|broker_order|reqOpenOrders|openOrder|whatIfOrder|ib\.placeOrder|Order\(" app tests` | Passed | No application Python matches; matches only in test sentinel strings |
| `pytest tests/ceri/test_ceri_routes_admin.py tests/winner_probability/test_routes_admin.py tests/setup_lifecycle/test_setup_lifecycle_acceptance_fixture.py tests/test_ib_services.py -q` | Passed | `34 passed in 7.29s` |

## Requirements Catalogue

| ID | Requirement | Source | Implementation evidence | Test evidence | Status |
|---|---|---|---|---|---|
| RQ-001 | App runs locally and binds to localhost by default | README, SRS NFR-001/NFR-008, SDD security | `Settings.app_host="127.0.0.1"` | `tests/test_settings.py`, route tests instantiate local app | Traced |
| RQ-002 | Uploaded CSV is the only MVP fundamental data source | README, SRS constraints, vision | `upload_routes`, `upload_service`, `csv_loader`, `column_mapper` | CSV/upload/mapping/fundamental tests | Traced |
| RQ-003 | Raw uploaded rows are preserved | README, SRS FR-003/DR-002 | `raw_company_rows.raw_json`, upload services | `tests/test_csv_upload_services.py`, `tests/test_upload_service_v2.py` | Traced |
| RQ-004 | Fundamental scores are deterministic and explainable | SRS FR-005 to FR-008 | fundamental services and `fundamental_scores` table | fundamental ranker/component/coverage tests | Traced |
| RQ-005 | IB integration reads contract metadata and OHLCV only | README, SRS FR-009 to FR-014 | `ib_routes`, `ib_connection`, `ib_contract_resolver`, `ib_data_fetcher`, `price_bars` | IB service/rate/fetch tests | Traced, with boundary gap below |
| RQ-006 | Technical calculations reproduce Pine-style indicators and classifications | README, SRS FR-015 to FR-028 | technical indicator/scoring/Pine replica services and `technical_scores` | technical, Pine, confidence, stage, warning tests | Traced |
| RQ-007 | Missing or insufficient technical data remains visible and does not erase fundamentals | README cockpit workflow, SRS FR-029/ER-004 | warning flags, confidence services, combined result fields | warning-flag, technical-confidence, integration tests | Traced |
| RQ-008 | Combined decisions merge fundamentals and technicals without dropping uploaded tickers | SRS FR-029 to FR-031 | `combined_decision`, `combined_results` | combined decision/golden pipeline tests | Traced |
| RQ-009 | Market regime is advisory and does not mutate ranking scores/order | market-regime doc | `score_threshold_adjustments_enabled=False`, market context in run/ranking routes | `tests/test_run_detail_view_models.py`, `tests/test_ranking_profile_routes.py` | Traced |
| RQ-010 | Sector rotation is advisory and does not mutate ticker ranking scores/order | sector-rotation doc | sector services persist separate snapshots/rows | sector service/repository/export/route tests | Traced |
| RQ-011 | Setup lifecycle uses completed daily evidence, forward-only by default | setup-lifecycle doc | setup lifecycle config, source loader, canonicalization, repositories | setup-lifecycle config/source/canonicalization tests | Traced |
| RQ-012 | Setup lifecycle replay and purge are non-authoritative/disabled by default | setup-lifecycle doc | config validation rejects authoritative replay and enabled purge | `tests/setup_lifecycle/test_setup_lifecycle_config.py` | Traced |
| RQ-013 | Winner probability preserves decision-time estimates and exposes insufficient evidence | winner-probability release/docs/tests | winner probability services and tables | winner-probability route, estimator, evidence tests | Traced |
| RQ-014 | CERI providers are gated, redacted, and do not block core SwingLens | `docs/ceri.md` | CERI config/provider/export/purge services | CERI provider, export, admin, acceptance tests | Traced |
| RQ-015 | Broker order placement/modification/cancellation is out of bounds | README, SRS NFR-002, SDD, feature docs | No app Python sentinel matches; `/ib/status` reports `order_endpoints: false` | targeted safety tests passed | Partially traced; needs repo-wide guard |

## Subsystem Trace Map

| Subsystem | Inputs | Outputs | Persistence | Routes/UI | Tests |
|---|---|---|---|---|---|
| CSV ingestion | Browser CSV upload, column aliases | upload run, raw rows, mapped fields, validation errors | `upload_runs`, `raw_company_rows` | `/`, `POST /uploads`, run mapping/history pages | upload, CSV, column mapping, dashboard tests |
| Fundamentals | mapped/raw company rows, scoring config | scores, labels, trap/warning flags, explanations | `fundamental_scores` | run detail, exports | fundamental ranker/component/coverage tests |
| IB market data | settings, IB Gateway/TWS, tickers, benchmarks | contracts, fetch plans/runs/items, price bars | `ib_contracts`, `ib_fetch_runs`, `ib_fetch_items`, `price_bars` | `/ib/*`, `/runs/{run_id}/ib/*` | IB services, fetch plan/job/executor, rate limiter tests |
| Technical scoring | price bars, benchmarks, Pine/technical config | technical scores, classifications, warnings, debug JSON | `technical_scores` | run detail, ticker chart panel, exports | indicators, Pine replica, confidence, v4, stage tests |
| Combined/ranking | raw rows, fundamental scores, technical scores, ranking profiles | combined results, ranking profile results, exports | `combined_results`, `ranking_results` | run detail, ranking profile routes, exports | combined, ranking, golden pipeline tests |
| Market regime | SPY/QQQ price bars, run universe | advisory snapshot, policy context, warnings | `market_regime_snapshots` | `/market-regime`, run market-regime routes, APIs, exports | market regime service/policy/repository/route/export tests |
| Sector rotation | run raw rows, technical/combined/ranking rows, optional ETF data | run-scoped sector snapshot/rows, advisory permissions | `sector_rotation_snapshots`, `sector_rotation_rows` | run sector dashboard/drilldown/API/export | sector taxonomy/universe/service/policy/repository/export/route tests |
| Setup lifecycle | completed daily run evidence, technical/context rows, config | immutable snapshots, episodes, events, alerts, replay output | setup lifecycle and signal tables | setup lifecycle dashboards/APIs/exports | setup-lifecycle package tests |
| Winner probability | decision-time run evidence, lifecycle/context features, outcomes | estimates, cohorts, models, calibration/drift/evidence | winner probability tables | winner probability dashboards/APIs/admin routes | winner-probability package tests |
| CERI | manual/provider records, estimates/guidance/earnings/catalysts | normalized source records, revisions, events, features, scores, alerts | CERI tables | CERI dashboard/API/admin/export/purge | CERI package tests |
| Jobs/pipeline | upload run, feature flags, queued background jobs | pipeline state, job state, result JSON | `background_jobs`, `pipeline_runs`, `pipeline_steps` | pipeline progress/cancel routes | background and pipeline tests |

## Safety Boundary Verification

Confirmed evidence:

- Requirement docs repeatedly state that SwingLens is local research/decision support only and must
  never place, modify, cancel, or route broker orders.
- A targeted Python scan found no application code references to common order-capable IB/order
  sentinels. Matches were limited to safety tests.
- `tests/test_ib_services.py` asserts `/ib/status` returns `order_endpoints` as `False`.
- `tests/setup_lifecycle/test_setup_lifecycle_acceptance_fixture.py` scans setup-lifecycle routes
  and services for forbidden order fragments.
- CERI admin routes require `ceri_admin_enabled`, localhost, and a CSRF token.
- Winner-probability admin routes require `winner_probability_admin_enabled` and localhost.

Residual gaps:

- The no-order scan is not yet a repository-wide test across all application Python, templates,
  JavaScript, and future files.
- Winner-probability admin routes do not enforce CSRF, unlike CERI.
- Several state-changing routes outside CERI/winner-probability have no explicit local-admin or CSRF
  gate. See findings PH1-001 and PH1-002.

## Ambiguous Product Terms

Terms requiring a glossary or exact semantic contract before later phases:

- `Strong candidate`
- `Candidate`
- `Watch`
- `Wait for pullback`
- `Breakout candidate`
- `High risk / reduced size`
- `Avoid`
- `Low priority`
- `Clean compounder`
- `High-quality quant`
- `Mixed but interesting`
- `Value trap risk`
- `Growth trap risk`
- `Prime clean pullback`
- `Fresh breakout`
- `Momentum continuation`
- `Overheated momentum`
- `confidence`, including `high`, `normal`, `low`, `error`, and `insufficient`
- `fresh`, especially for breakouts, revisions, estimates, CERI data, and market data
- `complete`, `incomplete`, `ready`, `actionable`, `low-confidence`, `stale`
- `position-size hint`, `full starter`, `reduced size`, `watch only`, `avoid_new_longs`
- `decision-time`, `latest`, `as-known`, `latest-corrected`, `authoritative`, `reconstructed`

## Assumptions Identified

- Market scope is US stocks only in the MVP.
- Fundamental data comes from uploaded CSV only.
- IB provides market data and contract metadata, not order actions.
- SPY and QQQ are the default benchmark/risk proxy set.
- Market regime and sector rotation use uploaded run universe metrics, not full-market breadth.
- Setup lifecycle authoritative triggers are completed daily sessions, not intraday crosses.
- CERI primary-provider behavior depends on licensed adapter implementation and environment-only credentials.
- Local-only operation is relied on heavily for security, while not all state-changing routes enforce a shared local-admin control.

## Findings Register

ID: PH1-001
Title: Non-CERI/non-winner state-changing routes lack explicit local-admin and CSRF controls
Severity: S1 High
Confidence: Confirmed
Affected components: `app/routers/run_routes.py`, `app/routers/market_regime_routes.py`,
`app/routers/sector_rotation_routes.py`, `app/routers/setup_lifecycle_routes.py`
Evidence: Direct route reads show POST handlers for fundamentals/technicals/pipeline/cancel,
market-regime recalculation, sector-rotation recalculation, setup-lifecycle alert
acknowledge/dismiss, evaluation, and replay commit or enqueue work without a `Request` local-admin
guard or CSRF token validation.
Reproduction steps: Inspect the POST handlers around `run_routes.py:560`,
`run_routes.py:578`, `run_routes.py:596`, `run_routes.py:698`,
`market_regime_routes.py:122`, `sector_rotation_routes.py:110`, and
`setup_lifecycle_routes.py:357-439`.
Expected behavior: Admin/replay/reconstruction/purge/backfill/recalculation/cancel operations have
explicit local-only authorization, CSRF protection for browser-reachable POSTs, and tests.
Observed behavior: CERI has that pattern; these state-changing routes do not.
Impact: If the local web server is reachable from a browser context or accidentally bound beyond
localhost, state can be mutated or background jobs enqueued without an explicit admin barrier.
Root cause or likely cause: Older MVP local-only routes predate the later CERI local-admin pattern.
Recommended remediation: Introduce a shared local-admin/CSRF dependency and apply it consistently
to all state-changing routes according to a route matrix. Keep upload/process routes intentionally
allowed only if documented.
Acceptance criteria: Every POST route is classified as public-local workflow, admin-local action, or
internal callback; admin-local actions enforce localhost, feature flag where applicable, and CSRF;
tests cover forbidden and allowed cases.
Regression tests required: Route matrix tests for all 38 POST routes.
Owner profile: Backend/security engineer
Dependencies: Decide UX for CSRF token propagation in Jinja/HTMX forms.

ID: PH1-002
Title: Winner-probability admin routes lack CSRF protection
Severity: S2 Medium
Confidence: Confirmed
Affected components: `app/routers/winner_probability_routes.py`
Evidence: `queue_winner_prediction_capture`, outcome processing, cohort refresh, and model retire
call `_require_local_admin`; `_require_local_admin` checks feature flag and localhost but not CSRF.
CERI's analogous guard checks `x-csrf-token` or `csrf_token`.
Reproduction steps: Inspect `winner_probability_routes.py:527-643` and compare with
`ceri_routes.py:949-970`.
Expected behavior: Browser-reachable admin POSTs use a consistent local-admin plus CSRF model.
Observed behavior: Localhost gating exists, but CSRF does not.
Impact: A browser-origin request from the local machine could trigger admin jobs if the feature flag
is enabled.
Root cause or likely cause: Winner-probability admin routes implemented before the stricter CERI
CSRF convention.
Recommended remediation: Add CSRF validation to winner-probability admin routes and tests mirroring
CERI admin tests.
Acceptance criteria: Missing CSRF returns 403 when admin is enabled; valid token succeeds from
localhost; non-local host remains forbidden.
Regression tests required: Update `tests/winner_probability/test_routes_admin.py`.
Owner profile: Backend/security engineer
Dependencies: Shared CSRF/local-admin helper from PH1-001.

ID: PH1-003
Title: No-order boundary is not yet protected repository-wide
Severity: S1 High
Confidence: Strong
Affected components: Safety boundary, IB integration, advanced engines
Evidence: Targeted scans and tests cover parts of the app, but the existing setup-lifecycle
acceptance test scans only setup-lifecycle routes/services; `/ib/status` asserts only metadata.
Reproduction steps: Inspect `tests/setup_lifecycle/test_setup_lifecycle_acceptance_fixture.py`
around the no-order test and `tests/test_ib_services.py` around `/ib/status`.
Expected behavior: A repository-wide regression test fails on order-capable API calls, route paths,
templates, and UI command copy.
Observed behavior: Partial safety tests exist, but future order-capable code outside SLSE could
escape these tests.
Impact: The central product boundary could regress without automated detection.
Root cause or likely cause: Boundary tests were added feature-by-feature instead of centralized.
Recommended remediation: Add a single repository-wide safety test with explicit allowlist for
documentation and sentinel test strings.
Acceptance criteria: Test scans `app/**/*.py`, `app/templates/**/*.html`, and `app/static` for
order-capable API calls/routes/UI commands.
Regression tests required: New `tests/test_no_order_boundary.py`.
Owner profile: Backend engineer
Dependencies: Agree forbidden/allowed terms so ordinary ranking "order" usage is not noisy.

ID: PH1-004
Title: Product labels and confidence terms lack a single semantic glossary
Severity: S2 Medium
Confidence: Confirmed
Affected components: Requirements, scoring engines, UI, exports
Evidence: Labels such as `Strong candidate`, `High risk / reduced size`, `Fresh breakout`,
`Low Confidence`, `ready`, `actionable`, `fresh`, and `complete` appear across SRS, feature docs,
templates, and config without one canonical glossary tying thresholds, persistence fields, UI labels,
and exports together.
Reproduction steps: Search docs/templates/config for the terms listed in the ambiguous product terms
section.
Expected behavior: Each high-impact label has exact inputs, thresholds, blocking conditions, and
user-facing/export semantics.
Observed behavior: Semantics are distributed across configs, service logic, docs, and UI labels.
Impact: Users and maintainers may interpret advisory labels as stronger or weaker than the engine
actually means; later remediation may change labels without testable traceability.
Root cause or likely cause: Rapid subsystem growth without a central domain glossary.
Recommended remediation: Add `docs/glossary.md` or a requirements appendix, and reference config
keys/tests for each label family.
Acceptance criteria: Every critical label has definition, source module, persistence field, route/UI
surface, and regression test.
Regression tests required: Snapshot/export label consistency tests where labels cross UI/API/CSV.
Owner profile: Product/backend owner
Dependencies: Finalize desired user semantics.

ID: PH1-005
Title: XLSX export remains in requirements but current documented exports are CSV/JSON/Markdown
Severity: S3 Low
Confidence: Strong
Affected components: Requirements and export documentation
Evidence: SRS FR-038 requires XLSX export; README current export list contains CSV/JSON/Markdown
routes. Repository service inventory found CSV/JSON/Markdown export services, but no current
`xlsx_exporter.py`.
Reproduction steps: Inspect SRS FR-038, README export list, and `app/services/*export*`.
Expected behavior: Either XLSX export is implemented and documented, or the MVP requirement is
updated/deferred.
Observed behavior: Requirement and implementation/documentation appear out of sync.
Impact: Acceptance criteria can be read as unmet even though current product behavior may have moved
to CSV/JSON/Markdown.
Root cause or likely cause: MVP requirements document is older than current export implementation.
Recommended remediation: Update SRS/README to mark XLSX as deferred or add XLSX export support.
Acceptance criteria: Export requirement matches current product and tests.
Regression tests required: If implemented, workbook sheet/header tests; if deferred, documentation
review only.
Owner profile: Product owner / backend engineer
Dependencies: Product decision on XLSX.

## Action Backlog

Immediate:

- Create a POST route matrix and classify all 38 POST routes by authorization and CSRF requirement.
- Add a shared local-admin/CSRF helper and apply it to admin-classified routes.
- Add repository-wide no-order-boundary tests.

Near term:

- Add CSRF checks to winner-probability admin routes.
- Add route tests for setup-lifecycle evaluate/replay and alert state changes.
- Add a domain glossary for decisions, confidence, data quality, actionability, and freshness.

Structural:

- Build a requirements traceability matrix maintained with each subsystem release.
- Add export/UI/API consistency tests for label, warning, and incomplete-data semantics.
- Update SRS/SDD to distinguish original MVP requirements from current implemented scope.

## Test Additions Proposal

- `tests/test_no_order_boundary.py`: repository-wide scan for forbidden order APIs/routes/UI copy.
- `tests/test_state_changing_route_controls.py`: route matrix enforcement for POST endpoints.
- Winner-probability CSRF tests matching CERI admin tests.
- Setup-lifecycle replay/evaluate authorization tests.
- Export parity tests proving warning/confidence labels are consistent across HTML, JSON, CSV, and
  Markdown where applicable.

## Decision Records Needed

- DR-PH1-001: Decide whether all recalculation/pipeline/cancel routes are admin-only or ordinary
  local workflow actions.
- DR-PH1-002: Decide whether XLSX remains an MVP acceptance requirement.
- DR-PH1-003: Define approved wording for advisory research labels versus trading instructions.

## Phase Scorecard

| Dimension | Rating | Rationale |
|---|---|---|
| Requirements coverage | Amber | Major subsystems trace to docs/tests, but current docs are split across SRS, SDD, feature docs, and execution plans |
| Decision-support boundary | Amber | No app order-code evidence found, but guard is not repository-wide |
| Admin operation traceability | Red | Multiple state-changing routes lack explicit local-admin/CSRF controls |
| Incomplete/stale/confidence visibility | Green | Many services/templates/tests expose warnings, insufficient states, and confidence |
| Advanced-engine advisory boundaries | Amber | Market/sector/CERI/SLSE docs are explicit; tests exist, but central glossary is missing |
| Export requirements consistency | Amber | Current export surface and old XLSX requirement diverge |

## Exit Report

Passed checks:

- Requirement sources were catalogued and mapped to implementation/test surfaces.
- No application Python order-capable sentinel matches were found.
- Targeted safety/admin tests passed: `34 passed`.
- CERI local-admin and CSRF pattern was verified in code and tests.
- Winner-probability local-admin gating was verified.
- Major incomplete-data and warning-state surfaces were identified across code/tests.

Failed checks:

- State-changing route controls are inconsistent across subsystems.
- No-order boundary is not yet protected by a central repository-wide regression test.
- Product label semantics are not centralized.

Deferred items:

- Full UI/export semantic comparison.
- Full security review of all POST routes, host binding, CSRF, and local-admin behavior.
- Formal requirements traceability matrix with one row per SRS FR/NFR/ER requirement.

Phase 1 status: requirements are broadly traceable, but admin controls and centralized safety
regression need remediation before the boundary can be considered fully protected.
