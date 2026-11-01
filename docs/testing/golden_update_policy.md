# Golden provenance and sole-owner update policy

## A13-R10 operator-approved exception, 2026-11-01

SwingLens is the personal repository `ivobog/swing-lens`. The operator explicitly
replaces mandatory independent-human review with authenticated sole-owner
authorization. No second account, different reviewer, independent CODEOWNER
approval count, or rejection merely because author and owner are the same person
is required. `@ivobog` remains the legitimate CODEOWNER.

**SOLO_OWNER_AUTHORIZED is not INDEPENDENT_HUMAN_REVIEW_VERIFIED.** This exception
changes human governance, not independent financial derivation. Original A13/R01–R09
PARTIAL findings remain historical and are not rewritten or retroactively satisfied.

The retained baseline remains A05 `a05-v3` / A05B `a05b-r3`: 45 provenance seals,
seven financial contracts, 402 rows, 541 relationships, independent calculation
vectors, and original A04/A16 authority. No expected value or tolerance changes.

## Integrity, owner authorization and activation are separate

The original loaders pin manifests, inputs, authoring records, configuration and
complete expected vectors. A04 compares persisted actual outputs; A16 binds lane
evidence to source/run/attempt. The existing provenance adapter reuses them.
No current production output may author expectations. Historical authoring
worksheets in `docs/testing/golden-provenance/a05b-r3/*.py.txt` remain non-executed
records, never regeneration commands. Local hashes, a claimed owner or editable
proposal metadata cannot authorize an update.

Owner acknowledgement authorizes review of the exact proposal only. It does not
establish mathematical equivalence or activate a golden. A financially invalid
baseline, missing derivation, incomplete oracle or failed required CI still fails.
Independent formula/literal derivation and financial verification remain required;
owner identity cannot override them. Successful authorization retains
`activation_authorized=false` and `financial_equivalence=NOT_EVALUATED`.

## Required proposal contract

Use `swinglens.golden-update-proposal.v1`. The proposal must contain:

1. `previous`: original input version, byte-manifest hash, normalized input
   identity, expectation revision and completion-manifest hash. They must match
   the retained baseline exactly.
2. `candidate`: distinct proposed input/expectation version identities, input
   and completion manifest paths/hashes, normalized input identity, every
   proposed artifact hash, and a complete old-to-new mapping of expectation
   JSON files. Manifest versions and input/expectation bindings must agree.
3. `contract_change`: exact input or financial-contract reason, source commit
   SHA and relevant configuration paths/hashes. The review also requires the
   actual source/configuration evidence, not just declared hashes.
4. `independent_derivation`: independently reviewed formulas and literal
   calculations, their artifact hashes, method INDEPENDENT_FORMULA_REVIEW and
   `production_outputs_used=false`. This declaration alone is not proof of
   independence. The owner must examine the actual independent derivation.
5. `expectation_changes`: every numerical and semantic difference, its original
   artifact and JSON pointer, exact before/after values and a specific reason.
   The adapter checks the complete content-difference inventory, including
   arrays, types, booleans, nulls, additions and removals. Production changing
   its output is never a sufficient financial reason.
6. `affected_obligations`: affected A04 gate identities and A05B domain/section,
   regression and precision contracts. Review must account for all relevant
   dependencies and both handoff variants.
7. `historical_artifacts`: all retained baseline artifact paths/hashes. Old
   inputs, expecteds, derivations, rejected candidates, discrepancies and native
   execution evidence must remain available and immutable.
8. `review_request`: proposal digest, candidate versions and source commit
   binding, with status PROPOSED. Review evidence from another source/version
   is invalid. Evidence produced by ACTUAL_RESULT_GENERATOR is not authority.

Candidate files must use new paths; old expected files cannot be overwritten.
No automatic snapshot refresh, production database export, exporter, actual
comparison result or current-output self-copy may author or approve expecteds.
A byte-identical copy does not prove who authored it and confers no new
approval. The retained baseline remains a content baseline, not a retroactively
authenticated reviewer attestation.

## Live owner acknowledgement

Use an open, non-draft PR into `ivobog/swing-lens:main`. After freezing its candidate
and latest head, the actual owner writes exactly one line in the PR description:

```text
A13-SOLO-OWNER-ACK head:<40-character current PR head SHA> sha256:<64-character canonical proposal digest>
```

Calculate the digest with the existing `proposal_digest()`; do not hand-edit a
receipt into approval. It covers the canonical proposal except `review_request`.
Every expectation change needs its complete original justification and independent
derivation. A new head or candidate requires a fresh, matching acknowledgement.

The trusted verifier reads live GitHub repository/PR/head/base and exact candidate
data. A fixed, read-only GraphQL query authenticates the PR body's latest editor
(or its author only when GitHub reports no edit). That actor must be the actual
individual repository owner by immutable node identity and login, currently admin,
and a CODEOWNER for every protected/proposed path. Another authorized maintainer
editing an owner's PR cannot impersonate the owner's acknowledgement. REST and
GraphQL PR/body/head identities must agree, and are rechecked before returning.
Local files, automation actors, stale bodies and missing API access fail closed.

The same owner may author and acknowledge the PR; a contributor's PR can be
acknowledged by the owner editing its description. No second-person review is read
or counted. Result: `owner_authorization=SOLO_OWNER_AUTHORIZED`,
`independent_human_review=NOT_CLAIMED`, `activation_authorized=false`.

## Trusted workflow and enforceable branch controls

`.github/workflows/golden-approval.yml` runs on `pull_request_target`, including
`edited` and `synchronize`. It checks out only the exact trusted base SHA with
credentials persistence disabled. It never checks out, imports, installs or executes
PR-controlled code. Candidate files are bounded regular-file data only.

Required current-source CI must finish successfully before final authorization.
If the description was acknowledged before CI finished, the owner can edit it
again after CI completes, or separately authorize a rerun of the current trusted
event. An old event head/base remains invalid. Non-golden PRs report NOT_APPLICABLE
and never claim financial authorization.

The financial authorization verifier continues to require protected main, strict
up-to-date checks pinned to GitHub Actions app 15368, administrator enforcement,
no PR bypass actors, and disabled force pushes/deletions. Required names remain:

- Authenticated golden approval
- Lint, Test, and Migration Gates
- Supported deterministic A04 certificate
- Core-release certification verdict

No GitHub review requirement may force a second person: review settings may be
absent, or have zero required approvals with CODEOWNER/latest-push reviews disabled.
CODEOWNERS coverage itself is retained. Additional genuine required CI is not
ignored. Checks must originate from the PR-associated trusted CI workflow and
exact head/current merge identity; push, old, missing or failed results do not pass.

The existing optional `GOLDEN_REVIEW_READ_TOKEN` may supply repository-scoped reads
for Metadata, Contents, Pull requests, Checks, Actions and Administration. REST
requests are GET-only; GraphQL uses POST for one fixed query, never a mutation.
There is no write requirement. Missing permission fails closed. Never create a
broader credential or execute PR code to work around a missing read.

## Deployment and promotion restrictions

At the last authenticated observation, main is unprotected and no rulesets exist.
This policy text does not enable enforcement. Access, protection and bypass
settings require separate explicit operator approval, with exact proposed settings
and a demonstrated runnable required-check path. Do not lock out the sole owner.

The later scheduled CI failure 41297539078 is a real separate readiness issue,
not a green check or authorization to repair unrelated CI. The historical d83645b
certificate remains historical and cannot certify a new source revision.

After owner review authorization, financial promotion remains a separate explicit
action through existing independent derivation, completeness and original native
A04/A16 checks. No acknowledgement, workflow or command here replaces active
golden pins. Tests with synthetic API fixtures are not genuine GitHub approvals.
