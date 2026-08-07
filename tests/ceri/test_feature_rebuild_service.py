from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.ceri_tables import CeriCompany, CeriRevisionFeature
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)


def test_feature_rebuild_persists_windows_and_acceleration() -> None:
    company = CeriCompany(id=7, ticker="MSFT", exchange="US")
    revisions = StubRevisionService()
    db = FakeDb({CeriCompany: [company], CeriRevisionFeature: []})

    result = CeriFeatureRebuildService(revisions=revisions).rebuild(
        db,
        CeriFeatureRebuildRequest(ticker="MSFT", as_of_session=date(2026, 8, 7)),
    )

    rows = db.rows[CeriRevisionFeature]
    assert result.features == 4
    assert result.processed_companies == 1
    assert len(rows) == 4
    assert {row.window_days for row in rows} == {7, 90}
    assert rows[0].acceleration == Decimal("0.10") or rows[1].acceleration == Decimal("0.10")


class StubRevisionService:
    def calculate_windows(self, _db, *, company_id, metric, cutoff_at, mode):
        return [
            CeriRevisionFeature(
                company_id=company_id,
                metric=metric,
                period_key=f"{metric}:current",
                as_of_session=cutoff_at.date(),
                window_days=window,
                actual_elapsed_days=window,
                absolute_change=Decimal("1.0" if window == 7 else "0.3"),
                pct_change=Decimal("0.1"),
                config_hash="hash",
                calculation_version="test",
            )
            for window in (7, 90)
        ]

    def with_acceleration(self, recent, _longer):
        recent.acceleration = Decimal("0.10")


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.next_id = 1

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return FakeResult(self.rows.get(model, []))

    def add(self, row):
        self.rows.setdefault(type(row), []).append(row)

    def flush(self):
        for rows in self.rows.values():
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = self.next_id
                    self.next_id += 1
