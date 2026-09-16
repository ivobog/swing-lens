# T12A — Readiness Contract and Typed Eligibility Foundation

## 1. Executive verdict

**T12A VERDICT: PASS.** T12A establishes the producer readiness foundation and a separate typed consumer eligibility
interface. Consumer enforcement remains deliberately partial. Numeric outputs, scores, ranks,
trading states, CERI posture and Winner eligibility are retained. All final verification lanes pass;
no final failure or skip remains. Full Phase-3 consumer enforcement is not certified by this task.

## 2. Baseline

| Recorded before modification | Value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Initial branch | `codex/t11e-phase2-immutable-evidence-certification` |
| Initial HEAD / certified Phase-2 baseline | `7b015124807b8f895911d7e790fcbf01949ff85f` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Git status / tracked changes / untracked files | Clean / none / none |
| Alembic head | `0079_setup_lifecycle_alert_ev`, one head |
| Python / repository venv | CPython 3.12.2 / CPython 3.12.2 |
| T12A branch | `codex/t12a-readiness-contract-foundation` |

Baseline is exact, not an inferred legitimately advanced checkout. A detached disposable
baseline worktree was used for comparison. No unrelated work was overwritten. The final
focused commit is identified in the final response; a commit cannot embed its own SHA.

Authoritative references read: original registry and synthesis, Phase-1 and Phase-2 certified
architecture, and T11E certification. Original audit documents were not rewritten.

## 3. Phase-3 scope

`INV-READINESS-001` foundation: numeric output cannot certify readiness. T12A adds immutable
metadata and interfaces, without moving every consumer to eligibility policies. Core Technical
edges are for T12B; contextual IBMI/Regime/Sector edges for T12C; Winner for T12D; integrated
certification for T12E. There are no invented business thresholds or scoring changes.

## 4. Existing readiness inventory

The native contracts were inspected before choosing normalization semantics. Freshness absent
from a producer is not inferred from a current timestamp. Confidence labels and scores retain
their own domains. Legacy behavior in every row means absent frozen metadata -> LEGACY_UNKNOWN.

| Producer | Existing readiness signals | Common status mapping | Blocking reasons | Warnings | Native confidence | Coverage | Freshness | Legacy behavior | Current consumers | Consumer enforcement status |
|---|---|---|---|---|---|---|---|---|---|---|
| Fundamental | missing penalties, V2 flags, debug coverage/parse diagnostics; no intrinsic hard coverage rejection | recognized coverage -> READY; native caveats -> DEGRADED; absent signals -> UNKNOWN | none invented | sparse data, missing penalty, original flags | no universal confidence field | score /10, ratio and priority counts retained | no dedicated freshness contract | LEGACY_UNKNOWN | Combined, Ranking, Sector, Setup metadata, Winner | PARTIALLY_ENFORCED |
| Technical | insufficient_data, missing_data_json, required history, error/low confidence, warning flags, quality score | insufficient -> INSUFFICIENT_EVIDENCE; error -> ERROR; low -> DEGRADED; explicit sufficiency + normal/high -> READY | TECH_INSUFFICIENT_HISTORY, TECH_ERROR | native low confidence and original warnings | low/normal/high/error and quality /10 | native required-history/missing-context state | input session retained; no new age cutoff | LEGACY_UNKNOWN | Combined, Ranking, direct Setup/Lifecycle, Winner | PARTIALLY_ENFORCED |
| Combined | is_complete, has_fundamental/technical, warnings; numeric availability defines completeness | incomplete -> INSUFFICIENT_EVIDENCE; complete -> READY/DEGRADED | COMBINED_INCOMPLETE | existing warning list | no comparable scalar | native completeness | identity cutoff/session only | LEGACY_UNKNOWN | Ranking-related serving, Sector, Setup earnings risk/metadata, Winner | PARTIALLY_ENFORCED |
| Ranking | is_complete, warnings, post-score low-confidence label, gates/penalties; profile identity | incomplete -> INSUFFICIENT_EVIDENCE; complete -> READY/DEGRADED; absent completeness -> UNKNOWN | RANKING_INCOMPLETE | original warnings | producer does not publish universal confidence | completeness only | identity anchor; IBMI debug coverage separate | LEGACY_UNKNOWN | Sector, Setup metadata, Winner/cohorts | PARTIALLY_ENFORCED |
| Regime | native confidence, bounded source/session information, stale warnings, gate and risk state | normal/high -> READY; low -> DEGRADED; emitted stale -> STALE | REGIME_STALE_INPUT | original benchmark/context warnings | native normal/low | source availability in input/index metadata | native source dates and stale policy warnings | LEGACY_UNKNOWN | Sector, Setup, CERI alignment, Winner | PARTIALLY_ENFORCED |
| Sector | per-sector confidence, source/prior quality, warnings, universe counts | normal/high rows -> READY; low/insufficient rows -> DEGRADED snapshot; empty/unrecognized -> UNKNOWN | no global row-confidence rejection invented | SECTOR_INCOMPLETE_CONTEXT and original warnings | each sector's native label | sector/ticker counts, native debug | source-session metadata; no new threshold | LEGACY_UNKNOWN | Setup, CERI alignment, Winner | PARTIALLY_ENFORCED |
| CERI | native data confidence, available-weight/rated policy, coverage, confidence gates/caps, timestamp/conflict subscores, warnings | native insufficient/unrated -> INSUFFICIENT_EVIDENCE; low -> DEGRADED; native feed stale -> STALE; supported -> READY/DEGRADED | insufficient confidence/weight, CERI_STALE_SOURCE | original feed/analyst/timestamp/conflict caveats | native label + score /10 and ledger | revision % and opportunity ledger/available weight | PROVIDER_FEED_FRESHNESS semantic, age, existing limit/status | LEGACY_UNKNOWN | CERI presentation, changes and alerts; excluded from core/Winner graph | PARTIALLY_ENFORCED |
| IBMI | confidence, coverage_status, freshness_status, components/reasons/warnings; unavailable/failed states | failed -> ERROR; unavailable/subscription/not-supported -> INSUFFICIENT_EVIDENCE; stale -> STALE; low -> DEGRADED; unknown quality -> UNKNOWN | IBMI_ERROR, IBMI_UNAVAILABLE, IBMI_STALE_SOURCE, insufficient confidence | native low confidence and warnings | HIGH/NORMAL/LOW/INSUFFICIENT | availability enum, native components retained | native availability/freshness enum | LEGACY_UNKNOWN | liquidity -> Ranking; volatility/short pressure -> CERI; own dashboard | PARTIALLY_ENFORCED |
| Setup | own confidence/quality labels, required feature coverage, promoted Technical confidence, FRESH/NEAR_STALE/STALE, warnings | own confidence and quality drive READY/DEGRADED/INSUFFICIENT; stale blocks; near-stale warns | insufficient native confidence/quality, SETUP_STALE_SOURCE | native low quality and context warnings | own score /100 + label; inherited Technical preserved separately | required_feature_coverage ratio | existing Setup trading-session freshness policy | LEGACY_UNKNOWN | Lifecycle, alerts, query/export | PARTIALLY_ENFORCED |
| Lifecycle | evaluation confidence/components, missing-observation metadata, native decision reasons; trading states separate | own confidence -> READY/DEGRADED/INSUFFICIENT; trading READY alone or gap evaluation without quality certification -> UNKNOWN | insufficient native confidence | native snapshot warnings, LIFECYCLE_OBSERVATION_GAP | own score /100 and label/components | inherited evidence; native gap counters/threshold, no fake scalar | exact evaluation session/cutoff | LEGACY_UNKNOWN | transition chain, alerts, outcome bridge, serving | PARTIALLY_ENFORCED |
| Winner | explicit technical/Combined exclusions, frozen quality features, missing-context warnings, cohort/model support and temporal/training decisions | own probability readiness policy absent -> UNKNOWN; upstream metrics recorded | Winner exclusions are not universal producer blockers | original missing-context/native-quality caveats | technical_data_quality retained; model support remains domain-specific | fundamental coverage retained; probability similarity/support stays native | frozen source cutoff; separate temporal validity decisions | LEGACY_UNKNOWN | estimates, cohorts/training, exports/publication | PARTIALLY_ENFORCED |

Error states not listed as explicit producer errors are not manufactured from null scores or
free-text explanation. Lifecycle failures/expired setups are business outcomes, not ERROR readiness.

## 5. Producer readiness vs consumer eligibility

Producer readiness describes intrinsic evidence quality at creation. Consumer eligibility
describes permission to influence a named consumer under that consumer's versioned policy.
Low native confidence can be a producer warning while consumers later choose penalty or rejection.
The two contracts are distinct enums/dataclasses; no global `eligible` producer flag is added.

## 6. Typed readiness model

`ProducerReadinessEnvelope`, `NativeReadinessMetrics`, `ReadinessStatus`, `ReadinessReason` and
normalization/read functions live in `app/services/producer_readiness.py`. Frozen nested JSON
storage prevents caller mutation through decoded views. DTO and canonical round trips are tested.
Supported statuses require Calculation Identity binding; mutable metric objects are rejected.

## 7. Readiness statuses

READY, DEGRADED, INSUFFICIENT_EVIDENCE, STALE, ERROR, UNKNOWN and LEGACY_UNKNOWN are retained,
without forcing every domain to emit each. ERROR/INSUFFICIENT dominate STALE while retaining all
blocking reasons. UNKNOWN never silently becomes READY. Plain Enum prevents trading-state equality.

## 8. Reason-code model

Typed codes cover only observed native sufficiency, completeness, failed/unavailable coverage,
confidence, staleness, sparse Fundamental data, Sector caveats and original warnings. Blocking and
warning tuples sort by code value and deduplicate. `PRODUCER_WARNING` retains exact native
codes/text in signals; it is not a new hard business threshold. The architecture document lists
reason semantics. Blocking tuples cannot certify READY/DEGRADED.

## 9. Native confidence/coverage/freshness semantics

Technical quality /10, CERI confidence /10, Fundamental coverage /10 and ratio, Setup/Lifecycle
confidence /100, IBMI availability labels and Sector labels are not made mathematically
comparable. CERI feed freshness retains its provider-feed meaning rather than being renamed
estimate-revision age. Native existing limits remain recorded; absent metrics/anchors are empty
objects/null, not invented measurements. CERI opportunity/confidence/timestamp/conflict ledgers
and Technical history/missing flags survive unchanged.

## 10. Legacy semantics

Unpointed current rows and pre-T12A evidence without an envelope return LEGACY_UNKNOWN.
No old row is re-normalized, updated or backfilled. A numeric legacy score remains usable only
under unchanged existing current/legacy consumer behavior, not certified by the new contract.
The default policy never implicitly grants permission.

## 11. Immutable evidence integration

`persist_core_evidence` owns the reserved payload member before payload/evidence hashing.
Ordinary and observation-gap lifecycle evaluations seal the envelope before hashing their native payloads. Winner adds it to
initial frozen lineage with Calculation Identity; selective ORM guards prohibit readiness
replacement/removal and prediction deletion while allowing operational lineage updates.
Legacy Winner ORM updates cannot introduce retrospective readiness certification.

Different sufficiency or policy means distinct evidence even for identical numerics. Exact new
retries reuse evidence. Earlier payloads survive projection movement and session reload. Address
assignment is outside readiness semantic hashing; calculation identity, versions and anchors are
inside. No independent mutable readiness column is introduced. Core projection readers and
lifecycle episode readers follow exact evidence pointers; Winner reads its own frozen lineage.

## 12. Consumer eligibility contract

`ConsumerEligibilityPolicy` accepts readiness, consumer and immutable native config.
`ConsumerEligibilityDecision` records typed ELIGIBLE/INELIGIBLE/POLICY_UNDECIDED, typed reasons,
policy version, exact readiness fingerprint and producer evidence address. The placeholder
returns POLICY_UNDECIDED for every input, explicitly identifying unknown/legacy states. Future
consumer policies extend domain-supported typed reasons. No business consumer invokes it yet.

## 13. Producer-by-producer mappings

### Fundamental

Sources: `fundamental_coverage_service`, `fundamental_ranker_v2.score_row_v2`,
`fundamental_warning_service.build_warning_flags_v2`, `upload_service` persistence. Coverage
supports its current producer contract; existing sparse flags and penalties degrade. No hard gate.

### Technical

Sources: `technical_confidence.build_data_readiness`, `technical_score_service._v4_persistence_fields`
and error persistence, `TechnicalScore` native columns. Positive numerics plus explicit insufficient
history block readiness; low confidence alone degrades. Missing-history details remain native.

### Combined

`confidence_service.build_combined_warning_flags` defines completeness from numeric availability;
`combined_decision.combine_row_decision` keeps its scoring/capping logic. Complete output can still
consume an insufficient Technical input: this remains a T12B edge defect, not silently fixed here.

### Ranking

`ranking_profile_engine.rank_single_row`, `ranking_profile_gates.apply_profile_gates` and persisted
`RankingResult` completeness/warnings. Normalization, low-confidence label caps and rows remain.

### Regime

`market_regime.classify_market_regime`, command-center source freshness and
`market_regime_policy.MarketRegimePolicyService.policy_for`. Native confidence and already-emitted
stale warnings normalize; source/session and existing gate decisions remain as recorded.

### Sector

`sector_universe_service._sector_confidence`, `sector_rotation_policy.decide_sector_rotation`,
`SectorRotationRepository._persist_evidence` freezes snapshot rows. Readiness retains per-sector
labels/warnings in canonical row order; aggregate degradation does not suppress valid sectors.

### CERI

`CeriOpportunityScoreService.calculate`, `CeriConfidenceService.calculate` and
`CeriSnapshotService.build_snapshot` ledgers freeze existing rated/coverage/confidence/feed-freshness states.
`persist_ceri_decision_evidence` uses the shared writer. Scores, posture and native confidence unchanged.

### IBMI

`ib_market_intelligence.calculations` produces native confidence/availability/freshness. The
constituent evidence writer seals the envelope with existing exact sources. No overlay consumer changes.

### Setup

`SetupLifecycleSnapshotBuilder` promotes native coverage, confidence and freshness; `persist_setup_evidence`
uses the shared writer. Its own confidence label takes precedence over inherited Technical label.
Existing STALE/NEAR_STALE states are recorded without changing actionability or transitions.

### Lifecycle

`SetupLifecycleConfidenceService`, `SetupLifecycleEngine.evaluate` and
`persist_lifecycle_evaluation_evidence`. Readiness is anchored to exact Setup identity, own evaluation
confidence/components and native warnings. Trading output state is excluded from normalization.
Observation-gap evaluations freeze native counters and the existing threshold, with UNKNOWN own
quality and a typed gap warning; they cannot certify a trading READY state as evidence-ready.

### Winner

`WinnerFeatureExtractor.extract` exclusions and `_canonical_feature_json` quality features remain
unchanged. `_build_prediction_snapshot` freezes native quality plus UNKNOWN own producer readiness
when identity exists. Unidentified/legacy predictions remain LEGACY_UNKNOWN. T12D owns a complete
probability-evidence producer/consumer policy; Winner exclusions are not imposed on every producer.

## 14. Consumer-site inventory

Static searches cover insufficiency, confidence, coverage, freshness/stale, warnings, completeness,
error, numeric-only availability, latest selectors, eligibility and ready/readiness. The initial
services search produced 5,555 matching lines; raw search logs are disposable QA artifacts, not an
assertion that every textual occurrence is a decision edge. Material sites are classified below.
Exact function names and source line anchors are recorded for the baseline/current files.

| Classification | Exact file/function location | Existing behavior / deferred gap |
|---|---|---|
| T12B_CORE_CONSUMER | `app/services/confidence_service.py:build_combined_warning_flags` (52); `combined_decision.py:combine_row_decision` (239), `_weighted_available_score` (391) | numeric availability defines completeness; weighted score uses positive insufficient Technical data |
| T12B_CORE_CONSUMER | `app/services/ranking_profile_engine.py:rank_profile` (55), `rank_single_row` (82), `calculate_profile_score` (201) | Technical numerics enter population/component normalization and scores |
| T12B_CORE_CONSUMER | `app/services/ranking_profile_gates.py:apply_profile_gates` (41), `_low_data_quality` (158) | label capped after score; row/rank retained, not full eligibility |
| T12B_CORE_CONSUMER | `app/services/setup_lifecycle/source_loader.py:build_run_source_context` (479), `_compatible_ticker_artifacts` (781); `snapshot_builder.py:_source_values` (434) | identity-safe direct Technical source still influences Setup without non-bypassable producer readiness |
| T12B_CORE_CONSUMER | `app/services/setup_lifecycle/family_adapters.py:evaluate_family_candidates` (25), `signal_number` (114); `breakout_adapter.py:evaluate` (28), `pullback_adapter.py:evaluate` (27), `vcp_adapter.py:evaluate`, `continuation_adapter.py:evaluate`, `generic_adapter.py:evaluate` (24); `lifecycle_engine.py:evaluate` (61) | direct Setup Technical-derived numerics drive family proposals and lifecycle states |
| T12C_CONTEXTUAL_CONSUMER | `app/services/ranking_profile_engine.py:_tradeability_penalty` (327) | IBMI AVAILABLE coverage enables liquidity penalty; complete confidence/freshness policy deferred |
| T12C_CONTEXTUAL_CONSUMER | `app/services/ceri/capture_service.py:_point_in_time_volatility_feature` (1025), `_point_in_time_short_pressure_feature` (1096) | exact identity/PIT/certified sources + AVAILABLE coverage, incomplete native quality gating |
| T12C_CONTEXTUAL_CONSUMER | `app/services/setup_lifecycle/snapshot_builder.py:_source_values` (434), `_promoted_fields` (342); `source_loader.py:_select_compatible_context_candidate` (747) | context identity enforced; label/gate/rank/confidence influence Setup, producer quality policy partial |
| T12C_CONTEXTUAL_CONSUMER | `app/services/sector_universe_service.py` accumulator `add` (183), `_add_technical` (241); `sector_rotation_policy.py` scoring (127–150) | numeric upstream aggregation and existing sector confidence policy; additional propagation candidate requiring later scope decision |
| T12C_CONTEXTUAL_CONSUMER | `app/services/ceri/capture_service.py:_alignment_context` (823) | bounded Combined/Regime/Sector alignment metadata; producer readiness propagation remains future policy |
| T12D_WINNER_CROSS_CONSUMER | `app/services/winner_probability/feature_extractor.py:extract` (89), `_canonical_feature_json` (281) | Technical insufficiency and Combined incompleteness hard-rejected; low/error confidence and optional Ranking/Regime/Sector quality partial |
| T12D_WINNER_CROSS_CONSUMER | `app/services/winner_probability/calculation_identity.py` source acquisition/compatibility policies; `capture_service.py:_capture_ticker` (346) | exact identity/evidence acquisition established, shared readiness eligibility not yet evaluated |
| ALREADY_SAFE | `app/services/winner_probability/feature_extractor.py:extract` explicit checks (150–161) | explicit Technical insufficiency / missing or incomplete Combined exclusion only; does not close all Winner quality |
| ALREADY_SAFE | `app/services/winner_probability/training_eligibility.py:persisted_capture_decision` (99); `temporal_eligibility.py` explicit certification predicates | separate explicit training/temporal certification; not universal producer readiness |
| ALREADY_SAFE | `app/services/ceri/opportunity_score_service.py:calculate` available-weight gate (127–149); `confidence_service.py:calculate` native zero-usable gate; `snapshot_service.py:derive_posture` (230) | existing unrated and insufficient confidence policy prevents rated posture |
| ALREADY_SAFE | `app/services/setup_lifecycle/actionability_policy.py:evaluate` (17); `lifecycle_engine.py:_actionability` (236) | existing own confidence/state blockers, only partial protection against upstream unready numerics |
| ALREADY_SAFE | `app/services/core_calculation_evidence.py:get_current_readiness`, `get_readiness_for_row`; `producer_readiness.py:readiness_from_evidence` | frozen readiness or explicit legacy/unknown; no numeric readiness fallback |
| OUT_OF_SCOPE | `app/services/fundamental_components_v2.py`, `technical_indicators.py`, `ranking_profile_components.py` numeric component helpers | arithmetic/native scoring primitives; no global readiness policy added |
| OUT_OF_SCOPE | `app/services/score_card_view_service.py`, `export_service.py`, `ranking_result_export.py`; serving router/template score displays | presentation may display retained numbers; no consumer decision enforcement claimed |
| OUT_OF_SCOPE | `app/services/historical_read_service.py` and evidence selectors; `pipeline_service.py`/handoff readers | temporal/identity/evidence readiness is a different contract; Phase-1/2 source safety retained |

Where one function has both a safe hard gate and an incomplete confidence policy it appears in
both classifications with the protected condition explicitly bounded. Consumer inventories do
not claim every optional-source or numeric primitive is a readiness defect.

## 15. Behavior-preservation evidence

The same ten unchanged test modules were initially run in the detached certified baseline and T12A checkout:
Combined decisions; Ranking engine and golden profiles; Setup snapshot builder, lifecycle engine
and actionability; CERI scoring and acceptance; Winner capture and acceptance. Both runs pass
124 tests against unchanged business-result assertions. This is fixture-scoped comparison, not a
claim of an exhaustive production-output replay. Focused mapping tests also prove input payloads
and retained positive scores do not mutate. Only metadata/evidence fingerprints/addresses change.

An additional opt-in probe (`scripts/qa/t12a_behavior_probe.py`) captures actual selected business
outputs while returning original result objects unchanged. Eleven identical test modules (adding
Winner probability estimator) pass 131 tests in each checkout. All **219 captured results match
exactly**, with canonical fingerprint
`9f0d54a8c7de714fbbb30c77ff7bb872ab8dbe56f930fd54fa7f3104d6729723` in both:
29 Combined, 8 Ranking population results, 20 Setup, 89 Lifecycle, 29 Actionability, 5 CERI,
16 Winner feature decisions, 13 frozen Winner predictions and 10 Winner estimates, including
native point/posterior probabilities where returned. Identity/address metadata is excluded from
this business comparison. The exact probe is copied to the baseline solely as disposable QA code.

## 16. Schema/migration impact

No columns/constraints/migration added. Head remains `0079_setup_lifecycle_alert_ev`.
Existing JSONB payload/lineage containers hold immutable subdocuments. Supported ORM Winner
readiness guards add no database schema. No production rewrite or legacy backfill.

## 17. Tests

Final lane results and reproducible commands follow. Test families cover
Technical insufficient/ready, degraded vs blocked, legacy, immutability/reload, same score/different
readiness, same readiness/different identity, canonical reasons, native metrics, lifecycle naming,
unknown default policy, behavior preservation and Phase-2/1/0 regressions. A focused PostgreSQL
test additionally exercises operational Winner lineage updates and readiness mutation/deletion.

The broad lane excludes integration/E2E, migration infrastructure and QA infrastructure self-test;
PostgreSQL suites run separately. External/E2E markers are deselected. Existing optional skips,
deprecations and any exploratory fixture/command failures are reported, not hidden.

| Lane | Result | Warnings / omissions |
|---|---|---|
| T12A focused contract + PostgreSQL | 46 passed | 2 warnings; no failed/skipped/deselected |
| Native producer contracts | 176 passed | 1 warning; all eleven producer contracts also covered by the focused mapping matrix |
| Final gap/maintenance/lifecycle regression | 60 passed | 1 warning; validates the final additional lifecycle writer path |
| Behavior probe baseline / T12A | 131 / 131 passed; 219 exact matching business results | 1 warning each; no failed/skipped |
| Phase-2 shared/native evidence and historical reads + new PG test | 61 passed | 18 warnings; no failed/skipped |
| Final readiness immutability + Setup/lifecycle evidence rerun | 51 passed | 3 warnings; includes persisted Winner delete guard after clearing in-memory lineage |
| Full affected Winner/Setup + evidence integration | 620 passed | 11 warnings; final narrower edits additionally covered by 46/60/51 reruns |
| Final Phase-1 identity | 134 passed | 1 warning; no failed/skipped |
| Phase-0 temporal/fence/preflight/publication, including PG | 141 passed | 40 warnings; no failed/skipped |
| Final broad clean repository lane | 2,618 passed | 7 external/E2E deselected, 22 warnings; no failed/skipped |
| QA infrastructure fixture verification | 7 QA tests pass within the 52-test QA/focused/PG lane | 2 lane warnings; initial missing-default-PG skip resolved on named disposable PG |
| Static gates | Ruff app/tests/scripts, compileall app/tests/scripts, diff check, one head: PASS | no schema columns changed; PG Phase-2 drift check passes |

Warning categories are existing Starlette/httpx deprecation, Alembic `prepend_sys_path` splitting
deprecation, and sqlite3 default datetime-adapter deprecation. Git also reports LF-to-CRLF
normalization notices for touched files; diff whitespace checks pass. No unexplained flaky failure.

Exploratory issues: one new PG fixture failed because its known as-of session omitted the
Phase-1-required known calendar identity; the fixture was corrected and passes. A producer-lane
command named nonexistent `test_ranking_profile_gates.py`; corrected to the actual engine tests.
The precursor broad lane included `tests/qa/test_qa_infrastructure.py` due to a wrong ignore path,
giving one default-PG-unavailable skip; the QA fixture passes separately on disposable PG, and
the corrected final broad lane has zero skips. Initial formatting findings were fixed before the
passing static gates. These are fixture/invocation issues, not relaxed implementation invariants.

Commands use repository `.venv/Scripts/python.exe`. PostgreSQL lanes set only
`SWINGLENS_TEST_POSTGRES_ADMIN_URL` to the named disposable container; credentials omitted.

```powershell
.venv/Scripts/python.exe -m pytest tests/test_producer_readiness.py tests/integration/test_readiness_evidence_postgresql.py -q
.venv/Scripts/python.exe -m pytest tests/test_fundamental_coverage_service.py tests/test_fundamental_ranker_v2.py tests/test_technical_confidence.py tests/test_combined_decision.py tests/test_ranking_profile_engine.py tests/test_market_regime_policy.py tests/test_sector_rotation_policy.py tests/ceri/test_ceri_confidence_service.py tests/ceri/test_ceri_freshness_semantics.py tests/ib_market_intelligence/test_calculations.py tests/setup_lifecycle/test_slse_confidence_coverage.py tests/setup_lifecycle/test_lifecycle_engine.py tests/winner_probability/test_feature_schema.py tests/test_producer_readiness.py -q
.venv/Scripts/python.exe -m pytest -p scripts.qa.t12a_behavior_probe --t12a-business-output=business.json tests/test_combined_decision.py tests/test_ranking_profile_engine.py tests/test_ranking_profiles_golden.py tests/setup_lifecycle/test_snapshot_builder.py tests/setup_lifecycle/test_lifecycle_engine.py tests/setup_lifecycle/test_actionability_policy.py tests/ceri/test_scoring.py tests/ceri/test_ceri_acceptance_fixture.py tests/winner_probability/test_capture_service.py tests/winner_probability/test_acceptance_fixture.py tests/winner_probability/test_probability_estimator.py -q
.venv/Scripts/python.exe -m pytest tests/integration/test_core_calculation_evidence_postgresql.py tests/integration/test_regime_sector_immutable_evidence_postgresql.py tests/integration/test_ceri_immutable_decision_evidence.py tests/integration/test_ibmi_immutable_constituent_evidence.py tests/integration/test_setup_lifecycle_alert_immutable_evidence.py tests/integration/test_repository_historical_read_enforcement.py tests/integration/test_phase2_immutable_evidence_certification.py tests/integration/test_readiness_evidence_postgresql.py -q
.venv/Scripts/python.exe -m pytest tests/test_calculation_identity.py tests/test_calculation_identity_adoption.py tests/test_combined_ranking_identity_adoption.py tests/test_contextual_calculation_identity_adoption.py tests/test_phase1_calculation_identity_certification.py tests/winner_probability/test_winner_calculation_identity_adoption.py -q
.venv/Scripts/python.exe -m pytest tests/test_domain_write_fence.py tests/test_session_temporal_integrity_remediation.py tests/test_temporal_preflight_adversarial.py tests/test_preflight_execution_context_contract.py tests/winner_probability/test_temporal_integrity.py tests/winner_probability/test_generation_publication_fence.py tests/winner_probability/test_estimate_publication_policy.py tests/ceri/test_ceri_temporal_containment.py tests/ib_market_intelligence/test_ibmi_temporal_containment.py tests/integration/test_session_temporal_integrity_postgresql.py tests/integration/test_transition_preflight_plan_postgresql.py tests/integration/test_winner_temporal_integrity_postgresql.py -q
.venv/Scripts/python.exe -m pytest tests --ignore=tests/integration --ignore=tests/e2e --ignore=tests/test_migration_remediation.py --ignore=tests/qa/test_qa_infrastructure.py -m 'not external and not e2e' -q -ra
.venv/Scripts/python.exe -m ruff check app tests scripts
.venv/Scripts/python.exe -m compileall -q app tests scripts
git diff --check
.venv/Scripts/python.exe -m alembic heads
```

Producer command contains the final gap test as well (177 cases on recollection); the recorded
176-case lane preceded that one additional test, which passes in the final focused/gap lanes.
QA logs/canonical comparison files remain ignored disposable artifacts under `.qa_work`.

## 18. Finding/invariant reconciliation

| Finding | T12A verdict | Remaining boundary |
|---|---|---|
| CORE-009 | PARTIAL | producer insufficiency typed and frozen; positive numeric scoring/consumer paths remain |
| RANK-001 | OPEN for consumer enforcement | Technical -> Combined and Ranking blocking not introduced |
| RANK-002 | OPEN for consumer enforcement | low-confidence rows remain in normalization/population/ranking |
| SETUP-003 | OPEN for consumer enforcement | direct Technical -> Setup/Lifecycle and contextual quality rules remain |
| WIN-004 | PARTIAL | existing explicit insufficiency/Combined gates preserved; shared/native confidence policy deferred |
| XINT-004 | PARTIAL | IBMI quality now frozen and typed; liquidity/volatility/short-pressure consumer policies deferred |
| INV-READINESS-001 | FOUNDATION_IMPLEMENTED / PARTIALLY_ENFORCED | new canonical readiness never inferred from numeric presence; full decision-edge enforcement not claimed |

## 19. T12B candidates

Technical -> Combined scoring/completeness; Technical -> Ranking normalization/scoring/ranks;
direct Technical -> Setup family proposals -> Lifecycle/alerts. Low-confidence treatment must
remain consumer-specific. No Ranking behavioral gate is substituted for direct Setup input safety.

## 20. T12C candidates

IBMI liquidity -> Ranking; IBMI volatility/short pressure -> CERI; Regime -> Setup; Sector -> Setup.
Sector numeric aggregation and CERI alignment are additional inventory candidates requiring a
later scoped policy decision; T12A does not invent enforcement for them.

## 21. T12D candidates

Ranking -> Winner; Regime/Sector -> Winner; remaining Technical confidence/required-feature
quality -> Winner; native probability-evidence readiness and cross-consumer certification.
Existing Winner-specific exclusions/cohort/probability formulas remain unchanged.

## 22. Residual risks

Foundation does not make current consumers safe automatically. Legacy current behavior is
unchanged but uncertified. Missing producer quality is UNKNOWN. Sector snapshot aggregation is
not a per-sector eligibility verdict. Winner's own probability readiness remains policy-undecided.
Existing business warnings are caveats, not fabricated invalidity. Application ORM guards do not
prevent privileged direct SQL/bulk bypass. No full production replay, browser/provider or live
brokerage certification is claimed. New evidence fingerprints legitimately differ due to metadata.

## 23. Final verdict

**T12A VERDICT: PASS. INV-READINESS-001: FOUNDATION_IMPLEMENTED / PARTIALLY_ENFORCED.**
All required typed, native-metric, legacy, identity-binding and immutability properties are
implemented and tested. Consumer business outputs are preserved in exact fixture comparisons.
The architecture contract and this complete inventory/report are delivered in one explicitly
staged focused commit. No merge or push is performed.

Certification used PostgreSQL 16 in named container `swinglens-t12a-pg-20260916`, loopback port
55432, with guarded `swinglens_pytest_*` databases automatically dropped by fixtures. Existing
head fresh-install, downgrade/re-upgrade and ORM/schema checks pass through the Phase-2 regression
suite; no new migration campaign or production database access was needed.

Only repository files, disposable baseline worktree and named local PostgreSQL test infrastructure
were mutated. Docker Desktop was started for the tests. No authoritative SwingLens DB, production
pipeline/evidence, provider write, broker action, Winner publication, production rewrite or backfill
occurred. The task-owned baseline checkout and PostgreSQL container are removed after tests; unrelated
existing worktrees and other containers are preserved. Final worktree contains only the focused commit.

Changed files: `app/models/tables.py`, `app/services/producer_readiness.py`,
`app/services/core_calculation_evidence.py`, `app/services/setup_lifecycle/decision_evidence.py`,
`app/services/winner_probability/capture_service.py`, `scripts/qa/t12a_behavior_probe.py`,
`tests/test_producer_readiness.py`, `tests/integration/test_readiness_evidence_postgresql.py`,
`docs/architecture/SWINGLENS_READINESS_CONTRACT.md`, and this report. No schema file changed.
