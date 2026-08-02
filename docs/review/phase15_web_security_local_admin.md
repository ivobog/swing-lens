# SwingLens Phase 15 Web Security, Local Admin, and Research-Only Boundary

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 1 traceability: `docs/review/phase1_requirements_traceability.md`
Phase 3 configuration: `docs/review/phase3_configuration_feature_flags.md`
Phase 4 database: `docs/review/phase4_database_migrations_transactions.md`
Phase 14 background jobs: `docs/review/phase14_background_jobs_concurrency_recovery.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 15 reviews the browser-facing and local-admin attack surface: host binding, debug exposure,
state-changing route protection, CSRF, local network exposure, hostile CSV/provider payloads, XSS,
SQL construction, exports, secrets, dependencies, and the research-only/no-broker-order boundary.

Overall status: not exit-ready. The application has useful local defaults and several well-shaped
controls, but state-changing POST routes are not protected by a central browser-local security
boundary, and the CERI CSRF token is static and public.

## Threat Model

In-scope attackers and mistakes:

- A malicious website running in the user's browser and issuing requests to `localhost`.
- Another machine on the local network if the app is accidentally bound to `0.0.0.0`.
- Malicious CSV content, CERI provider records, warning strings, notes, errors, and debug payloads.
- Host-header spoofing and proxy/forwarded-host ambiguity.
- A compromised dependency or stale vendored asset.
- Accidental introduction of broker order functionality in a research-only tool.

## Evidence Log

Inspected surfaces:

- `app/settings.py`, `app/main.py`, router registration, static mount, and host/debug settings.
- State-changing routes in `app/routers/*`.
- CERI and winner-probability admin helpers.
- Setup lifecycle, market regime, sector rotation, run, upload, and IB route protections.
- Templates and app-owned JavaScript for `|safe`, `tojson`, `innerHTML`, and escaping behavior.
- Query/sort services for dynamic ordering.
- Export services and upload filename handling.
- Broker/IB service calls and order-related strings.
- Vendor asset metadata in `app/static/vendor/README.md`.

Command evidence:

| Command | Result | Notes |
|---|---:|---|
| `uv run pytest tests/ceri/test_ceri_routes_admin.py tests/ceri/test_ceri_acceptance_fixture.py tests/winner_probability/test_routes_admin.py tests/winner_probability/test_routes_api.py tests/setup_lifecycle/test_routes.py tests/test_dashboard_upload.py tests/test_upload_service_v2.py tests/test_csv_upload_services.py tests/test_market_regime_routes.py tests/test_sector_rotation_routes.py -q` | Passed | `85 passed, 1 warning in 12.57s` |
| TestClient probe: `GET /health` with `Host: evil.example` | Accepted | Returned 200; no host allowlist middleware observed |
| Settings probe: `Settings(app_host="0.0.0.0", debug=True)` | Accepted | Confirms Phase 3 unsafe-bind/debug finding still applies |
| `uvx pip-audit` | Passed | No known vulnerabilities found at review time |
| Secret scan with `rg` for key/password/token/private-key patterns | Reviewed | Hits were config wording, test fixture secret, background execution-token fields, and CERI confirmation tokens |
| Broker-order scan for `placeOrder`, `cancelOrder`, `modifyOrder`, `reqOpenOrders`, `Order(`, `whatIf`, `transmit` | Reviewed | No app code hits; only a setup lifecycle localized acceptance test string |

## State-Changing Surface

Representative POST routes without a shared local-admin/CSRF dependency:

- Upload: `app/routers/upload_routes.py:52`.
- Run operations: `app/routers/run_routes.py:385`, `:420`, `:543`, `:560`, `:578`, `:596`, `:698`, `:720`, `:798`, `:928`, `:954`, `:963`.
- Market/sector recalculation: `app/routers/market_regime_routes.py:122`, `app/routers/sector_rotation_routes.py:110`.
- Global IB operations: `app/routers/ib_routes.py:76`, `:88`, `:123`.
- Setup lifecycle admin operations: `app/routers/setup_lifecycle_routes.py:357`, `:369`, `:381`, `:382`, `:383`, `:419`.

Admin helper coverage:

- CERI POSTs call `_require_local_admin` and check `ceri_admin_enabled`, loopback client host, and a CSRF token.
- Winner-probability POSTs call `_require_local_admin` and check `winner_probability_admin_enabled` plus loopback client host, but no CSRF token.
- The rest of the POST surface is not covered by a common guard.

## Positive Controls

- Default `Settings.app_host` is `127.0.0.1` (`app/settings.py:16`).
- CERI admin endpoints are feature-flagged and require local client hosts (`app/routers/ceri_routes.py:949`).
- CERI purge execution requires a confirmation token in addition to admin checks.
- Winner-probability admin endpoints are feature-flagged and local-client guarded (`app/routers/winner_probability_routes.py:636`).
- IB connections reviewed use `readonly=True` in app code (`app/services/ib_connection.py:45`, `app/services/ib_fetch_executor.py:60`, `app/services/bar_cache_service.py:92`, `app/routers/ib_routes.py:98`).
- App-owned JS mostly uses `textContent`; the setup lifecycle `innerHTML` renderer escapes interpolated values with `escapeHtml`.
- Templates use normal Jinja escaping or `tojson` for debug/evidence payloads; no app template `|safe` usage was found.
- Sort paths use allowlists or explicit maps, including `history_query_service`, setup lifecycle, and winner probability.
- Export paths reviewed generate content from database rows and validated export types rather than reading arbitrary filesystem paths.
- Vendored Lightweight Charts metadata records package, version, Apache-2.0 license, source URL, and SHA-256.

## Findings

### PH15-001 - State-changing POST routes lack a shared local-admin and CSRF boundary

Severity: High

Many state-changing routes can be reached with plain POSTs and no central browser-local guard. This
includes upload, recalculation, pipeline starts/cancels, IB fetch/test operations, setup lifecycle
evaluation/replay, market/sector recalculation, and global IB routes. Winner-probability admin routes
are local-client guarded but still lack CSRF. CERI is the only major area with a token check, but see
PH15-002.

Impact: a malicious website visited in the user's browser could attempt localhost POSTs against the
app. If the app is accidentally bound beyond loopback, the same surface becomes local-network
reachable. For destructive or expensive actions, method-only protection is not enough.

Required fix:

- Add a reusable dependency/middleware for state-changing routes.
- Require loopback binding or explicit admin mode for admin POSTs.
- Require a real CSRF mechanism for browser-originated state changes.
- Reject unsafe content types where JSON-only endpoints are expected.
- Add route-map tests that fail when new POST routes omit the guard or explicitly documented exemption.

### PH15-002 - CERI CSRF token is static, public, and accepted in the query string

Severity: High

CERI defines `CERI_CSRF_TOKENS = {"ceri-local-admin", "local-admin"}` (`app/routers/ceri_routes.py:55`).
The token is emitted into page context (`app/routers/ceri_routes.py:262`), hard-coded in JS
(`app/static/ceri.js:17`, `:47`), and accepted from either `x-csrf-token` or `csrf_token` query
parameter (`app/routers/ceri_routes.py:964`).

Impact: the token is not secret. Because query-string tokens are accepted, a cross-site form or
navigation-style POST can include the known token in the URL and satisfy the CERI CSRF check from the
user's own browser.

Required fix:

- Generate per-session or per-process unpredictable CSRF tokens and bind them to a cookie/session or
double-submit cookie.
- Stop accepting CSRF tokens from query strings.
- Use SameSite cookies and verify `Origin`/`Sec-Fetch-Site` for browser POSTs where practical.
- Add tests proving a known static token and query-string token are rejected.

### PH15-003 - Host/debug/public binding policy is not enforced

Severity: High

`Settings.debug` defaults to `True` (`app/settings.py:18`), `FastAPI(debug=app_settings.debug)` is
used (`app/main.py:97`), and `Settings(app_host="0.0.0.0", debug=True)` is accepted. A TestClient
probe showed `/health` accepts `Host: evil.example` with 200. No `TrustedHostMiddleware`,
proxy-header policy, or centralized Host allowlist was observed.

Impact: if the app is bound to a non-loopback interface, browser-local assumptions collapse. Debug
error pages and internal endpoint detail become more dangerous when combined with broad POST routes.

Required fix:

- Fail fast on public bind plus debug unless an explicit dangerous override is set.
- Add TrustedHost/allowed-host middleware for loopback hosts and the configured host/port.
- Document supported reverse-proxy/forwarded-host behavior.
- Add tests for Host spoofing, public bind rejection, and debug/public-bind combinations.

### PH15-004 - Public error details can expose raw provider or internal exception text

Severity: Medium

Several routes return `detail=str(exc)`, including IB 502 responses (`app/routers/ib_routes.py:104`,
`:154`) and multiple run, market, and sector routes. Some exception text may include provider
messages, local paths, SQL details, or operational context.

Impact: attacker-controlled or provider-controlled error text can leak into API responses and logs,
and may become a reflected-content vector if a future UI renders it unsafely.

Required fix:

- Return stable public error codes/messages and log detailed exceptions server-side with redaction.
- Add tests for credential-like strings, SQL fragments, provider payload snippets, and local paths in
  public error responses.

### PH15-005 - Hostile-content XSS regression coverage is incomplete

Severity: Medium

The reviewed templates and app-owned JS look mostly safe today: Jinja autoescaping, `tojson`, and
`textContent` dominate, and the setup lifecycle dynamic HTML renderer escapes values. However, there
is no broad hostile-content test suite proving CSV names, ticker/company fields, warning arrays,
CERI provider records, debug JSON, errors, and notes remain inert across pages and exports.

Impact: future evidence fields may be added to templates or JS sinks without escaping. This is a
classic regression class for data-heavy local tools.

Required fix:

- Add stored/reflected XSS tests with payloads such as `<script>`, `<img onerror>`, SVG payloads, and
  malformed JSON strings across upload, run detail, setup lifecycle, CERI, winner probability,
  market regime, sector rotation, and export surfaces.
- Add a static test that flags new app-owned `innerHTML` usage unless paired with a local escaping
  helper or a documented safe literal.

### PH15-006 - Research-only/no-broker-order boundary is not enforced repository-wide

Severity: Medium

No broker order calls were found in app code, and reviewed IB connections use `readonly=True`.
However, the only explicit no-order test found is scoped to setup lifecycle sources
(`tests/setup_lifecycle/test_setup_lifecycle_acceptance_fixture.py:234`). Phase 15 requires an
automated repository-wide regression test.

Impact: a future IB helper could introduce order placement outside the setup lifecycle files without
tripping the current localized guard.

Required fix:

- Add a repository-wide test that scans all app Python files for forbidden broker-order methods and
  route paths.
- Add fake-IB runtime tests asserting every connect call uses `readonly=True` and that no order
  methods are invoked during fetch/test/resolve flows.

### PH15-007 - Dependency/security scanning is manual, not a committed control

Severity: Low

`uvx pip-audit` found no known vulnerabilities at review time, and the vendored chart asset has
recorded license/source/hash metadata. This is positive, but the audit is not visible as a committed
CI or release gate, and no SBOM/license check was found.

Impact: dependency drift, vendored asset drift, or license regressions can enter unnoticed.

Required fix:

- Add a committed dependency-audit target and CI gate once CI exists.
- Add a vendor-asset integrity test that verifies the recorded SHA-256 against the checked-in file.
- Track licenses for Python and static dependencies.

## Input, SQL, and Export Notes

- Ticker input is normalized in several query services; no raw SQL ticker interpolation was found.
- Dynamic sort paths use allowlists or explicit maps. `history_query_service` maps sort keys before
  calling `sqlalchemy.column`; winner-probability and setup lifecycle use explicit sort fields/maps.
- Pagination/cursor validation is present in setup lifecycle and winner-probability query DTOs.
- Export services reviewed produce CSV/JSON from in-memory payloads and database rows; no path
  traversal route was identified in export endpoints.
- Upload filename handling should still remain in scope for a deeper hostile-upload phase, but the
  Phase 15 browser-security blocker is missing CSRF/local-admin protection on `/uploads`.

## Exit Criteria Assessment

Not met.

Required before closing Phase 15:

- Central guard for state-changing routes with local-admin and CSRF coverage.
- Real, non-static CSRF design; no query-string CSRF tokens.
- Host/debug/public-bind fail-fast validation and Host allowlist tests.
- Public error redaction tests.
- Hostile-content XSS regression tests across stored and reflected surfaces.
- Repository-wide no-broker-order regression test.
- Automated dependency/vendor integrity checks.

