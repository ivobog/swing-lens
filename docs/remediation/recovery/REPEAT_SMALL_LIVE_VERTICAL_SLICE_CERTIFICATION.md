# Repeat Small Live Vertical Slice Certification

Date: 2026-09-25 (Europe/Zurich)

Branch: `codex/market-regime-temporal-remediation`

Execution HEAD: `48d7a89b33f68be097c213ec9353f75ee698b0fb`

Classification: `PROVIDER_EXTERNAL` (normal `REQUIRE_IB` admission health preflight)

This certification records the one authorized upload request and the one authorized pipeline POST. The upload succeeded as run 165. The pipeline POST was rejected before admission with HTTP 409 `IB_GATEWAY_PROBE_TIMEOUT`; the response explicitly stated that no pipeline was created. Per the single-admission and failure policies, no second POST, retry, service restart, code change, data change, or alternate source was attempted. The real pipeline therefore did not execute, and this repeat live slice is a failure even though the separate bounded historical-data probe had passed all 24 requests.

## 1. Executive verdict

- SINGLE UPLOAD: YES
- SINGLE PIPELINE POST: YES
- SINGLE PIPELINE ADMISSION: NO — rejected before creation
- SEC PASSED: NO — not started
- MARKET DATA PASSED: NO — not started
- TECHNICAL PASSED: NO — not started
- MARKET REGIME PASSED: NO — not started
- MARKET REGIME CONTEXT PROPAGATION VERIFIED: NO — live stage not reached
- SECTOR CONTEXT PROPAGATION VERIFIED: NO — live stage not reached
- SECTOR PASSED: NO — not started
- COMBINED PASSED: NO — not started
- RANKING PASSED: NO — not started
- CERI PASSED: NO — not started
- SETUP PASSED: NO — not started
- LIFECYCLE/ALERTS PASSED: NO — not started
- WINNER PASSED: NO — not started
- LEASE CONTINUITY PASSED: NOT APPLICABLE — no root job or lease was created
- BACKEND TERMINATION REQUIRED: NO
- MANUAL INTERVENTION REQUIRED: NO
- UNEXPECTED AUTONOMOUS JOBS: 0
- REPEAT SMALL LIVE VERTICAL SLICE: FAIL
- READY FOR FINAL PRE-CANARY TEST GATE: NO
- READY FOR FINAL 100-SYMBOL LIVE CANARY: NO

The failure occurred at the normal admission gate, before any pipeline identity, calculation context, market-data fetch, stage, or lease existed. It is classified `PROVIDER_EXTERNAL`, not as evidence about the remediated Market Regime/Sector code.

## 2. Identity

- source: `C:\Users\Ivica\Documents\SwingLens\backups\small_live_vertical_slice_10_20260925.csv`
- source SHA-256: `03887924d8f9f16b2daa682bdb3a4ac0a54531458265a64156d916577733406e`
- exact symbols in source order: `BHE, BLLN, KLIC, LSCC, PDFS, ACMR, RDVT, AVT, DVN, JNJ`
- upload run: 165
- upload request ID: `request-97d1df848c494a7bbb72dbeca436161d`
- upload root correlation ID: `root-2606191806a840aab092247830390d6d`
- pipeline POST request ID: `request-78239ae722a940eba255053f340151af`
- pipeline POST root correlation ID: `root-22e40668b6d4432d8277f90fb4b875c3`
- pipeline: not created
- root job: not created
- execution token: not created
- calculation context: not created
- frozen cutoff/session: not created
- pipeline scope/membership fingerprint: not created
- refresh-cycle/acquisition-plan identity: not created
- execution-configuration identity: not created

Run 165 is durable `COMPLETED`, has 10 raw rows and 10 distinct symbols in the required order, and its stored uploaded file has the same SHA-256 as the retained source. Before the pipeline POST it had zero pipelines and zero jobs; after the rejected POST it still had zero pipelines and zero jobs.

## 3. Stage timeline

All times are Europe/Zurich (`+02:00`) on 2026-09-25. No pipeline stages started.

| Stage | Start | End | Duration | Result |
| --- | --- | --- | ---: | --- |
| SEC | - | - | - | NOT STARTED — admission rejected |
| Fundamental | - | - | - | NOT STARTED as a pipeline stage; upload produced the normal 10 upload-time fundamental rows |
| Market Data | - | - | - | NOT STARTED |
| Technical | - | - | - | NOT STARTED |
| Market Regime | - | - | - | NOT STARTED |
| Sector | - | - | - | NOT STARTED |
| Combined | - | - | - | NOT STARTED |
| Ranking | - | - | - | NOT STARTED |
| CERI | - | - | - | NOT STARTED |
| Setup | - | - | - | NOT STARTED |
| Lifecycle/Alerts | - | - | - | NOT STARTED |
| Winner | - | - | - | NOT STARTED |

## 4. Provider and admission evidence

The read-only, non-persisting bounded preflight covered the ten frozen symbols plus SPY and QQQ with `TRADES` and `ADJUSTED_LAST`, duration `2 D`, bar size `1 day`. All 12 contracts qualified and all 24 requests succeeded with two bars each, latest provider session `2026-09-24`. The artifact is `artifacts/diagnostics/ib_historical_probe_20260925_104151.json`, SHA-256 `cba9be62b5328a049df1c6f29ea45d6048d950bfdde3c31954eba4c8bd4c5a5e`. This probe used client ID 29.

The single normal `POST /runs/165/pipeline` used `market_data_policy=REQUIRE_IB`. Its own required health check used client ID 22 and returned after 3,636 ms:

- HTTP status: 409
- code: `IB_GATEWAY_UNAVAILABLE`
- provider status: `IB_PROCESS_RUNNING_API_NOT_READY`
- failure category: `IB_GATEWAY_PROBE_TIMEOUT`
- process running: true
- API connected/ready: false/false
- smoke response received: false
- response statement: `No pipeline was created.`

Durable state removes ambiguity: `pipeline_runs` remained at 155 total with maximum ID 155, `background_jobs` remained at 42,770 rows with maximum ID 43319, and run 165 has zero attached pipelines/jobs. No retry was attempted.

## 5. Market Data accounting

Pipeline Market Data accounting is all zero because admission failed before a fetch plan was created:

- planned: 0
- requested/executed: 0
- skipped/current: 0
- succeeded: 0
- failed pipeline items: 0
- retries: 0
- inserted/updated/revised/unchanged: 0/0/0/0

The 24/24 read-only provider probes above are preflight observations, not pipeline Market Data work, and are not counted as a pipeline pass.

## 6. Market Regime proof

No live Market Regime stage ran, so none of the required live assertions can be certified:

- `calculation_context_id` passed to loader: not observed
- SPY/QQQ frozen-context reads: not observed
- snapshot `as_of_date`: no run-165 snapshot
- snapshot `calculation_cutoff_at`: no run-165 snapshot
- identity expected session/cutoff: no calculation identity created
- mutation result/evidence ID: not created
- `MUTATION_ARTIFACT_TEMPORAL_MISMATCH`: did not occur because the stage never ran

The retained deterministic remediation evidence remains valid, but this report does not substitute it for the required live proof.

## 7. Sector proof

No live Sector/ETF stage ran. Calculation-context propagation, bounded source sessions, Sector snapshot creation, and mutation evidence therefore remain unverified by this live attempt.

## 8. Technical regression metrics

Technical did not start. Current equivalents are therefore not available:

| Metric | Previous small slice | Current attempt |
| --- | ---: | ---: |
| Runtime | 65.916 s | N/A |
| Validation queries | 22 | N/A |
| Canonical manifest bytes | 1,553,082 | N/A |
| Maximum checkpoint gap | <42.967 s | N/A |
| Lease expired | NO | NO — no lease existed |

Requested/visible/scored, manifests, payload sizes, checkpoints, and transaction durations are all zero/not applicable because no pipeline job was created.

## 9. Downstream evidence

There are no run-165 rows or evidence for Market Regime, Sector, Combined, Ranking, CERI, Setup, Lifecycle, Alerts, or Winner. Downstream-through-Winner is `FAIL` because the live pipeline never existed, not because a downstream stage emitted a failure.

## 10. Operational integrity

The pre-run runtime was the canonical controlled NORMAL topology at the exact execution HEAD:

- runtime instance: `1c4fcf19825a47519bd835c8f7fa32be`
- one web/API process: PID 9296
- one durable worker process: PID 296, instance `bfc22c8993e743b4b72991aa11a1a758`
- one logical supervisor root under the canonical process-group wrapper
- runtime classification: `ACTIVE_VALID`
- worker registered/heartbeat: healthy
- PostgreSQL: 18.3 at `127.0.0.1:5432/swinglens`
- migration: `0084_technical_recovery`
- web/worker/supervisor Prometheus targets: all up
- startup checks: healthy except the known non-blocking low-disk warning
- autonomous scheduler controls: maturation disabled, cohort refresh disabled, market-data prewarm disabled

No background job with ID above 43319 and no pipeline with ID above 155 appeared. Unexpected jobs, retry children, continuation chains, recoveries, reclaims, stale-owner events, and backend terminations were all zero. The worker retained the same PID throughout; the canonical shutdown was a normal lifecycle action, not backend intervention.

Protected-table totals were identical before and after except `upload_runs`, which changed 164 -> 165 as authorized. The upload also produced its expected 10 raw rows and 10 upload-time fundamental rows. Price bars, Technical scores/manifests, Market Regime, Sector, Combined, Ranking, CERI, Setup, Lifecycle, Alerts, Winner, pipeline runs, background jobs, calculation evidence, and IB fetch runs were unchanged. Retained run 164, pipeline 155, and job 43319 remained unchanged.

Canonical shutdown completed normally. Web, worker, supervisor, ports 8000/9101/9102, Prometheus 9090, and Grafana 3000 were stopped/closed; zero active business jobs remained. PostgreSQL and IB Gateway were left according to normal local policy. No forced process or database-backend termination was used.

## 11. Git and release gate

- branch: `codex/market-regime-temporal-remediation`
- execution HEAD: `48d7a89b33f68be097c213ec9353f75ee698b0fb`
- Market Regime implementation commit `49c8b2faad8692e92323b572b436c17e3eb6731a`: verified ancestor
- whole-application recovery commit `003c0f824a1fca518fd256f5398f065504877a71`: verified ancestor
- upstream: not configured
- pre-existing untracked `docs/remediation/recovery/WHOLE_APPLICATION_RECOVERY_AUDIT.md`: left untouched
- application code/settings/schema changed during run: NO
- pushed: NO
- final report HEAD: the Git commit containing this certification, reported in the final task response

The full broad suite was not run, as required. Because the repeat live slice did not reach Winner, it does not authorize the pre-canary broad-suite gate or the final 100-symbol live canary.

`REPEAT SMALL LIVE VERTICAL SLICE: FAIL`

`READY FOR FINAL PRE-CANARY TEST GATE: NO`

`READY FOR FINAL 100-SYMBOL LIVE CANARY: NO`
