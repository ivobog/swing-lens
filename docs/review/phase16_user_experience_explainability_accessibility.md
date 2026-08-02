# Phase 16 Review: User Experience, Explainability, and Accessibility

Date: 2026-08-02

## Objective

Ensure users can understand what SwingLens knows, what it does not know, and why each workflow produces its current state or recommendation.

## Scope Reviewed

Primary workflows covered:

- Upload and dashboard overview.
- IB fetch plan, fetch progress, retry/resume/cancel, coverage, and mapping.
- Full pipeline progress, run detail, decision cockpit, ranking details, exports, and history.
- Ticker chart and ticker-level decision summary.
- Market regime and sector rotation dashboards, drilldowns, CSV/JSON/brief exports.
- Setup lifecycle overview, ticker detail, episode evidence, alert/operation surfaces.
- Winner probability run/ticker/model/operation surfaces and CSV/JSON/audit exports.
- CERI dashboard, ticker detail, changes, operations, JSON forms, and exports.

Verification performed:

- Static UI/template review across `app/templates` and `app/static`.
- Focused route/export regression suite:
  `uv run pytest tests/test_gui_phase1.py tests/test_dashboard_upload.py tests/test_run_detail_view_models.py tests/test_ticker_chart_panel_routes.py tests/test_market_regime_routes.py tests/test_sector_rotation_routes.py tests/setup_lifecycle/test_routes.py tests/winner_probability/test_routes_ui.py tests/winner_probability/test_exports.py tests/ceri/test_ceri_routes_ui.py tests/ceri/test_ceri_export_service.py -q`
- Result: `108 passed, 1 warning` in 16.94s. The warning is the existing Starlette/httpx deprecation warning from FastAPI TestClient.

## Workflow Defect Register

| ID | Severity | Area | Evidence | Defect | Recommended Fix |
| --- | --- | --- | --- | --- | --- |
| UX16-01 | P1 | Fetch and pipeline progress | `app/templates/fetch_progress.html:39`, `app/templates/pipeline_progress.html:44`, `app/static/app.js:419-443`, `app/static/app.js:493-516` | Progress bars and auto-refreshing status updates are visual only. They have `aria-label`, but no `role="progressbar"`, `aria-valuenow`, `aria-valuemin/max`, or live status region. Network polling failures silently retry without exposing "connection lost / retrying" to users. | Add accessible progressbar attributes and update them in JS. Add a concise `role="status" aria-live="polite"` region for status/current-step/message changes and a visible stale/reconnecting state after repeated polling errors. |
| UX16-02 | P1 | Ticker charts | `app/templates/ticker_chart_panel.html:52-58`, `app/static/ticker_chart_panel.js:78-101`, `app/static/ticker_chart_panel.js:125-182` | The chart has only a generic `role="img"` label. Users relying on screen readers cannot get date range, latest close, trend, stop/target, or overlay values. Candles, volume, SMAs, stop, and target rely heavily on color and visual position. | Add an accessible chart summary and compact data table/fallback fed by the same chart payload. Add a text legend for up/down candles, SMA lines, stop, and target; do not rely on color alone. |
| UX16-03 | P1 | Global keyboard navigation | `app/templates/base.html:10-15`, `app/static/app.css:936-938`, `app/static/app.css:1532-1533` | The shell has no skip link before the top navigation, and focus styling is inconsistent. Only some components, such as clickable rows and lifecycle ribbon, define outlines; links, buttons, file upload labels, chips, selects, and table controls do not have a global `:focus-visible` treatment. | Add a visible-on-focus skip link to `main`, set `id="main-content"`, and define a global high-contrast `:focus-visible` rule for links, buttons, inputs, selects, textareas, summary, and upload labels. |
| UX16-04 | P2 | Table semantics | `app/templates/run_detail.html:582-596`, `app/templates/run_detail.html:765-777`, `app/templates/fetch_progress.html:114-127`, `app/templates/pipeline_progress.html:86-95`, compared with newer tables at `app/templates/ceri_dashboard.html:70-81` and `app/templates/setup_lifecycle.html:91-100` | Newer pages use captions and `scope="col"`, but older core tables do not. The decision cockpit also uses sortable header buttons without initial `aria-sort` state until JS sorting occurs. | Add captions and scoped headers consistently. Initialize `aria-sort="none"` for sortable headers and keep the button label/text unchanged while exposing sort state semantically. |
| UX16-05 | P2 | Evidence/provenance consistency | `app/templates/setup_lifecycle_ticker.html:25-30`, `app/templates/winner_probability_ticker.html:65-70`, `app/templates/winner_probability_ticker.html:124-131`, `app/templates/ceri_dashboard.html:82-96`, `app/templates/ceri_ticker.html:19-26` | Deep detail pages expose lineage well, but list pages and export entry points do not use one consistent vocabulary for evidence mode. Users can see stale/conflicted/cutoff/config in places, but cannot always distinguish decision-time, latest-corrected, live-derived, reconstructed, or simulated evidence before clicking into detail. | Introduce a shared evidence provenance badge model and render it in HTML, JSON, CSV, and Markdown: evidence mode, source cutoff, freshness, correction state, simulation/reconstruction flag, model/config version, and confidence. |
| UX16-06 | P2 | Financial research disclaimers | `app/templates/run_detail.html:675`, `app/templates/winner_probability_run.html:12`, `app/templates/ceri_dashboard.html:12`, but chart/market pages at `app/templates/ticker_chart_panel.html:26-39`, `app/templates/market_regime.html:49-65` | Research-only wording is present on several advanced pages, but decision/probability-adjacent views are inconsistent. The ticker chart shows decision and position size without nearby research-only/no-orders language; market regime shows position-size multiplier and gate state without the same warning. | Add a compact, shared disclaimer component near every decision, probability, gate, and position-size surface. Keep language short and identical across HTML exports/briefs where practical. |
| UX16-07 | P2 | Async action feedback | `app/static/setup_lifecycle.js:16-25`, `app/static/ceri.js:12-27`, `app/static/ceri.js:31-56` | Async expand/actions and JSON admin forms update inline text, but not through guaranteed live regions. Failure messages are generic and do not always offer next steps or a retry path. | Add `role="status"` to action outputs/status cells, keep failed buttons enabled with clear retry text, and map known API errors into user-facing recovery guidance. |
| UX16-08 | P3 | Destructive or long-running actions | `app/templates/run_detail.html:103-116`, `app/templates/ib_fetch_plan.html:73-87`, `app/templates/winner_probability_models.html:58-59`, `app/static/app.js:550` | Confirmation exists for the most obvious long-running/destructive actions, but the implementation relies on browser confirm and handles only form `submit`. It does not add context after browser refresh/back navigation or identify already-queued duplicate work before submission. | Add server-side idempotency/duplicate-job detection surfaced in UI. Replace browser confirm for high-impact actions with a reusable confirmation panel that summarizes action, target run/job, estimated impact, and recovery path. |

## Accessibility Audit

| Check | Status | Notes |
| --- | --- | --- |
| Landmarks and page titles | Partial pass | Base layout provides `html lang="en"`, viewport, header, nav, and main landmark. Add a skip link and `id` on main. |
| Headings | Mostly pass | Pages use one `h1` and section headings. Some nested detail grids jump into repeated `h3`s but remain understandable. |
| Forms and labels | Mostly pass | Filters and upload controls are labeled. Add `aria-describedby` for upload type/size constraints and for riskier filters with numeric min/max meaning. |
| Loading states | Partial | Buttons disable and change label through `data-loading-form`, but no global live announcement and no duplicate-submission context after refresh/back. |
| Empty states | Pass with gaps | Empty states are broadly present and usually give a next step. Progress and chart failure states need more actionable recovery text. |
| Error states | Partial | Server-rendered alerts exist, but many lack `role="alert"` or next-step guidance. Polling errors are silent. |
| Tables | Partial | Newer feature pages use captions/scoped headers; core run/progress/market/sector/winner tables need captions and `scope`. |
| Keyboard focus | Fail until fixed | No global focus-visible style and no skip link. Some component-level focus outlines exist. |
| Screen-reader updates | Partial | Copy feedback has `aria-live`; progress, async detail load, alert actions, and admin form outputs need live regions. |
| Color contrast | Needs visual audit | Palette appears restrained and text colors are likely serviceable, but no automated contrast evidence was run in this phase. Badge meaning should not depend only on tone. |
| Charts and non-text content | Fail until fixed | Ticker chart needs textual summary, legend, and data fallback. |
| Responsive layout | Partial | `table-wrap` and mobile CSS are used widely, but large dashboard tables should be verified with browser screenshots and keyboard tab order in a follow-up remediation pass. |

## Explainability Matrix

| Surface | What Is Clear | What Is Missing Or Inconsistent |
| --- | --- | --- |
| Upload/dashboard | Latest status, counts, max upload size, recent runs, and upload errors are visible. | Upload help is not attached with `aria-describedby`; upload failure does not always tell user whether no data was stored or partial rows were kept. |
| IB fetch/coverage | Ready/insufficient/stale/missing-volume/contract-failed states are visible and exports include results/failures. | Auto-refresh staleness, polling errors, and cancellation latency are not explicit to assistive tech. |
| Pipeline | Step-level status, messages, errors, cancel request state, and durable persistence wording are visible. | Progress semantics and retry/recovery guidance are weak. |
| Decision cockpit | Advisory wording, incomplete-first ranking rule, warnings, score components, model strings, confidence, missing fields, earnings gates, and detail expansion are strong. | Table accessibility and initial sort semantics need tightening; a shared disclaimer should be visible near decision/position columns. |
| Ticker chart | Decision, position, stop, target, reward/risk, and score cards are shown. | Chart itself has no usable textual summary and color-dependent overlays. |
| Market regime | Regime, risk state, confidence, stale index labels, gate, position-size multiplier, reasons, warnings, and exports are visible. | Research-only/no-orders wording is not adjacent to gate and position sizing; table semantics lag newer pages. |
| Sector rotation | Mode, as-of date, benchmark, market snapshot availability, confidence, reasons, warning distribution, CSV/JSON/brief exports are visible. | Evidence mode/correction state and shared disclaimer are not consistently surfaced. |
| Setup lifecycle | Research-only language, stale-system warning, low-confidence share, source links, origin type, canonical/noncanonical, confidence, timeline, episodes, and CSV/JSON exports are strong. | Async expansion/action feedback needs live regions; reconstructed/simulated exclusion policy is not obvious on user-facing list pages. |
| Winner probability | Outcome definition, entry model, horizon, estimate view, calibrated/insufficient counts, intervals, grade, training cutoff, feature/config hashes, source IDs, reproduction/audit export are strong. | Run table does not expose model/source version until detail; shared probability disclaimer should be consistent with chart/cockpit exports. |
| CERI | Research-only language, stale/conflicted counts, provider freshness, cutoff/config/evidence hash, source IDs, quarantine/conflict/stale ops are visible. | Top-level candidate rows do not show corrected vs as-known/latest mode; admin outputs need live regions and clearer retry/next-step text. |

## UI State Catalogue

| State | Current Coverage | Missing Design/Behavior |
| --- | --- | --- |
| Loading | Button labels change; chart has "not loaded yet"; progress pages auto-refresh. | Announce loading with live regions; show polling-retry state; avoid silent retries. |
| Empty | Broadly present across dashboard, cockpit, setup lifecycle, winner probability, CERI, sector, and chart score cards. | Empty states should consistently name prerequisite data and link to the next action. |
| Disabled | Disabled buttons/forms exist for unavailable fetch actions and details without episode IDs. | Add disabled reasons with text adjacent to the control or `aria-describedby`. |
| Partial | Fetch `PARTIAL`, coverage statuses, insufficient estimates, low-confidence/stale states are represented. | Use a shared partial-data badge with same labels across HTML/JSON/CSV/Markdown. |
| Stale | Coverage, market index health, setup lifecycle diagnostics, and CERI freshness expose stale state. | Surface stale timestamp/cutoff consistently in list pages and export rows. |
| Failed | Fetch item errors, pipeline errors, CERI action failures, and lifecycle failed states are visible. | Give next-step recovery text and retry eligibility; add `role="alert"` for newly rendered failures. |
| Retry/resume | Fetch retry failed/resume remaining, pipeline cancellation, and CERI queue forms exist. | Show duplicate-job/already-queued status after refresh/back; attach idempotency context to submit actions. |
| Corrected | CERI point-in-time services support correction behavior; ticker pages show hashes/cutoffs. | HTML list rows and exports need explicit `as_known`, `latest_corrected`, and correction/supersession labels. |
| Simulated/reconstructed | Setup lifecycle config/tests distinguish reconstructed origin and alert/export exclusions. | User-facing lifecycle pages should show when evidence is reconstructed/simulated and whether it is excluded from alerts/exports. |
| Live-derived | IB/CERI/market pages show source/freshness in pieces. | Add one normalized "evidence mode/source" field wherever decisions or probabilities are shown. |

## Error Message And Recovery Improvements

1. Progress pages: display "Connection interrupted; retrying in 5s" after polling failures and clear it after the next successful poll.
2. Progress bars: render `<div role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="...">` and keep `aria-valuetext` synced with completed/total counts.
3. Alerts: use `role="alert"` for new failure states and `role="status"` for non-error queue/update success.
4. Async expand: while loading episode/CERI detail, write into a live status region; on failure, preserve an enabled retry button.
5. Upload failure: state whether the file was rejected before storage, partially parsed, or stored with row-level warnings.
6. Duplicate actions: when a fetch/pipeline/reprocess request is already queued or running for the same target, show the existing job link instead of submitting another indistinguishable job.
7. Chart failure: distinguish missing data, failed chart API, JS/library unavailable, and unsupported payload shape.
8. Export links: add export metadata rows/fields for evidence mode, generated-at, source cutoff, model/config version, and freshness state.

## Exit Criteria Assessment

Phase 16 review is complete, but exit criteria are not fully met.

Met:

- Critical workflows expose many happy-path and non-happy-path states.
- Incomplete, stale, insufficient, warning, low-confidence, failed, retryable, and research-only concepts exist throughout the product.
- Detail pages for winner probability, setup lifecycle, and CERI have strong lineage and explainability.

Not yet met:

- High-impact accessibility remediation is still required for keyboard navigation, progress updates, and charts.
- Users cannot consistently distinguish complete/incomplete/stale/corrected/simulated/live-derived evidence at the list/export level.
- Financial-research disclaimers are present but not uniform at every decision/probability/position-size surface.

Recommended remediation order:

1. Fix global keyboard navigation and progress live semantics.
2. Add ticker chart summary/legend/data fallback.
3. Normalize evidence provenance badges/fields across HTML, JSON, CSV, and Markdown exports.
4. Standardize table captions/scoped headers and disclaimer placement.
5. Improve duplicate-action, stale-page, and async failure recovery messages.
