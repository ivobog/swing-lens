from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import CeriEvidenceDisposition, CeriScoreSnapshot
from app.services.ceri.evidence_eligibility import (
    ELIGIBLE,
    EXCLUDED,
    EvidenceDispositionRequest,
    append_evidence_dispositions,
    effective_disposition_by_snapshot,
    eligible_snapshot_predicate,
    eligible_snapshot_select,
    filter_eligible_snapshots,
)
from app.services.pipeline_outcome import derive_pipeline_outcome
from app.services.transition_preflight_plan_service import _build_decision_handoff_payload

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_owner_fallback_is_correlated_to_each_snapshot_upload(
    disposable_postgres_database: str,
) -> None:
    """An unrelated failed run cannot poison a NULL-context-owner fallback."""

    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    safe_partial_summary = derive_pipeline_outcome(
        "PARTIAL",
        {"combined_results": 2, "incomplete_rows": 1},
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into upload_runs(id,filename,status) values "
                "(100,'safe-partial.csv','COMPLETED'),"
                "(200,'unrelated-failed.csv','FAILED'),"
                "(300,'owned-failed.csv','FAILED')"
            )
        )
        connection.execute(
            text(
                "insert into pipeline_runs(id,upload_run_id,status,result_json) values "
                "(100,100,'PARTIAL',cast(:safe_result as jsonb)),"
                "(200,200,'FAILED','{}'::jsonb),"
                "(300,300,'FAILED','{}'::jsonb)"
            ),
            {"safe_result": json.dumps({"pipeline_outcome_summary": safe_partial_summary})},
        )
        connection.execute(
            text(
                "insert into market_calculation_contexts "
                "(id,pipeline_run_id,upload_run_id,cutoff_at,exchange_timezone,"
                "latest_completed_session,calendar_version,bar_readiness_version,cutoff_reason) "
                "values "
                "(901,null,100,'2026-10-15 20:00:00+00','America/New_York',"
                "'2026-10-15','test','test','test'),"
                "(903,null,300,'2026-10-15 20:00:00+00','America/New_York',"
                "'2026-10-15','test','test','test')"
            )
        )
        connection.execute(
            text(
                "insert into ceri_companies(id,ticker,exchange) values "
                "(901,'SAFE1','US'),(902,'SAFE2','US'),(903,'FAIL1','US')"
            )
        )
        connection.execute(
            text(
                "insert into ceri_score_snapshots "
                "(id,run_id,company_id,ticker,as_of_session,cutoff_at,calculation_context_id,"
                "opportunity_score,opportunity_coverage_pct,event_risk_score,data_confidence,"
                "coverage_pct,posture,config_version,config_hash,calculation_version,"
                "evidence_contract_version,comparison_state,evidence_hash) values "
                "(901,100,901,'SAFE1','2026-10-15','2026-10-15 20:00:00+00',901,"
                "7,100,1,'Normal',100,'Positive','test','test','test','test','COMPARABLE','safe1'),"
                "(902,100,902,'SAFE2','2026-10-15','2026-10-15 20:00:00+00',901,"
                "7,100,1,'Normal',100,'Positive','test','test','test','test','COMPARABLE','safe2'),"
                "(903,300,903,'FAIL1','2026-10-15','2026-10-15 20:00:00+00',903,"
                "7,100,1,'Normal',100,'Positive','test','test','test','test','COMPARABLE','fail1')"
            )
        )

    with Session(engine) as db:
        assert (
            append_evidence_dispositions(
                db,
                (
                    EvidenceDispositionRequest(
                        ceri_snapshot_id=902,
                        disposition=EXCLUDED,
                        reason_code="EXPLICIT_INTEGRITY_EXCLUSION",
                        incident_reference="OWNER_CORRELATION_REGRESSION",
                        actor_source="pytest",
                    ),
                ),
            )
            == 1
        )
        db.commit()

        all_ids = set(db.scalars(select(CeriScoreSnapshot.id)))
        eligible_ids = set(db.scalars(eligible_snapshot_select(CeriScoreSnapshot.id)))

    assert all_ids == {901, 902, 903}
    assert eligible_ids == {901}
    assert all_ids - eligible_ids == {902, 903}
    engine.dispose()


def test_append_only_quarantine_is_idempotent_concurrent_and_filters_run159_fixture(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    _seed_run159_fixture(engine)
    with engine.connect() as connection:
        source_before = connection.execute(
            text(
                "select id,run_id,company_id,ticker,as_of_session,cutoff_at,"
                "opportunity_score,event_risk_score,data_confidence,posture,"
                "comparison_snapshot_id,evidence_hash,created_at "
                "from ceri_score_snapshots where id between 11226 and 11407 order by id"
            )
        ).all()
    requests = tuple(
        EvidenceDispositionRequest(
            ceri_snapshot_id=snapshot_id,
            disposition=EXCLUDED,
            reason_code="UNAUTHORIZED_CERTIFICATION_EXECUTION",
            incident_reference="RUN159_CERTIFICATION_CLAIM_LEAK",
            actor_source="pytest",
            metadata_json={"fixture": True},
        )
        for snapshot_id in range(11226, 11408)
    )
    with Session(engine) as db:
        assert append_evidence_dispositions(db, requests) == 182
        db.commit()
    with Session(engine) as db:
        assert append_evidence_dispositions(db, requests) == 0
        db.commit()
        assert effective_disposition_by_snapshot(db, range(11226, 11408)) == {
            snapshot_id: EXCLUDED for snapshot_id in range(11226, 11408)
        }
        affected = list(
            db.scalars(
                select(CeriScoreSnapshot).where(
                    CeriScoreSnapshot.id.between(11226, 11407),
                    eligible_snapshot_predicate(),
                )
            )
        )
        assert affected == []
        all_rows = list(db.scalars(select(CeriScoreSnapshot)))
        eligible = filter_eligible_snapshots(db, all_rows)
        assert len(eligible) == 175
        assert all(row.id not in range(11226, 11408) for row in eligible)
        market_cutoff = SimpleNamespace(
            context_id=1,
            cutoff_at=datetime(2026, 9, 13, 20, tzinfo=UTC),
            exchange_timezone="America/New_York",
            latest_completed_session=date(2026, 9, 13),
            daily_bar_ready_at=datetime(2026, 9, 13, 20, tzinfo=UTC),
            calendar_version="test",
            bar_readiness_version="test",
            cutoff_reason="test",
        )
        handoff = _build_decision_handoff_payload(
            db,
            plan=SimpleNamespace(
                id=1,
                upload_run_id=159,
                pipeline_run_id=1,
                run_start_anchor_fingerprint="anchor",
            ),
            market_cutoff=market_cutoff,
            ceri_market_cutoff=market_cutoff,
            built_rows=[],
            decision_manifests={},
        )
        assert handoff["ceri_score_snapshots"] == []

    with engine.begin() as connection:
        count = connection.scalar(
            text(
                "select count(*) from ceri_evidence_dispositions "
                "where incident_reference='RUN159_CERTIFICATION_CLAIM_LEAK' "
                "and disposition='EXCLUDED'"
            )
        )
        unrelated = connection.scalar(
            text(
                "select count(*) from ceri_evidence_dispositions d "
                "join ceri_score_snapshots s on s.id=d.ceri_snapshot_id "
                "where d.disposition='EXCLUDED' and s.run_id<>159"
            )
        )
        assert count == 182
        assert unrelated == 0
        source_after = connection.execute(
            text(
                "select id,run_id,company_id,ticker,as_of_session,cutoff_at,"
                "opportunity_score,event_risk_score,data_confidence,posture,"
                "comparison_snapshot_id,evidence_hash,created_at "
                "from ceri_score_snapshots where id between 11226 and 11407 order by id"
            )
        ).all()
        assert source_after == source_before

    with Session(engine) as db:
        first = db.scalar(select(CeriEvidenceDisposition).order_by(CeriEvidenceDisposition.id))
        first.notes = "forbidden"
        with pytest.raises(DBAPIError, match="append-only"):
            db.commit()
        db.rollback()
        first = db.scalar(select(CeriEvidenceDisposition).order_by(CeriEvidenceDisposition.id))
        with pytest.raises(DBAPIError, match="append-only"):
            db.delete(first)
            db.commit()
        db.rollback()

    concurrent_request = EvidenceDispositionRequest(
        ceri_snapshot_id=20000,
        disposition=EXCLUDED,
        reason_code="CONCURRENCY_TEST",
        incident_reference="CONCURRENCY_TEST",
        actor_source="pytest",
    )

    def append_once() -> int:
        with Session(engine) as db:
            inserted = append_evidence_dispositions(db, (concurrent_request,))
            db.commit()
            return inserted

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: append_once(), range(2))) == [0, 1]

    with Session(engine) as db:
        reversal = EvidenceDispositionRequest(
            ceri_snapshot_id=20000,
            disposition=ELIGIBLE,
            reason_code="CONCURRENCY_TEST_RESOLVED",
            incident_reference="CONCURRENCY_TEST",
            actor_source="pytest",
        )
        assert append_evidence_dispositions(db, (reversal,)) == 1
        db.commit()
        assert effective_disposition_by_snapshot(db, (20000,)) == {20000: ELIGIBLE}

    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("ceri_evidence_dispositions")}
    assert "ix_ceri_evidence_dispositions_snapshot_effective" in indexes
    with engine.connect() as connection:
        connection.execute(text("set enable_seqscan=off"))
        plan = "\n".join(
            row[0]
            for row in connection.execute(
                text(
                    "explain (costs off) select disposition "
                    "from ceri_evidence_dispositions where ceri_snapshot_id=11226 "
                    "order by created_at desc,id desc limit 1"
                )
            )
        )
    assert "ix_ceri_evidence_dispositions_snapshot_effective" in plan
    engine.dispose()


def _seed_run159_fixture(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into upload_runs(id,filename,status) values "
                "(158,'run158.csv','COMPLETED'),(159,'run159.csv','CANCELLED')"
            )
        )
        connection.execute(
            text(
                "insert into ceri_companies(id,ticker,exchange) "
                "select 1000+g,'T'||lpad(g::text,3,'0'),'US' "
                "from generate_series(1,182) g"
            )
        )
        connection.execute(
            text(
                "insert into ceri_score_snapshots "
                "(id,run_id,company_id,ticker,as_of_session,cutoff_at,opportunity_score,"
                "opportunity_coverage_pct,event_risk_score,data_confidence,coverage_pct,posture,"
                "config_version,config_hash,calculation_version,evidence_contract_version,"
                "comparison_state,evidence_hash) "
                "select 11225+g,159,1000+g,'T'||lpad(g::text,3,'0'),'2026-09-13',"
                "'2026-09-13 18:48:32+02'::timestamptz,7,100,1,'Normal',100,'Positive',"
                "'test','test','test','test','COMPARABLE','run159-'||g "
                "from generate_series(1,182) g"
            )
        )
        connection.execute(
            text(
                "insert into ceri_score_snapshots "
                "(id,run_id,company_id,ticker,as_of_session,cutoff_at,opportunity_score,"
                "opportunity_coverage_pct,event_risk_score,data_confidence,coverage_pct,posture,"
                "config_version,config_hash,calculation_version,evidence_contract_version,"
                "comparison_state,evidence_hash) "
                "select 20000+g,158,1000+g,'T'||lpad(g::text,3,'0'),'2026-09-12',"
                "'2026-09-12 18:48:32+02'::timestamptz,6,100,1,'Normal',100,'Mixed',"
                "'test','test','test','test','COMPARABLE','run158-'||g "
                "from generate_series(1,174) g"
            )
        )
        connection.execute(
            text("insert into ceri_companies(id,ticker,exchange) values (9999,'UNRELATED','US')")
        )
        connection.execute(
            text(
                "insert into ceri_score_snapshots "
                "(id,run_id,company_id,ticker,as_of_session,cutoff_at,opportunity_score,"
                "opportunity_coverage_pct,event_risk_score,data_confidence,coverage_pct,posture,"
                "config_version,config_hash,calculation_version,evidence_contract_version,"
                "comparison_state,evidence_hash) values "
                "(20000,158,9999,'UNRELATED','2026-09-12','2026-09-12 18:48:32+02',"
                "6,100,1,'Normal',100,'Mixed','test','test','test','test','COMPARABLE','unrelated')"
            )
        )


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, "head")
