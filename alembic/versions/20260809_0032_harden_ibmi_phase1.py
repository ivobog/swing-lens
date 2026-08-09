"""harden IB market-intelligence phase 1 identities

Revision ID: 0032_harden_ibmi_phase1
Revises: 0031_add_ib_market_intelligence
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_harden_ibmi_phase1"
down_revision: str | None = "0031_add_ib_market_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ib_intelligence_features "
        "DROP CONSTRAINT uq_ib_intelligence_feature_version"
    )
    op.execute(
        "ALTER TABLE ib_intelligence_features ADD CONSTRAINT "
        "uq_ib_intelligence_feature_version UNIQUE "
        "(ticker, as_of_session, module, calculation_version, config_hash, input_signature)"
    )
    op.execute("DROP INDEX IF EXISTS ix_ib_execution_raw_hash")
    op.execute(
        "CREATE UNIQUE INDEX uq_ib_execution_raw_hash "
        "ON ib_execution_fills(raw_record_hash)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_ib_execution_active_external "
        "ON ib_execution_fills(external_execution_id) "
        "WHERE external_execution_id IS NOT NULL AND is_superseded = false"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ib_execution_active_external")
    op.execute("DROP INDEX IF EXISTS uq_ib_execution_raw_hash")
    op.execute("CREATE INDEX ix_ib_execution_raw_hash ON ib_execution_fills(raw_record_hash)")
    op.execute(
        "DELETE FROM ib_intelligence_features older USING ib_intelligence_features newer "
        "WHERE older.ticker = newer.ticker "
        "AND older.as_of_session = newer.as_of_session "
        "AND older.module = newer.module "
        "AND older.calculation_version = newer.calculation_version "
        "AND older.config_hash = newer.config_hash "
        "AND (older.calculated_at, older.id) < (newer.calculated_at, newer.id)"
    )
    op.execute(
        "ALTER TABLE ib_intelligence_features "
        "DROP CONSTRAINT uq_ib_intelligence_feature_version"
    )
    op.execute(
        "ALTER TABLE ib_intelligence_features ADD CONSTRAINT "
        "uq_ib_intelligence_feature_version UNIQUE "
        "(ticker, as_of_session, module, calculation_version, config_hash)"
    )
