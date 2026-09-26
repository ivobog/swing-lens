# IB Admission Probe and Repeat Small Live Slice Certification

Date: 2026-09-25 (Europe/Zurich)

Branch: `codex/market-regime-temporal-remediation`

Probe-fix execution HEAD: `4a543a82021a9755ecde492661238018c671d8ed`

Migration: `0084_technical_recovery`

Root-cause classification: `PROBE_IMPLEMENTATION_DEFECT`

This certification resolves the contradiction between the successful 24/24 bounded IB historical preflight and the rejected normal admission for retained run 165. The admission probe was repaired and repeatedly validated, run 165 was reused without another upload, and exactly one new `REQUIRE_IB` pipeline POST was admitted. The live pipeline then failed deterministically in Setup. In accordance with the task's post-admission stop rule, no application or data repair was attempted after admission.

## 1. Executive verdict

- IB ADMISSION PROBE HEALTHY: YES
- PRODUCTION CODE CHANGE REQUIRED: YES
- RUN 165 REUSED: YES
- NEW UPLOAD CREATED: NO
- EXACTLY ONE NEW PIPELINE POST: YES
- PIPELINE ADMITTED: YES — pipeline 156 / root job 43323
- TECHNICAL PASSED: YES
- MARKET REGIME CONTEXT/SESSION PASSED: YES
- SECTOR CONTEXT PASSED: YES
- COMBINED PASSED: YES
- RANKING PASSED: YES
- CERI PASSED: NO — scheduled by the root stage, but asynchronous capture job 43334 later failed
- SETUP PASSED: NO — root pipeline failed with `MUTATION_SETUP_CERTIFIED_PROJECTION_TARGET_REQUIRED`
- LIFECYCLE/ALERTS PASSED: NO — not reached
- WINNER PASSED: NO — not reached
- LEASE EXPIRED: NO
- RECOVERY/RECLAIM: 0
- BACKEND TERMINATION: NO
- MANUAL INTERVENTION: NO
- UNEXPECTED AUTONOMOUS JOBS: 0
- REPEAT SMALL LIVE VERTICAL SLICE: FAIL
- READY FOR FINAL PRE-CANARY TEST GATE: NO
- READY FOR FINAL 100-SYMBOL CANARY: NO

The admission defect is closed. The live slice is nevertheless a failure because Setup did not complete and Winner was not reached. This report does not treat deterministic or unit evidence as a substitute for those missing live gates.

## 2. Baseline and retained-run integrity

The repository began this task on `4f525b65ea43c48fc1d1101295794150d5f82c16`; the prior execution commit was `48d7a89b33f68be097c213ec9353f75ee698b0fb`. The pre-existing untracked `docs/remediation/recovery/WHOLE_APPLICATION_RECOVERY_AUDIT.md` was left untouched.

Before the authorized admission:

- canonical application runtime: stopped initially, then started in NORMAL mode for the admission window
- PostgreSQL: healthy at schema head `0084_technical_recovery`
- IB Gateway: process present and API port 4002 listening
- active SwingLens business jobs: 0
- run 165: `COMPLETED`, 10 raw rows, 10 distinct symbols
- pipelines attached to run 165: 0
- jobs attached to run 165: 0
- source and stored-upload SHA-256: `03887924d8f9f16b2daa682bdb3a4ac0a54531458265a64156d916577733406e`
- exact ordered membership: `BHE, BLLN, KLIC, LSCC, PDFS, ACMR, RDVT, AVT, DVN, JNJ`

The run and stored upload remained valid, so creating run 166 would have been unnecessary and was not authorized.

## 3. Exact IB probe root cause

The failed admission was not a TCP outage, API-handshake failure, client-ID collision, or leaked preflight session. It was a false negative caused by using `ib_insync.IB.connect()` as a short health probe.

The prior rejected POST was request `request-78239ae722a940eba255053f340151af`, root correlation `root-22e40668b6d4432d8277f90fb4b875c3`. Its web log shows:

- probe/connect start: `2026-09-25T10:42:31.756875Z`
- TCP connected: `10:42:31.757876Z` — approximately 1 ms
- server login acknowledged: `10:42:31.761874Z`
- IB API ready/handshake event: `10:42:31.764875Z` — approximately 8 ms after start
- disconnect after timeout: `10:42:34.789670Z`
- HTTP admission response latency: 3,636 ms
- mapped failure: `IB_GATEWAY_PROBE_TIMEOUT`

`IB.connect()` does not stop after the socket/API handshake. In installed `ib_insync` it next synchronizes positions, account updates and related trading state, then awaits `reqExecutionsAsync()` before emitting `Synchronization complete`. There was no initialization-request timeout log and no `Synchronization complete` log. The initial requests returned, the subsequent executions synchronization consumed the approximately three-second deadline, and the wrapper disconnected even though the API handshake had been ready for about three seconds. Thus the precise exceeded operation was the post-handshake `reqExecutionsAsync()` wait in full `IB.connect()` synchronization. That work is unrelated to admitting read-only historical-data work.

The total 3.636-second route response included the three-second connect/synchronization deadline plus process-detection and response overhead. The API itself was already ready; the old probe reported it not ready because the wrapper's broader synchronization contract did not finish.

Classification: `PROBE_IMPLEMENTATION_DEFECT`. The short timeout exposed the defect, but merely enlarging it would have retained the incorrect coupling between admission health and trading/account synchronization.

## 4. Bounded preflight versus admission probe

| Property | Bounded preflight | Admission probe before fix | Admission probe after fix |
| --- | --- | --- | --- |
| Caller | `scripts/ops/ib_historical_probe.py` | `POST /runs/{id}/pipeline` -> `check_status()` | same production route/service |
| Process | CLI/maintenance diagnostic | web | web |
| Client implementation | `ib_insync.IB` | `ib_insync.IB` | `ib_insync.IB` with low-level client handshake |
| Host / port | configured `127.0.0.1:4002` | configured `127.0.0.1:4002` | configured `127.0.0.1:4002` |
| Client ID | fixed diagnostic 29 | role-specific health ID 22 | role-specific health ID 22 |
| Connect timeout | 30 s bounded maximum | 3 s | 3 s |
| Request/readiness timeout | 30 s provider request timeout | same 3 s included unrelated full synchronization | 3 s handshake; bounded current-time smoke |
| API handshake required | yes | yes | yes |
| Account/trading callbacks required | yes, incidentally through full `IB.connect()` | yes, incorrectly through full `IB.connect()` | no |
| Contract request | all 12 required contracts | none | none |
| Historical request | 24 bounded requests | none | none |
| Semantic smoke signal | qualification + returned bars | intended current time, never reached on failure | `client.isReady()`, positive server version, `reqCurrentTime()` response |
| Event-loop/thread model | synchronous CLI wrapper over `ib_insync` loop | synchronous web request over `ib_insync` loop | same web model |
| Retry | none | none | none |
| Disconnect cleanup | `finally` disconnect | `finally` disconnect | `finally` disconnect |

Both paths used the same host and port. Their different client IDs were deliberate and non-overlapping; the material discrepancy was the old admission probe's use of full trading/account synchronization under a health-check deadline.

## 5. Client IDs and connection lifecycle

| IB consumer | Client-ID policy | Can overlap safely? |
| --- | --- | --- |
| Market-data worker/acquisition | fixed 21 | yes, distinct from health/capability ranges |
| Web admission/readiness | health base + WEB offset = 22 | yes; in-process probe lock prevents concurrent reuse |
| Worker health | health base + WORKER offset = 23 | yes |
| Supervisor health | health base + SUPERVISOR offset = 24 | yes |
| CLI/startup/release health | health base + CLI offset = 25 | yes |
| Web historical capability | health base + 4 + WEB offset = 26 | yes |
| Worker historical capability | 27 | yes |
| Supervisor historical capability | 28 | yes |
| CLI capability / bounded diagnostic | 29 | yes |

The controlled preflight-to-probe reproductions verified that client 29 disconnected and was immediately reusable, while client 22 connected independently. No occupied ID, active socket, stale reader task, pending disconnect, or Gateway-side session from the preflight was observed. The original failure also used different IDs (29 then 22), excluding a direct collision.

## 6. Probe reliability matrix

The old production probe happened to pass 10/10 isolated cycles after Gateway restart (124–407 ms, median 282 ms) and 5/5 preflight-to-probe cycles (319–359 ms). Those passes did not invalidate the original false negative: the retained log proves the API handshake succeeded while the broader synchronization timed out. They instead demonstrate the intermittent and semantically unrelated nature of the old synchronization dependency.

After the repair:

| Test | Attempts | Passed | Failed | Min | Median | Max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| admission probe only | 10 | 10 | 0 | 7 ms | 10.5 ms | 40 ms |
| preflight -> admission probe | 5 | 5 | 0 | 6 ms | 10 ms | 14 ms |

Probe-only handshake latency was 5–39 ms and current-time smoke latency was 0–2 ms. Every cycle disconnected cleanly. The preflight-to-probe sequence used one harmless SPY qualification per cycle rather than repeating the 24-request historical workload, respected the distinct client IDs, and proved immediate release.

The unchanged three-second timeout is now far above the measured 40 ms maximum while remaining short and fail-closed. No arbitrary 60–120 second timeout was introduced.

## 7. Production correction and focused verification

Commit `4a543a82021a9755ecde492661238018c671d8ed` contains the complete pre-admission production change:

- `app/services/ib_gateway_health_service.py`
  - `check_status()` now calls `ib.client.connect()` for the socket/API handshake rather than full `IB.connect()` account/trading synchronization.
  - readiness requires `client.isReady()`, a positive server version, and a harmless `reqCurrentTime()` response; a bare TCP listener cannot pass.
  - disconnect remains in `finally` and all failures remain fail-closed.
  - status diagnostics now record `failure_phase`, `handshake_latency_ms`, and `smoke_latency_ms`.
- `tests/test_ib_gateway_preflight.py`
  - updated the test client to model the low-level handshake.
  - asserted phase and latency diagnostics.
  - added a regression proving that unrelated full account synchronization is never invoked.

Focused verification:

- 53 passed: IB gateway preflight, operational pre-enqueue gate, run actions, and historical capability tests
- 88 passed: IB services, pipeline service/executor, readiness observability, and readiness metrics tests
- Ruff: clean for the changed files
- broad approximately 4,000-test suite: deliberately not run
- 100-symbol canary: deliberately not run

No code changed after pipeline admission.

## 8. Authorized admission and identity

The canonical runtime was started at the fix commit with runtime instance `462e1d37bb364070a5ffa1247625cc23`: one logical supervisor, one web process, and one durable worker; PostgreSQL and migration state were healthy; no business job was active.

Immediately before the POST, the exact production web status probe returned:

- state: `IB_API_READY`
- client ID: 22
- total latency: 11 ms
- handshake latency: 9 ms
- smoke latency: 1 ms
- server version: 176
- failure phase: none

Exactly one new `POST /runs/165/pipeline` with `market_data_policy=REQUIRE_IB` was submitted. It returned HTTP 303 to `/runs/165/pipeline/156`.

- request ID: `request-69e1b4238e654f11976b3a908767b77f`
- root correlation: `root-7b4cccfb34c94a4593a58589176d351c`
- pipeline: 156
- root job: 43323
- calculation context: 18
- cutoff: `2026-09-25T13:02:42.008185+02:00`
- frozen business session: `2026-09-24`
- execution token at claim: `f7efa051f468487a81130836c09a30d2`
- scope: `30d2ddee8417823b4b4fe8dbfec91e4c75b8917297b2da262b8359fcd8b5ed0b`
- refresh cycle: `bffe2d3f9a3c33ce3fcbed4bd25a53219c7edeb9da4f21a1298c0ccda28f6776`
- acquisition plan: `175ba8b360d750669647fced95f01e9594372fb5c9cccb63d379889d41c9f90a`
- admission health checked at: `2026-09-25T11:02:41.434976Z`

No second POST and no new upload occurred.

## 9. Live stage timeline

Times are from durable `pipeline_steps`; durations are wall time from persisted start to completion.

| Stage | Duration | Result |
| --- | ---: | --- |
| SEC | within 0.662 s CERI scheduling stage | PASS — SEC preflight reported 10/10 ready |
| Fundamental | 3.666 s | PASS |
| Market Data | 0.724 s | PASS — 24/24 current inputs skipped, 0 provider requests |
| Technical | 75.349 s | PASS — 10/10 accounted |
| Market Regime | 4.200 s | PASS |
| Sector | 10.139 s | PASS |
| Combined | 2.545 s | PASS |
| Ranking | 10.690 s | PASS — 50 results across 5 profiles |
| CERI | root scheduling stage 0.662 s | FAIL overall — expected child workflow ran, then capture job 43334 failed |
| Setup | 13.555 s | FAIL — `MUTATION_SETUP_CERTIFIED_PROJECTION_TARGET_REQUIRED` |
| Lifecycle/Alerts | - | NOT STARTED |
| Winner | - | NOT STARTED |

Additional pipeline stages were validation 0.107 s and decision-handoff manifest freeze 5.202 s. The root pipeline ran for 127.424 seconds of execution (132.638 seconds including queue delay) and became terminal `FAILED` at `2026-09-25T13:04:54.181874+02:00`.

The pipeline's durable error is exact and unambiguous: root job 43323 is `FAILED`, deterministic `ValueError`, `MUTATION_SETUP_CERTIFIED_PROJECTION_TARGET_REQUIRED`. Pipeline 156 is `FAILED` at `CAPTURING_SETUP_SIGNALS`. No repair was attempted.

## 10. Technical live gate

- requested / accounted / scored: 10 / 10 / 10
- runtime: 75.349 s, below the five-minute gate
- technical scores / calculation-evidence rows: 10 / 10
- canonical manifests: 1, manifest ID 2
- manifest digest: `c1aaa2cefcca226e0507296cf6e66afc6db7d5222aa85dd88d56a6ed713a49e6`
- canonical manifest bytes: 1,544,150
- unique sources / states: 22 / 16,082
- source-validation queries: 22, below the `8N + 50` bound
- total observed Technical SQL operations: 674
- compact per-score payload bytes: 99,572 total; 10,037 maximum
- calculation context / cutoff / session: 18 / `2026-09-25T13:02:42.008185+02:00` / `2026-09-24`
- worker processes / maximum in flight: 2 / 4
- input load / worker span / final publication: 45.654 s / 20.323 s / 9.202 s
- maximum durable progress-checkpoint gap: 45.673 s, below 60 s
- lease expiry / reclaim / recovery: no / no / 0

The one manifest and ten compact score/evidence rows were published without the old per-score universe-manifest duplication. The Technical gate remained healthy.

## 11. Market Regime live proof

Market Regime produced snapshot 100 and mutation evidence 345.

- calculation context passed: 18
- frozen cutoff: `2026-09-25T13:02:42.008185+02:00`
- expected completed session: `2026-09-24`
- SPY visible session: `2026-09-24`
- QQQ visible session: `2026-09-24`
- snapshot `as_of_date`: `2026-09-24`
- snapshot `input_as_of_session`: `2026-09-24`
- calendar version: `swinglens-us-equities-v1`
- regime / risk: `Bull trend` / `Green`
- mutation result: PASS, revision 1 current, evidence persisted
- temporal mismatch: none

The snapshot's persisted index-health and calculation-identity evidence both carry SPY/QQQ as-of `2026-09-24`, matching context 18. This is the required live proof that the frozen context reached the loaders and governed the artifact.

## 12. Sector live proof

Sector produced snapshot 98, four sector rows, and mutation evidence 406.

- calculation context passed: 18
- linked Market Regime snapshot: 100
- frozen cutoff: `2026-09-25T13:02:42.008185+02:00`
- expected session / artifact session: `2026-09-24` / `2026-09-24`
- calendar version: `swinglens-us-equities-v1`
- ticker / sector accounting: 10 / 4
- mutation result: PASS, revision 1 current, evidence persisted

Its persisted calculation identity and source-readiness evidence retain the same context, cutoff, and bounded source session. Sector context propagation therefore passes even though the later Setup gate fails the overall slice.

## 13. Downstream and autonomous-job evidence

The root completed Combined, Ranking, Sector, the handoff manifest, and CERI workflow scheduling before Setup failed. Durable run-165 rows after the attempt are: 10 Fundamentals, 10 Technical scores, 10 Combined results, 50 Ranking results, one Market Regime snapshot, one Sector snapshot/four rows, zero Setup snapshots, and zero Winner predictions.

The CERI stage created its expected pipeline-scoped child workflow. Jobs 43324–43333 completed or reached typed `PARTIAL` terminal states as designed; feature build and finalize completed. Expected capture child 43334 then failed with `MUTATION_CERI_CHANGE_CERTIFIED_SCORE_SOURCE_REQUIRED`. All were descendants of root 43323 and carried the same pipeline correlation, so they are explicitly part of the one admitted pipeline, not unexpected autonomous work.

- pipeline-created child jobs: 11
- unrelated/autonomous jobs: 0
- maturation jobs: 0
- cohort-refresh jobs: 0
- prewarm jobs: 0
- unrelated retry/continuation chains: 0
- active jobs after controlled shutdown: 0

Because Setup and CERI capture failed and Lifecycle/Alerts/Winner never ran, downstream-through-Winner is `FAIL`.

## 14. Control plane and shutdown

Root job 43323 retained one execution owner until deterministic terminal cleanup. Heartbeats renewed the lease, progress advanced through sequence 30, `stall_detected_at` remained null, and `recovery_count` remained zero. The terminal row cleared the lease owner, execution token, heartbeat and lease expiry normally.

- lease expired: NO
- stale-owner recovery: 0
- reclaim: 0
- backend/database termination: NO
- manual database or application repair: NO
- post-admission code change: NO

Canonical shutdown requested worker quiescence, established the durable claim fence, observed zero active jobs, and stopped web, worker, supervisor and observability normally. Ports 8000, 9101, 9102, 9090 and 3000 closed. PostgreSQL remained listening on 5432 and IB Gateway on 4002 according to normal local policy.

## 15. Final release gate

The IB admission false negative is remediated and certified. The repeat small slice is still a release-gate failure because the live pipeline did not reach Winner. The new Setup/CERI failures are preserved as evidence for a separate task and were not repaired here.

`IB ADMISSION PROBE HEALTHY: YES`

`REPEAT SMALL LIVE VERTICAL SLICE: FAIL`

`READY FOR FINAL PRE-CANARY TEST GATE: NO`

`READY FOR FINAL 100-SYMBOL CANARY: NO`
