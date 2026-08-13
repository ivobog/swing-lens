from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.models.ceri_tables import CeriGuidanceEvent
from app.services.ceri.guidance_comparison_service import compare_guidance
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService


@pytest.mark.parametrize("accepted", [None, False])
def test_guidance_scoring_is_explicit_true_allow_list(accepted: bool | None) -> None:
    event = _guidance(accepted=accepted)

    result = CeriOpportunityScoreService().calculate(
        revision_features=[], guidance_events=[event]
    )

    component = next(item for item in result.components if item.name == "guidance")
    assert component.available is False
    assert component.value is None
    assert component.evidence_ids == ()


def test_explicitly_accepted_clean_guidance_can_score() -> None:
    event = _guidance(accepted=True)

    result = CeriOpportunityScoreService().calculate(
        revision_features=[], guidance_events=[event]
    )

    component = next(item for item in result.components if item.name == "guidance")
    assert component.available is True
    assert component.value == 8.0
    assert component.evidence_ids == (1,)


def test_stale_and_superseded_guidance_are_excluded() -> None:
    as_of = date(2026, 8, 13)
    stale = _guidance(accepted=True, event_id=1, effective_session=as_of - timedelta(days=30))
    current = _guidance(accepted=True, event_id=2, effective_session=as_of)
    current.supersedes_id = stale.id

    result = CeriOpportunityScoreService().calculate(
        revision_features=[],
        guidance_events=[stale, current],
        as_of_session=as_of,
    )

    component = next(item for item in result.components if item.name == "guidance")
    assert component.evidence_ids == (2,)


def test_missing_prior_comparable_is_unknown_and_not_accepted() -> None:
    current = _guidance(accepted=True)

    comparison = compare_guidance(current, None)

    assert comparison.action == "UNKNOWN"
    assert comparison.confidence == "INSUFFICIENT"


def test_run101_golden_false_positive_passages_fail_closed() -> None:
    fixtures = json.loads(
        Path("tests/ceri/fixtures/run101_sec_false_positive_passages.json").read_text(
            encoding="utf-8"
        )
    )
    extractor = GuidanceExtractionService()

    for fixture in fixtures:
        assert extractor.extract(fixture["text"], locator=fixture["case"]) == []


def test_clean_visible_raised_revenue_range_is_extracted() -> None:
    passage = (
        "Management raised full-year revenue guidance and now expects revenue "
        "of $5.2 billion to $5.4 billion."
    )

    result = GuidanceExtractionService().extract(passage, locator="fixture")

    assert len(result) == 1
    assert result[0].metric == "REVENUE"
    assert result[0].period_label == "CURRENT_FISCAL_YEAR"
    assert result[0].low_value is not None
    assert result[0].high_value is not None


def test_sec_acceptance_migration_is_safe_and_fail_closed() -> None:
    migration = Path(
        "alembic/versions/20260813_0042_ceri_run101_fail_closed.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0041_sec_incremental_documents"' in migration
    assert "accepted_for_scoring IS NULL" in migration
    assert "server_default=sa.false()" in migration
    assert "nullable=False" in migration
    assert "ceri_score_snapshots" not in migration


def _guidance(
    *,
    accepted: bool | None,
    event_id: int = 1,
    effective_session: date | None = None,
) -> CeriGuidanceEvent:
    return CeriGuidanceEvent(
        id=event_id,
        source_record_id=event_id,
        company_id=1,
        action="RAISED",
        metric="REVENUE",
        period_type="CURRENT_FISCAL_YEAR",
        confidence="High",
        accepted_for_scoring=accepted,
        effective_session=effective_session,
    )
