"""Add immutable work-scope, refresh-cycle, and acquisition-plan records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0081_scope_refresh_identity"
down_revision = "0080_effective_configuration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "acquisition_plan_records",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("plan_kind", sa.String(160), nullable=False),
        sa.Column("plan_version", sa.String(160), nullable=False),
        sa.Column(
            "previous_plan_id",
            sa.String(64),
            sa.ForeignKey("acquisition_plan_records.plan_id", ondelete="RESTRICT"),
        ),
        sa.Column("revision_reason", sa.String(64)),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(previous_plan_id IS NULL) = (revision_reason IS NULL)",
            name="ck_acquisition_plan_revision_lineage",
        ),
        sa.CheckConstraint(
            "previous_plan_id IS NULL OR previous_plan_id <> plan_id",
            name="ck_acquisition_plan_not_self_referential",
        ),
    )
    op.create_table(
        "work_scope_records",
        sa.Column("scope_id", sa.String(64), primary_key=True),
        sa.Column("scope_kind", sa.String(160), nullable=False),
        sa.Column("subject_kind", sa.String(160), nullable=False),
        sa.Column("membership_policy", sa.String(32), nullable=False),
        sa.Column("membership_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "parent_scope_id",
            sa.String(64),
            sa.ForeignKey("work_scope_records.scope_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "acquisition_plan_id",
            sa.String(64),
            sa.ForeignKey("acquisition_plan_records.plan_id", ondelete="RESTRICT"),
        ),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "membership_policy IN ('FROZEN', 'DECLARED_DYNAMIC')",
            name="ck_work_scope_membership_policy",
        ),
        sa.CheckConstraint(
            "parent_scope_id IS NULL OR parent_scope_id <> scope_id",
            name="ck_work_scope_not_self_referential",
        ),
    )
    op.create_table(
        "work_scope_members",
        sa.Column(
            "scope_id",
            sa.String(64),
            sa.ForeignKey("work_scope_records.scope_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("subject_type", sa.String(64), primary_key=True),
        sa.Column("subject_id", sa.String(512), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("membership_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("scope_id", "ordinal", name="uq_work_scope_member_ordinal"),
    )
    op.create_index("idx_work_scope_members_scope", "work_scope_members", ["scope_id", "ordinal"])
    op.create_table(
        "refresh_cycle_records",
        sa.Column("refresh_cycle_id", sa.String(64), primary_key=True),
        sa.Column("refresh_kind", sa.String(160), nullable=False),
        sa.Column(
            "scope_id",
            sa.String(64),
            sa.ForeignKey("work_scope_records.scope_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "prior_refresh_id",
            sa.String(64),
            sa.ForeignKey("refresh_cycle_records.refresh_cycle_id", ondelete="RESTRICT"),
        ),
        sa.Column("observation_cycle_key", sa.String(512), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "prior_refresh_id IS NULL OR prior_refresh_id <> refresh_cycle_id",
            name="ck_refresh_cycle_not_self_referential",
        ),
        sa.UniqueConstraint(
            "scope_id",
            "refresh_kind",
            "observation_cycle_key",
            name="uq_refresh_cycle_semantic_key",
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_scope_identity_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'scope, refresh, and acquisition identity records are immutable';
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in (
        "acquisition_plan_records",
        "work_scope_records",
        "work_scope_members",
        "refresh_cycle_records",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_scope_identity_mutation()"
        )


def downgrade():
    op.drop_table("refresh_cycle_records")
    op.drop_index("idx_work_scope_members_scope", table_name="work_scope_members")
    op.drop_table("work_scope_members")
    op.drop_table("work_scope_records")
    op.drop_table("acquisition_plan_records")
    op.execute("DROP FUNCTION reject_scope_identity_mutation()")
