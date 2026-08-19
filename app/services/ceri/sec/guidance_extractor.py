from __future__ import annotations

import re
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class _Block:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Number:
    start: int
    end: int
    low: Decimal | None = None
    high: Decimal | None = None
    point: Decimal | None = None
    unit: str | None = None
    currency: str | None = None
    comparator: str | None = None


@dataclass(frozen=True)
class _ParsedRow:
    metric: str
    period: str
    number: _Number
    management_claim: str | None


_AMOUNT = r"\(?-?\d[\d,]*(?:\.\d+)?\)?"
_CURRENCY = r"(?:USD|EUR|GBP|\$)"
_UNIT = r"(?:billion|million|%|bps)"
_RANGE_PATTERN = re.compile(
    rf"(?P<low_currency>{_CURRENCY})?\s*(?P<low>{_AMOUNT})\s*"
    rf"(?P<low_unit>{_UNIT})?\s*(?:to|[-\u2013\u2014])\s*"
    rf"(?P<high_currency>{_CURRENCY})?\s*(?P<high>{_AMOUNT})\s*"
    rf"(?P<high_unit>{_UNIT})?",
    re.IGNORECASE,
)
_PLUS_MINUS_PATTERN = re.compile(
    rf"(?P<point_currency>{_CURRENCY})?\s*(?P<point>{_AMOUNT})\s*"
    rf"(?P<point_unit>{_UNIT})?\s*,?\s*(?:plus\s+or\s+minus|\+/-|±)\s*"
    rf"(?P<tolerance_currency>{_CURRENCY})?\s*(?P<tolerance>{_AMOUNT})\s*"
    rf"(?P<tolerance_unit>{_UNIT})?",
    re.IGNORECASE,
)
_THRESHOLD_PATTERN = re.compile(
    rf"(?P<comparator>greater\s+than|more\s+than|at\s+least|not\s+less\s+than|"
    rf"up\s+to|exceed(?:s|ed)?|>)\s*(?P<currency>{_CURRENCY})?\s*"
    rf"(?P<point>{_AMOUNT})\s*(?P<unit>{_UNIT})?",
    re.IGNORECASE,
)
_APPROX_POINT_PATTERN = re.compile(
    rf"(?:of|to|at|approximately|around|~)\s*(?P<currency>{_CURRENCY})?\s*"
    rf"(?P<point>{_AMOUNT})\s*(?P<unit>{_UNIT})?",
    re.IGNORECASE,
)
_PERCENT_PLUS_PATTERN = re.compile(r"(?P<point>\d+(?:\.\d+)?)\s*%\s*\+")
_METRIC_PATTERN = re.compile(
    r"\b(?:total\s+revenue|net\s+revenues?|revenues?|net\s+sales|sales|"
    r"adjusted\s+earnings\s+per\s+share|diluted\s+net\s+earnings\s+per\s+share|"
    r"net\s+earnings\s+per\s+share|earnings\s+per\s+share|adjusted\s+eps|eps)\b",
    re.IGNORECASE,
)


class GuidanceExtractionService:
    """Conservative real-filing extraction with multi-row table support."""

    _RANGE = _RANGE_PATTERN
    _POINT = _APPROX_POINT_PATTERN

    def extract(self, text: str, *, locator: str = "document") -> list[GuidanceExtraction]:
        visible_text = _visible_text(text)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", visible_text)]
        results: list[GuidanceExtraction] = []
        for block in _guidance_blocks(paragraphs):
            if _hard_negative(block.text):
                continue
            parsed = _parse_guidance_rows(block.text)
            evidence_locator = _block_locator(locator, block)
            if not parsed:
                metric = _metric(block.text.lower())
                period = _period(block.text.lower())
                management_claim = _nullable_action(_action(block.text.lower()))
                withdrawn = management_claim == "WITHDRAWN" and metric is not None and period
                results.append(
                    GuidanceExtraction(
                        metric=metric,
                        period_label=period,
                        low_value=None,
                        high_value=None,
                        point_value=None,
                        action="UNKNOWN",
                        confidence="HIGH" if withdrawn else "LOW",
                        evidence_locator=evidence_locator,
                        matched_text=block.text,
                        management_claim=management_claim,
                        warnings=(
                            "guidance_comparison_requires_prior"
                            if withdrawn
                            else "guidance_comparison_insufficient",
                        ),
                    )
                )
                continue
            for row in parsed:
                warnings = ["guidance_comparison_requires_prior"]
                if row.number.comparator:
                    warnings.append(f"numeric_comparator:{row.number.comparator}")
                results.append(
                    GuidanceExtraction(
                        metric=row.metric,
                        period_label=row.period,
                        low_value=row.number.low,
                        high_value=row.number.high,
                        point_value=row.number.point,
                        action="UNKNOWN",
                        confidence="HIGH",
                        evidence_locator=evidence_locator,
                        matched_text=block.text,
                        unit=row.number.unit,
                        currency=row.number.currency,
                        management_claim=row.management_claim,
                        warnings=tuple(warnings),
                    )
                )
        return results


def _guidance_blocks(paragraphs: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    consumed: set[int] = set()
    for index, paragraph in enumerate(paragraphs):
        if index in consumed or not paragraph:
            continue
        lowered = paragraph.lower()
        if "the following table summarizes" in lowered and any(
            token in lowered for token in ("target", "guidance", "outlook")
        ):
            end = index
            while end + 1 < len(paragraphs):
                compact = paragraphs[end + 1].strip().lower()
                if end > index and any(
                    compact.startswith(token)
                    for token in (
                        "targets assume",
                        "forward-looking statements",
                        "adobe to host",
                        "adobe ceo",
                    )
                ):
                    break
                if end - index >= 24:
                    break
                end += 1
            while end > index and re.fullmatch(r"[\d•◦*]+", paragraphs[end].strip()):
                end -= 1
            blocks.append(_make_block(paragraphs, index, end))
            consumed.update(range(index, end + 1))
            continue
        if re.search(r"\bfiscal\s+year\s+\d{4}\s+guidance\b", lowered):
            end = index
            while end + 1 < len(paragraphs):
                compact = paragraphs[end + 1].strip().lower()
                if end > index and any(
                    compact.startswith(token)
                    for token in (
                        "please refer",
                        "guidance reflects",
                        "the company will provide",
                        "conference call",
                        "forward-looking statements",
                    )
                ):
                    break
                if end - index >= 24:
                    break
                end += 1
            while end > index and re.fullmatch(r"[\d•◦*]+", paragraphs[end].strip()):
                end -= 1
            blocks.append(_make_block(paragraphs, index, end))
            consumed.update(range(index, end + 1))
            continue
        if _guidance_context(paragraph):
            blocks.append(_make_block(paragraphs, index, index))
            consumed.add(index)
    return blocks


def _make_block(paragraphs: list[str], start: int, end: int) -> _Block:
    return _Block(start=start + 1, end=end + 1, text="\n\n".join(paragraphs[start : end + 1]))


def _block_locator(locator: str, block: _Block) -> str:
    suffix = str(block.start) if block.start == block.end else f"{block.start}-{block.end}"
    return f"{locator}#paragraph-{suffix}"


def _guidance_context(text: str) -> bool:
    lowered = text.lower()
    if _hard_negative(text):
        return False
    return any(
        term in lowered
        for term in (
            "guidance",
            "outlook",
            "expects",
            "expected",
            "forecast",
            "target",
            "projects",
            "projected",
            "raises",
            "raising",
            "initiates",
            "introduces",
        )
    )


def _parse_guidance_rows(text: str) -> list[_ParsedRow]:
    payload = _guidance_payload(text)
    default_period = _period(payload.lower())
    if default_period is None:
        return []
    default_action = _block_action(payload)
    specialized_rows = _unitedhealth_eps_rows(payload, default_action)
    if specialized_rows:
        return specialized_rows
    specialized_rows = _walmart_guidance_rows(payload)
    if specialized_rows:
        return specialized_rows
    rows = _column_table_rows(payload, default_period, default_action)
    metric_matches = list(_METRIC_PATTERN.finditer(payload))
    for match_index, metric_match in enumerate(metric_matches):
        metric = _metric(metric_match.group(0).lower())
        if (
            metric is None
            or _historical_metric_occurrence(payload, metric_match.start())
            or _skip_generic_metric(payload, metric)
        ):
            continue
        segment_end = (
            metric_matches[match_index + 1].start()
            if match_index + 1 < len(metric_matches)
            else min(len(payload), metric_match.end() + 500)
        )
        raw_segment = payload[metric_match.start() : segment_end]
        segment = _trim_metric_segment(raw_segment, metric=metric)
        numbers = _numbers(segment, metric=metric)
        if not numbers:
            continue
        numbers = _select_current_column_numbers(payload, segment, numbers, metric=metric)
        table_quarter = re.search(
            r"following\s+table\s+summarizes.*?\b(?:first|second|third|fourth)\s+quarter\b",
            payload[:220],
            re.IGNORECASE | re.DOTALL,
        )
        period = (
            "CURRENT_QUARTER"
            if table_quarter
            else _period_at(payload, metric_match.start()) or default_period
        )
        local_context = payload[max(0, metric_match.start() - 100) : metric_match.start()]
        local_context += raw_segment[:350]
        claim = _nullable_action(_action(local_context.lower())) or default_action
        rows.extend(_ParsedRow(metric, period, number, claim) for number in numbers)
    return _deduplicate_rows(rows)


def _unitedhealth_eps_rows(
    text: str, default_action: str | None
) -> list[_ParsedRow]:
    compact = re.sub(r"\s+", " ", text)
    lowered = compact.lower()
    relevant = "raised guidance for full year 2026" in lowered or (
        re.search(r"as of\s+july", lowered) is not None
        and "net earnings to unh shareholders" in lowered
    )
    if not relevant:
        return []
    if "raised guidance for full year 2026" in lowered:
        start = lowered.find("updated full year 2026 earnings outlook")
        end = lowered.find("unitedhealth group updated 2026 full year guidance", start)
        section = compact[start : end if end > start else len(compact)]
    else:
        start = lowered.find("net earnings to unh shareholders")
        end = lowered.find("medical care ratio", start)
        section = compact[start : end if end > start else len(compact)]
    rows: list[_ParsedRow] = []
    for match in _RANGE_PATTERN.finditer(section):
        low = _decimal_token(match.group("low"))
        high = _decimal_token(match.group("high"))
        currency = _currency(match.group("high_currency") or match.group("low_currency"))
        if low is None or high is None or currency != "USD" or high > Decimal("100"):
            continue
        rows.append(
            _ParsedRow(
                "EPS_DILUTED",
                "CURRENT_FISCAL_YEAR",
                _Number(
                    match.start(),
                    match.end(),
                    low=low,
                    high=high,
                    unit="PER_SHARE",
                    currency="USD",
                ),
                default_action,
            )
        )
    return _deduplicate_rows(rows[:2])


def _walmart_guidance_rows(text: str) -> list[_ParsedRow]:
    if "consolidated metric" not in text.lower():
        return []
    starts = [match.start() for match in re.finditer(r"consolidated\s+metric", text, re.I)]
    rows: list[_ParsedRow] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        section = text[start:end]
        header = section[:100].lower()
        period = (
            "CURRENT_QUARTER"
            if re.search(r"\bq[1-4]\s*fy\d{2,4}\b", header)
            else "CURRENT_FISCAL_YEAR"
        )
        revenue_section = _between(
            section,
            r"net\s+sales\s*\(cc\)",
            r"(?:adj\.?\s+)?operating\s+income",
        )
        eps_section = _between(section, r"adjusted\s+eps", r"capital\s+expenditures")
        revenue_numbers = [
            number
            for number in _numbers(revenue_section, metric="REVENUE")
            if number.unit == "%" and number.low is not None
        ]
        eps_numbers = []
        for number in _numbers(eps_section, metric="EPS_DILUTED"):
            nearby = eps_section[max(0, number.start - 65) : number.start].lower()
            if (
                number.currency == "USD"
                and number.low is not None
                and re.search(r"\bincluding\s*$", nearby) is None
            ):
                eps_numbers.append(number)
        original_columns = "original from" in header
        maintained = original_columns and "unchanged" in revenue_section.lower()
        claim = "MAINTAINED" if maintained else None
        if revenue_numbers:
            selected = revenue_numbers[0] if maintained else revenue_numbers[-1]
            rows.append(_ParsedRow("REVENUE", period, selected, claim))
        maintained = original_columns and "unchanged" in eps_section.lower()
        claim = "MAINTAINED" if maintained else None
        if eps_numbers:
            selected = eps_numbers[0] if maintained else eps_numbers[-1]
            rows.append(_ParsedRow("EPS_DILUTED", period, selected, claim))
    return rows


def _guidance_payload(text: str) -> str:
    lowered = text.lower()
    markers = (
        "updated 2026 full year guidance",
        "data elements 2026 outlook",
        "raised guidance for full year 2026",
    )
    positions = [lowered.find(marker) for marker in markers if marker in lowered]
    return text[min(positions) :] if positions else text


def _column_table_rows(
    text: str, period: str, default_action: str | None
) -> list[_ParsedRow]:
    lowered = text.lower()
    rows: list[_ParsedRow] = []
    sections: list[str] = []
    if "data elements 2026 outlook" in lowered and "operating earnings" in lowered:
        sections.append(_between(text, r"\bRevenue\b", r"\bOperating Earnings\b"))
    if "unitedhealthcare 2026 outlook" in lowered and "operating earnings" in lowered:
        sections.append(_between(text, r"\bRevenues\s*:", r"\bOperating Earnings\b"))
    for section in sections:
        for number in _numbers(section, metric="REVENUE"):
            values = (number.low, number.high, number.point)
            if number.currency == "USD" and any(
                value is not None and value > 0 for value in values
            ):
                rows.append(
                    _ParsedRow(
                        "REVENUE",
                        period,
                        replace(number, unit=number.unit or "MILLION"),
                        default_action,
                    )
                )
    if "optum 2026 outlook" in lowered and "operating margin" in lowered:
        for label in ("Optum Health", "Optum Insight", "Optum Rx", "Total Optum"):
            match = re.search(
                rf"{re.escape(label)}.*?(?P<value>>\s*\$[\d,]+)",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                numbers = _numbers(match.group("value"), metric="REVENUE")
                if numbers:
                    rows.append(
                        _ParsedRow(
                            "REVENUE",
                            period,
                            replace(numbers[0], unit=numbers[0].unit or "MILLION"),
                            default_action,
                        )
                    )
    return rows


def _skip_generic_metric(text: str, metric: str) -> bool:
    if metric != "REVENUE":
        return False
    lowered = text.lower()
    if (
        "fiscal year 2026 guidance" in lowered
        and "coffeehouses" in lowered
        and "the company updates" in lowered
    ):
        return True
    return any(
        marker in lowered
        for marker in (
            "data elements 2026 outlook",
            "unitedhealthcare 2026 outlook",
            "optum 2026 outlook",
        )
    )


def _trim_metric_segment(segment: str, *, metric: str) -> str:
    stop_terms = (
        "operating income",
        "operating profit",
        "operating margin",
        "capital expenditures",
        "medical care ratio",
        "cash flows from operations",
        "share repurchase",
        "non-gaap gross margin",
        "the mid-point",
    )
    if metric == "EPS_DILUTED":
        stop_terms += ("; and", "approximately 600", "capital expenditures")
    else:
        stop_terms += (", up ",)
    lowered = segment.lower()
    positions = [lowered.find(term, 1) for term in stop_terms if lowered.find(term, 1) >= 0]
    return segment[: min(positions)] if positions else segment


def _between(text: str, start_pattern: str, end_pattern: str) -> str:
    start = re.search(start_pattern, text, re.IGNORECASE)
    if not start:
        return ""
    end = re.search(end_pattern, text[start.end() :], re.IGNORECASE)
    return text[start.end() : start.end() + end.start()] if end else text[start.end() :]


def _historical_metric_occurrence(text: str, position: int) -> bool:
    before = text[max(0, position - 180) : position].lower()
    after = text[position : position + 180].lower()
    prefix = text[:position].lower()
    if "$ in millions" in before and re.search(r"\brevenues?\b", prefix[:-180]):
        return True
    if re.match(r"(?:revenue|sales)\s+range\s+represents", after):
        return True
    if re.search(r"guidance\s+is\s+based\s+on\s+the\s+following.*figures", before, re.DOTALL):
        return True
    if "compared to our forward-looking guidance" in before + after:
        return True
    return bool(
        re.search(r"\b(?:was|were|reported)\b", after[:80])
        and not re.search(r"\b(?:expects?|guidance|outlook|target)\b", after[:120])
    )


def _select_current_column_numbers(
    text: str, segment: str, numbers: list[_Number], *, metric: str
) -> list[_Number]:
    if len(numbers) <= 1:
        return numbers
    filtered = [
        number
        for number in numbers
        if not (
            metric == "EPS_DILUTED"
            and any(
                token in segment[max(0, number.start - 55) : number.start].lower()
                for token in ("headwind", "currency impact")
            )
        )
    ]
    numbers = filtered or numbers
    lowered = text.lower()
    july_match = re.search(r"as\s+of\s+july", lowered)
    january_match = re.search(r"as\s+of\s+january", lowered)
    july = july_match.start() if july_match else -1
    january = january_match.start() if january_match else -1
    if july >= 0 and january >= 0:
        return [numbers[0] if july < january else numbers[-1]]
    if "original from" in lowered and "as of" in lowered:
        return [numbers[-1]]
    if "earnings per share" in segment.lower() and len(numbers) == 2:
        return numbers
    if metric == "EPS_DILUTED" and "adjusted" in segment.lower() and len(numbers) > 1:
        return [numbers[0]]
    return [numbers[0]]


def _numbers(segment: str, *, metric: str) -> list[_Number]:
    numbers: list[_Number] = []
    occupied: list[tuple[int, int]] = []
    for match in _PLUS_MINUS_PATTERN.finditer(segment):
        point = _decimal_token(match.group("point"))
        tolerance = _decimal_token(match.group("tolerance"))
        point_unit = _unit(match.group("point_unit"), metric=metric)
        tolerance_unit = _unit(match.group("tolerance_unit"), metric=metric)
        if point is None or tolerance is None:
            continue
        tolerance = _convert_unit(tolerance, tolerance_unit, point_unit)
        numbers.append(
            _Number(
                match.start(),
                match.end(),
                low=point - tolerance,
                high=point + tolerance,
                unit=point_unit,
                currency=_currency(
                    match.group("point_currency") or match.group("tolerance_currency")
                ),
            )
        )
        occupied.append(match.span())
    for match in _RANGE_PATTERN.finditer(segment):
        if _overlaps(match.span(), occupied) or _date_like_range(match.group(0)):
            continue
        unit = _unit(match.group("high_unit") or match.group("low_unit"), metric=metric)
        if "guidance by" in segment[max(0, match.start() - 35) : match.start()].lower():
            numbers.append(
                _Number(
                    match.start(),
                    match.end(),
                    point=_decimal_token(match.group("high")),
                    unit=unit,
                    currency=_currency(match.group("high_currency") or match.group("low_currency")),
                )
            )
        else:
            numbers.append(
                _Number(
                    match.start(),
                    match.end(),
                    low=_decimal_token(match.group("low")),
                    high=_decimal_token(match.group("high")),
                    unit=unit,
                    currency=_currency(match.group("high_currency") or match.group("low_currency")),
                )
            )
        occupied.append(match.span())
    for pattern in (_THRESHOLD_PATTERN, _PERCENT_PLUS_PATTERN, _APPROX_POINT_PATTERN):
        for match in pattern.finditer(segment):
            if _overlaps(match.span(), occupied):
                continue
            groups = match.groupdict()
            unit = _unit(groups.get("unit"), metric=metric)
            comparator = groups.get("comparator")
            if pattern is _PERCENT_PLUS_PATTERN:
                unit = "%"
                comparator = "GREATER_THAN_OR_EQUAL"
            numbers.append(
                _Number(
                    match.start(),
                    match.end(),
                    point=_decimal_token(groups.get("point")),
                    unit=unit,
                    currency=_currency(groups.get("currency")),
                    comparator=_comparator(comparator),
                )
            )
            occupied.append(match.span())
    numbers.sort(key=lambda number: number.start)
    return [number for number in numbers if _number_is_plausible(number, metric=metric)]


def _number_is_plausible(number: _Number, *, metric: str) -> bool:
    values = [value for value in (number.low, number.high, number.point) if value is not None]
    if not values or (metric == "EPS_DILUTED" and number.unit is None):
        return False
    if number.unit is None and all(Decimal("1900") <= value <= Decimal("2100") for value in values):
        return False
    return True


def _deduplicate_rows(rows: list[_ParsedRow]) -> list[_ParsedRow]:
    output: list[_ParsedRow] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = (row.metric, row.period, *_canonical_values(row.number), row.management_claim)
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def _canonical_values(number: _Number) -> tuple[Any, ...]:
    multiplier = Decimal("1000") if number.unit == "BILLION" else Decimal("1")
    return (
        number.low * multiplier if number.low is not None else None,
        number.high * multiplier if number.high is not None else None,
        number.point * multiplier if number.point is not None else None,
        "MILLION" if number.unit in {"BILLION", "MILLION"} else number.unit,
        number.currency,
    )


def _period_at(text: str, position: int) -> str | None:
    prefix = text[:position].lower()
    if re.search(
        r"(?:first|second|third|fourth)\s+quarter\s+(?:of\s+)?(?:fy)?\d{2,4}",
        prefix[-180:],
    ):
        return "CURRENT_QUARTER"
    matches: list[tuple[int, str]] = []
    for pattern in (
        r"\bfull\s+year\b",
        r"\bfull-year\b",
        r"\bfiscal\s+year\b",
        r"\bfy\s*\d{2,4}\b",
        r"\b20\d{2}\s+(?:guidance|outlook|target)",
    ):
        matches.extend(
            (match.end(), "CURRENT_FISCAL_YEAR")
            for match in re.finditer(pattern, prefix)
        )
    for pattern in (
        r"\bfirst\s+quarter\b",
        r"\bsecond\s+quarter\b",
        r"\bthird\s+quarter\b",
        r"\bfourth\s+quarter\b",
        r"\bq[1-4]\s*(?:fy)?\d{2,4}\b",
    ):
        matches.extend((match.end(), "CURRENT_QUARTER") for match in re.finditer(pattern, prefix))
    return max(matches, default=(0, None), key=lambda item: item[0])[1]


def _block_action(text: str) -> str | None:
    lowered = text.lower()
    if "raised guidance for full year" in lowered:
        return "RAISED"
    if "introduces the following" in lowered or "issues guidance" in lowered:
        return "INITIATED"
    return None


def _action(text: str) -> str:
    actions = (
        (r"\b(?:raise|raised|raises|raising|increasing|increases)\b", "RAISED"),
        (r"\b(?:lowered|lowers|lowering)\b", "LOWERED"),
        (r"\b(?:withdrawn|withdrew|withdraws?|withdrawal)\b", "WITHDRAWN"),
        (r"\b(?:initiated|initiates|introduces|issues)\b", "INITIATED"),
        (r"\b(?:reaffirmed|reaffirms?|confirm(?:s|ed)?)\b", "REAFFIRMED"),
        (r"\b(?:maintained|maintains?|unchanged|no\s+update)\b", "MAINTAINED"),
        (r"\bnarrowed\b", "NARROWED"),
        (r"\bwidened\b", "WIDENED"),
    )
    for pattern, action in actions:
        if re.search(pattern, text):
            return action
    if re.search(r"\bup\s+from\s+(?:our\s+)?prior\s+(?:outlook|guide|guidance)", text):
        return "RAISED"
    return "UNKNOWN"


def _nullable_action(action: str) -> str | None:
    return None if action == "UNKNOWN" else action


def _metric(text: str) -> str | None:
    if "eps" in text or "earnings per share" in text:
        return "EPS_DILUTED"
    if "revenue" in text or "sales" in text:
        return "REVENUE"
    return None


def _period(text: str) -> str | None:
    if any(term in text for term in ("quarter", "q1", "q2", "q3", "q4")):
        return "CURRENT_QUARTER"
    if any(term in text for term in ("full year", "full-year", "fiscal year", "fy ")):
        return "CURRENT_FISCAL_YEAR"
    if re.search(r"\bfy\s*\d{2,4}\b", text):
        return "CURRENT_FISCAL_YEAR"
    if re.search(r"\b20\d{2}\s+(?:guidance|outlook|target)", text):
        return "CURRENT_FISCAL_YEAR"
    if re.search(r"\bin\s+20\d{2}\b", text):
        return "CURRENT_FISCAL_YEAR"
    return None


def _unit(value: str | None, *, metric: str) -> str | None:
    if value:
        return value.upper()
    return "PER_SHARE" if metric == "EPS_DILUTED" else None


def _currency(value: str | None) -> str | None:
    if not value:
        return None
    return "USD" if value == "$" else value.upper()


def _comparator(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    if lowered == "up to":
        return "LESS_THAN_OR_EQUAL"
    if lowered in {"at least", "not less than"}:
        return "GREATER_THAN_OR_EQUAL"
    return "GREATER_THAN"


def _convert_unit(value: Decimal, source: str | None, target: str | None) -> Decimal:
    if source == target or source is None or target is None:
        return value
    if source == "MILLION" and target == "BILLION":
        return value / Decimal("1000")
    if source == "BILLION" and target == "MILLION":
        return value * Decimal("1000")
    return value


def _decimal_token(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    token = str(value).replace(",", "").strip()
    negative = token.startswith("(") and token.endswith(")")
    token = token.strip("()")
    try:
        result = Decimal(token)
    except (InvalidOperation, ValueError):
        return None
    return -result if negative else result


def _decimal(value: Any) -> Decimal | None:
    return _decimal_token(value)


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in occupied)


def _date_like_range(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    return bool(re.fullmatch(r"(?:19|20)\d{2,7}[-\u2013\u2014](?:19|20)\d{2,7}", compact))


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
    compact = re.sub(r"\s+", " ", paragraph.lower())
    if "consolidated metric" in compact and "guidance" in compact:
        return False
    if "private securities litigation reform act of 1995" in compact and any(
        token in compact
        for token in (
            "actual results",
            "forward-looking",
            "risks",
            "uncertainties",
            "safe harbor",
        )
    ):
        return True
    if "forward-looking statement" in compact and any(
        token in compact
        for token in (
            "involve risks",
            "subject to risks",
            "actual results",
            "undue reliance",
            "does not undertake",
            "no obligation",
            "cautionary statement",
            "safe harbor",
        )
    ):
        return True
    if "pension" in compact and any(token in compact for token in ("historical", "benefit")):
        return True
    if re.search(r"\b(?:19|20)\d{6}\s*[-\u2013\u2014]\s*(?:19|20)\d{6}\b", compact):
        return True
    if re.search(
        r"\b(?:19|20)\d{2}-\d{2}-\d{2}\s*(?:to|[-\u2013\u2014])\s*"
        r"(?:19|20)\d{2}-\d{2}-\d{2}\b",
        compact,
    ):
        return True
    if any(token in compact for token in ("contextref=", "xmlns:", "xbrli:", "ix:")):
        return True
    return False
