from types import SimpleNamespace

from app.models.tables import FundamentalScore, RawCompanyRow, UploadRun
from app.routers import run_routes
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.ib_fetch_plan_service import FetchPlan


def test_recalculate_run_fundamentals_replaces_scores_from_stored_raw_rows() -> None:
    db = FundamentalFakeDb(
        [
            RawCompanyRow(
                run_id=7,
                row_number=1,
                ticker="MSFT",
                company_name="Microsoft",
                sector="Technology",
                raw_json={
                    "Symbol": "MSFT",
                    "Description": "Microsoft",
                    "Sector": "Technology",
                    "Market capitalization": "3000000000000",
                    "Free cash flow TTM": "70000000000",
                    "Net income TTM": "80000000000",
                },
            )
        ]
    )

    scores = recalculate_run_fundamentals(db, run_id=7)

    assert db.deleted_fundamentals is True
    assert db.flushes == 1
    assert len(scores) == 1
    assert isinstance(db.added[0], FundamentalScore)
    assert db.added[0].ticker == "MSFT"
    assert db.added[0].scoring_model_version == "fundamentals_v2.0"


def test_refresh_combined_route_rebuilds_combined_only(monkeypatch) -> None:
    calls = {"combined": 0, "technicals": 0}

    monkeypatch.setattr(
        run_routes,
        "refresh_combined_results",
        lambda _db, _run_id: calls.__setitem__("combined", calls["combined"] + 1) or [],
    )
    monkeypatch.setattr(
        run_routes,
        "score_run_technicals",
        lambda _db, _run_id: calls.__setitem__("technicals", calls["technicals"] + 1) or [],
    )
    db = RouteFakeDb()

    response = run_routes.refresh_combined_results_action(run_id=7, db=db)

    assert calls == {"combined": 1, "technicals": 0}
    assert db.commits == 1
    assert "combined-refreshed" in response.headers["location"]


def test_recalculate_fundamentals_route_commits_scores(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "recalculate_run_fundamentals",
        lambda _db, _run_id: [SimpleNamespace(ticker="MSFT")],
    )
    db = RouteFakeDb()

    response = run_routes.recalculate_fundamentals_action(run_id=7, db=db)

    assert db.commits == 1
    assert "fundamentals-refreshed" in response.headers["location"]


def test_refresh_technicals_route_commits_scores(monkeypatch) -> None:
    monkeypatch.setattr(
        run_routes,
        "score_run_technicals",
        lambda _db, _run_id: [SimpleNamespace(ticker="MSFT")],
    )
    db = RouteFakeDb()

    response = run_routes.refresh_technicals_action(run_id=7, db=db)

    assert db.commits == 1
    assert "technicals-refreshed" in response.headers["location"]


def test_full_pipeline_queues_fetch_and_refreshes_scores(monkeypatch) -> None:
    calls = {}
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")
    run.raw_company_rows = [RawCompanyRow(run_id=7, row_number=1, ticker="MSFT", raw_json={})]
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT", "SPY"],
        items=[],
        estimated_request_count=2,
        estimated_full_backfills=1,
        estimated_top_ups=1,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    monkeypatch.setattr(run_routes, "_load_run", lambda _db, _run_id: run)
    monkeypatch.setattr(
        run_routes,
        "recalculate_run_fundamentals",
        lambda _db, _run_id: [SimpleNamespace(ticker="MSFT")],
    )
    monkeypatch.setattr(run_routes, "build_fetch_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(
        run_routes,
        "score_run_technicals",
        lambda _db, _run_id: [SimpleNamespace(ticker="MSFT")],
    )
    monkeypatch.setattr(
        run_routes,
        "refresh_combined_results",
        lambda _db, _run_id: [SimpleNamespace(ticker="MSFT")],
    )
    monkeypatch.setattr(
        run_routes,
        "create_queued_fetch_run",
        lambda _db, _plan, _options: SimpleNamespace(id=42),
    )
    monkeypatch.setattr(
        run_routes,
        "submit_fetch_job",
        lambda fetch_run_id, _plan, _options: calls.setdefault("submitted", fetch_run_id),
    )
    db = RouteFakeDb()

    response = run_routes.run_full_pipeline_action(run_id=7, db=db)

    assert db.commits == 1
    assert calls["submitted"] == 42
    assert "pipeline-queued" in response.headers["location"]


class FundamentalFakeDb:
    def __init__(self, raw_rows: list[RawCompanyRow]) -> None:
        self.raw_rows = raw_rows
        self.added = []
        self.deleted_fundamentals = False
        self.flushes = 0

    def scalars(self, statement):
        if "raw_company_rows" in str(statement):
            return FakeScalarResult(self.raw_rows)
        return FakeScalarResult([])

    def execute(self, statement):
        if "DELETE FROM fundamental_scores" in str(statement):
            self.deleted_fundamentals = True

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def flush(self) -> None:
        self.flushes += 1


class RouteFakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def scalar(self, statement):
        if "upload_runs" in str(statement):
            return 7
        return None

    def commit(self) -> None:
        self.commits += 1


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)
