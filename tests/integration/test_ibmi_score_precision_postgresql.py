"""Exercise the real PostgreSQL NUMERIC/evidence boundary across transactions."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.decision_evidence import (
    IbmiFeatureConstituents,
    get_certified_ibmi_evidence,
)
from app.services.ib_market_intelligence.dtos import FeatureResult, HistoricalMetricBarDTO
from app.services.ib_market_intelligence.repository import (
    persist_feature,
    persist_historical_metric_bar,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.7382548704040581, "0.738255"), (1.2345665, "1.234567"), (0, "0.000000")],
)
def test_postgresql_round_trip_matches_sealed_score_and_keeps_old_revision(
    disposable_postgres_database,
    score,
    expected,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    known = datetime(2026, 10, 30, 21, tzinfo=UTC)
    config = load_ib_market_intelligence_config()

    def publish(db, value, observed, feature_score):
        metric, _ = persist_historical_metric_bar(
            db,
            HistoricalMetricBarDTO(
                ticker="ACME",
                ib_conid=1,
                session_date=date(2026, 10, 30),
                timeframe="1 day",
                metric_type="HISTORICAL_VOLATILITY",
                open_value=value,
                high_value=value,
                low_value=value,
                close_value=value,
                requested_range="252 D",
                source_semantic_type="HISTORICAL_VOLATILITY",
            ),
            observed_at=observed,
        )
        return persist_feature(
            db,
            ticker="ACME",
            ib_conid=1,
            as_of_session=date(2026, 10, 30),
            feature=FeatureResult(
                "VOLATILITY",
                "IV_NORMAL",
                feature_score,
                "HIGH",
                "AVAILABLE",
                "AVAILABLE",
                {"historical_volatility": value},
                evidence_hashes=(metric.data_hash,),
            ),
            config=config,
            calculated_at=observed + timedelta(seconds=1),
            calculation_cutoff_at=observed + timedelta(seconds=1),
            constituents=IbmiFeatureConstituents(metric_bars=(metric,)),
        )[0]

    try:
        with Session(engine) as db:
            original = publish(db, 0.2, known, score)
            original_id = original.id
            old_evidence = deepcopy(get_certified_ibmi_evidence(db, original).payload_json)
            db.commit()
        with Session(engine) as db:
            original = db.get(IBIntelligenceFeature, original_id)
            assert original.score == Decimal(expected)
            # This performs the existing strict financial-value/source comparison.
            assert get_certified_ibmi_evidence(db, original).payload_json == old_evidence
            revised = publish(db, 0.4, known + timedelta(minutes=1), score)
            assert revised.evidence_id != original.evidence_id
            db.commit()
        with Session(engine) as db:
            original = db.get(IBIntelligenceFeature, original_id)
            assert get_certified_ibmi_evidence(db, original).payload_json == old_evidence
            assert original.score == Decimal(expected)
    finally:
        engine.dispose()
