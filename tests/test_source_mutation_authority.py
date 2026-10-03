from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import JSON, Integer, Numeric, String, inspect
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    make_transient_to_detached,
    mapped_column,
)

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.source_mutation_authority import (
    SOURCE_REFRESH_QUERY_PARAMETER_BUDGET,
    PrefetchedSourceBodies,
    _stable_container_witness,
    _validate_stable_container_witness,
    source_refresh_identity_chunk_size,
)


class _TestBase(DeclarativeBase):
    pass


class _RetainedScore(_TestBase):
    __tablename__ = "ceri_score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comparison_state: Mapped[str] = mapped_column(String)
    comparison_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    opportunity_score: Mapped[Decimal] = mapped_column(Numeric)
    component_json: Mapped[dict] = mapped_column(JSON)


class _UnrelatedRow(_TestBase):
    __tablename__ = "unrelated_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String)


def test_stable_container_witness_rejects_membership_order_and_sequence_replacement() -> None:
    first = object()
    second = object()
    rows = [first, second]
    value = {"SPY": rows}
    witness = _stable_container_witness(value)

    assert _validate_stable_container_witness(value, witness) == 2
    rows.reverse()
    with pytest.raises(ValueError, match="MUTATION_SOURCE_STABLE_CONTAINER_CHANGED"):
        _validate_stable_container_witness(value, witness)

    value["SPY"] = [first, second]
    with pytest.raises(ValueError, match="MUTATION_SOURCE_STABLE_CONTAINER_CHANGED"):
        _validate_stable_container_witness(value, witness)


def test_stable_container_witness_rejects_changed_pit_projection_body() -> None:
    row = _RetainedScore(
        id=1,
        comparison_state="CURRENT",
        comparison_snapshot_id=None,
        opportunity_score=Decimal("50"),
        component_json={"value": 1},
    )
    row._pit_projection_as_of = "2026-10-02T20:00:00Z"
    row._pit_projection_revision_id = 7
    value = {"SPY": [row]}
    witness = _stable_container_witness(value)

    row.opportunity_score = Decimal("51")

    with pytest.raises(ValueError, match="MUTATION_SOURCE_STABLE_CONTAINER_CHANGED"):
        _validate_stable_container_witness(value, witness)


def test_revalidation_clears_all_p1_p3_writer_caches() -> None:
    bundle = PrefetchedSourceBodies(None)
    bundle._writer_source_values[("rows", (1,))] = {"id": 1}
    bundle._writer_canonical_values[1] = {"id": 1}
    bundle._writer_canonical_fragments[1] = b'{"id":1}'
    bundle._writer_stable_container_values[2] = object()

    bundle._require_revalidation("TEST")

    assert bundle._writer_source_values == {}
    assert bundle._writer_canonical_values == {}
    assert bundle._writer_canonical_fragments == {}
    assert bundle._writer_stable_container_values == {}


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

    telemetry = bundle.telemetry_snapshot()
    assert telemetry["assert_calls"] == 2
    assert telemetry["assert_rows"] == 2
    assert telemetry["assert_ms"] >= 0


def _retained_score_bundle(count: int) -> tuple[Session, PrefetchedSourceBodies, list]:
    db = Session(expire_on_commit=False)
    bundle = PrefetchedSourceBodies(db)
    rows = []
    for index in range(1, count + 1):
        row = _RetainedScore(
            id=index,
            comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
            comparison_snapshot_id=None,
            opportunity_score=Decimal("50"),
            component_json={"stable": index},
        )
        make_transient_to_detached(row)
        db.add(row)
        key = ("ceri_score_snapshots", (index,))
        body = {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}
        bundle._rows[key] = row
        bundle._bodies[key] = deepcopy(body)
        bundle._row_keys_by_identity[id(row)] = key
        rows.append(row)
    bundle._sealed = True
    return db, bundle, rows


def test_clean_large_session_bundle_assertion_is_constant_work() -> None:
    db, bundle, _rows = _retained_score_bundle(2_000)
    try:
        for _ in range(20):
            bundle.assert_unchanged_in_memory()
        telemetry = bundle.telemetry_snapshot()
        assert telemetry["assert_calls"] == 20
        assert telemetry["assert_rows"] == 0
        assert telemetry["assert_clean_noop_calls"] == 20
    finally:
        db.close()


def test_one_dirty_retained_row_is_the_only_row_fingerprinted() -> None:
    db, bundle, rows = _retained_score_bundle(1_000)
    try:
        rows[777].opportunity_score = Decimal("51")
        with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED_IN_MEMORY"):
            bundle.assert_unchanged_in_memory()
        telemetry = bundle.telemetry_snapshot()
        assert telemetry["assert_rows"] == 1
        assert telemetry["assert_dirty_rows"] == 1
    finally:
        db.close()


def test_multiple_dirty_retained_rows_are_candidates_not_a_full_scan() -> None:
    db, bundle, rows = _retained_score_bundle(1_000)
    try:
        for index in (2, 500, 999):
            rows[index].comparison_state = "COMPARABLE"
            rows[index].comparison_snapshot_id = index + 10_000
        bundle.assert_unchanged_in_memory()
        telemetry = bundle.telemetry_snapshot()
        assert telemetry["assert_rows"] == 3
        assert telemetry["assert_dirty_rows"] == 3
    finally:
        db.close()


def test_dirty_non_source_orm_row_does_not_scan_retained_bundle() -> None:
    db, bundle, _rows = _retained_score_bundle(500)
    unrelated = _UnrelatedRow(id=99_999, value="OTHER")
    make_transient_to_detached(unrelated)
    db.add(unrelated)
    unrelated.value = "OTHER2"
    try:
        bundle.assert_unchanged_in_memory()
        telemetry = bundle.telemetry_snapshot()
        assert telemetry["assert_rows"] == 0
        assert telemetry["assert_clean_noop_calls"] == 1
    finally:
        db.close()


def test_full_boundary_audit_catches_untracked_in_place_mutation() -> None:
    db, bundle, rows = _retained_score_bundle(100)
    try:
        rows[50].component_json["stable"] = "forged"
        # Plain JSON in-place mutation is deliberately not assumed to be in
        # Session.dirty; the per-ticker clean check remains bounded.
        bundle.assert_unchanged_in_memory()
        with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED_IN_MEMORY"):
            bundle.full_audit_unchanged_in_memory()
        telemetry = bundle.telemetry_snapshot()
        assert telemetry["assert_rows"] == 0
        assert telemetry["full_audit_calls"] == 1
        assert telemetry["full_audit_rows"] == 100
    finally:
        db.close()
