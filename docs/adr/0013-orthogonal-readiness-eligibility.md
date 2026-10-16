# ADR-0013 - Orthogonal readiness, eligibility, and containment

Date: 2026-10-13

Status: Accepted

## Context

The Phase 1 forensic audit proved that SwingLens overloads producer readiness and
pipeline terminal state with unrelated meanings. A nonzero Fundamental missing-data
penalty can remove an otherwise calculated score from every contextual consumer;
ordinary investment-risk warnings can become Winner evidence exclusions after they
propagate through Combined and Ranking; and a pipeline whose processing completed
with analytical gaps can cause all independently certified CERI evidence to be
quarantined.

The product owner approved six policies for Phase 2:

1. preserve Fundamental score arithmetic while making consumer requirements explicit;
2. treat Winner as historical research evidence rather than a warning-free trade list;
3. use a versioned warning taxonomy;
4. base CERI containment on integrity hazards, not `PARTIAL` alone;
5. separate CERI certification/current visibility from confidence and actionability;
6. keep historical evidence and dispositions immutable.

## Decision

### Independent dimensions

Phase 2 models these facts independently:

- execution outcome;
- integrity/certification;
- analytical completeness;
- analytical confidence;
- typed warning categories;
- consumer-specific eligibility.

The versioned consumer decision outcomes are `INCLUDED`, `OMITTED_OPTIONAL`,
`REJECTED_REQUIRED`, and `UNDECIDED`. Every durable consequential decision binds the
producer/source identity, consumer edge, policy version, decision cutoff, dimension
results, original warning codes and categories, reason codes, and a deterministic
fingerprint.

Unknown warning codes and unreviewed consumer edges fail closed. Existing
`ProducerReadinessEnvelope`, `ConsumerEligibilityDecision`, and public API fields
remain readable during transition.

### Fundamental policy

Existing numeric scores and penalties are immutable inputs to eligibility. A positive
penalty is not itself an integrity failure. Consumer v2 contracts inspect the frozen
coverage and parse diagnostics. No numerical threshold is activated without a
documented scoring/component justification and deterministic sensitivity evidence.
Until then, proposed admissions are shadow-only and legacy production admission
remains fail closed.

### Warning governance

The catalog version is part of every classification. Original codes are never
rewritten. `INVESTMENT_RISK` and `STRATEGY_RESTRICTION` remain analytical features;
they do not degrade Combined/Ranking integrity. `INTEGRITY`, `TEMPORAL`, unknown, and
ambiguous codes remain blocking. Catalog changes require a policy-version change,
tests, and an owner-reviewed entry in this ADR or a superseding ADR.

### Winner acquisition

Technical, Ranking, and Combined remain mandatory exact sources. Every attempted
ticker receives an append-only acquisition decision, including exclusions. The
decision ledger is not a prediction and cannot create outcomes, estimates, cohort
membership, or training eligibility. Shadow admission never authorizes publication
or training.

### Pipeline and CERI

The legacy `PipelineStatus` remains the compatibility projection. A separate versioned
summary records execution, coverage, and integrity. `FAILED`, `CANCELLED`, and
`BLOCKED` retain existing containment semantics. `PARTIAL` is not an integrity hazard
without an explicit integrity reason. Existing `EXCLUDED` dispositions always remain
effective; the new predicate is prospective and never reactivates history.

CERI integrity eligibility and actionability are distinct. Low-confidence evidence can
be visible for audit/research but cannot drive alerts under the initial actionability
policy. Insufficient, unrated, stale, uncertified, or missing-provenance evidence remains
non-actionable.

## Safety invariants retained

- exact run/upload ownership and cross-run isolation;
- immutable calculation identity, payload, source graph, config, and version;
- PIT/cutoff/calendar enforcement and no current-data fallback;
- atomic certification and source sealing;
- cancellation, lease, checkpoint, and parent-terminal fencing;
- append-only quarantine and explicit review authority;
- current-projection cleanup without historical deletion;
- idempotency and uniqueness under retry/concurrency;
- no historical prediction fabrication or automatic training admission.

## Compatibility, versioning, and migration

- New ledgers are append-only and prospective. No Run 22–33 backfill is permitted.
- Migration `0092_phase2_eligibility_policy` follows the already-applied
  `0091_readiness_constraints` worktree head.
- Legacy status/readiness fields remain available and are not silently redefined.
- New policy activation is explicit by policy version. Shadow output is always marked
  proposed and non-authoritative.
- Rollback disables the new policy version and returns to legacy evaluation. Schema
  downgrade is permitted only after proving the new append-only rows are no longer
  required; normal rollback leaves the ledger intact and disables writers.

## Consequences

Valid but imperfect information can be retained without weakening provenance. The
system gains explicit denominators and auditable exclusions, at the cost of additional
schema, policy-version, UI, and regression-test complexity. Fundamental production
admission remains blocked until a numerical/component threshold is certified.

## Versioning and tests

- Policy family: `orthogonal-consumer-eligibility-v1`.
- Warning catalog: `warning-taxonomy-v2`.
- Pipeline summary: `pipeline-outcome-summary-v1`.
- CERI actionability: `ceri-actionability-v1`.
- Winner acquisition ledger: `winner-acquisition-v1`.
- Required tests: deterministic serialization, unknown-warning fail-closed, exact
  identity/cutoff binding, append-only/idempotent ledgers, Run 32/33 shadow fixtures,
  pipeline compatibility, CERI failed-owner/race protection, and API/UI population
  reconciliation.

## Links

- Phase 1 audit: external Codex artifact recorded in the Phase 2 implementation ledger.
- Traceability: `docs/remediation/readiness-phase2/TRACEABILITY_MATRIX.md`.
- Implementation ledger: `docs/remediation/readiness-phase2/IMPLEMENTATION_LEDGER.md`.
