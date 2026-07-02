from app.services.column_mapper import MappedCsvRow
from app.services.fundamental_ranker import score_row, score_rows


def test_scores_clean_compounder() -> None:
    row = MappedCsvRow(
        row_number=1,
        ticker="GROW",
        company_name="Growth Co",
        sector="Technology",
        canonical={
            "revenue_growth_ttm_yoy": "24",
            "eps_growth_ttm_yoy": "22",
            "revenue_growth_5y_cagr": "18",
            "gross_margin_ttm": "68",
            "operating_margin_ttm": "32",
            "net_margin_ttm": "24",
            "roe_ttm": "31",
            "roic_ttm": "26",
            "fcf_ttm": "1000000000",
            "fcf_margin_ttm": "25",
            "fcf_growth_ttm_yoy": "20",
            "net_debt_to_ebitda": "0.2",
            "current_ratio": "2.1",
            "pe_ratio": "24",
            "forward_pe": "20",
            "ps_ratio": "6",
            "ev_ebitda": "16",
            "buyback_yield": "1.5",
            "performance_1y_pct": "35",
            "dollar_volume_30d": "50000000",
            "market_cap": "100000000000",
        },
        raw={},
    )

    score = score_row(row)

    assert score.fundamental_label == "Clean compounder"
    assert score.fundamental_score >= 7.6
    assert score.trap_flags == []


def test_flags_value_trap_risk() -> None:
    row = MappedCsvRow(
        row_number=1,
        ticker="TRAP",
        company_name="Trap Co",
        sector="Industrial",
        canonical={
            "revenue_growth_ttm_yoy": "-12",
            "eps_growth_ttm_yoy": "-20",
            "gross_margin_ttm": "12",
            "operating_margin_ttm": "-3",
            "fcf_ttm": "-10000000",
            "fcf_margin_ttm": "-5",
            "net_debt_to_ebitda": "5.5",
            "current_ratio": "0.7",
            "pe_ratio": "8",
            "market_cap": "1000000000",
        },
        raw={},
    )

    score = score_row(row)

    assert score.fundamental_label == "Value trap risk"
    assert "Negative free cash flow" in score.trap_flags
    assert "High leverage" in score.trap_flags
    assert "Weak liquidity" in score.trap_flags


def test_score_rows_is_deterministic() -> None:
    row = MappedCsvRow(
        row_number=1,
        ticker="SAME",
        company_name=None,
        sector=None,
        canonical={"market_cap": "1", "fcf_ttm": "1"},
        raw={},
    )

    first = score_rows([row])[0]
    second = score_rows([row])[0]

    assert first == second
