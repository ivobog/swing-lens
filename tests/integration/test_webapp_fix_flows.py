from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from typing import Any

from fastapi import UploadFile

import app.services.ib_fetch_executor as executor
import app.services.ib_fetch_job_service as jobs
import app.services.upload_service as upload_service
from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    IBFetchItem,
    IBFetchRun,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services.bar_cache_service import BarUpsertSummary
from app.services.combined_decision import refresh_combined_results
from app.services.export_service import export_run_csv
from app.services.fundamental_ranker_v2 import FundamentalScoreV2Result
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_job_service import FetchJobOptions, resume_fetch_job
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    build_fetch_plan,
)
from app.services.ohlcv_coverage_service import OhlcvCoverageItem, OhlcvCoverageSummary
from app.settings import Settings


def test_upload_fetch_plan_execution_cockpit_and_export_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        upload_service,
        "get_settings",
        lambda: Settings(upload_dir=tmp_path, max_upload_size_mb=1),
    )
    monkeypatch.setattr(upload_service, "score_rows_v2", _fake_score_rows_v2)
    upload_db = UploadFakeDb()

    run = upload_service.create_upload_run(
        upload_db,
        UploadFile(
            filename="daily.csv",
            file=BytesIO(
                b"Symbol,Description,Sector,Price,Market capitalization\n"
                b"MSFT,Microsoft,Technology,410,3050000000000\n"
            ),
        ),
    )

    assert run.status == "COMPLETED"
    assert run.row_count == 1
    assert upload_db.raw_rows[0].ticker == "MSFT"
    assert upload_db.fundamental_scores[0].ticker == "MSFT"

    plan = build_fetch_plan(
        PlanFakeDb(),
        ["MSFT"],
        run_id=run.id,
        include_benchmarks=True,
        what_to_show_values=("TRADES",),
        settings=Settings(
            ib_benchmarks="SPY",
            ib_required_daily_bars=2,
            ib_daily_bar_stale_after_days=3,
        ),
    )

    assert plan.requested_tickers == ["MSFT"]
    assert plan.symbols_including_benchmarks == ["MSFT", "SPY"]
    assert any("SPY benchmark coverage is not ready" in warning for warning in plan.warnings)
    assert [item.ticker for item in plan.items] == ["MSFT", "SPY"]

    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker),
            error_message=None,
        ),
    )
    monkeypatch.setattr(executor, "fetch_daily_bars", lambda *args, **kwargs: ["bar"])
    monkeypatch.setattr(
        executor,
        "cache_bars",
        lambda db, bars: BarUpsertSummary(inserted=1),
    )

    fetch_run = execute_fetch_plan(
        db=ExecutorFakeDb(),
        plan=plan,
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
        settings=Settings(ib_max_retries=1),
        include_benchmarks=True,
    )

    assert fetch_run.status == "COMPLETED"
    assert fetch_run.executed_request_count == 2
    assert fetch_run.inserted_count == 2

    cockpit_db = CockpitFakeDb(
        raw_rows=upload_db.raw_rows,
        fundamentals=upload_db.fundamental_scores,
        technicals=[_technical("MSFT")],
    )
    combined = refresh_combined_results(cockpit_db, run.id)

    assert cockpit_db.deleted_combined_results
    assert len(combined) == 1
    assert combined[0].ticker == "MSFT"
    assert combined[0].is_complete

    run.raw_company_rows = upload_db.raw_rows
    run.fundamental_scores = upload_db.fundamental_scores
    run.technical_scores = cockpit_db.technicals
    run.combined_results = combined

    csv_text = export_run_csv(run, "combined", coverage=_coverage())

    assert "warning_flags,sort_bucket" in csv_text
    assert "technical_confidence" in csv_text
    assert "MSFT,Microsoft,Technology" in csv_text
    assert "normal" in csv_text
    assert "ready,2,2" in csv_text


def test_failed_contract_fetch_can_be_resumed(monkeypatch) -> None:
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(contract=None, error_message="No contract"),
    )
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["BAD"],
        symbols_including_benchmarks=["BAD"],
        items=[_plan_item("BAD", FetchAction.CONTRACT_RESOLUTION_REQUIRED)],
        estimated_request_count=0,
        estimated_full_backfills=0,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )

    failed_run = execute_fetch_plan(
        db=ExecutorFakeDb(),
        plan=plan,
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
        settings=Settings(ib_max_retries=1),
    )

    assert failed_run.status == "FAILED"
    assert failed_run.items[0].status == "FAILED"
    assert failed_run.items[0].error_message == "No contract"

    resume_plan = FetchPlan(
        run_id=7,
        requested_tickers=["BAD"],
        symbols_including_benchmarks=["BAD"],
        items=[_plan_item("BAD", FetchAction.FULL_BACKFILL)],
        estimated_request_count=1,
        estimated_full_backfills=1,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )
    calls = {}

    def fake_build_fetch_plan(**kwargs):
        calls.update(kwargs)
        return resume_plan

    monkeypatch.setattr(jobs, "build_fetch_plan", fake_build_fetch_plan)
    resume_db = ResumeFakeDb(failed_run)

    queued_run, queued_plan, options = resume_fetch_job(resume_db, failed_run.id)

    assert calls["tickers"] == ["BAD"]
    assert calls["include_benchmarks"] is False
    assert queued_run.status == "QUEUED"
    assert queued_plan is resume_plan
    assert options == FetchJobOptions(
        include_benchmarks=False,
        force_refresh=False,
        force_full_backfill=False,
    )


def _fake_score_rows_v2(rows) -> list[FundamentalScoreV2Result]:
    return [
        FundamentalScoreV2Result(
            ticker=row.ticker,
            growth_quality_score=8.0,
            profitability_quality_score=8.0,
            fcf_quality_score=7.0,
            earnings_quality_score=7.0,
            capital_efficiency_score=7.0,
            balance_sheet_quality_score=7.0,
            valuation_quality_score=6.0,
            forward_quality_score=6.0,
            shareholder_quality_score=6.0,
            liquidity_risk_score=8.0,
            data_coverage_score=9.0,
            missing_data_penalty=0.0,
            fundamental_score=8.0,
            fundamental_label="High-quality quant",
            warning_flags=[],
            explanation="Deterministic test score.",
            debug={"model_version": "fundamentals_v2.0"},
        )
        for row in rows
        if row.ticker
    ]


def _technical(ticker: str) -> TechnicalScore:
    return TechnicalScore(
        run_id=7,
        ticker=ticker,
        trend_score=Decimal("8"),
        local_trend_score=Decimal("8"),
        momentum_score=Decimal("8"),
        setup_score=Decimal("8"),
        risk_score=Decimal("2"),
        market_score=Decimal("8"),
        relative_strength_score=Decimal("8"),
        sector_relative_strength_score=Decimal("8"),
        combined_relative_strength_score=Decimal("8"),
        htf_score=Decimal("8"),
        dual_score=Decimal("8"),
        classification="Prime clean pullback",
        technical_confidence="normal",
        insufficient_data=False,
    )


def _plan_item(ticker: str, action: FetchAction) -> FetchPlanItem:
    return FetchPlanItem(
        ticker=ticker,
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=action,
        duration="10 D" if action != FetchAction.CONTRACT_RESOLUTION_REQUIRED else None,
        bar_size="1 day",
        current_bar_count=0,
        first_bar_date=None,
        latest_bar_date=None,
        required_bars=2,
        reason="test",
        estimated_request_count=0 if action == FetchAction.CONTRACT_RESOLUTION_REQUIRED else 1,
    )


def _coverage() -> OhlcvCoverageSummary:
    return OhlcvCoverageSummary(
        total_tickers=1,
        ready_count=1,
        insufficient_count=0,
        missing_count=0,
        benchmark_spy_ready=False,
        benchmark_qqq_ready=False,
        required_rows=2,
        items=[
            OhlcvCoverageItem(
                ticker="MSFT",
                adjusted_bars=2,
                trades_bars=2,
                has_price=True,
                has_volume=True,
                sufficient_history=True,
                status="ready",
                first_adjusted_date=date(2026, 7, 1),
                latest_adjusted_date=date(2026, 7, 2),
                first_trades_date=date(2026, 7, 1),
                latest_trades_date=date(2026, 7, 2),
                latest_bar_current=True,
                reason="ready",
            )
        ],
    )


class UploadFakeDb:
    def __init__(self) -> None:
        self.runs: list[UploadRun] = []
        self.raw_rows: list[RawCompanyRow] = []
        self.fundamental_scores: list[FundamentalScore] = []
        self.next_id = 7

    def add(self, row) -> None:
        if isinstance(row, UploadRun):
            row.id = self.next_id
            self.next_id += 1
            row.raw_company_rows = []
            row.fundamental_scores = []
            row.technical_scores = []
            row.combined_results = []
            self.runs.append(row)

    def add_all(self, rows) -> None:
        for row in rows:
            if isinstance(row, RawCompanyRow):
                self.raw_rows.append(row)
                self.runs[0].raw_company_rows.append(row)
            if isinstance(row, FundamentalScore):
                self.fundamental_scores.append(row)
                self.runs[0].fundamental_scores.append(row)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def refresh(self, row) -> None:
        pass


class PlanFakeDb:
    def execute(self, statement):
        statement_text = str(statement)
        if "price_bars" in statement_text:
            return FakeResult(
                [
                    ("MSFT", "TRADES", 0, None, None),
                    ("MSFT", "ADJUSTED_LAST", 0, None, None),
                ]
            )
        if "ib_contracts" in statement_text:
            return FakeResult([("MSFT", "RESOLVED"), ("SPY", "RESOLVED")])
        return FakeResult([])

    def scalars(self, statement):
        return FakeScalarResult([])


class ExecutorFakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flushes = 0
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, IBFetchRun) and row.id is None:
            row.id = 99
        if isinstance(row, IBFetchItem) and row.fetch_run and row not in row.fetch_run.items:
            row.fetch_run.items.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1


class CockpitFakeDb:
    def __init__(
        self,
        raw_rows: list[RawCompanyRow],
        fundamentals: list[FundamentalScore],
        technicals: list[TechnicalScore],
    ) -> None:
        self.raw_rows = raw_rows
        self.fundamentals = fundamentals
        self.technicals = technicals
        self.combined_results: list[CombinedResult] = []
        self.deleted_combined_results = False

    def scalars(self, statement):
        statement_text = str(statement)
        if "raw_company_rows" in statement_text:
            return FakeScalarResult(self.raw_rows)
        if "fundamental_scores" in statement_text:
            return FakeScalarResult(self.fundamentals)
        if "technical_scores" in statement_text:
            return FakeScalarResult(self.technicals)
        return FakeScalarResult([])

    def execute(self, statement):
        if "DELETE FROM combined_results" in str(statement):
            self.deleted_combined_results = True
            self.combined_results = []
        return FakeResult([])

    def add_all(self, rows) -> None:
        self.combined_results.extend(rows)

    def flush(self) -> None:
        pass


class ResumeFakeDb:
    def __init__(self, failed_run: IBFetchRun) -> None:
        self.failed_run = failed_run
        self.added = []

    def scalar(self, statement):
        return self.failed_run

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, IBFetchRun):
            row.id = 100

    def flush(self) -> None:
        pass


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class FakeIB:
    connected = False

    def connect(self, *args, **kwargs) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:
        return self.connected


class FakeLimiter:
    def wait_before_request(self) -> None:
        pass

    def backoff_after_error(self, error: Exception, attempt: int) -> None:
        pass
