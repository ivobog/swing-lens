from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import (
    CeriCompany,
    CeriEarningsActual,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.capture_service import _upcoming_earnings_for_companies
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import load_ceri_config
from app.services.ceri.deployment_identity import current_deployment_identity
from app.services.ceri.upcoming_earnings_authority import select_upcoming_earnings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_residual_ceri_authority_persists_and_revision_does_not_reinterpret_old_evidence(
    disposable_postgres_database: str,
) -> None:
    engine = _migrated_engine(disposable_postgres_database)
    try:
        with Session(engine) as db:
            company = CeriCompany(ticker="T15D", exchange="NASDAQ")
            db.add(company)
            db.flush()
            cutoff = datetime(2026, 9, 21, 13, tzinfo=UTC)
            source_v1 = _source(1, cutoff - timedelta(hours=1), "hash-v1")
            db.add(source_v1)
            db.flush()
            row_v1 = _upcoming(company.id, source_v1.id, date(2026, 10, 10))
            db.add(row_v1)
            db.flush()

            confidence = CeriConfidenceService().calculate(
                as_of_session=cutoff.date(),
                revision_features=_features("missing_observation_timestamp"),
                dataset_freshness_days={"estimates": 0},
            )
            selected_v1 = select_upcoming_earnings(
                company_id=company.id,
                as_of_session=cutoff.date(),
                cutoff_at=cutoff,
                earnings=[row_v1],
                source_records={source_v1.id: source_v1},
                config=load_ceri_config(),
            )
            snapshot = CeriScoreSnapshot(
                source_run_id_text="t15d-continuation",
                company_id=company.id,
                ticker=company.ticker,
                as_of_session=cutoff.date(),
                cutoff_at=cutoff,
                opportunity_score=5.0,
                opportunity_coverage_pct=100.0,
                event_risk_score=3.0,
                data_confidence=confidence.label.value,
                coverage_pct=confidence.coverage_pct,
                posture="WATCH",
                alignment_context_json={"earnings_clearance": selected_v1.evidence()},
                evidence_lineage_json={"upcoming_earnings_authority": selected_v1.evidence()},
                confidence_ledger_json={
                    "score": confidence.score,
                    "caps": list(confidence.caps),
                    "reasons": list(confidence.reasons),
                },
                config_version="t15d",
                config_hash=load_ceri_config().config_hash,
                calculation_version=load_ceri_config().engine.calculation_version,
                evidence_contract_version="ceri-decision-evidence-v1",
                comparison_state="NO_PRIOR",
                evidence_hash="t15d-v1",
            )
            db.add(snapshot)
            db.commit()
            old_evidence = dict(snapshot.evidence_lineage_json)

            source_v2 = _source(2, cutoff + timedelta(days=1), "hash-v2")
            source_v2.provider_record_id = source_v1.provider_record_id
            source_v2.supersedes_id = source_v1.id
            db.add(source_v2)
            db.flush()
            row_v2 = _upcoming(company.id, source_v2.id, date(2026, 10, 14))
            db.add(row_v2)
            db.flush()
            selected_v2 = select_upcoming_earnings(
                company_id=company.id,
                as_of_session=cutoff.date() + timedelta(days=1),
                cutoff_at=cutoff + timedelta(days=1, hours=1),
                earnings=[row_v1, row_v2],
                source_records={source_v1.id: source_v1, source_v2.id: source_v2},
                config=load_ceri_config(),
            )

            db.expire_all()
            stored = db.scalar(select(CeriScoreSnapshot).where(CeriScoreSnapshot.id == snapshot.id))
            assert stored.data_confidence == "Low"
            assert stored.confidence_ledger_json["caps"][-1].startswith(
                "CRITICAL_PROVENANCE_CAP_LOW:"
            )
            assert old_evidence["upcoming_earnings_authority"]["selected_exact_value"] == (
                "2026-10-10"
            )
            assert stored.evidence_lineage_json == old_evidence
            assert selected_v2.evidence()["selected_exact_value"] == "2026-10-14"

            schema_revision = db.scalar(text("select version_num from alembic_version"))
            identity = current_deployment_identity(
                config_hash="cfg",
                calculation_version="calc",
                database_schema_revision=str(schema_revision),
            )
            assert identity["database_schema_revision"] == "0084_technical_recovery"
            assert identity["ceri_evidence_schema_revision"] == "ceri-decision-evidence-v1"
            db.rollback()
    finally:
        engine.dispose()


def test_upcoming_earnings_authority_reads_are_population_bounded(
    disposable_postgres_database: str,
    record_property,
) -> None:
    engine = _migrated_engine(disposable_postgres_database)
    try:
        with Session(engine) as db:
            cutoff = datetime(2026, 9, 21, 13, tzinfo=UTC)
            companies = [CeriCompany(ticker=f"T15D{i:03}", exchange="NASDAQ") for i in range(50)]
            db.add_all(companies)
            db.flush()
            sources = [
                _source(index + 1, cutoff - timedelta(hours=1), f"hash-{index + 1}")
                for index in range(50)
            ]
            db.add_all(sources)
            db.flush()
            rows = [
                _upcoming(company.id, source.id, date(2026, 10, 10))
                for company, source in zip(companies, sources, strict=True)
            ]
            db.add_all(rows)
            db.flush()
            results = {}
            for population in (1, 50):
                statements = Counter()

                def observe(
                    _conn,
                    _cursor,
                    sql,
                    _params,
                    _context,
                    _many,
                    statements=statements,
                ):
                    statements[sql.lstrip().split(None, 1)[0].upper()] += 1

                event.listen(engine, "before_cursor_execute", observe)
                try:
                    selected = _upcoming_earnings_for_companies(
                        db,
                        company_ids={company.id for company in companies[:population]},
                        earnings=rows[:population],
                        as_of_session=cutoff.date(),
                        cutoff_at=cutoff,
                        config=load_ceri_config(),
                    )
                finally:
                    event.remove(engine, "before_cursor_execute", observe)
                assert len(selected) == population
                assert all(item.selected is not None for item in selected.values())
                results[population] = statements["SELECT"]
                record_property(f"earnings_provenance_{population}", statements["SELECT"])
            assert results == {1: 1, 50: 1}
            db.rollback()
    finally:
        engine.dispose()


def _migrated_engine(database_url: str):
    from sqlalchemy import create_engine

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0084_technical_recovery"]
    command.upgrade(config, "head")
    command.check(config)
    return create_engine(database_url)


def _source(identifier: int, retrieved_at: datetime, content_hash: str) -> CeriSourceRecord:
    return CeriSourceRecord(
        provider="manual",
        dataset="earnings",
        provider_record_id=f"event-{identifier}",
        published_at=retrieved_at - timedelta(hours=3),
        retrieved_at=retrieved_at,
        ingested_at=retrieved_at,
        content_hash=content_hash,
        idempotency_key=f"key-{identifier}",
    )


def _upcoming(company_id: int, source_id: int, session: date) -> CeriEarningsActual:
    return CeriEarningsActual(
        source_record_id=source_id,
        company_id=company_id,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 9, 30),
        report_at=datetime(session.year, session.month, session.day, 21, tzinfo=UTC),
        report_session=session,
        actual_value=None,
        event_kind="UPCOMING",
    )


def _features(warning: str) -> list[object]:
    return [
        SimpleNamespace(
            pct_change=Decimal("1"),
            revision_confidence_score=10.0,
            upward_count=6,
            downward_count=0,
            warnings_json=([warning] if index == 0 else []),
        )
        for index in range(24)
    ]
