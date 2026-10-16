# Task 2.9.12q Transaction Root Cause

## Proven owner

The expensive transaction is the parent Session used by Setup's atomic publication, not the
bounded source loader, detached CPU build, handoff reconstruction Session, progress channel, or a
child Session. The production-equivalent composed trace correlated:

- Python Session `1430738281648`
- SQLAlchemy root transaction `1431343082000`
- PostgreSQL backend `34480`
- root start at `confirm_transition_decision_verification_before_mutation`
- one `COMMIT` after 55.813513 seconds

The external PostgreSQL observer measured 55.611459 seconds. The parent transaction was absent
during CPU construction. The historical PID 12344 process was already gone, so its Python object
identity cannot be recovered; the equivalent trace proves the owner class without inventing an ID.

## Why the old structure stayed open

The parent transaction began at locked handoff confirmation and then performed, per member, full
native Setup recomputation, repeated execution-configuration delivery, repeated immutable evidence
and source-row retrieval, evidence creation, source-edge creation, projection advancement, and ORM
flushes. Baseline SQL was only 6.922818 seconds of an 80.526878-second capture, so the dominant cost
was Python/ORM validation while PostgreSQL correctly reported the transaction as idle between SQL
calls. The last SQL statement was an observation point, not the whole cause.

## Correction

Full deterministic output recomputation now happens in bounded child read Sessions before the
publication transaction. Every 25-member segment closes cleanly. Immediately before mutation, the
parent Session locks the exact durable handoff and set-locks the exact source graph. It revalidates
raw fingerprints, native row/evidence addresses, PIT price membership, configuration, history,
current selections, run/context, and prepared-payload binding. Evidence, Setup snapshots, and
current pointers are still published in one atomic parent transaction.

Shared frozen configuration is validated once per exact owner/resolution hash under transaction-
retained `FOR SHARE` locks. Exact source rows and evidence are bulk loaded and strongly retained.
Nested member writers reuse the batch semantic rollback scope; a member failure still propagates
and rolls back the batch.

## Remaining full-E2E failure

The one authorized Chromium-100 attempt (`20261015T191419Z-013522b1`) passed the earlier failing
handoff point but still reached 120.005556 seconds in `CAPTURING_SETUP_SIGNALS`. Backend 18600 was
idle after an `INSERT` into `core_calculation_evidence_sources`. The last committed pipeline stage
was `FREEZING_DECISION_HANDOFF_MANIFEST`; Winner was not reached.

The original node's harness marker had `transaction_trace=false`, so the retained invariant log
contains the backend identity but not a Python Session ID for this new failure. Its stage, timing,
worker/job lineage, last SQL family, and cleanup are retained. This prevents a false claim that the
full-scale transaction defect is closed.
