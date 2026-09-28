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
- `INV-CERI-CERTIFICATION-SCOPE-AUTHORITY`: a feature-certification batch derives company
  membership only from its immutable admitted certification ticker scope; a normal full-pipeline
  batch derives membership only from its upload/run rows. The two authorities never fall back to or
  broaden one another.
- `INV-CERI-CERTIFICATION-PROVIDER-IDENTITY-ADMISSION`: feature certification validates every
  requested canonical company and provider identity before persisting the run, pipeline, context, or
  job graph. Missing or conflicting identity fails admission with zero durable workflow work; the
  feature-stage identity invariant remains an independent drift guard.
- `INV-CERI-CERTIFICATION-CANONICAL-SESSION`: an operational feature-certification launcher
  discovers its session only from the canonical lifecycle state, then verifies the live
  certification mode, active process identities, exact deployed Git SHA, repository schema head,
  readiness isolation, and the matching registered supervisor/worker generation before importing
  or invoking durable admission. An explicit runtime-instance override is accepted only when it
  matches that canonical session; ambiguity or drift fails before durable workflow creation.

T15A implements the shared types, validation, immutable persistence, and adversarial tests. These
invariants are only partially enforced until T15B/T15C/T15D adopt them at domain entrypoints.

## T15B certified Pipeline and CERI adoption

### Pipeline root scopes and subscopes

Full Pipeline admission freezes the normalized ticker membership from the selected upload after the
market calculation context is fixed and before the durable root job is enqueued. The pipeline row and
root job carry the same `scope_id`, `refresh_cycle_id`, and `acquisition_plan_id`. A stage that has a
legitimate subset persists a separate frozen scope with `parent_scope_id` pointing to the root; it
does not rewrite root membership. The market-data stage persists the exact admitted `FetchPlan` as
its child authority.

### Retry, resume, reclaim, and continuation

Retry, resume, SEC-repair continuation, prewarm execution, and durable IB fetch execution require the
retained authority. They may replace a lease, worker, execution token, or attempt counter, but cannot
replace scope, refresh, plan, cutoff, or policy. Durable fetch jobs restore the serialized admitted
plan rather than resolving current coverage again. An interrupted pre-binding pipeline may
deterministically admit missing child authority only from an already retained exact immutable plan
and retained root scope. Every other legacy operation without complete authority fails closed; no
compatibility path resolves current coverage.

Frozen continuation is set subtraction over persisted members:

```text
remaining = persisted scope members - completed members
```

A newly eligible database row is therefore excluded from an old continuation. If remaining work is
non-empty and an attempt processes zero members, the operation becomes `BLOCKED` with the reason and
remaining membership persisted; it does not enqueue an equivalent unbounded continuation.

### Parent-child accounting

Every semantic child explicitly declares whether it is required for parent completion. Required
Pipeline, SEC-repair, and CERI-derived children inherit canonical authority. Counts expose planned,
completed, failed, cancelled, and remaining work. The parent cannot become complete while a required
child is non-terminal. Optional work remains explicitly optional rather than being inferred from its
job type.

### CERI refresh cycles and idempotency

CERI admission freezes the ticker/source population and acquisition semantics before enqueue. The
cycle key is an explicit scheduler/operator cycle when supplied, otherwise a deterministic
provider/dataset/ticker/business-session key. Retries of one cycle converge on the same refresh ID.
A later cycle creates another refresh ID even when the provider record key is stable.

The ingestion request key is namespaced by `refresh_cycle_id`. Within one refresh, the same source
and revision remain idempotent. Across refreshes, the stable provider key is allowed to produce a new
observation or a superseding source revision. No timestamp randomness is used to bypass deduplication.
CERI processing children inherit the same authority unless an explicitly admitted child scope is
needed.

Feature execution resolves its membership mode from the retained `work_scope_records.scope_kind`.
`ceri-feature-certification` requires exactly two retained ticker members, exact batch containment,
unambiguous canonical CERI companies, and matching provider identities; it does not consult
`raw_company_rows`. `full-pipeline-run` keeps the upload/run-row intersection. Any other scope kind
for a pipeline-owned feature batch fails closed before source-manifest construction.

Certification admission applies the same canonical-company and provider-identity prerequisites
before it calls the durable scope-admission boundary. Provider identities missing from legacy
company rows may be reconciled only from multiple normalized source records already linked to that
company. Every qualifying record must agree on provider ID and ticker, the provider record key must
agree with that ID, and no other company or provider alias may own it. Reconciliation writes the
canonical provider map plus a high-confidence `provider_company_id` alias whose source contains the
exact source IDs, ingestion IDs, datasets, time range, and evidence fingerprint. Existing conflicting
identities are never overwritten. A transaction advisory lock on the candidate provider identity
serializes cross-company ownership checks. Admission validation does not replace the feature-stage
guard.

### Acquisition-plan adoption and replanning

Pipeline, CERI, SEC repair, IB fetch, and prewarm operations assigned to T15B persist the canonical
T15A acquisition plan. It records exact subjects, provider/source class, request type, business
cutoff, range, policy, and requirements. A transport retry retains P1. A semantic change to subjects,
provider policy, date range, or requirements creates immutable P2 with `previous_plan_id = P1` and a
typed revision reason. Provider results report received/missing work but never redefine the plan.

### Checkpoint and scope relationships

Checkpoint and progress ownership is scoped by the durable owner's `scope_id` and, for refreshable
work, `refresh_cycle_id`. A checkpoint for S1/R1 cannot advance S2/R2. Shared immutable evidence may
be reused according to its own identity; mutable work progress may not. PostgreSQL foreign keys and
single-assignment triggers reject authority references that are missing or rebound after admission.

### Performance constraints

Authority persistence remains set-based: member insertion and validation use bounded statements for
1, 50, and 200 members. Pipeline admission, remainder calculation, CERI refresh admission, and plan
persistence do not issue per-member authority lookups. T14D query bounds remain the regression gates:
capture 29/29, pipeline 70/70, and material 29/29 for populations 1/50.

### T15B closure boundary

T15B enforces `INV-SCOPE-001` and `INV-REFRESH-001` for Pipeline/CERI, closes the assigned Pipeline,
IB/prewarm, CERI refresh, and acquisition-plan paths, and leaves repository-wide status partial.
Winner adoption and exact Winner source-revision lineage remain T15C work; other provenance and
algorithm-specific gaps remain T15D work.

## T15C certified Winner adoption

### Maturation scope

Primary H5/NEXT_OPEN admission performs one set-based selection of current, nonterminal,
temporally eligible predictions whose due session is at or before the retained completed market
session. The resulting `WINNER_PREDICTION` members are persisted before the root job is enqueued.
Every batch receives those retained prediction IDs; the live due query may filter terminal or
retry-deferred members, but it cannot add another prediction.

### Maturation refresh

A scheduler session key identifies one observation cycle. Concurrent admission is serialized and
converges on one scope/refresh/job. Retry, reclaim and required continuation children retain the
same scope, refresh, acquisition plan, operation cutoff, effective configuration anchor and due
session. A call while that workflow is active coalesces into the admitted operation; after it is
terminal, a later scheduler/operator cycle can admit a distinct refresh and newly due membership.

### Outcome truth revisions

Outcome proofs retain the complete `PriceBar` row, content hash, symbol, session, source and observed
timestamps. When a matching `PriceBarRevision` exists at the operation cutoff, the proof pins its
primary key and complete immutable revision body with `revision_identity_status = EXACT`. Birth bars
that predate revision-row creation retain `price_bar_revision_id = null` and the explicit status
`REVISION_IDENTITY_UNAVAILABLE`; the system never substitutes a later revision. A correction creates
a linked Winner outcome revision and leaves the predecessor proof unchanged. Consequently WIN-006
remains partial for historical birth bars.

### Cohort scope

Cohort admission advances the material-evidence watermark once, loads the exact watermark/cutoff-
bounded evidence universe, and persists typed prediction, forward-outcome and target/stop-outcome
members. Eligibility policy, outcome definition, watermark and training cutoff are retained in the
scope definition. Inline current-rules estimates remain explicitly distinct and are not forced into
this persisted generation scope.

### Generation scope

The generation is captured during admission and receives the same scope/refresh/plan in its birth
transaction. Slices resume that exact generation, watermark, configuration and manifest. The root
evidence manifest is checked against the admitted prediction members, so later matured evidence or
new cohort groups cannot enter G1; a later refresh creates G2.

### Publication target binding

Publication continues to require the reviewed transition manifest, exact candidate and predecessor
generation IDs, generation key, root manifest and candidate manifest hashes. A T15C generation's
semantic authority is copied to its publication request. Request-key retry validates the same
authority and exact manifest; it cannot resolve or switch to a newer candidate generation.

### Winner retry/resume

`WinnerProcessingRun` checkpoints carry immutable `scope_id`, `refresh_cycle_id` and
`acquisition_plan_id`. A checkpoint also records scope/refresh in its body. PostgreSQL foreign keys
and the shared single-assignment trigger reject rebinding. Zero progress with retained work records
the explicit reason and remaining members instead of producing an equivalent unbounded child chain.

### Legacy Winner scope

Rows created before T15C remain nullable and therefore `LEGACY_UNKNOWN`; migration 0083 performs no
semantic backfill. New capture, maturation and cohort jobs require retained authority at execution.
Historical backfill can admit only the explicit run IDs supplied by its request before iteration; it
does not reconstruct a historical prediction population from today's Winner tables.

## Relationship to Phases 0-5

Phase 1 calculation identity, Phase 2 immutable evidence, Phase 3 consumer eligibility, Phase 4
effective configuration, and Phase 5 mutation authority remain unchanged. Work scope composes those
identities; it does not replace them or weaken their proof boundaries.

Phase 7 remains separate because original-context reconstruction asks what historical configuration,
rules, sources, and context originally existed. Phase 6 asks which work and refresh cycle were
intended. A scope record cannot reconstruct historical facts that were never retained.

## T15D certified provenance and algorithm contract

### Provider provenance

Every new CERI estimate calculation resolves all eligible observations for one canonical business
observation key with the frozen provider-conflict policy. Evidence retains the complete candidate
source-record set, selected source record, selected provider, configuration identity and selection
reason. Provider priority is therefore executable semantics, not descriptive metadata. A later
policy applies only to a new calculation/refresh and never rewrites prior evidence. Operational
request IDs, credentials and connection details are excluded from semantic identity.

### Revision identity

Truth-bearing inputs use one of four explicit states: `EXACT_REVISION`,
`CONTENT_ADDRESSED_EXACT`, `REVISION_IDENTITY_UNAVAILABLE`, or `LEGACY_UNKNOWN`. Exact source-row or
revision IDs are retained when they exist. Content-addressed PriceBars retain their row ID and
content hash. If a historical revision row never existed, the consumer records unavailability and
must not substitute a current revision. Legacy rows without enough original facts remain unknown;
current database state is never used as a semantic backfill.

### Temporal known-at semantics

`published_at`, `observed_at`, `retrieved_at`, `ingested_at`, business effective period and
calculation cutoff are distinct meanings. A CERI fact is eligible only under the source-specific
point-in-time rules at the explicit cutoff. Later corrections can enter a later calculation but
cannot enter or mutate an earlier calculation. Core mixed-benchmark snapshots use the older common
source session as their effective date and lower confidence when required benchmarks disagree.

### Currency provenance

New structured CERI estimates retain canonical currency and scale on the selected source fact.
Legacy uploaded Fundamental values do not contain reliable native currency, normalized currency,
FX rate, source or effective time; immutable evidence records those dimensions as unavailable. No
conversion metadata is synthesized. Any future conversion must retain native and normalized
currencies, rate, provider, effective time and canonical rounding.

### Algorithm correctness and immutable evidence

Immutable evidence proves what a calculation used; it does not prove that the algorithm was right.
T15D therefore independently certifies provider conflict resolution, adjusted-price/TRADES-volume
basis, complete exchange-session history, mixed-benchmark session semantics and Ranking's explicit
non-dependency on command-center Regime and same-run Sector. A corrected defective fixture may
change; unaffected controls must remain stable. New results receive new evidence lineage while old
evidence remains unchanged.

### Legacy and unavailable revisions

`REVISION_IDENTITY_UNAVAILABLE` is a supported truthful state, not an invitation to infer a
revision. `LEGACY_UNKNOWN` means the original provider, rule, revision, timestamp or currency cannot
be recovered. CERI pre-certification rule/purge history, legacy Fundamental uploads and Winner birth
bars without revision rows keep these explicit limitations. They are not silently promoted to exact
provenance.

### Phase 7 boundary

Phase 6 closes population and refresh identity for every inventoried refresh-capable family and
defines truthful provenance behavior for new calculations. Phase 7 remains responsible for
`SETUP-006`, `SETUP-007` and `SETUP-010` original-context reconstruction, plus external governance
of source contracts. It may use retained Phase-6 identities, but it may not reconstruct absent
historical rules or source facts from today's state.
