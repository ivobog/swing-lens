import os,json,datetime
from pathlib import Path
from sqlalchemy import create_engine,text
from dotenv import dotenv_values
e=create_engine(dotenv_values("C:/Users/Ivica/Documents/SwingLens/.env")['DATABASE_URL'],connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=45000'})
c=e.connect()
c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
from sqlalchemy.orm import Session
from app.services.ceri.capture_service import _existing_snapshot_company_ids,pipeline_id_for_cutoff,consumer_context_identity,contextual_compatibility,calculation_identity_from_debug,CERI_CONTEXT_COMPATIBILITY
from app.services.ceri.config import load_ceri_config
from app.services.market_calculation_context_service import resolve_pipeline_ceri_context
from app.services.ceri.evidence_eligibility import eligible_snapshot_select,_owner_eligibility_predicate
from app.models.ceri_tables import CeriScoreSnapshot as S
from sqlalchemy import select,func
db=Session(bind=c,autoflush=False)
m=resolve_pipeline_ceri_context(db,calculation_context_id=116,upload_run_id=62,pipeline_run_id=66)
p=pipeline_id_for_cutoff(db,run_id=62,market_cutoff=m)
from app.services.configuration_delivery import load_configuration_delivery
from app.models.tables import BackgroundJob
anchor=db.get(BackgroundJob,2546).payload_json['effective_configuration_anchor']
from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
cfg=ContextualEffectiveConfiguration(load_configuration_delivery(db,anchor).configurations['contextual.ceri'].snapshot).ceri_config()
rows=db.scalars(select(S).where(S.run_id==62)).all()
print('read_only',c.exec_driver_sql('show transaction_read_only').scalar(),'pipeline_id_for_cutoff',p,'config',cfg.config_hash)
try: _existing_snapshot_company_ids(db,62,{r.company_id for r in rows},cfg,m,p)
except ValueError as ex: print(str(ex))
print('rejected',sum(not contextual_compatibility(consumer_context_identity(market_cutoff=m,run_id=62,pipeline_id=p,ticker=r.ticker,company_id=r.company_id),calculation_identity_from_debug(r.evidence_lineage_json),policy=CERI_CONTEXT_COMPATIBILITY).accepted for r in rows))
for u in (61,62):
 print(u,'owner_pass',db.scalar(select(func.count()).select_from(S).where(S.run_id==u,_owner_eligibility_predicate(S))),'eligible',len(db.scalars(eligible_snapshot_select().where(S.run_id==u)).all()))
c.rollback()
