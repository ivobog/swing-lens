"""Scope native CERI score uniqueness to the frozen calculation context."""

import sqlalchemy as sa

from alembic import op

revision = "0096_ceri_native_context"
down_revision = "0095_ib_bar_admission_receipts"
branch_labels = None
depends_on = None

TABLE = "ceri_score_snapshots"
OLD = "uq_ceri_score_snapshots_run_company_version"
KEY = ["run_id", "company_id", "config_hash", "calculation_version"]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql" or bind.dialect.server_version_info < (15,):
        raise RuntimeError("CERI_NATIVE_CONTEXT_REQUIRES_POSTGRESQL_15")
    # Keep the preflight and index replacement in one transactional DDL window.
    # An operator-supplied lock_timeout bounds admission behind runtime writers.
    bind.execute(sa.text("LOCK TABLE ceri_score_snapshots IN SHARE ROW EXCLUSIVE MODE"))
    collisions = bind.execute(
        sa.text("""
        SELECT 1 FROM ceri_score_snapshots
        WHERE controlled_replay_id IS NULL AND calculation_context_id IS NOT NULL
        GROUP BY run_id, company_id, calculation_context_id HAVING count(*) > 1 LIMIT 1
    """)
    ).first()
    if collisions:
        raise RuntimeError("CERI_NATIVE_CONTEXT_DUPLICATES_REQUIRE_REVIEW")
    for name, keys, state in (
        ("uq_ceri_native_score", ["run_id", "company_id", "calculation_context_id"], "NOT NULL"),
        ("uq_ceri_legacy_score", KEY, "NULL"),
    ):
        op.create_index(
            name,
            TABLE,
            keys,
            unique=True,
            postgresql_nulls_not_distinct=(state == "NOT NULL"),
            postgresql_where=sa.text(
                f"controlled_replay_id IS NULL AND calculation_context_id IS {state}"
            ),
        )
    op.drop_constraint(OLD, TABLE, type_="unique")


def downgrade():
    # PostgreSQL validates every row before removing either replacement index.
    # Multiple historical generations intentionally prevent a lossy downgrade.
    op.create_unique_constraint(OLD, TABLE, KEY)
    op.drop_index("uq_ceri_native_score", table_name=TABLE)
    op.drop_index("uq_ceri_legacy_score", table_name=TABLE)
