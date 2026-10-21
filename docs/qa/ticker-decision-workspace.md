# Deliverable 2: Ticker Decision Workspace

Date: 21 October 2026 (Europe/Zurich)

## Verdict

**IMPLEMENTATION PASS. Operational activation remains pending separately.** The workspace was verified in isolated application processes and against the actual database through an explicitly read-only temporary server. Port 8000, its workers and supervisor were not restarted. This report does not certify the entire application.

## Implementation

| Files | Changes |
| --- | --- |
| `app/routers/ticker_routes.py` | Standalone workspace route, validated identity/run parameters, truthful HTML availability/error states and a thin workspace chart-data endpoint. Existing search/summary paths remain unchanged. |
| `app/services/ticker_search_service.py` | Extract the existing identity resolution and eligible-decision iterator for shared use. Change only the preview's workspace destination contract. |
| `app/services/ticker_workspace_service.py` | Compose selected immutable combined evidence, its pinned producer evidence and the existing score cards; read bounded historical-run metadata. |
| `app/services/chart_data_service.py` | Optional explicit technical source for frozen stop/target values; explicit missing source never falls back to mutable scores. Existing callers retain their original behavior. |
| `app/templates/ticker_workspace.html` | Compact header, GET run selector, full-width chart, six accessible disclosures, provenance and missing-data states. |
| `app/templates/ticker_chart_panel.html`, `app/templates/partials/_ticker_price_chart.html`, `app/templates/partials/_score_card_body.html` | Share existing chart and score-card markup with the workspace while preserving the legacy layout/content. |
| `app/static/ticker_workspace.css`, `app/static/ticker_workspace.js` | Responsive layout and optional session-scoped disclosure persistence. |
| `app/static/ticker_search.js` | Open both eligible decisions and known unscored identities in the standalone workspace; retain popup, tab fallback and opener safety. |
| `tests/test_ticker_workspace.py`, `tests/e2e/test_ticker_workspace_browser.py` | Focused SQL/API/template and real-browser verification. |
| `tests/test_ticker_search.py`, `tests/e2e/test_ticker_search_dock.py`, `tests/test_route_security.py` | Update destination assertions, support shared disposable browser fixtures and account for the two new GET routes. All 52 mutation-route classifications remain unchanged. |
| `docs/qa/ticker-search-dock.md`, `docs/qa/ticker-decision-workspace.md` | Dated integration note and this report. |

No scoring formulas, analytical engines, publication writers, worker behavior, schemas, migrations, dependencies or lifecycle mechanisms were changed. Existing unrelated changes were preserved. No commit or push was made.

## Workspace and evidence

Standalone URL:

```text
/tickers/EOG
/tickers/EOG?identity=ceri:399&run_id=45
```

Without a run parameter, the workspace uses the same current-pointer, frozen-readiness, completeness, identity and owner-eligibility rules as the dock preview. An explicit run is validated independently; it never falls back to another decision. The selector reads compact metadata for up to 200 eligible runs, plus an explicitly requested eligible older run. Entire historical runs are not loaded.

Combined values come from the selected immutable COMBINED payload. Fundamental and technical cards use the exact evidence IDs pinned by that combined record, with matching kind, ticker, run and existing calculation-context compatibility checks. Missing or incompatible producer evidence is labeled unavailable; another run or a mutable compatibility score is never substituted. The unchanged `build_score_cards()` supplies every original card field, warning and tone, including valid zero values.

The chart uses the existing cached OHLCV/SMA/volume/overlay service and unchanged renderer. Its label is **Current cached prices; research levels from Run N.** It makes no claim of historical price reconstruction. The chart request carries the page's combined evidence ID; a changed decision pointer returns 409 rather than mixing levels with the previously rendered page.

Workspace chart API:

```text
GET /api/tickers/EOG/chart-data?identity=ceri:399&run_id=45&evidence_id=46423
```

The dock's summary fields and states remain compatible. For an eligible decision, `workspace_url` now targets `/tickers/{ticker}?identity=...&run_id=...`. A known, resolved ticker without an eligible decision gets an identity-only workspace URL and an accurate empty page; no run is invented. Ambiguous or unverified identities retain their disabled opening state. Popups, native-tab fallback, explicit selection, multiple independent pages and safe opener handling are preserved.

The new page returns 404 for an unknown ticker or unusable explicitly selected run, 409 for an ambiguous identity, 200 for a known ticker without an eligible decision or an unverified exchange assignment, and 503 for a database/query failure. Invalid run/identity parameters receive 422. Missing charts leave the legitimate decision and cards available. Missing component details have their own explicit messages.

Price Chart and Combined Decision start expanded. The other four decision cards start collapsed. Native `details/summary` controls work without a UI framework, and session storage preserves a user's expansion choices across workspace navigation when available. The run selector is a regular GET form, so bookmarked URLs, refresh and operation without the disclosure-persistence script remain functional.

## Verification

Executed in the current checkout with the existing virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ticker_workspace.py tests/test_ticker_search.py tests/test_ticker_chart_panel_routes.py tests/test_chart_data_service.py tests/test_score_card_view_service.py tests/test_dashboard_upload.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_route_security.py -q --tb=short
# 82 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 32 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 13 passed

.venv\Scripts\python.exe -m pytest tests/test_ticker_workspace.py -q --tb=short
# 11 passed again after expanding unpublished/quarantined and missing-level assertions

.venv\Scripts\ruff.exe check app/routers/ticker_routes.py app/services/ticker_search_service.py app/services/ticker_workspace_service.py app/services/chart_data_service.py tests/test_ticker_workspace.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/test_ticker_search.py tests/test_route_security.py
# All checks passed

node --check app/static/ticker_workspace.js
node --check app/static/ticker_search.js
# Both exit 0

git diff --check -- app/services/chart_data_service.py app/templates/ticker_chart_panel.html tests/test_route_security.py
# No whitespace errors
```

The final main suites cover **127 passing cases, zero failures** (82 backend/regression, 32 Chromium, 13 Firefox). The backend and Chromium suites were rerun after the final known-unscored workspace destination change; the Firefox run covers both the workspace and dock. The 11 workspace backend cases were also exercised separately while expanding edge assertions. The existing Starlette/httpx deprecation warning remains.

Tests exercise default and explicit historical selection; failed, incomplete, unpublished and quarantined newer candidates; frozen-versus-mutable score consistency; all five cards and their full field sets; zero values; missing and incompatible pinned evidence; run-specific stop/target levels and absence of invented levels; evidence-pointer changes; unknown/unscored/ambiguous identities; invalid run IDs; query errors; missing bars; read-only transactions; popup destination/identity/run/opener behavior; run switching and refresh; disclosure persistence; real chart canvases, volume/SMAs and fallback; dock resizing; and 390 px layout.

Existing chart and chart-data routes, score-card tones/vendor integration, ticker-detail fragments, search matching, keyboard navigation, confirmation dialogs, dashboard/run detail and pipeline progress were covered by the relevant regression suites. No full certification suite was run.

## Browser review using actual persisted data

An additional Playwright CLI walkthrough used a temporary server on port 8779. Its route database sessions were explicitly `READ ONLY`, and non-GET/HEAD requests were rejected. This was separate from the unchanged port 8000 process.

Observed EOG data:

| Selection | Combined evidence | Final score | Frozen stop | Frozen target | Cached bars |
| --- | --- | --- | --- | --- | --- |
| Default Run 45 | 46423 | 8.0815 | 155.3293 | 162.9537 | 758 |
| Explicit Run 44 | 45121 | 8.0877 | 155.3467 | 162.8665 | 758 |

The persisted eligible selector contained Runs 45, 44 and 36. Default rendering showed EOG Resources, Inc., combined rank 1, final 8.08, fundamental 9.11, technical 6.83, Strong candidate, Half starter and `market_risk_off`. Selecting Run 44 updated the bookmarked identity/run URL, header (final 8.09, technical 6.84), provenance, legacy links and chart levels. Candlesticks, volume and all three SMAs rendered from the current cache. Source values were compared to the immutable records in read-only queries. The walkthrough's browser console contained zero errors or warnings.

Desktop and mobile screenshots were inspected. Local output artifacts:

- `output/playwright/ticker-workspace-desktop.png` — disposable fixture.
- `output/playwright/ticker-workspace-mobile.png` — disposable fixture.
- `output/playwright/ticker-workspace-live.png` — actual persisted EOG evidence through the temporary read-only process.
- `output/playwright/ticker-workspace-historical.png` — selected historical Run 44 through the same read-only process.

The temporary read-only server and manual browser session were stopped after review; disposable test servers stop through fixture cleanup. Port 8000 remained owned by its original PID 24588. No lifecycle command or mutation against the real runtime was issued.

## Outstanding and Deliverable 3 readiness

There are no known outstanding Deliverable 2 implementation defects in the executed scope. **Operational activation on port 8000 remains pending**, as documented separately for Deliverable 1; no stack-wide restart or lifecycle remediation was attempted.

Deliverable 3 can reuse the standalone URL, qualified identity, selected-run/evidence context, independent chart loading, collapsible presentation and dock integration. CERI intelligence, lifecycle projections, ranking-profile comparisons and Winner evidence remain intentionally unimplemented. Future independent intelligence must retain its own publication/eligibility rules and source cutoffs instead of inheriting the decision run's timestamp.
