from datetime import date, datetime
from typing import Any


MISSING_EARNINGS_DATE_VALUES = {
    "",
    "-",
    "--",
    "\u2014",
    "\u00e2\u20ac\u201d",
    "n/a",
    "na",
    "none",
    "null",
}

EARNINGS_DATE_FORMATS = (
    "%Y-%m-%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
    "%m/%d/%Y",
    "%d.%m.%Y",
)


def parse_earnings_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if text.casefold() in MISSING_EARNINGS_DATE_VALUES:
        return None

    for date_format in EARNINGS_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue

    return None
