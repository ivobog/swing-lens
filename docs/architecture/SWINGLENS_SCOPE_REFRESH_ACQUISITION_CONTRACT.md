# SwingLens Scope, Refresh, and Acquisition Contract

Status: Phase-6 foundation (T15A)
Schema versions: `work-scope-v1`, `refresh-cycle-v1`, `acquisition-plan-v1`

## Calculation Identity vs Work Scope Identity

Calculation identity defines the semantics of a calculation; work-scope identity defines the exact
population to which those semantics apply. Calculation identity answers how a result is calculated.
Scope identity answers which subjects or targets belong to the unit of work. Equal calculation
identities do not imply equal scopes.

Execution identity is separate from both. Worker PID, execution token, lease, heartbeat, queue time,
and retry count never enter a scope, refresh, or acquisition-plan hash.

## Scope policies

`FROZEN` means the exact normalized member set is selected once and retained. Retry, resume, and
continuation must use that set. Members cannot silently enter or leave because current database state
changed.

`DECLARED_DYNAMIC` is allowed only with an immutable policy ID, policy version, and evaluation rule.
Each evaluation still records its exact membership and fingerprint. A later evaluation is auditable;
"all currently eligible rows" is not itself historical membership evidence.

The shared foundation deliberately does not add further policy taxonomy. Domain adoption may justify
an append-only or window-specific policy later, but it must be explicit and versioned.

## Membership semantics

Exact members are represented by `(subject_type, subject_id)`. Unicode NFKC and surrounding
whitespace normalization apply; subject type is uppercase, and ticker/symbol identifiers are
uppercase. Nulls and control characters are rejected. Integers use their base-10 text form.
Duplicates after normalization are rejected.

Membership is sorted by normalized `(subject_type, subject_id)` before SHA-256 hashing. The hash
includes `work-scope-membership-v1`, so database row order cannot affect identity. Adding or removing
one member changes both membership and scope identity. Persisted rows carry deterministic ordinals and
the common membership fingerprint.

For a large population, a future manifest may replace row membership only if it supports exact
reconstruction and verification under an equally explicit schema. Query text or a live eligibility
predicate alone is never a frozen manifest.

## Refresh cycle identity

A refresh cycle binds refresh kind, work-scope identity, caller-declared observation-cycle key,
business observation cutoff, refresh reason, effective configuration, provider/source policy,
calculation identity where applicable, and prior refresh lineage.

Retry preserves refresh-cycle identity. A legitimate new refresh receives a new refresh-cycle
identity. The observation-cycle key is semantic business input, not a worker attempt number. A new
cycle links to its predecessor and cannot be suppressed merely because an older request key or
idempotency record exists.

Idempotency prevents duplication of the same logical mutation within the same scope, plan, and
refresh cycle. It does not mean that a subject can never be observed again.

## Retry vs refresh

A retry retains scope, refresh cycle, calculation identity, effective configuration, business time,
and acquisition plan. Only execution ownership may change.

A refresh is a new observation cycle. It may reuse the same calculation algorithm and even the same
subject set, but it has a distinct observation-cycle key and identity. If membership changes, the new
cycle also binds the corresponding new scope snapshot or declared dynamic evaluation.

## Continuation semantics

A frozen scope is never re-derived from current state during retry, resume, or continuation. A
continuation carries the same scope ID and computes remaining work as frozen membership minus recorded
completed membership. Rows that become eligible after selection do not enter the old cycle.

A continuation that processes zero members cannot enqueue an equivalent continuation while frozen
members remain. It stops with `STOP_ZERO_PROGRESS`. A declared dynamic policy may schedule a future
reevaluation only with an explicit reason; that reevaluation produces attributable history.

## Parent/child accounting

Parent work records semantic child targets as planned, created, completed, failed, or cancelled.
Parent completion requires every planned required child target to be complete. A handler returning is
not semantic completion. Any failed or cancelled required child makes that state explicit; planned or
created children remain outstanding.

This is the foundation required by `PIPE-001`; T15B must bind actual pipeline child jobs to it.

## Acquisition plan identity

An acquisition plan freezes subjects, provider/source class, request type, business cutoff, temporal
window, configuration identity, policy identity, required/optional observations, plan version, and a
caller-declared revision key. Transport attempts, retry counters, sockets, and worker identity are not
plan semantics.

The current in-memory IB `FetchPlan` is useful planning structure but is not yet an immutable shared
plan identity. CERI requests likewise contain useful provider/dataset/range fields but do not yet bind
them to an immutable plan record.

## Replanning lineage

Acquisition replanning creates new lineage; it is not a retry of the old plan. A revised plan points to
its predecessor and records a typed reason: provider limitation, missing source, newly discovered
source, explicit operator replan, or policy change. The prior plan is immutable. Retrying a provider
call keeps the same plan identity; changing symbols, documents, range, source priority, or behavioral
policy normally creates a new plan.

## Revision/truth identity boundary

Historical truth consumers must identify an exact source revision or explicitly state that revision
lineage is unavailable or legacy.

- `PriceBar` has row ID and `data_hash`; changed values create `PriceBarRevision` IDs, revision
  numbers, hashes, values, source, fetch references, and observed time. Some Winner birth-price
  consumers still do not persist the exact revision IDs used.
- `CeriSourceRecord` has row ID, provider record ID, content/normalized hashes, supersession, and
  published/observed/source/retrieved/ingested times. Normalized artifacts point to a source record,
  but provider, currency, publication, and revision proof is not uniform across consumers.
- Winner predictions have explicit snapshot revisions and feature hashes. Forward outcomes and
  evidence manifests pin outcome revisions and hashes, but a lineage hash is not always an enumerable
  list of exact source bar revisions.

The foundation declares this boundary; T15C implements Winner revision adoption and T15D completes
remaining provenance and algorithm-specific gaps. Introducing scope identity does not close
`WIN-006`.

## Provider provenance boundary

Value provenance is complete only when the consumer can prove the provider record/revision, currency
and scale where relevant, publication/known-at state, retrieval observation, and content identity.
Current evidence is strongest at `CeriSourceRecord` and `PriceBarRevision`, but downstream artifacts
do not uniformly retain every exact reference. `CORE-005`, `WIN-006`, and applicable parts of
`XINT-007` remain open or partial according to the T14E snapshot.

## Legacy semantics

Historical work without exact retained membership is `LEGACY_UNKNOWN`. Today's database state must
never be queried to fabricate a certified historical scope. No T15A migration backfills historical
records. Legacy operations may remain readable or explicitly non-certified, but unknown is not
compatibility evidence.

## Persistence and performance

The additive tables are:

- `acquisition_plan_records`
- `work_scope_records`
- `work_scope_members`
- `refresh_cycle_records`

All are immutable in the ORM and PostgreSQL. Foreign keys preserve predecessor, parent, plan, and
scope lineage. Content-addressed inserts are idempotent and collision-checked. Scope member insertion
is one batched statement; validation is set-based, so persistence uses a constant statement count for
1, 50, and 200 members rather than N+1 queries.

## Invariants

- `INV-SCOPE-001`: continuation targets are frozen or explicitly declared dynamic.
- `INV-SCOPE-002`: retry/resume preserves semantic scope identity.
- `INV-SCOPE-003`: frozen membership cannot silently expand or shrink.
- `INV-SCOPE-004`: parent completion accounts for semantic child work.
- `INV-REFRESH-001`: retry idempotency cannot suppress a later legitimate refresh.
- `INV-REFRESH-002`: a new refresh receives a distinct refresh-cycle identity.
- `INV-PLAN-001`: acquisition retries preserve plan identity.
- `INV-PLAN-002`: replanning creates explicit predecessor lineage.
- `INV-REVISION-001`: historical truth identifies exact revision or declares it unavailable/legacy.

T15A implements the shared types, validation, immutable persistence, and adversarial tests. These
invariants are only partially enforced until T15B/T15C/T15D adopt them at domain entrypoints.

## Relationship to Phases 0-5

Phase 1 calculation identity, Phase 2 immutable evidence, Phase 3 consumer eligibility, Phase 4
effective configuration, and Phase 5 mutation authority remain unchanged. Work scope composes those
identities; it does not replace them or weaken their proof boundaries.

Phase 7 remains separate because original-context reconstruction asks what historical configuration,
rules, sources, and context originally existed. Phase 6 asks which work and refresh cycle were
intended. A scope record cannot reconstruct historical facts that were never retained.
