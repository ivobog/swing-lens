"""Complete native CERI inputs for Phase-3 READY business parity."""

from datetime import timedelta
from decimal import Decimal

from app.models.ceri_tables import CeriGuidanceEvent, CeriRevisionFeature
from app.services.ceri.catalyst_feature_service import CatalystFeature
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.ceri.surprise_feature_service import SurpriseSummary


def rated_ceri_snapshot(cutoff, volatility=None, short_pressure=None):
    config = load_ceri_config()
    revisions = [
        CeriRevisionFeature(
            id=index + 1,
            company_id=1,
            metric=metric,
            period_key=f"{period}:NEXT_YEAR",
            as_of_session=cutoff.latest_completed_session,
            window_days=window,
            actual_elapsed_days=window,
            pct_change=Decimal("0.15"),
            net_breadth=Decimal("0.75"),
            acceleration=Decimal("0.05"),
            upward_count=20,
            downward_count=2,
            revision_confidence_score=Decimal("9"),
            warnings_json=[],
        )
        for index, (metric, period, window) in enumerate(
            (metric, period, window)
            for metric in config.metrics.required
            for period in config.metrics.period_types
            for window in config.revision.windows_days
        )
    ]
    catalyst = CatalystFeature(
        catalyst_event_id=101,
        catalyst_revision_id=102,
        category="REGULATORY",
        status="SCHEDULED",
        direction="POSITIVE",
        materiality_score=6.0,
        opportunity_component=4.0,
        binary_risk_score=0.0,
        conflict_penalty=0.0,
        date_confidence="EXACT_DATE",
        selected=True,
        issuer_relevance=True,
        binary_eligible=True,
        risk_component="regulatory_binary_risk",
    )
    opportunity = CeriOpportunityScoreService().calculate(
        revision_features=revisions,
        surprise_summary=SurpriseSummary(
            features=(),
            average_surprise_pct=Decimal("0.10"),
            positive_count=2,
            negative_count=0,
            consistency="consistently_positive",
            price_response_quality=7.0,
        ),
        guidance_events=[
            CeriGuidanceEvent(
                id=103,
                source_record_id=104,
                company_id=1,
                action="RAISED",
                confidence="High",
                effective_session=cutoff.latest_completed_session,
                accepted_for_scoring=True,
                metric="EPS_DILUTED",
                period_type="FY",
                quality_warnings_json=[],
            )
        ],
        catalyst_features=[catalyst],
        price_response_quality=7.0,
        price_response_parent_event_id=101,
        as_of_session=cutoff.latest_completed_session,
    )
    confidence = CeriConfidenceService().calculate(
        revision_features=revisions,
        as_of_session=cutoff.latest_completed_session,
        dataset_freshness_days={CeriDataset.ESTIMATES.value: 0},
    )
    risk = CeriEventRiskService().calculate(
        as_of_session=cutoff.latest_completed_session,
        next_earnings_session=cutoff.latest_completed_session + timedelta(days=30),
        options_event_premium_score=volatility,
        short_pressure_classification=short_pressure,
    )
    snapshot = CeriSnapshotService().build_snapshot(
        company_id=1,
        ticker="MSFT",
        as_of_session=cutoff.latest_completed_session,
        cutoff_at=cutoff.cutoff_at,
        opportunity=opportunity,
        event_risk=risk,
        confidence=confidence,
        source_ids=[*range(1, len(revisions) + 1), 101, 102, 103, 104],
    )
    return snapshot, opportunity, confidence, risk
