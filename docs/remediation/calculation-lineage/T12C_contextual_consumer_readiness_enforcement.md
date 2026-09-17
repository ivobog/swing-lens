# T12C — Contextual Consumer Readiness Enforcement

## 1. Executive verdict

**T12C VERDICT: PASS.** The five requested contextual edges are enforced using the T12A typed
contract. Focused, native consumer, PostgreSQL, READY parity, Phase-2, Phase-1, Phase-0,
broader repository and static checks pass. Material T12C bypasses: zero; unexpected READY
changes: zero. This is a scoped consumer certification; producer history sufficiency and
repository-wide Phase-3 readiness remain partial.

## 2. Baseline

| Item recorded before editing | Value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase-2 certified baseline | `7b015124807b8f895911d7e790fcbf01949ff85f` |
| T12A | `4c8eb3402b951482d590890e726195a3c445d786` |
| Initial branch | `codex/t12b-core-consumer-readiness-enforcement` |
| T12B / exact starting HEAD | `b5af43ca8690cb65980be79c4a453ee1d4f7739e` |
| Initial tracked modifications / untracked files | None / none |
| T12C branch | `codex/t12c-contextual-consumer-readiness-enforcement` |
| Python | Repository venv, CPython 3.12.2 |
| Migration head | `0079_setup_lifecycle_alert_ev`, one head |

The audited registry/synthesis, Phase-1 and Phase-2 architecture certifications, T11E report,
T12A/T12B reports and readiness contract were used as authority. Their historical sections
are preserved. No baseline advance was assumed. Unrelated performance worktrees and existing
monitoring containers were preserved. The final commit SHA is reported outside its own
contents; the intended single commit is `fix: enforce contextual readiness across consumers`.

## 3. Scope

IBMI LIQUIDITY -> Ranking; IBMI VOLATILITY -> CERI; IBMI SHORT_PRESSURE -> CERI;
REGIME -> Setup; SECTOR -> Setup. Downstream Lifecycle/Confidence/Actionability/Alerts consume
recorded Setup permission. T12B Technical policies and T12A native producer mappings remain
unchanged. No Winner policy, formula configuration, schema, production rewrite or legacy
backfill is included. Optional omission does not automatically invalidate the consumer.

## 4. Contextual dependency graph

| Edge / function trace | Actual role | Existing missing behavior |
|---|---|---|
| `ranking_profile_service._load_liquidity_features` -> `_identity_safe_liquidity` -> `ranking_profile_engine.rank_single_row` -> `_tradeability_penalty` | Optional bounded IBMI liquidity penalty after Technical profile score | No IBMI overlay penalty; otherwise qualified Ranking remains valid |
| `ceri.capture_service._preload_ibmi_context` -> `_point_in_time_volatility_feature` -> `options_event_premium_score` -> `CeriEventRiskService.calculate` | Optional secondary event-risk penalty | Absent risk contribution, no new missing penalty |
| Same preload -> `_point_in_time_short_pressure_feature` -> `CeriEventRiskService.calculate` | Explanatory short-pressure risk reason | Reason absent; no numeric penalty exists for this source |
| Setup `source_loader` -> `snapshot_builder.build` -> `_source_values` / `_promoted_fields` / `_context_complete` | Regime label and market gate; context completeness | Missing optional market context; existing quality/confidence and actionability conventions |
| Same Setup path -> exact frozen Sector row | Sector rank/confidence and context completeness | Missing optional sector context; no new whole-consumer block |
| Frozen Setup -> `lifecycle_engine`, `family_adapters`, `confidence_service`, `actionability_policy` | Family evaluation, confidence context vote, market actionability | Omitted fields/date votes remain absent in current and historical evaluation |
| Lifecycle -> `alert_service`; optional alert market fallback reads Setup | Existing actionable transition and market-restriction rules | No restored omitted market permission; no fabricated transition |

Regime position sizing is producer diagnostics, not a direct Setup signal in this graph.
CERI Opportunity's available weight and Confidence coverage use their existing revision/
alignment evidence; neither IBMI module is a constituent of those formulas. CERI -> Setup,
CERI -> Ranking and IBMI -> Winner remain absent. Lifecycle adds no direct Regime/Sector query.

## 5. Policy architecture

`app/services/contextual_consumer_eligibility.py` reuses frozen `ProducerReadinessEnvelope`,
typed `ConsumerEligibilityDecision`, statuses and reasons from T12A. Consumer policies are
immutable, independently versioned and reject arbitrary configurable overrides.

| Policy | Version | READY | DEGRADED | Blocking readiness/reasons | UNKNOWN | LEGACY_UNKNOWN |
|---|---|---|---|---|---|---|
| IBMI_LIQUIDITY_TO_RANKING | ibmi-liquidity-to-ranking-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |
| IBMI_VOLATILITY_TO_CERI | ibmi-volatility-to-ceri-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |
| IBMI_SHORT_PRESSURE_TO_CERI | ibmi-short-pressure-to-ceri-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |
| REGIME_TO_SETUP | regime-to-setup-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |
| SECTOR_TO_SETUP | sector-to-setup-v1 | ELIGIBLE | POLICY_UNDECIDED | INELIGIBLE | POLICY_UNDECIDED | POLICY_UNDECIDED |

Blocking readiness means INSUFFICIENT_EVIDENCE, ERROR, STALE, or any native blocking reason.
Only ELIGIBLE grants behavioral use. Undecided preserves its distinct diagnostic meaning.
The selector validates evidence ID, kind, IBMI ticker/module or context owner, Calculation
Identity fingerprint and producer envelope binding. Eligible numerics/signals are projected
from exact frozen payloads, not mutable producer fields. Sector row lookup is within that
exact snapshot payload. Malformed/mismatched/unresolved pointers fail explicitly.

## 6. IBMI liquidity → Ranking

Permission is evaluated before `_tradeability_penalty` reads classification, dollar volume,
coverage or score. Existing READY grade/floor logic, bounded penalty and per-profile formula
are preserved. Debug records the complete decision even when the optional source is omitted.
Certified omitted sources remain exact ledger source edges; legacy unpointed references stay
diagnostic only. Technical rankability and T12B population exclusion are unchanged.

## 7. Ranking omission semantics

An ineligible/undecided liquidity source produces the same complete business DTO as missing
liquidity. No extra missing penalty, weight change, zero substitute, new gate or incomplete
label is added for optional IBMI omission. The ticker can still rank from eligible Technical
and Fundamental evidence. Same numerical values with unavailable coverage, stale/error/
unknown/legacy readiness have different inclusion from READY; excluded raw values cannot
affect valid peers. Repeated omission cannot double penalize. Native READY zero is still an
included source and is not confused with absent evidence.

## 8. IBMI volatility → CERI

The exact compatible selected volatility artifact is evaluated before Confidence/Opportunity
and event risk. Only its eligible frozen `components` supply the existing options premium
calculation. Missing/ineligible volatility passes `None` into event risk. Existing positive
premium penalty and reason remain unchanged. A genuine READY zero remains included with
provenance, while missing evidence has no options risk ledger entry.

## 9. IBMI short pressure → CERI

Short pressure has its own policy, selection and frozen classification. Only eligible evidence
adds the existing `ibkr_short_pressure_context:*` reason. The native implementation does not
add an independent numeric score or confidence/coverage contribution for this module.
Omission removes this reason without manufacturing a new risk penalty or suppressing valid
volatility evidence.

## 10. CERI available-evidence semantics

Both optional module selections now precede Confidence and Opportunity calculation. Every
IBMI input reaching a behavioral calculation is eligible. There is no post-weight invalid-input
removal. The existing Opportunity available-weight/minimum-evidence formula is unchanged;
IBMI volatility/short pressure do not belong to its denominator or Confidence's coverage.
Consequently omission cannot itself reduce Opportunity available weight in the current graph.
The required conditional minimum-evidence case is tested honestly: after excluding an invalid
IBMI source, no revisions still means available weight zero and Unrated/insufficient CERI;
remaining READY IBMI cannot rescue it. No invented denominator or confidence penalty was added.
CERI's own producer readiness remains separate from input permission.

## 11. CERI mixed-input behavior

Invalid volatility + READY short pressure omits the options premium and retains the short
reason. READY volatility + invalid short pressure retains the existing event-risk premium
and omits the short reason. Both invalid omits both influences. Full Opportunity, EventRisk,
Confidence and Posture business results are compared to corresponding genuinely missing
inputs. Each frozen permission independently records producer evidence, native status and
policy version. Native PostgreSQL persists READY and mixed snapshots in separate legitimate
runs/contexts and pins the omitted volatility evidence as well as included short pressure.

## 12. Regime → Setup

Regime permission is evaluated before label/gate promotion and context quality. Native emitted
`stale_market_data` / `severely_stale_market_data` warnings map to T12A STALE and block use even
when producer diagnostics retain bullish regime, `gate_ok=True` and positive sizing.
Omitted fields cannot establish permissive market context. Independently qualified Technical
behavior can still apply through existing optional-context semantics.

Sparse SPY history with native LOW is DEGRADED and undecided. A separate focused regression
records that sparse history with native NORMAL still maps READY: T12A has no native bar-count
sufficiency rule for this producer. T12C adds no consumer history threshold or producer fix.
This producer-side gap prevents claiming CORE-001 fully closed. Synthetic blocking/insufficient
envelopes are covered by the typed policy matrix; no new native producer state is invented.

## 13. Sector → Setup

Snapshot readiness is evaluated before exact frozen per-sector rank/confidence is exposed.
Sector snapshot native LOW/insufficient rows produce DEGRADED under existing aggregate
normalization, rather than a fabricated global INSUFFICIENT_EVIDENCE state. Because no degraded
Setup numeric-use grant exists, those snapshots are undecided and omitted even if their
current row appears strong. Eligible selection reads the row inside the frozen snapshot,
so mutable SectorRotationRow values cannot overwrite frozen rank/confidence. No new row-level
readiness framework is introduced.

## 14. Setup contextual omission semantics

Regime and Sector are filtered independently in a separate behavior context before signal/
quality construction. Original source IDs and exact evidence remain audit provenance.
Regime omission removes `market_regime` and `market_gate`; Sector omission removes `sector_rank`
and `sector_confidence`. Required Technical coverage is unchanged. Existing optional warning
and HIGH/NORMAL quality/context-confidence conventions apply without an added penalty.
`config/setup_lifecycle.yaml` still requires Technical only. Optional omission alone cannot
universally block actionability or invalidate Setup. Setup self-readiness is evaluated by its
unchanged T12A normalizer after these existing consequences.

## 15. Lifecycle/Alert propagation

Engine, family gateway, Confidence and Actionability sanitize recorded Setup permission.
They remove omitted raw/normalized signals and context lineage-date votes from behavioral
copies, including previous snapshots; stored diagnostic lineage is not rewritten. Missing
permission on identity-bound/context-linked Setup abstains. Generic unbound mathematical DTOs
retain their existing API behavior. Binding includes expected REGIME/SECTOR producer,
consumer, readiness fingerprint and evidence ID.

Episode application reads exact frozen Setup lineage using the existing T12B evidence path.
Native PostgreSQL starts from a historically valid READY episode, introduces stale Regime
and insufficient Sector context, and verifies no fabricated Triggered/Confirmed transition,
no lifecycle event and no new actionable alert. The prior evaluation and all old consumer
payloads remain byte-for-byte canonical equivalents. Optional omission follows existing
Technical-qualified behavior; it does not force every such episode into a new hard block.

The static audit additionally found Alert's market fallback could read residual mutable Setup
signals. It now resolves exact frozen Setup evidence, verifies scope, and applies recorded
context omission before market restrictions. A PostgreSQL adversary restores bullish raw
signals and clears mutable lineage; the pinned omission still wins. Existing event evidence
keeps its historical meaning. No new direct Regime/Sector -> Lifecycle/Alert producer edge exists.

## 16. DEGRADED policy decisions

| Edge reviewed independently | Native evidence | Decision and reason |
|---|---|---|
| Liquidity -> Ranking | `_tradeability_penalty`, `config/ranking_profiles.yaml`; AVAILABLE coverage and bounded optional penalty | No degraded-use grant; a grade/floor penalty is not quality permission. POLICY_UNDECIDED |
| Volatility -> CERI | `ib_market_intelligence.calculations.options_event_premium_score`, `ceri/event_risk_service.py`, `config/ceri.yaml` | Existing clamp/secondary penalty says how to use valid numerics, not that DEGRADED is usable. POLICY_UNDECIDED |
| Short pressure -> CERI | `ceri/event_risk_service.py` reason-only path, `config/ib_market_intelligence.yaml` source calculations | Classification reason has no degraded provider-context permission. POLICY_UNDECIDED |
| Regime -> Setup | `market_regime_policy.py` emits low/stale warnings; Setup market actionability rules | LOW warning is a producer convention, not a consumer use grant. POLICY_UNDECIDED |
| Sector -> Setup | `snapshot_builder.py` context/confidence, `config/setup_lifecycle.yaml`; T12A aggregate Sector normalization | Optional context and quality consequences do not grant snapshot DEGRADED numeric use. POLICY_UNDECIDED |

No blind inheritance from the T12B Technical policy, new confidence/freshness threshold or
configurable degraded permission exists. Future policy changes require reviewed versions.

## 17. UNKNOWN/legacy behavior

UNKNOWN and LEGACY_UNKNOWN are POLICY_UNDECIDED on all five edges and omitted. No unsealed
current row, dictionary, positive score, FRESH/CURRENT label or absent readiness envelope
implicitly becomes READY. Native producers are not retrospectively normalized for old
evidence. Diagnostic selected references remain visible even when they lack a certifiable
source pointer. Arbitrary unknown-native spellings remain T12A UNKNOWN.

## 18. Current/global fallback prevention

Existing identity compatibility chooses a candidate; readiness then decides that exact
candidate. A failure returns omission immediately and cannot select an older READY or global
replacement. Explicit missing/scope/identity errors fail rather than consult latest state.
Tests cover both CERI modules and exact pointers, mutable value changes, absent envelopes,
wrong ticker/module pointers and PostgreSQL current-pointer advancement. Regime tests mint
a later globally compatible READY artifact and prove the explicitly selected stale artifact
remains omitted. Ranking has no new fallback after `_identity_safe_liquidity`/permission.

## 19. Cross-run Regime behavior

Existing SETUP_REGIME_COMPATIBILITY preserves exact temporal cutoff/session, calendar,
effective configuration and algorithm compatibility without making run ID a universal
Regime barrier. PostgreSQL produces Regime in run 99, consumes compatible READY evidence in
Setup run 7 and retains its exact source pin. The same readiness policy omits stale/unknown
evidence regardless of owner. No new cross-run reuse rule or fallback is introduced.

## 20. Eligibility immutability

`contextual_consumer_eligibility` freezes per-edge decision, version, reasons, readiness DTO/
fingerprint, evidence ID, inclusion and selected reference in Ranking debug, CERI lineage and
Setup lineage before identity/evidence hashing. Certified omitted inputs are still pinned.
CERI's source manifest includes selected certified omitted feature IDs; unpointed legacy IDs
are diagnostic only. Current projections and source inventory grant no numeric permission.

PostgreSQL changes all five prospective registries to v2 and verifies old Ranking/CERI/Setup
payloads and frozen v1 Lifecycle behavior do not change. A fresh session confirms stored
payload round-trip equality. Exact Ranking retry returns the original evidence ID; Setup
retains the exact stale Regime pin; repeated omission has deterministic decisions and no
double penalty. Historical producer numerics/envelopes and consumer meaning are preserved.

## 21. READY parity

Committed opt-in tools are `scripts/qa/t12c_behavior_probe.py` and
`scripts/qa/t12c_compare_behavior.py`. A detached T12B worktree ran the identical nine native
READY scenarios. Only the three newly added helper/probe/scenario files were copied;
tracked baseline fixtures and production code were not replaced. Both sides passed nine
tests. Strict full canonical business comparison has no allowlist and excludes only assigned
provenance/operational clocks/internal diagnostic evidence.

15 exact captures: five Ranking populations covering every configured profile; three Setup,
three Lifecycle and three Actionability captures with varied prior states; one CERI capture
containing all four business outputs and complete Opportunity/Confidence/EventRisk ledgers,
reasons, warnings, contributors and alignment flags. CERI's no-revision case intentionally
remains Unrated despite both optional IBMI inputs being READY. Existing broader CERI business
tests exercise rated scoring and coverage separately.

Final READY fingerprint:
`cce4b2d41ca4f184b46bba6f57aed430e2ff4ca22fd7b3e824a3b2569d7c9386`.
Unexpected changes: zero. Final comparison repeated after the alert fallback fix is exact.

## 22. Expected remediation changes

Unavailable/stale/error/unknown/legacy/degraded IBMI no longer supplies Ranking overlay or
CERI volatility/short reasons. Ranking otherwise retains existing optional-missing behavior.
Stale/undecided Regime and Sector no longer supply Setup fields, market permission or context
confidence votes, including residual historical signal/date paths and Alert's Setup fallback.
Optional omission follows native consumer rules; no all-consumer invalidation or zero substitute
is introduced. Mixed sources remain independent and existing minimum evidence still applies.

Existing invalid fixture inputs were retained as exclusion regressions: unsealed Ranking
dictionary; FRESH CERI global features; CURRENT optional Ranking liquidity. Their old
permissive expectations are replaced by exact missing-business equivalence/legacy or unknown
decision assertions, followed by independently constructed fully native READY positives.
No fixture is globally auto-certified, adversarial input removed, readiness mocked always
eligible, test marked xfail/skip, threshold relaxed or failure hidden. No unexpected READY drift.

## 23. Performance/query impact

Existing evidence pointers gain view-only/select-in-loadable relationships for IBMI, Regime
and Sector; no database column changes. Ranking preloads cohort liquidity evidence once.
CERI now preloads both optional modules once for all tickers instead of separate per-ticker
module/evidence selection; standalone selectors still use bounded select-in evidence loading.
Setup context candidate queries preload evidence. A shared writer advances already loaded
relationships on pointer advancement, preventing stale same-session reads.

PostgreSQL seeds 12 actual IBMI artifacts across four tickers, preloads their exact evidence,
and observes zero SELECTs while evaluating the four liquidity permissions and seven profile
calculations. This certifies absence of per-evaluation readiness N+1 for the measured path.
Select-in loading remains ORM-batched for larger cohorts; no unmeasured runtime latency claim
or broad optimization is made. Alert's exact Setup fallback is outside Ranking/CERI cohort
formula loops and reuses the owning session's evidence identity map.

## 24. Static contextual consumer audit

Repeated `rg -n -uu` searches over Python sources cover IBMI/liquidity/volatility/short pressure,
Regime/Sector/rank, confidence/coverage/freshness/stale/readiness/eligibility, Setup/actionability/
gate/sizing/posture/event risk. `-uu` ensures ignored CERI directories are included. Searches
trace value extraction through behavior and persistence rather than equating a source ID with use.

| Material site | Classification | Verified boundary |
|---|---|---|
| Ranking liquidity cohort/identity selection, overlay extraction and penalty | T12C_ENFORCED | Exact permission before classification/volume/penalty; frozen projection |
| CERI optional module preload/selectors, options premium and short-pressure reason | T12C_ENFORCED | Independent exact decisions before weights/confidence/risk; no readiness fallback |
| Ranking/CERI source manifests and immutable ledger writers | T12C_ENFORCED | Included/omitted certified source pins and decisions frozen before hash |
| Setup context loader/builder/promoted fields/quality | T12C_ENFORCED | Independent Regime/Sector omission before values/context votes |
| Lifecycle/family/history/Confidence/Actionability | T12C_ENFORCED | Frozen Setup permission, omitted signals and date votes |
| Alert market fallback from Setup | T12C_ENFORCED | Exact Setup evidence and recorded omission; no raw current permission |
| Technical core consumers and exact historical evidence resolvers | ALREADY_SAFE | T12B and Phase-2 guarantees retained |
| Winner feature extraction, contextual identity adoption, Ranking/Regime/Sector feature schema and capture | T12D | Existing insufficient Technical handling only; remaining readiness deferred |
| Cockpit/command-center/query/export/score-card presentation | DISPLAY_ONLY | Producer diagnostic numerics and source status presentation |
| Unsealed compatible current context retained as diagnostic candidate | LEGACY_CURRENT | Omitted from certified behavior; original legacy presentation preserved |
| IBMI module production, Regime/Sector producer policy, CERI alignment context and provider ingestion | OUT_OF_SCOPE | Different producer/consumer edges; no claim of full repository enforcement |
| Generic unbound mathematical state-machine DTOs | OUT_OF_SCOPE | No certified contextual source binding |
| Material bypass in any of the five requested edges | REMAINING_BYPASS | 0 |

No CERI -> Setup/Ranking or IBMI -> Winner dependency is introduced. Winner's current readers
are explicitly deferred, rather than misclassified as safe because values/identity exist.

## 25. Tests

| Final lane | Result | Coverage |
|---|---|---|
| T12C focused | 82 passed, 1 warning | 59 typed/native contextual cases + 14 independent CERI cases + 9 READY scenarios |
| Ranking | 24 passed within native lane | engine 12, golden 2, service 10; focused omission/peer/parity tests additional |
| CERI | 456 passed within native lane | complete `tests/ceri`; native selectors/mixed/minimum/ledger assertions additional |
| Setup/Lifecycle/Alert | 265 passed within native lane | complete subsystem; frozen-permission/alert-fallback tests additional |
| T12B | 41 passed within native lane + PG | 37 focused + 4 READY scenarios; actual Technical blocked history/alerts |
| T12A | 45 passed within native lane + PG | unchanged typed producer normalization and binding |
| Combined native consumer lane | 913 passed, 1 warning | all above unit modules; no skip/deselection |
| Phase-2 + readiness/consumer PostgreSQL | 63 passed, 20 warnings | ten integration modules including T12C; fresh migration, populated downgrade/reupgrade, drift, pins, retry/history |
| Phase-1 | 134 passed, 1 warning | six typed identity/adoption/certification modules |
| Phase-0 | 141 passed, 40 warnings | fencing, temporal, preflight, publication and three native PG modules |
| READY baseline / final current | 9 passed each, 1 warning each | 15 complete captures, exact comparison |
| Broader repository | 2742 passed, 7 deselected, 22 warnings | final native repository lane; zero failures/skips |
| Static gates | passed | Ruff app/tests/scripts, compileall, diff check, one Alembic head |

Counts overlap and are not unique totals. Final native consumer command:

```text
.venv/Scripts/python.exe -m pytest tests/test_contextual_consumer_eligibility.py
  tests/test_ceri_contextual_eligibility.py tests/test_t12c_ready_scenarios.py
  tests/test_technical_consumer_eligibility.py tests/test_t12b_ready_scenarios.py
  tests/test_producer_readiness.py tests/test_ranking_profile_engine.py
  tests/test_ranking_profiles_golden.py tests/test_ranking_profile_service.py
  tests/setup_lifecycle tests/ceri -q -ra
```

Phase-2 integration modules: `test_core_calculation_evidence_postgresql.py`,
`test_regime_sector_immutable_evidence_postgresql.py`, `test_ceri_immutable_decision_evidence.py`,
`test_ibmi_immutable_constituent_evidence.py`, `test_setup_lifecycle_alert_immutable_evidence.py`,
`test_repository_historical_read_enforcement.py`, `test_phase2_immutable_evidence_certification.py`,
`test_readiness_evidence_postgresql.py`, `test_technical_consumer_eligibility_postgresql.py`,
`test_contextual_consumer_eligibility_postgresql.py`.

Phase-1: `test_calculation_identity.py`, `test_calculation_identity_adoption.py`,
`test_combined_ranking_identity_adoption.py`, `test_contextual_calculation_identity_adoption.py`,
`test_phase1_calculation_identity_certification.py`,
`winner_probability/test_winner_calculation_identity_adoption.py`.

Phase-0: domain write fence, session temporal integrity, temporal adversarial preflight,
execution-context preflight, Winner temporal integrity, generation publication fence,
estimate publication policy, CERI/IBMI temporal containment; PostgreSQL session temporal,
transition preflight plan and Winner temporal integrity modules.

Broader command:

```text
.venv/Scripts/python.exe -m pytest tests --ignore=tests/integration --ignore=tests/e2e
  --ignore=tests/test_migration_remediation.py --ignore=tests/qa/test_qa_infrastructure.py
  -m 'not external and not e2e' -q -ra
```

These are the existing native-lane boundaries. QA infrastructure's subprocess self-test,
external/provider/browser lanes and unrelated migration-remediation tests are not silently
called certified. Required migrations are exercised by the guarded native PostgreSQL lane.
The existing seven external/e2e deselections are reported; no new skip/xfail was added.
Warnings are existing Starlette/httpx, Python 3.12 SQLite datetime adapter, and Alembic
`path_separator` deprecations. Initial genuine fixture failures were investigated and corrected:
preserved legacy readiness assertions; historical episode family must match actual native
Setup; second CERI snapshot requires a distinct legitimate run; native IBMI requires the
real market calendar rather than the synthetic Phase-1 calendar. No flaky rerun classification.

| Required test family | Evidence / result |
|---|---|
| 1 liquidity READY parity | all five profiles, full DTO/penalty/ordinal comparison |
| 2 liquidity ineligible | native unavailable/stale/error cases equal actual missing business |
| 3 liquidity UNKNOWN | unknown/FRESH/CURRENT native spellings and unsealed input omitted |
| 4 same liquidity/different readiness | same values, different envelope, independent inclusion |
| 5 volatility READY | actual selector, premium, risk ledger and full CERI capture |
| 6 volatility ineligible | native statuses and no older READY fallback |
| 7 short pressure READY | actual selector and native explanatory reason |
| 8 short pressure ineligible | reason omitted without new numeric penalty |
| 9 partial IBMI | both mixed directions and both invalid; full four-output equivalence |
| 10 minimum evidence | excluded IBMI cannot rescue zero revision weight; conditional IBMI-driven weight drop is not applicable to actual graph |
| 11 CERI READY parity | full four outputs and ledgers/reasons/coverage captured exactly |
| 12 Regime READY parity | actual Setup/LC/actionability under three prior states |
| 13 stale bullish Regime | native stale warning wins over retained true gate/sizing |
| 14 sparse/insufficient Regime | LOW omitted; NORMAL sparse producer gap explicitly recorded; typed blocking matrix |
| 15 Sector READY parity | exact frozen rank/confidence, Setup/LC full business parity |
| 16 Sector ineligible | native low/insufficient aggregate DEGRADED omitted |
| 17 mixed Regime/Sector | independent omissions equal corresponding missing context |
| 18 both contexts ineligible | fields absent, quality equals missing, no whole-consumer invention |
| 19 Setup/LC propagation | engine/gateway/confidence/actionability and actual PG alerts |
| 20 existing READY episode | historical valid evidence preserved, no fabricated trigger/confirm/event |
| 21 eligibility immutability | all five prospective v2 registries leave pinned v1 evidence/replay unchanged |
| 22 retry idempotency | exact Ranking evidence ID retained; repeated decisions deterministic |
| 23 DEGRADED | independent five-policy matrix and native aggregate cases |
| 24 UNKNOWN/legacy | five-policy matrix plus retained adversarial current fixtures |
| 25 ERROR | five-policy matrix plus native IBMI error extraction |
| 26 STALE | five-policy matrix, actual Regime warnings and IBMI selection |
| 27 no zero substitution | valid zero included vs absent None; no fabricated ledger contribution |
| 28 no double penalty | omitted DTO equality with missing, repeated omission and unchanged confidence formula |
| 29 current/global fallback | incompatible vs exact ineligible distinctions; older/global READY cannot replace selected blocked source |
| 30 cross-run Regime | native run 99 -> Setup run 7, exact compatible READY pin |
| 31 T12B | 41 units plus PG retained |
| 32 T12A | 45 units plus PG retained |
| 33 Phase-2 | 63 native PG cases |
| 34 Phase-1 | 134 cases |
| 35 Phase-0 | 141 cases |
| 36 negative dependencies | static actual graph, no CERI -> Setup/Ranking, no IBMI -> Winner |

## 26. Finding reconciliation

| Finding | T12C disposition | Residual |
|---|---|---|
| CORE-001 | PARTIAL: native insufficient/degraded/unknown/stale Regime cannot supply Setup permission | Sparse NORMAL remains producer READY; no minimum history normalization fix in T12C |
| CORE-002 | Consumer bypass enforced: native emitted stale Regime cannot supply Setup gate/context | Native producer may retain bullish gate/sizing; changing producer stale policy is outside this task |
| CERI-007 | Enforced for IBMI volatility/short-pressure -> CERI | Provider ingestion, other IBMI edges and full cross-consumer certification not claimed |
| WIN-004 | DEFERRED T12D | Winner Technical quality and Ranking/Regime/Sector readiness |
| XINT-004 | PARTIAL | Five contextual edges now enforced; repository-wide consumers/integration remain |
| INV-READINESS-001 | CODE_ENFORCED / VALIDATED_AT_RUNTIME for T12C scope; repository PARTIAL | T12D/T12E and other explicitly out-of-scope edges |

The original audit registry is not silently rewritten as a later certified baseline.

## 27. Residual Phase-3 risks

Producer Regime sparse-history sufficiency and retained stale diagnostic gate/sizing are
unchanged. Sector aggregate DEGRADED does not provide per-row consumer permission. Other
IBMI consumers, Regime/Sector producer dependencies and CERI alignment context are outside
the five-edge scope. Unsealed legacy/current previews remain conservative. Winner and Phase-3
integration are deferred. Phase-2 privileged SQL/bulk mutation governance remains its existing
boundary; this task performs no historical rewrite or production repair.

No migration, column, constraint or new schema head is required. View-only relationships reuse
existing foreign keys. No authoritative database write, production pipeline execution, provider
call, production rewrite or legacy backfill occurred. Test databases use the guarded disposable
`swinglens_pytest_*` namespace on the task-owned PostgreSQL 16 container, never production.

## 28. T12D handoff

Certify Winner's remaining Technical quality policy plus exact Ranking -> Winner,
Regime -> Winner and Sector -> Winner permission while retaining its independent graph.
Keep existing explicit insufficient-Technical exclusion and Phase-1/2 source identity/evidence
rules. Do not add Setup/Lifecycle/CERI -> Winner or IBMI -> Winner. T12E owns integrated
Phase-3 certification and complete cross-consumer inventory/finding reconciliation.

## 29. Final verdict

PASS. All required final verification lanes passed. The five contextual edges have no material
consumer bypass. Fifteen READY captures exactly match T12B. Native producer diagnostics,
optional missing-input formulas, historical meaning, cross-run Regime compatibility, and
T12A/T12B/Phase-1/Phase-2/Phase-0 guarantees remain intact. No schema migration, production
rewrite or legacy backfill is required. Required final lanes have zero failures/skips;
the broad lane's seven existing external/e2e deselections and existing deprecations are reported.

The implementation is reviewable in sixteen production files, ten test/helper files, two
opt-in QA scripts, this complete 29-section report and the appended readiness architecture.
The one explicitly staged commit is `fix: enforce contextual readiness across consumers`;
its final SHA and clean worktree verification are reported outside its own contents.
No merge or push is authorized or performed. Phase-3 remains partial pending T12D/T12E and
the explicitly documented producer/out-of-scope residuals.

Changed-file manifest (30 files, explicitly staged):

```text
app/models/ib_market_intelligence_tables.py
app/models/tables.py
app/services/contextual_consumer_eligibility.py
app/services/core_calculation_evidence.py
app/services/ranking_profile_engine.py
app/services/ranking_profile_service.py
app/services/ceri/capture_service.py
app/services/ceri/decision_evidence.py
app/services/ceri/event_risk_service.py
app/services/setup_lifecycle/actionability_policy.py
app/services/setup_lifecycle/alert_service.py
app/services/setup_lifecycle/confidence_service.py
app/services/setup_lifecycle/family_adapters.py
app/services/setup_lifecycle/lifecycle_engine.py
app/services/setup_lifecycle/snapshot_builder.py
app/services/setup_lifecycle/source_loader.py
tests/contextual_readiness_helpers.py
tests/test_contextual_consumer_eligibility.py
tests/test_ceri_contextual_eligibility.py
tests/test_t12c_ready_scenarios.py
tests/integration/test_contextual_consumer_eligibility_postgresql.py
tests/setup_lifecycle/test_snapshot_builder.py
tests/test_ranking_profile_engine.py
tests/test_combined_ranking_identity_adoption.py
tests/test_contextual_calculation_identity_adoption.py
tests/test_phase1_calculation_identity_certification.py
scripts/qa/t12c_behavior_probe.py
scripts/qa/t12c_compare_behavior.py
docs/architecture/SWINGLENS_READINESS_CONTRACT.md
docs/remediation/calculation-lineage/T12C_contextual_consumer_readiness_enforcement.md
```

PostgreSQL 16.13 certification used only the task-owned
`swinglens-t12c-pg-20260916` container. After all PG lanes, zero `swinglens_pytest_*`
databases were verified, then that container was stopped/removed. The detached T12B
comparison worktree was removed after deleting only its three task-owned copied files.
Both unrelated performance worktrees and Prometheus/Grafana containers remain intact.
Ignored local QA logs/comparison payloads remain available for inspection. No authoritative
database, production runtime, provider or historical artifact was changed.
