# Session & Temporal Integrity Controlled Production Canary #2 — 2026-09-08

## A. Executive verdict

```text
CONTROLLED CANARY #2: FAIL
SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO
```

The remediated frozen-context chain succeeded for every newly produced technical, lifecycle, Market Regime, Sector Rotation, CERI job, handler, and score-snapshot path. The canary still fails a separate zero-tolerance condition: Run 149 canonicalization updated all five Run 148 lifecycle control rows. Their `is_canonical` values changed from `true` to `false`, their previously-null `superseded_by_snapshot_id` values became 36939–36943, and all five row hashes changed. The five Run 148 CERI control snapshots remained unchanged, so 5 of the required 10 Run 148 evidence rows changed.

This is an unexpected historical business mutation under the explicit canary contract. No repair, rollback, second run, migration, or production-code change was attempted after detection.

## B. Certified code baseline

| Item | Evidence |
| --- | --- |
| Branch | `codex/session-temporal-integrity-canary-remediation` |
| Canary execution HEAD | `dfd0f8bfd9b7ad09f7f1a69a02896599d94f3c7e` |
| Certified remediation HEAD | `dfd0f8bfd9b7ad09f7f1a69a02896599d94f3c7e` (exact execution HEAD) |
| Required ancestors | `1912eb4e5a1fb371e3898cb8971afd4e41cce048` and Canary #1 evidence `545292d55def3cf0ba825ff95b5ccf25bcd2db7a` verified ancestors |
| Initial worktree | Clean |
| Code / production Alembic | Single head `0068_market_calc_context` |
| Database | PostgreSQL 18.3, database `swinglens`, `127.0.0.1:5432` |
| Baseline clock | `2026-09-08T15:06:05.998443Z`; latest completed and latest bar session `2026-09-04` |
| Initial runtime | App and worker stopped; port 8000 unowned; zero runnable jobs |

A fresh custom-format `pg_dump` recovery point was made before any canary write:

- Path: `backups/session_temporal_canary_2_20260908/swinglens_pre_controlled_canary_2.dump`
- Size: 791,180,353 bytes
- Metadata timestamp: `2026-09-08T15:23:01.7250353Z`
- Manifest timestamp: `2026-09-08T17:23:01.410426+02:00`
- Commit: `dfd0f8bfd9b7ad09f7f1a69a02896599d94f3c7e`
- Alembic: `0068_market_calc_context`
- `pg_restore --list`: succeeded, 1,260 lines
- Database fingerprint in metadata was credential-redacted.

The canonical lifecycle started exactly one web app (PID 22616), one supervisor (PID 20012), and one durable worker (PID 20452; instance `4217ee7d4c6c4e0089646b1564a49d48`). Port 8000 had one owner. Health was good; readiness was initially degraded only by optional IB availability. EODHD, SEC, and network prerequisites were available without exposing credentials. IB was ready by pipeline execution, but no fetch was needed.

Process-level overrides disabled Winner capture/maturation/cohort refresh, market prewarm, CERI backfill, IB Gateway auto-launch, and setup capture handoff. Required pipeline, lifecycle, CERI provider/batch/capture/alert, Market Regime, and Sector Rotation stages remained enabled. The `.env` file was not edited.

## C. Canary identifiers

| Field | Value |
| --- | --- |
| Run | 149 (`COMPLETED`, five uploaded rows) |
| Pipeline | 142 (`PARTIAL`) |
| Full-pipeline job | 42876 (`COMPLETED`) |
| Tickers | AAPL, AMGN, AWK, CBOE, DHT |
| Context | 2 (the sole Run 149 context) |
| Cutoff | `2026-09-08T17:32:05.731953+02:00` / `2026-09-08T15:32:05.731953Z` |
| Latest completed session | `2026-09-04` |
| Daily bar ready at | `2026-09-04T16:15:00-04:00` |
| Calendar | `swinglens-us-equities-v1` |
| Bar-readiness policy | `daily-close-plus-15m-v1` |
| Cutoff reason | `FULL_PIPELINE_FROZEN_AT_ENQUEUE` |
| Queued / started / completed | `17:32:05.668590` / `17:32:06.879028` / `17:32:55.219568` +02:00 |

The exact five-ticker Run 148 CSV was reused through the standard `/uploads` endpoint. Exactly one `POST /runs/149/pipeline` request was made with `ALLOW_CACHE_FALLBACK`; no second pipeline was enqueued.

## D. Run 148 comparison

| Metric | Canary #1 / Run 148 | Canary #2 / Run 149 |
| --- | ---: | ---: |
| Tickers | 5 | 5 |
| Frozen context count | 1 | 1 |
| Lifecycle context violations | 5 | 0 |
| CERI snapshot context violations | 5 | 0 |
| CERI child-job context violations | not originally tracked / STI-CANARY-F003 | 0 |
| Future-session inputs consumed | 0 | 0 |
| Pre-event CERI reactions | 0 | 0 |
| Unexpected DB mutations | 0 | 5 historical Run 148 row updates |
| Duplicate job execution | 0 | 0 |
| Pipeline status | PARTIAL | PARTIAL |
| Job status | COMPLETED | COMPLETED |
| Historical repair | No | No |
| Control evidence unchanged | n/a | **No: 5/10 hashes changed** |

The remediation eliminated all ten frozen-context persistence failures seen in Run 148. The result nevertheless cannot pass because the comparative control was not preserved.

## E. Context chain

The exercised chain was internally identical to context 2:

```text
Pipeline 142 / market_calculation_contexts.id 2
  = technical scores 33625–33629
  = Market Regime snapshot 90
  = Sector Rotation snapshot 89
  = lifecycle capture/evaluation context
  = lifecycle snapshots 36939–36943
  = CERI jobs 42877–42889 payload context
  = CERI capture/change/alert processing cutoff
  = CERI snapshots 10919–10923
```

All 13 CERI jobs carried context ID 2, the exact cutoff, session `2026-09-04`, and calendar version. Provider/normalize/feature/finalize jobs 42877–42886 were children of full-pipeline job 42876. Capture 42887, change detection 42888, and alert rebuild 42889 formed the child continuation chain. Persisted processing runs 57237–57239 retained the exact cutoff, while score snapshot lineage persisted the full context identity. No handler substituted a wall-clock-derived context.

## F. Lifecycle evidence

Lifecycle evaluation runs 256 and 257 each completed with zero failures. Run 257 canonicalized five snapshots and produced zero signal changes, zero state transitions, and zero alerts.

| Ticker | Snapshot | Context | Cutoff | Data/input session | Calendar |
| --- | ---: | ---: | --- | --- | --- |
| AAPL | 36939 | 2 | exact pipeline cutoff | 2026-09-04 | `swinglens-us-equities-v1` |
| AMGN | 36940 | 2 | exact pipeline cutoff | 2026-09-04 | same |
| AWK | 36941 | 2 | exact pipeline cutoff | 2026-09-04 | same |
| CBOE | 36942 | 2 | exact pipeline cutoff | 2026-09-04 | same |
| DHT | 36943 | 2 | exact pipeline cutoff | 2026-09-04 | same |

Every `source_lineage_json.temporal_lineage` independently repeated context 2, the exact cutoff, ticker latest session `2026-09-04`, and the calendar. Lifecycle context violations were 0/5. Five `CANONICAL_REVISION` events (44507–44511) linked the new snapshots to Run 148 snapshots 36934–36938; that link caused the fatal historical updates described in section N.

## G. CERI parent/child evidence

| Job range/type | Count | Payload context | Handler/output result |
| --- | ---: | --- | --- |
| 42877/79/81/83 provider batches | 4 | context 2 / exact cutoff/session/calendar | Completed; 20 scoped ingestion runs; zero failures |
| 42878/80/82/84 normalize batches | 4 | same | Completed; zero failures |
| 42885 feature batch | 1 | same | Five tickers processed; 72 features evaluated, 40 current features updated, zero failures |
| 42886 finalizer | 1 | same | Completed; created capture job 42887 |
| 42887 capture | 1 | same | Processing 57237 used exact cutoff; five snapshots, three changes, zero failures |
| 42888 change detection | 1 | same | Processing 57238 used exact cutoff; three existing change IDs deduplicated |
| 42889 alert rebuild | 1 | same | Processing 57239 used exact cutoff; three inputs deduplicated; no alert created |

CERI snapshots 10919–10923 each persisted context 2, the exact cutoff, as-of session `2026-09-04`, calendar `swinglens-us-equities-v1`, and a matching `evidence_lineage_json.temporal_lineage`. Each compared to its corresponding Run 148 snapshot 10914–10918. Parent, handler, child-job, child-handler, and snapshot context mismatches were all zero.

All 14 background jobs (the full pipeline plus 13 CERI jobs) had one `CREATED` enqueue-attempt record, zero retries, zero recoveries, no error, and terminal `COMPLETED` status. Feature/finalizer claims that occurred before dependencies were ready were explicitly deferred and later executed once; they were not duplicate handler executions.

## H. Technical evidence

Five new technical scores (33625–33629) persisted context 2, the exact cutoff, session `2026-09-04`, and the calendar. All had high confidence, sufficient history, HTF data, benchmark data, and relative-strength data. Their input frames contained 779–797 rows and ended on `2026-09-04`.

For AAPL, AMGN, AWK, CBOE, and DHT, both `ADJUSTED_LAST` and `TRADES` series ended on `2026-09-04`; SPY and QQQ also ended on that session. `debug_json.temporal_lineage` reported zero ticker/trades/benchmark/sector session lag and no source later than the frozen session. No new price bars, revisions, or series versions were written. Future-session inputs consumed: zero.

Artifacts 8895–8899 retained temporal cache identity `input_as_of_session=2026-09-04` and existing series versions. Their `last_used_at` metadata was refreshed by this run; content and table count were unchanged. This is classified as expected cache bookkeeping, not a rewritten HTF output.

## I. CERI causal evidence

The five CERI score snapshots referenced existing causal reaction features 8670–8674. All use `daily-open-causal-v1`:

| Ticker | Event/effective session | Prior | Reaction start/session | H1 / H3 | Result |
| --- | --- | --- | --- | --- | --- |
| AAPL | `2026-09-03T14:29:23-04:00` / 09-03 | 09-03 | 09-04 | 09-04 / 09-09 | causal; H3 unavailable/null |
| AMGN | `2026-09-03T11:30:06-04:00` / 09-03 | 09-03 | 09-04 | 09-04 / 09-09 | causal; H3 unavailable/null |
| AWK | `2026-09-03T14:23:00-04:00` / 09-03 | 09-03 | 09-04 | 09-04 / 09-09 | causal; H3 unavailable/null |
| CBOE | `2026-08-25T07:45:06-04:00` / 08-25 | 08-24 | 08-25 | 08-25 / 08-27 | causal |
| DHT | `2026-09-03T10:20:01-04:00` / 09-03 | 09-03 | 09-04 | 09-04 / 09-09 | causal; H3 unavailable/null |

The regular-session events reacted from the next session; the pre-market CBOE event reacted at that session's 09:30 New York open. The 18 referenced stock/benchmark bars ranged from 2026-08-25 through 2026-09-04, with none after the frozen session. The 120 referenced revision features had zero `known_at` or `reference_at` values after the cutoff. No new price-response feature was written and pre-event reactions were zero.

## J. HTF / RS / IBMI

Weekly HTF was exercised for all five tickers. The completed market week and selected HTF week both ended `2026-09-04`; all artifacts recorded `htf_confirmed=true`, `htf_latest_week_excluded=false`, and `htf_insufficient_confirmed_history=false`. HTF violations: zero.

Relative Strength was exercised for all five tickers. Their canonical latest common ticker, trades, benchmark, and outer sector session was `2026-09-04`; missing benchmark overlap was false and session lags were zero. Four unsupported detailed sector labels and AWK's missing XLU data fell back explicitly to broad-market RS. RS temporal violations: zero.

IB Market Intelligence was **NOT EXERCISED LIVE**. Pipeline IB readiness was `READY`, but the frozen ranges were already current, so planned/executed requests were 0/0 and all IBMI counts remained unchanged. STI-F015/STI-F018 and IBMI session violations are therefore `NOT EXERCISED LIVE` / zero where exercised.

CERI cooldown was **NOT EXERCISED LIVE** because no new alert was emitted. The alert rebuild itself ran, but all three candidate inputs were deduplicated.

## K. Pipeline PARTIAL analysis

Pipeline 142 completed all 12 declared steps with zero step retries/errors. It produced five complete combined rows and 25 ranking rows, with zero incomplete rows, zero IB failures, and `degraded=false`. `PARTIAL` came solely from five analytical warning rows:

| Ticker | Warning/error row | Classification |
| --- | --- | --- |
| AAPL | balance-sheet stress, box/failed-breakout, value-trap risk | `ANALYTICAL_WARNING` |
| AMGN | balance-sheet stress, distribution risk, value-trap risk | `ANALYTICAL_WARNING` |
| AWK | balance-sheet/earnings-quality/liquidity/cash-conversion/value-trap warnings | `ANALYTICAL_WARNING` |
| CBOE | distribution risk | `ANALYTICAL_WARNING` |
| DHT | distribution/earnings-quality/cash-conversion/value-trap warnings | `ANALYTICAL_WARNING` |

The Sector Rotation snapshot additionally reported expected low-confidence/single-name risk-off analytical warnings. There was no provider, job, worker, temporal-integrity, operational, or unknown partial cause. The historical mutation is a separate post-run certification failure.

## L. Worker/job evidence

The single worker instance `4217ee7d4c6c4e0089646b1564a49d48` (PID 20452) claimed the canary jobs. Logs show its instance ID at claims and progress checkpoints. Heartbeat remained current; there was no stale lease or recovery. All canary jobs were terminal before shutdown, and no unrelated runnable job was consumed.

After evidence collection, `pwsh .\swinglens.ps1 stop` quiesced and stopped the worker/supervisor/app. Final checks showed port 8000 listeners = 0, matching app/worker/supervisor processes = 0, runnable jobs = 0, and canary nonterminal jobs = 0. Alembic remained `0068_market_calc_context`.

## M. Database delta

| Category | Before | After | Delta / classification |
| --- | ---: | ---: | --- |
| Upload / raw rows | 148 / 26,538 | 149 / 26,543 | +1 / +5 expected Run 149 |
| Fundamentals / technicals | 26,460 / 23,871 | 26,465 / 23,876 | +5 / +5 expected |
| Technical artifacts | 8,899 | 8,899 | no rows; five `last_used_at` bookkeeping updates |
| Combined / rankings | 23,763 / 86,855 | 23,768 / 86,880 | +5 / +25 expected |
| Pipelines / steps / contexts | 141 / 1,247 / 1 | 142 / 1,259 / 2 | +1 / +12 / +1 expected |
| Background jobs | 42,476 | 42,490 | +14 expected (pipeline + 13 CERI) |
| Market Regime | 89 | 90 | +1 expected |
| Sector snapshots / rows | 88 / 886 | 89 / 891 | +1 / +5 expected |
| Lifecycle evaluation / snapshots / events | 189 / 26,528 / 31,953 | 191 / 26,533 / 31,958 | +2 / +5 / +5 expected new rows |
| Lifecycle changes / alerts | 148,570 / 4,022 | unchanged | none |
| CERI ingestion / source rows | 50,427 / 867,262 | 50,447 / 867,295 | +20 / +33 expected provider work |
| CERI catalysts / revisions | 18,538 / 18,877 | 18,568 / 18,907 | +30 / +30 expected normalization |
| CERI estimates / earnings / guidance | 149,421 / 11,021 / 57,027 | unchanged | deduplicated/reused |
| CERI revision / derived / reaction feature counts | 186,198 / 23,838 / 5,787 | unchanged | current derived rows updated as expected; no new reaction |
| CERI processing / snapshots / changes / alerts | 57,099 / 10,723 / 9,852 / 1,057 | 57,127 / 10,728 / 9,855 / 1,057 | +28 / +5 / +3 / 0 expected |
| Price bars / revisions / series versions | 2,262,368 / 34,244 / 2,972 | unchanged | no market-data mutation |
| IBMI runs / requests / bars / features | 11 / 20 / 80 / 8 | unchanged | not exercised |
| Winner families | baseline counts | unchanged | automatic workflows disabled |
| Run 148 lifecycle snapshots | five existing rows | five updated rows | **unexpected historical mutation; fatal** |

All ordinary additions and mutable current-state/cache updates were scoped to the one canary. The five Run 148 lifecycle control-row updates are the only identified unexpected business mutations and are sufficient to fail the canary.

## N. Historical safety

Run 148 was not repaired, but it was not unchanged.

| ID / ticker | Baseline hash | Post-canary hash | Historical change |
| --- | --- | --- | --- |
| 36934 AAPL | `69f5bbca94cab12971f135019ac1c3f1` | `9985493c2c465c2eb92be5a222aca5df` | canonical true→false; superseded by 36939 |
| 36935 AMGN | `8992fc1b66a0cc26b8cea0b70a6c1c31` | `7764b234b876e16d9f72148e5a99403c` | canonical true→false; superseded by 36940 |
| 36936 AWK | `2e36d1b837c031b0cfc41f86c10d8823` | `700407e985965cdfe7eb984a04752d8b` | canonical true→false; superseded by 36941 |
| 36937 CBOE | `ad4e010ff08feb0007bde997f21c7815` | `4ce6d28bf3f4b3d50cb2e146cdfb7740` | canonical true→false; superseded by 36942 |
| 36938 DHT | `635709f203340d16ba7ca2d9846d7a77` | `39f190f63fe96de65c808f56b2067a15` | canonical true→false; superseded by 36943 |

The backup's COPY stream independently shows all five rows had `is_canonical=true` and `superseded_by_snapshot_id=NULL` before Run 149. The post-canary database shows `false` and the new snapshot IDs. Run 148 CERI snapshot hashes 10914–10918 remained exactly `b8e419e3...`, `d3e652b9...`, `5f5d194f...`, `1fd60624...`, and `6bf97145...`.

Therefore:

```text
historical_repair_performed = false
run_148_evidence_unchanged = false
unexpected_business_mutations = 5
```

No restoration was performed; the recovery dump is preserved outside Git.

## O. Post-canary regression evidence

The compact regression lane, Ruff, and compileall were **NOT RUN (FAIL-FAST)**. Section 38 requires immediate failure and cessation of new work after a historical row rewrite. The app/worker were stopped and read-only forensic evidence was preserved instead. The certified execution HEAD already carried the prior 367 focused / 703 cross-domain / 2,152 non-slow / 14 disposable-PostgreSQL certifications, but those are baseline evidence and are not represented as post-canary results.

Production code changed during this task: **no**.

## P. Final recommendation

```text
May the full temporal remediation chain now be merged to main?

NO
```

The frozen-context propagation remediation worked live, including STI-CANARY-F003. A separate remediation/certification cycle is still required to prevent a later same-session canary from mutating the immutable failed-control lifecycle snapshots (or to define a new immutable control-record strategy). Do not repair Run 148 in place and do not reuse Run 149 as a passing canary.
