# Final fresh-source live canary — failed release gate

**Certification:** FAIL. **PRODUCTION_READY: NO.** This is a failure certificate, not a successful production sign-off. The one authorized pipeline stopped at `SCORING_TECHNICALS`; CERI, Setup/Lifecycle, and Winner capture were not reached. The user directed termination after Technical scoring had made no committed progress for substantially more than 15 minutes. No second canary was submitted, no code was patched, and nothing was pushed.

## Source and release authority

| Item | Verified result |
| --- | --- |
| Original | `C:\Users\Ivica\Downloads\money money_2026-09-24.csv`; SHA-256 `d522efb2d0dfecbd83c4b5ce8c2a567e533e1414f2eedafb0f4ca24f95dfaf8b`; 101 rows, 101 distinct nonblank symbols, 122 columns |
| Frozen canary | `FINAL_FRESH_SOURCE_CANARY_100.csv`; header plus original rows 1–100, order and cells preserved; SHA-256 `36c4a6d96c211cf742e8a644a7b4ddec2a9367119401814d2f6d5aec661912be`; 100 distinct symbols, ACMR first and CORT last |
| Exclusion | ARQT, original row 101, solely to satisfy fixed cardinality; provider-based curation **none** |
| Preflight | 100/100 qualified; TRADES 100/100 available; ADJUSTED_LAST 100/100 available; typed unavailable 0, contract changed 0, unknown 0. Exact ticker results are in `FINAL_CANARY_CANDIDATE_PREFLIGHT.csv`. |
| Release | Local `main` fast-forwarded to `39bdb19213a97da8c8da7edc551dbde23128481b`; canonical V2 application/test source matched tested `e59906bfd17b1b6db9445fa79eefe6975f842310` entries; current canonical fingerprint `bef31fd9659ab0b35867a6acf8dbe6b3942258d13ec393b3e2adbea23eeff552` (the older fingerprint uses a different encoding). No application/test source was changed in this task. |
| Database | Authoritative PostgreSQL 18.3 at `127.0.0.1:5432/swinglens`, Alembic `0083_winner_scope_truth`; no active business jobs before admission. Historical runs 155/162 and pipelines 152/153 were not reused or retried. |
| Backup | Retained `swinglens_final_release_gate_pre_0081_20260923_122912.dump`; SHA-256 `acba22199a8bac158b4eb72a824bcf5b52075858e850b9a16d8b4dd0aa74703a`, matching required value. |

## Controlled live execution

The controlled wrapper started NORMAL mode with automatic Winner maturation, automatic cohort refresh, and market-data prewarm disabled. Web PID 9000, one durable worker PID 6316 (instance `88854c44dcb3461fb9075143d89db5ea`), and supervisor PID 3396 were verified; no duplicate worker. Runtime HEAD was `39bdb19213a97da8c8da7edc551dbde23128481b`. Prometheus and Grafana were ready, with web/worker/supervisor targets UP. IB Gateway `127.0.0.1:4002` handshake/current time/server version 176 passed; SPY ADJUSTED_LAST and TRADES controls each returned 10 bars. Initial disk was a nonblocking warning, approximately 77.1 GB/15.1% free.

Exactly one normal upload POST created completed run **163**: 100 raw rows, 100 distinct symbols, stored-file hash equal to the derived CSV, no prior pipeline on that run. Exactly one `POST /runs/163/pipeline` with `REQUIRE_IB` returned HTTP 303 and admitted pipeline **154**, root job **43301**. There was no duplicate admission or second canary. Frozen scope `0c61efa9a9fb7874eb3b2c78aff2e71dcd25e8f20cd4461cb3ac4f189da21922` contained the same 100 symbols; scope fingerprint `b7bbb4bb425f72eddac35e9639d859edd3b9f7d738ed0a0ab2b40fb564f76322`. Initial refresh `3cf8ca0f5632bb6efd25da7e9f44a85f960cff2aa06b23fd7a95b4a174f10f2b`, acquisition plan `f97e52090141e11cc3eabfc494560034c68fd06842f3cfcd2a34`, configuration anchor `65e3ab297fd4b3ee0387d61a5292be0ed20862c857b7e63727e2e5fc8e6423ad`, and configuration fingerprint `d27792f76cc8f1b872fcfc1f28c1b4aff9176d9dd2f6484030d48fe4a8943761` were recorded.

Root job 43301 queued required SEC-readiness child **43302**. Its first three retries encountered `SecEdgarError: SEC network or response failure`; the fourth attempt succeeded without manual retry. Resumed FULL_PIPELINE child **43303** completed validation, fundamentals, and market-data fetch. The 100 run-specific fundamental scores committed.

Market-data fetch **231** completed in approximately 7m17s: 102 requested subjects including SPY/QQQ, 204 feed items, 142 planned/executed/successful IB requests, 62 skipped due to fresh coverage, 0 failed requests, 0 fetch retries, and 0 same-plan successful-item replay. It fetched 8,854 bars, inserted 8,194, revised/updated 109, and left 551 unchanged. There was no provider insufficiency in this fetch and no zero-progress acquisition retry. The earlier market-data/Winner-obligation failure did not recur at this boundary; this does **not** certify downstream Winner processing.

| Pipeline stage | Final state | Local time (Europe/Zurich) |
| --- | --- | --- |
| VALIDATING_RUN | COMPLETED | 13:37:53 |
| SCORING_FUNDAMENTALS | COMPLETED | 13:37:53–13:38:14 |
| FETCHING_MARKET_DATA | COMPLETED | 13:38:14–13:45:32 |
| SCORING_TECHNICALS | FAILED after operator intervention | 13:45:33–15:07:07; ~81m34s |
| Regime, combine, ranking, sector, CERI, Setup, Lifecycle, Winner | PENDING / not reached | No certification possible |

Pipeline 154 ran approximately 1h47m08s from admission to FAILED. At intervention, Technical had committed **0** run-specific scores and had recorded no progress, while job 43303 still showed RUNNING with a heartbeat frozen at 13:45:33 and a lease expired at 14:00:33. Its PostgreSQL session was `idle in transaction` for more than 81 minutes, holding the job-row lock; a normal cancellation POST timed out waiting on that lock. The controlled stop correctly refused with `ACTIVE_LEASE_BLOCKS_STOP`. To honor the user's explicit stop instruction, the single identified backend PID 3204 was terminated, rolling back its uncommitted transaction. The pipeline then became FAILED and job 43303 requeued for automatic retry; that exact queued child was cancelled through the product's job-cancellation service before it could run again. This was **manual intervention**, not a clean unattended terminal. The immediate SQL failure recorded after backend termination (`<restricted:sql>`) is an effect of that intervention, not proof of the original Technical-stage root cause.

The final database has 100 raw rows, 100 fundamental scores, **0 technical**, 0 combined, 0 ranking, 0 Setup signals, and 0 Winner predictions for run 163. Jobs 43301 and 43302 are COMPLETED; 43303 is CANCELLED with one retry count attributable to the backend interruption; queued/running/recovering global jobs = **0**. No autonomous maturation, cohort-refresh, or prewarm jobs were observed in the canary window. No CERI/Setup/Winner performance, calculation identity, reconstruction/proof, retention, or negative-dependency contract can be newly certified from this incomplete run. The code-level release gate from the earlier remediation remains historical evidence only.

## Shutdown and disposition

Controlled shutdown succeeded after the active job was cleared: web, worker, supervisor, IB API, and observability stopped; port 8000 has no listener; canonical PIDs 9000/6316/3396 are absent. PostgreSQL remains running by canonical settings. Final disk free was **59,145,768,960 bytes / 11.58%**, still a warning. No live ownership and no active business jobs remain. The one fresh run and its successful durable market-data/fundamental evidence were retained; no backup restore, schema downgrade, or purge was performed. No successful-canary settling window exists.

**Failure classification:** `TECHNICAL_STAGE_LIVENESS_AND_LEASE_FAILURE / USER_DIRECTED_TERMINATION`. The Technical scoring implementation and its progress/heartbeat/timeout behavior require diagnosis before another production gate. The configured 30-minute long-stage progress timeout did not produce a terminal outcome despite >81 minutes without committed progress and an expired job lease. Do not infer a successful release from the clean market-data boundary.

**Release finding:** `REL-TECH-001 OPEN`. Prior `REL-IB-001`, `REL-MD-001`, `REL-MD-002`, `REL-IB-002`, `REL-WIN-002`, and `REL-WIN-003` remain code-gate-closed, but this canary did not exercise the downstream Winner/CERI proof needed for final production certification. Original audit: prior closed items retain their prior disposition; fresh end-to-end certification is **partial**, with `REL-TECH-001` **open**. `PRODUCTION_READY: NO`; manual interventions: **2** (targeted DB-session termination and exact queued-child cancellation, following one timed-out normal cancel request); second canary: **NO**; push: **NO**.

Read-only/raw operation evidence was retained outside the repository at `C:\Users\Ivica\Documents\SwingLens-remediation-safety-20260924\`, including preflight, preadmission, admission, final snapshots, cancellation attempt, backend termination, and final run-specific counts.
