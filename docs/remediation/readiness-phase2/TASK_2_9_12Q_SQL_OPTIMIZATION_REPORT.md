# Task 2.9.12q SQL Optimization Report

## Measured comparison

| Measure | Composed baseline | Final composed certification | Change |
|---|---:|---:|---:|
| Queries | 5,150 | 2,594 | -2,556 (-49.63%) |
| Duplicate queries | 4,488 | 2,522 | -1,966 (-43.81%) |
| Configuration loads | 902 | 32 | -870 (-96.45%) |
| Immutable evidence loads | 207 | 208 | +1 |
| ORM flushes | 403 | 403 | unchanged |
| Total observed SQL time | 6.922818 s | 6.276045 s | -0.646773 s |
| Capture wall time | 80.526878 s | 101.408218 s | +20.881340 s |
| Maximum transaction age | 68.094043 s | 55.611459 s | -12.482584 s |
| Best uninstrumented optimized transaction | — | 41.358952 s | preferred target met |

The final fully traced run satisfies the allowed `<= 60 s` acceptance bound but not the preferred
45-second target. The remaining time is justified only as measured application authority work:
the trace attributes 51.198767 seconds to application processing and 4.614746 seconds to SQL in the
root publication transaction. The subsequent Chromium failure shows that this margin is not stable
under the complete durable-worker E2E load, so no release claim is made.

## Classification

- Removed: repeated configuration anchor/binding/record decoding for the same exact owner and
  resolution hash.
- Removed: per-member retrieval of exact source rows already set-locked by the batch.
- Removed: redundant full native financial recomputation inside the final transaction.
- Reduced: repeated evidence/source graph retrieval through bulk loads and retained identity-map
  references.
- Preserved: independent financial recomputation, exact PIT price verification, immutable evidence
  membership, final source/config/handoff fencing, atomic rollback, and evidence/source-edge writes.
- Preserved: 403 flush observations because evidence IDs and source-edge/current-projection
  dependencies still require ordered ORM visibility. These remain the largest unoptimized area.

## Downstream consumers

Affected and retested consumers are Setup immutable evidence, Setup current selection, lifecycle
evaluation, pipeline continuation/result roll-up, and Winner eligibility/acquisition. The clean
cross-system gate passed 203 tests. The original Chromium run failed before Winner publication, so
full-scale Winner consumption remains uncertified.
