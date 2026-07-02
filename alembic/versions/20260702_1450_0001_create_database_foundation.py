"""create database foundation

Revision ID: 0001_create_database_foundation
Revises:
Create Date: 2026-07-02 14:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_create_database_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "upload_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("pine_engine_version", sa.Text(), nullable=True),
        sa.Column("python_engine_version", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ib_contracts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("ib_conid", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=True),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("primary_exchange", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("sec_type", sa.Text(), nullable=True),
        sa.Column("local_symbol", sa.Text(), nullable=True),
        sa.Column("trading_class", sa.Text(), nullable=True),
        sa.Column("resolution_status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker"),
    )

    op.create_table(
        "price_bars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=True),
        sa.Column("high", sa.Numeric(), nullable=True),
        sa.Column("low", sa.Numeric(), nullable=True),
        sa.Column("close", sa.Numeric(), nullable=True),
        sa.Column("volume", sa.Numeric(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("what_to_show", sa.Text(), nullable=False),
        sa.Column("adjustment_type", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "bar_date",
            "timeframe",
            "what_to_show",
            name="uq_price_bars_ticker_date_timeframe_what_to_show",
        ),
    )
    op.create_index("idx_price_bars_ticker_date", "price_bars", ["ticker", "bar_date"])

    op.create_table(
        "raw_company_rows",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_raw_company_rows_run_id", "raw_company_rows", ["run_id"])
    op.create_index("idx_raw_company_rows_ticker", "raw_company_rows", ["ticker"])

    op.create_table(
        "fundamental_scores",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("growth_score", sa.Numeric(), nullable=True),
        sa.Column("profitability_score", sa.Numeric(), nullable=True),
        sa.Column("fcf_score", sa.Numeric(), nullable=True),
        sa.Column("balance_sheet_score", sa.Numeric(), nullable=True),
        sa.Column("valuation_score", sa.Numeric(), nullable=True),
        sa.Column("momentum_score", sa.Numeric(), nullable=True),
        sa.Column("dilution_score", sa.Numeric(), nullable=True),
        sa.Column("risk_score", sa.Numeric(), nullable=True),
        sa.Column("missing_data_penalty", sa.Numeric(), nullable=True),
        sa.Column("fundamental_score", sa.Numeric(), nullable=True),
        sa.Column("fundamental_label", sa.Text(), nullable=True),
        sa.Column("trap_flags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("debug_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ticker", name="uq_fundamental_scores_run_ticker"),
    )
    op.create_index("idx_fundamental_scores_run_id", "fundamental_scores", ["run_id"])

    op.create_table(
        "technical_scores",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("trend_score", sa.Numeric(), nullable=True),
        sa.Column("local_trend_score", sa.Numeric(), nullable=True),
        sa.Column("momentum_score", sa.Numeric(), nullable=True),
        sa.Column("setup_score", sa.Numeric(), nullable=True),
        sa.Column("risk_score", sa.Numeric(), nullable=True),
        sa.Column("market_score", sa.Numeric(), nullable=True),
        sa.Column("relative_strength_score", sa.Numeric(), nullable=True),
        sa.Column("sector_relative_strength_score", sa.Numeric(), nullable=True),
        sa.Column("combined_relative_strength_score", sa.Numeric(), nullable=True),
        sa.Column("htf_score", sa.Numeric(), nullable=True),
        sa.Column("dual_score", sa.Numeric(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("pullback_health", sa.Text(), nullable=True),
        sa.Column("action_bias", sa.Text(), nullable=True),
        sa.Column("suggested_stop", sa.Numeric(), nullable=True),
        sa.Column("suggested_target", sa.Numeric(), nullable=True),
        sa.Column("reward_risk", sa.Numeric(), nullable=True),
        sa.Column("entry_risk_pct", sa.Numeric(), nullable=True),
        sa.Column("technical_confidence", sa.Text(), nullable=True),
        sa.Column("insufficient_data", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("missing_data_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("debug_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ticker", name="uq_technical_scores_run_ticker"),
    )
    op.create_index("idx_technical_scores_run_id", "technical_scores", ["run_id"])

    op.create_table(
        "combined_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("final_rank", sa.Integer(), nullable=True),
        sa.Column("final_score", sa.Numeric(), nullable=True),
        sa.Column("fundamental_score", sa.Numeric(), nullable=True),
        sa.Column("fundamental_label", sa.Text(), nullable=True),
        sa.Column("technical_classification", sa.Text(), nullable=True),
        sa.Column("dual_score", sa.Numeric(), nullable=True),
        sa.Column("combined_decision", sa.Text(), nullable=True),
        sa.Column("position_size_hint", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ticker", name="uq_combined_results_run_ticker"),
    )
    op.create_index("idx_combined_results_run_id", "combined_results", ["run_id"])
    op.create_index("idx_combined_results_run_rank", "combined_results", ["run_id", "final_rank"])

    op.create_table(
        "engine_parameters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("parameters_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_engine_parameters_run_id", "engine_parameters", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_engine_parameters_run_id", table_name="engine_parameters")
    op.drop_table("engine_parameters")
    op.drop_index("idx_combined_results_run_rank", table_name="combined_results")
    op.drop_index("idx_combined_results_run_id", table_name="combined_results")
    op.drop_table("combined_results")
    op.drop_index("idx_technical_scores_run_id", table_name="technical_scores")
    op.drop_table("technical_scores")
    op.drop_index("idx_fundamental_scores_run_id", table_name="fundamental_scores")
    op.drop_table("fundamental_scores")
    op.drop_index("idx_raw_company_rows_ticker", table_name="raw_company_rows")
    op.drop_index("idx_raw_company_rows_run_id", table_name="raw_company_rows")
    op.drop_table("raw_company_rows")
    op.drop_index("idx_price_bars_ticker_date", table_name="price_bars")
    op.drop_table("price_bars")
    op.drop_table("ib_contracts")
    op.drop_table("upload_runs")
