"""Add immutable Setup, lifecycle, transition, and alert decision evidence.

Revision ID: 0079_setup_lifecycle_alert_ev
Revises: 0078_ibmi_constituent_evidence
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0079_setup_lifecycle_alert_ev"
down_revision: str | None = "0078_ibmi_constituent_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_KINDS = (
    "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING', "
    "'REGIME', 'SECTOR', 'CERI', 'IBMI', 'SETUP')"
)
_PREVIOUS_KINDS = (
    "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING', "
    "'REGIME', 'SECTOR', 'CERI', 'IBMI')"
)


def upgrade() -> None:
    for table_name, constraint_name in (
        ("core_calculation_evidence", "ck_core_calculation_evidence_kind"),
        ("core_calculation_current_projections", "ck_core_current_projection_kind"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(constraint_name, table_name, _KINDS)

    op.add_column("setup_signal_snapshots", sa.Column("evidence_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_setup_signal_snapshots_evidence",
        "setup_signal_snapshots",
        "core_calculation_evidence",
        ["evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_setup_signal_snapshots_evidence", "setup_signal_snapshots", ["evidence_id"]
    )

    op.create_table(
        "setup_lifecycle_evaluation_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("setup_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("prior_evaluation_evidence_id", sa.BigInteger()),
        sa.Column("prior_transition_evidence_id", sa.BigInteger()),
        sa.Column("evaluation_run_id", sa.BigInteger()),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("setup_family", sa.String(32), nullable=False),
        sa.Column("decision_session", sa.Date(), nullable=False),
        sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True)),
        sa.Column("calendar_version", sa.String(64)),
        sa.Column("calculation_identity_fingerprint", sa.Text(), nullable=False),
        sa.Column("execution_mode", sa.String(32), nullable=False),
        sa.Column("previous_state", sa.String(32)),
        sa.Column("output_state", sa.String(32), nullable=False),
        sa.Column("output_phase", sa.String(64), nullable=False),
        sa.Column("transition_eligible", sa.Boolean(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column(
            "counters_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.Text(), nullable=False),
        sa.Column("evidence_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["setup_evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["prior_evaluation_evidence_id"],
            ["setup_lifecycle_evaluation_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["setup_lifecycle_evaluation_runs.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("evidence_key", name="uq_setup_lifecycle_evaluation_evidence_key"),
    )
    op.create_index(
        "idx_setup_lifecycle_evaluation_evidence_chain",
        "setup_lifecycle_evaluation_evidence",
        ["ticker", "timeframe", "setup_family", "decision_session"],
    )
    op.create_index(
        "idx_setup_lifecycle_evaluation_setup",
        "setup_lifecycle_evaluation_evidence",
        ["setup_evidence_id"],
    )

    op.create_table(
        "setup_lifecycle_transition_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("evaluation_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("setup_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("prior_transition_evidence_id", sa.BigInteger()),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("setup_family", sa.String(32), nullable=False),
        sa.Column("effective_session", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("from_state", sa.String(32)),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("from_phase", sa.String(64)),
        sa.Column("to_phase", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column(
            "reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.Text(), nullable=False),
        sa.Column("evidence_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_evidence_id"],
            ["setup_lifecycle_evaluation_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["setup_evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["prior_transition_evidence_id"],
            ["setup_lifecycle_transition_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("evaluation_evidence_id", name="uq_setup_transition_evaluation"),
        sa.UniqueConstraint("evidence_key", name="uq_setup_transition_evidence_key"),
    )
    op.create_index(
        "idx_setup_lifecycle_transition_chain",
        "setup_lifecycle_transition_evidence",
        ["ticker", "timeframe", "setup_family", "effective_session"],
    )
    op.create_foreign_key(
        "fk_setup_lifecycle_evaluation_prior_transition",
        "setup_lifecycle_evaluation_evidence",
        "setup_lifecycle_transition_evidence",
        ["prior_transition_evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    for column_name, target in (
        ("latest_evaluation_evidence_id", "setup_lifecycle_evaluation_evidence"),
        ("latest_transition_evidence_id", "setup_lifecycle_transition_evidence"),
    ):
        op.add_column("setup_lifecycle_episodes", sa.Column(column_name, sa.BigInteger()))
        op.create_foreign_key(
            f"fk_setup_lifecycle_episodes_{column_name}",
            "setup_lifecycle_episodes",
            target,
            [column_name],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "idx_setup_lifecycle_episode_latest_evidence",
        "setup_lifecycle_episodes",
        ["latest_evaluation_evidence_id", "latest_transition_evidence_id"],
    )

    op.add_column("setup_lifecycle_events", sa.Column("transition_evidence_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_setup_lifecycle_events_transition_evidence",
        "setup_lifecycle_events",
        "setup_lifecycle_transition_evidence",
        ["transition_evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_setup_lifecycle_events_transition_evidence",
        "setup_lifecycle_events",
        ["transition_evidence_id"],
    )

    op.create_table(
        "signal_alert_rule_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_row_id", sa.BigInteger()),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.Text(), nullable=False),
        sa.Column("evidence_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["rule_row_id"], ["signal_alert_rules.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("evidence_key", name="uq_signal_alert_rule_evidence_key"),
    )
    op.create_index(
        "idx_signal_alert_rule_evidence_rule", "signal_alert_rule_evidence", ["rule_id", "id"]
    )

    op.create_table(
        "signal_alert_decision_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("rule_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("setup_evidence_id", sa.BigInteger()),
        sa.Column("lifecycle_evaluation_evidence_id", sa.BigInteger()),
        sa.Column("lifecycle_transition_evidence_id", sa.BigInteger()),
        sa.Column("cooldown_predecessor_evidence_id", sa.BigInteger()),
        sa.Column("dedup_predecessor_evidence_id", sa.BigInteger()),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("effective_session", sa.Date(), nullable=False),
        sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True)),
        sa.Column("calendar_version", sa.String(64)),
        sa.Column("source_event_key", sa.Text(), nullable=False),
        sa.Column("semantic_key", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column(
            "reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_fingerprint", sa.Text(), nullable=False),
        sa.Column("evidence_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "decision IN ('GENERATED', 'SUPPRESSED_COOLDOWN', 'SUPPRESSED_DEDUP', 'INELIGIBLE')",
            name="ck_signal_alert_decision_evidence_decision",
        ),
        sa.ForeignKeyConstraint(
            ["rule_evidence_id"], ["signal_alert_rule_evidence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["setup_evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["lifecycle_evaluation_evidence_id"],
            ["setup_lifecycle_evaluation_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lifecycle_transition_evidence_id"],
            ["setup_lifecycle_transition_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cooldown_predecessor_evidence_id"],
            ["signal_alert_decision_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dedup_predecessor_evidence_id"],
            ["signal_alert_decision_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("evidence_key", name="uq_signal_alert_decision_evidence_key"),
    )
    op.create_index(
        "idx_signal_alert_decision_temporal",
        "signal_alert_decision_evidence",
        ["ticker", "timeframe", "effective_session"],
    )
    op.create_index(
        "idx_signal_alert_decision_semantic",
        "signal_alert_decision_evidence",
        ["semantic_key", "effective_session"],
    )

    op.add_column("signal_alert_events", sa.Column("decision_evidence_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_signal_alert_events_decision_evidence",
        "signal_alert_events",
        "signal_alert_decision_evidence",
        ["decision_evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_signal_alert_events_decision_evidence",
        "signal_alert_events",
        ["decision_evidence_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_signal_alert_events_decision_evidence", "signal_alert_events", type_="unique"
    )
    op.drop_constraint(
        "fk_signal_alert_events_decision_evidence", "signal_alert_events", type_="foreignkey"
    )
    op.drop_column("signal_alert_events", "decision_evidence_id")
    op.drop_table("signal_alert_decision_evidence")
    op.drop_table("signal_alert_rule_evidence")

    op.drop_index(
        "idx_setup_lifecycle_events_transition_evidence", table_name="setup_lifecycle_events"
    )
    op.drop_constraint(
        "fk_setup_lifecycle_events_transition_evidence",
        "setup_lifecycle_events",
        type_="foreignkey",
    )
    op.drop_column("setup_lifecycle_events", "transition_evidence_id")

    op.drop_index(
        "idx_setup_lifecycle_episode_latest_evidence", table_name="setup_lifecycle_episodes"
    )
    for column_name in ("latest_transition_evidence_id", "latest_evaluation_evidence_id"):
        op.drop_constraint(
            f"fk_setup_lifecycle_episodes_{column_name}",
            "setup_lifecycle_episodes",
            type_="foreignkey",
        )
        op.drop_column("setup_lifecycle_episodes", column_name)

    op.drop_constraint(
        "fk_setup_lifecycle_evaluation_prior_transition",
        "setup_lifecycle_evaluation_evidence",
        type_="foreignkey",
    )
    op.drop_table("setup_lifecycle_transition_evidence")
    op.drop_table("setup_lifecycle_evaluation_evidence")

    op.drop_index("idx_setup_signal_snapshots_evidence", table_name="setup_signal_snapshots")
    op.drop_constraint(
        "fk_setup_signal_snapshots_evidence", "setup_signal_snapshots", type_="foreignkey"
    )
    op.drop_column("setup_signal_snapshots", "evidence_id")

    for table_name, constraint_name in (
        ("core_calculation_current_projections", "ck_core_current_projection_kind"),
        ("core_calculation_evidence", "ck_core_calculation_evidence_kind"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(constraint_name, table_name, _PREVIOUS_KINDS)
