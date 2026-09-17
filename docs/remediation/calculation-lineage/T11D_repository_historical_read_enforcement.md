# T11D — Repository-wide historical-read enforcement

## 1. Baselines

| Item | Verified value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T11A | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` |
| T11B-1 | `bef1abe285779c3aa278e3691a9438126f5ccedc` |
| T11B-2 | `f5bfa546ac5d03e641eb67473b21e7a04629cb23` |
| T11B-3 / T11B | `3aee1c260f63c53f5fe25d85be2948283b48dbc1` |
| T11C / T11D starting HEAD | `7e19ad81c30727d5177f20d559b1a3b67304031a` |
| Branch | `codex/t11d-historical-read-enforcement` |
| Starting worktree | Clean: no tracked changes and no untracked files |
| Alembic head | `0079_setup_lifecycle_alert_ev` |
| Runtime | CPython 3.12.2 in repository `.venv` |
| Audit date | 2026-09-15, Europe/Zurich |

No unrelated work was present or overwritten. The original audited registry remains unchanged.

## 2. Scope

T11D audited read behavior, not evidence-table design. The review covered pipeline/context and
resume, Price PIT, Fundamental, Technical, Combined, Ranking, Regime, Sector, CERI, IBMI,
Setup, Lifecycle, Alerts, Winner prediction/outcomes/generations, generic run history, APIs,
repair/replay/backfill, and production-reachable service helpers. No schema, migration,
production data, job, provider, broker, or application-runtime mutation was performed.

## 3. Historical-read definition

A request is historical when it claims decision-time, as-of, replay-anchored,
Calculation-Identity-specific, or explicit-evidence truth. A run ID alone does not make a
current dashboard historical. Historical requests must resolve an exact immutable evidence
ID/identity and compatible scope, or reject with `EVIDENCE_UNAVAILABLE`,
`LEGACY_EVIDENCE_UNAVAILABLE`, `HISTORICAL_EVIDENCE_UNAVAILABLE`, or
`ORIGINAL_CONTEXT_RECONSTRUCTION_UNSUPPORTED`.

## 4. Current vs historical API contract

`ReadMode.CURRENT` resolves the independent current-evidence pointer and rejects historical
anchors. `ReadMode.EVIDENCE` requires exactly an evidence ID or Calculation Identity and
never consults a current projection after a miss. `ReadMode.CURRENT_RULES_RETROSPECTIVE` is
not presented as stored history. `ReadMode.ORIGINAL_CONTEXT` fails explicitly because full
original-context reconstruction is not implemented. Existing live dashboards remain usable
and identify themselves as `CURRENT_PROJECTION` or an equivalent current projection mode.

## 5. Repository-wide reader inventory

| Consumer / path | Material read | Classification | T11D disposition |
|---|---|---|---|
| Generic `/history` decisions | prior Combined decisions by run | `CERTIFIED_EVIDENCE_READ` | Reads `COMBINED` evidence JSON; no `CombinedResult` fallback. |
| Generic `/history` run cards | latest operational run summary | `CURRENT_PROJECTION_READ` | Retained and labeled current operational state. |
| Core exact reader | evidence ID / Calculation Identity | `HISTORICAL_MODE_EXPLICIT` | Exact immutable envelope or rejection. |
| Core current reader | run/ticker/profile pointer | `CURRENT_MODE_EXPLICIT` | Current evidence pointer remains convenient. |
| Regime history/dashboard/export | recent compatibility snapshots | `CURRENT_PROJECTION_READ` | Retained and labeled current projection. |
| Regime evidence API | explicit evidence ID | `CERTIFIED_EVIDENCE_READ` | Exact `REGIME` evidence. |
| Sector history/dashboard/export | recent compatibility snapshots | `CURRENT_PROJECTION_READ` | Retained and labeled current projection. |
| Sector evidence API | explicit evidence ID | `CERTIFIED_EVIDENCE_READ` | Exact `SECTOR` envelope and pinned source IDs. |
| Regime→Sector production selection | compatible run/global candidate | `COMPATIBLE_GLOBAL_EVIDENCE_READ` | Existing Phase-1 compatibility gate retained. |
| CERI latest/ticker UI | current score/feature detail | `CURRENT_PROJECTION_READ` | Explicit current label. |
| CERI stored ticker history | cutoff-bounded stored decision | `CERTIFIED_EVIDENCE_READ` | Renders sealed `CERI` `decision_output`; legacy-only history rejects. |
| CERI change comparison | current comparison/report | `CURRENT_RULES_RETROSPECTIVE` | Labeled current-projection comparison; not original history. |
| CERI point-in-time estimate query | `AS_KNOWN` / `LATEST_CORRECTED` | `ORIGINAL_CONTEXT_RECONSTRUCTION` | Existing receipt/effective-time bounds retained and distinct. |
| IBMI latest features | live feature view | `CURRENT_PROJECTION_READ` | Explicit current label. |
| IBMI evidence API | explicit evidence ID | `CERTIFIED_EVIDENCE_READ` | Exact `IBMI` envelope with constituent IDs. |
| Setup current screens/timeline | canonical snapshot / episode | `CURRENT_PROJECTION_READ` | Explicit current projection plus evidence links. |
| Setup historical-run changes | run-anchored historical view | `CERTIFIED_EVIDENCE_READ` | Requires Setup/transition evidence; sealed payload overrides presentation fields. |
| Setup evidence API | explicit evidence ID | `CERTIFIED_EVIDENCE_READ` | Exact `SETUP` envelope. |
| Lifecycle evidence APIs | evaluation/transition ID | `CERTIFIED_EVIDENCE_READ` | Exact immutable chain member. |
| Lifecycle current episode | episode ID | `CURRENT_PROJECTION_READ` | Explicit mutable-current response. |
| Alert list/notification controls | notification status | `CURRENT_PROJECTION_READ` | Current status retained; decision/rule meaning comes from sealed evidence. |
| Alert evidence API | decision evidence ID | `CERTIFIED_EVIDENCE_READ` | Exact rule and cooldown/dedup predecessor IDs. |
| Winner historical prediction API | frozen prediction/vector | `CERTIFIED_EVIDENCE_READ` | Display metadata now comes from pinned Combined/Ranking evidence. |
| Winner outcome/generation | versioned outcome/generation view | `CERTIFIED_EVIDENCE_READ` | Existing versioned/frozen design retained. |
| Pipeline resume | frozen handoff/context dependencies | `CERTIFIED_EVIDENCE_READ` | Manifest fingerprint, ownership, exact members, and evidence pointers validated. |
| Setup repair | current projection repair | `CURRENT_STATE_REPAIR` | Remains current-state repair; older-context violations reject. |
| Setup replay / CERI rebuild | current rules over selected inputs | `CURRENT_RULES_RETROSPECTIVE` | Honest label/contract; not original-context replay. |
| Backfill/recalculate/refresh | new calculation/evidence | `CURRENT_STATE_REPAIR` / new version | Never invoked by an evidence lookup miss. |
| Legacy compatibility rows | live/current serving | `LEGACY_CURRENT_READ` | Allowed only as explicit current/legacy; cannot certify history. |

## 6. Static selector classification

The initial and post-edit searches covered `latest`, `newest`, scalar/first/one selectors,
ordering/max, current/canonical, ownership IDs, evidence IDs, all temporal anchors, history,
replay/repair/backfill/rebuild/recalculate/resume, and legacy/unavailable status terms.

| Classification | Material examples | Post-edit status |
|---|---|---|
| `CURRENT_PROJECTION_READ` | dashboards, run cards, Regime/Sector history labels, CERI/IBMI latest, Setup episode/notification | Explicit and retained. |
| `CERTIFIED_EVIDENCE_READ` | core/history APIs, CERI stored history, Setup/Lifecycle/Alert exact APIs, Winner frozen metadata, resume gate | Exact IDs/identities; fail closed. |
| `COMPATIBLE_GLOBAL_EVIDENCE_READ` | Regime cross-run reuse for a new calculation | Phase-1 compatibility checked; not used as stored history. |
| `CURRENT_STATE_REPAIR` | Setup repair/maintenance and explicit recalculation | Current-only and does not claim original history. |
| `CURRENT_RULES_RETROSPECTIVE` | Setup replay and current CERI change comparison | Explicit; original-context requests are not routed here. |
| `ORIGINAL_CONTEXT_RECONSTRUCTION` | Price revision PIT and CERI `AS_KNOWN` | Existing bounded reconstruction retained; generic original replay unsupported. |
| `LEGACY_CURRENT_READ` | pre-Phase-2 compatibility rows in current mode | Explicit legacy state only. |
| `OUT_OF_SCOPE` | acquisition replanning/config/readiness/publication concerns unrelated to reads | Carried forward. |
| `CONFIRMED_HISTORICAL_FALLBACK` | none after fixes | Zero unexplained material occurrences. |

## 7. Core readers

Fundamental, Technical, Combined, and Ranking share a narrow explicit boundary. Current mode
uses `CoreCalculationCurrentProjection`; historical mode uses exact evidence ID or Calculation
Identity. Kind, run/ticker, and Ranking profile scope are checked. Same run+ticker is never an
identity substitute. Missing and legacy pointers reject without running a calculation or
reading compatibility result values.

## 8. Regime/Sector readers

Explicit evidence APIs return exact T11B envelopes. Sector evidence retains exact Ranking,
Regime, and predecessor source IDs. Current dashboard/export history remains a compatibility
projection and is labeled accordingly. Resume resolves the manifest-addressed Regime/Sector
row by exact ID, including legitimate cross-run Regime evidence; it never searches newest
global/current context. A later revision-marker update is accepted only when restoring the
known marker fields reproduces the frozen semantic hash, while other mutation rejects.

## 9. CERI readers

Stored ticker history now requires `STORED_SNAPSHOT` plus `as_of`, follows each snapshot's
exact `CERI` evidence pointer, verifies run/ticker scope, and renders sealed `decision_output`.
Legacy-only history rejects. `AS_KNOWN` and `LATEST_CORRECTED` remain distinct in the existing
PIT query. Latest detail remains current. The generic change comparison is explicitly a
current-projection comparison and is not advertised as original-context reconstruction.

## 10. IBMI readers

The latest feature view is explicitly current. The new evidence reader/API accepts only an
immutable IBMI evidence ID and returns its exact constituent map. Ranking and CERI's existing
certified consumers continue to pin exact IBMI evidence; no live metric, shortability,
availability, or PriceBar lookup occurs when reading an existing immutable feature.

## 11. Setup/Lifecycle/Alert readers

Setup historical-run results require exact Setup/transition evidence and batch-load it. Sealed
payload values override matching compatibility presentation fields, and current canonical or
active episode selection is disabled in historical mode. Exact Setup, lifecycle evaluation,
lifecycle transition, and alert decision APIs were added. Current Setup timelines and episode
views are labeled projections. Alert notification status remains current, while rule, severity,
reason, confidence, blockers, and decision semantics come from the exact T11C decision/rule
evidence whenever certified.

## 12. Winner readers

Winner's frozen vector and versioned outcome/generation design is unchanged. Historical
prediction serialization no longer re-reads mutable `CombinedResult` or `RankingResult` rows;
it reads the exact `combined_evidence_id` and `ranking_evidence_id` frozen in
`source_ids_json`, validates scope, and reports an explicit unavailable/mismatch status when
legacy or invalid. The negative dependency graph remains unchanged.

## 13. Pipeline/resume readers

Resume previously validated counts and presence. It now requires the immutable Decision
Handoff, verifies its fingerprint and run/pipeline/context ownership, validates every frozen
raw/core/context member, requires certified pointers, compares mutable core compatibility
content to the pinned evidence payload, and returns the validated handoff fingerprint/evidence
count. A newer independent current pointer is ignored. If a unique core compatibility row was
rewritten so downstream stages could no longer consume E1, resume fails closed rather than
substitute E2. Cross-run Regime is resolved from the exact handoff ID.

## 14. Repair/replay/backfill semantics

Repair remains `CURRENT_STATE_REPAIR`. Replay remains
`CURRENT_RULES_RETROSPECTIVE`. `ReadMode.ORIGINAL_CONTEXT` returns
`ORIGINAL_CONTEXT_RECONSTRUCTION_UNSUPPORTED`; it cannot silently enter replay. Backfill,
refresh, rebuild, and recalculate are producer operations that may create new evidence, but no
historical reader calls them after an evidence miss or labels their output as original history.

## 15. Legacy behavior

Legacy current rows are retained for live compatibility and remain `LEGACY_CURRENT` or
`LEGACY_UNKNOWN`. Historical readers never promote them. A missing pointer yields
`LEGACY_EVIDENCE_UNAVAILABLE`; a missing/wrong immutable row yields evidence unavailable.
Mixed CERI history exposes the count of excluded legacy rows; legacy-only history rejects.

## 16. Prevented fallback matrix

| Consumer | Historical request type | Old fallback | New evidence source | If evidence missing | Current-mode behavior | Certification |
|---|---|---|---|---|---|---|
| Fundamental | identity / evidence ID | same run+ticker row | T11A `FUNDAMENTAL` | fail closed | current pointer | PASS |
| Technical | identity / evidence ID | same run+ticker/current score | T11A `TECHNICAL` | fail closed | current pointer | PASS |
| Combined | run-history / identity / ID | mutable `CombinedResult` | T11A `COMBINED` | fail closed | current pointer/run card | PASS |
| Ranking | identity / ID | current `RankingResult` | T11A `RANKING` + profile | fail closed | current pointer | PASS |
| Regime | evidence ID / frozen resume context | newest global/current revision | T11B `REGIME` / handoff exact ID | fail closed | labeled projection | PASS |
| Sector | evidence ID / frozen resume context | latest prior/current snapshot | T11B `SECTOR` source chain | fail closed | labeled projection | PASS |
| CERI | stored as-of history | current snapshot enrichment | T11B `CERI` decision output | fail closed / explicit legacy count | latest/current remains | PASS |
| IBMI | historical feature ID | latest live constituents | T11B `IBMI` envelope | fail closed | latest features remain | PASS |
| Setup | historical-run / evidence ID | canonical Setup pointer | T11C `SETUP` | fail closed | canonical projection | PASS |
| Lifecycle | evaluation/transition ID | mutable episode/event state | T11C lifecycle chain | fail closed | current episode | PASS |
| Alert | decision audit | current rule/event meaning | T11C decision + rule + predecessors | fail closed | notification status current | PASS |
| Winner | historical prediction | current Combined/Ranking display join | frozen vector + pinned evidence | explicit unavailable/mismatch | current serving pointer unaffected | PASS |
| Pipeline resume | frozen pipeline/context | counts/latest run context | Decision Handoff + exact evidence | fail closed | ordinary fresh execution unchanged | PASS |

## 17. Performance/query impact

Exact evidence-ID reads use primary keys; Calculation Identity and scope reads retain T11A/B
indexes. Generic Combined history performs one evidence-to-run query over indexed evidence
kind/identity/scope rather than an N+1. Setup historical pages and alert pages batch-load
evidence IDs. Resume performs bounded set queries for per-run core artifacts and primary-key
lookups for the small shared context set. No unbounded evidence scan or new N+1 was added.

## 18. Tests

| Lane | Result |
|---|---|
| T11D focused | 20 passed, 1 warning |
| Repository historical-reader matrix | 183 passed, 1 warning |
| T11A/B/C evidence regression | 32 passed, 5 skipped, 1 warning |
| Affected systems | 1,228 passed, 1 warning; post-edit Setup/focused rerun 60 passed, 1 warning |
| Phase-1 identity | 134 passed, 1 warning |
| Phase-0 temporal/domain/publication | 116 passed, 1 warning |
| Broad safe local lane | 2,571 passed, 128 skipped, 90 deselected, 23 warnings |

The five Phase-2 skips require disposable PostgreSQL. Focused warnings are the known
FastAPI/Starlette `httpx` test-client deprecation. Broad warnings are that warning, one Alembic
configuration deprecation, and 21 Python 3.12 SQLite datetime-adapter deprecations. The first
broad attempt found the newly added GET routes missing from the governed route inventory;
the inventory was regenerated, its exact governance test passed, and the complete broad lane
then passed. Negative-edge coverage remains in the Phase-1 certification test.

## 19. Finding reconciliation

| Finding / invariant | T11D historical-read status | Remaining non-T11D scope |
|---|---|---|
| `PIPE-005` | OPEN / OUT OF SCOPE | Stored market-data plan enforcement and acquisition replanning remain. |
| `PIPE-006` | CLOSED for resume historical evidence proof | Other proof-boundary claims remain separately scoped. |
| `PIPE-007` | CLOSED | Resume no longer selects latest context by upload run. |
| `CORE-006` | CLOSED for historical evidence | Compatible global Regime remains allowed for new/current calculations. |
| `CORE-007` | CLOSED for historical evidence | New-calculation predecessor compatibility remains as Phase-1 policy. |
| `RANK-006` | CLOSED for historical reads | Mutable compatibility projection remains current-only. |
| `CERI-002` | PARTIAL | Historical sealed decision/Price PIT preserved; mixed-basis policy remains. |
| `CERI-003` | CLOSED for historical reads | Explicit current/direct paths remain legitimate current operations. |
| `CERI-004` | PARTIAL | Stored decisions are sealed; current-rules change rebuild is not original replay. |
| `SETUP-004` | CLOSED for historical chain reads | Consecutive-session/rule semantics are unrelated and remain. |
| `SETUP-005` | CLOSED for historical reads | Direct current entry remains current-mode. |
| `SETUP-006` | PARTIAL | Replay remains current-rules retrospective; original reconstruction deferred. |
| `SETUP-007` | CLOSED for historical alert reads | Global configuration authority remains later work. |
| `SETUP-009` | CLOSED for historical reads | Mutable serving pointers intentionally remain. |
| `SETUP-010` | PARTIAL | Maintenance remains current-context activity. |
| `XINT-003` | CLOSED for Phase-2 historical reads | Current/legacy wall-clock concerns outside historical evidence remain. |
| `XINT-005` | CLOSED for major repository historical readers | Absolute all-code/direct-SQL governance is not claimed. |
| `XINT-006` | PARTIAL | Entry-point/config/replay equivalence beyond read enforcement remains. |
| `XINT-008` | CLOSED for material Phase-2 historical/prior selectors | Current operations and future subsystems remain separately scoped. |
| `INV-EVIDENCE-001` | ENFORCED for major repository decision paths | Full original-context replay is not required for this verdict. |
| `INV-TRUTH-001` | ENFORCED for Phase-2 evidence scope | Later truth remains free to advance through separate projections/versions. |
| `INV-FALLBACK-001` | ENFORCED for historical evidence reads | Configuration/current-mode fallbacks are not globally closed. |
| `INV-TIME-002` | ENFORCED for material historical selectors | Exact-ID reads need no recency selector; prior selectors remain upper bounded. |

## 20. Residual Phase-2 risks

- Full original-context replay/reconstruction is intentionally unsupported.
- PostgreSQL migration/runtime constraints still require T11E certification.
- Market-data acquisition replanning (`PIPE-005`), configuration authority, readiness
  propagation, refresh identity, and broader entry-point equivalence remain outside T11D.
- Existing legacy rows remain unavailable as history because no backfill was fabricated.
- Privileged direct SQL can bypass application-layer read contracts and remains an operational
  governance concern.

## 21. PostgreSQL certification debt

No safe disposable PostgreSQL credentials became available. Offline migration status remains
at the already verified T11C head; T11D adds no migration.

**POSTGRESQL PHASE-2 MIGRATION CERTIFICATION: DEFERRED TO T11E.**

## 22. Final verdict

**T11D PASS.** Historical, as-of, decision-time, and exact-identity reads across the major Phase-2 graph now use certified
immutable evidence or fail closed. Current views remain explicit and usable. There are zero
unexplained `CONFIRMED_HISTORICAL_FALLBACK` classifications in the audited material paths.
