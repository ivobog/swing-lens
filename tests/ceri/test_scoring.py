from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriGuidanceEvent, CeriRevisionFeature, CeriScoreSnapshot
from app.services.ceri.catalyst_feature_service import CatalystFeature
from app.services.ceri.confidence_service import ConfidenceResult
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriConfidenceLabel
from app.services.ceri.event_risk_service import CeriEventRiskService, EventRiskResult
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.ceri.surprise_feature_service import SurpriseSummary

UTC = ZoneInfo("UTC")


def test_positive_opportunity_and_high_risk_are_simultaneously_visible() -> None:
    revision = _revision_feature(pct_change=Decimal("0.15"), net_breadth=Decimal("0.75"))
    catalyst = CatalystFeature(
        catalyst_event_id=1,
        catalyst_revision_id=2,
        category="REGULATORY",
        status="SCHEDULED",
        direction="POSITIVE",
        materiality_score=6.0,
        opportunity_component=4.0,
        binary_risk_score=6.0,
        conflict_penalty=0.0,
        date_confidence="EXACT_DATE",
        selected=True,
        issuer_relevance=True,
        binary_eligible=True,
        risk_component="regulatory_binary_risk",
    )
    opportunity = CeriOpportunityScoreService().calculate(
        revision_features=[revision],
        surprise_summary=SurpriseSummary(
            features=(),
            average_surprise_pct=Decimal("0.10"),
            positive_count=1,
            negative_count=0,
            consistency="consistently_positive",
            price_response_quality=7.0,
        ),
        guidance_events=[
            CeriGuidanceEvent(
                source_record_id=3,
                company_id=42,
                action="RAISED",
                confidence="High",
            )
        ],
        catalyst_features=[catalyst],
        price_response_quality=7.0,
    )
    risk = CeriEventRiskService().calculate(
        as_of_session=date(2026, 8, 1),
        next_earnings_session=date(2026, 8, 2),
        catalyst_features=[catalyst],
    )

    assert opportunity.score > 0
    assert risk.score >= 6.0
    assert risk.earnings_proximity.level == "blocked"


def test_ibkr_options_premium_is_bounded_and_short_pressure_is_context_only() -> None:
    service = CeriEventRiskService()
    baseline = service.calculate(
        as_of_session=date(2026, 8, 1),
        next_earnings_session=date(2026, 8, 20),
    )
    contextual = service.calculate(
        as_of_session=date(2026, 8, 1),
        next_earnings_session=date(2026, 8, 20),
        short_pressure_classification="EXTREME_BORROW_COST",
    )
    premium = service.calculate(
        as_of_session=date(2026, 8, 1),
        next_earnings_session=date(2026, 8, 20),
        options_event_premium_score=99,
    )
    assert contextual.score == baseline.score
    assert "ibkr_short_pressure_context:extreme_borrow_cost" in contextual.reasons
    assert premium.score == min(10.0, baseline.score + 1.5)


def test_changing_config_hash_creates_distinct_snapshot_without_mutating_old_one() -> None:
    base_config = load_ceri_config()
    changed_config = replace(base_config, config_hash="changed-hash")
    opportunity = _opportunity()
    risk = _risk()
    confidence = _confidence()

    first = CeriSnapshotService(config=base_config).build_snapshot(
        company_id=42,
        ticker="MSFT",
        as_of_session=date(2026, 8, 1),
        cutoff_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        opportunity=opportunity,
        event_risk=risk,
        confidence=confidence,
        source_ids=[1, 2, 3],
    )
    second = CeriSnapshotService(config=changed_config).build_snapshot(
        company_id=42,
        ticker="MSFT",
        as_of_session=date(2026, 8, 1),
        cutoff_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        opportunity=opportunity,
        event_risk=risk,
        confidence=confidence,
        source_ids=[1, 2, 3],
    )

    assert first.config_hash != second.config_hash
    assert first.evidence_hash != second.evidence_hash
    assert first.config_hash == base_config.config_hash


def test_score_reproduction_succeeds_from_stored_snapshot_inputs() -> None:
    service = CeriSnapshotService()
    snapshot = service.build_snapshot(
        company_id=42,
        ticker="MSFT",
        as_of_session=date(2026, 8, 1),
        cutoff_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        opportunity=_opportunity(),
        event_risk=_risk(),
        confidence=_confidence(),
        source_ids=[1, 2, 3],
        alignment_inputs={"fundamentals": True, "technicals": True},
        alignment_context={"fundamentals": {"score": 8.0}, "technicals": {"score": 7.0}},
        evidence_lineage={"revision_source_ids": [1, 2], "price_bar_ids": [9, 10]},
    )

    reproduction = service.reproduce_snapshot(snapshot)

    assert reproduction.matches is True
    assert reproduction.differences == ()
    assert snapshot.posture in {"Positive", "Improving", "Mixed", "Binary Risk", "Unrated"}
    assert snapshot.alignment_flags_json["fundamentals"] is True
    assert snapshot.component_json["source_ids"] == [1, 2, 3]
    assert snapshot.alignment_context_json["fundamentals"]["score"] == 8.0
    assert snapshot.evidence_lineage_json["price_bar_ids"] == [9, 10]


def test_legacy_snapshot_reproduction_is_read_only() -> None:
    snapshot = CeriScoreSnapshot(
        id=99,
        run_id=96,
        source_run_id_text="96",
        company_id=42,
        ticker="NWE",
        as_of_session=date(2026, 8, 1),
        cutoff_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        opportunity_score=8.0,
        event_risk_score=10.0,
        data_confidence="Low",
        coverage_pct=0.0,
        posture="Binary Risk",
        component_json={"components": [], "source_ids": [1, 2]},
        config_version="2026-07-31",
        config_hash="legacy-config",
        calculation_version="ceri-1.0.0",
        evidence_hash="immutable-stored-hash",
    )
    before = {
        "opportunity_score": snapshot.opportunity_score,
        "event_risk_score": snapshot.event_risk_score,
        "component_json": dict(snapshot.component_json),
        "evidence_hash": snapshot.evidence_hash,
    }

    CeriSnapshotService().reproduce_snapshot(snapshot)

    assert snapshot.opportunity_score == before["opportunity_score"]
    assert snapshot.event_risk_score == before["event_risk_score"]
    assert snapshot.component_json == before["component_json"]
    assert snapshot.evidence_hash == before["evidence_hash"]


def _revision_feature(
    *,
    pct_change: Decimal,
    net_breadth: Decimal,
) -> CeriRevisionFeature:
    return CeriRevisionFeature(
        company_id=42,
        metric="EPS_DILUTED",
        period_key="key",
        as_of_session=date(2026, 8, 1),
        window_days=30,
        actual_elapsed_days=30,
        absolute_change=Decimal("1.5"),
        pct_change=pct_change,
        net_breadth=net_breadth,
        acceleration=Decimal("0.05"),
        upward_count=7,
        downward_count=1,
        config_version="2026-07-31",
        config_hash="hash",
        calculation_version="ceri-1.0.0",
    )


def _opportunity():
    return CeriOpportunityScoreService().calculate(
        revision_features=[
            _revision_feature(pct_change=Decimal("0.10"), net_breadth=Decimal("0.5"))
        ],
        price_response_quality=6.0,
    )


def _risk() -> EventRiskResult:
    return CeriEventRiskService().calculate(
        as_of_session=date(2026, 8, 1),
        next_earnings_session=date(2026, 8, 20),
    )


def _confidence() -> ConfidenceResult:
    return ConfidenceResult(
        score=8.0,
        label=CeriConfidenceLabel.HIGH,
        coverage_pct=100.0,
        reasons=(),
        warnings=(),
    )
