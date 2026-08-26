# CERI Freshness Forensic Baseline

Captured before semantic code changes on 2026-08-26 (Europe/Zurich).

## Repository and runtime

- Branch: `codex/fix-main-ci-h5-ceri`
- HEAD: `dee45dabc8fd46e491ce958bcde1d3ff83d8c5b1`
- Worktree: tracked files clean; pre-existing untracked `output/` recovery artifacts preserved.
- Database: local PostgreSQL `swinglens`; database session timezone `Europe/Berlin`.
- Alembic current/head: `0056_cover_ceri_freshness` / `0056_cover_ceri_freshness`.
- Baseline tests: `pytest -q tests/ceri` => `404 passed, 1 warning in 39.27s`.
- CERI runtime flags: engine, provider ingest, run capture, UI, alerts, admin, backfill all enabled. YAML `engine.enabled=false` is deprecated and is not the runtime authority.
- Relevant `.env` overrides: `CERI_ENABLED=true`, `CERI_PROVIDER_INGEST_ENABLED=true`, `CERI_RUN_CAPTURE_ENABLED=true`, `CERI_UI_ENABLED=true`, `CERI_ALERTS_ENABLED=true`, `CERI_ADMIN_ENABLED=true`, `CERI_BACKFILL_ENABLED=true`, `CERI_LEGACY_PIPELINE_SCHEDULING_ENABLED=false`, `CERI_BATCHED_WORKFLOW_ENABLED=true`, and `CERI_CONFIG_PATH=config/ceri.yaml` / `CERI_TAXONOMY_PATH=config/ceri_catalyst_taxonomy.yaml`.

## Baseline effective configuration

- Calculation version: `ceri-1.2.0`
- Config version: `2026-08-14-changes-alerts-remediation-r1`
- Config hash: `d7686bfd4eee87c3c72492941e250bd8131c817a849fa617b880b8afda1d5c2b`
- Policy timezone: `America/New_York`; legacy freshness code did not consistently use it.
- Estimates: 7 calendar days
- Catalysts: 2 calendar days
- Earnings: 30 calendar days
- Guidance: 14 calendar days

## Baseline production evidence

- Tracked CERI companies: 942.
- Latest run 130: 332 snapshots; 214 (64.46%) contained `estimate_data_stale`.
- Run 129: 100 snapshots; 70 stale warnings.
- Run 128: 121 snapshots; 84 stale warnings.
- Run 126: 393 snapshots; 254 stale warnings.
- Durable `DATA_STALE`: 303 changes, 302 alerts.
- Durable `DATA_REFRESHED`: 421 changes, 0 alerts (no alert rule is configured for refresh).
- Repeated stale pairs: 1,824; erroneous repeated `DATA_STALE` changes: 0.
- Legacy negative-age rows: 179 EODHD earnings source rows; minimum age -70 days.

No production row was changed, deleted, reclassified, or replayed during the investigation.
