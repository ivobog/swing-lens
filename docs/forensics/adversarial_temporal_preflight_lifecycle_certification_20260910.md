# Adversarial Temporal / Preflight / Lifecycle Certification — 2026-09-10

## A. Executive verdict

The temporal/preflight/lifecycle subsystem is certified as one deterministic contract. One
critical, four high, and seven medium systemic defects were fixed. The exact latest-canary
timezone regression now has identical canonical bytes and fingerprints before and after a real
PostgreSQL roundtrip. The plan state machine, concurrency/CAS, pointer/ledger atomicity, lifecycle
transition modes, technical reconstruction, and CERI PIT selection all passed adversarial tests.

The production database was read only. No production pipeline, live canary, historical repair,
or merge was performed. The branch is safe for one final live canary after normal review.

## B. Baseline

| Item | Observed baseline |
|---|---|
| Branch / HEAD | clean `codex/preflight-context-selection-key-remediation` at `c59a6550a744bcb576ce17042f4bd4feb39337d9`; certification then moved to `codex/adversarial-temporal-preflight-certification` |
| Certified ancestry | `c59a655`, `b278601`, and `5c76d7ec` are all ancestors |
| Alembic code head | `0071_transition_preflight_plan` (single head) |
| Production Alembic head | `0071_transition_preflight_plan` |
| Production DB | PostgreSQL 18.3, database `swinglens`, role `postgres`, `127.0.0.1:5432`; session timezone `Europe/Berlin` |
| Processes | app stopped; worker stopped; supervisor stopped; no listener on port 8000 |
| Jobs | active 0; queued 0; running 0; recovering 0 |
| Latest run | Run 152, `COMPLETED` |
| Latest pipeline | Pipeline 144 for Run 151, `COMPLETED` |
| Latest job | Job 42931, `CERI_ALERT_REBUILD`, Run 151, `COMPLETED` |
| Preflight plans | 1 total; Plan 1 `CANCELLED` |
| Reserved contexts | 1 unowned; context 5 retained |
| Baseline clocks | local `2026-09-09T22:37:15.893753+02:00`; UTC `2026-09-09T20:37:15.893753Z`; New York `2026-09-09T16:37:15.893753-04:00` |

The baseline matched the required production head and safety state, so certification proceeded.

## C. Invariant registry summary

The [invariant registry](../architecture/temporal_preflight_invariants.md) defines 50 invariants:
TIME 7, CONTEXT 6, PIT 7, LIFECYCLE 7, PREFLIGHT 9, PERSISTENCE 7, and CONCURRENCY 6, plus one
cross-cutting forensic-logging invariant. All 50 are certified; none failed or remains ambiguous.

## D. Canonical serializer

`app/services/canonical_evidence.py` now owns `CanonicalEvidenceSerializer`. The contract is:

- aware datetimes only, normalized to UTC as `YYYY-MM-DDTHH:MM:SS.ffffffZ`;
- no floating-point Unix timestamp in safety evidence;
- distinct date, datetime, and time encodings;
- finite normalized Decimal/float handling, stable UUID and Enum values, base64 bytes;
- lexical map ordering with collision rejection; canonical set ordering; domain list order;
- fail closed for naive temporal values, NaN/Infinity, and unsupported objects;
- no `repr()` or `json.dumps(default=str)` dependence in remediated safety-critical paths.

The existing Winner manifest module is retained only as a compatibility API over the shared
serializer.

## E. Fingerprint inventory

The registry inventories preflight aggregate, frozen context, technical reconstruction, candidate
evidence, pointer/precondition, candidate set, CERI score/source/revision/price/feature,
lifecycle immutable/current-selection, technical artifact/config, and Winner temporal-manifest
fingerprints. All listed safety paths use the shared serializer directly or through the documented
compatibility wrapper. Time-bearing hashes are timezone invariant and semantic collections are
order invariant.

## F. Datetime/timezone certification

The exact regression pairs
`2026-09-09T19:55:33.682959+00:00` and
`2026-09-09T21:55:33.682959+02:00` now produce the same instant, canonical bytes, context
fingerprint, technical fingerprint, and candidate/evidence fingerprint. Generated cases cover UTC,
Europe/Zurich, America/New_York, Asia/Tokyo, both Zurich/New York DST transitions, market
open/close, daily-bar readiness, three midnights, weekend, and holiday boundaries. Naive semantic
times fail closed.

## G. Persistence roundtrip

Real disposable PostgreSQL sessions set to UTC, Europe/Zurich, and America/New_York reproduced
identical canonical identities after ORM → PostgreSQL → ORM. JSON roundtrip, JSON key permutation,
set/frozenset permutation, Decimal normalization, UUID/Enum/null/bytes encoding, and fixed
microsecond precision also passed.

## H. Preflight state machine

The persisted initial state is `RESERVED`; ephemeral `CREATED` and `READY` do not exist as
separate database states. The only valid lifecycle is explicit:

| From | Event | Result |
|---|---|---|
| RESERVED | consume | CONSUMED with one context/pipeline owner |
| RESERVED | cancel | CANCELLED, forensic context retained and invalid |
| RESERVED | expire | EXPIRED with `TTL_EXPIRED`, forensic context retained and invalid |
| RESERVED | invalidate | STALE |
| terminal | enqueue/state change | deterministic rejection or idempotent return; no resurrection |

Create/reload/enqueue, duplicate enqueue, cancel/enqueue, expire/enqueue, pointer/evidence change,
process/worker restart, cancel/reload, and abandonment were exercised. Distinct deterministic codes
cover stale, precondition, context, provenance, selection key, duplicate, expiry, cancellation,
concurrency, and invariant failures.

Context 5 is correctly retained as forensic evidence for cancelled Plan 1. It is not reusable. A
future explicit retention policy may garbage-collect terminal-plan metadata after its audit window;
this work introduced no cleanup and did not delete context 5.

## I. Concurrency/CAS

Real PostgreSQL transactions proved that a later pointer advance deterministically invalidates the
old plan, two concurrent consumers create exactly one pipeline, the plan row has one owner, and a
post-cutoff evidence arrival cannot change frozen execution. Sorted lifecycle advisory locks and
database uniqueness preserve deterministic session-key selection under concurrent completion.

## J. Pointer + ledger atomicity

Both `SAME_SESSION_REPLACEMENT` and `NEW_SESSION_CANONICAL_INITIALIZATION` update their pointer and
append exactly one matching selection event in the same transaction. Injection before the ledger
insert and after both rows were flushed but before commit rolled back both sides. No tested path
left a pointer without a ledger event or an event without the matching pointer state.

## K. Derived current state

Lifecycle suites cover multiple sessions, same-session replacement, new-session initialization,
missing session, gaps, out-of-order historical insertion, late historical snapshot, and concurrent
pointer writes. Derived current state remains the latest valid session-canonical pointer; earlier
session snapshots and pointers are not rewritten.

## L. Technical PIT reconstruction

Raw-bar order is normalized before fingerprinting. Frozen cutoff/session predicates exclude
post-cutoff insertion and revision; durable context survives cache warm/cold behavior, worker delay,
restart, and process-pool fallback. The current fallback explicitly forwards the same cutoff to
sequential execution. Technical decisions/fingerprints were invariant across the timezone and order
matrix.

## M. CERI PIT replay

On the restored clone, CERI snapshot 10929 reproduced evidence hash
`1df165a0cbeba82db61e9545a4f820a6599a687e0b45c7df1cb8f37fdf9a8eb8` from seven eligible source
records (IDs 867459–867465). Price bars 2263479 and 2263504 were present and cutoff eligible.
Post-cutoff source/bar revisions, older eligible versions, absent/naive receipt provenance,
reordered source results, worker delay, and timezone variation could not leak later knowledge into
the frozen calculation.

## N. Production-clone replay

A fresh read-only `pg_dump` of production was restored to isolated database
`swinglens_qa_temporal_cert_20260909`. Backup SHA-256:
`B9BE75A5DB62DE554025CDC6E529EFF2B4FFD1EAD18EA3D6264BF64CFD2DFA75`.

Restore validation found revision `0071_transition_preflight_plan`, zero foreign-key violations,
zero blank required evidence hashes, and all 152 upload runs. Runs 148–152 were present/completed;
their lifecycle snapshot counts were 5, 5, 5, 1, and 0. Targeted replay covered TBLA, NNI, TPL,
DRS, and CERI 10929.

Clone-only mutation created Plan 2, context 6, pipeline 145, and job 42932. Reload under
Europe/Zurich preserved identity; consume succeeded; a duplicate returned the authoritative
pipeline 145; total pipelines for the request remained one. This mutation occurred only in the
disposable clone.

Restore and replay evidence:
[restore validation](../../artifacts/forensics/adversarial_temporal_clone_restore_validation.json)
and [targeted replay](../../artifacts/forensics/adversarial_temporal_clone_replay.json).

## O. Randomized/metamorphic tests

Fixed-seed deterministic generation executed 512 combinations varying ticker, timeframe, session,
pointer existence/target, candidate state, cutoff timezone, source/bar version ordering, plan state,
and simulated concurrent change timing. The broad clone scan evaluated 1,491 current-universe
candidates: 1,273 HIGH and 218 LOW, including 341 same-session replacements and 932 new-session
canonical initializations. Clone row counts were identical before and after the read/replay scan.

## P. Failure injection

The transaction/restart matrix covers these 12 named boundaries: after context reservation, after
plan persistence, before enqueue commit, after run creation, before pipeline creation, after
pipeline creation, before job enqueue, after job enqueue, during worker execution, before pointer
update, after pointer update/before ledger append, and after ledger append/before commit. Focused
PostgreSQL tests directly inject the transaction-sensitive context/plan, pipeline/job, and both
pointer/ledger split points; established pipeline/worker restart tests cover the surrounding owner
boundaries. Every rollback/retry assertion found zero half-applied business state.

## Q. Adjacent findings

| Workflow | Classification | Certification finding |
|---|---|---|
| Run Full Pipeline preflight | SAFE | Authoritative reserved context is attached and reused. |
| IB Gateway readiness | OUT_OF_SCOPE_NON_BLOCKING | Operational check only; no evidence/context identity. |
| CERI active preflight | SAFE | Frozen context and PIT provenance/revision filters. |
| Winner maturation | SAFE | Shared canonical wrapper and PostgreSQL temporal regressions pass. |
| Market-data readiness/prewarm | SAFE | Operational preemption; cannot create C2. |
| Technical process-pool fallback | SAFE | Same supplied market cutoff reaches sequential fallback. |

No adjacent blocking defect remains.

## R. Systemic defects fixed

The [systemic defect register](temporal_preflight_systemic_defect_register_20260910.md) records 12
findings: offset-sensitive instant identity, fragmented safety serializers, cutoff-filtered mutable
pointer state, naive CERI provenance, offset-bearing CERI feature hashes, query-order candidate
evidence, presentation-oriented durable times, under-bound idempotency, conflated terminal errors,
missing formal context fingerprint, insufficient mismatch observability, and Alembic disabling
host-process forensic loggers. All 12 are fixed and certified.

## S. Migration

No migration is required. The change is behavioral and test/documentation-only at the schema
boundary. Code, production, and restored clone each have one head:
`0071_transition_preflight_plan`. No migration was applied to production.

## T. Full test matrix

<!-- TEST_MATRIX_START -->
| Gate | Result |
|---|---|
| Canonical serializer + exact regression + adversarial properties | 16 passed |
| CERI PIT enforcement | 10 passed |
| CERI score timezone/order regression band | 14 passed |
| Affected temporal/lifecycle/CERI/Winner domain regression | 1,010 passed |
| Expanded real-PostgreSQL preflight + Winner serialization regression | 22 passed |
| In-process Alembic followed by worker/supervisor/Winner log capture | 4 passed |
| Final full non-slow suite | **2,318 passed, 9 skipped, 58 deselected, 0 failed** in 25m24s |
| Changed Python formatting | 31 files already formatted |
| Ruff, active source (`app`, `tests`, `scripts`, `alembic/env.py`) | PASS |
| Ruff, every changed Python file | PASS |
| Compileall (`app`, `scripts`, `tests`) | PASS |
| Machine summary JSON validation | PASS |
| Alembic heads | one: `0071_transition_preflight_plan` |

A literal repository-wide Ruff scan also found 24 pre-existing style findings confined to immutable
migration files 0001–0015 and 0066. Those historical migration sources were not rewritten. All
active source, all changed Python, and the current Alembic environment pass Ruff; the legacy style
baseline is non-blocking and unrelated to runtime or migration correctness.
<!-- TEST_MATRIX_END -->

## U. Production safety

```text
Runs 148–152 changed: NO
CERI snapshot 10929 changed: NO
production business writes: NO
production pipeline: NO
live canary: NO
historical repair: NO
merge: NO
```

All production SQL was issued inside a read-only transaction. Final verification again observed
production revision `0071_transition_preflight_plan`; Run 152; pipeline 144; job 42931; one
cancelled plan; one unowned context; zero queued/running/recovering jobs; and the unchanged CERI
10929 hash. App, worker, supervisor, and port 8000 remained stopped. Final clocks were local
`2026-09-10T01:33:06.9917628+02:00`, UTC `2026-09-09T23:33:06.9917628Z`, and New York
`2026-09-09T19:33:06.9917628-04:00`.

## V. Residual risks

- The final live canary is intentionally not part of this certification. It remains the next
  operational event and should use the standard stopped-service/readiness checklist.
- Cancelled/expired contexts are deliberately retained; long-term metadata retention/GC policy is
  an operational follow-up, not a correctness blocker.
- The serializer intentionally rejects unsupported values. Future safety-critical payload types
  must add an explicit semantic encoding and tests rather than a permissive fallback.
- Historical values were not rewritten. Compatibility wrappers reproduce existing evidence while
  all newly computed safety identities use the consolidated policy.

None is a blocking defect within the certified contract.

## W. Final recommendation

Approve this branch for review and, after it is reviewed without semantic change, one final live
canary. Do not bypass preflight, do not create a replacement context at enqueue, and retain the
normal production rollback/observability posture. This certification itself does not authorize or
run that canary.
