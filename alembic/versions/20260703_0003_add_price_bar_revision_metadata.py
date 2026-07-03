"""add price bar revision metadata

Revision ID: 0003_price_bar_revision_metadata
Revises: 0002_fetch_warning_flags
Create Date: 2026-07-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_price_bar_revision_metadata"
down_revision: str | None = "0002_fetch_warning_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "price_bars",
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "price_bars",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "price_bars",
        sa.Column("revised_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "price_bars",
        sa.Column(
            "revision_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("price_bars", sa.Column("data_hash", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE price_bars
        SET data_hash = md5(concat_ws(
            '|',
            CASE
                WHEN open IS NULL THEN ''
                ELSE regexp_replace(regexp_replace(open::text, '(\\.\\d*?)0+$', '\\1'), '\\.$', '')
            END,
            CASE
                WHEN high IS NULL THEN ''
                ELSE regexp_replace(regexp_replace(high::text, '(\\.\\d*?)0+$', '\\1'), '\\.$', '')
            END,
            CASE
                WHEN low IS NULL THEN ''
                ELSE regexp_replace(regexp_replace(low::text, '(\\.\\d*?)0+$', '\\1'), '\\.$', '')
            END,
            CASE
                WHEN close IS NULL THEN ''
                ELSE regexp_replace(regexp_replace(close::text, '(\\.\\d*?)0+$', '\\1'), '\\.$', '')
            END,
            CASE
                WHEN volume IS NULL THEN ''
                ELSE regexp_replace(regexp_replace(volume::text, '(\\.\\d*?)0+$', '\\1'), '\\.$', '')
            END,
            COALESCE(source, ''),
            COALESCE(what_to_show, ''),
            COALESCE(adjustment_type, '')
        ))
        WHERE data_hash IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("price_bars", "data_hash")
    op.drop_column("price_bars", "revision_count")
    op.drop_column("price_bars", "revised_at")
    op.drop_column("price_bars", "last_seen_at")
    op.drop_column("price_bars", "first_seen_at")
