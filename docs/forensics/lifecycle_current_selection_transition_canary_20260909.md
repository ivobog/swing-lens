# Lifecycle Current-Selection Transition Canary — 2026-09-09

## A. Executive verdict

`LIFECYCLE CURRENT-SELECTION TRANSITION CANARY: FAIL`

`SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO`

Exactly one targeted natural canary was run for DRS. The run completed, produced one immutable lifecycle snapshot, initialized one new current-selection key, and appended one internally valid initialization event. It did **not** advance an existing pointer from an old snapshot to the new snapshot: `pointer_advances=0`. The mandatory end-to-end transition coverage therefore failed.

The canary also exposed a temporal defect in the asynchronous CERI capture path. All persisted context identifiers and child-job payload fields matched the frozen context, but CERI score snapshot 10929 cited seven source records retrieved after the cutoff and two DRS price bars first observed after the cutoff. This is one semantic CERI snapshot-context violation. No production code was changed because this task expressly forbids patch-and-continue.

## B. Baseline

| Control | Baseline |
|---|---|
| Branch | `codex/lifecycle-historical-immutability-remediation` |
| Execution HEAD | `b651b18da634289947c739fd84075de713331dfc` |
| Certified remediation HEAD | `730570d94308f514ab24c15825e7743ffd4d7ee0` (ancestor) |
| Prior evidence HEAD | `b651b18da634289947c739fd84075de713331dfc` (current and ancestor) |
| Working tree | clean |
| Alembic code / production | `0069_lifecycle_current_selection` / `0069_lifecycle_current_selection` |
| PostgreSQL | PostgreSQL 18.3, `127.0.0.1:5432`, database `swinglens` |
| App / worker / supervisor | stopped / stopped / stopped |
| Port 8000 | no listener |
| Active or queued jobs | 0 |
| Latest run / pipeline / job | 150 / 143 / 42915 |
| Latest lifecycle snapshot | 36948 |
| Current-selection pointers | 14,645 |
| Selection-event ledger | 0 |
| Baseline time | `2026-09-09T11:08:05+02:00` / `09:08:05Z` / `05:08:05-04:00` New York |
| Latest completed US session | `2026-09-08` |
| Daily-bar ready at | `2026-09-08T16:15:00-04:00` / `22:15:00+02:00` |

The fresh recovery point was made with PostgreSQL custom-format `pg_dump` before runtime startup:

| Backup evidence | Value |
|---|---|
| Timestamp | `2026-09-09T11:15:54.7715066+02:00` |
| Path | `backups/lifecycle_current_selection_transition_canary_20260909/swinglens_pre_lifecycle_transition_canary_20260909.dump` |
| Size | 791,584,534 bytes |
| SHA-256 | `DE55C959FEEF65341DA949F18374922275700657DCF3761295E518FC91A52F4F` |
| Verification | `pg_restore --list` exit 0; 1,289 TOC entries |

The backup is ignored and is not committed. No credentials are present in this report.

## C. Candidate-discovery methodology

Discovery was read-only and completed while the app, worker, and supervisor were stopped. Counts and maximum IDs were unchanged across discovery.

Repository inspection established that selection is scoped by `(ticker, timeframe, data_as_of_date)`, not merely by ticker. Within a scope, `app/services/setup_lifecycle/canonicalization.py` orders candidates by:

1. completed daily bar;
2. successful source pipeline without fatal warnings;
3. required-feature coverage;
4. market and sector context completeness;
5. latest `calculated_at`;
6. highest snapshot ID.

The current pointer is updated separately from the immutable snapshot. A successful selection change appends an event; the old snapshot must not be updated. Discovery joined current pointers to their selected snapshots, inspected source-run status, required-feature coverage, context availability, warnings, cached price-bar maxima, and the available uploaded source row. Candidates requiring a broad bootstrap or having fatal source warnings were excluded.

## D. Candidate ranking

The stored price-bar preflight showed `2026-08-03` as the latest available DRS/NNI/TBLA/TPL session. Each candidate already had a complete, successful `2026-08-03` current snapshot. A new successful same-key snapshot was expected to tie on the first four precedence terms and win deterministically on calculation time and ID.

| Rank | Ticker | Current snapshot ID | Current session | Latest available session | Expected transition reason | Confidence |
|---:|---|---:|---|---|---|---|
| 1 | DRS | 19017 | 2026-08-03 | 2026-08-03 | same-key eligible snapshot wins on later calculation time and ID | HIGH |
| 2 | NNI | 18927 | 2026-08-03 | 2026-08-03 | same-key eligible snapshot wins on later calculation time and ID | MEDIUM |
| 3 | TBLA | 18885 | 2026-08-03 | 2026-08-03 | same-key eligible snapshot wins on later calculation time and ID | MEDIUM |
| 4 | TPL | 18961 | 2026-08-03 | 2026-08-03 | same-key eligible snapshot wins on later calculation time and ID | MEDIUM |

## E. Selected ticker set

DRS alone was selected as the minimum universe because it was the only HIGH-confidence candidate. Before the run:

- pointer 5725 targeted snapshot 19017 for `DRS / 1d / 2026-08-03`;
- snapshot 19017 came from run 76 and evaluation 141, had full required-feature coverage, a successful source run, market context 20, and no fatal warning;
- the expected new key was the same `2026-08-03` scope;
- the expected advance was 19017 → the new run-151 snapshot, on `calculated_at` and snapshot ID after all earlier precedence terms tied.

The source input was a one-row DRS extraction from the existing run-76 upload, SHA-256 `EE11B9F023BB3B49B6B23A8663160245AF9FB7EE857999F49C59C81A1A9ED479`, 6,324 bytes. The temporary discovery copy was removed after use. No values were altered to create eligibility.

## F. Canary identifiers

| Item | Value |
|---|---|
| Tickers | `DRS` |
| Upload request / pipeline request | exactly 1 / exactly 1 |
| Run / pipeline / initial job | 151 / 144 / 42916 |
| Enqueue time | `2026-09-09T11:29:59.988564+02:00` |
| Calculation context | 4 (the only context created) |
| Cutoff | `2026-09-09T09:30:53.203249Z` (`11:30:53.203249+02:00`) |
| Latest completed session | `2026-09-08` |
| Calendar | `swinglens-us-equities-v1` |
| Bar readiness | `daily-close-plus-15m-v1` |
| Cutoff reason | `FULL_PIPELINE_FROZEN_AT_ENQUEUE` |

Job 42916 paused only for the standard SEC-readiness repair. Job 42917 performed that repair and job 42918 resumed the same pipeline. Jobs 42919–42931 were the expected CERI descendants. All 16 jobs finished `COMPLETED`, with retry count 0 and recovery count 0. This was one logical execution, not a duplicate run.

The runtime used one web app and one logical durable worker. Winner capture handoff, Winner pipeline capture, maturation, cohort refresh, market-data prewarm, CERI maintenance backfill, and IB Gateway auto-launch were disabled with process-level overrides. The lifecycle and required CERI paths under test remained enabled.

## G. Transition evidence

The mandatory transition did not occur.

The technical frozen view for DRS ended on `2026-07-30`, not the predicted `2026-08-03`. IB fetch run 215 fetched through `2026-09-08`, but those writes happened after context 4's cutoff. Seven pre-existing DRS bars were revised after cutoff (five adjusted bars and two trade bars across the late-July/August edge). The bounded repository correctly excluded current rows whose revision time was after the cutoff because their prior values cannot be reconstructed safely. Its usable DRS TRADES series therefore contained 748 rows and ended on July 30.

Run 151 consequently inserted snapshot 36949 under a different scope: `DRS / 1d / 2026-07-30`. It had context 4, cutoff `09:30:53.203249Z`, session `2026-09-08`, full required-feature coverage, successful source status, and precedence score `[1,1,1,0,calculated_at,36949]`. Pointer 14646 and event 1 were valid **new-key initialization** records:

| Evidence | Value |
|---|---|
| New snapshot | 36949, run 151, capture evaluation 260 |
| New pointer | 14646, selected snapshot 36949, revision 1 |
| Selection event | 1, evaluation 261, selected snapshot 36949 |
| Event previous snapshot | `NULL` |
| Reason | `phase_4_canonical_precedence` |
| Existing Aug-03 pointer after run | pointer 5725 → snapshot 19017, revision 1, unchanged |
| Pointer advances | 0 |
| Ledger rows appended | 1 |
| Mandatory old→new transition | false |

The literal ledger delta is one, but it does not correspond to a pointer advance and therefore fails the required `selection_events_appended == pointer_advances` transition gate. There were zero duplicate pointer scopes, zero duplicate event keys/revisions, zero stale competing pointers, and zero structural ledger violations.

Snapshot 36949 remained immutable after insertion (evidence hash `18edc2ad7695aa3bd154295fa01f2a6550db62f72d457621f84b981ee43fcbfa`). Its warnings were `DATA_QUALITY_INSUFFICIENT`, `MISSING_OPTIONAL_CONTEXT`, `MISSING_SECTOR_ROTATION`, and `STALE_PRICE_BAR`. These warnings explain quality; they did not cause an arbitrary pointer write.

## H. Historical immutability evidence

The control hash covers all certified `IMMUTABLE_DECISION_EVIDENCE` and `IMMUTABLE_TEMPORAL_LINEAGE` fields and excludes only the legacy administrative `superseded_by_snapshot_id`. Baseline, mid-run, post-descendant, and final hashes were identical.

| Run | Snapshot | Ticker | Baseline/final SHA-256 |
|---:|---:|---|---|
| 148 | 36934 | AAPL | `5c0251265fc27e993fa1cb02332204a7ce168ee9e71b54ce79c5ed187bd7a5b7` |
| 148 | 36935 | AMGN | `29d2c7413fc5a9431536227880abb5070b790bd80fb08862f998743c41aa8d9b` |
| 148 | 36936 | AWK | `5176e2900c92e7ba3854efb3e949f72033422172d78aaa7e6646ba81dea04b9e` |
| 148 | 36937 | CBOE | `3d1164996762f23e53a2273830af8cc07a2789cefd8b75b140cf6834f05a99fb` |
| 148 | 36938 | DHT | `fddd1f8cd61d446fe4cbc56a434efed1d00f4a187820235b345eb38045773eab` |
| 149 | 36939 | AAPL | `59eb7793566d6fcedd11c21dafe12c35189ae3f48380ebc29df04a72ad65d722` |
| 149 | 36940 | AMGN | `2c93ebb8a05d64f0c1649a43c1f5ceb41385a75f976837685648d084640e867a` |
| 149 | 36941 | AWK | `4b6433d9f7ca594e4964f48419b100d1bccd10b7e068cc827499825188ae519e` |
| 149 | 36942 | CBOE | `0cf54cb4c47966cd892874535fcff46345e0d79b31c7864bb163e61db47116b2` |
| 149 | 36943 | DHT | `d7630cab7da62d5df1928b695a2935c61cf138b5e2dde439976bf1e2923281a6` |
| 150 | 36944 | AAPL | `1f0059afd0324baec650d6e294ff234a39ef07d0371c37ec7beae345e4bb3a17` |
| 150 | 36945 | AMGN | `55bf5f7467bd59697536911dc8e81f1685cd4ca4d229176f8cf6d800050505db` |
| 150 | 36946 | AWK | `84e630ed859261fb4cbcc3bf2cf9954331d5941d1f8c80cbc6f6fb02335fdba3` |
| 150 | 36947 | CBOE | `89deaf1e84d4d253e96928fd3436dd3f54ca7d662549cf36bc7869181ba6a304` |
| 150 | 36948 | DHT | `c6892585de3c685cb81c442c2da6fc47d52b10a2516d75df8dd5f7a9b776989a` |

The predicted predecessor snapshot 19017 also remained byte-for-byte equivalent under the same evidence hash, `b900c89d2f05fb6b55b7122c81efc6a7883120e0d88883494ae8abf75db02ec5`. Its legacy `is_canonical=true` and `superseded_by_snapshot_id=NULL` fields did not change. Final results: `historical_snapshot_rewrites=0`; Run 148/149/150 immutable hash changes were 0/0/0.

## I. Temporal integrity evidence

Context 4 was persisted exactly once. Technical score 33635, lifecycle snapshot 36949, all CERI parent and child payloads, and CERI score snapshot 10929 carried context ID 4, the exact cutoff, session, and calendar. Envelope mismatch counts were zero.

The evidence graph of CERI snapshot 10929 was not point-in-time clean:

- source records 867459–867465 were retrieved/ingested between `11:31:56+02:00` and `11:31:59+02:00`, after the frozen `11:30:53.203249+02:00` cutoff, and were cited as six earnings sources and one catalyst source;
- price-response feature 8683 cited DRS bars 2263479 and 2263504, first observed at `11:31:04.697382+02:00` and `11:31:07.168191+02:00`, after cutoff;
- the two SPY bars in the same price response were known before cutoff;
- all cited market sessions were on or before `2026-09-08`, so this is not a future-session violation;
- catalyst revision 18933 was announced before its `2026-09-08` reaction session, so no pre-event reaction was used.

The code path explains the defect without modifying it: `_price_response_for_company` in `app/services/ceri/capture_service.py` calls the price-response calculator with `feature_as_of_session` but no cutoff, and `_bars` in `app/services/ceri/price_response_service.py` limits `bar_date` without limiting `first_seen_at` or `revised_at`. The separate rebuild path already demonstrates the required cutoff predicates. Production code was not patched during this canary.

| Integrity metric | Result |
|---|---:|
| Frozen-context envelope persistence violations | 0 |
| Lifecycle context violations | 0 |
| CERI parent payload/handler context violations | 0 |
| CERI child payload/handler context violations | 0 |
| CERI snapshot context violations | 1 (post-cutoff evidence lineage) |
| Post-cutoff CERI source inputs consumed | 7 |
| Post-cutoff CERI price-bar inputs consumed | 2 |
| Future-session inputs consumed | 0 |
| Pre-event CERI reactions | 0 |
| HTF confirmation violations | 0 |
| RS alignment violations | 0 |
| IBMI session violations | 0 (not exercised live) |

Read-only HTF recomputation over the bounded 748-row DRS frame produced confirmed source week `2026-07-24`, excluded the incomplete/latest bucket, and produced zero violations. DRS and SPY relative-strength inputs aligned by common session through July 30, so no unpaired benchmark future date was consumed.

## J. Non-advancing candidate explanations

DRS is classified `OTHER_PROVEN_REASON`: the natural live run produced a snapshot for a different, older effective key because after-cutoff price-bar revisions made July 31–August 3 unreconstructable at the frozen boundary. The system validly initialized the July-30 key but did not exercise the existing August-3 pointer. The prediction was wrong; no state was manipulated and no second run was started.

NNI, TBLA, and TPL were ranked but deliberately not selected because the one HIGH-confidence DRS candidate justified the minimum one-ticker universe. They are not live non-advancing candidates and caused no writes.

## K. Database delta

Every observed write belonged to run 151, pipeline 144, their frozen context, the DRS provider refresh/SEC repair, or the required CERI descendants. The CERI cutoff defect concerns which natural rows were consumed, not an unexplained external mutation.

| Domain | Baseline → final (delta) | Classification |
|---|---|---|
| Upload runs / pipelines | 150→151 (+1) / 143→144 (+1) | expected targeted canary |
| Background jobs / enqueue attempts / fanout roots | 42,504→42,520 (+16) / 42→58 (+16) / 19→20 (+1) | expected workflow bookkeeping |
| Calculation contexts | 3→4 (+1) | expected single frozen context |
| Raw / fundamental / technical / combined / ranking | 26,548→26,549 (+1) / 26,470→26,471 (+1) / 23,881→23,882 (+1) / 23,773→23,774 (+1) / 86,905→86,910 (+5) | expected scoring rows |
| Market / sector snapshots / sector rows | 91→92 (+1) / 90→91 (+1) / 896→897 (+1) | expected analytical context |
| IB fetch runs / items | 214→215 (+1) / 60,532→60,538 (+6) | expected targeted fetch |
| Price bars / revisions | 2,262,382→2,262,432 (+50) / 34,244→34,251 (+7) | expected provider writes |
| Lifecycle evaluations / snapshots | 193→195 (+2) / 26,538→26,539 (+1) | expected capture and evaluation |
| Current pointers / selection ledger | 14,645→14,646 (+1) / 0→1 (+1) | expected **new-key initialization**, not an advance |
| Lifecycle events | 31,958→31,960 (+2) | expected canonical-decision and phase-transition evidence |
| Lifecycle signal changes / alerts | unchanged / unchanged | expected no qualifying business transition |
| CERI ingestion / source / processing | 50,467→50,472 (+5) / 867,335→867,461 (+126) / 57,155→57,163 (+8) | expected DRS provider/processing rows |
| CERI estimate snapshots / earnings actuals | 149,421→149,469 (+48) / 11,021→11,027 (+6) | expected DRS evidence |
| CERI catalyst events / revisions / revision features | 18,593→18,594 (+1) / 18,932→18,933 (+1) / 186,318→186,342 (+24) | expected DRS evidence |
| CERI derived / price response / score snapshot | 23,853→23,856 (+3) / 5,791→5,792 (+1) / 10,733→10,734 (+1) | expected CERI outputs; score lineage has cutoff defect |
| SEC sync / documents / extractions | 1,047→1,048 (+1) / 49,010→49,015 (+5) / 74,187→74,192 (+5) | expected DRS readiness repair |
| CERI changes / alerts | unchanged / unchanged | expected no qualifying change |
| IBMI tables | unchanged | not exercised |
| Winner temporal/outcome/cohort families | unchanged | disabled/not exercised |

The run also created one expected DRS company mapping and normal provider/processing telemetry. Active/queued jobs returned to zero. `unexpected_business_mutations=0`, `duplicate_job_execution=0`, and `historical_repair_performed=false`.

## L. Pipeline status analysis

Pipeline 144 and all descendant jobs completed successfully. The pipeline result was `COMPLETED`, not `PARTIAL`: one combined row, `degraded=false`, no failure reason, IB readiness `READY`, two planned and two executed IB requests, two fresh fetches, and zero IB failures.

The sector snapshot's low-confidence single-sector warning, lifecycle stale/insufficient-quality warnings, and CERI `INSUFFICIENT_COMPONENT_COVERAGE` result are analytical/degraded-input findings rather than provider or operational failures. They do not explain away the mandatory transition miss. The post-cutoff CERI lineage is separately classified `TEMPORAL_INTEGRITY_FAILURE` and fails certification even though job status was completed.

## M. Post-canary tests

`NOT RUN (FAIL-FAST: no existing pointer advanced, and a CERI temporal-integrity failure was proven).`

The requested regression lane is authorized only when live certification has not already failed. JSON parsing, whitespace validation, and repository hygiene checks used to package this evidence are delivery validation, not the skipped post-canary regression suite.

## N. Final state

All jobs 42916–42931 are terminal `COMPLETED`; each has retry count 0 and recovery count 0. The canonical shutdown left the app, worker, and supervisor stopped, with zero Python runtime processes, zero active or queued jobs, and zero port-8000 listeners. PostgreSQL remained running under canonical settings. Unrelated observability was not restarted.

The only committed files from this task are this report and the machine-readable summary. The backup and runtime upload artifacts are ignored. Production-code diff from execution HEAD is empty; no lifecycle/CERI history was repaired; no merge was performed.

## O. Final recommendation

Do not merge the complete temporal remediation to main. A future certification must first remediate and independently verify cutoff-aware CERI capture, then run a new explicitly authorized natural canary that produces an eligible snapshot in an **existing** `(ticker, timeframe, data_as_of_date)` scope and proves exactly one pointer advance plus exactly one `old→new` ledger event. This task does not authorize that follow-up run or any patch.

```text
LIFECYCLE CURRENT-SELECTION TRANSITION CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL REMEDIATION TO MAIN: NO
```

No historical repair. No merge. No production-code change.
