"""add ceri tables

Revision ID: 0018_add_ceri_tables
Revises: 0017_create_setup_lifecycle_tables
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

from app.models.ceri_tables import CERI_TABLES

revision: str = "0018_add_ceri_tables"
down_revision: str | None = "0017_create_setup_lifecycle_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in CERI_TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(CERI_TABLES):
        table.drop(bind=bind, checkfirst=False)
