**Run 43 — forensic analysis of the market-data readiness block**

Investigated on October 18, 2026. All times in the timeline are Europe/Zurich (CEST, UTC+02:00). Application and SQL logs retain UTC. Scope: production pipeline 43, upload 43, market calculation context 78, IB fetch run 23, and continuation job 1452. This investigation made no application changes, provider requests, or production database writes.

**Finding.** The immediate cause was an application-side rejection of both DDS historical-data responses. Each response extended through the required October 16 session, but its earliest returned date was September 28. The planner had frozen September 29 as the earliest permitted date for its `14 D` daily request. The executor rejected the entire response before calling the bar cache. Consequently, DDS remained at October 5 in the cache, and the readiness gate correctly blocked the pipeline for missing October 16 data.

The underlying defect is the combination of an overly exact model of the provider's response footprint and an all-or-nothing response validator. The model assumes that `N D` with daily bars corresponds to exactly N inclusive US equity sessions. This assumption did not bound the DDS responses. The final `EXPECTED_SESSION_UNAVAILABLE` message describes the resulting cache state; it omits the acquisition rejection that created that state.

Confidence in this causal chain is high: retained fetch-item diagnostics, the readiness disposition, timestamped worker events, SQL telemetry, current cache provenance, and an offline replay of the actual executor agree. Why IB returned an earlier DDS date cannot be established from the retained evidence. The original response payload and full date list were not saved.

**The decisive database evidence.**

Both rows are preserved in [dds-failed-items.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/dds-failed-items.json).

| Field | Item 5599 | Item 5600 |
| --- | --- | --- |
| Instrument / contract | DDS / conId 6390 | DDS / conId 6390 |
| Feed | ADJUSTED_LAST | TRADES |
| Action / duration / bar size | TOP_UP_RECENT / 14 D / 1 day | TOP_UP_RECENT / 14 D / 1 day |
| Required missing sessions | October 6–16 | October 6–16 |
| Reviewed response interval | September 29–October 16 | September 29–October 16 |
| Actual returned endpoints | September 28–October 16 | September 28–October 16 |
| End mode | CURRENT, empty endDateTime | EXPLICIT, `20261016-23:59:59` |
| Result | FAILED, boundary_status=FAIL | FAILED, boundary_status=FAIL |
| Attempts | 1 | 1 |
| Fetched / inserted / updated / revised | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| Provider error / retryable flag | null / null | null / null |

Both stored errors say:

> IB returned 2026-09-28 before reviewed scope 2026-09-29; persisted none of this request.

The zero `fetched` counters do **not** prove an empty provider response. The executor assigns `fetched = len(bars)` only after response validation and caching succeed. Here it recorded the response endpoints in the exception handler and returned before assigning that counter. The actual returned bar count is unknown.

Readiness disposition 1828, revision 1, associates both failures with DDS, fetch run 23, and context 78. It records expected session `2026-10-16`, latest visible session `2026-10-05`, contract status `RESOLVED`, lifecycle state `UNKNOWN`, and reason `EXPECTED_SESSION_UNAVAILABLE`. Of 238 current dispositions, 237 are `READY`; DDS is the only blocker. See [dispositions.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/dispositions.json).

**Timeline and independent log corroboration.**

| Time on October 18, CEST | Evidence and implication |
| --- | --- |
| 21:57:15 | Upload 43 accepted 238 rows from `skimmer 2_2026-10-16 (1).csv`. |
| 21:57:23.615 | Context 78 froze the calculation cutoff and October 16 as the latest completed session. |
| 21:57:27–22:03:35 | Job 1451 completed SEC readiness repair. All 238 tickers became SEC-ready; continuation job 1452 was queued. |
| 22:04:12.743 | Step 549 entered FETCHING_MARKET_DATA. Fetch run 23 started. |
| 22:04:28.065 | Historical capability check reported IB_HISTORICAL_DATA_READY. Both SPY probe feeds succeeded; server version was 176. |
| 22:09:08.438 | DDS ADJUSTED_LAST network request logged SUCCESS, duration 1,018.892 ms. Item 5599 then failed response-boundary validation at 22:09:08.439. |
| 22:09:11.220 | DDS TRADES network request logged SUCCESS, duration 789.433 ms. Item 5600 then failed response-boundary validation at 22:09:11.222. |
| 22:22:17.406 | Fetch run 23 finished PARTIAL: 354 requests, 352 successes, two failures; another 126 plan items were skipped. The only failures were the DDS feeds. |
| 22:22:37.233–22:22:37.429 | Step 549 and pipeline 43 became BLOCKED with the reported readiness error. |
| 22:22:38.263 | Continuation job 1452 persisted BLOCKED. |

The market-data stage lasted approximately 18 minutes 24 seconds. It continued collecting the remaining symbols after the DDS rejection, so the final pipeline block appeared about 13 minutes 26 seconds after the second DDS failure.

The DDS network-success events are at [worker log line 702573](C:/Users/Ivica/Documents/SwingLens/logs/lifecycle-worker.log:702573) and [line 702575](C:/Users/Ivica/Documents/SwingLens/logs/lifecycle-worker.log:702575); the final stage block is at [line 702852](C:/Users/Ivica/Documents/SwingLens/logs/lifecycle-worker.log:702852). A compact retained copy is [timeline.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/timeline.json).

SQL telemetry independently records each DDS item's transition into RUNNING and then FAILED, with **zero price-bar inserts and zero failed SQL statements** in the DDS interval `20:09:04.940859–20:09:11.400000 UTC`. It contains 232 records and four fetch-item updates. The wider retained context also contains a preceding DBX cache insert at `20:09:04.762894`; that precedes DDS execution and is excluded from the DDS counts. The last DDS failure update is at `20:09:11.320231`, before the next ticker starts at `20:09:11.412206`. See [SQL window](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/sql-dds-fetch-window.json) and [log summary](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/log-summary.json).

The supervisor made 226 watchdog decisions for job 1452, all `HEALTHY_WITHIN_PROGRESS_TIMEOUT`. This was a deliberate readiness block, with no evidence of a watchdog termination.

**The code path that produces the failure.**

1. [The incremental planner](C:/Users/Ivica/Documents/SwingLens/app/services/ib_fetch_plan_service.py:557) computes an acquisition window from the previous latest bar plus the revision window. With October 5 as the prior session and five revision sessions, it reproduces `14 D` and September 29 as the start: nine missing sessions plus five prior sessions. Five is the current source default and reproduces the retained plan; it was not independently found in the durable execution.settings namespace, so this investigation does not claim a separate frozen settings witness for that value.

2. [The request-scope model](C:/Users/Ivica/Documents/SwingLens/app/services/ib_historical_request_scope.py:111) handles daily `D` durations by subtracting `amount - 1` US trading sessions from the end. For October 16 and `14 D`, that yields September 29. [Scope construction](C:/Users/Ivica/Documents/SwingLens/app/services/ib_historical_request_scope.py:52) chooses a current-ended request for ADJUSTED_LAST and an explicit end for TRADES. The retained DDS items prove both modes encountered the same lower-bound mismatch.

3. [The fetch adapter](C:/Users/Ivica/Documents/SwingLens/app/services/ib_data_fetcher.py:62) passes the duration, bar size, feed, and end to IB and converts returned observations to HistoricalBar objects. [The executor validates those observations](C:/Users/Ivica/Documents/SwingLens/app/services/ib_fetch_executor.py:633) before reaching `cache_bars` at line 662.

4. [The lower-bound guard](C:/Users/Ivica/Documents/SwingLens/app/services/ib_fetch_executor.py:1373) raises HistoricalRequestScopeViolation whenever the minimum returned date precedes the reviewed start. It does not retain the in-range subset. [The catch block](C:/Users/Ivica/Documents/SwingLens/app/services/ib_fetch_executor.py:685) stores actual endpoints, sets boundary_status=FAIL, marks the item FAILED, and returns immediately. It supplies neither a typed failure classification nor retryable=true.

5. [The readiness reader](C:/Users/Ivica/Documents/SwingLens/app/services/market_data_session_readiness.py:107) loads context-visible daily TRADES/ADJUSTED_LAST bars. DDS has no cached observations after October 5 at all, so this is not a case where an existing October 16 row merely failed a point-in-time visibility filter. Both latest DDS rows were first acquired by fetch run 12 on October 6; neither was written by run 23. See [DDS bar provenance](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/dds-recent-bars.json).

6. [The classifier](C:/Users/Ivica/Documents/SwingLens/app/services/market_data_session_readiness.py:233) has no terminal lifecycle evidence, no expected-session bar, no retryable provider failure, and no unresolved contract identity. It therefore takes the REQUIRED_DATA_UNAVAILABLE / EXPECTED_SESSION_UNAVAILABLE branch at line 347. [The pipeline](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_executor.py:663) blocks the complete requested universe when any such disposition remains.

IB's current [Historical Bars documentation](https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars) describes duration as a lookback interval and bar size as granularity. It does not supply the exact N-inclusive-equity-session response guarantee assumed by this code. This documentation supports validating the integration assumption; the retained DDS diagnostics establish the actual mismatch in this incident. Neither source establishes IB's internal reason for the earlier date.

**Why an unconditional one-session adjustment is insufficient.**

There are two useful controls in the retained data:

- In the same fetch run, PK items 5813 and 5814 also used `14 D`, daily bars, and the same October 16 end. Each returned 14 bars beginning September 29 and succeeded. The DDS result therefore cannot justify shifting every request's start by one session. See [PK comparison](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/pk-comparison-items.json).
- An earlier DDS TRADES request, item 1512 in fetch run 10, failed on October 5 with the same kind of lower-bound rejection. Its `7 D` scope began September 24, but the response began September 22 and ended October 2. That mismatch was two US trading sessions, and none of that request was persisted. This predates run 43. See [DDS fetch history](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/dds-fetch-history.json).

These observations establish a recurring response-footprint incompatibility. They do not establish a universal IB off-by-one rule or prove that the original DDS response contained every intermediate session.

**Causal reproduction and controls.**

[reproduce_failure.py](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/reproduce_failure.py) invokes the real planner helpers, executor, and readiness classifier with the retained DDS plans. Provider responses and cache writes are replaced in memory. The response dates between the retained endpoints and all OHLCV values are explicitly synthetic; this is a branch-level causal replay, not a replay of the original provider payload.

| Scenario, exercised for both feeds | Observed result |
| --- | --- |
| Synthetic response with retained September 28–October 16 endpoints | Same boundary error; one attempt despite a retry budget of three; zero cache calls; readiness remains REQUIRED_DATA_UNAVAILABLE. |
| Counterfactual: remove only the early September 28 observation | Executor calls cache once, succeeds, and the classifier returns READY. |
| Negative control: include an observation after October 16 | Upper-bound validation rejects the response with zero cache calls. |
| Negative control: stay in scope but omit October 16 | Fetch can succeed, but readiness still blocks for missing the expected session. |
| Reuse the existing failed item in the same acquisition | The real fetch loop makes zero provider calls, leaves attempt_count=1, and retains FAILED. |

All eight response scenarios and both resume replays passed their assertions. [reproduction-results.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/reproduction-results.json) contains the complete outputs. The trim counterfactual isolates the blocking condition; without the original raw prices it cannot certify that trimming the original live response alone would have passed every downstream data-quality check.

The existing request-scope, fetch-executor, and readiness test files also passed: **46 tests**, with one unrelated Starlette/httpx deprecation warning. See [test results](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/existing-tests.xml). Those tests cover the strict rejection behavior and the assumed session-count model, but they did not prevent this real provider-footprint mismatch.

**Alternatives ruled out, and impact.**

| Candidate explanation | Evidence |
| --- | --- |
| IB connectivity, entitlement, or general historical-data outage | Capability probes succeeded; DDS network calls returned successfully; 352 acquisition requests succeeded. The two failures have a local scope-validation error, with no provider error code. |
| Wrong frozen session, weekend expectation, or readiness latency | Context 78 explicitly expects Friday October 16, not Sunday October 18; its daily-bar ready time was October 16 at 22:15 CEST. Both DDS responses reached the expected date. |
| SEC repair failure | Repair job 1451 completed and reported 238 ready tickers before market-data acquisition. |
| Point-in-time filter hiding an existing latest DDS row | The cache contains no DDS bar dated after October 5. Current rows and first-fetch provenance corroborate rejection before insertion. |
| Unresolved DDS identity | Retained and runtime identities agree on conId 6390 and the same DDS contract fields; the resolver state is RESOLVED. |
| Established terminal instrument state permitting exclusion | There are no DDS lifecycle records. UNKNOWN is the appropriate evidence state; terminal exclusion has no basis here. |
| Database failure or worker watchdog timeout | DDS-window SQL has no failures; all 226 job watchdog decisions are healthy. |

The pipeline's durable status is BLOCKED. The upload is independently COMPLETED, and IB fetch run 23 is PARTIAL; those are separate records. There are 238 fundamental-score rows but zero technical scores, combined results, ranking results, setup snapshots, or winner snapshots for upload 43. The ten stages after market-data acquisition remain PENDING. See [impact counts](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/impact-counts.json) and [pipeline steps](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/steps.json).

**Recommendations, in priority order.**

1. **Define and version the provider-response boundary policy before recovery.** Separate the required session interval, the provider request/response envelope, and the dates permitted for persistence and calculation. For an explicitly approved policy, quarantine/audit earlier over-returned observations and admit only the authorized in-range subset after validating contract, feed, dates, duplicates, and bar quality. Keep the frozen upper session boundary strict and require the expected session to be present. If acquisition authority requires the complete received response to fit a reviewed envelope, revise that envelope through a new admitted plan instead. Either approach needs a recorded policy decision; silently widening an existing plan or deleting the validator would break its current contract.

2. **Certify the boundary behavior with retained provider fixtures.** Cover DDS's one-session and historical two-session excess, PK's exact response, both feeds/end modes, weekends/holidays, missing expected sessions, invalid or duplicate bars, and future-session returns. The planner and executor must agree on the admitted interval, and tests must verify exactly what is persisted. Do not replace the current model with an unverified claim that all daily D durations have calendar-day, N-session, or N+1-session behavior. Record Gateway/API versions with fixture captures.

3. **Use an explicit targeted recovery acquisition for DDS.** Preserve the failed evidence. A same-acquisition resume retains non-retryable failed items in [the fetch loop](C:/Users/Ivica/Documents/SwingLens/app/services/ib_fetch_executor.py:151); its retry budget does not override that rule. After certifying the policy, admit a repair or superseding acquisition through the supported authority path, retain October 16 as run 43's business-session boundary, and reacquire DDS under that authority. New bars observed after context 78's cutoff must satisfy [the pipeline-owned acquisition visibility rules](C:/Users/Ivica/Documents/SwingLens/app/services/price_bar_repository.py:491). An unrelated manual cache refresh is not, by itself, proof that those bars are valid for this frozen run. If supported recovery cannot preserve that lineage, use a clearly identified new run with a new context rather than rewriting run 43's historical context.

4. **Require a concrete recovery acceptance check.** Confirm DDS's repaired expected-session bar and required coverage, a new successful fetch-item witness, context-visible provenance, and a superseding readiness revision. Require 238 ready tickers before resuming at the market-data boundary. Retain the 237 successful readiness witnesses and the failed revision for audit. Do not label DDS terminal/inactive or mark the existing failures SUCCESS to bypass the gate.

5. **Preserve the acquisition cause in diagnostics.** Add a typed local cause such as HISTORICAL_RESPONSE_OUTSIDE_REVIEWED_SCOPE, an explicit retry policy, received/admitted/discarded counts, rejected endpoints, and a bounded raw-response artifact or digest plus per-session manifest. Carry the local cause into readiness and the run view. Emit ticker/feed/fetch-item identifiers on network and validation events so network SUCCESS is not confused with acquisition SUCCESS. This would make the actual cause visible immediately at the DDS failure, while the remaining acquisition work continues.

6. **Correct misleading summaries.** `_mark_pipeline_blocked` always writes “Pipeline blocked before execution,” even after fundamental scoring and acquisition have run. Report the actual blocked stage. Also label `actual_latest_data_session` accurately: [its implementation](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_executor.py:3430) takes the minimum of pre-fetch plan dates; run 43 retains September 30 there, whereas its final DDS readiness date is October 5. Derive final readiness summaries from persisted dispositions, or distinguish planned coverage from post-fetch coverage.

**Evidence integrity and limits.**

Production reads used PostgreSQL REPEATABLE READ with `default_transaction_read_only=on` and a statement timeout. [Preservation verification](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/preservation-verification.json) confirms that all 15 initially captured query result sets were unchanged after the investigation's reads, replays, and tests. The report and evidence scripts are new local files; application source and production state were left unchanged.

The worker and SQL logs identify runtime commit `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`, with a dirty working tree. The reviewed scope, planner, fetch executor, fetch adapter, readiness, calendar, and price-reader files match that commit after newline normalization. Pipeline executor has existing local changes; its blocked-outcome function matches the commit, and the retained diff contains no changes to the market-data readiness branch. [Source hashes and function comparisons](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/reviewed-source-sha256.json) and [the retained diff](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/reviewed-working-tree.diff) document this. A dirty runtime cannot be fully attested from a commit label alone; persisted behavior and log callers provide the incident-specific corroboration.

[Source-log hashes](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run43-market-data-readiness/source-log-sha256.json) identify the exact byte prefixes read, including actively appended logs. SQL parameters are retained as shapes/digests rather than raw values, so their item attribution also uses caller functions, timestamps, and database records. No original DDS response OHLCV, complete date list, or received count is available; none is fabricated in this report. The exact IB-internal explanation for its DDS response boundary remains unproved and is unnecessary to establish the application's rejection-to-readiness causal chain.

Reproduction commands from the repository root:

```powershell
.venv\Scripts\python.exe docs/forensics/run43-market-data-readiness/reproduce_failure.py
.venv\Scripts\python.exe docs/forensics/run43-market-data-readiness/verify_evidence.py
.venv\Scripts\python.exe -m pytest tests/test_ib_historical_request_scope.py tests/test_market_data_session_readiness.py tests/test_ib_fetch_executor.py -q
```
