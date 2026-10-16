"""Enforce lifecycle and ticker/session readiness cross-field invariants."""

from collections.abc import Sequence

from alembic import op

revision: str = "0091_readiness_constraints"
down_revision: str | None = "0090_market_data_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_instrument_lifecycle_revision_positive",
        "instrument_lifecycle_records",
        "revision > 0",
    )
    op.create_check_constraint(
        "ck_instrument_lifecycle_evidence_nonempty",
        "instrument_lifecycle_records",
        "jsonb_typeof(evidence_json) = 'object' AND evidence_json <> '{}'::jsonb",
    )
    op.create_check_constraint(
        "ck_instrument_lifecycle_terminal_boundary",
        "instrument_lifecycle_records",
        "lifecycle_state NOT IN ('INACTIVE', 'MERGED', 'DELISTED') OR "
        "effective_date IS NOT NULL OR last_trading_date IS NOT NULL OR "
        "contract_valid_to IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_instrument_lifecycle_trading_date_order",
        "instrument_lifecycle_records",
        "effective_date IS NULL OR last_trading_date IS NULL OR "
        "last_trading_date <= effective_date",
    )
    op.create_check_constraint(
        "ck_instrument_lifecycle_contract_date_order",
        "instrument_lifecycle_records",
        "contract_valid_from IS NULL OR contract_valid_to IS NULL OR "
        "contract_valid_from <= contract_valid_to",
    )
    op.create_check_constraint(
        "ck_market_data_session_revision_positive",
        "market_data_session_dispositions",
        "revision > 0",
    )
    op.create_check_constraint(
        "ck_market_data_session_evidence_nonempty",
        "market_data_session_dispositions",
        "jsonb_typeof(evidence_json) = 'object' AND evidence_json <> '{}'::jsonb",
    )
    op.create_check_constraint(
        "ck_market_data_session_bar_not_after_expected",
        "market_data_session_dispositions",
        "latest_bar_session IS NULL OR latest_bar_session <= expected_session",
    )
    op.create_check_constraint(
        "ck_market_data_session_eligibility",
        "market_data_session_dispositions",
        "(disposition = 'READY' AND technical_eligible AND downstream_eligible) OR "
        "(disposition <> 'READY' AND NOT technical_eligible AND NOT downstream_eligible)",
    )
    op.create_check_constraint(
        "ck_market_data_session_ready_fresh",
        "market_data_session_dispositions",
        "disposition <> 'READY' OR "
        "(latest_bar_session IS NOT NULL AND latest_bar_session = expected_session)",
    )
    op.create_check_constraint(
        "ck_market_data_session_terminal_lifecycle",
        "market_data_session_dispositions",
        "disposition <> 'TERMINAL_INACTIVE' OR "
        "lifecycle_state IN ('INACTIVE', 'MERGED', 'DELISTED')",
    )


def downgrade() -> None:
    for name in (
        "ck_market_data_session_terminal_lifecycle",
        "ck_market_data_session_ready_fresh",
        "ck_market_data_session_eligibility",
        "ck_market_data_session_bar_not_after_expected",
        "ck_market_data_session_evidence_nonempty",
        "ck_market_data_session_revision_positive",
    ):
        op.drop_constraint(name, "market_data_session_dispositions", type_="check")
    for name in (
        "ck_instrument_lifecycle_contract_date_order",
        "ck_instrument_lifecycle_trading_date_order",
        "ck_instrument_lifecycle_terminal_boundary",
        "ck_instrument_lifecycle_evidence_nonempty",
        "ck_instrument_lifecycle_revision_positive",
    ):
        op.drop_constraint(name, "instrument_lifecycle_records", type_="check")
