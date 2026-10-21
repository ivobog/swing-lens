# Ticker Intelligence Workspace — RV01 Remediation

21 October 2026 · `C:\Users\Ivica\Documents\SwingLens`

## Verdict

**PASS — RV01 is corrected and ready for separate live activation and focused sign-off.** Fresh application processes return successful, correctly scoped CERI revision/event detail responses. Both Chromium and Firefox actually follow displayed evidence links and return to a usable workspace. **146 distinct tests pass.**

**The NORMAL port-8000 backend was not restarted and has not loaded this correction.** Its three reference URLs still returned the original500 responses during verification. This report closes implementation/testing work; it does not claim live activation or replace the earlier release-verification verdict.

## Root cause

In `app/services/ceri/query_service.py`:

- `revision_detail()` fetched the requested feature, then called `_company_by_id()`. That helper attempted `_load(db, CeriCompany)`, loading the entire company catalog. Real SQLAlchemy sessions correctly reject this fixture-only operation.
- `event_detail()` had the same company lookup and an additional `_load(db, CeriCatalystEventRevision)` followed by Python filtering. Fixing the first call alone would leave another production-scope exception.
- The directly related `event_revisions()` endpoint repeated the unscoped revision loader after calling event detail.
- `_get()` fell through to `_load()` when a real-session primary-key lookup returned no row. Unknown evidence therefore raised the same loading error instead of the established structured404. Its other caller, `_require_run()`, was inspected and covered by a missing-run regression.

The production SQL-scope guard was functioning correctly. It remains unchanged and explicitly tested. Other catalog/list consumers of `_company_by_id()` were inspected; their unrelated implementations were not refactored.

## Code correction

Only one existing application file changed:

[query_service.py](C:/Users/Ivica/Documents/SwingLens/app/services/ceri/query_service.py)

| Function | Correction |
|---|---|
|`_get()`|Real sessions execute `SELECT model WHERE model.id = requested_id` and return the result, including a normal missing-row result. They never fall through to the fixture loader. Existing collection-fixture behavior remains available.|
|`revision_detail()`|Resolve only the feature's recorded company ID through the scoped key reader. Preserve the existing feature payload and complete lineage response.|
|`event_detail()`|Resolve only the requested event and its recorded company. Fetch revisions with `catalyst_event_id = event.id`, ordered by ID. Preserve the current revision and all associated historical audit revisions.|
|`event_revisions()`|Reuse event detail's already scoped revision payload for its existing sorting/pagination contract; avoid another fetch and any N+1 pattern.|

The requested feature/event primary key determines its owning company. Event revisions are constrained by that event's foreign key. Recorded source IDs and lineage remain unchanged; no unrelated company/event evidence is borrowed. Serializers, response keys, routes and mutation classifications are unchanged. Database errors propagate rather than becoming misleading empty responses.

Direct evidence detail retains its existing audit semantics: an explicitly requested historical, rejected or purge-marked record remains inspectable with its recorded flags. Current workspace eligibility remains separate and continues to exclude ineligible sources. No new publication/authority system, source substitution, scoring, calculation, persistence or schema change was introduced.

Queries are bounded by the requested identity: revision detail uses **two SELECTs**, event detail **three**, and unknown evidence **one**. Event detail intentionally retains its full associated-revision audit list; this is not a new universal limit on an individual event's revision history.

## Regression proof

Added:

- [PostgreSQL tests](C:/Users/Ivica/Documents/SwingLens/tests/integration/test_ceri_rv01_postgresql.py).
- [Browser tests](C:/Users/Ivica/Documents/SwingLens/tests/e2e/test_ceri_rv01_browser.py).

The ten database cases use the existing migrated disposable PostgreSQL fixture with production foreign keys intact. **Before the application change:9 failed,1 passed. Final corrected run:10 passed.** They cover:

- Valid feature identity, company, numeric fields, source IDs and lineage; valid event/current revision and associated historical revisions.
- Exclusion of unrelated-company/event revisions; exact SQL scope, row counts and fixed query counts; no fixture-only loader calls on production detail paths.
- Structured404s for unknown revisions/events and the shared missing-run check.
- Missing optional source references remaining NULL, without substitution.
- Missing-company lookup returning no row without catalog loading. A dangling company FK is rejected by the real schema; constraints were not disabled to manufacture an impossible orphan.
- Explicit audit visibility of rejected/quarantined/purge-marked evidence while current workspace selection still excludes it.
- Events without revisions, database-error propagation, unchanged table-content fingerprints, empty ORM mutation sets and explicit READ ONLY transactions.

The first corrected run had9 passing cases and one new test-recorder error: it classified a read-only workspace `WITH … SELECT` statement as non-SELECT. That recorder was corrected to recognize the existing read-only query form; the application and existing tests were not changed for it. Both PostgreSQL READ ONLY enforcement and before/after fingerprints remain in place. Initial failure logs are retained. Repeated runs are not added to the distinct-case total.

Executed commands:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_ceri_rv01_postgresql.py -q --tb=short
# Final:10 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ceri_rv01_browser.py --browser chromium --browser firefox -q --tb=short
# 2 passed

.venv\Scripts\python.exe -m pytest tests/ceri/test_ceri_query_service.py tests/ceri/test_ceri_routes_api.py tests/ceri/test_ceri_routes_ui.py tests/ceri/test_point_in_time_query.py tests/test_ceri_contextual_eligibility.py tests/integration/test_ceri_evidence_quarantine_postgresql.py tests/test_ticker_intelligence.py tests/test_ticker_workspace.py tests/integration/test_ticker_intelligence_remediation_postgresql.py tests/integration/test_ticker_certification_remediation_postgresql.py::test_c01_earnings_and_catalysts_share_source_time_and_authority tests/test_route_security.py -q --tb=short
# 122 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py --browser chromium --browser firefox -q --tb=short
# 12 passed
```

Total **146 distinct passing cases**, with no failures/skips in the final commands. The historical292-case certification suite was not rerun or claimed as this task's result. Affected regressions cover CERI query/detail route contracts, eligibility, F01/F02/C01 selection, workspace behavior, signal rendering, lazy details and route security. Ruff, compilation and scoped diff-whitespace checks pass. Existing Starlette/httpx and Alembic configuration deprecation warnings remain.

## Real evidence

`output/rv01-remediation/real_check.py` creates a fresh corrected application and serves requests through TestClient. Every authoritative database transaction explicitly uses **READ ONLY**. Expectations are independently read from persisted rows before requesting the endpoints.

| Requested path | Fresh corrected app | Company/source proof | SQL reads |
|---|---|---|---:|
|`/api/ceri/revisions/117847`|200|EOG/company399; observations112825/112826; matching recorded snapshot/source IDs, metric, fiscal period, configuration and evidence hashes|2|
|`/api/ceri/revisions/117848`|200|EOG/company399; observations112825/112827; same identity/provenance checks|2|
|`/api/ceri/events/5325`|200|EOG/company399; current revision5325/source112911; complete returned revision-ID set equals the rows owned by event5325|3|
|Unknown revision/event999999999999|404 each|Structured `TICKER_NOT_FOUND`, no internal loader exception|1 each|
|Previously working `/api/ceri/ticker/EOG`|200|Existing CERI ticker-detail contract remains operational|27|

Both feature snapshots' company relationships were independently checked. Returned source IDs and lineage match persisted values; no unrelated evidence was exposed. All captured statements are SELECTs. The first revision request took1684.79ms including cold application/query work; the next revision160.67ms and event141.10ms. These are diagnostic samples, not an SLA.

Saved expected identities, complete successful detail responses and SQL/request counts are under `output/rv01-remediation/`. The same script separately checked the **unrestarted NORMAL server**: all three reference URLs still returned500. No record substitution was needed, and the IDs appear only in verification code, not production logic.

## Browser verification

The new journey runs in fresh application processes against migrated disposable PostgreSQL, separately in Chromium and Firefox. It opens the EOG workspace, expands CERI and its source disclosure, and **clicks** pinned revision99501. The navigation response is200 and its JSON identifies EOG/company8000/source99500 with matching lineage. It returns through browser history, then clicks catalyst99502; that navigation returns200 with current revision99503 and source99500. The final Back restores a usable workspace, selected run, decision metrics and rendered chart; no JavaScript errors occur.

These are real browser navigations to real database-backed API responses, not mocked HTTP or hyperlink-presence assertions. The fixture IDs are distinct from the actual-database reference IDs verified above. The existing intelligence browser suite also passes all12 engine-specific cases. The NORMAL server was not substituted or restarted to obtain these results.

## Preservation

Before/after snapshots confirm:

- Only `app/services/ceri/query_service.py` changed among existing source/test/configuration files; exactly the two intended test modules were added. Unrelated working-tree changes were preserved.
- Same NORMAL web8744, worker36016 and supervisor60840, with unchanged launcher identities and process creation times; runtime instance `42b93b97d85549c9afc1c28b04ec6904` remains in place. No lifecycle start/stop/restart or worker/supervisor/database signal was issued.
- Schema remains `0092_phase2_eligibility_ledgers`. Full pipeline/job row-content hashes and inspected global Core/CERI/Lifecycle/Ranking/Winner ID sets/counts are unchanged. These are scoped supporting fingerprints, not a byte-for-byte database image.
- Port8000 remains open and8779 remains free. Final `/health`, `/ready/core` and `/ready` return200. Analytical mutations were confined to disposable test fixtures; real-data inspection was READ ONLY. No provider call, scoring operation, publication or historical pipeline repair was performed.

Disposable servers/databases and browser processes close through their fixtures. Original certification and remediation reports remain unchanged. No commit or push was made.

Evidence and reproducible probes: `output/rv01-remediation/`, including `before-tests.txt`, `backend-final.txt`, `browser-final.txt`, `affected-regression.txt`, `intelligence-browser-regression.txt`, `real-check.json`, `preservation.json` and `readiness-final.json`.

## Remaining work

**RV01 is ready for live activation and final focused sign-off.** The next separately authorized step is a supported lifecycle activation followed by actual port-8000 revision/catalyst clicked-link verification. The currently running backend still contains the old reader code. This task stops after implementation, tests and isolated verification; it does not authorize or perform that restart.
