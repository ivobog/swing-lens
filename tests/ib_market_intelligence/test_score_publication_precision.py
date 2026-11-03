from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.decision_evidence import _row_payload
from app.services.ib_market_intelligence.dtos import FeatureResult
from app.services.ib_market_intelligence.repository import persist_feature


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.7382548704040581, "0.738255"),
        (0.06134966242903328, "0.061350"),
        (1.4285714285714286, "1.428571"),
        (2.857142857142857, "2.857143"),
        (1.2345665, "1.234567"),
        (10, "10.000000"),
        (None, None),
    ],
)
def test_score_is_database_precision_before_immutable_evidence_is_built(
    monkeypatch, score, expected
):
    frozen = []

    class StagingSession:
        def scalar(self, statement):
            return None

        def add(self, row):
            row.id = 1

        def flush(self):
            pass

    monkeypatch.setattr(
        "app.services.ib_market_intelligence.decision_evidence.build_ibmi_constituent_manifest",
        lambda *args: ({}, ()),
    )
    monkeypatch.setattr(
        "app.services.ib_market_intelligence.decision_evidence.persist_ibmi_feature_evidence",
        lambda db, **kwargs: frozen.append(_row_payload(kwargs["feature"])),
    )
    row, inserted = persist_feature(
        StagingSession(),
        ticker="AAPL",
        ib_conid=265598,
        as_of_session=date(2026, 10, 30),
        feature=FeatureResult(
            "VOLATILITY", "IV_NORMAL", score, "HIGH", "AVAILABLE", "AVAILABLE", {}
        ),
        config=load_ib_market_intelligence_config(),
        calculation_cutoff_at=datetime(2026, 11, 2, 14, tzinfo=UTC),
        constituents=object(),
    )
    assert inserted
    assert row.score == (Decimal(expected) if expected is not None else None)
    assert (
        frozen[0]["score"] is None
        if expected is None
        else Decimal(frozen[0]["score"]) == Decimal(expected)
    )
    # PostgreSQL's later NUMERIC(12, 6) round trip must not change the sealed value.
    if row.score is not None:
        assert row.score.as_tuple().exponent == -6
