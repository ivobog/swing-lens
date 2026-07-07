from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

import pandas as pd

from app.models.tables import CombinedResult, FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.technical_display_fields import technical_v4_detail_fields

WARNING_BADGE_LABELS = {
    "incomplete_data": "Incomplete",
    "missing_fundamental": "No fundamentals",
    "missing_technical": "No technicals",
    "value_trap_risk": "Value trap",
    "growth_trap_risk": "Growth trap",
    "liquidity_warning": "Liquidity",
    "ib_fetch_failed": "IB failed",
    "insufficient_history": "Short history",
    "missing_market_data": "No market",
    "missing_benchmark_data": "No benchmark",
    "technical_error": "Technical error",
    "low_technical_confidence": "Low confidence",
    "negative_free_cash_flow": "Negative FCF",
    "high_leverage": "High leverage",
    "weak_liquidity": "Weak liquidity",
    "extreme_valuation": "Extreme valuation",
    "share_dilution": "Dilution",
    "earnings_quality_risk": "Earnings quality",
    "poor_cash_conversion": "Cash conversion",
    "high_accrual_risk": "Accrual risk",
    "capital_efficiency_deterioration": "Efficiency",
    "asset_growth_without_returns": "Asset growth",
    "balance_sheet_stress": "Balance sheet",
    "liquidity_buffer_weak": "Liquidity buffer",
    "forward_quality_weak": "Forward weak",
    "dividend_payout_risk": "Dividend payout",
    "sparse_fundamental_data": "Sparse data",
    "earnings_blocked": "Earnings block",
    "earnings_high_risk": "Earnings high",
    "earnings_medium_risk": "Earnings medium",
    "earnings_date_missing": "No earnings date",
    "earnings_date_unparseable": "Bad earnings date",
}

SEVERE_WARNING_FLAGS = {
    "value_trap_risk",
    "growth_trap_risk",
    "technical_error",
    "ib_fetch_failed",
    "negative_free_cash_flow",
    "high_leverage",
    "high_accrual_risk",
    "poor_cash_conversion",
    "balance_sheet_stress",
    "dividend_payout_risk",
    "distribution_risk",
    "blowoff_top",
    "failed_breakout",
    "earnings_blocked",
    "stage_4_downtrend",
}

MEDIUM_WARNING_FLAGS = {
    "missing_fundamental",
    "missing_technical",
    "incomplete_data",
    "earnings_quality_risk",
    "capital_efficiency_deterioration",
    "asset_growth_without_returns",
    "forward_quality_weak",
    "sparse_fundamental_data",
    "earnings_high_risk",
    "earnings_medium_risk",
    "earnings_date_missing",
    "earnings_date_unparseable",
    "low_technical_confidence",
    "insufficient_history",
    "missing_market_data",
    "missing_benchmark_data",
}


def build_score_cards(
    raw_row: RawCompanyRow | None,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    combined: CombinedResult | None,
) -> list[dict[str, Any]]:
    technical_details = technical_v4_detail_fields(technical)
    warnings = _warning_context(fundamental, technical, combined)
    return [
        _combined_card(raw_row, combined),
        _fundamentals_card(fundamental),
        _technicals_card(technical, technical_details),
        _risk_context_card(technical, combined),
        _warnings_card(warnings),
    ]


def score_tone(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "neutral"
    if number >= 7.5:
        return "good"
    if number >= 5.0:
        return "neutral"
    return "bad"


def risk_tone(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "neutral"
    if number <= 2.0:
        return "good"
    if number <= 4.0:
        return "neutral"
    return "bad"


def text_tone(label: str, value: Any) -> str:
    if value is None or value == "":
        return "neutral"
    lowered = f"{label} {value}".lower()
    if any(
        token in lowered
        for token in ["avoid", "blocked", "risk", "failed", "danger", "stage 4"]
    ):
        return "bad"
    if any(
        token in lowered
        for token in ["buyable", "strong", "clean", "prime", "clear", "candidate"]
    ):
        return "good"
    return "neutral"


def warning_badges(flags: Iterable[str]) -> list[dict[str, str]]:
    unique_flags = _unique_text(flags)
    if not unique_flags:
        return [{"flag": "clear", "label": "Clear", "tone": "success"}]
    return [
        {
            "flag": flag,
            "label": WARNING_BADGE_LABELS.get(flag, flag.replace("_", " ").title()),
            "tone": warning_tone(flag),
        }
        for flag in unique_flags
    ]


def warning_tone(flag: str) -> str:
    if flag in SEVERE_WARNING_FLAGS:
        return "danger"
    if flag in MEDIUM_WARNING_FLAGS:
        return "warning"
    return "muted"


def _combined_card(
    raw_row: RawCompanyRow | None,
    combined: CombinedResult | None,
) -> dict[str, Any]:
    return {
        "title": "Combined Decision",
        "items": [
            _item("Ticker", _first_present(_value(combined, "ticker"), _value(raw_row, "ticker"))),
            _item(
                "Company",
                _first_present(
                    _value(combined, "company_name"),
                    _value(raw_row, "company_name"),
                ),
            ),
            _item("Sector", _first_present(_value(combined, "sector"), _value(raw_row, "sector"))),
            _item("Final Rank", _value(combined, "final_rank")),
            _score_item("Final Score", _value(combined, "final_score")),
            _text_item("Decision", _value(combined, "combined_decision")),
            _item("Position Size", _value(combined, "position_size_hint")),
            _text_item("Fundamental Label", _value(combined, "fundamental_label")),
            _text_item("Technical Class", _value(combined, "technical_classification")),
            _boolean_item("Complete", _value(combined, "is_complete")),
        ],
    }


def _fundamentals_card(fundamental: FundamentalScore | None) -> dict[str, Any]:
    debug = _dict(_value(fundamental, "debug_json"))
    coverage = _dict(debug.get("coverage"))
    missing_core = coverage.get("missing_core_fields", debug.get("missing_fields", []))
    missing_high = coverage.get("missing_high_fields", [])
    warnings = _fundamental_warning_flags(fundamental)
    return {
        "title": "Fundamentals",
        "items": [
            _item(
                "Model",
                _first_present(
                    _value(fundamental, "scoring_model_version"),
                    debug.get("model_version"),
                ),
            ),
            _score_item("Score", _value(fundamental, "fundamental_score")),
            _score_item("Coverage", _value(fundamental, "data_coverage_score")),
            _score_item("Growth", _value(fundamental, "growth_quality_score")),
            _score_item("Profitability", _value(fundamental, "profitability_quality_score")),
            _score_item("FCF", _value(fundamental, "fcf_quality_score")),
            _score_item("Earnings", _value(fundamental, "earnings_quality_score")),
            _score_item("Capital", _value(fundamental, "capital_efficiency_score")),
            _score_item("Balance", _value(fundamental, "balance_sheet_quality_score")),
            _score_item("Valuation", _value(fundamental, "valuation_quality_score")),
            _score_item("Forward", _value(fundamental, "forward_quality_score")),
            _score_item("Shareholder", _value(fundamental, "shareholder_quality_score")),
            _risk_item("Liquidity Risk", _value(fundamental, "liquidity_risk_score")),
            _risk_item("Missing Penalty", _value(fundamental, "missing_data_penalty")),
            _list_item("Missing Core", missing_core, bad_when_present=True),
            _list_item("Missing High", missing_high, bad_when_present=True),
            _list_item("Warnings", warnings, bad_when_present=True),
        ],
    }


def _technicals_card(
    technical: TechnicalScore | None,
    details: dict[str, Any],
) -> dict[str, Any]:
    warnings = _list_from_any(_value(technical, "warning_flags_json")) or _split_text_list(
        details.get("warning_flags")
    )
    return {
        "title": "Technicals",
        "items": [
            _item("Model", details.get("technical_version")),
            _score_item("Score", _value(technical, "dual_score")),
            _score_item("Trend", _value(technical, "trend_score")),
            _score_item("Momentum", _value(technical, "momentum_score")),
            _score_item("Setup", _value(technical, "setup_score")),
            _risk_item("Risk", _value(technical, "risk_score")),
            _score_item("Market", _value(technical, "market_score")),
            _score_item("RS", _value(technical, "combined_relative_strength_score")),
            _score_item("HTF", _value(technical, "htf_score")),
            _text_item("Confidence", _value(technical, "technical_confidence")),
            _text_item("Stage", details.get("stage")),
            _text_item("Regime", details.get("market_regime")),
            _score_item("Leadership", details.get("leadership_score")),
            _score_item("VCP", details.get("vcp_score")),
            _score_item("Box Tightness", details.get("box_tightness_score")),
            _score_item("Breakout Quality", details.get("breakout_quality_score")),
            _risk_item("Climax Risk", details.get("climax_risk_score")),
            _item("Tags", details.get("sub_tags")),
            _list_item("Warnings", warnings, bad_when_present=True),
            _text_item("Action", _value(technical, "action_bias")),
        ],
    }


def _risk_context_card(
    technical: TechnicalScore | None,
    combined: CombinedResult | None,
) -> dict[str, Any]:
    earnings_risk = _value(combined, "earnings_risk_level")
    return {
        "title": "Risk Context",
        "items": [
            _item("Stop", _value(technical, "suggested_stop")),
            _item("Target", _value(technical, "suggested_target")),
            _score_item("Reward/Risk", _value(technical, "reward_risk")),
            _risk_item("Entry Risk", _value(technical, "entry_risk_pct")),
            _text_item("Earnings Risk", earnings_risk),
            _item("Earnings Date", _value(combined, "upcoming_earnings_date")),
            _item("Days Until Earnings", _value(combined, "days_until_earnings")),
            _text_item("Notes", _value(combined, "notes")),
        ],
    }


def _warnings_card(warnings: dict[str, list[str]]) -> dict[str, Any]:
    all_flags = warnings["all"]
    return {
        "title": "Warnings and Missing Data",
        "items": [
            _item(
                "Overall",
                "Clear" if not all_flags else f"{len(all_flags)} warning(s)",
                "good" if not all_flags else "bad",
            ),
            _list_item("Combined", warnings["combined"], bad_when_present=True),
            _list_item("Fundamental", warnings["fundamental"], bad_when_present=True),
            _list_item("Technical", warnings["technical"], bad_when_present=True),
            _list_item("Earnings", warnings["earnings"], bad_when_present=True),
        ],
        "badges": warning_badges(all_flags),
    }


def _warning_context(
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    combined: CombinedResult | None,
) -> dict[str, list[str]]:
    combined_flags = _list_from_any(_value(combined, "warning_flags_json"))
    fundamental_flags = _fundamental_warning_flags(fundamental)
    technical_flags = _list_from_any(_value(technical, "warning_flags_json"))
    earnings_flags = _list_from_any(_value(combined, "earnings_warning_flags_json"))
    return {
        "combined": combined_flags,
        "fundamental": fundamental_flags,
        "technical": technical_flags,
        "earnings": earnings_flags,
        "all": _unique_text(
            [*combined_flags, *fundamental_flags, *technical_flags, *earnings_flags]
        ),
    }


def _fundamental_warning_flags(fundamental: FundamentalScore | None) -> list[str]:
    v2_flags = _list_from_any(_dict(_value(fundamental, "v2_warning_flags_json")).get("flags"))
    trap_flags = _list_from_any(_dict(_value(fundamental, "trap_flags_json")).get("flags"))
    return _unique_text([*v2_flags, *trap_flags])


def _item(label: str, value: Any, tone: str = "neutral") -> dict[str, Any]:
    return {
        "label": label,
        "value": _format_value(value),
        "raw_value": _clean_value(value),
        "tone": tone,
    }


def _score_item(label: str, value: Any) -> dict[str, Any]:
    return _item(label, value, score_tone(value))


def _risk_item(label: str, value: Any) -> dict[str, Any]:
    return _item(label, value, risk_tone(value))


def _text_item(label: str, value: Any) -> dict[str, Any]:
    return _item(label, value, text_tone(label, value))


def _boolean_item(label: str, value: Any) -> dict[str, Any]:
    if value is None:
        return _item(label, None)
    return _item(label, "Yes" if bool(value) else "No", "good" if bool(value) else "bad")


def _list_item(
    label: str,
    values: Any,
    *,
    bad_when_present: bool = False,
) -> dict[str, Any]:
    items = _list_from_any(values)
    tone = "bad" if bad_when_present and items else "neutral"
    return _item(label, ", ".join(items) if items else "None", tone)


def _value(obj: Any, attribute: str) -> Any:
    if obj is None:
        return None
    return getattr(obj, attribute, None)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _format_value(value: Any) -> str:
    cleaned = _clean_value(value)
    if cleaned is None or cleaned == "":
        return "N/A"
    if isinstance(cleaned, bool):
        return "Yes" if cleaned else "No"
    if isinstance(cleaned, int):
        return str(cleaned)
    if isinstance(cleaned, float):
        return f"{cleaned:.2f}"
    if hasattr(cleaned, "isoformat"):
        return cleaned.isoformat()
    return str(cleaned)


def _clean_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if value is None or (not isinstance(value, (dict, list)) and pd.isna(value)):
        return None
    return value


def _number(value: Any) -> float | None:
    cleaned = _clean_value(value)
    if cleaned is None or isinstance(cleaned, bool):
        return None
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _list_from_any(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_text_list(value)
    if isinstance(value, Mapping):
        return _list_from_any(value.get("flags"))
    if isinstance(value, Iterable):
        return [str(item) for item in value if item is not None and str(item)]
    return [str(value)]


def _split_text_list(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _unique_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique
