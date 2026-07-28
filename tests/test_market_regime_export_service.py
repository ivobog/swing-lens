import csv
import json
from datetime import UTC, date, datetime
from io import StringIO

from app.models.tables import MarketRegimeSnapshot
from app.services.market_regime_export_service import (
    MARKET_REGIME_CSV_HEADERS,
    export_snapshot_csv,
    export_snapshot_json,
    snapshot_to_payload,
)


def test_snapshot_to_payload_includes_full_policy_and_debug_context() -> None:
    payload = snapshot_to_payload(_snapshot())

    assert payload["as_of_date"] == "2026-07-28"
    assert payload["calculation_version"] == "mrcc-1.0.0"
    assert payload["regime"] == "Bull pullback"
    assert payload["risk_state"] == "Yellow"
    assert payload["policy"]["preferred_profiles"] == ["quality_momentum"]
    assert payload["policy"]["reduced_profiles"] == ["early_rocket"]
    assert payload["index_health"] == {"SPY": {"above_sma200": True}}
    assert payload["universe_participation"] == {"ticker_count": 42}
    assert payload["sector_leadership"] == [{"sector": "Technology"}]
    assert payload["created_at"] == "2026-07-28T12:00:00+00:00"


def test_export_snapshot_json_returns_stable_json() -> None:
    payload = json.loads(export_snapshot_json(_snapshot()))

    assert payload["regime"] == "Bull pullback"
    assert payload["warnings"] == ["low_market_confidence"]
    assert payload["debug"] == {"source": "unit"}


def test_export_snapshot_csv_flattens_primary_fields() -> None:
    csv_text = export_snapshot_csv(_snapshot())
    row = next(csv.DictReader(StringIO(csv_text)))

    assert csv_text.startswith(",".join(MARKET_REGIME_CSV_HEADERS))
    assert row["as_of_date"] == "2026-07-28"
    assert row["regime"] == "Bull pullback"
    assert row["risk_state"] == "Yellow"
    assert row["score"] == "6.8"
    assert row["position_size_multiplier"] == "0.75"
    assert row["preferred_profiles"] == "quality_momentum"
    assert row["warnings"] == "low_market_confidence"


def _snapshot() -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        id=3,
        run_id=7,
        as_of_date=date(2026, 7, 28),
        calculation_version="mrcc-1.0.0",
        config_version="2026-07-28",
        regime="Bull pullback",
        risk_state="Yellow",
        score=6.8,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Prefer quality pullbacks.",
        position_size_multiplier=0.75,
        preferred_profiles_json=["quality_momentum"],
        allowed_profiles_json=["quality_momentum", "defensive_quality"],
        reduced_profiles_json=["early_rocket"],
        blocked_profiles_json=[],
        allowed_setups_json=["Clean bull pullback"],
        blocked_setups_json=["Failed breakout"],
        input_symbols_json={"primary_market": "SPY"},
        index_health_json={"SPY": {"above_sma200": True}},
        universe_participation_json={"ticker_count": 42},
        sector_leadership_json=[{"sector": "Technology"}],
        reasons_json=["missing_qqq_market_data"],
        warnings_json=["low_market_confidence"],
        debug_json={"source": "unit"},
        created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )
