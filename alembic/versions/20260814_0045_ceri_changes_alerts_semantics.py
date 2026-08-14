"""add CERI comparison, business-change, and alert-validity semantics

Revision ID: 0045_ceri_changes_alerts_semantics
Revises: 0044_ceri_controlled_replays
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045_ceri_changes_alerts_semantics"
down_revision: str | None = "0044_ceri_controlled_replays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ceri_score_snapshots", sa.Column("evidence_contract_version", sa.Text()))
    op.add_column("ceri_score_snapshots", sa.Column("comparison_state", sa.String(64)))
    op.add_column("ceri_score_snapshots", sa.Column("comparison_snapshot_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_ceri_score_snapshots_comparison_snapshot",
        "ceri_score_snapshots",
        "ceri_score_snapshots",
        ["comparison_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ceri_score_snapshots_comparison_state",
        "ceri_score_snapshots",
        ["comparison_state"],
    )

    op.add_column("ceri_change_events", sa.Column("guidance_event_id", sa.BigInteger()))
    op.add_column("ceri_change_events", sa.Column("importance", sa.String(32)))
    op.add_column("ceri_change_events", sa.Column("signal_class", sa.String(32)))
    op.add_column("ceri_change_events", sa.Column("comparison_state", sa.String(64)))
    op.create_foreign_key(
        "fk_ceri_change_events_guidance_event",
        "ceri_change_events",
        "ceri_guidance_events",
        ["guidance_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ceri_change_events_semantic_filters",
        "ceri_change_events",
        ["importance", "signal_class", "comparison_state"],
    )

    op.add_column("ceri_alert_events", sa.Column("importance", sa.String(32)))
    op.add_column("ceri_alert_events", sa.Column("signal_class", sa.String(32)))
    op.add_column("ceri_alert_events", sa.Column("validity_classification", sa.String(32)))
    op.add_column("ceri_alert_events", sa.Column("invalidated_reason", sa.Text()))
    op.add_column("ceri_alert_events", sa.Column("invalidated_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_ceri_alert_events_validity",
        "ceri_alert_events",
        ["validity_classification", "status"],
    )

    # 0043 changed which persisted estimate observations were eligible evidence.
    # That implementation boundary was not represented in the old score identity.
    op.execute(
        """
        UPDATE ceri_score_snapshots
        SET evidence_contract_version = CASE
            WHEN run_id >= 103 OR controlled_replay_id IS NOT NULL
                THEN 'ceri-evidence-contract-v2'
            ELSE 'ceri-evidence-contract-v1'
        END
        """
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   lag(id) OVER (
                       PARTITION BY company_id
                       ORDER BY as_of_session, cutoff_at, id
                   ) AS prior_id
            FROM ceri_score_snapshots
            WHERE controlled_replay_id IS NULL
        )
        UPDATE ceri_score_snapshots current
        SET comparison_snapshot_id = ordered.prior_id
        FROM ordered
        WHERE current.id = ordered.id
        """
    )
    op.execute(
        """
        UPDATE ceri_score_snapshots current
        SET comparison_state = CASE
            WHEN prior.id IS NULL THEN 'NO_PRIOR_COMPARABLE_SNAPSHOT'
            WHEN prior.calculation_version <> current.calculation_version
                THEN 'MODEL_VERSION_TRANSITION'
            WHEN prior.config_hash <> current.config_hash THEN 'CONFIG_TRANSITION'
            WHEN prior.evidence_contract_version <> current.evidence_contract_version
                THEN 'EVIDENCE_CONTRACT_TRANSITION'
            ELSE 'COMPARABLE'
        END
        FROM ceri_score_snapshots prior
        WHERE prior.id = current.comparison_snapshot_id
        """
    )
    op.execute(
        """
        UPDATE ceri_score_snapshots
        SET comparison_state = 'NO_PRIOR_COMPARABLE_SNAPSHOT'
        WHERE comparison_state IS NULL
        """
    )

    op.execute(
        """
        UPDATE ceri_change_events change
        SET comparison_state = CASE
            WHEN change.from_snapshot_id IS NULL AND change.to_snapshot_id IS NOT NULL
                THEN 'NO_PRIOR_COMPARABLE_SNAPSHOT'
            WHEN change.from_snapshot_id IS NULL OR change.to_snapshot_id IS NULL
                THEN 'COMPARABLE'
            WHEN prior.calculation_version <> current.calculation_version
                THEN 'MODEL_VERSION_TRANSITION'
            WHEN prior.config_hash <> current.config_hash THEN 'CONFIG_TRANSITION'
            WHEN prior.evidence_contract_version <> current.evidence_contract_version
                THEN 'EVIDENCE_CONTRACT_TRANSITION'
            ELSE 'COMPARABLE'
        END
        FROM ceri_score_snapshots prior, ceri_score_snapshots current
        WHERE prior.id = change.from_snapshot_id
          AND current.id = change.to_snapshot_id
        """
    )
    op.execute(
        """
        UPDATE ceri_change_events
        SET comparison_state = CASE
                WHEN from_snapshot_id IS NULL AND to_snapshot_id IS NOT NULL
                    THEN 'NO_PRIOR_COMPARABLE_SNAPSHOT'
                ELSE 'COMPARABLE'
            END
        WHERE comparison_state IS NULL
        """
    )
    op.execute(
        """
        UPDATE ceri_change_events
        SET importance = CASE
                WHEN change_type IN (
                    'DATA_STALE', 'DATA_REFRESHED', 'CONFLICT_OPENED',
                    'CONFLICT_RESOLVED', 'MODEL_VERSION_TRANSITION',
                    'CONFIG_TRANSITION', 'EVIDENCE_CONTRACT_TRANSITION',
                    'BASELINE_ESTABLISHED', 'EVENT_COMPLETED',
                    'EVENT_CANCELLED', 'EVENT_RESOLVED', 'RISK_RESOLVED',
                    'CATALYST_CANCELLED', 'CATALYST_RESOLVED'
                ) THEN 'INFO'
                WHEN change_type = 'NEW_BINARY_EVENT' THEN 'URGENT'
                WHEN change_type IN ('RISK_ESCALATED', 'CATALYST_DELAYED')
                    THEN 'IMPORTANT'
                ELSE 'NOTABLE'
            END,
            signal_class = CASE
                WHEN change_type IN (
                    'DATA_STALE', 'DATA_REFRESHED', 'CONFLICT_OPENED',
                    'CONFLICT_RESOLVED', 'MODEL_VERSION_TRANSITION',
                    'CONFIG_TRANSITION', 'EVIDENCE_CONTRACT_TRANSITION',
                    'BASELINE_ESTABLISHED'
                ) THEN 'DATA_QUALITY'
                WHEN change_type IN ('NEW_BINARY_EVENT', 'RISK_ESCALATED', 'CATALYST_DELAYED')
                    THEN 'RISK'
                WHEN change_type IN (
                    'REVISION_UP', 'REVISION_ACCELERATED', 'GUIDANCE_RAISED',
                    'CATALYST_CONFIRMED', 'OPPORTUNITY_UPGRADED', 'BECAME_RATED'
                ) THEN 'POSITIVE'
                WHEN change_type IN (
                    'REVISION_DOWN', 'REVISION_DECELERATED', 'GUIDANCE_LOWERED',
                    'GUIDANCE_WITHDRAWN', 'OPPORTUNITY_DOWNGRADED', 'BECAME_UNRATED'
                ) THEN 'NEGATIVE'
                ELSE 'NEUTRAL'
            END
        """
    )
    op.alter_column("ceri_score_snapshots", "evidence_contract_version", nullable=False)
    op.alter_column("ceri_score_snapshots", "comparison_state", nullable=False)
    op.alter_column("ceri_change_events", "importance", nullable=False)
    op.alter_column("ceri_change_events", "signal_class", nullable=False)
    op.alter_column("ceri_change_events", "comparison_state", nullable=False)

    op.execute(
        """
        UPDATE ceri_alert_events alert
        SET importance = change.importance,
            signal_class = change.signal_class
        FROM ceri_change_events change
        WHERE change.id = alert.source_change_event_id
        """
    )
    op.execute(
        """
        UPDATE ceri_alert_events
        SET importance = COALESCE(importance, 'NOTABLE'),
            signal_class = COALESCE(signal_class, 'NEUTRAL')
        """
    )
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (company_id) company_id, id
            FROM ceri_score_snapshots
            WHERE controlled_replay_id IS NULL
            ORDER BY company_id, as_of_session DESC, cutoff_at DESC, id DESC
        ), classified AS (
            SELECT alert.id,
                   CASE
                       WHEN change.id IS NULL OR alert.alert_rule_id IS NULL THEN 'ORPHANED'
                       WHEN change.comparison_state <> 'COMPARABLE' THEN 'INVALID_LEGACY'
                       WHEN change.change_type LIKE 'OPPORTUNITY_%'
                            AND (change.from_snapshot_id IS NULL OR change.to_snapshot_id IS NULL)
                           THEN 'INVALID_LEGACY'
                       WHEN latest.id = change.to_snapshot_id OR revision.is_current IS TRUE
                           THEN 'VALID_CURRENT'
                       ELSE 'VALID_HISTORICAL'
                   END AS validity
            FROM ceri_alert_events alert
            LEFT JOIN ceri_change_events change ON change.id = alert.source_change_event_id
            LEFT JOIN latest ON latest.company_id = change.company_id
            LEFT JOIN ceri_catalyst_event_revisions revision
                ON revision.id = change.catalyst_revision_id
        )
        UPDATE ceri_alert_events alert
        SET validity_classification = classified.validity,
            status = CASE
                WHEN classified.validity IN ('ORPHANED', 'INVALID_LEGACY', 'DUPLICATE')
                    THEN 'INVALIDATED'
                ELSE alert.status
            END,
            invalidated_reason = CASE classified.validity
                WHEN 'ORPHANED' THEN 'Underlying change-event or alert-rule lineage is missing.'
                WHEN 'INVALID_LEGACY' THEN 'Invalid under corrected comparison semantics.'
                WHEN 'DUPLICATE' THEN 'Duplicate deterministic alert identity.'
                ELSE NULL
            END,
            invalidated_at = CASE
                WHEN classified.validity IN ('ORPHANED', 'INVALID_LEGACY', 'DUPLICATE')
                    THEN now()
                ELSE NULL
            END
        FROM classified
        WHERE classified.id = alert.id
        """
    )
    op.alter_column("ceri_alert_events", "importance", nullable=False)
    op.alter_column("ceri_alert_events", "signal_class", nullable=False)
    op.alter_column("ceri_alert_events", "validity_classification", nullable=False)
    op.create_check_constraint(
        "ck_ceri_alert_events_change_lineage",
        "ceri_alert_events",
        "source_change_event_id IS NOT NULL AND alert_rule_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ceri_alert_events_change_lineage", "ceri_alert_events", type_="check")
    op.drop_index("ix_ceri_alert_events_validity", table_name="ceri_alert_events")
    op.drop_column("ceri_alert_events", "invalidated_at")
    op.drop_column("ceri_alert_events", "invalidated_reason")
    op.drop_column("ceri_alert_events", "validity_classification")
    op.drop_column("ceri_alert_events", "signal_class")
    op.drop_column("ceri_alert_events", "importance")
    op.drop_index("ix_ceri_change_events_semantic_filters", table_name="ceri_change_events")
    op.drop_constraint(
        "fk_ceri_change_events_guidance_event", "ceri_change_events", type_="foreignkey"
    )
    op.drop_column("ceri_change_events", "comparison_state")
    op.drop_column("ceri_change_events", "signal_class")
    op.drop_column("ceri_change_events", "importance")
    op.drop_column("ceri_change_events", "guidance_event_id")
    op.drop_index("ix_ceri_score_snapshots_comparison_state", table_name="ceri_score_snapshots")
    op.drop_constraint(
        "fk_ceri_score_snapshots_comparison_snapshot",
        "ceri_score_snapshots",
        type_="foreignkey",
    )
    op.drop_column("ceri_score_snapshots", "comparison_snapshot_id")
    op.drop_column("ceri_score_snapshots", "comparison_state")
    op.drop_column("ceri_score_snapshots", "evidence_contract_version")
