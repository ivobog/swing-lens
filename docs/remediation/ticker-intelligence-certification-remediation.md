# Ticker Intelligence Workspace — Certification Remediation

21 October 2026 · `C:\Users\Ivica\Documents\SwingLens`

## Verdict

**REMEDIATION PASS.** C01–C05 are corrected, their counterexamples have regression coverage, and F01–F06 remain closed. **292 distinct test cases passed:** the279-case baseline plus9 new migrated-PostgreSQL cases and4 new Chromium/Firefox cases. Repeated focused runs are not added to that total.

This is targeted remediation, not final certification. **No operational restart or activation was performed.** The existing NORMAL runtime remains on port8000 with its original processes. The corrected backend was verified in fresh isolated test processes; this report does not claim that the running backend has loaded these changes.

Branch remains `codex/ceri-authority-remediation`, HEAD `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. Unrelated working-tree changes were preserved. No commit or push was made. The original certification report and its diagnostic artifacts remain unchanged.

## Findings closure

| ID | Severity | Status | Proof |
|---|---|---|---|
|C01|P1|PASS|Original99100/99101/99102 fixture now returns only eligible99100 for both earnings and catalysts. Independent quarantine, local-possession, purge, rejection and supersession cases pass; audit rows remain persisted.|
|C02|P2|PASS|104 persisted valid profiles are accessible as100+4, without duplicates. Total/continuation is visible; both engines navigate, retry a failed next-page request and return to page one.|
|C03|P2|PASS|Workspace model identifier/key/version/status agree with canonical persisted metadata for ACTIVE, RETIRED and CANDIDATE models. Missing metadata remains unavailable; serving-estimate selection is unchanged.|
|C04|P2|PASS|Both engines verify default45→44→Back45→Forward44→refresh44, including selector, URL, score, stop/target and evidence provenance. Disclosures/lazy details survive.|
|C05|P2|PASS|At0/1,000/5,000 irrelevant features, the SQL projection fetches zero unrelated rows and materializes zero irrelevant ORM objects. A separate positive case retrieves exactly one legitimate feature and preserves its surprise value.|

Before application changes, the9 new database cases failed. Both browser cases failed in both engines. The retained C05 fixture independently fetched/materialized1,000 and5,000 unrelated rows before the fix. The initial browser fixture needed its existing `workspace_server` fixture imported; after that harness correction, all4 original browser failures reproduced. No application defect is attributed to that setup error.

## Implementation

| File | Targeted correction |
|---|---|
|`app/services/ceri/pit_eligibility.py`|Add a SQL equivalent of the existing `source_record_is_eligible` policy, combining local possession with all recorded external timestamps. Existing Python policy and writer callers are unchanged.|
|`app/services/ticker_intelligence_service.py`|Join ancillary records to their exact source rows; enforce source eligibility/quarantine/purge and established catalyst relevance/review/time rules. Add100-row ranking pagination/count metadata. Batch-load model registry rows and preserve canonical persisted metadata.|
|`app/services/ceri/query_service.py`|Move earnings-surprise family/key/company/session/version/context filtering into SQL, reject a recorded later calculation cutoff, select the canonical latest candidate with LIMIT1, and project only its JSON. Retain collection-reader output shape and legacy null-cutoff compatibility.|
|`app/routers/ticker_routes.py`|Add validated nonnegative ranking `offset`, pagination metadata and encoded continuation URLs carrying identity/run/combined-evidence parameters. No new route or mutation classification.|
|`app/templates/partials/_ticker_intelligence.html`|Explain ancillary source-cutoff scope; render ranking range/total and Previous/Next links.|
|`app/templates/ticker_workspace.html`|Expose the authoritative server-rendered selected run as page data.|
|`app/static/ticker_workspace.js`|Restore that run on pageshow and the next animation frame; fetch ranking continuation locally, retaining stale-response guards, caching, page-specific retry and normal GET run navigation.|
|`tests/integration/test_ticker_certification_remediation_postgresql.py`|Nine new cases using the migrated schema with production foreign keys.|
|`tests/e2e/test_ticker_certification_remediation_browser.py`|Two new scenarios, each executed in Chromium and Firefox.|

### C01: source authority and time

The new SQL time predicate is the counterpart of the existing possession-aware CERI policy: retrieval time, or its established ingestion fallback, plus recorded publication/observation/source timestamps must be at or before the cutoff. Provider dates cannot replace proof of local possession. The workspace additionally requires a non-quarantined, non-purged source.

Earnings retain exact source-record linkage and the established report-time/session boundary. Catalysts retain `is_current` selection; rejected, irrelevant, purged or ineligible revisions cannot enter the panel. Announced/effective times follow the existing catalyst read/capture semantics. The panel is explicitly scoped to eligible sources known at the displayed current CERI cutoff; future scheduled dates remain possible when already known. It does not reconstruct an older superseded revision or introduce a second current clock.

Source99100 remains visible. Source99101 is excluded as future-known;99102 remains excluded even after its dates are moved before the cutoff, proving quarantine is independently enforced. A later local retrieval time and a purged source each exclude99100. Rejected current and superseded catalyst versions remain absent. Source/normalized records are still directly readable for audit; no evidence state is rewritten.

Existing CERI scoring, ingestion, acceptance and writer policies were not changed. The guidance selector, headline/current-activity authority and pinned revision selection are unchanged.

### C02: bounded access to every persisted profile

Pages use the same selected-run/ticker scope, deterministic profile ordering, immutable evidence validation and frozen configuration projection. The new count is the number of **persisted profiles**, including rows whose own availability must still be checked. Counts and best rank for a partially displayed set are explicitly labeled as page-specific; invalid/unpublished rows retain their existing unavailable state.

The104-profile fixture returns100 then4 distinct profiles; all104 validate. Previous/Next links preserve verified identity, selected run and the combined-evidence guard. Changing that evidence address still returns refresh-required. Run6 contains its own3 profiles and none of Run7's overflow rows. A transient next-page503 retries that same page without navigating or reloading the workspace. Normal EOG's5-profile summary and values remain unchanged.

### C03: persisted model metadata

The canonical estimate serializer receives batch-loaded model relationships. The previous unconditional model-status overwrite is removed. Actual ACTIVE/RETIRED/CANDIDATE values are displayed, rather than inferring ACTIVE from estimate availability. Registry identifier, key and version label remain tied to the same persisted model. A missing model remains unavailable instead of treating the serializer's source-category fallback as registry status; source/version provenance remains separate.

No canonical Winner serializer, serving predicate, estimate ordering, outcome relationship, calculation or model lifecycle writer was modified. Earlier F03 competing-version tests remain passing.

### C04: history restoration

The rendered run comes from the server's selected evidence context. On pageshow it resets the selector immediately and again on the next frame, accounting for browsers restoring edited form values after that event. This does not navigate, derive a run from old input or replace historical evidence. The existing GET form, disclosure persistence and independent-request resume handling remain intact.

Both engines now agree on selector/URL/score/evidence/stop/target at every navigation step. A separate persisted-pageshow simulation also restores an edited selector to the rendered run. Test runs45/44 use disposable frozen evidence; the actual persisted EOG values were checked separately below.

### C05: relevant feature projection

Callers were audited before changing the shared helper: canonical CERI ticker composition, snapshot coverage composition and the workspace detail adapter. The JSON feature-list return contract and canonical latest-created/ID ordering are preserved. Existing collection-based readers retain the same shape; SQL-backed requests no longer fetch unrelated issuer history. Recorded later calculation cutoffs are excluded; legacy missing cutoffs retain established compatibility rather than inventing a timestamp.

The positive migrated test retains a legitimate17.5% surprise while excluding a later-cutoff distractor. At each irrelevant-history size its SQL query returns one JSON row. Earlier canonical CERI query and point-in-time regressions pass. F06's removed totals/aggregates remain absent; no cache, index, schema or ingestion change was introduced.

## Verification

New integration tests:

- `test_c01_earnings_and_catalysts_share_source_time_and_authority`.
- `test_c02_all_104_profiles_accessible_through_bounded_continuation`.
- `test_c03_model_metadata_equals_canonical_persisted_status` (3 states) and `test_c03_missing_model_does_not_invent_status`.
- `test_c05_relevant_feature_query_does_not_materialize_unrelated_history` (0/1,000/5,000 rows).

New browser tests:

- `test_c04_history_restores_authoritative_run_and_keeps_lazy_sections`.
- `test_c02_ranking_continuation_is_visible_bounded_and_retryable`.

Final commands, from the checkout:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_ticker_certification_remediation_postgresql.py -q --tb=short
# 9 passed, 10 warnings, 154.17s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_certification_remediation_browser.py --browser chromium --browser firefox -q --tb=short
# 4 passed, 1 warning, 182.10s

.venv\Scripts\python.exe -m pytest tests/integration/test_ticker_intelligence_remediation_postgresql.py tests/test_ticker_intelligence.py tests/test_ticker_workspace.py tests/test_ticker_search.py tests/test_ticker_chart_panel_routes.py tests/test_chart_data_service.py tests/test_score_card_view_service.py tests/test_dashboard_upload.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_route_security.py -q --tb=short
# 102 passed, 8 warnings, 236.65s

.venv\Scripts\python.exe -m pytest tests/ceri/test_ceri_query_service.py tests/setup_lifecycle/test_query_service.py tests/test_ranking_profile_service.py tests/test_ranking_profile_routes.py tests/winner_probability/test_routes_api.py tests/winner_probability/test_estimate_lifecycle.py tests/integration/test_ceri_evidence_quarantine_postgresql.py tests/ceri/test_ceri_alert_service.py tests/ceri/test_ceri_changes_alerts_forensic.py tests/ceri/test_point_in_time_query.py tests/ceri/test_guidance_normalizer.py -q --tb=short
# 120 passed, 3 warnings, 108.21s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 38 passed, 1 warning, 109.37s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 19 passed, 1 warning, 111.08s
```

**292 passed; zero failures/skips in final runs.** F01–F06's migrated cases were explicitly rerun within102; lifecycle signal rendering was rerun in both existing browser suites. Initial9+4 passing targeted runs were repeated after adding explicit purge/model-label/target-level assertions; repeated cases are not added to292. Existing Starlette/httpx and Alembic path-separator deprecations remain.

Ruff passes for all4 changed Python modules and both new test modules. JavaScript syntax, Python compilation and scoped whitespace checks pass. No existing test was weakened, deleted or edited.

### Actual persisted evidence

`output/certification-remediation/real_check.py` used explicit READ ONLY transactions. EOG snapshot5136/context83 still returns exactly the12 ledger pins117847–117858, with opportunity7.73/event risk0/Low confidence. All6 returned earnings and10 current catalysts were independently compared to their exact source rows: source time/possession, quarantine and purge checks pass; catalysts also retain current/review/relevance authority.

Run45 remains combined46423/score8.0815/stop155.3293/target162.9537, with5 profiles and best profile rank2. Run44 remains45121/8.0877/155.3467/162.8665, with5 profiles/best3. Neither five-profile response needs continuation. These comparisons do not claim activation of the corrected backend in the existing process.

## C05 performance

The retained migrated fixture was repeated before/after in fresh isolated TestClient processes. It contains0/1,000/5,000 unrelated derived features with4KiB JSON values. Measurements include real HTTP rendering/serialization through the application, executed SQL, ORM load events, response bytes and Python allocations. SQL count excludes fixture writes; the fixture contains no legitimate earnings item. The separate positive regression above fetches exactly one relevant JSON row.

| Irrelevant rows | Fetched/materialized before→after | SQL reads before→after | Warm request ms before→after | Traced peak MiB before→after |
|---:|---|---|---|---|
|0|0/0→0/0|14→14|199.67→218.91|9.27→9.59|
|1,000|1,000/1,000→0/0|14→14|324.24→219.26|7.51→2.12|
|5,000|5,000/5,000→0/0|14→14|700.17→285.99|30.83→2.05|

Response size is8,679 bytes before and8,789 after at each size; the110-byte increase is the explicit source-cutoff explanation. Traced request durations were3603/959/2176ms before and3001/813/918ms after; tracing and first-request compilation contribute substantial overhead. At5k, process RSS changed about207.91→218.42MiB before and210.52→211.04MiB after. These are point observations, not OS peak-memory measurements.

The result establishes bounded irrelevant-row transfer/materialization. It does **not** establish constant-time SQL, a universal speedup, an SLA or concurrent capacity. Runs included other local test activity. Both measured paths omit unused CERI change/alert totals and pair aggregates.

Reproduction:

```powershell
.venv\Scripts\python.exe output/certification-remediation/measure.py c05-after.json
.venv\Scripts\python.exe output/certification-remediation/real_check.py
```

Task-local before/after JSON, SQL and test outputs are under `output/certification-remediation/`. Original certification outputs were preserved.

## Preservation

No lifecycle command, stack restart, worker/supervisor signal, provider acquisition, pipeline recovery/retry, scoring refresh, publication, order action, schema change or observability repair was issued. Mutations ran only against safely named disposable migrated PostgreSQL databases, cleaned through existing fixture/finally paths.

Before/after snapshots confirm:

- Same NORMAL web20184, worker54476 and supervisor60544, with their original launcher PIDs; port8000 remains open.
- Identical full pipeline_runs/background_jobs row-content hashes and unchanged inspected Core/CERI/Lifecycle/Ranking/Winner ID sets/counts.
- Unchanged schema head;49 historical QUEUED jobs and stale RUNNING job182 retain their state.
- Exactly the7 intended existing application files changed; only the2 new regression modules were added under source/test roots. All other baseline source files and existing tests retain their hashes.

These supporting fingerprints are not a byte-for-byte forensic database image; normal runtime heartbeat/telemetry continues. No unrelated dirty-tree change was reset or attributed to this task. Disposable browser servers and browsers stop through their existing fixtures. No competing authoritative web process was started.

## Remaining limitations and final verification readiness

**All five findings are closed and ready for final independent verification.** Existing F01–F06 behavior remains passing. Concurrent large-history capacity and OS peak-memory behavior remain unverified, as requested; no broad certification or uncontrolled runtime load test was performed.

Operational activation of these fixes remains a separate authorized step. Python backend processes were not restarted; templates/static assets may be read from the shared checkout, so process preservation is not a claim about immutable frontend file serving. This task stops after remediation and its focused verification.
