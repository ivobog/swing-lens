# T10A — Calculation Identity Contract and Typed Envelope

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T09A commit | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B commit | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| T09C commit / T10A starting HEAD | `bbb345b3257d0df572db425242c62ea1447e5d24` |
| Branch | `codex/t10a-calculation-identity-contract` |
| Final HEAD | The focused T10A commit containing this report; the exact SHA is recorded in the final response because a commit cannot contain its own SHA. |
| Alembic head before/after | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 from `.venv` |

The starting HEAD exactly matched the required T09C base. There were no tracked
modifications. The pre-existing untracked architecture/audit registry and IB historical
probe files were preserved; they are neither implementation drift nor T10A outputs.

## 2. Problem statement

Execution identity answers who owns or is attempting work. It can contain an upload run,
pipeline run, background/root job, execution token, and worker attempt. Calculation identity
answers which exact semantic truth was produced. Equal `run_id + ticker` does not prove equal
pipeline, context, session, cutoff, calendar, effective configuration, algorithm, source
revision, or generation.

T10A introduces separate `ExecutionIdentity` and `CalculationIdentity` types. The
compatibility API accepts only the latter at runtime and by type annotation. Operational
job IDs, attempt numbers, worker identity, and execution tokens have no field in the semantic
envelope. `CalculationOwnership` retains only stable run and pipeline references; these are
necessary under strict decision policy but never sufficient.

## 3. Existing identity primitives

| Subsystem | Existing identity primitive | Persisted? | Validated? | Reusable? | Strong dimensions | Missing dimensions | Source for `CalculationIdentity`? |
|---|---|---:|---:|---:|---|---|---:|
| Upload | `UploadRun.id` | Yes | FK/run ownership | Ownership only | Run, upload metadata | Context, time, config, sources | Yes, ownership reference only |
| Pipeline | `PipelineRun.id` + `upload_run_id` | Yes | Relational ownership and job validation | Ownership only | Pipeline/run relationship | Semantic inputs and versions | Yes, ownership reference only |
| Market context | `MarketCalculationContext` / `MarketCalculationCutoff` + canonical fingerprint | Yes | Strong pipeline resolver and fingerprint | Yes | Run, pipeline, context, cutoff, completed session, calendar/readiness versions | Calculator config/algorithm/sources | Yes; first T10A producer |
| Decision handoff | `TransitionDecisionHandoffManifest` | Yes | Canonical payload/fingerprint and preflight/handoff comparison | Yes within declared boundary | Pipeline/context/cutoff, source IDs/hashes, Setup config/engine, pointer expectations | Repository-wide consumer adoption | Yes, future source-lineage adapter |
| Technical cache | `LocalArtifactKey` / `TechnicalFeatureArtifact.input_signature` | Yes | Keyed lookup and shadow validation | Yes within cache boundary | Ticker, price-series versions, session, feature/scoring config, engine/schema | Final score readiness and broader context | Yes, future feature-reuse adapter |
| Price series | `PriceSeriesVersion` plus `PriceBarRevision` | Yes | Canonical PIT reader/revision projection | Yes | Ticker, timeframe, basis, series version, bar revisions/knowledge time | Consumer config/algorithm | Yes, source references |
| CERI | processing/ingestion runs, feature/snapshot context/session/cutoff/config/calculation/evidence hashes | Yes | Strong on modern pipeline artifacts; weaker direct/legacy paths | Partly | Run/context/session/cutoff/config/version/source evidence | Uniform entry-point and legacy coverage | Yes, future CERI adapter |
| Setup/lifecycle | snapshot source/config/schema/engine identity and canonical transition manifest | Yes | Canonical transition preflight strong; alternates weaker | Partly | Run/pipeline/context/session/cutoff/config/source hash/transition expectation | Uniform alternate-path compatibility | Yes, future Setup adapter |
| Winner prediction | frozen feature vector/hash, config/calculation/schema, copied handoff metadata | Yes | Immutable-value conflicts; acquisition compatibility incomplete | Partly | Run/ticker, frozen vector, config/calculation/schema | Original pipeline/context compatibility | Yes after T10D acquisition work |
| Winner generation | cohort contract, generation key, material watermark, root/evidence manifests | Yes | Content/completeness/publication validation | Yes | Config, calculation/schema/policy versions, outcome definition, generation/watermark/cutoff | Prediction acquisition identity | Yes, generation/source fields |
| IBMI | intelligence run, output session/cutoff/config/engine/content hash | Yes | T09A bounds historical selectors; constituent envelope remains incomplete | Partly | Run/module/ticker/session/cutoff/config/version/hash | Market context and complete constituent revisions | Yes, future IBMI adapter |
| Regime | versioned snapshot, session/cutoff/context/evidence/calculation/config version | Yes | Repository selection/version validation | Partly | Versioned result and temporal fields | Full effective config/source identity | Yes, with unknown config until adopted |
| Sector | versioned snapshot and full config hash | Yes | Versioned repository key/evidence hash | Partly | Run/session/cutoff/context/config/calculation/revision | Compatible upstream Ranking/regime envelope | Yes, future source adapter |

Strong existing fingerprints remain authoritative only for their declared payloads. T10A
wraps or references them; it does not duplicate source rows, replace their schemas, or claim
that an existing partial hash is a complete identity.

## 4. Calculation Identity contract

The canonical implementation is `app/services/calculation_identity.py`. The envelope is
composed of typed groups:

| Group | Fields | Role |
|---|---|---|
| `CalculationOwnership` | typed `run_id`, `pipeline_id` dimensions | Stable execution ownership references, not attempt authority |
| `CalculationSubject` | typed ticker and company ID dimensions | Company/security identity; ticker is canonical uppercase |
| `CalculationContextIdentity` | market context ID and typed SHA-256 fingerprint | Reference to an existing canonical calculation context |
| `TemporalIdentity` | session date, aware cutoff instant, typed calendar identity | Explicit decision/business-time envelope |
| `ConfigurationIdentity` | `EffectiveConfigurationIdentity` | Namespace, full resolved-config fingerprint, resolution-contract version, coverage attestation |
| `AlgorithmIdentity` | calculation/model/schema/engine versions and ordered semantic components | Exact calculation semantics without overloading a model name |
| `SourceLineageIdentity` | typed artifact references, revision states, fingerprints, aggregate fingerprint, proof boundary | Exact source/revision envelope without top-level row-ID expansion |
| `GenerationIdentity` | generation ID, key, material-watermark fingerprint, generation cutoff | Versioned-output identity, explicitly N/A for simpler calculators |

`DigestIdentity` always declares algorithm, digest, and proof boundary.
`VersionIdentity` always declares namespace and version. `CalendarIdentity` separates
calendar name/version, exchange timezone, and bar-readiness version. Execution token,
background job ID, root job ID, and worker attempt exist only in `ExecutionIdentity`.

### Core versus context-specific dimensions

Strict decision compatibility requires run, pipeline, ticker, market context, temporal
identity, fully resolved effective configuration, calculation version, and source lineage.
Company ID and optional algorithm subversions may be N/A when the policy permits it.
Generation is context-specific: all four generation dimensions may be N/A together for a
non-generated calculation, or must be complete together when used. Narrow named policies
select only the dimensions their producer→consumer edge can legitimately prove.

## 5. Required, optional, N/A, and unknown semantics

Every variable dimension is an `IdentityDimension` with exactly one state:

| State | Meaning | Compatibility behavior |
|---|---|---|
| `KNOWN` | The typed value is supplied and structurally validated | Compared under the selected policy |
| `UNKNOWN` | The current producer cannot prove the value | Never equals another unknown; required comparison is `INSUFFICIENT_IDENTITY` |
| `NOT_APPLICABLE` | The dimension has no semantic meaning for this calculator | Passes only when both sides are N/A and the named policy permits N/A |
| `LEGACY_UNKNOWN` | Historical artifact predates the identity contract and cannot be reconstructed | Never passes strict compatibility |

Non-known states cannot carry a hidden/null value. `None == None` is therefore never used as
compatibility evidence. Nested unknowns inside known calendars or source-lineage structures
are detected and also produce `INSUFFICIENT_IDENTITY`.

## 6. Validation contract

Validation answers whether one envelope is structurally coherent; it does not authorize a
consumer edge. It checks:

- schema version, positive numeric IDs, uppercase/non-empty ticker, and declared text;
- timezone-aware cutoffs and dates for sessions;
- a known pipeline only with a known run;
- a known as-of session only with known cutoff and calendar;
- a known market context only with a complete temporal identity;
- SHA-256 fingerprints as 64 lowercase hexadecimal characters with proof boundaries;
- effective configuration namespace, resolution contract, and explicit
  `COMPLETE_EFFECTIVE_CONFIGURATION` coverage (a `PARTIAL_DEBUG` hash is invalid here);
- non-empty, whitespace-free version namespace/value pairs;
- source references, duplicate references, nested revisions/fingerprints, and proof boundary;
- generation ID, key, watermark, and cutoff as an all-or-nothing set.

Unknown/legacy envelopes can be structurally valid so adoption can represent incomplete
history honestly. They still fail policies that require those dimensions. No validator or
builder obtains business time from `datetime.now()`, `date.today()`, current context, latest
context, latest configuration, or latest generation.

## 7. Compatibility contract

`CalculationIdentityCompatibilityValidator.compare(expected, actual, policy=...)` returns:

- `EXACT_MATCH`: full envelope fingerprints match and every policy dimension is proven;
- `COMPATIBLE`: every policy dimension is proven equal, while non-policy dimensions differ;
- `INCOMPATIBLE`: at least one known value or applicability state conflicts;
- `INSUFFICIENT_IDENTITY`: structural validation failed or a required dimension is unknown,
  legacy-unknown, or impermissibly N/A.

The defined, named policy set is:

| Policy | Boundary |
|---|---|
| `STRICT_DECISION_COMPATIBILITY` | Complete decision-time producer/consumer envelope |
| `PIPELINE_CONTEXT_COMPATIBILITY` | Run/pipeline/context plus session/cutoff/calendar repeated in a durable full-pipeline job |
| `TEMPORAL_COMPATIBILITY` | Canonical market context fingerprint and temporal envelope |
| `FEATURE_REUSE_COMPATIBILITY` | Subject, temporal, effective config, algorithm, and sources |
| `GENERATION_COMPATIBILITY` | Effective config, algorithm, sources, and exact generation identity |

There are no `ignore_config`, `ignore_session`, `ignore_cutoff`, or similar boolean escape
hatches. Compatibility accepts only `CalculationIdentity`, so `ExecutionIdentity` cannot
accidentally satisfy it. Diagnostics identify the dimension, expected value/state, actual
value/state, policy, and reason. Unknown required evidence is not downgraded to a mismatch.

## 8. Canonical representation

The schema is `calculation-identity-v1`. `CalculationIdentity.canonical_payload()` emits the
fixed typed group/field schema and delegates scalar normalization and JSON ordering to the
existing `CanonicalEvidenceSerializer`:

- mapping keys are lexicographically ordered by canonical JSON serialization;
- aware datetimes normalize to UTC with six fractional digits and `Z`;
- sessions serialize as ISO dates;
- versions serialize as `{namespace, version}`;
- unknown/N/A/legacy states serialize as `{state}` without an ambiguous null value;
- known dimensions serialize as `{state: KNOWN, value: ...}`;
- source references and algorithm components are ordered by canonical JSON bytes;
- unsupported or timezone-naive values fail closed.

`from_canonical_payload()` supports schema-checked deserialization. A construct → canonical
serialize → deserialize → fingerprint → serialize round trip is deterministic. Equivalent
timezone representations and semantically unordered source order canonicalize identically.

## 9. Fingerprint

`CalculationIdentityFingerprint` is lowercase SHA-256 over UTF-8 canonical JSON produced by
`CanonicalEvidenceSerializer`. It records algorithm and identity schema version alongside
the value.

**WHAT IT PROVES:** equality of every supplied typed state and value in the complete
`calculation-identity-v1` envelope after canonical normalization.

**WHAT IT DOES NOT PROVE:** external/source truth, artifact completeness, correctness of a
producer's semantic claims, executable-code equivalence, database immutability, or the
correctness of fields omitted/marked unknown/N/A. Nested source fingerprints retain their
own narrower proof boundaries.

## 10. Legacy behavior

`CalculationIdentity.legacy_unknown(run_id=..., ticker=...)` preserves only values genuinely
known and marks every unrecoverable dimension `LEGACY_UNKNOWN`. It does not infer an old
cutoff, calendar, context, config, source revision, or generation from current state.
Legacy identities remain serializable/loggable but return `INSUFFICIENT_IDENTITY` under
strict decision compatibility. Future backfill/isolation decisions are deferred to adoption
tasks; T10A fabricates no historical identity.

## 11. Minimal adoption

Production producer: `calculation_identity_from_market_context()` converts an explicit
`MarketCalculationCutoff` plus stable run/pipeline references into a valid typed identity.
It uses the existing complete market-context fingerprint. Dimensions the context cannot
prove are explicitly N/A. It has no current/latest/wall-clock fallback.

Production consumer: `validate_pipeline_job_market_context()` now builds the authoritative
context identity and the job payload's repeated context identity, then applies
`PIPELINE_CONTEXT_COMPATIBILITY`. A compatible payload is accepted; a calendar/session/
cutoff/context/ownership mismatch fails closed with dimension diagnostics. Structured logs
expose expected/actual identity fingerprints, policy, and result without config values,
source manifests, or secrets.

This is deliberately one bounded producer→consumer edge. Fundamental, Technical, Combined,
Ranking, CERI, Setup capture/lifecycle, Winner capture/acquisition, and IBMI signatures or
tables were not retrofitted.

## 12. Tests

| Required test/rule | Evidence |
|---|---|
| Identical construction/validation/fingerprint/strict match | `test_identical_identity_validates_fingerprints_equal_and_is_exact` |
| Run+ticker insufficient / pipeline mismatch | `test_same_run_and_ticker_do_not_hide_pipeline_mismatch` plus parameterized policy test |
| Context mismatch | Parameterized `market_calculation_context_id` mutation |
| Session, cutoff, calendar mismatch | Parameterized temporal mutations |
| Effective-config mismatch and partial-hash rejection | Parameterized config mutation; validation negative test |
| Calculation/model/schema/engine/component mismatch | Parameterized algorithm mutations |
| Source revision mismatch | Parameterized source-lineage mutation |
| Generation mismatch | Four-dimension generation parameterization |
| Unknown is not equality, including nested unknown | Unknown config and nested source-revision tests |
| N/A policy semantics | Named strict-policy model-version N/A test |
| Legacy strict failure | Legacy compatibility test |
| Canonical time/source ordering and round trip | Timezone/source-order round-trip test |
| Typed deserialization | Wrong scalar-type payload rejection test |
| One-field fingerprint sensitivity | All parameterized material-dimension tests |
| Missing cutoff, naive cutoff, malformed digest, partial generation | Structural validation negative tests |
| Diagnostic exact dimension/values/policy | Mismatch diagnostic test |
| Execution identity type separation | Runtime type rejection test |
| Real producer | Market-context adoption producer test |
| Real consumer compatible acceptance | Pipeline job adoption acceptance test |
| Real consumer mismatch rejection | Pipeline job calendar mismatch test |
| Observability | Adoption acceptance log assertions |

## 13. Test exclusions

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py`, IBKR/IB
  Gateway, live SEC/provider/network/API smoke, credentials, and marker `external`.
- Browser/E2E: `tests/e2e/`, Playwright, Selenium, webapp E2E, and markers `e2e`/`slow`.
- PostgreSQL-dependent: `tests/integration/`, disposable PostgreSQL fixtures, credentials,
  containers, and PostgreSQL concurrency certification.

These paths are intentionally excluded by T10A policy. No unfiltered `pytest` invocation
was run.

## 14. Verification totals

| Lane | Command scope | Result |
|---|---|---|
| Identity contract | `tests/test_calculation_identity.py` | **35 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Minimal adoption | `tests/test_calculation_identity_adoption.py` | **3 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Shared infrastructure | canonical serializer, session/context remediation, pipeline service | **63 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Phase-0 regressions | IBMI/CERI/Setup temporal containment, domain fence, Winner publication fence | **80 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Broader clean lane | `tests`, with exact exclusions in §13 and `not external and not e2e and not slow` | **2451 passed, 0 failed, 7 skipped, 33 deselected, 22 warnings** |

The repeated single warning is the existing Starlette/httpx deprecation. The broad lane's
remaining 21 warnings are existing Python 3.12 SQLite datetime-adapter deprecations. Its
seven skips are environment-dependent tests retained by the clean lane; the explicitly
excluded PostgreSQL integration directory was not collected.

Static verification: Ruff passed for all changed Python files and `git diff --check` passed
apart from Git's line-ending notices.

## 15. Schema impact

```text
migration required: NO
production data rewrite required: NO
```

No database table or migration was changed. The contract remains software-first so T10B+
can choose deliberate persistence/backfill/isolation strategies.

## 16. Residual work

- T10B: adopt identity across the core calculation chain.
- T10C: adopt named compatibility at contextual consumer boundaries.
- T10D: bind Winner acquisition to complete decision/source identity.
- T10E: certify Phase-1 adoption and update the implementation registry without rewriting
  the original audited-baseline registry.

No Phase-2 immutable-evidence, readiness, config-resolution, entry-point, historical
reconstruction, formula, broad signature, schema, or production-backfill work was performed.

## 17. Finding verdict

```text
XINT-001: PARTIALLY_CLOSED
```

The canonical contract and one real compatibility boundary exist, but most repository
producer→consumer edges still rely on their earlier identities until T10B–T10E.

## 18. Invariant verdict

```text
INV-IDENTITY-001 contract: ENFORCED
INV-IDENTITY-001 repository-wide: PARTIAL
```

Contract correctness is enforced by typed construction, structural validation, named
policies, deterministic canonicalization, and negative tests. Adoption coverage is partial.

## 19. T10A verdict

```text
T10A CALCULATION IDENTITY CONTRACT: PASS
```

The design review found no unsafe YES condition:

1. run+ticker cannot pass strict policy without pipeline/context/time/config/version/sources;
2. outer or nested unknowns never compare equal;
3. no identity API derives historical time from wall clock/current/latest state;
4. no ad-hoc ignore flags exist; consumers select immutable named policies;
5. source order is canonicalized before hashing;
6. effective config requires a full-resolution coverage attestation and full SHA-256;
7. attempt/job/token fields exist only in `ExecutionIdentity`;
8. generation policy compares ID, key, watermark, and cutoff;
9. a known historical session without cutoff cannot validate;
10. future tasks have one canonical module, builder pattern, validator, policy set, and hash.

No production runtime, database, provider, job, queue, pipeline, Winner generation, or
serving state was read or mutated.
