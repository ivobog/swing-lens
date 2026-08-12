# SLSE Data Lineage and Field Mapping

## Current authoritative status (2026-08-12)

| Area | Original finding | First-pass disposition | Second-pass disposition | Current authoritative status |
|---|---|---|---|---|
| DEF-030 adapter inputs | Incomplete source proof | PARTIAL | Not revisited | **PASS** — `adapter_input_audit.py` is the executable catalog for every literal adapter signal and classification input. |
| DEF-031 prior history | Not supplied | PARTIAL | Typed bounded history supplied | **PASS** — temporal evidence consumes ordered prior canonical snapshots in every applicable family. |
| DEF-032 confidence lineage | Quality label conflated evidence | PARTIAL | Top-level formula corrected | **PASS** — freshness, run success, and lineage consistency are independent fields and scored independently. |
| DEF-033 actionability lineage | Branch order implicit | PARTIAL | Reduced posture separated | **PASS** — precedence and selected reason/blocker/metadata are persisted in decision evidence. |
| DEF-034 document status | Old and new claims conflicted | PARTIAL | Dated correction | **PASS** — this table is authoritative. |
| Public DTO/version/hash projection | Incomplete | PARTIAL | PARTIAL | **OPEN** — closure work continues; release remains FAIL. |

Behavior identity is engine `slse-1.2.0`, config `2026-08-12`, schema `slse-snapshot-1.0.0`. Engine is disabled and retained history is unchanged.

### Authoritative confidence derivation

- Final score: `round(100 × (0.30 coverage + 0.25 signal_agreement + 0.20 persistence + 0.15 freshness_and_lineage + 0.10 context_completeness))`.
- Signal agreement: configured weighted mean of `trend`, `contraction`, `relative_strength`, and `classification`; the current version assigns each 0.25.
- Freshness and lineage: configured weighted mean of `completed_bar_freshness`, `source_run_success`, and `lineage_consistency`; the current weights are 0.333333, 0.333333, and 0.333334.
- Lineage consistency requires explicit integrity evidence, engine/config/schema versions, configuration/source hashes, and consistent source identifiers. Missing evidence is never treated as success.
- Adapter confidence is not blended into the final score a second time.

### Authoritative adapter-input catalog

`app/services/setup_lifecycle/adapter_input_audit.py` records, for every adapter input, the adapter(s), signal key, business meaning, governing rule, persisted source entity/debug path and effective date, snapshot-builder mapping, stored JSON key, required/null behavior, point-in-time derivation, and prior-history use. `tests/setup_lifecycle/test_adapter_input_coverage.py` parses adapter source and fails if a literal signal/classification input is not both registered and cataloged, or if a prohibited fixture-only magic key is reintroduced.

## Conventions

- Effective date is the latest completed daily bar unless a context source explicitly carries an earlier `as_of_date`; context must never be newer than ticker data-as-of.
- Required lifecycle core for the reviewed v1 configuration is technical score, setup score, classification/family evidence, and close. Optional context remains null and may reduce confidence.
- `SC` = `setup_signal_snapshots`; `LE` = `setup_lifecycle_events`; `CE` = `signal_change_events`; `AE` = `signal_alert_events`; `EP` = `setup_lifecycle_episodes`.
- Every missing numeric value displays as `—` and remains JSON `null`/blank CSV; it is never displayed or exported as zero.

| Canonical field | Business meaning; source entity/path; effective date; requirement; type/unit; null semantics | Normalization; snapshot/signals; derived rule; favorable direction/materiality | Confidence/actionability/lifecycle/event/alert use | DB → query/API → GUI → CSV/JSON | Filter/sort/display/tests |
|---|---|---|---|---|---|
| ticker | Security identity; RawCompanyRow.ticker; run/date; required; uppercase text; null invalid | trim/uppercase; SC.ticker | Episode key and every event/alert identity | SC/EP/LE/CE/AE → `ticker` → both screens → both exports | exact/case-normalized filter; ticker sort optional; link; source-loader/DTO/export tests |
| company | Company name; RawCompanyRow.company_name then CombinedResult.company_name; run/date; optional text | first non-null; SC.company_name | Display only | SC → Market DTO `company` → Market Changes → CSV/JSON | text display; lineage test |
| sector | Canonical sector then raw/combined sector; run/date; optional text | canonical first; SC.sector | Sector context lookup | SC → Market DTO `sector` → Market Changes → CSV/JSON | exact filter; display; source/route tests |
| data_as_of_date | Latest completed ticker bar date; PriceBar.bar_date; required date | completed daily bar; SC.data_as_of_date | Event effective-date authority | SC/LE/CE/AE → `data_as_of_date`/`effective_date` → both screens → exports | selected-date filter, descending default; ISO date; point-in-time tests |
| comparison_date | Previous canonical ticker/day | repository previous canonical date; nullable on first observation | Delta/velocity reference | derived DTO → Market Changes badge/row → exports | display and missing-session-gap tests |
| source_run_id | Upstream run lineage; RawCompanyRow.run_id/UploadRun.id; required for live capture | SC.run_id plus immutable text | Audit only | SC → DTO `source_run_id` → source link → exports | optional run filter; ID link tests |
| snapshot_id / previous_snapshot_id | Exact current/prior canonical evidence IDs | SC.id; CE snapshot FKs; prior nullable | Audit and source linking | SC/CE/LE → DTO IDs → evidence/source links → exports | deterministic link tests |
| technical_score | Technical/dual setup strength; TechnicalScore.dual_score then CombinedResult.dual_score; required; points | SC.dual_score; signals.technical_score; higher better; abs 0.5, 10%, crossings 7/7.5/8 | Required coverage, family/confidence, score acceleration | SC/CE → current/previous/delta/velocities → Market Changes/alerts → exports | range filter; current-score/velocity sort; 2 decimals; mapping/velocity tests |
| setup_score | Setup readiness evidence; TechnicalScore.setup_score; required; points | SC.setup_score; signals.setup_score; higher better; abs 0.5, crossings 5.5/7.5 | Family state/confidence; score acceleration permitted by SRS | SC/CE → explicit values → Market Changes → exports | range/sort; 2 decimals; adapter/change tests |
| fundamental_score | Fundamental context; FundamentalScore.fundamental_score then CombinedResult; optional points | SC.fundamental_score; null warning only if source missing | Context/explainability, not hard gate unless configured | SC → evidence/timeline/export | display; null tests |
| final/profile score and profile rank | Combined/ranking context; CombinedResult.final_score, RankingResult.profile_score/profile_rank; optional | SC.final_score/profile_score; rank must be lower-is-better | Primary/context and future filters | SC/signals → DTO/evidence → exports | numeric/rank formatting; lineage tests |
| classification | Technical setup label; TechnicalScore.classification then CombinedResult; required enum/text | SC.technical_classification; signals.classification; material on change | Family selection/state evidence | SC/CE → DTO/reasons → Market/evidence/export | exact filter if exposed; enum change tests |
| stage | Technical stage; TechnicalScore.stage; optional enum | SC.stage; signals.stage; material on change | Phase evidence | SC/CE → DTO → evidence/export | display; enum test |
| close | Completed close; PriceBar.close then raw close fields; required price | SC.close_price; signals.close_price; never raw zero; high/low neutral | Required coverage, trigger/failure/freshness | SC → current evidence → Market/detail/export | price formatting; full-coverage/missing-close tests |
| high | Completed high; PriceBar.high then raw; optional price | SC.high_price; diagnostic high-cross only | Never trigger authority | SC.diagnostic JSON → evidence only | diagnostic label; close-vs-high test |
| pivot | Pivot/reference; raw pivot fields (or future normalized technical field); family-required when applicable | SC.pivot_price; distance derived; null prevents pivot transition | Breakout/VCP readiness | SC → DTO → Market/detail/export | price format; missing pivot family test |
| trigger | Trigger; raw trigger, fallback pivot; family-required when applicable | SC.trigger_price; close cross derived; completed close authoritative | TRIGGERED transitions/alerts | SC → DTO/evidence → Market/detail/export | price format; direct trigger tests |
| stop | Suggested invalidation; raw stop then TechnicalScore.suggested_stop; optional price | SC.stop_price | Failure/risk evidence when adapter supports | SC → evidence/export | price display; lineage test |
| target | Suggested objective; raw target then TechnicalScore.suggested_target; optional price | SC.target_price | Extension/reward context | SC → evidence/export | price display; lineage test |
| distance_to_pivot_pct | `(close-pivot)/pivot*100`; current date; nullable if close/pivot absent; percent | SC.distance_to_pivot_pct; signals key; lower better before trigger; abs .75, crossings 5/3/2/1/0 | READY hysteresis and phase | SC/CE → DTO current/previous/delta → Market Changes/export | range filter; trigger-distance sort; signed percent; boundary tests |
| close_above_trigger | Completed close >= trigger; nullable boolean | SC.close_above_trigger; signals.close_trigger_cross; true better/material | Trigger authority | SC/CE/LE → reason/evidence → Market/detail/alert/export | boolean label; close-only trigger tests |
| relative_strength | Leadership score; TechnicalScore.relative_strength_score; optional points | signals.relative_strength; higher better; abs .5/crossings 5.5/7 | Agreement/change event | CE → DTO delta → Market/export | delta filter/sort if exposed; change tests |
| sector_rank | SectorRotationRow.current_rank selected as-of; optional integer rank | signals.sector_rank; lower rank better; normalized and rank delta = old-new; material 3 places | Context/confidence; sector acceleration | SC/CE → current/previous/delta → Market/alerts/exports | range/sort; integer; 9→5=+4 tests |
| sector_rank_delta | Previous rank minus current rank | CE.rank_delta/normalized_delta; positive improvement | Sector acceleration threshold | CE → DTO → Market/alert/export | improvement filter/sort; signed integer; regression test |
| technical_score_delta | Current technical score minus prior | CE.delta_numeric/normalized_delta | Material change/alert evidence | CE → DTO → Market/export | signed 2 decimals; change test |
| technical_score_velocity_1/3/5/10d | Current score minus score N sessions back | CE evidence velocity by window; nullable until history exists; higher better | 3-session acceleration | CE → explicit DTO fields → Market/alert/export | velocity filters/sorts; signed; exact-window tests |
| setup_score_velocity_1/3/5/10d | Same for setup score | CE evidence; higher better | Score acceleration when configured | CE → explicit DTO → Market/export | same semantics/tests |
| market_regime | MarketRegimeSnapshot.regime as-of ticker date; optional enum | signals.market_regime; material enum; neutral change direction | Context completeness and market gate | SC/CE → DTO → both screens/export | exact filter; badge; point-in-time/gate tests |
| market_gate | Market policy permission; MarketRegimeSnapshot policy/gate field or normalized regime mapping; optional boolean/enum | explicit normalized signal required; no silent inference from display label | Actionability blocker only; state unchanged | SC/LE/AE → blocker/source fields → both/export | Gate Blocked filter; truth-table tests |
| earnings_risk | CombinedResult.earnings_risk_level then raw; optional enum | signals.earnings_risk; lower risk better; enum material | Hard blocker in configured window; state unchanged | SC/CE/LE/AE → DTO → both/export | filter/display; AC-04 test |
| liquidity_risk | FundamentalScore.liquidity_risk_score normalized to risk flag; optional boolean | signals.liquidity; false better; material | Hard blocker | SC/CE/LE/AE → DTO/blockers → both/export | filter/display; liquidity fixture |
| required_feature_coverage | Fraction of canonical required features present; required ratio | SC.required_feature_coverage; computed from technical/setup/classification/close; no optional fields | 30% confidence component; low coverage may lower confidence/block only per policy | SC → DTO → Market/alert evidence/export | range filter/sort; percent display; exact 1.0 populated test |
| freshness | Bar age relative to run/session: FRESH/NEAR_STALE/STALE | SC.freshness_status and warnings; session-aware target | 15% confidence component; staleness alone LOW_CONFIDENCE per recommended resolution | SC/CE/LE/AE → DTO → both/export | exact filter; badge; fresh/stale crossing tests |
| data_quality_label | HIGH/NORMAL/LOW/INSUFFICIENT | SC.data_quality_label; derived from required coverage, freshness, context | Confidence/actionability and DATA_DEGRADED | SC/CE/LE/AE → DTO → both/export | exact filter/sort if offered; badge; threshold tests |
| confidence components | Coverage/agreement/persistence/freshness/context | lifecycle decision evidence.confidence_components; 0..1 each | Produce final score | LE evidence + AE source confidence → explicit DTO/evidence/export | expansion; formula test |
| confidence_score/label | Explainability/data-quality 0–100; labels >=85/70/50 | SC/EP/LE; no fabricated default | Alert minimum confidence and actionability | DB → `confidence`/`confidence_label` → both/export | range filter/sort; badge; boundary tests |
| setup_family | BREAKOUT/PULLBACK/VCP/CONTINUATION/GENERIC | SC candidate, EP/LE authoritative | Episode key, family adapter/rule restriction | SC/EP/LE → DTO → Market/detail/export | exact filter; configured order; adapter tests |
| phase | Family-specific stage | SC.primary_phase, EP.current_phase, LE phases | Transition explanation | DB → DTO → Market/detail/export | display; adapter tests |
| lifecycle current/previous/transition | Common lifecycle progression | EP current; LE from/to; signal-only rows use snapshot candidates with no fabricated transition | State machine and lifecycle alerts | EP/LE/SC → explicit DTO → Market/Alert/export | state/transition filter; transition-priority sort; truth tables |
| state_age | Completed sessions continuously in state | EP.state_age_sessions; LE.state_age_before | Persistence/expiry/display | EP/LE → DTO → Market/export | range/sort; integer sessions; calendar tests |
| actionability | ACTIONABLE/WATCH_ONLY/BLOCKED/LOW_CONFIDENCE | EP/LE and snapshot candidate; independent policy | Alert GATE_BLOCKED predecessor truth table | EP/LE/AE → DTO → both/export | exact filter; badge; policy/alert tests |
| blockers | Stable hard-gate reason codes | EP.metadata_json / LE evidence / AE evidence; list | Explain blocked permission | DB JSONB → explicit DTO list → both/export | blocker filter; badges; gate fixtures |
| lifecycle reasons | Stable state/phase reason codes | LE.reason_codes_json | Explain transition | LE → DTO `reason_codes` → both/detail/export | display/expansion; state tests |
| signal-change reasons | Stable materiality/crossing reason codes | CE.reason_codes_json | Explain change/alert | CE → DTO → Market/Alert/export | display/expansion; detector tests |
| alert_type | Built-in rule ID | SignalAlertRule.rule_id; required enum | Alert semantics | rule join → `alert_type` → Alert Center/CSV/JSON | exact filter; default priority; DTO/UI parity tests |
| severity | INFO/NOTABLE/ACTIONABLE/RISK | rule/event severity | Visual/review priority | AE.severity → `severity` → Alert Center/export | exact filter; CASE priority; canonical enum test |
| review_status | UNREAD/ACKNOWLEDGED/DISMISSED | AE.status/timestamps | User workflow only | AE → `review_status` → Alert Center/export | exact filter; full-scope counts; mutation tests |
| source_type | LIFECYCLE_EVENT/SIGNAL_CHANGE_EVENT/ACTIONABILITY_CHANGE/DATA_QUALITY_CHANGE | derived from non-null source FKs and rule scope | Traceability | AE joins → `source_type` → Alert Center/export | exact filter; label; source-link tests |
| lifecycle_event_id / signal_change_event_id / source_event_key / evaluation_run_id | Exact source identity/version | AE FKs/keys | Idempotency/audit | AE → explicit fields/source URL → Alert Center/export | deterministic link tests |
| latest_reason / warnings | Top stable reason plus complete warnings | LE/CE reasons and SC warnings | Explainability/quality | DTO → Market Changes → exports | reason/warning filters; truncated display + expansion; parity tests |

## Known lineage gaps requiring repair

The repaired path now satisfies `close_price` coverage, confidence coverage, per-ticker context cutoff, Market Changes source union, joined Alert DTO, rank direction, explicit displayed velocity, full-scope counts, source IDs and v2 exports. Remaining gaps are confidence-component/version/hash projection on list DTOs, typed prior-snapshot history in family adapters, complete version/status labels, and the unfinished golden/performance/accessibility gates. These are tracked in `SLSE_Implementation_Audit.md`.

## Second-pass lineage corrections (2026-08-11)

- `confidence_score`: final value is exclusively `round(100 * sum(component * configured_weight))`; family adapter confidence is not a second-stage input. Family evidence strength is represented once inside `signal_agreement`.
- `freshness`: age is `us_trading_sessions_between(data_as_of_date, reference_date)`. Saturdays, Sundays, and NYSE holidays do not increment age.
- `actionability_metadata`: reduced market posture persists `{"market_posture": "REDUCED"}` with reason `MARKET_POLICY_REDUCED`; it does not alter confidence or lifecycle state.
- `previous_snapshots`: ordered typed canonical DTOs strictly before the current data-as-of date, bounded to `episodes.history_window_sessions=10`. They are batch-loaded per evaluation and supplied to every family adapter.
- `terminal_locked`: separate invariant flag. It does not imply confidence 100. Terminal confidence comes from the prior episode evidence when available.

The prior statement that typed history remained a gap is superseded. Remaining lineage gaps are list-level confidence components/version hashes and incomplete record/version labels.
