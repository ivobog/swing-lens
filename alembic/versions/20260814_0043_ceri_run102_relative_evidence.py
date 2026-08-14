"""rehydrate Run 102 same-provider relative EPS evidence

Revision ID: 0043_ceri_run102_relative_evidence
Revises: 0042_ceri_run101_fail_closed
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043_ceri_run102_relative_evidence"
down_revision: str | None = "0042_ceri_run101_fail_closed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These values never become absolute monetary evidence: currency stays NULL.
    # Rehydrate only EPS observations that carry a provider response identity,
    # which is the boundary required for SAME_PROVIDER_RELATIVE comparison.
    op.execute(
        """
        UPDATE ceri_estimate_snapshots
        SET consensus = (original_fields_json ->> 'consensus')::numeric,
            high = CASE
                WHEN COALESCE(original_fields_json ->> 'high', '')
                     ~ '^-?[0-9]+([.][0-9]+)?$'
                THEN (original_fields_json ->> 'high')::numeric
                ELSE high
            END,
            low = CASE
                WHEN COALESCE(original_fields_json ->> 'low', '')
                     ~ '^-?[0-9]+([.][0-9]+)?$'
                THEN (original_fields_json ->> 'low')::numeric
                ELSE low
            END,
            canonical_scale = COALESCE(source_scale, 1),
            quality_flags_json = CASE
                WHEN COALESCE(quality_flags_json, '[]'::jsonb)
                     @> '["relative_value_only"]'::jsonb
                THEN quality_flags_json
                ELSE COALESCE(quality_flags_json, '[]'::jsonb)
                     || '["relative_value_only"]'::jsonb
            END,
            normalization_version = 'ceri-normalization-1.2.0'
        WHERE metric = 'EPS_DILUTED'
          AND current_observation_reference IS NOT NULL
          AND canonical_currency IS NULL
          AND consensus IS NULL
          AND COALESCE(original_fields_json ->> 'consensus', '')
              ~ '^-?[0-9]+([.][0-9]+)?$'
        """
    )


def downgrade() -> None:
    # Derived evidence may have been consumed after the upgrade; erasing it on
    # downgrade would be destructive. Currency remains fail-closed throughout.
    pass
