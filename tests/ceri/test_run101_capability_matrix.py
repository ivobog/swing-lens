from __future__ import annotations

from datetime import date

from app.models.ceri_tables import (
    CeriCompany,
    CeriDerivedFeature,
    CeriEstimateSnapshot,
    CeriRevisionFeature,
)
from app.services.ceri.capability_matrix_service import CeriCapabilityMatrixService
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)


class StubRevisionService:
    def calculate_windows(self, _db, *, company_id, metric, period_slot, cutoff_at, mode):
        return [
            CeriRevisionFeature(
                company_id=company_id,
                metric=metric,
                period_key=f"{metric}:{period_slot}:current",
                period_slot=period_slot,
                as_of_session=cutoff_at.date(),
                window_days=window,
                config_hash="hash",
                calculation_version="test",
            )
            for window in (7, 90)
        ]

    def with_acceleration(self, recent, _longer):
        recent.acceleration = 0.1


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.next_id = 1
        self.flush_count = 0

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return FakeResult(self.rows.get(model, []))

    def add(self, row):
        self.rows.setdefault(type(row), []).append(row)

    def flush(self):
        self.flush_count += 1
        for rows in self.rows.values():
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = self.next_id
                    self.next_id += 1


class CountingRevisionService(StubRevisionService):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def calculate_windows(self, *args, metric, period_slot, **kwargs):
        self.calls.append((metric, period_slot))
        return super().calculate_windows(
            *args, metric=metric, period_slot=period_slot, **kwargs
        )


def test_globally_impossible_revision_family_short_circuits() -> None:
    company = CeriCompany(id=1, ticker="NONE")
    revisions = CountingRevisionService()
    db = FakeDb({CeriCompany: [company], CeriEstimateSnapshot: [], CeriRevisionFeature: []})

    result = CeriFeatureRebuildService(revisions=revisions).rebuild(
        db, CeriFeatureRebuildRequest(ticker="NONE", as_of_session=date(2026, 8, 13))
    )

    assert revisions.calls == []
    assert "revisions" in result.short_circuited_families
    assert result.family_query_counts["estimates"] == 1


def test_partial_revision_capability_builds_only_possible_slot() -> None:
    company = CeriCompany(id=2, ticker="PART")
    estimate = CeriEstimateSnapshot(
        source_record_id=1,
        company_id=2,
        metric="EPS_DILUTED",
        period_type="CURRENT_FISCAL_YEAR",
        canonical_period_slot="CURRENT_FISCAL_YEAR",
        fiscal_period_end=date(2026, 12, 31),
        canonical_observation_key="one",
    )
    revisions = CountingRevisionService()
    db = FakeDb(
        {CeriCompany: [company], CeriEstimateSnapshot: [estimate], CeriRevisionFeature: []}
    )

    result = CeriFeatureRebuildService(revisions=revisions).rebuild(
        db, CeriFeatureRebuildRequest(ticker="PART", as_of_session=date(2026, 8, 13))
    )

    assert revisions.calls == [("EPS_DILUTED", "CURRENT_FISCAL_YEAR")]
    assert result.features == 2


def test_matrix_records_sparse_unavailable_slots_without_scanning() -> None:
    matrix = CeriCapabilityMatrixService().build(
        company_ids=[1, 2],
        estimates_by_company={1: {("EPS_DILUTED", "CURRENT_QUARTER")}, 2: set()},
        earnings_company_ids=set(),
        guidance_company_ids=set(),
        catalyst_company_ids=set(),
    )

    assert matrix[1].revision_slots == {("EPS_DILUTED", "CURRENT_QUARTER")}
    assert matrix[2].unavailable_reasons["revisions"] == "NO_ELIGIBLE_ESTIMATE_INPUT"
    assert matrix[1].unavailable_reasons["earnings_surprise"] == "NO_REPORTED_EARNINGS"


def test_repeated_identical_derived_input_hash_reuses_result() -> None:
    service = CeriFeatureRebuildService()
    kwargs = {
        "company_id": 1,
        "family": "guidance",
        "key": "latest",
        "as_of_session": date(2026, 8, 13),
        "value": {"action": "RAISED"},
        "source_ids": [7],
    }
    db = FakeDb({CeriDerivedFeature: []})
    first = service._upsert_derived(db, **kwargs)
    first_flushes = db.flush_count

    second = service._upsert_derived(db, **kwargs)

    assert second is first
    assert db.flush_count == first_flushes
