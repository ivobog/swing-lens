"""Bind Winner durable generations, publication, and checkpoints to semantic authority."""

import sqlalchemy as sa

from alembic import op

revision = "0083_winner_scope_truth"
down_revision = "0082_pipeline_ceri_scope"
branch_labels = None
depends_on = None

_TABLES = (
    "winner_cohort_generations",
    "winner_estimate_publication_requests",
    "winner_processing_runs",
)


def _add_bindings(table: str) -> None:
    op.add_column(table, sa.Column("scope_id", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("refresh_cycle_id", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("acquisition_plan_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_scope_id",
        table,
        "work_scope_records",
        ["scope_id"],
        ["scope_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        f"fk_{table}_refresh_cycle_id",
        table,
        "refresh_cycle_records",
        ["refresh_cycle_id"],
        ["refresh_cycle_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        f"fk_{table}_acquisition_plan_id",
        table,
        "acquisition_plan_records",
        ["acquisition_plan_id"],
        ["plan_id"],
        ondelete="RESTRICT",
    )
    op.create_index(f"idx_{table}_scope_refresh", table, ["scope_id", "refresh_cycle_id"])
    op.execute(
        f"CREATE TRIGGER {table}_semantic_authority_immutable "
        f"BEFORE UPDATE ON {table} FOR EACH ROW "
        "EXECUTE FUNCTION reject_semantic_authority_rebinding()"
    )


def upgrade() -> None:
    for table in _TABLES:
        _add_bindings(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TRIGGER {table}_semantic_authority_immutable ON {table}")
        op.drop_index(f"idx_{table}_scope_refresh", table_name=table)
        op.drop_constraint(f"fk_{table}_acquisition_plan_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_refresh_cycle_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_scope_id", table, type_="foreignkey")
        op.drop_column(table, "acquisition_plan_id")
        op.drop_column(table, "refresh_cycle_id")
        op.drop_column(table, "scope_id")
