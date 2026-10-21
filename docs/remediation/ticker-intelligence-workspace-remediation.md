# Ticker Intelligence Workspace — Remediation

21 October 2026 · `C:\Users\Ivica\Documents\SwingLens`

## A. Overall verdict

**REMEDIATION PASS.** F01–F06 are corrected and have regression evidence. The original failures were reproduced before implementation: seven new migrated-PostgreSQL cases failed, and the new lifecycle browser case failed against the old rendering. They now pass. The final main regression suites returned **279 passed, zero failures/skips**; a final guidance-focused rerun returned29 passed without adding those repeated cases to the total.

This closes the six findings in `docs/review/ticker-intelligence-workspace-review.md`. It does **not** replace that historical review, certify the whole application, activate port8000, or resolve the separately recorded certification coverage limits.

Branch remains `codex/ceri-authority-remediation`, HEAD `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. No commit or push was made. Unrelated working-tree changes were preserved.

## B. Findings closure

| Finding | Severity | Status | Closure evidence |
| --- | --- | --- | --- |
| F01 | P1 | PASS | Migrated DB: quarantined/retired/failed/cancelled/blocked owners and uncertified snapshots cannot supply current activity; eligible fallback, legitimate historical changes, UNREAD/ACKNOWLEDGED/ACTIVE alerts and safe partial outcomes remain supported. Audit access retained. |
| F02 | P1 | PASS | Pinned lineage, context/config/cutoff/known-at checks; missing lineage explicit;35 verified pins show a30-row page with a truncation notice. Real EOG5136 returns exactly its12 ledger pins, all context83. |
| F03 | P1 | PASS | Migrated competing versions now select89999/81%, agreeing with the canonical API, including its77%–85% interval. Outcome, training-time and creation-time distractors cannot replace it; no latest rescore substitution. |
| F04 | P2 | PASS | Chromium/Firefox assert signal name, old/new values, delta, crossing and working evidence link; transition/alert fields remain correct. Real EOG17717 renders8.2971→6.8252, delta−1.4719. |
| F05 | P2 | PASS | Accepted null-session guidance is AVAILABLE in both workspace and canonical page. Explicit session, exact cutoff, future/rejected/quarantined-source guidance, supersession and expiry are tested. Expiry cannot resurrect superseded guidance. |
| F06 | P2 | PASS | SQL regression proves unused change/alert totals and pair aggregates absent; full list contracts retain their totals/context. Actual EOG detail20→17 reads; measured timings and response sizes below. |

Diagnostic evidence is retained in `output/ticker-remediation/before/` and `after/`. The original forensic artifacts were retained unchanged.

## C. Root causes and corrections

### F01 — one current-presentation authority scope

The old headline added owner quarantine/certification checks that its change/alert readers did not apply. `current_snapshot_authority_predicate()` in `app/services/ceri/evidence_eligibility.py:96` now composes the existing disposition, unsuccessful-owner and safe-partial policy with the current-presentation checks: quarantined/retired upload/context owner exclusion, matching CERI evidence kind/run/ticker, and purge invalidation exclusion.

The workspace headline and the CERI service's new bounded `recent_changes()`/`recent_alerts()` use that scope. Activity classification uses the **same selected headline snapshot**. Each referenced comparison snapshot is independently checked; an eligible historical input is allowed, and an unrelated event is not rejected merely because another record belongs to a rejected run. Current alerts also retain valid persisted classifications, existing comparison/legacy-alert semantics and native UNREAD/ACKNOWLEDGED states. Invalidated/invalid legacy records cannot become actionable current alerts.

Historical and audit visibility remains in the existing full `changes()`/`alerts()` contracts. Those methods are not replaced with workspace-specific filtering or missing totals. The new current-projection mode does not rewrite alert status, dispositions or evidence.

Consumers checked before shared changes: CERI capture, change rebuild, controlled replay, evidence population/containment, run detail, transition handoff, IB intelligence journal, existing CERI routes and query/alert tests. The base decision-use eligibility helper remains unchanged; the stricter serving scope is explicit. No scoring or writer policy was modified.

Regression: `test_f01_rejected_snapshot_cannot_supply_current_activity_and_audit_is_retained` in the new migrated integration test file. The retained original probe now falls back to opportunity5.00 **without** change9100/alert9300 in the workspace detail. Eligible fallback activity remains visible, and explicit audit reads retain the underlying event.

### F02 — revisions follow snapshot-owned addresses

The old query matched company/session/version and capped the resulting mixed generations at30. `revision_feature_ids()` and `pinned_revision_details()` in `app/services/ceri/query_service.py` now read the same opportunity-ledger feature addresses used by the existing lineage audit. The audit reuses the extracted address helper; no revision is recalculated.

Returned rows must match pinned ID, company, source session, calculation context, configuration version/hash, calculation version and calculation cutoff. Known-at values after that cutoff and purge-invalidated rows are excluded. Missing/unverifiable pins produce explicit unavailable/partial provenance; no same-date substitute is borrowed. A missing known-at value is not invented: exact pin/context/cutoff matching remains required, and the UI displays the missing value truthfully.

The source-scoped verified count is displayed and determines the explicit truncation notice. It does not count unrelated company history. The template now shows metric, fiscal period, period slot, window, confidence, cutoff, known-at, context and revision evidence link.

Three migrated regressions cover competing contexts/future-known or incompatible pins, absent/missing lineage, and35 valid pins plus35 unrelated earlier-ID rows. The latter returns30 selected rows, reports35 verified and exposes a continuation/history notice.

Read-only actual-data proof: snapshot5136 still has context83 and cutoff `2026-10-20T03:05:25.356265+02:00`. Its opportunity ledger pins **12 unique revision IDs**, not every one of the24 generated context83 features. The returned set equals those12 pins exactly; none belongs to context81. Raw persisted rows were independently compared to the returned set and their context/config/cutoff/known-at fields. See `after/real-verification.json`.

### F03 — canonical Winner scope and ordering, batched

The old adapter ordered all serving estimates by ascending ID and took the first. `estimate_read_statement()` and `estimate_selection_order()` in `app/services/winner_probability/api_service.py` now supply the shared serving/kind/time scope and descending training-cutoff/creation/ID ordering. The existing single-estimate API uses them; its LATEST_RESCORE generation rules remain intact.

The workspace applies the same decision-time scope in a partitioned SQL selection by prediction/outcome, returning one selected version per pair. It keeps the selected run, exact combined-evidence pin, historical outcome FK and serving lifecycle checks. The UI explicitly identifies the selected published decision-time estimate; unlabelled alternative versions are no longer printed as interchangeable estimates.

Consumers checked: canonical prediction/run APIs, the existing `_selected_estimate`/`_estimate_by_kind` callers and estimate-lifecycle tests. The shared serving predicate remains the authoritative predicate.

Migrated regression `test_f03_selected_estimates_equal_canonical_by_outcome_and_original_view` compares actual selected IDs/probabilities to the canonical API. It includes two valid serving versions, a larger-ID row with older training time, a larger-ID row with older creation time, another outcome, a current rescore, and the original candidate/superseded distractors. Selected89999 has probability0.81 and bounds0.77/0.85; historical Run6 remains0.41. Existing insufficient/no-zero and cross-run tests also pass.

### F04 — render each timeline DTO's actual fields

The shared template previously treated signal DTOs as state transitions. `app/templates/partials/_ticker_intelligence.html` now branches on signal/lifecycle/alert kind. Signals expose their actual key, previous/current values, unrounded numeric delta, threshold/crossing and source identifiers. Transitions keep from/to/actionability; alerts use alert-specific state/actionability/confidence/blockers/status.

The adapter batches source-snapshot evidence addresses for signal rows. Where an existing SETUP evidence address is available, it links to the supported read endpoint; otherwise the source identifier remains visible without an invented episode/link. No lifecycle evaluator or existing serializer was modified.

The new browser test `test_signal_transition_and_alert_render_their_own_fields` verifies all these fields, a source link returning200, one transition and one alert in both engines. Read-only actual EOG event17717 renders `technical_score`,8.2971,6.8252,−1.4719,`crossing_7`,EXIT and setup evidence48543. Screenshots: `output/playwright/remediation-eog-signal17717.png` and `remediation-eog-pinned-revision.png`; both were visually inspected.

### F05 — shared accepted-guidance selection at the exact cutoff

The old workspace's SQL excluded NULL effective sessions. Both the workspace and established guidance summary now use `accepted_guidance_at_cutoff()`. It supports optional sessions while checking effective-at, acceptance time when recorded, source observation eligibility, accepted flag, expiry and visible accepted replacements. Future replacements cannot hide guidance that was valid at an earlier cutoff; an expired replacement cannot resurrect its superseded predecessor.

The full reader retains its summary/status/selected/rejected shape, with additional selected-source provenance. Timestamp-aware callers pass the actual source cutoff rather than discarding its time. The existing batched CERI read context also loads guidance source addresses so the same selector works in canonical page composition, not just direct workspace SQL. Its earnings source-membership selection is preserved.

Consumers checked: CERI ticker detail, dashboard/snapshot DTO composition, batched read contexts, guidance normalization and point-in-time/query tests. No guidance acceptance writer or scoring calculation changed.

Migrated regression `test_f05_guidance_matches_established_reader_with_visibility_and_supersession` proves the original null-session88881 is selected and that both the workspace and canonical page agree. It adds explicit-session88882, future timestamp/session candidates, rejected and quarantined sources, supersession, an earlier source cutoff and expiration. A final focused rerun of this integration case plus all28 CERI query cases returned29 passed.

### F06 — omit work the panel does not use

The recent CERI projection reuses the existing SQL predicates and serializers while skipping full matching-history totals, run-pair aggregation and excluded-history summary. The full CERI list/history methods keep their original totals, pagination and comparison metadata. The current headline's fixed projection also avoids repeatedly finding a company-wide latest snapshot across historical change references.

`test_f06_recent_detail_skips_aggregates_and_preserves_full_contract` records executed SQL and rejects change/alert COUNT and pair-count work on the detail path, while asserting that the full readers still return their total and comparison context. No cache, index, materialized table, schema or framework was added.

## D. Performance measurements

Same actual EOG source5136/run45, local PostgreSQL, fresh explicit READ ONLY sessions and SQL event instrumentation. Both pre-fix and post-fix sampling included local test activity and cache/warmup differences; these are measured samples, not an SLA or universal speedup claim.

| Direct detail composition | SQL reads | Elapsed samples, ms | Serialized composition bytes |
| --- | ---: | --- | ---: |
| Before, reproduced during this task | 20 | 9310.99;4523.12 | 177563 |
| After, final three samples | 17 | 708.26;254.07;252.86 | 113386 |

Before, the two samples spent3186/1741ms in change COUNT and1893/1185ms in the run-pair aggregate; the change page query took2792/1037ms. Those unused aggregate queries are absent afterward. Final change selection was approximately28/13/14ms; the slowest SQL in each final sample was35/14/14ms. The remaining verified-pin COUNT is source-ID scoped, used by the UI and covered by F02 overflow checks.

Final HTTP samples: **368.16,363.83,375.81ms**, all200, **62707bytes** each. The retained pre-fix forensic HTTP response was61289bytes at2401.39ms; it was measured in the earlier review, not under identical load during this task. HTTP output grew slightly because revision identity/provenance is now rendered. The composition-byte figures above are internal serialized DTO size, not HTTP transfer size.

Correctness accompanies the speed improvement: actual returned revision IDs equal the ledger pins; recent changes739/740 are explicitly HISTORICAL relative to the selected headline snapshot; no eligible current alerts were returned for this EOG source. The migrated fixtures separately prove legitimate current alerts remain visible and rejected ones do not.

Evidence: `before/service-measurements.json`, `after/measurements.json`, `after/real-verification.json`, `after/http-ceri.json`. All are under `output/ticker-remediation/`.

The actual read-only run-context check also retained Run45 combined46423, prediction/estimate947 and five ranking profiles with best rank2; Run44 retained combined45121, prediction/estimate763 and five profiles with best rank3. Both Winner estimates remain explicitly insufficient, with no substituted probability or cross-run source pin.

## E. Regression commands and results

Executed from the repository root with the existing virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_ticker_intelligence_remediation_postgresql.py tests/test_ticker_intelligence.py tests/test_ticker_workspace.py tests/test_ticker_search.py tests/test_ticker_chart_panel_routes.py tests/test_chart_data_service.py tests/test_score_card_view_service.py tests/test_dashboard_upload.py tests/test_pipeline_progress_route.py tests/test_gui_phase1.py tests/test_pipeline_gui_semantics.py tests/test_route_security.py -q --tb=short
# 102 passed; 177.34s

.venv\Scripts\python.exe -m pytest tests/ceri/test_ceri_query_service.py tests/setup_lifecycle/test_query_service.py tests/test_ranking_profile_service.py tests/test_ranking_profile_routes.py tests/winner_probability/test_routes_api.py tests/winner_probability/test_estimate_lifecycle.py tests/integration/test_ceri_evidence_quarantine_postgresql.py tests/ceri/test_ceri_alert_service.py tests/ceri/test_ceri_changes_alerts_forensic.py tests/ceri/test_point_in_time_query.py tests/ceri/test_guidance_normalizer.py -q --tb=short
# 120 passed; 65.32s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py tests/e2e/test_browser_smoke.py --browser chromium -q --tb=short
# 38 passed; 89.81s

.venv\Scripts\python.exe -m pytest tests/e2e/test_ticker_intelligence_browser.py tests/e2e/test_ticker_workspace_browser.py tests/e2e/test_ticker_search_dock.py --browser firefox -q --tb=short
# 19 passed; 98.08s

.venv\Scripts\python.exe -m pytest tests/integration/test_ticker_intelligence_remediation_postgresql.py::test_f05_guidance_matches_established_reader_with_visibility_and_supersession tests/ceri/test_ceri_query_service.py -q --tb=short
# Final guidance refinement:29 passed;49.65s. Repeated cases, not added to279.
```

The seven new integration cases run the existing guarded Alembic migrations on disposable PostgreSQL and assert that CERI/Winner foreign keys remain present. Existing tests were not weakened to bypass authority or publication rules. The new browser fixture contains real persisted signal values and a no-episode signal, exercising the previously broken DTO path.

The original migrated counterexample script was copied into this task's diagnostic directory and run before and after, preserving the original forensic artifacts:

```powershell
.venv\Scripts\python.exe output/ticker-remediation/probes.py adversarial --migrated
.venv\Scripts\python.exe output/ticker-remediation/probes_after.py adversarial --migrated
```

After: quarantined activity IDs absent; Winner summary81% matches canonical89999; accepted null-session guidance AVAILABLE; unrelated future revision absent. The stronger new tests additionally cover pinned-but-incompatible/future features rather than relying only on the original unpinned counterexample.

Ruff passes for all changed Python files; scoped `git diff --check` passes. Existing Starlette/httpx and Alembic path-separator deprecation warnings remain. Initial newly written test failures after implementation were two template assertions before the template update and a relative-URL error in the new browser test; these were corrected, with final successful runs recorded. No unresolved test failure remains.

## F. Runtime and database preservation

Normal `swinglens.ps1 start` was **not** invoked. Its worker/recovery effects remain outside this task. No authoritative web, worker or supervisor process was started/stopped, and no pipeline, provider, recalculation, publication, order or historical-repair action was issued.

Actual-data inspection used explicitly READ ONLY transactions. The manual browser used a temporary loopback server on8779, PID54548, with worker/automatic processing disabled for that process, lifespan off and non-GET/HEAD rejected. Playwright session `ticker-remediation` initially used browser PID43404. Both browser and temporary server are stopped. Ports8000 and8779 are free; no authoritative web/worker/supervisor was found at the final check. Disposable test processes/databases were cleaned up through their fixtures/finally blocks.

Supporting preservation snapshots show unchanged pipeline/job row-content hashes and unchanged inspected evidence ID sets/counts. They supplement the enforced read-only boundary; they are not a forensic image of every persisted artifact.

The1292-file source baseline shows only these intended existing-file changes:

- `app/services/ceri/constants.py`: shared purge marker location, retaining its existing query-service import compatibility.
- `app/services/ceri/evidence_eligibility.py`: shared current-presentation predicate.
- `app/services/ceri/query_service.py`: bounded recent reads, pinned revision projection, shared guidance selection/read context.
- `app/services/winner_probability/api_service.py`: shared selection scope/order.
- `app/services/ticker_intelligence_service.py`: use those helpers and batch signal evidence addresses.
- `app/templates/partials/_ticker_intelligence.html`: required detail rendering/provenance.
- `tests/e2e/test_ticker_intelligence_browser.py`: signal fixture/assertions.

One source test file was added: `tests/integration/test_ticker_intelligence_remediation_postgresql.py`. This report and task-local diagnostics/screenshots are the other intentional artifacts. D1/D2 code, scoring algorithms, pipeline/worker code, schemas/migrations and unrelated changes were preserved. See `output/ticker-remediation/preservation.json`.

## G. Remaining unverified certification items

These remain coverage requirements, not silently resolved findings:

- More than100 persisted ranking profiles and ranking truncation behavior.
- Simultaneous source publication during active viewing and arbitrary cross-request authority changes.
- A representative model-backed Winner metadata display.
- Complete positive eligible catalyst/earnings examples and their temporal boundaries.
- Large-universe/concurrent-load/peak-memory behavior.
- Actual persisted ambiguous-identity browser behavior; seeded identity tests remain passing.
- Supported normal-stack activation after evaluating worker/recovery effects.

No historical data was manufactured or repaired to turn these items into PASS.

## H. Final certification readiness

**Ready for the separate independent end-to-end certification task.** All six required fixes have closure evidence, relevant prior behavior remains passing and no production evidence was mutated.

Operational activation and final certification remain separate. Stop after this remediation; do not start the normal stack, expand scope, commit or push without further authorization.
