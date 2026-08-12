# SLSE SRS/SDD Traceability Matrix

## Closure certification override (2026-08-12, final execution)

This table is the newest authoritative status and supersedes every older row or prose disposition below it. The detailed matrix remains as forensic history of the findings that drove the closure work.

| Requirement group | Current status | Direct evidence |
|---|---|---|
| Snapshot, PIT lineage, canonicalization, version/hash DTOs | **PASS** | 25-scenario golden; 236-test PostgreSQL closure suite; natural certification |
| Change detection, exact deltas, velocities, materiality, missing/freshness changes | **PASS** | Golden corpus; semantic/unit suite; rebuilt-history defect signatures all zero |
| Five-family lifecycle, semantic history, confidence, compound actionability | **PASS** | DEF-030..034 tests and production-stack golden sequences |
| Alert rules, filters, review actions, DTOs and full CSV/JSON export | **PASS** | Rebuilt E2E; 1,598 API/JSON/CSV rows; alert 3400 acknowledged and 3446 dismissed |
| Market Changes filters, combined streams, totals, sorts, keyset pagination, exports | **FUNCTIONAL PASS** | 236 closure tests; rebuilt Playwright/API/DB checks |
| Operator scopes, replay, audit and repair | **PASS** | Route/service closure suite and operations UI |
| Natural multi-date real-source certification | **PASS** | `evidence/slse_natural_certification_2026-08-12.json` |
| Dev/QA derived-history remediation | **PASS** | `evidence/slse_dev_rebuild_2026-08-12.json`; recoverable pre-rebuild dump |
| NFR-002 / 1,000-ticker evaluation <=60s | **PASS** | 57.210395 seconds on disposable PostgreSQL |
| NFR-003 / ordinary dashboard and timeline P95 <=500ms at >=100k snapshots | **PASS** | First page 307.075ms; deep cursor 250.431ms; No Material Change 170.688ms; timeline 23.561ms |
| NFR-003 / worst compound filter P95 <=500ms | **FAIL** | 1,183.523ms at 110,000 snapshots / 100,000 changes; no exception approved |
| NFR-009 accessibility | **PASS** | 19 Playwright tests plus keyboard/semantic snapshot evidence |
| Closure workflow | **IMPLEMENTED, NOT INDEPENDENTLY RUN** | `.github/workflows/slse-closure.yml`; local equivalent green except performance |
| Merge / activation | **BLOCKED** | Mandatory performance gate is red; engine and runtime flags remain disabled |
| Overall specification/release | **FAIL** | One mandatory performance criterion, independent CI, merge and activation remain open |

Current identity: engine `slse-1.2.0`, config `2026-08-12`, snapshot schema `slse-snapshot-1.0.0`, migration head `0034_slse_dashboard_indexes`.

## Superseded pre-closure status (retained as forensic history)

The table and detailed matrix below are superseded by the closure certification override above. They are retained only as the dated audit trail that motivated the implementation.

| Requirement/defect | Original finding | First-pass disposition | Second-pass disposition | Status recorded before final closure |
|---|---|---|---|---|
| DEF-030 / production adapter lineage | Not traced | Not assessed | Not assessed | **PASS** — executable input catalog plus AST/registry enforcement. |
| DEF-031 / FR-031 | History absent | PARTIAL | Transport PASS | **PASS** — semantic history use proven for all five families. |
| DEF-032 / FR-036 | Confidence inputs conflated | PARTIAL | Top-level formula PASS | **PASS** — exact agreement and freshness/lineage subcomponents proven independently. |
| DEF-033 / FR-037 | Compound precedence implicit | PARTIAL | Reduced posture PASS | **PASS** — canonical compound truth table passes. |
| DEF-034 / documentation | Conflicting status prose | PARTIAL | Addenda | **PASS** — this table supersedes dated dispositions. |
| Overall specification | PARTIAL | PARTIAL | PARTIAL | **PARTIAL / RELEASE FAIL** — remaining functional and certification rows are still open. |

Engine/config identity: `slse-1.2.0` / `2026-08-12`; snapshot schema: `slse-snapshot-1.0.0`. Engine activation and history rebuild remain blocked.

Authoritative inputs: SRS 1.0 and SDD 1.0 dated 31 July 2026. Review date: 11 August 2026. `PASS` means the requirement is supported by direct implementation and test evidence; existing green tests alone are not sufficient.

| Requirement ID | Expected behavior | SDD design | Implementation path | DB mapping | API mapping | GUI mapping | Tests | Observed behavior | PASS / FAIL / PARTIAL / NOT IMPLEMENTED | Defect ID |
|---|---|---|---|---|---|---|---|---|---|---|
| SLSE-FR-001 | Immutable snapshot per eligible ticker after upstream context completes | Snapshot builder after sector context | `source_loader.py`; `snapshot_builder.py` | `setup_signal_snapshots` | Evaluation result | Operations only | snapshot/source-loader tests | Per-ticker capture exists and is isolated | PASS | — |
| SLSE-FR-002 | Store lineage IDs, dates, timestamps, engine/config versions and hashes | Snapshot identity/version groups | `snapshot_builder.py`; repository | Snapshot identity/version columns | Snapshot timeline payload is partial | Ticker timeline is partial | schema/snapshot tests | Persisted; API omits several version/hash fields | PARTIAL | SLSE-DEF-014 |
| SLSE-FR-003 | Copy point-in-time scalar lifecycle features | Promoted scalar groups plus sparse JSON | `_promoted_fields`; `_source_values` | Scalar columns + `signals_json` | Payload exposes a subset | Market Changes infers from evidence | snapshot tests | Important fields are persisted but not carried through DTOs | PARTIAL | SLSE-DEF-014 |
| SLSE-FR-004 | Preserve available source identifiers | Source link group | `_source_ids` | Snapshot FKs | Snapshot payload | Ticker source links | snapshot/schema tests | IDs are persisted | PASS | — |
| SLSE-FR-005 | Optional missing values remain null with warnings, never zero | Graceful incompleteness | Builder warnings/missing JSON | JSONB + nullable scalars | JSON preserves null where exposed | UI often renders empty string | snapshot tests | Backend nulls survive; display/export contract incomplete | PARTIAL | SLSE-DEF-013 |
| SLSE-FR-006 | Capture idempotent for same identity/version/hash | Unique key and upsert | repository snapshot key/upsert | Unique constraint | Evaluation retry | N/A | retry/repository tests | Idempotency exists, including revised source hash | PASS | — |
| SLSE-FR-007 | Preserve noncanonical same-day snapshots | Append-only capture/canonical metadata | canonicalization service | All snapshots retained | Timeline snapshots | Noncanonical labels incomplete | canonical tests | Rows retained; GUI labeling incomplete | PARTIAL | SLSE-DEF-024 |
| SLSE-FR-008 | Forward capture; reconstructed history labeled | Origin enum/policy | config/constants | `origin_type` | Snapshot payload | Partial badge support | config tests | Forward mode and exclusion policy exist | PASS | — |
| SLSE-FR-009 | Per-ticker failure isolation and systemic reporting | Per-ticker transactions/status | capture/evaluation services | Evaluation counts/errors | Evaluation API | Operations | fault tests | Per-ticker errors yield PARTIAL | PASS | — |
| SLSE-FR-010 | Exactly one canonical snapshot per ticker/day | Canonical service and partial unique index | `canonicalization.py` | Canonical partial unique index | Snapshot timeline | Indirect | canonical/schema tests | Deterministic single canonical row | PASS | — |
| SLSE-FR-011 | Canonical precedence: completed/success/coverage/context/time/id | Versioned precedence | `canonicalization.py` | `canonical_reason`, decision JSON | Not fully exposed | Not fully exposed | canonical tests | Selection exists; debug contract is incomplete | PARTIAL | SLSE-DEF-024 |
| SLSE-FR-012 | Canonical revision audit without deletion | Revision event/supersession | canonicalization service | Snapshot supersession + lifecycle audit event | Timeline | Partial | canonical tests | Prior snapshot retained and audit event written | PASS | — |
| SLSE-FR-013 | Compare only previous canonical ticker/timeframe snapshot | Repository previous-canonical query | change detector | Snapshot FKs on change event | Timeline | Indirect | change/canonical tests | Implemented | PASS | — |
| SLSE-FR-014 | Trading-session gap calculation | US market calendar | episode/alert/maintenance | Episode counters | Episode/timeline | Displayed partially | calendar/maintenance tests | Implemented for episode/cooldown paths | PASS | — |
| SLSE-FR-015 | Stale source remains queryable and becomes LOW_CONFIDENCE | SDD permits LOW_CONFIDENCE or BLOCKED; SRS is stricter | builder + actionability policy | Freshness/warnings/actionability | Snapshot/episode payload | Badges partial | stale actionability test | Current policy returns LOW_CONFIDENCE unless another hard gate blocks | PASS | SLSE-AMB-001 |
| SLSE-FR-020 | Typed absolute/percentage/percentile/rank/boolean/enum/set changes | Registry normalizers | `change_detector.py` | Change value/delta columns | Signal-change payload partial | Ticker timeline only | change tests | Percentile delta is never calculated; raw rank delta sign is wrong | PARTIAL | SLSE-DEF-006 |
| SLSE-FR-021 | Threshold crossings independent of raw delta | Materiality algorithm | change detector | Threshold columns | Signal-change payload | Timeline | boundary tests | Implemented | PASS | — |
| SLSE-FR-022 | Events for scores, classification, stage, RS, sector, market, risks, quality, derived metrics | Registry-driven coverage | YAML registry/change detector | `signal_change_events` | Timeline; absent from Market Changes | Absent from Market Changes | registry/change tests | Registry is incomplete relative to SRS and dashboard stream excludes these rows | FAIL | SLSE-DEF-011 |
| SLSE-FR-023 | Store old/new, normalized delta, direction, category, severity, threshold, snapshots, reasons | Change-event schema | `_to_event` | Change-event columns | Payload omits numeric deltas/snapshot IDs | Not rendered | DB/change tests | Persistence mostly complete; DTO/UI/export incomplete | PARTIAL | SLSE-DEF-014 |
| SLSE-FR-024 | Canonical severity enum INFO/NOTABLE/ACTIONABLE/RISK | Enum | `enums.py` | Severity strings | API returns strings | Alert selector uses WARNING instead of NOTABLE | enum/UI tests missing | Backend enum is correct; UI contract is not | FAIL | SLSE-DEF-008 |
| SLSE-FR-025 | Configurable 1/3/5/10-session velocities | Registry windows | `velocity_by_window` | Evidence JSON | Not explicit in Market DTO | Often blank | velocity tests | Computed but buried in event evidence and not stable DTO fields | PARTIAL | SLSE-DEF-014 |
| SLSE-FR-026 | No event below all materiality thresholds | Detector materiality | `_is_material` | No row | N/A | N/A | change tests | Implemented | PASS | — |
| SLSE-FR-027 | Emit on crossing, no unchanged repeat | Entry/exit crossing and event key | detector + alert cooldown | Unique event keys | API event list | Timeline | no-repeat tests | Implemented for direct crossings | PASS | — |
| SLSE-FR-028 | Explicit missing/present and stale/fresh changes | Nullability/data-quality events | detector | Reason codes | Signal-change payload | Timeline | change tests | Missing/present works; freshness is only indirect through quality label | PARTIAL | SLSE-DEF-018 |
| SLSE-FR-030 | Evaluate family, phase, state, actionability, confidence, reasons | Lifecycle input/output | adapters, engine, policy, episode service | Snapshot/episode/event | Timeline/episode | Market rows partial | lifecycle tests | Core exists | PASS | — |
| SLSE-FR-031 | Use previous episode state and prior canonical snapshots | Stateful evaluator input | episode service/engine | Episode current state | N/A | N/A | sequence tests | Previous state is used; family adapters do not receive the configured prior snapshot window | PARTIAL | SLSE-DEF-017 |
| SLSE-FR-032 | Support all common lifecycle states | State machine | engine | State columns | DTOs | Badges | state tests | All states represented | PASS | — |
| SLSE-FR-033 | Family-specific configurable phases/adapters | Family adapters/config | adapter modules | Phase columns | DTOs | Displayed | family tests | Breakout/pullback/VCP/continuation/generic exist | PASS | — |
| SLSE-FR-034 | Persistence for normal progression; immediate failure/hard block | Engine precedence | engine/episode service | Events | DTO | Timeline | boundary tests | Implemented for lifecycle; hard block is orthogonal | PASS | — |
| SLSE-FR-035 | Hysteresis prevents flapping | Enter/exit thresholds | engine/adapters | State history | DTO | Timeline | oscillation tests | Implemented for principal cases | PASS | — |
| SLSE-FR-036 | Confidence 0–100 from coverage/freshness/persistence/agreement | Weighted confidence service | `confidence_service.py` | Snapshot/episode/event score/label | DTO fields | Displayed | confidence via lifecycle tests | Required-feature component incorrectly counts all registered signals | FAIL | SLSE-DEF-002 |
| SLSE-FR-037 | Gates do not overwrite lifecycle state | Orthogonal policy | actionability policy | Separate state/actionability columns | DTO fields | Separate badges | invariant tests | Implemented | PASS | — |
| SLSE-FR-038 | FAILED/EXPIRED closes episode with terminal reason | Episode close | episode service | Episode terminal columns | Episode DTO | Detail page | episode tests | Implemented | PASS | — |
| SLSE-FR-039 | Rearm only after cooldown/new signature | Rearm policy | episode service | New episode | DTO | Detail page | rearm tests | Implemented with strongest-state exception as fresh evidence | PASS | — |
| SLSE-FR-040 | At most one active episode per family key | Partial unique index | repository/schema | DB constraint | N/A | N/A | schema tests | Implemented | PASS | — |
| SLSE-FR-041 | Deterministic primary episode without deletion | Primary ranking | episode service | `is_primary`, rank | Episode DTO | Ticker page | selection tests | Implemented | PASS | — |
| SLSE-FR-042 | Insufficient evidence keeps state and reduces confidence | Prefer no-change | engine | Episode/event | DTO | Timeline | lifecycle tests | Implemented | PASS | — |
| SLSE-FR-050 | All built-in alert rules | Alert service/config | alert service | Rule/event tables | Alert list lacks type | Type absent from UI | alert tests | Rules seed, but acceleration and gate truth tables are wrong/incomplete | FAIL | SLSE-DEF-003, SLSE-DEF-004, SLSE-DEF-005 |
| SLSE-FR-051 | Rule enabled/severity/scope/cooldown/min confidence/restrictions | Alert rule config | config + service | Rule columns/JSON | Not exposed | Not exposed | config/alert tests | Market restrictions are persisted but not evaluated | PARTIAL | SLSE-DEF-019 |
| SLSE-FR-052 | Idempotent rule/source alert creation | Stable event key | alert service/repository | Unique event key | Stable IDs | N/A | retry tests | Implemented | PASS | — |
| SLSE-FR-053 | In-app and export alerts only | Alert Center/export | routes/templates/export | Alerts | API/export | Alert Center | route tests | Exists, but exports are incomplete | PARTIAL | SLSE-DEF-013 |
| SLSE-FR-054 | Acknowledge/dismiss without source mutation | Mutable review state only | alert service/routes | Status/timestamps | POST endpoints | Row actions | alert/route tests | Implemented | PASS | — |
| SLSE-FR-055 | Distinguish actionable/risk and filter unread/ack/date/ticker/state/severity | Alert query/UI | query service/routes | Alert/event tables | Only ticker/status/severity | Missing date/state/type/source filters | route tests | Required filters and semantic fields absent | FAIL | SLSE-DEF-007, SLSE-DEF-020 |
| SLSE-FR-060 | Daily dashboard transition counts and lists for selected date | Combined daily change view | query/routes/template | Events/snapshots | No `as_of`; lifecycle events only | Page-local counts | route tests | Wrong event source and count scope | FAIL | SLSE-DEF-009, SLSE-DEF-011 |
| SLSE-FR-061 | Full documented filter set | Promoted columns | query service | Snapshot/event fields | Partial filter set | Partial free-text form | route tests | Several filters missing or semantically wrong | PARTIAL | SLSE-DEF-020, SLSE-DEF-025 |
| SLSE-FR-062 | Required sort set with deterministic tie-break | Query service sorts | `_sort_events`, `_sort_alerts` | Indexed fields | Partial | Sort selector partial | performance/route tests | Transition priority is lexicographic severity; alert ordering ignores severity priority | FAIL | SLSE-DEF-010 |
| SLSE-FR-063 | Chronological ticker timeline of snapshots/phases/states/changes/blockers/events | Timeline query | query service | All core tables | Ticker API | Ticker page | route tests | Present, though pagination is fixed limit not cursor | PARTIAL | SLSE-DEF-024 |
| SLSE-FR-064 | Explain transitions with metric evidence | Evidence contract | lifecycle event evidence | JSONB | Opaque evidence dict | Raw JSON expansion | route tests | No stable explicit field mapping | PARTIAL | SLSE-DEF-014 |
| SLSE-FR-065 | Previous/current values side by side | Change DTO | signal-change event | Old/new JSON | Only ticker timeline signal payload | Market Changes absent | change tests | Not satisfied on Market Changes | FAIL | SLSE-DEF-011, SLSE-DEF-014 |
| SLSE-FR-066 | Filtered episode/event JSON and CSV export | Export service | routes/export service | Query results | Exports ignore active filters and cap first page | Links lose filters | export tests | Incomplete schemas and filter parity | FAIL | SLSE-DEF-013 |
| SLSE-FR-067 | Paginated lists with total counts | Query page | query service | Count query | `total`, cursors | Next page | query tests | Total count exists; offset cursor is deterministic only with correct sort | PASS | — |
| SLSE-FR-068 | Label reconstructed/stale/low-coverage/noncanonical/superseded | UI semantics | payload/templates | Origin/quality/canonical/version fields | Partial | Partial | route tests | Not complete on Market Changes/Alert Center | PARTIAL | SLSE-DEF-024 |
| SLSE-FR-070 | Admin evaluate by run/ticker/date/all eligible | Operations/replay | evaluate/replay routes | Evaluation run scope | Run plus replay scope | Operations | route/replay tests | Run supported; other capture scopes are incomplete | PARTIAL | SLSE-DEF-022 |
| SLSE-FR-071 | Dry-run replay with no writes | Replay service | replay service | None for dry run | Replay API | Operations | replay tests | Implemented | PASS | — |
| SLSE-FR-072 | Persisted replay creates new version without overwrite | Versioned replay | replay service | Evaluation/event versions | Replay API | Operations | replay tests | Implemented | PASS | — |
| SLSE-FR-073 | Evaluation status/counts/warnings/failures/versions/duration | Operations DTO | evaluation/query service | Evaluation table | Operations API | Operations page | service tests | Implemented | PASS | — |
| SLSE-FR-074 | Retry one ticker for data-specific failure | Repair job | maintenance/job handlers | Evaluation/repair rows | No dedicated public scoped endpoint | Operations partial | maintenance tests | Service exists; operator contract incomplete | PARTIAL | SLSE-DEF-022 |
| SLSE-FR-075 | Validate config startup/replay; fail fast | Config parser | config.py | Config hash | HTTP 400 on replay validation | Operations | config tests | Implemented | PASS | — |
| SLSE-NFR-001 | Deterministic results | Pure functions/versioned config | engine/repository keys | Version/hash columns | Stable DTOs partial | N/A | determinism tests | Core deterministic | PASS | — |
| SLSE-NFR-002 | 1,000-ticker evaluation within 60s | Batch design | loader/evaluation | Indexed writes | Metrics | Operations | synthetic performance test only | Real PostgreSQL target not proven | PARTIAL | SLSE-DEF-023 |
| SLSE-NFR-003 | P95 dashboard/timeline <500ms at 100k snapshots | Indexed queries | query service | Indexes | APIs | Pages | index contract test only | No measured 100k PostgreSQL proof | PARTIAL | SLSE-DEF-023 |
| SLSE-NFR-004 | Retry creates no duplicates | Unique keys/constraints | services/repository | Unique constraints | Idempotent endpoints | N/A | retry tests | Implemented for core rows | PASS | — |
| SLSE-NFR-005 | Every state/event exposes IDs/version/hash/reasons/evidence | Auditability contract | persistence complete; DTO incomplete | Columns/JSON | Payloads omit key lineage | UI omits lineage | schema tests | Fails at API/UI boundary | FAIL | SLSE-DEF-014 |
| SLSE-NFR-006 | SLSE failure does not corrupt upstream | Nonfatal pipeline | evaluation/job handlers | PARTIAL status | Run result | Operations | service tests | Implemented | PASS | — |
| SLSE-NFR-007 | External validated signal/transition config | YAML registry/config | config.py/yaml | Config hash | N/A | N/A | config tests | Implemented | PASS | — |
| SLSE-NFR-008 | Pure independently testable state machine | Pure engine | lifecycle_engine.py | N/A | N/A | N/A | extensive unit/property tests | Implemented | PASS | — |
| SLSE-NFR-009 | Accessible table headers/color-independent/keyboard usable | UI semantics | templates/JS | N/A | N/A | Table headers and labels exist | smoke only | Needs dedicated accessibility/keyboard proof | PARTIAL | SLSE-DEF-023 |
| SLSE-NFR-010 | Existing FastAPI/PostgreSQL topology | Same application | current package | PostgreSQL | FastAPI | Jinja/HTMX | suite | Implemented | PASS | — |
| SLSE-NFR-011 | Append-only snapshots/source events | Append-only design | repository/services | Rows append; canonical metadata controlled | N/A | N/A | schema tests | Implemented | PASS | — |
| SLSE-NFR-012 | Queryable counts/durations for capture/evaluation/transitions/alerts/replay | Observability design | metrics/evaluation runs | Counts and timestamps | Diagnostics/operations | Operations | service tests | Most metrics exist; detailed latency proof incomplete | PARTIAL | SLSE-DEF-023 |
| AC-01 | One snapshot/ticker and retry dedupe | Capture idempotency | builder/repository | Unique snapshot identity | Evaluation result | N/A | capture retry test | Satisfied | PASS | — |
| AC-02 | Two same-day snapshots retained, one canonical | Canonical sequence | canonicalization | Partial unique index | Timeline | Partial labels | canonical tests | Satisfied | PASS | — |
| AC-03 | TIGHTENING→READY creates one event and one non-repeating NEW_READY | Lifecycle/alert rules | episode + alert service | Event/alert unique keys | Alert API | Alert Center | separate tests | Exact combined truth-table sequence not tested | PARTIAL | SLSE-DEF-016 |
| AC-04 | READY + imminent earnings remains READY, becomes BLOCKED with reason | Orthogonal policy | actionability policy | Episode/actionability metadata | Episode DTO | Badges | invariant test | Satisfied | PASS | — |
| AC-05 | Hysteresis stops READY/TIGHTENING flapping | Hysteresis | engine/adapters | State history | Timeline | Timeline | sequence test | Satisfied | PASS | — |
| AC-06 | Hard failure immediately FAILED, closes episode, RISK event | Failure first | engine/episode/alert | Episode/event/alert | APIs | Pages | lifecycle/episode/alert tests | Covered in separate layers; vertical fixture incomplete | PARTIAL | SLSE-DEF-016 |
| AC-07 | One filtered absence does not close; expiry after threshold | Gap maintenance | maintenance/episode service | Gap counter | Episode/timeline | Detail | maintenance tests | Satisfied | PASS | — |
| AC-08 | Required missing remains null and causes low confidence/no transition | Missing policy | builder/engine/policy | Nullable fields/warnings | Snapshot/episode | Badges | missing-data tests | False missing-close warning contaminates fully populated data | FAIL | SLSE-DEF-001 |
| AC-09 | Filter newly triggered and sort velocity/confidence | Dashboard query | query service | Event/snapshot | Partial | Partial | route forwarding only | Velocity mapping/filter semantics not proven | PARTIAL | SLSE-DEF-014, SLSE-DEF-025 |
| AC-10 | Detail explains prior/current metrics, reasons, versions, source links | Stable evidence DTO | query service | All core tables | Partial | Raw JSON/incorrect link | route tests | Not satisfied end-to-end | FAIL | SLSE-DEF-012, SLSE-DEF-014 |
| AC-11 | Dry replay no writes | Replay | replay service | No writes | Replay API | Operations | replay test | Satisfied | PASS | — |
| AC-12 | Persisted replay new version, retains original | Replay | replay service | Parallel version | Replay API | Operations | replay test | Satisfied | PASS | — |
| AC-13 | State/gap age uses trading sessions | Calendar | episode/maintenance | Age fields | DTO | Display | calendar tests | Satisfied | PASS | — |
| AC-14 | 1,000 ticker performance or documented exception | Performance design | evaluation | Metrics | Operations | Operations | key-generation test only | Not proven | NOT IMPLEMENTED | SLSE-DEF-023 |
| AC-15 | No order placement/staging | Research-only boundary | package/routes | N/A | No order endpoints | Disclaimer | source scan test | Satisfied | PASS | — |

## Specification ambiguities

- `SLSE-AMB-001`: SRS FR-015 mandates stale snapshots receive `LOW_CONFIDENCE`; SDD 10.1 permits severely stale/insufficient data to become `LOW_CONFIDENCE or BLOCKED according to policy`. Recommendation: preserve FR-015 for staleness alone; reserve `BLOCKED` for an independently defined hard-required-data or gate condition.
- `SLSE-AMB-002`: SDD 16.1 describes Alert Center as “actionable/risk alerts,” while SRS FR-050 and the default rule table require `NOTABLE` acceleration alerts and FR-055 requires severity filtering. Recommendation: Alert Center includes all four canonical severities; default filters may emphasize unread actionable/risk but must never hide NOTABLE as unsupported.

## Post-repair compliance override

The row-by-row table above preserves the pre-repair forensic observation. Current status after repair/testing is:

| Requirement set | Current status | Evidence / remaining gap |
|---|---|---|
| FR-001–009 | PASS except FR-002/003 presentation PARTIAL | required mappings/nulls/coverage and per-ticker point-in-time context repaired; not every version/hash is on list DTOs |
| FR-010–015 | PASS | canonical behavior retained; stale-only policy follows SRS FR-015 recommendation |
| FR-020–028 | PASS | typed changes, +rank normalization, velocities, quality/freshness signals and materiality tested |
| FR-030–042 | PASS except FR-031 PARTIAL | state/actionability/confidence/episodes/gaps pass; adapters still lack typed prior snapshot window |
| FR-050–055 | PASS except FR-055 PARTIAL | all alert types/severities/source types and truth boundaries implemented; complete date-range/actionability/blocker UI filters remain |
| FR-060–068 | PASS except FR-063/068 PARTIAL | combined streams, explicit DTOs, no-change view, counts, deterministic sorts, source links and exports pass; timeline cursor/version labels remain |
| FR-070–075 | PASS for 071/072/073/075; PARTIAL for 070/074 | replay/status/config pass; all explicit operator scopes/retry endpoint remain |
| NFR-001/004/006/007/008/010/011 | PASS | deterministic/idempotent/pure/versioned/research topology tests |
| NFR-002/003/009/012 | PARTIAL | real PostgreSQL and Playwright proof passed; scale targets and dedicated accessibility/latency audit remain |
| NFR-005 | PARTIAL | stable source IDs/reasons/evidence exposed; complete version/hash list projection remains |
| AC-01/02/04/05/07/08/09/10/11/12/13/15 | PASS | unit, PostgreSQL, export and browser evidence |
| AC-03/06 | PARTIAL | component truth tables pass; complete golden vertical sequences remain |
| AC-14 | NOT IMPLEMENTED | no measured 1,000-ticker ≤60s certification |

Because AC-14 and required golden/adapter/operator/version-label work remain open, the overall specification compliance decision is **PARTIAL**, not PASS.

## Second-pass compliance override (2026-08-11)

The following rows supersede the earlier post-repair override where they conflict.

| Requirement | Second-pass status | Direct evidence |
|---|---|---|
| FR-014 / AC-13 freshness | PASS | Snapshot freshness now counts completed US trading sessions; weekend and NYSE holiday boundary tests pass. |
| FR-031 prior history | PASS | Typed, ordered, bounded, no-future prior canonical window reaches every family adapter through one batched retrieval plus chronological roll-forward. |
| FR-036 confidence | PASS | Exact 30/25/20/15/10 score; no adapter-confidence blend; exact 85 and 69/70, 84/85 boundaries. |
| FR-037 actionability orthogonality | PASS | Reduced market posture is WATCH_ONLY with metadata; hard market block is BLOCKED; evidence problems remain LOW_CONFIDENCE. |
| FR-038 terminal closure | PASS for corrected semantics | FAILED is BLOCKED; EXPIRED is WATCH_ONLY; terminal confidence preserves prior evidence or is unavailable/0, never fabricated 100. |
| AC-03 / AC-06 golden vertical sequences | PARTIAL | Component and episode sequences pass, but the required full 25-scenario snapshot-to-DTO corpus is not complete. |
| NFR-002 / NFR-003 / NFR-009 / AC-14 | PARTIAL / NOT IMPLEMENTED | No new scale/accessibility certification was produced in this pass. |

Current overall decision remains **PARTIAL**. The corrected semantic core does not by itself satisfy release certification.
