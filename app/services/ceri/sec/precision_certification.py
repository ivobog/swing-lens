from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService


@dataclass(frozen=True)
class GuidancePrecisionCertification:
    true_positives: int
    false_positives: int
    false_negatives: int
    accepted_precision: float
    golden_false_positives: int


def certify_guidance_precision(
    labeled_passages: Iterable[dict[str, Any]],
    *,
    extractor: GuidanceExtractionService,
) -> GuidancePrecisionCertification:
    true_positives = false_positives = false_negatives = 0
    for index, fixture in enumerate(labeled_passages):
        rows = extractor.extract(str(fixture["text"]), locator=f"golden-{index}")
        accepted = any(
            row.confidence == "HIGH"
            and row.metric is not None
            and row.period_label is not None
            and (
                row.low_value is not None
                or row.point_value is not None
                or row.management_claim == "WITHDRAWN"
            )
            for row in rows
        )
        expected = bool(fixture["label"])
        if accepted and expected:
            true_positives += 1
        elif accepted:
            false_positives += 1
        elif expected:
            false_negatives += 1
    denominator = true_positives + false_positives
    precision = true_positives / denominator if denominator else 0.0
    return GuidancePrecisionCertification(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        accepted_precision=precision,
        golden_false_positives=false_positives,
    )
