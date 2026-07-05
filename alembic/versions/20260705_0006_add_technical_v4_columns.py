"""add technical scoring v4 columns

Revision ID: 0006_add_technical_v4_columns
Revises: 0005_add_fundamentals_v2_columns
Create Date: 2026-07-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_add_technical_v4_columns"
down_revision: str | None = "0005_add_fundamentals_v2_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V4_NUMERIC_COLUMNS = [
    "data_quality_score",
    "leadership_score",
    "vcp_score",
    "box_tightness_score",
    "breakout_quality_score",
    "climax_risk_score",
    "atr_percentile_252",
    "volume_percentile_252",
    "range_percentile_252",
    "extension_percentile_252",
]

V4_JSON_COLUMNS = [
    "feature_flags_json",
    "warning_flags_json",
    "sub_tags_json",
    "v4_debug_json",
]


def upgrade() -> None:
    op.add_column(
        "technical_scores",
        sa.Column("technical_engine_version", sa.String(length=32), nullable=True),
    )
    for column_name in V4_NUMERIC_COLUMNS:
        op.add_column("technical_scores", sa.Column(column_name, sa.Numeric(), nullable=True))

    op.add_column(
        "technical_scores",
        sa.Column("stage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "technical_scores",
        sa.Column("market_regime", sa.String(length=64), nullable=True),
    )

    for column_name in V4_JSON_COLUMNS:
        op.add_column(
            "technical_scores",
            sa.Column(
                column_name,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for column_name in reversed(V4_JSON_COLUMNS):
        op.drop_column("technical_scores", column_name)
    op.drop_column("technical_scores", "market_regime")
    op.drop_column("technical_scores", "stage")
    for column_name in reversed(V4_NUMERIC_COLUMNS):
        op.drop_column("technical_scores", column_name)
    op.drop_column("technical_scores", "technical_engine_version")
