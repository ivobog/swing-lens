from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriRevisionFeature,
)
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService
from app.services.ceri.snapshot_service import (
    CeriSnapshotService,
    canonical_json_dumps,
    score_evidence_hash,
)
from app.services.ceri.surprise_feature_service import SurpriseSummary


def test_opportunity_below_sixty_percent_is_unrated_and_missing_is_not_zero() -> None:
    result = CeriOpportunityScoreService().calculate(
        revision_features=[],
        price_response_quality=0.0,
    )
    assert result.score is None
    assert result.rated is False
    assert result.coverage_pct == 5.0
    assert result.unrated_reason == "INSUFFICIENT_COMPONENT_COVERAGE"
    price = next(component for component in result.components if component.name == "price_response")
    revision = next(
        component for component in result.components if component.name == "revision_magnitude"
    )
    assert price.available is True and price.value == 0.0
    assert revision.available is False and revision.value is None


def test_opportunity_reweights_available_components_at_sixty_percent() -> None:
    features = [
        _feature(pct_change=Decimal("5"), net_breadth=Decimal("1"), acceleration=Decimal("0.2"))
    ]
    guidance = CeriGuidanceEvent(
        id=1,
        source_record_id=1,
        company_id=1,
        action="RAISED",
        metric="EPS_DILUTED",
        period_type="CURRENT_FISCAL_YEAR",
        confidence="High",
        accepted_for_scoring=True,
    )
    result = CeriOpportunityScoreService().calculate(
        revision_features=features,
        guidance_events=[guidance],
        price_response_quality=5.0,
    )
    assert result.coverage_pct == 70.0
    assert result.rated is True
    assert result.reweighted is True
    assert result.score is not None


def test_signed_revision_does_not_reward_equal_cut() -> None:
    service = CeriOpportunityScoreService()
    raised = service.calculate(
        revision_features=[_feature(pct_change=Decimal("10"))],
        price_response_quality=0.0,
    )
    cut = service.calculate(
        revision_features=[_feature(pct_change=Decimal("-10"))],
        price_response_quality=0.0,
    )
    raised_value = next(c.value for c in raised.components if c.name == "revision_magnitude")
    cut_value = next(c.value for c in cut.components if c.name == "revision_magnitude")
    assert raised_value == 10.0
    assert cut_value == 0.0


def test_unknown_guidance_is_rejected_from_opportunity() -> None:
    unknown = CeriGuidanceEvent(
        id=1,
        source_record_id=1,
        company_id=1,
        action="UNKNOWN",
        confidence="Insufficient",
        accepted_for_scoring=False,
        rejection_reason="GUIDANCE_ACTION_UNKNOWN",
    )
    result = CeriOpportunityScoreService().calculate(
        revision_features=[], guidance_events=[unknown]
    )
    guidance = next(c for c in result.components if c.name == "guidance")
    assert guidance.available is False
    assert guidance.value is None


def test_latest_guidance_state_replaces_history_for_same_metric_and_period() -> None:
    prior = CeriGuidanceEvent(
        id=1,
        source_record_id=1,
        company_id=1,
        action="RAISED",
        metric="EPS_DILUTED",
        period_type="CURRENT_FISCAL_YEAR",
        confidence="High",
        accepted_for_scoring=True,
        effective_session=date(2026, 7, 1),
    )
    current = CeriGuidanceEvent(
        id=2,
        source_record_id=2,
        company_id=1,
        action="LOWERED",
        metric="EPS_DILUTED",
        period_type="CURRENT_FISCAL_YEAR",
        confidence="High",
        accepted_for_scoring=True,
        effective_session=date(2026, 8, 1),
    )

    result = CeriOpportunityScoreService().calculate(
        revision_features=[], guidance_events=[prior, current]
    )
    guidance = next(c for c in result.components if c.name == "guidance")

    assert guidance.value == 2.0
    assert guidance.evidence_ids == (2,)


def test_retrospective_trend_is_excluded_before_known_at_and_currently_eligible() -> None:
    observed = datetime(2026, 8, 12, 12, tzinfo=UTC)
    current = _estimate(1, observed, known_at=observed)
    baseline = _estimate(
        2,
        observed - timedelta(days=30),
        known_at=observed,
        trend_days=30,
        origin="PROVIDER_RETROSPECTIVE_WINDOW",
    )
    query = CeriPointInTimeQuery(snapshots=[current, baseline])
    early = query.eligible_estimates(
        FakeDb(),
        company_id=1,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
    )
    assert baseline not in early
    selected = query.select_baseline(
        FakeDb(),
        current=current,
        company_id=1,
        metric="EPS_DILUTED",
        cutoff_at=observed,
        window_days=30,
    )
    assert selected.baseline is baseline


def test_no_cross_period_baseline() -> None:
    current_at = datetime(2026, 8, 12, tzinfo=UTC)
    baseline_at = datetime(2026, 7, 12, tzinfo=UTC)
    current = _estimate(1, current_at, known_at=current_at)
    baseline = _estimate(2, baseline_at, known_at=baseline_at)
    baseline.fiscal_period_end = date(2027, 12, 31)
    selection = CeriPointInTimeQuery(snapshots=[current, baseline]).select_baseline(
        FakeDb(),
        current=current,
        company_id=1,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 12, tzinfo=UTC),
        window_days=30,
    )
    assert selection.baseline is None


def test_percentage_acceleration_contract() -> None:
    service = CeriRevisionFeatureService(query=CeriPointInTimeQuery(snapshots=[]))
    recent = _feature(pct_change=Decimal("7"), elapsed=7)
    longer = _feature(pct_change=Decimal("15"), elapsed=30)
    service.with_acceleration(recent, longer)
    assert recent.acceleration == Decimal("0.5")
    assert recent.acceleration_unit == "PERCENTAGE_POINTS_PER_DAY"


def test_issuer_mismatch_and_resolved_lifecycle_are_rejected() -> None:
    event = CeriCatalystEvent(
        id=1, company_id=1, category="REGULATORY", subject_key="foreign-issuer"
    )
    mismatch = CeriCatalystEventRevision(
        id=1,
        catalyst_event_id=1,
        revision_number=1,
        status="SCHEDULED",
        direction="POSITIVE",
        materiality=8.0,
        issuer_relevance=False,
        relevance_reason="ISSUER_RELEVANCE_MISMATCH",
        binary_eligible=True,
    )
    feature = CeriCatalystFeatureService().calculate(
        event=event, revision=mismatch, as_of_session=date(2026, 8, 12)
    )
    assert feature.selected is False
    assert feature.binary_risk_score == 0.0
    risk = CeriEventRiskService().calculate(
        as_of_session=date(2026, 8, 12), catalyst_features=[feature]
    )
    assert risk.rejected_events == (
        {
            "event_id": 1,
            "revision_id": 1,
            "reason": "ISSUER_RELEVANCE_MISMATCH",
        },
    )

    resolved = CeriCatalystEventRevision(
        id=2,
        catalyst_event_id=1,
        revision_number=2,
        status="RESOLVED",
        direction="NEGATIVE",
        materiality=8.0,
        issuer_relevance=True,
        binary_eligible=True,
    )
    resolved_feature = CeriCatalystFeatureService().calculate(
        event=event, revision=resolved, as_of_session=date(2026, 8, 12)
    )
    assert resolved_feature.selected is True
    assert resolved_feature.binary_eligible is False
    assert resolved_feature.binary_risk_score == 0.0


def test_rejected_parent_event_excludes_price_response() -> None:
    from app.services.ceri.catalyst_feature_service import CatalystFeature

    rejected = CatalystFeature(
        catalyst_event_id=9,
        catalyst_revision_id=10,
        category="REGULATORY",
        status="SCHEDULED",
        direction="POSITIVE",
        materiality_score=8.0,
        opportunity_component=8.0,
        binary_risk_score=0.0,
        conflict_penalty=0.0,
        date_confidence="EXACT",
        selected=False,
        rejection_reason="ISSUER_RELEVANCE_MISMATCH",
        issuer_relevance=False,
        opportunity_available=False,
    )
    result = CeriOpportunityScoreService().calculate(
        revision_features=[],
        catalyst_features=[rejected],
        price_response_quality=9.0,
        price_response_parent_event_id=9,
    )

    price = next(c for c in result.components if c.name == "price_response")
    assert price.available is False
    assert price.value is None
    assert price.unavailable_reason == "PARENT_EVENT_INELIGIBLE"


def test_event_risk_uses_dominant_max_not_addition() -> None:
    from app.services.ceri.catalyst_feature_service import CatalystFeature

    features = [
        CatalystFeature(
            catalyst_event_id=index,
            catalyst_revision_id=index,
            category="REGULATORY",
            status="SCHEDULED",
            direction="UNKNOWN",
            materiality_score=0,
            opportunity_component=0,
            binary_risk_score=5,
            conflict_penalty=0,
            date_confidence="UNKNOWN",
            selected=True,
            issuer_relevance=True,
            binary_eligible=True,
            risk_component="regulatory_binary_risk",
            dedup_key="same-event",
        )
        for index in (1, 2)
    ]
    result = CeriEventRiskService().calculate(
        as_of_session=date(2026, 8, 12), catalyst_features=features
    )
    assert result.score == 5.0
    assert len(result.selected_event_ids) == 1


def test_zero_revision_coverage_hard_gates_confidence() -> None:
    result = CeriConfidenceService().calculate(
        as_of_session=date(2026, 8, 12), revision_features=[]
    )
    assert result.label.value == "Insufficient"
    assert "ZERO_USABLE_CORE_REVISION_COVERAGE" in result.gates


def test_canonical_hash_is_timezone_and_order_stable() -> None:
    berlin = datetime.fromisoformat("2026-08-12T03:53:49.997862+02:00")
    utc = datetime.fromisoformat("2026-08-12T01:53:49.997862+00:00")
    left = {"when": berlin, "amount": Decimal("1.2300"), "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "amount": Decimal("1.23"), "when": utc}
    assert canonical_json_dumps(left) == canonical_json_dumps(right)
    assert score_evidence_hash(left) == score_evidence_hash(right)


def test_clean_complete_evidence_fixture_is_rated_and_reproducible() -> None:
    from app.services.ceri.catalyst_feature_service import CatalystFeature

    slots = (
        "CURRENT_QUARTER",
        "NEXT_QUARTER",
        "CURRENT_FISCAL_YEAR",
        "NEXT_FISCAL_YEAR",
    )
    features = []
    feature_id = 1
    for metric in ("EPS_DILUTED", "REVENUE"):
        for slot in slots:
            for window in (7, 30, 90):
                feature = _feature(
                    pct_change=Decimal("4"),
                    net_breadth=Decimal("0.6"),
                    acceleration=Decimal("0.1"),
                    elapsed=window,
                )
                feature.id = feature_id
                feature.metric = metric
                feature.period_slot = slot
                feature.upward_count = 8
                feature.downward_count = 2
                feature.revision_confidence_score = 9.0
                features.append(feature)
                feature_id += 1
    guidance = CeriGuidanceEvent(
        id=31,
        source_record_id=31,
        company_id=1,
        action="RAISED",
        metric="EPS_DILUTED",
        period_type="CURRENT_FISCAL_YEAR",
        confidence="High",
        accepted_for_scoring=True,
    )
    catalyst = CatalystFeature(
        catalyst_event_id=41,
        catalyst_revision_id=42,
        category="PRODUCT",
        status="ANNOUNCED",
        direction="POSITIVE",
        materiality_score=8.0,
        opportunity_component=8.0,
        binary_risk_score=0.0,
        conflict_penalty=0.0,
        date_confidence="EXACT_DATE",
        selected=True,
        issuer_relevance=True,
        opportunity_available=True,
    )
    surprise = SurpriseSummary(
        features=(),
        average_surprise_pct=Decimal("6"),
        positive_count=4,
        negative_count=0,
        consistency="consistently_positive",
        price_response_quality=7.0,
    )
    opportunity = CeriOpportunityScoreService().calculate(
        revision_features=features,
        surprise_summary=surprise,
        guidance_events=[guidance],
        catalyst_features=[catalyst],
        price_response_quality=7.0,
        price_response_parent_event_id=41,
    )
    confidence = CeriConfidenceService().calculate(
        as_of_session=date(2026, 8, 12),
        revision_features=features,
        dataset_freshness_days={
            "estimates": 0,
            "earnings": 0,
            "guidance": 0,
            "catalysts": 0,
        },
    )
    risk = CeriEventRiskService().calculate(as_of_session=date(2026, 8, 12))
    snapshot_service = CeriSnapshotService()
    snapshot = snapshot_service.build_snapshot(
        company_id=1,
        ticker="CLEAN",
        as_of_session=date(2026, 8, 12),
        cutoff_at=datetime(2026, 8, 12, 20, tzinfo=UTC),
        opportunity=opportunity,
        event_risk=risk,
        confidence=confidence,
        source_ids=list(range(1, 25)),
        evidence_lineage={"fixture": "clean-complete-evidence-v1"},
    )

    assert opportunity.rated is True
    assert opportunity.coverage_pct == 100.0
    assert confidence.label.value in {"High", "Normal"}
    assert snapshot.posture != "Unrated"
    assert snapshot_service.reproduce_snapshot(snapshot).matches is True


def _feature(
    *,
    pct_change: Decimal | None = Decimal("5"),
    net_breadth: Decimal | None = None,
    acceleration: Decimal | None = None,
    elapsed: int = 30,
) -> CeriRevisionFeature:
    return CeriRevisionFeature(
        id=1,
        company_id=1,
        metric="EPS_DILUTED",
        period_key="key",
        period_slot="CURRENT_QUARTER",
        as_of_session=date(2026, 8, 12),
        window_days=elapsed,
        actual_elapsed_days=elapsed,
        pct_change=pct_change,
        net_breadth=net_breadth,
        acceleration=acceleration,
        config_version="test",
        config_hash="hash",
        calculation_version="ceri-1.1.0",
    )


def _estimate(
    snapshot_id: int,
    effective_at: datetime,
    *,
    known_at: datetime,
    trend_days: int | None = None,
    origin: str | None = None,
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=snapshot_id,
        company_id=1,
        metric="EPS_DILUTED",
        period_type="CURRENT_FISCAL_YEAR",
        fiscal_period_end=date(2026, 12, 31),
        consensus=Decimal("10"),
        canonical_currency="USD",
        canonical_scale=Decimal("1"),
        effective_at=effective_at,
        effective_session=effective_at.date(),
        reference_at=effective_at,
        known_at=known_at,
        trend_baseline_window_days=trend_days,
        baseline_origin=origin,
        current_observation_reference="same",
        canonical_observation_key=str(snapshot_id),
    )


class FakeDb:
    pass
