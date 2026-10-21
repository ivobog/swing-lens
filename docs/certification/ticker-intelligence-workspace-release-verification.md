# Ticker Intelligence Workspace — Release Verification

21 October 2026 · `C:\Users\Ivica\Documents\SwingLens`

## 1. Executive verdict

**Feature certification: FAILED. Operational activation: ACTIVATED. Complete workspace ready for normal research use: NO.**

C01–C05 pass independent verification. The F01–F06 targeted regressions pass, including exact source selection. Both Chromium and Firefox pass the activated server's search, preview, independent workspace and historical navigation journeys. **One confirmed P2 release blocker remains: RV01, displayed CERI revision and catalyst evidence links return HTTP 500 with real PostgreSQL sessions.** Passing source selection does not satisfy the separate requirement for working evidence drill-down.

The single authorized lifecycle restart succeeded. Corrected backend behavior, process ownership, schema, safety and readiness checks pass. This operational verdict concerns activation; the failed feature gate prevents release approval. The NORMAL runtime remains running. No application code or existing tests were changed, no second restart occurred, and nothing was committed or pushed.

## 2. Baseline and activation

Read the original Vision v1.0, previous certification and both remediation reports. Their claims were checked against the current checkout and fresh execution. This request governs the verification-only scope; the vision's implementation suggestions were not treated as additional authorization.

Branch: `codex/ceri-authority-remediation`. HEAD: `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. Extensive existing tracked and untracked changes, including the uncommitted workspace implementation, were preserved. The baseline fingerprints cover 1,204 files under app/tests/scripts/migrations/config.

The actual initial state differed from the request's historical runtime description: **port 8000 was closed; no NORMAL web, worker or supervisor process was running; lifecycle status was STOPPED.** Historical worker generation124/PID54476 and supervisor generation52/PID60544 had stopping timestamps at16:50:33+02, preceding this task. Those PIDs were not adopted as current identities. PostgreSQL18.3 remained running at schema `0092_phase2_eligibility_ledgers`.

The immediately preceding safety gate, at **15:11:59 UTC**, established:

- Zero authorized QUEUED/RUNNING jobs under the actual execution-authority predicate. The49 historical QUEUED rows and stale RUNNING job182 are nonauthoritative.
- Zero operational/actionable recovery invariants and no automatic dependency reconciliation due. Three HISTORICAL_FORENSIC invariants remain preserved.
- Winner automatic maturation would return existing BLOCKED job968 before enqueue/root-action creation. Prewarm has no authorized queued work or automatic web-start enqueue.
- Schema already at the expected head; no unexpected migration required. No authoritative running job would be interrupted. Prior registry owners were stopped; supported generation acquisition remained intact.
- Unchanged source, pipeline/job content hashes and global inspected evidence IDs/counts.

Executed exactly once:

```powershell
pwsh -NoProfile -File .\swinglens.ps1 restart -Json
```

Exit0; lifecycle operation `93102048-6cdc-42f1-8d21-53059d9df36d`. The supported locked schema/provenance gate and canonical supervisor-root launcher were used without overrides or manual process termination.

| Activated ownership | Verified value |
|---|---|
|Runtime instance|`42b93b97d85549c9afc1c28b04ec6904`|
|Web/listener|PID8744; sole listener on127.0.0.1:8000|
|Supervisor|PID60840; process-group launcher75632; generation53|
|Worker|PID36016; generation125; fresh heartbeat; owner `local-worker-1`|
|Schema|`0092_phase2_eligibility_ledgers`, unchanged|
|Health/readiness|`/health`, `/ready/core`, `/ready`:200|
|Final quiet lifecycle status|Core OK; Application OK; Web/Worker ready; overall DEGRADED|

Corrected backend loading is established by behavior, not PID alone: the live ranking response includes the new five-profile pagination object, and `offset=-1` is rejected with422. The previous backend contract lacked that pagination/validation. All four live panels load from the new generation; source files remain unchanged throughout verification. Adversarial cases absent from real data are proven in fresh migrated disposable processes, not manufactured in the authoritative database.

Startup and one later status snapshot reported Application FAILED with no application payload; direct readiness returned200. The latter snapshot overlapped preservation reads and recovered its core probe on retry. A quiet supported `status -Json` repeat returned Application OK and full readiness payload. These transient observations are retained, not represented as uninterrupted readiness. Docker commands exit1; Prometheus/Grafana remain unavailable. This separate observability degradation did not prevent the required checks and was not repaired.

## 3. C01–C05 verification

| Finding | Result | Independent proof |
|---|---|---|
|C01|PASS|Migrated positive99100 survives; future-known99101, independently quarantined99102, later local retrieval, purged sources, rejected/current and superseded catalyst cases are excluded. Real EOG returns six earnings and ten catalyst rows whose exact source records satisfy quarantine/purge/time authority at snapshot5136's cutoff. Audit rows remain persisted.|
|C02|PASS|104 valid persisted profiles are accessible as100+4 distinct profiles, with accurate totals, bounded continuation, frozen run/evidence guards and no duplicates. Both browsers pass next-page failure/retry and previous-page navigation. Real EOG correctly reports5 profiles.|
|C03|PASS|ACTIVE, RETIRED and CANDIDATE model metadata agree with canonical persisted payloads and rendered model version/status. Missing metadata remains unavailable. These model-backed edge cases use migrated fixtures; real EOG's estimates are insufficient, not model-status examples.|
|C04|PASS|Actual port8000 in both engines: default45→44→Back45→Forward44→refresh44. URL, selector, scores, immutable evidence, profile values, Winner IDs and stop/target agree at every step. Source-specific CERI/lifecycle dates remain independent.|
|C05|PASS|Migrated0/1,000/5,000 irrelevant-history cases return zero irrelevant JSON rows and materialize zero irrelevant ORM objects. The independent positive test returns exactly one legitimate feature with17.5% surprise, excluding the later-cutoff distractor. The measured detail path remains14 SQL reads at all three sizes.|

No mocked response is used as proof of a database-authority rule. Browser503 injection is confined to transport isolation/retry checks.

## 4. F01–F06 regression confirmation

| Protection | Result |
|---|---|
|F01|PASS: migrated rejected/quarantined-owner cases exclude invalid current activity while retaining valid fallback/history/audit access.|
|F02|PASS for source selection: real snapshot5136 returns exactly ledger pins117847–117858, all12 from context83 at the captured cutoff/configuration; no context81/newer substitute. Migrated missing/competing-context/35-pin cases pass. **Working linked drill-down fails separately as RV01.**|
|F03|PASS: selected estimate agrees with the canonical selector, including competing training/creation order, historical outcome and rescore distractors.|
|F04|PASS: both browser regression suites verify kind-specific signal/transition/alert fields. Actual17717 renders technical_score8.2971→6.8252, delta−1.4719, crossing_7/EXIT; setup evidence48543 returns200.|
|F05|PASS: canonical/workspace guidance agree for null sessions, source cutoffs, rejection/quarantine, supersession and expiry.|
|F06|PASS: SQL inspection rejects unused change/alert totals and pair aggregates; full APIs retain their contracts. Actual CERI detail remains17 reads.|

These passing targeted protections do not close RV01 or make the complete evidence-link requirement pass.

## 5. Search-to-workspace browser results

Both engines used **http://127.0.0.1:8000**, not a disposable substitute, for the central journey. EO results were EOG,GEO,NEOG,OKE,PPLI,PRDO. EOG's US identity `ceri:399` preview matched8.08/9.11/6.83. Explicit Open Workspace produced a qualified URL with identity399/run45 and a null opener. The original dashboard remained open and successfully searched AAPL afterward.

The default workspace contains ten sections, with intelligence initially collapsed. Both engines load its actual758-bar chart, render all four intelligence panels, pass390px overflow checks and show the accurate AZTA known-but-unscored state. Four separate injected detail503 cases per engine leave the decision, chart and sibling panel usable; local retry succeeds. These are transport tests, not substituted source evidence.

Each engine recorded41 ticker GET requests in the successful central journey and no JavaScript errors or unexpected page-response failures. Separate direct checks of displayed source URLs recorded the500 failures. **Overall live browser verdict: FAIL because of RV01**, despite passing navigation/rendering/isolation assertions. The task-local browser probe exits1 and preserves that failure.

## 6. Historical navigation consistency

Expected values came directly from persisted immutable Combined and pinned Technical evidence, not from screenshots or hard-coded demo scores. The browser checked every navigation stage against those values.

| Run | Combined evidence | Final / fundamental / technical | Stop / target | Ranking / Winner context |
|---|---:|---|---|---|
|45|46423|8.0815 /9.1093 /6.8252|155.3293 /162.9537|Five frozen profiles; best#2; prediction/estimate947|
|44|45121|8.0877 /9.1093 /6.8390|155.3467 /162.8665|Five frozen profiles; best#3; prediction/estimate763|

Both Combined ranks remain1. Every profile label/rank/score was compared with its persisted evidence. The chart explicitly says “Current cached prices; research levels from Run N.” Selector45 is restored on Back, and selector44 on Forward/refresh, with matching provenance and levels. Screenshots `output/playwright/release-{chromium,firefox}-back45.png` and `release-{chromium,firefox}-workspace.png` support the assertions; the Chromium screenshot was visually inspected.

## 7. Intelligence evidence correctness

CERI remains current-independent snapshot5136/context83, cutoff `2026-10-20T03:05:25.356265+02:00`, with opportunity7.73/risk0 and exactly12 pinned revision links. Its six earnings and ten current catalysts were independently joined back to actual source rows in a READ ONLY transaction. Changes739/740 remain HISTORICAL relative to this snapshot; no eligible current alerts were substituted.

Lifecycle remains daily episode910, READY/GENERIC/BLOCKED, source date19October. Actual signal17717 and source snapshot/evidence linkage agree with persisted values. Full lifecycle, episode910 and setup evidence48543 links return200.

Winner retains exact combined/run pins:947 for45,763 for44. Both selected estimates are published but insufficient (`no_eligible_cohort`); no invented zero probability appears. Prediction947 and estimate947 reproduction links return200. Legacy chart, run detail, pipeline45 progress/status and full CERI ticker history also return200. CERI revision/catalyst drill-down fails below.

## 8. Performance and resources

Targeted bounded-materialization result: **PASS within the measured scope**. No universal capacity, SLA or memory-safety claim follows.

Actual NORMAL HTTP samples below are sequential network requests. SQL counts come from separate fresh READ ONLY TestClient instrumentation of the same checkout and authoritative database, three requests per operation; they are not counters injected into the NORMAL process.

| Operation | Actual HTTP ms | Actual bytes | Instrumented SQL reads |
|---|---:|---:|---:|
|Search|277.11|677|1|
|Preview|296.19|643|3|
|Initial workspace HTML|259.56|22,344|6|
|Chart|1,031.40|171,408|8|
|CERI detail|1,638.02|62,817|17|
|Lifecycle detail|429.29|17,218|16|
|Rankings detail|152.82|16,528|6|
|Winner detail|96.06|4,987|7|

Instrumented CERI detail samples were910.60/537.95/339.96ms; search1262.51/118.34/104.66ms. Full samples are retained rather than treating warm/cold variation as a universal speedup. All36 instrumented responses returned200 with no SQL writes. Initial HTML differs by36 bytes between NORMAL and the instrumentation configuration.

| Irrelevant4KiB rows | Fetched / ORM materialized | SQL reads | Warm request ms | Traced Python peak MiB | Bytes |
|---:|---|---:|---:|---:|---:|
|0|0 /0|14|346.29|9.56|8,789|
|1,000|0 /0|14|718.17|2.12|8,789|
|5,000|0 /0|14|577.50|2.12|8,789|

The SQL projects only the relevant JSON with filtering and LIMIT1. No unused history counts are executed. Tracing/cold compilation affect timings; these figures establish bounded irrelevant-row transfer/materialization, not constant-time SQL. No concurrent load test was run against NORMAL.

## 9. Test commands and counts

All commands used the existing virtual environment from the checkout. Production foreign keys were retained in migrated authority tests. Existing tests and expected behavior were not edited.

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_ticker_certification_remediation_postgresql.py -q --tb=short
# 9 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_certification_remediation_browser.py --browser chromium --browser firefox -q --tb=short
# Initial run: 3 passed, 1 Firefox chart-loading timeout

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_certification_remediation_browser.py --browser firefox -k c04 -q --tb=short
# Unchanged isolated repeat: 1 passed, 1 deselected

.venv\Scripts\python.exe -m pytest tests/integration/test_ticker_intelligence_remediation_postgresql.py tests/test_ticker_intelligence.py tests/test_ticker_workspace.py tests/test_ticker_search.py tests/test_ticker_chart_panel_routes.py tests/test_chart_data_service.py tests/test_score_card_view_service.py tests/test_dashboard_upload.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_route_security.py -q --tb=short
# 102 passed

.venv\Scripts\python.exe -m pytest tests/ceri/test_ceri_query_service.py tests/setup_lifecycle/test_query_service.py tests/test_ranking_profile_service.py tests/test_ranking_profile_routes.py tests/winner_probability/test_routes_api.py tests/winner_probability/test_estimate_lifecycle.py tests/integration/test_ceri_evidence_quarantine_postgresql.py tests/ceri/test_ceri_alert_service.py tests/ceri/test_ceri_changes_alerts_forensic.py tests/ceri/test_point_in_time_query.py tests/ceri/test_guidance_normalizer.py -q --tb=short
# 120 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 38 passed

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 19 passed
```

**292 distinct cases ultimately passed**:102+120+38+19+9+4. The isolated repeat is not added again. There were293 case executions:292 passing executions and one initial timeout. That timeout occurred while other local verification work was active, at Run44's chart-loading assertion, after selector consistency had passed. Its unchanged isolated repeat and both actual-runtime C04 journeys pass; no deterministic C04 defect was reproduced. Initial failure/server logs remain available. Existing Starlette/httpx and Alembic configuration deprecation warnings remain.

Additional probes are not counted as pytest cases:

```powershell
.venv\Scripts\python.exe output/release-verification/live_browser.py
# exits1: both engines retain the confirmed CERI source-link failure
.venv\Scripts\python.exe output/release-verification/revision_link.py
.venv\Scripts\python.exe output/release-verification/real_check.py
.venv\Scripts\python.exe output/release-verification/real.py
.venv\Scripts\python.exe output/release-verification/network_probe.py
.venv\Scripts\python.exe output/release-verification/measure.py c05-resource.json
.venv\Scripts\python.exe output/release-verification/preserve.py
```

The existing regression suites cover global navigation, dock/keyboard/fallback behavior, dashboard controls, historical selection, charts/cards, CERI/lifecycle/Winner routes, pipeline/readiness views and confirmation dialogs. Successful tests alone did not determine certification: RV01 was found by actual linked-URL checks.

## 10. Preservation

`output/release-verification/preservation.json` confirms zero changed/added source files among the1,204 baseline files, unchanged schema, identical full pipeline/job content hashes and unchanged global inspected Core/CERI/Lifecycle/Ranking/Winner ID counts/sets:45,324/4,118/1,088/23,435/1,037.

Content fingerprints match across22 relevant tables: all rows except Core evidence, whose content fingerprint is EOG-scoped. The separate global Core ID/count check remains unchanged. Full pipeline hash remains `17483e08f56de501007950b9000ef7a7`; job hash remains `84a9426400bf4cd432a2656c44c1cd3d`. These checks are supporting evidence, not a byte-for-byte database image.

An initial expensive all-company Core content SELECT was explicitly cancelled; its traceback is retained. The probe was narrowed to EOG and given a statement timeout. No authoritative worker/web process or analytical record was altered by that cancellation.

Expected lifecycle registration, generation, heartbeat, journal and telemetry changes occurred. Historical jobs, dependencies and inspected evidence content did not change. Final authority inspection still finds zero authorized pending work and the same historical invariants/scheduler block. One listener owns8000;8779 remains free. Disposable fixture databases/servers and task-owned browsers were closed. No provider acquisition, scoring refresh, queue enqueue, historical recovery/repair/replay, evidence publication or order action was invoked by workspace interactions. Source inspection shows five GET routes and read-only composition; request/SQL instrumentation confirms this boundary for executed paths.

## 11. Confirmed remaining finding

### RV01 — P2: production CERI evidence drill-down returns500

**Failed requirement:** required working intelligence source/detail links and reproducible evidence inspection. Actual displayed revision links117847/117848 and catalyst event5325 fail, although their eligible records exist and their workspace data renders.

Minimal reproduction on the activated runtime:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/ceri/revisions/117847
Invoke-WebRequest http://127.0.0.1:8000/api/ceri/events/5325
# Both return HTTP500; revision117848 independently returns500 too.
```

Fresh READ ONLY calls reproduce `TypeError: predicate-free CERI model loading is fixture-only; production reads require SQL scope`.

In `app/services/ceri/query_service.py`, `revision_detail()` line701 and `event_detail()` line634 call `_company_by_id()`. That helper calls `_load(db, CeriCompany)` at4230; the real-session guard at4007 rejects it. Event detail additionally contains a predicate-free revision loader. This establishes the observed failing path without attempting remediation or assuming when it was introduced. Fixture-backed query tests do not establish production endpoint compatibility here.

Proof: `revision-link-failure.json`, `catalyst-link-failure.json`, `additional-source-links.json` and both engines' `live-browser.json`, under `output/release-verification/`. One common P2 finding is counted, not a separate defect for every affected URL. No P0/P1 finding was demonstrated. No code fix was attempted.

## 12. Unverified limitations

Concurrent large-history capacity and OS peak memory remain **NOT VERIFIED**. Sequential migrated fixtures and Python allocation/RSS observations do not establish those properties. No additional capacity framework was built; NORMAL was not subjected to concurrent load. The initial browser timeout and transient readiness probes also prevent any claim of guaranteed latency under competing local verification load. Operational scope is the measured local research workflow, not arbitrary concurrency or history size.

Cross-source atomic snapshots, automatic live revocation and reconstructed historical price bars are not claimed. Current cache/source labels remain explicit. Observability remains degraded independently of the feature blocker.

## 13. Final release readiness

**Do not certify the complete workspace for release.** C01–C05 and the targeted F01–F06 authority/rendering regressions pass, and the corrected NORMAL backend is safely activated with healthy final application/core readiness. RV01 still prevents the mandatory evidence-link workflow from passing. The complete feature is therefore FAILED and not ready for normal research sign-off. Verification stops here; no remediation, commit, push or further restart is authorized by this report.
