"""Add instrument lifecycle evidence and ticker/session readiness ledgers."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0090_market_data_readiness"
down_revision: str | None = "0089_pipeline_execution_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instrument_lifecycle_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("last_trading_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("successor_ticker", sa.Text(), nullable=True),
        sa.Column("contract_valid_from", sa.Date(), nullable=True),
        sa.Column("contract_valid_to", sa.Date(), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_current_revision", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("supersedes_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('ACTIVE', 'HALTED', 'INACTIVE', 'MERGED', 'DELISTED', 'UNKNOWN')",
            name="ck_instrument_lifecycle_state",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["instrument_lifecycle_records.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "revision", name="uq_instrument_lifecycle_ticker_revision"),
    )
    op.create_index(
        "idx_instrument_lifecycle_current",
        "instrument_lifecycle_records",
        ["ticker", "is_current_revision"],
    )
    op.create_index(
        "uq_instrument_lifecycle_one_current",
        "instrument_lifecycle_records",
        ["ticker"],
        unique=True,
        postgresql_where=sa.text("is_current_revision"),
    )

    op.create_table(
        "market_data_session_dispositions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=False),
        sa.Column("market_calculation_context_id", sa.BigInteger(), nullable=False),
        sa.Column("fetch_run_id", sa.BigInteger(), nullable=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("expected_session", sa.Date(), nullable=False),
        sa.Column("latest_bar_session", sa.Date(), nullable=True),
        sa.Column("disposition", sa.String(length=48), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False),
        sa.Column("technical_eligible", sa.Boolean(), nullable=False),
        sa.Column("downstream_eligible", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        sa.Column("reason_message", sa.Text(), nullable=False),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_current_revision", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("supersedes_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('READY', 'TRANSIENT_FAILURE', "
            "'REQUIRED_DATA_UNAVAILABLE', 'TERMINAL_INACTIVE', 'UNKNOWN')",
            name="ck_market_data_session_disposition",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('ACTIVE', 'HALTED', 'INACTIVE', 'MERGED', 'DELISTED', 'UNKNOWN')",
            name="ck_market_data_session_lifecycle_state",
        ),
        sa.ForeignKeyConstraint(["fetch_run_id"], ["ib_fetch_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["market_calculation_context_id"],
            ["market_calculation_contexts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["market_data_session_dispositions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pipeline_run_id", "ticker", "revision", name="uq_market_data_disposition_revision"
        ),
    )
    op.create_index(
        "idx_market_data_disposition_current",
        "market_data_session_dispositions",
        ["pipeline_run_id", "is_current_revision", "ticker"],
    )
    op.create_index(
        "uq_market_data_disposition_one_current",
        "market_data_session_dispositions",
        ["pipeline_run_id", "ticker"],
        unique=True,
        postgresql_where=sa.text("is_current_revision"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_market_data_disposition_one_current", table_name="market_data_session_dispositions"
    )
    op.drop_index(
        "idx_market_data_disposition_current", table_name="market_data_session_dispositions"
    )
    op.drop_table("market_data_session_dispositions")
    op.drop_index("uq_instrument_lifecycle_one_current", table_name="instrument_lifecycle_records")
    op.drop_index("idx_instrument_lifecycle_current", table_name="instrument_lifecycle_records")
    op.drop_table("instrument_lifecycle_records")
