from typing import Any

from app.models.tables import FundamentalScore, TechnicalScore

FUNDAMENTAL_TRAP_FLAG_MAP = {
    "negative free cash flow": "negative_free_cash_flow",
    "high leverage": "high_leverage",
    "weak liquidity": "weak_liquidity",
    "extreme valuation": "extreme_valuation",
    "share dilution": "share_dilution",
}


def warning_flags_for_row(
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
) -> list[str]:
    flags: set[str] = set()

    if fundamental is None:
        flags.update({"missing_fundamental", "incomplete_data"})
    else:
        flags.update(_fundamental_warning_flags(fundamental))

    if technical is None:
        flags.update({"missing_technical", "incomplete_data"})
    else:
        flags.update(_technical_warning_flags(technical))

    if "missing_fundamental" in flags or "missing_technical" in flags:
        flags.add("incomplete_data")

    return sorted(flags)


def _fundamental_warning_flags(fundamental: FundamentalScore) -> set[str]:
    flags: set[str] = set()
    if fundamental.fundamental_label == "Value trap risk":
        flags.add("value_trap_risk")
    if fundamental.fundamental_label == "Growth trap risk":
        flags.add("growth_trap_risk")

    for trap_flag in [
        *_trap_flags(fundamental.trap_flags_json),
        *_trap_flags(fundamental.v2_warning_flags_json),
    ]:
        normalized = trap_flag.strip().lower()
        if normalized in FUNDAMENTAL_TRAP_FLAG_MAP:
            flags.add(FUNDAMENTAL_TRAP_FLAG_MAP[normalized])
        elif "_" in normalized:
            flags.add(normalized)

    return flags


def _technical_warning_flags(technical: TechnicalScore) -> set[str]:
    flags: set[str] = set()
    confidence = (technical.technical_confidence or "").lower()
    missing_data = technical.missing_data_json or {}
    flags.update(_technical_v4_warning_flags(technical.warning_flags_json))

    if confidence == "error":
        flags.add("technical_error")
        flags.add("low_technical_confidence")
    elif confidence == "low":
        flags.add("low_technical_confidence")

    if technical.insufficient_data or _truthy(missing_data.get("insufficient_history")):
        flags.add("insufficient_history")
        flags.add("low_technical_confidence")

    if _truthy(missing_data.get("missing_market_data")) or _reason_mentions(
        missing_data,
        "market",
    ):
        flags.add("missing_market_data")

    if _truthy(missing_data.get("missing_benchmark_data")) or _reason_mentions(
        missing_data,
        "benchmark",
    ):
        flags.add("missing_benchmark_data")

    debug_json = technical.debug_json or {}
    derived = debug_json.get("derived")
    if isinstance(derived, dict) and derived.get("liquidity_warning"):
        flags.add("liquidity_warning")

    return flags


def _technical_v4_warning_flags(warning_flags_json: list[str] | None) -> set[str]:
    if not isinstance(warning_flags_json, list):
        return set()
    return {str(flag) for flag in warning_flags_json if str(flag).strip()}


def _trap_flags(trap_flags_json: dict[str, Any] | None) -> list[str]:
    if not trap_flags_json:
        return []
    raw_flags = trap_flags_json.get("flags")
    if not isinstance(raw_flags, list):
        return []
    return [str(flag) for flag in raw_flags]


def _reason_mentions(missing_data: dict[str, Any], token: str) -> bool:
    reason = missing_data.get("reason")
    return isinstance(reason, str) and token in reason.lower()


def _truthy(value: Any) -> bool:
    return bool(value) if value is not None else False
