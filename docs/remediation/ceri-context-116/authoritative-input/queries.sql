BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
-- contexts
select * from market_calculation_contexts where pipeline_run_id between 63 and 66 order by id;

-- scores
select run_id,source_run_id_text,calculation_context_id,cutoff_at,as_of_session,config_hash,config_version,calculation_version,calendar_version,min(id),max(id),count(*) from ceri_score_snapshots where run_id in (61,62) group by 1,2,3,4,5,6,7,8,9;

-- deps
select pipeline_run_id,id,dependency_type,state,root_job_id,child_job_id,continuation_job_id from pipeline_dependencies where pipeline_run_id between 63 and 66 order by id;

-- runs
select id,upload_run_id,status,started_at,completed_at,scope_id,result_json->>'pipeline_root_job_id' root,result_json->>'ceri_provider_workflow_key' wf,result_json->'ceri_failure_jobs' failures,result_json->>'decision_handoff_manifest_id' manifest,result_json->>'failure_reason' reason from pipeline_runs where id between 63 and 66 order by id;

-- uploads
select id,filename,file_path,row_count,uploaded_at from upload_runs where id in (61,62);

-- indexes
select indexdef from pg_indexes where tablename='ceri_score_snapshots' and indexdef like '%UNIQUE%';

-- jobs
select id,job_type,status,root_job_id,parent_job_id,scope_id,error_message,left((payload_json::jsonb - 'tickers' - 'company_ids')::text,1400) payload from background_jobs where id in (2180,2305,2308,2427,2546,2547,2548) order by id;

-- workerSchema
select table_name,string_agg(column_name,',') cols from information_schema.columns where table_schema='public' and (column_name ~ 'revision|git_sha|commit_sha' or table_name='worker_instances') group by 1;

-- otherContexts
select * from market_calculation_contexts where id in (111,113,116);

-- workerTables
select tablename from pg_tables where schemaname='public' and tablename ~ 'runtime|worker_instance|worker_reg|disposition|run_start_manifest';

-- inputOverlap
select count(*) filter(where a and b) both,count(*) filter(where a and not b) only61,count(*) filter(where b and not a) only62 from (select ticker,bool_or(run_id=61) a,bool_or(run_id=62) b from raw_company_rows where run_id in(61,62) group by ticker) x;

-- perOwnerJobs
select payload_json->>'pipeline_run_id' owner,workflow_key,job_type,status,min(id),max(id),count(*) from background_jobs where related_run_id in(61,62) and (payload_json->>'pipeline_run_id' in ('63','64','65','66') or workflow_key like 'ceri:pipeline:%') group by 1,2,3,4 order by 5;

-- moreIndexes
select tablename,indexdef from pg_indexes where tablename in ('core_calculation_evidence','market_calculation_contexts','transition_decision_handoff_manifests') and indexdef like '%UNIQUE%';

-- historicalEvidence
select s.run_id,count(*) n,count(e.id) sealed,count(distinct e.calculation_identity_fingerprint) identities,min(s.created_at) first,max(s.created_at) last from ceri_score_snapshots s left join core_calculation_evidence e on e.id=s.evidence_id where s.run_id in(61,62) group by 1;

-- manifestProof
select m.id,m.created_at,m.manifest_fingerprint,count(*) members,count(s.id) matched,count(distinct s.id) distinct_scores from transition_decision_handoff_manifests m cross join lateral jsonb_array_elements(m.manifest_json::jsonb->'ceri_score_snapshots') x left join ceri_score_snapshots s on s.id=(x->>'id')::int and s.run_id=61 and s.calculation_context_id=111 where m.pipeline_run_id=63 group by m.id;

-- secOriginal
select id,status,counts_json,completed_at from ceri_processing_runs where id in(27590,28303) order by id;

-- dupeCIK
select id,ticker,cik,created_at from ceri_companies where cik='0001636519' order by id;

-- failedSteps
select pipeline_run_id,step_name,status,started_at,completed_at,error_message from pipeline_steps where pipeline_run_id between 63 and 66 order by pipeline_run_id,step_order;

-- proofRows
select s.run_id,s.id snapshot_id,s.company_id,s.ticker,s.calculation_context_id,s.evidence_hash,s.evidence_id,e.calculation_identity_fingerprint,e.evidence_key,e.calculation_identity_json->'execution' execution from ceri_score_snapshots s join core_calculation_evidence e on e.id=s.evidence_id where s.run_id in(61,62) order by s.id;

-- keysAt65
select id,request_key,scope_id,status from background_jobs where workflow_key=(select result_json->>'ceri_provider_workflow_key' from pipeline_runs where id=64) order by id limit 1;

-- ownership
select s.run_id,e.calculation_identity_json::jsonb->'ownership' ownership,count(*) from ceri_score_snapshots s join core_calculation_evidence e on e.id=s.evidence_id where s.run_id in(61,62) group by 1,2;

-- contextCounts
select c.id,c.upload_run_id,count(distinct s.ticker) certified_tickers from market_calculation_contexts c left join ceri_score_snapshots s on s.calculation_context_id=c.id and s.run_id=c.upload_run_id and s.evidence_id is not null where c.id in(111,113,116) group by c.id order by c.id;

-- dispositions
select run_id,disposition,reason_code,d.created_at,count(*) from ceri_evidence_dispositions d join ceri_score_snapshots s on s.id=d.ceri_snapshot_id where run_id in(61,62) group by 1,2,3,4 order by 4;

-- allInputCandidates
select count(distinct r.ticker) requested,count(distinct s.ticker) candidates,count(distinct s.company_id) occupied_keys,count(distinct s.ticker) filter(where s.calculation_context_id=116) exact_context from raw_company_rows r left join ceri_score_snapshots s on s.run_id=r.run_id and upper(s.ticker)=upper(r.ticker) and s.config_hash='28adaf159aa4a4af9a62827496436c81be7da239b58bade6b7272897e49e0d9a' and s.calculation_version='ceri-1.3.0' where r.run_id=62;

-- launchTimes
select id,job_type,status,created_at,started_at,completed_at,request_key from background_jobs where id in(2128,2130,2180,2182,2183,2184,2186,2187,2291,2305,2306,2307,2308,2427,2545,2546) order by id;
ROLLBACK;
