from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
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
    unit: str | None = None
    currency: str | None = None
    management_claim: str | None = None
    comparison_confidence: str = "INSUFFICIENT"
    warnings: tuple[str, ...] = ()


class GuidanceExtractionService:
    """Conservative filing-text extraction with reproducible evidence locators."""

    _RANGE = re.compile(
        r"(?:(?P<low_currency>USD|EUR|GBP)\s*)?\$?(?P<low>\d+(?:\.\d+)?)\s*"
        r"(?P<low_unit>%|million|billion)?\s*(?:to|[-\u2013])\s*"
        r"(?:(?P<high_currency>USD|EUR|GBP)\s*)?\$?(?P<high>\d+(?:\.\d+)?)\s*"
        r"(?P<high_unit>%|million|billion)?",
        re.I,
    )
    _POINT = re.compile(
        r"(?:of|at|approximately|around)\s*(?:(?P<currency>USD|EUR|GBP)\s*)?\$?"
        r"(?P<point>\d+(?:\.\d+)?)",
        re.I,
    )

    def extract(self, text: str, *, locator: str = "document") -> list[GuidanceExtraction]:
        results: list[GuidanceExtraction] = []
        visible_text = _visible_text(text)
        # Keep sentence context within a bounded visible-text paragraph. Splitting
        # at every period separated historical-table/XBRL warnings from the
        # nearby token that the old extractor then misclassified as guidance.
        parts = re.split(r"\n\s*\n", visible_text)
        for index, paragraph in enumerate(parts):
            lowered = paragraph.lower()
            if _hard_negative(paragraph):
                continue
            if not any(word in lowered for word in ("guidance", "outlook", "expects", "forecast")):
                continue
            management_claim = _action(lowered)
            metric = _metric(lowered)
            period = _period(lowered)
            match = self._RANGE.search(paragraph)
            low = high = point = None
            unit = currency = None
            warnings: list[str] = []
            if match:
                low = _decimal(match.group("low"))
                high = _decimal(match.group("high"))
                units = match.group("high_unit") or match.group("low_unit")
                unit = units.upper() if units else None
                currency = match.group("high_currency") or match.group("low_currency")
            else:
                point_match = self._POINT.search(paragraph)
                point = _decimal(point_match.group("point")) if point_match else None
                currency = point_match.group("currency") if point_match else None
                if point_match and "per share" in lowered:
                    unit = "PER_SHARE"
                elif point_match and "%" in paragraph[point_match.start() :]:
                    unit = "%"
            withdrawn = management_claim == "WITHDRAWN"
            complete = metric is not None and period is not None and (
                low is not None or point is not None or withdrawn
            )
            if not complete:
                management_claim = management_claim if management_claim != "UNKNOWN" else None
                warnings.append("guidance_comparison_insufficient")
            elif management_claim is not None:
                warnings.append("guidance_comparison_requires_prior")
            confidence = (
                "HIGH"
                if complete
                else "LOW"
            )
            results.append(
                GuidanceExtraction(
                    metric=metric,
                    period_label=period,
                    low_value=low,
                    high_value=high,
                    point_value=point,
                    action="UNKNOWN",
                    confidence=confidence,
                    evidence_locator=f"{locator}#paragraph-{index + 1}",
                    matched_text=paragraph.strip(),
                    unit=unit,
                    currency=currency.upper() if currency else None,
                    management_claim=management_claim,
                    comparison_confidence="INSUFFICIENT",
                    warnings=tuple(warnings),
                )
            )
        return results


def _action(text: str) -> str:
    actions = (
        ("raised", "RAISED"),
        ("lowered", "LOWERED"),
        ("withdrawn", "WITHDRAWN"),
        ("withdrew", "WITHDRAWN"),
        ("withdraw", "WITHDRAWN"),
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
    if any(term in text for term in ("full year", "full-year", "fiscal year", "fy ")):
        return "CURRENT_FISCAL_YEAR"
    if any(term in text for term in ("quarter", "q1", "q2", "q3", "q4")):
        return "CURRENT_QUARTER"
    return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


class _VisibleTextParser(HTMLParser):
    _IGNORED = {
        "head",
        "script",
        "style",
        "ix:hidden",
        "xbrli:context",
        "xbrli:unit",
        "context",
        "unit",
        "schema",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if self.depth or normalized in self._IGNORED or normalized.endswith(":context"):
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.depth and data.strip():
            self.parts.append(data.strip())


def _visible_text(text: str) -> str:
    if "<" not in text or ">" not in text:
        return unescape(text)
    parser = _VisibleTextParser()
    parser.feed(text)
    parser.close()
    return "\n\n".join(parser.parts)


def _hard_negative(paragraph: str) -> bool:
    lowered = paragraph.lower()
    compact = re.sub(r"\s+", " ", lowered)
    if "private securities litigation reform act of 1995" in compact:
        return True
    if "forward-looking statement" in compact and any(
        token in compact for token in ("involve risks", "actual results", "undue reliance")
    ):
        return True
    if "pension" in compact and any(token in compact for token in ("historical", "benefit")):
        return True
    if re.search(r"\b(?:19|20)\d{6}\s*[-\u2013]\s*(?:19|20)\d{6}\b", compact):
        return True
    if re.search(
        r"\b(?:19|20)\d{2}-\d{2}-\d{2}\s*(?:to|[-\u2013])\s*"
        r"(?:19|20)\d{2}-\d{2}-\d{2}\b",
        compact,
    ):
        return True
    if any(token in compact for token in ("contextref=", "xmlns:", "xbrli:", "ix:")):
        return True
    return False
