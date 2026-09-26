"""Add exact first-fetch provenance and shared Technical source manifests."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0084_technical_recovery"
down_revision = "0083_winner_scope_truth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_bars", sa.Column("first_fetch_run_id", sa.BigInteger(), nullable=True))
    op.add_column("price_bars", sa.Column("first_fetch_item_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_price_bars_first_fetch_run",
        "price_bars",
        "ib_fetch_runs",
        ["first_fetch_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_price_bars_first_fetch_item",
        "price_bars",
        "ib_fetch_items",
        ["first_fetch_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_price_bars_first_fetch_item", "price_bars", ["first_fetch_item_id"]
    )

    op.create_table(
        "technical_source_manifests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("calculation_context_id", sa.BigInteger(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("state_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["calculation_context_id"], ["market_calculation_contexts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_digest", name="uq_technical_source_manifest_digest"),
    )
    op.create_index(
        "idx_technical_source_manifest_run",
        "technical_source_manifests",
        ["run_id", "pipeline_run_id"],
    )
    op.create_index(
        "idx_technical_source_manifest_context",
        "technical_source_manifests",
        ["calculation_context_id"],
    )

    op.add_column("technical_scores", sa.Column("source_manifest_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_technical_scores_source_manifest",
        "technical_scores",
        "technical_source_manifests",
        ["source_manifest_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_technical_scores_source_manifest", "technical_scores", ["source_manifest_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_technical_scores_source_manifest", table_name="technical_scores")
    op.drop_constraint(
        "fk_technical_scores_source_manifest", "technical_scores", type_="foreignkey"
    )
    op.drop_column("technical_scores", "source_manifest_id")
    op.drop_index(
        "idx_technical_source_manifest_context", table_name="technical_source_manifests"
    )
    op.drop_index("idx_technical_source_manifest_run", table_name="technical_source_manifests")
    op.drop_table("technical_source_manifests")
    op.drop_index("idx_price_bars_first_fetch_item", table_name="price_bars")
    op.drop_constraint("fk_price_bars_first_fetch_item", "price_bars", type_="foreignkey")
    op.drop_constraint("fk_price_bars_first_fetch_run", "price_bars", type_="foreignkey")
    op.drop_column("price_bars", "first_fetch_item_id")
    op.drop_column("price_bars", "first_fetch_run_id")
