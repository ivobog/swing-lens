-- READ ONLY. Retain this output before scheduling any future migration.
SHOW server_version;
SELECT version_num FROM alembic_version;
SELECT controlled_replay_id IS NOT NULL AS replay,
       calculation_context_id IS NULL AS legacy_context, run_id IS NULL AS null_upload,
       count(*) AS rows, count(DISTINCT calculation_context_id) AS contexts
FROM ceri_score_snapshots GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT run_id, company_id, calculation_context_id, count(*)
FROM ceri_score_snapshots
WHERE controlled_replay_id IS NULL AND calculation_context_id IS NOT NULL
GROUP BY 1,2,3 HAVING count(*) > 1;
SELECT run_id, company_id, config_hash, calculation_version, count(*)
FROM ceri_score_snapshots
WHERE controlled_replay_id IS NULL AND calculation_context_id IS NULL AND run_id IS NOT NULL
GROUP BY 1,2,3,4 HAVING count(*) > 1;
SELECT controlled_replay_id, company_id, count(*)
FROM ceri_score_snapshots WHERE controlled_replay_id IS NOT NULL
GROUP BY 1,2 HAVING count(*) > 1;
SELECT indexname, indexdef FROM pg_indexes WHERE tablename='ceri_score_snapshots';
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid='ceri_score_snapshots'::regclass ORDER BY conname;

-- Downgrade admissibility: ANY returned row prevents restoration of the old
-- contract. NULL upload rows remain distinct under the historical constraint.
SELECT run_id, company_id, config_hash, calculation_version, count(*)
FROM ceri_score_snapshots WHERE run_id IS NOT NULL
GROUP BY 1,2,3,4 HAVING count(*) > 1;
