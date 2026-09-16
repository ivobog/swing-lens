# SwingLens Effective Configuration Contract — Phase 4 foundation

## 1. Purpose

T13A establishes the common language for `INV-CONFIG-001`: persisted calculations
and decisions must retain the effective configuration governing their behavior.
This is a foundation, not subsystem adoption or Phase-4 certification. Phase-0
safety, Phase-1 Calculation Identity, Phase-2 immutable evidence, and Phase-3
readiness retain their respective authorities.

> A configuration file, environment, profile name, or rule name is not configuration identity.

> Effective configuration identity is derived from the canonical behavior-affecting values actually used by the calculation.

The original audited lineage registry remains historical and is not rewritten.
The repository-wide inventory and adoption work items live in
[the T13A report](../remediation/calculation-lineage/T13A_effective_configuration_contract.md).

## 2. Configuration terminology

`ConfigurationFamily` declares a namespace, effective-value schema, classified
keys, native resolver contract, and compatibility-policy identifier.
`ConfigurationEntry` holds a frozen typed value, classification, winning source,
and default/override markers. `ConfigurationResolution` identifies the resolver
and its precedence. `ConfigurationSource` describes a safe source identifier and
version. `EffectiveConfigurationSnapshot` freezes the complete declared family.
`EffectiveConfigurationIdentity` is the existing Phase-1 attestation, reused
without changing its wire format. `ConfigurationCompatibilityResult` and
`ConfigurationDrift` express comparison and historical/current divergence.

## 3. Declared vs resolved vs effective configuration

Defaults, YAML, Settings, environment, requests, database rules, and pipeline
context are resolution inputs. A snapshot captures the resulting values after
native parsing, coercion, defaults, and selection. The snapshot constructor does
not resolve configuration. Consumers receive the snapshot or its identity;
they must not recreate historical configuration from today's sources.

The snapshot must cover exactly the family's declared classified keys. Missing,
extra, duplicate, and misclassified keys fail. Completeness is an attestation
within that family, not automatic proof that an entire subsystem was inventoried.
Adapters must include transitive configuration dependencies or bind their own
separate identities. Later adoption must audit that attestation against consumers.

## 4. Behavioral vs operational vs secret classification

| Classification | Semantic hash | Snapshot/provenance payload | Meaning |
|---|---|---|---|
| BEHAVIORAL | Included | Included | Governs calculation/decision semantics |
| OPERATIONAL | Excluded | Included if explicitly safe | Execution/resource controls |
| SECURITY_SECRET | Excluded entirely | Excluded entirely | Authentication/secret material |
| OBSERVABILITY | Excluded | Included if explicitly safe | Labels, descriptions, telemetry |
| ENVIRONMENTAL_DEPENDENCY | Excluded | Safe declared value only | Connection/deployment dependencies |
| UNKNOWN_CLASSIFICATION | Excluded; coverage PARTIAL_DEBUG | Included if safe | Needs classification before certified binding |

An environmental dependency materially affecting behavior must be represented
by a separate BEHAVIORAL safe identity/value; the category is not permission to
omit semantic provider, account, or source-selection dependencies. Operational
limits affecting evidence coverage need explicit review, not blanket exclusion.
Raw secrets must never be passed through a non-secret value or metadata field.

## 5. Configuration sources

Supported repository source kinds are CODE_DEFAULT, SETTINGS_MODEL, ENVIRONMENT,
DOTENV, DATABASE, PROFILE (repository YAML/profile definitions), REQUEST,
PIPELINE_CONTEXT, POLICY_VERSION, and UNKNOWN. These reflect actual code paths;
there is no universal resolver or precedence imposed by the enum. No unsupported
rule-file, feature-flag service, or model-configuration service is invented.
Unknown sources cannot carry fabricated identifiers or versions.

Metadata accepts bounded non-secret identifiers, not URLs, DSNs, credential-bearing
paths, arbitrary reprs, or unrestricted metadata dictionaries. This format check
does not prove an identifier is non-secret: only resolver-owned, safe identifiers
are authorized. Identifiers are never derived from secret bytes.

## 6. Resolution authority

The native Settings constructor, Ranking parser, Regime loader/consumers, Sector
loader, CERI typed loader/taxonomy, IBMI loader plus module enablement resolver,
Setup/Lifecycle loader, database alert-rule selector, Winner loader/model builder,
and named readiness policies own their respective resolutions. The new shared
constructor owns canonicalization/freezing, not source selection.

`effective_configuration_families.py` supplies three opt-in examples. Existing
production producers do not call them automatically. Caller-supplied resolved
objects default to UNKNOWN provenance. The target adoption sequence is: native
resolution once → freeze → bind Calculation Identity → write immutable evidence →
consume exact frozen evidence. Every adopted adapter must reject an incomplete
family and document its native precedence.

## 7. Source precedence

Precedence is resolver-specific and represented highest priority first.

- Settings: explicit constructor values > process environment > configured `.env`
  > code defaults, with Pydantic validation/aliases and cached `get_settings()`.
  No repository-defined global YAML/DB overlay exists for Settings.
- Ranking: explicit selected YAML path/name selects a source; per-field YAML
  values > parser defaults. Required weights/components have no fallback. The
  parser validates all profiles and returns enabled profiles only.
- Technical v4/v5: YAML overrides recursively replace code-default leaves. The
  feature engine uses truthy supplied params, otherwise loads native defaults.
- Regime: explicit path selects the YAML source; required sections are validated;
  loader and consumer `get`/`or` fallbacks resolve optional values. Technical
  feature calculation independently loads Pine and merged v4 configuration.
- IBMI: explicit path > Settings path for source selection. Module enabled is
  Settings global flag AND Settings module flag AND YAML section enabled; this
  is conjunction, not an override ladder.
- Fundamental, Combined, Sector, CERI, Setup, and Winner retain native source/path
  selection and parser/consumer defaults; the precise families are in the report.
- Readiness policies are code-defined versioned matrices, with no configurable
  permissive override. Alert rules are selected database rows frozen separately.

Precedence metadata explains native rules; it does not apply overrides itself.
Only resolved winning values enter the snapshot, never discarded raw candidates.

## 8. EffectiveConfigurationSnapshot

Frozen dataclasses contain the family, immutable tuple of entries, optional
producer version, and optional aware evaluation datetime frozen in UTC. Nested caller objects
are immediately encoded into canonical JSON strings; retaining the caller's
mutable mapping/list is forbidden. Exports allocate fresh dictionaries. Entry
classification and type are explicit; secret values are discarded before
serialization, repr, or hashing. Secret entries are omitted from all exports.

Ranking's registered family covers every parsed profile field: enablement,
weights, technical components, missing-data policy, thresholds, penalties, gates,
tradeability overlay, and display metadata. Regime covers the typed policy config,
native optional fallbacks, Pine configuration, and merged Technical v4 feature
configuration. Readiness covers producer, consumer, policy version, module, and
required producer-readiness version. Whole Ranking also consumes scoring/earnings
configuration, which is a separate T13B adoption dependency.

## 9. EffectiveConfigurationIdentity

The existing Phase-1 type holds namespace, SHA-256 `DigestIdentity`, shared
effective-value schema `VersionIdentity`, and coverage. The schema identity is
independent of a particular source or loader: two resolvers producing equal
values under the same schema may be semantically exact. The actual resolver and
its version belong to provenance. No Phase-1 field is removed or renamed.

## 10. Semantic hash

`semantic_hash = SHA256(Phase2CanonicalJSON(semantic_payload))`. The payload
contains the shared contract schema, family namespace/schema, and sorted
behavioral key/value-type/tagged-value pairs. Operational/display/environmental
and secret values do not enter this payload. Policy/model versions governing
behavior must be behavioral entries or pinned algorithm dimensions, not labels.

## 11. Resolution/provenance hash

`resolution_hash` hashes the semantic payload, native resolution contract, and
sorted non-secret entries including classifications, values, sources, and
default/override markers. Operational changes may alter this hash. Evaluation
time and producer metadata are evidence context, outside both configuration
hashes. Raw secrets and even secret-entry keys/sources are absent.
Equal semantic values with different provenance remain EXACT; different
resolution hashes are not a behavioral incompatibility test.

## 12. Canonicalization

The Phase-2 `CanonicalEvidenceSerializer` remains the JSON/UTF-8/SHA-256 authority:
sorted keys, compact separators, explicit null/boolean, finite numbers, and no
repr/default-string fallback. Configuration v1 adds typed value tags before
calling that serializer; existing Phase-1/2 payloads and hashes are unchanged.
String mapping keys are required; unsupported runtime objects and nested
secret-bearing keys fail closed. Enums use their underlying values.
Datetime values must be aware and serialize in UTC with six fractional digits;
dates and times use Phase-2 ISO profiles with distinct type tags. No filesystem,
locale, hash-seed, or object-memory-address ordering is consulted. Standalone fixed
timezone offsets with fractional seconds fail closed because the Phase-2 timezone
descriptor does not preserve that precision; aware datetimes retain microseconds.

## 13. Numeric semantics

REAL entries treat supported int/float/Decimal representations as numerical
values: `0.5`, `0.50`, and `Decimal('0.50')` become tagged real `"0.5"`.
STRUCTURE recursively distinguishes integer `1`, real `1.0`, string `"1"`, boolean
true, and null. Nested float and Decimal real values normalize to the same fixed
point text. Declared INTEGER vs REAL remains distinct; adapters declare REAL only
where native coercion proves that equivalence. No rounding or epsilon comparison.
NaN/infinity are rejected; negative real zero becomes `"0"`. Fixed point trimming
does not use Decimal.normalize(), preventing ambient precision from rounding.

## 14. Ordered/unordered collections

Lists/tuples preserve order and share the ordered-list tag. Sets/frozensets sort
their tagged members by canonical bytes and share a distinct unordered-set tag.
Mappings sort keys. A list is never guessed to be a set from its key/name;
configuration tags also prevent Phase-2's reason-code ordering heuristic from
reordering ordered config lists. Provider priority and preference lists retain
their native order. An adapter may supply a set only if its semantics are unordered.

## 15. Secret handling

SECURITY_SECRET input is discarded immediately, with no digest, sentinel derived
from bytes, retained object, candidate value, or source metadata. Secret-only
changes leave both hashes unchanged. Common secret-bearing key names cannot be
classified non-secret; nested password/token/key structures are rejected and
must be split into explicitly secret entries. Do not serialize a whole Settings
dump, provider object, environment, or DSN. Synthetic exclusion tests and a local
value-aware scan protect the new code/docs/tests without printing credentials.
This contract assumes trusted classification and safe metadata supplied by the
native resolver; it cannot identify every arbitrarily mislabeled secret string.

## 16. Calculation Identity integration

`bind_configuration(identity, snapshot)` returns a new CalculationIdentity with
the existing configuration dimension KNOWN and preserves every other dimension.
Unknown classifications cannot bind certified complete configuration. The original
identity is untouched. Different semantic configs yield different Calculation
Identity fingerprints under otherwise equal inputs. Data, calendar, temporal,
algorithm, and ownership validation still apply independently.

## 17. Immutable evidence integration

Migration required: **NO**. Phase-2 `CoreCalculationEvidence.payload_json` already
provides immutable, addressable, hashed configuration storage. The opt-in
`effective_configuration` argument to `persist_core_evidence` requires an EXACT
bound configuration dimension. The writer owns
`effective_configuration_at_creation`, rejects caller-pasted metadata, and embeds
the snapshot before readiness/payload/evidence hashing. Without this argument,
existing producers retain their old behavior and evidence shape.

Semantic identity is content-addressed; exact evidence retries reuse the unique
evidence key. Identical configs across distinct decisions may be embedded more
than once: T13A does not add a global physical deduplication table. This avoids
parallel storage/FK infrastructure while preserving the exact configuration
alongside each adopted decision. Queries follow exact evidence IDs and their
configuration dimension, not current resolver state. A future shared store may
deduplicate physical payloads without changing semantic identity.

`configuration_from_evidence` verifies semantic and resolution hashes, the stored
identity binding, the evidence payload fingerprint, and Calculation Identity
fingerprint. It returns only the frozen identity. The frozen tagged payload
retains effective values/provenance for reproduction and explanation. Unknown
history never calls a resolver. ORM append-only protections reject updates/deletes,
as in Phase 2; this is an application-writer boundary, not protection against
privileged direct SQL or database administrators. No production backfill occurs.

## 18. Compatibility semantics

| Result | Rule |
|---|---|
| EXACT | Known complete SHA-256 identities, equal namespace/schema and semantic digest |
| COMPATIBLE | Reserved for an explicitly reviewed consumer policy; none implemented |
| INCOMPATIBLE | Known complete identities differ in semantics, namespace, or schema |
| UNKNOWN | Missing/current unknown/not-applicable/partial/malformed proof |
| LEGACY_UNKNOWN | Either side lacks historical certified configuration |

Same run, ticker, subsystem, profile name, or rule name proves no compatibility.
Unknown never means compatible, including unknown compared to itself. Different
behavioral hashes default to INCOMPATIBLE; metadata cannot grant an exception.

## 19. Legacy semantics

An evidence row without the frozen snapshot returns LEGACY_UNKNOWN even if a
Phase-1 debug config digest exists. Its historical values are not certified by
the new snapshot contract. No row is assigned today's configuration or rewritten.
Absent newly resolved configuration can be UNKNOWN without being historical.

## 20. Configuration drift

`ConfigurationDrift` retains historical and current dimensions and a typed
comparison. `detected` is false for EXACT, true for INCOMPATIBLE, and null when
proof is unavailable. C2 does not alter C1. Provenance-only divergence is visible
through resolution hashes without being semantic drift. No UI enforcement is added.

## 21. Policy/model versions

The readiness adapter accepts existing TechnicalConsumerPolicy and
ContextualConsumerPolicy instances, including Winner's policies. It freezes the
native producer/consumer/module/version requirements; the code-defined matrix is
identified by its existing policy version. Policy evaluation is untouched. Winner
model/cohort/feature-schema versions remain separate algorithm identities until
their configuration builder adopts exact effective snapshots in T13D.

## 22. Current vs historical config

Current dashboards may resolve current configuration and display names/versions.
Historical evidence reads its exact frozen payload or explicit unavailability.
Current settings caching, YAML reloads, admin rule edits, profile changes, model
publication, or pipeline resume cannot reinterpret the stored historical payload.

## 23. Proof boundaries

The semantic hash proves canonical effective behavioral payload equality within
the declared schema/family. It does not prove configuration correctness, complete
subsystem adoption, correct data/algorithm, temporal eligibility, external provider
state, secret validity, or readiness permission. Provenance records resolution
claims; its hash does not independently verify those claims against a live source.
Coverage requires native authority auditing. Hashes do not replace exact evidence
source pins, temporal fences, or consumer readiness policies.

## 24. Phase-4 adoption plan

T13B adopts Fundamental, Technical, Combined, and Ranking. T13C adopts Regime,
Sector, CERI, and IBMI. T13D adopts Setup, Lifecycle, Alerts, Winner, readiness
policies, and relevant job/pipeline propagation. T13E certifies the complete graph,
drift, historical isolation, authority inventory, and compatibility. RANK-005,
CORE-008, WIN-003, CERI-008, and SETUP-007 remain open for configuration adoption.
XINT-010 is partial; INV-CONFIG-001 is foundation implemented and enforced only
on the new opt-in writer path.

## 25. T13B core adoption and authority boundary

Sections 1–24 establish the T13A contract and its then-current adoption state.
The following sections supplement that state for T13B; the shared canonical
identity, classification, provenance, compatibility and legacy rules are unchanged.
T13B is certified PASS within the supported core producer boundary. Detailed native proofs,
lane accounting and finding dispositions are in
[the T13B report](../remediation/calculation-lineage/T13B_core_configuration_adoption.md).

| Producer | Family / schema | Authoritative entry resolver | Native sources / key groups |
|---|---|---|---|
| Fundamental v2 | `core.fundamental` / `core.fundamental-v1` | `resolve_fundamental_configuration` | Validated fundamentals YAML; model, weights, missing data, thresholds, field priorities, coverage fields, components |
| Technical | `core.technical` / `core.technical-v1` | `resolve_technical_configuration` | Pine YAML, native v4 file/default merge, active v5 rules, three native Settings flags and explicit benchmark request |
| Combined | `core.combined` / `core.combined-v1` | `resolve_combined_configuration` | Own mixture/rescaling, penalties, labels, fully merged earnings gate |
| Ranking | `core.ranking` / `core.ranking-v1` | `resolve_ranking_configuration` | Selected parsed profile rules, missing-data policy, thresholds, gates, penalties, tradeability overlay and own earnings gate |

The complete per-key defaults, tagged types, classifications, sources, resolver,
schema, consumers and identity coverage are in
[the executable inventory](../remediation/calculation-lineage/T13B_core_configuration_inventory.csv).
Repository defaults produce 600 entries across the four families and five
profiles, including 577 behavioral and 23 frozen observability entries.
Technical inventory depends on actual enabled/shadow calculation flags.

## 26. Resolve, execute, bind and freeze

Each supported producer resolves before financial calculation. The
`CoreEffectiveConfiguration` wrapper retains only the shared immutable snapshot;
native value trees are decoded afresh, preventing caller alias mutations. Values
must round-trip without numeric loss. Native helpers receive those explicit trees.
Calculation Identity uses the existing effective-configuration dimension, bound
to the same snapshot's namespace, schema and semantic digest. No parallel ID model.

The writer requires the exact typed snapshot for known `core.*` identity, verifies
identity correspondence, owns `effective_configuration_at_creation` and embeds it
before evidence hashing. It retains semantic/resolution hashes and full safe
values/provenance in existing immutable evidence. Native input/context/code/source
identity and independently frozen Phase-3 permissions remain present. No new
table/column/index/FK or migration, production rewrite or legacy backfill.

Fundamental resolves once per batch. Technical entry/coordinator resolves each
native file once, passes Pine/v4 through every feature/HTF/RS/Pine worker and
market helper, and shares its snapshot through finalization/fallback. Certified
Technical finalization cannot guess prior feature configuration. Empty parameters
cannot activate a hidden live fallback. Operational Settings are privately copied;
three behavior flags are replaced from the snapshot. Existing cache hashes use
the same explicit parameters. Combined resolves its own rules before decisions.
Ranking parses once per step and freezes each selected profile before ticker math.

## 27. Native precedence and semantic exclusions

File values/default merges keep native authority. Ephemeral source metadata sits
outside native dict/dataclass value semantics. Settings keeps constructor >
environment > dotenv > file-secret > code-default precedence. Only the three
Technical flag winners are traced; no credential/source dictionaries are retained
in the trace. Private normalized booleans/source kinds are excluded from model_dump.
Hand-built objects without native trace retain honest unknown provenance or the
resolved Settings boundary, rather than fabricated environment/file sources.

The family precedence descriptor records Technical's layered REQUEST > resolved
SETTINGS_MODEL boundary > ENVIRONMENT > DOTENV > PROFILE > CODE_DEFAULT; native
Settings winner tracing explains its inner precedence. Other families declare
REQUEST > PROFILE > CODE_DEFAULT. This does not introduce competing global sources
or new file overrides. Logical source/default identifiers are safe; absolute file
paths are omitted unless an explicit logical identifier is supplied.

Names, descriptions, unused port/debug/benchmark labels and flags overridden by
active selection remain frozen explanation outside semantic hashing. The actually
used benchmark, financial rules and active v5 configuration are behavioral.
Operational execution/cache/resources and secrets never become financial identity.
Actual algorithm/engine versions remain independently represented by code identity.
Native configurable defaults are materialized; fixed formula constants remain
under their existing algorithm version contract.

## 28. Composition and Ranking profile granularity

Combined and Ranking include only their own configuration. Fundamental, Technical
and IBMI producer rules propagate through exact upstream evidence/identity pins.
New core source references use immutable evidence addresses so compatibility-row
replacement cannot alter an exact retry's lineage or diagnostic payload. This
does not change eligibility decisions, policy versions or financial behavior.

Each Ranking snapshot contains one selected profile plus own earnings gate.
Changing Defensive does not invalidate Momentum. Equal selected rules with
different sources have equal semantic identity/different resolution hash. Same
profile name with 45/55 -> 40/60 weights produces different semantic and Calculation
Identity fingerprints and distinct immutable evidence. Profile name/label/
description/schema/provenance remain frozen, and old rules remain inspectable.

## 29. Historical authority, legacy and retries

Historical readers verify the exact evidence payload/identity before rehydrating
the snapshot and checking both hashes. No current Settings, YAML, environment,
profile/default or database rule is consulted. Ranking result readers eager-load
exact evidence; result APIs present their frozen profile and per-result snapshot.
Export selection no longer requires a surviving current profile definition.
Current catalogue and contextual market display remain separate current views.

Legacy evidence without a snapshot remains LEGACY_UNKNOWN, including old debug
hash/name remnants. No current-rule substitution or backfill. Core entry APIs
accept frozen snapshots for retries; an incompatible supplied expected Calculation
Identity fails before financial math. Native PG retries rehydrate C1 after current
resolver attack and reuse exact identity/evidence. Unanchored current resolution
creates the current configuration identity; it cannot retain a supplied C1 anchor.
Durable storage/delivery of anchors across pipeline restart remains T13D.

## 30. T13B coverage, proof and handoff

The bounded native AST census plus manual call-graph review records 237
T13B_FROZEN, 28 OPERATIONAL_ONLY, 21 DISPLAY_ONLY, 2 T13C, 2 T13D, zero detected
TEST_ONLY and zero material UNKNOWN_REMAINING reads. It is not repository-wide
dataflow certification. Certified live material behavioral reads are zero;
explicit reads from resolved frozen trees and code-versioned formulas remain.
Legacy standalone v1/preview/helper fallback APIs do not certify original config.

The final focused lane passes 70 tests; core representatives pass 292, full PG
passes 78, Phase-3 passes 563, Phase-1 passes 135 and Phase-0 passes 86 + 71.
The final broad lane passes 3,203 tests with seven established live IBMI cases
deselected and zero failures/skips. Native baseline/current business bytes are
identical (SHA-256
`52e42c9f6721371f494fd0ade8f65163771514cc29b1d2d504d57504d356c0f1`).
No unexpected score/readiness/decision behavior change. Native resolution-count
tests prohibit per-ticker file reads and repeated per-step profile parsing.

RANK-005 is CLOSED for newly certified selected profile results, with legacy
explicitly unknown. CORE-008 remains OPEN for Regime in T13C. XINT-010 remains
PARTIAL. INV-CONFIG-001 is ENFORCED for supported certified core producers and
PARTIAL repository-wide. T13C adopts Regime/Sector/CERI/IBMI; T13D adopts
Setup/Lifecycle/Alert/Winner and durable configuration/policy propagation;
T13E certifies integration. Privileged direct SQL, original-context replay and
truth of external provenance claims retain the existing stated proof boundaries.

## 31. T13C native contextual families

`contextual_effective_configuration.py` adapts the native parsers to the existing
T13A tagged snapshot, semantic identity and resolution hash. It does not define
another identity or ledger. Families are `contextual.regime`, `contextual.sector`,
`contextual.ceri` and five `contextual.ibmi.<metric>` families: liquidity,
short_pressure, volatility, options_activity and histogram. Each uses its own
`<namespace>-v1` snapshot schema. Native normalization runs before financial math;
services consume native typed adapters reconstructed from the frozen values.

The resolver boundary declares REQUEST > SETTINGS_MODEL > ENVIRONMENT > DOTENV >
PROFILE > CODE_DEFAULT. This describes existing layers, rather than adding a new
override path. File parsers retain actual winning leaf provenance. Changed explicit
requests cannot inherit a former file winner. The Settings trace now separately
retains the names and source kinds of five contextual booleans in addition to the
three core booleans; it never retains complete Settings/source dictionaries.

## 32. Complete Regime and Sector authority

Regime freezes the normalized eight native policy roots, all nine supported Regime
mappings and policy fallbacks, freshness, required symbols and the Pine/v4 rules
actually consumed by market-input feature calculation. Missing policy/mapping,
missing feature groups and material UNKNOWN provenance are rejected. Unused feature
groups, disabled-group children and unused display switches remain explanatory
outside semantic hashing. Explicit frozen feature arguments remove hidden parser
reads inside `_load_market_input`.

Regime compatibility still compares temporal/calendar/context and source identity.
Run identity does not become a substitute for those dimensions: same-context,
same-configuration cross-run use remains permitted; different configuration fails
compatibility even when output values happen to match.

Sector freezes its own universe, weights, profile selection, policy, confidence,
taxonomy/proxies and active ETF feature rules. It does not copy upstream Ranking,
Regime or prior-Sector configuration. ETF-disabled children are display-only;
enabling ETF freezes its actual Pine/v4 feature dependencies. A prior S1 retains
its immutable C1. Existing strict raw hash/mode/calendar/prior-context compatibility
continues to govern whether current C2 may consume S1; this change does not relax
the native same-configuration prior-state rule. The actual opaque full own-config
hash used by prior selection is frozen as behavioral selection authority, because
even directly inactive fields can affect that legacy compatibility filter.

## 33. CERI four-output and ordered native policy authority

CERI freezes its calculation config, resolved taxonomy, weights, windows,
freshness/coverage/confidence rules, active provider order and code-owned posture,
guidance, event-risk and confidence policies. Capture additionally freezes the
actual Settings winners controlling capture and IBMI consumption, its own IBMI
volatility contribution cap, and the actual revision-feature selection hash.
Standalone scoring snapshots declare that capture context has not been supplied;
they cannot silently certify the capture input-selection rule.

The native provider conflict selector consumes frozen priority, quality, freshness
and source-id ordering. The native guidance scorer consumes frozen timestamp/id
ordering and continues its existing behavior of ignoring provider priority.
Freezing these distinct active policies does not close CERI-001's separate
selection algorithm defect. The configuration file's declared posture labels do
not replace the code-owned posture rules actually used by the four outputs.

Alert/change thresholds, backfill, exports and retention remain with their native
downstream loaders for T13D. Provider transport/capability metadata and unused
display clauses are excluded from financial semantic hashing. Capture's legacy
revision-feature filter really compares the full native CERI raw hash, which can
change following downstream-only file edits. Its opaque filter value is therefore
behavioral capture authority; claiming all such file edits are semantically
neutral would misdescribe the existing algorithm. Redesigning that legacy filter
is separate integration work, rather than silently changing selected inputs.

## 34. IBMI metric granularity and operational separation

Each metric freezes only its engine/source versions, own mathematical parameters,
materialized native defaults and the freshness fields that its calculator uses.
Liquidity freezes its windows, minimum dollar volume and spread grades; short
pressure freezes fee/availability thresholds and historical/live ages; volatility
freezes IV/lookback/ratio rules and historical age; options activity freezes
activity/put-call thresholds and live age; histogram freezes activity fraction
and percentile rules. An edit to liquidity does not invalidate volatility.

Acquisition use-RTH/period/generic-tick switches, enable/shortlist flags and unused
freshness fields are operational for these calculations. Timeout, retry, request
budget, pacing, concurrency, cache, host/port and scanner transport settings do not
enter their semantic identity. Credentials/Flex tokens and full transport trees
are absent from snapshots. The existing safe legacy configuration hash is retained
as operational compatibility metadata so a frozen retry preserves the native
evidence address; it is not reconstructed from the scoped subset.
The native compatibility hashing boundary excludes secret-bearing keys and login,
cookies/authorization. Credential-only edits therefore cannot influence retained
digest values or either snapshot hash. Existing files without secret keys retain
their original compatibility hash.

## 35. Exact contextual evidence, history and retries

New contextual identities replace the existing configuration dimension with the
shared semantic hash/schema identity before metadata or evidence is produced.
The shared core writer requires the exact matching snapshot for new contextual
namespaces and embeds it in existing immutable evidence. Exact ORM evidence
pointers and payload/identity fingerprints are checked before historical decoding.
No migration or financial model field is needed.

Historical decoding and typed adapters use only the embedded tagged values.
They consult no current files, Settings, environment, thresholds or parser
defaults. Legacy evidence without a snapshot remains LEGACY_UNKNOWN; no backfill
or upgrade through present-day defaults. Provided frozen C1 is usable for a retry;
a supplied expected C1 configuration rejects C2 before financial calculation.
This is a configuration anchor check, alongside existing temporal/source/write
fences, rather than a claim of complete durable retry delivery.

Frozen families are reused at their native context/run scope. Repeated per-ticker
resolution uses immutable adapters/cached snapshots and no file/Settings reads.
Winner's current Regime/Sector producer expectations are also frozen once per
native RunCaptureContext, so complete Regime feature rules do not introduce
per-ticker parser reads. A new run context resolves current rules again; this
ephemeral producer expectation cache does not adopt Winner's own T13D policy.
Local tagged JSON decoding, validation and hashing remain real CPU work and are
not characterized as free or as a certified production latency improvement.

## 36. T13C proof boundary and T13D handoff

The deterministic leaf inventory covers native defaults plus active ETF Sector:
735 behavioral, 42 operational and 149 display leaves. A bounded AST census records
52 native source-boundary call sites, with manual call-graph interpretation in
the T13C report. These different units are not a repository-wide dataflow proof.
Certified contextual paths have zero remaining live material behavioral rereads.
Transport/upstream current selection and downstream policy/delivery reads remain
separate authority boundaries, explicitly inventoried for T13D.

Phase-3 permissions and readiness policy versions remain independent and unchanged.
No CERI-to-Ranking/Setup/Winner, IBMI-to-Winner or Sector-to-same-run-Ranking edge
is introduced. CORE-008 closes for newly certified complete Regime policies;
legacy is unknown. RANK-005 remains closed; CERI-001 algorithm work, XINT-010 and
repository-wide INV-CONFIG-001 remain partial. T13D must adopt own Setup,
Lifecycle, Alert, Winner and readiness consumer policy authority and transport
configuration/identity/evidence anchors through durable jobs and resume. T13E
certifies integration; original-context reconstruction and privileged SQL
governance remain deferred. See the 36-section T13C remediation report for the
native resolver/key/function inventory and executable certification record.

T13C final certification: 3,233 safe broad tests; 177 PostgreSQL/focused tests
(82 PG + 95 configuration units); Phase-3 563, Phase-1 135, Phase-0 86 + 71.
Zero failures/skips; seven established external IBMI cases deselected in broad.
The identical 211-test native probe yields 308 exact business captures, SHA-256
`c2aca12aed30ea178d2542a64e8404764a5362fc442c055323c89498849e49df`.
