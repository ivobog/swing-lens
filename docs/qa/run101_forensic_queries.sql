-- CERI run 101 forensic certification queries.
-- Execute only against a preserved copy or inside this read-only transaction.
BEGIN TRANSACTION READ ONLY;

-- Run and input universe.
SELECT id, filename, uploaded_at, processed_at, row_count, status
FROM upload_runs WHERE id = 101;

SELECT count(*) AS rows,
       count(DISTINCT upper(trim(ticker))) AS tickers
FROM raw_company_rows WHERE run_id = 101;

-- Snapshot reconciliation and processor/configuration identity.
SELECT count(*) AS snapshots,
       count(DISTINCT ticker) AS tickers,
       count(DISTINCT company_id) AS companies,
       count(*) FILTER (WHERE opportunity_score IS NULL) AS null_scores,
       count(*) FILTER (WHERE posture = 'Unrated') AS unrated,
       min(cutoff_at) AS first_cutoff,
       max(cutoff_at) AS last_cutoff,
       calculation_version,
       config_version,
       config_hash
FROM ceri_score_snapshots
WHERE run_id = 101
GROUP BY calculation_version, config_version, config_hash;

SELECT ticker, count(*)
FROM ceri_score_snapshots
WHERE run_id = 101
GROUP BY ticker HAVING count(*) <> 1;

-- Coverage, score and confidence distribution.
SELECT posture,
       data_confidence,
       opportunity_coverage_pct,
       coverage_pct AS confidence_coverage_percent,
       confidence_ledger_json->>'score' AS raw_confidence_score,
       count(*)
FROM ceri_score_snapshots
WHERE run_id = 101
GROUP BY 1, 2, 3, 4, 5 ORDER BY 3, 5;

-- Run-scoped ingestion reconciliation.
SELECT provider, dataset, status,
       count(*) AS runs,
       sum(requested_count) AS requested,
       sum(fetched_count) AS fetched,
       sum(inserted_count) AS inserted,
       sum(deduplicated_count) AS deduplicated,
       sum(corrected_count) AS corrected,
       sum(quarantined_count) AS quarantined,
       sum(failed_count) AS failed,
       sum(retry_count) AS retries
FROM ceri_ingestion_runs
WHERE scope_json->>'run_id' = '101'
GROUP BY provider, dataset, status
ORDER BY provider, dataset, status;

-- Historical source records created by run 101 and their logical storage size.
SELECT sr.provider, sr.dataset, count(*) AS records,
       sum(pg_column_size(sr.*)) AS logical_row_bytes,
       sum(pg_column_size(sr.raw_json)) AS raw_json_bytes,
       sum(pg_column_size(sr.restricted_normalized_json)) AS restricted_json_bytes
FROM ceri_source_records sr
JOIN ceri_ingestion_runs ir ON ir.id = sr.ingestion_run_id
WHERE ir.scope_json->>'run_id' = '101'
GROUP BY sr.provider, sr.dataset
ORDER BY sr.provider, sr.dataset;

-- EODHD estimate usability and currency integrity.
SELECT count(*) AS estimates,
       count(*) FILTER (WHERE canonical_currency IS NULL) AS missing_canonical_currency,
       count(*) FILTER (WHERE currency_verified IS TRUE) AS verified_currency,
       count(*) FILTER (WHERE known_at > '2026-08-13 19:12:51.330758+02') AS known_after_cutoff,
       count(*) FILTER (WHERE effective_at > '2026-08-13 19:12:51.330758+02') AS effective_after_cutoff
FROM ceri_estimate_snapshots es
JOIN ceri_source_records sr ON sr.id = es.source_record_id
JOIN ceri_ingestion_runs ir ON ir.id = sr.ingestion_run_id
WHERE ir.scope_json->>'run_id' = '101' AND sr.provider = 'eodhd';

-- Revision feature result: the expected 24 slots per ticker exist, but are unusable.
SELECT count(*) AS features,
       count(DISTINCT company_id) AS companies,
       count(*) FILTER (WHERE current_snapshot_id IS NOT NULL
                         AND baseline_snapshot_id IS NOT NULL) AS usable_features
FROM ceri_revision_features
WHERE as_of_session = '2026-08-13'
  AND config_hash = 'aff83bb918fee7febe22dc35c66489178bbacce9d41cb9e88fc5a31f9434d677'
  AND company_id IN (
    SELECT DISTINCT company_id FROM ceri_score_snapshots WHERE run_id = 101
  );

-- Earnings/surprise values created by run 101.
SELECT count(*) AS actual_rows,
       count(*) FILTER (WHERE actual_value IS NOT NULL) AS actual_values,
       count(*) FILTER (WHERE provider_consensus_value IS NOT NULL) AS provider_consensus_values,
       count(*) FILTER (WHERE surprise_pct IS NOT NULL) AS computed_surprises
FROM ceri_earnings_actuals ea
JOIN ceri_source_records sr ON sr.id = ea.source_record_id
JOIN ceri_ingestion_runs ir ON ir.id = sr.ingestion_run_id
WHERE ir.scope_json->>'run_id' = '101' AND sr.provider = 'eodhd';

-- Catalyst semantic eligibility in the current universe.
WITH universe AS (
  SELECT DISTINCT company_id FROM ceri_score_snapshots WHERE run_id = 101
)
SELECT count(*) AS revisions,
       count(*) FILTER (WHERE issuer_relevance IS TRUE) AS issuer_relevant,
       count(*) FILTER (WHERE binary_eligible IS TRUE) AS binary_eligible,
       count(*) FILTER (WHERE materiality > 0) AS material
FROM ceri_catalyst_event_revisions r
JOIN ceri_catalyst_events e ON e.id = r.catalyst_event_id
JOIN universe u ON u.company_id = e.company_id
WHERE r.is_current IS TRUE;

-- SEC run outcome and durable-registry state.
SELECT status, count(*) AS ticker_runs, sum(failed_count) AS failures,
       sum(requested_count) AS requested, sum(fetched_count) AS fetched,
       sum(inserted_count) AS inserted
FROM ceri_ingestion_runs
WHERE provider = 'sec' AND dataset = 'guidance'
  AND scope_json->>'run_id' = '101'
GROUP BY status;

SELECT x.status, count(*) AS documents, sum(d.last_content_bytes) AS bytes
FROM ceri_sec_filing_documents d
JOIN ceri_sec_document_extractions x ON x.document_id = d.id
WHERE x.processor_signature = 'sec-guidance:910cfd73179f55a7'
  AND d.cik IN (
    SELECT DISTINCT c.cik
    FROM ceri_ingestion_runs ir
    JOIN ceri_companies c ON c.ticker = ir.scope_json->>'ticker'
    WHERE ir.provider = 'sec' AND ir.scope_json->>'run_id' = '101'
      AND c.cik IS NOT NULL
  )
GROUP BY x.status;

-- Legacy SEC evidence acceptance and selected false positives.
SELECT count(*) AS guidance_rows,
       count(*) FILTER (WHERE accepted_for_scoring IS TRUE) AS explicitly_accepted,
       count(*) FILTER (WHERE accepted_for_scoring IS NULL) AS acceptance_unknown
FROM ceri_guidance_events g
WHERE g.company_id IN (
  SELECT DISTINCT company_id FROM ceri_score_snapshots WHERE run_id = 101
);

SELECT g.id, c.ticker, g.action, g.metric, g.period_type,
       g.low_value, g.high_value, g.point_value, g.unit,
       g.confidence, g.accepted_for_scoring, g.effective_session,
       g.filing_accession, g.evidence_locator, g.comparison_basis
FROM ceri_guidance_events g
JOIN ceri_companies c ON c.id = g.company_id
WHERE g.id IN (4281, 191, 192, 18207, 23105)
ORDER BY c.ticker, g.id;

-- Point-in-time check over every source record named in a run-101 snapshot.
WITH source_ids AS (
  SELECT DISTINCT
    (jsonb_array_elements_text(COALESCE(component_json->'source_ids', '[]')))::bigint AS id
  FROM ceri_score_snapshots WHERE run_id = 101
)
SELECT count(*) AS source_refs,
       count(*) FILTER (
         WHERE COALESCE(sr.source_timestamp, sr.observed_at, sr.published_at,
                        sr.retrieved_at, sr.ingested_at)
               > '2026-08-13 19:12:51.330758+02'
       ) AS source_refs_after_cutoff
FROM source_ids ids JOIN ceri_source_records sr ON sr.id = ids.id;

-- Lifecycle regression and alerts.
SELECT ce.change_type, count(*)
FROM ceri_change_events ce
JOIN ceri_score_snapshots s ON s.id = ce.to_snapshot_id
WHERE s.run_id = 101
GROUP BY ce.change_type ORDER BY ce.change_type;

SELECT count(*) AS null_to_null_upgrades
FROM ceri_change_events ce
JOIN ceri_score_snapshots s ON s.id = ce.to_snapshot_id
WHERE s.run_id = 101 AND ce.change_type = 'OPPORTUNITY_UPGRADED'
  AND ce.from_snapshot_id IS NULL AND s.opportunity_score IS NULL;

SELECT count(*) AS alerts, count(DISTINCT s.ticker) AS tickers
FROM ceri_alert_events ae
JOIN ceri_change_events ce ON ce.id = ae.source_change_event_id
JOIN ceri_score_snapshots s ON s.id = ce.to_snapshot_id
WHERE s.run_id = 101;

-- Historical depth available for predictive-value certification.
SELECT count(*) AS snapshots, count(DISTINCT run_id) AS runs,
       count(DISTINCT as_of_session) AS sessions,
       min(as_of_session) AS first_session, max(as_of_session) AS last_session
FROM ceri_score_snapshots;

ROLLBACK;
