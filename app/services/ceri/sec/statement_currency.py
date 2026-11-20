"""Conservative presentation-currency extraction for the reviewed SEC source policy."""

from __future__ import annotations

import html
import re
from datetime import date

# Explicitly supported ISO 4217 monetary units. Expand only with a reviewed filing.
CURRENCIES = frozenset({"BRL", "CAD", "CNY", "EUR", "GBP", "JPY", "PEN", "USD"})
_INCOME_HEADING = re.compile(
    r"(?:consolidated\s+(?:interim\s+)?statements?\s+of\s+(?:operations|income|earnings|loss|profit)"
    r"|consolidated\s+(?:interim\s+)?income\s+statements?)",
    re.I,
)
_REVENUE = re.compile(r"revenue|sales|premiums\s+earned", re.I)
_INCOME = re.compile(r"(?:net|operating)\s+(?:income|earnings|loss|profit)", re.I)
_TAG = re.compile(r"<[^>]*>")
_ATTRIBUTES = re.compile(r"""([\w:-]+)\s*=\s*["']([^"']*)["']""")


def _element(name: str) -> re.Pattern[str]:
    return re.compile(rf"<(?:[\w-]+:)?{name}\b([^>]*)>(.*?)</(?:[\w-]+:)?{name}\s*>", re.I | re.S)


def _attributes(text: str) -> dict[str, str]:
    return {key.lower(): value for key, value in _ATTRIBUTES.findall(text)}


def _token(name: str, body: str) -> str:
    result = _element(name).search(body)
    return result.group(2).strip() if result else ""


def _text(body: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", body)).split())


def _contexts(body: str, cik: str, fiscal_end: date, annual: bool) -> set[str]:
    matching = set()
    for attributes, context in _element("context").findall(body):
        if re.search(
            r"<(?:[\w-]+:)?(?:segment|scenario|explicitMember|typedMember)\b", context, re.I
        ):
            continue
        if _token("identifier", context).zfill(10) != cik:
            continue
        if _token("endDate", context) != fiscal_end.isoformat():
            continue
        try:
            days = (fiscal_end - date.fromisoformat(_token("startDate", context))).days
        except ValueError:
            continue
        if (annual and 320 <= days <= 400) or (not annual and 65 <= days <= 120):
            matching.add(_attributes(attributes).get("id", ""))
    return matching - {""}


def extract_presentation_currency(
    body: str, *, cik: str, fiscal_end: date, annual: bool, reviewed_interim: bool
) -> str | None:
    """Read only statement-table facts tied to the consolidated entity and period.

    The reviewed interim profile also accepts explicitly euro-labelled, dated
    financial tables in a 6-K. A dollar sign alone never establishes an ISO unit.
    """
    matching = _contexts(body, cik, fiscal_end, annual)
    units = {}
    for attributes, unit in _element("unit").findall(body):
        code = _token("measure", unit)
        if code.startswith("iso4217:") and "divide" not in unit.lower():
            units[_attributes(attributes).get("id", "")] = code.removeprefix("iso4217:")

    currency_candidates: set[str] = set()
    for table in _element("table").finditer(body):
        contents = _text(table.group(2))
        before = _text(body[max(0, table.start() - 9000) : table.start()])[-650:]
        if not _INCOME_HEADING.search(before + " " + contents[:320]):
            continue
        if not _REVENUE.search(contents) or not _INCOME.search(contents):
            continue
        table_units = set()
        for fact in re.finditer(r"<ix:nonfraction\b([^>]*)>", table.group(2), re.I):
            attrs = _attributes(fact.group(1))
            code = units.get(attrs.get("unitref", ""))
            if code and attrs.get("contextref") in matching:
                table_units.add(code)

        if reviewed_interim:
            # Date and currency must be explicit in the selected interim statement.
            fiscal_label = f"{fiscal_end.strftime('%B')} {fiscal_end.day}, {fiscal_end.year}"
            labels = before + " " + contents[:450]
            if fiscal_label.casefold() not in labels.casefold():
                continue
            if not re.search(
                r"\bin\s+(?:€\s*|EUR\s+|euros?\s+)(?:millions|thousands)\b", labels, re.I
            ):
                continue
            table_units.add("EUR")
            # Convenience columns are ambiguous unless they are separately reviewed.
            if re.search(r"\bUSD\b|\bUS\$", contents, re.I):
                return None

        if table_units:
            currency_candidates.update(table_units)
    if len(currency_candidates) != 1 or not currency_candidates <= CURRENCIES:
        return None
    return next(iter(currency_candidates))
