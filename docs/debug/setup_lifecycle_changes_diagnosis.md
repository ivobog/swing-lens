# Setup Lifecycle Changes Diagnosis

Date: 2026-08-13
Phase: 1 (diagnosis only)
Repository checkpoint: `2d3e4f1` on `codex/ibmi-phase1-correctness`

## Executive conclusion

The global empty page is a route regression, not an absence of persisted changes. The live
database has a latest canonical snapshot date of 2026-08-13, but both visible event streams
end on 2026-08-12. `GET /api/setup-lifecycle/changes` returns 51,267 rows; the global HTML
route silently converts the latest snapshot date to an exact event-date filter and therefore
renders zero rows.

Run 101 is a different case. Its completed evaluation captured 200 snapshots but produced no
material `SignalChangeEvent` and no lifecycle opening/transition event. Its 51 lifecycle rows
are `CANONICAL_REVISION` audit events, intentionally excluded from Market Changes. Therefore
run 101's empty result is correct for the persisted evidence and is not caused by its 149
noncanonical snapshots.

The run-scoped implementation nevertheless has independent confirmed bugs. It ignores all
query parameters, its shared UI actions and pagination escape the run path, and its query uses
current-market canonical/current-version guards that hide immutable history attributable to
older runs. Run 82 proves the latter: the current query returns 290 lifecycle rows and zero
signal rows, while the run owns 3,904 material signal changes whose snapshots were later
superseded.

No production behavior was changed before this report was completed.

## End-to-end data flow

1. `SetupLifecycleSnapshotCaptureService.capture_snapshots_for_run()` loads a completed
   `UploadRun` and persists one immutable `SetupSignalSnapshot` per eligible source row.
   `snapshot_builder.py:156-157` assigns `run_id=context.raw_row.run_id`; the capture service
   retains that lineage and evaluation ownership (`snapshot_builder.py:565-642`).
2. `SetupLifecycleEvaluationService.evaluate_run()` creates a `LIVE` evaluation with
   `source_run_id=run_id`, then executes capture, canonicalize, change detection, signal alert
   evaluation, lifecycle episode evaluation, lifecycle alerts, and finalize in that order
   (`evaluation_service.py:109-230`). The handoff validation rejects snapshots owned by a
   different run (`evaluation_service.py:235-264`).
3. `SetupLifecycleCanonicalizer` groups snapshots globally by
   `(ticker, timeframe, data_as_of_date)`, selects one winner, and promotes it. Promotion
   demotes the prior canonical snapshot and records `superseded_by_snapshot_id`
   (`canonicalization.py:61-117`, `repository.py:267-292`). Canonicality is therefore not a
   per-run property.
4. `SetupLifecycleChangeDetector.detect_and_persist()` compares each selected snapshot with
   its prior canonical snapshot and persists material `SignalChangeEvent` rows linked through
   `current_snapshot_id` and `evaluation_run_id` (`change_detector.py:88-143`).
5. `SetupLifecycleEpisodeService` consumes selected canonical snapshots and persists immutable
   `SetupLifecycleEvent` rows. Logical same-episode/date/type revisions use
   `is_current_version`/`superseded_by_event_id` (`repository.py:598-617`).
6. `SetupLifecycleQueryService.changes()` merges:
   - current lifecycle `EPISODE_OPENED`, `STATE_TRANSITION`, and `PHASE_TRANSITION` events;
   - material signal changes whose current snapshot is globally canonical.
   It applies event-date, source-specific, snapshot, run, and semantic filters before combined
   keyset pagination (`query_service.py:106-195`, `query_service.py:1471-1630`).
7. The global route currently derives an implicit `as_of` from diagnostics before invoking the
   API function (`setup_lifecycle_routes.py:63-137`). The run route instead constructs only
   `SetupLifecycleFilters(run_id=run_id)` and fixed display defaults
   (`setup_lifecycle_routes.py:291-322`).
8. `_changes_template_context()` builds export and pagination URLs, currently with a fixed
   `/setup-lifecycle` base (`setup_lifecycle_routes.py:1297-1337`). The template hardcodes
   global quick-filter and Clear links (`setup_lifecycle.html:70-122`).

## Runtime and database evidence

The live app at `http://127.0.0.1:8000` returned HTTP 200 throughout. Its configured database
was PostgreSQL `swinglens` at `127.0.0.1:5432`.

### Source counts and dates

| Check | Result |
|---|---:|
| All snapshots | 16,467 |
| Canonical snapshots | 8,158 |
| All lifecycle events | 18,448 |
| Current visible lifecycle events | 4,891 |
| All signal changes | 70,810 |
| Canonical-visible signal changes | 46,376 |
| Latest canonical snapshot date | 2026-08-13 |
| Latest current lifecycle change date | 2026-08-12 |
| Latest canonical-visible signal change date | 2026-08-12 |
| Visible changes on 2026-08-12 | 305 lifecycle + 3,252 signal = 3,557 |
| Visible changes on 2026-08-13 | 0 |

The required SQL checks were run directly. Their decisive predicates/results were:

```sql
SELECT MAX(data_as_of_date)
FROM setup_signal_snapshots
WHERE is_canonical = TRUE;
-- 2026-08-13

SELECT MAX(effective_date)
FROM setup_lifecycle_events
WHERE is_current_version = TRUE
  AND event_type IN ('EPISODE_OPENED','STATE_TRANSITION','PHASE_TRANSITION');
-- 2026-08-12

SELECT MAX(c.effective_date)
FROM signal_change_events c
JOIN setup_signal_snapshots s ON s.id = c.current_snapshot_id
WHERE s.is_canonical = TRUE;
-- 2026-08-12
```

### Global route/API comparison

| Request | Applied query | API/page total | Rendered rows | Displayed date |
|---|---|---:|---:|---|
| `/api/setup-lifecycle/changes` | no date filter | 51,267 | 50 API items | null |
| `/api/setup-lifecycle/changes?as_of=2026-08-13` | exact event date | 0 | 0 | 2026-08-13 |
| `/api/setup-lifecycle/changes?as_of=2026-08-12` | exact event date | 3,557 | 50 API items | 2026-08-12 |
| `/setup-lifecycle` | route injects `as_of=2026-08-13` | 0 | 0 | 2026-08-13 |
| `/setup-lifecycle?as_of=2026-08-12` | explicit exact date | 3,557 | 50 | 2026-08-12 |

The executed SQL trace confirmed the distinction. Without `as_of`, the lifecycle branch has
only current/type predicates and the signal branch has only canonicality plus source filters.
With `as_of=2026-08-13`, SQLAlchemy added:

```sql
AND setup_lifecycle_events.effective_date = %(effective_date_1)s::DATE
AND signal_change_events.effective_date = %(effective_date_1)s::DATE
-- effective_date_1 = date(2026, 8, 13)
```

The browser snapshot showed `Data as of 2026-08-13`, `Total Changes 0`, the exact-date value
pre-filled in the filter, and an empty Candidate Changes state. The before screenshot is
`output/playwright/slse-lifecycle-global-empty-before.png`.

### Run 101

Run 101 is `COMPLETED`, has 200 source rows, and has two SLSE evaluation records. Evaluation
173 is the completed full evaluation:

| Metric | Result |
|---|---:|
| snapshots/read/captured/canonical selected | 200 / 200 / 200 / 200 |
| persisted run snapshots | 200 |
| currently canonical run snapshots | 51 |
| currently noncanonical run snapshots | 149 |
| detector `changed_count` / persisted signal changes | 0 / 0 |
| lifecycle events | 51 |
| eligible opening/transition lifecycle events | 0 |
| audit-only `CANONICAL_REVISION` events | 51 |
| query-service total | 0 |
| rendered rows | 0 |

The executed run query contained `setup_signal_snapshots.run_id = 101`; only the signal branch
also contained `setup_signal_snapshots.is_canonical IS true`. The HTML displayed 2026-08-13
only as a diagnostics fallback; no `as_of` filter was actually applied. A URL containing
`ticker=MSFT&sort=score&direction=asc&limit=1&cursor=1` still executed only `run_id=101` with
default sort/direction/limit/cursor.

### Historical supersession proof

Run 82 owns 469 snapshots across three dates. All 469 are now noncanonical. It owns:

| Stream | Attributable immutable rows | Current run query rows |
|---|---:|---:|
| eligible lifecycle events | 290 | 290 |
| material signal changes | 3,904 | 0 |
| combined | 4,194 | 290 |

Other examples show both current mechanisms lose run history: run 94 has 112 attributable
lifecycle events but only 107 current versions, and 2,514 attributable signal changes but
only 585 still-canonical changes. This is a persistent lineage/query mismatch, not a synthetic
edge case.

## Browser and URL evidence

Using a real Chromium browser:

- `/setup-lifecycle` rendered zero rows and the implicit 2026-08-13 date.
- `/runs/82/setup-lifecycle?ticker=ZZZZ&sort=score&direction=asc&limit=1` ignored every supplied
  query parameter: it rendered 50 default-sorted rows, an empty ticker control, and the
  default sort/direction.
- The run page's CSV/JSON links contained `run_id=82`, but only the fixed default filters.
- Quick filters and Clear linked to `/setup-lifecycle...`, escaping run scope.
- Next linked to `/setup-lifecycle?run_id=82&...&cursor=k1...` rather than the run path.
- Clicking Next navigated to the global route. That route does not accept `run_id`, injected
  `as_of=2026-08-13`, and rendered zero rows. The cursor-carried summary still said 290 while
  the item list was empty, demonstrating why filter/scope preservation is mandatory.

The populated-run before screenshot is
`output/playwright/slse-lifecycle-run82-before.png`.

Alert Center and Operations are intentionally global because no run-scoped equivalents or
run filters exist. Ticker timeline and episode/source links are entity evidence pages and may
remain global. Quick filters, Clear, pagination, and exports represent the current result set
and must preserve run scope.

## Root-cause classification

### Bug A: global page incorrectly defaults `as_of` — CONFIRMED

This is the primary global regression. Persisted rows and the unfiltered API are populated,
but the route maps the newest snapshot date to an exact event-date predicate. A canonical
snapshot day need not contain an event. The live 2026-08-13/2026-08-12 split is the exact
failure described in the handoff.

Regression: `e59673db194c23cb447d68770cc93a546374e347` (`Repair SLSE market changes and alerts`)
added `selected_as_of = as_of or latest_canonical_date` and forwarded it as `as_of`. The
parent implementation did not apply an implicit date.

### Bug B: run page does not use shared filters — CONFIRMED

The route signature accepts only path `run_id`, request, and DB. It never constructs the
shared list query from request parameters. FastAPI accepts unknown query strings at the HTTP
layer, but they cannot reach the service. Browser and code-spy evidence agree.

Origin: `ae084765254797150955494d88c4f4725a97c23d` (`Add setup lifecycle phase 9 APIs`)
introduced the fixed `SetupLifecycleFilters(run_id=run_id)` route. `46f7384b...` reused the
full shared template without adding forwarding. `3553511d...` expanded global/API filters
but left the run route unchanged. This is a latent implementation defect, not a new database
regression.

### Bug C: historical run uses current canonical/current-version semantics — CONFIRMED

The intended contract is attributable run history. Immutable snapshots/events are retained
indefinitely; the core SLSE document says every completed daily run becomes immutable setup
evidence, and the route is part of run-detail navigation. The single-run certification derives
expected rows from evaluation runs whose `source_run_id` equals the selected run. Global
canonical promotion is intentionally cross-run, so applying its later state to a historical
run is temporally unstable.

The signal branch's canonical guard hides 3,904 rows from run 82. The lifecycle branch's
`is_current_version` guard also hides superseded events (for example, 5 in run 94). Historical
scope must remove those two current-market guards without weakening them globally. Audit-only
canonical revision event types remain excluded.

Regression point: `e59673d...` changed Market Changes from an unguarded lifecycle-only list to
current lifecycle rows plus canonical signal rows. Those safeguards are correct for the new
global current-market contract but were applied to the already-existing run route without a
separate semantic mode. `3553511d...` retained them during the keyset rewrite.

This bug is unrelated to run 101's current empty result: run 101 has no eligible material or
transition rows even before those guards.

### Bug D: run-page pagination URL is wrong — CONFIRMED

`_changes_template_context()` always calls `_pagination(..., "/setup-lifecycle")`.
The run route also ignores `cursor`. Run 82 supplied a six-page current result, and browser
navigation proved Next escaped to the global route and did not deliver page 2.

Origin: `46f7384bf0ef43e49f8abe569f1d393d0bb19837` (`Add setup lifecycle phase 10 UI`)
introduced the shared context with the fixed global base path.

### Bug E: template navigation escapes run scope — CONFIRMED

Clear and every quick-filter URL are hardcoded to `/setup-lifecycle`; they leave a historical
run. Export links use the global API and currently retain only `run_id` plus fixed defaults,
not ignored active filters, and they cannot request historical semantics. Alert Center,
Operations, ticker timelines, episode detail, and source evidence have no run-scoped route and
are intentionally global.

Origin: `46f7384b...` introduced the hardcoded Clear, quick-filter URLs, and shared template
context. `e59673d...` added filter-derived export URLs but could only serialize the run route's
fixed filter dictionary.

### Combined keyset backend — NOT A BUG for stable scopes

A direct live-database sweep certified 54 combinations: nine supported sorts
(`latest_event_time`, `transition_priority`, `confidence`, `score`, `setup_score`, `velocity`,
`state_age`, `trigger_distance`, `sector_rank`) x asc/desc x mixed/lifecycle/signal sources.
For 2026-08-12, every paginated union exactly equaled the SQL result set:

- mixed 3,557;
- lifecycle-only 305;
- signal-only 3,252;
- no duplicates, omissions, or order instability;
- 305 lifecycle rows split at limit 61 produced exactly five pages and no extra cursor;
- run 82's current 290-row query produced six complete pages;
- an Industrials + score ascending filter produced 842 rows across seven pages;
- repeated score-desc traversal returned the same 3,557-key sequence;
- null-primary coverage was real (305/305 lifecycle and 2,819/3,252 signal rows had null
  3-session velocity);
- 428 equal date/severity/score tie groups existed, with a maximum tie of 32.

JSON and CSV export pagination for the 842-row filtered result matched the API's full ordered
key sequence exactly. The backend keyset algorithm is sound when the cursor is replayed with
the same filters and semantic scope. The run URL bugs violate that precondition.

## Intended page semantics

### Global `/setup-lifecycle`

Current Market Changes across runs:

- current-version lifecycle opening/transition/phase events;
- material signal changes whose source snapshot is currently canonical;
- newest events first by default;
- no implicit exact-date predicate;
- explicit `as_of` means one exact event date; explicit date ranges remain filters;
- current canonical/current-version safeguards remain in force.

Lifecycle events use their explicit event-version authority; signal changes use their source
snapshot's canonical authority. These mechanisms intentionally differ and must not be
collapsed into one hidden rule.

### Run `/runs/{run_id}/setup-lifecycle`

Historical Market Changes attributable to the selected `UploadRun`:

- the path run ID is authoritative;
- immutable eligible lifecycle and material signal events linked to that run's snapshots stay
  visible even after later cross-run canonicalization or event-version promotion;
- canonical-revision audit events remain excluded;
- all global filter/sort/date/pagination controls apply within the forced run scope;
- pagination, quick filters, Clear, and exports preserve the run path/semantic scope.

An empty run page remains valid when the run produced no eligible changes, as with run 101.

## Why existing tests passed

- `tests/e2e/test_slse_populated_browser.py` explicitly visits
  `/setup-lifecycle?as_of=2026-08-10` and seeds snapshots and both event streams on that same
  date. It cannot expose a newer no-change snapshot day.
- `test_market_changes_page_renders_full_data` uses a fake service payload. Diagnostics also
  returns 2026-08-01, but the fake ignores the route-injected exact-date filter.
- API forwarding tests exercise `setup_lifecycle_changes()`, not the run HTML route.
- No route test invokes `/runs/{run_id}/setup-lifecycle` with filters or a cursor.
- The single-run certification validates current/canonical API semantics immediately after a
  fresh run; no later run supersedes its snapshots before assertions.
- Existing pagination tests and closure evidence validate the query service under a stable
  filter set. They do not navigate the run page's generated Next URL.
- No shared-template assertion checks Clear, quick-filter, or export scope on a run page.

Current pre-fix focused baseline: `35 passed` for
`tests/setup_lifecycle/test_routes.py tests/setup_lifecycle/test_query_service.py`.

## Commit audit

- `ae084765...`: introduced the run API route with only a fixed `run_id` filter.
- `46f7384b...`: introduced the run HTML/shared template, global pagination base, global quick
  filters, and global Clear link.
- `e59673d...`: introduced the implicit global exact-date default and the combined current /
  canonical query semantics without separating historical runs.
- `42fa338d8b81efdd77014c3600998bef1e445075`: touched none of the affected route, query,
  template, route-test, query-test, or populated-browser files; it is unrelated to A-E.
- `3553511d702865649bbcb9771669e97f78759069`: replaced combined offset paging with stable
  keyset paging and expanded filters, but preserved the implicit date, fixed run route,
  current/canonical run semantics, and global template base paths.

## Phase 2 repair boundary

The smallest semantically correct implementation is:

1. Remove the global route's implicit `as_of`, except when the explicit No Material Change
   quick view needs a selected snapshot date.
2. Introduce a named query scope: current market (default/API compatible) vs historical run.
3. In historical-run scope only, omit snapshot canonicality and lifecycle current-version
   guards while retaining eligible event types and forced path `run_id`.
4. Parse global and run filters through one shared route dependency/helper.
5. Make pagination, quick-filter, Clear, and export context paths scope-aware.
6. Carry an explicit historical scope into run exports so displayed/API/export sets agree.
7. Bind combined cursors to semantic scope (and preserve all filters in generated URLs) so a
   cursor cannot silently reuse current-market totals in historical mode.
8. Add route, query-service, export, pagination, and populated-browser regressions covering
   every confirmed failure mode.
