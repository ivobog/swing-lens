"""Bind Pipeline, CERI, IB, and durable jobs to semantic work authority."""

import sqlalchemy as sa

from alembic import op

revision = "0082_pipeline_ceri_scope"
down_revision = "0081_scope_refresh_identity"
branch_labels = None
depends_on = None


_BINDING_TABLES = (
    "pipeline_runs",
    "background_jobs",
    "ib_fetch_runs",
    "ceri_ingestion_runs",
    "ceri_processing_runs",
)


def _add_binding_columns(table: str) -> None:
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


def upgrade() -> None:
    for table in _BINDING_TABLES:
        _add_binding_columns(table)
    op.add_column(
        "background_jobs",
        sa.Column(
            "required_for_parent_completion",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_semantic_authority_rebinding() RETURNS trigger AS $$
        BEGIN
            IF OLD.scope_id IS NOT NULL AND NEW.scope_id IS DISTINCT FROM OLD.scope_id THEN
                RAISE EXCEPTION 'semantic work scope cannot be rebound';
            END IF;
            IF OLD.refresh_cycle_id IS NOT NULL
               AND NEW.refresh_cycle_id IS DISTINCT FROM OLD.refresh_cycle_id THEN
                RAISE EXCEPTION 'semantic refresh cycle cannot be rebound';
            END IF;
            IF OLD.acquisition_plan_id IS NOT NULL
               AND NEW.acquisition_plan_id IS DISTINCT FROM OLD.acquisition_plan_id THEN
                RAISE EXCEPTION 'semantic acquisition plan cannot be rebound';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in _BINDING_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_semantic_authority_immutable "
            f"BEFORE UPDATE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION reject_semantic_authority_rebinding()"
        )


def downgrade() -> None:
    for table in _BINDING_TABLES:
        op.execute(f"DROP TRIGGER {table}_semantic_authority_immutable ON {table}")
    op.execute("DROP FUNCTION reject_semantic_authority_rebinding()")
    op.drop_column("background_jobs", "required_for_parent_completion")
    for table in reversed(_BINDING_TABLES):
        op.drop_index(f"idx_{table}_scope_refresh", table_name=table)
        op.drop_constraint(f"fk_{table}_acquisition_plan_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_refresh_cycle_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_scope_id", table, type_="foreignkey")
        op.drop_column(table, "acquisition_plan_id")
        op.drop_column(table, "refresh_cycle_id")
        op.drop_column(table, "scope_id")
