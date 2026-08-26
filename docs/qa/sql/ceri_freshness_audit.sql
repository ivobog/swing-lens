-- SwingLens CERI freshness forensic audit (PostgreSQL, read-only)
-- Baseline date: 2026-08-26. No statement in this file mutates data.
BEGIN TRANSACTION READ ONLY;

-- 1. Latest successful provider check per provider/dataset (canonical feed freshness).
SELECT provider,
       dataset,
       max(completed_at) FILTER (WHERE status = 'COMPLETED') AS last_successful_check_at,
       current_date - max(completed_at::date) FILTER (WHERE status = 'COMPLETED') AS age_days
FROM ceri_ingestion_runs
GROUP BY provider, dataset
ORDER BY provider, dataset;

-- 2. Latest successful provider check per ticker/dataset.
SELECT upper(scope_json ->> 'ticker') AS ticker,
       provider,
       dataset,
       max(completed_at) FILTER (WHERE status = 'COMPLETED') AS last_successful_check_at,
       current_date - max(completed_at::date) FILTER (WHERE status = 'COMPLETED') AS age_days
FROM ceri_ingestion_runs
WHERE scope_json ->> 'ticker' IS NOT NULL
GROUP BY upper(scope_json ->> 'ticker'), provider, dataset
ORDER BY ticker, provider, dataset;

-- 3. Latest usable normalized estimate per ticker.
WITH ranked AS (
    SELECT c.ticker,
           e.*,
           s.provider,
           s.provider_record_id,
           s.quarantine_reason,
           row_number() OVER (
               PARTITION BY c.id
               ORDER BY coalesce(e.retrieved_at, s.retrieved_at, s.ingested_at) DESC, e.id DESC
           ) AS row_number
    FROM ceri_companies c
    JOIN ceri_estimate_snapshots e ON e.company_id = c.id
    JOIN ceri_source_records s ON s.id = e.source_record_id
    WHERE e.consensus IS NOT NULL
      AND s.quarantine_reason IS NULL
)
SELECT ticker,
       id AS estimate_snapshot_id,
       source_record_id,
       provider,
       provider_record_id,
       metric,
       period_type,
       fiscal_period_end,
       provider_observed_at,
       effective_at,
       effective_session,
       known_at,
       retrieved_at,
       quality_flags_json
FROM ranked
WHERE row_number = 1
ORDER BY ticker;

-- 4. Latest normalized earnings evidence per ticker; event and retrieval dates remain separate.
WITH ranked AS (
    SELECT c.ticker,
           e.*,
           s.provider_record_id,
           s.published_at,
           s.retrieved_at,
           row_number() OVER (
               PARTITION BY c.id
               ORDER BY coalesce(s.retrieved_at, s.ingested_at) DESC, e.id DESC
           ) AS row_number
    FROM ceri_companies c
    JOIN ceri_earnings_actuals e ON e.company_id = c.id
    JOIN ceri_source_records s ON s.id = e.source_record_id
)
SELECT ticker,
       id AS earnings_actual_id,
       source_record_id,
       provider_record_id,
       event_kind,
       report_at,
       fiscal_period_end,
       published_at AS legacy_source_publication_field,
       retrieved_at
FROM ranked
WHERE row_number = 1
ORDER BY ticker;

-- 5. Latest EODHD catalyst provider check per ticker.
SELECT upper(scope_json ->> 'ticker') AS ticker,
       max(completed_at) FILTER (WHERE status = 'COMPLETED') AS last_successful_check_at,
       current_date - max(completed_at::date) FILTER (WHERE status = 'COMPLETED') AS age_days
FROM ceri_ingestion_runs
WHERE provider = 'eodhd'
  AND dataset = 'catalysts'
  AND scope_json ->> 'ticker' IS NOT NULL
GROUP BY upper(scope_json ->> 'ticker')
ORDER BY ticker;

-- 6. Latest SEC guidance provider check per ticker.
SELECT upper(scope_json ->> 'ticker') AS ticker,
       max(completed_at) FILTER (WHERE status = 'COMPLETED') AS last_successful_check_at,
       current_date - max(completed_at::date) FILTER (WHERE status = 'COMPLETED') AS age_days
FROM ceri_ingestion_runs
WHERE provider = 'sec'
  AND dataset = 'guidance'
  AND scope_json ->> 'ticker' IS NOT NULL
GROUP BY upper(scope_json ->> 'ticker')
ORDER BY ticker;

-- 7. Latest CERI snapshot per ticker.
WITH ranked AS (
    SELECT s.*,
           row_number() OVER (
               PARTITION BY company_id
               ORDER BY cutoff_at DESC, id DESC
           ) AS row_number
    FROM ceri_score_snapshots s
    WHERE controlled_replay_id IS NULL
)
SELECT ticker,
       id AS snapshot_id,
       run_id,
       cutoff_at,
       as_of_session,
       data_confidence,
       opportunity_score,
       event_risk_score,
       warnings_json,
       evidence_hash,
       config_version,
       config_hash,
       calculation_version
FROM ranked
WHERE row_number = 1
ORDER BY ticker;

-- 8. Tickers with estimate_data_stale in the latest affected run.
WITH latest_affected_run AS (
    SELECT run_id
    FROM ceri_score_snapshots
    WHERE warnings_json ? 'estimate_data_stale'
      AND run_id IS NOT NULL
    ORDER BY cutoff_at DESC, run_id DESC
    LIMIT 1
)
SELECT s.ticker,
       s.id AS snapshot_id,
       s.run_id,
       s.cutoff_at,
       s.data_confidence,
       s.warnings_json,
       s.confidence_ledger_json
FROM ceri_score_snapshots s
JOIN latest_affected_run r ON r.run_id = s.run_id
WHERE s.warnings_json ? 'estimate_data_stale'
ORDER BY s.ticker;

-- 9. Estimate evidence-age distribution (usable normalized estimates).
WITH latest AS (
    SELECT e.company_id,
           max(coalesce(e.retrieved_at, s.retrieved_at, s.ingested_at)) AS retrieved_at
    FROM ceri_estimate_snapshots e
    JOIN ceri_source_records s ON s.id = e.source_record_id
    WHERE e.consensus IS NOT NULL
      AND s.quarantine_reason IS NULL
    GROUP BY e.company_id
), ages AS (
    SELECT c.id,
           c.ticker,
           current_date - latest.retrieved_at::date AS age_days
    FROM ceri_companies c
    LEFT JOIN latest ON latest.company_id = c.id
)
SELECT count(*) AS total_tracked,
       count(*) FILTER (WHERE age_days <= 7) AS fresh,
       count(*) FILTER (WHERE age_days > 7) AS stale,
       count(*) FILTER (WHERE age_days IS NULL) AS missing,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY age_days) AS p50_age_days,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY age_days) AS p90_age_days,
       max(age_days) AS max_age_days
FROM ages;

-- 10. Estimate feed fresh/stale/missing distribution (canonical scoring semantic).
WITH latest AS (
    SELECT upper(scope_json ->> 'ticker') AS ticker,
           max(completed_at) FILTER (WHERE status = 'COMPLETED') AS completed_at
    FROM ceri_ingestion_runs
    WHERE dataset = 'estimates'
      AND scope_json ->> 'ticker' IS NOT NULL
    GROUP BY upper(scope_json ->> 'ticker')
), ages AS (
    SELECT c.ticker,
           current_date - latest.completed_at::date AS age_days
    FROM ceri_companies c
    LEFT JOIN latest ON latest.ticker = c.ticker
)
SELECT count(*) AS total_tracked,
       count(*) FILTER (WHERE age_days <= 7) AS fresh,
       count(*) FILTER (WHERE age_days > 7) AS stale,
       count(*) FILTER (WHERE age_days IS NULL) AS missing,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY age_days) AS p50_age_days,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY age_days) AS p90_age_days,
       max(age_days) AS max_age_days
FROM ages;

-- 11. Future-dated timestamp fields that legacy freshness fallback could select.
SELECT provider,
       dataset,
       count(*) FILTER (WHERE source_timestamp > now()) AS future_source_timestamp,
       count(*) FILTER (WHERE observed_at > now()) AS future_observed_at,
       count(*) FILTER (WHERE published_at > now()) AS future_published_at,
       count(*) FILTER (
           WHERE coalesce(observed_at, published_at, ingested_at) > now()
       ) AS legacy_negative_age_rows
FROM ceri_source_records
GROUP BY provider, dataset
ORDER BY provider, dataset;

-- 12. Rows where the legacy Ops calculation produces age < 0.
SELECT id,
       provider,
       dataset,
       provider_record_id,
       company_hint_json ->> 'ticker' AS ticker,
       observed_at,
       published_at,
       retrieved_at,
       ingested_at,
       current_date - coalesce(observed_at, published_at, ingested_at)::date AS legacy_age_days
FROM ceri_source_records
WHERE current_date - coalesce(observed_at, published_at, ingested_at)::date < 0
ORDER BY legacy_age_days, id;

-- 13. Exact old scoring fallback trace for selected snapshots.
WITH target AS (
    SELECT *
    FROM ceri_score_snapshots
    WHERE id IN (7295, 7315, 7355, 7081, 7073)
), source_ids AS (
    SELECT t.id AS snapshot_id,
           t.ticker,
           t.cutoff_at,
           jsonb_array_elements_text(t.component_json -> 'source_ids')::bigint AS source_id
    FROM target t
), ranked AS (
    SELECT i.*,
           s.dataset,
           s.provider_record_id,
           s.source_timestamp,
           s.observed_at,
           s.published_at,
           s.retrieved_at,
           s.ingested_at,
           coalesce(s.retrieved_at, s.observed_at, s.published_at, s.ingested_at)
               AS legacy_scoring_timestamp,
           row_number() OVER (
               PARTITION BY i.snapshot_id, s.dataset
               ORDER BY coalesce(
                   s.retrieved_at, s.observed_at, s.published_at, s.ingested_at
               ) DESC
           ) AS row_number
    FROM source_ids i
    JOIN ceri_source_records s ON s.id = i.source_id
    WHERE coalesce(s.retrieved_at, s.observed_at, s.published_at, s.ingested_at)
          <= i.cutoff_at
)
SELECT ticker,
       snapshot_id,
       dataset,
       source_id,
       provider_record_id,
       legacy_scoring_timestamp,
       cutoff_at::date - legacy_scoring_timestamp::date AS legacy_age_days
FROM ranked
WHERE row_number = 1
ORDER BY ticker, dataset;

-- 14. DATA_STALE / DATA_REFRESHED transition, dedup, and alert counts.
SELECT change_type,
       count(*) AS change_count,
       count(a.id) AS alert_count,
       count(DISTINCT c.dedup_key) AS distinct_change_identities,
       count(DISTINCT a.event_key) AS distinct_alert_identities
FROM ceri_change_events c
LEFT JOIN ceri_alert_events a ON a.source_change_event_id = c.id
WHERE change_type IN ('DATA_STALE', 'DATA_REFRESHED')
GROUP BY change_type
ORDER BY change_type;

-- 15. Repeated stale snapshots must not produce repeated DATA_STALE changes.
WITH ordered AS (
    SELECT company_id,
           id,
           warnings_json ? 'estimate_data_stale' AS stale,
           lag(warnings_json ? 'estimate_data_stale') OVER (
               PARTITION BY company_id ORDER BY cutoff_at, id
           ) AS prior_stale
    FROM ceri_score_snapshots
    WHERE controlled_replay_id IS NULL
)
SELECT count(*) AS repeated_stale_snapshot_pairs,
       count(*) FILTER (
           WHERE EXISTS (
               SELECT 1
               FROM ceri_change_events c
               WHERE c.to_snapshot_id = ordered.id
                 AND c.change_type = 'DATA_STALE'
           )
       ) AS erroneous_repeated_stale_changes
FROM ordered
WHERE stale AND prior_stale;

-- 16. Snapshot/change comparison-state drift caused by scoped historical rebuilds.
SELECT count(*) AS mismatched_change_rows,
       count(DISTINCT change.to_snapshot_id) AS affected_snapshots,
       count(DISTINCT snapshot.ticker) AS affected_tickers,
       count(DISTINCT snapshot.run_id) AS affected_runs,
       min(snapshot.run_id) AS first_run,
       max(snapshot.run_id) AS last_run
FROM ceri_change_events change
JOIN ceri_score_snapshots snapshot ON snapshot.id = change.to_snapshot_id
WHERE change.comparison_state = 'COMPARABLE'
  AND snapshot.comparison_state <> 'COMPARABLE';

ROLLBACK;
