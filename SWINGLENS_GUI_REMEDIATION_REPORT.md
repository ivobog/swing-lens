# SwingLens GUI Remediation Report

## 1. EXECUTIVE VERDICT

All 16 confirmed findings in `SWINGLENS_GUI_FORENSIC_REPORT.md` are remediated and directly recertified against the preserved Run 30 evidence. The changes keep UploadRun and PipelineRun authority separate, retain excluded CERI evidence without promoting it, preserve persisted combined-decision lineage, use run-scoped Setup relationships, and do not rewrite any Run 30 artifact.

Branch `codex/ceri-authority-remediation` and HEAD `c2dc012052544d9964c45171b66d35aa99f5ae0e` are unchanged. No commit or push was performed. Alembic is at the single repository/database head `0091_readiness_constraints`. The supported lifecycle status reports `OVERALL HEALTHY`.

The final focused remediation gate passed 161 tests. Exhaustive live recertification passed all 160 root-link targets, all 880 identity-bearing child pages, all 29 current Setup pages, and all 26 Run 30 Setup pages. A read-only before/after database comparison found no change to Run 30's pipeline, steps, CERI evidence/dispositions, Setup evidence, Winner evidence, background jobs, handoff manifest, or market contexts.

The full repository run produced 3,981 passes, 442 skips, 19 failures, and 24 setup errors. One remediation-caused failure—generated route inventory drift—was fixed and subsequently passed. The remaining 18 failures reproduce in pre-existing dirty-worktree areas (unrelated migrations/schema expectations, lifecycle launcher contract, PostgreSQL-only SQL exercised by SQLite, and authority/source-pin governance drift). All 24 setup errors are environmental: the disposable E2E database fixture attempted `postgres/postgres`, which the local PostgreSQL server rejected. These results are disclosed in Section 12.

## 2. FINDING REMEDIATION MATRIX

| Finding | Before | Root cause | Change | Files | Tests | After | Status |
|---|---|---|---|---|---|---|---|
| SL-GUI-001 | `/runs/30` showed UploadRun `COMPLETED` and hid PipelineRun 30 `PARTIAL` unless `?pipeline_id=30` was supplied. | The route resolved a pipeline only for an explicit ID. | Resolve the newest active execution-authority pipeline for the upload run by default; show separate Upload ingestion and Pipeline execution states and a correctly bound pipeline link. | `app/routers/run_routes.py`, `app/templates/run_detail.html` | Route/view-model tests; live `/runs/30`; link resolves `/runs/30/pipeline/30`. | Upload `COMPLETED` and Pipeline `PARTIAL` are simultaneously and unambiguously visible without query parameters. | PASS |
| SL-GUI-002 | 209 captured CERI objects appeared as no capture because only the eligible projection was counted. | Captured evidence and eligible/current projection were collapsed. | Added an authority-aware population service with captured, eligible, excluded, quarantined, state, and reason counts; wired Run/CERI/ticker views. | `app/services/ceri/evidence_population.py`, `app/services/ceri/evidence_eligibility.py`, CERI/run routers and templates | Service/route tests; Run 30 DB and browser checks; ABBV excluded-state page. | Run 30 shows 209 captured, 0 eligible/current, 209 excluded, 0 quarantined, reason `PARENT_PIPELINE_UNSUCCESSFUL`. | PASS |
| SL-GUI-003 | 64 later TechnicalScore values were labeled as if they fed the historical combined decision. | Display logic fell back to TechnicalScore when `CombinedResult.dual_score` was null. | Decision input now comes only from persisted combined data; later observations are separately labeled “Observed … retained but not used.” Active score data attributes remain blank for non-input rows. | `app/services/technical_display_fields.py`, run route/template | Null-combined/non-null-technical tests; browser pages counted 11 + 44 + 9 = 64 non-input rows. | All 64 are explicitly “No technical decision input / Does not feed Final / Decision”; no fallback changes decision lineage. | PASS |
| SL-GUI-004 | Setup admin metadata declared local-admin intent without complete executable CSRF enforcement or coherent browser transport. | Metadata was descriptive rather than independently enforced. | Central guard enforces `local_admin_required` and `csrf_required`; evaluation/replay also have explicit route guards; token is rendered and sent by JS. | `app/security.py`, Setup router/template/JS | Missing/invalid/remote/valid tests; live missing/invalid requests return structured 403; live non-persisting replay returns 200. | Both Setup mutations require loopback local-admin and valid CSRF, while legitimate local GUI replay remains usable. | PASS |
| SL-GUI-005 | Winner process/refresh mutations lacked CSRF enforcement and token transport. | Route policy and frontend transport were incomplete. | Both routes declare CSRF, call explicit defense-in-depth guards, and receive the page token through JS. | Winner router/template/JS, `app/security.py` | Security and Winner admin tests; live missing/invalid requests return 403; browser confirms both forms carry tokens. | Both Winner mutations enforce loopback local-admin plus CSRF. No live Winner mutation was executed against preserved evidence. | PASS |
| SL-GUI-006 | Run 30's Setup card used a global active-episode count. | Aggregate was not joined through the run's current canonical snapshots. | Added a shared run-summary service; active episodes are counted through `episode.current_snapshot_id -> snapshot.run_id`. | `app/services/setup_lifecycle/run_summary.py`, Setup query service, run route/template | Service/cross-screen tests; DB/browser count. | Run 30 shows 165 “active episodes represented by this run's current snapshots,” not the global 246. | PASS |
| SL-GUI-007 | “Changes” meant 305 events on one screen and 1,290 on another. | Canonical maintenance, lifecycle events, and signal changes shared an ambiguous label. | Shared contract separates canonical revisions, meaningful lifecycle changes, signal changes, and total meaningful changes. | Setup run-summary/query service and run template | Contract tests; exhaustive Run 30 Setup pagination; DB/browser checks. | Run 30 shows 209 canonical revisions, 96 lifecycle changes, 1,194 signal changes, and 1,290 meaningful changes. | PASS |
| SL-GUI-008 | A terminal job displayed its persisted terminal-stage evidence as “Active job stage.” | Static wording ignored terminal state. | Template and polling JS select “Last job stage” for terminal states while retaining the historical stage value. | `app/templates/pipeline_progress.html`, `app/static/app.js` | Pipeline GUI tests; live Pipeline 30 DOM check. | Pipeline 30 has no “Active job stage” fiction and retains `CAPTURING_WINNER_PREDICTIONS` as the last stage. | PASS |
| SL-GUI-009 | Normalization rendered impossible progress `909 / 618`. | Unknown-total processed work was added to a known-total numerator. | Progress model separates known-total processed/total from processed work in unknown-total jobs. | `app/services/pipeline_service.py`, pipeline template/JS | Service/template tests; live DOM check. | Normalization renders `618 / 618 known-total items` plus `291 processed across 6 job(s) with unknown totals`. | PASS |
| SL-GUI-010 | A reconnect badge carrying `hidden` remained visible because component display CSS won. | No authoritative global hidden rule. | Added `[hidden] { display: none !important; }`. | `app/static/app.css` | Static regression and computed-style scans on Run/Pipeline pages. | Zero elements with `hidden` were visibly rendered; reconnect warning remains hidden until needed. | PASS |
| SL-GUI-011 | Winner operations hid `UNAVAILABLE` and `FAILED` obligation populations. | Template rendered only fetch-required/satisfied categories. | Added unavailable and failed summary cards using backend counts. | `app/templates/winner_probability_operations.html` | Template tests; live operations page. | Live operations exposes Fetch Required 278, Satisfied 0, Unavailable 67, and Failed 1. | PASS |
| SL-GUI-012 | CERI advertised 52,547 stale records but exposed only the first 50. | Router used a hard limit with no offset controls. | Added validated offset/limit, range metadata, and previous/next navigation using the existing backend pagination contract. | CERI router/template | Route/template tests; live next-page click. | Browser navigated from rows 1–50 to 51–100 of 52,547 with next/previous controls. | PASS |
| SL-GUI-013 | A missing CERI ticker HTML route returned a successful 200 shell. | Domain status was flattened during template error handling. | Preserve the domain HTTP status; distinguish a truly absent ticker from captured-but-excluded evidence. | CERI router/template and evidence-population service | Route tests; live missing ticker and ABBV checks. | Missing ticker returns useful HTML with HTTP 404; ABBV captured-excluded remains a distinct HTTP 200 audit state. | PASS |
| SL-GUI-014 | Setup Replay submitted a native form contract incompatible with the query-parameter endpoint and navigated to raw responses. | Replay was not handled by the guarded async operations client. | Both Setup forms use one JS path that serializes the declared query fields, sends CSRF, and renders per-form success/error feedback. | Setup template/JS/router tests | Route/template tests; live dry replay for a nonexistent ticker/date range. | Browser sent ticker, date range, requester, reason, and false persistence exactly; received in-page `Replay completed: 0 item(s) read.` | PASS |
| SL-GUI-015 | Run detail was 2.57 MB/15.70 s cold; chart navigation averaged 5.52 s and eager per-row details inflated HTML/work. | 209 detail bodies were rendered eagerly; chart endpoints hydrated the full run; secondary tables and cockpit were unpaged; calendar holiday sets were repeatedly recomputed. | Added 100-row cockpit pages, bounded secondary previews, lazy detail endpoint/loading, direct per-ticker chart queries, non-duplicative chart-data assembly, and bounded immutable calendar caching. | Run route/template/JS, detail partial, calendar service, route inventory | Performance/view tests; live 3-sample timings; lazy request; exhaustive 209-chart crawl. | Run detail is 556,720 bytes and 4.688–5.426 s; chart shell 0.020–0.057 s; chart data 0.323–0.526 s; detail fragment 0.018–0.030 s. | PASS |
| SL-GUI-016 | Winner cells rendered literal Python `None`. | Null values were interpolated without semantic formatting. | Render null estimates/metadata as “Unavailable,” “Insufficient evidence,” or an em dash according to field meaning. | `app/templates/winner_probability_run.html` | Template tests; live 28-row DOM scan. | Run 30 Winner page contains zero literal `None` tokens and shows semantic insufficient-evidence text. | PASS |

## 3. SECURITY CERTIFICATION

- Central mutation authorization now checks local-admin and CSRF declarations independently before runtime mutation authority.
- Rejections are structured `403` responses with code `ADMIN_FORBIDDEN`.
- Setup evaluation and replay, Winner outcome processing, and Winner cohort refresh retain explicit route-level guard calls.
- Browser pages receive a server-issued token; JS sends it in `X-CSRF-Token`; comparison is constant-time.
- Negative proof: missing and invalid tokens returned 403 for all three live endpoint families without entering mutation code. Non-loopback rejection and valid-loopback acceptance are covered by focused tests.
- Positive browser proof used only a non-persisting Setup replay for a nonexistent ticker. Winner positive acceptance was proven with isolated test fixtures to avoid queuing work against Run 30.

## 4. RUN DETAIL AUTHORITY CERTIFICATION

- `/runs/30` automatically resolves PipelineRun 30 without `pipeline_id`.
- The hero separately labels `Upload ingestion COMPLETED` and `Pipeline execution PARTIAL`.
- The `PARTIAL` link is `/runs/30/pipeline/30`; the broken empty-identity URL is regression-tested.
- Run 30's upload status, pipeline status, 13 steps, execution-authority state, timestamps, IDs, and messages match the pre-remediation database capture.

## 5. CERI CERTIFICATION

- Run 30: 209 captured, 0 eligible/current, 209 excluded, 0 quarantined.
- All 209 effective dispositions remain `EXCLUDED`; all 209 reasons remain `PARENT_PIPELINE_UNSUCCESSFUL`.
- Current projection remains zero. No excluded record was promoted or rewritten.
- `/runs/30/ceri` and the Run card agree on population and reason.
- `/ceri/ticker/ABBV` exposes a captured-excluded audit state; a truly nonexistent ticker returns HTTP 404.
- Stale evidence is navigable beyond row 50.

## 6. SETUP LIFECYCLE CERTIFICATION

- Active episodes are explicitly defined as active episodes whose current canonical snapshot is owned by Run 30: 165.
- Canonical revisions: 209.
- Meaningful lifecycle changes: 96 (`EPISODE_OPENED`, `PHASE_TRANSITION`, `STATE_TRANSITION`); canonical maintenance is not included.
- Signal changes: 1,194.
- Total meaningful changes: 1,290.
- Exhaustive pagination rendered all 29 current pages/2,874 rows and all 26 Run 30 pages/1,290 rows with exact API identity/order and no mismatch.
- Replay contract preserved ticker, dates, requester, reason, persistence, and confirmation semantics; live validation used `persist=false`.

## 7. WINNER CERTIFICATION

- Run 30 still owns 28 prediction snapshots and 28 estimates; exhaustive child validation covered all 28 prediction IDs.
- Run 30 obligations remain 56 `FETCH_REQUIRED` and are unchanged from baseline.
- Operations now surfaces all four categories, including live global counts of 67 unavailable and 1 failed.
- Run 30's 28 rendered prediction rows contain no literal Python `None`; insufficient evidence is semantic and explicit.
- Winner mutations enforce local-admin plus CSRF. No live Winner queue/process mutation was executed.

## 8. PROGRESS/UI STATE CERTIFICATION

- Pipeline 30 is terminal `PARTIAL`; persisted progress stage is labeled “Last job stage.”
- Known and unknown progress are mathematically separated; no rendered numerator exceeds its denominator.
- The former `909 / 618` display is absent.
- HTML `hidden` wins over component display rules; computed-style scans found zero visible hidden elements.
- Lazy ticker detail expansion returned 200 and inserted accessible detail content without navigation.

## 9. PERFORMANCE BEFORE/AFTER

Measurements used the live local HTTP stack and three samples per final endpoint.

| Surface | Before | After | Result |
|---|---:|---:|---|
| Run 30 HTML size | 2.57 MB | 556,720 bytes | Approximately 78% smaller |
| Run 30 request | 15.70 s cold forensic sample | 5.426 / 5.016 / 4.688 s (5.043 s mean) | Approximately 68% faster than the cited cold sample |
| Ticker chart navigation | 5.52 s mean; 14.38 s p95; 20.83 s max | Shell 0.057 / 0.023 / 0.020 s; async data 0.526 / 0.323 / 0.371 s | Shell is immediate; payload is targeted |
| Lazy detail | Included for all 209 rows | 0.030 / 0.022 / 0.018 s on demand | Eager detail payload eliminated |

Profiling identified repeated NYSE holiday computation as an additional coverage hot path. Caching immutable year sets reduced final Run 30 samples from the first post-pagination 6.493–6.943 s range to 4.688–5.426 s without changing session semantics. The exhaustive chart crawl's slowest of 209 identities was 477 ms.

## 10. RUN-ISOLATION CERTIFICATION

Read-only baseline and after-state JSON were compared structurally. These objects are unchanged:

- UploadRun 30 identity/status/timestamps/row count.
- PipelineRun 30 and all 13 PipelineStep records.
- Background jobs and pipeline dependencies associated with Run 30.
- Core evidence IDs/counts by kind and current projections.
- CERI snapshots, evidence IDs, contexts, effective dispositions, and exclusion reasons.
- Setup snapshots, evaluation runs, lifecycle events, and signal changes.
- Winner prediction IDs, estimates, and obligations.
- Decision handoff manifest 20 and market contexts 53/54.

The non-persisting browser replay produced no durable row or job changes. Cross-run ownership scans in the forensic baseline and after-state remain empty.

## 11. CROSS-SCREEN CONSISTENCY CERTIFICATION

- Run root and Pipeline 30 agree: UploadRun `COMPLETED`, PipelineRun `PARTIAL`, 13/13 durable steps.
- Run root and Run CERI agree: 209 captured, 0 eligible, 209 excluded, `PARENT_PIPELINE_UNSUCCESSFUL`.
- Run root and Run Setup use the shared meaningful-change contract: 96 lifecycle + 1,194 signal = 1,290; 209 canonical revisions are separate.
- Run root and Winner Run agree on 28 predictions/estimates.
- All 209 chart children, 290 Setup ticker children, 353 Setup episode children, and 28 Winner prediction children returned 200 with the requested identity and no error marker.

## 12. TEST RESULTS

- Final focused remediation suite: **161 passed**, 1 Starlette TestClient deprecation warning.
- Calendar/coverage/performance regression subset: **57 passed**.
- Code quality: Ruff passed; scoped `git diff --check` passed (only Windows LF-to-CRLF notices).
- Exhaustive live recertification:
  - Root targets: **160/160 passed**.
  - Dynamic children: **880/880 passed** with zero bad status, identity failure, or error page.
  - Current Setup pagination: **29 pages / 2,874 rows**, exact identity/order.
  - Run 30 Setup pagination: **26 pages / 1,290 rows**, exact identity/order.
- Full repository run: **3,981 passed, 442 skipped, 19 failed, 24 errors** in 20m07s.
  - **NEW FAILURE:** generated route inventory omitted the new lazy-detail endpoint. Fixed by regenerating `docs/routes_exports.md`; the governance test passes in the final 161-test gate.
  - **PRE-EXISTING FAILURES (18):** schema-head expectations lag the pre-existing untracked 0090/0091 migrations; root lifecycle test lags the existing `certify-runtime` action; a CERI purge test runs PostgreSQL JSONB SQL under SQLite; existing worker schema fixture expects 0089; architecture/T14 source-pin and operation-family inventories are already stale across numerous unrelated dirty files (including `instrument_lifecycle_service.py`, transition preflight, pipeline, and model changes).
  - **ENVIRONMENT ERRORS (24):** E2E/disposable-browser fixtures require an admin PostgreSQL URL using `postgres/postgres`; authentication is rejected on this machine. These tests did not reach application assertions.
  - No remaining focused or live forensic failure is attributable to the GUI remediation.

## 13. FILES CHANGED

Remediation implementation:

- Security/routes: `app/security.py`; `app/routers/run_routes.py`; `app/routers/ceri_routes.py`; `app/routers/setup_lifecycle_routes.py`; `app/routers/winner_probability_routes.py`.
- Services: `app/services/ceri/evidence_eligibility.py`; new `app/services/ceri/evidence_population.py`; `app/services/pipeline_service.py`; `app/services/setup_lifecycle/query_service.py`; new `app/services/setup_lifecycle/run_summary.py`; `app/services/technical_display_fields.py`; `app/services/us_market_calendar.py`.
- Browser assets: `app/static/app.css`; `app/static/app.js`; `app/static/setup_lifecycle.js`; `app/static/winner_probability.js`.
- Templates: CERI dashboard/operations/ticker; pipeline progress; Run detail; new `_run_ticker_detail.html` partial; Setup operations; Winner operations/run.
- Documentation: `docs/routes_exports.md`.
- Tests: CERI evidence/UI, Setup routes/run summary, route security, Run view models, technical display, chart/detail routes, pipeline GUI semantics, and Winner route/admin/UI suites.
- Evidence: `output/playwright/gui_forensic/remediation/` (baseline/after DB captures, matrix, exhaustive recertification JSON/script, screenshots, and timing HTML).

Pre-existing dirty-worktree files outside this list were preserved. No migration, scoring algorithm, Run 30 row, or historical evidence object was created, rewritten, or deleted by this remediation.

## 14. REMAINING RISKS

- The Run 30 root is materially faster and smaller but still averages about 5.0 s locally because it retains broad cross-domain summary/query work. Further query consolidation could improve it, but is outside the confirmed defect and should be profiled independently.
- The repository-wide suite cannot be fully green until the pre-existing schema fixtures/governance pins are reconciled and disposable PostgreSQL admin credentials are configured. Updating those pins now would absorb unrelated dirty-worktree changes and violate the preservation boundary.
- Valid Winner mutation behavior is fixture-certified rather than executed against live Run 30, intentionally avoiding new jobs or evidence.
- The Starlette/httpx TestClient deprecation warning remains non-blocking.
- Because the worktree combines pre-existing unrelated edits with this remediation, commit readiness remains `NO` despite complete GUI remediation; changes should be separated or the broader pre-existing gates resolved first.

## 15. FINAL VERDICT

The confirmed SwingLens GUI defects are fixed. Runtime authority, evidence eligibility, run isolation, PIT lineage, immutability, and fail-closed behavior remain intact. Run 30 is preserved and now renders its historically correct partial pipeline, excluded CERI evidence, non-input technical observations, run-scoped Setup metrics, complete Winner states, valid progress semantics, paginated evidence, protected mutations, and semantic nulls. Performance is materially improved with measured evidence.

GUI REMEDIATION COMPLETE: YES

SL-GUI-001: PASS
SL-GUI-002: PASS
SL-GUI-003: PASS
SL-GUI-004: PASS
SL-GUI-005: PASS
SL-GUI-006: PASS
SL-GUI-007: PASS
SL-GUI-008: PASS
SL-GUI-009: PASS
SL-GUI-010: PASS
SL-GUI-011: PASS
SL-GUI-012: PASS
SL-GUI-013: PASS
SL-GUI-014: PASS
SL-GUI-015: PASS
SL-GUI-016: PASS

RUN 30 PRESERVED: YES

RUN ISOLATION: PASS

CERI AUTHORITY: PASS

SETUP AUTHORITY: PASS

WINNER AUTHORITY: PASS

CROSS-SCREEN CONSISTENCY: PASS

SECURITY CONTRACTS: PASS

APPLICATION HEALTHY: YES

READY FOR FINAL GUI RECERTIFICATION: YES

READY TO COMMIT: NO
