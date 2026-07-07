from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_EARNINGS_RISK_GATE_CONFIG = {
    "enabled": True,
    "block_if_within_days": 2,
    "high_risk_if_within_days": 5,
    "medium_risk_if_within_days": 10,
    "missing_date_policy": "warn",
    "apply_to_combined_score": True,
    "block_new_entries": True,
    "penalties": {
        "blocked": 3.0,
        "high": 2.0,
        "medium": 1.0,
        "unknown": 0.3,
        "clear": 0.0,
    },
}


@dataclass(frozen=True)
class EarningsRiskResult:
    upcoming_earnings_date: date | None
    days_until_earnings: int | None
    risk_level: str
    penalty: float
    warning_flags: tuple[str, ...]
    decision_blocked: bool
    message: str


def calculate_earnings_risk(
    *,
    upcoming_earnings_date: date | None,
    raw_value_present: bool,
    today: date,
    config: dict[str, Any] | None,
) -> EarningsRiskResult:
    gate = _merged_config(config)
    if not bool(gate["enabled"]):
        return _result(
            upcoming_earnings_date=upcoming_earnings_date,
            days_until_earnings=(
                (upcoming_earnings_date - today).days
                if upcoming_earnings_date is not None
                else None
            ),
            risk_level="clear",
            message="earnings gate disabled",
        )

    if upcoming_earnings_date is None:
        return _unknown_result(raw_value_present=raw_value_present, gate=gate)

    days = (upcoming_earnings_date - today).days
    if days < 0:
        return _result(
            upcoming_earnings_date=upcoming_earnings_date,
            days_until_earnings=days,
            risk_level="clear",
            message="earnings already passed",
        )

    if days <= int(gate["block_if_within_days"]):
        return _result(
            upcoming_earnings_date=upcoming_earnings_date,
            days_until_earnings=days,
            risk_level="blocked",
            warning_flags=("earnings_blocked",),
            decision_blocked=True,
            penalty=_penalty(gate, "blocked"),
            message="earnings within blocked window",
        )

    if days <= int(gate["high_risk_if_within_days"]):
        return _result(
            upcoming_earnings_date=upcoming_earnings_date,
            days_until_earnings=days,
            risk_level="high",
            warning_flags=("earnings_high_risk",),
            penalty=_penalty(gate, "high"),
            message="earnings within high-risk window",
        )

    if days <= int(gate["medium_risk_if_within_days"]):
        return _result(
            upcoming_earnings_date=upcoming_earnings_date,
            days_until_earnings=days,
            risk_level="medium",
            warning_flags=("earnings_medium_risk",),
            penalty=_penalty(gate, "medium"),
            message="earnings within medium-risk window",
        )

    return _result(
        upcoming_earnings_date=upcoming_earnings_date,
        days_until_earnings=days,
        risk_level="clear",
        message="no near earnings risk",
    )


def current_local_date(timezone_name: str = "Europe/Zurich") -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


def _unknown_result(
    *,
    raw_value_present: bool,
    gate: dict[str, Any],
) -> EarningsRiskResult:
    if gate["missing_date_policy"] != "warn":
        return _result(
            upcoming_earnings_date=None,
            days_until_earnings=None,
            risk_level="unknown",
            message="earnings date unavailable",
        )

    flag = "earnings_date_unparseable" if raw_value_present else "earnings_date_missing"
    message = "earnings date unparseable" if raw_value_present else "earnings date missing"
    return _result(
        upcoming_earnings_date=None,
        days_until_earnings=None,
        risk_level="unknown",
        warning_flags=(flag,),
        penalty=_penalty(gate, "unknown"),
        message=message,
    )


def _result(
    *,
    upcoming_earnings_date: date | None,
    days_until_earnings: int | None,
    risk_level: str,
    warning_flags: tuple[str, ...] = (),
    decision_blocked: bool = False,
    penalty: float = 0.0,
    message: str,
) -> EarningsRiskResult:
    return EarningsRiskResult(
        upcoming_earnings_date=upcoming_earnings_date,
        days_until_earnings=days_until_earnings,
        risk_level=risk_level,
        penalty=penalty,
        warning_flags=warning_flags,
        decision_blocked=decision_blocked,
        message=message,
    )


def _merged_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_EARNINGS_RISK_GATE_CONFIG)
    merged["penalties"] = dict(DEFAULT_EARNINGS_RISK_GATE_CONFIG["penalties"])
    if not config:
        return merged
    for key, value in config.items():
        if key == "penalties" and isinstance(value, dict):
            merged["penalties"].update(value)
        else:
            merged[key] = value
    return merged


def _penalty(gate: dict[str, Any], risk_level: str) -> float:
    if not bool(gate["apply_to_combined_score"]):
        return 0.0
    return float(gate["penalties"].get(risk_level, 0.0))
