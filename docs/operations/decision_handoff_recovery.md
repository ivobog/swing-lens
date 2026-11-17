# Decision handoff progress and recovery

Fresh pipeline preparation reports verified source-loading, price-page, snapshot,
manifest and lineage work before a plan is available. Large shared-evidence hashes
also report bytes actually hashed. These checkpoints use the detached job-control
connection; they must not commit the financial transaction or bypass cancellation.

The handoff validates a shared sector/regime pair once per distinct pair of native
source objects in each invocation. The native payload/identity seals, omission
policy and source locks remain required. The verified result is not cached across
transactions, handoff attempts, callbacks or later source changes. Shared artifact
hashes are similarly calculated once after per-company callbacks finish.

Fresh preparation may reuse an already loaded source context in the same unchanged
transaction. It rebuilds snapshots after the plan checkpoint and reconstructs
pointer-dependent decisions again, preserving nested source changes and pointer
change detection. The final run-start anchor comparison remains mandatory.

Session-limited price reads fetch candidate session pages and authorize/project
them before deciding whether older pages are required. All receipt seals remain
verified. This path validates the history needed to prove the consumed sessions;
unrelated older cache versions are not materialized solely for validation. Full-
history consumers retain full-history checks. Receipt indexing retains every
candidate version and the existing exact-version and tie-breaking rules.

## Qualification

Run `python -m scripts.qa.qualify_pipeline61_handoff --output <empty-directory>`
from a clean candidate checkout with local disposable PostgreSQL test access.
The production database must never be the tests' write target. The qualification
includes the prior scheduler contract, PIT/receipt and integrity regressions,
and actual native 159-company fresh handoff, setup/lifecycle and Winner consumers.
Providers are deterministic. The production watchdog is exercised with explicit
observed times rather than a 30-minute sleep.

Activation requires a passing receipt for the exact clean commit and source
hashes, no selected failures/errors/skips, complete selected-target coverage and
the retained native proof. Verify with
`python -m scripts.qa.verify_pipeline61_handoff_qualification --receipt <receipt>`.
The verifier also checks the retained JUnit hash and native properties.

## Diagnosing a future stall

The existing autonomous control heartbeat observes persisted useful progress
without changing it. At 75% of the stage timeout, it records one
`job.progress_stall_diagnostic` per unchanged progress signature. The record has
the stage/item/counts, memory and up to 32 handler stack frames. It contains no
frame locals, arguments, source text, SQL bind values or execution tokens.
Its database read has a two-second statement limit; diagnostic failures do not
stop authority renewal or the financial operation. The watchdog and equivalent-
stall recovery limit remain active.

Before resuming a blocked pipeline, verify its frozen configuration, market
context and completed upstream outputs. Resume through the supported pipeline
action at the first incomplete boundary; do not edit database status rows to
manufacture completion or replace the original cutoff with today's context.
