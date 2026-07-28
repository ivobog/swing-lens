from datetime import date
from decimal import Decimal
from pathlib import Path

from app.db import Base
from app.models.tables import (
    FundamentalScore,
    IBFetchItem,
    IBFetchRun,
    MarketRegimeSnapshot,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    UploadRun,
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


def test_ranking_result_model_includes_profile_persistence_columns() -> None:
    table = Base.metadata.tables["ranking_results"]

    for column_name in [
        "run_id",
        "raw_row_id",
        "ticker",
        "company_name",
        "sector",
        "ranking_profile",
        "ranking_label",
        "profile_rank",
        "profile_score",
        "technical_profile_score",
        "fundamental_score",
        "base_technical_score",
        "technical_classification",
        "fundamental_label",
        "decision_label",
        "position_size_hint",
        "notes",
        "warning_flags_json",
        "penalties_json",
        "gates_json",
        "component_scores_json",
        "debug_json",
        "upcoming_earnings_date",
        "days_until_earnings",
        "earnings_risk_level",
        "is_complete",
        "has_warning",
        "has_fundamental",
        "has_technical",
        "sort_bucket",
        "created_at",
        "updated_at",
    ]:
        assert column_name in table.c


def test_ranking_result_model_defines_constraints_and_indexes() -> None:
    table = RankingResult.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}

    assert "uq_ranking_results_run_profile_ticker" in constraint_names
    assert {
        "idx_ranking_results_run_id",
        "idx_ranking_results_ticker",
        "idx_ranking_results_profile",
        "idx_ranking_results_run_profile_rank",
        "idx_ranking_results_run_profile_score",
        "idx_ranking_results_earnings_risk",
    }.issubset(index_names)


def test_upload_run_has_ranking_results_relationship() -> None:
    assert "ranking_results" in UploadRun.__mapper__.relationships
    assert UploadRun.__mapper__.relationships["ranking_results"].cascade.delete


def test_market_regime_snapshot_model_includes_persistence_columns() -> None:
    table = Base.metadata.tables["market_regime_snapshots"]

    for column_name in [
        "run_id",
        "as_of_date",
        "calculation_version",
        "config_version",
        "regime",
        "risk_state",
        "score",
        "risk_off",
        "gate_ok",
        "confidence",
        "action_summary",
        "position_size_multiplier",
        "preferred_profiles_json",
        "allowed_profiles_json",
        "reduced_profiles_json",
        "blocked_profiles_json",
        "allowed_setups_json",
        "blocked_setups_json",
        "input_symbols_json",
        "index_health_json",
        "universe_participation_json",
        "sector_leadership_json",
        "reasons_json",
        "warnings_json",
        "debug_json",
        "created_at",
        "updated_at",
    ]:
        assert column_name in table.c


def test_market_regime_snapshot_model_defines_constraints_and_indexes() -> None:
    table = MarketRegimeSnapshot.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}

    assert "uq_market_regime_snapshots_run_date_version" in constraint_names
    assert {
        "idx_market_regime_snapshots_as_of_date",
        "idx_market_regime_snapshots_run_id",
        "idx_market_regime_snapshots_regime",
        "idx_market_regime_snapshots_risk_state",
    }.issubset(index_names)


def test_upload_run_has_market_regime_snapshots_relationship() -> None:
    assert "market_regime_snapshots" in UploadRun.__mapper__.relationships


def test_sector_rotation_snapshot_model_includes_persistence_columns() -> None:
    table = Base.metadata.tables["sector_rotation_snapshots"]

    for column_name in [
        "run_id",
        "market_regime_snapshot_id",
        "as_of_date",
        "calculation_version",
        "config_version",
        "config_hash",
        "mode",
        "default_ranking_profile",
        "benchmark_ticker",
        "sector_count",
        "ticker_count",
        "leading_sector",
        "weakest_sector",
        "riskiest_sector",
        "summary_json",
        "warning_flags_json",
        "debug_json",
        "created_at",
        "updated_at",
    ]:
        assert column_name in table.c


def test_sector_rotation_row_model_includes_persistence_columns() -> None:
    table = Base.metadata.tables["sector_rotation_rows"]

    for column_name in [
        "snapshot_id",
        "sector",
        "sector_slug",
        "sector_proxy_ticker",
        "ticker_count",
        "universe_share",
        "average_fundamental_score",
        "average_technical_score",
        "average_final_score",
        "average_profile_score",
        "top_10_count",
        "top_25_count",
        "top_50_count",
        "top_25_share",
        "buyable_count",
        "watch_count",
        "danger_count",
        "buyable_share",
        "watch_share",
        "danger_share",
        "clean_pullback_count",
        "breakout_count",
        "vcp_count",
        "tight_base_breakout_count",
        "extended_or_overheated_count",
        "missing_fundamental_count",
        "missing_technical_count",
        "universe_leadership_score",
        "etf_rotation_score",
        "sector_final_score",
        "rotation_state",
        "sector_permission",
        "position_size_multiplier",
        "confidence",
        "previous_rank",
        "current_rank",
        "rank_change",
        "score_change",
        "profile_distribution_json",
        "setup_distribution_json",
        "warning_distribution_json",
        "etf_metrics_json",
        "component_scores_json",
        "reason_codes_json",
        "warning_flags_json",
        "debug_json",
    ]:
        assert column_name in table.c


def test_sector_rotation_models_define_constraints_and_indexes() -> None:
    snapshot_constraints = {
        constraint.name for constraint in SectorRotationSnapshot.__table__.constraints
    }
    snapshot_indexes = {index.name for index in SectorRotationSnapshot.__table__.indexes}
    row_constraints = {constraint.name for constraint in SectorRotationRow.__table__.constraints}
    row_indexes = {index.name for index in SectorRotationRow.__table__.indexes}

    assert "uq_sector_rotation_snapshots_run_date_version_mode" in snapshot_constraints
    assert {
        "idx_sector_rotation_snapshot_run_date",
        "idx_sector_rotation_snapshot_date",
    }.issubset(snapshot_indexes)
    assert "uq_sector_rotation_rows_snapshot_sector_slug" in row_constraints
    assert {
        "idx_sector_rotation_rows_snapshot_rank",
        "idx_sector_rotation_rows_sector_slug",
    }.issubset(row_indexes)


def test_upload_run_has_sector_rotation_snapshots_relationship() -> None:
    relationship = UploadRun.__mapper__.relationships["sector_rotation_snapshots"]

    assert relationship.cascade.delete
    assert relationship.cascade.delete_orphan


def test_sector_rotation_models_accept_values() -> None:
    snapshot = SectorRotationSnapshot(
        run_id=7,
        market_regime_snapshot_id=3,
        as_of_date=date(2026, 7, 28),
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="abc123",
        mode="universe_only",
        default_ranking_profile="momentum_swing",
        benchmark_ticker="SPY",
        sector_count=2,
        ticker_count=42,
        leading_sector="Technology",
        weakest_sector="Utilities",
        riskiest_sector="Energy",
        summary_json={"leading_sector": "Technology"},
        warning_flags_json=["missing_etf_confirmation"],
        debug_json={"source": "unit"},
    )
    row = SectorRotationRow(
        snapshot_id=1,
        sector="Technology",
        sector_slug="technology",
        sector_proxy_ticker="XLK",
        ticker_count=14,
        universe_share=0.3333,
        average_fundamental_score=7.1,
        average_technical_score=8.2,
        average_final_score=7.8,
        average_profile_score=7.5,
        top_10_count=3,
        top_25_count=6,
        top_50_count=10,
        top_25_share=0.4286,
        buyable_count=4,
        watch_count=2,
        danger_count=1,
        buyable_share=0.2857,
        watch_share=0.1429,
        danger_share=0.0714,
        universe_leadership_score=8.1,
        sector_final_score=8.1,
        rotation_state="Leading",
        sector_permission="full_allowed",
        position_size_multiplier=1.0,
        confidence="high",
        current_rank=1,
        profile_distribution_json={"momentum_swing": {"top_25_count": 6}},
        setup_distribution_json={"Fresh breakout": 2},
        warning_distribution_json={"liquidity_warning": 1},
        component_scores_json={"risk_control": 9.0},
        reason_codes_json=["top_candidate_overrepresentation"],
        warning_flags_json=[],
        debug_json={"source": "unit"},
    )

    assert snapshot.mode == "universe_only"
    assert snapshot.leading_sector == "Technology"
    assert row.sector == "Technology"
    assert row.profile_distribution_json["momentum_swing"]["top_25_count"] == 6


def test_market_regime_snapshot_model_accepts_values() -> None:
    snapshot = MarketRegimeSnapshot(
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        regime="Bull pullback",
        risk_state="Yellow",
        score=6.8,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Prefer quality pullbacks.",
        position_size_multiplier=0.75,
        preferred_profiles_json=["quality_momentum"],
        allowed_profiles_json=["quality_momentum", "defensive_quality"],
        reduced_profiles_json=["early_rocket"],
        blocked_profiles_json=[],
        allowed_setups_json=["Clean bull pullback"],
        blocked_setups_json=["Failed breakout"],
        input_symbols_json={"primary_market": "SPY"},
        index_health_json={"SPY": {"above_sma200": True}},
        universe_participation_json={"ticker_count": 42},
        sector_leadership_json=[{"sector": "Technology"}],
        reasons_json=["missing_qqq_market_data"],
        warnings_json=["low_market_confidence"],
        debug_json={"source": "test"},
    )

    assert snapshot.regime == "Bull pullback"
    assert snapshot.position_size_multiplier == 0.75
    assert snapshot.reduced_profiles_json == ["early_rocket"]
    assert snapshot.index_health_json == {"SPY": {"above_sma200": True}}


def test_ranking_result_model_accepts_profile_values() -> None:
    result = RankingResult(
        run_id=7,
        raw_row_id=11,
        ticker="MSFT",
        company_name="Microsoft",
        sector="Technology",
        ranking_profile="momentum_swing",
        ranking_label="Momentum Swing",
        profile_rank=1,
        profile_score=Decimal("8.1234"),
        technical_profile_score=Decimal("8.4"),
        fundamental_score=Decimal("7.8"),
        base_technical_score=Decimal("8.1"),
        technical_classification="Prime clean pullback",
        fundamental_label="High-quality quant",
        decision_label="Strong candidate",
        position_size_hint="Full starter",
        notes="aligned",
        warning_flags_json=["earnings_medium_risk"],
        penalties_json={"earnings_medium_risk": 1.0},
        gates_json={"earnings_block": False},
        component_scores_json={"momentum_strength": 8.7},
        debug_json={"ranking_engine_version": "1.0.0"},
        is_complete=True,
        has_warning=True,
        has_fundamental=True,
        has_technical=True,
        sort_bucket=0,
    )

    assert result.ranking_profile == "momentum_swing"
    assert result.profile_score == Decimal("8.1234")
    assert result.warning_flags_json == ["earnings_medium_risk"]
    assert result.debug_json == {"ranking_engine_version": "1.0.0"}


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


def test_ranking_results_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260709_0011_create_ranking_results.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0011_create_ranking_results"' in migration
    assert 'down_revision: str | None = "0010_add_earnings_risk_gate"' in migration


def test_market_regime_snapshots_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260728_0012_add_market_regime_snapshots.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0012_add_market_regime_snapshots"' in migration
    assert 'down_revision: str | None = "0011_create_ranking_results"' in migration


def test_sector_rotation_tables_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260728_0013_add_sector_rotation_tables.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0013_add_sector_rotation_tables"' in migration
    assert 'down_revision: str | None = "0012_add_market_regime_snapshots"' in migration


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
