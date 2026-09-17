# T13A — Canonical effective configuration identity and authority contract

## 1. Executive verdict

**T13A VERDICT: PASS.** The typed foundation and opt-in evidence path are
implemented and verified. Effective semantic identity, separate provenance,
secret exclusion, conservative compatibility, and frozen historical meaning are
proven within the declared families. This task does not certify Phase 4 or close
subsystem adoption findings. Business behavior is unchanged; migration is not required.

## 2. Baseline

Recorded before modifying tracked files:

| Item | Value |
|---|---|
| Repository | `C:/Users/Ivica/Documents/SwingLens` |
| Starting branch | `codex/t12e-phase3-readiness-certification` |
| Starting HEAD / Phase-3 certificate | `5422bcdf7703db891810d9e9c20a8f1770241fc9` |
| New branch | `codex/t13a-effective-configuration-contract` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase-2 certificate | `7b015124807b8f895911d7e790fcbf01949ff85f` |
| Tracked changes | None; git status and diff were empty |
| Untracked files | None in git's non-ignored inventory |
| origin/main / local main | Both `febe376be67f01589199cd3ba55af98fd54001ed` |
| Migration head | One: `0079_setup_lifecycle_alert_ev` |
| Python | Repository CPython 3.12.2; global Python also 3.12.2 |
| PostgreSQL capability | No psql/pg_ctl on PATH; disposable Docker PostgreSQL available |
| Docker | Executable available; server 29.6.2 |

Starting HEAD exactly matches the required baseline. Existing ignored artifacts,
logs, `.env`, and data are preserved. Baseline references were read alongside
implementation; the original registry remains unchanged. No reset, clean, blanket
staging, push, merge, or production database access is used.

## 3. Phase-4 scope

INV-CONFIG-001 and XINT-010 receive a canonical contract, native source/authority
inventory, classified family registry, deterministic typed values, secret-safe
hashes, conservative compatibility/drift, representative adapters, and an opt-in
immutable evidence link. Broad calculation/consumer adoption belongs to T13B–D.

## 4. Existing configuration inventory

Implementation remains authoritative. The following matrix covers material
behavioral families; the companion machine inventory records individual key/read
locations without values. Paths below are relative to `app/services` unless noted.
"Existing identity" means the Phase-1/native digest/version, not the new T13A
effective-value/provenance contract.

| Family | Keys/families and declarations/defaults | Native resolution and overrides | Behavioral? | Existing persisted identity/version | Current rereads / adoption gap |
|---|---|---|---|---|---|
| Fundamental | `fundamental_ranker.py` formula constants + `config/scoring_weights.yaml`; v2 `config/fundamentals_v2.yaml`: model_version, weights, missing_data, thresholds, field_priorities, coverage_only_fields, components | v1 `_load_scoring_weights`; v2 `load_fundamentals_v2_config` validates explicit/default path; score producer `fundamental_score_service.py` | Yes; financial thresholds, weighting, missingness and field selection | Model/config hash and Phase-1 configuration dimension; Phase-2 output/debug frozen | New scoring rereads YAML; split v1/v2 authorities and pin resolved config at producer |
| Technical | `technical_indicators.py`, `config/pine_defaults.yaml`, `technical_scoring_config.py` defaults + YAML v4; `technical_scoring_v5_config.py` defaults + YAML v5; Settings engine/cache flags | Truthy supplied Pine/v4 params > native loaders; v4/v5 recursive YAML leaf overrides > defaults; technical service settings select active/shadow engines | Yes for feature periods, confidence, thresholds, engine selection; process pool/concurrency operational | Config/calculation/component hashes in Phase-1/Phase-2; separate artifact/cache identity | Independent feature/scoring reads and defaults remain; snapshot active resolved engine/config and safe source policies |
| Combined | `config/scoring_weights.yaml`: combined_score weights, labels, position sizing, earnings_risk_gate; `combined_decision.py` fallbacks | `_load_scoring_config`; selected weights and consumer fallbacks; upstream exact evidence/readiness retained | Yes | Existing config hash and Phase-1 config dimension; Phase-2 debug evidence | New refresh rereads scoring YAML; freeze effective consumed values, not whole file/debug label |
| Ranking | `ranking_profile_config.py` dataclasses, parser defaults, `config/ranking_profiles.yaml`: weights, components, missingness, thresholds, gates, penalties, tradeability; shared scoring earnings rules | `load_ranking_profiles`/`get_ranking_profile` → `_parse_profile` → validation; file fields > parser defaults; profile service and engine consume separate scoring config | Yes; name/label/description are metadata | Phase-1 resolved profile/config envelope and Phase-2 profile scope/debug; not the new entry/provenance payload | Recalculate/detail paths select current profile YAML; whole Ranking must freeze shared scoring and profile together |
| Regime | `market_regime_policy.py` typed config + `config/market_regime_command_center.yaml`: symbols, freshness, risk_state_mapping, market_regime_v4, policies; classifier constants; Pine/v4 feature dependencies | Native loader validates required sections; command center/classifier/policy apply `get`/`or` defaults; feature engine loads current Pine/v4 | Yes; formula constants are algorithm identity | Existing Phase-1 hash of typed Regime config and versions; Phase-2 snapshot | Feature dependencies and winning/default sources need adopted snapshot binding |
| Sector | `sector_rotation_config.py` + YAML: version, defaults, taxonomy/proxies, universe_score, etf_score, combined_score, rotation_states, permissions | `load_sector_rotation_config` → validation; service/policy consume native fallback values and exact upstream evidence | Yes | `sector_rotation_config_hash`, version, Phase-1/2 evidence | Current YAML policy/service reads; canonicalize effective leaves and missing-ETF/permission behavior |
| CERI | `ceri/config.py` typed dataclasses + YAML/taxonomy: engine, provider priority/capabilities, dataset staleness, revision windows/period weights, missing_values, currency_conversion, opportunity_weights, event_risk, confidence, changes, posture, price response, alerts; SEC Settings and provider getenv | `load_ceri_config`/`load_ceri_taxonomy` parse required sections and code policy constants; Settings flags select processing; SEC provider direct env fallback | Yes for scoring/coverage/provider precedence; HTTP/retry operational; credentials secret | Native config_hash/config_version/calculation_version + Phase-1/2 decision evidence | Typed config hash omits independently read behavioral environment/provider policy; freeze taxonomy, SEC lookbacks/caps/readiness selection and effective provider source policy |
| IBMI | `ib_market_intelligence/config.py`, Settings, YAML raw sections: module flags, lookbacks, scanner presets/version/filters, shortlist, histogram/volatility/fee periods | `load_ib_market_intelligence_config`; path argument > Settings path; `effective_module_enabled`: global AND module AND YAML enabled; services additionally read Settings | Yes; request spacing/transport operational; Flex token secret | Raw YAML evidence_hash and engine/source/config versions; Phase-1/2 feature/constituent evidence | Raw YAML hash is insufficient for Settings-dependent module behavior and lookbacks; preserve safe source/account identity only where already appropriate |
| Setup | `setup_lifecycle/config.py` + YAML: engine/timeframe/origin, canonicalization, states/phases/families, confidence, data labels, actionability, signals, reconstructed origin; Settings capture flags | Typed loader/registry validates sections; snapshot_builder/source_loader/adapters/actionability resolve native defaults and frozen upstream eligibility | Yes; API targets operational; UI labels display | Native config_hash/config_version/engine/schema + Setup Phase-1/2 | New capture/actionability still reads current config; bind exact effective family/registry before decision |
| Lifecycle | Same Setup config: states/phases, episode cooldown/invalidation/observation gap, family transitions; engine constants | `lifecycle_engine.py`, episode/evaluation services and gap evidence use supplied/current Setup config and predecessor snapshot | Yes | Config hash/version in evaluations, transitions and episodes; Phase-2 dedicated append-only chain | Freeze the policy governing every evaluation/transition; current episode is projection, not config authority |
| Alerts | Setup signal registry/config + `SignalAlertRule` database fields: rule_id, enabled, severity, scope, family, cooldown, filters/conditions/restrictions; CERI separate alert config | `setup_lifecycle/alert_service.py` selects enabled DB rules; `decision_evidence.py` freezes exact rule row; CERI alert_service resolves CERI policy | Yes; notification delivery status operational | Dedicated rule snapshot hash, exact rule-evidence FK and immutable decision; CERI config version | Rule snapshot already freezes payload; compose shared config with registry/evaluator version and existing rule snapshot instead of name-only identity |
| Winner | `winner_probability/config.py` + YAML: feature schema, entry/horizon/outcome policies, cohort prior/coverage/evidence grades, cold start/drift/model governance/filter rules; DB model/cohort versions; Settings enable/capture flags | Typed loader validates sections; FeatureSchemaRegistry, cohort/model builders and publication services select effective config/version; jobs use supplied Settings | Yes; slice size/wall limits operational unless truncating semantic evidence | Existing native config_hash, model/cohort/schema/algorithm identity, immutable vectors/manifests and exact Combined/Ranking pins | Complete model building/scoring config plus readiness policy family must be frozen, not inferred from current version labels |
| Pipeline | `market_calculation_context_service.py`, `market_clock_service.py`, `pipeline_service.py`, Settings: calendar/cutoff/readiness versions, module scheduling/enablement, fetch policies | Context freezes once at enqueue and is reused; explicit pipeline context required; stage handlers read Settings/YAML independently | Yes for source/session/engine/stage choices; worker/lease/retry operational | Phase-1 context fingerprint and version; Phase-2 handoff manifest | Temporal context remains separate; propagate the resolved configuration bundle through handoff/resume instead of rerereading per stage |
| Background jobs | Settings CERI/Winner/Setup scheduling flags, SEC guidance caps/lookbacks/readiness, IB fetch/revision/lookback policies; handler request parameters | `background_job_service.py`, provider/feature/maintenance handlers, native Settings and explicit job payload/context | Business selection/coverage knobs yes; heartbeat, lease and worker controls operational | Execution identity/job payload and subsystem evidence versions; no common configuration bundle | Audit limits affecting selection vs transport, capture stable job semantic policies at creation and preserve retry/resume identity |
| Readiness policies | `technical_consumer_eligibility.py`, `contextual_consumer_eligibility.py`, Winner `consumer_eligibility.py`, `producer_readiness.py`: named policy/version, producer/module/required readiness version, native status/reasons | Code-defined matrices; no permissive config overrides; exact bound frozen readiness required | Yes | Phase-3 frozen policy_version and readiness fingerprint/decisions | Represent native policy as behavioral configuration without replacing permission decisions; adoption must pin the policy at consumer creation |

## 5. Configuration fragmentation

Fragmentation remains in independent Settings/getenv, YAML/profile reads, parser
defaults, consumer fallbacks, database rule selection, and model/job configuration
builders. T13A adds the shared language; it intentionally does not remove these
production reads. The companion
[key/location inventory](T13A_configuration_authority_inventory.csv) is regenerated
by `scripts/ops/effective_configuration_authority_audit.py` and includes every
Settings declaration, repository YAML key, and detected config/profile/threshold/
weight/rule/policy/default/env read in app Python. It emits no values or excerpts.

This is a conservative lexical/AST candidate inventory, not a proof that each
candidate is a behavioral setting. E.g. a local `config` object method is recorded
even when it merely constructs a DTO. Ambiguous runtime/shared candidates remain
explicit UNKNOWN_AUTHORITY and require review. Numeric algorithm constants not
named as configuration are covered by the manual family matrix as algorithm
identity; the scanner is not a complete program data-flow analysis.

## 6. Behavioral / operational / secret classification

The common enum distinguishes BEHAVIORAL, OPERATIONAL, SECURITY_SECRET,
OBSERVABILITY, ENVIRONMENTAL_DEPENDENCY, and UNKNOWN_CLASSIFICATION. Weight,
threshold, missingness, engine selection, source coverage, provider priority and
readiness policy versions are behavioral. Process pool size, heartbeat, lease,
retry delay, HTTP timeout, ports and logs are operational/observability. Connection
provider endpoints are safe environmental dependencies only after review; raw
database URLs and credentials are secret and cannot be snapshotted.

Settings inventory classifications are initial handoff categories, not automatic
production family registration. Source paths select definitions, not effective
value identity. Transport budgets/limits must be reviewed for effects on evidence
coverage; section-level YAML classification is deliberately conservative.

## 7. Source inventory

Actual supported sources: code defaults/constants, typed Settings, process
environment, `.env`, repository YAML/profile definitions, database rule/model/
operator records, explicit request/job parameters, pipeline context, and
code-versioned readiness policy. The enum represents these plus UNKNOWN. No
generic RULE_SET or remote feature-flag authority is fabricated.

## 8. Resolution authorities

The matrix in §4 names native resolution points and consumers. The new family
registry declares three native contracts. Only native adapters may attest that
their keys exhaust a family; consumers receive frozen values. Generic snapshot
construction validates declared key coverage but cannot independently prove the
native inventory is complete. This distinction is a required adoption gate.

## 9. Precedence inventory

Settings: constructor > environment > configured dotenv > defaults, then native
validation and cached access. Ranking: YAML fields > parser defaults, after explicit
path/name selection; required weights/components have no default. Technical:
supplied truthy params > loader; recursive YAML overrides > default leaves.
Regime: selected YAML required sections plus loader/consumer fallbacks; Pine/v4
feature configuration is independently resolved. IBMI uses conjunction of flags,
not a repository-wide override rule. Fundamental v1 loads weights when scoring;
v2 uses the supplied/default validated YAML. Combined/Sector consume the loaded
file and their own fallbacks. CERI loads selected config and separate taxonomy,
plus Settings/direct SEC env values. Setup/Winner use selected typed YAML with
parser defaults and separate registries. Alerts select DB rules and freeze exact
payload. Readiness uses native versioned code matrices. Job request/context
selection and source availability do not become a made-up global precedence chain.

## 10. Typed configuration model

`app/services/effective_configuration.py` contains the small frozen model,
classification/source/type enums, family declaration, resolution contract,
canonical typed values, snapshot, compatibility/drift, binding and verified
historical read. It imports the existing Phase-1 EffectiveConfigurationIdentity.
`effective_configuration_families.py` registers Ranking profile, Regime and
readiness policy examples without production adoption.

## 11. EffectiveConfigurationSnapshot

Nested caller values are serialized immediately into immutable canonical strings;
mutating inputs or an exported dict cannot alter the snapshot. Exact classified
family keys are required. Optional producer/evaluation context is supported;
evaluation timestamps must be aware. The evidence envelope supplies the
Calculation Identity binding and historical address, avoiding circular hashes.

## 12. EffectiveConfigurationIdentity

The existing Phase-1 wire format is unchanged. The namespace and shared effective
schema accompany a SHA-256 digest and COMPLETE_EFFECTIVE_CONFIGURATION or
PARTIAL_DEBUG coverage. UNKNOWN_CLASSIFICATION cannot bind complete evidence.
Whole-subsystem completeness is not implied by completeness of a profile family.

## 13. Semantic vs provenance identity

Semantic hash includes namespace/schema and effective behavioral typed values.
Resolution hash additionally includes resolver/version/precedence and non-secret
entry classification/source/default/override metadata and effective safe values.
Changing source or resolver while retaining identical effective semantics/schema
preserves config identity and Calculation Identity; provenance hash may differ.
Profile names/display labels are metadata, never enough to identify behavior.

## 14. Canonicalization rules

Use Phase-2 canonical JSON and SHA-256, with configuration-specific value tags.
Keys sort; enum values normalize; bool/null are explicit; unsupported object repr,
NaN/infinity, non-string mapping keys and naive datetimes fail. REAL has exact
fixed point text, without ambient Decimal rounding; int/real/string/bool/null
remain distinct unless an adapter explicitly declares numerical REAL equivalence.
Lists/tuples preserve order; sets/frozensets sort canonical members. Temporal
values retain explicit tagged ISO/UTC profiles. No name-based list reordering.

## 15. Secret-safety model

SECURITY_SECRET values and provenance are discarded before serialization/hashing;
all exports omit secret entries, including their key. Common secret-key
misclassification and nested secret-bearing maps fail closed. Metadata is bounded
safe non-secret identifiers, never arbitrary objects/URLs/DSNs. Classification is
trusted resolver input; arbitrary mislabeled strings cannot be proven secret-free
by heuristics. Never snapshot whole Settings/environment/provider objects.
Synthetic tests prove exclusion even for objects whose repr raises. New tracked
code/docs/tests are scanned against local secret settings without printing values.

## 16. Compatibility contract

EXACT requires known complete SHA-256 identities with equal namespace/schema and
semantic digest. Different known behavioral identities default INCOMPATIBLE.
UNKNOWN and LEGACY_UNKNOWN remain unavailable, including self-comparison.
COMPATIBLE is a reserved typed status; no broad permissive policy exists. Run,
ticker, profile/rule name or resolution-hash equality is not a substitute.

## 17. Calculation Identity integration

`bind_configuration` composes the existing dimension and preserves all other
Phase-1 dimensions. It returns a new identity, refuses unknown classifications,
and round-trips through the existing parser. Source-only differences retain the
same effective identity. Complete temporal/calendar validation still applies.

## 18. Immutable evidence integration

The sole existing production writer change is an optional snapshot parameter and
writer-owned payload key. It requires matching bound configuration and embeds
the immutable snapshot before Phase-2/3 hashing; no producer opts in automatically.
Historical reads verify config/payload/identity integrity and do not resolve live
config. Missing snapshot returns LEGACY_UNKNOWN. Exact retries reuse evidence.
The PostgreSQL test freezes C1, advances to C2/current projection, reloads C1 in a
new session, and proves update/delete rejection and secret absence.

## 19. Legacy behavior

No production backfill/rewrite. Existing Phase-1 hash attestations and Phase-2
payloads retain their original meaning. T13A snapshot absence is LEGACY_UNKNOWN,
not a reason to attach current config. Dashboards may separately display current
config; historical evidence does not use it as a fallback.

## 20. Representative Ranking profile proof

The adapter reads once, reuses existing `_parse_profile` and `_validate_profile`,
validates all profiles, and returns the same enabled selection as native loading.
It freezes every parsed dataclass field and traces file/default source at the
field level. Required weights/components use PROFILE; absent optional leaves use
CODE_DEFAULT. Relative repository source paths or an approved logical source ID
are recorded; absolute local paths do not invent a source identifier. Same values
from different safe source IDs retain semantic equality with distinct provenance.
Same candidate threshold from file vs parser is semantic EXACT with
different provenance. Same profile name with changed weights is INCOMPATIBLE.
Profile-only snapshots do not claim the separate shared earnings/scoring config.
Full Ranking decision output/debug is unchanged by capture.

## 21. Representative Regime proof

The adapter freezes typed Regime config and native symbol/freshness/classifier/
policy fallback values, plus explicitly resolved Pine/v4 feature dependencies.
It does not alter the command center, classifier or policy calculation. Existing
resolved objects cannot prove mixed field-level YAML/default origin, so entries
honestly retain UNKNOWN source while the resolver/precedence contract is named.
T13C must instrument the native resolution boundary for complete leaf provenance,
and compose settings/temporal/algorithm dependencies. Freshness and feature-period
changes yield distinct semantics. Regime and policy outputs are preserved.

## 22. Representative rule/policy proof

Use native Phase-3 readiness policies, already versioned and secret-free, rather
than introducing another alert/CERI rule store. Snapshot producer, consumer,
policy_version, module and required_readiness_version. Policy source is
POLICY_VERSION. Policy-version change changes semantic identity. Capturing the
policy leaves its native decisions unchanged across all ReadinessStatus values;
the full Phase-3 matrix/consumer suites remain required independently.

## 23. Config drift proof

Frozen C1 values/hash survive nested input mutation, exported-payload mutation,
environment changes, C2 creation, and a new PostgreSQL session. Typed drift is
false/true/null for exact/incompatible/unavailable proof. Provenance-only changes
are explainable without treating them as behavioral drift.

## 24. Persistence/schema impact

Migration required: **NO**. Existing Phase-2 JSONB envelope, unique evidence_key,
exact evidence IDs/source FKs and append-only ORM protections support embedded
snapshot retention. Semantic snapshots are content-addressed and exact retries
reuse evidence. Different decisions may physically duplicate equivalent config
payloads; there is no global configuration table/deduplication FK in T13A. A
future store can deduplicate bytes without changing this contract. The smallest
correct persistence change is the writer-owned embedded payload and verified
binding, preserving Phase-2 historical addressing. No models/migrations change.

## 25. PostgreSQL certification

Disposable container `swinglens-t13a-pg-20260916`, PostgreSQL 16.13, loopback
random port 23230; repository fixture creates guard-verified `swinglens_pytest_*`
databases. No authoritative PostgreSQL connection is made. Fresh → existing head,
ORM round-trip, evidence-key reuse, C1/C2 isolation, projection advancement,
identity mismatch rejection, secret absence and ORM append-only checks pass in
the focused persistence suite. Alembic check reports no upgrade operations.
Inherited Phase-2 suite additionally certifies upgrade/downgrade/re-upgrade and
FK/constraint behavior. Direct privileged SQL remains outside Phase-2's ORM
immutability boundary; no trigger/schema is invented to silently change it.

## 26. Static authority audit

Final deterministic inventory: 5,538 rows and 253 Settings keys. Counts include
both declarations and read candidates, not distinct configuration keys:
CANONICAL_RESOLVER 2,214; BEHAVIORAL_DIRECT_READ 2,026;
OPERATIONAL_DIRECT_READ 593; SECRET_READ 23; DISPLAY_ONLY 234;
LEGACY 0 detected; UNKNOWN_AUTHORITY 448. The matrix plus
machine CSV is the handoff, not an assertion that all existing readers now use
the new contract. CANONICAL_RESOLVER denotes native parsing/resolution candidates;
BEHAVIORAL_DIRECT_READ remains migration work; OPERATIONAL_DIRECT_READ includes
observability; SECRET_READ is excluded from snapshot payloads; DISPLAY_ONLY
records serving/export access. LEGACY and UNKNOWN_AUTHORITY remain explicit where
detected. TEST_ONLY reads are outside the production app inventory and covered
by regression modules. Dynamic keys are recorded as DYNAMIC_KEY rather than
evaluating runtime objects. No local `.env` is read by the inventory generator.

## 27. T13B candidates

| Work item | Resolver / behavioral keys | Direct reads to adopt | Current identity | Required adoption |
|---|---|---|---|---|
| B1 Fundamental | v1 scoring loader; v2 typed YAML; weights/thresholds/missingness/field priorities/model version | rankers/components, score producer | Existing native/Phase-1 hash; no T13A payload | Resolve once per batch, declare exact v1/v2 family, bind raw/source/calculation config, persist embedded snapshot |
| B2 Technical | Pine + v4/v5 merged loaders; feature periods/confidence/thresholds/active engine; safe IB fetch/revision policy | technical feature/scoring/service/cache builders, Settings flags | Existing hashes/cache identity; no T13A payload | Freeze active effective config; classify shadow/cache/concurrency separately; include transitive feature deps in calculation identity |
| B3 Combined | scoring YAML + native weight/label/earnings/sizing defaults | combine_row_decision, refresh and earnings-risk helpers | Existing config digest; no T13A payload | Freeze resolved consumed values and bind exact upstream config/source evidence without changing formulas |
| B4 Ranking | profile adapter plus shared scoring/earnings resolver; all RANKING_KEYS + earnings config | profile service, engine, gates/penalties/components, current profile detail loader | Existing profile/config digest; optional foundation only | Adopt complete profile + shared scoring snapshot/bundle at producer; persist exact config, eliminate historical current-profile fallback |

## 28. T13C candidates

| Work item | Resolver / behavioral keys | Direct reads to adopt | Current identity | Required adoption |
|---|---|---|---|---|
| C1 Regime | typed policy + native fallbacks + Pine/v4; symbols/freshness/risk mapping/params/policies | command center, policy/classifier, feature loader | Existing typed-config hash; no T13A payload | Instrument leaf/default origins; freeze all consumed feature/policy config; bind before persisted snapshot |
| C2 Sector | validated YAML + service/policy defaults; weights/state/permission/taxonomy/missing-ETF policy | sector service/policy/config getters | Existing raw config digest/version; no T13A payload | Effective family, provenance, conservative config compatibility and exact predecessor/Regime/Ranking evidence |
| C3 CERI | typed config + taxonomy + SEC/provider Settings/getenv; scoring/confidence/staleness/provider priority/lookbacks/caps/readiness | CERI calculators/eligibility/providers, SEC provider env reads | Existing typed config hash/version; no shared resolved env family | Pin taxonomy and effective source policy; exclude credentials; freeze selected policy/coverage configuration in decision evidence |
| C4 IBMI | YAML + Settings enablement AND resolver; lookbacks/presets/filters/shortlist/source version | module services/provider collectors and Settings lookbacks | Raw YAML hash/version; no complete Settings-aware snapshot | Explicit conjunction and per-module family; safe source/account semantics without secrets; embed exact config in constituent evidence |

## 29. T13D candidates

| Work item | Resolver / behavioral keys | Direct reads to adopt | Current identity | Required adoption |
|---|---|---|---|---|
| D1 Setup | typed loader/signal registry/actionability/source adapter; timeframe/origin/state/family/confidence/gates | snapshot_builder, adapters, actionability and query selectors | Native hash/version; no T13A payload | Bind effective config + registry and exact upstream policy identities at capture; separate current display from history |
| D2 Lifecycle | supplied Setup config and evaluation/episode engine; transition/cooldown/invalidation/gap thresholds | lifecycle/evaluation/episode/maintenance services | Hash/version in immutable evaluation/transition | Compose shared config snapshot with exact predecessor evidence; preserve historical config through replay/current projection |
| D3 Alerts | selected DB rule + registry/evaluator and CERI config; conditions/restrictions/cooldown/severity/scope | setup alert_service, CERI alert_service | Exact dedicated immutable rule payload hash/FK exists | Reuse existing rule evidence, add shared effective policy identity; distinguish notification transport/status from decision config |
| D4 Winner | typed config/feature registry/model/cohort builders + Settings flags; priors/coverage/outcomes/horizon/entry/drift/model version | snapshot/cohort/training/estimate/publication/job handlers | Config/model/cohort/schema/version + immutable vectors/manifests | Freeze full effective modeling/scoring config and consumer policy identities; keep temporal/generation and source pins independent |
| D5 Readiness | existing named Technical/Contextual policies; producer/consumer/module/policy/required version | core/contextual/Winner eligibility adapters | Phase-3 policy version frozen; no shared config family | Adopt policy snapshot at consumer creation without changing matrix or introducing permissive UNKNOWN handling |
| D6 Jobs/Pipeline | native Settings/request/stage/context authorities; module selection/fetch coverage/SEC caps/policies | pipeline and background stage handlers | Context/handoff/source pins; execution identity separate | Propagate frozen semantic config through enqueue, retry, handoff and resume; classify resource/transport limits before identity inclusion |

## 30. Finding reconciliation

| Finding | T13A status | Remaining configuration work |
|---|---|---|
| RANK-005 | FOUNDATION_AVAILABLE; open | Ranking producer/evidence must adopt exact profile + shared consumed config |
| CORE-008 | FOUNDATION_AVAILABLE; open | Regime producer must bind complete policy/feature configuration and provenance |
| WIN-003 configuration portion | FOUNDATION_AVAILABLE; open | Winner model/scoring config and exact policy adoption |
| CERI-008 configuration portion | FOUNDATION_AVAILABLE; open | Effective taxonomy/provider/environment/scoring policy lineage |
| SETUP-007 configuration portion | FOUNDATION_AVAILABLE; open | Actionability/family/registry and evaluation policy config binding |
| XINT-010 | PARTIAL | Native source authorities remain fragmented; migration T13B–D |
| INV-CONFIG-001 | FOUNDATION_IMPLEMENTED / PARTIALLY_ENFORCED | Enforced on explicit new writer argument only; full graph adoption/certification deferred |

The original audited registry and synthesis findings are not rewritten or closed.

## 31. Tests

All selected verification lanes pass. Logs/JSON are retained in ignored
`.qa_work/t13a-*`; tests and static checks use repository CPython.

| Lane | Passed | Failed | Skipped | Deselected | Warnings | Evidence |
|---|---|---|---|---|---|---|
| T13A focused, final tree | 43 (41 unit + 2 PostgreSQL) | 0 | 0 | 0 | 6 | t13a-focused-certified.json/log |
| Secret safety subset | 12 unit + 1 PostgreSQL within focused | 0 | 0 | 0 | Shared focused warnings | Eight synthetic secret variants; nested/misclassification/metadata/type guards; secret-safe ORM storage |
| Representative/native behavior lane | 117 | 0 | 0 | 0 | 1 | t13a-representatives.json/log; final additional source/type/temporal cases in focused |
| Baseline/current business comparison | Byte-identical | 0 | 0 | 0 | None | t13a-baseline-behavior.json/log; t13a-current-behavior.json/log |
| Evidence/native PostgreSQL, inherited Phase-2/3 + new storage | 73 | 0 | 0 | 0 | 33 | t13a-postgres.json/log |
| Phase-3 full certified contract/consumer matrix | 563 | 0 | 0 | 0 | 1 | t13a-phase3.json/log; same module list as certified T12E |
| Phase-1 full certified identity lane | 135 | 0 | 0 | 0 | 1 | t13a-phase1.json/log; same six identity/adoption modules |
| Phase-0 fence/session/publication/containment | 86 | 0 | 0 | 0 | 16 | t13a-phase0.json/log |
| Phase-0 session/SEC/transition-preflight supplement | 71 | 0 | 0 | 0 | 25 | t13a-phase0-supplement.json/log; disjoint modules, 157 cases across both safety lanes |
| Broader safe repository | 3,172 | 0 | 0 | 7 | 22 | t13a-broad.json/log |
| Static gates | All PASS | 0 | N/A | N/A | Existing Git line-ending notices only | Ruff six changed Python files; compileall app/tests/scripts; diff check; one head; native Alembic check; unchanged model/migration paths; nine-file secret scan; deterministic inventory regeneration; 24/33 required document sections |

Broad collection included the initial 34 new unit cases. Seven later adversarial
cases and the final source/temporal/type hardening are covered by the final 43-case
focused run; they were added after broad collection, not skipped or deselected.
Production behavior paths remain unchanged and all inherited lanes pass.

Baseline parity uses a detached worktree at the required starting HEAD and the
same capture script in each tree: three Ranking profiles, supported/missing/risk
Regime scenarios with policy outputs, and two readiness policies across seven
states. Canonical output bytes have SHA-256
`9fe848959b18d9682e44d815d617948caed568bc0cca7180a47832f4261b6d85`
in both trees. The temporary baseline worktree is removed after capture.

Native migration/model drift has no changes. The single development test failure
was a new integration fixture missing the existing Phase-1 required calendar;
the fixture was corrected without weakening the validator. Initial lint findings
were fixed; no suppression, skip, xfail or flaky designation was introduced.
Final failed/skipped = 0. No flaky tests identified. Warning families are existing
Starlette/httpx deprecation, SQLite/Alembic deprecations, and the existing
mutually referencing lifecycle evidence tables' schema-sort warning. Counts
belong to individual runs and must not be summed as distinct warnings/tests.

The broader lane uses the certified safe baseline exclusions: integration is run
separately, live browser E2E is excluded, unrelated legacy migration remediation
and subprocess QA self-test are excluded, `not external and not e2e` filters seven
live IBMI provider smoke parameters. No config/precedence/secret/legacy/readiness
test is excluded. Exact commands, outcomes, warnings and deselection IDs are
retained in QA JSON reports. Lanes overlap and must not be summed as distinct tests.

Changed files (explicit staging; no model/migration/dependency changes):

- `app/services/effective_configuration.py`
- `app/services/effective_configuration_families.py`
- `app/services/core_calculation_evidence.py`
- `scripts/ops/effective_configuration_authority_audit.py`
- `tests/test_effective_configuration.py`
- `tests/integration/test_effective_configuration_postgresql.py`
- `docs/architecture/SWINGLENS_EFFECTIVE_CONFIGURATION_CONTRACT.md`
- `docs/remediation/calculation-lineage/T13A_effective_configuration_contract.md`
- `docs/remediation/calculation-lineage/T13A_configuration_authority_inventory.csv`

## 32. Residual risks

Native readers remain until adoption. Unknown leaf provenance of already resolved
Regime/feature objects is explicit; declarations/classification are trusted and
need native consumer review. Embedded bytes are not globally physically deduplicated.
Privileged SQL/admin mutation is outside existing application-writer protection.
Hash equality is neither correctness nor readiness/temporal/provider validity.
No deployment, provider call, production rewrite, legacy backfill, merge or push.

## 33. Final verdict

**T13A VERDICT: PASS.** Business behavior change: NONE.
Migration required: NO. Production rewrite/backfill required: NO. Phase-4 graph
certification deferred to T13E after T13B core, T13C contextual and T13D decision/
Winner/policy adoption. The commit containing this report will be the final T13A
tree; its exact SHA is returned in the delivery response to avoid self-reference.
Only disposable test databases/container and the temporary baseline worktree
are created and removed. Existing production/runtime/data/configuration and other
worktrees are preserved. The requested focused commit is created without merge
or push; the delivery response records final worktree status.
