"""add raw company sector metadata

Revision ID: 0014_sector_metadata
Revises: 0013_add_sector_rotation_tables
Create Date: 2026-07-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_sector_metadata"
down_revision: str | None = "0013_add_sector_rotation_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNKNOWN = "Unknown"
TAXONOMY = "tradingview"

CANONICAL_SECTORS = {
    "Technology",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Energy",
    "Basic Materials",
    "Real Estate",
    "Utilities",
    "Government / Other",
    "Miscellaneous / Other",
    "ETF / Fund",
    UNKNOWN,
}

TRADINGVIEW_MAP = {
    "energy minerals": "Energy",
    "non-energy minerals": "Basic Materials",
    "process industries": "Basic Materials",
    "producer manufacturing": "Industrials",
    "industrial services": "Industrials",
    "transportation": "Industrials",
    "distribution services": "Industrials",
    "commercial services": "Industrials",
    "technology services": "Technology",
    "electronic technology": "Technology",
    "communications": "Communication Services",
    "finance": "Financial Services",
    "health services": "Healthcare",
    "health technology": "Healthcare",
    "consumer durables": "Consumer Cyclical",
    "consumer services": "Consumer Cyclical",
    "retail trade": "Consumer Cyclical",
    "consumer non-durables": "Consumer Defensive",
    "utilities": "Utilities",
    "government": "Government / Other",
    "miscellaneous": "Miscellaneous / Other",
}

ALIASES = {
    "information technology": "Technology",
    "technology services": "Technology",
    "electronic technology": "Technology",
    "health care": "Healthcare",
    "financials": "Financial Services",
    "consumer discretionary": "Consumer Cyclical",
    "consumer staples": "Consumer Defensive",
    "materials": "Basic Materials",
    "real estate services": "Real Estate",
}


def upgrade() -> None:
    op.add_column(
        "raw_company_rows",
        sa.Column("sector_canonical", sa.Text(), nullable=True),
    )
    op.add_column(
        "raw_company_rows",
        sa.Column("sector_taxonomy", sa.Text(), nullable=True),
    )
    op.add_column(
        "raw_company_rows",
        sa.Column("sector_mapping_status", sa.Text(), nullable=True),
    )

    _backfill_sector_metadata()

    op.create_index(
        "idx_raw_company_rows_sector_canonical",
        "raw_company_rows",
        ["run_id", "sector_canonical"],
    )
    op.create_index(
        "idx_raw_company_rows_sector_mapping_status",
        "raw_company_rows",
        ["run_id", "sector_mapping_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_raw_company_rows_sector_mapping_status",
        table_name="raw_company_rows",
    )
    op.drop_index(
        "idx_raw_company_rows_sector_canonical",
        table_name="raw_company_rows",
    )
    op.drop_column("raw_company_rows", "sector_mapping_status")
    op.drop_column("raw_company_rows", "sector_taxonomy")
    op.drop_column("raw_company_rows", "sector_canonical")


def _backfill_sector_metadata() -> None:
    raw_company_rows = sa.table(
        "raw_company_rows",
        sa.column("id", sa.BigInteger),
        sa.column("sector", sa.Text),
        sa.column("sector_canonical", sa.Text),
        sa.column("sector_taxonomy", sa.Text),
        sa.column("sector_mapping_status", sa.Text),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(raw_company_rows.c.id, raw_company_rows.c.sector)
    ).mappings()
    for row in rows:
        canonical, status = _normalize(row["sector"])
        bind.execute(
            raw_company_rows.update()
            .where(raw_company_rows.c.id == row["id"])
            .values(
                sector_canonical=canonical,
                sector_taxonomy=TAXONOMY,
                sector_mapping_status=status,
            )
        )


def _normalize(raw_sector: str | None) -> tuple[str, str]:
    text = " ".join(str(raw_sector or "").strip().split())
    if not text:
        return UNKNOWN, "missing"

    canonical_by_key = {sector.casefold(): sector for sector in CANONICAL_SECTORS}
    key = text.casefold()
    if key in canonical_by_key:
        return canonical_by_key[key], "canonical"
    if key in TRADINGVIEW_MAP:
        return TRADINGVIEW_MAP[key], "mapped"
    if key in ALIASES:
        return ALIASES[key], "mapped"
    return UNKNOWN, "unmapped"
