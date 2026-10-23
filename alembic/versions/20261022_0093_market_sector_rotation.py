"""Add independent immutable market-sector rotation history (D2 only)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0093_market_sector_rotation"
down_revision = "0091_readiness_constraints"
branch_labels = None
depends_on = None

_TABLES = ("market_sector_rotation_snapshots", "market_sector_rotation_rows")
_METRICS = (
    "rs_5_pct",
    "rs_21_pct",
    "rs_63_pct",
    "rs_prior_21_pct",
    "relative_momentum_pp",
    "etf_return_63_pct",
    "spy_return_63_pct",
)


def upgrade():
    identifier = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    document = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        _TABLES[0],
        sa.Column("id", identifier, primary_key=True, autoincrement=True),
        sa.Column("market_session", sa.Date(), nullable=False),
        sa.Column("captured_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column("benchmark_symbol", sa.String(16), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("cohort_status", sa.String(16), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("source_material_digest", sa.String(64), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("evidence_summary_json", document, nullable=False),
        sa.UniqueConstraint(
            "market_session",
            "algorithm_version",
            "configuration_digest",
            name="uq_msi_rotation_session_version_config",
        ),
        sa.CheckConstraint(
            "cohort_status = 'COMPLETE' AND eligible_count = 11", name="ck_msi_rotation_complete"
        ),
        sa.CheckConstraint(
            "benchmark_symbol = 'SPY' AND timeframe = '1 day'", name="ck_msi_rotation_benchmark"
        ),
    )
    op.create_index(
        "idx_msi_rotation_latest", _TABLES[0], [sa.text("market_session DESC"), sa.text("id DESC")]
    )
    op.create_index("idx_msi_rotation_algorithm", _TABLES[0], ["algorithm_version"])
    op.create_table(
        _TABLES[1],
        sa.Column("id", identifier, primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            identifier,
            sa.ForeignKey(f"{_TABLES[0]}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sector_canonical", sa.String(128), nullable=False),
        sa.Column("sector_display", sa.String(128), nullable=False),
        sa.Column("etf_symbol", sa.String(16), nullable=False),
        sa.Column("quality_status", sa.String(32), nullable=False),
        sa.Column("price_basis", sa.String(32), nullable=False),
        sa.Column("benchmark_price_basis", sa.String(32), nullable=False),
        sa.Column("anchor_sessions_json", document, nullable=False),
        *(sa.Column(name, sa.Float(), nullable=False) for name in _METRICS),
        sa.Column("rotation_state", sa.String(32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("snapshot_id", "etf_symbol", name="uq_msi_rotation_symbol"),
        sa.UniqueConstraint("snapshot_id", "rank", name="uq_msi_rotation_rank"),
        sa.CheckConstraint("rank BETWEEN 1 AND 11", name="ck_msi_rotation_rank"),
        sa.CheckConstraint("quality_status = 'ELIGIBLE'", name="ck_msi_rotation_eligible"),
        sa.CheckConstraint(
            "price_basis IN ('TRADES', 'ADJUSTED_LAST') AND price_basis = benchmark_price_basis",
            name="ck_msi_rotation_basis",
        ),
        sa.CheckConstraint(
            "rotation_state IN ('Leading', 'Improving', 'Weakening', 'Lagging')",
            name="ck_msi_rotation_state",
        ),
        *(
            sa.CheckConstraint(
                f"{name} BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308",
                name=f"ck_msi_finite_{name}",
            )
            for name in _METRICS
        ),
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION msi_rotation_reject_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'MARKET_SECTOR_ROTATION_IMMUTABLE'; END;
            $$ LANGUAGE plpgsql""")
        for table in _TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION msi_rotation_reject_mutation()"
            )
    elif dialect == "sqlite":
        for table in _TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_{action.lower()}_immutable BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'MARKET_SECTOR_ROTATION_IMMUTABLE'); END"
                )


def downgrade():
    for table in reversed(_TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION msi_rotation_reject_mutation()")
