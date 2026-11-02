"""Trading-risk warnings remain visible while the preserved v1 policies abstain."""

import pytest
from readiness_helpers import seal_technical
from test_combined_decision import _config
from test_technical_consumer_eligibility import _context

from app.services.combined_decision import combine_row_decision
from app.services.producer_readiness import ReadinessStatus, readiness_from_evidence
from app.services.ranking_profile_config import get_ranking_profile
from app.services.ranking_profile_engine import rank_single_row
from app.services.technical_consumer_eligibility import TECHNICAL_TO_COMBINED, TECHNICAL_TO_RANKING
from app.services.warning_taxonomy import classify_warning


@pytest.mark.parametrize(
    "warnings",
    [
        ["distribution_risk", "market_risk_off"],
        ["market_risk_off", "distribution_risk"],
        ["market_risk_off", "stage_3_distribution", "distribution_risk"],
    ],
)
def test_high_quality_risk_only_technical_is_intentionally_omitted_by_each_v1_consumer(warnings):
    context = _context(ReadinessStatus.READY)
    technical = context.technical_score
    technical.technical_confidence = "high"
    technical.insufficient_data = False
    technical.warning_flags_json = warnings
    seal_technical(technical)
    readiness = readiness_from_evidence(technical.calculation_evidence)
    assert all(not classify_warning(code).blocks_integrity_readiness for code in warnings)
    assert readiness.status is ReadinessStatus.DEGRADED
    assert not readiness.blocking_reasons
    for policy in (TECHNICAL_TO_COMBINED, TECHNICAL_TO_RANKING):
        decision = policy.evaluate(readiness)
        assert decision.status.value == "POLICY_UNDECIDED"
        assert [reason.value for reason in decision.reasons] == ["DEGRADED_POLICY_UNDECIDED"]
        assert decision.producer_evidence_id == technical.evidence_id
    combined = combine_row_decision(
        context.raw_row, context.fundamental_score, technical, config=_config()
    )
    assert combined.has_fundamental and not combined.has_technical and not combined.is_complete
    assert combined.dual_score is None
    ranking = rank_single_row(
        profile=get_ranking_profile("momentum_swing"),
        row=context.raw_row,
        fundamental=context.fundamental_score,
        technical=technical,
        config=_config(),
    )
    assert ranking.has_fundamental and not ranking.has_technical and not ranking.is_complete
    assert ranking.technical_profile_score is ranking.base_technical_score is None
    assert ranking.component_scores == {}
    assert ranking.position_size_hint == "No new entry"
    assert ranking.decision_label not in {"Candidate", "Strong candidate"}
    assert technical.warning_flags_json == warnings
    assert technical.technical_confidence == "high" and technical.insufficient_data is False


def test_complete_rows_with_analytical_warnings_still_require_partial_business_status():
    from app.services.pipeline_executor import _final_pipeline_status

    result = {
        "combined_results": 3, "incomplete_rows": 0, "warning_rows": 3,
        "ib_failure_count": 0, "technical_error_count": 0, "fetch_failed": 0,
        "market_regime_low_confidence": 0, "ceri_failed": 0,
        "setup_lifecycle_failed": 0, "winner_prediction_failed": 0,
    }
    assert _final_pipeline_status(result) == "PARTIAL"
    assert result["incomplete_rows"] == 0
    assert result["warning_rows"] == 3
