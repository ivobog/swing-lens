from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models.tables import CombinedResult, UploadRun


@dataclass(frozen=True)
class RunHistorySummary:
    run_id: int
    filename: str
    uploaded_at: datetime | None
    processed_at: datetime | None
    status: str
    row_count: int
    combined_count: int
    top_ticker: str | None
    top_score: Decimal | None
    top_decision: str | None


@dataclass(frozen=True)
class RecentDecision:
    run_id: int
    uploaded_at: datetime | None
    rank: int | None
    ticker: str
    company_name: str | None
    sector: str | None
    final_score: Decimal | None
    combined_decision: str | None
    position_size_hint: str | None


def summarize_runs(runs: list[UploadRun]) -> list[RunHistorySummary]:
    return [summarize_run(run) for run in runs]


def summarize_run(run: UploadRun) -> RunHistorySummary:
    results = _sorted_results(run.combined_results)
    top = results[0] if results else None
    return RunHistorySummary(
        run_id=run.id,
        filename=run.filename,
        uploaded_at=run.uploaded_at,
        processed_at=run.processed_at,
        status=run.status,
        row_count=run.row_count or 0,
        combined_count=len(results),
        top_ticker=top.ticker if top else None,
        top_score=top.final_score if top else None,
        top_decision=top.combined_decision if top else None,
    )


def recent_decisions(runs: list[UploadRun], limit: int = 100) -> list[RecentDecision]:
    decisions: list[RecentDecision] = []
    for run in runs:
        for result in _sorted_results(run.combined_results):
            decisions.append(_recent_decision(run, result))
    decisions.sort(
        key=lambda item: (
            item.uploaded_at or datetime.min,
            -(item.rank or 0),
        ),
        reverse=True,
    )
    return decisions[:limit]


def _recent_decision(run: UploadRun, result: CombinedResult) -> RecentDecision:
    return RecentDecision(
        run_id=run.id,
        uploaded_at=run.uploaded_at,
        rank=result.final_rank,
        ticker=result.ticker,
        company_name=result.company_name,
        sector=result.sector,
        final_score=result.final_score,
        combined_decision=result.combined_decision,
        position_size_hint=result.position_size_hint,
    )


def _sorted_results(results: list[CombinedResult]) -> list[CombinedResult]:
    return sorted(results, key=lambda result: result.final_rank or 0)
