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
