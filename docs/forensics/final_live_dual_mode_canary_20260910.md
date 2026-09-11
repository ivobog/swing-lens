# Final Live Dual-Mode Canary — 2026-09-10

## A. Executive verdict

```text
FINAL LIVE DUAL-MODE CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL/PREFLIGHT/LIFECYCLE REMEDIATION TO MAIN: NO
```

The one authorized production canary ran once and was not retried. Three independent blocking invariants failed:

1. `SAME_SESSION_REPLACEMENT` was not exercised. The persisted HIGH TBLA plan key was `TBLA/1d/2026-08-03`, but execution created snapshot 36950 at `TBLA/1d/2026-07-31`. Pointer 5815 therefore stayed on snapshot 18885 and no same-session ledger event was written.
2. CERI price-response children 8684 and 8685 were produced under the frozen cutoff and calendar but persisted `calculation_context_id = NULL`. The DRS CERI snapshot itself used Context 6, so this is a two-row child-context propagation failure.
3. The live IB refresh changed the full-row hashes of explicitly protected bars 2263479 and 2263504. An isolated restore of the verified backup proved that only `last_seen_at` changed, from 2026-09-09 to 2026-09-10. Production was not repaired.

The new-session transition, PostgreSQL fingerprint roundtrip, CERI source/bar PIT eligibility, immutable lifecycle snapshots, duplicate prevention, and primary context reuse all passed. They cannot override any blocking failure above.

## B. Certified baseline

| Control | Observed |
|---|---|
| Branch | `codex/adversarial-temporal-preflight-certification` |
| Execution HEAD | `a4e80fef538d47ee7e49b5f4f1cce2b3bda43999` |
| Initial worktree | clean |
| Required ancestry | `a4e80fef`, `b278601`, and `c59a655`: all ancestors |
| Alembic code head | one head, `0071_transition_preflight_plan` |
| Production Alembic head | `0071_transition_preflight_plan` |
| Database | `swinglens`, role `postgres`, `127.0.0.1:5432`, PostgreSQL 18.3 |
| Initial app / worker / supervisor | stopped / stopped / stopped |
| Initial port 8000 owner | none |
| Initial runnable jobs | queued 0 / running 0 / retrying 0 |
| Latest run / pipeline / job | 152 / 144 / 42931 |
| Plans / contexts / unowned contexts | 1 / 5 / 1 |
| Session pointers / selection ledger | 14,646 / 1 |
| Local / UTC / New York | approximately `2026-09-10T10:13:04+02:00` / `08:13:04Z` / `04:13:04-04:00` |
| Latest completed US session | 2026-09-09 |
| Daily-bar ready-at | `2026-09-09T20:15:00Z` |
| Calendar / readiness | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |

No migration or production source change was performed.

## C. Production backup

| Item | Value |
|---|---|
| Method | PostgreSQL 18 `pg_dump`, custom format |
| Path | `backups/swinglens_final_live_dual_mode_canary_20260910_0815.dump` |
| Created | `2026-09-10T08:37:28.8263177Z` |
| Size | 791,675,296 bytes |
| SHA-256 | `EA836C141DC672C89D115DE4D7D2D653634CD1E695E044B11B84986D0B13083C` |
| Verification | PASS: `pg_restore --list` exited 0 |
| Evidence manifest | `backups/swinglens_final_live_dual_mode_canary_20260910_0815.evidence.json` |

The backup was also used read-only through an isolated temporary database to compare the two protected price-bar rows. That temporary database was dropped after comparison; production was not altered by the comparison.

## D. Protected historical controls

| Control | Baseline hash | Final hash | Change |
|---|---|---|---:|
| Run 148 immutable snapshots (5) | `16317a517fe0a90145cf424231c8868b7e7aee50dfae712a1847ec5363cecec4` | same | 0 |
| Run 149 immutable snapshots (5) | `8d3dd47fdc1ebbd3177ff6fae6ad924f98eda6a33b52541163771ecd9eeabd52` | same | 0 |
| Run 150 immutable snapshots (5) | `a5f9c75bfe8f3fb1b6d7f513531d257b534258a151a207530234951bd53a1952` | same | 0 |
| Run 151 immutable snapshots (1) | `713c9c5feabe58ca031a9af7e54e68b2c7ee419d1782149daac07b45e89c02eb` | same | 0 |
| Run 152 immutable snapshots (0) | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` | same | 0 |
| CERI snapshot 10929 full row | `75971daad3f89f7f3500d616346b43d4217edd75482f8b4a65f7040dfb883d87` | same | 0 |
| Prior ledger rows 1..1 aggregate | `8bbf89096f7fe58ecd06a5e5290b1979807b0507a1183d7f598328da691983b4` | same | 0 |
| TBLA pointer 5815 | `d45c1d54b34cf0a444a2db0eae93afec0f02c00a9c1c903691b2902a7038c7ff` | same | 0 |
| Bar 2263479 | `3e7836acd9af1afe1ddbcca794c9edf4202c4904f95772df7401f0993b5f921e` | `23187262a46b571805163cd69fc3d2a9d0d522716e0970d3c16937ece98e6ffd` | 1 |
| Bar 2263504 | `98845a743d042cb618ccad7a5b1ba401251e6edac06f739280065d9e10ecbb27` | `454b31e0af4791effe3ad4519aef6902f5c9ad3c51e65d289ff973ef841bfea1` | 1 |

Run-row hashes for Runs 148–152 also remained exactly `cb94a8c5…`, `7ae6c9b0…`, `25ea0d9b…`, `d06b29ef…`, and `b2ada2a1…`. Sources 867459–867465 retained all seven baseline hashes. Snapshot 10929 retained stored evidence hash `1df165a0cbeba82db61e9545a4f820a6599a687e0b45c7df1cb8f37fdf9a8eb8`.

Backup comparison isolated the protected bar differences:

| Bar | Field | Before | After |
|---:|---|---|---|
| 2263479 | `last_seen_at` | `2026-09-09T09:31:04.697382Z` | `2026-09-10T09:31:54.083450Z` |
| 2263504 | `last_seen_at` | `2026-09-09T09:31:07.168191Z` | `2026-09-10T09:31:56.949094Z` |

OHLCV, `first_seen_at`, `revised_at`, revision count, and data hash did not change. The explicit full-row protection nevertheless failed.

## E. Candidate discovery

Certified current-state discovery was run read-only at the fixed cutoff `2026-09-10T08:13:04.357207Z`. Database counts before and after the scan were identical.

```text
universe = 1,491
HIGH = 1,273
HIGH same-session = 341
HIGH new-session = 932
MEDIUM = 0
LOW = 218
```

Reason counts were 341 deterministic existing-pointer advances, 932 new-session canonical initializations, 7 incomplete PIT inputs, and 211 non-displacing prospective snapshots.

## F. Selected dual-mode candidates

| Mode | Ticker | Planned key | Existing state | Plan confidence |
|---|---|---|---|---|
| `SAME_SESSION_REPLACEMENT` | TBLA | `TBLA/1d/2026-08-03` | pointer 5815 → snapshot 18885, revision 1 | HIGH |
| `NEW_SESSION_CANONICAL_INITIALIZATION` | DRS | `DRS/1d/2026-09-08` | exact pointer absent; derived current snapshot 19017 at 2026-08-03 | HIGH |

The exact two-row upload was Run 153. Its source CSV SHA-256 was `38E97F4D0A3166253ACC42B445C2531777872C49DA59B660850871A815A987EA`.

## G. Preflight plan/reserved context

| Field | Value |
|---|---|
| Preflight plan | 2, final status `CONSUMED` once by Pipeline 145 |
| Reserved context | 6, final owner Pipeline 145 |
| Frozen cutoff | `2026-09-10T08:13:04.357207Z` |
| Latest session | 2026-09-09 |
| Calendar / readiness | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |
| Candidate tickers | DRS, TBLA |
| Aggregate evidence fingerprint | `5c85361550758aa899c30f8f0cf8f25eda7d77029e2b1ed1c8fa7eba81c9f0b5` |
| Aggregate technical fingerprint | `6f581692bec5c2264d66ac8c9b61a544c4ccbf6185b9f615e194bbf55147b4ef` |
| Context fingerprint | `65a89bc8a3a1a818051869d4899d6edee20ebc0e7e9ca38f53a127c2421f3f50` |
| Full plan fingerprint | `50f4edf39c5ec6e2682c8986792628dd2a6dea5a51af11fa01df4a24ec784b1e` |

TBLA evidence/technical fingerprints were `96a5ad0c…` / `388af2ab…`; DRS fingerprints were `ef008d75…` / `4037b1e7…`.

## H. PostgreSQL persistence round-trip proof

The creating session committed and closed. Fresh sessions reloaded the stored rows under Europe/Zurich and America/New_York.

| Proof | Value |
|---|---|
| Original cutoff representation | `2026-09-10T08:13:04.357207+00:00` |
| Zurich reload | `2026-09-10T10:13:04.357207+02:00` |
| New York reload | `2026-09-10T04:13:04.357207-04:00` |
| Canonical representation | `2026-09-10T08:13:04.357207Z` |
| Same instant | true |
| Stored/reloaded context fingerprint | `65a89bc8…` / `65a89bc8…` |
| Stored/reloaded plan fingerprint | `50f4edf3…` / `50f4edf3…` |
| Stored/reloaded candidate fingerprints | all exact |

`postgres_roundtrip_fingerprint_match = true`.

## I. Preflight revalidation

Immediately before enqueue, Plan 2 was reserved, unexpired, not cancelled or stale; Context 6 was unowned; runnable jobs were zero; the sole worker heartbeat age was 0.108 seconds; and IB was `READY`. Both candidates remained HIGH. Observed aggregate evidence and technical fingerprints exactly matched the persisted values. No pointer ID or revision changed. Revalidation passed without regenerating the plan.

## J. Run/pipeline/job

| Item | Value |
|---|---|
| Run | 153, `COMPLETED`, two rows |
| Pipeline | 145, `PARTIAL` |
| Root job | 42962, `COMPLETED`, retry 0 / max 0 |
| Enqueued | `2026-09-10T09:30:53.226847Z` |
| Plan / context | 2 / 6 |
| Tickers | TBLA, DRS |

Exactly one pipeline and one top-level job were created. SEC readiness repair job 42963 performed one normal repair and created descendant resume job 42964 for the same Pipeline 145; this was not a second pipeline or top-level job. The full chain comprised 16 unique request keys, all terminal `COMPLETED`, with retry sum 0 and recovery sum 0.

## K. Context identity chain

Plan 2, Pipeline 145, root job 42962, resume job 42964, technical rows 33636/33637, market-regime row 93, sector row 92, lifecycle snapshots 36950/36951, every CERI batch/finalize/capture/change/alert job, and CERI snapshot 10930 all resolved to Context 6 with cutoff `2026-09-10T08:13:04.357207Z`, session 2026-09-09, and calendar `swinglens-us-equities-v1`. The pipeline/root job also carried readiness `daily-close-plus-15m-v1`.

CERI child rows 8684 and 8685 carried the correct cutoff, session, and calendar but both had null `calculation_context_id`. Therefore:

```text
preflight_execution_context_mismatches = 2
ceri_child_context_violations = 2
```

## L. Same-session replacement evidence

```text
planned key = TBLA/1d/2026-08-03
pointer before = 5815 -> snapshot 18885, revision 1
execution snapshot = 36950, key TBLA/1d/2026-07-31, non-canonical
pointer after = 5815 -> snapshot 18885, revision 1
same-session ledger events = 0
```

Snapshot 18885 retained immutable hash `1e67759875d00390920a6f2bc9e8b8284781068ab6b3b5d59fe29ef7743c3641`. The key mismatch prevented the required replacement and is a `SELECTION_SEMANTICS_FAILURE`.

## M. New-session initialization evidence

```text
planned key = DRS/1d/2026-09-08
exact pointer before = absent
new snapshot = 36951
pointer after = 14647 -> snapshot 36951, revision 1
ledger event = 2, NEW_SESSION_CANONICAL_INITIALIZATION
```

The previous DRS pointer 5725 at `DRS/1d/2026-08-03` remained on snapshot 19017, whose immutable hash stayed `4bf435a7b86f576355322d4996e4e6986d9a1499da9c704ef7136b2cd3831eda`.

## N. Derived current-state evidence

DRS historical lookup at 2026-08-03 still resolves snapshot 19017; session lookup at 2026-09-08 and derived current both resolve snapshot 36951. TBLA derived current remains snapshot 18885 because the required same-key candidate was never created. The stored lookup structure is internally consistent, so `derived_cross_session_current_state_violations = 0`; it does not satisfy the missing TBLA transition.

## O. Pointer/ledger integrity

One pointer transition occurred and exactly one matching append-only event was created. The prior ledger row aggregate remained unchanged. No pre-existing pointer row had `updated_at` inside the canary window; the only inserted/updated pointer row was new DRS pointer 14647.

```text
same_session_replacements = 0
same_session_ledger_events = 0
new_session_canonical_initializations = 1
new_session_initialization_events = 1
current_pointer_violations = 1  # required TBLA advance absent
selection_ledger_violations = 0
duplicate_selection_events = 0
```

## P. Historical immutability

All 16 immutable lifecycle snapshots from Runs 148–151 and the empty Run-152 set retained their exact aggregate hashes. `historical_snapshot_rewrites = 0`. CERI snapshot 10929 and sources 867459–867465 were unchanged. The explicit protected-price-bar control failed twice through `last_seen_at` updates, as detailed in section D.

No historical repair, pointer rewrite, or manual ledger insertion was performed.

## Q. CERI PIT live evidence

CERI snapshot 10930 (DRS, Context 6) used exactly these 23 source rows. Every row used `retrieved_at` as `known_at`, and every value was at or before the frozen cutoff:

| Source IDs | UTC `known_at` |
|---|---|
| 867411, 867412, 867413 | 2026-09-09 09:31:26.942444; 09:31:27.821592; 09:31:28.392965 |
| 867415, 867417, 867418, 867419, 867421 | 09:31:29.601446; 09:31:30.689821; 09:31:31.447586; 09:31:31.922051; 09:31:33.039160 |
| 867429, 867430, 867431, 867433 | 09:31:40.935171; 09:31:41.871202; 09:31:42.347705; 09:31:43.729231 |
| 867441, 867442, 867443, 867445 | 09:31:50.665100; 09:31:51.341842; 09:31:52.496358; 09:31:53.127019 |
| 867459, 867460, 867461, 867462, 867463, 867464 | 09:31:56.393435; 09:31:56.499459; 09:31:56.667520; 09:31:56.762760; 09:31:56.849008; 09:31:56.994388 |
| 867465 | 09:31:59.195489 |

All timestamps above are on 2026-09-09 UTC, before `2026-09-10T08:13:04.357207Z`.

| Bar | Session | First seen UTC | Revised | PIT result |
|---:|---|---|---|---|
| 2263479 | 2026-09-08 | `2026-09-09T09:31:04.697382Z` | null | PASS |
| 2263504 | 2026-09-08 | `2026-09-09T09:31:07.168191Z` | null | PASS |

Thus `post_cutoff_ceri_sources_consumed = 0`, `post_cutoff_price_bars_consumed = 0`, and `missing_pit_provenance_inputs_used = 0`. The separate null child context IDs remain blocking.

## R. Temporal-integrity metrics

```text
lifecycle_context_violations = 0
ceri_parent_context_violations = 0
ceri_child_context_violations = 2
ceri_snapshot_context_violations = 0
future_session_inputs_consumed = 0
pre_event_ceri_reactions = 0
htf_confirmation_violations = 0
rs_alignment_violations = 0
ibmi_session_violations = NOT_EXERCISED_LIVE
```

Technical lineage for both tickers recorded Context 6 and the frozen cutoff. DRS source sessions topped out at 2026-09-08/2026-09-04; TBLA at 2026-07-31/2026-09-04, all within the frozen 2026-09-09 session.

## S. Idempotency/duplicate execution

Plan 2 was consumed exactly once. It references Pipeline 145, and Context 6 has exactly that owner. Run 153 has one pipeline and one parentless top-level job. All 16 chain jobs had unique request keys, retry count 0, recovery count 0, and terminal status.

```text
stale_preflight_acceptances = 0
duplicate_pipeline_from_same_plan = 0
duplicate_job_execution = 0
```

The accepted plan was not stale at enqueue; the blocking TBLA discrepancy is between the certified prediction key and the later live lifecycle snapshot key.

## T. Production DB delta

The backup-to-final delta consists of the preflight/run rows below plus the pipeline delta. Tables not listed were unchanged in count.

| Area | Delta | Classification |
|---|---:|---|
| upload runs / raw rows / fundamental scores | +1 / +2 / +2 | `EXPECTED_CANARY_RUN` |
| preflight plans / contexts | +1 / +1 | `EXPECTED_PREFLIGHT`, `EXPECTED_CONTEXT` |
| pipeline runs / steps | +1 / +12 | `EXPECTED_PIPELINE_JOB` |
| background jobs / enqueue attempts / fanout roots | +16 / +16 / +1 | `EXPECTED_PIPELINE_JOB` |
| background workers | +1 terminal registration | `EXPECTED_RUNTIME_BOOKKEEPING` |
| IB fetch runs / items | +1 / +8 | `EXPECTED_RUN_SCOPED_RESULT` |
| price bars / revisions | +58 / +10 | `EXPECTED_RUN_SCOPED_RESULT` |
| technical scores / feature artifacts | +2 / +2 | `EXPECTED_RUN_SCOPED_RESULT` |
| combined / rankings | +2 / +10 | `EXPECTED_RUN_SCOPED_RESULT` |
| market regime / sector snapshots / sector rows | +1 / +1 / +1 | `EXPECTED_RUN_SCOPED_RESULT` |
| lifecycle evaluation runs / snapshots | +2 / +2 | `EXPECTED_RUN_SCOPED_RESULT` |
| lifecycle events / signal changes | +1 / +21 | `EXPECTED_RUN_SCOPED_RESULT` |
| session pointers / selection ledger | +1 / +1 | `EXPECTED_NEW_SESSION_POINTER_INITIALIZATION`, `EXPECTED_LEDGER_EVENT` |
| CERI company / source records | +1 / +183 | `EXPECTED_RUN_SCOPED_RESULT` |
| CERI ingestion / processing / provider telemetry | +9 / +13 / +6 | `EXPECTED_RUN_SCOPED_RESULT` |
| CERI SEC filing docs / extractions / sync states | +5 / +5 / +1 | `EXPECTED_RUN_SCOPED_RESULT` |
| CERI estimates / earnings / revision features | +48 / +12 / +24 | `EXPECTED_RUN_SCOPED_RESULT` |
| CERI derived features / build states / price-response features | +4 / +2 / +2 | `EXPECTED_RUN_SCOPED_RESULT` |
| CERI score snapshots / change events | +1 / +1 | `EXPECTED_RUN_SCOPED_RESULT` |
| CERI alert events | 0 | not touched |
| Winner tables | 0 | not touched; capture disabled |
| IBMI tables | 0 | not touched |
| protected bars 2263479/2263504 `last_seen_at` | 2 existing rows changed | `UNEXPECTED_BUSINESS_MUTATION` |

IB fetch 216 completed 8/8 requests with 98 fetched, 58 inserted, 10 revised, 30 unchanged, and 0 failures. The protected bar updates occurred in the provider's “unchanged” path. `unexpected_business_mutations = 2`.

## U. Pipeline status/PARTIAL analysis

Pipeline 145 was `PARTIAL` only because both complete combined rows carried analytical warnings: DRS had `distribution_risk` and `stage_3_distribution`; TBLA had `liquidity_warning`. The pipeline recorded two combined rows, zero incomplete rows, zero IB failures, `degraded = false`, and no failure reason. This PARTIAL cause is `ANALYTICAL_WARNING`.

The overall canary still fails independently as `SELECTION_SEMANTICS_FAILURE`, `TEMPORAL_INTEGRITY_FAILURE` (missing CERI child context IDs), and `HISTORICAL_IMMUTABILITY_FAILURE` (protected full-row bar controls).

## V. Post-canary regression

`SKIPPED_FAIL_FAST_LIVE_INVARIANT_FAILURE`.

The task permits the compact regression lane only if the live canary has not failed. No regression, patch-and-continue, source edit, second canary, or retry was performed.

## W. Final runtime state

```text
all canary jobs terminal = YES (16/16 COMPLETED; retries 0)
app = STOPPED
worker = STOPPED
supervisor = STOPPED
active jobs = 0
queued jobs = 0
retrying jobs = 0
port 8000 listeners = 0
production Alembic = 0071_transition_preflight_plan
```

Unrelated observability services and PostgreSQL were preserved. Process-only quiet-mode overrides were used; `.env` was not edited.

## X. Final merge recommendation

Do not merge. The next remediation must explain and fix, in a separately authorized task, why preflight predicts TBLA's 2026-08-03 same-session key while live execution emits 2026-07-31; why CERI price-response children omit Context 6; and whether protected price-bar `last_seen_at` is permitted or must be isolated from immutable evidence. This task performed no source patch, historical repair, retry, second canary, or merge.

```text
FINAL LIVE DUAL-MODE CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL/PREFLIGHT/LIFECYCLE REMEDIATION TO MAIN: NO
```
