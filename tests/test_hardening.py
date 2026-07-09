import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services import technical_score_service
from app.services.pine_replica_engine import PineReplicaScore


def test_score_run_technicals_continues_when_symbol_fails(monkeypatch) -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.added = []
            self.executed = False
            self.flushed = False

        def execute(self, statement):
            self.executed = True

        def add_all(self, rows):
            self.added = rows

        def flush(self):
            self.flushed = True

    def fail_score(*args, **kwargs):
        raise RuntimeError("bad cached bars")

    empty_frame = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    monkeypatch.setattr(technical_score_service, "_load_price_frame", lambda *args: empty_frame)
    monkeypatch.setattr(technical_score_service, "_score_ticker", fail_score)

    db = FakeDb()
    rows = technical_score_service.score_run_technicals(db, run_id=9, tickers=["bad"])

    assert db.executed is True
    assert db.flushed is True
    assert len(rows) == 1
    assert rows[0].ticker == "BAD"
    assert rows[0].technical_confidence == "error"
    assert rows[0].technical_engine_version == "4.0.0"
    assert rows[0].insufficient_data is True
    assert rows[0].missing_data_json["reason"] == "bad cached bars"
    assert rows[0].warning_flags_json == ["technical_error"]
    assert rows[0].v4_debug_json["debug"]["score_source"] == "technical_error"
    assert rows[0].debug_json["explainability"] == rows[0].v4_debug_json


def test_unavailable_technical_score_is_export_safe() -> None:
    score = technical_score_service.unavailable_technical_score(
        run_id=11,
        ticker="msft",
        reason="No cached OHLCV bars",
    )

    assert score.run_id == 11
    assert score.ticker == "MSFT"
    assert score.dual_score is None
    assert score.classification == "No trade"
    assert score.action_bias == "No data"
    assert score.technical_engine_version == "4.0.0"
    assert score.data_quality_score == 0
    assert score.warning_flags_json == ["technical_error"]
    assert score.v4_debug_json["error"]["reason"] == "No cached OHLCV bars"
    assert score.v4_debug_json["final_v4_classification"] == "No trade"


def test_score_run_technicals_adds_run_level_leadership_debug(monkeypatch) -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.added = []
            self.executed = False
            self.flushed = False

        def execute(self, statement):
            self.executed = True

        def add_all(self, rows):
            self.added = rows

        def flush(self):
            self.flushed = True

    fake_scores = {
        "AAA": _replica_score("AAA", roc21=1, roc63=2, roc126=3, dual_score=5),
        "BBB": _replica_score("BBB", roc21=3, roc63=4, roc126=5, dual_score=8),
    }

    monkeypatch.setattr(
        technical_score_service,
        "load_technical_scoring_v4_config",
        lambda: {
            "relative_leadership": {
                "run_percentiles": True,
                "leadership_min_percentile": 70,
            },
            "market_regime_v4": {"use_qqq": False},
        },
    )
    monkeypatch.setattr(
        technical_score_service,
        "_load_price_frame",
        lambda *args: pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]),
    )
    monkeypatch.setattr(technical_score_service, "_market_features", lambda *args: {})
    monkeypatch.setattr(
        technical_score_service,
        "_score_ticker",
        lambda **kwargs: fake_scores[kwargs["ticker"].upper()],
    )

    rows = technical_score_service.score_run_technicals(
        FakeDb(),
        run_id=9,
        tickers=["aaa", "bbb"],
    )

    assert rows[0].debug_json["leadership"]["leadership_score"] == 5.0
    assert rows[0].debug_json["leadership"]["leadership_tags"] == []
    assert rows[0].debug_json["explainability"]["leadership"]["leadership_score"] == 5.0
    assert rows[1].debug_json["leadership"]["leadership_score"] == 10.0
    assert rows[1].debug_json["leadership"]["leadership_tags"] == ["rs_leader"]
    assert rows[1].debug_json["explainability"]["leadership"]["leadership_score"] == 10.0
    assert rows[1].leadership_score == 10.0
    assert rows[1].feature_flags_json == ["rs_leader"]
    assert rows[1].sub_tags_json == ["RS leader"]
    assert "rs_leader" in rows[1].debug_json["explainability"]["feature_flags"]
    assert "RS leader" in rows[1].debug_json["explainability"]["sub_tags"]


def test_score_run_technicals_passes_configured_sector_benchmark(monkeypatch) -> None:
    class FakeDb:
        def execute(self, statement):
            pass

        def add_all(self, rows):
            self.added = rows

        def flush(self):
            pass

    frames = {
        "SPY": pd.DataFrame(
            {"date": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        ),
        "QQQ": pd.DataFrame(
            {"date": [1], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]}
        ),
    }
    captured = {}

    monkeypatch.setattr(
        technical_score_service,
        "load_pine_defaults",
        lambda: {"market_rs": {"useSectorBenchmark": True, "sectorSymbol": "QQQ"}},
    )
    monkeypatch.setattr(
        technical_score_service,
        "load_technical_scoring_v4_config",
        lambda: {
            "relative_leadership": {"run_percentiles": False},
            "market_regime_v4": {"use_qqq": False},
        },
    )
    monkeypatch.setattr(
        technical_score_service,
        "_load_price_frame",
        lambda _db, ticker: frames[ticker.upper()],
    )
    monkeypatch.setattr(technical_score_service, "_market_features", lambda *args: {})

    def fake_score_ticker(**kwargs):
        captured["sector_price"] = kwargs["sector_price"]
        return _replica_score("AAA", roc21=1, roc63=2, roc126=3, dual_score=5)

    monkeypatch.setattr(technical_score_service, "_score_ticker", fake_score_ticker)

    technical_score_service.score_run_technicals(FakeDb(), run_id=9, tickers=["aaa"])

    assert captured["sector_price"] is frames["QQQ"]


def test_ready_route_returns_operational_shape() -> None:
    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert "database_ok" in payload
    assert "local_dirs_ok" in payload
    assert "checks" in payload


def _replica_score(
    ticker: str,
    roc21: float,
    roc63: float,
    roc126: float,
    dual_score: float,
) -> PineReplicaScore:
    return PineReplicaScore(
        ticker=ticker,
        local_trend_score=5.0,
        trend_score=5.0,
        momentum_score=5.0,
        setup_score=dual_score,
        risk_score=2.0,
        market_score=5.0,
        relative_strength_score=dual_score,
        sector_relative_strength_score=5.0,
        combined_relative_strength_score=dual_score,
        htf_score=5.0,
        dual_score=dual_score,
        classification="No trade",
        action_bias="No clear trade",
        pullback_health="Mixed",
        suggested_stop=None,
        suggested_target=None,
        reward_risk=None,
        entry_risk_pct=None,
        insufficient_data=False,
        missing_data={},
        debug={
            "derived": {
                "stock_roc_short": roc21,
                "stock_roc_medium": roc63,
                "stock_roc_long": roc126,
            }
        },
    )
