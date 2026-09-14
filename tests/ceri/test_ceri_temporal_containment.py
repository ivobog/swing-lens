from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import PriceBar, PriceBarRevision
from app.services.ceri.change_detection_service import ChangeDetectionResult
from app.services.ceri.change_rebuild_service import (
    CeriChangeRebuildRequest,
    CeriChangeRebuildService,
)
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.price_response_service import CeriPriceResponseService

CUTOFF = datetime(2026, 8, 5, 21, 15, tzinfo=UTC)
AS_OF = date(2026, 8, 5)


def test_historical_price_response_requires_both_temporal_anchors() -> None:
    service = CeriPriceResponseService()
    kwargs = dict(
        db=None,
        company_id=1,
        ticker="MSFT",
        event_type="EARNINGS",
        event_id=1,
        event_effective_at=datetime(2026, 8, 4, 16, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="explicit CURRENT mode or historical anchors"):
        service.calculate(**kwargs)
    with pytest.raises(ValueError, match="requires feature_as_of_session and cutoff_at"):
        service.calculate(
            **kwargs, feature_as_of_session=AS_OF, operation_mode="HISTORICAL"
        )
    with pytest.raises(ValueError, match="requires feature_as_of_session and cutoff_at"):
        service.calculate(**kwargs, cutoff_at=CUTOFF, operation_mode="HISTORICAL")


def test_historical_price_response_rejects_unproven_injected_current_bars() -> None:
    current = _bar(1, "MSFT", AS_OF, open_value="999", close_value="999")
    current.first_seen_at = CUTOFF + timedelta(seconds=1)
    current.last_seen_at = current.first_seen_at

    with pytest.raises(ValueError, match="not eligible"):
        CeriPriceResponseService().calculate(
            None,
            company_id=1,
            ticker="MSFT",
            event_type="EARNINGS",
            event_id=1,
            event_effective_at=datetime(2026, 8, 4, 16, tzinfo=UTC),
            stock_bars=[current],
            benchmark_bars=[],
            feature_as_of_session=AS_OF,
            cutoff_at=CUTOFF,
        )


def test_post_cutoff_price_revision_is_reconstructed_for_old_response() -> None:
    prior_day = date(2026, 8, 4)
    stock_prior = _bar(1, "MSFT", prior_day, open_value="99", close_value="100")
    stock_reaction = _bar(2, "MSFT", AS_OF, open_value="200", close_value="220")
    stock_reaction.revised_at = CUTOFF + timedelta(days=1)
    stock_reaction.revision_count = 1
    benchmark_prior = _bar(3, "SPY", prior_day, open_value="99", close_value="100")
    benchmark_reaction = _bar(4, "SPY", AS_OF, open_value="100", close_value="101")
    revision = PriceBarRevision(
        id=10,
        price_bar_id=2,
        ticker="MSFT",
        bar_date=AS_OF,
        timeframe="1 day",
        what_to_show="TRADES",
        revision_number=1,
        previous_data_hash="before",
        new_data_hash="after",
        previous_values_json={
            "open": "101",
            "high": "106",
            "low": "100",
            "close": "105",
            "volume": "1000",
            "source": "IB",
            "what_to_show": "TRADES",
        },
        new_values_json={},
        observed_at=CUTOFF + timedelta(days=1),
    )
    db = _PriceSession(
        [stock_prior, stock_reaction, benchmark_prior, benchmark_reaction],
        [revision],
    )

    result = CeriPriceResponseService().calculate(
        db,
        company_id=1,
        ticker="MSFT",
        event_type="EARNINGS",
        event_id=1,
        event_effective_at=datetime(2026, 8, 4, 16, tzinfo=UTC),
        feature_as_of_session=AS_OF,
        cutoff_at=CUTOFF,
    )

    assert result.unavailable_reason is None
    assert result.metrics["gap_pct"] == pytest.approx(0.01)
    assert result.metrics["return_1d"] == pytest.approx(0.05)
    assert 2 in result.price_bar_ids


def test_explicit_current_price_response_still_accepts_current_prepared_bars() -> None:
    prior_day = date(2026, 8, 4)
    rows = [
        _bar(1, "MSFT", prior_day, open_value="99", close_value="100"),
        _bar(2, "MSFT", AS_OF, open_value="101", close_value="105"),
    ]
    benchmark = [
        _bar(3, "SPY", prior_day, open_value="99", close_value="100"),
        _bar(4, "SPY", AS_OF, open_value="100", close_value="101"),
    ]

    result = CeriPriceResponseService().calculate(
        None,
        company_id=1,
        ticker="MSFT",
        event_type="EARNINGS",
        event_id=1,
        event_effective_at=datetime(2026, 8, 4, 16, tzinfo=UTC),
        stock_bars=rows,
        benchmark_bars=benchmark,
        operation_mode="CURRENT",
    )

    assert result.metrics["return_1d"] == pytest.approx(0.05)


def test_feature_rebuild_without_historical_anchor_fails_closed() -> None:
    with pytest.raises(ValueError, match="requires cutoff_at or as_of_session"):
        CeriFeatureRebuildService().prepare_batch(
            _RowsDb({}), CeriFeatureRebuildRequest(ticker="MSFT")
        )


def test_change_rebuild_excludes_post_cutoff_catalyst_and_guidance() -> None:
    detector = _RecordingDetector()
    db = _change_db()

    result = CeriChangeRebuildService(detector=detector).rebuild(
        db,
        CeriChangeRebuildRequest(
            company_ids=(1,),
            as_of_session=AS_OF,
            cutoff_at=CUTOFF,
        ),
    )

    assert result.failed == 0
    assert detector.revision_ids == [11]
    assert detector.guidance_ids == [21]


def test_change_rebuild_is_wall_clock_invariant_and_missing_cutoff_fails() -> None:
    with pytest.raises(ValueError, match="requires as_of_session/to_session and cutoff_at"):
        CeriChangeRebuildService(detector=_RecordingDetector()).rebuild(
            _change_db(), CeriChangeRebuildRequest(company_ids=(1,), as_of_session=AS_OF)
        )

    first = _RecordingDetector()
    second = _RecordingDetector()
    request = CeriChangeRebuildRequest(
        company_ids=(1,), as_of_session=AS_OF, cutoff_at=CUTOFF
    )
    CeriChangeRebuildService(detector=first).rebuild(_change_db(), request)
    CeriChangeRebuildService(detector=second).rebuild(_change_db(), request)

    assert (first.revision_ids, first.guidance_ids) == (
        second.revision_ids,
        second.guidance_ids,
    )


def _change_db() -> _RowsDb:
    known = _source(1, CUTOFF - timedelta(days=1))
    learned_late = _source(2, CUTOFF + timedelta(days=1))
    company = CeriCompany(id=1, ticker="MSFT", exchange="US")
    event = CeriCatalystEvent(id=5, company_id=1, category="PRODUCT", subject_key="x")
    old_revision = CeriCatalystEventRevision(
        id=11,
        catalyst_event_id=5,
        source_record_id=1,
        revision_number=1,
        is_current=False,
        status="ANNOUNCED",
        direction="POSITIVE",
        effective_session=AS_OF,
    )
    future_revision = CeriCatalystEventRevision(
        id=12,
        catalyst_event_id=5,
        source_record_id=2,
        revision_number=2,
        is_current=True,
        status="DELAYED",
        direction="NEGATIVE",
        effective_session=AS_OF,
    )
    old_guidance = CeriGuidanceEvent(
        id=21,
        source_record_id=1,
        company_id=1,
        action="RAISED",
        effective_session=AS_OF,
        accepted_for_scoring=True,
    )
    future_guidance = CeriGuidanceEvent(
        id=22,
        source_record_id=2,
        company_id=1,
        action="LOWERED",
        effective_session=AS_OF,
        accepted_for_scoring=True,
    )
    return _RowsDb(
        {
            CeriCompany: [company],
            CeriCatalystEvent: [event],
            CeriCatalystEventRevision: [old_revision, future_revision],
            CeriGuidanceEvent: [old_guidance, future_guidance],
            CeriScoreSnapshot: [],
            CeriSourceRecord: [known, learned_late],
        }
    )


def _source(source_id: int, known_at: datetime) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_id,
        provider="test",
        dataset="test",
        provider_record_id=str(source_id),
        retrieved_at=known_at,
        ingested_at=known_at,
        content_hash=f"source-{source_id}",
        idempotency_key=f"source-key-{source_id}",
    )


def _bar(
    row_id: int,
    ticker: str,
    session: date,
    *,
    open_value: str,
    close_value: str,
) -> PriceBar:
    known_at = CUTOFF - timedelta(days=1)
    return PriceBar(
        id=row_id,
        ticker=ticker,
        bar_date=session,
        timeframe="1 day",
        open=Decimal(open_value),
        high=max(Decimal(open_value), Decimal(close_value)) + 1,
        low=min(Decimal(open_value), Decimal(close_value)) - 1,
        close=Decimal(close_value),
        volume=Decimal("1000"),
        source="IB",
        what_to_show="TRADES",
        first_seen_at=known_at,
        last_seen_at=known_at,
        data_hash=f"bar-{row_id}",
    )


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)


class _RowsDb:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return _Rows(self.rows_by_model.get(model, []))

    def get(self, model, identifier):
        return next(
            (row for row in self.rows_by_model.get(model, []) if row.id == identifier),
            None,
        )


class _PriceSession(Session):
    def __init__(self, bars, revisions):
        super().__init__()
        self._bars = bars
        self._revisions = revisions

    def scalars(self, statement, *args, **kwargs):
        model = statement.column_descriptions[0]["entity"]
        return _Rows(self._bars if model is PriceBar else self._revisions)


class _RecordingDetector:
    def __init__(self):
        self.revision_ids = []
        self.guidance_ids = []

    def detect_score_changes(self, *_args, **_kwargs):
        return ChangeDetectionResult(0, 0)

    def detect_catalyst_revision(self, _db, *, revision, **_kwargs):
        self.revision_ids.append(revision.id)
        return ChangeDetectionResult(1, 0)

    def detect_guidance_change(self, _db, *, guidance, **_kwargs):
        self.guidance_ids.append(guidance.id)
        return ChangeDetectionResult(1, 0)
