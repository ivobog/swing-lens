"""add explicit SEC processor promotion and applicability state

Revision ID: 0050_sec_processor_promotion
Revises: 0049_winner_jobs_reliability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0050_sec_processor_promotion"
down_revision: str | None = "0049_winner_jobs_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V2_SIGNATURE = "sec-guidance:948beb114caa8da9"
V3_SIGNATURE = "sec-guidance:eed017654682a0c9"


def upgrade() -> None:
    op.add_column(
        "ceri_companies",
        sa.Column(
            "sec_applicability",
            sa.String(length=32),
            server_default="REQUIRED",
            nullable=False,
        ),
    )
    op.add_column(
        "ceri_companies", sa.Column("sec_applicability_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "ceri_companies",
        sa.Column("sec_applicability_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_ceri_companies_sec_applicability",
        "ceri_companies",
        "sec_applicability IN ('REQUIRED', 'NOT_APPLICABLE')",
    )
    op.create_table(
        "ceri_sec_processor_releases",
        sa.Column("processor_signature", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("deployed_git_sha", sa.Text(), nullable=True),
        sa.Column(
            "certification_evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("certified_by", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DEPLOYED', 'CERTIFIED', 'ACTIVE', 'RETIRED')",
            name="ck_ceri_sec_processor_releases_status",
        ),
        sa.PrimaryKeyConstraint("processor_signature"),
    )
    op.create_index(
        "ix_ceri_sec_processor_releases_status",
        "ceri_sec_processor_releases",
        ["status"],
    )
    op.create_index(
        "uq_ceri_sec_processor_releases_active",
        "ceri_sec_processor_releases",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO ceri_sec_processor_releases
                (processor_signature, status, deployed_git_sha,
                 certification_evidence_json, certified_at, certified_by,
                 activated_at, activated_by)
            VALUES
                (:v2, 'ACTIVE', :v2_sha,
                 CAST(:v2_evidence AS jsonb), CURRENT_TIMESTAMP, 'migration-0050',
                 CURRENT_TIMESTAMP, 'migration-0050'),
                (:v3, 'DEPLOYED', :v3_sha,
                 CAST(:v3_evidence AS jsonb), NULL, NULL, NULL, NULL)
            """
        ).bindparams(
            v2=V2_SIGNATURE,
            v2_sha="1127a7b5ba1149e01d8921915a2b7914e82f8781",
            v2_evidence='{"source":"run111_112_sec_recovery_final_20260818T184419Z","certified_tickers":193,"migration_seed":true}',
            v3=V3_SIGNATURE,
            v3_sha="52e7e80f98bae5cb957b368e042e7cc3727ddb22",
            v3_evidence='{"migration_seed":true}',
        )
    )


def downgrade() -> None:
    op.drop_index(
        "uq_ceri_sec_processor_releases_active",
        table_name="ceri_sec_processor_releases",
    )
    op.drop_index(
        "ix_ceri_sec_processor_releases_status",
        table_name="ceri_sec_processor_releases",
    )
    op.drop_table("ceri_sec_processor_releases")
    op.drop_constraint(
        "ck_ceri_companies_sec_applicability", "ceri_companies", type_="check"
    )
    op.drop_column("ceri_companies", "sec_applicability_updated_at")
    op.drop_column("ceri_companies", "sec_applicability_reason")
    op.drop_column("ceri_companies", "sec_applicability")
