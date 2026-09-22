# SwingLens Original-Context Reconstruction Contract

## Original context definition

Original-context reconstruction is a proof problem, not a timestamped recalculation.

`ORIGINAL_CONTEXT` means that every material input, policy, configuration, temporal boundary,
scope, predecessor, and source revision is proven to belong to the historical decision being
reconstructed. A current row is not historical authority merely because its business date is old.
Current state may be used only when independently proven to be identical to the historical
authority being reconstructed.

The reconstruction target is a historical decision artifact, not a date range alone. The target
must have a stable artifact identity and historical-decision identity.

## ORIGINAL_CONTEXT vs CURRENT_RULES_RETROSPECTIVE

SwingLens recognizes three disjoint modes:

- `ORIGINAL_CONTEXT` uses exact retained historical authority. It is executable only with status
  `EXACT`.
- `CURRENT_RULES_RETROSPECTIVE` applies the currently certified rules and configuration to
  historical inputs. It creates new lineage and never claims to reproduce the original decision.
- `CURRENT` calculates under current state and current semantic time.

An unavailable `ORIGINAL_CONTEXT` request returns `ORIGINAL_CONTEXT_UNAVAILABLE`. The caller must
make a new, explicit `CURRENT_RULES_RETROSPECTIVE` request to use current rules. No implementation
may downgrade modes implicitly.

## Reconstruction manifest

`OriginalContextReconstructionManifest` is the immutable, typed semantic input freeze. It records:

- target artifact type and identity;
- target historical decision identity;
- requested mode;
- each required authority dimension and whether it is material;
- exact, bounded, current-only, legacy-unknown, purged, or unavailable resolution;
- resolved immutable references and proof that a current-looking reference is historically
identical when that exceptional route is used, including a stable independent proof identity;
- calculation, scope, refresh, acquisition-plan, and code/deployment identity where applicable;
- derived completeness, reconstruction status, canonical JSON, and fingerprint.

For a cross-domain decision it also records the declared dependency edges, exact expected and
resolved producer evidence, the machine-readable compatibility contract and result for every edge,
and a proof-boundary fingerprint. Substituting a material producer therefore changes the manifest
fingerprint even when the final numeric value is unchanged.

Operational attempt data such as worker token, job attempt, or execution token is deliberately not
part of semantic identity. Persistence is deferred in T16A: the contract is frozen in memory and
content-addressed, while T16B/T16C must decide whether durable attempts/results need additive tables.

## Authority dimensions

The finite dimension set is implemented in
`app/services/original_context_reconstruction.py`. It covers calculation identity, work scope,
refresh cycle, acquisition plan, business cutoff, calendar/session, effective configuration,
readiness policy, source evidence and revision, provider, currency, possession time, predecessor,
prior decision, rule/policy version, execution-independent decision inputs, algorithm/schema
version, and code/deployment identity.

Only dimensions that materially influenced the target are required. A dimension that cannot affect
the result is omitted or non-material; it must not be invented to inflate completeness.

## Exact vs bounded reconstruction

`EXACT` requires every material dimension to be exact. Exact references must be retained historical,
content-addressed historical, an authoritative external archive, or independently proven identical
to the historical authority. One material gap prevents `EXACT`; percentages and confidence scores
cannot waive the gap.

`BOUNDED` means all non-exact material dimensions have an enumerated bounded set and the result can
be proved to lie within known alternatives. Bounded reconstruction cannot execute as or claim
`ORIGINAL_CONTEXT`. An arbitrary choice from the set is prohibited.

`INSUFFICIENT_EVIDENCE` means authority is missing without proof of permanent loss.
`PERMANENTLY_UNAVAILABLE` means a material fact was never retained or was purged without an
authoritative archive. `CURRENT_RULES_ONLY` is a mode-specific classification, not evidence of the
original rules.

## Evidence completeness

The completeness record contains material dimension counts: required, exact, bounded, unavailable,
and permanently unavailable. Its `exact_for_original_context` flag is true only when exact equals
required. Counts are diagnostic; authorization depends on the all-material exact predicate.

## Source/revision requirements

Historical source identity includes the consumed evidence object, exact revision or a
content-addressed exact body, provider when selection depends on it, and SwingLens possession time
when point-in-time eligibility depends on knowability. A later corrected source, latest database row,
or current provider response is a replacement, not original authority.

Source resolution must be set-based. The contract validates batches of 1, 50, and 200 references
without filling missing identities. Missing requested IDs or unexpected replacements fail closed.

## Configuration requirements

Phase-4-native decisions can carry an immutable effective-configuration identity. Pre-Phase-4 rows
without such evidence remain `LEGACY_UNKNOWN`. Today's `.env`, defaults, YAML, or resolved service
configuration may not backfill an old decision. A current configuration is acceptable only with an
independent content/identity proof that it is the exact historical configuration.

## Rule-version requirements

Thresholds, cooldowns, classification policies, confidence caps, feature interpretation, and schema
versions are semantic authority when they affect output. Git HEAD is not a rule-version proof.
Legacy rules absent from immutable decision evidence cause `CURRENT_RULES_ONLY` or
`PERMANENTLY_UNAVAILABLE`, according to whether historical inputs remain usable.

## Predecessor-state requirements

Setup/Lifecycle/Alert reconstruction uses immutable predecessor linkage: the predecessor snapshot,
episode, transition, alert decision, and cooldown state actually consulted. A current primary episode
or current pointer cannot establish the historical predecessor. Missing predecessor identity is a
material gap.

## Business-time requirements

The manifest freezes decision session, calculation cutoff, event session, and operation time where
operation time changes semantics. Point-in-time source eligibility uses SwingLens possession at the
historical cutoff; facts first possessed later are excluded. Wall clock at reconstruction time is
never semantic authority.

Historical calendar identity is material when a calendar/readiness version could change session
selection. For stable audited sessions, deterministic exchange-calendar rules plus retained session
and cutoff bound the proof; a deployment SHA must not be inferred from timestamp alone.

## Scope/refresh requirements

Where a calculation depends on a population, the original work scope, refresh cycle, and acquisition
plan are material. Phase 6 retains these for supported current operations. Legacy scope cannot be
derived from today's eligible population, and a retry cannot reselect a different cohort while
claiming the same reconstruction.

## Code/deployment identity boundary

Code identity is classified as `EXACT_DEPLOYMENT_IDENTITY`, `BOUNDED_CODE_IDENTITY`, or
`UNKNOWN_CODE_IDENTITY`. A repository commit is exact only when authoritative deployment evidence
binds it to the historical execution. Timestamp-to-commit inference is prohibited. Algorithm and
schema identities retained in decision evidence remain independently material even when deployment
identity is unknown.

## Immutable reconstruction outputs

Reconstruction never updates the original artifact. The model is original artifact `A`, immutable
manifest/attempt `R`, and separate typed `ReconstructionResult` `AR`. The result binds its manifest
fingerprint, mode, completeness, output fingerprint, and comparison. A future persistence design must be additive,
content-addressed, immutable, legacy-safe, and contain no historical backfill.

The manifest is frozen before calculation. Resolved sources, configuration, rules, scope, and cutoff
cannot be reread from changing current database state during execution.

## Comparison semantics

A result may be `MATCHED_ORIGINAL`, `DIFFERS_FROM_ORIGINAL`, `ORIGINAL_VALUE_UNAVAILABLE`,
`INSUFFICIENT_AUTHORITY`, or `CURRENT_RULES_RETROSPECTIVE_ONLY`. A mismatch is evidence for
investigation; it can indicate a historical defect, missing authority, an incorrect reconstruction,
or nondeterminism. It never authorizes overwriting the historical result.

The typed comparison contract prevents retrospective output from claiming an original-context
match. Differences must be explicit.

## Legacy/unavailable semantics

Legacy rows are not repaired with synthetic configuration, source revision, scope, provider, rule,
or predecessor IDs. `LEGACY_UNKNOWN`, `REVISION_IDENTITY_UNAVAILABLE`, and `PURGED` remain evidence
states. Permanent unavailability is a truthful residual boundary, not a runtime failure of current
certified paths.

## External governance boundary

Privileged SQL controls, DBA policy, provider archive guarantees, contractual retention, and external
access control are governance concerns. Repository code can preserve and classify imported evidence
but cannot assert enforcement outside its boundary. `XINT-010` therefore routes to T16D as
`EXTERNAL_GOVERNANCE_ONLY`, not to a reconstruction calculator.

## Future reconstructability

`INV-RECONSTRUCT-001`: new certified decisions retain enough semantic authority to reproduce their
original calculation context later, or explicitly record every external authority dimension that is
not retainable. This invariant is prospective; it does not relabel legacy rows.

`INV-RECONSTRUCT-002`: a result may claim `ORIGINAL_CONTEXT` only when every material historical
authority dimension is exact. `authorize_reconstruction` machine-enforces this predicate.

`INV-RECONSTRUCT-003`: an unavailable `ORIGINAL_CONTEXT` request may not silently execute
`CURRENT_RULES_RETROSPECTIVE` or `CURRENT`. `authorize_reconstruction` returns the requested mode and
an unavailable outcome without changing it.

`INV-RECONSTRUCT-004`: `ORIGINAL_CONTEXT` requires every material producer-consumer dependency edge
to satisfy its declared historical compatibility contract. Individually exact artifacts are not
an exact reconstruction when their temporal, configuration, scope, lineage, readiness, predecessor,
schema, rule, or provider authorities are incompatible. Manifest authorization machine-enforces
this predicate.

`INV-PROOF-001`: fingerprints and manifests declare and bind every semantic dependency dimension
needed to prove compatibility. Omitted dimensions may not be inferred from the same run, pipeline,
ticker, timestamp proximity, current pointer, or equal output value.

`INV-ARCHIVE-001`: unavailable, externally unavailable, or intentionally purged historical
authority is represented by a typed availability state. It may never be reconstructed from a
current provider response, current configuration, current security metadata, matching current
value, or latest database row.

`INV-RETENTION-001`: every internally controllable authority dimension required by a new certified
decision remains retained while dependent immutable decision evidence is certified reconstructable.
The horizon is structural rather than an invented duration. An external provider limitation must be
recorded as an explicit exception at creation time when the consumed evidence cannot legally or
technically be retained.

`INV-PURGE-001`: operational-only state may be purged without changing reconstruction availability.
Material authority pinned by certified evidence is purge-protected. Unpinned material authority may
be purged only with an explicit, machine-visible reconstruction downgrade; semantic evidence is
never silently mutated while its former fingerprint continues to claim exactness.

For the same decision made on supported current paths, future exact reconstruction is feasible where
the native evidence contains immutable source/config/rule/predecessor/scope identities. T16B/T16C
must implement resolvers that prove those references and must preserve explicit external gaps.

## Relationship to Phases 0–6

Phases 0–6 certify supported current behavior, mutation authority, point-in-time evidence,
configuration identity, scope, refresh, and handoff conservation. Phase 7 does not reinterpret those
certificates as proof for legacy decisions. It consumes their immutable identities prospectively and
keeps these negative dependencies absent: CERI to Ranking, CERI to Setup, CERI to Winner, Setup to
Winner, and Lifecycle to Winner.

T16A introduces no writer, schema, migration, production mutation, or bypass. Phase-5 mutation
authority and Phase-6 scope/refresh fences remain the only approved mutation boundaries.

T16C's composition validator is read-only. Exact deployment SHA is not required downstream when an
immutable producer evidence object fully freezes the consumed semantic result, its configuration,
readiness, source lineage and algorithm/schema identity. Deployment identity remains material only
where evidence does not independently freeze those semantics; missing legacy identity is then kept
unavailable rather than guessed.

## Archive and retention boundary

The repository distinguishes hot mutable projections, immutable decision/source evidence, archived
immutable authority, and purgeable operational data. A valid archive preserves semantic IDs,
content hashes, configuration and scope identities, source/revision identities, predecessor links,
schema identity, the reconstruction manifest fingerprint, and the result fingerprint. A readable
value export without those identities is not historical authority.

Configuration snapshots, calculation evidence, readiness decisions, historical rules, predecessor
links, scope/refresh/acquisition records, retained PriceBar revisions, and reconstruction manifests
are retention-critical once referenced by certified immutable evidence. Rebuildable caches, delivery
logs, and unreferenced operational projections are purgeable under their own operational policies.
Existing database `RESTRICT` relationships and domain purge guards enforce pins; the shared typed
retention validator supplies the same policy for archive and non-relational checks.

Provider payloads may be subject to contractual deletion. SwingLens retains the consumed content
and revision when permitted. If not permitted, it records the external exception and cannot promise
future exact reconstruction. This governance limitation does not authorize substitution from a
future provider response.
