# Deliverable 1: Global Ticker Search Dock

Date: 20 October 2026 (Europe/Zurich)

## A. Verdict

**PARTIAL overall: implementation and focused verification pass; activation in the existing server remains pending.** This is not certification of the entire application. The existing web process on port 8000 predates the new router and requires its normal restart to activate the endpoints; it was not restarted during this work. Verification used fresh isolated application processes, including a temporary read-only view of the actual runtime database. The available managed lifecycle restart also restarts other runtime components, outside this feature's read-only boundary.

## B. Implementation

| File | Change |
| --- | --- |
| `app/main.py` | Register the new read-only router. |
| `app/routers/ticker_routes.py` | Validated local search and qualified summary GET APIs. |
| `app/services/ticker_search_service.py` | SQL identity deduplication, matching, aliases, conservative disambiguation, compact certified decision selection. |
| `app/templates/base.html` | One shared dock, stylesheet and deferred script. |
| `app/templates/partials/_nav.html` | Persistent search button and icon. |
| `app/templates/partials/_ticker_search.html` | Accessible, non-modal search and preview markup. |
| `app/static/ticker_search.js` | 200 ms debounce, cancellation/version guards for search and preview, retry, keyboard selection, safe text rendering and independent opening. |
| `app/static/ticker_search.css` | 390 px desktop dock; stacked layout at 1000 px and below; content-width adjustments for existing metrics/chart while dock is open. |
| `tests/test_ticker_search.py` | Eight PostgreSQL/API tests using disposable databases. |
| `tests/e2e/test_ticker_search_dock.py` | Eight browser cases, run in Chromium and Firefox. |
| `tests/test_route_security.py` | Update the total route count for two GET endpoints and assert that both remain read-only; mutation registry remains unchanged. |
| `docs/qa/ticker-search-dock.md` | This handoff report. |

The dock has no backdrop, focus trap, page scroll lock, chart instance, polling, resize framework or additional dependency. Closing returns focus to the initiating control, or the navigation button when opened from the page body. Escape defers to existing visible dialogs.

## C. API contracts

`GET /api/tickers/search?q=EO&limit=15`

Query defaults to empty, is trimmed, accepts up to 100 characters and rejects control characters. Empty queries return no results without a database read. Limit is 1–50, default 15; invalid parameters return 422. SQL wildcard characters are escaped. Matching is case-insensitive and ordered exact symbol/established alias, prefix, substring, company name, then symbol/exchange/name/identity for stable ties.

Actual response excerpt from the runtime database:

```json
{
  "results": [
    {
      "identity": "ceri:399",
      "ticker": "EOG",
      "company_name": "EOG Resources, Inc.",
      "exchange": "US",
      "qualifier": "US"
    }
  ]
}
```

`GET /api/tickers/{ticker}/summary?identity=ceri:399`

The optional identity is an opaque search-result key (`ceri:<id>`, `raw:<id>` or `contract:<id>`). Unknown identities/tickers return 404; invalid qualifiers return 422. Successful availability responses are 200, including missing/ambiguous evidence states.

Actual EOG response, with JSON field order shortened for readability:

```json
{
  "state": "available",
  "identity": {
    "identity": "ceri:399",
    "ticker": "EOG",
    "company_name": "EOG Resources, Inc.",
    "exchange": "US",
    "qualifier": "US"
  },
  "decision": {
    "run_id": 45,
    "evidence_id": 46423,
    "cutoff": "2026-10-20T01:15:57.541921+02:00",
    "final_rank": 1,
    "final_score": "8.0815",
    "fundamental_score": "9.1093",
    "dual_score": "6.8252",
    "combined_decision": "Strong candidate",
    "position_size_hint": "Half starter",
    "sector": "Energy minerals",
    "warning_flags_json": ["market_risk_off"],
    "earnings_warning_flags_json": []
  },
  "workspace_url": "/runs/45/tickers/EOG/chart",
  "intelligence": {
    "ceri": "not_checked",
    "lifecycle": "not_checked",
    "winner": "not_checked"
  }
}
```

Other states are `no_eligible_decision`, `identity_ambiguous`, and `decision_identity_unverified`; they have null decision/workspace URL. Unknown ticker is reported as a 404 with `detail: "unknown_ticker"`. The UI disables opening when there is no eligible destination. Scores retain persisted precision in the API and display to two decimal places.

## D. Verification

Executed from the repository root with the existing virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ticker_search.py tests/test_dashboard_upload.py tests/test_ticker_chart_panel_routes.py tests/test_score_card_view_service.py tests/test_chart_data_service.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_ranking_profile_routes.py -q --tb=short
# 63 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 27 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 8 passed

.venv\Scripts\python.exe -m pytest tests/test_route_security.py tests/test_architecture_registry.py -q --tb=short
# 26 passed

.venv\Scripts\ruff.exe check app/main.py app/routers/ticker_routes.py app/services/ticker_search_service.py tests/test_ticker_search.py tests/e2e/test_ticker_search_dock.py
# All checks passed

node --check app/static/ticker_search.js
# Exit 0

git diff --check -- app/main.py app/templates/base.html app/templates/partials/_nav.html
# No whitespace errors
```

Total: **124 passing test cases** across the four final suites. The existing Starlette/httpx deprecation warning remains. Initial failures in newly written tests and their fixtures were corrected before these successful runs. The existing security inventory's hardcoded route total was also updated from 197 to 199 for the two new GET endpoints; all 52 mutation-route classifications remain unchanged.

Coverage includes historical discovery, deterministic matching, deduplication, valid/expired/future aliases, punctuation isolation, exchange/name ambiguity, limits and invalid input, frozen score consistency, failed/incomplete/unpublished/legacy/unsafe-partial exclusion, pointer/readiness mismatch, and SQL reads without writes or lazy-loading history. Browser coverage includes the global dock, original dashboard file-picker interaction, arrow keys/Enter, single-click preview, both request races, both retry paths, Escape with existing confirmation dialogs, close/reopen, popup and blocked-popup link fallback, multiple independent pages, safe opener, safe string rendering and 390 px layout. The smoke suite checks the shared application surfaces and existing CSV upload/run-detail flow.

Additional Playwright CLI walkthrough used a fresh server with database sessions explicitly set to `READ ONLY` and non-GET/HEAD requests rejected. Actual observations:

- `EO` returned six local matches; EOG's preview matched certified Run 45 evidence.
- Open Workspace created one independent EOG chart page and retained the dashboard/dock.
- The existing chart rendered 758 cached daily bars through 19 October 2026, overlays and score cards; opening its own dock resized the chart successfully.
- AAPL, absent from Run 45, resolved to its eligible Run 44 decision.
- ABM was discoverable with no eligible decision; opening was disabled.
- The runtime database had no CERI-only symbols; that case was verified using disposable test data.

Screenshots are in ignored local output:

- `output/playwright/ticker-search-live.png`
- `output/playwright/ticker-chart-with-dock.png`
- `output/playwright/ticker-known-no-decision.png`
- `output/playwright/ticker-search-dock.png` (disposable fixture)

## E. Evidence and identity correctness

Search uses all upload runs, persisted CERI companies/currently valid ticker aliases and otherwise unrepresented IB contracts. Repeated upload rows are grouped by exact normalized symbol and company name in SQL. An upload identity joins a single CERI identity only on matching symbol/name, or when the CERI name is absent and uploads supply one unambiguous name. Conflicting names and exchanges remain separate. Dot/hyphen variants are never generated; only stored aliases are used. Provider alias collisions yield multiple companies.

Run scores lack a verified exchange address. When multiple CERI securities share a symbol, a qualified company remains selectable but receives `decision_identity_unverified`, rather than an arbitrary score. Different historical upload names also remain explicit identities; company renames are not guessed.

Preview selection follows the run-scoped `CoreCalculationCurrentProjection` to matching immutable COMBINED evidence and requires the compatibility result to point to that same evidence. Legacy-serving declarations are excluded. The evidence must be complete, have stored READY readiness bound to its fingerprint with no blockers, and have a matching terminal pipeline owner. COMPLETED and safely certified PARTIAL outcomes follow the existing pipeline outcome containment contract; quarantined/retired owners are excluded. Missing/legacy readiness is never recomputed under today's policy.

Candidates are ordered by the frozen calculation cutoff, with IDs only as deterministic tie-breakers. Failed, running, incomplete, uncertified and unsafe-partial candidates cannot replace the earlier authoritative decision. Final/fundamental/technical values, rank, labels and warnings all come from one frozen combined payload. The summary does not independently select newer component scores. Exactly one matching raw row/name is required for the existing chart destination.

## F. Performance

No schema, index, search master table or cache was added. Existing ticker/run/identity indexes were inspected. `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` showed sequential work for substring/name matching over this small catalog; one observed search plan took about 92 ms execution. Reusing the SQL upload-name aggregation avoids repeating that aggregation for each source.

The runtime database contained 5,039 upload rows, 594 CERI companies and 596 IB contracts. Five warmed service calls after one priming call, using a read-only session, measured:

| Call | Samples, ms | Median |
| --- | --- | --- |
| Search `EO` | 62.52, 76.10, 85.42, 97.94, 57.96 | 76.10 ms |
| Summary `EOG` | 44.85, 49.14, 59.77, 89.65, 68.52 | 59.77 ms |

These are local service timings, not HTTP latency, cold-start figures, an SLA or a large-universe benchmark. Earlier cold calls were slower. Substring/name queries can scan existing persisted rows; returned identities are limited in SQL and historical ORM objects are never materialized. A normal successful summary uses three SQL reads; search uses one. Candidate decision metadata is read in batches of ten if invalid older envelopes need skipping. The browser receives only the selected compact preview and bounded search results. No price bars or intelligence histories are loaded by these APIs.

A read-only source-coverage check found no tickers in `price_series_versions`, `fundamental_scores`, `technical_scores` or `combined_results` that were absent from the chosen raw/company/contract sources in this database.

## G. Regression and scope

Existing chart URLs, chart-data routes, score-card composition, dashboard, run detail, confirmation dialogs, navigation and pipeline progress semantics remain operational in the executed checks. Analytical algorithms, pipeline execution, workers, scoring, evidence mutation contracts and database contents were not changed by this feature. Database writes occurred only in disposable test fixtures. Existing unrelated working-tree changes were preserved. No commit or push was made.

The existing port 8000 web process still returns 404 for the newly added search endpoint until restarted; source verification was performed on fresh processes. The standard lifecycle restart also affects other runtime components, so it was not invoked as part of this focused change. The temporary verification server is stopped after the walkthrough.

## H. Deliverable 2 readiness

Deliverable 2 can reuse the catalog, identity keys, summary selection, dock and explicit workspace link. It can replace the existing chart destination with `/tickers/{ticker}` once that page exists. The new standalone workspace, run selector, collapsible score-card layout and CERI/lifecycle/ranking/Winner sections are intentionally unimplemented. The current existing chart's cached-price/provenance presentation was preserved; no reconstructed historical chart is claimed.

## I. Final activation inspection — 21 October 2026

**Final verdict: PARTIAL; activation blocked.** Inspection ran from 00:36 to 00:39 CEST (UTC+02:00) against the actual server at `http://127.0.0.1:8000`. No restart was performed, following the closure task's instruction to stop if a safe supported independent web restart is unavailable.

**Exact blocker:** the supported lifecycle exposes only a stack-wide restart. `swinglens.ps1:4` has no web-only action. `scripts/ops/SwingLensLifecycle.psm1:980` implements restart as `Stop-SwingLensStack` followed by `Start-SwingLensStack`; the stop path quiesces worker claims and stops core processes, observability and the database as configured (`:916`). On Windows, the lower-level `signal-break` command writes a supervisor-scoped shutdown request (`scripts/ops/lifecycle_probe.py:1059`), whose supervisor cleanup shuts down both web and worker (`app/worker_supervisor.py:382`). Automatic recovery after a web-child failure exists, but is not a controlled web-only recycle command. It was not triggered artificially, and lifecycle ownership/state was not bypassed or edited. Startup scripts and process-management code were not changed.

Runtime identity remained unchanged throughout inspection:

| Component | Before / after |
| --- | --- |
| Runtime instance | `cf3732d8933f411da2f4821d6427dc21` |
| Web listener | PID **24588**, created 20 October at 20:04:07 CEST; one listener on port 8000 |
| Web launcher | PID **60528**; no replacement web process started |
| Supervisor | PID **55268**, generation **51**, same instance and fresh heartbeat |
| Worker | PID **77676**, generation **123**, same instance and fresh heartbeat |
| Restart counters | Web **0**, worker **0** |

Worker quiesce/stopping fields remained null. Read-only database snapshots showed unchanged pipeline/job status counts. No jobs were cancelled, retried, resumed or enqueued; no database writes, lease resets, generation changes, process signals or additional servers were initiated by this task. `/ready` reported zero authoritative runnable/running jobs; its historical-job classification identified 49 non-authoritative queued rows and one stale running row.

Live read-only checks:

| Request | Result |
| --- | --- |
| `/health` | HTTP 200, `ok` |
| `/ready/core` and `/ready` | HTTP 200, `ok`; current web/supervisor/worker identities verified |
| `/api/tickers/search?q=EO&limit=15` | HTTP **404**, confirmed twice; new router remains unloaded |
| `/api/tickers/EOG/summary?identity=ceri:399` | HTTP **404** route-presence probe using the previously known key; no live preview/evidence verification possible |
| `/runs/45/tickers/EOG/chart` | HTTP 200 |
| `/runs/45/pipeline/45/status` | HTTP 200, `PARTIAL` |
| `/runs/9/pipeline/9/status` | HTTP 200, `WAITING_FOR_CERI_COMPLETION` |
| `/` and `/runs/45` | Each exceeded a 15-second read-only request timeout before any activation action; successful responses were not verified in this inspection |

**Unrelated existing observations:** historical Run 9 remains waiting for CERI. Job 182 (`CERI_PROVIDER_INGEST_BATCH`, retry count 0, cancellation flag false) is still marked RUNNING with a lease expired on 1 October 2026; readiness classifies it as historical/non-authoritative. The dashboard/run-detail timeouts above also preceded any activation. These were recorded without remediation. Bounded checks of the last 100 JSON records in each existing web, worker and supervisor log found no WARNING/ERROR/CRITICAL records; no new startup was attempted.

The requested activated-server browser smoke test was **not run** because activation could not safely proceed and both new endpoints remain absent. No source changes or test reruns were needed; the previous 124 passing automated cases and prior separate-process browser evidence remain intact. Only this QA report was updated during closure inspection. No commit or push was made.

**Outstanding:** a supported web-only activation mechanism is unavailable within the permitted scope. Deliverable 1 remains open; Deliverable 2 operational handoff is **NOT READY** pending successful activation and live browser verification.

## J. Deliverable 2 integration — 21 October 2026

The Decision Workspace implementation now supplies `/tickers/{ticker}?identity=...&run_id=...`, and eligible summary responses point Open Workspace to that destination. Known, resolved tickers without an eligible decision can also open an identity-only workspace URL, which displays an accurate empty state without inventing a run. Existing contract examples and disabled-opening behavior above record Deliverable 1's earlier chart-page target. Shared identity/eligibility helpers keep preview and workspace selection consistent. See `docs/qa/ticker-decision-workspace.md` for the new implementation and isolated verification results. Operational activation remains pending; section I's runtime blocker was not changed or remediated.
