SELECT version_num FROM alembic_version;
SELECT indexname,indexdef FROM pg_indexes WHERE tablename='ceri_score_snapshots';
SELECT indexrelid::regclass,indisvalid,indisready,indisunique,indnullsnotdistinct
FROM pg_index WHERE indrelid='ceri_score_snapshots'::regclass;
SELECT run_id, calculation_context_id, count(*), count(DISTINCT company_id), count(evidence_id)
FROM ceri_score_snapshots WHERE run_id IN (61,62) GROUP BY 1,2 ORDER BY 1,2;
SELECT run_id,company_id,calculation_context_id,count(*)
FROM ceri_score_snapshots WHERE controlled_replay_id IS NULL AND calculation_context_id IS NOT NULL
GROUP BY 1,2,3 HAVING count(*)>1;
-- Compare complete retained score/evidence/disposition/manifest fingerprints
-- with the pre-migration baseline. A row count alone is insufficient.
