import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services import technical_score_service


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
    assert rows[0].insufficient_data is True
    assert rows[0].missing_data_json["reason"] == "bad cached bars"


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


def test_ready_route_returns_operational_shape() -> None:
    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert "database_ok" in payload
    assert "local_dirs_ok" in payload
    assert "checks" in payload
