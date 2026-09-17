# SwingLens Mutation Entry-Point Contract

## 1. Purpose

T14A foundation boundary is the declaration contract, final persistence/writer
ownership and exhaustive initiator discovery/disposition. Writer/discovery gates
PASS; final regression and commit evidence are recorded in the report. Caller
contract enforcement is later T14B/C/D adoption. Raw paths and inferred edges are
forensic evidence, not independent semantic review obligations.

Phase-5 T14A establishes the declaration contract and repository inventory for
`INV-ENTRY-001`: every supported mutating entry point must enforce the minimum
provenance and semantic authority required by the artifact it creates or changes.
This foundation does not certify repository-wide adoption. No existing writer is
wrapped, and no calculation, configuration, scoring or readiness formula changes.

## 2. Phase-5 problem statement

Phases 0â€“4 certify temporal/execution safety, Calculation Identity, immutable
evidence, consumer permission and effective configuration on their declared
paths. Independent routes, public services, CLI commands and repairs can still
select a different root context or reach a lower writer with less authority.
Execution order is not a dependency graph. Authentication is not lineage proof.

## 3. Entry point vs writer

An entry point initiates an operation: HTTP handler, public service method, CLI,
scheduler, startup action or durable delivery. The semantic writer owns creation
or material change of a business artifact. Generic `repository.add` is a
persistence mechanism, and model-field copying is a helper; neither becomes an
independent user operation simply because it changes an ORM object. The source
census retains these mechanisms so they cannot disappear from reverse review.

## 4. Business vs operational mutation

Business authority covers raw/source facts, price revisions, financial evidence,
projections, setup/lifecycle/alert decisions, Winner predictions/outcomes/models,
cohorts/generations/publication, frozen contexts and configuration bindings.
Heartbeats, worker/supervisor registrations, job progress, telemetry, processor
leases and audit records have operational requirements. Mixed transactions retain
both classifications. Operational token ownership cannot certify financial truth.

## 5. Immutable evidence vs current projection

The shared append-only ledger and dedicated decision ledgers retain original
identity, payload, source pins, configuration and readiness. Mutable serving rows
and projection pointers can advance without rewriting those ledgers. Projection
scope and monotonic order must be validated independently of evidence creation.

## 6. Semantic mutation modes

`MutationSemanticMode` declares CANONICAL_CALCULATION,
CURRENT_PROJECTION_ADVANCE, CURRENT_STATE_REPAIR, CURRENT_RULES_RETROSPECTIVE,
BOOTSTRAP, MAINTENANCE, PUBLICATION, OUTCOME_MATURATION, LEGACY_UNCERTIFIED and
OPERATIONAL. These correspond to native calculation, pointer selection,
maintenance/repair, retained-input replay, default-rule seeding, publication and
outcome operations. `ORIGINAL_CONTEXT` is reserved and rejected by every policy.
No full original-context reconstruction capability is claimed. Native LIVE,
PERSISTED_REPLAY and DRY_RUN_REPLAY labels need mapping to these semantics during
adoption; a persisted replay does not thereby become original historical truth.

## 7. DomainMutationContext

[domain_mutation.py](../../app/services/domain_mutation.py) defines a frozen
context with typed domain/mode, entry-point identity, writer identity/version,
reason, optional Calculation Identity, MarketCalculationCutoff, effective
configuration identity, exact table/ID/fingerprint references, frozen
readiness/eligibility bindings, run/pipeline scope and durable execution ownership.
Tuples and frozen existing Phase-1/3/0 types avoid mutable authority envelopes.
Large configuration/source payloads remain in the established stores.

## 8. Mutation authority requirements

`MUTATION_AUTHORITY_POLICIES` is a read-only map of domain-specific declarations.
Financial producers declare relevant identity, temporal and configuration fields;
consumers declare required/optional source roles and permission records. Raw
acquisition, alert acknowledgments, outcome maturation and operational writes
have distinct policies. CERI_REVIEW is human source review in MAINTENANCE mode,
requiring exact review-target/human-review pins without score dependencies.
CERI_ALERT, WINNER_MODEL and WINNER_DIAGNOSTICS have native declaration policies.
Model retirement is maintenance; promotion requires its exact gate/configuration
references. Diagnostic authority uses artifact-specific subject/input/contract pins.
Outcome maturation references the frozen prediction
contract and outcome price manifest, rather than requiring new Ranking/Setup/CERI.
Undeclared dependencies, unsupported modes and missing authority fail closed.

## 9. Calculation Identity authority

Reuse the existing validator. Policy-required dimensions must be KNOWN; UNKNOWN
and LEGACY_UNKNOWN cannot certify them. Required source lineage and algorithm
version must be known. An execution token never becomes a semantic identity.
Structural identity validity alone does not prove selection of eligible sources.
The writer must use the existing domain compatibility profile for each source.

## 10. Temporal authority

Temporal declarations reference the existing frozen cutoff/session/calendar.
For identity-bound calculations the declared session, cutoff, calendar version,
exchange timezone and persisted context ID must agree. Historical/as-of input
selection still uses the established PIT readers and revision knowledge-time
rules. Outcome-session authority is separate from the prediction's original
session. No omitted temporal field is filled from the clock or latest run.

## 11. Evidence authority

References retain role, table, exact address and expected fingerprint. Different
tables can have the same numeric ID. Before writing, resolve those exact
references, validate fingerprints/owners/subjects/roles with Phase-2 helpers and
verify domain compatibility. Constituent manifests are source authorities for
raw prices/providers/IBMI, not invented CoreCalculationEvidence IDs.

## 12. Readiness authority

Reference the stored `ProducerReadinessEnvelope` and
`ConsumerEligibilityDecision`; do not recalculate historical readiness. The
decision must name the consumer and match the exact producer evidence ID and
readiness fingerprint. Included numeric input requires ELIGIBLE. Denied inputs
may be retained as excluded diagnostics in blocked/insufficient artifacts.
Winner's Technical, Combined and Ranking consumption is mandatory; Fundamental,
Regime and Sector retain their native nullable semantics. Surviving numerics do
not substitute for permission. Domain writers verify policy versions and frozen
envelopes against the persisted source, using the existing Phase-3 machinery.

## 13. Configuration authority

The required family must carry complete effective configuration with a valid
SHA-256 identity/resolution contract. Identity-bound mutations must match the
configuration dimension of Calculation Identity. Actual durable mutation
adoption must load and validate the retained Phase-4 anchor and owner binding in
the same operation. An unverified digest declaration is not a retained snapshot.
Current-rule operations freeze a new operation's rules; they cannot overwrite
the original operation's configuration or claim original-context semantics.

## 14. Execution ownership

Durable contexts require an explicit `DomainWriteOwnership` job ID/token.
`fence_mutation_transaction` uses the actual Phase-0
`assert_current_execution_ownership` row lock in a real SQLAlchemy Session.
Stale/nonrunning/mismatched attempts fail. No independent fence implementation or
new global ORM interception is introduced. Existing worker commit fencing remains.

## 15. Pipeline/run authority

Run-required policies validate the declared positive run and identity owner.
Pipeline-bound work additionally requires matching pipeline and known context
ID/fingerprint. Writers must resolve the persisted owning PipelineRun and
MarketCalculationContext and validate handoff/resume references. Standalone
contexts may be parentless where the domain supports them; they must not borrow
an implicit latest pipeline and pretend that it was the initiating authority.

## 16. Canonical writer ownership

Safety requirements belong at the canonical semantic writer boundary, not only
at individual routes or callers.

A trusted/admin/maintenance caller does not waive calculation lineage
requirements for certified business evidence.

Core producers converge through their native services and `persist_core_evidence`.
Regime/Sector use their native repositories; CERI and IBMI use their decision
evidence adapters and native stores. Setup repository/evidence, lifecycle
episode/evaluation/transition, alert decision, and Winner capture/outcome/cohort/
generation/publication services retain distinct semantic ownership. The T14A
report lists exact owners and parallel variants. Generic `add` does not own final
business validation; the immediately surrounding semantic writer does.

## 17. Transactional validation boundary

Validate declarations, resolve exact retained inputs/configuration, validate
domain identity/eligibility/projection order, acquire/retain the execution row
lock, write and commit in one effective transaction. The foundation function
does not commit. Calling it and then committing before the actual write loses
the lock; revalidate after every intermediate commit. Application semantic
validation supplements foreign keys, immutability triggers and unique keys.
`MutationValidationResult.valid` means declaration consistency; it is explicitly
not a certification that references exist or that financial math is correct.

## 18. Current projection mutation

Declare CURRENT_PROJECTION_ADVANCE with exact target evidence and projection
scope. Reuse Phase-2 compatible scope, source pins and monotonic selection rules.
Do not replace E1's evidence when advancing to E2, point a current row to
incompatible evidence, or allow an older repair to overwrite a newer certified
selection. Setup canonical selection and Winner activation/publication have
their own ordering predicates and locks; atomic publication alone is insufficient.

## 19. Repair/current-rule semantics

Inventory includes repair, rebuild, recalculate, refresh, backfill, bootstrap,
recovery/resume, rescore and replay. These labels do not establish authority.
Retained-input/new-rules replay is CURRENT_RULES_RETROSPECTIVE; maintenance that
changes serving state is CURRENT_STATE_REPAIR or MAINTENANCE according to its
domain. Retry/resume of an original durable root retains original identity and
configuration with a new token. It is not automatically a current-rule repair.

## 20. Legacy semantics

LEGACY_UNCERTIFIED is explicit and rejected by certified-domain policies. Existing
legacy code remains reachable where the inventory shows it. In particular the
non-durable Full Pipeline branch computes only a subset of canonical stages;
missing context/evidence is not retroactively certified or backfilled. T14D must
wrap, isolate or retire such paths under an explicit operation contract.

## 21. Direct SQL boundary

The census includes ORM statements delivered through execute/scalar/scalars,
PostgreSQL insert aliases, literal SQL, field changes, generic repositories,
SQLAlchemy events, migration triggers and external restore commands. Runtime
application SQL has the same semantic boundary requirement. Rollback-only
verification attacks are identified separately. Arbitrary privileged external SQL
and pg_restore cannot be made safe by an application DTO; governance and evidence
preservation remain explicit boundaries for later work.

## 22. Negative dependency rules

CERI â†’ Ranking/Setup/Winner, Setup/Lifecycle â†’ Winner, IBMI â†’ Winner directly,
and Sector â†’ same-run Ranking remain absent. Ranking's IBMI liquidity is its own
optional contributor. Winner consumes independent Raw/Technical/Combined/Ranking
and eligible optional Fundamental/Regime/Sector branches. Setup's ranking values
are metadata; Combined earnings risk is a separate behavioral input. Never turn
the pipeline's whole context bundle into a universal required source checklist.

## 23. Entry-point classification

Distinguish canonical pipeline/public APIs, durable jobs, scheduler enqueue-only,
current-rule repair/retrospective, admin, CLI, startup/bootstrap/maintenance,
legacy, operational-only and potential/confirmed bypass. Classification records
business-semantic authority rather than application security permission. Inventory
graph proof labels distinguish explicit imported/local calls, declared defaults
and source-reviewed dispatches. Globally unique names do not establish edges;
the raw census remains separate. Review source hashes become stale after source
changes. Missing semantic path/role/edge/declaration review blocks certification.

## 24. Writer classification

Source census distinguishes shared semantic writers, current projections,
current-rule/legacy writers, operational sites, direct SQL, test/migration-only
sites and transient builders. Writer sites are not counted as independent
financial algorithms. A table reverse index records known writers or explicit
read-only legacy status. No regex match by itself certifies safety or reachability.

## 25. Phase-5 adoption plan

T14B: core and contextual producer/evidence/projection boundaries, exact input
and configuration resolution, source manifests and optional contributor permission.
T14C: Setup/Lifecycle/Alerts and Winner capture/estimation/outcomes/cohorts/
generations/publication boundaries, including projection ordering and independent
Winner acquisition. T14D: route/admin/CLI/repair/maintenance/bootstrap/scheduler/
startup/non-durable variants, honest operation-mode mapping and continuation
delivery. T14E: integration certification after adoption. Acquisition/refresh
scope, original-context reconstruction and privileged SQL governance remain
separate work; no production rewrite or legacy backfill occurs in T14A.


The final continuation certifies finite semantic units: domain policies, concrete
persistence sinks, writer families, initiator families and artifact ownership.
Raw path permutations and inferred edge counts are retained discovery evidence,
not independent zero-count gates. A provisional family cannot become certified
merely by changing its label to potential bypass. CERI_ALERT, WINNER_MODEL and
WINNER_DIAGNOSTICS use exact native authority references; model retirement does
not require promotion training gates. Writer ownership and initiator discovery/
disposition are closed; full caller authority adoption remains T14B/C/D.

The final writer normalization resolves all seven unfinished groups into 99 final
writer/supporting-state families owning 187 business persistence boundaries.
Setup metadata/events/rules/generic add, Regime derived deletion and IB research
association have separate owner IDs. Fundamental's bound and unbound predicates
are explicit; the latter remains confirmed bypass. Combined serving-link detach
has separate maintenance authority from financial output. Winner inline estimate
and cohort cache authority remains separate from generation/publication.

All 291 business initiator candidates are DISCOVERED and assigned exclusive
T14B/T14C/T14D or supported-distinct disposition. Discovery is not a claim of
ALREADY_CANONICAL caller enforcement. Machine-readable primary IDs and native
writer dependencies are in T14A_phase5_handoff.json. The three existing
configuration-delivery initiators retain supported distinct Phase-4 semantics.

Final foundation certification: PASS. All writer ownership, persistence-site,
artifact, initiator mapping/disposition and preserved-edge endpoint gates pass.
Contract, deterministic inventory, representative PostgreSQL, Phase-0–4, broader
repository and static validation evidence is recorded in the T14A report and
validation summary. Later caller enforcement remains T14B/C/D.


## 26. T14B certified core and contextual writer adoption

T14B verdict: PASS. All 32 exact assigned writer families and 36 assigned initiator IDs are reconciled with zero defects/unreconciled IDs. This dated adoption supplements the T14A foundation history above; historical classifications remain historical facts. Current inventory has 99 writers/291 initiators/188 business persistence sites, zero confirmed bypass and four remaining potential Winner bypass families assigned T14C.

Fundamental, Technical, Combined, Ranking, Regime, Sector, CERI and IBMI enforce the existing typed mutation contract at their native writer and immutable evidence boundaries. Actual retained calculation/time/configuration, named source addresses and native frozen eligibility are validated under the effective transaction. Actual durable job status/token is independently locked and fenced; retained C1 authority survives rotating T1/T2 attempt ownership. Missing authority is rejected without current/latest/clock repair. Source acquisition declares its native request/observation role; cache uses its distinct LocalArtifactKey and PIT/version/checksum/shadow proof. Neither invents financial identity or global required contributors.

Upload initial Fundamental values are explicitly LEGACY_SERVING_ONLY/LEGACY_UNCERTIFIED and cannot supply certified evidence/readiness, including through a forged pointer. Unbound recalculation fails before financial replacement. Private persistence mechanisms require the named semantic owner scope. Rejection rolls back the effective caller transaction, including earlier autoflush, preventing caught failures from committing partial financial state. Current projection validates exact certified target/scope/configuration; PostgreSQL advisory transaction locks protect absent-row creation and row locks protect existing pointers. Older replay cannot regress a core serving value behind its newer pointer. Regime maintenance deletes only locked checked derived IDs and refuses retained certified history.

Native frozen readers retain historical serialization. Technical cache-fallback post-seal diagnostic overlays remain separate from immutable financial values/readiness; reduced native projections validate supplied consumed values and the exact locked physical evidence address. No formulas or thresholds change. CERI human review/overrides/alerts retain separate T14C/T14D ownership. Flex source import does not adopt authoritative WF_IB_TRADE_EPISODES, which remains T14D. The seven forbidden required dependencies remain unchanged.

Certification evidence is in T14B_core_contextual_writer_adoption.md, T14B_core_contextual_writer_certification.json, T14B_validation_summary.json and T14B_business_parity.json under docs/remediation/calculation-lineage. Final native PostgreSQL/Phase 0–4 plus attacks: 324 passed; broader: 3,448 passed, seven optional live-IB skips, 321 deselected. Source adapter rejection/structural proof is distinct from native financial positive commits and calculator parity. No migration, production rewrite, legacy backfill or live provider/trading execution occurs.

PIPE-008 is closed for core/contextual writers and partial overall; PIPE-003/PIPE-007 remain caller-delivery work for T14D. SETUP-005 and XINT-006 remain partial. INV-ENTRY-001 is enforced for core/contextual writers and partial repository-wide. T14C still adopts Setup/Lifecycle/Alert and Winner writers; T14D still unifies standalone/repair/admin/CLI/legacy/bootstrap/scheduler callers and trade episodes; T14E integrates Phase-5 after those adoptions. Background refresh/scope, original-context reconstruction and privileged external SQL governance remain separate.
