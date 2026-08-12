# SLSE Implementation Audit and Defect Register

## Current authoritative status (2026-08-12)

| Item | Original finding | First-pass disposition | Second-pass disposition | Current authoritative status |
|---|---|---|---|---|
| DEF-030 | Adapter inputs were not traced to production evidence | Not registered | Not registered | **FIXED** — executable catalog and AST audit cover every adapter input and reject obsolete magic keys. |
| DEF-031 / FR-031 | Prior canonical history did not reach adapters | PARTIAL | Typed history reached adapters but semantic use was overclaimed | **FIXED / PASS** — temporal rules consume bounded canonical history in BREAKOUT, PULLBACK, VCP, CONTINUATION, and GENERIC; paired-history tests prove material effect. |
| DEF-032 | Freshness/lineage and signal agreement were not decomposed | Not registered | Top-level confidence blend fixed | **FIXED** — exact deterministic subcomponents and contradictory-evidence tests are implemented. |
| DEF-033 | Compound actionability relied on branch order | Not registered | Reduced-posture case fixed alone | **FIXED** — explicit precedence and full mandatory compound truth table pass. |
| DEF-034 | Dated audit prose contained stale status claims | Addenda only | Still ambiguous | **FIXED** — the current table governs; dated text below remains forensic history. |
| Overall release | FAIL | FAIL | FAIL | **FAIL** pending every remaining mandatory closure gate. No rebuild or activation is authorized. |

Behavior identity: engine `slse-1.2.0`, config `2026-08-12`, schema `slse-snapshot-1.0.0`. Current local focused result: 206 passed; Ruff passed.

## Audit method

The review followed the required bottom-up order: migrations/schema; source loading; snapshot construction and coverage; point-in-time rules; canonicalization; registry and change detector; velocity; adapters/state machine/episodes; confidence/actionability; alert rules and persistence; query/API/export; Jinja/HTMX; tests. The SRS/SDD were read in full before code inspection. Existing tests initially reported `151 passed`; this baseline is not treated as compliance proof.

## Defects

### SLSE-DEF-001 — False missing-required close and capped coverage

- Requirement: FR-005, FR-030, FR-036, AC-08.
- Expected: a populated `close_price` counts toward required-feature coverage and creates no missing-required warning.
- Actual: `_promoted_fields()` creates `close_price`, but `_source_values()` omits it while `REQUIRED_FEATURE_SOURCES` requires it. Coverage is capped at 0.75 and every ticker gets `MISSING_REQUIRED_CLOSE_PRICE`.
- Root cause: split promoted/source maps without a single canonical field registry.
- Affected layers: snapshot, confidence, actionability, alerts, query/UI, historical data.
- Affected persisted data: snapshots and all dependent episodes/events/alerts produced by the faulty version.
- Severity: CRITICAL.
- Fix: insert canonical `close_price` into source values, persist it in `signals_json`, and add fully populated/missing-close regression tests.
- Historical remediation: rebuild dev/QA SLSE-derived rows; retained history requires new evaluation/config version and superseding events.

### SLSE-DEF-002 — Confidence component uses all signals as required coverage

- Requirement: FR-036; SDD 10.2.
- Expected: the 30% component is the snapshot’s required-feature coverage.
- Actual: `_coverage()` counts every registered signal, including optional market/sector/risk/diagnostic values.
- Root cause: `NormalizedSnapshot` omits the persisted required coverage field.
- Affected layers: confidence, actionability, lifecycle, alerts.
- Affected persisted data: confidence/actionability on snapshots, episodes, events, alerts.
- Severity: HIGH.
- Fix: add explicit coverage to the normalized DTO and score the component from it.
- Regression tests: exact component and final-score tests for complete required evidence with missing optional context.
- Historical remediation: replay from corrected snapshots/config version.

### SLSE-DEF-003 — Initial or LOW_CONFIDENCE to BLOCKED can emit GATE_BLOCKED

- Requirement: FR-050; SRS 6.6 GATE_BLOCKED truth table.
- Expected: only `ACTIONABLE -> BLOCKED` and `WATCH_ONLY -> BLOCKED`.
- Actual: `_became_blocked()` accepts every prior value except `BLOCKED`, including `None` and `LOW_CONFIDENCE`.
- Root cause: inequality used instead of explicit allowed predecessor set.
- Affected layers: alert evaluation/persistence/UI.
- Affected persisted data: false `GATE_BLOCKED` alerts.
- Severity: HIGH.
- Fix: explicit predecessor set.
- Regression tests: `None`, `BLOCKED`, `LOW_CONFIDENCE`, initial blocked episode, and the two positive transitions.
- Historical remediation: identify and supersede/rebuild false alerts.

### SLSE-DEF-004 — Signal-change confidence defaults to fabricated 100

- Requirement: FR-036, FR-051; NFR-005.
- Expected: use real current snapshot/lifecycle confidence; absence is not perfect evidence.
- Actual: `_source_confidence()` defaults to 100 on missing or invalid evidence.
- Root cause: permissive fallback added to satisfy alert minimum confidence.
- Affected layers: change event, alert rule, alert persistence/UI.
- Affected persisted data: acceleration alerts created with unsupported confidence.
- Severity: HIGH.
- Fix: persist current snapshot confidence in change evidence and use a non-fabricating nullable/zero fallback.
- Regression tests: missing confidence never passes a 70 floor; actual 72 passes and is exposed.
- Historical remediation: rebuild signal-derived alerts.

### SLSE-DEF-005 — Acceleration rules do not implement configured window/crossing semantics

- Requirement: FR-050; SRS 6.6.
- Expected: configured 3-session rise by configured amount plus tracking-threshold crossing; sector rank uses positive normalized improvement and sufficient sector confidence.
- Actual: any favorable technical-score or sector-rank material event can match; setup-score acceleration, exact window/amount/tracking crossing, and sector confidence are not enforced.
- Root cause: alert matcher only checks signal key and positive normalized delta.
- Affected layers: config, change evidence, alert evaluation.
- Affected persisted data: false/missing NOTABLE alerts.
- Severity: HIGH.
- Fix: explicit rule filters (`signal_keys`, `velocity_window`, minimum delta, tracking threshold, sector confidence) and truth-table tests.
- Historical remediation: rebuild signal-derived alerts.

### SLSE-DEF-006 — Raw rank delta has the opposite business sign

- Requirement: FR-020, FR-023; SDD 7.1.
- Expected: `old_rank - new_rank`, positive means improvement.
- Actual: `rank_delta` stores `new_rank - old_rank`; only `normalized_delta` is correct.
- Root cause: duplicated delta logic.
- Affected layers: event persistence, DTO, filters/sorts/exports.
- Affected persisted data: signal change rank deltas.
- Severity: HIGH.
- Fix: make stored rank delta equal normalized rank improvement.
- Regression tests: 9 -> 5 equals +4 everywhere.
- Historical remediation: rebuild/supersede affected change events.

### SLSE-DEF-007 — Alert DTO/UI hides Alert Type and source semantics

- Requirement: FR-050, FR-055, NFR-005.
- Expected: explicit `alert_type`, `severity`, `review_status`, `source_type`, lifecycle/actionability/confidence/blockers/source IDs.
- Actual: payload exposes rule DB ID, ambiguous `status`, severity and opaque evidence only; UI has no Alert Type column.
- Root cause: query does not join `signal_alert_rules` or source events.
- Affected layers: query/API/template/export.
- Affected persisted data: no schema loss, presentation contract loss.
- Severity: HIGH.
- Fix: joined stable Alert DTO and explicit columns/filters.
- Regression tests: DTO/UI/CSV/JSON parity.
- Historical remediation: none after code fix, except false alerts from other defects.

### SLSE-DEF-008 — Canonical NOTABLE severity is absent from UI selector

- Requirement: FR-024, FR-050.
- Expected: INFO, NOTABLE, ACTIONABLE, RISK.
- Actual: selector offers ACTIONABLE, RISK, WARNING, INFO.
- Root cause: UI-local enum drift.
- Affected layers: template/filter behavior.
- Severity: HIGH.
- Fix: source filter options from canonical enum and render NOTABLE.
- Regression tests: template contains NOTABLE and no WARNING.

### SLSE-DEF-009 — Summary counts are page-local

- Requirement: FR-060, FR-067.
- Expected: full filtered-scope counts, independent of visible page.
- Actual: route template helpers count `payload.items` only.
- Root cause: aggregate semantics implemented in presentation code.
- Affected layers: query/API/page.
- Severity: HIGH.
- Fix: aggregate counts in query service before pagination and consume `payload.summary`.
- Regression tests: > page size, filter + pagination, review-status mutation.

### SLSE-DEF-010 — Same-date alert and transition ordering is semantically wrong

- Requirement: FR-062.
- Expected: deterministic priority with severity/business transition ranking and ID tie-break.
- Actual: same-date alerts sort by descending ID; severity sort is lexicographic; transition priority maps to severity text.
- Root cause: no explicit CASE priority.
- Affected layers: query/UI/export order.
- Severity: MEDIUM.
- Fix: CASE expressions for RISK/ACTIONABLE/NOTABLE/INFO and lifecycle transition precedence, then date/ID tie-breakers.
- Regression tests: mixed same-date severities/types.

### SLSE-DEF-011 — Market Changes reads only lifecycle events

- Requirement: FR-020–028, FR-060–065.
- Expected: combined material signal changes and lifecycle transitions.
- Actual: `changes()` selects only `SetupLifecycleEvent`.
- Root cause: dashboard implemented as a lifecycle-event list.
- Affected layers: repository/query/API/template/export.
- Severity: CRITICAL.
- Fix: union/merge both canonical event streams under a stable DTO with explicit `source_type` and current/previous values.
- Regression tests: one lifecycle-only, one signal-only, and one same-snapshot pair all appear with correct total.
- Historical remediation: none for valid rows; UI visibility changes.

### SLSE-DEF-012 — Alert source link uses lifecycle-event ID as episode ID

- Requirement: AC-10, NFR-005.
- Expected: lifecycle source links resolve the event or its actual episode; signal source links resolve the change event.
- Actual: `/api/setup-lifecycle/episodes/{alert.lifecycle_event_id}`.
- Root cause: ID domains conflated in template.
- Affected layers: DTO/template.
- Severity: HIGH.
- Fix: expose `episode_id`, `lifecycle_event_id`, `signal_change_event_id`, and a canonical `source_url`.
- Regression tests: route ID/link target assertions.

### SLSE-DEF-013 — CSV/export contracts are incomplete and lose filters

- Requirement: FR-053, FR-066.
- Expected: filtered GUI/API/JSON/CSV semantic parity.
- Actual: alerts export six fields; changes export ten lifecycle-only fields; export routes ignore active filters and return at most 500 first-page rows.
- Root cause: legacy narrow column tuples and unparameterized routes.
- Affected layers: routes/export.
- Severity: HIGH.
- Fix: versioned expanded schemas, pass filter/sort parameters, and assert round-trip parity.

### SLSE-DEF-014 — Market Changes DTO is not a stable business contract

- Requirement: FR-023, FR-025, FR-060–065, NFR-005.
- Expected: explicit identity, dates, previous/current state/value, deltas/velocities, quality, blockers, reasons and lineage.
- Actual: lifecycle-event payload plus arbitrary evidence; template guesses score/velocity/sector rank from JSON.
- Root cause: internal event model passed directly to presentation.
- Affected layers: query/API/template/export.
- Severity: HIGH.
- Fix: dedicated DTO builder backed by snapshot/source events.

### SLSE-DEF-015 — Run-level context cutoff is the oldest ticker date

- Requirement: FR-001, point-in-time rules.
- Expected: context is the latest available not newer than each ticker’s own as-of date.
- Actual: one market/sector snapshot is selected using the minimum ticker cutoff and applied to all tickers.
- Root cause: run-wide context loading before ticker-specific cutoff.
- Affected layers: source loader/snapshot/confidence/actionability.
- Severity: MEDIUM.
- Fix: load bounded context candidates and select per ticker, or prove run dates are uniform.
- Historical remediation: replay runs with mixed ticker as-of dates.

### SLSE-DEF-016 — Alert/lifecycle truth tables are fragmented, not vertical

- Requirement: AC-03, AC-06 and Phase 5 mission.
- Expected: exact sequence tests from snapshot through event/alert, including negative boundaries.
- Actual: unit tests cover individual methods but omit several initial/unchanged/blocked/min-confidence combinations.
- Severity: HIGH.
- Fix: table-driven truth tests plus persistence retry cases.

### SLSE-DEF-017 — Prior canonical history is not supplied to family evaluation

- Requirement: FR-031.
- Expected: current snapshot, previous episode state, and prior canonical snapshots.
- Actual: episode service supplies state/persistence counters but not prior snapshot DTOs.
- Severity: MEDIUM.
- Fix: extend lifecycle input/history and adapters where persistence/velocity evidence requires it.

### SLSE-DEF-018 — DATA_DEGRADED is inferred only from quality-label change

- Requirement: FR-028, FR-050.
- Expected: direct configured threshold crossings for required coverage and freshness, with no repeat.
- Actual: one `data_quality` enum event is the sole source.
- Severity: MEDIUM.
- Fix: register coverage/freshness signals or emit explicit data-quality changes with prior/current threshold evidence.

### SLSE-DEF-019 — Alert market restrictions are stored but ignored

- Requirement: FR-051.
- Expected: optional market/family restrictions affect matching.
- Actual: market restriction JSON is not evaluated.
- Severity: MEDIUM.
- Fix: enforce restrictions from source snapshot/evidence and test both allow/block cases.

### SLSE-DEF-020 — Required alert and dashboard filters are missing

- Requirement: FR-055, FR-061.
- Expected: date/state/type/source and the complete dashboard filter set.
- Actual: alerts only support ticker/status/severity; dashboard lacks selected date and several exact semantics.
- Severity: MEDIUM.
- Fix: extend filter DTO, validation, query, route, and UI options.

### SLSE-DEF-021 — “No Material Change” quick filter cannot match persisted event rows

- Requirement: SRS UI 8.1.
- Expected: candidates observed for the selected date with no material change are available as a muted view.
- Actual: filter compares lifecycle `event_type` to a value that is never emitted.
- Severity: MEDIUM.
- Fix: query canonical snapshots without material/lifecycle events for the selected date as explicit `NO_MATERIAL_CHANGE` DTO rows.

### SLSE-DEF-022 — Administrative evaluation scopes are incomplete at API/UI boundary

- Requirement: FR-070, FR-074.
- Expected: run, ticker, date range, all eligible, and independent retry.
- Actual: run evaluation and internal repair/replay services exist, but the full explicit operator contract is incomplete.
- Severity: LOW.

### SLSE-DEF-023 — Required PostgreSQL performance, accessibility, and real E2E proof is incomplete

- Requirement: NFR-002/003/009/012, AC-14.
- Expected: measured real PostgreSQL and Playwright evidence.
- Actual: synthetic/index-contract tests and broad existing certification do not assert cell-by-cell specification equivalence for SLSE.
- Severity: HIGH (release evidence).

### SLSE-DEF-024 — Record-status/version labels and timeline pagination are incomplete

- Requirement: FR-063, FR-068.
- Expected: explicit reconstructed, stale, low-coverage, noncanonical, superseded labels and incremental timeline pagination.
- Actual: payload/template expose only a subset and use fixed limits.
- Severity: LOW.

### SLSE-DEF-025 — Filter/sort names do not match business semantics

- Requirement: FR-061/062.
- Expected: transition means the actual from/to transition; velocity means an explicit configured window/value.
- Actual: `transition` filters `event_type`; velocity reads a generic evidence key that is usually a nested map.
- Severity: HIGH.

## Existing test classification

- Valid specification tests: canonical single-row selection, terminal immutability, family state reachability, hysteresis, hard failure, staleness-only LOW_CONFIDENCE, append-only retry keys, observation gaps, research-only boundary.
- Partial tests: source mapping, confidence, alert rules, route rendering, export schemas, query filters, performance.
- Implementation-locking tests: narrow CSV headers, lifecycle-only Market Changes payload assumptions, permissive score-acceleration match.
- Missing integration coverage: disposable-PostgreSQL SLSE constraints/joins, combined change stream aggregates, alert joined DTO, filter/export parity, cell-level GUI/API/DB/source proof.

## Post-repair disposition (2026-08-11)

This table supersedes the baseline “Actual” statements above; the original findings are retained as forensic evidence.

| Defect | Disposition | Verification |
|---|---|---|
| SLSE-DEF-001 | FIXED | populated/missing-close builder tests; required coverage 1.0 |
| SLSE-DEF-002 | FIXED | confidence reads persisted required coverage; optional-context regression |
| SLSE-DEF-003 | FIXED | table-driven predecessor truth table excludes `None`, `BLOCKED`, `LOW_CONFIDENCE` |
| SLSE-DEF-004 | FIXED | change evidence carries actual confidence; missing confidence is 0/not eligible |
| SLSE-DEF-005 | FIXED | configured 3-session velocity, amount, tracking crossing, rank improvement and sector confidence tests |
| SLSE-DEF-006 | FIXED | 9→5 persists/displays/exports +4 |
| SLSE-DEF-007 | FIXED | joined Alert DTO and explicit Alert Type/Source Type columns |
| SLSE-DEF-008 | FIXED | INFO/NOTABLE/ACTIONABLE/RISK end-to-end; WARNING rejected |
| SLSE-DEF-009 | FIXED | SQL full-filter aggregates before pagination; PostgreSQL test with limit 1 |
| SLSE-DEF-010 | FIXED | explicit severity, alert-type and transition priority with deterministic keys |
| SLSE-DEF-011 | FIXED | canonical material changes plus current lifecycle transitions; canonical-revision audit rows excluded |
| SLSE-DEF-012 | FIXED | episode, lifecycle-event and signal-change IDs remain separate; signal anchor added |
| SLSE-DEF-013 | FIXED | v2 CSV schemas and JSON use the same query filters/sorts and semantic DTOs |
| SLSE-DEF-014 | PARTIAL | stable row DTO now covers visible fields and lineage IDs; confidence components and every version/hash remain evidence/detail-only |
| SLSE-DEF-015 | FIXED | market/sector context selected per ticker cutoff from batched as-of-bounded candidates |
| SLSE-DEF-016 | PARTIAL | alert boundaries and PostgreSQL/UI vertical fixtures added; the full 25-scenario golden sequence corpus remains incomplete |
| SLSE-DEF-017 | OPEN | family adapters still receive current normalized snapshot plus counters, not a typed prior-snapshot window |
| SLSE-DEF-018 | FIXED | coverage and freshness registered as direct quality signals; threshold/no-repeat tests |
| SLSE-DEF-019 | FIXED | allowed/blocked market-regime restrictions enforced; missing restricted context suppresses |
| SLSE-DEF-020 | PARTIAL | date/type/source/state and core dashboard filters added; actionability/blocker/date-range Alert UI filters remain incomplete |
| SLSE-DEF-021 | FIXED | explicit selected-date canonical `SNAPSHOT_OBSERVATION` rows with no lifecycle/material event |
| SLSE-DEF-022 | OPEN | ticker/date/all-eligible operator capture/retry contract remains incomplete |
| SLSE-DEF-023 | PARTIAL | disposable PostgreSQL and two Playwright paths pass; 1,000-ticker/100k-row performance and dedicated accessibility audit remain open |
| SLSE-DEF-024 | OPEN | complete version labels and cursor-paginated ticker timeline remain incomplete |
| SLSE-DEF-025 | FIXED | transitions use from/to predicates; velocity is explicitly 3-session; quick filters have real event/bound semantics |

No open item is silently classified as compliant. Release readiness remains FAIL until the PARTIAL/OPEN items required by the SRS definition of done are closed.

## Second-pass forensic disposition (2026-08-11)

This section supersedes any earlier statement that FR-031, FR-036, actionability, or freshness was fully compliant.

### SLSE-DEF-026 - Undocumented transition-confidence blend

- Verdict: CONFIRMED; severity HIGH.
- Specification: SDD 10.2 defines the final score as the 30/25/20/15/10 weighted composition. Family evidence belongs inside signal agreement; no second 50/50 blend is authorized.
- Root cause: `confidence_service.py` averaged the completed SDD score with `FamilyEvidence.confidence_score`.
- Fix: removed the second-stage average. `weighted_confidence_score()` is now the single final-score function. Family evidence still contributes once through signal agreement and may rank candidate families, but does not transform the completed transition-confidence score again.
- Proof: exact 85 example; weights sum to 1.0; adapter-confidence independence; persistence 0/1/2/3; 69/70 and 84/85 boundaries.
- Historical impact: confidence, labels, actionability, lifecycle-event evidence, and alert eligibility produced by the prior version remain invalid.

### SLSE-DEF-027 - Reduced market posture classified as LOW_CONFIDENCE

- Verdict: CONFIRMED; severity HIGH.
- Specification: SDD 10.1 separates market permission from evidence confidence. A reduced market posture is WATCH_ONLY or reduced ACTIONABLE, while LOW_CONFIDENCE is reserved for evidence/data problems.
- Decision for the current configuration: `NEUTRAL`, `YELLOW`, `MIXED`, and `CAUTION` produce `WATCH_ONLY` with `MARKET_POLICY_REDUCED` and `market_posture=REDUCED`. A hard market block remains `BLOCKED`.
- Fix: separate branches for hard blockers, evidence confidence, reduced posture, non-actionable lifecycle states, and actionable lifecycle states. Actionability metadata is persisted in episode metadata and lifecycle-event evidence.
- Proof: READY 90 GREEN/NEUTRAL/YELLOW/BEARISH; READY 60 GREEN; stale GREEN; stale BEARISH; DEVELOPING; EXTENDED; FAILED.

### SLSE-DEF-028 - Freshness used calendar days

- Verdict: CONFIRMED; severity HIGH.
- Specification: FR-014, AC-13, SDD 7.1 and 19.1 require completed US trading-session distance.
- Fix: freshness uses the shared `us_trading_sessions_between()` calendar utility. Weekend and NYSE holiday dates add no age.
- Proof: Friday-Monday, Friday-Tuesday, Friday-next-Friday, weekend-only, Good Friday/Easter, Independence Day, Thanksgiving, Christmas, and New Year.
- Historical impact: snapshot freshness/data quality/confidence and DATA_DEGRADED eligibility around weekends and holidays require regeneration.

### SLSE-DEF-029 - Terminal EXPIRED semantics and fabricated confidence

- Verdict: CONFIRMED in the public pure-engine path; severity MEDIUM.
- Reachability: `LifecycleEngine.evaluate(previous_state=EXPIRED)` is directly callable and is also relevant to replay/repair invariants even though normal active-episode processing closes expired episodes.
- Fix: terminal `FAILED` remains `BLOCKED`; terminal `EXPIRED` is `WATCH_ONLY`. The decision preserves supplied prior evidence confidence and otherwise uses 0 instead of fabricating 100. Terminal-lock certainty is exposed separately as `terminal_locked=true`.
- Proof: direct FAILED and EXPIRED tests plus the existing observation-gap close/no-repeat episode sequence.

### SLSE-DEF-017 / FR-031 - Typed prior canonical history

- Disposition: FIXED in the evaluation path.
- Implementation: `LifecycleEvaluationInput.previous_snapshots` is a typed tuple; adapters receive it; the engine rejects unordered, over-window, current-date, or future history. The configured window is 10 sessions.
- Retrieval: evaluation performs one bounded window-function query for all ticker/timeframe keys, then advances each chronological in-memory window as selected snapshots are evaluated. There is no per-ticker history query.
- Evidence: lifecycle decisions record prior snapshot count/dates; tests prove ordering, point-in-time rejection, and one batched load with chronological roll-forward.

## Second-pass current gate status

Focused SLSE tests: 194 passed locally. The disposable PostgreSQL vertical test and six shared market-calendar tests also passed. Repository-wide non-E2E/non-external: 1,274 passed, 8 skipped, with one known unrelated CERI fake-DB test explicitly deselected after it failed in the unfiltered run. DEF-026/027/028/029 and FR-031 are corrected, but the 25-scenario full-layer golden corpus, natural multi-date real-source alert certification, historical rebuild, CI archive, scale measurements, and accessibility audit remain open. Market Changes and Alert Center therefore remain FAIL.

## Closure-pass defect disposition (2026-08-12)

### SLSE-DEF-030 — Family adapters depended on inputs not proven in production snapshots

- Severity: CRITICAL / release blocker.
- Root cause: test fixtures supplied convenient signal keys without a complete persisted-source or point-in-time derivation contract.
- Fix: `adapter_input_audit.py` provides a machine-checkable catalog for adapter, signal key, business meaning, SRS/SDD rule, source entity/path/effective date, snapshot-builder mapping, JSON key, required/null semantics, derivation, and history use. Snapshot construction now maps the required persisted technical evidence and immutable debug fields. The AST audit rejects any literal adapter input absent from the signal registry/catalog and rejects obsolete magic inputs.
- Proof: adapter-input coverage tests and the full 206-test focused suite pass.
- Historical impact: prior family decisions remain invalid until the gated rebuild.

### SLSE-DEF-031 — Prior canonical history did not affect family evidence

- Severity: HIGH / release blocker.
- Root cause: typed history transport existed, but family decisions still read only the current snapshot.
- Fix: history now drives multi-session contraction/tightness, decreasing volume, close-above-trigger persistence/follow-through, pullback progression/support evidence, VCP contraction count, and generic improving-score phase evidence. Only genuinely temporal SRS/SDD rules use history.
- Proof: for every family, the same current snapshot with history A and history B produces different evidence, phase, or lifecycle decision as appropriate.
- Disposition: **FIXED; FR-031 PASS at the semantic-core layer**.

### SLSE-DEF-032 — Confidence freshness/lineage and agreement were underspecified

- Severity: HIGH.
- Root cause: `data_quality_label` was used as a proxy for several independent facts and agreement was not tied to the four specified evidence dimensions.
- Fix: final confidence is exactly `100 × (0.30 coverage + 0.25 agreement + 0.20 persistence + 0.15 freshness_and_lineage + 0.10 context)`; agreement is the configured weighted mean of trend, contraction, relative-strength, and classification alignment; freshness/lineage is the configured weighted mean of completed-bar freshness, successful source run, and consistent lineage/version/hash evidence.
- Proof: each freshness/lineage dimension is varied independently; high setup evidence with contradictory agreement is covered; thresholds and prior DEF-026 cases remain green.

### SLSE-DEF-033 — Compound actionability precedence was incomplete

- Severity: HIGH.
- Fix order: terminal FAILED; terminal EXPIRED; hard blockers (required data, market, earnings, liquidity); insufficient/low/stale evidence; reduced market posture; lifecycle posture; actionable state. Lifecycle state is never rewritten by the actionability policy.
- Proof: mandatory READY+60+NEUTRAL, stale+neutral, stale+bearish, 60+earnings, DEVELOPING+low confidence, EXTENDED+stale, EXPIRED+stale, and FAILED combinations assert actionability, reasons, blockers, and metadata.

### SLSE-DEF-034 — Stale pre-second-pass status statements

- Severity: MEDIUM / governance.
- Fix: each required audit document now begins with an authoritative four-stage disposition table. Older findings remain dated and are not release authority.
