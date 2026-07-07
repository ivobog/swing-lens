from decimal import Decimal
from pathlib import Path

from app.db import Base
from app.models.tables import (
    FundamentalScore,
    IBFetchItem,
    IBFetchRun,
    RawCompanyRow,
    TechnicalScore,
)
from app.services.combined_decision import _to_model, combine_row_decision


def test_combined_result_model_includes_warning_persistence_columns() -> None:
    table = Base.metadata.tables["combined_results"]

    for column_name in [
        "warning_flags_json",
        "is_complete",
        "has_fundamental",
        "has_technical",
        "has_warning",
        "sort_bucket",
    ]:
        assert column_name in table.c


def test_earnings_risk_gate_model_includes_persistence_columns() -> None:
    raw_table = Base.metadata.tables["raw_company_rows"]
    combined_table = Base.metadata.tables["combined_results"]

    assert "upcoming_earnings_date" in raw_table.c
    for column_name in [
        "upcoming_earnings_date",
        "days_until_earnings",
        "earnings_risk_level",
        "earnings_warning_flags",
    ]:
        assert column_name in combined_table.c


def test_ib_fetch_summary_models_match_phase2_tables() -> None:
    fetch_run_columns = IBFetchRun.__table__.c
    fetch_item_columns = IBFetchItem.__table__.c

    assert "requested_tickers" in fetch_run_columns
    assert "symbols_including_benchmarks" in fetch_run_columns
    assert "include_benchmarks" in fetch_run_columns
    assert "planned_request_count" in fetch_run_columns
    assert "executed_request_count" in fetch_run_columns
    assert "skipped_count" in fetch_run_columns
    assert "success_count" in fetch_run_columns
    assert "failure_count" in fetch_run_columns
    assert "updated_count" in fetch_run_columns
    assert "revised_count" in fetch_run_columns
    assert "unchanged_count" in fetch_run_columns
    assert "fetch_run_id" in fetch_item_columns
    assert "what_to_show" in fetch_item_columns
    assert "action" in fetch_item_columns
    assert "duration" in fetch_item_columns
    assert "bar_size" in fetch_item_columns
    assert "reason" in fetch_item_columns
    assert "current_bar_count" in fetch_item_columns
    assert "updated" in fetch_item_columns
    assert "revised" in fetch_item_columns
    assert "unchanged" in fetch_item_columns
    assert "attempt_count" in fetch_item_columns
    assert "error_message" in fetch_item_columns


def test_price_bar_model_includes_revision_metadata_columns() -> None:
    table = Base.metadata.tables["price_bars"]

    for column_name in [
        "first_seen_at",
        "last_seen_at",
        "revised_at",
        "revision_count",
        "data_hash",
    ]:
        assert column_name in table.c


def test_fundamental_score_model_includes_v2_persistence_columns() -> None:
    table = Base.metadata.tables["fundamental_scores"]

    for column_name in [
        "growth_quality_score",
        "profitability_quality_score",
        "fcf_quality_score",
        "earnings_quality_score",
        "capital_efficiency_score",
        "balance_sheet_quality_score",
        "valuation_quality_score",
        "forward_quality_score",
        "shareholder_quality_score",
        "liquidity_risk_score",
        "data_coverage_score",
        "scoring_model_version",
        "v2_warning_flags_json",
    ]:
        assert column_name in table.c


def test_fundamental_score_model_accepts_v2_values() -> None:
    score = FundamentalScore(
        run_id=1,
        ticker="MSFT",
        growth_quality_score=Decimal("8.1"),
        profitability_quality_score=Decimal("8.2"),
        fcf_quality_score=Decimal("7.4"),
        earnings_quality_score=Decimal("7.8"),
        capital_efficiency_score=Decimal("8.0"),
        balance_sheet_quality_score=Decimal("7.1"),
        valuation_quality_score=Decimal("5.9"),
        forward_quality_score=Decimal("6.5"),
        shareholder_quality_score=Decimal("5.8"),
        liquidity_risk_score=Decimal("7.7"),
        data_coverage_score=Decimal("8.7"),
        scoring_model_version="fundamentals_v2.0",
        v2_warning_flags_json={"flags": ["high_accrual_risk"]},
    )

    assert score.scoring_model_version == "fundamentals_v2.0"
    assert score.earnings_quality_score == Decimal("7.8")
    assert score.v2_warning_flags_json == {"flags": ["high_accrual_risk"]}


def test_technical_score_model_includes_v4_persistence_columns() -> None:
    table = Base.metadata.tables["technical_scores"]

    for column_name in [
        "technical_engine_version",
        "data_quality_score",
        "stage",
        "market_regime",
        "leadership_score",
        "vcp_score",
        "box_tightness_score",
        "breakout_quality_score",
        "climax_risk_score",
        "atr_percentile_252",
        "volume_percentile_252",
        "range_percentile_252",
        "extension_percentile_252",
        "feature_flags_json",
        "warning_flags_json",
        "sub_tags_json",
        "v4_debug_json",
    ]:
        assert column_name in table.c


def test_technical_score_model_accepts_v4_values() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        technical_engine_version="4.0.0",
        data_quality_score=Decimal("9.5"),
        stage="Stage 2",
        market_regime="Bull trend",
        leadership_score=Decimal("8.9"),
        vcp_score=Decimal("8.1"),
        box_tightness_score=Decimal("7.5"),
        breakout_quality_score=Decimal("6.5"),
        climax_risk_score=Decimal("2.2"),
        atr_percentile_252=Decimal("41.5"),
        volume_percentile_252=Decimal("33.8"),
        range_percentile_252=Decimal("25.0"),
        extension_percentile_252=Decimal("52.0"),
        feature_flags_json=["vcp_detected"],
        warning_flags_json=[],
        sub_tags_json=["Stage 2", "VCP"],
        v4_debug_json={"final_v4_score": 8.42},
    )

    assert score.technical_engine_version == "4.0.0"
    assert score.stage == "Stage 2"
    assert score.feature_flags_json == ["vcp_detected"]
    assert score.v4_debug_json == {"final_v4_score": 8.42}


def test_fundamentals_v2_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260704_0005_add_fundamentals_v2_columns.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0005_add_fundamentals_v2_columns"' in migration
    assert 'down_revision: str | None = "0004_expand_ib_fetch_persistence"' in migration


def test_technical_v4_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260705_0006_add_technical_v4_columns.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0006_add_technical_v4_columns"' in migration
    assert 'down_revision: str | None = "0005_add_fundamentals_v2_columns"' in migration


def test_earnings_risk_gate_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260707_0010_add_earnings_risk_gate.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0010_add_earnings_risk_gate"' in migration
    assert 'down_revision: str | None = "0009_history_indexes"' in migration


def test_combined_decision_to_model_persists_phase2_fields() -> None:
    decision = combine_row_decision(
        _row("MSFT"),
        _fundamental("MSFT", "Clean compounder", "8.8"),
        _technical("MSFT", "Prime clean pullback", "8.6", risk_score="2.5"),
        config=_config(),
    )

    model = _to_model(run_id=7, final_rank=1, decision=decision)

    assert model.warning_flags_json == []
    assert model.is_complete
    assert model.has_fundamental
    assert model.has_technical
    assert not model.has_warning
    assert model.sort_bucket == 10


def test_combined_decision_to_model_persists_incomplete_warning_fields() -> None:
    decision = combine_row_decision(
        _row("MISS"),
        _fundamental("MISS", "Clean compounder", "10.0"),
        None,
        config=_config(),
    )

    model = _to_model(run_id=7, final_rank=2, decision=decision)

    assert model.final_score == Decimal("9.0")
    assert model.warning_flags_json == ["incomplete_data", "missing_technical"]
    assert not model.is_complete
    assert model.has_fundamental
    assert not model.has_technical
    assert model.has_warning
    assert model.sort_bucket == 50


def _row(ticker: str) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=1,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        raw_json={"Symbol": ticker},
    )


def _fundamental(
    ticker: str,
    label: str,
    score: str,
) -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker=ticker,
        fundamental_label=label,
        fundamental_score=Decimal(score),
    )


def _technical(
    ticker: str,
    classification: str,
    dual_score: str,
    risk_score: str,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker=ticker,
        classification=classification,
        dual_score=Decimal(dual_score),
        risk_score=Decimal(risk_score),
        debug_json={"derived": {"liquidity_warning": False}},
    )


def _config() -> dict:
    return {
        "combined_score": {
            "fundamental_score": 0.55,
            "dual_score": 0.45,
        },
        "penalties": {
            "danger_classification": 3.0,
            "overheated_momentum": 1.5,
            "value_trap_risk": 2.0,
            "growth_trap_risk": 1.5,
            "missing_data": 1.0,
            "liquidity_warning": 1.0,
        },
        "labels": {
            "strong_candidate_min_score": 8.0,
            "candidate_min_score": 6.8,
            "watch_min_score": 5.5,
        },
    }
