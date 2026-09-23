# REL-IB-001 — IB historical TRADES capability investigation

Status: **CERTIFIED CLOSED; READY_FOR_FINAL_CANARY_RERUN = YES**, subject to a separately authorized new upload/run and fresh admission. This is a new operational finding, not a change to the original audit's 63 CLOSED / 7 PARTIAL / 0 OPEN findings.

## Incident and preserved evidence

The only authorized live canary was upload run **155**, pipeline **152**, root job **43279**, SEC repair job **43280**, and continuation job **43281**. Pipeline 152 remains `BLOCKED` at `FETCHING_MARKET_DATA`; job 43281 remains `BLOCKED`. The preflight returned zero ticker-level IB requests, zero fresh fetches, and zero `ib_fetch_runs` for run 155. The application runtime was stopped after the canary. No retry, resume, new pipeline admission, order, or account mutation was part of this investigation.

The read-only pre-fix evidence is retained locally (ignored by Git):

| Artifact | SHA-256 | Purpose |
| --- | --- | --- |
| `backups/swinglens_final_canary_20260923.forensics.json` | `C035D7715F0E68E0182982EA95C99BA382183E6D5FBF4941812D8600DD136877` | Pipeline, job, step, scope, refresh, acquisition-plan and configuration records; captured 2026-09-23 15:33:00 UTC |
| `backups/swinglens_final_canary_20260923.http.json` | `72793B446B41C5F60524ADF77903E6F5F77A64D704383366FCFC24DEE418383B` | Canary HTTP/admission evidence |
| `backups/swinglens_final_canary_20260923.business_pre.json` | `F6BB33048A2A36830680AFF2DA4110A8024710AFDA676ACB28A4885D97FBC106` | Pre-canary business baseline |
| `backups/swinglens_final_canary_20260923.admission.json` | `226372CB00D3424580E681046A4CF2B1C7E448BFD8974E90EE889AAD78BC44F0` | Admission and frozen authority IDs |
| `backups/swinglens_final_canary_20260923.post_shutdown.json` | `54608094779163A8CFA8099346E8ABA37DF606A2F646B7A2AA25007684EE1701` | Shutdown state |
| `logs/lifecycle-worker.log` | `8FB1CED3A9691F06A54312ABE600DB7AE61794F555C2B71681C1AC7FCAB0F12D` | Original `ib.historical_capability.failed` event and worker sequence |

The original log event at **2026-09-23 15:30:39.759 UTC** records API-ready/server version 176, `TRADES` failed after **3016 ms** with zero bars, null IB error code, empty error message and generic `PROVIDER_ERROR`; `ADJUSTED_LAST` returned two bars in **1109 ms**. The next stage event at 15:30:39.820 UTC records `BLOCKED`/`IB_HISTORICAL_DATA_UNAVAILABLE`. The frozen scope, refresh, acquisition plan, and configuration IDs are respectively `c2209b8278a623b9aa249d5c0605800a63937d580a4978fd74838dea21a3eb67`, `6e410a2a8f9a14609201ac11afed175c4abad16cfc8053307930ca2eb7bdd116`, `18675eb1b0870a9faf12b52b6677806c52d83065288e6cce0322f13bdc750788`, and `65e3ab297fd4b3ee0387d61a5292be0ed20862c857b7e63727e2e5fc8e6423ad`.

Before changes, `main` was clean at `eb1e1eae0cf5592c868235fbd522599519abe586`. Every entry in the 1,143-entry prior V2 freeze still matched the committed tree. The previous certified application source was `e529e5b73b177cf1e94abbd07a99d3c2c807a010`, fingerprint `cfaec2e02c0cb9c37c83251ad309575f3f2fa118876badec8678af42bbd41682`. Investigation branch: `codex/ib-historical-trades-capability-remediation`. Authoritative migration head was and remains `0083_winner_scope_truth`; no migration was needed.

## Exact request and production comparison

The original preflight connected to `127.0.0.1:4002` read-only with worker client ID **27**, server version **176**, connect timeout **3 seconds** and `IB.RequestTimeout` **3 seconds**. It qualified `Stock("SPY", "SMART", "USD")` and issued sequential synchronous `reqHistoricalData` calls. The canary did **not** persist its qualified conId, IB request IDs, callback lifecycle, or exception class. Subsequent live qualification and the existing contract cache both yielded SPY conId **756733**, `STK`, `SMART`, primary exchange `ARCA`, `USD`; conId 756733 is corroborating current identity, not an observed original field.

| Parameter | Original capability TRADES / ADJUSTED_LAST | Frozen run-155 SPY acquisition TRADES / ADJUSTED_LAST | Equivalent before fix? |
| --- | --- | --- | --- |
| Contract resolution | Qualified `Stock("SPY", "SMART", "USD")` | Cached/qualified same SPY identity | Yes, current conId agrees; original conId was not retained |
| Historical API | `ib_insync.IB.reqHistoricalData` via `fetch_daily_bars` | Same adapter and synchronous IB API | Yes |
| `whatToShow` | `TRADES` / `ADJUSTED_LAST` | `TRADES` / `ADJUSTED_LAST` | Yes |
| `endDateTime` | empty / empty | `20260922-23:59:59` / empty | **No**, TRADES uses a frozen prior-session cutoff in acquisition |
| `durationStr` | `2 D` / `2 D` | `12 D` / `12 D` | **No**, acquisition includes revision/missing-session window |
| `barSizeSetting` | `1 day` / `1 day` | `1 day` / `1 day` | Yes |
| `useRTH` | true / true | true / true | Yes |
| `formatDate` | 1 / 1 | 1 / 1 | Yes |
| `keepUpToDate` | false / false | false / false | Yes |
| `chartOptions` | empty / empty | empty / empty | Yes |
| Historical request timeout | 3 seconds | 30 seconds | **No**; health-check bound was incorrectly reused for HMDS retrieval |
| Client ID | 27 | 21 | Intentional independent read-only preflight session, not a request-semantic difference |
| Callback/error handling | Adapter captured errors by conId only; generic exceptions became empty `PROVIDER_ERROR` | Same underlying adapter; acquisition has separate result handling | Material diagnostic weakness in preflight |

Within each original probe pair, all request parameters were identical except `whatToShow`. Within the actual frozen acquisition pair, `endDateTime` also intentionally differed because TRADES used the prior-session cutoff while ADJUSTED_LAST used the current endpoint. After the fix, pipeline preflight takes the frozen SPY plan item for each feed, including its end time, duration and bar size, and uses the same 30-second historical timeout. The separately connected client ID remains intentional. If a non-pipeline caller supplies no plan, the standalone two-day SPY health probe remains available but is not used to certify a pipeline plan.

## Cause and error propagation

**Primary classification: `SWINGLENS_TIMEOUT_POLICY_DEFECT`.** A historical HMDS request was subject to the three-second *connection health* bound, while actual ticker acquisition was allowed 30 seconds. The failed `TRADES` duration of 3016 ms closely tracks the configured three-second client timeout. An isolated reproduction through the installed `ib_insync` synchronous wrapper shows that expiry of `IB.RequestTimeout` raises `TimeoutError()` with an empty `str()`. The old capability generic-exception handler stored `PROVIDER_ERROR` and `_safe_message(str(exc))`, which explains the empty message and absent provider code. The code did not retain the exception class or IB request ID. The original callback stream and whether IB completed after the client timeout were not persisted, so the exact provider-side latency trigger or late completion cannot be recovered; neither permission denial nor HMDS outage is established by the original record. Both raw IB and the canonical adapter now succeed under the exact original and production-equivalent contracts, so “transient provider failure” would be unsupported.

Secondary SwingLens defects were nonrepresentative probe parameters and insufficient request-ID/error observability. The existing provider callback policy did not classify a timeout, and an error could be associated by contract conId without its historical request ID. The fix records the actual request ID, associates only matching callbacks, preserves IB code and sanitized message when supplied, distinguishes timeout/no-data/disconnect/contract/provider/adapter failures, and cancels only the timed-out *historical data request*. It does not cancel or change an order. An absence of IB provider code on a client-side timeout is now explicitly represented as `TIMEOUT`, not fabricated into a provider rejection.

The mandatory feed remains mandatory. SwingLens uses `ADJUSTED_LAST` for adjusted prices and `TRADES` for actual trade volume in price-bar storage, OHLCV coverage, technical scoring and CERI/price-response inputs. [IB's historical-bar documentation](https://interactivebrokers.github.io/tws-api/historical_bars.html) distinguishes split-adjusted `TRADES` from split-and-dividend-adjusted `ADJUSTED_LAST`. No `TRADES` substitution with `ADJUSTED_LAST`, `MIDPOINT`, or `BID_ASK` was introduced. `REQUIRE_IB` still blocks before ticker-level market-data execution if either required capability fails.

## Read-only diagnostics and validation

All diagnostics used a dedicated read-only client, qualified SPY, requested historical bars only, and disconnected. No live pipeline or trading API was called.

| Check | Raw IB | SwingLens adapter | Correlated callback / error |
| --- | --- | --- | --- |
| Exact old `2 D` / current end / 3-second `ADJUSTED_LAST` | 2 bars, 291 ms | 2 bars, 290 ms | Completion; no request error |
| Exact old `2 D` / current end / 3-second `TRADES` | 2 bars, 302 ms | 2 bars, 299 ms | Completion; no request error |
| Frozen production SPY `12 D` / prior-session end `TRADES` | 12 bars, 311 ms | 12 bars, final repeat 280/305/303 ms | Request IDs 5/6/7 each completed; 3/3, 16-second spacing, no pacing violation |
| Frozen production SPY `12 D` / current end `ADJUSTED_LAST` | 12 bars, 316 ms | 12 bars, final repeat 285 ms | Completion; no request error |
| Final capability using frozen run-155 plan | — | `IB_HISTORICAL_DATA_READY`; TRADES 12 bars/441 ms, ADJUSTED_LAST 12 bars/331 ms, timeout 30 seconds | Request IDs 4/5; no provider callback error |

The raw and adapter traces recorded informational request ID `-1` events 2104 (market data farm OK), 2106 (HMDS farm OK), and 2158 (security-definition farm OK). These are connectivity notices, not historical-request failures. Every successful historical request had its own `historicalDataEnd` callback. Raw original and adapter-original evidence are `backups/ib_trades_raw_exact_20260923.json` (SHA-256 `F1AC4AFBBD4B9B32FE8A432607F4B5622D8E6F5B99E42EE03B2A84CF2F07DD47`) and `backups/ib_trades_adapter_exact_20260923.json` (`63EC0F59CE8BDA9EBB3DD99E6BA555D0316CCAD24F102560EE3480A6800B4DFF`). Raw production evidence is `backups/ib_trades_raw_production_spy_20260923.json` (`9B9D3AF9B768E90F70EE69E94742C9654BA88EEF2A0D8F4F9DD62CE0D746FABC`). Final post-fix capability and repeatability traces are `backups/ib_trades_capability_frozen_plan_20260923.json` (`3FA2DEDA21C35036A57B32B0BD3C76C07BC410AE72EE3FCE2E76226AD153499F`) and `backups/ib_trades_adapter_production_repeat_final_20260923.json` (`ABBD962012AC9C08432A116905394EC9E0F69C53C7FD785CA4059D6178C10433`).

## Change, tests and next canary

The focused behavior change is in `app/services/ib_data_fetcher.py`, `app/services/ib_historical_capability.py`, and the preflight call in `app/services/pipeline_executor.py`, with deterministic tests in the corresponding IB and pipeline test modules. It preserves fail-closed behavior, uses the existing structured pipeline result/log fields, and requires no migration. Tests cover both-feed success, provider rejection, permission denial, no data, timeout, disconnect, adapter exceptions, IB code/message propagation, request-ID isolation, frozen-plan parameters, timeout cancellation, and zero ticker fetches on failed mandatory preflight.

The first full non-E2E diagnostic run reached **4,047 passed, 10 skipped, four failed**. All four failures were Phase-5 source-proof guards: three T14A checks correctly marked the changed `pipeline_executor.py` source pin stale, and the T14D family reconciliation rejected the new source-bound family ID. The reviewed current-source derivative is now `IB_HISTORICAL_TRADES_phase5_current_authority.json` (SHA-256 `8A533D0009E4A7FCE4A91ACC6326A0CC27D46B64F960B21B6A6701D50AFD1833`) with its 62-row reconciliation CSV (`BCE2CBC954AEA849DE8E6977DEF52AF6D589A1852FC771C00B4AD26184B76F05`). It pins the exact new pipeline blob `393d85eddfdfab3fafb20be0da53e064c15e66a4`, records one reviewed preflight-only proof evolution, and independently verifies unchanged 252 callers, 220 operation families, stable semantic family identity, initiator addresses, authority contract, writer contract, and zero bypasses. The prior `RELEASE_phase5_current_authority.json` and all historical Phase-5/7 certificates remain untouched. The four former failures and the rest of their two modules then passed **40/40**.

Final certification: targeted IB/provider/preflight lane **87 passed**; focused Phase-5 authority lane **40 passed**; focused Phase-6 scope/refresh and Phase-7 reconstruction/retention/integration lane **42 passed**. The fresh canonical full non-E2E/non-external repository run passed **4,051 passed, 3 skipped, 38 deselected, 0 failed** in **5,293.61 seconds**. The 15-node Winner caller module passed in explicit reverse collection order **15/15** in **201.61 seconds**. Separate E2E passed **28/28** in **729.45 seconds**, with no concurrent pytest. Repository-wide Ruff check, changed-file Ruff format check, Python compileall, and Git diff whitespace check all passed. The single focused source/test commit is `4a9f414ceebbc8bde0332a4a4730e28f6b229015`. Its canonical committed-source V2 fingerprint is `4558eda1d87ccf707070fa1fcf0e55a3accc03f60eb6446713b3f3f6763c3b57` over 680 implementation and 463 test entries (1,143 total). This replaces the old release-source identity; a subsequent docs-only commit does not alter these application/test blobs.

After certification, a read-only authoritative query reconfirmed migration `0083_winner_scope_truth`, pipeline 152 alone for run 155 and still `BLOCKED` at `FETCHING_MARKET_DATA`, jobs 43279/43280 `COMPLETED` and 43281 `BLOCKED`, zero run-155 `ib_fetch_runs`, and zero active run-155 jobs. The SwingLens HTTP port 8000 had no listener. The disposable PostgreSQL 18.6 test container and its anonymous volume were removed after the test lanes; the authoritative database was not the test target. No second pipeline, order, account mutation, backup restore or downgrade occurred.

There is no suitable existing 100-ticker, pipeline-free upload run: run 155 owns blocked pipeline 152, and the newer runs already have pipelines. The correct *next-task* candidate is a new 100-ticker upload from retained source `data/uploads/20260911_101959_f626ad5b_money money_2026-09-11.csv` (160,801 bytes, SHA-256 `B467F00F618116CF2D65B862756CA548B3F80DC5711CD1DA637D1EC78F4ADBDC`). Its run ID is intentionally **TBD** and its existing-pipeline count will be zero by construction. Creating that upload or pipeline requires separate authorization; this task does neither.

**Readiness verdict: YES for one separately authorized final canary rerun using a newly created clean 100-ticker upload/run.** The next task must create that run, verify its own admission and no existing pipeline, then explicitly authorize its single pipeline submission. Pipeline 152 must remain blocked and untouched. Live HMDS success now is evidence of current capability, not a guarantee of future provider availability; the next task must retain fail-closed preflight behavior.
