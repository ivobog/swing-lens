# Independent golden provenance and update policy

The retained baseline is A05 input `a05-v3` with A05B expectation supplement
`a05b-r3`. A13 approves no new golden version. Existing financial expectations,
derivations, tolerances, seven completed contracts, 402 rows, 541 relationships
and both protected A04 620-gate variants remain unchanged.

## Integrity and approval are separate

The existing A05/A05B loaders pin the input and completion manifests, artifact
hashes, configuration and exact expected vectors. The original A04 finalizer
compares actual persisted results and validates native execution and seals.
A16 binds those certificates and other lane evidence to source/run/attempt.
These controls establish content and execution facts. A SHA-256 digest, a
successful calculation, a PR checkbox, a claimed name or editable `APPROVED`
metadata does not establish independent human approval.

`scripts/qa/golden_provenance.py` reuses those original loaders and completeness
checks. The current registry, deterministic A04 producer and A16 certificate
consumer enforce its baseline checks. The four historical A05B authoring
worksheets are preserved byte-for-byte under
`docs/testing/golden-provenance/a05b-r3/*.py.txt`; their hashes are already sealed
in the unchanged completion manifest. These are non-executed authoring records,
not regeneration commands. Their old writer paths must never be run against
the frozen golden. Scoped Git attributes preserve exact sealed bytes.

The verifier can inspect a proposed revision and its trusted GitHub Actions path
can verify live authenticated reviews. It cannot activate expectations, write
goldens or issue a financial certificate. `require_authorized_update()` without
an explicit trusted GitHub PR context continues to deny every proposal.
A locally valid proposal reports
content-integrity PASS, financial equivalence NOT_EVALUATED, human approval
NOT_VERIFIED and `activation_authorized=false`.

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
   independence. The human reviewer must examine the actual derivation.
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

## Explicit approval workflow

1. Preserve the current frozen version and source review. Prepare an additive
   candidate with independent derivation and the complete proposal above in a
   disposable review workspace. No automatic command advances active hashes.
2. Inspect each financial difference against the input/contract change and
   derivation. Reject confounded explanations, production-derived expecteds,
   missing rows/sections, relaxed tolerances or incomplete obligations. Retain
   rejected candidates and original failed evidence.
3. Freeze candidate content and source, then request review through a protected
   repository PR. An authenticated, suitably authorized independent human must
   approve the exact candidate and latest source commit. They must be distinct
   from the PR author and automatic actual-result/expectation generator. An
   administrator must supply real reviewer identities; A13 invents none.
4. Require stale approvals to be dismissed after changes and disallow an
   automation/admin bypass that would permit self-approval. Protect golden,
   provenance, verifier, registry and relevant financial-config paths. Reuse
   GitHub authenticated reviews rather than creating signing keys or a service.
5. A trusted verifier must read current GitHub PR/review/permission/protection
   state through an authenticated API, bind the review to the candidate digest
   and source, and distinguish it from an editable downloaded JSON snapshot.
   Wire this as a required, non-bypassable review gate before accepting a new
   active hash. The implementation below is available; actual external protection,
   real reviewer assignment and authenticated end-to-end operation remain unverified.
6. After authorized review, verify the new independent oracle's completeness
   and regressions through the existing infrastructure. Run both original A04
   variants on frozen source, requiring 620/620 gates and all 13 seals each.
   Full-release and live promotion still require their separate A16/A04 gates.
   Approval of expected values does not imply successful runtime certification.

## Current enforcement limit

Observed GitHub `ivobog/swing-lens` main has `protected=false` and no rulesets.
The detailed protection endpoint returned 403, so its administrative detail
is unverified. Root CODEOWNERS explicitly contains placeholder accounts to
replace before enabling protection. The local release branch is not present
remotely. The current workflows have no environment reviewer-approval gate;
the existing PR template's golden checkbox is not approval evidence.

No repository protection, reviewer assignment, PR, signature, new golden
version, commit or push was created by A13 or A13-R01. Local proposal checks
always deny activation. **A13 remains PARTIAL until an independently authenticated
external approval control is configured and verified.**

## Trusted GitHub verification implementation (A13-R01)

The existing adapter's `verify_github_update()` accepts no downloaded review JSON
or claimed username. It performs authenticated GETs directly against GitHub for
`ivobog/swing-lens`, configured protected target `main`. Its Actions entry requires
`pull_request_target`, a clean checkout of the exact live base SHA and the unchanged
base verifier. `.github/workflows/golden-approval.yml` checks out that base only;
it never checks out, imports, installs dependencies from or executes PR code.
Proposed content is fetched as bounded, regular, non-executable data at the exact
API-returned head SHA into a temporary directory, which is removed on exit.

For a golden change, the PR must include `docs/testing/golden-update-proposal.json`
using the existing proposal schema. All historical baseline seals remain required.
The financial source commit must be an ancestor of the latest PR head; the proposal
cannot contain its own enclosing commit SHA. Authenticated review must apply to
the latest head and include this exact line, calculated with `proposal_digest()`:

```text
A13-APPROVE sha256:<canonical proposal digest>
```

That line alone confers no authority. The live review must be APPROVED by an actual
User distinct from the PR author, with current write/maintain/admin permission,
and that user must be an applicable CODEOWNER for all protected/proposed paths.
Review dismissal, stale commit, wrong digest/version, later changes, absent owner
permissions, pending changes-requested reviews or a missing required approval fail.
The conservative implementation supports individual CODEOWNERS entries, with
last-match semantics. Unsupported team/email/syntax is rejected, never inferred.

The detailed branch-protection read must show admin enforcement, required owner
review, dismissal of stale approvals, independent last-push approval, no PR bypass
actors, force pushes/deletions disabled and strict up-to-date status checks pinned
to GitHub Actions. Required names include `Authenticated golden approval`,
`Lint, Test, and Migration Gates`, `Supported deterministic A04 certificate` and
`Full-release certification verdict`. Additional required checks are not ignored.
Other required checks must report success, match the live PR/head or current merge
SHA and originate from the unchanged `.github/workflows/ci.yml`. A push, local
receipt, skipped/neutral check, another workflow or another GitHub App cannot supply
this authority. Live PR/base/review/protection state is rechecked before returning.

The current reviewed corpus/contract shape is retained: complete artifact manifests,
all existing sections, row identities/populations, required fields, handoff variants
and semantic relationships. Missing oracle content still fails after a valid review.
Changing the corpus or obligation schema requires a separate prior review of the
trusted verifier. Structural completeness is not independent numerical derivation;
the reviewer must inspect the actual formulas and every difference. Both original
A04 variants and A16 release evidence remain mandatory. Review verification returns
`review_authorized=true` with `activation_authorized=false`; it never advances pins.

The workflow and ordinary PR CI explicitly use read-only GITHUB_TOKEN permissions.
Reading detailed protection requires GitHub Administration:read; the default Actions
token and current connected integration may lack it. In that case verification
fails closed. An administrator must supply an existing suitably scoped read-only
GitHub identity through `GOLDEN_REVIEW_READ_TOKEN` if available; no credential, PAT,
GitHub App, private key or signing service is created here. Do not grant write scope
or run PR code to work around missing reads. If this capability is unavailable,
authenticated enforcement remains blocked. Rerun the trusted target check after
the latest independent review and required CI complete; it does not approve itself.

Actual repository configuration and a real independently authorized review still
have to be demonstrated. Offline synthetic API tests establish only the verifier's
accept/reject behavior, never deployed GitHub protection or approval of a new golden.
