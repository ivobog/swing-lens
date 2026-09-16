# T13C — Contextual configuration authority adoption

## 1. Executive verdict

**PASS.** Final executable certification is recorded in sections 32 and 36.
Regime, Sector, CERI's four outputs and five IBMI metrics use the existing T13A
snapshot and T13B immutable evidence contract. Their effective values are frozen
before calculation and bound to the existing Calculation Identity configuration
dimension. No migration, production rewrite or legacy backfill is required.

## 2. Baselines

| Baseline | Commit |
| --- | --- |
| Original audit | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase 2 | `7b015124807b8f895911d7e790fcbf01949ff85f` |
| Phase 3 | `5422bcdf7703db891810d9e9c20a8f1770241fc9` |
| T13A | `7fa173c1e34752d457043fce5b4c5f22ec6e25d5` |
| T13B / starting HEAD | `ea8d48a510acbee9b88886390a9f775a36a4510a` |

Branch: `codex/t13c-contextual-configuration-adoption`. One focused PASS commit
is authorized: `feat: bind contextual calculations to effective configuration`.
No merge or push is authorized/performed. The commit containing this report is
the final implementation commit; its immutable hash is reported in the task reply.

## 3. Scope

Native parsers, contextual typed adapters, actual financial consumers, identity
builders and existing immutable evidence writers/readers are adopted. Fixture
updates give synthetic newly certified artifacts their actual matching snapshots
and preserve legacy/readiness blocking assertions. No model/migration/formula or
readiness permission redesign is included. Downstream authority and durable
delivery belong to T13D; original-context reconstruction belongs to later work.

## 4. Contextual dependency graph

Regime feeds certified contextual selection. Sector consumes exact Ranking and
Regime evidence and may consume a compatible prior Sector. Ranking consumes
certified IBMI liquidity; CERI consumes certified IBMI volatility/short pressure
under existing separate permissions. CERI remains contextual output. Exact
upstream evidence pins carry producer rules; downstream snapshots contain own
rules rather than copies of upstream configuration. No forbidden CERI-to-
Ranking/Setup/Winner, IBMI-to-Winner or Sector-to-same-run-Ranking edge is added.

## 5. Regime configuration inventory

Family `contextual.regime`, schema `contextual.regime-v1`, resolver
`resolve_regime_configuration`. The machine-readable companion inventory lists
every effective leaf, tagged value/type, default flag, actual winning source,
precedence, classification, consumers and binding state. Default Regime has 276
leaves: 215 behavioral, 61 display, zero operational and zero UNKNOWN winners.

Eight native policy roots are enabled, calculation_version, config_version,
symbols, freshness, risk_state_mapping, market_regime_params and policies.
All nine supported classifications
have complete risk mappings and policy values: size multiplier, preferred/allowed/
reduced/blocked profiles, allowed/blocked setups, score adjustment, summary and
warnings. Pine/v4 active feature groups and parser/default fallbacks are included.
Unused optional-symbol/use-SPY switches, unused feature groups and disabled-group
children are explanatory outside financial semantic hashing.

## 6. Regime authority/adoption

`load_market_regime_command_center_config` records native file/default winners.
`MarketRegimeCommandCenterService.build_snapshot` resolves before market-feature
calculation, checks an optional expected configuration anchor, rehydrates native
policy, and passes frozen Pine/v4 values into `_load_market_input`. The existing
repository writer receives the same snapshot. Native policy and outputs remain
unchanged; there is one authoritative resolver and no parallel identity scheme.

## 7. Regime complete-config proof

Schema validation requires the exact eight dataclass roots, nonempty Pine/v4
groups, every supported risk mapping and all ten policy fields/fallbacks. Material
UNKNOWN Regime provenance is rejected. The immutable payload carries the full
resolved snapshot with semantic and resolution hashes. Fresh-session PG history
checks retain E1/C1 after C2; unit checks prohibit live sources during decoding.
This supports CORE-008 closure for new certified Regime evidence only.

## 8. Cross-run Regime compatibility

The existing identity compatibility comparator retains temporal, calendar, market
context and source checks. Same context and C1 remain compatible across run IDs.
C1 versus C2 is incompatible even when the same inputs produce equal outputs.
The native PG case changes freshness configuration, retains exact financial
output, verifies different semantic identities, and exercises cross-run use.

## 9. Sector configuration inventory

Family `contextual.sector`, resolver `resolve_sector_configuration`, schema
`contextual.sector-v1`. Default: 116 leaves, 99 behavioral and 17 display.
The ETF-enabled variant freezes 278 leaves; the companion file records its
classifications independently. Own universe/taxonomy mappings, own selected
profiles, score weights, normalization/confidence cutoffs, state/permission
policy and active ETF proxy/Pine/v4 rules are frozen. Disabled ETF children/proxies
are display-only and do not acquire authority until the feature is enabled.

## 10. Sector authority/adoption

`load_sector_rotation_config` carries file/default metadata outside dict values.
`SectorRotationService.build_sector_rotation_snapshot` uses the frozen own config
before `SectorUniverseService.build`, `SectorEtfRotationService.build` and
`SectorRotationPolicyService.decide`. Native feature calls receive frozen Pine/v4.
The current result identity and repository evidence writer receive that snapshot.
Setup/Winner expected-Sector helpers now compare this same current producer
configuration; their own downstream policies are not adopted in T13C.

## 11. Prior Sector configuration semantics

S1 retains C1 in E1; changing current rules to C2 cannot rebind S1. Prior selection
preserves native raw-config-hash, operation mode, calendar and prior-context checks,
plus effective semantic configuration identity. The full own-config hash used by
native prior filtering is frozen as `selection.prior_config_hash` and verified
against frozen own config without files/defaults. An inactive ETF benchmark edit
can affect prior input eligibility through that filter, so its opaque hash is
behavioral even while the direct unused ETF clause is display.
Existing same-configuration prior
rules may correctly reject C1 as an input to C2. The native PG drift case verifies
unchanged Ranking/Regime parents, distinct current evidence and no improper C1
prior consumption. Historical inspection does not resolve present-day Sector rules.

## 12. CERI configuration inventory

Family `contextual.ceri`, resolver `resolve_ceri_configuration`, schema
`contextual.ceri-v1`. Default standalone adapter: 181 leaves, 158 behavioral,
8 operational and 15 display. Capture supplies a separately resolved consumer
variant. Fourteen native calculation roots are engine, providers, datasets,
metrics, revision, missing_values, currency_conversion, opportunity_weights,
event_risk, confidence, enabled_categories, price_response, taxonomy and config_hash.
Only classified behavioral leaves enter the semantic hash. Full Alert/backfill/
export/retention/change-threshold/posture-declaration trees are not copied into
the calculation snapshot. The full native parser still serves those downstream
services; the frozen scoring adapter fills those unused subsystem fields with None.

## 13. CERI authority/adoption

Opportunity, event risk, confidence and snapshot services resolve/consume frozen
native config before their behavior. Capture freezes actual capture and IBMI
consumer flags, its own volatility contribution cap and the existing revision-
feature config-hash filter before selecting inputs/calculating. That opaque full
legacy hash is behavioral because capture really uses it in a WHERE selection.
Alert-only file edits can therefore change selected inputs under the existing
algorithm; T13C freezes that truth instead of silently redesigning the filter.
Four-output standalone config without a capture selection hash cannot certify
capture and is rejected as an insufficient supplied capture context.

## 14. CERI four-output configuration

| Output | Frozen governing values |
| --- | --- |
| Opportunity Score | own component weights; revision windows/periods/coverage; guidance freshness, eligibility/action scores and timestamp/id selection; selected catalyst/price-response rules |
| Event Risk Score | earnings block/high/medium windows and scores; event risk bases/caps; actual own staleness penalty and options/IBMI contribution cap |
| Data Confidence | own weights, coverage/analyst windows, dataset staleness, confidence labels and frozen code-owned freshness/sample/timestamp scores |
| CERI Posture | actual code-owned 6/7/5/3 thresholds, insufficient criterion and output labels, rather than unused file declarations |

Native fixed formula constants continue under the existing algorithm version;
configuration-owned/native policy selection listed here is frozen explicitly.

## 15. Provider priority configuration

The configured provider tuple remains ordered in tagged serialization. Reversing
it changes semantic identity; no sorting/set conversion masks order. Native
`CeriProviderConflictService` consumes frozen provider priority followed by quality
penalty, freshness and source ID, including the frozen unknown-provider fallback.
The guidance scorer actively orders by effective timestamp/id and ignores provider
priority. That different native algorithm remains unchanged and explicitly frozen.
CERI-001's configuration portion is resolved; its algorithm defect is not closed.

## 16. CERI native rule/policy configuration

`native_policy` records posture, event_risk, confidence, guidance and provider roots.
Native file winners, code-owned defaults and explicit request winners stay distinct.
Adding capture consumer context preserves an already frozen C1 native policy and
its provenance. Untyped historical values are restored to native enums, tuples
and cutoff time without today's defaults. Incomplete native policy/consumer
roots fail historical certification. CERI's own stale treatment remains separate
from unchanged Phase-3 IBMI-to-CERI eligibility permissions.

## 17. IBMI configuration inventory

Resolver `resolve_ibmi_configuration`; five `<namespace>-v1` schemas.

| Family suffix | Behavioral leaves | Operational leaves | Actual behavioral scope |
| --- | ---: | ---: | --- |
| liquidity | 12 | 6 | 5/20/60-session windows, dollar-volume threshold, four spread grades, historical age, three versions |
| short_pressure | 9 | 6 | high/extreme fee, low/very-low availability, historical/live ages, three versions |
| volatility | 8 | 6 | IV expansion/lookback windows, high/extreme IV/HV ratios, historical age, three versions |
| options_activity | 7 | 7 | abnormal activity multiple, call/put thresholds, live age, three versions |
| histogram | 5 | 9 | high-activity fraction, low percentile, three versions |

Native missing fallbacks, including partial spread-grade dictionaries, are
materialized before calculation. Full transport/scanner/Flex trees are absent.
The native compatibility hashing boundary excludes secret-bearing keys and
login/cookies/authorization so secret values cannot affect retained digests.
Files without those keys retain their exact existing legacy hash.

## 18. IBMI authority/adoption

The native loader precomputes five cached family snapshots once per native context.
`_rebuild_ticker_feature_impl` freezes/rehydrates the selected module before input
queries/math and checks optional supplied C1/expected identity. Histogram math
receives the frozen histogram parameters before its evidence writer runs.
`persist_ibmi_feature_evidence` refuses newly certified evidence without a
precalculation snapshot. Exact metric/live/request/price constituents remain
pinned; no copy of their producer configuration enters another family.

## 19. Operational vs behavioral IBMI settings

Timeout/retry/pacing/request budget/concurrency/cache and host/port credentials
do not govern metric mathematics and do not enter semantic identity. Acquisition
period/use-RTH/generic-tick, shortlist and enable switches remain operational at
this scoped calculator boundary; evidence already freezes acquired constituents.
Unused module/freshness settings are operational, including the volatility CERI
cap which becomes CERI's own consumer rule when actually consumed there.
Safe legacy raw hash is operational compatibility metadata and is retained for
native immutable evidence-address reuse on retry. It is never rehashed from a
scoped subset. A changed transport setting may alter this diagnostic hash without
altering financial semantic identity. Full transport trees/secret values are absent.

## 20. Configuration granularity

Regime is per resolved market-policy context. Sector is per own scoring context
with a distinct ETF-enabled dependency variant. CERI is per four-output/capture
consumer context. IBMI is per metric module. Upstream exact evidence carries its
own configuration. A liquidity-only edit leaves other IBMI family semantics
unchanged. Same semantic values with different actual provenance keep equal
semantic identity and distinct resolution hashes. Ordered provider changes are
semantic; unordered mapping key insertion order is not.

## 21. Calculation Identity integration

`ContextualEffectiveConfiguration.bind` reuses the existing shared configuration
dimension. Temporal/source/code/cohort dimensions retain native meanings. Producer
and expected-source helpers use that dimension consistently. Actual historical
IBMI identity derives C1 from its exact E1 pointer; current expected identities
derive the selected current cached module snapshot. Unsealed legacy candidates
remain blocked, including the newest legacy row that must block fallback to an
older READY row. No hash-only remnant becomes certified configuration authority.

## 22. Immutable evidence integration

The existing core evidence payload embeds the shared snapshot, semantic hash,
resolution hash and schema. New core/contextual namespace writes require the exact
matching snapshot. Payload and identity fingerprints are verified on read. Existing
immutable ledgers, source association tables, append-only/write/temporal fences
and exact-address semantics remain intact. Evidence can retain distinct diagnostic
payload versions under the same semantic identity; history is never overwritten.
No database model or migration file changes are needed.

## 23. Historical config reads

`contextual_configuration_from_evidence` verifies frozen payload and identity;
`contextual_configuration_for_row` enforces the exact evidence pointer. Regime,
Sector, CERI and IBMI historical adapters read embedded tagged values only.
Tests prohibit file and Settings access during decoding/typed restoration.
CERI AS_KNOWN and LATEST_CORRECTED select their existing immutable historical
views and retain the selected version's config, not current native config.
Current catalogue/transport/upstream selection boundaries remain separate.

## 24. Retry behavior

Explicit frozen C1 and matching expected configuration preserve C1 semantics;
C2 against an expected C1 raises CONFIGURATION_RETRY_MISMATCH before financial
behavior. The native IBMI guard is tested before any input query. The PG native
IBMI case rehydrates C1, retains the original full legacy hash and reuses E1 with
the exact constituent manifest. Regime's native PG case exercises explicit frozen
C1 and rejection of incompatible C2. Generic drift tests cover all eight families.
Durable job serialization/delivery of these anchors remains T13D. This configuration
guard complements existing full temporal/source/write checks; it does not claim
complete original-context reconstruction or privileged SQL governance.

## 25. Legacy behavior

Evidence without a snapshot stays LEGACY_UNKNOWN. Legacy raw hash/name/debug
metadata cannot supply frozen effective values. Sealed legacy IBMI hash-only identity has UNKNOWN effective configuration and
cannot pass certified source validation even if an old readiness claim is READY.
Unsealed markers retain the existing native selection/blocking rule.
No present-day parser substitution,
inferred certification, evidence rewrite or backfill occurs. Legacy consumer
readiness remains blocked under existing Phase-3 rules. Test-only synthetic
new-certified fixtures explicitly create a matching snapshot before sealing.

## 26. Config drift

Regime freshness, Sector confidence, CERI native policy/cap and each IBMI module's
material numerical rule produce different semantic identities under drift. Existing
C1 evidence remains immutable and inspectable after C2. Same-output coincidences
do not make C1/C2 configuration compatible. Provenance-only changes retain semantic
identity but change resolution hashes. Operational-only request-budget edits and
secret-only Flex token edits cannot change financial semantic identity.

## 27. Secret safety

Only five explicitly allowlisted contextual booleans are traced in Settings.
Field names/source kinds are retained privately; complete dictionaries and
credential values are never retained. IBMI's engine scope selects explicit safe
versions/enable and selected module/freshness values, rather than whole raw engine
or transport config. Tokens, API keys, login, cookies, DSNs and credential paths
are absent from snapshots and semantic/resolution hash material. Synthetic secret
tests verify payload absence and module semantic neutrality. External provenance
claims still have T13A's stated truth boundary; integrity proves retained claims,
not independent attestation that an external source was truthful.

## 28. Direct-read audit

Companion: `T13C_contextual_configuration_inventory.json`, generated by
`scripts/qa/t13c_configuration_inventory.py` from native parsers and bounded AST.
Its 926 leaves cover default eight families plus ETF-enabled Sector: 735
T13C_FROZEN, 42 OPERATIONAL_ONLY, 149 DISPLAY_ONLY, zero material UNKNOWN_REMAINING.
Those counts include variant duplication and are not unique repository read sites.
The bounded production census separately identifies 52 source-boundary call sites:
25 in adopted resolver/consumer files, 12 in operational IBMI orchestration and
15 in deferred downstream/delivery files. File-level labels require manual
interpretation: the orchestration config load freezes calculation authority before
math as well as supplying transport; capture's Alert flag is downstream T13D;
private legacy helper fallbacks are not certified historical entry points.

TEST_ONLY production call-site count is zero; fixture helpers remain test-only and
are excluded from this production census. SECRET snapshot leaves are zero because
credential-bearing transport/Settings trees are excluded at the boundary. T13D
has no leaves in the contextual snapshots; its 15 bounded source sites and precise
family inventory in section 34 are a different unit. Supported families reject material UNKNOWN winners. Incomplete CERI native
policy/consumer caps and IBMI metric/freshness/version keys cannot fall back to
current defaults.
No unresolved live material
behavioral configuration reread remains in the certified contextual path. This
bounded AST/manual inventory is not repository-wide dataflow certification.

## 29. READY/business parity

The identical opt-in T13C native probe is installed in an isolated detached T13B
worktree and current workspace. It captures financial output while excluding new
identity/evidence metadata. Native Regime, Sector and all five IBMI calculators
extend the established READY Ranking/CERI/Setup/Lifecycle/Actionability probe.
Exact canonical output equality is required, not approximate rounded comparison.
The probe serializes native DTO/namespace values without modifying app behavior.
Final comparison artifact and per-producer counts are recorded below after runs.


Final parity: **211 passed; 308 exact captures; zero unexpected changes**.
Canonical SHA-256: `c2aca12aed30ea178d2542a64e8404764a5362fc442c055323c89498849e49df`.

| Producer | Captures |
| --- | ---: |
| Actionability | 4 |
| CERI | 33 |
| Combined | 26 |
| IBMI.histogram | 5 |
| IBMI.liquidity | 10 |
| IBMI.options_activity | 4 |
| IBMI.short_pressure | 6 |
| IBMI.volatility | 9 |
| Lifecycle | 4 |
| Ranking | 7 |
| Regime | 7 |
| Sector | 38 |
| SectorUniverse | 42 |
| Setup | 71 |
| Winner | 21 |
| WinnerFeatures | 21 |

## 30. Performance/query impact

Native parsers resolve at context scope, IBMI precomputes five modules, capture
preloads own consumer context and IBMI once before company iteration, and frozen
Pine/v4 is passed explicitly into financial feature calls. Repeated frozen
resolution across 12 ticker iterations passes with Path.open/read_text and
get_settings forbidden. No new database configuration lookup or per-ticker YAML/
environment parse is introduced. Existing metric/price/source queries remain native.
Winner's producer expectations now resolve once per native RunCaptureContext;
12 ticker acquisitions retain the same immutable Regime/Sector expectations.
A second run context resolves again, so this is not a global stale-config cache.
Winner's own probability/configuration policy remains T13D. The bounded cache
removes the additional parsing that complete Regime rules would otherwise add to
its existing per-ticker expected-source loader calls.

Tagged JSON decode/asdict, validation and hashing consume CPU. The comparable
211-test probe took roughly 29s on T13B and 75s on an earlier T13C run; that
construction-heavy test runtime is a measured cost, not a production SLA benchmark.
Concurrent broad/PG certification adds host load. No unsupported latency/performance
improvement or zero-overhead claim is made. A cold native config is cached at scope;
durable restart anchor delivery remains separate T13D work.

## 31. PostgreSQL certification

Disposable PostgreSQL 16: task-owned `swinglens-t13c-pg-20260916`, loopback port
26316. Each fixture checks the safe admin URL, creates/drops its own GUID database,
upgrades to unchanged head `0079_setup_lifecycle_alert_ev` and runs native services.
The four new cases exercise real Regime feature inputs, real SQL Sector parents,
native CERI four-output calculators and native IBMI metric constituents. Existing
immutable/core/contextual/readiness/pipeline/Winner PG cases remain unchanged in
assertion strength, with matching snapshots added to newly certified fixtures.

The full 82-case lane includes 60 Phase-2 evidence cases plus Phase-3 consumer/
resume/native integration coverage and four T13C cases. Migration check detects
no schema drift. Fresh-session history, E1 retention, exact source addresses,
configuration drift, retry and legacy/readiness behavior are covered. No
authoritative/production database or existing user-owned containers are touched.

## 32. Tests


### Final certification record

| Lane / subset | Passed | Warning events in containing run | Evidence / scope |
| --- | ---: | ---: | --- |
| T13C focused (T13A+B+C units + native T13C PG) | 99 | 52 | PG report subset; warnings belong to entire combined lane |
| Regime | 43 | 22 | broad report file-family subset |
| Sector | 78 | 22 | broad report file-family subset |
| CERI | 456 | 22 | broad report file-family subset |
| IBMI | 56 | 22 | broad report file-family subset |
| T13C configuration/drift/legacy/bounded reads | 30 | 52 | 29 unit behaviors plus native prior-selection criterion case |
| T13A | 41 | 52 | final combined report subset |
| T13B | 24 | 52 | final combined report subset |
| Phase 2 | 60 | 52 | final PG report subset |
| PostgreSQL | 82 | 52 | full 82-case final lane |
| Combined PostgreSQL/focused | 177 | 52 | 177 total; no overlap counted twice within this lane |
| Broader safe lane | 3233 | 22 | seven established external IBMI cases deselected |
| Business parity | 211 | 1 | 308 exact captures; SHA-256 in section 29 |
| Phase 3 | 563 | 1 | established lane; counts overlap broader/PG coverage |
| Phase 1 | 135 | 1 | established lane; counts overlap broader/PG coverage |
| Phase 0 | 86 | 16 | established lane; counts overlap broader/PG coverage |
| Phase 0 supplement | 71 | 25 | established lane; counts overlap broader/PG coverage |
| Core representative | 292 | 1 | established lane; counts overlap broader/PG coverage |

Final failures: **0**. Final skips: **0**. Deselected: **7 established live IBMI cases** in the safe broad lane only. Collection errors: **0**. Flaky tests: **none observed**, no rerun plugin. Warnings are existing deprecations/metadata cycles; warning counts are events in overlapping runs and must not be summed as unique defects. Static gates pass Ruff (40 Python files), format, compileall, diff whitespace, unchanged models/migrations, deterministic 926-leaf/52-boundary inventory and certified application hash verification.

Development-only failures also included a missing optional-identity guard, deliberate partial-rule fixtures with undeclared source winners, and a synthetic short-pressure drift using a nonexistent parameter. These were fixed explicitly and the final certificates replace those runs. The earlier Phase-0 batch skipped 36 PG cases because its test URL was absent; the final safe PG-enabled lanes pass all 86 + 71. No exclusions/assertions were weakened.
 Focused
configuration coverage comprises T13A 41, T13B 24 and T13C 30 unit cases, plus four
new native PG cases. Core representatives pass 292; Phase-3 563, Phase-1 135,
Phase-0 86 + 71. The native parity lane has 211 tests and 308 captures. Lanes
overlap; their counts must not be summed as unique tests.

Development failures were deterministic, not flaky: outdated synthetic certified
fixtures lacked matching snapshots; private replaced DTOs lost their hidden
snapshot; tamper-test fixture payload fingerprints were stale; the test probe
initially could not encode a stub namespace; one helper exercised lazy='raise';
and early trial commands used incorrect test filenames. These were corrected
without suppressing assertions/exclusions. An initial broad development run had
48 failures, followed by focused fixes and a clean 3,222-test run before the last
bounded refinements. Static E501/import errors were resolved through Ruff fixes/
formatting. Interrupted prefinal runs are not represented as certificates.

Final warnings are existing Starlette/httpx, Alembic path-separator and cyclic
foreign-key metadata, and Python 3.12 SQLite datetime adapter deprecations.
No skips/new exclusions are authorized. Seven established external IBMI cases
remain deselected in the safe broad lane. No flaky retry/rerun plugin is used.

## 33. Finding reconciliation

| Finding | Status and proof boundary |
| --- | --- |
| RANK-005 | CLOSED for T13B new certified selected profiles; unchanged |
| CORE-008 | CLOSED for new certified complete Regime policy; legacy unknown |
| CERI-001 | Configuration portion resolved; separate guidance/provider selection algorithm remains open |
| CERI-007 | Own configuration portion closed; stale algorithm/readiness integration remains partial |
| CERI-008 | Native four-output configuration scope closed; overall downstream/integration scope partial for T13D |
| XINT-010 | PARTIAL; downstream/durable delivery and integration remain |
| INV-CONFIG-001 | ENFORCED for supported certified core and contextual producers; PARTIAL repository-wide |

## 34. T13D handoff

These are own downstream authority records, not copies of producer snapshots.

| Family | Native resolver and behavioral keys | Direct behavior consumers/current binding | Required T13D work |
| --- | --- | --- | --- |
| Setup | `setup_lifecycle/config.py::load_setup_lifecycle_config`; engine origin/trigger/timeframe, canonicalization required_context/preference/precedence, family precedence/enabled/fallback/confidence/parameters, phases, signal registry definitions, confidence/data-quality rules | source_loader and family_adapters/normalizer/confidence services use current native config; source evidence pins and legacy native raw config hash/code identity exist, own T13A snapshot not adopted | Freeze actual selected Setup rules before normalization/family/confidence behavior; bind own identity/evidence; retain exact upstream pointers and legacy unknown |
| Lifecycle | same resolver; states transition_precedence/terminal/supported, family tracking/ready/max-age/gap/cooldown/parameters, episode one-active/history/gap/rearm, confidence labels, actionability minimum_actionable_confidence and code-owned gate policy | `lifecycle_engine.py::SetupLifecycleEngine.evaluate`, `_state_from_evidence`, `_apply_hysteresis`; `actionability_policy.py::SetupLifecycleActionabilityPolicy.evaluate` load/use current config; Phase-2 evaluation/transition ledgers and policy/code versions exist | Freeze actual lifecycle/actionability policy before evaluation and retry; separate own policy from Setup source configuration; bind full resolved values |
| Setup/Lifecycle Alerts | same config resolver plus `signal_registry.py` definitions; alerts built_in_rules_enabled, rules enabled/severity/source/cooldown/minimum_confidence/filters, default cooldown, reconstructed-origin exclusion, registry signal rule definitions | `alert_service.py::SetupLifecycleAlertService` uses current config and persisted dynamic rule objects; immutable Alert ledger/source pins/native versions exist, no own effective snapshot | Freeze selected built-in/dynamic DB rule and cooldown/filter policy before matching/dedup; bind Alert identity/evidence and policy authority |
| CERI Alerts/changes | `ceri/config.py::load_ceri_config`; change_thresholds score/revision/risk/acceleration deltas, alerts enabled/rules severity/source_change_type/cooldown/acknowledgement/dedup, existing capture Alert flags | `ceri/change_detection_service.py`, `ceri/alert_service.py`; capture constructor `ceri_flags().alerts`; native historical score evidence exists, own downstream snapshot absent | Keep four-output frozen producer config separate; adopt actual change/Alert matching and cooldown/delivery rules; address opaque legacy feature selection hash as explicit integration decision |
| Winner | `winner_probability/config.py::load_winner_probability_config`; entry models/offsets, horizon/outcome definitions, episode rules, cohort hierarchy/min_effective_n/prior_strength/prior_probability/max_interval_width, evidence grades/membership/filtering, cold_start/drift/governance/schema | `probability_estimator.py::ProbabilityEstimator` and feature/outcome/cohort builders consume current native config; generation/cohort/evidence manifest pins and legacy hash/version identity exist, own effective snapshot absent | Freeze selected actual probability/feature/outcome/cohort rules before behavior; bind generation and estimate own configuration; avoid importing IBMI or CERI forbidden edges |
| Readiness consumer policies | `technical_consumer_eligibility.py` and `contextual_consumer_eligibility.py`; fixed policy versions, producer/consumer/module, supported readiness version, blocked/degraded/unknown handling; `ContextualConsumerPolicy.evaluate` rejects configurable overrides in v1 | Phase-3 eligibility decisions retain own policy version/readiness fingerprint/evidence ID; they remain separate current code-policy authority | Freeze/identify actual consumer permission policy independently when required; preserve READY/DEGRADED/legacy semantics; do not duplicate producer threshold snapshots |
| Pipeline/job configuration delivery | `pipeline_executor.py::execute_full_pipeline`, `_execute_resumed_pipeline`, wrapper builders and Settings/ceri_flags; setup/winner/ceri scheduling/capture flags, technical flags/benchmark, module selection and selected profile; `background_worker.py::_execute_full_pipeline_job` consumes payload and current Settings | Phase-0 execution token/cutoff/write fences and Phase-3 immutable resume evidence manifests exist; durable jobs do not consistently deliver family snapshots/expected config anchors | Resolve at authorized context scope and serialize safe immutable family identity/snapshot/evidence anchors through job payload, dependencies and per-step checkpoint; keep timeout/concurrency/cache operational |
| Durable retries/resume | worker `execute_job`; pipeline `_validate_resume_checkpoint`, `_validate_resume_evidence`, `_require_frozen_context_evidence`, checkpoint/current config constructors; native IBMI rebuild exposes frozen/expected arguments | Exact evidence/temporal resume fencing exists; own configuration arguments are not delivered end-to-end on restart; current native resolvers can start a new unanchored calculation | Persist/deliver C1 anchors, reject current C2 against expected C1 before calculation, preserve source/cutoff context and exact evidence retry address, distinguish new authorized C2 execution from C1 retry |

Operational API/backfill/export/retention and secret transport Settings are not
automatically financial authority. T13D must classify their actual downstream
behavior before adoption. Original-context replay remains explicitly deferred;
controlled replay is a separate authorized new calculation version, not a claim
to reconstruct arbitrary original config/context from current files.

## 35. Residual risks

Full native CERI legacy feature-hash selection couples downstream file changes to
input eligibility; its actual opaque selection value is frozen, redesign deferred.
Guidance ignores provider order under its existing algorithm. Local immutable
decode/hashing has measurable CPU cost. External provenance truth and privileged
direct SQL remain stated boundaries. T13D must deliver anchors durably and adopt
remaining own downstream policy. T13E integration certification, original-context
reconstruction and later entry-point/SQL governance remain deferred. No unsupported
repository-wide completion, production latency or algorithm-fix claim is made.

## 36. Final verdict

**PASS**: all required configuration, native PG, identity/legacy/drift, parity,
regression and static checks pass. Final broader lane: 3,233 passed; final
PostgreSQL/focused lane: 177 passed (82 PG + 95 units); zero failures/skips.
Configuration identity/evidence authority is enforced for supported new native
contextual producers. Remaining downstream/durable/original-context work is
explicitly deferred in sections 34–35, not claimed complete.
Production/runtime mutations: no production data/schema/config/service changes;
local task branch/files, disposable task PG and temporary baseline worktree only.
Existing Prometheus/Grafana containers and unrelated user-owned worktrees are
preserved. Task-owned baseline worktree and PG were removed after certification.
Both unrelated worktrees retain their original branches/HEADs. Existing monitoring
containers remain running. No production schema/data rewrite, runtime restart,
merge or push was performed.

### Changed-file manifest

- `app/services/ceri/capture_service.py`
- `app/services/ceri/confidence_service.py`
- `app/services/ceri/config.py`
- `app/services/ceri/controlled_replay_service.py`
- `app/services/ceri/decision_evidence.py`
- `app/services/ceri/event_risk_service.py`
- `app/services/ceri/opportunity_score_service.py`
- `app/services/ceri/provider_conflict_service.py`
- `app/services/ceri/snapshot_service.py`
- `app/services/combined_ranking_identity.py`
- `app/services/contextual_calculation_identity.py`
- `app/services/core_calculation_evidence.py`
- `app/services/core_settings_provenance.py`
- `app/services/ib_market_intelligence/config.py`
- `app/services/ib_market_intelligence/decision_evidence.py`
- `app/services/ib_market_intelligence/orchestration.py`
- `app/services/market_regime_command_center.py`
- `app/services/market_regime_policy.py`
- `app/services/market_regime_repository.py`
- `app/services/sector_etf_rotation_service.py`
- `app/services/sector_rotation_config.py`
- `app/services/sector_rotation_repository.py`
- `app/services/sector_rotation_service.py`
- `app/services/setup_lifecycle/source_loader.py`
- `app/services/winner_probability/calculation_identity.py`
- `app/settings.py`
- `docs/architecture/SWINGLENS_EFFECTIVE_CONFIGURATION_CONTRACT.md`
- `tests/ceri/test_wave1_p0.py`
- `tests/contextual_readiness_helpers.py`
- `tests/integration/test_ceri_immutable_decision_evidence.py`
- `tests/integration/test_contextual_consumer_eligibility_postgresql.py`
- `tests/integration/test_ibmi_immutable_constituent_evidence.py`
- `tests/integration/test_winner_consumer_eligibility_postgresql.py`
- `tests/test_contextual_calculation_identity_adoption.py`
- `tests/winner_probability/test_t12e_readiness_certification.py`
- `tests/winner_probability/test_winner_calculation_identity_adoption.py`
- `app/services/contextual_effective_configuration.py`
- `docs/remediation/calculation-lineage/T13C_contextual_configuration_adoption.md`
- `docs/remediation/calculation-lineage/T13C_contextual_configuration_inventory.json`
- `scripts/qa/t13c_behavior_probe.py`
- `scripts/qa/t13c_configuration_inventory.py`
- `tests/integration/test_contextual_configuration_adoption_postgresql.py`
- `tests/test_contextual_effective_configuration.py`
