# Final Dual-Mode Lifecycle Transition Canary — 2026-09-09

## A. Executive verdict

```text
FINAL DUAL-MODE TRANSITION CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO
```

Failure classification: `PREFLIGHT_CONTRACT_FAILURE`.

The candidate gate succeeded, but enqueue failed closed before a pipeline was created. The exact reserved context was persisted as UTC during preflight and rehydrated by PostgreSQL with the session's `+02:00` offset. Both values represent the same instant, but the preflight evidence and technical fingerprint paths hash `datetime.isoformat()` without UTC normalization. Revalidation therefore produced different fingerprints for the same context/evidence world and raised:

```text
PRECONDITION_CHANGED: pointer, selection key, or required PIT evidence changed after preflight
```

This was not a real pointer, input revision, or session change. It is a deterministic representation defect in the shared-context contract. The task forbids patch-and-continue and a second live canary, so execution stopped immediately. No pipeline or job was created; neither lifecycle transition mode was exercised.

## B. Baseline / migration 0071

| Control | Result |
|---|---|
| Branch / execution HEAD | `codex/preflight-context-selection-key-remediation` / `b27860143ea7d62d381f3d8f3a090edc7664967e` |
| Initial worktree | clean |
| Required ancestry | `b278601`, `bb7dcf5`, and `5c76d7ec` are ancestors |
| Code head | one head: `0071_transition_preflight_plan` |
| Production head before / after | `0070_ceri_price_response_pit_context` / `0071_transition_preflight_plan` |
| Database | PostgreSQL 18.3, `swinglens`, role `postgres`, `127.0.0.1:5432` |
| Initial app / worker / supervisor | stopped / stopped / stopped |
| Initial port 8000 listener | none |
| Initial active / queued jobs | 0 / 0 |
| Latest run / pipeline / job | 151 / 144 / 42931 |
| Pointers / selection events | 14,646 / 1 |
| Preflight plans / reserved contexts | table absent / 0 |
| Baseline local / UTC / New York | `2026-09-09T21:17:45.681969+02:00` / `19:17:45.681969Z` / `15:17:45.681969-04:00` |
| Completed US session / ready-at | 2026-09-08 / `2026-09-08T16:15:00-04:00` |
| Calendar / readiness versions | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |

Migration 0071 was inspected at the certified HEAD. Its upgrade only creates `transition_preflight_plans` with foreign keys, status/uniqueness constraints, and lookup/expiry indexes. It contains no data update, historical plan creation, context fabrication, lifecycle rewrite, pointer change, or ledger change. The downgrade only drops the new table and its indexes. There is one Alembic head.

Immediately after migration, all Run 148–151 hashes, CERI snapshot 10929, sources 867459–867465, bars 2263479/2263504, pointer count, and the pre-existing ledger hash were unchanged. The new table was empty and no context was reserved.

### Fresh backup

| Item | Value |
|---|---|
| Method | PostgreSQL 18 `pg_dump`, custom format |
| Source head | `0070_ceri_price_response_pit_context` |
| Path | `backups/final_dual_mode_canary_pre_0071_20260909T191843Z.dump` |
| Size | 791,664,731 bytes |
| SHA-256 | `ae6f3268efa27a8d87c0878793042d03fd50281852412e1e393780a9961e74b6` |
| Verification | PASS: `pg_restore --list`, 1,277 TOC entries |

The backup is ignored and was not committed. No credentials are present in it or this report.

## C. Candidate discovery

The corrected production scanner ran read-only at cutoff `2026-09-09T19:32:00Z`. Before/after counts were identical.

```text
universe = 1,491
HIGH = 1,273
HIGH same-session replacements = 341
HIGH new-session initializations = 932
MEDIUM = 0
LOW = 218
```

An exact small-universe follow-up selected two natural Run-76 source rows. No source value or lifecycle threshold was changed.

### SAME_SESSION_REPLACEMENT ranking

| Candidate | Key | Existing snapshot / revision | Confidence | Reason |
|---|---|---|---|---|
| TBLA | `TBLA/1d/2026-08-03` | 18885 / 1 | HIGH | `EXISTING_POINTER_ADVANCE_DETERMINISTIC_UNDER_FROZEN_CONTEXT` |
| NNI | `NNI/1d/2026-08-03` | 18927 / 1 | HIGH | same |
| TPL | `TPL/1d/2026-08-03` | 18961 / 1 | HIGH | same |
| META | `META/1d/2026-07-22` | 15094 / 1 | HIGH | same |

### NEW_SESSION_CANONICAL_INITIALIZATION ranking

| Candidate | Prior latest key | Prospective key | Confidence | Reason |
|---|---|---|---|---|
| DRS | `DRS/1d/2026-08-03` | `DRS/1d/2026-09-08` | HIGH | `NEW_SESSION_CANONICAL_INITIALIZATION_ADVANCES_CURRENT_STATE` |
| AAPL | `AAPL/1d/2026-09-04` | `AAPL/1d/2026-09-08` | HIGH | same |
| AMGN | `AMGN/1d/2026-09-04` | `AMGN/1d/2026-09-08` | HIGH | same |
| AWK | `AWK/1d/2026-09-04` | `AWK/1d/2026-09-08` | HIGH | same |
| CBOE | `CBOE/1d/2026-09-04` | `CBOE/1d/2026-09-08` | HIGH | same |

TBLA and DRS were selected because both came from the same unmodified Run-76 upload and remained HIGH together in the exact two-ticker Run-152 preflight. The source upload SHA-256 was `38e97f4d0a3166253acc42b445c2531777872c49da59b660850871a815a987ea`.

## D. Preflight plan/context

| Field | Value |
|---|---|
| Upload run | 152, `COMPLETED`, exactly TBLA and DRS |
| Preflight plan | 1 |
| Reserved context | 5 |
| Context status | unowned (`pipeline_run_id = NULL`) |
| Cutoff | `2026-09-09T19:55:33.682959Z` |
| Latest completed session | 2026-09-08 |
| Calendar | `swinglens-us-equities-v1` |
| Bar readiness | `daily-close-plus-15m-v1` |
| Plan evidence fingerprint | `3b4b8ae6fd86088de6b1a106487652ba5e84bfb38359aedec568922105b117ec` |
| Plan technical fingerprint | `febe19d665ae7042768fa5fac917635f400c44b232e0077be1c4cddece3933ba` |
| Initial expiry | `2026-09-09T20:25:36.620988Z` |
| Final plan state | `CANCELLED`, never consumed |

Under context 5, TBLA was HIGH with expected pointer 5815, snapshot 18885, revision 1 and evidence fingerprint `4ae9f01a…`. DRS was HIGH with expected latest snapshot 19017, revision 1, no exact 2026-09-08 pointer, and evidence fingerprint `ae6e126f…`.

The plan was valid, unexpired, persisted, and immutable before enqueue. Runtime started as one app, one supervisor-managed worker, and zero duplicate workers. Health was 200, port 8000 had one owner, IB Gateway was `READY`, the worker advertised schema 0071 and deployment HEAD `b278601`, and unrelated automatic Winner, prewarm, technical maintenance, CERI backfill, and IB auto-launch workflows were disabled.

## E. Preflight vs execution context

`start_pipeline(... transition_preflight_plan_id=1)` reloaded context 5 and reran candidate discovery before any pipeline insert. PostgreSQL returned the same cutoff instant as `2026-09-09T21:55:33.682959+02:00`; the plan-time object represented it as `2026-09-09T19:55:33.682959+00:00`.

The fingerprint canonicalizer did not normalize those equivalent offsets:

| Fingerprint | Plan-time | Reload-time |
|---|---|---|
| Aggregate evidence | `3b4b8ae6…117ec` | `b071f126…10e` |
| Aggregate technical | `febe19d6…3ba` | `cd265813…487` |
| TBLA technical | `3952fdbb…bf8` | `be991249…af` |
| DRS technical | `30ef5038…70e` | `cf256b1a…b9c` |

Candidate keys, pointer IDs/revisions, classifications, and real evidence remained unchanged. Only the cutoff's offset representation and fingerprints derived from it differed. Enqueue correctly failed closed with `PRECONDITION_CHANGED`; it did not create C2 or accept a stale plan.

Certification metrics:

```text
preflight_execution_context_mismatches = 1
stale_preflight_acceptances = 0
duplicate_pipeline_from_same_plan = 0
pipeline rows created = 0
root jobs created = 0
```

The mismatch count records the failed preflight/enqueue evidence-boundary comparison. There was no execution context because no pipeline existed.

## F. Same-session replacement evidence

Not exercised. TBLA pointer 5815 remains revision 1 and still selects snapshot 18885 for `TBLA/1d/2026-08-03`. Snapshot 18885 retains immutable hash `61b78dbfd43c0e2f33a129b85072b2f648f906d5fc5006d9bc7ad057ed009e95`.

```text
same_session_replacements = 0
same_session_ledger_events = 0
```

## G. New-session initialization evidence

Not exercised. No `DRS/1d/2026-09-08` pointer was initialized. DRS's latest session-canonical pointer remains pointer 5725 → snapshot 19017 for 2026-08-03; its older pointer 14646 → Run-151 snapshot 36949 for 2026-07-30 is also unchanged. Snapshot 19017 retains immutable hash `b900c89d2f05fb6b55b7122c81efc6a7883120e0d88883494ae8abf75db02ec5`.

```text
new_session_canonical_initializations = 0
new_session_initialization_events = 0
```

## H. Derived cross-session current-state evidence

Because no lifecycle output was written, derived current state correctly remains TBLA snapshot 18885 at 2026-08-03 and DRS snapshot 19017 at 2026-08-03. Historical session lookups still return their existing canonical rows.

```text
derived_cross_session_current_state_violations = 0 (transition not exercised)
current_pointer_violations = 0
selection_ledger_violations = 0
duplicate_selection_events = 0
```

## I. Historical immutability

All 16 protected snapshots from Runs 148–151 were hashed before migration, immediately after migration, and after the failed enqueue. Every immutable hash matched.

```text
historical_snapshot_rewrites = 0
run_148_immutable_hash_changes = 0
run_149_immutable_hash_changes = 0
run_150_immutable_hash_changes = 0
run_151_immutable_hash_changes = 0
```

CERI snapshot 10929 remains at canonical full-row hash `75971daad3f89f7f3500d616346b43d4217edd75482f8b4a65f7040dfb883d87`. Sources 867459–867465 and bars 2263479/2263504 match their baseline hashes. The pre-existing selection ledger remains one row with aggregate hash `d116d88cf5740d648f0af43b650b8172105576ca1b3180c2bb3b157e0071997e`.

## J. CERI PIT live evidence

Not exercised because no pipeline or CERI job was created. Run 152 has zero CERI snapshots, source rows, features, or descendants. The protected historical CERI controls were only read and were not repaired.

```text
post_cutoff_ceri_sources_consumed = 0 (not exercised)
post_cutoff_price_bars_consumed = 0 (not exercised)
missing_pit_provenance_inputs_used = 0 (not exercised)
```

These zeros do not constitute the required live CERI proof and therefore cannot support PASS.

## K. Temporal integrity metrics

No technical, lifecycle, CERI, HTF, RS, IBMI, or Winner execution occurred. Observed violation counters are zero but not live-exercised:

```text
lifecycle_context_violations = 0
ceri_parent_context_violations = 0
ceri_child_context_violations = 0
ceri_snapshot_context_violations = 0
future_session_inputs_consumed = 0
pre_event_ceri_reactions = 0
htf_confirmation_violations = 0
rs_alignment_violations = 0
ibmi_session_violations = 0
duplicate_job_execution = 0
```

## L. Database delta

| Area | Baseline → final | Classification |
|---|---|---|
| Alembic | 0070 → 0071 | authorized schema migration |
| Upload runs | 151 → 152 (+1) | authorized targeted canary input |
| Raw / fundamental rows | 26,549 → 26,551 (+2) / 26,471 → 26,473 (+2) | exact TBLA/DRS upload rows and normal upload scoring |
| Preflight plans | absent/0 → 1 | expected plan; final `CANCELLED`, never consumed |
| Market contexts | 4 → 5 (+1) | expected reserved context 5; remains unowned |
| Pipelines / jobs | 144 → 144 / 42,520 → 42,520 | no enqueue accepted |
| Technical / combined / ranking | unchanged at 23,882 / 23,774 / 86,910 | no pipeline outputs |
| Lifecycle evaluations / snapshots | unchanged at 195 / 26,539 | no lifecycle execution |
| Session pointers / selection ledger | unchanged at 14,646 / 1 | no selection mutation |
| CERI score/source/bar rows | unchanged at 10,734 / 867,461 / 2,262,432 | no CERI execution |
| Worker/supervisor registration | heartbeat/generation and terminal `stopping_at` updated | expected runtime bookkeeping |

`unexpected_business_mutations = 0`. The saved Run-152 upload and ignored backup are authorized operational artifacts. No historical repair, manual pointer write, manual ledger event, maintenance job, or unrelated queue drain occurred.

## M. Pipeline status / PARTIAL analysis

```text
NOT_CREATED — PREFLIGHT_CONTRACT_FAILURE
```

There is no PARTIAL result to classify. IB Gateway readiness succeeded, but `start_pipeline` rejected the plan before inserting the pipeline or root job.

## N. Post-canary regression

`SKIPPED_FAIL_FAST_PREFLIGHT_CONTRACT_FAILURE`.

The task permits the post-canary regression lane only if live certification has not failed. The mandatory shared-context gate failed before enqueue, so no post-canary suite was run and no production code was patched. The earlier certified remediation tests remain historical evidence, not a substitute for this live canary.

## O. Final runtime state

```text
all jobs terminal = YES (no canary jobs created)
app = stopped
worker = stopped
supervisor = stopped
active jobs = 0
queued jobs = 0
port 8000 listeners = 0
production Alembic = 0071_transition_preflight_plan
preflight plan 1 = CANCELLED
reserved context 5 pipeline owner = NULL
```

The app started one supervisor-managed worker at certified deployment HEAD `b278601`; no duplicate worker was started. Shutdown completed normally and unrelated observability services were not changed.

## P. Final recommendation

Do not merge the complete remediation. Normalize every timestamp entering preflight, technical, candidate, and aggregate fingerprints to a single canonical UTC representation before hashing. Add a PostgreSQL round-trip test proving that reserving in UTC, reloading under a non-UTC database/session timezone, and revalidating produces byte-identical fingerprints. Also repair the process-pool exceptional fallback discovered during read-only scanning, where `_score_tickers_pure_sequential` is invoked without `market_cutoff` after a broken pool.

After those changes are independently reviewed and certified, a new task must authorize a new preflight and one new live canary. Do not reuse cancelled plan 1 or silently regenerate it. The future canary must again exercise both TBLA-like same-session replacement and DRS-like new-session initialization under one reserved context, then complete the CERI PIT and regression gates.

```text
FINAL DUAL-MODE TRANSITION CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO
```

No production-code change, historical repair, second canary, or merge occurred.
