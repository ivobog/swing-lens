from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.ceri.change_detection_service import CeriChangeDetectionService
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.services.ceri.sec.precision_certification import certify_guidance_precision


def test_clean_range_preserves_action_metric_period_unit_and_currency() -> None:
    row = GuidanceExtractionService().extract(
        "Management raised full-year revenue guidance to "
        "USD 100 million to USD 110 million."
    )[0]

    assert row.management_claim == "RAISED"
    assert row.metric == "REVENUE"
    assert row.period_label == "CURRENT_FISCAL_YEAR"
    assert (row.low_value, row.high_value) == (100, 110)
    assert row.unit == "MILLION"
    assert row.currency == "USD"


def test_maintained_withdrawn_and_point_guidance_are_distinct() -> None:
    extractor = GuidanceExtractionService()
    maintained = extractor.extract(
        "The company maintained full year revenue guidance of "
        "USD 200 million to USD 220 million."
    )[0]
    withdrawn = extractor.extract("Management withdrew its full-year revenue guidance.")[0]
    point = extractor.extract(
        "The company expects full-year EPS guidance of USD 2.50 per share."
    )[0]

    assert maintained.management_claim == "MAINTAINED"
    assert withdrawn.management_claim == "WITHDRAWN"
    assert withdrawn.low_value is None and withdrawn.point_value is None
    assert point.point_value == 2.50
    assert point.unit == "PER_SHARE"
    assert point.currency == "USD"


def test_rejected_guidance_cannot_create_lifecycle_event() -> None:
    guidance = SimpleNamespace(
        action="RAISED",
        accepted_for_scoring=False,
        effective_session=None,
    )

    result = CeriChangeDetectionService().detect_guidance_change(
        None, guidance=guidance, company_id=1, prior_action="MAINTAINED"
    )

    assert result.changes == 0


def test_labeled_processor_signature_meets_initial_precision_gate() -> None:
    labels = json.loads(
        (Path(__file__).parent / "fixtures" / "sec_guidance_precision_v2.json").read_text()
    )

    result = certify_guidance_precision(labels, extractor=GuidanceExtractionService())

    assert result.accepted_precision >= 0.90
    assert result.golden_false_positives == 0
    assert result.true_positives >= 4
