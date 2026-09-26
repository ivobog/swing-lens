# Small Live Vertical Slice Certification

Date: 2026-09-25 (Europe/Zurich)

Branch: `codex/whole-application-recovery-remediation`

Evidence source HEAD: `003c0f824a1fca518fd256f5398f065504877a71`

Classification: `APPLICATION_REGRESSION` (`DOWNSTREAM` stage domain)

This certification records the one authorized 10-symbol live upload and the one authorized `REQUIRE_IB` full-pipeline admission. The slice passed migration, provider, Market Data, fresh-fetch visibility, Technical evidence, lease, checkpoint, and transaction gates. It failed at `MARKET_REGIME_SNAPSHOT` with `ValueError: MUTATION_ARTIFACT_TEMPORAL_MISMATCH`, before Combined or Winner. Per the failure policy, no retry, source change, code fix, schema change, database patch, restart, or second admission was attempted.

## 1. Executive verdict

- MIGRATION 0084 APPLIED: YES
- LIVE SOURCE FROZEN: YES
- SINGLE UPLOAD: YES
- SINGLE PIPELINE ADMISSION: YES
- MARKET DATA PASSED: YES
- FRESH-FETCH VISIBILITY PASSED: YES
- TECHNICAL PASSED: YES
- TECHNICAL WITHIN 5 MINUTES: YES
- JOB LEASE CONTINUITY PASSED: YES
- CHECKPOINT GAP <60s: YES
- TRANSACTION MODEL PASSED: YES
- COMBINED PASSED: NO
- RANKING PASSED: NO
- CERI PASSED: NO
- SETUP PASSED: NO
- LIFECYCLE/ALERTS PASSED: NO
- WINNER PASSED: NO
- UNEXPECTED AUTONOMOUS JOBS: NO
- BACKEND TERMINATION REQUIRED: NO
- SMALL LIVE VERTICAL SLICE: FAIL
- READY FOR FINAL 100-SYMBOL LIVE CANARY: NO

## 2. Source identity

The current source was `C:\Users\Ivica\Downloads\money money_2026-09-24.csv`, SHA-256 `d522efb2d0dfecbd83c4b5ce8c2a567e533e1414f2eedafb0f4ca24f95dfaf8b`, with 101 rows and 101 distinct symbols. Its provenance had already been established by the preceding fresh-source certification.

A byte-preserving, header-preserving selection through the supported screener ingestion format produced `C:\Users\Ivica\Documents\SwingLens\backups\small_live_vertical_slice_10_20260925.csv`, SHA-256 `03887924d8f9f16b2daa682bdb3a4ac0a54531458265a64156d916577733406e`, with exactly these frozen symbols in source order:

`BHE, BLLN, KLIC, LSCC, PDFS, ACMR, RDVT, AVT, DVN, JNJ`

The controls were unchanged eligible source rows spanning Technology, Industrials, Energy, and Healthcare. No substitution occurred after freeze. Selected source-row SHA-256 values, excluding the line terminator, were:

| Symbol | Row SHA-256 |
| --- | --- |
| BHE | `b7bbd97980c1df156c47b1068bcc56dc68aa065f3cdaf081340cc61eb1bb39a5` |
| BLLN | `ac07564380d4c7ac4becfb3e5066e00cdcfc104e80ff5c62501b0eefcf2a891d` |
| KLIC | `2408504d6308360f667ffadfd07fa8891a7e7402e4acc18b1299281d5db3858a` |
| LSCC | `3ddca9c2c3e93614808aec9cf33a399792d55e9c8626407c2f1e6d09bb16fdbe` |
| PDFS | `d88d60bf6f64ba11d0daf7817f4f369c6abe16ec29b799ecac791dc57cd7288d` |
| ACMR | `237aa75827e55b61a06f047834847f18f3111d99cd2557a9ad270bd15d31b45e` |
| RDVT | `937e053471a97c9abf6c93df8adc6c304139704bf94c69c3403998912f839df` |
| AVT | `056c33b13b5941cf291ed5c7d6740ed80b3e91f7ce3cc2bbf49a8aad5c146cbc` |
| DVN | `ae8851596073ddff570b376f902b4b29ca9afa4f339bb86dff3c22d8e49bad5e` |
| JNJ | `41eca0b430b71532cfa912ad3236e1b14ce460c2aec15bd32fe5acd2b4db1ad1` |

Exactly one `POST /uploads` returned `303` to `/runs/164`; request ID `request-c6bd9e3e56d84ebf8aaeb56ffb095d02`, root correlation ID `root-84d9eb91fb1740b081ba50f5f63b4a4e`. Durable run 164 is `COMPLETED`, contains 10 rows and 10 distinct frozen symbols, and stores the same source hash. Before admission it had zero pipelines and zero jobs.

Exactly one `POST /runs/164/pipeline` with `market_data_policy=REQUIRE_IB` returned `303` to `/runs/164/pipeline/155`; request ID `request-891286ed17fd4de19a196cb6ec726591`, root correlation ID `root-cee959ca825149069fc6f6243b02afe8`. Pipeline 155 created only parentless root job 43319.

Admission identities:

- execution token: `9044048502ed48debd30e82da1d337f4`
- pipeline scope: `67d641ffe23faf309d9c44dad86e4b6b6ca6bb850ec21c79a329203e6bdc816f`
- membership fingerprint: `6c013055a3f9f589609145a441ed85d9d7fc16535be035e43544e5984fa85139`
- refresh cycle: `eb324cb07b51fed00e059e1d0f27f5f7d1bc9ffd43bc0e319fec85407cc27c34`
- acquisition plan: `0604026fbb70f106f0c7a7d8e3fab3aac58bf050eb373f01d4a36f256a6671e1`
- calculation context: 17; cutoff `2026-09-25 00:43:46.618832+02:00`; business session `2026-09-24`
- execution configuration anchor: `65e3ab297fd4b3ee0387d61a5292be0ed20862c857b7e63727e2e5fc8e6423ad`; fingerprint `d27792f76cc8f1b872fcfc1f28c1b4aff9176d9dd2f6484030d48fe4a8943761`
- logged calculation identity: actual `4fedd3f7a9422b44ebec3f32a16885366ce402143fd83b06fedc3fe5f02e9380`, expected `9206c96c9652078455526ba98f49fc98be2d802a739241d2fa08c9ad37091d91`, policy result `PIPELINE_CONTEXT_COMPATIBILITY: COMPATIBLE`

## 3. Migration evidence

The authoritative local database was PostgreSQL 18.3 at `127.0.0.1:5432/swinglens`, Windows service `postgresql-x64-18`. Before mutation it was at `0083_winner_scope_truth`; the repository head was `0084_technical_recovery`. Retained run 163 was `COMPLETED`, pipeline 154 was `FAILED`, and there were no active business jobs or pipelines.

The non-overwriting retained pre-migration backup is:

- dump: `C:\Users\Ivica\Documents\SwingLens\backups\swinglens_small_live_slice_pre_0084_20260925_151500.dump`
- timestamp: `2026-09-25T00:28:00.7540477+02:00`
- size: 814,176,953 bytes
- SHA-256: `b2606bc6688c864703e408bfb562bcb281883fd6f185f6b919aa8039f9d59ded`
- evidence manifest SHA-256: `8c2991378872877f89647d26165bcc8547be35e420423500a4461443459b05b2`
- metadata SHA-256: `251be4cbb21831bfc28a864528363f8b06b6357dbeca81bc4f771192188dea21`
- recorded revision: `0083_winner_scope_truth`

A direct Alembic invocation without the required database-safety context was safely rejected before a migration transaction. The canonical lifecycle migration path then verified PostgreSQL provenance and applied `0083_winner_scope_truth -> 0084_technical_recovery`. The database and repository both report `0084_technical_recovery`.

Schema verification confirmed nullable `price_bars.first_fetch_run_id`, `price_bars.first_fetch_item_id`, and `technical_scores.source_manifest_id`; the three required `RESTRICT` foreign keys; the lineage and Technical manifest indexes; and the canonical `technical_source_manifests` schema. All 2,290,369 legacy `price_bars` rows retained null first-fetch lineage; no manual backfill occurred. Focused migration and fresh-fetch smoke tests completed with `6 passed, 2 skipped`.

## 4. Stage timeline

All times are 2026-09-25 Europe/Zurich (`+02:00`). SEC readiness completed inside the validation window and did not create a repair job.

| Stage | Start | End | Duration | Result |
| --- | --- | --- | ---: | --- |
| SEC | 00:43:49.295346 | 00:43:49.385937 | <=0.091 s | COMPLETED: 10 ready, 0 blocking, 0 retries |
| Fundamental | 00:43:49.442577 | 00:43:51.940712 | 2.498 s | COMPLETED: 10 scores |
| Market Data | 00:43:52.003750 | 00:45:05.147976 | 73.144 s | COMPLETED: 24/24 requests |
| Technical | 00:45:05.195991 | 00:46:11.111751 | 65.916 s | COMPLETED: 10/10 accounted |
| Market Regime | 00:46:11.139682 | 00:46:13.477118 | 2.338 s | FAILED: `MUTATION_ARTIFACT_TEMPORAL_MISMATCH` |
| Combined | - | - | - | NOT STARTED |
| Ranking | - | - | - | NOT STARTED |
| CERI | - | - | - | NOT STARTED |
| Setup | - | - | - | NOT STARTED |
| Lifecycle/Alerts | - | - | - | NOT STARTED |
| Winner | - | - | - | NOT STARTED |

Pipeline 155 and root job 43319 are terminal `FAILED`. The root has `requested_cancel=false`, `recovery_count=0`, and cleared ownership/token/lease fields. The worker emitted `job.failed`, `error_type=ValueError`, `error_summary=MUTATION_ARTIFACT_TEMPORAL_MISMATCH`. This is a live application regression in downstream orchestration, not an IB or SEC provider failure.

## 5. Market Data accounting

The bounded preflight qualified all 10 contracts and obtained two bars from both `TRADES` and `ADJUSTED_LAST` for every symbol: 20/20 probes, zero errors. The preflight artifact is `artifacts/diagnostics/ib_historical_probe_20260924_224237.json` and was not committed.

Fetch run 232 used 12 symbols including SPY and QQQ, with two feeds per symbol:

- planned: 24
- requested/executed: 24
- skipped/current: 0
- succeeded: 24
- failed: 0
- retried: 0 (all item attempt counts were 1)
- fetched bars: 144
- inserted: 24
- updated: 6
- revised: 6
- unchanged: 114
- authority scope: `b941841497c06a4f151e377e356101c380262076dc984a233f561b95992b2e1b`
- refresh cycle: `a0487e193c51ea691481910ac299624f4bf3e3fffa39100c2d85121d26c07c03`
- acquisition plan: `3f4d87136e07667209fd3fdd2757d5c16471a2f12514fe1057890d1eca445f36`

Each incident symbol received two newly inserted `2026-09-24` bars after the frozen cutoff, with pipeline-owned lineage and matching execution token. Each then produced a Technical score and evidence row referencing manifest 1:

| Symbol | First-fetch item IDs | PriceBar IDs | Feeds | Technical visibility |
| --- | --- | --- | --- | --- |
| BHE | 64122, 64123 | 2291450, 2291451 | ADJUSTED_LAST, TRADES | score 34953 / evidence 315 |
| BLLN | 64124, 64125 | 2291452, 2291453 | ADJUSTED_LAST, TRADES | score 34954 / evidence 316 |
| KLIC | 64130, 64131 | 2291458, 2291459 | ADJUSTED_LAST, TRADES | score 34955 / evidence 317 |
| LSCC | 64132, 64133 | 2291460, 2291461 | ADJUSTED_LAST, TRADES | score 34956 / evidence 318 |
| PDFS | 64134, 64135 | 2291462, 2291463 | ADJUSTED_LAST, TRADES | score 34957 / evidence 319 |

All ten rows have `first_fetch_run_id=232`; each item is `SUCCESS`, attempt 1, and uses the root execution token. A natural unrelated post-cutoff negative row was not available, and none was injected. The canonical manifest contained only the 22 source identities admitted by the frozen cutoff plus matching pipeline acquisition authority; no unrelated temporal leakage was observed.

## 6. Technical evidence

- runtime: 65.916 s (warning threshold 120 s; failure threshold 300 s)
- requested / accounted / scored: 10 / 10 / 10
- insufficient but accounted: 1 (`BLLN`, limited listed history)
- canonical manifests: 1, manifest ID 1
- manifest digest: `43f1ac3bdb9a131a581395c622cb282fd383213af612586830f84d32c36e38b3`
- canonical manifest bytes: 1,553,082
- unique sources / states: 22 / 16,082
- live source-validation queries: 22; total Technical SQL statements observed: 679
- compact per-score payload bytes: 99,572 total; 10,037 maximum
- score / evidence rows: 10 / 10
- calculation context / cutoff / session: 17 / `2026-09-25 00:43:46.618832+02:00` / `2026-09-24`
- worker processes / maximum in flight: 2 / 4
- input load / worker span / final publication: 41.924 s / 14.898 s / 8.938 s
- checkpoint advances during/at Technical: 9 (`progress_sequence` 57 -> 66)
- maximum checkpoint gap: `<42.967 s`, a conservative supervisor-observation upper bound; therefore below 60 s

The Technical preparation/read transaction was observed for 56.854 s and the atomic final publication transaction for 8.931 s. There was no 80-minute-style outer transaction and no control-row lock retained across Technical. The one manifest, ten scores, and ten evidence rows were published together; there was no per-score universe-manifest duplication or partial publication. Compared with the deterministic 100-symbol maximum checkpoint gap of 49.020 s, this live observation remained bounded below 60 s.

## 7. Control-plane evidence

The initial lease expiration was observed at approximately `2026-09-25 00:59:24+02:00`; it was renewed while the unchanged execution token remained owned by the same root execution. Supervisor samples saw `progress_sequence=57` with heartbeat age increasing only to 37 seconds, then sequence 58 and subsequent sequences through 66. The lease did not expire.

- stale-owner events: 0
- recovery/reclaim events: 0
- job `recovery_count`: 0
- cancellation request: false
- backend termination: not used
- worker heartbeat: healthy throughout active work
- execution token continuity: continuous until terminal cleanup
- cancellation/control row: independently observable and accessible throughout Technical
- watchdog: transient `LOCKED_CANDIDATE_SKIPPED` observations around checkpoints/publication, with no stall, reclaim, ownership loss, or cancellation anomaly

## 8. Downstream evidence

Technical completed, after which `MARKET_REGIME_SNAPSHOT` failed before producing a run-164 snapshot. No inference from deterministic tests is used here: live run-164 counts are Market Regime 0, Combined 0, Ranking 0, CERI 0, Setup 0, and Winner 0. Consequently, the live slice did not reach any required downstream gate or Winner.

The failure was terminal and deterministic. No code or data remediation was attempted in this task. A separate application remediation is required before another live admission or the final 100-symbol canary.

## 9. Unexpected mutations/jobs

From admission through post-run observation, the only new `background_jobs` row was root 43319. Run 164 has one pipeline, one parentless job, and one total job. No SEC repair, continuation, Winner maturation, cohort refresh, market prewarm, retry child, unrelated job, or second admission appeared. The terminal root row records `retry_count=1`, but no second job, second POST, continuation, or retry chain was created.

Protected-table deltas from the pre-run snapshot were exactly: upload runs +1, pipeline runs +1, background jobs +1, price bars +24, and Technical scores +10; Combined, Ranking, CERI, Setup, and Winner counts were unchanged. Run 164 additionally contains the expected 10 Fundamental rows, 10 Technical evidence rows, and one Technical source manifest. These writes match the single pipeline scope.

The controlled shutdown removed the web, worker, supervisor, and observability listeners. Ports 8000, 9101, 9102, 9090, and 3000 were closed; zero SwingLens business jobs remained active. PostgreSQL stayed running and IB Gateway remained listening on port 4002. No database backend termination was required. No manual database or runtime repair was performed.

## 10. Final gate

`SMALL LIVE VERTICAL SLICE: FAIL`

`READY FOR FINAL 100-SYMBOL LIVE CANARY: NO`
