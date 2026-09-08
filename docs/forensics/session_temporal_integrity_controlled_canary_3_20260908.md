# Controlled Real Canary #3 — Final Temporal & Historical Immutability Certification

## A. Executive verdict

```text
CONTROLLED CANARY #3: FAIL
SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO
```

The certified code and migration ran without a temporal violation, historical rewrite, duplicate execution, unexpected business mutation, repair, or production-code change. Nevertheless, the canary did **not** meet the explicit final certification gate: none of the five newly inserted lifecycle snapshots became the current selection, so no current-selection pointer advanced and no selection-event row was appended. The new snapshots legitimately lost deterministic precedence to their complete Run 149 predecessors, but this left the remediated pointer/ledger transition path unexercised. A PASS would therefore be unsupported.

No historical repair was performed. No merge was performed.

## B. Certified baseline

The execution branch was `codex/lifecycle-historical-immutability-remediation` at the exact certified commit `730570d94308f514ab24c15825e7743ffd4d7ee0`; that commit was current, not merely an ancestor. The tracked working tree was clean. Code had one Alembic head, `0069_lifecycle_current_selection`; production was at `0068_market_calc_context`.

PostgreSQL was 18.3 on `127.0.0.1:5432`, database `swinglens`. The app and worker were stopped, port 8000 was free, and no runnable or scheduled jobs were present. Latest IDs were Run 149, Pipeline 142, Job 42889, and calculation context 2. Local time was Europe/Zurich (UTC+02); New York was UTC-04. The latest completed US session was 2026-09-08, daily bars were ready at 16:15 New York, while the latest persisted price-bar session was initially 2026-09-04.

Selected pre-migration counts:

| Area | Pre-canary count |
|---|---:|
| Runs / pipelines / pipeline steps / contexts | 149 / 142 / 1,259 / 2 |
| Background jobs | 42,490 |
| Raw / fundamental / technical / combined rows | 26,543 / 26,465 / 23,876 / 23,768 |
| Technical feature artifacts / ranking results | 8,899 / 86,880 |
| Market Regime snapshots | 90 |
| Sector snapshots / rows | 89 / 891 |
| Lifecycle evaluation runs / snapshots / events | 191 / 26,533 / 31,958 |
| Signal changes / alerts | 148,570 / 4,022 |
| CERI ingestion / source / processing / score snapshot rows | 50,447 / 867,295 / 57,127 / 10,728 |
| Price bars / revisions / series versions | 2,262,368 / 34,244 / 2,972 |
| IBMI runs / requests / bars / features | 11 / 20 / 80 / 8 |

Winner baseline counts included 7,019 episodes, 17,973 prediction snapshots, 5,281 temporal-validity decisions, 173,995 forward outcomes, 7,978 market-data obligations, 33,836 target/stop outcomes, 8,859 eligibility decisions, 390 replays, 11 cohort generations, 569 definitions, and 6,342 statistics. These counts did not change.

## C. STI-IMM-F001–F006 gate

The gate was completed before migration. None was classified `BLOCKING`.

| Finding | Pre-deployment classification | Basis and live observation |
|---|---|---|
| STI-IMM-F001 — Market Regime | `DEFERRED_NON_BLOCKING` | The known administrative current-revision demotion is outside lifecycle immutable evidence and was placed under exact pre/post audit. The problematic path was not exercised: snapshots 90 and 91 remained current within their distinct per-run revisions. |
| STI-IMM-F002 — Sector Rotation | `DEFERRED_NON_BLOCKING` | Same boundary and audit treatment. The problematic path was not exercised: snapshots 89 and 90 remained current within their distinct per-run revisions. |
| STI-IMM-F003 — lifecycle events | `DEFERRED_NON_BLOCKING` | Event-version administrative state is separate from the immutable snapshot boundary and was audited. Lifecycle event, signal-change, and signal-alert counts were unchanged. |
| STI-IMM-F004 — CERI revisions | `DEFERRED_NON_BLOCKING` | CERI uses explicit revisions and was placed under prior-row audit. Twenty-five new catalyst events and twenty-five revision-1 rows were inserted; all had no prior revision, and no previous revision was demoted. Estimate revisions were unchanged. |
| STI-IMM-F005 — Winner outcomes | `NOT_EXERCISED` | Winner capture and automatic maturation were disabled. |
| STI-IMM-F006 — cohort generations | `NOT_EXERCISED` | Winner cohort refresh was disabled. |

`DEFERRED_NON_BLOCKING` means the adjacent path was not remediated by lifecycle commit `730570d`, but it could not mutate the lifecycle evidence under certification and was covered by pre/post mutation controls. No live observation contradicted that classification.

## D. Canary identifiers

| Field | Value |
|---|---|
| Run / Pipeline / parent Job | 150 / 143 / 42902 |
| Tickers | AAPL, AMGN, AWK, CBOE, DHT |
| Upload request | One `POST /uploads`, 2026-09-08T21:27:10.548557Z |
| Pipeline request | One `POST /runs/150/pipeline`, 2026-09-08T21:28:11.256310Z |
| Calculation context | 3 (exactly one) |
| Cutoff | 2026-09-08T21:28:11.548525Z (23:28:11.548525 Europe/Zurich) |
| Frozen session | 2026-09-08 |
| Calendar / readiness | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |
| Cutoff reason | `FULL_PIPELINE_FROZEN_AT_ENQUEUE` |
| Pipeline / job status | PARTIAL / COMPLETED |

The original upload and the Run 149 reused file had SHA-256 `CC7B0525723A32FA7486F48C76F84CDD0E9D184571A74BE61AC3257C077BEAE3`. Only the requested five tickers were uploaded; the production upload path naturally created its one saved copy.

## E. Three-run comparison

| Metric | Run 148 / Pipeline 141 | Run 149 / Pipeline 142 | Run 150 / Pipeline 143 |
|---|---:|---:|---:|
| Ticker count | 5 | 5 | 5 |
| Frozen-context violations | 10 | 0 | 0 |
| Lifecycle context violations | 5 | 0 | 0 |
| CERI context violations | 5 | 0 | 0 |
| Historical lifecycle rewrites | 0 | 5 (Run 148 rows) | 0 |
| Run 148 immutable hash changes caused | not characterized by immutable hash at execution | 5 | 0 |
| Run 149 immutable hash changes caused | N/A | 0 | 0 |
| Future-session inputs | 0 | 0 | 0 |
| Pre-event CERI reactions | 0 | 0 | 0 |
| HTF violations | 0 | 0 | 0 |
| RS violations | 0 | 0 | 0 |
| Duplicate execution | 0 | 0 | 0 |
| Unexpected DB mutations | 0 | 5 | 0 |
| Pipeline / job status | PARTIAL / COMPLETED | PARTIAL / COMPLETED | PARTIAL / COMPLETED |
| Historical repair | No | No | No |

Run 150 reached zero for every violation and mutation metric. It still fails certification because its mandatory new pointer/ledger transition coverage was zero.

## F. Frozen-context evidence

Context 3 was created once at enqueue and persisted the exact cutoff, session, calendar, readiness version, and reason listed above. Technical scores, Market Regime, lifecycle snapshots, the CERI parent request, 13 CERI child jobs, their handlers, and five persisted CERI score snapshots all resolved to context 3. Parent and child payload cutoffs, session, and calendar values matched the original context.

| Control | Result |
|---|---:|
| Frozen-context persistence violations | 0 |
| Lifecycle context violations | 0 |
| Market Regime context violations | 0 |
| Sector context violations | 0 |
| CERI parent payload/handler violations | 0 |
| CERI child payload/handler violations | 0 |
| CERI snapshot context violations | 0 |
| Future source/session inputs consumed | 0 |

Fourteen Sep 8 PriceBars were fetched after the 21:28:11Z cutoff (IDs 2263441–2263454). They were correctly excluded from technical calculation because they were unavailable at the frozen cutoff. The calculation therefore used Sep 4 technical source sessions, a documented one-session US-market lag across the Sep 7 Labor Day closure.

## G. Lifecycle immutability evidence

The canary inserted five immutable snapshots, IDs 36944–36948. Each retained context 3, the exact frozen cutoff, input session 2026-09-08, and the certified calendar, but had actual `data_as_of_date` 2026-09-04. All five were context-incomplete because they had no Sector Rotation link (`sector_rotation_snapshot_id` null); their warnings included `MISSING_OPTIONAL_CONTEXT` and `MISSING_SECTOR_ROTATION`.

Sector snapshot 90 had outer `as_of_date` 2026-09-08 while its technical inputs used the causally available Sep 4 bars. Lifecycle snapshots keyed to Sep 4 could not link that Sep 8 sector snapshot. Deterministic precedence therefore retained each complete Run 149 predecessor.

| Ticker | Previous/current Run 149 snapshot | New Run 150 snapshot | Post-run pointer | Pointer advanced | Selection event |
|---|---:|---:|---:|---|---|
| AAPL | 36939 | 36944 | 36939 rev. 1 | No | None |
| AMGN | 36940 | 36945 | 36940 rev. 1 | No | None |
| AWK | 36941 | 36946 | 36941 rev. 1 | No | None |
| CBOE | 36942 | 36947 | 36942 rev. 1 | No | None |
| DHT | 36943 | 36948 | 36943 rev. 1 | No | None |

The no-op selection evaluation refreshed the pointer's `selection_decision_json` with `changed:false`, previous equal to selected, and the deterministic precedence score. It did not change selected snapshot ID, revision, reason, or selection timestamp, and did not mutate the predecessor snapshot.

There are 14,645 unique pointer scopes, zero duplicate pointer scopes, zero duplicate selected snapshots, and zero ledger rows. Because no selection transition occurred, there was no malformed or missing event for a valid transition: `current_pointer_violations=0`, `selection_ledger_violations=0`, and `duplicate_selection_events=0`. Separately, mandatory transition coverage failed: `current_selection_advances=0`, `selection_events_appended=0`.

No lifecycle transitions, signal changes, alerts, or Winner episodes were created by Run 150. Downstream historical reconstruction was therefore not exercised.

## H. Migration 0069 evidence

Before application, `20260908_0069_lifecycle_current_selection.py` was inspected against the certified design. Its upgrade:

- adds `setup_signal_snapshot_current_selections` as the unique current pointer;
- adds append-only, idempotently constrained `setup_signal_snapshot_selection_events`;
- bootstraps pointers deterministically from the legacy current flag with reason `LEGACY_CURRENT_FLAG_BOOTSTRAP`;
- records `historical_intervals_reconstructed=false` and fabricates no historical selection interval or timestamp;
- does not update any `setup_signal_snapshots` row;
- replaces the legacy partial unique canonical index with a non-unique decision-time evidence index; and
- supplies the certified uniqueness, foreign-key, ordering, and concurrency constraints.

The downgrade is explicitly lossy and can rewrite the legacy administrative flag; no downgrade was run. The repository had one Alembic head. Exactly `0068_market_calc_context -> 0069_lifecycle_current_selection` was applied. The database then reported 0069, both structures existed, all inspected constraints/indexes were present, 14,645 pointers were deterministically bootstrapped, the event ledger was empty, and no duplicate scope or selected-snapshot reference existed.

The pre-migration backup is `backups/session_temporal_canary_3_20260908/swinglens_pre_controlled_canary_3_20260908.dump`, created with PostgreSQL 18 `pg_dump`. It is 791,264,117 bytes with SHA-256 `E05044BC6F9956A64B875C2B8649D2CB98CBBC8E79FEFEA96EC2112120ED0432`. `pg_restore --list` succeeded with exit 0 and produced 1,260 TOC lines. Credential-redacted metadata and evidence manifests remain in the ignored backup directory and were not committed.

Run 148 and Run 149 immutable hashes were unchanged immediately after migration.

## I. Technical evidence

Technical score IDs 33630–33634 all persisted context 3, the exact cutoff, input session 2026-09-08, and the certified calendar. Actual ticker, SPY, QQQ, sector, and trade source sessions were all 2026-09-04 and never exceeded the frozen session. The calculated trading-session lag was one for every ticker. Row counts were AAPL 797, AMGN 787, AWK 783, CBOE 779, and DHT 784.

Temporal cache artifacts 8900–8904 used input session 2026-09-08 with their own new input signatures and series-version identities. No future bar was consumed. Thus `future_session_inputs_consumed=0` and all technical context/source-lineage controls passed.

## J. CERI evidence

The parent and 13 child jobs were 42902–42915. Every child persisted context 3 with the exact cutoff, input session, and calendar. Persisted CERI score snapshots 10924–10928 matched the same context. No parent, child, handler, descendant, or snapshot divergence was found.

| Ticker | Feature | Event time (New York) | Effective / reaction start | Prior reference | Window/bar evidence | Causal result |
|---|---:|---|---|---|---|---|
| AAPL | 8678 | 2026-09-08 15:56 | Sep 8 / Sep 9 09:30 | Not yet resolvable | window not elapsed; no reaction bars | Pass |
| AMGN | 8679 | 2026-09-08 13:35:17 | Sep 8 / Sep 9 09:30 | Not yet resolvable | window not elapsed; no reaction bars | Pass |
| AWK | 8680 | 2026-09-07 00:57:16 | Sep 8 / Sep 8 09:30 | Sep 4 | H1 Sep 8, H3 Sep 10; bars 2263441/2263442/2263449/2263450 | Pass |
| CBOE | 8681 | 2026-09-07 10:15 | Sep 8 / Sep 8 09:30 | Sep 4 | H1 Sep 8, H3 Sep 10; bars 2263441/2263442/2263451/2263452 | Pass |
| DHT | reused 8674 | earlier evidence reused | existing policy result | existing evidence | no new price-response row | Not newly exercised |

All newly evaluated rows used `daily-open-causal-v1`, and every reaction open was at or after its event timestamp. `pre_event_ceri_reactions=0`. CERI produced 20 ingestion runs, 40 source records, 25 catalyst events, 25 first revisions, 120 revision features, 28 processing runs, 15 derived features, four price-response rows, and five score snapshots. It created no change or alert event.

## K. HTF / RS / IBMI

HTF confirmation passed for all five artifacts. Sep 8 lies in the incomplete exchange-calendar week ending Sep 11; the last completed market week ended Sep 4, which was the consumed higher-timeframe source week. This was exchange-calendar validation, not a plain `W-FRI` assumption. `htf_confirmation_violations=0`.

RS inputs aligned stock, benchmark, and mapped sector on the canonical actual source session, Sep 4; unsupported mappings used the recorded broad-market fallback. `rs_alignment_violations=0`.

IBMI was not requested or executed and its counts were unchanged. STI-F015 and STI-F018 are `NOT EXERCISED LIVE`; `ibmi_session_violations=0 where exercised`.

## L. PARTIAL analysis

The pipeline was PARTIAL solely because of analytical warnings. `degraded=false`, `failure_reason=null`, incomplete rows were zero, IB failures were zero, and every one of the 12 pipeline steps completed.

| Ticker | Reasons | Classification |
|---|---|---|
| AAPL | balance_sheet_stress; box_failure; failed_breakout; value_trap_risk | `ANALYTICAL_WARNING` |
| AMGN | balance_sheet_stress; distribution_risk; value_trap_risk | `ANALYTICAL_WARNING` |
| AWK | balance_sheet_stress; earnings_quality_risk; liquidity_buffer_weak; poor_cash_conversion; value_trap_risk | `ANALYTICAL_WARNING` |
| CBOE | distribution_risk | `ANALYTICAL_WARNING` |
| DHT | distribution_risk; earnings_quality_risk; poor_cash_conversion; value_trap_risk | `ANALYTICAL_WARNING` |

There was no provider, operational, temporal-integrity, historical-immutability, or unknown PARTIAL cause.

## M. Worker/job evidence

The successful controlled runtime contained one app (PID 14604), one supervisor (PID 22996, instance `d0cebd5f97704697b6b87b3f4a706faa`), and one durable worker (PID 19316, instance `0e1ac77398f84431ac0db675b69ee60b`). Port 8000 had one owner. `/health` returned 200; `/ready` returned 200 with only the optional cached IB status degraded, while the direct IB status was READY and API-connected. EODHD and the configured SEC client were healthy; DNS/TLS checks succeeded and the live SEC work completed.

The parent job started at 2026-09-08 23:28:12.282570+02 and completed at 23:31:45.503525+02. Pipeline 143 ran from 23:28:13.302109+02 through 23:31:45.417890+02. Its 13 CERI children all completed terminally with zero recorded retries, recoveries, or errors. Five children had multiple claim attempts (2–4) solely from expected dependency deferral before prerequisites became terminal; each request key and terminal effect was unique. That is not duplicate handler execution: `duplicate_job_execution=0`. No job was stuck or left runnable.

One initial startup command was rejected by Settings validation before launch because disabling technical series-version maintenance is incompatible with temporal artifact caching. No canary row or job existed at that point. The invalid override was removed; required temporal maintenance remained enabled. No production code was changed.

Effective quiet-mode overrides disabled capture handoff, Winner pipeline capture/maturation/cohort refresh, market-data prewarm, CERI backfill, and IB Gateway auto-launch. Required CERI pipeline stages remained enabled. After evidence capture, the canonical stop command returned the app, supervisor, and worker to STOPPED; port 8000 had no listener, active and queued jobs were zero, and PostgreSQL remained running by canonical policy.

## N. Database delta

| Area | Pre | Post | Delta | Classification |
|---|---:|---:|---:|---|
| Runs / pipelines / steps / contexts | 149 / 142 / 1,259 / 2 | 150 / 143 / 1,271 / 3 | +1 / +1 / +12 / +1 | expected canary rows |
| Background jobs | 42,490 | 42,504 | +14 | expected parent/child canary rows |
| Raw / fundamental / technical / combined | 26,543 / 26,465 / 23,876 / 23,768 | 26,548 / 26,470 / 23,881 / 23,773 | +5 each | expected canary rows |
| Technical artifacts / rankings | 8,899 / 86,880 | 8,904 / 86,905 | +5 / +25 | expected canary rows |
| Market Regime | 90 | 91 | +1 | expected canary row |
| Sector snapshots / rows | 89 / 891 | 90 / 896 | +1 / +5 | expected canary rows |
| Lifecycle evaluations / snapshots | 191 / 26,533 | 193 / 26,538 | +2 / +5 | expected canary rows |
| Current pointers | 0 (pre-migration) | 14,645 | +14,645 | expected migration bootstrap |
| Selection-event ledger | 0 | 0 | 0 | no valid selection change; certification coverage failure |
| Lifecycle events / signal changes / alerts | 31,958 / 148,570 / 4,022 | unchanged | 0 | expected no transition |
| CERI ingestion / source | 50,447 / 867,295 | 50,467 / 867,335 | +20 / +40 | expected canary rows |
| CERI catalyst / revisions / revision features | 18,568 / 18,907 / 186,198 | 18,593 / 18,932 / 186,318 | +25 / +25 / +120 | expected canary rows |
| CERI processing / derived / price response / score | 57,127 / 23,838 / 5,787 / 10,728 | 57,155 / 23,853 / 5,791 / 10,733 | +28 / +15 / +4 / +5 | expected canary rows |
| CERI changes / alerts | 9,855 / 1,057 | unchanged | 0 | expected no transition |
| Price bars / revisions / series versions | 2,262,368 / 34,244 / 2,972 | 2,262,382 / 34,244 / 2,972 | +14 / 0 / 0 | expected provider rows |
| IBMI | 11 / 20 / 80 / 8 | unchanged | 0 | not exercised |
| Winner temporal/outcome/cohort families | baseline above | unchanged | 0 | disabled/not exercised |

Worker/supervisor registrations, heartbeats, status transitions, pointer decision-JSON refreshes, and artifact-cache activity were expected runtime bookkeeping. `unexpected_business_mutations=0`.

## O. Historical control evidence

Immutable evidence hashes include all certified evidence and temporal-lineage fields and exclude only the legacy `superseded_by_snapshot_id` administrative field. They include `is_canonical` because it was decision-time evidence in these historical rows.

| Run | Snapshot | Ticker | SHA-256 before | After migration / mid-run / final |
|---:|---:|---|---|---|
| 148 | 36934 | AAPL | `5c0251265fc27e993fa1cb02332204a7ce168ee9e71b54ce79c5ed187bd7a5b7` | unchanged |
| 148 | 36935 | AMGN | `29d2c7413fc5a9431536227880abb5070b790bd80fb08862f998743c41aa8d9b` | unchanged |
| 148 | 36936 | AWK | `5176e2900c92e7ba3854efb3e949f72033422172d78aaa7e6646ba81dea04b9e` | unchanged |
| 148 | 36937 | CBOE | `3d1164996762f23e53a2273830af8cc07a2789cefd8b75b140cf6834f05a99fb` | unchanged |
| 148 | 36938 | DHT | `fddd1f8cd61d446fe4cbc56a434efed1d00f4a187820235b345eb38045773eab` | unchanged |
| 149 | 36939 | AAPL | `59eb7793566d6fcedd11c21dafe12c35189ae3f48380ebc29df04a72ad65d722` | unchanged |
| 149 | 36940 | AMGN | `2c93ebb8a05d64f0c1649a43c1f5ceb41385a75f976837685648d084640e867a` | unchanged |
| 149 | 36941 | AWK | `4b6433d9f7ca594e4964f48419b100d1bccd10b7e068cc827499825188ae519e` | unchanged |
| 149 | 36942 | CBOE | `0cf54cb4c47966cd892874535fcff46345e0d79b31c7864bb163e61db47116b2` | unchanged |
| 149 | 36943 | DHT | `d7630cab7da62d5df1928b695a2935c61cf138b5e2dde439976bf1e2923281a6` | unchanged |

Raw full-row MD5 controls for the five Run 148 lifecycle rows and five Run 148 CERI score snapshots also remained identical to their pre-canary controls. Final results: `historical_snapshot_rewrites=0`, `run_148_immutable_hash_changes=0`, and `run_149_immutable_hash_changes=0`.

## P. Post-canary regression evidence

`NOT RUN (FAIL-FAST: mandatory pointer/ledger advance not exercised).`

The task authorizes the compact regression only if the live canary has not already failed. Running it after this coverage failure would contradict that instruction and could not convert the live verdict to PASS. Evidence JSON parsing and repository hygiene checks are delivery validation, not the prohibited post-canary production regression suite.

## Q. Final recommendation

Do not merge the complete temporal remediation to main on this certification. The system preserved one frozen calculation context and kept Runs 148 and 149 immutable, and every enumerated zero-tolerance metric is zero. However, the decisive pointer advance and append-only ledger insertion were not exercised by the live canary. A future certification must arrange a causally complete candidate that legitimately outranks the current pointer, then demonstrate one deterministic pointer advance and one idempotent selection event per ticker without changing the historical evidence rows.

```text
CONTROLLED CANARY #3: FAIL
SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO
```

No historical repair. No merge. No production-code change.
