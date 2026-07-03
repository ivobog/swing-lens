import math
import re
from dataclasses import dataclass
from decimal import Decimal
from numbers import Real

MISSING_TOKENS = {"", "-", "--", "N/A", "NA", "NONE", "NAN", "NULL"}
SUFFIX_MULTIPLIERS = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "T": 1_000_000_000_000,
}
CURRENCY_SYMBOLS = "$€£¥₹₩₽₺₪₫₴₦₱฿"
CURRENCY_CODES = {
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "EUR",
    "GBP",
    "HKD",
    "INR",
    "JPY",
    "KRW",
    "MXN",
    "NOK",
    "SEK",
    "SGD",
    "USD",
}
NUMBER_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


@dataclass(frozen=True)
class NumericParseResult:
    value: float | None
    raw: object
    normalized: str | None
    parsed: bool
    reason: str | None


def parse_financial_number(value: object) -> NumericParseResult:
    if value is None:
        return NumericParseResult(None, value, None, False, "missing")

    if isinstance(value, bool):
        return NumericParseResult(None, value, str(value), False, "boolean is not numeric")

    if isinstance(value, Decimal):
        return _result_from_real(float(value), value)

    if isinstance(value, Real):
        return _result_from_real(float(value), value)

    text = str(value).strip()
    if text.upper() in MISSING_TOKENS:
        return NumericParseResult(None, value, text, False, "missing")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    normalized = _remove_currency(text)
    normalized = normalized.replace("%", "")
    normalized = normalized.replace(",", "")
    normalized = re.sub(r"\s+", "", normalized).strip()

    suffix = ""
    if normalized and normalized[-1:].upper() in SUFFIX_MULTIPLIERS:
        suffix = normalized[-1:].upper()
        normalized = normalized[:-1]

    if normalized.upper() in MISSING_TOKENS:
        return NumericParseResult(None, value, normalized, False, "missing")

    if not NUMBER_PATTERN.match(normalized):
        return NumericParseResult(None, value, normalized or None, False, "invalid numeric format")

    parsed_value = float(normalized)
    if negative:
        parsed_value = -parsed_value
    if suffix:
        parsed_value *= SUFFIX_MULTIPLIERS[suffix]

    return NumericParseResult(parsed_value, value, normalized, True, None)


def _result_from_real(value: float, raw: object) -> NumericParseResult:
    if math.isnan(value):
        return NumericParseResult(None, raw, str(raw), False, "missing")
    if math.isinf(value):
        return NumericParseResult(None, raw, str(raw), False, "infinite value")
    return NumericParseResult(value, raw, str(raw), True, None)


def _remove_currency(text: str) -> str:
    without_symbols = text.translate(str.maketrans("", "", CURRENCY_SYMBOLS))
    parts = without_symbols.split()
    if parts and parts[0].upper().rstrip(".") in CURRENCY_CODES:
        parts = parts[1:]
    if parts and parts[-1].upper().rstrip(".") in CURRENCY_CODES:
        parts = parts[:-1]
    return " ".join(parts)
