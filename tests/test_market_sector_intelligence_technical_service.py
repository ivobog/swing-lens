import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from market_sector_technical_helpers import CUTOFF, frame, loader, native_inputs, receipt, symbols

from app.services import market_sector_intelligence_technical_adapter as adapter
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_technical_service import MarketSectorTechnicalService


@pytest.fixture
def isolated_validation(monkeypatch):
    # Source contract tests reuse already verified native scores; no fake readiness
    # is used by the full real-engine tests or cached-loader HTTP integration.
    _, base, market = native_inputs()

    def selected_base(ticker, *_args, calendar_version):
        assert calendar_version == CUTOFF.calendar_version
        return replace(base, ticker=ticker)

    def selected_market(_price, _volume, ticker, *_args, calendar_version):
        assert calendar_version == CUTOFF.calendar_version
        return SimpleNamespace(latest=market[ticker])

    monkeypatch.setattr(adapter, "build_base", selected_base)
    monkeypatch.setattr(adapter, "local_features", selected_market)


def test_full_native_preview_shared_inputs_cohort_and_disagreement(monkeypatch):
    calls, bases, peers, cohorts = [], [], [], []
    build, rs, rank, v4, v5 = (
        adapter.build_base,
        adapter.calculate_relative_strength_features,
        adapter.rank_leadership_v5,
        adapter.technical_score_v4_from_base_score,
        adapter.technical_score_v5_from_base_score,
    )
    model_bases = {"v4": [], "v5": []}

    def shared(*args, **kwargs):
        assert kwargs["calendar_version"] == CUTOFF.calendar_version
        result = build(*args, **kwargs)
        bases.append((args[0], id(args[3]), result))
        return result

    def peer(stock, spy, sector, **kwargs):
        peers.append((id(stock), id(spy), sector, deepcopy(kwargs["params"])))
        return rs(stock, spy, sector, **kwargs)

    def cohort(rows, config):
        cohorts.append((deepcopy(rows), deepcopy(config)))
        return rank(rows, config)

    def model4(base, **kwargs):
        model_bases["v4"].append(base)
        return v4(base, **kwargs)

    def model5(base, **kwargs):
        model_bases["v5"].append(base)
        return v5(base, **kwargs)

    monkeypatch.setattr(adapter, "build_base", shared)
    monkeypatch.setattr(adapter, "calculate_relative_strength_features", peer)
    monkeypatch.setattr(adapter, "rank_leadership_v5", cohort)
    monkeypatch.setattr(adapter, "technical_score_v4_from_base_score", model4)
    monkeypatch.setattr(adapter, "technical_score_v5_from_base_score", model5)
    result = MarketSectorTechnicalService(frame_loader=loader(calls=calls)).preview(
        object(), cutoff=CUTOFF
    )
    assert result["cohort_status"] == result["v5_cohort_status"] == "COMPLETE"
    assert result["v4_ready_count"] == result["v5_ready_count"] == len(result["rows"]) == 11
    assert [row["etf_symbol"] for row in result["rows"]] == symbols()
    assert len(calls) == len(set(symbol for symbol, _ in calls)) == 13
    assert all(
        kwargs
        == dict(
            timeframe="1 day", max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
        )
        for _, kwargs in calls
    )
    assert len({spy_id for _, spy_id, _ in bases}) == 1
    assert all(peer[2] is None and not peer[3]["market_rs"]["useSectorBenchmark"] for peer in peers)
    assert len(peers) == 11
    assert len(cohorts) == 1 and {row["ticker"] for row in cohorts[0][0]} == set(symbols())
    assert cohorts[0][1]["scope"] == "fixed_11_etf_universe"
    assert all(a is b for a, b in zip(model_bases["v4"], model_bases["v5"], strict=True))
    assert result["market_inputs"]["QQQ"]["context_role"] == "market_regime_context_only"
    assert result["effective_configuration"]["sector_benchmark_policy"] == "NOT_APPLICABLE_ETF"
    for row in result["rows"]:
        assert row["v5"]["leadership_scope"] == "fixed_11_etf_universe"
        assert (
            row["comparison"]["raw_score_delta"]
            == row["v5"]["technical_composite_score"] - row["v4"]["final_v4_score"]
        )
        assert row["comparison"]["classification_agreement"] == (
            row["v5"]["classification"] == row["v4"]["classification"]
        )
        assert row["v5"]["components"]["sector_benchmark"]["benchmark_symbol"] is None
    assert "blended_score" not in json.dumps(result, allow_nan=False)


def test_disabled_feature_skips_every_price_read():
    read = Mock(side_effect=AssertionError("no price reads"))
    result = MarketSectorTechnicalService(frame_loader=read).preview(
        object(),
        cutoff=CUTOFF,
        config=replace(load_market_sector_intelligence_config(), enabled=False),
    )
    assert result["cohort_status"] == result["v5_cohort_status"] == "DISABLED"
    assert len(result["rows"]) == 11 and read.call_count == 0
    assert all(row["data_reasons"] == ["FEATURE_DISABLED"] for row in result["rows"])


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing", "MISSING_DATA"),
        ("stale", "STALE"),
        ("gap", "MISSING_TRADING_SESSION"),
        ("duplicate", "DUPLICATE_SESSION"),
        ("future", "FUTURE_SESSION"),
        ("invalid", "INVALID_OHLCV"),
        ("nonfinite", "INVALID_OHLCV"),
        ("volume", "MISSING_VOLUME"),
        ("range", "INVALID_OHLC_RANGE"),
        ("basis", "UNKNOWN_PRICE_BASIS"),
        ("mixed", "INCOMPATIBLE_PRICE_BASIS"),
        ("columns", "INVALID_COLUMNS"),
        ("exception", "TECHNICAL_EVIDENCE_UNAVAILABLE"),
    ],
)
def test_one_invalid_etf_is_visible_and_withholds_all_v5(
    fault, reason, isolated_validation, caplog
):
    bad = frame("XLK")
    if fault == "missing":
        bad = bad.iloc[:0]
    elif fault == "stale":
        bad = bad.iloc[:-1]
    elif fault == "gap":
        bad = bad.drop(bad.index[-30])
    elif fault == "duplicate":
        bad = pd.concat([bad, bad.iloc[-1:]])
    elif fault == "future":
        bad.loc[bad.index[-1], "date"] = CUTOFF.latest_completed_session + timedelta(days=1)
    elif fault in {"invalid", "nonfinite"}:
        bad.loc[bad.index[-1], "close"] = 0 if fault == "invalid" else float("inf")
    elif fault == "volume":
        bad.loc[bad.index[-10], "volume"] = None
    elif fault == "range":
        bad.loc[bad.index[-1], "high"] = 1
    elif fault in {"basis", "mixed"}:
        bad.attrs["price_basis"] = None if fault == "basis" else "ADJUSTED_LAST"
    elif fault == "columns":
        bad = bad.drop(columns=["open"])
    else:
        bad = RuntimeError("private provider or connection details")
    result = MarketSectorTechnicalService(frame_loader=loader({"XLK": bad})).preview(
        object(), cutoff=CUTOFF
    )
    row = next(row for row in result["rows"] if row["etf_symbol"] == "XLK")
    assert reason in row["data_reasons"] and row["v4"]["final_v4_score"] is None
    assert result["v4_ready_count"] == 10 and result["v5_ready_count"] == 0
    assert result["v5_cohort_status"] == "V5_COHORT_NOT_COMPARABLE"
    assert all(
        row["v5"]["technical_composite_score"] is row["comparison"]["raw_score_delta"] is None
        for row in result["rows"]
    )
    assert "private provider" not in json.dumps(result) + caplog.text


@pytest.mark.parametrize(
    "symbol,reason", [("SPY", "BENCHMARK_UNAVAILABLE"), ("QQQ", "QQQ_CONTEXT_UNAVAILABLE")]
)
def test_missing_market_context_never_defaults_to_favorable_regime(
    symbol, reason, isolated_validation
):
    result = MarketSectorTechnicalService(
        frame_loader=loader({symbol: frame(symbol).iloc[:0]})
    ).preview(object(), cutoff=CUTOFF)
    assert result["v4_ready_count"] == result["v5_ready_count"] == 0
    assert len(result["rows"]) == 11
    assert all(reason in row["data_reasons"] for row in result["rows"])


@pytest.mark.parametrize("fault", ["manifest", "boundary", "receipt", "fingerprint", "acquisition"])
def test_untrusted_pit_source_fails_closed(fault, isolated_validation):
    read = loader()

    def corrupt(db, symbol, **kwargs):
        price, volume = read(db, symbol, **kwargs)
        if symbol == "XLK":
            if fault == "manifest":
                del price.attrs["pit_source_manifest"]
            elif fault == "boundary":
                price.attrs["pit_source_manifest"]["as_of"] = (
                    CUTOFF.cutoff_at + timedelta(seconds=1)
                ).isoformat()
            elif fault == "receipt":
                price["created_at"] = CUTOFF.cutoff_at + timedelta(seconds=1)
            elif fault == "fingerprint":
                price.attrs["pit_source_manifest"]["states"][-1]["fingerprint"] = "bad"
            else:
                price.attrs["pit_source_manifest"]["acquisition_authority"] = {"pipeline": 3}
        return price, volume

    result = MarketSectorTechnicalService(frame_loader=corrupt).preview(object(), cutoff=CUTOFF)
    assert result["v4_ready_count"] == 10 and result["v5_ready_count"] == 0


def test_missing_v5_beta_input_keeps_all_valid_v4_and_never_ranks_ten(
    monkeypatch, isolated_validation
):
    original = adapter.leadership_input

    def deficient(base, policy):
        row, missing = original(base, policy)
        return row, ["rolling_beta_126"] if base.ticker == "XLK" else missing

    forbidden = Mock(side_effect=AssertionError("must not rank incomplete cohort"))
    monkeypatch.setattr(adapter, "leadership_input", deficient)
    monkeypatch.setattr(adapter, "rank_leadership_v5", forbidden)
    result = MarketSectorTechnicalService(frame_loader=loader()).preview(object(), cutoff=CUTOFF)
    assert result["v4_ready_count"] == 11 and result["v5_ready_count"] == 0
    assert result["v5_missing_members"] == {"XLK": ["rolling_beta_126"]}
    assert forbidden.call_count == 0


def test_material_identity_ignores_clock_drift_ids_and_frame_order_but_tracks_revisions(
    isolated_validation,
):
    read = loader()
    service = MarketSectorTechnicalService(frame_loader=read)
    first = service.preview(object(), cutoff=CUTOFF)
    later = service.preview(
        object(), cutoff=replace(CUTOFF, cutoff_at=CUTOFF.cutoff_at + timedelta(hours=1))
    )
    assert first["as_of_utc"] != later["as_of_utc"]
    assert first["evaluation_digest"] == later["evaluation_digest"]

    def reordered(db, symbol, **kwargs):
        price, volume = read(db, symbol, **kwargs)
        price = price.iloc[::-1].reset_index(drop=True)
        price.attrs["pit_source_manifest"]["states"].reverse()
        for state in price.attrs["pit_source_manifest"]["states"]:
            state["id"] += 100_000
        return price, volume

    stable = MarketSectorTechnicalService(frame_loader=reordered).preview(object(), cutoff=CUTOFF)
    assert stable["evaluation_digest"] == first["evaluation_digest"]

    def revised(db, symbol, **kwargs):
        price, volume = read(db, symbol, **kwargs)
        if symbol == "XLK":
            price.attrs["pit_source_manifest"]["states"][0]["fingerprint"] = "a" * 64
        return price, volume

    changed = MarketSectorTechnicalService(frame_loader=revised).preview(object(), cutoff=CUTOFF)
    assert changed["evaluation_digest"] != first["evaluation_digest"]
    assert changed["rows"][-1]["v4"] == first["rows"][-1]["v4"]


def test_optional_qqq_is_loaded_only_if_active_regime_requires_it(monkeypatch, isolated_validation):
    original = adapter.scoped_policy
    monkeypatch.setattr(
        adapter, "scoped_policy", lambda symbols: replace(original(symbols), requires_qqq=False)
    )
    calls = []
    result = MarketSectorTechnicalService(frame_loader=loader(calls=calls)).preview(
        object(), cutoff=CUTOFF
    )
    assert len(calls) == 12 and "QQQ" not in {symbol for symbol, _ in calls}
    assert result["optional_market_context"] is None


@pytest.mark.parametrize("fault", [None, "missing_volume_session", "volume_revision"])
def test_separate_trades_volume_basis_alignment_and_material_identity(fault):
    config = load_market_sector_intelligence_config()

    def read(_db, ticker, **kwargs):
        price, volume = frame(ticker, basis="ADJUSTED_LAST"), frame(ticker)
        if fault == "missing_volume_session":
            volume = volume.iloc[1:]
        elif fault == "volume_revision":
            volume.loc[volume.index[-100], "volume"] *= 1.2
        return receipt(price, ticker, kwargs), receipt(volume, ticker, kwargs)

    result = MarketSectorTechnicalService(frame_loader=read)._load(object(), "XLK", config, CUTOFF)
    if fault == "missing_volume_session":
        assert result["reasons"] == ["MISSING_VOLUME_SESSION"]
    else:
        assert result["reasons"] == []
        assert result["price_basis"] == "ADJUSTED_LAST" and result["volume_basis"] == "TRADES"
        assert result["volume"] is not None
        if fault == "volume_revision":

            def unchanged(_db, ticker, **kwargs):
                return receipt(frame(ticker, basis="ADJUSTED_LAST"), ticker, kwargs), receipt(
                    frame(ticker), ticker, kwargs
                )

            before = MarketSectorTechnicalService(frame_loader=unchanged)._load(
                object(), "XLK", config, CUTOFF
            )
            assert before["input_digest"] != result["input_digest"]


def test_indicator_working_copies_preserve_manifest_sources_and_exact_native_base():
    config = load_market_sector_intelligence_config()
    read = loader()
    kwargs = dict(
        timeframe="1 day", max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
    )
    raw, _ = read(object(), "XLB", **kwargs)
    raw_spy, _ = read(object(), "SPY", **kwargs)
    before = deepcopy(raw.attrs)
    selected = MarketSectorTechnicalService(frame_loader=lambda *_a, **_k: (raw, None))._load(
        object(),
        "XLB",
        config,
        CUTOFF,
    )
    assert selected["reasons"] == [] and raw.attrs == before
    assert selected["source_manifests"]["price"] == before["pit_source_manifest"]
    assert selected["price"].attrs == {"price_basis": "TRADES"}
    pd.testing.assert_frame_equal(selected["price"], raw)
    policy, _, market = native_inputs()
    original = adapter.build_base(
        "XLB",
        raw,
        None,
        raw_spy,
        market["SPY"],
        market["QQQ"],
        CUTOFF.latest_completed_session,
        policy,
    )
    working = adapter.build_base(
        "XLB",
        selected["price"],
        None,
        raw_spy,
        market["SPY"],
        market["QQQ"],
        CUTOFF.latest_completed_session,
        policy,
    )
    assert original == working
    assert adapter.v4_payload(original, policy) == adapter.v4_payload(working, policy)
