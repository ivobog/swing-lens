# T12D Winner and cross-consumer readiness enforcement

## 1. Executive verdict

PASS after all required final verification gates. T12D enforces
Technical, Ranking, Regime and Sector permission at new certified Winner acquisition.
Only exact ELIGIBLE evidence contributes. Technical and Ranking are required;
Regime and Sector use existing model-defined missing representations. Capture-time
decisions are sealed without changing the business vector hash, probability math,
cohort rules, outcome truth, publication rules or historical predictions.

The enumerated major Phase-3 graph has 12 enforced edges and no remaining material
consumer bypass. This is scoped consumer enforcement, not repository-wide producer
sufficiency certification. T12E integration and the documented producer/policy
residuals remain open.

## 2. Baselines

| Baseline | Full SHA |
|---|---|
| Original audited | 3a9d47063be996908b7d5d1cc5769e0bbd033546 |
| Phase-2 certified | 7b015124807b8f895911d7e790fcbf01949ff85f |
| T12A | 4c8eb3402b951482d590890e726195a3c445d786 |
| T12B | b5af43ca8690cb65980be79c4a453ee1d4f7739e |
| T12C / T12D starting HEAD | 40f7f1bfa54fe2914f1f9f73ffb8c84026fedf01 |

Starting branch was `codex/t12c-contextual-consumer-readiness-enforcement`, clean.
T12D branch is `codex/t12d-winner-readiness-enforcement`. Final commit SHA is reported
outside its own contents. The original audit registry is preserved as historical evidence.

## 3. T12D scope

Four independently versioned source policies; exact acquisition integration;
registry-based completeness validation; frozen decision lineage; narrow ORM guards;
Ranking evidence scope validation; cohort evidence preloads; focused adversarial,
native PostgreSQL, READY parity and inherited regression verification.

T12A producer mappings, T12B/T12C policy versions, weights, feature definitions,
thresholds, model registry, outcome truth, priors, generation/publication logic and
historical prediction data are unchanged. Fundamental/Combined policy expansion
is not inferred from this task. No provider or authoritative database operation occurs.

## 4. Winner acquisition dependency graph

Native capture: frozen MarketCalculationContext + immutable Decision Handoff ->
exact Raw/Fundamental/Technical/Combined/Ranking/Regime/Sector identity membership ->
four independent permission decisions -> frozen source projections -> vector
completeness and existing PIT validation -> prediction identity -> atomic prediction,
episode, temporal/training evidence, pending outcomes and estimate persistence.

`calculation_identity.acquire_winner_sources` is the sole certified acquisition.
`capture_service.capture_run` performs acquisition before opening the per-item write
transaction. `_capture_ticker` rejects a real SQL Session without acquisition.
`feature_extractor._canonical_feature_json` uses only projected sources for the
four targets. Rescore, maturation and generation depend on existing frozen vectors.

Repository search found an unused `setup_lifecycle_features` injection hook. No
repository reader populates it and no configured Winner schema defines those
features. Certified acquisition now rejects a nonempty hook; generic math fixtures
retain their original implementation. No CERI/Setup/Lifecycle/Alert/IBMI dependency
is introduced.

## 5. Winner source requirement matrix

| Source | Initial audit classification | Actual feature use | Requirement / consequence |
|---|---|---|---|
| Raw | NOT_APPLICABLE to calculated-producer readiness | ticker, sector, provenance | Mandatory base fact; exact handoff semantic membership |
| Fundamental | NO_PRODUCER_READINESS_POLICY | independent score and coverage | Optional nullable_warning; identity-invalid/missing remains absent; existing coverage diagnostics, no new Winner Fundamental policy |
| Technical | T12D_TARGET | score, band, trigger, quality, RR | MANDATORY; reject non-ELIGIBLE |
| Combined | ALREADY_SAFE for existing native incomplete gate | combined score/band, setup family, RR/earnings context | Required existing source/completeness gate; broader readiness states are not certified by a new policy |
| Ranking | T12D_TARGET | first compatible ranking_profile, setup/earnings fallback where used | MANDATORY because ranking_profile is required_for_eligible_prediction; reject non-ELIGIBLE |
| Regime | T12D_TARGET | regime/family/risk state | OPTIONAL / MODEL_DEFINED_MISSING; omit to None + existing warnings |
| Sector | T12D_TARGET | state/rank/leadership bucket | OPTIONAL / MODEL_DEFINED_MISSING; omit to None + existing warnings |

These classifications match T12A's Fundamental coverage/Combined completeness
inventory. No newly discovered contradiction outside the four targets was hidden.
Fundamental lacks a dedicated Winner policy and Combined's safety claim is limited
to its native incomplete condition; neither is falsely covered by the new policies.
Ranking profile_score/profile_rank are retained source diagnostics, not newly added
model feature definitions. A copied Combined Fundamental score in prediction
denormalization is DISPLAY_ONLY: Bayesian/cohort feature_json uses the independently
validated Fundamental source and cannot resurrect that omitted feature.

## 6. Consumer policy architecture

`winner_probability/consumer_eligibility.py` reuses TechnicalConsumerPolicy and
ContextualConsumerPolicy with T12A ProducerReadinessEnvelope and typed
ConsumerEligibilityDecision. Exact envelope/evidence binding, identity fingerprint,
kind and ownership are validated; Ranking additionally checks ticker/profile.
Sector resolves its exact row from the snapshot evidence payload.

| Readiness | Each of the four v1 decisions | Use |
|---|---|---|
| READY | ELIGIBLE | Included |
| DEGRADED | POLICY_UNDECIDED | No permission |
| INSUFFICIENT_EVIDENCE | INELIGIBLE | No permission |
| ERROR | INELIGIBLE | No permission |
| STALE | INELIGIBLE | No permission |
| UNKNOWN | POLICY_UNDECIDED | No permission |
| LEGACY_UNKNOWN | POLICY_UNDECIDED | No permission |

Blocking reasons deny permission. The envelope constructor already prevents a
contradictory READY/blocking envelope; blocking UNKNOWN is independently tested.
Policy names are source-specific, not one aggregate winner-readiness version.

## 7. TECHNICAL_TO_WINNER

`technical-to-winner-v1`, consumer WINNER, required. Exact frozen native readiness
precedes score, trigger, band, quality and RR extraction. Retained positive Technical
numbers cannot override insufficient, errored, degraded or unknown evidence.
Native NORMAL/false-insufficiency READY retains exact business behavior. Native LOW
is DEGRADED and not granted use; `ok` remains UNKNOWN rather than an invented READY alias.

## 8. Existing insufficiency gate migration/preservation

Native `Technical.insufficient_data=true` remains a hard exclusion. T12A supplies
INSUFFICIENT_EVIDENCE with TECH_INSUFFICIENT_HISTORY, so the typed Winner policy
subsumes this native gate at acquisition. `insufficient_completed_bars` remains the
machine-readable reason. Certified capture now writes no excluded numeric vector
or dependent artifacts for that rejected source. The original generic extractor
gate remains compatible for legacy math tests; it cannot bypass certified SQL acquisition.

## 9. RANKING_TO_WINNER

`ranking-to-winner-v1`, required. Identity/handoff filtering and existing ranking
ordering precede evaluation of the first actual feature source. Missing required
ranking_profile is no longer allowed to yield a certified vector merely with a warning.
Incomplete, unknown, legacy, degraded or errored selected Ranking rejects capture.
Rank 0 / No new entry is not evidence of readiness; no new numeric sentinel rule
is invented. Other inventory Ranking rows remain audit references and cannot replace
the selected blocked row. Evidence kind, owner, ticker, profile and identity must match.

## 10. REGIME_TO_WINNER

`regime-to-winner-v1`, optional. Non-ELIGIBLE exact Regime contributes no label,
family or risk state. Native stale/severely-stale warnings block even retained
Confirmed Uptrend / Green values. Missing representation is None with existing
warnings, and otherwise valid Technical/Ranking can still capture. No extra entry
veto, sizing formula or history-length threshold is added.

## 11. SECTOR_TO_WINNER

`sector-to-winner-v1`, optional. Non-ELIGIBLE exact snapshot contributes no state,
rank or leadership bucket; Leading/rank 1 cannot authorize an insufficient aggregate.
Only the selected snapshot's exact frozen sector row supplies eligible values.
Omission preserves the model's native nullable context, without zero or substitution.

## 12. DEGRADED policies

Each edge was reviewed independently against Winner configuration and the feature
registry. Technical quality has no degraded-use grant; the required Ranking profile
has no degraded permission convention; optional Regime low-confidence diagnostics
do not grant Winner use; optional Sector aggregate confidence does not permit
degraded snapshot numerics. Each v1 returns POLICY_UNDECIDED for DEGRADED. No new
threshold, configuration override or global degraded permission is introduced.

## 13. UNKNOWN/legacy policies

UNKNOWN and LEGACY_UNKNOWN never become implicit permission. Required unknowns
reject with `winner_readiness_undecided`; optional unknowns remain omitted. Unsealed
inputs cannot be auto-normalized from present numeric values during acquisition.
A declared but unresolved evidence pointer is an evidence/identity failure, not
a silently promoted legacy source. The original unloaded identity-only fixture is
retained as an explicit rejection regression, beside separate native READY fixtures.

## 14. Mandatory vs optional source behavior

Requiredness comes from FeatureSchemaRegistry's existing missingness policies, not
an arbitrary omit-all/reject-all strategy. Technical and ranking_profile must exist.
Regime and Sector have nullable_warning representations. All four decisions are
computed before required rejection; mixed combinations preserve independent
inclusions/omissions. Required vector members are validated for None/empty string;
legitimate zero values are not mistaken for missing or fabricated as substitutes.

## 15. Capture atomicity

No target eligibility decision occurs after a partial prediction write. Required
rejections increment excluded, retain all four decisions in readiness_rejections,
and persist no prediction/episode/outcome/estimate. Other feature/identity failures
retain existing failure classification. Subsequent persistence remains per-ticker
transactional. Native PostgreSQL checks all five artifact tables are empty after
Technical insufficiency or late Ranking incompleteness. Capture preparation only
loads existing evidence; rejected rows cannot enter training/cohorts or generations.

## 16. Eligibility evidence immutability

NativeReadinessMetrics freezes the full canonical four-source decision set before
returning acquisition. Prediction lineage `winner_consumer_eligibility` contains
producer envelope/fingerprint, decision policy/version/reasons/evidence ID,
source IDs, requiredness and included/omitted flags. Exact source artifact inventory
retains certified omitted evidence for audit. The canonical set participates in
prediction Calculation Identity. ORM guards reject edits/removal and retroactive
addition of the sealed member; ordinary operational lineage remains mutable.
Native PG verifies mutation rejection after a successful capture/rescore.

## 17. Frozen vector semantics

Eligible target values come from immutable evidence projections. Source clocks
remain the exact selected row's original clocks for the separate PIT audit.
Decisions live outside feature_json and feature_vector_hash, preserving READY
business hash parity. Acquisition identity changes when source permission/policy
changes, preventing an active prediction from being overwritten on a prospective
v2 retry. Separate new-capture fixtures use all four explicitly selected v2 policies
without changing the READY vector. No feature schema version, configured core feature
or business formula changes.

## 18. Historical Winner reads

Existing predictions retain frozen feature_json, source references and any stored
capture-time decisions. Historical reads never invoke new producer eligibility.
Pre-T12D predictions are not retroactively certified or assigned a v1 decision.
Native PG advances current Technical evidence, reloads the frozen prediction, and
verifies identical vector/lineage with policy/acquisition functions forbidden.

## 19. Rescore behavior

Actual ProbabilityEstimator latest rescore derives cohorts and probability from
the frozen vector. Unit tests reseal all four current sources with non-permitted
native readiness and forbid acquisition
and policy evaluation, then produce a rated estimate. Native PG repeats this on SQL
with legitimately insufficient historical cohort evidence; probability remains
honestly insufficient rather than fabricated. Frozen vector and decisions remain equal.

## 20. Maturation behavior

Actual OutcomeMaturationService uses frozen entry/outcome contracts and eligible
price bars. Current Technical ERROR / Ranking incompleteness do not prevent native
forward and target/stop maturation. The guarded regression matures a 3% close return
and a true primary winner while acquisition/policies are forbidden. Existing
temporal, source-availability, revision and truth/publication tests remain intact.

## 21. Cohort/generation impact

Hierarchy, Bayesian prior, minimum counts, target manifests, generation construction,
serving model rules and publication fences are unchanged. Newly rejected captures
cannot create training members or cohort inputs; any resulting prospective cohort
membership reduction is EXPECTED REMEDIATION CHANGE. Historical cohorts/predictions
are not rewritten and generations do not reevaluate current source readiness.

## 22. Current/global fallback prevention

Readiness is evaluated after exact identity/handoff selection. Ineligibility never
restarts selection. Adversarial cases provide newer/current READY alternatives for
each target; optional context stays absent, required capture stays rejected. Ranking
cannot skip its first compatible blocked profile for a READY second one. Regime
global/current projection cannot replace the exact handoff-pinned blocked snapshot.
Semantic tampering fails the handoff before values can be used.

## 23. Cross-run Regime behavior

The existing compatibility policy permits exact READY Regime run 99 for Winner run 7
when cutoff, session, calendar, configuration and algorithm match. Incompatible newer
run-local context is ignored during identity selection. Native PG captures with the
cross-run exact evidence. Cross-run compatibility supplies identity permission only;
stale/unknown/degraded exact evidence still receives no Winner use permission.

## 24. Same-value/different-readiness tests

Separate fixtures retain Technical 8.4, Ranking 8.6, bullish Regime and Sector rank 1
while changing native or explicit stored contract readiness. Required sources reject;
optional features become None; numeric retention never authorizes use. Typed tests
cover all seven states for all four policies and the full 12-edge graph. Native
fixtures use real T12A normalizers/writers; defensive statuses not emitted natively
are stored contract tests, not invented producer thresholds or always-eligible mocks.

## 25. READY parity

Six shared scenarios run on detached T12C and current T12D: canonical, compatible
cross-run with newer incompatible context, missing Regime, missing Sector, missing
both optional contexts, and historical replay. Opt-in QA hooks capture complete
feature_json, vector hash, exact source IDs, eligibility/exclusion and rated Bayesian
point/interval/sample/grade results. Strict comparison finds 24 equal captures:
WinnerFeatures 6, Winner 6, WinnerEstimate 6, WinnerProbability 6.
Unexpected changes = 0. Canonical comparison fingerprint:
`729bca53e4d0b0ead90110077d4ddb3138714fc99ef15ee99bc0217ada12c3d8`.

Identity/permission lineage intentionally differs prospectively and is not a
business parity exception. Both sides independently use native READY evidence;
baseline producer/calculation code is not modified. Only three opt-in helper/probe
files are copied into the detached baseline; original legacy fixtures remain unchanged.

## 26. Winner static audit

Counts below identify explicitly enumerated audit units; historical/display units
are not extra producer edges. Only four target edges form REMAINING_WINNER_BYPASS.

| Classification | Count | Enumerated units / boundary |
|---|---:|---|
| T12D_ENFORCED | 4 | Technical, first selected Ranking, exact Regime, exact Sector -> acquisition/vector |
| ALREADY_SAFE | 2 | Raw handoff membership; Combined native incomplete source gate (condition-scoped) |
| FROZEN_HISTORICAL_READ | 4 | prediction reads; rescore; maturation; cohort/generation |
| DISPLAY_ONLY | 1 | copied Fundamental score denormalization outside feature_json |
| LEGACY | 1 | generic object-db math path; real Session persistence requires acquisition |
| NO_PRODUCER_READINESS_POLICY | 1 | previously inventoried Fundamental -> Winner coverage semantics |
| PRODUCER_GAP | 2 | sparse NORMAL Regime; retained bullish stale diagnostics |
| REMAINING_WINNER_BYPASS | 0 | Four T12D targets |

Search traced producer models, extractor use, repository loaders, private capture,
rescore/outcome/generation modules, scripts and the inactive Setup injection hook.
There is no alternate certified SQL capture path or direct CERI/IBMI/Setup dependency.
Fundamental policy absence and broader Combined readiness are explicit inventory
limits, not falsely counted as remediated edges or new producer defects.

## 27. Cross-consumer readiness audit

| Stage | Edge | Native enforcement point |
|---|---|---|
| T12B | Technical -> Combined | existing Combined Technical projection before calculation |
| T12B | Technical -> Ranking | ranking_profile_engine before profile calculation |
| T12B | Technical -> Setup/Lifecycle | snapshot_builder then frozen permission propagation |
| T12C | IBMI liquidity -> Ranking | ranking_profile_engine before tradeability overlay |
| T12C | IBMI volatility -> CERI | exact capture selector before event-premium risk |
| T12C | IBMI short pressure -> CERI | independent selector before risk reason |
| T12C | Regime -> Setup/Lifecycle | snapshot_builder then engine/confidence/actionability/alert propagation |
| T12C | Sector -> Setup/Lifecycle | exact frozen row before Setup context and downstream propagation |
| T12D | Technical -> Winner | exact acquisition before frozen vector |
| T12D | Ranking -> Winner | exact first feature source before frozen vector |
| T12D | Regime -> Winner | exact optional context before frozen vector |
| T12D | Sector -> Winner | exact optional snapshot/row before frozen vector |

T12B_ENFORCED=3; T12C_ENFORCED=5; T12D_ENFORCED=4; known-edge total=12.
The companion audit units from section 26 retain ALREADY_SAFE=2, DISPLAY_ONLY=1,
LEGACY_CURRENT=1 and PRODUCER_GAP=2; Fundamental NO_PRODUCER_READINESS_POLICY=1
is separately recorded. These are not extra remediated-edge counts.
REMAINING_CONSUMER_BYPASS=0 for the enumerated known major graph. The 84-case runtime
matrix exercises 12 actual policy objects x seven states. No global UNKNOWN/degraded
override, omission-triggered replacement or negative dependency is added.

## 28. Producer sufficiency gaps

CORE-001 remains PARTIAL: sparse Regime with native LOW is DEGRADED and denied,
but sparse SPY history still emitted NORMAL/READY remains eligible under the supplied
producer contract. Winner introduces no guessed minimum-bar threshold.
CORE-002 consumer use of correctly marked stale bullish Regime is blocked; native
producer gate/sizing diagnostics may remain bullish. Producer diagnostics are not fixed.
Technical STALE is defensively denied but currently NATIVE_NOT_APPLICABLE as a
producer emission; no freshness threshold is invented. Fundamental coverage policy
absence/Combined condition-only safety are the pre-existing separate policy inventory,
not claimed as closed producer defects.

## 29. Performance/query impact

Technical, Ranking, Regime and Sector evidence relationships are select-in loaded
by Winner's bulk repository. Ranking reuses its existing evidence_id FK through a
view-only lazy-raise relationship. Real native PG listener measurement verifies
seven complete acquisitions after preload issue zero SELECTs. No per-policy,
per-ticker or per-profile evidence fetch is added to that path. Existing lineage
update guards retain their bounded stored-row read. This is permission-query
certification, not a new large-universe throughput benchmark or performance rewrite.

## 30. PostgreSQL certification

Only task-owned PostgreSQL 16 container `swinglens-t12d-pg-20260916`, loopback port
55433, guarded `swinglens_pytest_*` databases. Each native fixture applies the complete
unchanged Alembic chain and is safely dropped. New four-case lane covers READY,
Technical native insufficiency, Ranking native incompleteness and both optional
contexts blocked, including real evidence writers/source edges, loader preloads,
cross-run Regime, SQL atomicity, exact retries, current evidence advancement,
historical reload, actual SQL rescore and ORM decision mutation rejection.

Preliminary fixture defects were corrected without weakening production checks:
PostgreSQL-normalized values must be loaded before freezing handoff fingerprints;
historical source and pointer-update availability clocks must share the historical
fixture anchor; the assertion must use the actual decision DTO evidence-ID member.
Required PIT and identity failures remained active. Native policy-version changes
also verify prospective v2 decisions and rejection of active v1 overwrite. Required
rejection retries retain all four decisions and empty artifact tables. A separate
native READY current evidence projection for blocked Technical/Ranking cannot
replace the handoff-pinned source. Final results are in section 31.
No production DB, pipeline, historical artifact or provider call is involved.

## 31. Test results

| Final lane | Passed | Other results |
|---|---:|---|
| T12D focused units | 74 | 1 existing warning; 61 policy/acquisition + 7 frozen operations + 6 READY scenarios |
| Winner full subsystem | 377 | 1 existing warning; includes 19 identity-adoption cases |
| Cross-consumer matrix | 84 | Included with Winner in a 461-pass final lane |
| T12C focused | 82 | Included in final 168-pass inherited foundation lane |
| T12B focused | 41 | Included in final 168-pass inherited foundation lane |
| T12A foundation | 45 | Included in final 168-pass inherited foundation lane |
| Full inherited consumer lane | 913 | 1 existing warning |
| Phase-2 native integration | 63 | 20 existing warnings; unchanged migration/schema/drift/historical certification |
| Phase-1 identity | 135 | 1 existing warning |
| Phase-0 safety | 141 | 40 existing warnings; includes 36 native PG tests |
| New Winner PostgreSQL | 4 | 5 existing warnings; final strengthened exact pins/v2/rejected retries/current-projection attacks |
| Readiness guard PG rerun | 5 | 6 existing warnings; new 4 + inherited readiness test, overlapping counts |
| Broader native repository | 2901 | 7 existing external/e2e deselections; 22 existing warnings |
| READY parity | 24 captures | Strict equality, zero unexpected changes |
| Static gates | All pass | Ruff app/tests/scripts; compileall; diff check; one unchanged Alembic head |

Zero failures/skips in final lanes. Counts overlap and must not be summed as unique
repository tests. The 14 required native integration modules contain 103 tests:
63 Phase-2 + 4 new Winner + 36 Phase-0; guard reruns are overlapping. No flaky tests
or new suppressions. Existing warnings are Starlette/httpx, Python 3.12 SQLite
datetime adapter and Alembic path_separator deprecations; line-ending notices are
Git's existing Windows configuration, not test failures.

Required test-family coverage (counts overlap):

| Family | Native / adversarial evidence |
|---|---|
| 1 Technical READY parity | complete six-scenario vector/source/hash/probability comparison |
| 2 Technical insufficiency | actual normalizer, retained reason, empty native SQL artifact tables |
| 3 Technical DEGRADED | native LOW; no implicit quality permission |
| 4 Technical UNKNOWN/legacy | native ok, unsealed evidence, original unloaded fixture retained |
| 5 Ranking READY parity | exact first compatible profile and frozen evidence pins |
| 6 Ranking blocking/unknown | native incomplete + defensive contract-state cases |
| 7 rank-0 sentinel | No new entry cannot authorize blocked Ranking |
| 8 Regime READY parity | full regime/family/risk fields and probability comparison |
| 9 stale bullish Regime | native stale warning overrides retained bullish label |
| 10 Regime UNKNOWN/legacy | absent features + full considered-source metadata |
| 11 Sector READY parity | exact frozen row, state/rank/bucket |
| 12 strong ineligible Sector | Leading/rank 1 retained but omitted |
| 13 mixed sources | each independent required rejection / optional omission |
| 14 multiple invalid | both optional, required+optional, both required |
| 15 atomicity | five SQL tables empty; no episode/outcome/estimate leak |
| 16 same value/different readiness | unchanged positive numerics under different permission |
| 17 all UNKNOWN/legacy | four-policy and 12-policy matrices; no auto-promotion |
| 18 ERROR | native Technical error and defensive producer contract states |
| 19 STALE | native Regime; defensive seven-state matrix; Technical emission N/A |
| 20 zero substitution | invalid optional values None, missing required score rejects |
| 21 current/global fallback | four independent adversarial replacement cases |
| 22 cross-run Regime | compatible run 99 -> Winner 7 including native PG |
| 23 immutable decisions | canonical acquisition snapshot + real ORM guard |
| 24 rescore | actual rated unit and honestly insufficient native SQL estimate with policies forbidden |
| 25 maturation | actual service/bar truth, policies forbidden, forward and target winner matured |
| 26 retries | original vector/decisions/artifact counts retained |
| 27 policy version | four prospective v2 policies, active overwrite conflict, stored v1 unchanged |
| 28 handoff/identity | retained source tampering/context/owner/calendar/config regressions |
| 29 negative edges | repository search + certified Setup injection rejection |
| 30 T12C | 82 focused units plus native contextual PG and full consumer lane |
| 31 T12B | 41 focused units plus native Technical consumer PG |
| 32 T12A | 45 foundation units plus native immutable readiness PG |
| 33 Phase-2 | 63 inherited native cases + new Winner cases |
| 34 Phase-1 | 135 retained identity cases |
| 35 Phase-0 | 141 retained temporal/fence/publication cases including native PG |
| 36 cross-consumer matrix | 84 = 12 known actual policies x seven readiness states |

Commands and lane boundaries:

```text
pytest tests/winner_probability tests/test_phase3_consumer_readiness_matrix.py -q -ra
pytest tests/test_contextual_consumer_eligibility.py tests/test_ceri_contextual_eligibility.py
  tests/test_t12c_ready_scenarios.py tests/test_technical_consumer_eligibility.py
  tests/test_t12b_ready_scenarios.py tests/test_producer_readiness.py
  tests/test_ranking_profile_engine.py tests/test_ranking_profiles_golden.py
  tests/test_ranking_profile_service.py tests/setup_lifecycle tests/ceri -q -ra
pytest tests --ignore=tests/integration --ignore=tests/e2e
  --ignore=tests/test_migration_remediation.py --ignore=tests/qa/test_qa_infrastructure.py
  -m 'not external and not e2e' -q -ra
```

All use repository `.venv/Scripts/python.exe -m`. Phase-1's six identity modules and
Phase-0's twelve domain/session/preflight/Winner/generation/publication/CERI/IBMI
modules retain the T12C native lane boundaries. Phase-2's ten evidence/historical/
certification/readiness/Technical/contextual PostgreSQL modules are retained; the
new Winner integration module adds four native cases. The final guard rerun also
includes existing readiness PG tests. Required full migrations are tested here;
excluded unrelated migration-remediation/QA subprocess/browser/provider lanes are
not silently called certified. No new skip, xfail, weakened assertion or deleted test.
Initial production metrics-constructor errors were fixed; contradictory READY/blocking
fixture construction, separate READY evidence fixtures, native SQL fixture details,
and exact exception-class assertions were corrected with retained negative coverage.
The preliminary broad run was superseded after final guard/scope changes; it is
not reported as final certification or a flaky pass.

## 32. Finding reconciliation

| Finding | T12D disposition | Residual |
|---|---|---|
| WIN-003 | Readiness acquisition gap closed for four targets | profile/configuration authority work remains separate |
| WIN-004 | Consumer quality/readiness enforced in four-target scope | Fundamental coverage policy and broader configuration semantics not blanket-certified |
| WIN-005 | Correctly marked insufficient/unknown/degraded/stale target use prevented | producer-side sufficiency defects remain PARTIAL |
| CORE-001 | PARTIAL | sparse native-NORMAL Regime producer gap |
| CORE-002 | stale Regime consumer bypass blocked for Setup and Winner | native producer gate/sizing diagnostics unchanged |
| XINT-004 | known major graph enforced (3+5+4) | T12E repository/integration certification pending |
| INV-READINESS-001 | CODE_ENFORCED / VALIDATED_AT_RUNTIME for 12 edges | repository-wide CERTIFIED pending T12E and explicit policy/producer residuals |

No original audit finding is retroactively rewritten into a later certified baseline.

## 33. INV-READINESS status

New certified acquisitions cannot use the four target sources without ELIGIBLE
permission. Frozen downstream operations cannot restore omitted source influence
by querying current numerics. Together with T12B/C, the known major 12-edge graph
is enforced and runtime validated with zero remaining scoped bypass. Certification
does not imply that every producer sufficiency diagnostic or every repository
consumer has an independently audited policy. T12E owns that integrated conclusion.

## 34. Residual Phase-3 risks

Sparse Regime sufficiency, retained stale producer diagnostics, pre-inventoried
Fundamental/Combined policy limits, other IBMI/context producer edges and later
configuration authority remain outside the four-target remedy. Legacy/pre-T12D
predictions retain old frozen semantics and are not retrospectively certified.
Privileged SQL/bulk-mutation governance retains the Phase-2 boundary; no repair,
rewrite, backfill or new production eligibility interpretation is performed.

## 35. T12E handoff

Certify integrated Phase-3 behavior and reconcile the complete known graph and
explicit residual inventory against T12A/B/C/D commits. Preserve independent Winner
acquisition, frozen historical vectors, source-specific policy versions, no fallback,
no zero substitution, negative edges, identity/temporal fences and truth/publication.
Do not claim producer sparse-history/configuration issues fixed by consumer denial.
No migration or legacy backfill is required to consume T12D's new frozen decisions.

## 36. Final verdict

PASS. All required final lanes pass. REMAINING_WINNER_BYPASS=0 for four targets;
REMAINING_CONSUMER_BYPASS=0 for the enumerated major 12-edge graph. READY business
outputs exactly match T12C. New required rejection is atomic, legitimate optional
omission is model-defined, exact source/decision references remain frozen, and
historical rescore/maturation/cohort/publication semantics are preserved. Producer
and pre-inventoried policy residuals remain explicit; repository-wide Phase-3
certification is deferred to T12E.

One focused explicitly staged commit: `fix: enforce readiness at winner acquisition`.
No merge or push. No migration/column/schema head change, production rewrite or
legacy backfill. Changed-file manifest (18 files):

```text
app/models/tables.py
app/services/contextual_consumer_eligibility.py
app/services/winner_probability/consumer_eligibility.py
app/services/winner_probability/calculation_identity.py
app/services/winner_probability/capture_service.py
app/services/winner_probability/feature_extractor.py
app/services/winner_probability/repository.py
tests/winner_probability/readiness_capture_helpers.py
tests/winner_probability/test_consumer_readiness.py
tests/winner_probability/test_readiness_frozen_operations.py
tests/winner_probability/test_t12d_ready_scenarios.py
tests/winner_probability/test_winner_calculation_identity_adoption.py
tests/test_phase3_consumer_readiness_matrix.py
tests/integration/test_winner_consumer_eligibility_postgresql.py
scripts/qa/t12d_behavior_probe.py
scripts/qa/t12d_compare_behavior.py
docs/architecture/SWINGLENS_READINESS_CONTRACT.md
docs/remediation/calculation-lineage/T12D_winner_readiness_enforcement.md
```

PostgreSQL 16.13 used only the task-owned container. Zero `swinglens_pytest_*`
databases were verified after all native lanes, then that container was stopped and
removed. The detached T12C comparison worktree was removed after deleting only its
three task-owned copied files from the verified absolute baseline path. Both
unrelated performance worktrees and Prometheus/Grafana containers remain intact.
Ignored local QA logs and full parity payloads remain available. No authoritative
database/runtime, provider, historical artifact or production pipeline was changed.
