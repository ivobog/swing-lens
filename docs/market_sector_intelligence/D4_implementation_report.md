# Market Sector Intelligence D4 implementation

**D4 CHECKPOINT: PASS — focused browser-history repair and 423 distinct passing tests.**

22 October 2026. The first independent checkpoint correctly failed on browser Back restoration despite 422 automated tests passing. The subsequently authorized focused repair and added real-browser regression now pass. The original failure and its evidence are preserved below. This remains local engineering verification, not production readiness or activation. The architecture scan remains **GLOBAL FAIL** with exactly the same 18 clean-D3 findings; D4 adds none.

## Focused repair and final checkpoint

The repair changes only `app/static/market_sector_intelligence.js`, the existing browser test file, and this report relative to the failed checkpoint. The complete D4 delivery remains the same fourteen-file manifest. No dashboard redesign, scoring/rotation change, schema, acquisition, worker or pipeline change was made.

### Correction and before/after proof

The original `popstate` handler retained the previous ETF when the destination URL omitted `etf`. The handler now assigns either the destination's validated ETF or the established XLK default. A single frontend defaults object serves initialization and missing-parameter restoration for ETF, horizon, mode, snapshot and compared symbols. ETF/symbol spelling is normalized consistently with the existing server validation; direct-link and invalid-query server behavior is unchanged.

The handler updates controls and rerenders dependent views without calling `pushState` or `replaceState`. It keeps loaded panels and invokes the cached chart reader only when the restored horizon changes. D3 is never requested by same-document Back/Forward or ordinary widget changes. Saved history still uses cached persisted snapshots and its existing bounded detail read if needed.

Added one browser test: `tests/test_market_sector_intelligence_browser.py::test_browser_history_restores_default_explicit_and_optional_url_state`. It uses the existing disposable fixture and actual routes/engines. The test was first run against the original JavaScript: **1 failed, 8 deselected**, 33.46 s, at the exact XLK-expected/XLF-actual assertion. After the repair: **1 passed, 8 deselected**, 43.06 s. The complete nine-test browser rerun independently passed afterward.

| Real browser observation | URL state | Control and drilldown | History length |
|---|---|---|---:|
| Initial | `/sector-rotation`, no query | XLK / Technology | 2 |
| Select XLF | Explicit `etf=XLF` | XLF / Financials | 3 |
| Back | `/sector-rotation`, no query | **XLK / Technology** | 3 |
| Forward | Explicit `etf=XLF` | **XLF / Financials** | 3 |
| Back through optional controls | No query; horizon 126, relative mode, no snapshot, default ETF comparison set | XLK / Technology; 127 line-value rows | 6, unchanged by navigation |
| Direct XLF link → Settings → Back | `/sector-rotation?etf=XLF` | **XLF / Financials** | 4, unchanged by Back |

The regression also verifies restored ETF checkboxes and plotted/table selection, snapshot removal and PREVIEW source, mode and exact horizon, no duplicate history entries, zero page errors/failed requests, no forbidden calls or DML, and zero additional D3 requests during widget/history controls. Only one new chart GET occurs for the previously uncached five-session horizon; returning to 126 uses its loaded cache. Returning from Settings may initialize one new document and one D3 read, never an extra per-widget calculation.

Actual post-repair screenshots `history-default-back.png` and `history-deep-link-back.png` were inspected and show matching XLK/XLF selectors and drilldowns. The existing desktop/mobile and six degraded-state browser journeys pass as well. JUnit properties record the actual URLs, control values, detail headings and history lengths above.

### Final fresh gates

| Gate | Result |
|---|---|
| Inherited non-PostgreSQL | **373 passed**, 262.83 s. |
| Disposable PostgreSQL migration/concurrency/immutability | **1 passed**, 17.31 s. |
| D4 backend | **35 passed**, 50.17 s. |
| D4 browser, including new regression | **9 passed**, 238.59 s. |
| Node-native JavaScript | **5 passed**, zero failures/cancellations/skips. |
| Deduplicated total | **423 passed = 374 inherited + 35 backend + 9 browser + 5 JavaScript**; zero final failures/errors/skips and zero canonical duplicate identities. |
| Ruff and syntax | All eight Python files pass Ruff; dashboard `node --check` passes. |
| Whitespace | Working and full staged diff checks pass. |
| SQL bounds/safety | Actual chart HTTP test: **24 SELECTs, zero DML/autoflush**, max twelve sources × 127 observations, at most 1,524 normalized points. Exceptional cleanup and cross-Session isolation probe passes again. |
| Security | **206 routes**, **52 unsafe**; all complete classification records match unchanged clean D3: 48 NORMAL_ONLY, 2 CERTIFICATION_SESSION_SCOPED, 2 READ_ONLY. |
| Alembic/schema | One head, **0093_market_sector_rotation**; no migrations, models, schema or registration changes. Disposable PostgreSQL test exercises the actual migration chain and immutable/concurrent capture semantics. |
| Architecture | Fresh D3 and D4 scans both exit **1**, **GLOBAL FAIL**; identical complete diagnostics: **5 writer + 13 transaction**, zero additions/removals. No suppression or unrelated remediation. |
| Protected source/identities | Seven old GUI and nine old API function ASTs unchanged. D1–D3, protected stock/downstream/acquisition/pipeline sources, immutable stored-history implementation and configuration identities unchanged. |

Fresh canonical JUnit inputs are `repair-inherited.xml`, `repair-postgresql.xml`, `repair-backend.xml`, `repair-browser.xml` and `repair-node.xml`, under ignored `output/playwright/market-sector-intelligence-d4/`. `repair-verification-summary.json` records all 423 exact identities and observed properties. The focused positive test and five focused Node results overlap these identities and add zero to the total. The deliberate pre-fix failure is retained in `repair-history-before.xml`, not counted as a passing final result. Existing Starlette/httpx and Alembic path-separator warnings remain.

The chart fixture measured **4.0152755 s** and **99,329 response bytes**. Browser fixture RSS was **241,086,464 bytes**; its **33.422 s** desktop property measures the entire scripted journey. These concurrent local synthetic measurements are not live performance claims. `repair-sql-scope.json` again proves 320 → 127 → 320 and six-session scope restoration, a separate Session retaining 320 rows, cleanup after RuntimeError/BaseException and loader/validation errors, restored autoflush/listener state and a subsequent COMPLETE request, with SELECTs only.

### Checkpoint Git boundary

Branch: `feature/market-sector-intelligence-d4-dashboard`. Worktree: `C:/Users/Ivica/.codex/worktrees/market-sector-intelligence-d4/SwingLens`. Exact D3 parent: `20cad796fb8844ed91ae5edc74501fccacdad598`. The intended checkpoint contains exactly the fourteen files below, including the focused fix, regression and report, with subject `feat(market-sector-intelligence): complete D4 dashboard`. Its complete staged diff and blob identities are reviewed before commit; generated screenshots, XML, logs, snapshots, local databases and other artifacts are excluded. The final response records the resulting commit SHA and post-commit clean status; the report cannot contain its own commit's SHA.

D1/D2/D3 remain clean at their recorded checkpoint SHAs. Original checkout, main and every other branch ref are preserved; full original-checkout status/index/diff/content fingerprints are compared again after commit. No operational database or saved evidence was changed. Test-only captures occur solely during disposable fixture setup, using the existing authorized helper.

**D4 is suitable as a committed engineering baseline for a separately authorized final integration.** Integration itself was not started. Production schema activation, real ETF/SPY/QQQ cache/basis/volume coverage, operational capture authority, sufficient real saved history and live performance remain unverified. No push, PR, merge, deployment, provider acquisition, production migration/capture or NORMAL restart occurred.

## Original independent checkpoint — failed before repair

**Decision: FAIL.** The requested browser back/forward acceptance gate fails. Staging and the checkpoint commit were withheld. No implementation fix, additional feature work or integration was performed.

### Blocking defect: default-URL history restoration

Reproduced twice in the actual disposable application through a separate headed Playwright CLI browser:

1. Open `/sector-rotation` without query parameters. The selector is `XLK`, and the detail heading is `XLK · Technology`.
2. Select `XLF`. The URL gains `etf=XLF`, and the detail heading becomes `XLF · Financials`.
3. Press browser Back. The URL returns to `/sector-rotation` with an empty query, but both selector and detail still show `XLF`.
4. Forward correctly returns to the explicit XLF URL, but does not repair the failed Back transition.

Cause: `app/static/market_sector_intelligence.js:398` assigns `state.etf` on `popstate` only when the URL contains an allowed ETF. The original parameter-free history entry contains no ETF, so the previous selection survives instead of restoring the server's XLK default. Initial state is not written into that history entry. The displayed sector therefore disagrees with the state restored by browser Back. The existing eight browser tests cover deep links but do not assert this transition.

Ignored evidence: `checkpoint-history-initial.txt` records XLK and an empty query; `checkpoint-history-back.txt` records XLF and the same empty query. The separate CLI snapshots preserve the rendered states. These files are under `output/playwright/market-sector-intelligence-d4/` and are excluded from Git.

### Fresh automated and static gates

| Gate | Independent checkpoint result |
|---|---|
| Inherited non-PostgreSQL tests | **373 passed**, 355.98 s, one existing Starlette/httpx warning. |
| Disposable PostgreSQL migration/concurrency/immutability | **1 passed**, 32.58 s, three existing warnings. Safety helper and verified disposable database used; no application database migrated. |
| D4 backend | **35 passed**, 99.93 s, one existing warning. |
| D4 browser | **8 passed**, 230.88 s, one existing warning. |
| Node-native UI tests | **5 passed**, zero failures, cancellations or skips. |
| Exact identity aggregate | **422 distinct**, zero duplicate identities, failures, errors or skips across the five fresh JUnit inputs. Ad hoc checkpoint probes are excluded from this count. |
| Ruff / JavaScript | All eight Python files pass Ruff; dashboard `node --check` passes. |
| Whitespace | Working diff and all ten new text files pass. `git diff --cached --check` also passes against the deliberately empty index; no staged-content acceptance is claimed. |
| Routes/security | **206 routes**, **52 unsafe**. All 52 complete classification records exactly match clean D3: 48 NORMAL_ONLY, 2 CERTIFICATION_SESSION_SCOPED, 2 READ_ONLY. |
| Alembic/schema | One unchanged head: **0093_market_sector_rotation**. No migration, model, schema or registration changes. |
| Architecture | Clean D3 and D4 both exit **1**, **GLOBAL FAIL**. Complete diagnostics are identical: **5 writer + 13 transaction**, zero added/removed. The exact list below is unchanged. |
| Protected source | Complete diff remains four existing files plus ten new files. All seven old GUI and nine old independent API function ASTs match D3. Protected configuration, scoring, D1–D3 engines, acquisition, workers and pipeline sources are unchanged. |

Fresh JUnit inputs: `checkpoint-inherited.xml`, `checkpoint-postgresql.xml`, `checkpoint-backend.xml`, `checkpoint-browser.xml`, `checkpoint-node.xml`. `checkpoint-verification-summary.json` records every exact test identity and observed properties. Tests ran concurrently with other checkpoint checks; timings are fixture observations under contention, not comparable production benchmarks.

### Additional SQL and browser acceptance evidence

The actual HTTP backend test again proves **24 SELECTs**, zero DML/autoflush, twelve source loads, **127 observations per source** and at most **1,524 normalized points**. Both date predicates are present in the actual PriceBar SQL. Revision queries are restricted to IDs of the already bounded selected bars. The representative chart response is **99,329 bytes** and took **10.0098335 s** in this concurrent run. Browser fixture RSS was **242,245,632 bytes**; the recorded **60.906 s** desktop property covers the entire scripted journey, not initial rendering.

An additional disposable SQL probe proves scope cleanup beyond the existing normal-exit regression. The same Session reads 320 ordinary bars, 127 within the D4 bound, 320 after a forced RuntimeError, six within a shorter subsequent scope, then 320 again. A second Session keeps all 320 bars while the first listener is active. Listener count and autoflush state are restored after ordinary exceptions, caught loader/validation errors and an uncaught BaseException. A subsequent valid chart build is COMPLETE. Every observed statement is SELECT. Evidence: `checkpoint-sql-scope.json`.

An additional browser probe deliberately held a real 21-session chart response, disabled cancellation only in the test browser, completed a newer five-session selection, then delivered the obsolete response. The display remained five sessions / six table rows. It also injected an HTML-looking state string into an otherwise actual API response: text and SVG labels retained the literal string, created zero image elements and executed no handler. This probe recorded zero page errors, failed requests or HTTP errors, and exactly one technical-preview resource request throughout its widget/history interactions. Evidence: `checkpoint-browser-probe-result.txt`.

Fresh screenshots were actually inspected: `checkpoint-desktop-top.png`, `checkpoint-desktop-cli.png`, `checkpoint-mobile-top.png`, the new full mobile capture, and representative partial/unavailable captures. Automated journeys also exercised all six degraded modes, keyboard focus, accessible numeric tables, tooltips, native scores and saved-source distinctions. This evidence does not override the Back-navigation failure.

The user explicitly clarified the checkpoint's network wording: **“Allow read-only dashboard GETs; forbid provider calls and writes.”** Horizon/history-limit GETs are evaluated under that rule. The independent manual fixture recorded 363 SELECTs across three page loads and their read-only interactions, zero forbidden calls and no DML. No D3 request was added by widget changes. The named browser and dedicated loopback fixture server were stopped. Nine newly generated CLI YAML files were moved into ignored evidence; existing tracked artifacts were preserved.

### Git outcome and isolation

Branch remains `feature/market-sector-intelligence-d4-dashboard`; worktree remains `C:/Users/Ivica/.codex/worktrees/market-sector-intelligence-d4/SwingLens`; HEAD remains exact D3 parent `20cad796fb8844ed91ae5edc74501fccacdad598`. **No new commit, no staged files; exactly the fourteen-file manifest below remains uncommitted.** Only this report changed during checkpoint review; implementation files were not repaired.

D1/D2/D3 remain clean at their recorded SHAs. All branch refs match the pre-checkpoint snapshot, including main at `aeb0ef4b32220ef178a4a0a810086a3b043a7abb`. The original checkout's branch, HEAD, main, status/index/diff hashes and complete content hash match before/after for all 1,422 dirty/untracked files and 468,379,433 bytes. Its content SHA-256 remains `ce20e794da9c585216230aa702608ea877c101d9ce3478338f27306a17792a99`.

**D4 is not yet an accepted baseline for final feature integration.** Correct the isolated history-state defect and verify that transition in a separately authorized repair/checkpoint. Production schema activation, real cache/basis/volume coverage, operational capture authority, sufficient saved history and live performance/integration remain unverified. No push, PR, merge, deployment, provider acquisition, production migration/capture or NORMAL restart occurred.

## Implementation-stage baseline and Git boundary

- Branch: `feature/market-sector-intelligence-d4-dashboard`.
- Worktree: `C:/Users/Ivica/.codex/worktrees/market-sector-intelligence-d4/SwingLens`.
- Exact parent and unchanged HEAD: `20cad796fb8844ed91ae5edc74501fccacdad598`.
- New managed worktree created from the exact clean D3 commit; original dirty changes were excluded.
- D1 remains clean at `9ad285e6cfc8c920f38c090960b7ac1907d25466`; D2 remains clean at `41e8eb3e33cca701952fb716b597da71526fd278`; D3 remains clean at the parent above.
- Original checkout remains on `codex/ceri-authority-remediation`, HEAD `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. Pre/post branch, HEAD, main ref, full status, index/working diffs and content fingerprints match for all **1,422 changed/untracked files, 468,379,433 bytes**.
- `main` remains `aeb0ef4b32220ef178a4a0a810086a3b043a7abb`. No existing branch ref moved.
- **All 14 D4 files remain uncommitted; index empty.** No commit, push, PR, merge, deployment, provider request, production migration or NORMAL restart.

Inputs read: supplied vision, handoff, SRS, SDD, implementation prompt and D3 report; actual local D1/D2/D3 reports, services, routes, serializer contracts, templates, navigation and assets. Local D3 code was authoritative. The initial planned source boundary was recorded in this file before implementation edits: new chart reader, additive GUI/API GETs, one nav item, feature-owned template/assets and focused tests, with no protected service/configuration/schema changes.

## Complete change manifest

| File | Change |
|---|---|
| `app/services/market_sector_intelligence_chart_service.py` | New bounded read-only PIT price-chart reader, independent period readiness, exact return/normalization helpers and evidence identities. |
| `app/routers/market_sector_intelligence_routes.py` | Adds only chart GET/import; all nine pre-existing function ASTs unchanged. |
| `app/routers/gui_routes.py` | Adds only dashboard GET/imports; all seven pre-existing function ASTs unchanged. |
| `app/templates/partials/_nav.html` | One Market Sector Intelligence link, preserving all old entries. |
| `app/templates/market_sector_intelligence.html` | New independent accessible dashboard shell and controls. |
| `app/static/market_sector_intelligence.js` | New fault-isolated API composition, SVG plots, tables, saved trails, native diagnostic mapping, query state and drilldown. |
| `app/static/market_sector_intelligence.css` | New feature-scoped responsive styles; existing shared CSS unchanged. |
| `tests/test_route_security.py` | Expected route total 204 → 206; unsafe inventory/classifications unchanged. |
| `tests/test_market_sector_intelligence_charts.py` | 32 pure math, session/calendar, evidence, invalid-source, selection, disabled and identity tests. |
| `tests/test_market_sector_intelligence_dashboard.py` | Three actual cached-loader/API/GUI tests: bounded SQL, no autoflush or mutations, PIT revision replay, upload independence, validation and configuration states. |
| `tests/market_sector_dashboard_fixture.py` | Test-only disposable browser app; actual routes and engines, synthetic caches and explicitly authorized test captures. Not imported by product code. |
| `tests/test_market_sector_intelligence_browser.py` | Nine browser journeys: desktop, mobile, six degraded modes and the new default/explicit/optional-state Back/Forward regression; keyboard, sparse history, no technical recalculation and resource evidence. |
| `tests/market_sector_intelligence_dashboard.test.cjs` | Five Node-native pure UI tests for zero/one/sparse/consecutive trails, config/version/basis boundaries, V5 suppression, sorting and cross-panel warnings. |
| `docs/market_sector_intelligence/D4_implementation_report.md` | This report. |

Four pre-existing tracked files modified, ten new files. No dependency manifest, new model/table, migration, writer/transaction registration, provider, scheduler, stock-scoring or worker change. Existing tracked Playwright artifacts from D3 were preserved. Four implementation-stage and nine checkpoint CLI YAML snapshots were moved into ignored evidence.

## Delivered page and contracts

`GET /sector-rotation` renders a lightweight Jinja shell with `active_nav=market-sector-intelligence`, without a DB dependency, run ID, upload or blocking technical calculation. Existing run-sector, Market Regime and Settings routes retain their original implementation. One navigation entry and a working Settings crosswalk anchor are present.

The page independently loads D1 coverage, D2 current rotation, a bounded saved-history index/details, D3 current technicals and D4 chart prices. A failed or slow technical request does not block other panel responses. D3 executes once per page load or explicit reload, never on ETF selection, sort, saved-date selection, chart horizon or heatmap-mode changes. Browser tests verify zero additional technical requests during these controls. Chart requests have abort/generation guards so obsolete horizons cannot overwrite the latest selection.

The four status cards and panel headers retain separate source/session/read/capture metadata. Saved rotation is PERSISTED; current rotation is PREVIEW; current technicals are TECHNICAL_PREVIEW; prices are CHART_PRICE_HISTORY. Different sessions, bases or D2 methodology/configuration produce explicit warnings; no cross-panel blended conclusion or score is calculated.

The dashboard includes:

- All-eleven-sector table with authoritative D2 rank/state, RS5/21/63, momentum PP, absolute ETF63 return, last session and reasons. Stable accessible sort reorders copies without changing ranks.
- Native D2 RS63 (%) versus momentum (percentage points) scatter with zero axes. State labels come from D2; no frontend quadrant classification. Explicit custom/RRG-inspired, non-JdK disclosure.
- Saved-date selection and modest selected-ETF trails, from actual immutable D2 snapshot details only.
- Absolute or ratio-relative 5/21/63/126-session heatmap and common-start normalized ETF/SPY lines, using only D4 prices.
- Native current V4 final and V5 raw/adjusted scores, classifications, actions, confidence, leadership, readiness and descriptive D3 delta. V5_COHORT_NOT_COMPARABLE suppresses every final V5 display/comparison while retaining valid V4.
- Sector drilldown with separate price/rotation/technical evidence, native components/warnings, full source identities/cutoffs and stored observation table. Saved JSON links address the selected snapshot, never a substituted current preview.
- ETF/horizon/mode/snapshot URL state, browser back handling, keyboard/tap plot inspection, visible focus, semantic numeric alternatives and deliberate table scrolling. Snapshot query IDs are limited to the JavaScript exact-integer range.

There is no fetch/capture/migrate control. Reload means another read of existing cached/stored evidence. Stock-engine classifications/actions are labeled research diagnostics, not calibrated ETF forecasts or trading recommendations.

## Chart API, mathematics and source boundaries

`GET /api/market-sector-intelligence/charts/price-series?horizon=126&symbols=XLK,XLF`

- Horizon: exactly 5, 21, 63 or 126 sessions; default 126.
- Symbols: unique comma-separated members of the authoritative D1 eleven-ETF universe; omitted means all eleven. Case is normalized; unknown/blank/duplicate/overlong selections and invalid horizons fail before SQL. SPY is always added once; QQQ never enters chart reads.
- One request-local completed-US-session UTC cutoff; every preferred-loader call receives that same `max_session`, `as_of` and daily timeframe, with no calculation context/acquisition authority.
- A scoped `do_orm_execute` listener adds a minimum-date predicate only to PriceBar SELECTs on that request's Session. It is removed in finally, within no_autoflush. The canonical preferred reader and revision projection are unchanged. Tests prove both >= start and <= end SQL predicates, then prove an ordinary reader still returns its full 320 bars after D4 finishes.
- At most twelve preferred symbol loads; 126 sessions plus anchor, 127 selected price observations per symbol. At most **1,524 normalized points** in full output. No full OHLCV or source-manifest arrays in JSON.
- Actual selected price basis and PIT manifest symbol/timeframe/cutoff/max-session/acquisition/fingerprint contracts are validated. Duplicate, non-session/future labels, post-cutoff receipts and invalid OHLC/ranges fail honestly. Each window needs every exact exchange session in its own span, not the last N available rows.
- Windows are independently ready: an absent or invalid six-month anchor does not null a valid shorter span. No forward-fill, interpolation or shifted common baseline.
- ETF/SPY price bases must agree for relative returns and normalized comparison. Separately valid ETF absolute returns may remain when SPY is unavailable or incompatible, with explicit reasons and no misleading ETF/SPY line.
- UTC receipt/revision replay is delegated to the existing canonical reader and tested against real SQLite PriceBar/Revision rows. Nonfinite financial values are rejected; final canonical JSON is finite-or-null.
- Material identity includes selected close/validation state, immutable source fingerprints, actual basis, session/horizon, method/config/calendar identity. Incidental clock drift and DB IDs are excluded; eligible revisions change identity.

For exact start s=t-N:

```text
Absolute ETF % = 100 × (E[t] / E[s] − 1)
SPY-relative % = 100 × ((E[t]/SPY[t]) / (E[s]/SPY[s]) − 1)
Normalized E[u] = 100 × E[u] / E[s]
```

The relative result is not arithmetic subtraction of two returns. Tests use 20% ETF versus 10% SPY, yielding 9.090909...%, not 10%. These are selected-basis price returns, not certified total returns. Historical prices are visible as of the current read cutoff and are not contemporary archived D2 signals.

## Saved-history semantics

Initial history limit is eight captures, optionally twelve; detail retrieval has concurrency two, plus at most one explicitly selected ID outside that index. No D2 capture or price-based historical rotation reconstruction occurs.

Trails contain only finite native rows from actual PERSISTED snapshots. Zero captures produces no trail; one produces one marker. Segments join only verified consecutive US sessions with matching algorithm/configuration and basis. Sparse captures, incompatible cohorts and dates outside the chart calendar's verified 127-session continuity range stay separate points with a gap/segregation explanation. No synthetic point or continuous line is created across unknown dates. Selecting a historical snapshot limits the visible trail to that date. The current preview has a distinct hollow marker and never becomes an archive record.

The disposable browser ready fixture actually captures **16, 19 and 21 October 2026** through the unchanged D2 repository's explicitly authorized test helper. The Friday-to-Monday pair is contiguous in the exchange calendar; the missing Tuesday separates the later point. Pure UI tests separately cover one capture and incompatible method/config/basis cohorts. Nothing was captured in an operational database, and no V4/V5 history is fabricated.

## Earlier implementation-stage verification

**422 distinct tests passed: 374 inherited + 43 new Python + 5 new Node tests. Zero failures, errors or skips in the final evidence set.**

| Gate | Actual result |
|---|---|
| All inherited non-PostgreSQL tests | **373 passed**, 173.35 s; all 264 D1, 65 D2 and 44 D3 tests, including route/security and protected scoring regressions. |
| Disposable PostgreSQL | **1 passed**, 20.97 s; full D2 migration chain, additive schema, concurrent/idempotent capture and immutability. Verified named disposable database created/dropped; application DB not selected. |
| New D4 chart/API/GUI | **35 passed**, 31.77 s. |
| New browser suite | **8 passed**, 105.10 s; 1440px desktop, 390px mobile, keyboard and six degraded scenarios. |
| Final UI/query contract rerun | **5 passed**, 64.69 s, six intentionally deselected; three API/GUI tests and two desktop/mobile identities overlap the prior suites and add zero to the distinct count. A preceding two-UI rerun also passed and is excluded from the aggregate. |
| Pure UI Node tests | **5 passed**, zero failures/cancellations/skips. |
| Ruff | All **8** changed/new Python files pass. |
| JavaScript syntax | `node --check` passes. |
| Whitespace | Working diff and every new text file checked; no errors. Index remains empty. |
| Routes/security | **206 total**; **52 unsafe** entries all classified, unchanged capability counts: 48 NORMAL_ONLY, 2 CERTIFICATION_SESSION_SCOPED, 2 READ_ONLY. New page/API are GET-only. |
| Alembic | Exactly one unchanged head, **0093_market_sector_rotation**; no D4 migration. |
| Architecture | Clean D3 and D4 both exit **1**, **GLOBAL FAIL**, exact same 18 findings: 5 writer + 13 transaction. Zero additions/removals/suppression. |
| Protected source | Only the four shared integration/nav/test files in the manifest differ among existing tracked files. All old GUI/API function ASTs match. Configurations, D1 mapping/coverage, D2 math/capture/storage/schema, D3 adapter/service, all stock/downstream/acquisition/worker/pipeline files remain unchanged. |

Final canonical JUnit inputs, under ignored `output/playwright/market-sector-intelligence-d4/`: `inherited.xml`, `postgresql.xml`, `d4-backend-final.xml`, `browser-final.xml`, `ui-pure-final.xml`. The five matching identities in `ui-contract-final.xml` provide later UI/query evidence without inflating counts. `verification-summary.json` records all 422 exact identities, zero canonical duplicates and final observed properties.

Existing Starlette/httpx and two Alembic path-separator warnings remain. Preliminary development failures were corrected rather than counted as final passes: integer Literal URL parsing, an incomplete fixture clock stub and a shared SQLite connection under concurrent GETs. The final browser harness uses separate pooled connections to disposable SQLite files. No protected product service was changed to accommodate a fixture.

### SQL, resource and browser evidence

The actual chart HTTP fixture proves **24 SELECTs**, no DML, no upload queries/autoflush and **12 symbols maximum**, with 127 selected bars per symbol. Pending UploadRun remains unflushed. Provider/socket, queue, capture, technical artifact, stock scoring and context mutation spies are forbidden and unused. Unrelated uploads leave identical chart output. HTML/config/invalid-parameter/disabled chart journeys issue zero SQL.

The final measured chart request took **1.6571955 seconds** and returned **99,329 JSON bytes**. The browser's fully populated chart transfer was 99,629 bytes including HTTP transfer overhead. Browser trace entries show chart completion around **3.221 seconds**, independently before technical preview completion around **10.912 seconds** in that concurrent fixture run. Two rapid horizon changes leave the six-point five-session line authoritative; an obsolete request cannot overwrite it.

The recorded desktop property named `desktop_load_seconds` measures **17.265 seconds for the complete scripted journey including chart and keyboard controls**, not pure initial render. Fixture-process RSS was **244,924,416 bytes**, including pytest, five disposable caches, SQLAlchemy and native model state. A separate CLI mobile inspection observed DOMContentLoaded at **124.3 ms**, viewport width 390 and document width 375 (desktop-browser scrollbar, no horizontal overflow), and exactly one technical-preview resource request. These observations are synthetic local measurements, not isolated allocation, production throughput, latency or availability guarantees.

Screenshots actually inspected:

- `desktop.png`: full 1440px dashboard, native models and actual saved fixture captures.
- `mobile.png`: 390px keyboard/select/table journey and accessible numeric alternatives.
- `desktop-cli.png` / `mobile-cli.png`: separately inspected viewport captures via the Playwright skill/CLI.
- `empty.png`, `partial.png`, `qqq.png`, `disabled.png`, `unactivated.png`, `unavailable.png`: truthful degraded-state browser evidence.

Browser tests exercise unknown saved IDs without fallback, saved-session warnings, native chart tooltips with units/source/as-of, current-to-saved drilldown, last-request authority, Settings navigation, focus and source identities. All final journeys have zero JavaScript page errors, no horizontal mobile overflow and zero forbidden calls. The named CLI browser and dedicated loopback fixture server were stopped. Generated logs, screenshots, XML, snapshots and synthetic DB artifacts remain ignored and excluded from the D4 change set.

### Exact architecture baseline comparison

The complete diagnostic lists match on clean D3 and D4. No census registration, suppression or unrelated remediation was performed.

```text
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/market_calculation_context_service.py', 'create_pipeline_ceri_certification_context', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/decision_evidence.py', 'lock_setup_publication_sources', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/decision_evidence.py', 'prefetch_setup_validation_sources', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/repository.py', '_setup_source_evidence_ids', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/repository.py', 'visit', 'add')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/ceri/batched_job_handlers.py', 'execute_provider_ingest_batch_job', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'autonomous_session')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'begin_nested')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'flush')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'rollback')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/market_calculation_context_service.py', 'create_pipeline_ceri_certification_context', 'flush')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/setup_lifecycle/snapshot_builder.py', '_close_clean_parent_transaction_before_setup_build', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/setup_lifecycle/snapshot_builder.py', 'bounded_verification_checkpoint', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/setup_lifecycle/snapshot_builder.py', 'capture_snapshots_for_run', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_process_pool', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_process_pool', 'rollback')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_pure_sequential', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_pure_sequential', 'rollback')
```

## Reproduction

Run from the D4 worktree with the existing shared Python environment. Inherited file selection is the D3 checkpoint selection (all 25 files), not a whole-repository test claim.

```powershell
$d4Python = 'C:/Users/Ivica/Documents/SwingLens/.venv/Scripts/python.exe'
& $d4Python -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_config.py tests/test_market_sector_intelligence_coverage.py tests/test_market_sector_intelligence_routes.py tests/test_sector_rotation_config.py tests/test_sector_taxonomy.py tests/test_us_market_calendar.py tests/test_session_temporal_integrity_remediation.py tests/test_sector_etf_rotation_service.py tests/test_sector_rotation_service.py tests/test_sector_rotation_policy.py tests/test_sector_rotation_routes.py tests/test_gui_phase1.py tests/test_settings.py tests/test_route_security.py tests/test_technical_score_v4.py tests/test_technical_v5_calibration.py tests/setup_lifecycle/test_snapshot_builder.py tests/winner_probability/test_feature_schema.py tests/test_market_sector_intelligence_rotation_math.py tests/test_market_sector_intelligence_rotation_service.py tests/test_market_sector_intelligence_rotation_storage.py tests/test_market_sector_intelligence_rotation_pit.py tests/test_market_sector_intelligence_technical_adapter.py tests/test_market_sector_intelligence_technical_service.py tests/test_market_sector_intelligence_technical_pit.py --junitxml=output/playwright/market-sector-intelligence-d4/inherited.xml
& $d4Python -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_charts.py tests/test_market_sector_intelligence_dashboard.py --junitxml=output/playwright/market-sector-intelligence-d4/d4-backend-final.xml
& $d4Python -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_browser.py --junitxml=output/playwright/market-sector-intelligence-d4/browser-final.xml
node --test tests/market_sector_intelligence_dashboard.test.cjs
& $d4Python -B -m ruff check app/routers/gui_routes.py app/routers/market_sector_intelligence_routes.py app/services/market_sector_intelligence_chart_service.py tests/test_market_sector_intelligence_charts.py tests/test_market_sector_intelligence_dashboard.py tests/market_sector_dashboard_fixture.py tests/test_market_sector_intelligence_browser.py tests/test_route_security.py
node --check app/static/market_sector_intelligence.js
& $d4Python -B -m alembic heads
& $d4Python -B scripts/check_architecture_registry.py --check
git diff --check
```

PostgreSQL uses the unchanged test `tests/integration/test_market_sector_intelligence_rotation_postgresql.py` with the existing `resolve_test_postgres_admin_url` safety helper. A child process reads local credentials without printing them, sets DATABASE_URL to administrative postgres and SWINGLENS_DATABASE_SAFETY_CONTEXT=DISPOSABLE_TEST, and runs only the fixture's verified separate `swinglens_pytest_*` database. Production migrations and product capture CLI were not executed.

For an interactive fixture only, launch `tests/market_sector_dashboard_fixture.py` in a separate process with the D4 root on PYTHONPATH. It binds loopback port 8768, disables application lifespan/workers, creates only disposable synthetic databases and guards providers/writers after explicit fixture setup. This is test infrastructure, never NORMAL or a product server.

## Acceptance matrix

| Gate | Evidence |
|---|---|
| AT-01 | Exact clean D3 parent, isolated D4 branch/worktree; matching original/D1/D2/D3/main preservation checks. |
| AT-02–03 | SQL-free page/nav without run; eleven sectors from D1, Settings link and preserved crosswalk. |
| AT-04–05 | Four real exact session anchors, ratio-versus-arithmetic example, normalized 100 common start, independent missing windows and no filled gaps. |
| AT-06 | Native D2 coordinates/state/rank, zero-coordinate UI fixture and explicit non-JdK label. |
| AT-07–08 | Pure zero/one/sparse/adjacent/mismatched trails; actual browser 16/19/21 saved captures, gap separation and historic selection. |
| AT-09–10 | Native D3 display, full missing-cohort V5 gate, separate provenance and cross-session/config/basis warnings. |
| AT-11 | Actual empty, partial, missing QQQ, disabled, unactivated D2 schema and technical HTTP failure journeys. |
| AT-12 | Actual bounded 24-SELECT chart GET, pending ORM object, forbidden write/provider/queue/context/artifact/capture guards. |
| AT-13–14 | Desktop/mobile/keyboard, tooltip, deep-link, validation, safe-text and security checks pass. The original default-ETF Back failure is preserved above; the focused repair and new real-browser regression now pass all three history scenarios. |
| AT-15–16 | All 374 inherited tests including disposable PostgreSQL; protected tracked diff/AST checks and unchanged identities. |
| AT-17 | Exact same 18 architecture findings; GLOBAL FAIL, D4 delta zero. |
| AT-18 | Source/query/point/JSON bounds, real HTTP/browser timing/RSS and no D3 widget recalculation. |
| AT-19–20 | No schema or writer; complete fourteen-file report, screenshots and exact identities. Implementation stopped uncommitted; the separately authorized checkpoint reviews and commits exactly the same fourteen-file scope. |

## Preserved identities and remaining limits

Legacy sector hash: `26e0f6ea6099ea73ed29df8f7eb7260bbcfde75e0d560e87e485eb1624e8afcb`; legacy ETF blending remains **false**. Independent D2 digest: `4488f53e320f43daba6a257eea75e688e13b444b3bf911ecdff5eab9e946dfaa`; independent feature remains **true**. All configuration files, architecture registries and old snapshots remain unchanged.

Production schema activation, actual cached ETF/SPY/QQQ availability/basis/volume, operational capture authority, sufficient real saved history, live integration and production performance remain unverified. D3 has no archived technical history. Price history is not archived historical signal evidence or certified total return. The page is a research tool using stock model interpretations.

**Current stop state: D4 CHECKPOINT PASS after the focused history-state repair and 423 distinct passing tests. Exactly fourteen intended files form one checkpoint commit on the isolated D4 branch with the exact D3 parent. Stop after the checkpoint; no integration, push, PR, merge, deployment, provider acquisition, production migration/capture or NORMAL restart.**
