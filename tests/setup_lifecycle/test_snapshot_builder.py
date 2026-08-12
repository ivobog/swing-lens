from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    MarketRegimeSnapshot,
    PriceBar,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    SetupLifecycleEvaluationRun,
    TechnicalScore,
    UploadRun,
)
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import DataQualityLabel, EvaluationStatus
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleSnapshotBuilder,
    SetupLifecycleSnapshotCaptureService,
)
from app.services.setup_lifecycle.source_loader import (
    RunSourceContext,
    TickerSourceContext,
)

_MISSING = object()


def test_snapshot_builder_normalizes_promoted_fields_signals_and_source_ids() -> None:
    builder = SetupLifecycleSnapshotBuilder(load_setup_lifecycle_config())
    context = _ticker_context()

    built = builder.build(context)

    assert built.dto.ticker == "MSFT"
    assert built.dto.data_as_of_date == date(2026, 8, 1)
    assert built.dto.promoted_fields["dual_score"] == Decimal("8.2")
    assert built.dto.promoted_fields["close_above_trigger"] is True
    assert built.dto.promoted_fields["high_above_trigger"] is True
    assert built.dto.signals["technical_score"]["value"] == "8.2"
    assert built.dto.signals["close_trigger_cross"]["value"] is True
    assert built.dto.signals["volume_dry_up"]["value"] is True
    assert built.dto.signals["range_contraction"]["value"] is True
    assert built.dto.signals["failed_breakout"]["value"] is False
    assert built.dto.signals["vcp_score"]["value"] == "7.4"
    assert built.dto.signals["volume_percentile_252"]["value"] == "30"
    assert built.dto.signals["feature_flags"]["value"] == ["vcp_detected", "volume_dry_up"]
    assert built.dto.source_ids["raw_row_id"] == 101
    assert built.dto.source_ids["technical_score_id"] == 301
    assert built.dto.source_ids["sector_rotation_snapshot_id"] == 701
    assert built.dto.source_lineage["latest_bar"]["data_hash"] == "MSFT-2026-08-01-101"
    assert built.dto.source_lineage["source_run_successful"] is True
    assert built.dto.source_lineage["lineage_integrity"] is True
    assert built.required_feature_coverage == 1.0
    assert built.dto.promoted_fields["required_feature_coverage"] == Decimal("1.0")
    assert "MISSING_REQUIRED_CLOSE_PRICE" not in built.dto.warning_flags


def test_populated_close_is_counted_but_missing_close_remains_null_and_warns() -> None:
    builder = SetupLifecycleSnapshotBuilder(load_setup_lifecycle_config())

    populated = builder.build(_ticker_context())
    missing = builder.build(
        _ticker_context(
            price_bars=(),
            technical_score=_technical(),
        )
    )

    assert populated.required_feature_coverage == 1.0
    assert populated.dto.promoted_fields["close_price"] == Decimal("101")
    assert missing.dto.promoted_fields["close_price"] is None
    assert missing.required_feature_coverage == 0.75
    assert "MISSING_REQUIRED_CLOSE_PRICE" in missing.dto.warning_flags


def test_missing_optional_data_remains_null_and_adds_warnings() -> None:
    builder = SetupLifecycleSnapshotBuilder(load_setup_lifecycle_config())
    context = _ticker_context(
        fundamental_score=None,
        combined_result=None,
        market_regime_snapshot=None,
        sector_rotation_snapshot=None,
        sector_rotation_row=None,
    )

    built = builder.build(context)

    assert built.dto.promoted_fields["fundamental_score"] is None
    assert built.dto.promoted_fields["market_regime"] is None
    assert "MISSING_FUNDAMENTAL_SCORE" in built.dto.warning_flags
    assert "MISSING_COMBINED_RESULT" in built.dto.warning_flags
    assert "MISSING_MARKET_REGIME" in built.dto.warning_flags
    assert "MISSING_SECTOR_ROTATION" in built.dto.warning_flags


def test_stale_source_bars_produce_low_quality_freshness_warnings() -> None:
    builder = SetupLifecycleSnapshotBuilder(load_setup_lifecycle_config())
    upload = _upload_run(processed_at=datetime(2026, 8, 10, 21, 30, tzinfo=UTC))
    context = _ticker_context(
        upload_run=upload,
        price_bars=(_bar(date(2026, 7, 30), close=101),),
    )

    built = builder.build(context)

    assert built.freshness_status == "STALE"
    assert built.dto.data_quality_label == DataQualityLabel.INSUFFICIENT.value
    assert "STALE_PRICE_BAR" in built.dto.warning_flags


@pytest.mark.parametrize(
    ("as_of", "reference", "expected"),
    [
        (date(2026, 8, 7), date(2026, 8, 10), "FRESH"),  # Friday -> Monday
        (date(2026, 8, 7), date(2026, 8, 11), "FRESH"),  # Friday -> Tuesday
        (date(2026, 8, 7), date(2026, 8, 14), "NEAR_STALE"),
        (date(2026, 8, 7), date(2026, 8, 9), "FRESH"),  # weekend only
        (date(2026, 4, 2), date(2026, 4, 6), "FRESH"),  # Good Friday/Easter
        (date(2026, 7, 2), date(2026, 7, 6), "FRESH"),  # Independence Day
        (date(2026, 11, 25), date(2026, 11, 27), "FRESH"),  # Thanksgiving
        (date(2026, 12, 24), date(2026, 12, 28), "FRESH"),  # Christmas
        (date(2026, 12, 31), date(2027, 1, 4), "FRESH"),  # New Year
    ],
)
def test_freshness_uses_completed_us_trading_sessions(
    as_of: date,
    reference: date,
    expected: str,
) -> None:
    builder = SetupLifecycleSnapshotBuilder(load_setup_lifecycle_config())

    assert builder._freshness_status(as_of, reference, True) == expected


def test_snapshot_source_hash_changes_when_relevant_evidence_changes() -> None:
    builder = SetupLifecycleSnapshotBuilder(load_setup_lifecycle_config())
    original = builder.build(_ticker_context())
    changed = builder.build(
        _ticker_context(technical_score=_technical(dual_score=Decimal("8.9")))
    )

    assert original.source_data_hash != changed.source_data_hash


def test_capture_service_persists_one_snapshot_per_ticker_and_retries_idempotently() -> None:
    repository = FakeRepository()
    loader = FakeLoader(
        RunSourceContext(
            upload_run=_upload_run(),
            market_regime_snapshot=_market_snapshot(),
            sector_rotation_snapshot=_sector_snapshot(),
            tickers=(_ticker_context(ticker="MSFT"), _ticker_context(ticker="AAPL")),
        )
    )
    service = SetupLifecycleSnapshotCaptureService(
        loader=loader,
        repository=repository,
        config=load_setup_lifecycle_config(),
    )

    first = service.capture_snapshots_for_run(db=object(), run_id=7)
    second = service.capture_snapshots_for_run(db=object(), run_id=7)

    assert first.status == EvaluationStatus.COMPLETED.value
    assert first.captured == 2
    assert second.captured == 2
    assert len(repository.snapshots_by_key) == 2


def test_capture_service_marks_partial_when_one_ticker_fails() -> None:
    repository = FakeRepository()
    service = SetupLifecycleSnapshotCaptureService(
        loader=FakeLoader(
            RunSourceContext(
                upload_run=_upload_run(),
                market_regime_snapshot=_market_snapshot(),
                sector_rotation_snapshot=_sector_snapshot(),
                tickers=(_ticker_context(ticker="MSFT"), _ticker_context(ticker="BAD")),
            )
        ),
        builder=FailingBuilder(load_setup_lifecycle_config()),
        repository=repository,
        config=load_setup_lifecycle_config(),
    )

    result = service.capture_snapshots_for_run(db=object(), run_id=7)

    assert result.status == EvaluationStatus.PARTIAL.value
    assert result.captured == 1
    assert result.failed == 1
    assert result.errors_by_ticker == {"BAD": "bad ticker source context"}
    assert repository.completed_runs[-1].status == EvaluationStatus.PARTIAL.value


class FakeLoader:
    def __init__(self, context: RunSourceContext) -> None:
        self.context = context

    def load_run_context(self, _db, _run_id: int) -> RunSourceContext:
        return self.context


class FakeRepository:
    def __init__(self) -> None:
        self.next_id = 1
        self.snapshots_by_key = {}
        self.completed_runs: list[SetupLifecycleEvaluationRun] = []

    def create_evaluation_run(self, _db, **kwargs) -> SetupLifecycleEvaluationRun:
        return SetupLifecycleEvaluationRun(id=900 + len(self.completed_runs), **kwargs)

    def upsert_snapshot(self, _db, dto):
        key = SetupLifecycleRepository.snapshot_identity_key(
            run_id=dto.run_id,
            ticker=dto.ticker,
            timeframe=dto.timeframe,
            data_as_of_date=dto.data_as_of_date,
            engine_version=dto.engine_version,
            config_hash=dto.config_hash,
            source_data_hash=dto.source_data_hash,
        )
        if key not in self.snapshots_by_key:
            self.snapshots_by_key[key] = SimpleNamespace(id=self.next_id, dto=dto)
            self.next_id += 1
        return self.snapshots_by_key[key]

    def complete_evaluation_run(self, _db, evaluation_run, *, status, counts, **_kwargs):
        evaluation_run.status = status
        evaluation_run.counts_json = counts
        self.completed_runs.append(evaluation_run)
        return evaluation_run


class FailingBuilder(SetupLifecycleSnapshotBuilder):
    def build(self, context: TickerSourceContext):
        if context.ticker == "BAD":
            raise ValueError("bad ticker source context")
        return super().build(context)


def _ticker_context(
    *,
    ticker: str = "MSFT",
    upload_run: UploadRun | None = None,
    fundamental_score=_MISSING,
    technical_score=_MISSING,
    combined_result=_MISSING,
    market_regime_snapshot=_MISSING,
    sector_rotation_snapshot=_MISSING,
    sector_rotation_row=_MISSING,
    price_bars: tuple[PriceBar, ...] | None = None,
) -> TickerSourceContext:
    upload = upload_run or _upload_run()
    raw = _raw_row(ticker)
    raw.run = upload
    ranking = _ranking(ticker)
    return TickerSourceContext(
        raw_row=raw,
        fundamental_score=_fundamental(ticker)
        if fundamental_score is _MISSING
        else fundamental_score,
        technical_score=_technical(ticker=ticker)
        if technical_score is _MISSING
        else technical_score,
        combined_result=_combined(ticker) if combined_result is _MISSING else combined_result,
        ranking_results=(ranking,),
        ranking_results_by_profile={ranking.ranking_profile: ranking},
        market_regime_snapshot=market_regime_snapshot
        if market_regime_snapshot is _MISSING
        else market_regime_snapshot,
        sector_rotation_snapshot=sector_rotation_snapshot
        if sector_rotation_snapshot is not _MISSING
        else _sector_snapshot(),
        sector_rotation_row=_sector_row()
        if sector_rotation_row is _MISSING
        else sector_rotation_row,
        price_bars=price_bars if price_bars is not None else (_bar(date(2026, 8, 1), close=101),),
    )


def _upload_run(
    *,
    processed_at: datetime = datetime(2026, 8, 1, 21, 30, tzinfo=UTC),
) -> UploadRun:
    return UploadRun(
        id=7,
        filename="run.csv",
        status="COMPLETED",
        uploaded_at=processed_at,
        processed_at=processed_at,
    )


def _raw_row(ticker: str) -> RawCompanyRow:
    return RawCompanyRow(
        id=101 if ticker == "MSFT" else 102,
        run_id=7,
        row_number=1,
        ticker=ticker,
        company_name=ticker,
        sector="Technology",
        sector_canonical="Technology",
        raw_json={"pivot_price": "100", "trigger_price": "100"},
    )


def _fundamental(ticker: str) -> FundamentalScore:
    return FundamentalScore(
        id=201 if ticker == "MSFT" else 202,
        run_id=7,
        ticker=ticker,
        fundamental_score=Decimal("88"),
        liquidity_risk_score=Decimal("8"),
    )


def _technical(
    *,
    ticker: str = "MSFT",
    dual_score: Decimal = Decimal("8.2"),
) -> TechnicalScore:
    return TechnicalScore(
        id=301,
        run_id=7,
        ticker=ticker,
        dual_score=dual_score,
        trend_score=Decimal("7.5"),
        momentum_score=Decimal("7.1"),
        setup_score=Decimal("7.8"),
        risk_score=Decimal("2.1"),
        classification="Breakout",
        stage="PIVOT_READY",
        pullback_health="Clean",
        action_bias="Constructive",
        reward_risk=Decimal("2.5"),
        entry_risk_pct=Decimal("4.0"),
        technical_confidence="high",
        data_quality_score=Decimal("9.0"),
        relative_strength_score=Decimal("8.4"),
        leadership_score=Decimal("8.1"),
        vcp_score=Decimal("7.4"),
        box_tightness_score=Decimal("7.2"),
        atr_percentile_252=Decimal("25"),
        volume_percentile_252=Decimal("30"),
        range_percentile_252=Decimal("28"),
        extension_percentile_252=Decimal("40"),
        feature_flags_json=["vcp_detected", "volume_dry_up"],
        warning_flags_json=[],
        v4_debug_json={
            "contraction": {"range_contraction": True},
            "box": {"box_failure": False},
        },
        debug_json={
            "derived": {
                "atr": 2.0,
                "atr_pct": 2.0,
                "extension_mid_pct": 3.0,
                "volume_ratio": 0.8,
                "volume_dry_up": True,
                "red_vol_declining": True,
                "held_near_support": True,
                "pullback_depth_pct": 8.0,
                "failed_breakout": False,
                "heavy_mid_ma_break": False,
                "fresh_breakout": True,
            }
        },
    )


def _combined(ticker: str) -> CombinedResult:
    return CombinedResult(
        id=401 if ticker == "MSFT" else 402,
        run_id=7,
        ticker=ticker,
        company_name=ticker,
        sector="Technology",
        final_score=Decimal("92"),
        fundamental_score=Decimal("88"),
        dual_score=Decimal("8.2"),
        combined_decision="Buyable",
        earnings_risk_level="LOW",
        is_complete=True,
    )


def _ranking(ticker: str) -> RankingResult:
    return RankingResult(
        id=501 if ticker == "MSFT" else 502,
        run_id=7,
        ticker=ticker,
        ranking_profile="growth",
        ranking_label="Growth",
        profile_rank=2,
        profile_score=Decimal("95"),
        decision_label="Buyable",
    )


def _market_snapshot() -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        id=601,
        run_id=7,
        as_of_date=date(2026, 8, 1),
        calculation_version="v1",
        regime="RISK_ON",
        risk_state="NORMAL",
        score=80.0,
        action_summary="Constructive",
    )


def _sector_snapshot() -> SectorRotationSnapshot:
    return SectorRotationSnapshot(
        id=701,
        run_id=7,
        as_of_date=date(2026, 8, 1),
        calculation_version="v1",
        mode="LIVE",
    )


def _sector_row() -> SectorRotationRow:
    return SectorRotationRow(
        id=801,
        snapshot_id=701,
        sector="Technology",
        sector_slug="technology",
        rotation_state="LEADING",
        sector_permission="ALLOW",
        confidence="HIGH",
        current_rank=1,
    )


def _bar(bar_date: date, *, close: int) -> PriceBar:
    return PriceBar(
        id=901 + close,
        ticker="MSFT",
        bar_date=bar_date,
        timeframe="1 day",
        open=Decimal(close - 1),
        high=Decimal(close + 1),
        low=Decimal(close - 2),
        close=Decimal(close),
        volume=Decimal("1000000"),
        source="IBKR",
        what_to_show="TRADES",
        data_hash=f"MSFT-{bar_date.isoformat()}-{close}",
    )
