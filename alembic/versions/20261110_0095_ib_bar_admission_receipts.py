"""Seal exact accepted bar versions independently of request completeness."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0095_ib_bar_admission_receipts"
down_revision = "0094_sector_acquisition"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ib_bar_admission_receipts",
        sa.Column("receipt_hash", sa.String(64), primary_key=True),
        sa.Column("fetch_item_id", sa.BigInteger(), sa.ForeignKey(
            "ib_fetch_items.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("fetch_run_id", sa.BigInteger(), sa.ForeignKey(
            "ib_fetch_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("upload_run_id", sa.BigInteger(), sa.ForeignKey(
            "upload_runs.id", ondelete="RESTRICT")),
        sa.Column("pipeline_run_id", sa.BigInteger(), sa.ForeignKey(
            "pipeline_runs.id", ondelete="RESTRICT")),
        sa.Column("calculation_context_id", sa.BigInteger(), sa.ForeignKey(
            "market_calculation_contexts.id", ondelete="RESTRICT")),
        sa.Column("acquisition_plan_id", sa.String(64), sa.ForeignKey(
            "acquisition_plan_records.plan_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scope_id", sa.String(64), sa.ForeignKey(
            "work_scope_records.scope_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("refresh_cycle_id", sa.String(64), sa.ForeignKey(
            "refresh_cycle_records.refresh_cycle_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("what_to_show", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("(pipeline_run_id IS NULL) = (calculation_context_id IS NULL)",
                           name="ck_ib_bar_admission_owner_context"),
    )
    op.create_index("idx_ib_bar_admission_context_key", "ib_bar_admission_receipts",
                    ["calculation_context_id", "ticker", "what_to_show"])
    op.create_index("idx_ib_bar_admission_scope", "ib_bar_admission_receipts",
                    ["scope_id", "fetch_run_id"])
    op.execute("""
        CREATE FUNCTION reject_ib_bar_admission_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'IB_BAR_ADMISSION_RECEIPTS_ARE_APPEND_ONLY'; END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER ib_bar_admission_receipts_immutable
        BEFORE UPDATE OR DELETE ON ib_bar_admission_receipts
        FOR EACH ROW EXECUTE FUNCTION reject_ib_bar_admission_mutation()
    """)


def downgrade():
    op.drop_table("ib_bar_admission_receipts")
    op.execute("DROP FUNCTION reject_ib_bar_admission_mutation()")
