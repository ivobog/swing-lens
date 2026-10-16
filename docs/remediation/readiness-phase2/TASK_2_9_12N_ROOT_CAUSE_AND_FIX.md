# Task 2.9.12n Root Cause and Fix

## Scope and verdict

The admission transaction and certification-harness remediation is a focused PASS. The
subsequent one-shot Chromium-100 pipeline certification failed at a later, distinct
decision-handoff invariant and was not retried.

## Proven original root cause

Task 2.9.12m declared same-named fixtures in the consuming test module and expected them
to override dependencies of an imported `certification_environment` fixture. Pytest
resolved those dependencies from the fixture's defining visibility context, so the local
opt-ins never reached the environment builder. The subprocesses therefore received
neither transaction tracing nor the pre-enqueue stop. The ordinary endpoint admitted a
pipeline and the test's expected HTTP 409 failed.

This was a harness configuration defect, not evidence that the production transaction
split was ineffective.

## Remediation

1. `certification_environment` now reads a validated, typed
   `certification_harness(transaction_trace, admission_mode)` marker before subprocess
   startup. Unsupported fields, types, modes, and an intercept without tracing fail
   closed. No cross-module fixture override is used.
2. `PYTEST_CURRENT_TEST` is removed from child environments so WEB and DURABLE_WORKER
   retain their real process identities.
3. The web adapter writes a process-owned status handshake containing role, PID,
   execution ID, disposable database, runtime mode, trace directory/writability, trace
   installation, admission mode, and intercept installation. The browser test verifies
   that handshake and live PID twice before admission.
4. The intercept is restricted to CERTIFICATION mode, `DISPOSABLE_TEST`, a generated
   `swinglens_pytest_cert_*` database, the WEB role, and an installed trace. It uses an
   explicit `start_pipeline` callback after independent verification and before pipeline,
   job, market-context, or publication mutation. It rolls the verification transaction
   back and then returns the test-only 409.
5. The production route commits the immutable RESERVED plan before calling
   `start_pipeline`. The same request-owned Session then starts a new transaction and
   independently re-reads and verifies current source authority.
6. Test-scoped SQLAlchemy tracing now records Session construction stack, transaction
   create/begin/end, completion action, checkout/checkin, backend PID, bounded SQL
   summaries, request/thread identity, rollback/commit, and Session close. Per-query
   fsync was replaced by bounded first/last summary evidence.

## Final targeted evidence

Execution `20261015T110103Z-8edf818a` proved:

- WEB process `23916`, thread `37824`, Session `2769305581520`.
- Preflight creation: backend `11276`, 67.653811 seconds, COMMIT, 329 SQL operations,
  1.489140 seconds of measured SQL execution.
- Pre-enqueue verification: backend `41744`, 67.381420 seconds, ROLLBACK at the test
  boundary, 330 SQL operations, 1.459961 seconds of measured SQL execution.
- Maximum continuously observed PostgreSQL age: 67.629003 seconds.
- One RESERVED plan and 100 raw members; zero pipelines and zero jobs at interception.
- The Session closed with no active transaction and neither correlated backend retained
  an open transaction.

Stage timing showed the transaction lifetime is dominated by mandatory independent
technical reconstruction, not PostgreSQL: the representative reconstruction passes took
roughly one minute while SQL took about 1.5 seconds. Moving this work outside the
transaction safely would require materializing immutable inputs and adding a new
post-computation source revalidation protocol. That is an architectural change, so it was
not attempted in this bounded remediation. Both phases retain more than 52 seconds of
headroom below the hard 120-second limit.

## Development work log

- First development browser run reached the correct 409 and zero-work boundary but a
  correlation assertion incorrectly required both transactions to use the verification
  backend. The trace proved one Session used two pooled backends; the assertion was
  corrected to correlate each transaction independently.
- Second development browser run passed.
- Focused service/negative development selection passed 14/14.
- After formatting and official source-pin regeneration, the clean targeted invocation
  passed 19/19 with native JUnit.

## Later Chromium failure

The one permitted Chromium-100 run reached and completed the CERI graph, including a
100-member capture, then failed with
`DECISION_MANIFEST_MISMATCH: run-start anchor no longer matches at decision handoff`.
No retry was started. The surviving evidence does not identify which anchor field
diverged, so this report does not speculate about a cause.
