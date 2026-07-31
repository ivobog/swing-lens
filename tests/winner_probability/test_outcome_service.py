from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import (
    EntryDataStatus,
    FirstEvent,
    OutcomeStatus,
    PriceBar,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.outcome_service import OutcomeMaturationService


def test_due_pending_outcome_matures_with_entry_day_inclusive_metrics() -> None:
    prediction = _prediction()
    forward = _forward()
    target_stop = _target_stop()
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[target_stop],
        bars={
            "MSFT": _bars([100, 101, 102, 102, 103], highs=[101, 102, 103, 103, 104]),
            "SPY": _bars([200, 200, 201, 201, 202], ticker="SPY"),
            "XLK": _bars([50, 50, 50.5, 51, 51], ticker="XLK"),
        },
    )
    db = FakeOutcomeDb(repository)

    result = OutcomeMaturationService(repository=repository).process_due_outcomes(
        db,
        now=datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
    )

    assert result.processed == 1
    assert result.matured == 1
    assert result.target_stop_matured == 1
    assert forward.status == OutcomeStatus.MATURED
    assert prediction.entry_data_status == EntryDataStatus.AVAILABLE
    assert forward.entry_price == Decimal("100")
    assert forward.exit_price == Decimal("103")
    assert forward.close_return_pct == Decimal("3.000000")
    assert forward.mfe_pct == Decimal("4.000000")
    assert forward.mae_pct == Decimal("-1.000000")
    assert forward.sessions_to_mfe == 5
    assert forward.sessions_to_mae == 1
    assert forward.beat_spy is True
    assert forward.beat_sector is True
    assert target_stop.first_event == FirstEvent.TARGET_FIRST
    assert target_stop.event_session == date(2026, 8, 5)
    assert target_stop.primary_winner is True


def test_pending_rows_mature_exactly_once_when_due_selector_runs_again() -> None:
    prediction = _prediction()
    forward = _forward()
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[],
        bars={"MSFT": _bars([100, 100, 100, 100, 101]), "SPY": [], "XLK": []},
    )
    service = OutcomeMaturationService(repository=repository)
    db = FakeOutcomeDb(repository)
    now = datetime(2026, 8, 10, 21, 0, tzinfo=UTC)

    first = service.process_due_outcomes(db, now=now)
    second = service.process_due_outcomes(db, now=now)

    assert first.matured == 1
    assert second.processed == 0
    assert len(repository.forward_outcomes) == 1
    assert forward.revision == 1


def test_signal_close_diagnostic_uses_signal_close_and_stays_isolated() -> None:
    prediction = _prediction(prediction_as_of_date=date(2026, 7, 31))
    diagnostic = _forward(
        entry_model="SIGNAL_CLOSE_DIAGNOSTIC",
        entry_session=date(2026, 7, 31),
        due_session=date(2026, 8, 6),
    )
    diagnostic_target = _target_stop(
        entry_model="SIGNAL_CLOSE_DIAGNOSTIC",
        horizon_sessions=5,
    )
    next_open_target = _target_stop(id=3)
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[diagnostic],
        target_stop_outcomes=[diagnostic_target, next_open_target],
        bars={
            "MSFT": [
                _bar(date(2026, 7, 31), open_price=95, high=101, low=94, close=100),
                *_bars([101, 101, 102, 103], start=date(2026, 8, 3)),
            ],
            "SPY": [],
            "XLK": [],
        },
    )
    db = FakeOutcomeDb(repository)

    result = OutcomeMaturationService(repository=repository).process_due_outcomes(
        db,
        now=datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
    )

    assert result.matured == 1
    assert diagnostic.entry_price == Decimal("100")
    assert diagnostic_target.status == OutcomeStatus.MATURED
    assert next_open_target.status == OutcomeStatus.PENDING


def test_missing_entry_bar_keeps_outcome_pending_and_marks_entry_missing() -> None:
    prediction = _prediction()
    forward = _forward()
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[],
        bars={"MSFT": _bars([101, 102, 103, 104], start=date(2026, 8, 4))},
    )
    db = FakeOutcomeDb(repository)

    result = OutcomeMaturationService(repository=repository).process_due_outcomes(
        db,
        now=datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
    )

    assert result.pending == 1
    assert forward.status == OutcomeStatus.PENDING
    assert forward.metadata_json["pending_reason"] == "missing_entry_bar"
    assert prediction.entry_data_status == EntryDataStatus.MISSING


def test_invalid_ohlc_excludes_outcome_and_marks_entry_invalid() -> None:
    prediction = _prediction()
    forward = _forward()
    bars = _bars([100, 100, 100, 100, 101])
    bars[0].high = Decimal("99")
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[],
        bars={"MSFT": bars},
    )
    db = FakeOutcomeDb(repository)

    result = OutcomeMaturationService(repository=repository).process_due_outcomes(
        db,
        now=datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
    )

    assert result.excluded == 1
    assert forward.status == OutcomeStatus.EXCLUDED
    assert prediction.entry_data_status == EntryDataStatus.INVALID


def test_revised_bar_lineage_creates_new_current_revision() -> None:
    prediction = _prediction()
    forward = _forward()
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[],
        bars={"MSFT": _bars([100, 100, 100, 100, 101])},
    )
    service = OutcomeMaturationService(repository=repository)
    db = FakeOutcomeDb(repository)
    now = datetime(2026, 8, 10, 21, 0, tzinfo=UTC)

    service.process_forward_outcome(db, forward, now=now)
    original_hash = forward.source_bar_lineage_hash
    repository.bars["MSFT"][4].close = Decimal("102")
    repository.bars["MSFT"][4].high = Decimal("102")
    repository.bars["MSFT"][4].data_hash = "revised-close"
    result = service.process_forward_outcome(db, forward, now=now)

    assert result.revised == 1
    assert forward.is_current_revision is False
    assert len(repository.forward_outcomes) == 2
    current = repository.forward_outcomes[-1]
    assert current.revision == 2
    assert current.is_current_revision is True
    assert current.source_bar_lineage_hash != original_hash


class FakeOutcomeRepository:
    def __init__(
        self,
        *,
        predictions: list[WinnerPredictionSnapshot],
        forward_outcomes: list[WinnerForwardOutcome],
        target_stop_outcomes: list[WinnerTargetStopOutcome],
        bars: dict[str, list[PriceBar]],
    ) -> None:
        self.predictions = {row.id: row for row in predictions}
        self.forward_outcomes = forward_outcomes
        self.target_stop_outcomes = target_stop_outcomes
        self.bars = bars

    def get_due_pending_forward_outcomes(self, _db, *, completed_on: date, limit: int):
        return [
            outcome
            for outcome in self.forward_outcomes
            if outcome.status == OutcomeStatus.PENDING
            and outcome.is_current_revision
            and outcome.due_session <= completed_on
        ][:limit]

    def get_prediction(self, _db, prediction_id: int):
        return self.predictions.get(prediction_id)

    def get_target_stop_outcomes_for_forward(
        self,
        _db,
        *,
        prediction_id: int,
        entry_model: str,
        horizon_sessions: int,
    ):
        return [
            outcome
            for outcome in self.target_stop_outcomes
            if outcome.prediction_id == prediction_id
            and outcome.entry_model == entry_model
            and outcome.horizon_sessions == horizon_sessions
            and outcome.is_current_revision
        ]

    def load_bars(self, _db, ticker: str, *, start_date: date, end_date: date):
        return [
            bar
            for bar in self.bars.get(ticker.upper(), [])
            if start_date <= bar.bar_date <= end_date
        ]


class FakeOutcomeDb:
    def __init__(self, repository: FakeOutcomeRepository) -> None:
        self.repository = repository
        self.flushes = 0
        self._next_id = 100

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        if isinstance(row, WinnerForwardOutcome):
            self.repository.forward_outcomes.append(row)
        elif isinstance(row, WinnerTargetStopOutcome):
            self.repository.target_stop_outcomes.append(row)

    def flush(self) -> None:
        self.flushes += 1


def _prediction(*, prediction_as_of_date: date = date(2026, 7, 31)) -> WinnerPredictionSnapshot:
    return WinnerPredictionSnapshot(
        id=1,
        run_id=7,
        ticker="MSFT",
        prediction_as_of_date=prediction_as_of_date,
        source_data_cutoff_at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
        planned_entry_session=date(2026, 8, 3),
        entry_schedule_status="RESOLVED",
        entry_data_status=EntryDataStatus.NOT_DUE,
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash="hash",
        config_hash="config-hash",
        calculation_version="owpe-calc-1.0.0",
        feature_json={"canonical_sector": "Technology"},
    )


def _forward(
    *,
    entry_model: str = "NEXT_OPEN",
    entry_session: date = date(2026, 8, 3),
    due_session: date = date(2026, 8, 7),
) -> WinnerForwardOutcome:
    return WinnerForwardOutcome(
        id=1,
        prediction_id=1,
        entry_model=entry_model,
        horizon_sessions=5,
        entry_session=entry_session,
        due_session=due_session,
        status=OutcomeStatus.PENDING,
        revision=1,
        is_current_revision=True,
        metadata_json={},
    )


def _target_stop(
    *,
    id: int = 2,
    entry_model: str = "NEXT_OPEN",
    horizon_sessions: int = 5,
) -> WinnerTargetStopOutcome:
    return WinnerTargetStopOutcome(
        id=id,
        prediction_id=1,
        outcome_definition_id=id,
        entry_model=entry_model,
        horizon_sessions=horizon_sessions,
        status=OutcomeStatus.PENDING,
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        metadata_json={},
        outcome_definition=WinnerOutcomeDefinition(
            id=id,
            definition_id=f"definition-{id}",
            entry_model=entry_model,
            horizon_sessions=horizon_sessions,
            target_pct=2.5,
            stop_pct=2.0,
            same_bar_conflict_policy="CONSERVATIVE_STOP_FIRST",
        ),
    )


def _bars(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    start: date = date(2026, 8, 3),
    ticker: str = "MSFT",
) -> list[PriceBar]:
    dates = [start]
    while len(dates) < len(closes):
        from app.services.winner_probability.trading_session_service import next_regular_session

        dates.append(next_regular_session(dates[-1]))
    highs = highs or closes
    lows = lows or [close - 1 for close in closes]
    return [
        _bar(
            bar_date,
            ticker=ticker,
            open_price=closes[index],
            high=highs[index],
            low=lows[index],
            close=close,
        )
        for index, (bar_date, close) in enumerate(zip(dates, closes, strict=True))
    ]


def _bar(
    bar_date: date,
    *,
    ticker: str = "MSFT",
    open_price: float,
    high: float | None = None,
    low: float | None = None,
    close: float | None = None,
) -> PriceBar:
    close = open_price if close is None else close
    high = max(open_price, close) if high is None else high
    low = min(open_price, close) - 1 if low is None else low
    return PriceBar(
        id=hash((ticker, bar_date, open_price, close)) % 100000,
        ticker=ticker,
        bar_date=bar_date,
        timeframe="1 day",
        open=Decimal(str(open_price)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("1000"),
        source="IBKR",
        what_to_show="TRADES",
        adjustment_type=None,
        created_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
        revision_count=0,
        data_hash=f"{bar_date.isoformat()}-{open_price}-{high}-{low}-{close}",
    )
