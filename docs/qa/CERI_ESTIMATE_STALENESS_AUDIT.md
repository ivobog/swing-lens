# CERI Estimate Staleness Audit

## Population

At baseline, usable estimate evidence across 942 tracked companies was: 584 fresh (62.00%), 337 stale (35.77%), 21 missing (2.23%), 0 rejected-only. Evidence age p50 was 6 days, p90 12 days, maximum 17 days.

Canonical EODHD estimate **feed-check** coverage in the configured New York date was: 718 fresh (76.22%), 219 stale (23.25%), 5 missing (0.53%). Feed and evidence are therefore materially different dimensions and both are retained.

Run 130 contained 214/332 (64.46%) `estimate_data_stale` warnings. All 214 had an estimate provider check age of 0-1 days. Replacing the conflated freshness subscore with the canonical estimate feed subscore raises average confidence from 6.61 to 7.98; 27 tickers cross from Low to Normal. Opportunity values are not directly freshness-weighted. Event Risk did not use this confidence warning (see state-machine audit).

## Representative trace

| Ticker | Latest estimate request | Request outcome | Latest immutable estimate source | Normalized estimate | Revision feature | Stale snapshot/change | Actual pre-fix stale driver |
|---|---|---|---|---|---|---|---|
| A | 34166, Aug 26 00:54+02 | 48 fetched, 48 deduped | 821844, Aug 25 retrieval | 134263 | 105326 | snapshot 7081 / change 9355 | earnings source 334865 retrieved Aug 15, age 10 |
| ADSK | 34172, Aug 26 00:54+02 | 48 fetched, 48 deduped | 821988, Aug 25 retrieval | 134407 | 105494 | snapshot 7073 / change 9347 | earnings source 340241 retrieved Aug 15, age 10 |
| AGNC | 33594, Aug 25 23:59+02 | 48 fetched, 48 deduped | 342823, Aug 18 retrieval | 114822 | 114782 | snapshot 7295 / change 9555 | guidance source 245136 retrieved Aug 13, age 12 |
| AMN | 33599, Aug 25 23:59+02 | 48 fetched, 48 deduped | 342975, Aug 18 retrieval | 114974 | 114854 | snapshot 7315 / change 9564 | earnings source 330797 retrieved Aug 14, age 11 |
| XPEL | 33718, Aug 26 00:03+02 | 48 fetched, 48 deduped | 309262, Aug 14 retrieval | 84476 | 116426 | snapshot 7355 / change 9582 | estimate/earnings/catalyst retrieval age 11 |

All five estimate requests completed successfully with no failed or quarantined records. Their unchanged values were deduplicated.

## Dedup semantics

`CeriSourceRecordService.store_source_record` hashes the economic payload. If the provider identity and content hash already exist, it returns the immutable row and does not change `retrieved_at`. `ceri_source_records` has no general `last_seen_at`/`last_confirmed_at` field. Therefore “unchanged value” previously left source retrieval age old even though a fresh `ceri_ingestion_runs` row proved the provider was checked.

The fix does not mutate source evidence. Feed freshness now reads the completed ingestion run; evidence age continues to read the immutable source. No schema migration is required.

## Classification

- `TIMESTAMP_SEMANTICS_BUG` / High: all-dataset retrieval ages were mislabeled estimate freshness.
- `DEDUP_LAST_SEEN_BUG` / High: immutable dedup timestamps were incorrectly reused as check health.
- `GLOBAL_VS_TICKER_SCOPE_MISMATCH` / Medium-High: provider-global Ops status hid ticker coverage.

Affected tables: `ceri_ingestion_runs`, `ceri_source_records`, `ceri_estimate_snapshots`, `ceri_revision_features`, `ceri_score_snapshots`, `ceri_change_events`, `ceri_alert_events`. Historical rows remain immutable.
