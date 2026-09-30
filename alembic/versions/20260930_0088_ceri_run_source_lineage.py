# ruff: noqa: E501
"""Add run-local CERI source membership and contain incident runs 6/7.

Migration invariants:

* immutable provider rows retain their original ``ingestion_run_id``;
* backfill attaches a row only to that original ingestion run;
* historical runs 6 and 7 remain terminal and are never resumed;
* incident evidence is append-only excluded and failed current pointers are
  rebuilt when a prior eligible candidate exists, otherwise removed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0088_ceri_run_source_lineage"
down_revision: str | None = "0087_pipeline_dependencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ceri_ingestion_run_source_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_record_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("ingestion_outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "normalization_state",
            sa.String(length=32),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("normalization_reason", sa.Text(), nullable=True),
        sa.Column("normalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ingestion_outcome IN "
            "('INSERTED', 'DEDUPLICATED', 'CORRECTED', 'REPLACED', 'QUARANTINED')",
            name="ck_ceri_ingestion_source_outcome",
        ),
        sa.CheckConstraint(
            "normalization_state IN "
            "('PENDING', 'NORMALIZED', 'REUSED', 'QUARANTINED', 'REJECTED')",
            name="ck_ceri_ingestion_source_normalization_state",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], ["ceri_ingestion_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"], ["ceri_source_records.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ingestion_run_id",
            "source_record_id",
            name="uq_ceri_ingestion_source_membership",
        ),
    )
    op.create_index(
        "ix_ceri_ingestion_source_membership_run",
        "ceri_ingestion_run_source_records",
        ["ingestion_run_id", "source_record_id"],
    )
    op.create_index(
        "ix_ceri_ingestion_source_membership_ticker",
        "ceri_ingestion_run_source_records",
        ["ticker", "dataset", "ingestion_run_id"],
    )

    # Conservative historical backfill: only original ownership is known with
    # certainty.  Never infer that a source belonged to some unrelated run.
    op.execute(
        sa.text(
            """
            INSERT INTO ceri_ingestion_run_source_records (
                ingestion_run_id, source_record_id, ticker, provider, dataset,
                ingestion_outcome, normalization_state, normalization_reason,
                normalized_at
            )
            SELECT
                s.ingestion_run_id,
                s.id,
                upper(nullif(s.company_hint_json->>'ticker', '')),
                s.provider,
                s.dataset,
                CASE
                    WHEN s.quarantine_reason IS NOT NULL THEN 'QUARANTINED'
                    WHEN s.correction_type IS NOT NULL THEN 'CORRECTED'
                    ELSE 'INSERTED'
                END,
                CASE
                    WHEN s.quarantine_reason IS NOT NULL THEN 'QUARANTINED'
                    WHEN EXISTS (SELECT 1 FROM ceri_estimate_snapshots n WHERE n.source_record_id = s.id)
                      OR EXISTS (SELECT 1 FROM ceri_earnings_actuals n WHERE n.source_record_id = s.id)
                      OR EXISTS (SELECT 1 FROM ceri_guidance_events n WHERE n.source_record_id = s.id)
                      OR EXISTS (SELECT 1 FROM ceri_catalyst_sources n WHERE n.source_record_id = s.id)
                    THEN 'REUSED'
                    ELSE 'PENDING'
                END,
                s.quarantine_reason,
                CASE
                    WHEN s.quarantine_reason IS NOT NULL
                      OR EXISTS (SELECT 1 FROM ceri_estimate_snapshots n WHERE n.source_record_id = s.id)
                      OR EXISTS (SELECT 1 FROM ceri_earnings_actuals n WHERE n.source_record_id = s.id)
                      OR EXISTS (SELECT 1 FROM ceri_guidance_events n WHERE n.source_record_id = s.id)
                      OR EXISTS (SELECT 1 FROM ceri_catalyst_sources n WHERE n.source_record_id = s.id)
                    THEN now() ELSE NULL
                END
            FROM ceri_source_records s
            WHERE s.ingestion_run_id IS NOT NULL
            ON CONFLICT ON CONSTRAINT uq_ceri_ingestion_source_membership DO NOTHING
            """
        )
    )

    # Append-only containment for the two forensic incident runs.
    op.execute(
        sa.text(
            """
            INSERT INTO ceri_evidence_dispositions (
                ceri_snapshot_id, disposition, reason_code, incident_reference,
                actor_source, notes, metadata_json, event_fingerprint
            )
            SELECT
                s.id, 'EXCLUDED', 'HISTORICAL_PIPELINE_UNSUCCESSFUL',
                'ceri-lineage-incident-runs-6-7', 'migration-0088',
                'Run remains historical; derived evidence is quarantined.',
                jsonb_build_object(
                    'pipeline_run_id', p.id,
                    'pipeline_status', p.status,
                    'migration', '0088_ceri_run_source_lineage'
                ),
                '0088:exclude:pipeline:' || p.id::text || ':snapshot:' || s.id::text
            FROM ceri_score_snapshots s
            JOIN market_calculation_contexts c ON c.id = s.calculation_context_id
            JOIN pipeline_runs p ON (
                p.id = c.pipeline_run_id
                OR (c.pipeline_run_id IS NULL AND p.upload_run_id = s.run_id)
            )
            WHERE p.id IN (6, 7)
            ON CONFLICT ON CONSTRAINT uq_ceri_evidence_dispositions_event_fingerprint
            DO NOTHING
            """
        )
    )

    # If an incident pointer has another eligible certified candidate in the
    # same projection scope, select it deterministically before deleting any
    # pointer for which no valid authority remains.
    op.execute(
        sa.text(
            """
            WITH affected AS (
                SELECT p.id AS projection_id, p.run_id, p.ticker
                FROM core_calculation_current_projections p
                WHERE p.artifact_kind = 'CERI'
                  AND EXISTS (
                      SELECT 1
                      FROM ceri_score_snapshots bad
                      JOIN market_calculation_contexts bc
                        ON bc.id = bad.calculation_context_id
                      WHERE bad.evidence_id = p.evidence_id
                        AND (
                            bc.pipeline_run_id IN (6, 7)
                            OR EXISTS (
                                SELECT 1 FROM pipeline_runs bp
                                WHERE bp.id IN (6, 7) AND bp.upload_run_id = bad.run_id
                            )
                        )
                  )
            ), candidates AS (
                SELECT
                    a.projection_id,
                    e2.id AS evidence_id,
                    row_number() OVER (
                        PARTITION BY a.projection_id
                        ORDER BY e2.calculated_at DESC, e2.id DESC
                    ) AS candidate_rank
                FROM affected a
                JOIN core_calculation_evidence e2 ON true
                JOIN ceri_score_snapshots s2 ON s2.evidence_id = e2.id
                LEFT JOIN market_calculation_contexts c2 ON c2.id = s2.calculation_context_id
                LEFT JOIN pipeline_runs pr2 ON pr2.id = c2.pipeline_run_id
                WHERE e2.artifact_kind = 'CERI'
                  AND e2.run_id IS NOT DISTINCT FROM a.run_id
                  AND e2.ticker IS NOT DISTINCT FROM a.ticker
                  AND (
                      (
                          c2.pipeline_run_id IS NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM pipeline_runs badp
                              WHERE badp.upload_run_id = e2.run_id
                                AND badp.status IN ('FAILED','CANCELLED','BLOCKED','PARTIAL')
                          )
                      )
                      OR pr2.status NOT IN ('FAILED','CANCELLED','BLOCKED','PARTIAL')
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM LATERAL (
                          SELECT d.disposition
                          FROM ceri_evidence_dispositions d
                          WHERE d.ceri_snapshot_id = s2.id
                          ORDER BY d.created_at DESC, d.id DESC
                          LIMIT 1
                      ) latest WHERE latest.disposition = 'EXCLUDED'
                  )
            )
            UPDATE core_calculation_current_projections p
            SET evidence_id = candidate.evidence_id, updated_at = now()
            FROM candidates candidate
            WHERE candidate.projection_id = p.id
              AND candidate.candidate_rank = 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM core_calculation_current_projections p
            WHERE p.artifact_kind = 'CERI'
              AND EXISTS (
                  SELECT 1
                  FROM ceri_score_snapshots bad
                  JOIN market_calculation_contexts bc ON bc.id = bad.calculation_context_id
                  WHERE bad.evidence_id = p.evidence_id
                    AND (
                        bc.pipeline_run_id IN (6, 7)
                        OR EXISTS (
                            SELECT 1 FROM pipeline_runs bp
                            WHERE bp.id IN (6, 7) AND bp.upload_run_id = bad.run_id
                        )
                    )
              )
            """
        )
    )

    # Preserve checkpoints and counters while closing orphan active state.
    op.execute(
        sa.text(
            """
            UPDATE ceri_processing_runs r
            SET status = CASE WHEN p.status = 'CANCELLED' THEN 'CANCELLED' ELSE 'FAILED' END,
                completed_at = COALESCE(r.completed_at, now()),
                heartbeat_at = now(),
                execution_token = NULL,
                errors_json = COALESCE(r.errors_json, '{}'::jsonb) ||
                    jsonb_build_object(
                        'parent_pipeline_terminal_state', p.status,
                        'parent_pipeline_run_id', p.id,
                        'reconciled_by', '0088_ceri_run_source_lineage'
                    )
            FROM pipeline_runs p
            WHERE p.id IN (6, 7)
              AND r.status IN ('PENDING', 'QUEUED', 'RUNNING')
              AND (
                  (r.id = 229 AND p.id = 6)
                  OR r.deterministic_request_key LIKE ('ceri:pipeline:' || p.id::text || ':%')
                  OR r.scope_json->>'run_id' = p.upload_run_id::text
              )
            """
        )
    )


def downgrade() -> None:
    # Source facts and normalized artifacts are never modified by this migration.
    op.execute(
        sa.text(
            "DELETE FROM ceri_evidence_dispositions WHERE actor_source = 'migration-0088'"
        )
    )
    op.drop_index(
        "ix_ceri_ingestion_source_membership_ticker",
        table_name="ceri_ingestion_run_source_records",
    )
    op.drop_index(
        "ix_ceri_ingestion_source_membership_run",
        table_name="ceri_ingestion_run_source_records",
    )
    op.drop_table("ceri_ingestion_run_source_records")
