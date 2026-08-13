# Setup Lifecycle Changes Fix Report

Date: 2026-08-13
Phase: 2 (implementation and certification)
Diagnosis gate: completed first in `docs/debug/setup_lifecycle_changes_diagnosis.md`

## Outcome

The confirmed current-vs-historical routing defects are repaired without weakening the
global current-market safeguards.

- `/setup-lifecycle` no longer turns the latest canonical snapshot date into an implicit
  exact event-date filter. It shows the newest current/canonical changes across dates, while
  an explicit `as_of` remains an exact-date filter.
- `/runs/{run_id}/setup-lifecycle` is now an explicit historical-run view. It returns eligible
  immutable lifecycle and signal-change evidence attributable through that run's snapshots,
  including evidence superseded later by global canonicalization or event versioning.
- The run route accepts the same filters, dates, sort, direction, limit, and cursor as the
  global route. The path `run_id` remains authoritative.
- Next, quick filters, Clear, CSV, and JSON preserve run scope. Run exports use the same named
  historical scope and filters as the rendered page.
- Combined cursors are bound to both semantic scope and the complete filter set. Replaying a
  cursor under another scope/filter set now fails instead of mixing totals or page boundaries.

Run 101 correctly remains empty: its persisted evidence contains zero material signal changes
and zero eligible lifecycle opening/transition events. Its 51 `CANONICAL_REVISION` audit rows
are intentionally outside Market Changes. Run 82 is the real production proof of historical
recovery: its visible set increases from the current-market subset of 290 to all 4,194
attributable eligible rows.

## Confirmed root causes repaired

1. **Implicit global `as_of` regression.** The HTML route used
   `diagnostics.latest_canonical_date` (2026-08-13) as an exact event date even though the
   newest visible changes were on 2026-08-12.
2. **Run filter forwarding gap.** The run handler accepted only its path parameter and built a
   fixed query, although it rendered the full shared filter UI.
3. **Current-market guards applied to historical evidence.** Run queries required signal
   snapshots to remain globally canonical and lifecycle events to remain current versions.
4. **Fixed global pagination base.** Run Next URLs pointed at `/setup-lifecycle` and the run
   handler did not accept the cursor.
5. **Global-only shared actions.** Clear and quick filters escaped the run; exports lacked
   active run filters and a historical semantic mode.

The combined keyset algorithm itself was not changed: Phase 1 proved it complete and
deterministic under a stable scope. Phase 2 only makes that scope explicit and cursor-bound.

## Semantic decisions

### Current market

`SetupLifecycleViewScope.CURRENT_MARKET` is the default for the API and global page. It keeps:

- `SetupLifecycleEvent.is_current_version = TRUE`;
- `SetupSignalSnapshot.is_canonical = TRUE` for material signal changes;
- current/canonical no-material-change snapshot semantics;
- newest-first default ordering with no implicit date predicate.

### Historical run

`SetupLifecycleViewScope.HISTORICAL_RUN` requires `run_id`. It keeps the eligible event-type
rules but omits only the two mutable present-state guards. Run attribution still goes through
`SetupSignalSnapshot.run_id`, and `CANONICAL_REVISION` audit events remain excluded.

Alert Center, Operations, ticker timelines, episode detail, and source evidence remain global
because no run-scoped variants exist. Clear, quick filters, pagination, and exports preserve
the historical run because they alter or continue the Market Changes result set itself.

The No Material Change quick view still needs a concrete snapshot date. With no explicit
`as_of`, it derives the latest canonical date in current-market scope or the selected run's
latest snapshot date in historical scope. Ordinary Market Changes never receives this default.

## Files changed

- `app/services/setup_lifecycle/query_service.py`
  - added the explicit view scope;
  - made canonical/current guards scope-specific;
  - required a run for historical scope;
  - included scope in summaries/page metadata;
  - bound keyset cursors to scope and filter hash.
- `app/routers/setup_lifecycle_routes.py`
  - introduced one shared page-parameter dependency and response builder;
  - removed the ordinary global implicit date;
  - forced the path run ID while forwarding every shared filter;
  - carried view scope through API and export routes;
  - made template context URLs base-path/scope aware.
- `app/templates/setup_lifecycle.html`
  - distinguished current vs historical provenance;
  - added date-range controls and the already-supported `setup_score` sort;
  - made Clear context aware.
- `tests/setup_lifecycle/test_routes.py`
  - added no-implicit-date and authoritative run-filter/URL/export assertions.
- `tests/setup_lifecycle/test_query_service.py`
  - added historical-run validation and scope/filter cursor-binding regressions.
- `tests/integration/test_slse_market_alert_vertical.py`
  - added real PostgreSQL supersession and exhaustive historical pagination coverage.
- `tests/e2e/test_slse_populated_browser.py`
  - added a newer no-change snapshot day, explicit empty-date behavior, superseded run
    evidence, run-scoped Next/Clear/quick-filter/export checks, and page-boundary checks.
- `docs/debug/setup_lifecycle_changes_diagnosis.md`
  - Phase 1 evidence and classifications, completed before production edits.

No schema or migration change was required.

## Regression coverage and certification

### Focused route/query tests

`uv run pytest -q tests/setup_lifecycle/test_routes.py tests/setup_lifecycle/test_query_service.py`

- Result: **39 passed**, one pre-existing Starlette `TestClient` deprecation warning.

### Required setup-lifecycle suite

`uv run pytest -q tests/setup_lifecycle`

- Result: **226 passed**, one pre-existing Starlette `TestClient` deprecation warning.
- This includes alert-center, ticker-timeline, episode-detail, and export regressions.

### Required PostgreSQL and browser suite

`uv run pytest -q tests/integration/test_slse_market_alert_vertical.py tests/e2e/test_slse_populated_browser.py`

- Result: **2 passed**, one pre-existing Starlette `TestClient` deprecation warning.
- The integration fixture creates 52 historical rows, split at limit 13 into exactly four
  pages, and checks all nine supported sorts in ascending and descending order.
- Across all 18 traversals, the page union equals the complete expected set with 52 unique
  keys, no duplicates, no omissions, opaque cursors, and stable totals.
- The browser fixture proves current-market exclusion after supersession and historical-run
  retention, scoped page navigation, and export/API parity.

### Static checks

`uv run ruff check app tests scripts`

- Result: **All checks passed**.

### Broader regression attempt

`uv run pytest -q -m "not e2e and not external"` exceeded the five-minute execution ceiling
without emitting a failure result. Its remaining process tree was terminated after the
timeout. This is recorded as **inconclusive**, not passed; the mandated and directly affected
suites above all completed successfully.

## Before/after production database, API, and UI evidence

The current working tree was loaded in an isolated local server on port 8011 against the real
`swinglens` PostgreSQL database. This avoided disrupting the pre-existing development server,
whose reload process was stale.

| Evidence | Before | After |
|---|---:|---:|
| Global API total without `as_of` | 51,267 | 51,267 (`CURRENT_MARKET`) |
| Global page total | 0 | 51,267 |
| Global page `as_of` control | silently 2026-08-13 | blank |
| Global newest rendered event date | none | 2026-08-12 |
| Explicit `as_of=2026-08-13` | 0 | 0 (preserved exact-date behavior) |
| Run 101 historical total | 0 | 0 (correct persisted result) |
| Run 82 current-market subset | 290 | 290 |
| Run 82 historical-run result | unavailable | 4,194 |
| Run 82 requested `limit=1` | ignored; 50 rendered | 1 rendered |
| Run 82 Next destination | global `/setup-lifecycle` | `/runs/82/setup-lifecycle` |
| Run 82 export scope | current/default filters | `run_id=82`, active filters, `HISTORICAL_RUN` |

A headed Chromium verification against the current working tree showed:

- global `Total Changes 51267`, source cutoff 2026-08-12, and an empty Data as of control;
- run 82 `Total Changes 4194` and `Run 82 · Historical evidence`;
- historical provenance and scoped CSV/JSON URLs;
- clicking Next retained `/runs/82/setup-lifecycle`, advanced the cursor offset from 50 to
  100, and rendered the next result page.

The synthetic PostgreSQL/browser fixtures additionally prove the behavior when a later
snapshot demotes the selected run's snapshot and a later lifecycle revision marks its event
non-current. The global result becomes zero while the historical run still returns both
original evidence rows, and its JSON export total equals the page/API total.

## Remaining risks

- Cursors issued before this change lack scope/filter bindings and are intentionally rejected;
  users must restart pagination from page 1.
- Historical result sets are larger (run 82 grows from 290 to 4,194), so export row limits and
  existing resource guards remain important. The implementation preserves them.
- The broad non-E2E regression command timed out as noted above. No failure was observed in
  the required SLSE, PostgreSQL, browser, lint, alert, ticker, episode, or export coverage.
