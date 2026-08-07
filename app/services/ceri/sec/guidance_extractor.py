from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class GuidanceExtraction:
    metric: str | None
    period_label: str | None
    low_value: Decimal | None
    high_value: Decimal | None
    point_value: Decimal | None
    action: str
    confidence: str
    evidence_locator: str
    matched_text: str
    warnings: tuple[str, ...] = ()


class GuidanceExtractionService:
    """Conservative filing-text extraction with reproducible evidence locators."""

    _RANGE = re.compile(
        r"\$?(?P<low>\d+(?:\.\d+)?)\s*(?:to|[-\u2013])\s*"
        r"\$?(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>%|million|billion)?",
        re.I,
    )
    _POINT = re.compile(
        r"(?:of|at|approximately|around)\s*\$?"
        r"(?P<point>\d+(?:\.\d+)?)",
        re.I,
    )

    def extract(self, text: str, *, locator: str = "document") -> list[GuidanceExtraction]:
        results: list[GuidanceExtraction] = []
        parts = re.split(r"\n\s*\n|(?<=[.!?])\s+", text)
        for index, paragraph in enumerate(parts):
            lowered = paragraph.lower()
            if not any(word in lowered for word in ("guidance", "outlook", "expects", "forecast")):
                continue
            action = _action(lowered)
            metric = _metric(lowered)
            period = _period(lowered)
            match = self._RANGE.search(paragraph)
            low = high = point = None
            warnings: list[str] = []
            if match:
                low = _decimal(match.group("low"))
                high = _decimal(match.group("high"))
            else:
                point_match = self._POINT.search(paragraph)
                point = _decimal(point_match.group("point")) if point_match else None
            if metric is None or period is None or (low is None and point is None):
                action = "UNKNOWN"
                warnings.append("guidance_comparison_insufficient")
            confidence = (
                "HIGH"
                if metric
                and period
                and action != "UNKNOWN"
                and (low is not None or point is not None)
                else "LOW"
            )
            results.append(
                GuidanceExtraction(
                    metric,
                    period,
                    low,
                    high,
                    point,
                    action,
                    confidence,
                    f"{locator}#paragraph-{index + 1}",
                    paragraph.strip(),
                    tuple(warnings),
                )
            )
        return results


def _action(text: str) -> str:
    actions = (
        ("raised", "RAISED"),
        ("lowered", "LOWERED"),
        ("withdrawn", "WITHDRAWN"),
        ("initiated", "INITIATED"),
        ("maintained", "MAINTAINED"),
        ("narrowed", "NARROWED"),
        ("widened", "WIDENED"),
    )
    for word, action in actions:
        if word in text:
            return action
    return "UNKNOWN"


def _metric(text: str) -> str | None:
    if "revenue" in text or "sales" in text:
        return "REVENUE"
    if "eps" in text or "earnings per share" in text:
        return "EPS_DILUTED"
    return None


def _period(text: str) -> str | None:
    if any(term in text for term in ("full year", "fiscal year", "fy ")):
        return "CURRENT_FISCAL_YEAR"
    if any(term in text for term in ("quarter", "q1", "q2", "q3", "q4")):
        return "CURRENT_QUARTER"
    return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None
