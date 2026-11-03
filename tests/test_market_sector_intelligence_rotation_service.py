from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
import yaml
from market_sector_rotation_helpers import ANCHORS, CUTOFF, loader_with, synthetic_frame

from app.services.market_sector_intelligence_config import (
    CONFIG_PATH,
    MarketSectorIntelligenceConfigError,
    load_market_sector_intelligence_config,
    validate_market_sector_intelligence_config,
)
from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)
from app.services.us_market_calendar import subtract_us_trading_sessions


def test_complete_preview_has_exact_math_and_twelve_identical_pit_reads():
    calls = []
    service = MarketSectorRotationService(frame_loader=loader_with(calls=calls))
    result = service.preview(object(), cutoff=CUTOFF)
    assert len(calls) == len({ticker for ticker, _ in calls}) == 12
    assert all(
        kwargs
        == dict(
            timeframe="1 day", max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
        )
        for _ticker, kwargs in calls
    )
    assert result["cohort_status"] == "COMPLETE"
    assert result["eligible_count"] == result["total_sector_count"] == 11
    assert result["captured_at_utc"] is None
    assert len(result["sectors"]) == 11
    first = result["sectors"][0]
    assert first["rs_63_pct"] == pytest.approx(9.09090909090909)
    assert first["relative_momentum_pp"] == pytest.approx(2.36352392196549)
    assert first["rotation_state"] == "Leading"
    assert first["bars_used"] == 5 and first["bar_count"] == 64
    assert first["required_sessions"] == {
        str(offset): day.isoformat() for offset, day in ANCHORS.items()
    }
    assert [
        row["etf_symbol"] for row in sorted(result["sectors"], key=lambda row: row["rank"])
    ] == sorted(row["etf_symbol"] for row in result["sectors"])


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing", "MISSING_DATA"),
        ("stale", "STALE"),
        ("anchor", "MISSING_ANCHOR"),
        ("duplicate", "DUPLICATE_SESSION"),
        ("invalid", "INVALID_CLOSE"),
        ("mixed_basis", "INCOMPATIBLE_PRICE_BASIS"),
        ("basis", "UNKNOWN_PRICE_BASIS"),
        ("future", "FUTURE_SESSION"),
        ("nonfinite", "INVALID_CLOSE"),
    ],
)
def test_single_bad_etf_stays_visible_but_has_no_metrics_or_rank(fault, reason):
    frame = synthetic_frame("XLK")
    if fault == "missing":
        frame = frame.iloc[:0]
    elif fault == "stale":
        frame = frame[frame["date"] != ANCHORS[0]]
    elif fault == "anchor":
        frame = frame[frame["date"] != ANCHORS[42]]
        frame.loc[len(frame) + 2] = [subtract_us_trading_sessions(ANCHORS[0], 64), 100]
        assert len(frame) == 64  # row count cannot replace the missing exact anchor
    elif fault == "duplicate":
        frame = pd.concat([frame, frame.iloc[-1:]], ignore_index=True)
    elif fault in {"invalid", "nonfinite"}:
        frame.loc[frame["date"] == ANCHORS[42], "close"] = 0 if fault == "invalid" else float("nan")
    elif fault in {"mixed_basis", "basis"}:
        frame.attrs["price_basis"] = "ADJUSTED_LAST" if fault == "mixed_basis" else None
    else:
        frame.loc[len(frame)] = [ANCHORS[0] + timedelta(days=1), 100]
    result = MarketSectorRotationService(frame_loader=loader_with({"XLK": frame})).preview(
        object(), cutoff=CUTOFF
    )
    assert result["cohort_status"] == "PARTIAL" and result["eligible_count"] == 10
    row = next(row for row in result["sectors"] if row["etf_symbol"] == "XLK")
    assert row["quality_status"] == "INSUFFICIENT_DATA" and reason in row["reasons"]
    assert row["rank"] is row["rs_63_pct"] is row["relative_momentum_pp"] is None
    assert row["rotation_state"] == "Insufficient data"


@pytest.mark.parametrize("fault", ["missing", "stale", "invalid", "basis"])
def test_benchmark_failure_invalidates_every_sector(fault):
    frame = synthetic_frame("SPY")
    if fault == "missing":
        frame = frame.iloc[:0]
    elif fault == "stale":
        frame = frame.iloc[:-1]
    elif fault == "invalid":
        frame.loc[frame["date"] == ANCHORS[21], "close"] = -1
    else:
        frame.attrs["price_basis"] = None
    result = MarketSectorRotationService(frame_loader=loader_with({"SPY": frame})).preview(
        object(), cutoff=CUTOFF
    )
    assert result["cohort_status"] == "UNAVAILABLE" and result["eligible_count"] == 0
    assert all(
        row["rs_5_pct"] is row["rank"] is None and "BENCHMARK_UNAVAILABLE" in row["reasons"]
        for row in result["sectors"]
    )


def test_disabled_skips_every_price_read():
    loader = Mock(side_effect=AssertionError("must not read"))
    result = MarketSectorRotationService(frame_loader=loader).preview(
        object(),
        cutoff=CUTOFF,
        config=replace(load_market_sector_intelligence_config(), enabled=False),
    )
    assert result["cohort_status"] == "DISABLED" and len(result["sectors"]) == 11
    assert loader.call_count == 0


def test_same_material_identity_excludes_wall_time_but_tracks_provenance():
    loader = loader_with()
    service = MarketSectorRotationService(frame_loader=loader)
    first = service.preview(object(), cutoff=CUTOFF)
    second = service.preview(
        object(), cutoff=replace(CUTOFF, cutoff_at=CUTOFF.cutoff_at + timedelta(hours=1))
    )
    assert first["cutoff_at_utc"] != second["cutoff_at_utc"]
    assert first["source_material_digest"] == second["source_material_digest"]
    assert first["snapshot_digest"] == second["snapshot_digest"]

    def revised(db, ticker, **kwargs):
        frame, volume = loader(db, ticker, **kwargs)
        if ticker == "XLK":
            frame.attrs["pit_source_manifest"]["states"][-1]["fingerprint"] = "a" * 64
        return frame, volume

    revised_result = MarketSectorRotationService(frame_loader=revised).preview(
        object(), cutoff=CUTOFF
    )
    assert revised_result["source_material_digest"] != first["source_material_digest"]


def test_untrusted_manifest_and_post_cutoff_receipt_fail_closed():
    base = loader_with()
    for fault in ("manifest", "cutoff", "receipt"):

        def corrupt(db, ticker, _fault=fault, **kwargs):
            frame, volume = base(db, ticker, **kwargs)
            if _fault == "manifest":
                del frame.attrs["pit_source_manifest"]
            elif _fault == "cutoff":
                frame.attrs["pit_source_manifest"]["as_of"] = (
                    CUTOFF.cutoff_at + timedelta(days=1)
                ).isoformat()
            else:
                frame["first_seen_at"] = CUTOFF.cutoff_at + timedelta(seconds=1)
            return frame, volume

        result = MarketSectorRotationService(frame_loader=corrupt).preview(object(), cutoff=CUTOFF)
        assert result["eligible_count"] == 0


def test_d2_config_is_version_locked_and_legacy_hash_is_unchanged():
    feature = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    legacy = load_sector_rotation_config()
    before = sector_rotation_config_hash(legacy)
    d2 = validate_market_sector_intelligence_config(feature, legacy)
    del feature["rotation_engine"]
    d1 = validate_market_sector_intelligence_config(feature, legacy)
    assert (
        d1.configuration_digest
        == "c821b44f8fbbff852f33d68e462d5a80d92ccefff7086abb78c98147c048b725"
    )
    assert d1.configuration_digest != d2.configuration_digest
    assert (
        sector_rotation_config_hash(legacy)
        == before
        == "26e0f6ea6099ea73ed29df8f7eb7260bbcfde75e0d560e87e485eb1624e8afcb"
    )
    feature["rotation_engine"] = {
        "algorithm_version": "market-rotation-v1",
        "session_offsets": [0, 4],
    }
    with pytest.raises(MarketSectorIntelligenceConfigError):
        validate_market_sector_intelligence_config(feature, legacy)


@pytest.mark.parametrize(
    "fault", ["boolean_offset", "float_limit", "changed_version", "changed_rank"]
)
def test_invalid_rotation_rule_types_or_reused_versions_are_rejected(fault):
    feature = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if fault == "boolean_offset":
        feature["rotation_engine"]["session_offsets"][0] = False
    elif fault == "float_limit":
        feature["rotation_engine"]["max_history_limit"] = 100.0
    elif fault == "changed_version":
        feature["rotation_engine"]["algorithm_version"] = "unregistered-v2"
    else:
        feature["rotation_engine"]["primary_rank_metric"] = "rs_5_pct"
    with pytest.raises(MarketSectorIntelligenceConfigError):
        validate_market_sector_intelligence_config(feature, load_sector_rotation_config())


def test_isolated_input_error_is_sanitized_and_other_ten_sectors_still_evaluate(caplog):
    base = loader_with()

    def load(db, ticker, **kwargs):
        if ticker == "XLK":
            raise RuntimeError("private-connection-details")
        return base(db, ticker, **kwargs)

    result = MarketSectorRotationService(frame_loader=load).preview(object(), cutoff=CUTOFF)
    assert result["eligible_count"] == 10
    assert "private-connection-details" not in str(result) + caplog.text
