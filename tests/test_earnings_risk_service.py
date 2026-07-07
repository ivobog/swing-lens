from datetime import date

from app.services.earnings_risk_service import (
    DEFAULT_EARNINGS_RISK_GATE_CONFIG,
    calculate_earnings_risk,
)


TODAY = date(2026, 7, 7)


def test_earnings_today_is_blocked() -> None:
    result = _risk(date(2026, 7, 7))

    assert result.days_until_earnings == 0
    assert result.risk_level == "blocked"
    assert result.penalty == 3.0
    assert result.warning_flags == ("earnings_blocked",)
    assert result.decision_blocked is True


def test_earnings_tomorrow_is_blocked() -> None:
    result = _risk(date(2026, 7, 8))

    assert result.days_until_earnings == 1
    assert result.risk_level == "blocked"
    assert result.warning_flags == ("earnings_blocked",)


def test_earnings_in_four_days_is_high_risk() -> None:
    result = _risk(date(2026, 7, 11))

    assert result.days_until_earnings == 4
    assert result.risk_level == "high"
    assert result.penalty == 2.0
    assert result.warning_flags == ("earnings_high_risk",)
    assert result.decision_blocked is False


def test_earnings_in_eight_days_is_medium_risk() -> None:
    result = _risk(date(2026, 7, 15))

    assert result.days_until_earnings == 8
    assert result.risk_level == "medium"
    assert result.penalty == 1.0
    assert result.warning_flags == ("earnings_medium_risk",)


def test_earnings_after_medium_window_is_clear() -> None:
    result = _risk(date(2026, 7, 18))

    assert result.days_until_earnings == 11
    assert result.risk_level == "clear"
    assert result.penalty == 0.0
    assert result.warning_flags == ()


def test_past_earnings_date_is_clear() -> None:
    result = _risk(date(2026, 7, 1))

    assert result.days_until_earnings == -6
    assert result.risk_level == "clear"
    assert result.message == "earnings already passed"


def test_missing_earnings_date_is_unknown_with_missing_warning() -> None:
    result = _risk(None, raw_value_present=False)

    assert result.days_until_earnings is None
    assert result.risk_level == "unknown"
    assert result.penalty == 0.3
    assert result.warning_flags == ("earnings_date_missing",)
    assert result.message == "earnings date missing"


def test_unparseable_earnings_date_is_unknown_with_unparseable_warning() -> None:
    result = _risk(None, raw_value_present=True)

    assert result.risk_level == "unknown"
    assert result.penalty == 0.3
    assert result.warning_flags == ("earnings_date_unparseable",)
    assert result.message == "earnings date unparseable"


def test_disabled_gate_returns_clear_without_penalty() -> None:
    config = _config()
    config["enabled"] = False

    result = _risk(date(2026, 7, 8), config=config)

    assert result.days_until_earnings == 1
    assert result.risk_level == "clear"
    assert result.penalty == 0.0
    assert result.warning_flags == ()
    assert result.decision_blocked is False
    assert result.message == "earnings gate disabled"


def test_apply_to_combined_score_false_keeps_warning_but_removes_penalty() -> None:
    config = _config()
    config["apply_to_combined_score"] = False

    result = _risk(date(2026, 7, 11), config=config)

    assert result.risk_level == "high"
    assert result.penalty == 0.0
    assert result.warning_flags == ("earnings_high_risk",)


def test_missing_date_policy_can_suppress_unknown_warning_penalty() -> None:
    config = _config()
    config["missing_date_policy"] = "ignore"

    result = _risk(None, raw_value_present=False, config=config)

    assert result.risk_level == "unknown"
    assert result.penalty == 0.0
    assert result.warning_flags == ()


def test_custom_thresholds_and_penalties_are_respected() -> None:
    config = _config()
    config["high_risk_if_within_days"] = 7
    config["penalties"]["high"] = 1.25

    result = _risk(date(2026, 7, 13), config=config)

    assert result.days_until_earnings == 6
    assert result.risk_level == "high"
    assert result.penalty == 1.25


def _risk(
    upcoming_earnings_date: date | None,
    *,
    raw_value_present: bool = True,
    config: dict | None = None,
):
    return calculate_earnings_risk(
        upcoming_earnings_date=upcoming_earnings_date,
        raw_value_present=raw_value_present,
        today=TODAY,
        config=config or _config(),
    )


def _config() -> dict:
    config = dict(DEFAULT_EARNINGS_RISK_GATE_CONFIG)
    config["penalties"] = dict(DEFAULT_EARNINGS_RISK_GATE_CONFIG["penalties"])
    return config
