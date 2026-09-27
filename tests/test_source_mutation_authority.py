import pytest

from app.services.source_mutation_authority import (
    SOURCE_REFRESH_QUERY_PARAMETER_BUDGET,
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
