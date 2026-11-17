# Pipeline 59: Sector memory and recovery

This change is based on release `db8bf10c4962062b68d578d458bc54f7933fff8d`.
The original forensic captures remain in the main workspace under
`docs/remediation/pipeline59-sector-replay`.

The original Sector attempt was fenced for `WORKER_MEMORY_CRITICAL` at the
unchanged 6144 MiB worker limit. The subsequent root replay marked the still-open
Sector step "Interrupted by replay at VALIDATING_RUN." That message described
replay bookkeeping, not validation interrupting a healthy Sector calculation.

## Recovery contract

Worker loss records the interrupted attempt and its actual cause before removing
execution ownership. If the actual in-flight stage is Sector Rotation, its
continuation is `SECTOR_ROTATION_SNAPSHOT`, including when the original enqueue
payload declared validation or market-data fetching. The replacement worker must
validate completed predecessors, exact scope membership, ranking inventory, frozen
configuration, cutoff, source ownership and immutable evidence before continuing.
An invalid checkpoint blocks explicitly. It cannot silently restart at validation.

If Sector publication committed before worker loss, recovery verifies the current
snapshot, every serving child, its frozen configuration, exact ranking/regime
sources and native contributor manifest. It then reuses the certified publication.
A terminal pipeline cannot be reopened by a late delivery.

This does not turn an interrupted, incomplete Ranking stage into a completed
Sector checkpoint. During implementation, the old runtime was already physically
stopped and pipeline 59's replacement attempt was stale at Ranking (750/2330,
last heartbeat 17:34:46 CET). Its historical original Sector interruption is not
rewritten.

## Memory contract

* The Sector evidence reader checks exact database addresses and loads bodies in
  windows of 32. Its transaction/attempt-bound cache retains at most 64 records
  and 64 MiB of decoded values. Oversized bodies are validated without being
  retained. These limits describe the cache, not total process memory.
* Universe aggregation validates full frozen projections and exact stored source
  values before retaining compact aggregation values. It releases evidence
  relationships and producer debug trees progressively. The source manifest and
  permissions retain their original values and order.
* Completed pipeline stages release their returned cohorts after their commit.
* Sector serialization makes fresh independent copies while preserving shared
  internal subtrees. Canonical and historical snapshot hashes stream the exact
  existing bytes. No financial algorithm, configuration or eligibility policy is
  changed, and no successful validation decision is cached.
* Long Sector hashes report actual bytes processed, with cancellation and lease
  checks. Progress callbacks leave financial publication atomic.

## Diagnosis and visibility

The supervisor logs and persists the exact sample used for a critical-memory
decision before fencing or cleanup. Evidence includes private/RSS bytes, selected
measurement, budget, timestamp, registered PID and worker instance. The current
job's stage, Sector subphase and attempt are retained with the cause. Memory
critical takes precedence when a no-progress threshold is also reached.

The progress page explains the recovery cause and continuation validation state
while replacement work runs. Historical records without a triggering sample show
the retained cause without inventing a peak. The old post-cleanup sample is not
presented as the original triggering peak.

## Verification

The final focused release run passes 315 checks. The current architecture
registry passes with zero exceptions, and all 30 changed Python files pass lint.

Receipts are written to the main workspace's corresponding remediation folder.
They include native complete fence/requeue/claim/handler recovery, unchanged
upstream evidence and frozen authority, post-publication idempotence, scope/profile
tampering, terminal delivery, cache eviction and transaction isolation, exact
canonical/historical bytes, caller-copy independence, and atomic cancellation.
The native universe test compares compact aggregation with complete frozen
projections and proves evidence is released before its read transaction closes.

The scale probes are deliberately separate. One exercises observed-size nested
JSON transport and serialization; its synthetic producer records are unsealed and
cannot establish financial authority. The full native probe uses normal admitted
466-company scope, all five frozen ranking profiles, cached price facts, real
producer writers and the real Sector writer, with no live provider calls. Both
measure isolated process memory against the unchanged 6144 MiB budget.

The first full native run completed all real producers and entered Sector
persistence, but its already-running harness expired at the old 3600-second wall
limit. This is a recorded failed test run, not a memory-budget failure or a passed
contiguous pipeline. Its observed peak was 3,148,247,040 private bytes; the actual
producer JSON was 1.019 times the captured production size. Its manifest contained
944 sources, 713,664 states and 70,805,188 JSON bytes. The future full-test harness
allows 7200 seconds.

Before that timeout, an enforced read-only capture and native dump preserved all
3729 sealed producer records, all five 466-row ranking groups, and exact frozen
scope/configuration/cutoff. A separate continuation qualification restores that
checkpoint into a new guarded test database. Its operational step receipts are
explicitly derived QA observations and must pass the production strict validator
before commit. It then tests real owner loss, requeue and replacement-worker
Sector execution with every upstream/provider path forbidden. Its receipt is
reported separately; it cannot be represented as a contiguous full run.

The restored qualification published Sector and advanced to the downstream
decision-handoff stage before its own 3600-second driver limit. A fresh guarded
process then passed full native Sector seal and all 11 child-value checks, legacy
versus streaming canonical-hash parity, unchanged full upstream row/evidence
digests, producer metadata, and exact frozen ownership/binding/cutoff checks.
The recovery worker's external one-second samples observed a peak of
3,494,543,360 private bytes (about 3.25 GiB); its internal final peak receipt was
unavailable after the driver timeout. The separate verifier's sampled peak was
1,286,516,736 private bytes. These measurements have separate process scopes.
The driver timeout remains a failed full-continuation run; the independent
committed-Sector verification passes. See `restored-sector-resume-timeout.json`,
`restored-sector-resume-verify.json` and `restored-worker-observed-memory.json`.

The independent transport test passed with 547,022,954 evidence JSON bytes and a
90,482,582-byte shared manifest. Its observed private-memory peak was 842,043,392
bytes (about 803 MiB), with exact row and canonical-hash parity.

The broader unit run found four pre-existing CERI/IBMI VOLATILITY/SHORT_PRESSURE
acceptance-fixture failures. The exact same four failures reproduce on the
unchanged base release (24 other tests in that file pass). These are recorded in
`baseline-contextual-unit.txt`; they are not waived as new passing checks. The
historical T14A inventory fixture's absent baseline review file is also unchanged;
the targeted source-adoption and current architecture checks are run separately.
