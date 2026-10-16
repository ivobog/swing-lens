# SwingLens Task 2.9.12p Setup Transaction Remediation

## Original 2.9.12o blocker

The prior Chromium-100 failure reported a 120.326891-second idle transaction during
`CAPTURING_SETUP_SIGNALS`. Task 2.9.12o hypothesized that `SOURCE_CONTEXT` progress wrote through
the parent Session. Code inspection in 2.9.12p disproved that part: the durable worker callback
creates an independent bounded control Session. The actual risk was the parent Session's inherited
read transaction and the absence of production builder checkpoints before expensive construction.

## Implemented correction

`app/services/setup_lifecycle/snapshot_builder.py` now:

1. loads source context/history in a bounded child Session;
2. commits and closes that clean read transaction;
3. refuses to close the parent boundary if it contains pending ORM writes;
4. commits a clean inherited parent transaction before CPU-only construction;
5. supplies the production builder checkpoint callback;
6. reasserts the clean parent boundary every 25 members;
7. retains independent worker progress, cancellation, and heartbeats;
8. performs bounded read-only decision reconstruction;
9. locks and re-confirms the durable plan/handoff pair before mutation; and
10. leaves financial publication atomic.

No checkpoint commits or rolls back unrelated parent financial work.

`app/services/setup_lifecycle/repository.py` additionally batch-prefetches the immutable evidence
IDs already named by the 100 Setup DTOs and keeps strong references through validation. All evidence,
configuration, PIT, source-address, and publication checks still run.

## Targeted real PostgreSQL evidence

The first 100-member run exposed 2,603 redundant immutable evidence loads and failed only its query
ceiling. After the evidence prefetch, the same production path passed:

| Measurement | Value |
|---|---:|
| Population | 100 |
| Test duration | 211.599 s |
| Capture wall time | 40.608105 s |
| SQL time | 3.40655 s |
| Query count | 4,136 |
| Duplicate-query count | 3,489 |
| Flushes | 403 |
| Immutable evidence writes | 100 |
| Immutable evidence loads | 104 |
| Configuration loads | 702 |
| Progress events | 106 |
| Maximum observed transaction age | 35.933041 s |
| Parent transaction during CPU checkpoints | `false,false,false,false,false,false` |
| Parent transaction after completion | none |

The test uses real PostgreSQL, 100 realistic members, the production loader, detached source
context, actual builder, actual progress wiring, actual parent Session, verification boundaries,
and the publication path. Rollback, altered retry, concurrent duplicate capture, and direct attack
tests also passed.

## Chromium-100 result and remaining defect

The original Chromium-100 test nevertheless failed once, without retry, at 120.292053 seconds.
The latest path includes a consumed 100-member decision handoff and full E2E telemetry absent from
the performance fixture. Its transaction began at approximately `16:13:58.853Z`, publication began
at `16:14:23.314Z`, and configuration/source/evidence validation was still running after
`16:15:59Z`.

The last observed SQL was an execution-configuration-anchor lookup. The targeted run's 702
configuration loads and 4,136 queries show remaining repeated authority work, but the current
evidence does not prove that configuration-anchor caching alone accounts for the entire excess.
SQL records also lack backend PID, preventing conclusive child-versus-parent Session attribution.

Therefore:

- the inherited transaction and CPU-only ownership defect is corrected and regression-tested;
- the exact full-E2E 100-member transaction lifetime is **not corrected/certified**;
- publication atomicity and the 120-second limit were not weakened;
- no speculative post-failure patch or prohibited Chromium retry was made.

**SETUP TRANSACTION acceptance status: NOT CLOSED.** A follow-up must add backend-PID/Session
correlation to the existing trace, reproduce the composed 100-member handoff+publication path in a
targeted PostgreSQL test, and reduce proven redundant work while retaining one atomic publication.

