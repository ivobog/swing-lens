# SwingLens Complete Forensic GUI Analysis

Audit date: 2026-10-08 (Europe/Zurich)  
Application: `http://127.0.0.1:8000`  
Forensic subject: UploadRun 30 / PipelineRun 30  
Method: rendered-browser inspection, Playwright interaction and exhaustive GET crawl, HTTP/API extraction, source tracing, and PostgreSQL transactions forced read-only.

## 1. EXECUTIVE VERDICT

**FAIL — the audited GUI is not a truthful, complete representation of authoritative runtime/evidence state.** The backend contains strong ownership and fail-closed evidence controls in several important paths, and every current application link resolved, but the rendered GUI has five P1, ten P2, and one P3 confirmed defects.

The most consequential facts are:

- `/runs/30` prominently reports UploadRun `COMPLETED` while the authoritative PipelineRun 30 is `PARTIAL`; the pipeline is omitted unless `?pipeline_id=30` is supplied.
- Run 30 contains 209 captured and certified CERI snapshots/evidence rows, all explicitly excluded after the parent pipeline became `PARTIAL`; the GUI instead says `0 snapshots` and `No CERI capture yet`.
- 64 cockpit rows render a technical score with the claim `Feeds Final / Decision`, while the persisted combined row says `has_technical=false`, `dual_score=NULL`, and `missing_technical`.
- The run-scoped Setup card includes a global active-episode count (246) rather than Run 30's current-snapshot count (165), and its `305 changes` disagrees with the linked Run 30 Setup view's authoritative 1,290 changes.
- Setup administrative mutation routes do not enforce the declared local-admin or CSRF requirements. Winner administrative routes enforce loopback but not CSRF.
- Terminal progress remains semantically active (`CAPTURING_WINNER_PREDICTIONS`), normalization renders `909 / 618 items`, and a hidden reconnect warning is visibly displayed despite no network failure.

No production code or database contents were changed. No mutating GUI action was executed. Diagnostic artifacts are in `output/playwright/gui_forensic/` plus this report; browser tooling also left `.playwright-cli/` present, and it was preserved because cleanup was prohibited. The running application was not restarted; an attempted duplicate foreground bind was rejected because port 8000 was already occupied and caused no application or database state change.

## 2. ENVIRONMENT / FORENSIC BASELINE

| Item | Recorded baseline |
|---|---|
| Branch | `codex/ceri-authority-remediation` |
| HEAD | `c2dc012052544d9964c45171b66d35aa99f5ae0e` |
| Worktree | Pre-existing dirty worktree: 15 modified tracked files and 8 untracked source/test/migration/script paths; preserved. Diagnostic outputs are ignored and no pre-existing change was altered. |
| App health | `/health`: `status=ok`; `/ready/core`: `status=ok`, database/migrations/storage/supervisor/web/worker/topology/metrics all `ok`. |
| Runtime | Supervisor PID 30120; web listener PID 11628 on `127.0.0.1:8000`; worker PID 24876; supervisor topology verified. |
| Database | PostgreSQL 18.3, database `swinglens`, host `127.0.0.1:5432`, user `postgres`; provenance `AUTHORITATIVE_LOCAL`. All audit connections used `default_transaction_read_only=on`. |
| Schema | Database and runtime readiness both report Alembic `0091_readiness_constraints`. |
| Timezones | Database `Europe/Berlin`; browser `Europe/Zurich`; both were UTC+02:00 on the audit date. API timestamps carried `+02:00`. No silent offset conversion was observed. |
| Durable mode | `use_durable_pipeline=true`; durable worker process enabled; embedded/job worker disabled in web process. CERI, CERI UI, Setup Lifecycle, and Winner Probability enabled. |
| UploadRun 30 | `COMPLETED`; file `skimmer 2_2026-10-08.csv`; uploaded `2026-10-08T11:58:49.622632+02:00`; processed `11:58:51.183836+02:00`; 209 rows. |
| PipelineRun 30 | Upload owner 30; `PARTIAL`; execution authority `ACTIVE`; created `11:59:24`, started `11:59:29`, terminal `16:01:04`; 13/13 persisted steps `COMPLETED`; message records 209 combined, 181 incomplete, 0 IB failures, Winner 28 inserted/181 excluded/0 failed. |
| Execution identity | Pipeline `scope_id`, `refresh_cycle_id`, `acquisition_plan_id`, market context 53, and Decision Handoff manifest 20 consistently bind UploadRun/PipelineRun 30. Post-acquisition CERI context 54 is intentionally not a pipeline-owned market context. |
| Jobs | Related job family IDs 895–963; every inspected child job has `related_run_id=30`; no run/payload ownership mismatch. Job 963 is `COMPLETED`, 209/209, but retains stage `CAPTURING_WINNER_PREDICTIONS`. |
| CERI | 209 score snapshots and 209 core CERI evidence records for Run 30 at `13:47:06+02`; all effective dispositions `EXCLUDED`, reason `PARENT_PIPELINE_UNSUCCESSFUL`, actor `pipeline_terminal_reconciliation`; zero current projections by policy. |
| Setup | Capture evaluation 42: 209 snapshots. Evaluation 43: 209 canonical snapshots, 1,194 signal changes, 96 meaningful lifecycle changes (1 phase + 19 state + 76 episode opens), 27 alerts, 738 warnings. Total historical Run 30 changes: 1,290. |
| Winner | Prediction IDs 146–173, all `run_id=30`; 28 estimates; 56 Run 30 market-data obligations (two per prediction), all `FETCH_REQUIRED`. Decision Handoff manifest 20 owns the Run 30 prediction capture. |

Reproduction evidence: `db_forensics.json`, `api_evidence.json`, `browser_inventory.json`, `missing_and_wrong_identity_routes.json`, and `evidence_manifest.json` under `output/playwright/gui_forensic/`.

## 3. GUI COVERAGE

| Coverage dimension | Result |
|---|---:|
| Root pages | 4 / 4 |
| Distinct representative child layouts rendered and screenshot | 18 |
| Unique identity-bearing dynamic children resolved | 880: 209 run charts, 290 Setup ticker timelines, 353 Setup episodes, 28 Winner predictions |
| Unique child URLs covered including non-overlapping layout pages | 895 |
| Root controls inventoried | 907 (539 link instances, 311 buttons, 12 forms) |
| Full element-ledger rows | 4,719 (2,483 controls, 26 table instances, 2,210 badges/status/messages) |
| Unique root-link targets checked | 160 |
| Root/representative table rows rendered | 3,419 (1,166 root + 2,253 child-layout rows) |
| Direct field/cell comparisons | 11,010, plus 4,164 exhaustive Setup row-identity/order comparisons |
| Setup pagination | Current: all 29 pages / 2,874 rows; Run 30: all 26 pages / 1,290 rows |
| Structured API/export calls inspected | 72; zero failed |
| Link/detail GET validations | 1,040 (160 root targets + 880 dynamic children); zero failures |
| Safe interactions | 26/26 Setup Expand controls; run detail expand/collapse, filters, search, two sort directions, clipboard, clear, reload, back/forward |
| Screenshots | 22 full-page PNGs (four roots plus 18 child layouts) |

Mutating POST actions were not executed, as required. They were certified through rendered state, DOM wiring, client JavaScript, route declaration, authority checks, and backend implementation. Repeated dynamic pages were all opened and identity/error-checked; each distinct layout/state was visually inspected. The repeated cells of all 880 same-layout detail pages were not individually re-queried from PostgreSQL because that would duplicate the same server-side DTO/template mapping; their IDs, owners, status, and error markers were exhaustively validated, while representative layouts and all root/paginated tables received field-level validation.

The complete element inventory is `output/playwright/gui_forensic/gui_element_inventory.csv` with the requested PAGE, ELEMENT, LABEL, TYPE, selector, state, action, target, expected, actual, source, result, and finding fields.

## 4. RUN 30 DETAIL PAGE FINDINGS

### SL-GUI-001 — P1 — Default run page masks authoritative `PARTIAL` pipeline state

- **PAGE / URL:** `/runs/30`; compare `/runs/30?pipeline_id=30` and `/runs/30/pipeline/30`.
- **GUI ELEMENT:** Hero status and optional Pipeline card.
- **OBSERVED BEHAVIOR:** Default page prominently displays `COMPLETED` from UploadRun 30 and omits PipelineRun 30. Supplying `pipeline_id=30` reveals `PARTIAL` and the partial-completion message.
- **EXPECTED BEHAVIOR:** The current authoritative pipeline execution for the run must be visible by default and clearly distinguished from upload ingestion status.
- **REPRODUCTION STEPS:** Open `/runs/30`; record hero status and absence of Pipeline 30; open `?pipeline_id=30`; compare `PARTIAL`.
- **SCREENSHOT / DOM:** `root_runs_30_2958ebf8a3.png`; `child_runs_30_pipeline_id_30_e50f0f0ce9.png`; body text in `browser_inventory.json`.
- **NETWORK/API:** `/runs/30/pipeline/30/status` returns PipelineRun 30 `PARTIAL`; no network errors.
- **BACKEND CODE PATH:** `app/routers/run_routes.py:256-276`, `_pipeline_status_for_run` at 1749-1757; `app/templates/run_detail.html` hero and conditional pipeline card.
- **DATABASE/EVIDENCE QUERY:** `SELECT id,upload_run_id,status,message FROM pipeline_runs WHERE id=30;` → `PARTIAL`; `SELECT id,status FROM upload_runs WHERE id=30;` → `COMPLETED`.
- **ROOT CAUSE:** The route only loads a pipeline when a query-string ID is supplied; the hero always uses the upload row's status.
- **AFFECTED DATA/ROWS:** UploadRun 30 / PipelineRun 30.
- **RUN 30 AFFECTED:** Yes. **OTHER RUNS:** Any run with a pipeline and no `pipeline_id` query parameter.
- **CROSS-RUN RISK:** Low for ownership; high semantic risk from choosing the wrong state domain.
- **OPERATIONAL IMPACT:** An operator can treat a partial decision pipeline as successfully complete.
- **RECOMMENDED REMEDIATION:** Resolve and display the canonical/current pipeline by run ownership; label upload and pipeline states separately; retain explicit pipeline selection only for historical executions.

### SL-GUI-002 — P1 — Captured CERI evidence is silently rendered as no capture

- **PAGE / URL:** `/runs/30` and `/runs/30/ceri`.
- **GUI ELEMENT:** CERI card (`0 snapshots`, `No CERI capture yet`) and empty Run 30 CERI page.
- **OBSERVED / EXPECTED:** 209 immutable snapshots and 209 evidence rows exist, but all were excluded because PipelineRun 30 is `PARTIAL`. The GUI must say “captured but excluded” with count and reason, not “no capture.”
- **REPRODUCTION:** Open both URLs; compare with CERI disposition evidence.
- **SCREENSHOT / DOM:** `root_runs_30_2958ebf8a3.png`; `child_runs_30_ceri_65bdd45623.png`.
- **NETWORK/API:** `/api/ceri/run/30` legitimately returns zero *eligible* items; the GUI loses the distinction between absent and ineligible.
- **BACKEND CODE PATH:** `run_routes._ceri_context` uses `eligible_snapshot_select`; `app/services/ceri/evidence_eligibility.py:20-21,141`; `run_detail.html:186-200`.
- **DATABASE/EVIDENCE QUERY:** Count `ceri_score_snapshots WHERE run_id=30` → 209; latest joined dispositions → 209 `EXCLUDED/PARENT_PIPELINE_UNSUCCESSFUL`.
- **ROOT CAUSE:** Eligibility filtering occurs before summary construction and no excluded-population diagnostic is returned to the template.
- **AFFECTED:** All 209 Run 30 CERI snapshots/evidence. **RUN 30:** Yes. **OTHER RUNS:** Any failed/blocked/cancelled/partial owner.
- **CROSS-RUN RISK:** None observed; this is authority-state erasure, not leakage.
- **OPERATIONAL IMPACT:** Auditors cannot distinguish missing capture from deliberately contained evidence.
- **RECOMMENDED REMEDIATION:** Return eligible, excluded, and absent counts separately with disposition reason and parent pipeline identity.

### SL-GUI-003 — P1 — 64 technical scores are falsely labeled as decision inputs

- **PAGE / URL:** `/runs/30` Decision Cockpit.
- **GUI ELEMENT:** Technical column and `Feeds Final / Decision` lineage label.
- **OBSERVED BEHAVIOR:** 64 rows display the `technical_scores.dual_score`; their authoritative combined rows have `dual_score=NULL`, `has_technical=false`, and `missing_technical`. Example ANF shows 6.8593 although its final 7.638 is the 8.638 fundamental score with incomplete penalty, not a technical-weighted result.
- **EXPECTED:** Display the persisted combined input that actually produced Final/Decision, or clearly label a separate unused/current technical score.
- **REPRODUCTION:** Inspect ANF (and 63 peers) in the cockpit; compare Combined CSV/DB with row `data-technical-score`.
- **SCREENSHOT / DOM:** `root_runs_30_2958ebf8a3.png`; `table_value_forensics.json` records 64 semantic mismatches.
- **NETWORK/API:** Combined CSV exports blank `dual_score` and `missing_technical`; browser DOM contains a numeric active technical score.
- **BACKEND CODE PATH:** `app/services/technical_display_fields.py:13-24` falls back from combined to TechnicalScore; `run_detail.html:851-881,935` labels that fallback as feeding Final/Decision.
- **DATABASE QUERY:** Join `combined_results c` to `technical_scores t` on Run 30/ticker where `c.dual_score IS NULL AND t.dual_score IS NOT NULL` → 64; non-null mismatches → 0.
- **ROOT CAUSE:** `_first_present(combined.dual_score, score.dual_score)` treats an absent combined input as permission to substitute a different value while retaining decision-lineage wording.
- **AFFECTED:** 64/209 cockpit rows. **RUN 30:** Yes. **OTHER RUNS:** Any combined record lacking a technical input while a technical row exists.
- **CROSS-RUN RISK:** No cross-run value was found; the semantic lineage is nevertheless false.
- **OPERATIONAL IMPACT:** Reviewers can attribute a score/decision to evidence the decision explicitly rejected as missing.
- **RECOMMENDED REMEDIATION:** Never fall back for the “feeds decision” field; render unavailable plus a separately labeled non-input technical observation.

### SL-GUI-006 — P2 — Run card uses global active Setup episode count

- **PAGE / URL:** `/runs/30` Setup Lifecycle card.
- **GUI ELEMENT:** `246 active episodes`.
- **OBSERVED / EXPECTED:** 246 is the global active count. Run 30 current snapshots own 165 active episodes; the run card must be run/evidence scoped or explicitly labeled global.
- **REPRODUCTION:** Open `/runs/30`; compare active episodes joined through Run 30 current snapshot IDs.
- **SCREENSHOT / DOM:** `root_runs_30_2958ebf8a3.png`.
- **NETWORK/API:** Run 30 Setup page/API is scoped to evaluation/source run 30; card query is not.
- **BACKEND:** `run_routes._setup_lifecycle_context`, lines 1326-1332, filters only `SetupLifecycleEpisode.status == ACTIVE`.
- **DATABASE QUERY:** Global active → 246; active episodes whose current snapshot has `run_id=30` → 165; episodes opened from Run 30 snapshots → 76.
- **ROOT CAUSE:** Missing ownership join/filter.
- **AFFECTED:** The displayed count; **RUN 30:** Yes; **OTHER RUNS:** All run cards.
- **CROSS-RUN RISK:** Confirmed aggregate contamination.
- **OPERATIONAL IMPACT:** Overstates run-specific Setup activity by 81.
- **RECOMMENDED REMEDIATION:** Scope through the canonical snapshot/evaluation owner and name the chosen metric precisely.

### SL-GUI-007 — P2 — Run Setup `changes` count disagrees with its child view

- **PAGE / URL:** `/runs/30` versus `/runs/30/setup-lifecycle`.
- **GUI ELEMENT:** `305 changes` versus `1,290 changes`.
- **OBSERVED / EXPECTED:** Card counts all 305 lifecycle events (including 209 canonical-revision events) but omits 1,194 signal-change events. The child uses 96 meaningful lifecycle + 1,194 signal = 1,290. Linked screens must use the same definition or explicit labels.
- **REPRODUCTION:** Compare the card and child page; traverse all 26 child pages.
- **SCREENSHOT / DOM:** root and `child_runs_30_setup-lifecycle_dccdf05154.png`.
- **NETWORK/API:** Run 30 API meta total is 1,290; every page/row identity matched.
- **BACKEND:** `run_routes.py:1308-1324` counts `SetupLifecycleEvent` only; Setup query service unifies meaningful lifecycle and signal events.
- **DATABASE QUERY:** Lifecycle types: 209 canonical revision + 76 opened + 1 phase + 19 state = 305; signal changes = 1,194.
- **ROOT CAUSE:** Two incompatible definitions share the same `changes` label.
- **AFFECTED:** Run 30 summary; **RUN 30:** Yes; **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** None; within-run semantic inconsistency.
- **OPERATIONAL IMPACT:** Understates actionable change history by 985 and mixes maintenance events into the count.
- **RECOMMENDED REMEDIATION:** Reuse the child-view aggregation contract or split canonical revisions, lifecycle changes, and signal changes.

## 5. SETUP LIFECYCLE / MARKET CHANGES FINDINGS

“Current” is `CURRENT_MARKET` / `CURRENT_PROJECTION`: canonical current projections, not simply the largest ID. The current feed traversed 29 cursor pages, 2,874 rows, 290 tickers, and 353 episode IDs. Run 30 historical mode traversed 26 pages and exactly 1,290 rows. Ordering, cursors, identities, and page totals were stable and exact. No legacy/current authority mixing was found in those query-service paths.

### SL-GUI-004 — P1 — Setup admin mutations do not enforce declared local-admin/CSRF policy

- **PAGE / URL:** `/setup-lifecycle/operations`; POST `/api/setup-lifecycle/evaluations` and `/api/setup-lifecycle/replay`.
- **GUI ELEMENT:** Queue scoped evaluation and Replay forms.
- **OBSERVED / EXPECTED:** Decorators declare `local_admin_required=True`, but the common authorizer never evaluates that field or `csrf_required`; the Setup endpoints do not call `require_local_admin`. The GUI sends no CSRF token. Local administrative mutation must enforce both origin-independent CSRF and loopback/admin gating.
- **REPRODUCTION:** Inspect form DOM/JS and route wrappers. No POST was sent.
- **SCREENSHOT / DOM:** `child_setup-lifecycle_operations_ddfedfba7e.png`; element ledger form rows.
- **NETWORK/API:** Static wiring targets the two mutation endpoints without a token.
- **BACKEND:** `app/security.py:180-219` checks runtime mutation capability only; Setup routes at 762-877 rely solely on decorator metadata.
- **DATABASE/EVIDENCE QUERY:** Not executed; source proves that a successful request can enqueue a BackgroundJob or persist replay output. Baseline job IDs remained unchanged during the audit.
- **ROOT CAUSE:** Security metadata is descriptive but not enforced, and routes omit the explicit guard used by CERI/Winner.
- **AFFECTED:** All users able to reach the HTTP listener. **RUN 30:** Potentially if submitted with its identity. **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** High: caller-selected scope/run/ticker.
- **OPERATIONAL IMPACT:** Unauthorized job queuing or persisted replay is possible within reachable network/runtime constraints.
- **RECOMMENDED REMEDIATION:** Enforce both flags centrally and add explicit defense-in-depth guards/tokens; add negative integration tests.

### SL-GUI-014 — P2 — Replay form does not transmit values in the endpoint's expected location

- **PAGE / URL:** `/setup-lifecycle/operations` → `/api/setup-lifecycle/replay`.
- **GUI ELEMENT:** Replay form.
- **OBSERVED / EXPECTED:** The form submits URL-encoded body fields, while FastAPI declares scalar query parameters. Unlike the evaluation form, Replay has no JavaScript binding that converts fields to query parameters. Inputs can therefore be ignored and the browser navigates to raw JSON. The action must preserve ticker/date/persist/confirmation/requester/reason and provide in-page result/error feedback.
- **REPRODUCTION:** Inspect form and `setup_lifecycle.js`; POST intentionally not executed.
- **SCREENSHOT / DOM:** Operations screenshot and inventory row for the form.
- **NETWORK/API:** Form action/method captured; no data-moving JS handler applies to it.
- **BACKEND:** `setup_lifecycle_routes.py:869-903`; template lines 44-53; JS `bindSetupLifecycleOperationForms` only matches `[data-slse-operation-form]`.
- **DATABASE QUERY:** Not applicable; mutation prohibited.
- **ROOT CAUSE:** Form encoding and route parameter contract mismatch.
- **AFFECTED:** Replay form fields. **RUN 30:** Not directly unless entered. **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** Inputs may be dropped, broadening replay scope.
- **OPERATIONAL IMPACT:** Wrong-scope dry run, inability to persist as requested, raw JSON navigation.
- **RECOMMENDED REMEDIATION:** Use a typed body or JS serialization, retain context, and add confirmation/result UX.

## 6. CERI FINDINGS

Backend CERI authority is fail-closed: `PARTIAL` belongs to `UNSUCCESSFUL_PIPELINE_STATES`; all Run 30 snapshots were reconciled to explicit excluded dispositions. No uncertified/quarantined/other-run snapshot entered the eligible Run 30 response. The GUI defects are loss of that authority explanation and incomplete operations visibility.

### SL-GUI-012 — P2 — CERI Operations silently truncates 52,547 stale records to 50

- **PAGE / URL:** `/ceri/operations`.
- **GUI ELEMENT:** Stale count and stale-record cards/list.
- **OBSERVED / EXPECTED:** Page reports 52,547 stale items but route requests only 50 and provides no paging/filter/navigation control for the remainder.
- **REPRODUCTION:** Open Operations; count rendered stale records; compare operations status total.
- **SCREENSHOT / DOM:** `child_ceri_operations_6d1c3423df.png`; inventory.
- **NETWORK/API:** `/api/ceri/operations/stale` supports query pagination; page call hard-codes `limit=50`.
- **BACKEND:** `ceri_routes.py:331-349`; `CeriQueryService.operations_stale`.
- **DATABASE QUERY:** `_database_stale_count(...)` / operations status → 52,547; GUI list → 50.
- **ROOT CAUSE:** Summary total and fixed sample are rendered without disclosing sampling or navigation.
- **AFFECTED:** 52,497 hidden stale records. **RUN 30:** Potentially mixed into global operations. **OTHER RUNS:** Yes/global.
- **CROSS-RUN RISK:** Global page is intentional, but hidden population prevents run/provider investigation.
- **OPERATIONAL IMPACT:** Operators cannot inspect almost all reported stale evidence.
- **RECOMMENDED REMEDIATION:** Label the sample and add cursor/offset pagination and filters.

### SL-GUI-013 — P2 — Missing CERI ticker returns HTTP 200 error shell

- **PAGE / URL:** `/ceri/ticker/NOTREAL` (also an ineligible real ticker such as ABBV produces the shell).
- **GUI ELEMENT:** Ticker detail empty/error state.
- **OBSERVED / EXPECTED:** HTML route returns 200 and a `TICKER_NOT_FOUND` error shell; the API route correctly maps the same domain error to 404. Missing targets must return 404 while preserving a useful HTML error body.
- **REPRODUCTION:** Direct GET `/ceri/ticker/NOTREAL`; compare `/api/ceri/ticker/NOTREAL`.
- **SCREENSHOT / DOM:** `child_ceri_ticker_ABBV_bee889e5f9.png`; `missing_and_wrong_identity_routes.json`.
- **NETWORK/API:** HTML 200 vs structured API 404.
- **BACKEND:** `ceri_ticker_page` at `ceri_routes.py:211-225`; `_ui_payload_or_empty` at 1131 catches `CeriQueryError` and discards its status.
- **DATABASE QUERY:** No matching eligible CERI current projection for `NOTREAL`.
- **ROOT CAUSE:** HTML error helper flattens domain errors into empty payloads without setting response status.
- **AFFECTED:** Missing/ineligible ticker routes. **RUN 30:** ABBV demonstrates the same state. **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** None.
- **OPERATIONAL IMPACT:** Monitoring, automation, caches, and users interpret a missing target as a successful page.
- **RECOMMENDED REMEDIATION:** Preserve domain HTTP status and distinguish not-found from captured-but-excluded.

## 7. WINNER EVIDENCE FINDINGS

Run 30 Winner ownership is correctly scoped: all 28 predictions, their estimates, 56 obligations, child URLs, and Decision Handoff link resolve to Run/Pipeline 30. No older-run prediction or obligation appeared in the Run 30 view. Probabilities are correctly withheld as insufficient evidence. Operations is intentionally global, but it omits important global statuses.

### SL-GUI-005 — P1 — Winner admin POST actions lack CSRF protection

- **PAGE / URL:** `/winner-probability/operations`; POST outcomes/process and cohorts/refresh.
- **GUI ELEMENT:** `Process due outcomes`, `Refresh cohorts`.
- **OBSERVED / EXPECTED:** Routes explicitly enforce enabled + loopback client, but do not request CSRF validation; JavaScript sends only `Accept: application/json`. Local administrative mutations must reject cross-origin submissions.
- **REPRODUCTION:** Inspect rendered forms, `winner_probability.js`, route guard. POST not executed.
- **SCREENSHOT / DOM:** `root_winner-probability_operations_f13c3e827b.png`.
- **NETWORK/API:** Form actions captured; no CSRF header/token.
- **BACKEND:** `winner_probability_routes.py:637-704` calls `_require_local_admin`; helper calls `require_local_admin(... csrf_required=False)`; `security.py:292-299` would validate only when requested.
- **DATABASE QUERY:** Not executed; actions enqueue maturation/cohort jobs and commit.
- **ROOT CAUSE:** Local-host validation is treated as sufficient CSRF protection.
- **AFFECTED:** Global Winner processing. **RUN 30:** Could affect its outcomes/obligations. **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** High because actions operate global due populations.
- **OPERATIONAL IMPACT:** A browser-based cross-origin request may queue costly/global work, subject to browser private-network policy.
- **RECOMMENDED REMEDIATION:** Issue and validate the existing local-admin CSRF token, include it in JavaScript, and test hostile-origin submissions.

### SL-GUI-011 — P2 — Operations hides 68 failed/unavailable obligations

- **PAGE / URL:** `/winner-probability/operations`.
- **GUI ELEMENT:** Market-data obligation metrics.
- **OBSERVED / EXPECTED:** Backend returns `FETCH_REQUIRED=278`, `UNAVAILABLE=67`, `FAILED=1`; UI renders only Fetch Required and Satisfied (default 0). All non-success statuses, especially terminal failures, must be visible.
- **REPRODUCTION:** Compare page metrics with operations JSON.
- **SCREENSHOT / DOM:** Winner operations screenshot; API evidence.
- **NETWORK/API:** `/api/winner-probability/operations/status` includes all grouped statuses.
- **BACKEND:** `operations_service.py:64-94` groups every status; template lines 36-37 selects two keys.
- **DATABASE QUERY:** `SELECT status,count(*) FROM winner_market_data_obligations GROUP BY status;` → values above.
- **ROOT CAUSE:** Template-level whitelist omits failure states.
- **AFFECTED:** 68 global obligations. **RUN 30:** Its 56 are visible within Fetch Required, not among hidden failures. **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** Global by design; no false owner shown.
- **OPERATIONAL IMPACT:** Serious market-data failures are invisible on the operations dashboard.
- **RECOMMENDED REMEDIATION:** Render every status with failure emphasis and drill-down.

### SL-GUI-016 — P3 — Literal Python `None` leaks into Winner model cells

- **PAGE / URL:** `/runs/30/winner-probability`.
- **GUI ELEMENT:** Model column.
- **OBSERVED / EXPECTED:** 28 literal `None` cells render. Missing optional model labels/status should be blank or `Unavailable`/`Insufficient`.
- **REPRODUCTION:** Open Run 30 Winner page; inspect each row.
- **SCREENSHOT / DOM:** `child_runs_30_winner-probability_9b2d9c8b58.png`; `table_value_forensics.json`.
- **NETWORK/API:** Source fields are JSON null; semantic probability/grade values otherwise match.
- **BACKEND:** `winner_probability_run.html:173` renders nullable fields without fallback.
- **DATABASE QUERY:** 28 serving estimates have null model label/status presentation field.
- **ROOT CAUSE:** Missing null-safe template formatting.
- **AFFECTED:** 28 rows. **RUN 30:** Yes. **OTHER RUNS:** Yes where model metadata is null.
- **CROSS-RUN RISK:** None.
- **OPERATIONAL IMPACT:** Minor ambiguity/unpolished evidence presentation.
- **RECOMMENDED REMEDIATION:** Use explicit null fallback consistent with evidence grade.

## 8. CHILD PAGE FINDINGS

All 209 run ticker charts, 290 Setup ticker timelines, 353 Setup episodes, and 28 Winner prediction details returned HTTP 200, contained the requested identity, and had no detected error marker. Eighteen distinct child layouts were screenshot and inspected. Wrong-owner pipeline URLs and nonexistent run ticker, Setup episode, and Winner prediction URLs correctly returned 404. CERI's missing-ticker 200 is SL-GUI-013.

The following child behavior passed:

- `/runs/29/pipeline/30` and `/runs/30/pipeline/29` both returned 404, proving the pipeline child route checks UploadRun ownership.
- Direct navigation, reload, back, and forward preserved `/runs/30` context.
- All 26 enabled Setup Expand controls on the first page loaded and collapsed correctly without failed requests or JavaScript errors.
- Run detail Top 10 returned 10 and restored 209; search `SN` returned `SN`, `VRSN`, `SNX`; clear restored 209; numeric sort was correct in both directions; copy-top-10 wrote 10 symbols.
- Every actual root link target returned 200, including streamed samples of large exports.

## 9. CROSS-SCREEN CONSISTENCY FINDINGS

Confirmed disagreements:

- UploadRun `COMPLETED` vs PipelineRun `PARTIAL` (SL-GUI-001).
- CERI `0/no capture` vs 209 captured-and-excluded evidence objects (SL-GUI-002).
- Setup `305 changes` vs linked Run 30 Setup `1,290` (SL-GUI-007).
- Setup `246 active episodes` is global; Run 30 current-snapshot ownership yields 165 (SL-GUI-006).
- Cockpit technical value/lineage vs persisted combined input (SL-GUI-003).

Consistent entities:

- Winner Run 30 displays 28 predictions and 28 estimates, matching database and CSV/API.
- Pipeline child page shows 13 persisted steps and their timestamps/statuses exactly.
- CERI eligibility policy is consistent between CERI API and core current projections; the defect is the GUI explanation.
- Run/Pipeline/Decision Handoff/market context ownership keys match; no foreign-run child job, prediction, estimate, or handoff was found.

## 10. RUN-ISOLATION / AUTHORITY FINDINGS

Run isolation is **not certified** because the Run 30 Setup card executes an unscoped global active-episode query (SL-GUI-006). No other confirmed cross-run data inclusion was found.

Positive checks:

- Pipeline child routes call `_require_pipeline_for_run` and reject owner mismatch.
- All jobs in the inspected workflow family bind Run/Pipeline 30 consistently; zero mismatch query results.
- Winner run service filters predictions by `run_id=30`; all 28 children/estimates/obligations match their prediction owner.
- Setup Run 30 mode uses evaluation/source-run/canonical evidence and exact cursor contracts.
- CERI Run 30 uses `eligible_snapshot_select` and excludes every parent-unsuccessful snapshot. No “latest row wins” leak was observed.

Suspicious-selector review covered `.limit(1)`, descending timestamps, max/first/latest/global lookup patterns in the audited routes/services/repositories. Legitimate uses were either global operations summaries, explicitly current canonical projections, or owner-filtered queries. The confirmed unsafe selector is the global Setup active-episode count. CERI Operations' fixed 50-row sample is completeness defect SL-GUI-012, not cross-run contamination.

## 11. FRONTEND / NETWORK FINDINGS

### SL-GUI-008 — P2 — Terminal pipeline labels a persisted last stage as active

- **PAGE / URL:** `/runs/30/pipeline/30`.
- **GUI ELEMENT:** `Active job stage CAPTURING_WINNER_PREDICTIONS`.
- **OBSERVED / EXPECTED:** Pipeline and job are terminal; stage should be cleared or labeled `Last stage`.
- **REPRODUCTION:** Open progress child and compare status/API/job row.
- **SCREENSHOT / DOM:** `child_runs_30_pipeline_30_ad0460fdcb.png`.
- **NETWORK/API:** Status payload exposes terminal `COMPLETED/PARTIAL` and persisted stage together.
- **BACKEND:** pipeline status payload uses job `progress_stage`; template `pipeline_progress.html:139-140` hard-labels it Active.
- **DATABASE QUERY:** Job 963 `COMPLETED`, `progress_stage=CAPTURING_WINNER_PREDICTIONS`, 209/209.
- **ROOT CAUSE:** Terminal cleanup/label semantics do not distinguish current from last known progress.
- **AFFECTED:** Job 963/Pipeline 30. **RUN 30:** Yes. **OTHER RUNS:** Any terminal job retaining stage.
- **CROSS-RUN RISK:** None. **IMPACT:** Falsely suggests ongoing work.
- **RECOMMENDATION:** Clear current stage on terminal transition or change label based on terminality.

### SL-GUI-009 — P2 — CERI normalization progress exceeds 100%

- **PAGE / URL:** `/runs/30/pipeline/30`.
- **GUI ELEMENT:** Normalization `909 / 618 items`.
- **OBSERVED / EXPECTED:** Completed processed count must not exceed denominator; unknown totals should be separate.
- **REPRODUCTION:** Open progress page/API.
- **SCREENSHOT / DOM:** Pipeline screenshot.
- **NETWORK/API:** Same values in status payload.
- **BACKEND:** `pipeline_service.phase_view`, lines 540-560, sums processed and `progress_total or 0` independently.
- **DATABASE QUERY:** 20 normalize jobs; 6 null totals; processed sum 909, known-total sum 618.
- **ROOT CAUSE:** Processed values from jobs with null denominators contribute to numerator only.
- **AFFECTED:** Normalization phase. **RUN 30:** Yes. **OTHER RUNS:** Any mixed null-total phase.
- **CROSS-RUN RISK:** None. **IMPACT:** Invalid progress interpretation.
- **RECOMMENDATION:** Aggregate only comparable pairs or report known/unknown totals separately.

### SL-GUI-010 — P2 — Hidden reconnect warning is visibly rendered

- **PAGE / URL:** `/runs/30/pipeline/30` (same pattern in fetch progress).
- **GUI ELEMENT:** `Connection interrupted; retrying in 5s.`
- **OBSERVED / EXPECTED:** Element has `hidden` and no failed request; it must be invisible until JavaScript explicitly reveals it.
- **REPRODUCTION:** Direct-load terminal page; inspect computed style: `hidden=true`, Playwright visible, `display:inline-flex`.
- **SCREENSHOT / DOM:** Pipeline screenshot and browser DOM capture.
- **NETWORK/API:** Zero failed responses/console/page errors; terminal page does not start active polling.
- **BACKEND/FRONTEND:** `pipeline_progress.html:243-245`; `.badge { display:inline-flex }` at `app.css:1201` overrides user-agent hidden styling; no global `[hidden]` rule.
- **DATABASE QUERY:** Not applicable; job/pipeline are terminal.
- **ROOT CAUSE:** Author CSS display declaration defeats the HTML hidden attribute.
- **AFFECTED:** Reconnect badge components. **RUN 30:** Yes. **OTHER RUNS:** Yes.
- **CROSS-RUN RISK:** None. **IMPACT:** False live connectivity alarm.
- **RECOMMENDATION:** Add authoritative `[hidden]{display:none!important}` or avoid display rules on hidden nodes.

Network/browser result: root pages generated four requests each (document/static/status), no failed request, 4xx/5xx, console error, page exception, duplicate storm, cancellation, horizontal overflow, or detected stale client-state crossover. Query parameters/cursors were stable. No transient incorrect asynchronous state was observed beyond the persistently visible false reconnect badge.

## 12. PERFORMANCE FINDINGS

### SL-GUI-015 — P2 — Run detail and chart views are materially slow

- **PAGE / URL:** `/runs/30`, `/runs/30?pipeline_id=30`, and all run ticker charts.
- **GUI ELEMENT:** Whole-page load/navigation.
- **OBSERVED / EXPECTED:** Cold browser load of default run page was 15.70 s with 2.57 MB HTML; pipeline-query variant 8.42 s. Across 209 ticker charts: average 5.52 s, p95 14.38 s, max 20.83 s. A local evidence GUI should support responsive navigation.
- **REPRODUCTION:** Playwright `goto(... wait_until=load)` at 1440×1000, Europe/Zurich; enumerate all charts.
- **SCREENSHOT / DOM:** Navigation timing in `browser_inventory.json`; exhaustive timings in `exhaustive_gui_crawl.json`.
- **NETWORK/API:** Four root requests; no duplicate calls. Slowest child layouts: coverage 7.23 s; Winner outcomes 4.37 s. Slow exports included fetch plan 6.10 s, coverage 5.82 s, combined 5.70 s.
- **BACKEND:** Server-rendered Run detail eagerly emits large per-row detail markup; chart routes perform per-ticker data assembly.
- **DATABASE QUERY:** No destructive profiling/EXPLAIN ANALYZE was run; timing is end-to-end observed evidence.
- **ROOT CAUSE:** INVESTIGATION REQUIRED for exact SQL attribution; large 2.57 MB response and eager 209-row detail rendering are confirmed contributors.
- **AFFECTED:** 209-row Run 30 UI and charts. **RUN 30:** Yes. **OTHER RUNS:** Scales with run size.
- **CROSS-RUN RISK:** None. **IMPACT:** Slow forensic navigation and increased timeout/race exposure.
- **RECOMMENDATION:** Profile read-only query plans; defer detail payloads; paginate/virtualize without weakening identity scope.

Other measured roots: Setup 6.33 s / 229 KB, CERI 3.44 s / 10 KB, Winner Operations 3.34 s / 8.5 KB. Dynamic averages: Setup ticker 1.42 s, Setup episode 1.25 s, Winner prediction 0.94 s.

## 13. TABLE AND VALUE VALIDATION RESULTS

| Surface | Exhaustive result |
|---|---|
| Run coverage | 209/209 rows, 2,508 cells, exact match to CSV/backend. |
| Column mapping | 123/123 rows, 738 cells; one display-format difference: export prefixes negative text with `'` for spreadsheet-injection safety, GUI shows the numeric text. No semantic difference. |
| IB fetch 16 | 422/422 rows, 4,642 cells, exact match. |
| Decision cockpit | 209/209 identities; 2,926 attributes. 145 rows exact; 64 confirmed semantic technical-input mismatches (SL-GUI-003). |
| Winner Run 30 | 28/28 rows; 196 semantic comparisons exact; 28 presentation-only `None` leaks (SL-GUI-016). |
| Setup current | All 29 cursor pages; 2,874/2,874 row identities/order exact; stable total 2,874. |
| Setup Run 30 | All 26 pages; 1,290/1,290 identities/order exact; stable total 1,290. |
| CERI changes | 7 pages; 305/305 rows. |
| Pipeline steps | 13/13 rows match persisted status/timestamps; aggregate progress defects SL-GUI-008/009 remain. |
| CERI Operations | Provider/freshness/checkpoint tables rendered; stale list incompletely exposed (SL-GUI-012). |

Nullable/boolean/enum checks found no `NaN` or `undefined`. Raw JSON legitimately contains `null`; the semantic display leak is limited to Winner's 28 `None` cells. Timestamp offsets agree among DB/API/browser for the audit date; templates mostly show offset-bearing ISO values without conversion ambiguity.

## 14. BROKEN / INCORRECT LINKS AND ACTIONS

- Actual links: 160 unique same-origin targets, all HTTP 200; dynamic identity children: 880/880 HTTP 200 with correct identity.
- Wrong-owner/missing checks: five correct 404s (two pipeline owner mismatches, missing run ticker, Setup episode, Winner prediction); CERI missing ticker incorrectly returns 200 (SL-GUI-013).
- Safe controls: filters, sort, search, expand/collapse, clipboard, reload, back/forward passed.
- Mutating controls were not executed. Setup enforcement is unsafe (SL-GUI-004); Winner lacks CSRF (SL-GUI-005); Setup Replay transport is incorrect (SL-GUI-014).
- Disabled Run detail recalculation controls remained disabled. Enabled pipeline/IB actions were inspected for form target, confirmation, identity, and guards but not submitted.

## 15. FINDINGS REGISTER

| Severity | Finding | Short title |
|---|---|---|
| P1 | SL-GUI-001 | Default run masks Pipeline 30 `PARTIAL` |
| P1 | SL-GUI-002 | Captured/excluded CERI shown as no capture |
| P1 | SL-GUI-003 | 64 non-input technical scores labeled as decision inputs |
| P1 | SL-GUI-004 | Setup admin policy metadata not enforced |
| P1 | SL-GUI-005 | Winner admin actions lack CSRF |
| P2 | SL-GUI-006 | Global active Setup count contaminates run card |
| P2 | SL-GUI-007 | 305 vs 1,290 Setup change definition mismatch |
| P2 | SL-GUI-008 | Terminal job stage labeled active |
| P2 | SL-GUI-009 | CERI progress 909/618 |
| P2 | SL-GUI-010 | False reconnect warning visible |
| P2 | SL-GUI-011 | 68 failed/unavailable Winner obligations hidden |
| P2 | SL-GUI-012 | CERI stale list silently limited to 50/52,547 |
| P2 | SL-GUI-013 | Missing CERI ticker returns 200 |
| P2 | SL-GUI-014 | Setup Replay form/endpoint parameter mismatch |
| P2 | SL-GUI-015 | Materially slow run/detail navigation |
| P3 | SL-GUI-016 | 28 literal `None` Winner cells |

No P0 defect was confirmed. The browser-private-network feasibility of cross-origin local-admin POSTs can vary by browser policy, but the missing server-side CSRF enforcement is confirmed and does not depend on successfully exploiting it during this audit.

## 16. ROOT-CAUSE MAP

| Root cause | Downstream findings |
|---|---|
| UI chooses/labels the wrong state domain | SL-GUI-001, 006, 007, 008 |
| Eligibility filtering erases exclusion provenance | SL-GUI-002, SL-GUI-013 |
| Fallback value substitutes for decision-time lineage | SL-GUI-003 |
| Security classification flags are not centrally enforced | SL-GUI-004; contributes to inconsistent protection in 005 |
| Frontend/backend action contract mismatch | SL-GUI-014 |
| Independent aggregation of incompatible progress fields | SL-GUI-009 |
| CSS display rule overrides HTML hidden state | SL-GUI-010 |
| Template whitelists/limits/null rendering | SL-GUI-011, 012, 016 |
| Eager large server-rendered payloads / unresolved query cost | SL-GUI-015 |

## 17. RECOMMENDED REMEDIATION ORDER

1. Enforce local-admin and CSRF policies centrally and add route-level defense/testing (SL-GUI-004/005).
2. Restore decision/evidence truth: canonical pipeline visibility, captured-vs-excluded CERI, and exact combined technical lineage (001/002/003).
3. Correct run-scoped Setup counts/definitions and add cross-screen contract tests (006/007).
4. Repair progress semantics and hidden-state CSS (008/009/010).
5. Expose all Winner obligation states and paginated CERI stale records; preserve CERI HTTP error status (011/012/013).
6. Fix Replay transport/result UX and nullable Winner formatting (014/016).
7. Profile and reduce Run/detail payload/query cost while retaining point-in-time/run ownership (015).

No remediation was implemented.

## 18. FINAL CERTIFICATION MATRIX

| Area | Certification | Basis |
|---|---|---|
| Root surface coverage | YES | All four roots rendered, inventoried, screenshot, network-captured, and source traced. |
| Child surface coverage | YES | All 895 unique children covered; every dynamic identity resolved; distinct layouts visually inspected. |
| Link correctness | NO | Reachable links resolve, but missing CERI ticker HTTP semantics are incorrect. |
| Action correctness/safety | NO | Setup authority and Winner CSRF defects; Replay contract mismatch. |
| Table/value truth | NO | 64 decision-lineage mismatches plus count/progress/completeness defects. |
| Run 30 status truth | NO | Upload completion masks partial pipeline. |
| Run isolation | NO | Confirmed global active-episode aggregate on run page. |
| CERI authority presentation | NO | Backend exclusion is correct; GUI erases captured/excluded state. |
| Setup authority presentation | NO | Counts and run scope are inconsistent; admin guard defective. |
| Winner authority presentation | NO | Run ownership passes, but failure obligations are hidden and actions lack CSRF. |
| Cross-screen consistency | NO | Multiple confirmed disagreements. |
| Database mutation safety of audit | YES | Read-only SQL; no mutating endpoint invoked; Run 30 preserved. |

Evidence package: `output/playwright/gui_forensic/` contains the element CSV, root/child screenshots, browser/API/DB extracts, exhaustive crawls, value comparisons, missing-target tests, diagnostic scripts, and SHA-256 manifest (42 artifacts before this report).

GUI FORENSIC REVIEW COMPLETE: YES

ROOT PAGES FULLY COVERED: YES

CHILD PAGES FULLY COVERED: YES

LINKS CERTIFIED: NO

BUTTONS/ACTIONS CERTIFIED: NO

TABLE VALUES CERTIFIED: NO

RUN 30 GUI TRUTHFUL: NO

RUN ISOLATION CERTIFIED: NO

CERI GUI AUTHORITY CERTIFIED: NO

SETUP LIFECYCLE GUI AUTHORITY CERTIFIED: NO

WINNER GUI AUTHORITY CERTIFIED: NO

CROSS-SCREEN CONSISTENCY CERTIFIED: NO

P0 FINDINGS: 0

P1 FINDINGS: 5

P2 FINDINGS: 10

P3 FINDINGS: 1

SAFE TO REMEDIATE FINDINGS: YES
