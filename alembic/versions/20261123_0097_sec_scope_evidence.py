"""Persist immutable, exact SEC guidance selection evidence."""

import sqlalchemy as sa

from alembic import op

revision = "0097_sec_scope_evidence"
down_revision = "0096_ceri_native_context"
branch_labels = None
depends_on = None

TABLE = "ceri_sec_scope_evidence"


def upgrade():
    op.create_table(
        TABLE,
        sa.Column("cik", sa.String(10), primary_key=True),
        sa.Column("dataset", sa.String(64), primary_key=True),
        sa.Column("processor_signature", sa.String(96), primary_key=True),
        sa.Column("generation", sa.BigInteger(), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column(
            "ingestion_run_id",
            sa.BigInteger(),
            sa.ForeignKey("ceri_ingestion_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "scope_id",
            sa.String(64),
            sa.ForeignKey("work_scope_records.scope_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND dataset = 'guidance'", name="ck_sec_scope_issuer"
        ),
        sa.CheckConstraint(
            "generation > 0 AND ("
            "(ordinal = 0 AND kind = 'OPEN') OR (ordinal = 1 AND kind = 'SELECTED') OR "
            "(ordinal > 1 AND kind IN ('DOCUMENT_OUTCOME', 'ATTRIBUTION_REFUSAL')))",
            name="ck_sec_scope_event_order",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence::jsonb) = 'object' AND "
            "evidence_sha256 = encode(sha256(convert_to(evidence, 'UTF8')), 'hex')",
            name="ck_sec_scope_evidence_seal",
        ),
    )
    op.create_index(
        "uq_sec_scope_open_run",
        TABLE,
        ["ingestion_run_id", "cik", "dataset", "processor_signature"],
        unique=True,
        postgresql_where=sa.text("kind = 'OPEN'"),
    )
    op.execute("""
        CREATE FUNCTION reject_sec_scope_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Committed SEC scope evidence is immutable' USING ERRCODE = '23000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER sec_scope_evidence_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON ceri_sec_scope_evidence FOR EACH STATEMENT
        EXECUTE FUNCTION reject_sec_scope_evidence_mutation()
    """)


def downgrade():
    # A populated ledger cannot be removed without losing certified evidence.
    op.execute("LOCK TABLE ceri_sec_scope_evidence IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ceri_sec_scope_evidence) THEN
                RAISE EXCEPTION 'SEC scope evidence must be retained';
            END IF;
        END;
        $$
    """)
    op.drop_table(TABLE)
    op.execute("DROP FUNCTION reject_sec_scope_evidence_mutation()")
