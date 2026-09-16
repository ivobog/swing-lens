"""Content-addressed immutable configuration records for durable delivery."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0080_effective_configuration"
down_revision = "0079_setup_lifecycle_alert_ev"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "effective_configuration_records",
        sa.Column("resolution_hash", sa.String(64), primary_key=True),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("semantic_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_effective_configuration_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'effective configuration records are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER effective_configuration_records_immutable
        BEFORE UPDATE OR DELETE ON effective_configuration_records
        FOR EACH ROW EXECUTE FUNCTION reject_effective_configuration_mutation();
    """)
    op.create_table(
        "execution_configuration_anchors",
        sa.Column("anchor_id", sa.String(64), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "execution_configuration_bindings",
        sa.Column("binding_key", sa.String(80), primary_key=True),
        sa.Column(
            "anchor_id",
            sa.String(64),
            sa.ForeignKey("execution_configuration_anchors.anchor_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("background_jobs.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "pipeline_run_id",
            sa.BigInteger(),
            sa.ForeignKey("pipeline_runs.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "winner_cohort_generation_id",
            sa.BigInteger(),
            sa.ForeignKey("winner_cohort_generations.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.CheckConstraint(
            "(CASE WHEN job_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN pipeline_run_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN winner_cohort_generation_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_configuration_binding_scope",
        ),
    )
    for table in ("execution_configuration_anchors", "execution_configuration_bindings"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_effective_configuration_mutation()"
        )


def downgrade():
    op.drop_table("execution_configuration_bindings")
    op.drop_table("execution_configuration_anchors")
    op.drop_table("effective_configuration_records")
    op.execute("DROP FUNCTION reject_effective_configuration_mutation()")
