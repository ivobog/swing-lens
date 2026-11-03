# Manual Sector Refresh implementation

Date: 2026-10-23. Primary checkout: `C:/Users/Ivica/Documents/SwingLens`.
Branch: `release/swinglens-msi-live`. Starting commit: `feb0820f58e903650550c41721a36c66966873f5`.

## Result

`SECTOR MANUAL REFRESH: PASS` — source implementation and isolated verification.
No operational activation or live-provider certification was performed.

The Sector Rotation page has one **Refresh Sector** action and an independent,
amber **STALE** indicator. Stored-session details and affected ETF/basis pairs
are available in an adjacent keyboard-accessible disclosure. Missing and unknown
evidence remain distinct; acquisition completion never substitutes for freshness.

## Admission and existing contracts

- `POST /api/market-sector-intelligence/refresh`, JSON `{}` only. Extra fields
  are rejected. Existing `LOCAL_ADMIN`, `NORMAL_ONLY`, CSRF and loopback-client
  guards apply; Origin must equal the application origin and cross-site requests
  are rejected before admission. No client-selected ticker or connection settings.
- Uses `ib_gateway_health_service.check_status(settings=...)`, the existing
  pipeline preflight: API handshake and current-time smoke response. Requires
  `IB_API_READY`, connected/ready flags and the smoke response before creating
  any acquisition authority, fetch run or job. It never launches Gateway.
- The public prewarm POST lacks the required fixed-cohort/preflight/CSRF
  boundary, and automatic prewarm remains disabled. The narrow MSI facade uses
  its existing underlying acquisition path: `build_fetch_plan`,
  `create_queued_fetch_run`, `submit_fetch_job`, and the native `IB_FETCH`
  handler `execute_durable_fetch_job`. No provider client, worker type, queue,
  database table or migration was added.
- Universe comes from validated MSI sector configuration: all eleven canonical
  ETFs plus fixed SPY/QQQ, both TRADES and ADJUSTED_LAST. Only this operation's
  planner settings request daily bars, at least the native Technical window
  (currently 756 sessions), and a four-year initial history request. Explicit
  manual refresh uses the existing recent-refresh option; it does not force a
  full backfill of an already populated cache or change global settings.
- A feature-scoped PostgreSQL transaction advisory lock makes admission single
  flight across processes. Active marked jobs above the activation fence are
  reused. Admission/probe attempts are throttled for five seconds. Busy admission
  recovers through GET observation; the browser never retries an acquisition POST.
- Run, semantic authority, marked durable job and configuration delivery commit
  atomically. Failures roll back. Existing worker leases, request pacing,
  provenance, receipts, revisions, PIT visibility, calendar and completed-session
  response admission remain native. Adjusted bars retain v3 admission.
- `GET /refresh/active` recovers only active MSI work above the fence.
  `GET /refresh/{job_id}` follows the exact marked job and its fetch run through
  `get_fetch_progress`; both reads suppress autoflush. Job failure overrides
  unfinished fetch state, and a COMPLETED job with PARTIAL/FAILED fetch evidence
  is never represented as successful completion. Display metadata excludes raw
  provider diagnostics, arbitrary payloads and connection details.

## Browser and freshness

Only a deliberate click can POST. Ordinary initialization makes one GET-only
active-operation lookup; polling exists only while observing work or bounded
status recovery. Polls use 2.5 seconds, with three transient retries and a
GET-only check-status option after observation becomes unavailable. Session
storage and browser back/forward restoration recover observation without POST.

The action is semantically disabled with `aria-disabled` while busy; the
controller rejects repeat activation and preserves keyboard focus. COMPLETED
reloads the full current URL once. PARTIAL reloads cached panels with a retained
warning and affected bases; FAILED/CANCELLED preserve cached evidence and permit
retry. Unload aborts observation requests; uncertain responses recover read-only.

Coverage adds `freshness` without changing D1 preferred-basis rows or model math.
For every ETF/basis, the canonical `load_price_bars_frame` applies the same
receipt/revision PIT cutoff and completed-session upper bound. Invalid closes do
not qualify as usable evidence. The existing session validator and
`MarketClockService` calculate exact trading-session lag using calendar v2 and
close-plus-15-minute readiness. One lagging basis makes STALE; absent or uncertain
bases remain disclosed and cannot certify CURRENT. No freshness read acquires data.

The empty-cache coverage contract is now 46 price/PIT-scoped SELECTs: existing 24 D1
preferred-basis reads plus 22 independent ETF/basis freshness reads. Its regression
test verifies this count and still forbids DML and non-price-table reads.

## Verification

**472 distinct passing tests: 435 pytest cases and 37 Node cases.** No unresolved
failures or skipped cases in the distinct final result set.

- 29 new backend tests: calendar/readiness edges, both bases, invalid/missing/PIT
  evidence, same-origin/CSRF/local-client/runtime/payload rejection, throttling,
  rollback and durable terminal-status mapping.
- Four new disposable PostgreSQL tests: zero admission on unavailable/incomplete
  preflight, atomic rollback after interrupted enqueue, concurrent single-flight
  admission, and native durable worker/cache/provenance/readonly-status behavior.
  Synthetic provider output includes a forming adjusted bar; it is not persisted.
- Two new actual Chrome tests: unavailable Gateway creates zero jobs; explicit
  refresh and revisit observe one operation; native worker completion reloads once
  with URL selections intact; STALE persists after a completed unusable basis,
  then clears after a subsequent valid revision; missing data remains MISSING.
  Desktop/mobile keyboard focus and disclosure behavior pass. Six calendar cases
  exercise weekend, holiday and readiness-boundary CURRENT rendering with zero
  POSTs/probes. No uncaught browser JavaScript errors occurred. The intentional
  unavailable-Gateway response is HTTP 503.
- 368 distinct focused regressions: MSI configuration, D1 coverage/routes,
  rotation/math/PIT/storage, native Technical diagnostics, charts/dashboard,
  calendar versions, review remediations, Market Regime separation, prewarm,
  IB planner/executor/status and pipeline Gateway/activation-fence contracts.
  The initial run had one obsolete 24-SELECT assertion; its corrected 46-SELECT
  contract passed the final run. All other 367 cases passed unchanged.
- All 32 existing Chrome dashboard regressions pass. The Node total comprises
  17 existing dashboard cases and 20 refresh/freshness cases.
- Ruff passes on all ten changed/new Python files. Both JavaScript syntax checks
  and `git diff --check` pass. Route inventory: 214 routes, 53 classified mutations,
  49 NORMAL_ONLY. Alembic remains at sole head `0093_market_sector_rotation`.

**Architecture registry: global FAIL, three unchanged baseline findings, zero
additional findings.** The pre-edit and final scans report exactly:

1. `ARCH_WRITER_CANDIDATE_UNCLASSIFIED`: `ib_fetch_executor._admit_returned_bars`, `add`.
2. `ARCH_WRITER_CANDIDATE_UNCLASSIFIED`: `pipeline_outcome._integrity_hazards`, `add`.
3. `ARCH_RECOVERY_PRIMITIVE_UNREGISTERED`: recovery-like production function census drift.

The new mutation and three transaction sites are registered explicitly. Generated
registry documentation changes only its mutation count. No baseline finding was
suppressed, hidden or repaired.

## Scope, evidence and handover

Changed production files are the GUI/MSI routers, MSI coverage, the new refresh
admission service, MSI template/CSS/JavaScript and new refresh controller. Registry
and generated registry documentation reflect the action. Tests comprise the two
existing inventory/coverage files, new backend and PostgreSQL tests, disposable
helpers, browser journeys and Node cases, plus this report.

Exact file inventory (relative to the primary checkout above):

```text
app/routers/gui_routes.py
app/routers/market_sector_intelligence_routes.py
app/services/market_sector_intelligence_coverage.py
app/services/market_sector_refresh_service.py
app/static/market_sector_intelligence.css
app/static/market_sector_intelligence.js
app/static/market_sector_refresh.js
app/templates/market_sector_intelligence.html
config/architecture_registry.json
docs/audit/whole-application-closure/GENERATED_ARCHITECTURE_REGISTRY.md
docs/market_sector_intelligence/SECTOR_REFRESH_IMPLEMENTATION_REPORT.md
tests/test_route_security.py
tests/test_market_sector_intelligence_routes.py
tests/test_market_sector_refresh.py
tests/sector_refresh_helpers.py
tests/integration/test_sector_refresh_postgresql.py
tests/test_sector_refresh_browser.py
tests/market_sector_refresh.test.cjs
```

Technical V4/V5, Fundamental, Combined, Ranking, Setup, Lifecycle, Alerts, Winner,
CERI, Market Regime, legacy run-specific sector rotation, provider acquisition,
existing workers and orchestration source files remain unchanged. No immutable
MSI captures or scheduler were added. Activation minima 1642/46 and `.env` remain
unchanged. Disposable test runs configure an empty control database, never the
operational database; final database censuses and environment-file hashes match.

Raw run evidence, exact module lists and distinct case manifest are outside Git:
`C:/Users/Ivica/Documents/SwingLens-Integration/Sector-Refresh-20261023/`.
Key files: `finalmatrix.xml`, `finalmatrix-result.json`, `regressions.xml`,
`browser.xml`, `browserqa.xml`, `distinct-pytest-results.json`, `node-final.tap`.
Screenshots are ignored under `output/playwright/sector-refresh/`.

No operational database mutations, provider requests, pipeline starts, NORMAL
restart, deployment, push, merge or additional worktree occurred. Port 8000
remained stopped. The compatibility worktree remains clean at the starting SHA.
Live Gateway authentication, entitlement, real-provider completeness/pacing and
the operational browser deployment remain unverified.

Manual check: start SwingLens normally, open
`http://127.0.0.1:8000/sector-rotation`, inspect stored-session/STALE details,
then click **Refresh Sector** with Gateway signed in. Observe progress and one
automatic reload retaining URL selections. STALE clears only after stored
coverage verifies current inputs. Optionally retry with Gateway closed: an
inline error must appear and no fetch job may be created.
