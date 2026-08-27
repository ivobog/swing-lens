# CERI Freshness Remediation Plan

## Implemented minimal semantic fix

1. Define provider feed freshness from successful ingestion-run completion.
2. Make Ops provider-global feed health and ticker-level scoring share the same timestamp/age function.
3. Restrict `estimate_data_stale` to estimates feed age and its 7-day threshold.
4. Preserve immutable evidence age as separate retrieval and observation metrics.
5. Reject future business/event dates from evidence timestamp fallback.
6. Stop mapping upcoming earnings event dates to publication timestamps.
7. Expose Ops ticker coverage and ticker feed/evidence details in the UI/API.
8. Resolve runtime config paths from application settings.
9. Bump calculation/config versions so v1.2 and v1.3 snapshots are non-comparable; this prevents a synthetic bulk `DATA_REFRESHED` transition at the semantic boundary.
10. Make scoped change rebuilds select comparison history outside the requested output scope, preventing historical snapshot comparison metadata from being reset.
11. Derive legacy freshness-alert validity at query time: hide pre-provider-feed stale alerts from the active feed while retaining them under the Invalidated forensic filter.

## Deployment/reprocessing

- Apply no schema migration; Alembic remains at 0056.
- Deploy code/config together.
- Run a normal CERI provider-ingest/feature/capture cycle under `ceri-1.3.0`.
- Do not replay or rewrite historical score/source rows.
- Optionally re-ingest upcoming earnings so new corrected immutable rows supersede legacy future-`published_at` records.
- Optionally run a separate audited classification job to label 303 historical stale changes and 302 alerts as legacy-semantic-invalid. Do not delete them.
- Optionally repair the 685 historical snapshot comparison metadata rows only through a separately authorized, logged migration/backfill; this investigation does not mutate them, and change rows retain their own valid comparison lineage.
- The first v1.3 snapshot is a calculation-version boundary and emits no stale/refreshed transition. Subsequent comparable v1.3 snapshots exercise the canonical state machine.

## Deferred enhancements

- Persist per-snapshot last successful check timestamps in a dedicated freshness ledger if high-volume historical UI queries require it. Current ingestion runs already provide durable lineage, so a source-row `last_seen_at` migration is not required.
- Add rejected-only/NONE_FOUND coverage columns to Ops when provider request results persist an explicit per-ticker evidence outcome. Do not infer NONE_FOUND from zero inserted rows alone.
- Add a configured `DATA_REFRESHED` alert rule only if product requirements call for operator notification; the change already exists.
