from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.models.ib_market_intelligence_tables import (
    IBExecutionFill,
    IBTradeEpisode,
    IBTradeResearchLink,
)
from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RankingResult,
    SetupSignalSnapshot,
    TechnicalScore,
    UploadRun,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.operational_metrics import operational_metrics
from app.services.winner_probability.estimate_lifecycle import estimate_is_serving

ZERO = Decimal("0")


@dataclass
class EpisodeDraft:
    ticker: str
    account_hash: str | None
    direction: str
    opened_at: datetime
    position: Decimal = ZERO
    entry_quantity: Decimal = ZERO
    exit_quantity: Decimal = ZERO
    entry_notional: Decimal = ZERO
    exit_notional: Decimal = ZERO
    gross_pnl: Decimal = ZERO
    commissions: Decimal = ZERO
    fees: Decimal = ZERO
    broker_realized_pnl: Decimal = ZERO
    has_broker_pnl: bool = False
    fill_ids: list[int] = field(default_factory=list)
    closed_at: datetime | None = None


def rebuild_trade_episodes(db: Session) -> list[IBTradeEpisode]:
    fills = db.scalars(
        select(IBExecutionFill)
        .where(IBExecutionFill.is_superseded.is_(False))
        .where(IBExecutionFill.is_excluded.is_(False))
        .order_by(
            IBExecutionFill.account_hash.asc().nullsfirst(),
            IBExecutionFill.symbol,
            IBExecutionFill.execution_time,
            IBExecutionFill.id,
        )
    ).all()
    drafts = construct_trade_episodes(fills)
    existing_rows = {
        row.episode_key: row for row in db.scalars(select(IBTradeEpisode)).all()
    }
    active_keys: set[str] = set()
    persisted: list[IBTradeEpisode] = []
    for draft in drafts:
        key = _episode_key(draft)
        active_keys.add(key)
        row = existing_rows.get(key)
        values = _episode_values(draft)
        if row is None:
            row = IBTradeEpisode(episode_key=key, **values)
            db.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
        persisted.append(row)
    for key, row in existing_rows.items():
        if key not in active_keys:
            row.status = "SUPERSEDED"
    db.flush()
    return persisted


def construct_trade_episodes(fills: list[IBExecutionFill]) -> list[EpisodeDraft]:
    grouped: dict[tuple[str | None, str], list[IBExecutionFill]] = defaultdict(list)
    for fill in fills:
        grouped[(fill.account_hash, fill.symbol.upper())].append(fill)
    completed: list[EpisodeDraft] = []
    for (account_hash, ticker), rows in grouped.items():
        rows.sort(key=lambda fill: (fill.execution_time, fill.id or 0))
        current: EpisodeDraft | None = None
        for fill in rows:
            signed = fill.quantity if fill.side == "BUY" else -fill.quantity
            remaining = abs(signed)
            fill_direction = 1 if signed > 0 else -1
            while remaining > ZERO:
                if current is None or current.position == ZERO:
                    current = EpisodeDraft(
                        ticker=ticker,
                        account_hash=account_hash,
                        direction="LONG" if fill_direction > 0 else "SHORT",
                        opened_at=fill.execution_time,
                    )
                position_direction = (
                    1
                    if current.direction == "LONG"
                    else -1
                    if current.position == ZERO
                    else 1
                    if current.position > 0
                    else -1
                )
                if position_direction == fill_direction:
                    allocated = remaining
                    _apply_entry(current, fill, allocated)
                    remaining = ZERO
                else:
                    closing = min(abs(current.position), remaining)
                    _apply_exit(current, fill, closing)
                    remaining -= closing
                    if current.position == ZERO:
                        current.closed_at = fill.execution_time
                        completed.append(current)
                        current = None
        if current is not None and current.position != ZERO:
            completed.append(current)
    return sorted(completed, key=lambda item: (item.opened_at, item.ticker))


def match_episode_to_research(
    db: Session,
    episode: IBTradeEpisode,
    *,
    lookback_sessions: int = 5,
    policy: str = "latest-completed-before-entry-v1",
) -> IBTradeResearchLink:
    cutoff = episode.opened_at
    earliest = cutoff - timedelta(days=max(lookback_sessions * 2, 7))
    candidates = db.execute(
        select(UploadRun, CombinedResult, TechnicalScore, FundamentalScore)
        .join(CombinedResult, CombinedResult.run_id == UploadRun.id)
        .outerjoin(
            TechnicalScore,
            (TechnicalScore.run_id == UploadRun.id)
            & (TechnicalScore.ticker == CombinedResult.ticker)
            & (TechnicalScore.created_at <= cutoff),
        )
        .outerjoin(
            FundamentalScore,
            (FundamentalScore.run_id == UploadRun.id)
            & (FundamentalScore.ticker == CombinedResult.ticker)
            & (FundamentalScore.created_at <= cutoff),
        )
        .where(func.upper(CombinedResult.ticker) == episode.ticker.upper())
        .where(UploadRun.processed_at.is_not(None))
        .where(UploadRun.processed_at <= cutoff)
        .where(UploadRun.processed_at >= earliest)
        .where(CombinedResult.created_at <= cutoff)
        .order_by(CombinedResult.created_at.desc(), UploadRun.processed_at.desc())
    ).all()
    candidates = sorted(
        candidates,
        key=lambda candidate: (
            candidate[1].created_at.date() == cutoff.date(),
            candidate[1].created_at,
            candidate[0].processed_at,
            candidate[0].id,
        ),
        reverse=True,
    )
    existing = db.scalar(
        select(IBTradeResearchLink).where(IBTradeResearchLink.trade_episode_id == episode.id)
    )
    if not candidates:
        link = existing or IBTradeResearchLink(trade_episode_id=episode.id)
        link.matching_status = "UNMATCHED"
        link.matching_policy = policy
        link.decision_timestamp = None
        link.context_json = {}
        link.leakage_check = "PASS"
        link.ambiguity_json = []
        if existing is None:
            db.add(link)
        operational_metrics.increment("swinglens_ibmi_unmatched_executions_total")
        db.flush()
        return link
    best_time = candidates[0][1].created_at
    tied = [candidate for candidate in candidates if candidate[1].created_at == best_time]
    if len(tied) > 1:
        link = existing or IBTradeResearchLink(trade_episode_id=episode.id)
        link.matching_status = "AMBIGUOUS"
        link.matching_policy = policy
        link.decision_timestamp = best_time
        link.context_json = {}
        link.leakage_check = "PASS" if best_time <= cutoff else "FAIL"
        link.ambiguity_json = [
            {"upload_run_id": candidate[0].id, "combined_result_id": candidate[1].id}
            for candidate in tied
        ]
        if existing is None:
            db.add(link)
        operational_metrics.increment("swinglens_ibmi_ambiguous_research_links_total")
        db.flush()
        return link
    run, combined, technical, fundamental = candidates[0]
    winner = db.scalar(
        select(WinnerPredictionSnapshot)
        .where(WinnerPredictionSnapshot.run_id == run.id)
        .where(func.upper(WinnerPredictionSnapshot.ticker) == episode.ticker.upper())
        .where(WinnerPredictionSnapshot.source_data_cutoff_at <= cutoff)
        .order_by(WinnerPredictionSnapshot.source_data_cutoff_at.desc())
    )
    winner_estimate = None
    if winner is not None:
        winner_estimate = _serving_winner_estimate(
            db, prediction_id=winner.id, cutoff=cutoff
        )
    setup = db.scalar(
        select(SetupSignalSnapshot)
        .where(SetupSignalSnapshot.run_id == run.id)
        .where(func.upper(SetupSignalSnapshot.ticker) == episode.ticker.upper())
        .where(SetupSignalSnapshot.calculated_at <= cutoff)
        .order_by(SetupSignalSnapshot.calculated_at.desc())
    )
    ranking = None
    if setup is not None and setup.ranking_result_id is not None:
        ranking = db.scalar(
            select(RankingResult)
            .where(RankingResult.id == setup.ranking_result_id)
            .where(RankingResult.created_at <= cutoff)
        )
    if ranking is None:
        ranking_query = (
            select(RankingResult)
            .where(RankingResult.run_id == run.id)
            .where(func.upper(RankingResult.ticker) == episode.ticker.upper())
            .where(RankingResult.created_at <= cutoff)
        )
        if winner is not None and winner.ranking_profile:
            ranking_query = ranking_query.where(
                RankingResult.ranking_profile == winner.ranking_profile
            )
        ranking = db.scalar(ranking_query.order_by(RankingResult.created_at.desc()))
    ceri = db.scalar(
        select(CeriScoreSnapshot)
        .where(CeriScoreSnapshot.run_id == run.id)
        .where(func.upper(CeriScoreSnapshot.ticker) == episode.ticker.upper())
        .where(CeriScoreSnapshot.cutoff_at <= cutoff)
        .order_by(CeriScoreSnapshot.cutoff_at.desc())
    )
    decision_reference = setup.close_price if setup else None
    slippage = None
    if decision_reference is not None and decision_reference > 0:
        if episode.direction == "LONG":
            slippage = (
                Decimal("100")
                * (episode.average_entry_price - decision_reference)
                / decision_reference
            )
        else:
            slippage = (
                Decimal("100")
                * (decision_reference - episode.average_entry_price)
                / decision_reference
            )
    context: dict[str, Any] = {
        "final_score": _string(combined.final_score),
        "combined_decision": combined.combined_decision,
        "fundamental_score": _string(fundamental.fundamental_score) if fundamental else None,
        "technical_score": _string(technical.dual_score) if technical else None,
        "market_regime": technical.market_regime if technical else None,
        "setup_family": setup.primary_setup_family if setup else None,
        "setup_state": setup.lifecycle_state_candidate if setup else None,
        "ceri_opportunity_score": ceri.opportunity_score if ceri else None,
        "ceri_event_risk_score": ceri.event_risk_score if ceri else None,
        "winner_probability": _string(winner_estimate.point_probability)
        if winner_estimate
        else None,
        "winner_evidence_grade": winner_estimate.evidence_grade if winner_estimate else None,
        "ranking_profile": (
            winner.ranking_profile
            if winner
            else ranking.ranking_profile
            if ranking
            else None
        ),
        "ranking_profile_score": _string(ranking.profile_score) if ranking else None,
        "ranking_profile_rank": ranking.profile_rank if ranking else None,
        "ranking_decision": ranking.decision_label if ranking else None,
        "sector": combined.sector,
        "sector_state": winner.sector_state if winner else None,
        "slippage_reference": "SETUP_DECISION_CLOSE" if decision_reference is not None else None,
        "slippage_reference_price": _string(decision_reference),
        "execution_slippage_pct": _string(slippage),
    }
    link = existing or IBTradeResearchLink(trade_episode_id=episode.id)
    link.upload_run_id = run.id
    link.combined_result_id = combined.id
    link.technical_score_id = technical.id if technical else None
    link.fundamental_score_id = fundamental.id if fundamental else None
    link.matching_status = "MATCHED"
    link.matching_policy = policy
    link.decision_timestamp = combined.created_at
    link.context_json = context
    evidence_times = [run.processed_at, combined.created_at]
    evidence_times.extend(
        timestamp
        for timestamp in (
            technical.created_at if technical else None,
            fundamental.created_at if fundamental else None,
            setup.calculated_at if setup else None,
            ranking.created_at if ranking else None,
            ceri.cutoff_at if ceri else None,
            winner.source_data_cutoff_at if winner else None,
            winner_estimate.created_at if winner_estimate else None,
        )
        if timestamp is not None
    )
    link.leakage_check = "PASS" if all(value <= cutoff for value in evidence_times) else "FAIL"
    link.ambiguity_json = []
    if existing is None:
        db.add(link)
    db.flush()
    return link


def _serving_winner_estimate(
    db: Session,
    *,
    prediction_id: int,
    cutoff: datetime,
) -> WinnerProbabilityEstimate | None:
    return db.scalar(
        select(WinnerProbabilityEstimate)
        .where(WinnerProbabilityEstimate.prediction_id == prediction_id)
        .where(WinnerProbabilityEstimate.created_at <= cutoff)
        .where(estimate_is_serving())
        .order_by(
            WinnerProbabilityEstimate.created_at.desc(),
            WinnerProbabilityEstimate.id.desc(),
        )
        .limit(1)
    )


def journal_analytics(db: Session, *, group_by: str | None = None) -> dict[str, Any]:
    episodes = db.scalars(
        select(IBTradeEpisode)
        .where(IBTradeEpisode.status == "CLOSED")
        .where(IBTradeEpisode.is_excluded.is_(False))
        .order_by(IBTradeEpisode.closed_at)
    ).all()
    links = {link.trade_episode_id: link for link in db.scalars(select(IBTradeResearchLink)).all()}
    overall = _summarize(episodes)
    overall["average_slippage_pct"] = _average(
        [
            float(link.context_json["execution_slippage_pct"])
            for link in links.values()
            if link.context_json.get("execution_slippage_pct") is not None
        ]
    )
    allowed = {
        "setup_family",
        "score_band",
        "ceri_band",
        "winner_evidence_grade",
        "market_regime",
        "sector_state",
        "ranking_profile",
    }
    if group_by is None:
        group_by = "setup_family"
    if group_by not in allowed:
        raise ValueError(f"Unsupported journal analytics grouping: {group_by}")
    grouped: dict[str, list[IBTradeEpisode]] = defaultdict(list)
    for episode in episodes:
        context = (links.get(episode.id).context_json if links.get(episode.id) else {}) or {}
        value = _analytics_dimension(group_by, context)
        grouped[str(value or "UNAVAILABLE")].append(episode)
    group_summaries: dict[str, dict[str, Any]] = {}
    for key, rows in sorted(grouped.items()):
        summary = _summarize(rows)
        summary["average_slippage_pct"] = _average(
            [
                float(links[row.id].context_json["execution_slippage_pct"])
                for row in rows
                if row.id in links
                and links[row.id].context_json.get("execution_slippage_pct") is not None
            ]
        )
        group_summaries[key] = summary
    return {"overall": overall, "group_by": group_by, "groups": group_summaries}


def _apply_entry(draft: EpisodeDraft, fill: IBExecutionFill, quantity: Decimal) -> None:
    ratio = quantity / fill.quantity
    direction = Decimal("1") if draft.direction == "LONG" else Decimal("-1")
    draft.position += direction * quantity
    draft.entry_quantity += quantity
    draft.entry_notional += quantity * fill.price
    _apply_costs(draft, fill, ratio)
    _append_fill(draft, fill)


def _apply_exit(draft: EpisodeDraft, fill: IBExecutionFill, quantity: Decimal) -> None:
    ratio = quantity / fill.quantity
    average_entry = draft.entry_notional / draft.entry_quantity
    if draft.direction == "LONG":
        draft.gross_pnl += quantity * (fill.price - average_entry)
        draft.position -= quantity
    else:
        draft.gross_pnl += quantity * (average_entry - fill.price)
        draft.position += quantity
    draft.exit_quantity += quantity
    draft.exit_notional += quantity * fill.price
    _apply_costs(draft, fill, ratio)
    if fill.broker_realized_pnl is not None:
        draft.broker_realized_pnl += fill.broker_realized_pnl * ratio
        draft.has_broker_pnl = True
    _append_fill(draft, fill)


def _apply_costs(draft: EpisodeDraft, fill: IBExecutionFill, ratio: Decimal) -> None:
    draft.commissions += abs(fill.commission) * ratio
    draft.fees += abs(fill.fees) * ratio


def _append_fill(draft: EpisodeDraft, fill: IBExecutionFill) -> None:
    if fill.id not in draft.fill_ids:
        draft.fill_ids.append(fill.id)


def _episode_values(draft: EpisodeDraft) -> dict[str, Any]:
    closed = draft.position == ZERO and draft.closed_at is not None
    average_entry = draft.entry_notional / draft.entry_quantity
    average_exit = draft.exit_notional / draft.exit_quantity if draft.exit_quantity else None
    net = draft.gross_pnl - draft.commissions - draft.fees if closed else None
    deployed = draft.entry_notional
    return {
        "ticker": draft.ticker,
        "direction": draft.direction,
        "opened_at": draft.opened_at,
        "closed_at": draft.closed_at,
        "entry_quantity": draft.entry_quantity,
        "exit_quantity": draft.exit_quantity,
        "average_entry_price": average_entry,
        "average_exit_price": average_exit,
        "deployed_entry_capital": deployed,
        "gross_pnl": draft.gross_pnl if closed else None,
        "broker_realized_pnl": draft.broker_realized_pnl if draft.has_broker_pnl else None,
        "commissions": draft.commissions,
        "fees": draft.fees,
        "net_pnl": net,
        "return_pct": (Decimal("100") * net / deployed) if net is not None and deployed else None,
        "holding_seconds": int((draft.closed_at - draft.opened_at).total_seconds())
        if closed
        else None,
        "status": "CLOSED" if closed else "OPEN",
        "matching_policy": "FIFO_POSITION_V1",
        "fill_ids_json": draft.fill_ids,
        "is_excluded": False,
    }


def _episode_key(draft: EpisodeDraft) -> str:
    raw = "|".join(
        (
            draft.account_hash or "",
            draft.ticker,
            draft.opened_at.isoformat(),
            str(draft.fill_ids[0] if draft.fill_ids else ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _summarize(rows: list[IBTradeEpisode]) -> dict[str, Any]:
    count = len(rows)
    returns = sorted(float(row.return_pct) for row in rows if row.return_pct is not None)
    net_values = [float(row.net_pnl) for row in rows if row.net_pnl is not None]
    wins = sum(value > 0 for value in net_values)
    return {
        "trade_count": count,
        "win_rate": wins / count if count else None,
        "average_return_pct": sum(returns) / len(returns) if returns else None,
        "median_return_pct": _median(returns),
        "realized_net_pnl": sum(net_values),
        "commission_impact": sum(float(row.commissions + row.fees) for row in rows),
        "average_holding_seconds": sum(row.holding_seconds or 0 for row in rows) / count
        if count
        else None,
        "broker_reported_realized_pnl": sum(
            float(row.broker_realized_pnl) for row in rows if row.broker_realized_pnl is not None
        ),
        "broker_reported_count": sum(row.broker_realized_pnl is not None for row in rows),
    }


def _analytics_dimension(group_by: str, context: dict[str, Any]) -> Any:
    if group_by == "score_band":
        return _band(
            context.get("final_score"), (50, 65, 80), ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")
        )
    if group_by == "ceri_band":
        return _band(
            context.get("ceri_opportunity_score"), (3, 6, 8), ("LOW", "MEDIUM", "HIGH", "VERY_HIGH")
        )
    return context.get(group_by)


def _band(value: Any, thresholds: tuple[float, ...], labels: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    number = float(value)
    for threshold, label in zip(thresholds, labels, strict=False):
        if number < threshold:
            return label
    return labels[-1]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _string(value: Any) -> str | None:
    return str(value) if value is not None else None
