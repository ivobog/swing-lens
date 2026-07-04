from decimal import Decimal

from app.models.tables import FundamentalScore, TechnicalScore
from app.services.warning_flag_service import warning_flags_for_row


def test_warning_flags_include_fundamental_traps() -> None:
    fundamental = FundamentalScore(
        run_id=1,
        ticker="TRAP",
        fundamental_score=Decimal("4.0"),
        fundamental_label="Value trap risk",
        trap_flags_json={
            "flags": [
                "Negative free cash flow",
                "High leverage",
                "Weak liquidity",
            ]
        },
    )

    flags = warning_flags_for_row(fundamental, _technical())

    assert "value_trap_risk" in flags
    assert "negative_free_cash_flow" in flags
    assert "high_leverage" in flags
    assert "weak_liquidity" in flags


def test_warning_flags_include_v2_fundamental_warnings() -> None:
    fundamental = FundamentalScore(
        run_id=1,
        ticker="TRAP",
        fundamental_score=Decimal("4.0"),
        fundamental_label="Quality risk",
        v2_warning_flags_json={
            "flags": [
                "high_accrual_risk",
                "poor_cash_conversion",
            ]
        },
    )

    flags = warning_flags_for_row(fundamental, _technical())

    assert "high_accrual_risk" in flags
    assert "poor_cash_conversion" in flags


def test_warning_flags_include_missing_and_incomplete_data() -> None:
    flags = warning_flags_for_row(_fundamental(), None)

    assert "missing_technical" in flags
    assert "incomplete_data" in flags


def test_warning_flags_include_technical_confidence_and_liquidity() -> None:
    technical = _technical(
        technical_confidence="low",
        insufficient_data=True,
        missing_data_json={"insufficient_history": True},
        debug_json={"derived": {"liquidity_warning": True}},
    )

    flags = warning_flags_for_row(_fundamental(), technical)

    assert "low_technical_confidence" in flags
    assert "insufficient_history" in flags
    assert "liquidity_warning" in flags


def _fundamental() -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker="MSFT",
        fundamental_score=Decimal("8.0"),
        fundamental_label="Clean compounder",
        trap_flags_json={"flags": []},
    )


def _technical(
    technical_confidence: str = "normal",
    insufficient_data: bool = False,
    missing_data_json: dict | None = None,
    debug_json: dict | None = None,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker="MSFT",
        dual_score=Decimal("8.0"),
        classification="Prime clean pullback",
        technical_confidence=technical_confidence,
        insufficient_data=insufficient_data,
        missing_data_json=missing_data_json or {},
        debug_json=debug_json or {"derived": {"liquidity_warning": False}},
    )
