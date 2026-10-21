# Deliverable 3: Ticker Intelligence Integration

21 October 2026 · Repository: `C:\Users\Ivica\Documents\SwingLens`

## A. Verdict

**IMPLEMENTATION PASS.** The full Ticker Intelligence Workspace is implementation-ready for separate operational activation. **Port 8000 has not been activated or restarted.** Verification covers the focused integration and regression checks below.

## B. Implementation

| Files | Purpose |
| --- | --- |
| `app/services/ticker_intelligence_service.py` | Four bounded read adapters using existing identity, publication, certification, readiness and estimate-lifecycle rules. |
| `app/routers/ticker_routes.py` | One GET route for independent summaries and lazy detail fragments. |
| `app/templates/ticker_workspace.html` | Add four native disclosures after the original six sections, including for known tickers without decisions. |
| `app/templates/partials/_ticker_intelligence.html` | Escaped metrics, ranking table, chronological events, warnings, source links and secondary provenance disclosures. |
| `app/static/ticker_workspace.js` | Independent summary/detail requests, loading/error/retry, cancellation and obsolete-response guards, cached details, browser page-cache restoration and existing disclosure persistence. |
| `app/static/ticker_workspace.css` | Matching metrics/event presentation, wrapping and contained table scrolling. |
| `app/services/ceri/query_service.py` | Optional latest-successful-check-per-feed query for freshness; existing callers retain their original behavior. |
| `tests/test_ticker_intelligence.py`, `tests/e2e/test_ticker_intelligence_browser.py` | PostgreSQL authority/read-only cases and Chromium/Firefox interaction checks. |
| `tests/test_ticker_workspace.py`, `tests/e2e/test_ticker_workspace_browser.py` | Verify ten sections while preserving original cards and chart behavior. |
| `tests/e2e/test_ticker_search_dock.py`, `tests/test_route_security.py` | Qualified popup destinations and the additional GET route; all 52 mutation-route classifications remain unchanged. |
| `docs/qa/ticker-intelligence-integration.md` | This report. |

No calculations, writers, pipeline execution, workers, supervision, schemas, migrations, provider calls or dependencies were changed. Unrelated working-tree changes were preserved. No commit or push was made.

## C. Intelligence sources and boundaries

| Section | Selection and displayed evidence |
| --- | --- |
| CERI Intelligence | Canonical `eligible_snapshot_select`, ticker/qualified identity and established cutoff ordering. Excluded dispositions, quarantined/retired owners, purge invalidations and missing certified CERI evidence cannot supply the current score. Existing CERI serializers, `CeriQueryService.changes/alerts` and freshness helpers provide opportunity/risk, confidence, coverage, posture, earnings proximity, components, contributors, revisions, earnings, catalysts, accepted guidance, warnings and recent activity. Source cutoff and elapsed whole-day age are explicit. CERI master/UI flags govern access and source links. |
| Setup Lifecycle | `published_lifecycle_select`, certified active episodes and `current_canonical_snapshot_predicate`, with existing episode/event/snapshot/alert serializers. Daily primary/secondary episodes show state, actionability, confidence, age, opening/observation dates, technical context, warnings and blockers. The recent timeline includes published transitions, signal changes and alerts; superseded transitions and retired/invalidated alerts are excluded. Active-alert counts explicitly refer to the bounded recent page. |
| Ranking Profiles | Selected eligible decision run and verified identity. Persisted `RankingResult` rows must match their current evidence projection, shared `get_certified_evidence_for_row` profile/scope checks, selected calculation context and frozen READY envelope. Values, gates, penalties and configuration fingerprints come from immutable ranking payloads; mutable compatibility values and today's ranking YAML are not used. Missing/unpublished profiles have an explicit unavailable state. Profile ranks are labeled separately from Combined rank. |
| Winner Evidence | Existing prediction visibility predicate, selected run and the exact pinned combined-evidence ID. Only serving **DECISION_TIME** estimates pass `estimate_is_serving`; candidate, superseded and unpublished estimates are excluded. Outcome definitions follow each persisted estimate's FK, including historical versions. Probability/interval, eligibility, sample/effective sample size, grade, outcome/horizon, source/version, returns, MFE/MAE, reasons, context and provenance are shown when supported. Insufficient evidence stays distinct from 0%. No independent latest rescore is substituted. |

CERI and Lifecycle remain **current independent intelligence**, with their own source dates and an explicit notice that they may postdate the decision. Changing the Decision Run refreshes Rankings and Winner context without rebasing current CERI/Lifecycle evidence. Missing sources, ambiguous/unverified identities and unavailable decisions receive separate states; a query failure receives an error and retry, rather than being reported as no evidence.

## D. API contract and loading

```text
GET /api/tickers/{ticker}/intelligence/{section}
    ?identity=ceri:399&run_id=45&evidence_id=46423&detail=false
```

`section` is `ceri`, `lifecycle`, `rankings` or `winner`. Identity/run/evidence parameters retain the workspace's validation. `run_id` is optional for independent current intelligence; Rankings/Winner report unavailable without a usable selected run. A changed combined pointer produces a refresh-required state. Invalid parameters return 422; SQL/query failures return a safe 503 response.

Successful responses contain `state`, `summary` and an actual source `as_of` when available. `detail=true` also returns an autoescaped server-rendered HTML fragment. Other availability states use HTTP 200 and explicit state/message fields. Disabled CERI responses contain no CERI detail link.

The main HTML does not invoke these intelligence readers. Four small summaries load independently after rendering; detail requests start on expansion (or restoration of an explicitly saved expansion choice). Loaded details are reused during collapse/reopen. Page departure aborts requests; version/document guards ignore obsolete responses. Returning through the browser page cache resumes unfinished reads.

Bounds: 10 CERI changes/alerts/catalysts/earnings, 30 current revision features; 20 active lifecycle episodes and 20 mixed timeline entries from bounded source pages; 100 persisted ticker-specific ranking profiles; 10 visible selected-run Winner predictions and 100 serving estimates. Source-page links provide deeper history. Queries use existing ticker/run, primary-key and publication indexes; no migrations were needed. Linked alert context and Winner upstream evidence are loaded in batches.

## E. Verification

Main runs: **234 distinct passing cases; zero failures or skips.** The existing Starlette/httpx deprecation warning remains.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ticker_intelligence.py tests/test_ticker_workspace.py tests/test_ticker_search.py tests/test_ticker_chart_panel_routes.py tests/test_chart_data_service.py tests/test_score_card_view_service.py tests/test_dashboard_upload.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_route_security.py -q --tb=short
# 95 passed

.venv\Scripts\python.exe -m pytest tests/ceri/test_ceri_query_service.py tests/setup_lifecycle/test_query_service.py tests/test_ranking_profile_service.py tests/test_ranking_profile_routes.py tests/winner_probability/test_routes_api.py tests/winner_probability/test_estimate_lifecycle.py -q --tb=short
# 84 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 37 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 18 passed
```

Additional final focused reruns (not added to the distinct-case total):

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ticker_intelligence.py -q --tb=short
# 13 passed after final authority/presentation assertions

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py --browser chromium --browser firefox -q --tb=short
# 10 passed after browser page-cache restoration handling

.venv\Scripts\python.exe -m pytest tests/test_ticker_intelligence.py::test_identity_run_validation_missing_and_changed_evidence tests/e2e/test_ticker_intelligence_browser.py::test_missing_sources_keyboard_narrow_layout_and_dock --browser chromium --browser firefox -q --tb=short
# 3 passed after the final ambiguity and no-selected-run checks

.venv\Scripts\ruff.exe check app/services/ticker_intelligence_service.py app/routers/ticker_routes.py app/services/ceri/query_service.py tests/test_ticker_intelligence.py tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_search_dock.py tests/test_ticker_workspace.py tests/test_route_security.py
node --check app/static/ticker_workspace.js
git diff --check -- app/services/ceri/query_service.py tests/test_route_security.py
# All passed
```

The tests exercise newer excluded/quarantined evidence, publication and profile/context mismatches, frozen-versus-mutable scores, historical configuration/outcome versions, unsupported probabilities, stale/obsolete requests, lazy/cache/retry behavior, escaped external text, keyboard use, narrow layouts and read-only transactions. Summary response sizes and query counts are bounded in tests. No analytical entry point or job creation is needed.

## F. Persisted-data browser review and timings

A separate temporary server on port 8779 used `SET TRANSACTION READ ONLY` for every database session, rejected all non-GET/HEAD requests, and ran with application lifespan hooks disabled. The browser expanded all four EOG sections, used the Decision Run selector from 45 to 44, and inspected charts, rankings, Winner status and source dates. All observed chart/intelligence requests returned 200. The console contained **zero errors and zero warnings**.

| Source | Observed result |
| --- | --- |
| EOG CERI | Opportunity 7.73, event risk 0.00, confidence Low; cutoff `2026-10-20T03:05:25.356265+02:00`, unchanged across decision runs. Recent changes were available; no eligible recent alerts were returned. |
| EOG Lifecycle | Published READY / GENERIC / BLOCKED episode, source date `2026-10-19`; timeline, blockers and source links rendered independently of decision selection. |
| EOG Rankings | Five persisted profiles in both runs. Best profile rank changed from #2 in Run 45 to #3 in Run 44, with their own frozen scores/configuration. |
| EOG Winner | Run 45 prediction/estimate #947 and Run 44 #763; both published estimates report `Insufficient evidence`, reason `no_eligible_cohort`. Their distinct source cutoffs and publication timestamps were displayed; neither was rendered as 0% probability. Supported 64%/41% probability and interval paths were verified with disposable fixtures. |
| AZTA (`ceri:128`) | Known ticker without an eligible decision or current CERI/lifecycle evidence; all four sections returned truthful empty/unavailable states without an invented run. |

Sequential local HTTP measurements during concurrent test activity: summaries **106–338 ms** (82–155 response bytes); Lifecycle detail **490–526 ms**; Rankings **173–191 ms**; Winner **176–185 ms**; CERI **5,499–7,340 ms**. These are observed requests, not a capacity benchmark. CERI's established change/alert reads remain the slowest detail path; independent loading leaves the chart, decision and other panels usable. Its bounded freshness query also matched the existing full-history freshness output for actual EOG snapshot #5136.

Artifacts under `output/playwright/`: `ticker-intelligence-live.png`, `ticker-intelligence-rankings-live.png`, `ticker-intelligence-empty-live.png`, `ticker-intelligence-mobile.png`, and `ticker-intelligence-timings.json`.

## G–I. Regression, outstanding and readiness

The original chart and chart-data routes, full score-card fields, qualified search/preview/popups, keyboard/focus behavior, historical run selector, dashboard/run detail, pipeline progress, confirmation dialogs and the existing CERI/Lifecycle/Ranking/Winner read services passed relevant regressions. No entire-application certification suite was run.

No required Deliverable 3 implementation work remains in the verified scope. CERI detail latency is recorded above. **Operational activation remains pending separately** under the existing safe-activation constraint. The temporary browser/server were stopped; port 8000 remained owned by its original PID 24588. No worker, supervisor or pipeline lifecycle action was issued.
