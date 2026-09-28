from decimal import Decimal

import pytest
from sqlalchemy import inspect

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.source_mutation_authority import (
    SOURCE_REFRESH_QUERY_PARAMETER_BUDGET,
    PrefetchedSourceBodies,
    source_refresh_identity_chunk_size,
)


@pytest.mark.parametrize(
    ("primary_key_width", "expected_chunk_size"),
    [(1, 50_000), (2, 25_000), (3, 16_666), (7, 7_142)],
)
def test_source_refresh_chunk_size_accounts_for_primary_key_width(
    primary_key_width: int,
    expected_chunk_size: int,
) -> None:
    chunk_size = source_refresh_identity_chunk_size(primary_key_width)

    assert chunk_size == expected_chunk_size
    assert chunk_size * primary_key_width <= SOURCE_REFRESH_QUERY_PARAMETER_BUDGET
    assert (chunk_size + 1) * primary_key_width > SOURCE_REFRESH_QUERY_PARAMETER_BUDGET


def test_source_refresh_chunk_size_reserves_explicit_additional_parameters() -> None:
    chunk_size = source_refresh_identity_chunk_size(3, additional_parameter_count=2)

    assert chunk_size * 3 + 2 <= SOURCE_REFRESH_QUERY_PARAMETER_BUDGET
    assert (chunk_size + 1) * 3 + 2 > SOURCE_REFRESH_QUERY_PARAMETER_BUDGET


@pytest.mark.parametrize(
    ("primary_key_width", "additional_parameter_count", "code"),
    [
        (0, 0, "MUTATION_SOURCE_PRIMARY_KEY_REQUIRED"),
        (1, -1, "MUTATION_SOURCE_PARAMETER_COUNT_INVALID"),
        (
            2,
            SOURCE_REFRESH_QUERY_PARAMETER_BUDGET - 1,
            "MUTATION_SOURCE_PARAMETER_BUDGET_EXHAUSTED",
        ),
    ],
)
def test_source_refresh_chunk_size_rejects_unsafe_budgets(
    primary_key_width: int,
    additional_parameter_count: int,
    code: str,
) -> None:
    with pytest.raises(ValueError, match=code):
        source_refresh_identity_chunk_size(
            primary_key_width,
            additional_parameter_count=additional_parameter_count,
        )


def test_score_bundle_allows_only_supporting_comparison_pointer_mutation() -> None:
    row = CeriScoreSnapshot(
        id=1,
        comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
        comparison_snapshot_id=None,
        opportunity_score=Decimal("50"),
    )
    key = ("ceri_score_snapshots", (1,))
    body = {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}
    bundle = PrefetchedSourceBodies(None)
    bundle._rows[key] = row
    bundle._bodies[key] = body
    bundle._sealed = True

    row.comparison_state = "COMPARABLE"
    row.comparison_snapshot_id = 2
    bundle.assert_unchanged_in_memory()

    row.opportunity_score = Decimal("51")
    with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED_IN_MEMORY"):
        bundle.assert_unchanged_in_memory()
