# Session & Temporal Integrity Controlled Production Canary — 2026-09-08

## A. Executive verdict

```text
CONTROLLED CANARY: FAIL
SAFE TO MERGE REMEDIATION TO MAIN: NO
```

The production migration and the bounded technical, Market Regime, Sector Rotation, CERI feature-rebuild, causal-reaction, weekly HTF, and Relative Strength paths behaved correctly. The canary nevertheless failed the central run-wide invariant in two real runtime continuations:

1. All five Setup Lifecycle snapshots were recaptured during evaluation without the frozen cutoff and ended with `calculation_context_id`, `calculation_cutoff_at`, `input_as_of_session`, and `calendar_version` null.
2. All five asynchronous CERI score snapshots were created from a fresh cutoff at `2026-09-08T12:08:01.949706+02:00`, not the pipeline cutoff `2026-09-08T12:06:38.383968+02:00`, and persisted `calculation_context_id = NULL` even though the capture job payload carried the correct context.

No future daily bar or pre-event reaction was consumed. That does not rescue the result: one pipeline did not remain one auditable frozen calculation context, and missing temporal validity did not fail closed. The pipeline also ended `PARTIAL` (five ordinary warning rows) rather than `COMPLETED`, independently failing the task's strict successful-pipeline criterion.

No remediation was attempted after observing the blocker. There was no second pipeline and no production-code change.

## B. Baseline

| Item | Evidence |
| --- | --- |
| Branch | `codex/session-temporal-integrity-remediation` |
| Execution HEAD | `1912eb4e5a1fb371e3898cb8971afd4e41cce048` (`docs: record exact certification commands`) |
| Certified remediation ancestor | `3df713653f7d234198108312f4038a36caf377c1`; verified ancestor of execution HEAD |
| Worktree | Clean before migration and canary |
| Database | PostgreSQL 18.3, database `swinglens`, user `postgres`, `127.0.0.1:5432`; database timezone `Europe/Berlin` |
| Safe DB identity | `sha256:2f034888ed83da8c08f4756770109956df4315840f42dd7dbe2bd878f11274f2` |
| Pre-migration Alembic | `0067_worker_quiesce` |
| Post-migration Alembic | `0068_market_calc_context` (single head) |
| Pre-canary process state | App stopped, worker stopped, supervisor stopped, port 8000 unowned |
| Pre-canary runnable jobs | Zero `QUEUED`, `RUNNING`, or `RECOVERING`; one retained `BLOCKED` job was not claimable |
| Pre-canary job totals | BLOCKED 1; CANCELLED 1,056; COMPLETED 24,333; FAILED 1,195; PARTIAL 15,865; STALE 12 |
| Frozen-session baseline | At task start, latest completed US session and latest available daily bars were `2026-09-04` |

The `0068` migration was inspected before execution. It is additive, has one `0067 -> 0068` path, creates nullable lineage fields/indexes/foreign keys, and contains no update/backfill. After migration, all legacy lineage columns remained null and all business-row counts were unchanged.

Recovery point:

- `backups/session_temporal_canary_20260908/swinglens_pre_0068_controlled_canary.dump`
- 790,944,167 bytes, PostgreSQL custom-format dump, created `2026-09-08T09:57:07.8195964Z`
- Evidence manifest recorded Alembic `0067_worker_quiesce`; `pg_restore --list` succeeded with 1,228 TOC entries.

## C. Runtime configuration

The canonical lifecycle was used: `pwsh .\swinglens.ps1 start|status|stop`. One web process supervised one durable worker. No secrets are reproduced.

| Setting/path | Effective canary value |
| --- | --- |
| `JOB_WORKER_ENABLED` | `true` |
| Setup Lifecycle engine / pipeline / alerts | enabled |
| `SETUP_CAPTURE_HANDOFF_ENABLED` | `false` (normal default; material to the failure) |
| CERI / provider ingest / batched workflow / capture / alerts | enabled |
| Winner capture, automatic maturation, automatic cohort refresh, cohort v2 refresh | temporarily forced `false` |
| Market-data prewarm | temporarily forced `false` |
| CERI backfill | temporarily forced `false` |
| IB Gateway auto-launch | temporarily forced `false` |
| IB Market Intelligence | disabled |
| Sector ETF mode | disabled; `universe_only` |
| Run market-data policy | `ALLOW_CACHE_FALLBACK` |

The standard `.env` file was not edited. Process-level overrides suppressed unrelated automatic production work. EODHD and SEC readiness were available. IB Gateway was initially unavailable but reported ready by pipeline execution; the plan executed zero IB requests and created no market bars.

Startup identity was exactly one web process (PID 17012), one supervisor (PID 7204), and one worker (PID 8808), with worker instance `ba4319fcc2d4477aac1a31a56f2ad860`. The API was core-ready and only degraded by the initially optional IB dependency.

## D. Canary universe

| Ticker | Selection rationale |
| --- | --- |
| AAPL | Liquid technology name, current daily history, broad CERI catalyst history |
| AMGN | Health-technology coverage and current daily history |
| AWK | Utilities diversification and current daily history |
| CBOE | Financial-market infrastructure with estimate/catalyst coverage |
| DHT | Transportation/industrial diversification and current daily history |

All five names already existed in supported production data, had at least 779 usable daily rows, ended on `2026-09-04`, and represented five sectors. The uploaded five-row subset used the ordinary `/uploads` API, followed by exactly one ordinary `/runs/148/pipeline` enqueue.

| Identifier | Value |
| --- | --- |
| Upload run | 148 |
| Pipeline run | 141 |
| Full-pipeline job | 42862 |
| Worker | `ba4319fcc2d4477aac1a31a56f2ad860` |
| Ticker count | 5 |

## E. Frozen calculation context

| Field | Value |
| --- | --- |
| Context ID | 1 |
| `cutoff_at` | `2026-09-08T12:06:38.383968+02:00` (`2026-09-08T10:06:38.383968Z`) |
| Exchange timezone | `America/New_York` |
| Latest completed session | `2026-09-04` |
| Daily bar ready at | `2026-09-04T22:15:00+02:00` |
| Calendar | `swinglens-us-equities-v1` |
| Bar-readiness policy | `daily-close-plus-15m-v1` |
| Reason | `FULL_PIPELINE_FROZEN_AT_ENQUEUE` |

| Pipeline stage | Same context/session evidence |
| --- | --- |
| Validation / fundamentals / combine / rankings | Run-scoped non-market stages; one upload/pipeline identity |
| Market-data planning | Context 1; zero planned and executed IB requests; no new bars |
| Technical scoring | Five scores persist context 1, exact cutoff, session `2026-09-04`, and calendar version |
| Market Regime | Snapshot 89 persists context 1 and session `2026-09-04` |
| Sector Rotation | Snapshot 88 persists context 1 and session `2026-09-04` |
| CERI enqueue / feature rebuild | Jobs 42863–42873 carry context 1, exact cutoff, session, and calendar; new derived features carry the same cutoff/session |
| Setup capture | Initial capture received context 1, but evaluation recaptured with no handoff and overwrote all five rows without first-class context — **FAIL** |
| CERI capture continuation | Job 42873 payload carried context 1, but its handler invoked capture without it; snapshots 10914–10918 used a fresh cutoff and null context — **FAIL** |
| Winner capture | Intentionally skipped by temporary override |

The Setup failure is explained by the reachable normal configuration path: `SETUP_CAPTURE_HANDOFF_ENABLED=false` makes `pipeline_executor.py` invoke evaluation with no capture handoff; `evaluation_service.py` then calls `capture_snapshots_for_run` without `market_cutoff`. The repository faithfully replaces the lineage fields with nulls.

The CERI failure is explicit at `app/services/ceri/job_handlers.py:333`: `execute_capture_run_job` calls `capture_run(db, run_id)` without rebuilding/passing the market cutoff that is already present in the job payload.

## F. Finding-by-finding live certification

| Finding | Classification | Live evidence |
| --- | --- | --- |
| STI-F001 | `LIVE_EXERCISED_PASS` | Five technical frames ended on frozen session; no future bar consumed. |
| STI-F002 | `LIVE_EXERCISED_PASS` | Ticker/trades/SPY/QQQ/sector latest sessions all `2026-09-04`; all exchange-session lags zero. |
| STI-F003 | `LIVE_EXERCISED_PASS` | Five misses created schema `2-temporal` artifacts keyed by session and source versions; no cross-session reuse. |
| STI-F004 | `LIVE_EXERCISED_PASS` | Market Regime 89 bounded SPY/QQQ to `2026-09-04`. |
| STI-F005 | `LIVE_EXERCISED_PASS` | Market source freshness/session lag was exchange-session based and zero. |
| STI-F006 | `NOT_EXERCISED_LIVE` | Inner sector ETF mode disabled; outer `universe_only` path was bounded. Automated certification remains supporting evidence only. |
| STI-F007 | `LIVE_EXERCISED_FAIL` | Final lifecycle rows used the fallback recapture path rather than retaining frozen cutoff identity; correct dates were incidental to available bars. |
| STI-F008 | `LIVE_EXERCISED_PASS` | Feature jobs and derived rows used session `2026-09-04` and the exact frozen cutoff; known records were bounded and unavailable baselines remained explicitly unavailable. |
| STI-F009 | `LIVE_EXERCISED_FAIL` | CERI capture snapshots used `12:08:01.949706+02:00`, not the frozen cutoff, and persisted null context IDs. |
| STI-F010 | `LIVE_EXERCISED_PASS` | Five resolvable reactions; zero reaction opens preceded their events. |
| STI-F011 | `LIVE_EXERCISED_PASS` | Four regular-session events advanced to the next session; one pre-market event used the same session. |
| STI-F012 | `NOT_EXERCISED_LIVE` | Zero CERI changes/alerts meant no cooldown decision. Automated weekend/holiday/DST coverage passed. |
| STI-F013 | `LIVE_EXERCISED_PASS` | All five artifacts confirmed the completed week ending `2026-09-04`; none excluded that week. |
| STI-F014 | `LIVE_EXERCISED_PASS` | RS used canonical date sessions, common `2026-09-04` stock/benchmark state, with no missing benchmark overlap. |
| STI-F015 | `NOT_EXERCISED_LIVE` | IB Market Intelligence disabled; no requests or rows. Automated semantic-range coverage passed. |
| STI-F016 | `LIVE_EXERCISED_FAIL` | Ten new rows lacked required first-class frozen-context provenance: five lifecycle and five CERI score snapshots. |
| STI-F017 | `LIVE_EXERCISED_FAIL` | Setup recapture dropped context; asynchronous CERI capture drifted 83.565738 seconds from the frozen cutoff. |
| STI-F018 | `NOT_EXERCISED_LIVE` | IBMI feature rebuild/capture disabled; no new feature rows. Automated cutoff coverage passed. |

## G. Technical lineage evidence

All scores used price basis `ADJUSTED_LAST`, volume basis `TRADES`, calendar `swinglens-us-equities-v1`, context 1, the exact frozen cutoff, and engine `4.0.0`. Independent queries found no eligible source row after the frozen session.

| Ticker | Score ID | Adjusted bar ID | Trades bar ID | Ticker / SPY / QQQ / sector latest | Rows | Artifact ID |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| AAPL | 33620 | 2248252 | 2247951 | `2026-09-04` / `2026-09-04` / `2026-09-04` / `2026-09-04` | 797 | 8895 |
| AMGN | 33621 | 2252429 | 2252428 | same | 787 | 8896 |
| AWK | 33622 | 2255777 | 2255776 | same | 783 | 8898 |
| CBOE | 33623 | 2248137 | 2248136 | same | 779 | 8897 |
| DHT | 33624 | 2255051 | 2255050 | same | 784 | 8899 |

SPY adjusted/trades IDs were 2263433/2263434 and QQQ adjusted/trades IDs were 2263435/2263436, all dated `2026-09-04`. Every artifact used schema `2-temporal`, included `input_as_of_session=2026-09-04`, series versions, feature-config hash, and a distinct input signature. All were `READY/UNVALIDATED`; therefore none was eligible as an active certified hit. Cache result was exactly five `not_found` misses. A repeated production calculation was not run because it would mutate cache usage metadata; automated same-session/different-session key tests passed.

## H. CERI causal evidence

All five reaction features used policy `daily-open-causal-v1`. Reaction opens are 09:30 New York on the reaction-start session.

| ID / ticker | Event timestamp (New York) | Effective session | Reaction start / open | Prior | H1 / H3 | Price-bar IDs | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 8670 AAPL | 2026-09-03 14:29:23 EDT | 2026-09-03 | 2026-09-04 09:30 EDT | 2026-09-03 | 09-04 / 09-09 | 2247951, 2248252, 2263433, 2263434 | causal |
| 8671 AMGN | 2026-09-03 11:30:06 EDT | 2026-09-03 | 2026-09-04 09:30 EDT | 2026-09-03 | 09-04 / 09-09 | 2252428, 2252429, 2263433, 2263434 | causal |
| 8672 AWK | 2026-09-03 14:23:00 EDT | 2026-09-03 | 2026-09-04 09:30 EDT | 2026-09-03 | 09-04 / 09-09 | 2255776, 2255777, 2263433, 2263434 | causal |
| 8673 CBOE | 2026-08-25 07:45:06 EDT | 2026-08-25 | 2026-08-25 09:30 EDT | 2026-08-24 | 08-25 / 08-27 | 2181109, 2181110, 2181841, 2181842, 2199641, 2199642, 2205315, 2205316 | causal |
| 8674 DHT | 2026-09-03 10:20:01 EDT | 2026-09-03 | 2026-09-04 09:30 EDT | 2026-09-03 | 09-04 / 09-09 | 2255050, 2255051, 2263433, 2263434 | causal |

The feature-rebuild stage created 24 CBOE revision-feature rows. Twelve EPS rows had `known_at` on 2026-08-21 or 2026-08-28, before the frozen cutoff. Twelve revenue rows had no `known_at` and were explicitly persisted as `UNAVAILABLE_BASELINE_NOT_ACCUMULATED`, rather than scored as valid point-in-time revisions. New derived feature rows used cutoff `2026-09-08T12:06:38.383968+02:00`, session `2026-09-04`, and the calendar version.

The subsequent five CERI score snapshots used those causal bar IDs and the right session, but their capture cutoff/context drift described in sections A/E remains fatal.

No CERI change or alert was created, so live cooldown behavior was not exercised.

## I. HTF / RS / IBMI evidence

### Weekly HTF

`2026-09-04` was the final trading session of the completed US trading week before the Tuesday canary (Monday 2026-09-07 was a market holiday). Each of artifacts 8895–8899 recorded:

- `htf_source_week = 2026-09-04`
- `htf_confirmed = true`
- `htf_latest_week_excluded = false`
- `htf_insufficient_confirmed_history = false`

Thus HTF confirmation violations: zero.

### Relative Strength

All five scores had benchmark data, sufficient RS history, no missing benchmark overlap, no session-alignment warning, and stock/benchmark latest common session `2026-09-04`. Date-only `PriceBar.bar_date` values remained canonical dates. Four unsupported sector labels and AWK's unavailable XLU series explicitly fell back to broad-market RS; no newer sector series was silently consumed.

### IB Market Intelligence

`NOT_EXERCISED_LIVE`. IBMI was disabled and all four IBMI table counts remained unchanged (11 runs, 20 request items, 80 historical metric bars, 8 features). Zero IBMI requests were planned or executed. The compact automated lane covered semantic requested sessions and cutoff propagation.

## J. Job/runtime evidence

Pipeline 141 ran from `2026-09-08T12:06:39.470872+02:00` to `12:06:55.477205+02:00`. All 12 pipeline-step records completed with zero retries and no errors. Full-pipeline job 42862 completed with zero retries. It produced five fundamentals, five technicals, five combined rows, 25 rankings, Market Regime 89, Sector Rotation 88, five setup snapshots, 59 signal changes, three reported transitions, and one setup alert.

The CERI workflow created jobs 42863–42875: four provider batches, four normalize batches, one feature batch, one finalize, one capture, one change detection, and one alert rebuild. Every canary job was scoped to run 148/workflow `ceri:pipeline:148:...`, executed once, completed terminally, and had zero retries/errors. No unrelated runnable job was consumed. Fourteen total canary background jobs changed `COMPLETED` from 24,333 to 24,347.

Pipeline status was `PARTIAL`, with five combined rows, zero incomplete rows, zero IB failures, and `warning_rows=5`. The warnings were ordinary analytical flags (for example distribution/breakout and low-confidence single-name sector warnings), not temporal exceptions. The status is still not `COMPLETED`, so the strict success checklist is not satisfied.

Temporal observability captured:

- web counter `swinglens_market_calculation_cutoffs_total{scope="pipeline"} = 1`;
- worker cache counter: five `not_found` misses;
- source-lag histograms: five observations for each ticker/trades/benchmark/sector source, each sum zero;
- `swinglens_technical_scores_temporally_degraded_total = 0`;
- CERI next-session shift counter emitted during rebuilds.

No runtime metric/log rejected or surfaced the missing lifecycle/CERI context handoffs. That observability gap is part of the blocker: invalid run-wide provenance was persisted rather than prevented.

## K. Database delta

Every changed business category was attributable to upload run 148, pipeline 141, job 42862, or its CERI workflow. No new daily market bars, revisions, series versions, Winner artifacts, or IBMI artifacts were created.

| Category | Before | After | Delta / explanation |
| --- | ---: | ---: | --- |
| Upload / raw rows | 147 / 26,533 | 148 / 26,538 | +1 run / +5 rows |
| Fundamentals / technicals / technical artifacts | 26,455 / 23,866 / 8,894 | 26,460 / 23,871 / 8,899 | +5 each |
| Combined / rankings | 23,758 / 86,830 | 23,763 / 86,855 | +5 / +25 |
| Pipeline / steps / calculation contexts | 140 / 1,235 / 0 | 141 / 1,247 / 1 | +1 / +12 / +1 |
| Background jobs | 42,462 | 42,476 | +14, all canary-scoped and terminal |
| Market Regime | 88 | 89 | +1 |
| Sector snapshots / rows | 87 / 881 | 88 / 886 | +1 / +5 |
| Lifecycle evaluation runs / snapshots | 187 / 26,523 | 189 / 26,528 | +2 / +5 |
| Lifecycle events / signal changes / alerts | 31,945 / 148,511 / 4,021 | 31,953 / 148,570 / 4,022 | +8 / +59 / +1 |
| CERI ingestion runs / source records | 50,407 / 867,107 | 50,427 / 867,262 | +20 / +155 |
| CERI normalized estimate / catalyst revisions | 149,324 / 18,819 | 149,421 / 18,877 | +97 / +58 |
| CERI revision / derived / reaction features | 186,174 / 23,835 / 5,782 | 186,198 / 23,838 / 5,787 | +24 / +3 / +5 |
| CERI processing / score snapshots | 57,071 / 10,718 | 57,099 / 10,723 | +28 / +5 |
| CERI changes / alerts | 9,852 / 1,057 | unchanged | No live decisions |
| Price bars / revisions / series versions | 2,262,368 / 34,244 / 2,972 | unchanged | No market-data mutation |
| IBMI runs / requests / bars / features | 11 / 20 / 80 / 8 | unchanged | Path disabled |
| Winner tables | Baseline counts | unchanged | Automatic Winner work disabled |

Five existing mutable `ceri_feature_build_states` watermarks (IDs 5699, 5716, 5729, 6047, 5787) were expectedly refreshed for the five canary tickers; table count stayed 5,351. Four previously ahead operational states became aligned and one aligned state was refreshed. These are current per-company build watermarks, not immutable historical feature rows. No delete occurred. Unexpected business mutations: zero.

## L. Historical safety

**No historical production repair was performed.**

The forensic audit was run immediately before migration/canary and again after shutdown. Immutable affected populations and their stable hashes remained unchanged:

| Historical population | Pre | Post |
| --- | ---: | ---: |
| Technical future-session scores | 1,122 | 1,122 |
| Setup Lifecycle session mismatches | 390 | 390 |
| CERI pre-event reactions | 2,705 | 2,705 |
| CERI ahead score labels | 8,319 | 8,319 |
| CERI source-bar-ahead snapshots | 137 | 137 |
| Weekly HTF stale-week scores | 6,808 | 6,808 |
| IBMI ahead feature labels | 8 | 8 |

Representative hashes stayed identical: technical affected IDs `82a2468e...9577f8`, lifecycle IDs `cb50d4e3...fa952f`, CERI causal IDs `523c2d1e...31406`, CERI ahead-score IDs `d172f997...b6756`, and CERI source-bar-ahead IDs `f7829850...8da7`.

The operational `ceri_feature_build_states` distribution changed from 4,725 ahead / 625 aligned to 4,721 ahead / 630 aligned because the five canary watermark rows were refreshed. This expected current-state update is disclosed; it was not a bulk repair and did not change the immutable historical feature, score, reaction, lifecycle, or technical populations.

## M. Final state

| Item | Final state |
| --- | --- |
| App | stopped |
| Worker | stopped |
| Supervisor | stopped |
| Prometheus / Grafana | stopped; restored to pre-canary state |
| Active jobs | 0 |
| Queued jobs | 0 |
| Other retained jobs | BLOCKED 1; CANCELLED 1,056; COMPLETED 24,347; FAILED 1,195; PARTIAL 15,865; STALE 12 |
| PostgreSQL | running per canonical lifecycle policy |
| Alembic | `0068_market_calc_context` (single head) |
| Production code | unchanged |
| Worktree before evidence files | clean |

Validation results:

- Temporal-focused regression: `651 passed, 7 skipped, 1 warning in 264.32s`.
- Pipeline/remediation smoke: `42 passed, 1 warning in 1.44s`.
- Ruff check across the 42 certified remediation Python files: passed.
- Ruff format check: `42 files already formatted`.

The sole warning class was the existing Starlette `TestClient`/httpx deprecation warning.

## N. Recommendation

```text
May the certified remediation now be merged to main?

NO
```

Create a separate remediation follow-up; do not patch-and-continue this canary. At minimum:

1. Make Setup Lifecycle evaluation always reuse the already-captured snapshot IDs/frozen market cutoff on the normal rollout configuration, or eliminate the duplicate capture safely.
2. Reconstruct `MarketCalculationCutoff` from the CERI job payload and pass it to `CeriRunCaptureService.capture_run`; propagate the same identity into follow-on processing records as applicable.
3. Add integration tests for the real default `SETUP_CAPTURE_HANDOFF_ENABLED=false` pipeline path and for `CERI_CAPTURE_RUN` job-handler payload propagation, asserting exact cutoff/context persistence.
4. Add an observable fail-closed guard that rejects any market-sensitive canary row missing or differing from the pipeline's persisted context.
5. Re-run full remediation certification, then a new controlled production canary. A new canary must be a new explicitly authorized operation; this failed run must not be reused or silently repaired.
