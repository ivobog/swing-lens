from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, column, func, select
from sqlalchemy.orm import Session

from app.models.tables import CombinedResult, UploadRun
from app.services.pagination import Page, paginate_query


@dataclass(frozen=True)
class RunFilters:
    status: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    search: str | None = None
    sort: str = "uploaded_at"
    direction: str = "desc"


@dataclass(frozen=True)
class DecisionFilters:
    from_date: date | None = None
    to_date: date | None = None
    decision: str | None = None
    ticker: str | None = None
    sector: str | None = None
    min_score: Decimal | None = None
    has_warning: bool | None = None
    incomplete_only: bool = False


@dataclass(frozen=True)
class RunListSummary:
    run_id: int
    filename: str
    uploaded_at: datetime | None
    processed_at: datetime | None
    status: str
    row_count: int
    combined_count: int
    incomplete_count: int
    warning_count: int
    strong_count: int
    top_complete_ticker: str | None
    top_complete_score: Decimal | None


@dataclass(frozen=True)
class HistoricalDecision:
    run_id: int
    uploaded_at: datetime | None
    rank: int | None
    ticker: str
    company_name: str | None
    sector: str | None
    final_score: Decimal | None
    combined_decision: str | None
    position_size_hint: str | None
    has_warning: bool
    is_complete: bool


def paged_runs(
    db: Session,
    filters: RunFilters,
    page: int,
    page_size: int,
    max_page_size: int = 200,
) -> Page:
    statement = _runs_statement(filters)
    raw_page = paginate_query(db, statement, page, page_size, max_page_size=max_page_size)
    return Page(
        items=[_run_summary_from_row(row) for row in raw_page.items],
        page=raw_page.page,
        page_size=raw_page.page_size,
        total_items=raw_page.total_items,
        total_pages=raw_page.total_pages,
    )


def paged_decisions(
    db: Session,
    filters: DecisionFilters,
    page: int,
    page_size: int,
    max_page_size: int = 200,
) -> Page:
    statement = _decisions_statement(filters)
    raw_page = paginate_query(db, statement, page, page_size, max_page_size=max_page_size)
    return Page(
        items=[_decision_from_row(row) for row in raw_page.items],
        page=raw_page.page,
        page_size=raw_page.page_size,
        total_items=raw_page.total_items,
        total_pages=raw_page.total_pages,
    )


def _runs_statement(filters: RunFilters) -> Select:
    combined_count = (
        select(func.count(CombinedResult.id))
        .where(CombinedResult.run_id == UploadRun.id)
        .correlate(UploadRun)
        .scalar_subquery()
    )
    incomplete_count = (
        select(func.count(CombinedResult.id))
        .where(CombinedResult.run_id == UploadRun.id)
        .where(CombinedResult.is_complete.is_(False))
        .correlate(UploadRun)
        .scalar_subquery()
    )
    warning_count = (
        select(func.count(CombinedResult.id))
        .where(CombinedResult.run_id == UploadRun.id)
        .where(CombinedResult.has_warning.is_(True))
        .correlate(UploadRun)
        .scalar_subquery()
    )
    strong_count = (
        select(func.count(CombinedResult.id))
        .where(CombinedResult.run_id == UploadRun.id)
        .where(CombinedResult.combined_decision == "Strong candidate")
        .where(CombinedResult.is_complete.is_(True))
        .correlate(UploadRun)
        .scalar_subquery()
    )
    top_ticker = (
        select(CombinedResult.ticker)
        .where(CombinedResult.run_id == UploadRun.id)
        .where(CombinedResult.is_complete.is_(True))
        .order_by(CombinedResult.final_rank.asc().nullslast(), CombinedResult.final_score.desc())
        .limit(1)
        .correlate(UploadRun)
        .scalar_subquery()
    )
    top_score = (
        select(CombinedResult.final_score)
        .where(CombinedResult.run_id == UploadRun.id)
        .where(CombinedResult.is_complete.is_(True))
        .order_by(CombinedResult.final_rank.asc().nullslast(), CombinedResult.final_score.desc())
        .limit(1)
        .correlate(UploadRun)
        .scalar_subquery()
    )

    statement = select(
        UploadRun.id.label("run_id"),
        UploadRun.filename,
        UploadRun.uploaded_at,
        UploadRun.processed_at,
        UploadRun.status,
        UploadRun.row_count,
        combined_count.label("combined_count"),
        incomplete_count.label("incomplete_count"),
        warning_count.label("warning_count"),
        strong_count.label("strong_count"),
        top_ticker.label("top_complete_ticker"),
        top_score.label("top_complete_score"),
    )

    if filters.status:
        statement = statement.where(UploadRun.status == filters.status)
    if filters.search:
        statement = statement.where(UploadRun.filename.ilike(f"%{filters.search}%"))
    if filters.from_date:
        statement = statement.where(UploadRun.uploaded_at >= _start_of_day(filters.from_date))
    if filters.to_date:
        statement = statement.where(UploadRun.uploaded_at < _day_after(filters.to_date))

    return statement.order_by(*_run_ordering(filters))


def _decisions_statement(filters: DecisionFilters) -> Select:
    statement = (
        select(
            CombinedResult.run_id,
            UploadRun.uploaded_at,
            CombinedResult.final_rank.label("rank"),
            CombinedResult.ticker,
            CombinedResult.company_name,
            CombinedResult.sector,
            CombinedResult.final_score,
            CombinedResult.combined_decision,
            CombinedResult.position_size_hint,
            CombinedResult.has_warning,
            CombinedResult.is_complete,
        )
        .join(UploadRun, CombinedResult.run_id == UploadRun.id)
        .order_by(UploadRun.uploaded_at.desc(), CombinedResult.final_rank.asc().nullslast())
    )

    if filters.from_date:
        statement = statement.where(UploadRun.uploaded_at >= _start_of_day(filters.from_date))
    if filters.to_date:
        statement = statement.where(UploadRun.uploaded_at < _day_after(filters.to_date))
    if filters.decision:
        statement = statement.where(CombinedResult.combined_decision == filters.decision)
    if filters.ticker:
        statement = statement.where(CombinedResult.ticker.ilike(f"%{filters.ticker}%"))
    if filters.sector:
        statement = statement.where(CombinedResult.sector == filters.sector)
    if filters.min_score is not None:
        statement = statement.where(CombinedResult.final_score >= filters.min_score)
    if filters.has_warning is not None:
        statement = statement.where(CombinedResult.has_warning.is_(filters.has_warning))
    if filters.incomplete_only:
        statement = statement.where(CombinedResult.is_complete.is_(False))

    return statement


def _run_ordering(filters: RunFilters) -> tuple[Any, ...]:
    direction = filters.direction if filters.direction in {"asc", "desc"} else "desc"
    sort_map = {
        "uploaded_at": UploadRun.uploaded_at,
        "run_id": UploadRun.id,
        "status": UploadRun.status,
        "row_count": UploadRun.row_count,
        "combined_count": "combined_count",
        "incomplete_count": "incomplete_count",
        "warning_count": "warning_count",
        "strong_count": "strong_count",
        "best_score": "top_complete_score",
    }
    sort_column = sort_map.get(filters.sort, UploadRun.uploaded_at)
    if isinstance(sort_column, str):
        ordered = (
            column(sort_column).asc()
            if direction == "asc"
            else column(sort_column).desc()
        )
    else:
        ordered = sort_column.asc() if direction == "asc" else sort_column.desc()
    return (ordered, UploadRun.id.desc())


def _run_summary_from_row(row: Any) -> RunListSummary:
    return RunListSummary(
        run_id=row.run_id,
        filename=row.filename,
        uploaded_at=row.uploaded_at,
        processed_at=row.processed_at,
        status=row.status,
        row_count=row.row_count or 0,
        combined_count=row.combined_count or 0,
        incomplete_count=row.incomplete_count or 0,
        warning_count=row.warning_count or 0,
        strong_count=row.strong_count or 0,
        top_complete_ticker=row.top_complete_ticker,
        top_complete_score=row.top_complete_score,
    )


def _decision_from_row(row: Any) -> HistoricalDecision:
    return HistoricalDecision(
        run_id=row.run_id,
        uploaded_at=row.uploaded_at,
        rank=row.rank,
        ticker=row.ticker,
        company_name=row.company_name,
        sector=row.sector,
        final_score=row.final_score,
        combined_decision=row.combined_decision,
        position_size_hint=row.position_size_hint,
        has_warning=row.has_warning,
        is_complete=row.is_complete,
    )


def _start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _day_after(value: date) -> datetime:
    return datetime.combine(value + timedelta(days=1), time.min)
