# SEC Guidance Normalization Performance

## Defect confirmation

Run 111 normalized SEC guidance with a Python-side load of the entire 55,304-row `ceri_guidance_events` table for every new source record. Identity resolution also selected all 817 companies and 87 aliases per record. Observed examples included 2,027 rows taking 6,628,463 ms, 708 rows taking 2,707,140 ms, and 55 rows taking 195,500 ms.

## Changes

- Replaced `_prior_guidance()` table-wide loading with a bounded SQL predicate and `ORDER BY effective_at DESC, id DESC LIMIT 1`.
- Prefetched immutable company and alias snapshots once per normalization batch.
- Reduced SEC identity persistence to one targeted company/CIK operation per batch identity.
- Changed source-record idempotency probes to select only the indexed identifier.
- Added `ix_ceri_guidance_events_prior_lookup (company_id, metric, period_type, effective_at, id)`.
- Added `ix_ceri_source_records_ingestion_id (ingestion_run_id, id)`.
- Limited durable processing-run checkpoint updates to the configured checkpoint interval.

## PostgreSQL plans

| Query | Before | After |
|---|---|---|
| Prior guidance sample | Bitmap scan by company, 1,238 rows visited, filter + top-N sort, 3.687 ms | Backward composite-index scan, one row, 0.143 ms |
| Source records for ingestion 9807 (2,574 rows) | Parallel scan of 443,009-row table + sort, 308.118 ms | Ordered composite-index scan, 37.195 ms |

The database revision after migration is `0048_sec_guidance_normalization_performance`.

## Rollback-only representative workload

The live-data test normalized 195 unprocessed SEC source records for HG inside a transaction that was rolled back. No canonical rows or processing-run state were retained.

| Metric | After |
|---|---:|
| Records read / normalized / failed | 195 / 195 / 0 |
| Elapsed | 3.316 s |
| SQL statements | 825 |
| SQL SELECTs | 395 |
| Full-table guidance loads per record | 0 |
| Full company loads per record | 0 |
| Full alias loads per record | 0 |
| Company-table full loads per batch | 1 |
| Alias-table full loads per batch | 1 |
| Bounded prior-guidance lookups | 193 |
| Source-record idempotency probes | 195 |
| Slow repeated statement groups (>=100 ms aggregate) | 4 |

The most repeated SELECTs are bounded indexed prior-guidance lookups and indexed source-record existence probes. No `SELECT * FROM ceri_guidance_events`, `ceri_companies`, or `ceri_company_aliases` occurs per source record.
