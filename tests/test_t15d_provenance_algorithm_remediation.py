from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.models.tables import PriceBar
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.price_response_service import _bar_source_manifest, _preferred_ohlcv_rows
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.ohlcv_coverage_service import (
    BarSeriesCoverage,
    OhlcvCoverageStatus,
    _coverage_item,
)
from app.services.ranking_profile_engine import RankingProfileDecision
from app.services.ranking_profile_service import _to_ranking_model
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService
from app.services.technical_indicators import _bar_quality_summary


def test_ceri_provider_priority_selects_configured_winner_and_retains_candidates() -> None:
    primary = _source(1, "primary", retrieved_at=datetime(2026, 8, 3, 13, tzinfo=UTC))
    manual = _source(2, "manual", retrieved_at=datetime(2026, 8, 3, 13, tzinfo=UTC))
    primary_fact = _estimate(
        11,
        primary.id,
        Decimal("12"),
        effective_at=datetime(2026, 8, 3, 11, tzinfo=UTC),
    )
    manual_fact = _estimate(12, manual.id, Decimal("10"))
    query = CeriPointInTimeQuery(
        snapshots=[primary_fact, manual_fact],
        source_records={primary.id: primary, manual.id: manual},
    )

    selected = query.current_snapshot(
        object(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 3, 14, tzinfo=UTC),
    )

    assert selected is manual_fact
    provenance = query.provider_selection_for(selected)
    assert provenance is not None
    assert provenance.candidate_source_ids == (1, 2)
    assert provenance.resolution_reason == "provider_priority_then_quality_then_freshness"

    query._provider_conflicts._priority = {"primary": 0, "manual": 1}
    selected_under_new_policy = query.current_snapshot(
        object(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 3, 14, tzinfo=UTC),
    )
    assert selected_under_new_policy is primary_fact
    assert selected is manual_fact  # the old selection object was not rewritten


def test_ceri_historical_cutoff_pins_old_source_and_new_cutoff_can_use_revision() -> None:
    first = _source(1, "manual", retrieved_at=datetime(2026, 8, 3, 13, tzinfo=UTC))
    correction = _source(2, "manual", retrieved_at=datetime(2026, 8, 5, 13, tzinfo=UTC))
    correction.supersedes_id = first.id
    first_fact = _estimate(
        11,
        first.id,
        Decimal("10"),
        effective_at=datetime(2026, 8, 3, 12, tzinfo=UTC),
    )
    corrected_fact = _estimate(
        12,
        correction.id,
        Decimal("11"),
        effective_at=datetime(2026, 8, 5, 12, tzinfo=UTC),
        effective_session=date(2026, 8, 5),
    )
    query = CeriPointInTimeQuery(
        snapshots=[first_fact, corrected_fact],
        source_records={first.id: first, correction.id: correction},
    )

    at_c1 = query.current_snapshot(
        object(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
    )
    at_c2 = query.current_snapshot(
        object(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
    )

    assert at_c1 is first_fact
    assert at_c2 is corrected_fact
    assert at_c1.source_record_id == 1


def test_ceri_price_basis_is_order_independent_and_uses_trades_volume() -> None:
    rows = [
        _bar(1, "ADJUSTED_LAST", date(2026, 8, 3), close="50", volume="999"),
        _bar(2, "TRADES", date(2026, 8, 3), close="100", volume="10"),
        _bar(3, "ADJUSTED_LAST", date(2026, 8, 4), close="55", volume="999"),
        _bar(4, "TRADES", date(2026, 8, 4), close="110", volume="20"),
    ]

    price, volume, basis = _preferred_ohlcv_rows(rows)
    reverse_price, reverse_volume, reverse_basis = _preferred_ohlcv_rows(list(reversed(rows)))

    assert basis == reverse_basis == "ADJUSTED_LAST"
    assert [row.id for row in price] == [row.id for row in reverse_price] == [1, 3]
    assert [row.id for row in volume] == [row.id for row in reverse_volume] == [2, 4]
    rows[0].first_seen_at = datetime(2026, 8, 3, 21, tzinfo=UTC)
    assert (
        json.loads(json.dumps(_bar_source_manifest([rows[0]])))[0]["revision_identity"]
        == "REVISION_IDENTITY_UNAVAILABLE"
    )


def test_core_internal_trading_session_gap_is_not_ready_and_is_diagnosed() -> None:
    sessions = (
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 6),
        date(2026, 8, 7),
    )
    stats = {
        ("MSFT", basis): BarSeriesCoverage(
            count=4, first_date=sessions[0], latest_date=sessions[-1]
        )
        for basis in ("ADJUSTED_LAST", "TRADES")
    }
    item = _coverage_item(
        "MSFT",
        stats,
        required_rows=4,
        today=date(2026, 8, 7),
        sessions={("MSFT", basis): sessions for basis in ("ADJUSTED_LAST", "TRADES")},
    )
    frame = pd.DataFrame({"date": pd.to_datetime(sessions)})

    assert item.status == OhlcvCoverageStatus.INCOMPLETE_SESSIONS
    assert item.missing_price_sessions == (date(2026, 8, 5),)
    assert _bar_quality_summary(frame, required_rows=4)["missing_trading_sessions"] == [
        "2026-08-05"
    ]


def test_core_mixed_benchmark_effective_date_is_the_common_older_session() -> None:
    service = MarketRegimeCommandCenterService()
    inputs = [
        SimpleNamespace(as_of_date=date(2026, 8, 4)),
        SimpleNamespace(as_of_date=date(2026, 8, 3)),
    ]
    assert service._snapshot_date(inputs, date(2026, 8, 4)) == date(2026, 8, 3)


def test_bar_quality_marks_trailing_expected_session_missing() -> None:
    frame = pd.DataFrame({"date": pd.to_datetime([date(2026, 10, 2), date(2026, 10, 5)])})

    quality = _bar_quality_summary(
        frame,
        required_rows=2,
        expected_session=date(2026, 10, 6),
    )

    assert quality["latest_observed_session"] == "2026-10-05"
    assert quality["expected_session"] == "2026-10-06"
    assert quality["missing_trading_sessions"] == ["2026-10-06"]
    assert quality["session_complete"] is False


def test_setup_capture_rejects_membership_drift_from_parent_frozen_scope() -> None:
    loader = SimpleNamespace(
        load_run_context=lambda *_args, **_kwargs: SimpleNamespace(
            tickers=(SimpleNamespace(ticker="A"), SimpleNamespace(ticker="C"))
        )
    )
    service = SetupLifecycleSnapshotCaptureService(loader=loader)

    with pytest.raises(Exception, match="SETUP_FROZEN_SCOPE_MEMBERSHIP_MISMATCH"):
        service.capture_snapshots_for_run(
            object(),
            7,
            market_cutoff=object(),
            frozen_tickers=("A", "B"),
        )


def test_ranking_declares_regime_and_same_run_sector_non_authoritative() -> None:
    decision = RankingProfileDecision(
        ticker="MSFT",
        raw_row_id=1,
        company_name="Microsoft",
        sector="Technology",
        ranking_profile="momentum_swing",
        ranking_label="Momentum Swing",
        profile_rank=1,
        profile_score=8.0,
        technical_profile_score=8.0,
        fundamental_score=8.0,
        base_technical_score=8.0,
        technical_classification="Buyable",
        fundamental_label="Strong",
        decision_label="Candidate",
        position_size_hint="Normal",
        notes=[],
        warning_flags=[],
        penalties={},
        gates={},
        component_scores={},
        debug={},
        upcoming_earnings_date=None,
        days_until_earnings=None,
        earnings_risk_level=None,
        is_complete=True,
        has_warning=False,
        has_fundamental=True,
        has_technical=True,
        sort_bucket=1,
    )

    row = _to_ranking_model(7, decision)
    contract = row.debug_json["market_regime_consumer_contract"]

    assert contract["status"] == "NON_AUTHORITATIVE_FOR_RANKING"
    assert "market_regime_snapshot" in contract["excluded_sources"]
    assert "same_run_sector" in contract["excluded_sources"]


def _source(source_id: int, provider: str, *, retrieved_at: datetime) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_id,
        provider=provider,
        dataset="estimates",
        provider_record_id=f"{provider}-{source_id}",
        content_hash=f"hash-{source_id}",
        idempotency_key=f"key-{source_id}",
        retrieved_at=retrieved_at,
        ingested_at=retrieved_at,
    )


def _estimate(
    snapshot_id: int,
    source_id: int,
    consensus: Decimal,
    *,
    effective_at: datetime = datetime(2026, 8, 3, 12, tzinfo=UTC),
    effective_session: date = date(2026, 8, 3),
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=source_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        canonical_period_slot="CURRENT_FISCAL_YEAR",
        consensus=consensus,
        canonical_scale=Decimal("1"),
        canonical_currency="USD",
        effective_at=effective_at,
        effective_session=effective_session,
        canonical_observation_key="42:EPS:FY2026",
    )


def _bar(
    row_id: int,
    basis: str,
    session: date,
    *,
    close: str,
    volume: str,
) -> PriceBar:
    return PriceBar(
        id=row_id,
        ticker="MSFT",
        bar_date=session,
        timeframe="1 day",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal(volume),
        source="IB",
        what_to_show=basis,
    )
