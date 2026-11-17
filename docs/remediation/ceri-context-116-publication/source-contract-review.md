# PR 82 source-contract review

The release-plan job stopped because registered test-file fingerprints still
referred to GitHub main before the CERI and prerequisite changes. This review
updates static intent under `docs/testing/source_contract_policy.md`. It does
not supply a current A04 certificate, measured fault observation, live-provider
proof or authenticated human approval. Historical review identities and sealed
certification artifacts are retained.

Four files affect fourteen existing registrations across CERI temporal lineage,
regime/sector selection and native pipeline certification:

- The registered CERI bulk-rebuild test has the same semantic AST and all twelve
  assertions. It still commits four nonempty artifact domains, compares retry
  lineage and hashes, and enforces independent read/write bounds. File-level
  changes add true pipeline/context/workflow fixtures and independent capture,
  ownership, seal and continuation controls; they do not remove its oracle.
- The deployment-identity positive case changes three operational schema-head
  strings from 0094 to current migration 0096. Its five assertions still require
  current database/repository agreement and a separate evidence-schema version.
  The stale-database refusal case retains its semantic AST and four assertions.
  No financial expectation or immutable historical schema identity is changed.
- The sector residency case retains its semantic AST and five assertions. Its
  checkpoint fault test retains twelve assertions and all original boundaries,
  then adds canonical-hash cancellation/failure after the writer stages data.
  The new branch enables real hash checkpoints in the small native fixture.
  Exact row-lock contention, invisible staged rows, no lazy reads in CPU phases,
  one bulk read per producer, full rollback and post-caller-commit conservation
  remain required for the original and added boundaries.
- Both registered pipeline-executor tests retain their semantic ASTs and all
  five/four assertions. File-level changes add offline-to-online initial-plan
  retry controls; original completed fundamentals and preflight refusal checks
  remain unchanged. These remain unit-double controls, not native certificates.

The existing CERI contract also explicitly associates the shared ownership and
population validators and six meaningful new native/fast controls. Actual
PostgreSQL constraints cover independent contexts, NULL/replay behavior,
concurrent insertion and transactional downgrade refusal. Dedicated change and
pipeline tests cover real sealed ownership, exact handoff/dependency receipts
and strict refusals. Physical/temporal mismatch tests retain unit-double fidelity.
These selectors are additive; no original selector or mandatory lane is removed.

`source-contract-review.json` records normalized before/after file identities,
semantic-AST comparison and assertion counts for each existing binding. Counts
alone are not the review: the assertions, fixtures and fault branches above were
inspected. The registry association digests record only this static review.
Existing 900-test and 193-test records keep their original source identities;
current CI must execute and finalize its own required evidence.

## Local validation of the review

The registry checker accepts the updated associations and the impact plan is
`PLAN_READY`, with no unassigned high-risk path. Full Ruff and the tracked-secret
scan pass. The expanded governance/native run passed 211 cases, including every
native T14B authority/fault/rollback case, and exposed one route-document source
binding failure. The direct script invocation had loaded the shared editable
primary checkout. Regeneration with `python -m scripts.docs.check_route_inventory`
binds the PR checkout and adds its three existing history-recovery routes.

The final source-contract suite and the formerly failing import-origin control
passed 48 cases, with zero failures/errors/skips. The source-origin guard still
rejects a conflicting editable checkout and records the correct bound origin.
No test assertion, source guard, financial oracle or CI requirement was weakened.
