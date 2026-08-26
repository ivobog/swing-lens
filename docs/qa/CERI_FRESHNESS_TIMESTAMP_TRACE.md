# CERI Freshness Timestamp Trace

## Exact pre-fix engines

| Dimension | Ops (pre-fix) | Ticker/scoring (pre-fix) |
|---|---|---|
| Scope | Latest row across any ticker per provider/dataset | Source IDs selected into one ticker snapshot |
| Service | `CeriQueryService.operations_status` | `CeriRunCaptureService.capture_run` -> `CeriConfidenceService.calculate` |
| Query/method | `_database_freshness_records` -> `_source_observed_at` | `_confidence_freshness_days` -> `_freshness_score` |
| Timestamp | `coalesce(source.observed_at, source.published_at, source.ingested_at)` | Per source: `retrieved_at or observed_at or published_at or ingested_at`; latest per dataset |
| Aggregation | Maximum timestamp for provider/dataset globally | Maximum source timestamp per dataset, then **maximum age across all datasets** |
| Threshold | Correct dataset threshold | **Always estimates threshold (7 days)** |
| Timezone | `datetime.now(UTC).date()` against DB-returned timestamp `.date()` | cutoff `.date()` against DB-returned timestamp `.date()`; policy timezone unused |
| Future behavior | Negative age accepted as fresh | Future values filtered; resulting age clamped with `max(0, ...)` |

Thus there was no single timestamp that correctly meant “the estimate is stale.” `estimate_data_stale` was emitted when the worst retrieval age among estimates, earnings, guidance, or catalysts made the confidence freshness subscore less than 6, using the estimates limit. This also had an off-by-one consequence: estimate age 8 scored 6 and did not warn even though 7 was the configured maximum.

Ops `eodhd / estimates / 0 days` was caused by source row **832869** (`KBH`, provider identity `KBH.US:NEXT_QUARTER:2026-11-30:EPS_DILUTED`): `observed_at=2026-08-26T01:05:50.983104+02:00`. It was the newest global `coalesce(observed_at,published_at,ingested_at)` value across any ticker, not a provider request timestamp and not an alerted-ticker timestamp.

## Dataset fallback chains (pre-fix)

- Ops estimates/earnings/catalysts/guidance: `observed_at -> published_at -> ingested_at`.
- Ticker API: `retrieved_at -> observed_at -> published_at -> ingested_at`, clamped to zero.
- Scoring confidence: same ticker-source fallback as the ticker API, future values excluded, then worst dataset age compared to 7.
- EODHD estimate mapping: provider `observedAt/updatedAt/lastUpdated`, else retrieval clock; fallback is labeled `RETRIEVAL_FALLBACK`.
- EODHD earnings mapping: no observation timestamp; `report_date` was passed as `published_at`, including upcoming events.
- EODHD catalysts: announcement/news publication timestamp.
- SEC guidance: SEC filing date as source/observed/published timestamp; retrieval stored separately.

## Post-fix canonical dimensions

- Provider feed freshness: latest `ceri_ingestion_runs.completed_at` with `status='COMPLETED'`, scoped by provider/dataset globally or by ticker/dataset for scoring.
- Evidence retrieval age: immutable source `retrieved_at` (fallback `ingested_at`).
- Evidence observation age: `source_timestamp -> observed_at -> published_at`; future values are rejected, then retrieval-only fallback is explicit.
- Usable feature age: revision feature `known_at` / `as_of_session`; it is not labeled provider freshness.
- Business/event dates (`report_at`, `expected_date`, `fiscal_period_end`) never participate in feed freshness.
- Calendar-day conversion is explicit in `America/New_York`, the configured CERI policy timezone.

## Representative row-level evidence

All timestamps below are database `timestamptz` rendered in the session timezone (`Europe/Berlin`, UTC+02). “First/last seen” is explicitly **not stored** on the general `ceri_source_records` table: `ingested_at` is the immutable first durable appearance; a duplicate has no durable `last_seen_at` or `last_confirmed_at`. A normalized row exists and its source has `quarantine_reason=NULL`, so the listed normalization state is accepted/available.

### A

- Latest estimate check at the alert cutoff: ingestion run **31479**, `eodhd/estimates`, scope `{run_id:126,ticker:A,worker_id:local-worker-2}`, started `2026-08-25T13:03:12.910282+02:00`, completed `13:03:15.313924+02:00`, `COMPLETED`; 48 fetched, 5 inserted/corrected, 43 deduplicated, 0 quarantined/failed.
- Raw estimate: source **821844**, identity `A.US:NEXT_FISCAL_YEAR:2027-10-31:EPS_DILUTED:baseline:90`, source/observed `2026-08-25T13:03:14.204602+02:00`, retrieved `13:03:14.396127`, ingested `13:03:14.407165`, content hash `c5d54d8...f94c699`, correction superseding **739583**; first/last-seen fields absent.
- Normalized estimate **134263**: EPS diluted / next fiscal year / fiscal end 2027-10-31; provider observed `2026-08-25T13:03:14.204602+02:00`, effective session 2026-05-27, analyst count 21, source currency unavailable, canonical scale 1, source 821844, accepted with relative-value/currency caveats.
- Revision feature **105326**: EPS diluted / next fiscal year, as-of 2026-08-25, 7-day window; baseline snapshot/source 134260/821841 at reference `2026-08-18T13:03:14.204602+02:00`, current snapshot/source 134259/821840 at `2026-08-25T13:03:14.204602+02:00`, 2 up/2 down, Normal, evidence hash `12011872...ceabd`.
- CERI snapshot **7081**, run 126, cutoff `2026-08-25T17:18:27.069840+02:00`, session 2026-08-25, confidence Normal, opportunity 3.1801, Event Risk 5.0, evidence hash `0f89f007...01655`; warnings include `estimate_data_stale` and `guidance_rows_rejected`.
- Legacy decision: earnings source **334865** `retrieved_at=2026-08-15T09:15:24.502307+02:00` won the worst-dataset-age aggregation; reference cutoff above, age 10 calendar days, estimates threshold 7, stale true. Change **9355** compares fresh snapshot 5715 -> 7081 (`COMPARABLE`); alert **912**. Canonical estimate-feed decision uses run 31479 completion, age 0, stale false.

### ADSK

- Check: run **31486**, `eodhd/estimates`, ticker ADSK/run 126, `2026-08-25T13:03:31.484751` -> `13:03:34.001751+02:00`, `COMPLETED`; 48 fetched, 19 inserted/corrected, 29 deduplicated, 0 quarantined/failed.
- Raw estimate **821988**, `ADSK.US:CURRENT_QUARTER:2026-07-31:REVENUE`, observed/source `2026-08-25T13:03:33.391686`, retrieved `13:03:33.860714`, ingested `13:03:33.865997+02:00`, hash `5e769b16...82e9fd`, correction superseding 801816; no first/last-seen fields.
- Normalized **134407**: revenue/current quarter/fiscal end 2026-07-31, effective session 2026-08-25, analyst count 26, currency unavailable/scale 1, source 821988, accepted with currency caveats.
- Feature **105494**: EPS diluted/next fiscal year, session 2026-08-25, window 7; baseline 134390/821971 at Aug 18, current 134389/821970 at Aug 25, 2 up/1 down, Normal, hash `352312aa...9920`.
- Snapshot **7073**, run 126, cutoff `2026-08-25T17:18:27.069840+02:00`, Normal, opportunity 3.57485, Event Risk 5.0, hash `3dffef30...081b`; stale plus coverage/unavailable warnings.
- Legacy driver: earnings source **340241**, retrieved `2026-08-15T12:53:06.621957+02:00`, age 10 vs threshold 7. Change **9347**, prior 6732 -> 7073, alert **906**. Canonical estimate check age 0, not stale.

### AGNC

- Check: run **33082**, `eodhd/estimates`, ticker AGNC/run 128, `2026-08-25T18:27:03.484092` -> `18:27:05.566386+02:00`, `COMPLETED`; all 48 rows deduplicated, 0 inserted/corrected/quarantined/failed.
- Raw estimate **342823**, `AGNC.US:NEXT_QUARTER:2026-12-31:EPS_DILUTED:baseline:90`, observed/source `2026-08-18T00:08:12.157008`, retrieved `00:08:12.295642`, ingested `00:08:12.301790+02:00`, hash `e26002dc...6402`, correction superseding 303207; no stored last-seen confirmation.
- Normalized **114822**: EPS diluted/next quarter/fiscal end 2026-12-31, effective session 2026-05-20, analyst count 12, scale 1/currency unavailable, source 342823, accepted with relative-value/currency caveats.
- Feature **114782**: EPS diluted/next fiscal year, session 2026-08-25, window 7; baseline 114814/342815 at Aug 11, current 114813/342814 at Aug 18, 5 up/3 down, Normal, hash `45595b37...a90e`.
- Snapshot **7295**, run 128, cutoff `2026-08-25T18:43:52.175347+02:00`, Normal, opportunity 2.38022, Event Risk 0, hash `809a5f40...5720`; stale plus rejected-guidance/coverage/unavailable warnings.
- Legacy driver: SEC guidance source **245136**, retrieved `2026-08-13T01:40:26.115050+02:00`, age 12 vs threshold 7. Change **9555**, prior 6452 -> 7295, alert **1028**. Estimate feed check age 0, not stale; immutable estimate evidence age is separately 8 New York dates.

### AMN

- Check: run **33087**, `eodhd/estimates`, ticker AMN/run 128, `2026-08-25T18:27:14.040482` -> `18:27:16.218090+02:00`, `COMPLETED`; 48/48 deduplicated, no errors or quarantine.
- Raw estimate **342975**, `AMN.US:CURRENT_QUARTER:2026-09-30:REVENUE`, observed/source `2026-08-18T00:08:22.555742`, retrieved `00:08:22.994750`, ingested `00:08:23.000205+02:00`, hash `0e51ebb8...e15e`, correction superseding 303526; no last-seen field.
- Normalized **114974**: revenue/current quarter/fiscal end 2026-09-30, session 2026-08-18, analyst count 9, currency unavailable/scale 1, source 342975, accepted with currency caveats.
- Feature **114854**: EPS diluted/next fiscal year, session 2026-08-25, window 7; baseline 114952/342953 at Aug 11, current 114951/342952 at Aug 18, 1 up/3 down, Normal, hash `144a7ed9...9bc`.
- Snapshot **7315**, run 128, cutoff `2026-08-25T18:43:52.175347+02:00`, Normal, opportunity 7.34494, Event Risk 0, hash `dc67eac5...35c`; stale plus coverage/unavailable warnings.
- Legacy driver: earnings source **330797**, retrieved `2026-08-14T08:34:03.160251+02:00`, age 11 vs threshold 7. Change **9564**, prior 6465 -> 7315, alert **1032**. Canonical estimate check age 0, not stale.

### XPEL

- Check: run **33206**, `eodhd/estimates`, ticker XPEL/run 128, `2026-08-25T18:31:06.632961` -> `18:31:08.226892+02:00`, `COMPLETED`; 48/48 deduplicated, no errors/quarantine.
- Raw estimate **309262**, `XPEL.US:CURRENT_QUARTER:2026-06-30:REVENUE`, observed/source `2026-08-14T00:53:22.140425`, retrieved `00:53:22.983370`, ingested `00:53:22.992944+02:00`, hash `81b5d956...1b3e`, correction superseding 300236; no last-seen field.
- Normalized **84476**: revenue/current quarter/fiscal end 2026-06-30, effective session 2026-08-14, analyst count 3, scale 1/currency unavailable, source 309262, accepted with currency caveats.
- Feature **116426**: revenue/next fiscal year, session 2026-08-25/window 7; current snapshot/source 64131/103436, no baseline, `UNAVAILABLE_BASELINE_NOT_ACCUMULATED`, hash `b2c39066...b73a`.
- Snapshot **7355**, run 128, cutoff `2026-08-25T18:43:52.175347+02:00`, confidence Insufficient, opportunity unrated, Event Risk 0, hash `35b2b8a5...ee1`; stale, sparse analyst, and feature/coverage unavailable warnings.
- Legacy driver: estimate, earnings, and catalyst retrieval ages each reached 11 days; estimate source 309262 was one chosen timestamp. Age 11 vs threshold 7, stale true. Change **9582**, prior 6374 -> 7355, alert **1041**. Canonical estimate check age 0, not stale.

## Full evidence hashes

| Ticker | Raw source content hash | Revision feature evidence hash | CERI snapshot evidence hash |
|---|---|---|---|
| A | `c5d54d8faf1b1ea98d80dae4f8d292a13ae9ec8f4959925620991a015f94c699` | `12011872e523153ff01d2e58ac75abe88d0d73fb1049132d27d20215832ceabd` | `0f89f0071e4fd417122639f45aa9a0031cd1c05f2ff2160c01f0a33af7c01655` |
| ADSK | `5e769b16d2f0ec6645b3b39d51434e9e951e9955a097fc402a3d7e179882e9fd` | `352312aa5724fd1e2eb609138b390b434009d6894c54cea453f4ab75e2539920` | `3dffef301728b731b538827e8f4fe1a121c6ffb813dc3982a9997d78f148081b` |
| AGNC | `e26002dc4e6939a87684735143167928ad60a6f1ffc31056406dbdf5e9a66402` | `45595b378b3d10ef26cc9cdbe68b40f34f4dde5ca10e674186f68275b7fda90e` | `809a5f40887d1f7f1dbe28d5648c58b6d37148c44d2f190c945c7b029e7d5720` |
| AMN | `0e51ebb84b8d3eeb8a6384d83b970a1a468a9aca1e05cd9b00638f3d7f96e15e` | `144a7ed9861b3ba970925bacafc087c6ebd2043c0b64dc9fc7d78d7ebc1269bc` | `dc67eac5bac6b79e08adb7487fae281882eb005f466d8ecfcc8e48d8e787535c` |
| XPEL | `81b5d9565ec6cecd2fd9fa21fc390a98d1e698bc5a0de8c2a96d54106e501b3e` | `b2c3906640256b62ecb86dcd52bfd0bde66ecb78f0114e8cde02a6ff3413b73a` | `35b2b8a5767cdf3346619b89be3a10a75289e224d975a534fb81d7427ab24ee1` |
