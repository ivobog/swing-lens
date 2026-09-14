from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from app.models.ib_market_intelligence_tables import (
    IBHistoricalMetricBar,
    IBHistoricalMetricRevision,
    IBMarketIntelligenceSnapshot,
)
from app.services.ib_market_intelligence import orchestration
from app.services.ib_market_intelligence.calculations import calculate_liquidity
from app.services.ib_market_intelligence.enums import (
    AvailabilityStatus,
    HistoricalMetricType,
    IntelligenceModule,
)
from app.services.ib_market_intelligence.repository import (
    project_historical_metric_rows_as_of,
)

CUTOFF = datetime(2026, 1, 5, 21, 15, tzinfo=UTC)


def test_task07_jan05_jan10_future_metric_positive_control_is_rejected() -> None:
    future = _metric_bar(
        10,
        session=date(2026, 1, 10),
        first_seen=CUTOFF - timedelta(days=1),
        spread=2,
    )
    db = _RowsDb({IBHistoricalMetricBar: [future]})

    selected = orchestration._metric_bars(
        db,
        "MSFT",
        HistoricalMetricType.BID_ASK,
        as_of_session=date(2026, 1, 5),
        cutoff_at=CUTOFF,
    )
    result = calculate_liquidity(selected, as_of=date(2026, 1, 5))

    assert selected == []
    assert (result.score, result.classification, result.freshness_status) != (
        1.0,
        "VERY_POOR",
        AvailabilityStatus.AVAILABLE,
    )
    assert result.classification == "INSUFFICIENT"


def test_metric_requires_both_effective_session_and_knowledge_eligibility() -> None:
    eligible = _metric_bar(1, session=date(2026, 1, 5), first_seen=CUTOFF)
    learned_late = _metric_bar(
        2,
        session=date(2026, 1, 5),
        first_seen=CUTOFF + timedelta(seconds=1),
    )
    future_session = _metric_bar(
        3,
        session=date(2026, 1, 6),
        first_seen=CUTOFF - timedelta(days=1),
    )

    selected = orchestration._metric_bars(
        _RowsDb({IBHistoricalMetricBar: [future_session, learned_late, eligible]}),
        "MSFT",
        HistoricalMetricType.BID_ASK,
        as_of_session=date(2026, 1, 5),
        cutoff_at=CUTOFF,
    )

    assert [row.id for row in selected] == [1]


def test_metric_revision_is_projected_to_pre_cutoff_values() -> None:
    revised = _metric_bar(1, session=date(2026, 1, 5), first_seen=CUTOFF - timedelta(days=1))
    revised.close_value = Decimal("110")
    revised.revised_at = CUTOFF + timedelta(days=1)
    revised.revision_count = 1
    revision = IBHistoricalMetricRevision(
        id=7,
        metric_bar_id=1,
        revision_number=1,
        previous_data_hash="old-hash",
        new_data_hash="new-hash",
        previous_values_json={
            "open_value": "100",
            "high_value": "101",
            "low_value": "99",
            "close_value": "100.1",
            "availability_status": "AVAILABLE",
            "warning_flags": [],
        },
        new_values_json={},
        observed_at=CUTOFF + timedelta(days=1),
    )

    projected = project_historical_metric_rows_as_of(
        _RowsDb({IBHistoricalMetricRevision: [revision]}),
        [revised],
        as_of=CUTOFF,
    )

    assert len(projected) == 1
    assert projected[0].id == 1
    assert projected[0].close_value == Decimal("100.1")
    assert projected[0].data_hash == "old-hash"


@pytest.mark.parametrize("snapshot_type", ["SHORTABLE", "OPTIONS_ACTIVITY"])
def test_future_live_snapshot_never_falls_back_as_historical(snapshot_type: str) -> None:
    eligible = _snapshot(1, snapshot_type, date(2026, 1, 5), CUTOFF - timedelta(minutes=5))
    future = _snapshot(2, snapshot_type, date(2026, 1, 6), CUTOFF + timedelta(minutes=1))
    db = _RowsDb({IBMarketIntelligenceSnapshot: [future, eligible]})

    selected = orchestration._latest_snapshot(
        db,
        "MSFT",
        snapshot_type,
        as_of_session=date(2026, 1, 5),
        cutoff_at=CUTOFF,
    )

    assert selected is eligible
    only_future = orchestration._latest_snapshot(
        _RowsDb({IBMarketIntelligenceSnapshot: [future]}),
        "MSFT",
        snapshot_type,
        as_of_session=date(2026, 1, 5),
        cutoff_at=CUTOFF,
    )
    assert only_future is None


def test_shortable_observation_history_excludes_future_rows() -> None:
    eligible = _snapshot(1, "SHORTABLE", date(2026, 1, 5), CUTOFF)
    future = _snapshot(2, "SHORTABLE", date(2026, 1, 6), CUTOFF + timedelta(seconds=1))

    selected = orchestration._shortable_share_observations(
        _RowsDb({IBMarketIntelligenceSnapshot: [future, eligible]}),
        "MSFT",
        as_of_session=date(2026, 1, 5),
        cutoff_at=CUTOFF,
    )

    assert [row["evidence_hash"] for row in selected] == ["snapshot-1"]


def test_dollar_volume_uses_pit_reader_with_original_boundary(monkeypatch) -> None:
    calls = []

    def load(_db, ticker, *, max_session, as_of):
        calls.append((ticker, max_session, as_of))
        price = pd.DataFrame([{"date": date(2026, 1, 5), "close": 10.0}])
        price.attrs["price_basis"] = "ADJUSTED_LAST"
        volume = pd.DataFrame([{"date": date(2026, 1, 5), "volume": 100.0}])
        volume.attrs["volume_basis"] = "TRADES"
        return price, volume

    monkeypatch.setattr(orchestration, "load_preferred_ohlcv_frames", load)

    first = orchestration._dollar_volume(
        object(), "MSFT", as_of_session=date(2026, 1, 5), cutoff_at=CUTOFF
    )
    second = orchestration._dollar_volume(
        object(), "MSFT", as_of_session=date(2026, 1, 5), cutoff_at=CUTOFF
    )

    assert first == second == 1000.0
    assert calls == [("MSFT", date(2026, 1, 5), CUTOFF)] * 2


def test_historical_live_freshness_uses_cutoff_not_execution_clock(monkeypatch) -> None:
    snapshot = _snapshot(1, "SHORTABLE", date(2026, 1, 5), CUTOFF - timedelta(minutes=20))
    config = SimpleNamespace(section=lambda _name: {"live_max_age_minutes": 30})

    monkeypatch.setattr(orchestration, "datetime", _WallClock(datetime(2026, 9, 14, tzinfo=UTC)))
    first = orchestration._snapshot_availability(snapshot, config, reference_at=CUTOFF)
    monkeypatch.setattr(orchestration, "datetime", _WallClock(datetime(2027, 9, 14, tzinfo=UTC)))
    second = orchestration._snapshot_availability(snapshot, config, reference_at=CUTOFF)

    assert first == second == AvailabilityStatus.AVAILABLE


def test_historical_feature_rebuild_without_cutoff_fails_closed() -> None:
    config = SimpleNamespace(section=lambda _name: {}, calculation_version="v", config_hash="h")
    with pytest.raises(ValueError, match="requires calculation_cutoff_at"):
        orchestration._rebuild_ticker_feature_impl(
            object(),
            "MSFT",
            IntelligenceModule.LIQUIDITY,
            date(2026, 1, 5),
            config,
            1,
        )


def _metric_bar(
    row_id: int,
    *,
    session: date,
    first_seen: datetime,
    spread: float = 0.1,
) -> IBHistoricalMetricBar:
    return IBHistoricalMetricBar(
        id=row_id,
        ticker="MSFT",
        session_date=session,
        effective_session=session,
        timeframe="1 day",
        metric_type=HistoricalMetricType.BID_ASK.value,
        open_value=Decimal("100"),
        high_value=Decimal("101"),
        low_value=Decimal("99"),
        close_value=Decimal(str(100 + spread)),
        source="IBKR",
        source_semantic_type="BID_ASK",
        availability_status=AvailabilityStatus.AVAILABLE,
        data_hash=f"metric-{row_id}",
        first_seen_at=first_seen,
        last_seen_at=first_seen,
        warning_flags_json=[],
    )


def _snapshot(
    row_id: int,
    snapshot_type: str,
    effective_session: date,
    observed_at: datetime,
) -> IBMarketIntelligenceSnapshot:
    return IBMarketIntelligenceSnapshot(
        id=row_id,
        ticker="MSFT",
        effective_session=effective_session,
        observed_at=observed_at,
        snapshot_type=snapshot_type,
        values_json={"shortable_shares": row_id * 100},
        availability_status=AvailabilityStatus.AVAILABLE,
        evidence_hash=f"snapshot-{row_id}",
        source_request_json={},
        warning_flags_json=[],
    )


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _RowsDb:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return _Rows(self.rows_by_model.get(model, []))


class _WallClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self, _tz):
        return self._now
