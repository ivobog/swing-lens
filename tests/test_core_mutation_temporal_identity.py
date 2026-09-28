from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services.core_mutation_authority import _validate_artifact_temporal_identity

CUTOFF = datetime(2026, 9, 28, 20, 37, 58, tzinfo=UTC)
SESSION = date(2026, 9, 28)


def _identity():
    return SimpleNamespace(
        temporal=SimpleNamespace(
            as_of_session=SimpleNamespace(value=SESSION),
            calculation_cutoff=SimpleNamespace(value=CUTOFF),
        )
    )


def test_older_effective_source_date_is_valid_for_exact_input_session() -> None:
    row = SimpleNamespace(
        input_as_of_session=SESSION,
        as_of_date=date(2026, 9, 25),
        calculation_cutoff_at=CUTOFF,
    )

    _validate_artifact_temporal_identity(row, _identity())


@pytest.mark.parametrize(
    "row",
    [
        SimpleNamespace(
            input_as_of_session=date(2026, 9, 25),
            as_of_date=date(2026, 9, 25),
            calculation_cutoff_at=CUTOFF,
        ),
        SimpleNamespace(
            input_as_of_session=SESSION,
            as_of_date=date(2026, 9, 29),
            calculation_cutoff_at=CUTOFF,
        ),
    ],
)
def test_mismatched_input_session_or_future_source_date_is_rejected(row) -> None:
    with pytest.raises(ValueError, match="MUTATION_ARTIFACT_TEMPORAL_MISMATCH"):
        _validate_artifact_temporal_identity(row, _identity())
