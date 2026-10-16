# SwingLens Task 2.9.12p Regression Causal Analysis

## Conclusion

The latest failure is not a CERI producer, normalization, feature, capture, completion-barrier, or
continuation defect. It is a Setup transaction-lifetime failure in the downstream continuation.
The new run reproduced the same failure class as 2.9.12o with a more specific SQL position:
100-member Setup authority validation/publication was still active when its PostgreSQL transaction
crossed 120 seconds.

No recent CERI change was identified as causal, and no CERI fix was rolled back.

## Change-impact analysis

| Area | Recent change reviewed | Direct/shared effects | Finding |
|---|---|---|---|
| CERI producer and normalization | Batched workflow, authority scaling, exact run/context selection | Provider, normalize, feature fan-out, finalization | Completed in latest run; not causal |
| CERI capture and barrier | Exact certified-count reconciliation and coalesced continuation | Capture rows, parent roll-up, continuation enqueue | Exact barrier admitted continuation once; not causal |
| Decision handoff | v1/v2 anchor compatibility, reconstruction diagnostics, bounded verification | CERI consumer, Setup pre-mutation authority | Handoff completed; its 100-member verification begins the observed Setup transaction window |
| Setup source loading | Bounded child source Session and detached graph repair from 2.9.12o | Snapshot builder and handoff reconstruction | Corrected detached relationship defect; no recurrence |
| Setup progress | Durable worker callback | Job progress/heartbeat | Callback uses an independent control Session; the old hypothesis that it wrote through the parent Session was disproved |
| Setup CPU build | New explicit clean boundary and 25-member checkpoints | Parent transaction ownership | Targeted real PostgreSQL test proves no parent transaction during CPU-only construction |
| Setup publication | Existing atomic writer with per-member authority validation | Snapshot rows, immutable evidence, current pointers | Still too long at full E2E scale/observability; causal failure area |
| Winner | No relevant producer change | Downstream consumer | Not reached; not causal |
| GUI/status | Operational metadata only | Progress/status rendering | Non-interference test proves it does not change CERI semantic authority |

## Proven defects corrected in this task

### Inherited parent transaction before CPU construction

The production Setup capture path now closes only a clean parent transaction before CPU-only work.
It refuses the boundary if `new`, `dirty`, or `deleted` ORM state exists, so pending financial work
cannot be silently rolled back. The production builder now receives its checkpoint callback, and
the boundary is reasserted every 25 members. Worker progress and cancellation continue through
independent control Sessions.

### Redundant immutable evidence loading

The first real 100-member targeted run completed financial work but failed its performance
invariant with 2,603 immutable evidence SELECTs. `SetupLifecycleRepository.upsert_snapshots` now
batch-loads the exact evidence IDs already named in the DTO lineage and retains strong references
through publication. It does not substitute evidence or weaken validation. The rerun used 104
evidence loads and passed.

## Latest change-to-failure relationship

The 2.9.12p changes did not alter CERI output. They fixed the inherited transaction and redundant
evidence lookup defects. The single Chromium run then exposed a remaining scale gap not exercised
by the targeted Setup-100 fixture: the full run combines a consumed 100-member decision handoff,
full source/configuration authority revalidation, immutable evidence publication, production SQL
telemetry, and the transaction observer in one path.

That path continued performing repeated configuration and source validation inside the required
atomic publication transaction. Targeted evidence recorded 702 configuration loads and 4,136
queries even after evidence prefetch. In the actual E2E path, the transaction window began at the
pre-mutation handoff boundary and crossed 120 seconds while configuration-anchor validation was
still active. This is a transaction-lifetime/shared-service defect, not a changed financial result.

The trace proves the owning execution, worker, job, stage, and SQL family. Because SQL telemetry
does not record backend PID, it does not prove which SQLAlchemy Session object held PID 12344 across
the verification/publication boundary. A further repair must first close that attribution gap and
then remove unnecessary authority reloads or otherwise bound the exact atomic writer without
weakening its validation.

## Why prior certification missed it

1. The earlier cross-stage suite and Chromium-10 did not create the 100-member transaction load.
2. The new real Setup-100 test proved source load, CPU construction, progress isolation, and
   publication, but it did not include the complete consumed 100-member decision-handoff graph and
   E2E SQL telemetry overhead in the same transaction.
3. Prior tests independently covered handoff and Setup publication; they did not measure their
   composed 100-member transaction.
4. CERI-focused tests correctly stopped at CERI authority and continuation. The defect is later.

## Failure classification

| Candidate class | Result |
|---|---|
| Genuine CERI calculation/persistence defect | Rejected |
| Incorrect CERI normalization | Rejected |
| Missing certified evidence | Rejected by completed exact barrier |
| Cross-run/wrong-context selection | Rejected by targeted negatives and latest continuation |
| Stale/superseded authority | Rejected; one current worker generation owned job 32 |
| Retry/cancellation/continuation failure | Rejected for CERI; continuation was acquired once |
| Incorrect CERI counter/status | Rejected by targeted barrier certification |
| Transaction-lifetime defect | Confirmed in Setup |
| Shared service regression | Confirmed in authority/configuration validation workload |
| Test fixture/observability defect | Not demonstrated; observer correctly caught a real 120-second backend transaction |
| Downstream consumer defect | Confirmed: Setup, before Winner |

## Affected producers and consumers

- CERI provider, normalization, feature, finalization, capture, change, and alert producers were
  executed and were not affected semantically.
- Decision handoff produced valid authority and completed.
- Setup consumed the handoff and owned the failing stage.
- Runtime transaction monitoring correctly rejected the long transaction.
- Pipeline terminal roll-up, Winner publication, GUI terminal assertions, and financial-cardinality
  assertions were prevented from completing.

