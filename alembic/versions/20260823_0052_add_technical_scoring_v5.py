"""add technical scoring v5 persistence

Revision ID: 0052_technical_scoring_v5
Revises: 0051_worker_progress_reliability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0052_technical_scoring_v5"
down_revision: str | None = "0051_worker_progress_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC_COLUMNS = (
    "technical_strength_score",
    "setup_quality_score",
    "entry_quality_score",
    "technical_composite_score",
    "confidence_adjusted_score",
    "leadership_v5_score",
    "residual_momentum_score",
    "trigger_distance_atr",
    "stop_distance_atr",
    "stage_modifier",
)


def upgrade() -> None:
    for name in NUMERIC_COLUMNS:
        op.add_column("technical_scores", sa.Column(name, sa.Numeric(), nullable=True))
    op.add_column("technical_scores", sa.Column("setup_type", sa.String(64), nullable=True))
    op.add_column(
        "technical_scores", sa.Column("sector_benchmark_symbol", sa.String(32), nullable=True)
    )
    op.add_column(
        "technical_scores",
        sa.Column(
            "v5_debug_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("technical_scores", "v5_debug_json")
    op.drop_column("technical_scores", "sector_benchmark_symbol")
    op.drop_column("technical_scores", "setup_type")
    for name in reversed(NUMERIC_COLUMNS):
        op.drop_column("technical_scores", name)
