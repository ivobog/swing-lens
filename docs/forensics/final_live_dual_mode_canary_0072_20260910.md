# Final Live Dual-Mode Canary 0072 — 2026-09-10

## A. Executive verdict

```text
FINAL LIVE DUAL-MODE CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL/PREFLIGHT/LIFECYCLE REMEDIATION TO MAIN: NO
```

The hard stop occurred before enqueue. IB Gateway reported `READY` at
`2026-09-10T13:59:32.838500Z`, then the locked pre-enqueue transaction observed
`PROCESS_RUNNING_API_NOT_READY` / `IB_GATEWAY_API_NOT_READY` at
`2026-09-10T14:00:13.971932Z`. No Pipeline or canary Job was created. The runbook prohibits
retrying, regenerating, patching, or continuing after a new blocking operational defect.

Startup also scheduled unrelated Winner maturation Job 42981. It completed with
`NO_WORK_REMAINING` and zero material evidence changes, but its execution violates the required
isolated-queue boundary. This is an independent `UNEXPECTED_MUTATION` failure.

## B. Baseline / migration 0072

| Control | Result |
|---|---|
| Branch / execution HEAD | `codex/run153-decision-manifest-lineage-remediation` / `d5accbf70fcd0c5e7244a7ab05eb20f62f2ffb60` |
| Initial worktree | clean |
| Required ancestry | `d5accbf`, `0363a148`, and `a4e80fef` all ancestors |
| Code / production head before | `0072_ceri_artifact_context_lineage` / `0071_transition_preflight_plan` |
| Database | `swinglens`, PostgreSQL 18.3, `127.0.0.1:5432` |
| Initial runtime | app/worker/supervisor stopped; port 8000 listeners 0 |
| Initial jobs | running 0, queued 0, retrying 0, recovering 0 |
| Latest IDs before | Run 153 / Pipeline 145 / Job 42977 |
| Pointers / ledger / plans / contexts | 14,647 / 2 / 2 / 6, including one unowned context |
| Market boundary | latest completed session `2026-09-09`; ready at `2026-09-09T20:15:00Z` |
| Versions | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |

Migration file hash `f480bfaae3201ac584e41982f00b2988e13987d7` was byte-identical to certified
implementation commit `0529a0ee6dc7dc2811898417e19cbb6232a1f8ee`. It is additive: it adds lineage and
ownership columns, foreign keys, indexes, uniqueness identities, and conditional checks. Existing
artifacts were classified `LEGACY_UNKNOWN`; no historical context ID was fabricated.

The exact 0071→0072 upgrade completed transactionally. `alembic current` and `alembic heads`
both returned the single head `0072_ceri_artifact_context_lineage`.

Fresh backup:

| Item | Value |
|---|---|
| Method | PostgreSQL 18 `pg_dump`, custom format |
| Path | `backups/swinglens_final_live_dual_mode_canary_0072_20260910_131018.dump` |
| Completed | `2026-09-10T13:13:32.048105Z` |
| Size | 791,772,888 bytes |
| SHA-256 | `B7AE8371C0141FD86D75B52DD8944A6F47375BFC2705D0A20B387A859D41AC89` |
| Verification | PASS: PostgreSQL 18 `pg_restore --list` exited 0 |

## C. Candidate discovery

The certified production-path scanner ran read-only under cutoff
`2026-09-10T13:24:55.965490Z`. Counts before and after were identical.

```text
universe = 1,491
HIGH = 1,273
HIGH SAME_SESSION_REPLACEMENT = 340
HIGH NEW_SESSION_CANONICAL_INITIALIZATION = 933
MEDIUM = 0
LOW = 218
```

TBLA remained HIGH but had advanced to a new-session candidate, so it was not forced into the
same-session role. The smallest dual-mode set selected was ADAM plus DRS.

## D. Reserved context / preflight plan

Run 154 contains exactly ADAM and DRS and completed upload/fundamental processing normally.
Plan 3 reserved Context 7 at cutoff `2026-09-10T13:54:37.252136Z`, latest session
`2026-09-09`, calendar `swinglens-us-equities-v1`, and readiness
`daily-close-plus-15m-v1`.

| Mode | Ticker | Key | Existing state | Manifest hash |
|---|---|---|---|---|
| SAME_SESSION_REPLACEMENT | ADAM | `ADAM/1d/2026-07-06` | pointer→snapshot 11778, revision 1 | `a2dc4c78d518468f1bbade9d14ec4b47e3187e3e94a36901ecf39f2b640ba49f` |
| NEW_SESSION_CANONICAL_INITIALIZATION | DRS | `DRS/1d/2026-09-09` | latest snapshot 36951 at `2026-09-08`; exact pointer absent | `987ccd04c4326e5ccff5883168fab7a23a8f29e1a7540e354ff0d32846af9564` |

Both remained HIGH when Plan 3 was created. Aggregate evidence fingerprint is
`e92667dce9a1ced2f7719ee1c1cd7a8c645f436887e526c1201c5c75c2f20b2d`; aggregate
technical fingerprint is `9f85de3c4c6b276999556c6ce5312064f87cd4ab39b8cf4eaedf5582536c98bf`.
Plan 3 remains preserved as `RESERVED`, Context 7 remains unowned, and no second plan or run was
created.

## E. Decision Manifest equivalence

Both canonical preflight manifests were constructible and persisted. Production execution
equivalence was not exercised because the IB hard stop occurred before `start_pipeline` and before
the production lifecycle path. Observed manifest mismatches are zero, but this is not live proof.

## F. SAME_SESSION_REPLACEMENT evidence

ADAM was a valid HIGH candidate, but no pipeline executed. Replacements: 0. Same-session ledger
events: 0. Snapshot 11778 and its pointer remained unchanged.

## G. NEW_SESSION_CANONICAL_INITIALIZATION evidence

DRS was a valid HIGH candidate, but no pipeline executed. Initializations: 0. Initialization ledger
events: 0. Its existing `2026-09-08` pointer remained unchanged; no `2026-09-09` pointer was
created.

## H. Derived cross-session current-state evidence

No lifecycle mutation occurred. Existing derived current state remained internally consistent,
with zero observed violations, but the required live DRS advance was not exercised.

## I. Pointer / ledger atomicity

All 14,647 pre-existing pointer rows retained aggregate hash
`56136abc1688b236ac2d59dc53bcc4ab5fc701f3ca587b2363c7f1351b08abad`. Both ledger rows
retained aggregate hash `b62bf3b2e495272f483f6c0538a004d4bb6b9332ca68c151c9ebe352dcfda848`.
No pointer or ledger transaction was attempted.

## J. CERI artifact lineage

Migration constraints are installed, and the global count of pipeline-owned rows with NULL context
is zero in revision features, derived features, feature build states, and price-response features.
Rows 8684/8685 retained every pre-0072 field and were classified `LEGACY_UNKNOWN` without context
backfill. No canary CERI artifact was created, so live lineage certification was not exercised.

## K. CERI PIT evidence

No CERI source or bar was consumed by a canary. Post-cutoff sources, post-cutoff bars, and missing
receipt provenance consumed are all zero but are not live execution proof.

## L. Price-bar immutable-evidence certification

Both protected controls were identical before migration, after migration, and after the hard stop:

| Bar | Immutable hash | Full-row diagnostic | Changed fields |
|---:|---|---|---|
| 2263479 | `c9049dcc13baaefabb3b99ea24dfb8ce927485e55749b99fca05d0c236285ab5` | `ee1db44fca17de8da90b60a029d09217d0ccfb128a86a4a5449dfc4b02360eda` | none |
| 2263504 | `36127195b9fcce23befdef9d9db6cc926ba2fdd4b973fc870eab056860f013fa` | `5caf3751ceebe82c480fa6bcc4cde8b0c3eb1945e85b0e449a85c477409beff9` | none |

No price-bar operational metadata changed during this attempt.

## M. Historical immutability

Runs 148–153 each had zero immutable snapshot hash changes. CERI snapshots 10929/10930, sources
867459–867465, rows 8684/8685’s original columns, all prior Run 148–153 lifecycle snapshots,
pointers, ledger events, and protected bars were unchanged. Historical snapshot rewrites: 0.

## N. Database delta

Expected writes were migration 0072; Run 154; two raw rows; two fundamental rows; reserved Context
7; and Plan 3. There were no canary Pipeline, Step, Job, technical, lifecycle, CERI, IBMI, or Winner
evidence writes.

Unexpected startup workflow: enqueue attempt 223 created fanout root
`root-a36029dfa3b246e8821d878bd824dc23`, Job 42981, and Winner processing run 9999. It scanned zero
due items, produced zero material evidence changes, and completed `NO_WORK_REMAINING`. Its four
control-plane inserts are operationally harmless but violate the explicit zero-unrelated-workflow
canary boundary; `unexpected_business_mutations` is conservatively 1.

## O. Pipeline/PARTIAL classification

Pipeline status: `NOT_CREATED_FAIL_FAST_IB_GATE`. Classification: `OPERATIONAL_FAILURE` for
`IB_GATEWAY_API_NOT_READY`, plus `UNEXPECTED_MUTATION` for the unrelated scheduled workflow.
There is no PARTIAL pipeline to classify.

## P. Post-canary regression

`SKIPPED_FAIL_FAST_PRE_ENQUEUE_OPERATIONAL_FAILURE`. The runbook permits this lane only if live
certification has not failed. No patch-and-continue occurred.

## Q. Final runtime state

```text
all canary jobs terminal = YES (none created)
app = STOPPED
worker = STOPPED
supervisor = STOPPED
active jobs = 0
queued jobs = 0
retrying jobs = 0
port 8000 listeners = 0
production Alembic = 0072_ceri_artifact_context_lineage
```

The final worktree contained only this report and its machine summary before the evidence commit.

## R. Final recommendation

Do not merge. The required live dual-mode transitions, production Decision Manifest equality, CERI
lineage, and CERI PIT consumption were not exercised. A future task needs separate authorization
for another canary after IB Gateway stability and queue isolation are established. No historical
repair, production-code change, second canary, or merge occurred here.
