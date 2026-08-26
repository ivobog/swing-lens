# CERI Earnings Negative-Age Audit

## Root cause

EODHD upcoming earnings records used the scheduled `report_date` as `CeriSourceRecord.published_at`. Ops chose `coalesce(observed_at,published_at,ingested_at)`, so a future scheduled event won over retrieval time.

The row that produced -70 days was source record 340160:

- Provider identity: `HOG.US:2026-11-04`
- Event kind: `UPCOMING`
- Scheduled report: 2026-11-04
- Legacy `published_at`: 2026-11-04
- Provider retrieval: 2026-08-15
- Ops reference date: 2026-08-26

Baseline counts: 179 EODHD earnings source rows had future `published_at` and a negative legacy Ops age; minimum was -70. There were 461 normalized future earnings events, which are valid event records but not freshness timestamps.

## Fix

- Upcoming earnings keep `report_at`/`source_date` as event fields.
- They no longer populate `published_at`.
- New payloads carry `source_timestamp_semantics=EVENT_DATE_NOT_PUBLICATION_V1`, causing a corrected immutable superseding row on re-ingestion instead of deduplicating to the legacy malformed row.
- Ops provider feed age reads the successful ingestion completion, so it cannot use an event date.
- Evidence timestamp selection rejects future source/published fields and explicitly falls back to `retrieved_at` with `RETRIEVAL_ONLY` quality.

Historical malformed rows are not rewritten. Re-ingesting earnings will naturally supersede them; a destructive backfill is neither required nor permitted.

Classification: `NEGATIVE_AGE_EVENT_DATE_BUG` and `TIMESTAMP_SEMANTICS_BUG`, High severity. User impact was misleading health reporting and possible operator complacency; scoring already filtered future timestamps and did not receive a negative age.
